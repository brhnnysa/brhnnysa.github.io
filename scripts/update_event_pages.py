#!/usr/bin/env python3
"""Build crawlable, individual event pages from the Bandsintown artist feed."""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import urllib.parse
import urllib.request
from datetime import datetime, timezone
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
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # Bandsintown's event datetime feed is UTC; make a naive API value explicit.
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def safe_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


def build_page(event: dict, page_url: str) -> str:
    venue = event.get("venue") or {}
    venue_name = text(venue.get("name")) or "Venue to be announced"
    title = display_title(event)
    description = text(event.get("description")) or f"See Brhn Nysa perform live at {venue_name}."
    date_value = text(event.get("datetime"))
    date = parse_datetime(date_value)
    # Bandsintown event times are venue-local; keep the original local ISO time
    # when no IANA timezone is supplied so Google can interpret it at the venue.
    start_date = date_value or (date.isoformat() if date else "")
    venue_name = text(venue.get("name")) or "Venue to be announced"
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
    image_url = text(event.get("image")) or text(event.get("image_url"))
    raw_offers = event.get("offers") if isinstance(event.get("offers"), list) else []
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
    schema = {
        "@context": "https://schema.org",
        "@type": "MusicEvent",
        "@id": page_url + "#event",
        "name": title,
        "url": page_url,
        "startDate": start_date,
        "eventStatus": "https://schema.org/EventScheduled",
        "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
        "location": location,
        "performer": {"@type": "Person", "@id": BASE_URL + "/#artist", "name": "Brhn Nysa"},
        "description": description,
    }
    if schema_offers:
        schema["offers"] = schema_offers
    if image_url:
        schema["image"] = [image_url]

    display_date = date.strftime("%A, %d %B %Y · %H:%M") if date else date_value
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
    safe_date = html.escape(display_date or "Date to be announced")
    image_html = (
        f'<img class="event-image" src="{html.escape(image_url, quote=True)}" alt="{safe_title}" />'
        if image_url else ""
    )
    schema_text = safe_json(schema)
    ticket_html = (
        f'<a class="ticket" href="{html.escape(ticket_url, quote=True)}" target="_blank" '
        'rel="noopener noreferrer">TICKETS / EVENT INFO ↗</a>'
        if ticket_url else ""
    )

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
.event-image{{display:block;width:100%;max-height:360px;object-fit:cover;border-radius:9px;margin:22px 0}}
.ticket{{display:inline-block;margin-top:28px;padding:12px 18px;border-radius:6px;background:#d6b56d;color:#111;text-decoration:none;font-size:12px;font-weight:900;letter-spacing:.1em}}
.back{{display:block;margin-top:24px;color:#d6b56d;text-decoration:none;font-size:13px}}
footer{{color:#777;text-align:center;padding:24px;font-size:12px}}
</style>
</head>
<body>
<main>
<div class="brand">Brhn Nysa · Official Event</div>
<h1>{safe_title}</h1>
{image_html}
<div class="label">DATE &amp; TIME</div><div class="value">{safe_date}</div>
<div class="label">VENUE &amp; LOCATION</div><div class="value">{safe_location}</div>
<p>{safe_description}</p>
{ticket_html}
<a class="back" href="{ARTIST_URL}">← Back to Brhn Nysa's official website</a>
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
        parsed = parse_datetime(text(event.get("datetime")))
        if parsed and parsed > now:
            upcoming.append((parsed, event))
    upcoming.sort(key=lambda item: item[0])

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
        manifest_events.append({
            "id": text(event.get("id")),
            "title": display_title(event),
            "url": page_url,
            "bandsintown_url": text(event.get("url")),
            "datetime": text(event.get("datetime")),
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
