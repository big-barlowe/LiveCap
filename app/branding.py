import hashlib
import html
import json
import logging
import os
import re
import tempfile
from pathlib import Path

log = logging.getLogger("livecap.branding")

MAX_LOGO_BYTES = 5 << 20
MAX_HEADING = 80
DEFAULT_HEADING = "Live Captions"
APPEARANCES = ("auto", "light", "dark")
COLORS = ("accent", "header", "background")
DEFAULTS = {"heading": "", "appearance": "auto", "accent": "", "header": "", "background": ""}
LOGO_TYPES = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp", "gif": "image/gif", "svg": "image/svg+xml"}

# An uploaded SVG opened directly would run any script inside it on this site.
LOGO_HEADERS = {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "public, max-age=31536000, immutable"}

# The defaults from style.css, which custom colors are worked out against.
PALETTES = {
    "light": {"background": "#f7f7f4", "header": "#ffffff", "accent": "#0b6e4f",
              "warn_bg": "#fdf1d7", "warn_text": "#5d4300", "error": "#b3261e"},
    "dark": {"background": "#101214", "header": "#191c20", "accent": "#3ecf9c",
             "warn_bg": "#3a2e12", "warn_text": "#f7dd9d", "error": "#ff8a80"},
}
DARK_TEXT, LIGHT_TEXT = "#16181c", "#f2f3f1"
READABLE = 4.5  # WCAG AA contrast for body text
VISIBLE = 1.5  # below this, a colored button all but disappears into what is behind it
ROOT = ':root, :root:not([data-theme="light"])'  # matches the specificity of style.css's dark rule, and comes later


def _channels(color):
    return [int(color[i:i + 2], 16) for i in (1, 3, 5)]


def luminance(color):
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in (v / 255 for v in _channels(color))]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a, b):
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def mix(a, b, amount):
    """a moved towards b by amount, from 0 to 1."""
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(_channels(a), _channels(b)))


def text_on(color):
    """Black and white between them always reach READABLE, so any color can be read on."""
    soft = max((DARK_TEXT, LIGHT_TEXT), key=lambda text: contrast(text, color))
    return soft if contrast(soft, color) >= READABLE else max(("#000000", "#ffffff"), key=lambda text: contrast(text, color))


def _is_dark(color):
    return contrast("#000000", color) < contrast("#ffffff", color)


def _surface(background, accent, palette):
    """The colors for content sitting on this background."""
    text = text_on(background)
    muted = next(m for m in (mix(text, background, amount) for amount in (0.4, 0.3, 0.2, 0.1, 0)) if contrast(m, background) >= READABLE)
    shown_accent = accent if contrast(accent, background) >= VISIBLE else text
    return {
        "--text": text,
        "--muted": muted,
        "--line": mix(text, background, 0.82),
        "--accent": shown_accent,
        "--accent-text": text_on(shown_accent),
        "--warn-bg": palette["warn_bg"],
        "--warn-text": palette["warn_text"],
        "--error": palette["error"],
        "color-scheme": "dark" if _is_dark(background) else "light",
    }


def _block(selector, declarations):
    return f"{selector} {{ " + " ".join(f"{name}: {value};" for name, value in declarations.items()) + " }\n"


def _scheme_css(settings, scheme):
    defaults = PALETTES[scheme]
    background = settings["background"] or defaults["background"]
    header = settings["header"] or defaults["header"]
    accent = settings["accent"] or defaults["accent"]
    # Warnings and errors follow whichever background they end up on, not the viewer's device.
    palette = PALETTES["dark" if _is_dark(background) else "light"]
    page = _surface(background, accent, palette) | {"--bg": background}
    page["--panel"] = mix(background, page["--text"], 0.04)
    bar = _surface(header, accent, palette) | {"--panel": header}
    bar["--bg"] = mix(header, bar["--text"], 0.08)  # the controls inside the bar
    bar["color"] = "var(--text)"
    return _block(ROOT, page) + _block(".bar, .status", bar)


def theme_css(settings):
    """Added after style.css. Empty until something is customized, which leaves the built-in look alone."""
    if settings["appearance"] != "auto":
        return _scheme_css(settings, settings["appearance"])
    if not any(settings[key] for key in COLORS):
        return ""
    return _scheme_css(settings, "light") + "@media (prefers-color-scheme: dark) {\n" + _scheme_css(settings, "dark") + "}\n"


def validated(values):
    """Settings from untrusted input. Raises ValueError with a message for the person editing them."""
    if not isinstance(values, dict):
        raise ValueError("Theme settings were not understood.")
    heading = values.get("heading", "")
    if not isinstance(heading, str):
        raise ValueError("The heading must be text.")
    heading = " ".join(heading.split())
    if len(heading) > MAX_HEADING:
        raise ValueError(f"The heading can be at most {MAX_HEADING} characters.")
    appearance = values.get("appearance", "auto")
    if appearance not in APPEARANCES:
        raise ValueError("Appearance must be automatic, light or dark.")
    settings = {"heading": heading, "appearance": appearance}
    for key in COLORS:
        color = values.get(key, "")
        if color and not (isinstance(color, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", color)):
            raise ValueError(f"The {key} color must look like #1a2b3c.")
        settings[key] = color.lower() if color else ""
    return settings


def logo_type(data):
    """The file type from the contents, because a file's name and stated type can say anything."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    # Design tools can put a long comment before the <svg> tag.
    head = data[:65536].removeprefix(b"\xef\xbb\xbf").lstrip()
    if head.startswith(b"<") and re.search(rb"<svg[\s>]", head):
        return "svg"
    return None


class Branding:
    """The logo and theme chosen on the admin page, kept in the data folder so they outlast restarts and app updates."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.settings = self._load_settings()
        self.logo_path = next((path for kind in LOGO_TYPES if (path := self.folder / f"logo.{kind}").exists()), None)
        self.logo_url = self._versioned_logo(self.logo_path.read_bytes()) if self.logo_path else None

    @staticmethod
    def _versioned_logo(data):
        # Versioned addresses let browsers keep these for good, yet see a change at once.
        return "/brand/logo?v=" + hashlib.sha256(data).hexdigest()[:12]

    def _load_settings(self):
        path = self.folder / "theme.json"
        try:
            return validated(json.loads(path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return dict(DEFAULTS)
        except (OSError, ValueError) as failure:
            log.warning("ignoring %s: %s", path, failure)
            return dict(DEFAULTS)

    def _write(self, name, data):
        self.folder.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.folder, prefix=name + ".")
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(data)
            os.replace(tmp, self.folder / name)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def save_settings(self, values):
        settings = validated(values)
        self._write("theme.json", json.dumps(settings, indent=2).encode())
        self.settings = settings

    def save_logo(self, data):
        kind = logo_type(data)
        if kind is None:
            raise ValueError("The logo must be a PNG, JPEG, WebP, GIF or SVG image.")
        name = f"logo.{kind}"
        self._write(name, data)
        if self.logo_path and self.logo_path.name != name:
            self.logo_path.unlink(missing_ok=True)
        self.logo_path = self.folder / name
        self.logo_url = self._versioned_logo(data)

    def remove_logo(self):
        if self.logo_path:
            self.logo_path.unlink(missing_ok=True)
        self.logo_path = self.logo_url = None

    @property
    def heading(self):
        return self.settings["heading"] or DEFAULT_HEADING

    @property
    def logo_media_type(self):
        return LOGO_TYPES[self.logo_path.suffix[1:]]

    @property
    def css_url(self):
        return "/brand/theme.css?v=" + hashlib.sha256(theme_css(self.settings).encode()).hexdigest()[:12]

    def message(self):
        """Sent to every page on connecting and after each change."""
        return {"type": "theme", "settings": self.settings, "defaultHeading": DEFAULT_HEADING,
                "heading": self.heading, "logo": self.logo_url, "css": self.css_url}

    def render_page(self, path):
        logo = self.logo_url
        return (Path(path).read_text(encoding="utf-8")
                .replace("__HEADING__", html.escape(self.heading))
                .replace("__THEME_CSS__", self.css_url)
                .replace("__LOGO__", f'src="{logo}"' if logo else "hidden"))
