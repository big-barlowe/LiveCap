import logging
import os
import shutil
import subprocess
import threading
import time

import numpy as np

log = logging.getLogger("livecap.obs")

BYTES_PER_READ = 3200 * 4  # 200 ms of 16 kHz float samples
FFMPEG = os.environ.get("LIVECAP_FFMPEG", "ffmpeg")


class Listener(threading.Thread):
    """Keeps ffmpeg listening for one incoming stream, and hands on its audio as 16 kHz mono."""

    def __init__(self, name, url, input_args, on_audio, relay=None):
        super().__init__(daemon=True, name=f"obs-{name}")
        self.relay = relay
        self.protocol = name
        self.url = url
        self.input_args = input_args
        self.on_audio = on_audio
        self.last_audio = 0.0
        self.process = None

    def command(self):
        return [FFMPEG, "-hide_banner", "-loglevel", "warning", "-nostdin",
                "-probesize", "256k", "-analyzeduration", "500000",
                *self.input_args, "-i", "pipe:0" if self.relay else self.url,
                "-vn", "-sn", "-dn", "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-f", "f32le", "pipe:1"]

    def run(self):
        while True:
            log.info("%s listening on %s", self.protocol, self.url)
            started = time.monotonic()
            try:
                source = subprocess.Popen(self.relay, stdout=subprocess.PIPE) if self.relay else None
                self.process = subprocess.Popen(self.command(), stdout=subprocess.PIPE,
                                                stdin=source.stdout if source else None)
                if source:
                    source.stdout.close()
            except OSError:
                log.exception("could not start ffmpeg")
                time.sleep(5)
                continue
            leftover = b""
            while True:
                data = self.process.stdout.read1(BYTES_PER_READ)
                if not data:
                    break
                data = leftover + data
                usable = len(data) - len(data) % 4
                leftover = data[usable:]
                if usable:
                    self.last_audio = time.monotonic()
                    self.on_audio(np.frombuffer(data[:usable], np.float32).copy())
            code = self.process.wait()
            log.info("%s stream ended (ffmpeg exit %s)", self.protocol, code)
            # A failure before any audio, such as a missing protocol, would otherwise retry constantly.
            time.sleep(0.5 if self.last_audio > started else 5)

    def stop(self):
        if self.process:
            self.process.terminate()


def ffmpeg_has_srt():
    try:
        protocols = subprocess.run([FFMPEG, "-hide_banner", "-protocols"], capture_output=True, text=True).stdout
    except OSError:
        return False
    return "srt" in protocols.split()


def srt_listener(on_audio, port):
    url = f"srt://0.0.0.0:{port}?mode=listener"
    if ffmpeg_has_srt():
        return Listener("srt", url, [], on_audio)
    # Homebrew's ffmpeg has no SRT, so its own tool receives the stream and passes it on.
    if shutil.which("srt-live-transmit"):
        relay = ["srt-live-transmit", "-a:no", "-q", f"srt://:{port}?mode=listener", "file://con"]
        return Listener("srt", url, ["-f", "mpegts"], on_audio, relay)
    log.warning("SRT input is unavailable: ffmpeg has no SRT support and srt-live-transmit is not installed")
    return None


def start_listeners(on_audio, rtmp_port, srt_port):
    listeners = [Listener("rtmp", f"rtmp://0.0.0.0:{rtmp_port}/live/stream", ["-listen", "1"], on_audio),
                 srt_listener(on_audio, srt_port)]
    listeners = [listener for listener in listeners if listener]
    for listener in listeners:
        listener.start()
    return listeners
