from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "tools" / "benchmark-review.html").read_text(encoding="utf-8")
CSS = (ROOT / "tools" / "benchmark-review.css").read_text(encoding="utf-8")
JAVASCRIPT = (ROOT / "tools" / "benchmark-review.js").read_text(encoding="utf-8")


def test_review_app_is_local_only_and_has_strict_browser_boundaries() -> None:
    assert (
        "default-src 'none'; script-src 'self'; style-src 'self'; "
        "media-src blob:; connect-src 'none'; object-src 'none'; "
        "base-uri 'none'; form-action 'none'"
    ) in HTML
    assert '<meta name="referrer" content="no-referrer">' in HTML
    assert '<script defer src="benchmark-review.js"></script>' in HTML
    assert '<link rel="stylesheet" href="benchmark-review.css">' in HTML
    assert "innerHTML" not in JAVASCRIPT
    for forbidden in (
        "fetch(",
        "XMLHttpRequest",
        "WebSocket",
        "EventSource",
        "sendBeacon",
        "localStorage",
        "sessionStorage",
        "http://",
        "https://",
    ):
        assert forbidden not in JAVASCRIPT
    assert 'crypto.subtle.digest("SHA-256"' in JAVASCRIPT
    assert "URL.createObjectURL" in JAVASCRIPT
    assert "URL.revokeObjectURL" in JAVASCRIPT


def test_review_app_covers_packet_audio_progress_and_explicit_final_review() -> None:
    for control_id in (
        "packet-file",
        "audio-files",
        "progress-file",
        "fixture-player",
        "word-start",
        "word-end",
        "word-scorable",
        "play-preview",
        "mark-reviewed",
        "export-progress",
        "export-final",
    ):
        assert f'id="{control_id}"' in HTML
        assert f'"{control_id}"' in JAVASCRIPT
    assert "alignment-benchmark-review-packet-v1" in JAVASCRIPT
    assert "alignment-benchmark-review-completion-v1" in JAVASCRIPT
    assert ".filter((entry) => decisions.get(entry.key).reviewed)" in JAVASCRIPT
    assert "reviewed === total" in JAVASCRIPT
    assert "fixtureAudio.size === fixtures.length" in JAVASCRIPT
    assert "acceptedWithoutClipping" in JAVASCRIPT
    assert "Math.abs(measuredDurationMs - fixture.audioDurationMs) > 2000" in JAVASCRIPT


def test_review_app_copy_is_switchable_and_responsive_without_overflow() -> None:
    copy_keys = set(re.findall(r'data-copy="([A-Za-z0-9]+)"', HTML))
    assert copy_keys
    for key in copy_keys:
        assert JAVASCRIPT.count(f"{key}:") == 2
    assert 'data-language="en"' in HTML
    assert 'data-language="es"' in HTML
    assert "document.documentElement.lang = locale" in JAVASCRIPT
    assert "@media (max-width: 48rem)" in CSS
    assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in CSS
    assert "grid-template-columns: minmax(0, 1fr)" in CSS
    assert "min-width: 0" in CSS
    assert "overflow-wrap: anywhere" in CSS
    assert "min-height: 2.75rem" in CSS
    assert "@media (prefers-reduced-motion: reduce)" in CSS
