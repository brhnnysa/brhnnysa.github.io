#!/usr/bin/env python3
"""Refresh the public BRHN NYSA YouTube uploads feed using YouTube's public RSS feed."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET

import requests

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "youtube-videos.json"
HANDLE_URL = "https://www.youtube.com/@brhnnysa"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; BRHNNYSAWebsiteBot/1.0; +https://brhnnysa.github.io/)"}
ATOM = "{http://www.w3.org/2005/Atom}"
YT = "{http://www.youtube.com/xml/schemas/2015}"
MEDIA = "{http://search.yahoo.com/mrss/}"


def main() -> None:
    response = requests.get(HANDLE_URL, headers=HEADERS, timeout=30)
    response.raise_for_status()
    page = response.text

    channel_id = ""
    for pattern in (
        r'"channelId"\s*:\s*"(UC[A-Za-z0-9_-]{20,})"',
        r'"externalId"\s*:\s*"(UC[A-Za-z0-9_-]{20,})"',
        r'https://www\.youtube\.com/channel/(UC[A-Za-z0-9_-]{20,})',
    ):
        match = re.search(pattern, page)
        if match:
            channel_id = match.group(1)
            break
    if not channel_id:
        raise RuntimeError("Could not resolve the @brhnnysa YouTube channel ID from its public channel page.")

    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    feed_response = requests.get(feed_url, headers=HEADERS, timeout=30)
    feed_response.raise_for_status()
    root = ET.fromstring(feed_response.content)

    videos = []
    for entry in root.findall(f"{ATOM}entry"):
        video_id = (entry.findtext(f"{YT}videoId") or "").strip()
        title = (entry.findtext(f"{ATOM}title") or "").strip()
        published = (entry.findtext(f"{ATOM}published") or "").strip()
        if not video_id or not title:
            continue
        videos.append({
            "id": video_id,
            "title": title,
            "artist": "BRHN NYSA",
            "publishedAt": published,
            "artwork": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
            "url": f"https://www.youtube.com/watch?v={video_id}",
        })

    payload = {
        "artist": "BRHN NYSA",
        "channel": HANDLE_URL,
        "source": "YouTube public channel RSS feed",
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "videos": videos[:20],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Updated BRHN NYSA YouTube feed with {len(payload['videos'])} uploads.")


if __name__ == "__main__":
    main()
