"""Capture the screenshots and the trace JSON behind docs/showcase/compound-query-trace.md.

The showcase page walks through one compound question's trace in the Trace Explorer
(`/tech/traces/<query_id>`). This script makes that page reproducible: it opens the
trace in a headless browser, saves one PNG per panel the page discusses, and saves the
trace itself (header + event stream, as the BFF serves it) so the page stays checkable
after the audit schema prunes the live trace.

Needs the UI (Vite dev server) and the BFF running locally, and Playwright with
Chromium in the Python environment.

Usage:
    python scripts/docs/capture_trace_showcase.py
    python scripts/docs/capture_trace_showcase.py --base-url http://localhost:5173 \
        --query-id 86b9693aedc644f9
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path

from playwright.sync_api import FloatRect, Locator, Page, ViewportSize, sync_playwright

REPO_ROOT = Path(__file__).resolve().parents[2]
IMAGE_DIR = REPO_ROOT / "docs" / "images" / "trace-showcase"
TRACE_DIR = REPO_ROOT / "docs" / "showcase"

DEFAULT_QUERY_ID = "86b9693aedc644f9"
DEFAULT_BASE_URL = "http://localhost:5173"

# A tall viewport keeps every panel on screen at once, so the app's inner scroll
# container never clips an element screenshot. At 1180 px the page uses its
# one-column layout (index.css), so the answer panel gets the full content width
# instead of a 460 px side column.
VIEWPORT: ViewportSize = {"width": 1180, "height": 9000}

# Event-stream rows expanded for the detail shot: the rewritten dependent sub-query,
# its branch decision, and one computed ratio with its bindings and source fact ids.
DETAIL_EVENT_SEQS = (13, 16, 22)
# LLM calls whose recorded response is shown: the decomposer's plan and one verifier.
RESPONSE_CALL_SEQS = (1, 34)


def card(page: Page, title: str) -> Locator:
    """Returns the `.card` whose heading starts with `title`."""
    heading = page.locator(".card > h3", has_text=title).first
    return heading.locator("xpath=..")


def row_with_seq(container: Locator, seq: int) -> Locator:
    """Returns the expandable row (a nested `.card`) whose first cell is `seq`."""
    seq_cell = container.page.locator(f"span.mono:text-is('{seq}')")
    return container.locator(".card", has=seq_cell).first


def save(locator: Locator, name: str) -> None:
    """Saves an element screenshot as docs/images/trace-showcase/<name>.png."""
    path = IMAGE_DIR / f"{name}.png"
    locator.scroll_into_view_if_needed()
    locator.screenshot(path=str(path), animations="disabled")
    print(f"wrote {path.relative_to(REPO_ROOT)}")


def save_union(page: Page, locators: list[Locator], name: str) -> None:
    """Saves one screenshot of the box enclosing all `locators`."""
    boxes: list[FloatRect] = []
    for loc in locators:
        box = loc.bounding_box()
        if box is None:
            raise RuntimeError(f"{name}: an element is not visible")
        boxes.append(box)
    x0 = min(b["x"] for b in boxes)
    y0 = min(b["y"] for b in boxes)
    x1 = max(b["x"] + b["width"] for b in boxes)
    y1 = max(b["y"] + b["height"] for b in boxes)
    pad = 12
    clip: FloatRect = {
        "x": x0 - pad,
        "y": y0 - pad,
        "width": x1 - x0 + 2 * pad,
        "height": y1 - y0 + 2 * pad,
    }
    path = IMAGE_DIR / f"{name}.png"
    page.screenshot(path=str(path), clip=clip, full_page=True, animations="disabled")
    print(f"wrote {path.relative_to(REPO_ROOT)}")


def capture(base_url: str, query_id: str) -> None:
    """Writes every showcase screenshot for `query_id`."""
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        # Pin the light theme (the UI's default) so a saved preference can't change the images.
        context = browser.new_context(viewport=VIEWPORT, device_scale_factor=1)
        context.add_init_script("try { localStorage.setItem('arag:theme', 'light'); } catch (e) {}")
        page = context.new_page()
        page.goto(f"{base_url}/tech/traces/{query_id}")
        page.locator(".card > h3", has_text="Event stream (").wait_for()
        page.wait_for_load_state("networkidle")

        # 1. The question, the headline tiles and the stage waterfall.
        question = page.locator(".card", has=page.locator(".badge", has_text="replay on map")).first
        waterfall = card(page, "Stage waterfall")
        save_union(page, [question, waterfall], "01-overview")

        # 2-5. The final answer panel, one card at a time.
        save(card(page, "Answer"), "02-answer")
        save(card(page, "Supporting statements"), "03-supporting-statements")
        save(card(page, "Sources"), "04-sources")
        save(card(page, "How it was answered"), "05-how-it-was-answered")

        # 6-7. Entity/period resolution, and the per-call LLM ledger.
        save(card(page, "Resolution & fallbacks"), "06-resolution")
        llm = card(page, "LLM calls")
        save(llm, "07-llm-calls")

        # 8. The whole event stream, collapsed: one line per event.
        stream = card(page, "Event stream (")
        save(stream, "08-event-stream")

        # 9. Selected events expanded.
        rows = []
        for seq in DETAIL_EVENT_SEQS:
            row = row_with_seq(stream, seq)
            row.locator("> div").first.click()
            rows.append(row)
        save_union(page, rows, "09-event-details")
        for row in rows:
            row.locator("> div").first.click()

        # 10-11. The decomposer's and a verifier's recorded responses.
        names = ("10-decomposer-response", "11-verifier-response")
        for seq, name in zip(RESPONSE_CALL_SEQS, names, strict=True):
            row = row_with_seq(llm, seq)
            row.locator("> div").first.click()
            response = row.locator("pre.prompt-pre").last
            header = row.locator("> div").first
            save_union(page, [header, response], name)
            header.click()

        browser.close()


def export_trace(base_url: str, query_id: str) -> None:
    """Saves the trace (header + events) exactly as the BFF serves it."""
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    url = f"{base_url}/api/traces/{query_id}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        trace = json.load(resp)
    path = TRACE_DIR / f"trace-{query_id}.json"
    path.write_text(json.dumps(trace, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {path.relative_to(REPO_ROOT)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--base-url", default=DEFAULT_BASE_URL, help="UI origin (proxies /api to the BFF)"
    )
    parser.add_argument("--query-id", default=DEFAULT_QUERY_ID, help="trace to capture")
    parser.add_argument("--skip-json", action="store_true", help="screenshots only")
    args = parser.parse_args()
    capture(args.base_url, args.query_id)
    if not args.skip_json:
        export_trace(args.base_url, args.query_id)


if __name__ == "__main__":
    main()
