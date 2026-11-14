#!/usr/bin/env python3
"""Refresh the public BRHN NYSA music feed from Audiomack.

This uses only publicly accessible artist/song pages. It does not upload music
or require API credentials. If Audiomack changes its page markup, the updater
fails safely and leaves the existing releases.json untouched.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import requests

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
        self.jsonld = []
        self._script = False
        self._script_type = ""
        self._script_data = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            href = attrs.get("href", "")
            if href:
                self.links.add(href)
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
        if re.search(r"audiomack\\.com/brhnnysa/song/[^/]+$", absolute, re.I):
            found.add(absolute)
    # Some server-rendered pages expose links only in embedded JSON/text.
    for match in re.findall(r'(?:https?:)?//?audiomack\\.com/brhnnysa/song/[A-Za-z0-9_-]+', html, re.I):
        found.add(match if match.startswith("http") else "https://" + match.lstrip("/"))
    return sorted(found)


def song_record(url: str) -> dict | None:
    html = get(url)
    parser = PageParser()
    parser.feed(html)
    meta = parser.meta
    title = (meta.get("og:title") or meta.get("twitter:title") or "").strip()
    title = re.sub(r"\\s+by\\s+Brhn\\s+Nysa.*$", "", title, flags=re.I).strip()
    artwork = meta.get("og:image") or meta.get("twitter:image") or ""
    release_date = meta.get("music:release_date") or meta.get("article:published_time") or meta.get("datePublished") or ""
    # Search the visible HTML for Audiomack's published Release Date field.
    if not release_date:
        match = re.search(r"Release Date:\\s*([A-Za-z]+\\s+\\d{1,2},\\s+\\d{4})", html, re.I)
        if match:
            try:
                release_date = datetime.strptime(match.group(1), "%B %d, %Y").date().isoformat()
            except ValueError:
                release_date = match.group(1)
    if not title:
        return None
    slug = url.rstrip("/").split("/")[-1]
    links = {"audiomack": url, **PROFILE_LINKS}
    return {
        "id": slug,
        "title": title,
        "artist": "Brhn Nysa",
        "releaseDate": release_date,
        "artwork": artwork,
        "links": links,
    }


def main():
    existing = json.loads(FEED.read_text(encoding="utf-8")) if FEED.exists() else {"releases": []}
    existing_by_url = {}
    for item in existing.get("releases", []):
        audiomack = item.get("links", {}).get("audiomack")
        if audiomack:
            existing_by_url[audiomack.rstrip("/")] = item

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
        raise RuntimeError("No song links were found. Existing feed was not changed. " + " | ".join(errors))

    discovered = []
    for url in song_urls[:60]:
        try:
            record = song_record(url)
            if record:
                old = existing_by_url.get(url.rstrip("/"), {})
                # Keep previously known metadata if the page omits a field today.
                for key in ("releaseDate", "artwork"):
                    if not record.get(key) and old.get(key):
                        record[key] = old[key]
                discovered.append(record)
            time.sleep(0.25)
        except Exception as exc:
            print(f"Skipping {url}: {exc}")

    if not discovered:
        raise RuntimeError("Song URLs were found, but no song metadata could be read. Existing feed was not changed.")

    discovered.sort(key=lambda item: item.get("releaseDate") or "", reverse=True)
    payload = {
        "artist": "BRHN NYSA",
        "source": "Audiomack public artist profile",
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "releases": discovered,
    }
    FEED.parent.mkdir(parents=True, exist_ok=True)
    FEED.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
    print(f"Updated release feed with {len(discovered)} Audiomack songs.")


if __name__ == "__main__":
    main()
