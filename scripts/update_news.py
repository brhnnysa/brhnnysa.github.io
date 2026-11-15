#!/usr/bin/env python3
"""Refresh the Brhn Nysa website news cards from the public Blogger RSS feed."""
from __future__ import annotations

import html
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

FEED_URL = "https://lealsnews.blogspot.com/feeds/posts/default?alt=rss"
OUTPUT = Path("data/news.json")
MAX_POSTS = 30


class ContentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.image: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "img" and self.image is None:
            values = dict(attrs)
            self.image = values.get("src") or values.get("data-src")

    def handle_data(self, data: str) -> None:
        value = data.strip()
        if value:
            self.parts.append(value)


def local_text(element: ET.Element, names: tuple[str, ...]) -> str:
    for child in element.iter():
        tag = child.tag.rsplit("}", 1)[-1].lower()
        if tag in names and child.text:
            return child.text.strip()
    return ""


def parse_date(value: str) -> tuple[str, float]:
    if not value:
        return "", 0.0
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.strftime("%b %d, %Y").upper(), parsed.timestamp()
    except (TypeError, ValueError, OverflowError):
        return value, 0.0


def main() -> None:
    request = urllib.request.Request(
        FEED_URL,
        headers={"User-Agent": "BRHN-NYSA-NewsFeed/1.0 (+https://www.brhnnysa.com/)"},
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        root = ET.fromstring(response.read())

    posts: list[dict[str, str]] = []
    sortable: list[tuple[float, dict[str, str]]] = []
    for item in root.findall(".//item"):
        title = re.sub(r"(?i)\\bbrhn\\s+nysa\\b", "Brhn Nysa", html.unescape(local_text(item, ("title",))))
        url = local_text(item, ("link",))
        raw_date = local_text(item, ("pubdate",))
        raw_content = local_text(item, ("encoded", "description"))
        if not title or not url:
            continue

        parser = ContentParser()
        parser.feed(raw_content)
        image = parser.image
        summary = re.sub(r"\s+", " ", " ".join(parser.parts)).strip()
        if len(summary) > 230:
            summary = summary[:227].rsplit(" ", 1)[0] + "…"
        date_label, timestamp = parse_date(raw_date)
        post = {
            "title": title,
            "url": url,
            "published": date_label,
            "published_at": datetime.fromtimestamp(timestamp, timezone.utc).isoformat() if timestamp else "",
            "summary": summary,
        }
        if image and image.startswith(("https://", "http://")):
            post["image"] = image
        sortable.append((timestamp, post))

    sortable.sort(key=lambda pair: pair[0], reverse=True)
    posts = [post for _, post in sortable[:MAX_POSTS]]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(
            {"source": FEED_URL, "updated_at": datetime.now(timezone.utc).isoformat(), "posts": posts},
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    print(f"Saved {len(posts)} Blogger news posts to {OUTPUT}")


if __name__ == "__main__":
    main()
