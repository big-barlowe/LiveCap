import json

import pytest

from app.branding import DEFAULT_HEADING, DEFAULTS, MAX_HEADING, READABLE, Branding, contrast, logo_type, text_on, theme_css, validated

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 20
SVG = b'<?xml version="1.0"?><!-- made with a design tool --><svg xmlns="http://www.w3.org/2000/svg"></svg>'


def settings(**values):
    return DEFAULTS | values


def variable(css, selector, name):
    block = next(line for line in css.splitlines() if line.startswith(selector))
    return block.split(f"{name}: ")[1].split(";")[0]


@pytest.mark.parametrize("color", ["#000000", "#ffffff", "#777777", "#808080", "#ffcc00", "#0000ff", "#7a1f2b", "#3b5998"])
def test_text_is_readable_on_any_color(color):
    assert contrast(text_on(color), color) >= READABLE


def test_built_in_look_is_untouched_until_something_is_customized():
    assert theme_css(settings()) == ""


def test_captions_and_muted_text_stay_readable_on_a_chosen_background():
    css = theme_css(settings(background="#7a1f2b", appearance="light"))
    assert contrast(variable(css, ":root", "--text"), "#7a1f2b") >= READABLE
    assert contrast(variable(css, ":root", "--muted"), "#7a1f2b") >= READABLE


def test_bar_text_follows_the_header_color_not_the_page():
    css = theme_css(settings(header="#1b2a57", background="#ffffff", appearance="light"))
    assert luminance_is_light(variable(css, ".bar", "--text"))
    assert not luminance_is_light(variable(css, ":root", "--text"))


def luminance_is_light(color):
    return contrast(color, "#000000") > contrast(color, "#ffffff")


def test_button_stays_visible_when_its_color_matches_the_header():
    css = theme_css(settings(header="#1b2a57", accent="#1b2a57", appearance="light"))
    assert variable(css, ".bar", "--accent") == variable(css, ".bar", "--text")
    assert contrast(variable(css, ".bar", "--accent-text"), variable(css, ".bar", "--accent")) >= READABLE


def test_a_light_brand_color_is_kept_with_dark_text_on_it():
    css = theme_css(settings(accent="#c9a227", appearance="light"))
    assert variable(css, ".bar", "--accent") == "#c9a227"
    assert contrast(variable(css, ".bar", "--accent-text"), "#c9a227") >= READABLE


def test_automatic_appearance_covers_both_light_and_dark_devices():
    css = theme_css(settings(accent="#c9a227"))
    assert "@media (prefers-color-scheme: dark)" in css


@pytest.mark.parametrize("values", [
    {"accent": "red"},
    {"header": "#12345"},
    {"background": "#123456; } body { display: none"},
    {"accent": 7},
    {"appearance": "neon"},
    {"heading": "x" * (MAX_HEADING + 1)},
    {"heading": 7},
    [],
])
def test_bad_settings_are_refused(values):
    with pytest.raises(ValueError):
        validated(values)


def test_settings_are_tidied():
    assert validated({"heading": "  Grace \n Church ", "accent": "#ABCDEF"}) == settings(heading="Grace Church", accent="#abcdef")


@pytest.mark.parametrize("data, kind", [
    (PNG, "png"),
    (b"\xff\xd8\xff\xe0rest", "jpg"),
    (b"RIFF\0\0\0\0WEBPVP8 ", "webp"),
    (b"GIF89a", "gif"),
    (SVG, "svg"),
    (b"\xef\xbb\xbf<svg></svg>", "svg"),
    (b"<html><script>alert(1)</script></html>", None),
    (b"not an image", None),
    (b"", None),
])
def test_logo_type_comes_from_the_contents(data, kind):
    assert logo_type(data) == kind


def test_everything_survives_a_restart(tmp_path):
    first = Branding(tmp_path)
    first.save_settings({"heading": "Grace Church", "accent": "#7a1f2b"})
    first.save_logo(PNG)
    again = Branding(tmp_path)
    assert again.heading == "Grace Church"
    assert again.settings["accent"] == "#7a1f2b"
    assert again.logo_path.read_bytes() == PNG
    assert again.logo_url == first.logo_url
    assert again.css_url == first.css_url


def test_a_new_logo_replaces_one_of_another_type(tmp_path):
    branding = Branding(tmp_path)
    branding.save_logo(PNG)
    png_url = branding.logo_url
    branding.save_logo(SVG)
    assert not (tmp_path / "logo.png").exists()
    assert branding.logo_media_type == "image/svg+xml"
    assert branding.logo_url != png_url


def test_a_refused_logo_leaves_the_old_one(tmp_path):
    branding = Branding(tmp_path)
    branding.save_logo(PNG)
    with pytest.raises(ValueError):
        branding.save_logo(b"not an image")
    assert branding.logo_path.read_bytes() == PNG


def test_removing_the_logo_lasts(tmp_path):
    branding = Branding(tmp_path)
    branding.save_logo(PNG)
    branding.remove_logo()
    assert branding.logo_url is None
    assert Branding(tmp_path).logo_path is None


def test_damaged_settings_fall_back_to_the_defaults(tmp_path):
    (tmp_path / "theme.json").write_text("{not json")
    assert Branding(tmp_path).settings == DEFAULTS
    (tmp_path / "theme.json").write_text(json.dumps({"accent": "url(evil)"}))
    assert Branding(tmp_path).settings == DEFAULTS


def test_pages_get_the_heading_escaped(tmp_path):
    page = tmp_path / "page.html"
    page.write_text('<title>__HEADING__</title><link href="__THEME_CSS__"><img __LOGO__>')
    branding = Branding(tmp_path / "branding")
    assert f"<title>{DEFAULT_HEADING}</title>" in branding.render_page(page)
    assert "<img hidden>" in branding.render_page(page)
    branding.save_settings({"heading": "<script>St. Mary & John</script>"})
    branding.save_logo(PNG)
    rendered = branding.render_page(page)
    assert "<script>" not in rendered
    assert "&lt;script&gt;St. Mary &amp; John" in rendered
    assert f'<img src="{branding.logo_url}">' in rendered
