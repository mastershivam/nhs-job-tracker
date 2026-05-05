# NHS Paediatric clinical fellow job tracker

Checks the official NHS Jobs API endpoint [`/api/v1/search_xml`](https://www.jobs.nhs.uk/api/v1/search_xml) on a schedule, filters **London** roles matching **paediatric clinical fellow** criteria, and sends one [ntfy](https://ntfy.sh/) notification per **new** vacancy.

State is stored in [`seen.json`](seen.json) (committed by GitHub Actions when it changes).

## Setup

### 1. ntfy topic

1. Pick a **hard-to-guess** topic name (acts like a password on the public ntfy.sh server).
2. On your phone, install the **ntfy** app and subscribe to `https://ntfy.sh/your-topic-name` (or your self-hosted server).
3. In the GitHub repo: **Settings → Secrets and variables → Actions → New repository secret**
   - Name: `NTFY_TOPIC`
   - Value: your topic name (not the full URL).

Optional: use a private ntfy server by setting a secret is not supported for the server URL in the default workflow; instead fork and set `NTFY_SERVER` in the workflow env, or run locally with `NTFY_SERVER` set (see below).

### 2. Push this repo to GitHub

The workflow [`.github/workflows/check-jobs.yml`](.github/workflows/check-jobs.yml) runs **hourly** and on **manual dispatch** (Actions → workflow → Run workflow).

The first successful run records a **baseline** of all currently matching jobs in `seen.json` and **does not** send notifications. Later runs notify only when **new** job IDs appear.

### Local run

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
# create a .env file with: NTFY_TOPIC=your-topic
python tracker.py
```

Optional environment variables:

| Variable | Description |
|----------|-------------|
| `NTFY_TOPIC` | Required. Topic name on ntfy.sh. |
| `NTFY_SERVER` | Default `https://ntfy.sh`. |
| `SEEN_PATH` | Path to state file (default `seen.json`). |
| `MAX_PAGES` | Safety cap on listing pages (default `50`). |
| `API_KEYWORD` | API keyword filter (default `paediatric clinical fellow`). |
| `API_LOCATION` | API location filter (default `London`). |
| `API_DISTANCE` | API distance filter in miles (default `25`). |
| `API_STAFF_GROUP` | API staff group filter (default `MEDICAL_AND_DENTAL`). |
| `API_SORT` | API sort option (default `publicationDateDesc`). |
| `API_LIMIT` | Results per API page, max 100 (default `100`). |

The script auto-loads variables from `.env` via `python-dotenv` for local runs.

## Verification

1. First local run with empty `seen.json`: should print a baseline message and fill `seen.json`.
2. Remove one ID from `seen.json`, run again: should send one ntfy message for that job (if it still matches and is still listed).
