#!/usr/bin/env python3
"""Generate TikTok-ready weekly media from already-approved public Nutri-A Wix files.

The stage request is written explicitly to this public callback repository.
All conversions are TikTok-only; original Wix masters are never changed.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path

from PIL import Image, ImageOps

REQUEST = Path("queue/weekly-tiktok-staging-request.json")
OUT = Path("tiktok-media")
PREFIX = "https://nutriawebaruhaz.github.io/nutri-a-tiktok-callback/tiktok-media/"
SIZE = (1080, 1080)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_video(poster: Path, target: Path) -> None:
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-loop", "1", "-framerate", "30", "-i", str(poster),
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-t", "5", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "96k", "-shortest", "-movflags", "+faststart",
        str(target),
    ], check=True, timeout=180)
    probe = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries",
        "stream=codec_type,codec_name,width,height,avg_frame_rate",
        "-of", "json", str(target),
    ], timeout=20)
    streams = json.loads(probe)["streams"]
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not video or video.get("codec_name") != "h264" or (video.get("width"), video.get("height")) != SIZE:
        raise RuntimeError("TIKTOK_VIDEO_FORMAT_CHECK_FAILED")
    if not audio or audio.get("codec_name") != "aac":
        raise RuntimeError("TIKTOK_VIDEO_AUDIO_CHECK_FAILED")


def main() -> None:
    data = json.loads(REQUEST.read_text(encoding="utf-8"))
    week = str(data.get("batchWeek") or "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", week):
        raise RuntimeError("TIKTOK_BATCH_WEEK_INVALID")
    week_start = dt.date.fromisoformat(week)
    entries = data.get("posts") or []
    if not isinstance(entries, list) or not 1 <= len(entries) <= 7:
        raise RuntimeError("TIKTOK_BATCH_STAGE_COUNT_INVALID")
    OUT.mkdir(parents=True, exist_ok=True)
    items = {}
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        for post in entries:
            ref = str(post.get("referenceId") or "")
            route = str(post.get("route") or "").upper()
            raw = str(post.get("scheduledFor") or "")
            if not re.fullmatch(r"[A-Za-z0-9_-]+", ref) or ref in items:
                raise RuntimeError(f"TIKTOK_BAD_OR_DUPLICATE_REFERENCE {ref}")
            when = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if when.tzinfo is None or not week_start <= when.date() <= week_start + dt.timedelta(days=6):
                raise RuntimeError(f"TIKTOK_INVALID_STAGE_SLOT {ref}")
            if route not in {"PHOTO", "VIDEO"}:
                raise RuntimeError(f"TIKTOK_INVALID_ROUTE {ref}")
            url = str(post.get("sourceUrl") or "")
            if not url.startswith("https://static.wixstatic.com/media/f9969b_") or "?" in url:
                raise RuntimeError(f"TIKTOK_UNTRUSTED_WIX_SOURCE_URL {ref}")
            req = urllib.request.Request(url, headers={"User-Agent": "Nutri-A-TikTok-Public-Stage/1.0"})
            with urllib.request.urlopen(req, timeout=70) as response:
                raw_bytes = response.read(19_922_944 + 1)
            if not 0 < len(raw_bytes) <= 19_922_944:
                raise RuntimeError(f"TIKTOK_WIX_SOURCE_BYTE_LIMIT_FAIL {ref}")
            source = tmpdir / (ref + ".jpg")
            source.write_bytes(raw_bytes)
            with Image.open(source) as im:
                im.load()
                if im.size != (3000, 3000):
                    raise RuntimeError(f"TIKTOK_WIX_MASTER_SIZE_MISMATCH {ref} {im.size}")
                rgb = ImageOps.exif_transpose(im).convert("RGB")
                poster = tmpdir / (ref + "-poster.jpg")
                rgb.resize(SIZE, Image.Resampling.LANCZOS).save(
                    poster, format="JPEG", quality=90, optimize=True, progressive=False
                )
            with Image.open(poster) as verify:
                verify.verify()
            if route == "PHOTO":
                filename = ref + "-tiktok-1080.jpg"
                path = OUT / filename
                path.write_bytes(poster.read_bytes())
                transport = "photo"
                mime = "image/jpeg"
                max_bytes = 19_922_944
            else:
                filename = ref + ".mp4"
                path = OUT / filename
                make_video(poster, path)
                transport = "video"
                mime = "video/mp4"
                max_bytes = 1_000_000_000
            if not 0 < path.stat().st_size <= max_bytes:
                raise RuntimeError(f"TIKTOK_OUTPUT_BYTE_LIMIT_FAIL {ref}")
            items[ref] = {transport: {
                "url": PREFIX + filename,
                "sha256": sha256(path),
                "contentType": mime,
            }}
            print(f"TIKTOK_STAGE_ITEM_PASS ref={ref} route={route} bytes={path.stat().st_size}")
    manifest = {
        "schemaVersion": 1,
        "batchWeek": week,
        "status": "STAGED",
        "items": items,
    }
    (OUT / ("weekly-batch-" + week + ".json")).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"TIKTOK_BATCH_MEDIA_STAGE_PASS week={week} count={len(items)}")


if __name__ == "__main__":
    main()
