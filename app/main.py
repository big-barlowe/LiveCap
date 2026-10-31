import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import os
import secrets
import signal
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

import numpy as np
import soxr
import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from .branding import LOGO_HEADERS, MAX_LOGO_BYTES, Branding, theme_css, validated
from .engine import SAMPLE_RATE, Engine
from .hub import ORIGINAL, Hub
from .languages import AVAILABLE_OUTPUTS, INPUT_LANGUAGES, RTL_LANGUAGES
from .models import DATA_DIR
from .obs import start_listeners
from .translate import Translator
from .tts import Speaker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("livecap")

HTTP_PORT = int(os.environ.get("LIVECAP_HTTP_PORT", 8000))
HTTPS_PORT = int(os.environ.get("LIVECAP_HTTPS_PORT", 8443))
RTMP_PORT = int(os.environ.get("LIVECAP_RTMP_PORT", 1935))
SRT_PORT = int(os.environ.get("LIVECAP_SRT_PORT", 9000))
INPUT_LANG = os.environ.get("LIVECAP_INPUT_LANG", "en")
PRELOAD = [code.strip() for code in os.environ.get("LIVECAP_PRELOAD", "").split(",") if code.strip()]
STATIC = Path(__file__).parent / "static"
# Goes in the addresses of the page's files, so a browser holding an older version can't reuse it.
STATIC_VERSION = hashlib.sha256(b"".join(path.read_bytes() for path in sorted(STATIC.iterdir()) if path.is_file())).hexdigest()[:12]
SOURCES = ("", "mic", "obs")
INITIAL_SOURCE = os.environ.get("LIVECAP_SOURCE", "")
SESSION_TOKEN = secrets.token_urlsafe(32)  # new each start, so an admin page reloads and re-authenticates
OBS_LIVE_SECONDS = 2.0
MAX_MIC_FRAME_BYTES = 1 << 20
CATCH_UP_STEPS = (0.0, 0.1, 0.2, 0.3)

translator = Translator()
speaker = Speaker()
hub = Hub(translator, speaker)
engine = Engine(hub.on_sentence, hub.on_partial, INPUT_LANG if INPUT_LANG == "auto" or INPUT_LANG in INPUT_LANGUAGES else "en",
                source=INITIAL_SOURCE if INITIAL_SOURCE == "obs" else None)
hub.set_input_language(engine.input_lang)
branding = Branding(DATA_DIR / "branding")
last_public_state = None
listeners = []
mic_owner = None

app = FastAPI(title="LiveCap", docs_url=None, redoc_url=None, openapi_url=None)


def same_origin(socket):
    # Stops another website the viewer has open from driving this server through their browser.
    origin = socket.headers.get("origin")
    return origin is None or urlsplit(origin).netloc == socket.headers.get("host")


def admin_password():
    password = os.environ.get("LIVECAP_ADMIN_PASSWORD")
    if password:
        return password
    path = DATA_DIR / "admin-password"
    if path.exists():
        log.info("admin password is in %s", path)
        return path.read_text().strip()
    password = secrets.token_urlsafe(12)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o600)
    path.write_text(password + "\n")
    log.warning("generated admin password, also saved in %s: %s", path, password)
    return password


ADMIN_PASSWORD = admin_password()


def cookie_name(secure):
    # Separate names, so a session opened over HTTPS is never sent over plain HTTP.
    return "livecap_admin_tls" if secure else "livecap_admin"


def has_session(connection):
    secure = connection.url.scheme in ("https", "wss")
    return secrets.compare_digest(connection.cookies.get(cookie_name(secure), ""), SESSION_TOKEN)


def basic_auth_ok(request):
    scheme, _, encoded = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "basic":
        return False
    try:
        _, _, password = base64.b64decode(encoded).decode().partition(":")
    except ValueError:
        return False
    return secrets.compare_digest(password.encode(), ADMIN_PASSWORD.encode())


def obs_live():
    return [listener.protocol for listener in listeners if time.monotonic() - listener.last_audio < OBS_LIVE_SECONDS]


def admin_state():
    return {
        "type": "state",
        "source": engine.source or "",
        "inputLang": engine.input_lang,
        "obsLive": obs_live(),
        "speaking": engine.speaking,
        "backlog": round(engine.backlog_seconds, 1),
        "readMs": round(engine.last_read_ms),
        "droppedSeconds": round(engine.dropped_seconds),
        "viewers": len(hub.clients),
        "error": engine.error,
    }


def public_state():
    running = bool(engine.source) and engine.error is None
    waiting = running and engine.source == "obs" and not obs_live()
    return {"type": "state", "live": running and not waiting, "waiting": waiting, "speaking": engine.speaking}


def publish_state():
    """Viewers only get told when something they can see changed."""
    global last_public_state
    public = public_state()
    changed = public != last_public_state
    last_public_state = public
    hub.broadcast_state(admin_state(), public if changed else None)


def set_source(source):
    engine.configure(source=source)
    publish_state()


def valid_output(lang):
    return lang if lang == ORIGINAL or lang in AVAILABLE_OUTPUTS else ORIGINAL


@app.get("/healthz")
def healthz():
    return {"ok": engine.is_alive() and engine.error is None}


@app.get("/api/config")
def config():
    return {
        "outputLanguages": [{"code": code, "name": name, "voice": speaker.has_voice(code), "rtl": code in RTL_LANGUAGES}
                            for code, (name, _) in AVAILABLE_OUTPUTS.items()],
    }


@app.get("/api/admin/config")
def admin_config(request: Request):
    if not has_session(request):
        raise HTTPException(401)
    return {
        "inputLanguages": INPUT_LANGUAGES,
        "rtmpPort": RTMP_PORT,
        "srtPort": SRT_PORT,
        "httpsPort": HTTPS_PORT,
    }


def require_admin(request):
    if not same_origin(request):
        raise HTTPException(403)
    if not has_session(request):
        raise HTTPException(401, "Your admin session has ended. Reload the page and sign in again.")


def branding_changed():
    message = branding.message()
    hub.broadcast_state(message, message)
    return message


@app.get("/brand/theme.css")
def brand_css():
    return Response(theme_css(branding.settings), media_type="text/css", headers={"Cache-Control": LOGO_HEADERS["Cache-Control"]})


@app.get("/brand/logo")
def brand_logo():
    if not branding.logo_path:
        raise HTTPException(404)
    return FileResponse(branding.logo_path, media_type=branding.logo_media_type, headers=LOGO_HEADERS)


@app.get("/api/admin/branding/preview.css")
def brand_preview(request: Request):
    require_admin(request)
    try:
        css = theme_css(validated(dict(request.query_params)))
    except ValueError as failure:
        raise HTTPException(400, str(failure)) from None
    return Response(css, media_type="text/css", headers={"Cache-Control": "no-store"})


@app.put("/api/admin/branding")
async def save_branding(request: Request):
    require_admin(request)
    try:
        branding.save_settings(await request.json())
    except ValueError as failure:
        raise HTTPException(400, str(failure)) from None
    return branding_changed()


@app.put("/api/admin/branding/logo")
async def save_logo(request: Request):
    require_admin(request)
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > MAX_LOGO_BYTES:
            raise HTTPException(413, f"The logo can be at most {MAX_LOGO_BYTES >> 20} MB.")
    try:
        branding.save_logo(bytes(data))
    except ValueError as failure:
        raise HTTPException(400, str(failure)) from None
    return branding_changed()


@app.delete("/api/admin/branding/logo")
async def delete_logo(request: Request):
    require_admin(request)
    branding.remove_logo()
    return branding_changed()


@app.get("/api/speech/{lang}/{segment_id}.wav")
async def speech(lang: str, segment_id: int, boost: float = 0.0):
    # Fixed steps, because each one is synthesized and kept separately.
    boost = min(CATCH_UP_STEPS, key=lambda step: abs(step - boost))
    try:
        audio = await hub.speech(lang, segment_id, boost)
    except Exception as failure:
        log.warning("speech %s/%s failed: %s", lang, segment_id, failure)
        raise HTTPException(503, "speech unavailable") from None
    if audio is None:
        raise HTTPException(404)
    return Response(audio, media_type="audio/wav", headers={"Cache-Control": "no-store"})


async def caption_session(socket, admin):
    client = hub.join(valid_output(socket.query_params.get("lang", ORIGINAL)), admin)
    await socket.send_json(admin_state() if admin else public_state())
    await socket.send_json(branding.message())

    async def writer():
        while True:
            message = await client.outbox.get()
            if message is None:
                await socket.close(code=1013)
                return
            await socket.send_json(message)

    task = asyncio.create_task(writer())
    try:
        while True:
            message = json.loads(await socket.receive_text())
            kind = message.get("type")
            if kind == "language":
                hub.set_language(client, valid_output(message.get("lang")))
            elif not admin:
                continue
            elif kind == "source" and message.get("source") in SOURCES:
                set_source(message["source"])
            elif kind == "inputLang" and (message.get("lang") == "auto" or message.get("lang") in INPUT_LANGUAGES):
                engine.configure(input_lang=message["lang"])
                hub.set_input_language(message["lang"])
                publish_state()
    except (WebSocketDisconnect, ValueError, AttributeError):
        pass
    finally:
        task.cancel()
        hub.leave(client)


@app.websocket("/ws/captions")
async def captions(socket: WebSocket):
    if not same_origin(socket):
        await socket.close(code=1008)
        return
    await socket.accept()
    await caption_session(socket, admin=False)


async def accept_admin(socket):
    """Accepts first where the session is stale, so the page can see why and sign in again."""
    if not same_origin(socket):
        await socket.close(code=1008)
        return False
    await socket.accept()
    if not has_session(socket):
        await socket.close(code=4401)
        return False
    return True


@app.websocket("/ws/admin")
async def admin_captions(socket: WebSocket):
    if await accept_admin(socket):
        await caption_session(socket, admin=True)


@app.websocket("/ws/mic")
async def mic(socket: WebSocket):
    global mic_owner
    if not await accept_admin(socket):
        return
    try:
        rate = int(socket.query_params.get("rate", ""))
    except ValueError:
        rate = 0
    if not 8000 <= rate <= 192000:
        await socket.close(code=1003)
        return
    # The newest microphone takes over; only one can feed the single stream.
    previous, mic_owner = mic_owner, socket
    if previous:
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await previous.close(code=4001, reason="another microphone took over")
    set_source("mic")
    resampler = soxr.ResampleStream(rate, SAMPLE_RATE, 1, dtype="float32") if rate != SAMPLE_RATE else None
    try:
        while True:
            data = await socket.receive_bytes()
            if len(data) > MAX_MIC_FRAME_BYTES or len(data) % 4:
                await socket.close(code=1009)
                return
            samples = np.frombuffer(data, np.float32)
            engine.feed(resampler.resample_chunk(samples) if resampler else samples.copy(), "mic")
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        if mic_owner is socket:
            mic_owner = None
            if engine.source == "mic":
                set_source("")


class Static(StaticFiles):
    async def get_response(self, path, scope):
        # Without this a browser guesses how long to keep a file, and can run old code on a new page after an update.
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/static", Static(directory=STATIC), name="static")


def branded_page(name):
    # Filled in on the server, so a page never flashes the default look before the branding arrives.
    page = branding.render_page(STATIC / name).replace("__STATIC_VERSION__", STATIC_VERSION)
    return HTMLResponse(page, headers={"Cache-Control": "no-cache"})


@app.get("/favicon.ico")
def favicon():
    # Browsers ask for this by name on anything that isn't one of the two pages.
    return FileResponse(STATIC / "favicon.ico", media_type="image/x-icon", headers={"Cache-Control": "no-cache"})


@app.get("/")
def index():
    return branded_page("index.html")


@app.get("/admin")
def admin(request: Request):
    if not basic_auth_ok(request):
        return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="LiveCap admin", charset="UTF-8"'})
    response = branded_page("admin.html")
    secure = request.url.scheme == "https"
    response.set_cookie(cookie_name(secure), SESSION_TOKEN, httponly=True, samesite="strict", secure=secure, path="/")
    return response


def tls_files():
    """A self-signed certificate, unless cert.pem and key.pem were put in the data folder's certs folder."""
    folder = DATA_DIR / "certs"
    cert, key = folder / "cert.pem", folder / "key.pem"
    if not (cert.exists() and key.exists()):
        folder.mkdir(parents=True, exist_ok=True)
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650",
                        "-keyout", key, "-out", cert, "-subj", "/CN=livecap",
                        "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"],
                       check=True, capture_output=True)
    return cert, key


class Server(uvicorn.Server):
    # main() handles signals, so both servers stop together.
    @contextlib.contextmanager
    def capture_signals(self):
        yield


async def tick(stop):
    parent = int(os.environ.get("LIVECAP_PARENT_PID", 0))
    while True:
        await asyncio.sleep(1)
        publish_state()
        # If the Mac app dies without stopping this server, nothing else would.
        if parent and os.getppid() != parent:
            log.warning("the app that started this server has gone; stopping")
            stop()


def preload():
    for lang in PRELOAD:
        if lang not in AVAILABLE_OUTPUTS:
            log.warning("LIVECAP_PRELOAD: unknown language %s", lang)
            continue
        try:
            for source in {INPUT_LANG if INPUT_LANG in INPUT_LANGUAGES else "en"} - {lang}:
                translator.prepare(source, lang)
            if speaker.has_voice(lang):
                speaker.prepare(lang)
            log.info("preloaded %s", lang)
        except Exception as failure:
            log.warning("could not preload %s: %s", lang, failure)


async def main():
    loop = asyncio.get_running_loop()
    hub.attach(loop)
    engine.start()
    listeners.extend(start_listeners(lambda samples: engine.feed(samples, "obs"), RTMP_PORT, SRT_PORT))
    cert, key = tls_files()
    common = {"app": app, "host": "0.0.0.0", "lifespan": "off", "log_level": "warning", "ws_max_size": MAX_MIC_FRAME_BYTES}
    servers = [Server(uvicorn.Config(port=HTTP_PORT, **common)),
               Server(uvicorn.Config(port=HTTPS_PORT, ssl_certfile=str(cert), ssl_keyfile=str(key), **common))]

    def stop():
        for server in servers:
            server.should_exit = True

    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop)
    ticker = asyncio.create_task(tick(stop))
    if PRELOAD:
        loop.run_in_executor(None, preload)
    log.info("LiveCap on http://0.0.0.0:%s and https://0.0.0.0:%s", HTTP_PORT, HTTPS_PORT)
    await asyncio.gather(*(server.serve() for server in servers))
    ticker.cancel()
    for listener in listeners:
        listener.stop()
    os._exit(0)  # the engine's blocking threads hold no state worth waiting for


if __name__ == "__main__":
    asyncio.run(main())
