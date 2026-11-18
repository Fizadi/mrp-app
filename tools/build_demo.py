"""
Build the final MP4 for a page:
  1. edge-tts  narration.{lang}.txt  →  demo.{lang}.mp3
  2. edge-tts  --write-subtitles     →  demo.{lang}.srt (auto, from same text)
  3. ffmpeg    demo_videos/<slug>.webm + demo.{lang}.mp3  →  final/<slug>.<lang>.mp4

Usage:
    python tools/build_demo.py --page crm/opportunities --lang fa
    python tools/build_demo.py --page crm/opportunities --lang en
    python tools/build_demo.py --all --lang fa
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMOS_DIR = ROOT / "demos"
RAW_VIDEO_DIR = ROOT / "demo_videos"


def sh(cmd, **kw):
    print("  $", " ".join(str(c) for c in cmd))
    r = subprocess.run(cmd, **kw)
    if r.returncode != 0:
        raise RuntimeError(f"Command failed: {cmd}")


def build_one(page_dir: Path, lang: str):
    manifest = json.loads((page_dir / "manifest.json").read_text(encoding="utf-8"))
    slug = manifest["page_id"].replace("/", "__")

    raw_webm = RAW_VIDEO_DIR / f"{slug}.webm"
    if not raw_webm.exists():
        print(f"⚠️  Missing webm: {raw_webm}  (record first)")
        return

    narration_txt = page_dir / f"narration.{lang}.txt"
    if not narration_txt.exists():
        print(f"⚠️  Missing narration: {narration_txt}")
        return

    mp3       = page_dir / f"demo.{lang}.mp3"
    srt       = page_dir / f"demo.{lang}.srt"
    final_dir = page_dir / "final"
    final_dir.mkdir(exist_ok=True)
    final_mp4 = final_dir / f"{slug.split('__')[-1]}.{lang}.mp4"

    voice_cfg = manifest.get("audio", {}).get(lang, {})
    voice = voice_cfg.get("voice", "fa-IR-FaridNeural")
    rate  = voice_cfg.get("rate", "+0%")
    vol   = voice_cfg.get("volume", "+0%")

    print(f"\n▶ Building  {manifest['page_id']}  [{lang}]")

    # --- 1. TTS ---
    if not mp3.exists() or mp3.stat().st_mtime < narration_txt.stat().st_mtime:
        print("  [1/3] Generating speech…")
        sh([
            "edge-tts",
            "--voice", voice,
            "--rate", rate,
            "--volume", vol,
            "--file", str(narration_txt),
            "--write-media", str(mp3),
            "--write-subtitles", str(srt),
        ])
    else:
        print("  [1/3] Speech already up to date.")

    # --- 2. Duration check (warn, don't block) ---
    print("  [2/3] Checking durations…")
    def dur(path):
        out = subprocess.check_output([
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=nk=1:nw=1", str(path),
        ])
        return float(out.strip())

    v_dur = dur(raw_webm)
    a_dur = dur(mp3)
    print(f"        video: {v_dur:.1f}s   audio: {a_dur:.1f}s")
    if abs(v_dur - a_dur) > 8:
        print(f"        ⚠️  Difference >8 s — consider adjusting pauses or TTS rate.")

    # --- 3. Mux ---
    print(f"  [3/3] Muxing → {final_mp4.name}")
    ff = manifest.get("ffmpeg", {})
    sh([
        "ffmpeg", "-y",
        "-i", str(raw_webm),
        "-i", str(mp3),
        "-c:v", "libx264",
        "-preset", ff.get("preset", "slow"),
        "-crf", str(ff.get("crf", 20)),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", ff.get("audio_bitrate", "128k"),
        "-shortest",
        "-movflags", "+faststart",
        str(final_mp4),
    ])
    print(f"  ✅ {final_mp4}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--page")
    ap.add_argument("--lang", default="fa", choices=["fa", "en"])
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    if args.all:
        targets = sorted(DEMOS_DIR.rglob("manifest.json"))
    elif args.page:
        targets = [DEMOS_DIR / args.page / "manifest.json"]
    else:
        ap.error("Pass --page <id> or --all")

    for m in targets:
        if m.exists():
            try:
                build_one(m.parent, args.lang)
            except Exception as e:
                print(f"  ❌ {m.parent.name}: {e}")


if __name__ == "__main__":
    main()