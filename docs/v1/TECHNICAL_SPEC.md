# JobRadar — Technical Specification
**Version:** 1.0  
**Date:** July 2026  
**Stack:** Python 3.11 · GitHub Actions · Google Sheets API · Apify · JobSpy · Claude Haiku

---

## 1. System Overview

JobRadar is a serverless Python pipeline running on GitHub Actions. It has no persistent server — every run is stateless. All state (seen jobs, scores, run history) lives in Google Sheets, making it free to operate beyond API costs.

```
GitHub Actions (cron / manual trigger)
        ↓
  main.py  (orchestrator, 11 steps)
        ↓
  ┌─────────────────────────────────────┐
  │  scraper.py  →  normalizer.py       │
  │  dedup.py    →  scorer.py           │
  │  validator.py → resume_generator.py │
  │  sheets.py   →  budget_guard.py     │
  └─────────────────────────────────────┘
        ↓
  Google Sheets (5 tabs)  +  Google Drive (resumes)
```

---

## 2. Module Reference

### 2.1 `main.py` — Orchestrator
**Role:** Runs all 11 pipeline steps in order. Every step is wrapped in try/except — a failure in one step logs the error and continues to the next. The run never hard-crashes.

**Pipeline Steps:**
| Step | Action | File |
|---|---|---|
| 1 | Load config.yaml + init budget guard | budget_guard.py |
| 2 | Connect to Google Sheet | sheets.py |
| 3 | Scrape all sources | scraper.py |
| 4 | Normalize to common schema | normalizer.py |
| 5 | Dedup against SeenJobs ledger | dedup.py |
| 6 | Append new IDs to SeenJobs (crash-safe) | dedup.py |
| 7 | Stage A scoring (free, rule-based) | scorer.py |
| 8 | Company page validation | validator.py |
| 9 | Stage B scoring (Claude Haiku) | scorer.py |
| 10 | ATS resume generation (top jobs) | resume_generator.py |
| 11 | Write to Sheet + Run Log | sheets.py |

---

### 2.2 `src/scraper.py` — Scraping Layer

**Apify Actors Used:**
- `valig/linkedin-jobs-scraper` — LinkedIn jobs
- `epic-scrapers/naukri-scraper` — Naukri jobs

**JobSpy Sites:**
- India Remote: `["indeed", "google"]`
- Global Remote: `["indeed", "glassdoor", "google", "zip_recruiter"]`

**Key design decisions:**
- Each source runs TWICE per run: once for Tier 1 titles, once for Tier 2
- This ensures every job is tagged with `role_tier` before scoring
- `_apify_client()` raises `EnvironmentError` if `APIFY_TOKEN` is missing — caught gracefully
- Polite delays: 1s between LinkedIn calls, 2s between JobSpy calls

**Apify actor call:**
```python
run = client.actor(actor_id).call(run_input=run_input, wait_secs=timeout_secs)
items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
```

---

### 2.3 `src/normalizer.py` — Schema Normalization

**Purpose:** Maps every source's wildly different raw dict into a single unified schema.

**Unified schema fields:**
```python
{
  "job_id": str,              # SHA-256 hash (first 20 chars)
  "source": str,              # linkedin | naukri | indeed | glassdoor | google | zip_recruiter
  "category": str,            # mumbai | india_remote | naukri | global_remote
  "role_tier": str,           # tier1_core_data | tier2_broader
  "title": str,
  "company": str,
  "location": str,
  "posted_date": str,         # ISO date string
  "days_old": int | None,
  "url": str,
  "experience_required_text": str,
  "experience_required_min": int | None,   # extracted via regex
  "salary_text": str,
  "salary_currency": str,
  "description_text": str,    # truncated to 4000 chars
  "is_startup": bool,
  "first_seen_date": str,
  "last_seen_date": str,
  "validation_status": str,   # pending | valid | stale | flagged
  "validation_note": str,
}
```

**YOE Extraction (regex-based):**
- Patterns: "4+ years", "2-5 years", "minimum of 3 years", "at least 5 years", "1 to 3 years", "2 yrs"
- Junior signals: "fresher", "entry-level", "junior", "graduate program", "trainee" → treated as 0 YOE
- Returns minimum YOE found across all patterns

**job_id generation:**
```python
raw = f"{source}|{company.lower()}|{title.lower()}|{location.lower()}"
job_id = hashlib.sha256(raw.encode()).hexdigest()[:20]
```
Content-based hash — immune to source re-issuing listing IDs.

**Role Tier Resolution:**
If a Tier 2 search returns a job with "Data Analyst" in the title, the normalizer upgrades it to `tier1_core_data` — never downgrade genuine data roles.

---

### 2.4 `src/scorer.py` — Two-Stage Scoring Engine

#### Stage A Formula (Free, Rule-Based)
```
score = (skill_weight * skill_adj)
      + (exp_weight   * exp_fit)
      + (comp_weight  * comp_fit)
      + recency_bonus
      + region_bonus
      - tier2_flat_penalty

Clamped to [0.0, 10.0]
```

**Weights (from config.yaml):**
| Component | Weight | Range |
|---|---|---|
| Skill Match | 0.35 | 0–10 |
| Experience Fit | 0.25 | 0–10 |
| Comp Fit | 0.25 | 0–10 |
| Recency Bonus | additive | 0–2.0 |
| Region Bonus | additive | 0–1.5 |
| Tier 2 Penalty | subtractive | -0.5 |

**Experience Gate Logic:**
| Min YOE Required | Gate Label | Exp Fit Score | Routing |
|---|---|---|---|
| 0 | ideal_fresher | 10 | Job Tracker |
| 1 | ideal_1yr | 9 | Job Tracker |
| 2 | pass | 6 | Job Tracker |
| 3–4 | stretch | 3 | Job Tracker (flagged) |
| >4 | exclude | 0 | Reach Roles tab |

**Recency Bonuses:**
| Age | Bonus |
|---|---|
| 0–3 days | +2.0 |
| 4–7 days | +1.5 |
| 8–14 days | +0.75 |
| 15–30 days | +0.25 |
| >30 days | +0.0 |

**Region Bonuses:**
| Region | Bonus |
|---|---|
| Mumbai | +1.5 |
| Naukri | +1.0 |
| India Remote | +0.75 |
| Global Remote | +0.0 |

**Routing Rules (Stage A):**
- `gate_label == "exclude"` → Reach Roles tab
- `stage_a_score < 2.0` → Skipped entirely
- Otherwise → Job Tracker

#### Stage B (Claude Haiku — Paid)
- Only runs on jobs with `stage_a_score >= 6.0`
- Batches up to 10 JDs per API call
- Cost estimate: ~$0.001 per job (Haiku pricing)
- Output per job: `refined_score`, `fit_note`, `red_flags[]`, `is_genuine_data_role`
- Final `overall_score = (stage_a_score + stage_b_refined_score) / 2`
- Red flag: if `is_genuine_data_role == false`, adds routing flag "Title may not match actual role"

**System prompt context:** Nagesh's full profile is embedded in the system prompt — Claude knows his exact YOE, skills, and certifications.

---

### 2.5 `src/dedup.py` — Deduplication

**SeenJobs tab schema:** `[job_id, first_seen_date, last_seen_date]`

**Flow:**
1. Load all `job_id`s from SeenJobs tab into a Python set
2. Filter new jobs: those whose `job_id` is NOT in the set
3. Identify re-sighted jobs: those whose `job_id` IS in the set
4. **Immediately append new IDs to SeenJobs** (before scoring — crash-safe)
5. After run: update `last_seen_date` for re-sighted jobs
6. Archive jobs with `last_seen_date` > 30 days ago

---

### 2.6 `src/validator.py` — Company Page Validation

- Runs HTTP GET on the company's careers page / job URL
- Checks if the job title still appears on the page
- Returns:
  - `valid` — listing still live
  - `stale` — URL returns 404 or title not found
  - `flagged` — request failed (timeout, blocked)
- Only validates jobs with `stage_a_score >= 5.0`
- User-agent: `Mozilla/5.0 (JobRadar validation bot; contact: nageshkhichade00@gmail.com)`

---

### 2.7 `src/sheets.py` — Google Sheets Integration

**Auth:** `gspread.service_account_from_dict(creds_info)` (gspread v6+ API)

**Scopes:**
- `https://www.googleapis.com/auth/spreadsheets`
- `https://www.googleapis.com/auth/drive`

**Key operations:**
| Function | What it does |
|---|---|
| `connect_sheet(sheet_id)` | Auth + open sheet by ID |
| `ensure_tabs(sheet, config)` | Create all 5 tabs with headers if missing |
| `append_job_rows(sheet, jobs, tab)` | Batch append job rows |
| `log_run(sheet, stats)` | Append one row to Run Log |

**Job Tracker tab columns (30 columns):**
`#, job_id, Category, Role Tier, Job Title, Company, Location, Posted Date, Days Old, Recency Bucket, Exp. Required, Exp. Gate, Startup?, Pay, Currency, Skill Match, Experience Fit, Comp Fit, Recency Bonus, Region Bonus, Stage A Score, Stage B Score, Overall Score, Apply Link, Resume Link, Validation, Fit Note, Red Flags, Source, First Seen`

---

### 2.8 `src/budget_guard.py` — Monthly Budget Guard

- Reads current month's `spend_usd` from Run Log tab on init (lazy)
- `check_and_debit(service, amount)`: raises `BudgetExceeded` if ceiling hit
- In-memory running total — written to sheet at end of run via `log_run()`
- Hard ceiling: `$10.00/month` (config-driven)
- Degraded mode: once triggered, ALL paid calls raise `BudgetExceeded` for rest of run

---

### 2.9 `src/resume_generator.py` — ATS Resume Generator

- Triggers for jobs with `overall_score >= 7.5`, max 5 per day
- Uses Claude Haiku to tailor `resume_base.json` to the job description
- Outputs `.docx` format (python-docx library)
- Uploads to Google Drive folder (`GOOGLE_DRIVE_FOLDER_ID`)
- Stores Drive share link as `resume_link` in job dict → written to sheet

---

## 3. Configuration Reference (`config.yaml`)

All tunable parameters without touching Python code:

| Section | Key Parameters |
|---|---|
| `candidate_profile` | YOE band, skill list, title tiers, currency targets |
| `sources` | Each scraper's location, max results, charge limits |
| `scoring` | All weights, bonuses, thresholds |
| `validation` | Enable/disable, timeout, on-failure behavior |
| `budget` | Monthly ceiling in USD |
| `sheets` | Tab names, archive threshold |
| `llm` | Model names, batch size, JD truncation |

---

## 4. GitHub Actions Workflow

**File:** `.github/workflows/daily_job_scan.yml`

**Triggers:**
- `schedule: cron: '0 2 * * *'` (daily at 7:30 AM IST)
- `workflow_dispatch` (manual trigger with optional `dry_run` input)

**Secrets Required:**
| Secret | Used By |
|---|---|
| `GOOGLE_SHEET_ID` | sheets.py — identifies the spreadsheet |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | sheets.py, resume_generator.py — auth |
| `APIFY_TOKEN` | scraper.py — LinkedIn + Naukri actors |
| `ANTHROPIC_API_KEY` | scorer.py, resume_generator.py — Claude |
| `GOOGLE_DRIVE_FOLDER_ID` | resume_generator.py — resume upload |

**Artifacts uploaded per run:**
- `jobradar-run-log-{run_number}` — `jobradar_run.log` (14-day retention)
- `jobradar-resumes-{run_number}` — generated `.docx` files (30-day retention)

---

## 5. Data Flow (End-to-End)

```
Raw scraped dict (LinkedIn/Naukri/Indeed/Glassdoor)
    ↓  normalizer.py
Normalized job dict (unified schema)
    ↓  dedup.py
New job (not in SeenJobs) OR Re-sighted (update last_seen)
    ↓  scorer.py (Stage A)
Scored job: stage_a_score, routing, gate_label
    ↓  validator.py
validation_status: valid | stale | flagged
    ↓  scorer.py (Stage B, if score >= 6.0)
refined_score, fit_note, red_flags, overall_score
    ↓  resume_generator.py (if score >= 7.5)
tailored .docx → Google Drive → resume_link
    ↓  sheets.py
Written to Job Tracker or Reach Roles tab
```

---

## 6. Error Handling Strategy

Every step in `main.py` is wrapped:
```python
try:
    result = do_step()
except SpecificException as e:
    logger.warning(...)
    stats["errors"].append(...)
    # degrade gracefully, don't crash
except Exception as e:
    logger.error(..., exc_info=True)
    stats["errors"].append(...)
```

Critical steps (Scrape, Normalize) return early if they fail. All others continue even on failure. The Run Log always gets written (via `_finalize()`), even if the run crashed midway.

---

## 7. Dependencies

| Package | Version | Purpose |
|---|---|---|
| python-jobspy | >=0.1.0 | Free job scraping |
| apify-client | >=1.7.0 | LinkedIn + Naukri via Apify |
| gspread | >=6.1.0 | Google Sheets read/write |
| google-auth | >=2.29.0 | Service account auth |
| google-api-python-client | >=2.125.0 | Google Drive upload |
| anthropic | >=0.28.0 | Claude Haiku API |
| python-docx | >=1.1.0 | .docx resume generation |
| pyyaml | >=6.0.1 | config.yaml parsing |
| requests | >=2.31.0 | Company page validation |
| python-dateutil | >=2.9.0 | Multi-format date parsing |
