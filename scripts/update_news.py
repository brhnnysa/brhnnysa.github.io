#!/usr/bin/env python3
"""Refresh Brhn Nysa website news cards from the Blogger RSS feed."""
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
from urllib.parse import urlparse

FEEDS = [
    ("Blogger", "https://lealsnews.blogspot.com/feeds/posts/default?alt=rss"),
]
OUTPUT = Path("data/news.json")
MAX_POSTS = 40


class ContentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.image: str | None = None
        self.video: str | None = None
        self.embed: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag.lower() == "img" and self.image is None:
            self.image = values.get("src") or values.get("data-src")
        if tag.lower() in ("video", "source") and self.video is None:
            self.video = values.get("src")
        if tag.lower() == "iframe" and self.embed is None:
            candidate = values.get("src", "")
            host = urlparse(candidate).hostname or ""
            allowed = ("youtube.com", "youtube-nocookie.com", "youtu.be",
                       "player.vimeo.com")
            if candidate.startswith("https://") and any(
                host == domain or host.endswith("." + domain) for domain in allowed
            ):
                self.embed = candidate

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


def post_url(item: ET.Element) -> str:
    link = local_text(item, ("link",))
    if link:
        return link
    for child in item.iter():
        if child.tag.rsplit("}", 1)[-1].lower() == "link":
            href = child.attrib.get("href", "")
            if href:
                return href
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
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.strftime("%b %d, %Y").upper(), parsed.timestamp()
        except (TypeError, ValueError, OverflowError):
            return value, 0.0


def fetch_feed(source: str, feed_url: str) -> list[tuple[float, dict[str, str]]]:
    request = urllib.request.Request(
        feed_url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        root = ET.fromstring(response.read())

    results: list[tuple[float, dict[str, str]]] = []
    entries = root.findall(".//item") or root.findall(".//{*}entry")
    for item in entries:
        title = re.sub(
            r"(?i)brhn\s*nysa", "Brhn Nysa",
            html.unescape(local_text(item, ("title",)))
        )
        url = post_url(item)
        raw_date = local_text(item, ("pubdate", "published", "updated"))
        raw_content = local_text(item, ("encoded", "content", "description", "summary"))
        if not title or not url:
            continue

        parser = ContentParser()
        parser.feed(raw_content)
        summary = re.sub(
            r"(?i)brhn\s*nysa", "Brhn Nysa",
            re.sub(r"\s+", " ", " ".join(parser.parts)).strip()
        )
        if len(summary) > 230:
            summary = summary[:227].rsplit(" ", 1)[0] + "…"
        date_label, timestamp = parse_date(raw_date)
        post = {
            "title": title,
            "url": url,
            "published": date_label,
            "published_at": datetime.fromtimestamp(
                timestamp, timezone.utc
            ).isoformat() if timestamp else "",
            "summary": summary,
            "source": source,
        }
        image = parser.image
        if not image:
            image = local_text(item, ("thumbnail",))
        if image and image.startswith(("https://", "http://")):
            post["image"] = image
        if parser.video and parser.video.startswith(("https://", "http://")):
            post["video"] = parser.video
        if parser.embed:
            post["embed"] = parser.embed
        for child in item.iter():
            if child.tag.rsplit("}", 1)[-1].lower() == "enclosure":
                media_url = child.attrib.get("url", "")
                media_type = child.attrib.get("type", "")
                if media_url.startswith("https://") and media_type.startswith("video/"):
                    post["video"] = media_url
                    break
        results.append((timestamp, post))
    return results


def main() -> None:
    sortable: list[tuple[float, dict[str, str]]] = []
    unavailable_sources: set[str] = set()
    previous_posts: list[dict[str, str]] = []
    if OUTPUT.exists():
        try:
            previous = json.loads(OUTPUT.read_text(encoding="utf-8"))
            previous_posts = [
                post for post in previous.get("posts", [])
                if isinstance(post, dict) and post.get("url")
            ]
        except (OSError, json.JSONDecodeError, AttributeError):
            print("Could not read the previous news feed; continuing with live sources.")

    for source, feed_url in FEEDS:
        try:
            feed_posts = fetch_feed(source, feed_url)
            if not feed_posts:
                raise RuntimeError("feed returned no posts")
            sortable.extend(feed_posts)
            print(f"Loaded {len(feed_posts)} {source} posts")
        except Exception as error:
            unavailable_sources.add(source)
            print(f"WARNING: Could not load {source} feed: {error}; keeping its previous posts.")

    # Preserve the last good Blogger posts if the feed temporarily blocks
    # or fails the request.
    for post in previous_posts:
        if post.get("source") in unavailable_sources:
            try:
                timestamp = datetime.fromisoformat(
                    str(post.get("published_at", "")).replace("Z", "+00:00")
                ).timestamp()
            except (TypeError, ValueError, OverflowError):
                timestamp = 0.0
            sortable.append((timestamp, post))

    if not sortable:
        raise RuntimeError("No news feeds could be loaded and no previous posts are available")

    unique: dict[str, tuple[float, dict[str, str]]] = {}
    for timestamp, post in sortable:
        unique[post["url"]] = (timestamp, post)
    ordered = sorted(unique.values(), key=lambda pair: pair[0], reverse=True)
    posts = [post for _, post in ordered[:MAX_POSTS]]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(
            {"sources": [url for _, url in FEEDS],
             "updated_at": datetime.now(timezone.utc).isoformat(),
             "posts": posts},
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    print(f"Saved {len(posts)} combined news posts to {OUTPUT}")


if __name__ == "__main__":
    main()
