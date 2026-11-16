#!/usr/bin/env python3
"""Refresh Brhn Nysa's public regular YouTube uploads without scraping watch pages."""
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
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; BRHNNYSAWebsiteBot/1.0; +https://brhnnysa.com/)"
}
ATOM = "{http://www.w3.org/2005/Atom}"
YT = "{http://www.youtube.com/xml/schemas/2015}"


def extract_json_object(source: str, opening_brace: int) -> str | None:
    """Return one balanced JSON object, respecting quoted braces and escapes."""
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


def model_title(model: dict) -> str:
    """Read the title embedded in a channel video card (new or legacy format)."""
    lockup = ((model.get("metadata") or {}).get("lockupMetadataViewModel") or {})
    title = lockup.get("title") or model.get("title") or {}
    if isinstance(title, dict):
        content = title.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        runs = title.get("runs") or []
        parts = [str(run.get("text") or "") for run in runs if isinstance(run, dict)]
        return "".join(parts).strip()
    return str(title).strip()


def load_existing() -> dict[str, dict]:
    """Keep already-published titles/dates as a safe fallback if YouTube omits them."""
    try:
        payload = json.loads(OUTPUT.read_text(encoding="utf-8"))
        return {
            str(item.get("id")): item
            for item in payload.get("videos", [])
            if isinstance(item, dict) and item.get("id")
        }
    except (OSError, json.JSONDecodeError, AttributeError, TypeError):
        return {}


def main() -> None:
    session = requests.Session()
    session.headers.update(HEADERS)

    response = session.get(HANDLE_URL, timeout=30)
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
        raise RuntimeError("Could not resolve the @brhnnysa YouTube channel ID; existing feed left unchanged.")

    videos_page_url = f"https://www.youtube.com/channel/{channel_id}/videos"
    videos_page_response = session.get(videos_page_url, timeout=30)
    videos_page_response.raise_for_status()
    source = videos_page_response.text

    # This tab is the allow-list: Shorts and other content types are excluded.
    regular_video_order: list[str] = []
    card_models: dict[str, dict] = {}
    content_types: set[str] = set()

    for marker, id_key in (
        ('"lockupViewModel"\s*:\s*\{', "contentId"),
        ('"videoRenderer"\s*:\s*\{', "videoId"),
    ):
        for match in re.finditer(marker, source):
            opening = source.find("{", match.start())
            raw = extract_json_object(source, opening)
            if not raw:
                continue
            try:
                model = json.loads(raw)
            except json.JSONDecodeError:
                continue
            video_id = model.get(id_key)
            content_type = str(model.get("contentType") or "")
            if content_type:
                content_types.add(content_type)
            if not isinstance(video_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
                continue
            # New channel cards label regular uploads as VIDEO; Shorts are not VIDEO.
            if id_key == "contentId" and "VIDEO" not in content_type.upper():
                continue
            if video_id not in card_models:
                regular_video_order.append(video_id)
                card_models[video_id] = model

    print(
        f"Found {len(regular_video_order)} regular uploads on Videos tab; "
        f"content types={sorted(content_types)}"
    )
    if not regular_video_order:
        markers = [
            marker for marker in ("videoRenderer", "gridVideoRenderer", "lockupViewModel", "ytInitialData")
            if marker in source
        ]
        print(f"YouTube page diagnostics: HTTP {videos_page_response.status_code}, "
              f"{len(source)} characters, markers={markers}")
        raise RuntimeError("Could not identify regular uploads; existing feed left unchanged.")

    # RSS provides exact publication timestamps when available. Do not request
    # individual watch pages or oEmbed: those repeated requests caused HTTP 429.
    rss_by_id: dict[str, dict] = {}
    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    try:
        feed_response = session.get(feed_url, timeout=30)
        if feed_response.ok:
            root = ET.fromstring(feed_response.content)
            for entry in root.findall(f"{ATOM}entry"):
                video_id = (entry.findtext(f"{YT}videoId") or "").strip()
                title = (entry.findtext(f"{ATOM}title") or "").strip()
                published = (entry.findtext(f"{ATOM}published") or "").strip()
                if video_id and title:
                    rss_by_id[video_id] = {"title": title, "publishedAt": published}
        else:
            print(f"YouTube RSS returned HTTP {feed_response.status_code}; using channel-card metadata.")
    except (requests.RequestException, ET.ParseError) as exc:
        print(f"YouTube RSS unavailable ({exc}); using channel-card metadata.")

    existing = load_existing()
    videos = []
    for video_id in regular_video_order:
        previous = existing.get(video_id, {})
        from_rss = rss_by_id.get(video_id, {})
        title = (
            from_rss.get("title")
            or model_title(card_models[video_id])
            or str(previous.get("title") or "").strip()
        )
        if not title:
            print(f"Skipping {video_id}: YouTube supplied no title and no previous title exists.")
            continue
        published = (
            from_rss.get("publishedAt")
            or str(previous.get("publishedAt") or "")
        )
        videos.append({
            "id": video_id,
            "title": title,
            "artist": "Brhn Nysa",
            "publishedAt": published,
            "artwork": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
            "url": f"https://www.youtube.com/watch?v={video_id}",
        })

    if not videos:
        raise RuntimeError("No regular video details could be recovered; existing feed left unchanged.")

    payload = {
        "artist": "Brhn Nysa",
        "channel": HANDLE_URL,
        "source": "YouTube public channel Videos tab and RSS feed",
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "videos": videos[:20],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"Updated Brhn Nysa regular-video feed with {len(payload['videos'])} videos "
        "(Shorts excluded; no per-video page scraping)."
    )


if __name__ == "__main__":
    main()
