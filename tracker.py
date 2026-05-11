"""
Fetch NHS Jobs adverts via official search XML API, filter paediatric clinical
fellow roles in/near London, notify via ntfy, and persist seen IDs in seen.json.
"""

from __future__ import annotations

import json
import os
import re
import sys
import xml.etree.ElementTree as ET

import requests
from requests.exceptions import RequestException
from dotenv import load_dotenv

SEARCH_XML_ENDPOINT = "https://www.jobs.nhs.uk/api/v1/search_xml"
SEEN_PATH = os.environ.get("SEEN_PATH", "seen.json")
API_LIMIT = int(os.environ.get("API_LIMIT", "100"))

PAEDS_PATTERN = re.compile(r"paed|paediatric|paediatrics|pediatric", re.IGNORECASE)

# Matches plan intent: clinical fellow + common variants.
FELLOW_PATTERN = re.compile(
    r"clinical\s+fellow|clinical\s+teaching\s+fellow|clinical\s+research\s+fellow|"
    r"clinical\s+education\s+fellow|junior\s+clinical\s+fellow|senior\s+clinical\s+fellow",
    re.IGNORECASE,
)

# Load local .env for developer runs. In CI, environment variables/secrets still take priority.
load_dotenv()


def is_london(location: str, url: str) -> bool:
    location_text = location.lower()
    return "london" in location_text or "/London/" in url


def matches_filters(title: str, description: str) -> bool:
    haystack = f"{title} {description}"
    # Keep paediatric matching strict to the title to avoid unrelated fellow jobs
    # whose descriptions mention paediatric populations.
    return bool(PAEDS_PATTERN.search(title) and FELLOW_PATTERN.search(haystack))


def api_query(page: int) -> dict[str, str]:
    return {
        "keyword": os.environ.get("API_KEYWORD", "paediatric clinical fellow"),
        "location": os.environ.get("API_LOCATION", "London"),
        "distance": os.environ.get("API_DISTANCE", "25"),
        "staffGroup": os.environ.get("API_STAFF_GROUP", "MEDICAL_AND_DENTAL"),
        "sort": os.environ.get("API_SORT", "publicationDateDesc"),
        "limit": str(API_LIMIT),
        "page": str(page),
    }


def fetch_page(page: int) -> str:
    r = requests.get(SEARCH_XML_ENDPOINT, params=api_query(page), timeout=60)
    r.raise_for_status()
    return r.text


def element_text(parent: ET.Element, name: str) -> str:
    child = parent.find(name)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def parse_jobs(xml_text: str) -> tuple[list[dict], int]:
    root = ET.fromstring(xml_text)
    total_pages_text = element_text(root, "totalPages")
    total_pages = int(total_pages_text) if total_pages_text.isdigit() else 1
    items: list[dict] = []
    for vacancy in root.findall("vacancyDetails"):
        job_id = element_text(vacancy, "id")
        if not job_id:
            continue
        locations = []
        for loc in vacancy.findall("./locations/location"):
            if loc.text and loc.text.strip():
                locations.append(loc.text.strip())
        for loc in vacancy.findall("./locations/locations"):
            if loc.text and loc.text.strip():
                locations.append(loc.text.strip())
        location = "; ".join(locations)
        title = element_text(vacancy, "title")
        description = element_text(vacancy, "description")
        items.append(
            {
                "id": job_id,
                "title": title,
                "description": description,
                "employer": element_text(vacancy, "employer"),
                "location": location,
                "url": element_text(vacancy, "url"),
            }
        )
    return items, total_pages


def fetch_all_listings() -> list[dict]:
    all_rows: list[dict] = []
    seen_ids: set[str] = set()
    page = 1
    total_pages = 1
    while page <= total_pages:
        xml_text = fetch_page(page)
        rows, page_count = parse_jobs(xml_text)
        total_pages = page_count
        if not rows:
            break
        new_on_page = 0
        for row in rows:
            if row["id"] not in seen_ids:
                seen_ids.add(row["id"])
                all_rows.append(row)
                new_on_page += 1
        if new_on_page == 0:
            break
        page += 1
    return all_rows


def load_seen(path: str) -> list[str]:
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a JSON array of strings")
    return [str(x) for x in data]


def save_seen(path: str, ids: list[str]) -> None:
    out = sorted(set(ids))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
        f.write("\n")


def filter_matching(jobs: list[dict]) -> list[dict]:
    out: list[dict] = []
    for j in jobs:
        if not matches_filters(j["title"], j["description"]):
            continue
        if not is_london(j["location"], j["url"]):
            continue
        out.append(j)
    return out


def notify_ntfy(topic: str, job: dict) -> None:
    server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    url = f"{server}/{topic}"
    body = f"{job['employer']} — {job['location']}"
    headers = {
        "Title": job["title"][:200],
        "Click": job["url"],
    }
    r = requests.post(url, data=body.encode("utf-8"), headers=headers, timeout=30)
    r.raise_for_status()


def main() -> int:
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        print("NTFY_TOPIC is not set", file=sys.stderr)
        return 1

    try:
        jobs = fetch_all_listings()
    except (RequestException, ET.ParseError) as e:
        print(f"Failed to fetch listings: {e}", file=sys.stderr)
        return 1

    matching = filter_matching(jobs)
    matching_by_id = {j["id"]: j for j in matching}
    current_ids = sorted(matching_by_id.keys())

    try:
        seen = load_seen(SEEN_PATH)
    except (OSError, json.JSONDecodeError, ValueError) as e:
        print(f"Failed to read {SEEN_PATH}: {e}", file=sys.stderr)
        return 1

    seen_set = set(seen)

    if not seen_set:
        save_seen(SEEN_PATH, current_ids)
        print(f"Baseline: recorded {len(current_ids)} matching job(s), no notifications sent.")
        return 0

    if not current_ids:
        print("No matching jobs parsed; leaving seen.json unchanged.")
        return 0

    new_ids = sorted(set(current_ids) - seen_set)
    for jid in new_ids:
        job = matching_by_id[jid]
        try:
            notify_ntfy(topic, job)
            print(f"Notified: {job['title']} ({jid})")
        except requests.RequestException as e:
            print(f"ntfy failed for {jid}: {e}", file=sys.stderr)
            return 1

    updated = sorted(seen_set | set(current_ids))
    save_seen(SEEN_PATH, updated)
    print(f"Done. New: {len(new_ids)}, total seen: {len(updated)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
