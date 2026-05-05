"""
Fetch NHS Jobs Medical Paediatrics listings, filter London clinical fellow roles,
notify via ntfy, persist seen vacancy IDs in seen.json.
"""

from __future__ import annotations

import json
import os
import re
import sys
from urllib.parse import urljoin

import requests
from curl_cffi import requests as curl_requests
from requests.exceptions import RequestException
from bs4 import BeautifulSoup
from dotenv import load_dotenv

BASE_SITE = "https://www.nhsjobs.com"
LIST_PATH = "/job_list/Medical_and_Dental/s2/Medical_Paediatrics/d578"
DEFAULT_LIST_QUERY = "_srt=grade&_sd=a&_ts=1"
SEEN_PATH = os.environ.get("SEEN_PATH", "seen.json")
MAX_PAGES = int(os.environ.get("MAX_PAGES", "50"))

# Minimal browser-like headers (some environments block python-requests otherwise).
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-GB,en;q=0.9",
}

# Matches plan intent: clinical fellow + common variants (e.g. clinical teaching fellow).
FELLOW_PATTERN = re.compile(
    r"clinical\s+fellow|clinical\s+teaching\s+fellow|clinical\s+research\s+fellow|"
    r"clinical\s+education\s+fellow|junior\s+clinical\s+fellow|senior\s+clinical\s+fellow",
    re.IGNORECASE,
)

JOB_ID_PATTERN = re.compile(r"-v(\d+)(?:\?|$|&)")

# Load local .env for developer runs. In CI, environment variables/secrets still take priority.
load_dotenv()


def list_url(page: int) -> str:
    q = os.environ.get("JOB_LIST_QUERY", DEFAULT_LIST_QUERY)
    parts = [f"{BASE_SITE}{LIST_PATH}?"]
    if q.strip():
        parts.append(q.strip())
        parts.append("&")
    parts.append(f"_pg={page}")
    return "".join(parts)


def job_id_from_href(href: str) -> str | None:
    m = JOB_ID_PATTERN.search(href)
    return m.group(1) if m else None


def text_or_empty(node) -> str:
    if node is None:
        return ""
    return node.get_text(" ", strip=True)


def is_london(full_url: str, location: str) -> bool:
    if "/UK/London/" in full_url:
        return True
    return "london" in location.lower()


def matches_fellow_filter(title: str, grade: str) -> bool:
    blob = f"{title} {grade}"
    return bool(FELLOW_PATTERN.search(blob))


def fetch_page(page: int) -> str:
    """TLS fingerprint + headers similar to Chrome (plain urllib/requests often get 403)."""
    url = list_url(page)
    impersonate = os.environ.get("CURL_CFFI_IMPERSONATE", "chrome120")
    r = curl_requests.get(
        url,
        impersonate=impersonate,
        headers=REQUEST_HEADERS,
        timeout=60,
    )
    r.raise_for_status()
    return r.text


def parse_jobs(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []
    for li in soup.select("li.hj-job"):
        a = li.find("a", href=True)
        if not a:
            continue
        href = a["href"].strip()
        if "/job/UK/" not in href:
            continue
        job_id = job_id_from_href(href)
        if not job_id:
            continue
        title_el = li.select_one(".hj-jobtitle")
        grade_el = li.select_one(".hj-grade")
        employer_el = li.select_one(".hj-employername")
        location_el = li.select_one(".hj-locationtown")
        title = text_or_empty(title_el) or (a.get("title") or "").strip()
        grade = text_or_empty(grade_el)
        employer = text_or_empty(employer_el)
        location = text_or_empty(location_el)
        full_url = urljoin(BASE_SITE, href)
        items.append(
            {
                "id": job_id,
                "title": title,
                "grade": grade,
                "employer": employer,
                "location": location,
                "url": full_url,
            }
        )
    return items


def fetch_all_listings() -> list[dict]:
    all_rows: list[dict] = []
    seen_ids: set[str] = set()
    for page in range(1, MAX_PAGES + 1):
        html = fetch_page(page)
        rows = parse_jobs(html)
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
        if not matches_fellow_filter(j["title"], j["grade"]):
            continue
        if not is_london(j["url"], j["location"]):
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
    except RequestException as e:
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
