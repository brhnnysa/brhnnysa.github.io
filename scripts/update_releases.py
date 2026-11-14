#!/usr/bin/env python3
"""Refresh the public BRHN NYSA music feed from Audiomack.

Uses public artist/song pages only; no API credentials are required.
If Audiomack changes its markup or blocks the request, the script fails safely
and leaves the previous releases.json untouched.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import requests
from requests_oauthlib import OAuth1

ROOT = Path(__file__).resolve().parents[1]
FEED = ROOT / "data" / "releases.json"
ARTIST = "https://audiomack.com/brhnnysa"
SONGS_PAGE = ARTIST + "/songs"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; BRHNNYSAReleaseBot/1.0; +https://brhnnysa.github.io/)"}
PROFILE_LINKS = {
    "boomplay": "https://www.boomplay.com/artists/EQJZtA_A5BuhoOCxLVWDxWpn",
    "soundcloud": "https://soundcloud.com/brhnnysa-466715604",
    "mdundo": "https://play.mdundo.com/artist/717441/Brhn-Nysa",
    "genius": "https://genius.com/Brhn-nysa",
}


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = set()
        self.meta = {}
        self._script = False
        self._script_data = []
        self.jsonld = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("href"):
            self.links.add(attrs["href"])
        if tag == "meta":
            key = attrs.get("property") or attrs.get("name") or attrs.get("itemprop")
            value = attrs.get("content")
            if key and value:
                self.meta[key.lower()] = value
        if tag == "script" and attrs.get("type") == "application/ld+json":
            self._script = True
            self._script_data = []

    def handle_data(self, data):
        if self._script:
            self._script_data.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._script:
            raw = "".join(self._script_data).strip()
            if raw:
                try:
                    self.jsonld.append(json.loads(raw))
                except json.JSONDecodeError:
                    pass
            self._script = False


def get(url: str) -> str:
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.text


def find_song_urls(html: str) -> list[str]:
    parser = PageParser()
    parser.feed(html)
    found = set()
    for href in parser.links:
        absolute = urljoin(ARTIST + "/", href).split("?")[0].rstrip("/")
        if re.search(r"audiomack\.com/brhnnysa/song/[^/]+$", absolute, re.I):
            found.add(absolute)
    for match in re.findall(r'(?:https?:)?//?audiomack\.com/brhnnysa/song/[A-Za-z0-9_-]+', html, re.I):
        found.add(match if match.startswith("http") else "https://" + match.lstrip("/"))
    return sorted(found)


def song_record(url: str) -> dict | None:
    html = get(url)
    parser = PageParser()
    parser.feed(html)
    meta = parser.meta
    title = (meta.get("og:title") or meta.get("twitter:title") or "").strip()
    title = re.sub(r"\s+by\s+Brhn\s+Nysa.*$", "", title, flags=re.I).strip()
    artwork = meta.get("og:image") or meta.get("twitter:image") or ""
    release_date = meta.get("music:release_date") or meta.get("article:published_time") or meta.get("datepublished") or ""
    if not release_date:
        def find_date(value):
            if isinstance(value, dict):
                for key in ("datePublished", "uploadDate", "dateCreated"):
                    if value.get(key):
                        return str(value[key])
                for nested in value.values():
                    found = find_date(nested)
                    if found:
                        return found
            elif isinstance(value, list):
                for nested in value:
                    found = find_date(nested)
                    if found:
                        return found
            return ""
        for structured in parser.jsonld:
            release_date = find_date(structured)
            if release_date:
                break
    if not release_date:
        match = re.search(r"Release Date:\s*([A-Za-z]+\s+\d{1,2},\s+\d{4})", html, re.I)
        if match:
            try:
                release_date = datetime.strptime(match.group(1), "%B %d, %Y").date().isoformat()
            except ValueError:
                release_date = match.group(1)
    if not title:
        return None
    slug = url.rstrip("/").split("/")[-1]
    return {
        "id": slug,
        "title": title,
        "artist": "Brhn Nysa",
        "releaseDate": release_date,
        "artwork": artwork,
        "links": {"audiomack": url, **PROFILE_LINKS},
    }



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
    existing = json.loads(FEED.read_text(encoding="utf-8")) if FEED.exists() else {"releases": []}
    existing_by_url = {}
    for item in existing.get("releases", []):
        link = item.get("links", {}).get("audiomack")
        if link:
            existing_by_url[link.rstrip("/")] = item

    errors = []
    song_urls = []
    for page in (SONGS_PAGE, ARTIST):
        try:
            song_urls = find_song_urls(get(page))
            if song_urls:
                break
        except Exception as exc:
            errors.append(f"{page}: {exc}")

    if not song_urls:
        raise RuntimeError("No song links found; keeping the existing feed. " + " | ".join(errors))

    discovered = []
    for url in song_urls[:60]:
        try:
            record = song_record(url)
            if record:
                old = existing_by_url.get(url.rstrip("/"), {})
                for key in ("releaseDate", "artwork"):
                    if not record.get(key) and old.get(key):
                        record[key] = old[key]
                discovered.append(record)
            time.sleep(0.25)
        except Exception as exc:
            print(f"Skipping {url}: {exc}")

    if not discovered:
        raise RuntimeError("Song pages were found but metadata could not be read; keeping the existing feed.")

    discovered.sort(key=lambda item: item.get("releaseDate") or "", reverse=True)
    payload = {
        "artist": "BRHN NYSA",
        "source": "Audiomack public artist profile",
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "releases": discovered,
    }
    FEED.parent.mkdir(parents=True, exist_ok=True)
    FEED.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Updated release feed with {len(discovered)} Audiomack songs.")
    try:
        update_top_plays()
    except Exception as exc:
        print(f"Audiomack top plays could not be refreshed: {exc}")


if __name__ == "__main__":
    main()
