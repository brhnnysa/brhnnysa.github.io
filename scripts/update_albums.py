#!/usr/bin/env python3
"""Refresh Brhn Nysa's Audio.com album collections for the official website."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
import sys
import time

import requests

API = "https://api.audio.com/v1"
USERNAME = "brhn-nysa"
TOKEN = os.environ.get("AUDIOCOM_TOKEN", "").strip()
OUT = Path("data/albums.json")
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"}


def api_get(path: str, **params):
    response = requests.get(f"{API}/{path}", params=params, headers=HEADERS, timeout=40)
    if response.status_code != 200:
        raise RuntimeError(f"Audio.com API {path} returned HTTP {response.status_code}: {response.text[:400]}")
    return response.json()


def main():
    if not TOKEN:
        raise SystemExit("AUDIOCOM_TOKEN is missing.")

    collections = []
    page = 1
    while True:
        batch = api_get("collection/list", sort="newest", page=page, **{"per-page": 100})
        if not isinstance(batch, list):
            raise RuntimeError("Unexpected Audio.com collection list response.")
        collections.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    albums = [
        c for c in collections
        if c.get("type") == "album" and c.get("username") == USERNAME
    ]

    output = []
    for collection in albums:
        collection_id = str(collection.get("id", ""))
        slug = collection.get("slug", "")
        if not collection_id or not slug:
            continue

        tracks = api_get("collection/items", id=collection_id)
        if not isinstance(tracks, list):
            raise RuntimeError(f"Unexpected tracklist response for {collection.get('title', 'album')}.")

        track_items = []
        for track in tracks:
            track_slug = track.get("slug", "")
            track_title = track.get("title") or "Untitled track"
            if track_title.lower().startswith(("brhn_nysa_", "brhn nysa")):
                track_title = re.sub(r"^Brhn[_\\s-]*Nysa[_\\s-]*", "", track_title, flags=re.IGNORECASE)
                track_title = re.sub(r"[-_][A-Za-z0-9]{12,}$", "", track_title)
                track_title = re.sub(r"[_-]+", " ", track_title).strip().title() or "Untitled track"
            track_items.append({
                "title": track_title,
                "url": f"https://audio.com/{USERNAME}/audio/{track_slug}" if track_slug else f"https://audio.com/{USERNAME}",
                "artwork": track.get("image") or "",
            })

        artwork = collection.get("image") or next(
            (track.get("image") for track in tracks if track.get("image")), ""
        )
        collection_url = f"https://audio.com/{USERNAME}/collections/{slug}"
        created = collection.get("date_created")
        release_date = (
            datetime.fromtimestamp(created, timezone.utc).date().isoformat()
            if isinstance(created, (int, float)) else ""
        )

        output.append({
            "id": collection_id,
            "title": collection.get("title") or "Untitled album",
            "artist": "Brhn Nysa",
            "releaseDate": release_date,
            "artwork": artwork,
            "url": collection_url,
            "links": {
                "audiomack": "https://audiomack.com/brhnnysa/",
                "boomplay": "https://www.boomplay.com/artists/EQJZtA_A5BuhoOCxLVWDxWpn",
                "soundcloud": "https://soundcloud.com/brhnnysa-466715604",
                "mdundo": "https://play.mdundo.com/artist/717441/Brhn-Nysa",
                "genius": "https://genius.com/artists/Brhn-nysa",
                "tidal": "https://tidal.com/artist/88532992",
                "audiocom": collection_url,
            },
            "tracks": track_items,
            "trackCount": len(track_items),
        })

    output.sort(key=lambda a: a.get("releaseDate", ""), reverse=True)
    payload = {
        "artist": "Brhn Nysa",
        "source": "Audio.com authenticated API",
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "albums": output,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(OUT)
    print(f"Updated {OUT}: {len(output)} albums, {sum(a['trackCount'] for a in output)} tracks.")
    for album in output:
        print(f"- {album['title']}: {album['trackCount']} tracks; artwork={'yes' if album['artwork'] else 'no'}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Album refresh failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise
