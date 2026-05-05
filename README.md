# NHS Paediatric clinical fellow job tracker

Checks [NHS Jobs — Medical: Paediatrics](https://www.nhsjobs.com/job_list/Medical_and_Dental/s2/Medical_Paediatrics/d578?_srt=grade&_sd=a&_ts=1) on a schedule, filters **London** roles whose title/grade match **clinical fellow** (and common variants such as clinical teaching fellow), and sends one [ntfy](https://ntfy.sh/) notification per **new** vacancy.

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
set NTFY_TOPIC=your-topic
python tracker.py
```

Listings are fetched with **[curl_cffi](https://github.com/lwthiker/curl_cffi)** (`chrome120` TLS impersonation) because plain `requests` often receives HTTP **403** (“Site unavailable”) from nhsjobs.com.

Optional environment variables:

| Variable | Description |
|----------|-------------|
| `NTFY_TOPIC` | Required. Topic name on ntfy.sh. |
| `NTFY_SERVER` | Default `https://ntfy.sh`. |
| `SEEN_PATH` | Path to state file (default `seen.json`). |
| `JOB_LIST_QUERY` | Query string before `&_pg=` (default `_srt=grade&_sd=a&_ts=1`). |
| `MAX_PAGES` | Safety cap on listing pages (default `50`). |
| `CURL_CFFI_IMPERSONATE` | Browser profile for TLS impersonation (default `chrome120`). |

## Verification

1. First local run with empty `seen.json`: should print a baseline message and fill `seen.json`.
2. Remove one ID from `seen.json`, run again: should send one ntfy message for that job (if it still matches and is still listed).
