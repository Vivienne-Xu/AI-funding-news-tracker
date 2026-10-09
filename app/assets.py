"""Turns the monthly HTML report into a PDF and two chart pictures, using a headless browser (Playwright)."""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("app.assets")

CARD_IDS = {"layers": "#card-layers", "global": "#card-global"}
VIEWPORT = {"width": 1100, "height": 900}
READY_TIMEOUT_MS = 30_000  # charts load Chart.js from the internet, so allow time


def render_assets(html_path: Path) -> tuple[bytes | None, dict[str, bytes], str]:
    """Returns (pdf bytes, {chart name: png bytes}, note). On any problem: (None, {}, reason), never an exception."""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(viewport=VIEWPORT, device_scale_factor=2)
                page.goto(html_path.resolve().as_uri())
                page.wait_for_function("window.__chartsReady === true", timeout=READY_TIMEOUT_MS)
                charts = {name: page.locator(selector).screenshot() for name, selector in CARD_IDS.items()}
                pdf = page.pdf(format="A4", print_background=True, margin={"top": "14mm", "bottom": "14mm", "left": "10mm", "right": "10mm"})
            finally:
                browser.close()
        return pdf, charts, ""
    except Exception as exc:  # noqa: BLE001 - a missing browser or no internet must not stop the report
        reason = f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"
        if "Executable doesn't exist" in str(exc):
            reason = "the headless browser is not installed (run: playwright install chromium)"
        log.warning("PDF and chart pictures could not be created: %s", reason)
        return None, {}, reason
