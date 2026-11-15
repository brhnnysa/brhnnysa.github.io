#!/usr/bin/env python3
"""Build crawlable, individual event pages from the Bandsintown artist feed."""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import textwrap
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIST_ID = "15666367"
APP_ID = os.environ.get("BANDSINTOWN_APP_ID", "84610745a9c36219ffda7dc902864730")
BASE_URL = "https://www.brhnnysa.com"
ARTIST_URL = BASE_URL + "/"
FEED_URL = (
    f"https://rest.bandsintown.com/artists/id_{ARTIST_ID}/events/"
    f"?app_id={urllib.parse.quote(APP_ID)}&date=upcoming"
)
MANIFEST_PATH = ROOT / "data" / "events.json"
STATE_PATH = ROOT / "data" / "generated-event-pages.json"
SITEMAP_PATH = ROOT / "sitemap.xml"
IMAGE_MAP_PATH = ROOT / "data" / "event-images.json"

# Correct venue names confirmed by the artist when the feed supplies only a region.
VENUE_NAME_OVERRIDES = {"109156719": "Ryan Hotel and Suites Limited"}


def text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def slugify(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return value[:70].strip("-") or "live-show"


def event_slug(event: dict) -> str:
    identity = text(event.get("id")) or text(event.get("url"))
    if not identity:
        identity = "|".join(
            [text(event.get("title")), text(event.get("datetime")),
             text((event.get("venue") or {}).get("name"))]
        )
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:8]
    title = text(event.get("title")) or "Brhn Nysa Live"
    venue = text((event.get("venue") or {}).get("name"))
    date = text(event.get("datetime"))[:10]
    label = "-".join(part for part in [title, venue, date] if part)
    return f"{slugify(label)}-{digest}"


def display_title(event: dict) -> str:
    venue_name = text((event.get("venue") or {}).get("name"))
    title = text(event.get("title")) or "Brhn Nysa Live"
    if title.casefold() == venue_name.casefold() or title.casefold() in {"brhn nysa live", "live show", "concert", "event"}:
        description = text(event.get("description"))
        title = description.split(".")[0].strip() or "Brhn Nysa Live"
    return title


def parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def event_datetime(event: dict) -> datetime | None:
    """Interpret naive feed times in the venue's actual IANA timezone."""
    parsed = parse_datetime(text(event.get("datetime")))
    if not parsed or parsed.tzinfo is not None:
        return parsed
    venue = event.get("venue") or {}
    zone_name = (
        text(event.get("timezone")) or text(event.get("time_zone")) or
        text(venue.get("timezone")) or text(venue.get("time_zone"))
    )
    if not zone_name:
        latitude, longitude = venue.get("latitude"), venue.get("longitude")
        if latitude not in (None, "") and longitude not in (None, ""):
            try:
                from timezonefinder import TimezoneFinder
                zone_name = TimezoneFinder().timezone_at(
                    lat=float(latitude), lng=float(longitude)
                ) or ""
            except (ImportError, TypeError, ValueError):
                zone_name = ""
    if zone_name:
        try:
            return parsed.replace(tzinfo=ZoneInfo(zone_name))
        except ZoneInfoNotFoundError:
            pass
    # Retain the source wall time rather than inventing a timezone.
    return parsed


def safe_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


class EventImageParser(HTMLParser):
    """Extract the public poster and Event JSON-LD from a Bandsintown event page."""
    def __init__(self) -> None:
        super().__init__()
        self.image_url = ""
        self.jsonld: list[dict] = []
        self._in_jsonld = False
        self._script_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "meta":
            key = (values.get("property") or values.get("name") or "").lower()
            candidate = values.get("content", "").strip()
            if key in {"og:image", "og:image:url", "twitter:image", "twitter:image:src"} and candidate.startswith(("https://", "http://")):
                self.image_url = candidate
        elif tag.lower() == "script" and "ld+json" in values.get("type", "").lower():
            self._in_jsonld = True
            self._script_parts = []

    def handle_data(self, data: str) -> None:
        if self._in_jsonld:
            self._script_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._in_jsonld:
            raw = "".join(self._script_parts).strip()
            try:
                parsed = json.loads(raw)
                self.jsonld.append(parsed)
            except (json.JSONDecodeError, TypeError):
                pass
            self._in_jsonld = False
            self._script_parts = []


def _walk_jsonld(value: object):
    """Yield JSON-LD objects, including objects nested in @graph and arrays."""
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_jsonld(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_jsonld(child)


def event_page_metadata(event: dict) -> dict:
    """Read authoritative event fields from the public Bandsintown event page."""
    event_id = text(event.get("id"))
    event_url = text(event.get("url"))
    metadata: dict = {}
    if not event_url.startswith("https://www.bandsintown.com/e/"):
        return metadata
    try:
        request = urllib.request.Request(
            event_url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; BrhnNysaEventPages/1.0)",
                     "Accept": "text/html,application/xhtml+xml"},
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            page = response.read(3_000_000).decode("utf-8", errors="replace")
        parser = EventImageParser()
        parser.feed(page)
        if parser.image_url:
            metadata["image"] = parser.image_url
        # Bandsintown may embed the event payload in page scripts rather than JSON-LD.
        # Read only explicit end-date fields; never estimate an event duration.
        end_match = re.search(
            r"""["'](?:endDate|endDateTime|end_datetime|datetime_end|end_date|event_end_datetime)["']\s*:\s*["']([^"']+)["']""",
            page,
            flags=re.IGNORECASE,
        )
        if end_match:
            metadata["endDate"] = end_match.group(1)
        for root in parser.jsonld:
            for item in _walk_jsonld(root):
                kind = item.get("@type", [])
                kinds = [kind] if isinstance(kind, str) else kind
                if any(str(k).lower() in {"event", "musicevent"} for k in kinds):
                    for field in ("endDate", "startDate", "description", "image", "offers", "name"):
                        if item.get(field) not in (None, "", [], {}):
                            metadata[field] = item[field]
                    break
        return metadata
    except Exception as exc:
        print(f"Could not read event details from Bandsintown event {event_id}: {exc}")
        return metadata


def first_value(source: dict, keys: tuple[str, ...]):
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return value
    return None


def event_image_url(event: dict, image_overrides: dict, page_metadata: dict) -> str:
    """Prefer the artist's actual event artwork from the feed or Bandsintown page."""
    event_id = text(event.get("id"))
    candidate = (
        text(image_overrides.get(event_id)) or
        text(first_value(event, ("image", "image_url", "event_image", "poster", "poster_url"))) or
        text(page_metadata.get("image"))
    )
    return candidate if candidate.startswith(("https://", "http://")) else ""


def generated_event_artwork(event: dict, title: str, date: datetime | None) -> str:
    """Create a crawlable, event-specific branded fallback image when no real poster exists."""
    event_id = text(event.get("id")) or hashlib.sha1(title.encode("utf-8")).hexdigest()[:10]
    safe_id = re.sub(r"[^a-zA-Z0-9_-]+", "-", event_id)
    filename = f"brhn-nysa-event-{safe_id}.svg"
    output_dir = ROOT / "assets" / "event-artwork"
    output_dir.mkdir(parents=True, exist_ok=True)
    venue = event.get("venue") or {}
    venue_text = ", ".join(part for part in [
        text(venue.get("name")), text(venue.get("city")), text(venue.get("country"))
    ] if part)
    if date:
        event_hour = int(date.strftime("%I"))
        event_zone = date.strftime("%Z")
        date_text = f"{date.strftime('%d %b %Y')} · {event_hour}:{date.strftime('%M %p')} {event_zone}".strip()
    else:
        date_text = "UPCOMING LIVE EVENT"
    title_lines = textwrap.wrap(title, width=30)[:2] or ["LIVE EVENT"]
    title_svg = "".join(
        f'<text x="600" y="{760 + index * 76}" text-anchor="middle" class="event-title">{html.escape(line)}</text>'
        for index, line in enumerate(title_lines)
    )
    svg = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="1200" viewBox="0 0 1200 1200" role="img" aria-labelledby="title desc">
<title id="title">{html.escape(title)} · Brhn Nysa event artwork</title>
<desc id="desc">Brhn Nysa branded artwork for {html.escape(title)}{(" at " + html.escape(venue_text)) if venue_text else ""}.</desc>
<defs>
  <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#242017"/><stop offset=".55" stop-color="#0b0b0b"/><stop offset="1" stop-color="#1b160d"/></linearGradient>
  <radialGradient id="glow"><stop offset="0" stop-color="#d6b56d" stop-opacity=".34"/><stop offset="1" stop-color="#d6b56d" stop-opacity="0"/></radialGradient>
  <linearGradient id="gold" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#f4dfae"/><stop offset="1" stop-color="#b58a43"/></linearGradient>
  <pattern id="grain" width="44" height="44" patternUnits="userSpaceOnUse"><circle cx="2" cy="2" r="1" fill="#fff" opacity=".08"/></pattern>
</defs>
<rect width="1200" height="1200" fill="url(#bg)"/><rect width="1200" height="1200" fill="url(#grain)"/>
<circle cx="600" cy="470" r="440" fill="url(#glow)"/>
<rect x="70" y="70" width="1060" height="1060" rx="42" fill="none" stroke="#d6b56d" stroke-opacity=".55" stroke-width="3"/>
<text x="600" y="180" text-anchor="middle" fill="#d6b56d" font-family="Arial,sans-serif" font-size="38" font-weight="700" letter-spacing="13">BRHN NYSA</text>
<text x="600" y="245" text-anchor="middle" fill="#b8a47e" font-family="Arial,sans-serif" font-size="19" font-weight="700" letter-spacing="9">OFFICIAL EVENT</text>
<circle cx="600" cy="475" r="210" fill="#0c0b09" stroke="url(#gold)" stroke-width="5"/><circle cx="600" cy="475" r="178" fill="none" stroke="#d6b56d" stroke-opacity=".3" stroke-width="2"/>
<path d="M648 340v210c0 55-43 92-96 92-42 0-72-25-72-60 0-40 39-69 88-69 13 0 25 2 36 6V388l142-34v158c0 55-43 92-96 92-42 0-72-25-72-60 0-40 39-69 88-69 13 0 25 2 36 6V340z" fill="url(#gold)" transform="translate(-30,-28) scale(.92)"/>
<path d="M350 500h-44m588 0h-44M600 240v-38m0 584v-38" stroke="#d6b56d" stroke-opacity=".65" stroke-width="5" stroke-linecap="round"/>
{title_svg}
<text x="600" y="960" text-anchor="middle" fill="#e6c985" font-family="Arial,sans-serif" font-size="34" font-weight="700" letter-spacing="4">{html.escape(date_text)}</text>
<text x="600" y="1030" text-anchor="middle" fill="#aaa18e" font-family="Arial,sans-serif" font-size="24" letter-spacing="2">{html.escape(venue_text[:58] or "LIVE MUSIC EXPERIENCE")}</text>
<text x="600" y="1090" text-anchor="middle" fill="#776b54" font-family="Arial,sans-serif" font-size="17" letter-spacing="6">WWW.BRHNNYSA.COM</text>
</svg>
'''
    (output_dir / filename).write_text(svg, encoding="utf-8")
    return f"{BASE_URL}/assets/event-artwork/{filename}"


def build_page(event: dict, page_url: str) -> str:
    venue = event.get("venue") or {}
    venue_name = VENUE_NAME_OVERRIDES.get(text(event.get("id"))) or text(venue.get("name")) or "Venue to be announced"
    title = display_title(event)
    page_metadata = event_page_metadata(event)
    description = (
        text(event.get("description")) or
        text(page_metadata.get("description"))
    )
    if not description:
        city = text(venue.get("city"))
        region = text(venue.get("region"))
        country = text(venue.get("country"))
        place = ", ".join(part for part in [venue_name, city, region, country] if part)
        description = f"Join Brhn Nysa for a live music event at {place}."
    date_value = text(event.get("datetime"))
    date = event_datetime(event)
    start_date = date.isoformat() if date else date_value
    end_value = first_value(event, (
        "endDate", "end_date", "end_datetime", "datetime_end",
        "endDateTime", "event_end_datetime", "ends_at"
    )) or page_metadata.get("endDate")
    end_date = text(end_value)
    if isinstance(end_value, dict):
        end_date = text(end_value.get("@value") or end_value.get("dateTime"))
    if end_date:
        parsed_end = parse_datetime(end_date)
        if parsed_end and parsed_end.tzinfo is None and date and date.tzinfo:
            parsed_end = parsed_end.replace(tzinfo=date.tzinfo)
        if parsed_end:
            end_date = parsed_end.isoformat()
    venue_name = VENUE_NAME_OVERRIDES.get(text(event.get("id"))) or text(venue.get("name")) or "Venue to be announced"
    address = {
        "streetAddress": text(venue.get("street_address")),
        "addressLocality": text(venue.get("city")),
        "addressRegion": text(venue.get("region")),
        "postalCode": text(venue.get("postal_code")),
        "addressCountry": text(venue.get("country")),
    }
    address = {key: value for key, value in address.items() if value}
    location: dict = {"@type": "Place", "name": venue_name}
    if address:
        location["address"] = {"@type": "PostalAddress", **address}
    latitude, longitude = venue.get("latitude"), venue.get("longitude")
    if latitude not in (None, "") and longitude not in (None, ""):
        try:
            location["geo"] = {
                "@type": "GeoCoordinates",
                "latitude": float(latitude),
                "longitude": float(longitude),
            }
        except (TypeError, ValueError):
            pass

    event_url = text(event.get("url")) or page_url
    image_overrides = {}
    if IMAGE_MAP_PATH.exists():
        try:
            image_overrides = json.loads(IMAGE_MAP_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            image_overrides = {}
    image_url = event_image_url(event, image_overrides, page_metadata) or generated_event_artwork(event, title, date)
    feed_offers = event.get("offers")
    if not isinstance(feed_offers, list) or not feed_offers:
        feed_offers = page_metadata.get("offers", [])
    raw_offers = feed_offers if isinstance(feed_offers, list) else []
    schema_offers = []
    for raw_offer in raw_offers:
        if not isinstance(raw_offer, dict) or not text(raw_offer.get("url")):
            continue
        offer = {"@type": "Offer", "url": text(raw_offer.get("url"))}
        if text(raw_offer.get("type")):
            offer["name"] = text(raw_offer.get("type"))
        if raw_offer.get("price") not in (None, ""):
            offer["price"] = raw_offer.get("price")
        if text(raw_offer.get("currency")):
            offer["priceCurrency"] = text(raw_offer.get("currency"))
        status = text(raw_offer.get("status")).casefold()
        if status in {"available", "in stock", "instock"}:
            offer["availability"] = "https://schema.org/InStock"
        elif status in {"sold out", "sold_out", "soldout"}:
            offer["availability"] = "https://schema.org/SoldOut"
        schema_offers.append(offer)
    ticket_url = schema_offers[0]["url"] if schema_offers else event_url

    # Bandsintown event payloads may expose additional billed artists under
    # lineup/artists/performers. Use only names actually present in the feed.
    artist_names = []
    for field in ("lineup", "artists", "performers"):
        raw_artists = event.get(field)
        if isinstance(raw_artists, list):
            for artist in raw_artists:
                name = text(artist.get("name")) if isinstance(artist, dict) else text(artist)
                if name and name.casefold() not in {n.casefold() for n in artist_names}:
                    artist_names.append(name)
        elif isinstance(raw_artists, str) and raw_artists.strip():
            for name in raw_artists.split(","):
                name = name.strip()
                if name and name.casefold() not in {n.casefold() for n in artist_names}:
                    artist_names.append(name)
    if "Brhn Nysa" not in artist_names:
        artist_names.insert(0, "Brhn Nysa")
    organizer_entities = [
        {"@type": "Person", "name": name,
         **({"@id": BASE_URL + "/#artist", "url": ARTIST_URL} if name.casefold() == "brhn nysa" else {})}
        for name in artist_names
    ]
    schema = {
        "@context": "https://schema.org",
        "@type": "MusicEvent",
        "@id": page_url + "#event",
        "name": title,
        "url": page_url,
        "startDate": start_date,
        "organizer": organizer_entities,
        "eventStatus": "https://schema.org/EventScheduled",
        "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
        "location": location,
        "performer": organizer_entities,
        "description": description,
    }
    if end_date:
        schema["endDate"] = end_date
    if schema_offers:
        schema["offers"] = schema_offers
    if image_url:
        schema["image"] = [image_url]

    # Display source-provided start and end separately in the venue's local timezone.
    # Never estimate an end time or event duration.
    parsed_end_for_display = parse_datetime(end_date) if end_date else None
    if parsed_end_for_display and date and date.tzinfo:
        if parsed_end_for_display.tzinfo is None:
            parsed_end_for_display = parsed_end_for_display.replace(tzinfo=date.tzinfo)
        else:
            parsed_end_for_display = parsed_end_for_display.astimezone(date.tzinfo)
    def format_event_datetime(value):
        if not value:
            return "Not provided by Bandsintown"
        event_hour = int(value.strftime("%I"))
        local_value = f"{value.strftime('%a, %b %d, %Y')} · {event_hour}:{value.strftime('%M %p')}"
        zone = value.strftime("%Z") if value.tzinfo else ""
        return f"{local_value} {zone}".strip()
    display_start = format_event_datetime(date)
    display_end = format_event_datetime(parsed_end_for_display)
    address_text = ", ".join(
        value for value in [
            address.get("streetAddress"), address.get("addressLocality"),
            address.get("addressRegion"), address.get("postalCode"),
            address.get("addressCountry")
        ] if value
    )
    location_text = " · ".join(value for value in [venue_name, address_text] if value)
    safe_title = html.escape(title)
    safe_description = html.escape(description)
    safe_location = html.escape(location_text or "Venue details to be announced")
    safe_start = html.escape(display_start)
    safe_end = html.escape(display_end)
    organizers_html = ", ".join(html.escape(name) for name in artist_names)
    generated_image = "/assets/event-artwork/" in image_url
    image_html = (
        f'<figure class="event-poster{" generated-event-icon" if generated_image else ""}"><img class="event-image" src="{html.escape(image_url, quote=True)}" alt="{safe_title} event artwork" loading="lazy" /></figure>'
        if image_url else ""
    )
    schema_text = safe_json(schema)
    details_html = (
        f'<a class="action secondary" href="{html.escape(event_url, quote=True)}" target="_blank" rel="noopener noreferrer">VIEW DETAILS ↗</a>'
        if event_url else ""
    )
    ticket_html = (
        f'<a class="action primary" href="{html.escape(ticket_url, quote=True)}" target="_blank" rel="noopener noreferrer">GET TICKETS ↗</a>'
        if ticket_url else ""
    )
    countdown_target = date.isoformat() if date else ""
    safe_countdown_target = html.escape(countdown_target, quote=True)

    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_title} | Brhn Nysa</title>
<meta name="description" content="{safe_description}">
<link rel="canonical" href="{html.escape(page_url, quote=True)}">
<meta name="robots" content="index,follow">
<meta property="og:type" content="event">
<meta property="og:title" content="{safe_title} | Brhn Nysa">
<meta property="og:description" content="{safe_description}">
<meta property="og:url" content="{html.escape(page_url, quote=True)}">
{f'<meta property="og:image" content="{html.escape(image_url, quote=True)}">' if image_url else ''}
<script type="application/ld+json">{schema_text}</script>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#090909;color:#f5f5f5;font:16px/1.65 Arial,sans-serif}}
main{{width:min(760px,calc(100% - 32px));margin:54px auto;padding:30px;background:linear-gradient(145deg,#171717,#0d0d0d);border:1px solid #80683e;border-radius:14px}}
.brand{{color:#d6b56d;font-size:12px;font-weight:800;letter-spacing:.25em;text-transform:uppercase}}
h1{{font-size:clamp(28px,6vw,44px);line-height:1.15;margin:16px 0;color:#fff}}
.label{{color:#d6b56d;font-size:11px;font-weight:800;letter-spacing:.16em;margin-top:24px}}
.value{{margin-top:4px;color:#ddd;overflow-wrap:anywhere}}
.event-poster{{margin:14px 0 20px;max-width:190px;padding:6px;border:1px solid #3b3325;border-radius:9px;background:#111}}
.event-image{{display:block;width:100%;height:auto;max-height:125px;object-fit:contain;border-radius:5px;margin:0}}\n .event-poster.generated-event-icon{{width:58px;height:58px;max-width:58px;margin:14px 0 20px;padding:0;border:1px solid #80683e;border-radius:9px;overflow:hidden;background:#111}}\n .event-poster.generated-event-icon .event-image{{width:100%;height:100%;max-height:none;object-fit:cover}}
.action{{display:inline-block;margin:12px 8px 0 0;padding:11px 15px;border-radius:6px;text-decoration:none;font-size:11px;font-weight:900;letter-spacing:.08em}}
.action.primary{{background:#d6b56d;color:#111}}.action.secondary{{border:1px solid #80683e;color:#e6d5b4}}
.countdown{{margin-top:22px;padding:14px;border:1px solid #3b3325;border-radius:8px;background:#111;color:#d6b56d;font-weight:800}}
.back{{display:block;margin-top:24px;color:#d6b56d;text-decoration:none;font-size:13px}}
@media(max-width:600px){{main{{width:calc(100% - 20px);margin:18px auto;padding:18px}}.event-poster{{max-width:150px}}.event-image{{max-height:100px}}h1{{font-size:clamp(25px,8vw,34px)}}}}\nfooter{{color:#777;text-align:center;padding:24px;font-size:12px}}
</style>
</head>
<body>
<main>
<div class="brand">Brhn Nysa · Official Event</div>
<h1>{safe_title}</h1>
<p class="event-description">{safe_description}</p>
{image_html}
<div class="label">DATA</div><div class="value">{safe_start}</div>
<div class="label">END</div><div class="value">{safe_end}</div>
<div class="label">ORGANIZERS</div><div class="value">{organizers_html}</div>
<div class="countdown" id="event-countdown" data-start="{safe_countdown_target}">Countdown loading…</div>
<div class="label">VENUE &amp; LOCATION</div><div class="value">{safe_location}</div>
<div class="actions">{details_html}{ticket_html}</div>
<a class="back" href="{ARTIST_URL}">← Back to Brhn Nysa's official website</a>
<script>
(function(){{
 const box=document.getElementById('event-countdown');
 const raw=box&&box.getAttribute('data-start');
 if(!box||!raw){{if(box)box.textContent='Event date not available';return;}}
 const target=new Date(raw).getTime();
 function update(){{
  const remaining=target-Date.now();
  if(remaining<=0){{box.textContent='The event has started';return;}}
  const days=Math.floor(remaining/86400000);
  const hours=Math.floor((remaining%86400000)/3600000);
  const minutes=Math.floor((remaining%3600000)/60000);
  const seconds=Math.floor((remaining%60000)/1000);
  box.textContent='COUNTDOWN · '+days+'d '+hours+'h '+minutes+'m '+seconds+'s';
 }}
 update();setInterval(update,1000);
}})();
</script>
</main>
<footer>Official website of Brhn Nysa</footer>
</body>
</html>
'''


def main() -> None:
    request = urllib.request.Request(FEED_URL, headers={"Accept": "application/json", "User-Agent": "BrhnNysaEventPages/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        raise RuntimeError("Bandsintown did not return an event list; leaving existing event pages unchanged.")

    now = datetime.now(timezone.utc)
    upcoming = []
    for event in payload:
        if not isinstance(event, dict):
            continue
        parsed = event_datetime(event)
        if parsed and parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if parsed and parsed > now:
            upcoming.append((parsed, event))
    upcoming.sort(key=lambda item: item[0])

    image_overrides = {}
    if IMAGE_MAP_PATH.exists():
        try:
            image_overrides = json.loads(IMAGE_MAP_PATH.read_text(encoding="utf-8"))
            if not isinstance(image_overrides, dict):
                image_overrides = {}
        except (OSError, json.JSONDecodeError):
            image_overrides = {}

    old_state = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {"pages": []}
    old_pages = set(old_state.get("pages", []))
    new_pages: list[str] = []
    manifest_events = []
    sitemap_urls = {ARTIST_URL}

    for _, event in upcoming:
        slug = event_slug(event)
        relative = f"events/{slug}/index.html"
        page_url = f"{BASE_URL}/{relative}"
        page_path = ROOT / relative
        page_path.parent.mkdir(parents=True, exist_ok=True)
        page_path.write_text(build_page(event, page_url), encoding="utf-8")
        new_pages.append(relative)
        sitemap_urls.add(page_url)
        page_metadata = event_page_metadata(event)
        end_value = first_value(event, (
            "endDate", "end_date", "end_datetime", "datetime_end",
            "endDateTime", "event_end_datetime", "ends_at"
        )) or page_metadata.get("endDate")
        if isinstance(end_value, dict):
            end_value = end_value.get("@value") or end_value.get("dateTime")
        manifest_events.append({
            "id": text(event.get("id")),
            "title": display_title(event),
            "url": page_url,
            "bandsintown_url": text(event.get("url")),
            "datetime": text(event.get("datetime")),
            "end_datetime": text(end_value),
            "image": event_image_url(event, image_overrides, page_metadata) or generated_event_artwork(event, display_title(event), event_datetime(event)),
            "venue": event.get("venue") if isinstance(event.get("venue"), dict) else {},
            "description": text(event.get("description")),
            "lineup": event.get("lineup") if isinstance(event.get("lineup"), list) else [],
        })

    # Remove only pages previously created by this generator that are no longer upcoming.
    for relative in old_pages - set(new_pages):
        target = (ROOT / relative).resolve()
        if ROOT.resolve() in target.parents and target.name == "index.html" and target.exists():
            shutil.rmtree(target.parent, ignore_errors=True)

    (ROOT / "data").mkdir(exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps({"events": manifest_events}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    STATE_PATH.write_text(json.dumps({"pages": new_pages}, indent=2) + "\n", encoding="utf-8")

    # Keep the homepage URL and every generated upcoming event page discoverable.
    url_entries = "\n".join(f"  <url><loc>{html.escape(url)}</loc></url>" for url in sorted(sitemap_urls))
    SITEMAP_PATH.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + url_entries + "\n</urlset>\n",
        encoding="utf-8",
    )
    print(f"Generated {len(new_pages)} upcoming Brhn Nysa event page(s).")


if __name__ == "__main__":
    main()
