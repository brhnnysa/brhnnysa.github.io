#!/usr/bin/env python3
"""Refresh the public Instagram cards used by the BRHN NYSA website.

Uses EdgeAIG/socialfetch's anonymous public Instagram reader. No Instagram
login, cookies, API key, or paid service is used. If Instagram blocks the
scheduled runner, the previous good JSON file is kept unchanged.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "social-posts.json"
SOCIALFETCH = Path(os.environ.get("SOCIALFETCH_BIN", "socialfetch"))
HANDLE = os.environ.get("INSTAGRAM_HANDLE", "heisbrhn")


def main() -> int:
    try:
        proc = subprocess.run(
            [str(SOCIALFETCH), "ig", "user", HANDLE, "--limit", "5", "--json"],
            check=False, capture_output=True, text=True, timeout=90,
        )
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "SocialFetch failed").strip()[-1200:])
        result = json.loads(proc.stdout)
        if not result.get("ok"):
            raise RuntimeError(result.get("error") or "Instagram returned an unsuccessful result")

        profile = result.get("data") or {}
        recent = profile.get("recent") or []
        posts = []
        for item in recent[:5]:
            media = item.get("media") or []
            image = media[0] if media else None
            url = item.get("url")
            if not url:
                code = item.get("shortcode")
                url = f"https://www.instagram.com/p/{code}/" if code else None
            if not url:
                continue
            posts.append({
                "id": str(item.get("id") or item.get("shortcode") or url),
                "platform": "Instagram",
                "author": profile.get("username") or HANDLE,
                "caption": (item.get("caption") or "").strip(),
                "url": url,
                "media_url": image,
                "media_type": item.get("type") or "image",
                "published_at": item.get("taken_at"),
                "likes": item.get("likes"),
                "comments": item.get("comments"),
            })

        if not posts:
            raise RuntimeError("Instagram returned no recent public posts; keeping the previous feed.")

        OUT.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "source": "Instagram public profile",
            "username": HANDLE,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "posts": posts,
        }
        fd, temp_name = tempfile.mkstemp(prefix="social-posts-", suffix=".json", dir=OUT.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.write("\n")
            os.replace(temp_name, OUT)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        print(f"Updated {len(posts)} public Instagram posts for @{HANDLE}.")
        return 0
    except Exception as exc:  # keep the last successful feed on temporary blocks
        print(f"Instagram refresh skipped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
