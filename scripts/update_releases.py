#!/usr/bin/env python3
"""Refresh BRHN NYSA's latest releases from the artist's public SoundCloud RSS feed."""
from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests
from requests_oauthlib import OAuth1

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
            "artist": "BRHN NYSA",
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


def update_top_plays():
    """Write Audiomack's artist top-10 plays from its documented 60-minute metrics."""
    consumer_key = os.getenv("AUDIOMACK_CONSUMER_KEY")
    consumer_secret = os.getenv("AUDIOMACK_CONSUMER_SECRET")
    if not consumer_key or not consumer_secret:
        print("Audiomack API credentials are not configured; top plays feed remains pending.")
        return

    api = "https://api.audiomack.com/v1"
    auth = OAuth1(consumer_key, client_secret=consumer_secret)
    response = requests.get(f"{api}/artist/brhnnysa", auth=auth, timeout=30)
    response.raise_for_status()
    artist = response.json().get("results", {})
    artist_id = artist.get("id")
    if not artist_id:
        raise RuntimeError("Audiomack artist lookup did not return an artist ID.")

    response = requests.get(f"{api}/artist/{artist_id}/metrics", auth=auth, timeout=30)
    response.raise_for_status()
    metrics = response.json().get("results", {})
    top_items = metrics.get("music_top10", [])
    top_plays = []
    for entry in top_items:
        music = entry.get("music") or {}
        uploader = music.get("uploader") or {}
        # Only include songs uploaded by BRHN NYSA, not reposted tracks.
        if str(uploader.get("url_slug", "")).lower() != "brhn-nysa":
            continue
        slug = music.get("url_slug")
        top_plays.append({
            "id": str(music.get("id") or slug or ""),
            "title": music.get("title") or "Untitled",
            "artist": music.get("artist") or "Brhn Nysa",
            "artwork": music.get("image") or "",
            "plays": int(entry.get("play_counter") or 0),
            "url": f"https://audiomack.com/brhnnysa/song/{slug}" if slug else ARTIST,
        })

    payload = {
        "artist": "BRHN NYSA",
        "period": "last_60_minutes",
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "totalPlaysLast60Minutes": (metrics.get("event_counters") or {}).get("play", 0),
        "topPlays": top_plays[:10],
        "source": "Audiomack artist metrics API",
    }
    (ROOT / "data" / "top-plays.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Updated Audiomack top plays with {len(top_plays[:10])} tracks.")




def main():
    releases = soundcloud_releases()
    payload = {
        "artist": "BRHN NYSA",
        "source": "SoundCloud artist RSS",
        "sourceUrl": SOUNDCLOUD_RSS,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "releases": releases,
    }
    FEED.parent.mkdir(parents=True, exist_ok=True)
    FEED.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Updated release feed with {len(releases)} SoundCloud tracks.")
    # Keep the existing Audiomack top-plays updater separate from release discovery.
    try:
        update_top_plays()
    except Exception as exc:
        print(f"Audiomack top plays could not be refreshed: {exc}")


if __name__ == "__main__":
    main()
