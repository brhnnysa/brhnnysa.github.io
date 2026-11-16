# Read-only diagnostics for Audio.com owned collections; no account changes.
#!/usr/bin/env python3
"""Read-only authentication check for the Audio.com Public API."""
import json
import os
import sys
import requests

token = os.environ.get("AUDIOCOM_TOKEN", "").strip()
if not token:
    raise SystemExit("FAIL: GitHub Actions secret AUDIOCOM_TOKEN is missing or empty.")

url = "https://api.audio.com/v1/audio/list"
try:
    response = requests.get(
        url,
        params={"sort": "newest", "page": 1, "per-page": 100},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=30,
    )
except requests.RequestException as exc:
    raise SystemExit(f"FAIL: Audio.com API request failed: {type(exc).__name__}: {exc}")

if response.status_code == 401:
    raise SystemExit("FAIL: Audio.com rejected the token (401). Check that AUDIOCOM_TOKEN is a fresh, valid access token.")
if response.status_code != 200:
    raise SystemExit(f"FAIL: Audio.com API returned HTTP {response.status_code}: {response.text[:500]}")

try:
    data = response.json()
except ValueError:
    raise SystemExit("FAIL: Audio.com returned a non-JSON response.")

if not isinstance(data, list):
    raise SystemExit("FAIL: Audio.com returned an unexpected response shape.")

summary = []
for item in data:
    if not isinstance(item, dict):
        continue
    summary.append({
        "title": item.get("title"),
        "slug": item.get("slug"),
        "image_available": bool(item.get("image")),
        "category": item.get("category"),
        "listed": item.get("is_listed"),
    })

print("PASS: AUDIOCOM_TOKEN was accepted by Audio.com's authenticated API.")
print(json.dumps({"owned_audio_count_on_page": len(summary), "audio": summary}, ensure_ascii=False, indent=2))
search_response = requests.get(
    "https://api.audio.com/v1/search",
    params={"q": "Brhn Nysa", "types": "collection", "page": 1},
    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    timeout=30,
)
if search_response.status_code != 200:
    raise SystemExit(f"FAIL: Audio.com collection search returned HTTP {search_response.status_code}: {search_response.text[:500]}")
try:
    search_data = search_response.json()
except ValueError:
    raise SystemExit("FAIL: Audio.com collection search returned non-JSON data.")
print("COLLECTION_SEARCH:")
print(json.dumps(search_data, ensure_ascii=False, indent=2)[:12000])

collection_response = requests.get(
    "https://api.audio.com/v1/collection/list",
    params={"sort": "newest", "page": 1, "per-page": 100},
    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    timeout=30,
)
print(f"OWNED_COLLECTION_LIST_HTTP: {collection_response.status_code}")
print("OWNED_COLLECTION_LIST_BODY:")
print(collection_response.text[:12000])
if collection_response.status_code == 200:
    try:
        owned = collection_response.json()
    except ValueError:
        owned = []
    for collection in owned if isinstance(owned, list) else []:
        collection_id = collection.get("id")
        if not collection_id:
            continue
        detail = requests.get(
            "https://api.audio.com/v1/collection/view",
            params={"id": collection_id},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=30,
        )
        print(f"COLLECTION_DETAIL {collection.get('title')} HTTP {detail.status_code}:")
        print(detail.text[:6000])
        for endpoint in ("item/list", "items"):
            item_response = requests.get(
                f"https://api.audio.com/v1/collection/{endpoint}",
                params={"id": collection_id, "collection_id": collection_id, "page": 1, "per-page": 100},
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                timeout=30,
            )
            print(f"COLLECTION_ITEMS {endpoint} for {collection.get('title')} HTTP {item_response.status_code}:")
            print(item_response.text[:6000])
print("NOTE: Read-only collection detail test; no account data is modified.")
