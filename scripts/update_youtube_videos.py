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
    # YouTube now uses lockupViewModel for many channel video cards instead
    # of the older videoRenderer structure. Extract each JSON object safely.
    regular_video_ids: set[str] = set()
    regular_video_order: list[str] = []
    content_types: set[str] = set()

    def extract_json_object(source: str, opening_brace: int) -> str | None:
        depth = 0
        in_string = False
        escaped = False
        for pos in range(opening_brace, len(source)):
            char = source[pos]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return source[opening_brace:pos + 1]
        return None

    for match in re.finditer(r'"lockupViewModel"\s*:\s*\{', videos_page_response.text):
        opening = videos_page_response.text.find("{", match.start())
        raw_object = extract_json_object(videos_page_response.text, opening)
        if not raw_object:
            continue
        try:
            model = json.loads(raw_object)
        except json.JSONDecodeError:
            continue
        video_id = model.get("contentId")
        content_type = str(model.get("contentType") or "")
        if content_type:
            content_types.add(content_type)
        if isinstance(video_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) and "VIDEO" in content_type.upper():
            if video_id not in regular_video_ids:
                if video_id not in regular_video_ids:
                regular_video_ids.add(video_id)
                regular_video_order.append(video_id)
                regular_video_order.append(video_id)

    # Keep compatibility with older YouTube pages that still expose videoRenderer.
    renderer_pattern = re.compile(r'"videoRenderer"\s*:\s*\{')
    for match in renderer_pattern.finditer(videos_page_response.text):
        opening = videos_page_response.text.find("{", match.start())
        raw_object = extract_json_object(videos_page_response.text, opening)
        if not raw_object:
            continue
        try:
            model = json.loads(raw_object)
        except json.JSONDecodeError:
            continue
        video_id = model.get("videoId")
        if isinstance(video_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            regular_video_ids.add(video_id)

    print(f"Found {len(regular_video_ids)} regular video IDs on Videos tab; content types={sorted(content_types)}")
    if not regular_video_ids:
        markers = [marker for marker in ("videoRenderer", "gridVideoRenderer", "lockupViewModel", "ytInitialData") if marker in videos_page_response.text]
        print(f"YouTube page diagnostics: HTTP {videos_page_response.status_code}, {len(videos_page_response.text)} characters, markers={markers}")
        raise RuntimeError("Could not identify regular uploads on the channel Videos tab; leaving the existing feed unchanged.")

    # YouTube's legacy channel RSS endpoint can return HTTP 404 even when
    # the channel Videos tab is available. Try RSS first, then safely fall
    # back to each regular-video page (the allow-list still excludes Shorts).
    videos = []
    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    feed_response = requests.get(feed_url, headers=HEADERS, timeout=30)
    if feed_response.ok:
        root = ET.fromstring(feed_response.content)
        for entry in root.findall(f"{ATOM}entry"):
            video_id = (entry.findtext(f"{YT}videoId") or "").strip()
            title = (entry.findtext(f"{ATOM}title") or "").strip()
            published = (entry.findtext(f"{ATOM}published") or "").strip()
            if not video_id or not title or video_id not in regular_video_ids:
                continue
            videos.append({
                "id": video_id, "title": title, "artist": "BRHN NYSA",
                "publishedAt": published,
                "artwork": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                "url": f"https://www.youtube.com/watch?v={video_id}",
            })
    else:
        print(f"YouTube RSS returned HTTP {feed_response.status_code}; using regular-video page fallback.")
    if not videos:
        for video_id in regular_video_order:
            watch_url = f"https://www.youtube.com/watch?v={video_id}"
            try:
                page_response = requests.get(watch_url, headers=HEADERS, timeout=20)
                page_response.raise_for_status()
                page = page_response.text
                title_response = requests.get(
                    "https://www.youtube.com/oembed",
                    params={"url": watch_url, "format": "json"},
                    headers=HEADERS, timeout=20,
                )
                title_response.raise_for_status()
                title = str(title_response.json().get("title") or "").strip()
                date_match = re.search(r"<meta[^>]+itemprop=['\"]datePublished['\"][^>]+content=['\"]([^'\"]+)", page, re.I)
                if not date_match:
                    date_match = re.search(r'"publishDate"\s*:\s*"([^"]+)"', page)
                published = date_match.group(1) if date_match else ""
                if title:
                    videos.append({
                        "id": video_id, "title": title, "artist": "BRHN NYSA",
                        "publishedAt": published,
                        "artwork": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                        "url": watch_url,
                    })
            except requests.RequestException as exc:
                print(f"Could not fetch video {video_id}: {exc}")
    if not videos:
        raise RuntimeError("Could not retrieve regular YouTube video details; keeping the existing feed unchanged.")
    if all(not item.get("publishedAt") for item in videos):
        print("Video publish timestamps were unavailable; preserving newest-first channel Videos tab order.")
    else:
        videos.sort(key=lambda item: item.get("publishedAt") or "", reverse=True)

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
