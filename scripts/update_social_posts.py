#!/usr/bin/env python3
"""Refresh the public Instagram cards used by the BRHN NYSA website.

Uses SocialFetch's anonymous public Instagram reader. No Instagram login,
cookies, API key, or paid service is used. Instagram may throttle cloud runners,
so retry temporary failures and preserve the last good feed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "social-posts.json"
SOCIALFETCH = Path(os.environ.get("SOCIALFETCH_BIN", "socialfetch"))
HANDLE = os.environ.get("INSTAGRAM_HANDLE", "heisbrhn")
RETRY_DELAYS = (20, 45, 90)


def fetch_posts() -> list[dict]:
    last_error = "Instagram did not return recent posts."
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            proc = subprocess.run(
                [str(SOCIALFETCH), "ig", "user", HANDLE, "--limit", "5", "--json"],
                check=False, capture_output=True, text=True, timeout=90,
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    (proc.stderr or proc.stdout or "SocialFetch failed").strip()[-1200:]
                )
            result = json.loads(proc.stdout)
            if not result.get("ok"):
                raise RuntimeError(
                    result.get("error") or "Instagram returned an unsuccessful result"
                )

            profile = result.get("data") or {}
            recent = profile.get("recent") or []
            posts = []
            for item in recent[:5]:
                media = item.get("media") or []
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
                    "media_url": media[0] if media else None,
                    "media_type": item.get("type") or "image",
                    "published_at": item.get("taken_at"),
                    "likes": item.get("likes"),
                    "comments": item.get("comments"),
                })

            if posts:
                return posts
            last_error = (
                "Instagram profile was reached, but its anonymous feed returned "
                "no recent posts (possibly throttled or temporarily blocked)."
            )
        except Exception as exc:
            last_error = str(exc)

        if attempt < len(RETRY_DELAYS):
            delay = RETRY_DELAYS[attempt]
            print(
                f"Instagram fetch attempt {attempt + 1} failed: {last_error}. "
                f"Retrying in {delay}s.",
                file=sys.stderr,
            )
            time.sleep(delay)

    raise RuntimeError(last_error)


def main() -> int:
    try:
        posts = fetch_posts()
        OUT.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "source": "Instagram public profile",
            "username": HANDLE,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "posts": posts,
        }
        fd, temp_name = tempfile.mkstemp(
            prefix="social-posts-", suffix=".json", dir=OUT.parent
        )
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
    except Exception as exc:
        print(f"Instagram refresh skipped; previous feed preserved: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
