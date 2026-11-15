#!/usr/bin/env python3
"""Refresh Brhn Nysa's latest releases from the artist's public SoundCloud RSS feed."""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
FEED = ROOT / "data" / "releases.json"
SOUNDCLOUD_RSS = "https://feeds.soundcloud.com/users/soundcloud:users:1559781363/sounds.rss"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; BRHNNYSAReleaseBot/1.0; +https://brhnnysa.github.io/)"}
PROFILE_LINKS = {
    "audiomack": "https://audiomack.com/brhnnysa",
    "boomplay": "https://www.boomplay.com/artists/EQJZtA_A5BuhoOCxLVWDxWpn",
    "mdundo": "https://play.mdundo.com/artist/717441/Brhn-Nysa",
    "genius": "https://genius.com/Brhn-nysa",
}


def child_text(element: ET.Element, name: str) -> str:
    for child in element:
        if child.tag.rsplit("}", 1)[-1].lower() == name.lower():
            return (child.text or "").strip()
    return ""


def soundcloud_releases() -> list[dict]:
    """Read every public track currently exposed by the artist's SoundCloud RSS feed."""
    response = requests.get(SOUNDCLOUD_RSS, headers=HEADERS, timeout=30)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    channel = next((node for node in root.iter() if node.tag.rsplit("}", 1)[-1].lower() == "channel"), root)
    items = [node for node in channel if node.tag.rsplit("}", 1)[-1].lower() == "item"]
    releases = []
    seen = set()

    for item in items:
        title = child_text(item, "title")
        link = child_text(item, "link")
        guid = child_text(item, "guid")
        if not title or not link:
            continue
        link = link.strip()
        if link in seen:
            continue
        seen.add(link)

        published = child_text(item, "pubDate")
        release_date = ""
        if published:
            try:
                parsed = parsedate_to_datetime(published)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                release_date = parsed.date().isoformat()
            except (TypeError, ValueError, OverflowError):
                release_date = ""

        artwork = ""
        for node in item.iter():
            local = node.tag.rsplit("}", 1)[-1].lower()
            if local in {"image", "thumbnail"}:
                artwork = node.attrib.get("href") or node.attrib.get("url") or ""
                if artwork:
                    break
        if not artwork:
            description = child_text(item, "description")
            match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', description, re.I)
            if match:
                artwork = match.group(1)

        clean_title = re.sub(r"\s+by\s+BRHN\s+NYSA\s*$", "", title, flags=re.I).strip()
        releases.append({
            "id": guid or link.rstrip("/").split("/")[-1],
            "title": clean_title or title,
            "artist": "Brhn Nysa",
            "releaseDate": release_date,
            "artwork": artwork,
            "links": {"soundcloud": link, **PROFILE_LINKS},
        })

    if not releases:
        raise RuntimeError("SoundCloud RSS returned no public tracks; keeping the existing releases feed.")
    # Sort by the actual RSS publication timestamp's calendar date, newest first.
    releases.sort(key=lambda song: song.get("releaseDate") or "", reverse=True)
    print(f"SoundCloud RSS returned {len(releases)} public tracks.")
    return releases



def main():
    releases = soundcloud_releases()
    payload = {
        "artist": "Brhn Nysa",
        "source": "SoundCloud artist RSS",
        "sourceUrl": SOUNDCLOUD_RSS,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "releases": releases,
    }
    FEED.parent.mkdir(parents=True, exist_ok=True)
    FEED.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Updated release feed with {len(releases)} SoundCloud tracks.")


if __name__ == "__main__":
    main()
