"""Tests for H2Edu brand identity, logo assets, and template configuration."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from app.platform.branding import (
    BRAND_FAVICON_PATH,
    BRAND_LOGO_128_PATH,
    BRAND_LOGO_256_PATH,
    BRAND_LOGO_MASTER_PATH,
    BRAND_LOGO_TRANSPARENT_128_PATH,
    BRAND_LOGO_TRANSPARENT_PATH,
    BRAND_NAME,
    BRAND_SLOGAN,
)

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "app" / "static"
TEMPLATES_DIR = ROOT / "app" / "templates"


def test_brand_constants_match_h2edu_spec() -> None:
    assert BRAND_NAME == "H2Edu"
    assert BRAND_SLOGAN == "Học thông minh - Thi bứt phá"


def test_logo_and_favicon_assets_exist_with_expected_dimensions_and_transparency() -> None:
    expected = {
        BRAND_LOGO_MASTER_PATH: ((1024, 1024), "RGB"),
        "img/logo.jpg": ((1024, 1024), "RGB"),
        BRAND_LOGO_256_PATH: ((256, 256), "RGB"),
        BRAND_LOGO_128_PATH: ((128, 128), "RGB"),
        BRAND_LOGO_TRANSPARENT_PATH: ((1024, 1024), "RGBA"),
        BRAND_LOGO_TRANSPARENT_128_PATH: ((128, 128), "RGBA"),
        BRAND_FAVICON_PATH: ((64, 64), "RGBA"),
    }
    for rel_path, (dims, mode) in expected.items():
        path = STATIC_DIR / rel_path
        assert path.exists(), f"missing asset {rel_path}"
        with Image.open(path) as im:
            assert im.size == dims, f"{rel_path} size mismatch: {im.size} != {dims}"
            assert im.mode == mode, f"{rel_path} mode mismatch: {im.mode} != {mode}"
            if mode == "RGBA":
                assert im.getpixel((0, 0))[3] == 0, f"{rel_path} corner should be transparent"


def test_templates_use_h2edu_and_no_legacy_exam_system_brand() -> None:
    base_html = (TEMPLATES_DIR / "base.html").read_text(encoding="utf-8")
    index_html = (TEMPLATES_DIR / "index.html").read_text(encoding="utf-8")
    assert "BRAND_NAME" in base_html
    assert "BRAND_SLOGAN" in base_html
    assert "Học thông minh - Thi bứt phá" in index_html

    for tpl in TEMPLATES_DIR.rglob("*.html"):
        text = tpl.read_text(encoding="utf-8")
        assert "Exam System" not in text, f"stale brand string in {tpl.relative_to(ROOT)}"
