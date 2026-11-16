#!/usr/bin/env python3
"""Read-only test of Brhn Nysa's public Audio.com album pages.

This deliberately uses public HTML only: no OAuth token, account changes, or uploads.
It exits non-zero if the public pages do not expose album links, covers, and track lists.
"""
from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
import json
import re
import requests

BASE = "https://audio.com"
PROFILE = "https://audio.com/brhn-nysa/collections"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; BrhnNysaAlbumCheck/1.0)"}


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}
        self.anchors = []
        self.images = []
        self.headings = []
        self._anchor = None
        self._heading = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key and a.get("content"):
                self.meta[key] = a["content"]
        elif tag == "a":
            href = a.get("href")
            if href:
                self._anchor = {"href": urljoin(BASE, href), "text": ""}
        elif tag == "img":
            self.images.append({
                "src": urljoin(BASE, a.get("src") or a.get("data-src") or ""),
                "alt": a.get("alt", "")
            })
        elif tag in ("h1", "h2"):
            self._heading = {"tag": tag, "text": ""}

    def handle_data(self, data):
        if self._anchor is not None:
            self._anchor["text"] += data
        if self._heading is not None:
            self._heading["text"] += data

    def handle_endtag(self, tag):
        if tag == "a" and self._anchor is not None:
            self._anchor["text"] = " ".join(self._anchor["text"].split())
            self.anchors.append(self._anchor)
            self._anchor = None
        elif tag in ("h1", "h2") and self._heading is not None:
            self._heading["text"] = " ".join(self._heading["text"].split())
            self.headings.append(self._heading)
            self._heading = None


def get_page(url: str) -> tuple[str, Page]:
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    parser = Page()
    parser.feed(response.text)
    return response.text, parser


def main():
    _, profile = get_page(PROFILE)
    album_urls = []
    for anchor in profile.anchors:
        path = urlparse(anchor["href"]).path.rstrip("/")
        if path.startswith("/brhn-nysa/collections/") and path != "/brhn-nysa/collections":
            if anchor["href"] not in album_urls:
                album_urls.append(anchor["href"])

    if not album_urls:
        raise SystemExit("FAIL: public profile HTML did not expose any album collection links.")

    results = []
    for url in album_urls:
        html, page = get_page(url)
        title = next((h["text"] for h in page.headings if h["tag"] == "h1" and h["text"]), "")
        if not title:
            title = page.meta.get("og:title", "").split("|")[0].strip()
        cover = page.meta.get("og:image", "")
        if not cover:
            cover = next((img["src"] for img in page.images if img["src"]), "")
        tracks = []
        for anchor in page.anchors:
            path = urlparse(anchor["href"]).path
            if path.startswith("/brhn-nysa/audio/") and anchor["text"]:
                item = {"title": anchor["text"], "url": anchor["href"]}
                if item not in tracks:
                    tracks.append(item)
        if not title or not cover or not tracks:
            raise SystemExit(
                "FAIL: incomplete public album page: "
                + json.dumps({"url": url, "title": title, "cover_found": bool(cover), "tracks": tracks})
            )
        results.append({
            "title": title,
            "url": url,
            "cover": cover,
            "tracks": tracks,
            "track_count": len(tracks),
        })

    print("PASS: Audio.com public album pages can be read without OAuth.")
    print(json.dumps({"profile": PROFILE, "album_count": len(results), "albums": results}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
