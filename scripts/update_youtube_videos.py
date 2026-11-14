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

    # The channel's Videos tab separates regular uploads from Shorts.
    # Use its videoRenderer IDs as an allow-list, then retain RSS ordering/dates.
    videos_page_url = f"https://www.youtube.com/channel/{channel_id}/videos"
    videos_page_response = requests.get(videos_page_url, headers=HEADERS, timeout=30)
    videos_page_response.raise_for_status()
    # YouTube changes renderer field order and nesting. Inspect each regular
    # video renderer separately rather than assuming videoId is its first field.
    regular_video_ids: set[str] = set()
    renderer_pattern = re.compile(r'"(?:videoRenderer|gridVideoRenderer)"\s*:\s*\{')
    renderer_matches = list(renderer_pattern.finditer(videos_page_response.text))
    for index, renderer_match in enumerate(renderer_matches):
        section_end = renderer_matches[index + 1].start() if index + 1 < len(renderer_matches) else renderer_match.end() + 10000
        renderer_section = videos_page_response.text[renderer_match.end():min(section_end, renderer_match.end() + 10000)]
        video_match = re.search(r'"videoId"\s*:\s*"([A-Za-z0-9_-]{11})"', renderer_section)
        if video_match:
            regular_video_ids.add(video_match.group(1))
    print(f"Found {len(regular_video_ids)} regular video IDs on Videos tab.")
    if not regular_video_ids:
        markers = [marker for marker in ('videoRenderer', 'gridVideoRenderer', 'lockupViewModel', 'ytInitialData') if marker in videos_page_response.text]
        print(f"YouTube page diagnostics: HTTP {videos_page_response.status_code}, {len(videos_page_response.text)} characters, markers={markers}")
        raise RuntimeError("Could not identify regular uploads on the channel Videos tab; leaving the existing feed unchanged.")

    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    feed_response = requests.get(feed_url, headers=HEADERS, timeout=30)
    feed_response.raise_for_status()
    root = ET.fromstring(feed_response.content)

    videos = []
    for entry in root.findall(f"{ATOM}entry"):
        video_id = (entry.findtext(f"{YT}videoId") or "").strip()
        title = (entry.findtext(f"{ATOM}title") or "").strip()
        published = (entry.findtext(f"{ATOM}published") or "").strip()
        if not video_id or not title or video_id not in regular_video_ids:
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
    print(f"Updated BRHN NYSA regular-video feed with {len(payload['videos'])} videos (Shorts excluded).")


if __name__ == "__main__":
    main()
