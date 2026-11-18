"""
Generic demo recorder. Reads a page manifest and executes its steps.

Usage:
    python tools/record_demo.py --page crm/opportunities
    python tools/record_demo.py --page crm/quotes --lang fa
    python tools/record_demo.py --all                 (records every manifest under demos/)

The recorded .webm lands in  demo_videos/<page-slug>.webm
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
DEMOS_DIR = ROOT / "demos"
RAW_VIDEO_DIR = ROOT / "demo_videos"


# ---------------------------------------------------------------------------
# Step normalizer — accepts BOTH manifest formats:
#   * Generic:     {"action": "goto", "url": "..."}
#   * Shorthand:   {"goto": "..."}   /  {"click": "..."}  /  {"wait": 800}
# ---------------------------------------------------------------------------
def normalize_step(step):
    """
    Return a dict with a guaranteed 'action' key, mapping shorthand forms
    to the generic action vocabulary used by execute_steps().
    """
    if "action" in step:
        return step

    if "goto" in step:
        return {"action": "goto", "url": step["goto"], "ms": step.get("ms", 800)}

    if "click" in step:
        return {"action": "click", "selector": step["click"],
                "timeout_ms": step.get("timeout_ms", 8000)}

    if "hover" in step:
        return {"action": "hover", "selector": step["hover"],
                "timeout_ms": step.get("timeout_ms", 8000)}

    if "wait" in step:
        return {"action": "pause", "ms": step["wait"]}

    raise ValueError(f"Unrecognized step: {step!r}")


# ---------------------------------------------------------------------------
# Step executor
# ---------------------------------------------------------------------------
def execute_steps(page, steps):
    for raw_step in steps:
        step = normalize_step(raw_step)
        action = step["action"]

        if action == "pause":
            page.wait_for_timeout(step.get("ms", 1000))

        elif action == "goto":
            page.goto(step["url"])
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(step.get("ms", 800))

        elif action == "click":
            page.click(step["selector"], timeout=step.get("timeout_ms", 8000))

        elif action == "hover":
            page.hover(step["selector"], timeout=step.get("timeout_ms", 8000))

        elif action == "wait_for":
            page.wait_for_selector(
                step["selector"],
                state="visible",
                timeout=step.get("timeout_ms", 8000),
            )

        elif action == "wait_for_hidden":
            # Non-fatal: if the save fails (or the modal never closes), we
            # still want the recording to finish and be saved so we can
            # inspect the video afterwards.
            try:
                page.wait_for_selector(
                    step["selector"],
                    state="hidden",
                    timeout=step.get("timeout_ms", 10000),
                )
            except Exception as e:
                print(f"  ⚠️  wait_for_hidden failed for {step['selector']}: {e}")
                print("       The video will still be saved — check the recording.")
                page.wait_for_timeout(1500)

        elif action == "wait_for_option":
            page.wait_for_function(
                f"""() => {{
                    const s = document.querySelector('{step["selector"]}');
                    return s && s.options.length >= {step.get("min_options", 2)};
                }}""",
                timeout=step.get("timeout_ms", 10000),
            )

        elif action == "type":
            sel = step["selector"]
            val = step["value"]
            delay = step.get("delay_ms", 55)
            # Optional pre-pause: useful when the Farsi narration needs a beat
            # before the Persian field starts filling.
            if step.get("pause_before_ms"):
                page.wait_for_timeout(step["pause_before_ms"])
            page.click(sel)                     # focus → caret visible in video
            page.wait_for_timeout(200)
            page.fill(sel, "")                  # clear
            page.type(sel, val, delay=delay)    # type char-by-char
            page.wait_for_timeout(step.get("pause_ms", 1500))

        elif action == "select_by_label":
            page.click(step["selector"])
            page.wait_for_timeout(300)
            page.select_option(step["selector"], label=step["label"])
            page.wait_for_timeout(step.get("pause_ms", 1200))

        elif action == "select_by_value":
            page.click(step["selector"])
            page.wait_for_timeout(300)
            page.select_option(step["selector"], value=step["value"])
            page.wait_for_timeout(step.get("pause_ms", 1200))

        elif action == "select_first_nonempty":
            page.click(step["selector"])
            page.wait_for_timeout(300)
            first_val = page.eval_on_selector(
                step["selector"],
                "sel => { const o = Array.from(sel.options).find(x => x.value !== ''); return o ? o.value : null; }",
            )
            if not first_val:
                raise RuntimeError(f"No options in {step['selector']} — seed data first.")
            page.select_option(step["selector"], value=first_val)
            page.wait_for_timeout(step.get("pause_ms", 1200))

        else:
            raise ValueError(f"Unknown action: {action}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def record_one(page_dir: Path, lang: str):
    manifest = json.loads((page_dir / "manifest.json").read_text(encoding="utf-8"))
    slug = manifest["page_id"].replace("/", "__")
    out_webm = RAW_VIDEO_DIR / f"{slug}.webm"

    print(f"\n▶ Recording  {manifest['page_id']}  ({lang})")
    print(f"  URL: {manifest['url']}")
    print(f"  Output will be: {out_webm}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            slow_mo=manifest.get("recording", {}).get("slow_mo_ms", 120),
            args=["--start-maximized"],
        )
        ctx = browser.new_context(
            record_video_dir=str(RAW_VIDEO_DIR),
            record_video_size=manifest.get("viewport", {"width": 1280, "height": 720}),
            viewport=manifest.get("viewport", {"width": 1280, "height": 720}),
        )
        page = ctx.new_page()
        page.goto(manifest["url"])

        # Manual login hook
        print("\n  Browser is open. Log in / navigate. Then press ENTER here.")
        input("  >>> Press ENTER to start recording... ")

        # ------------------------------------------------------------------
        # 1. Navigate to the real page and settle.
        #    The second goto (after the manual login step) is race-tolerant:
        #    we wait only for DOMContentLoaded (not 'load', not 'networkidle')
        #    because the Flask dev server can abort lazy-loaded assets and
        #    cause ERR_ABORTED on those waits. Everything is wrapped so the
        #    recording proceeds even if the page is momentarily unstable.
        # ------------------------------------------------------------------
        try:
            page.goto(manifest["url"], wait_until="domcontentloaded", timeout=15000)
        except Exception as e:
            print(f"  ⚠️  goto warning (non-fatal): {e}")
            print("       Continuing — the page is probably still loaded.")
        page.wait_for_timeout(manifest.get("recording", {}).get("post_load_pause_ms", 2500))
        try:
            page.evaluate("window.scrollTo(0, 0)")
        except Exception:
            pass
        page.wait_for_timeout(800)

        # ------------------------------------------------------------------
        # 2. Manifest pre-navigation (e.g. switch language to Farsi).
        #    These steps are executed BEFORE the main steps, and the final
        #    goto re-renders the real URL in whatever language was left
        #    active — guaranteeing the demo begins in the intended language.
        # ------------------------------------------------------------------
        pre_nav = manifest.get("pre_nav", [])
        if pre_nav:
            print(f"  Running {len(pre_nav)} pre_nav step(s)...")
            execute_steps(page, pre_nav)
            # Re-goto the real URL so it's rendered in the language the
            # narration uses. Same race-tolerant approach as above.
            try:
                page.goto(manifest["url"], wait_until="domcontentloaded", timeout=15000)
            except Exception as e:
                print(f"  ⚠️  pre_nav re-goto warning (non-fatal): {e}")
            page.wait_for_timeout(manifest.get("recording", {}).get("post_load_pause_ms", 2500))

        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(800)

        # Run recorded steps
        execute_steps(page, manifest["steps"])

        # Extra end-of-video breath
        page.wait_for_timeout(manifest.get("recording", {}).get("final_pause_ms", 4000))

        video_tmp = page.video.path()
        ctx.close()
        browser.close()

    if video_tmp and Path(video_tmp).exists():
        shutil.move(str(video_tmp), str(out_webm))
        print(f"  ✅ Video: {out_webm}")
        return out_webm
    else:
        print("  ⚠️  Could not locate the recorded .webm")
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", help="Page ID, e.g. crm/opportunities")
    ap.add_argument("--lang", default="fa", choices=["fa", "en"])
    ap.add_argument("--all", action="store_true", help="Record every manifest under demos/")
    args = ap.parse_args()

    RAW_VIDEO_DIR.mkdir(exist_ok=True)

    targets = []
    if args.all:
        targets = sorted(DEMOS_DIR.rglob("manifest.json"))
    elif args.page:
        targets = [DEMOS_DIR / args.page / "manifest.json"]
    else:
        ap.error("Pass --page <id> or --all")

    for m in targets:
        if not m.exists():
            print(f"⚠️  Missing: {m}")
            continue
        record_one(m.parent, args.lang)


if __name__ == "__main__":
    main()