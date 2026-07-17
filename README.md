# JobRadar — Daily Mumbai/India + High-Pay Remote Job Intelligence Agent

Automated daily pipeline for Nagesh Khichade's AI/ML/Data job search.  
Runs on GitHub Actions at **7:30 AM IST** every day. No manual trigger needed. ~$0.10–0.30/day.

---

## What it does

1. **Scrapes** LinkedIn (Apify), Naukri (Apify), Indeed/Glassdoor/Google/ZipRecruiter (JobSpy)
2. **Deduplicates** using a permanent content-hash-based ledger — zero repeats ever
3. **Validates** that job listings are still live on company careers pages (flags stale posts)
4. **Scores** every new job: free rule-based Stage A (100% of jobs) + Claude Haiku Stage B (top scorers)
5. **Generates** tailored ATS resumes (.docx) for top 5 jobs daily, uploaded to Google Drive
6. **Writes** everything to a live Google Sheet with 5 tabs — always fresh, sortable, filterable

---

## Tabs in the Google Sheet

| Tab | Purpose |
|-----|---------|
| **Job Tracker** | Main live view — all new jobs sorted by Overall Score |
| **SeenJobs** | Hidden dedup ledger — never touch this manually |
| **Archive** | Jobs older than 30 days (auto-moved) |
| **Reach Roles (5yr+)** | Jobs requiring >4 YOE — visible but separated |
| **Run Log** | One row per daily run — check this first if something looks off |

---

## First-time Setup

### 1. Fork / clone this repo
```bash
git clone https://github.com/YOUR_USERNAME/job-appl.git
cd job-appl
```

### 2. Create a fresh Google Sheet
- Go to [sheets.google.com](https://sheets.google.com) → Create new sheet
- Name it anything (e.g. `JobRadar Live`)
- Copy the Sheet ID from the URL: `https://docs.google.com/spreadsheets/d/**SHEET_ID**/edit`

### 3. Set up Google Cloud service account
1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a new project (or use existing)
3. Enable **Google Sheets API** and **Google Drive API**
4. Create a **Service Account** → Create key → **JSON** type
5. Download the JSON key file
6. Share your Google Sheet with the service account email (Editor access)
7. Create a Google Drive folder for resumes → share with same service account email
8. Copy the Drive folder ID from its URL

### 4. Get API keys
- **Apify**: [apify.com](https://apify.com) → Account → API tokens
- **Anthropic**: [console.anthropic.com](https://console.anthropic.com) → API Keys

### 5. Add GitHub Secrets
Go to your repo → Settings → Secrets and variables → Actions → New repository secret:

| Secret Name | Value |
|-------------|-------|
| `APIFY_TOKEN` | Your Apify API token |
| `ANTHROPIC_API_KEY` | Your Anthropic API key |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Full contents of your service account JSON key |
| `GOOGLE_SHEET_ID` | ID of your Google Sheet |
| `GOOGLE_DRIVE_FOLDER_ID` | ID of your Google Drive resumes folder |

### 6. Populate resume_base.json ← **Required for resume generation**
Open [`resume_base.json`](resume_base.json) and:
- Replace all `YOUR_*`, `YYYY-MM`, and `Placeholder —` values with real data
- Update duration_months for internships
- Change `_validation_sentinel` to `"POPULATED"` when done
- The pipeline will silently skip resume gen (no error) until this is done

---

## Running Locally (for testing)

```bash
# Install deps
pip install -r requirements.txt

# Set env vars (Windows PowerShell)
$env:APIFY_TOKEN="your_token"
$env:ANTHROPIC_API_KEY="your_key"
$env:GOOGLE_SERVICE_ACCOUNT_JSON='{"type":"service_account",...}'
$env:GOOGLE_SHEET_ID="your_sheet_id"
$env:GOOGLE_DRIVE_FOLDER_ID="your_folder_id"

# Run pipeline
python main.py

# Run tests (no API keys needed for tests)
python -m pytest tests/ -v
```

---

## Retuning without touching code

All scoring thresholds, title lists, and budget settings live in **[`config.yaml`](config.yaml)**.

Key sections:
- `candidate_profile.target_titles_tier1_core_data` — add/remove Tier 1 job titles
- `candidate_profile.target_titles_tier2_broader` — add/remove Tier 2 titles
- `scoring.recency_bonus` — adjust recency weight (0–3 days currently gets highest bonus)
- `scoring.resume_min_score` — threshold for resume generation (currently 7.5)
- `budget.monthly_ceiling_usd` — hard monthly spend cap (currently $10)
- `validation.enabled` — toggle company page validation on/off

---

## Cost breakdown (target ~$0.10–0.30/day)

| Item | Est. Cost | Cap |
|------|-----------|-----|
| Apify LinkedIn | ~$0.02–0.05/day | `max_charge_usd_per_call` per call |
| Apify Naukri | ~$0.01–0.03/day | `max_charge_usd_per_call` per call |
| JobSpy (Indeed/Glassdoor/Google/ZipRecruiter) | **$0** | Free library |
| Claude Haiku Stage B scoring | ~$0.02–0.08/day | Budget guard trips at $10/month |
| Claude Haiku resume generation | ~$0.05–0.15/day | Hard cap: 5 resumes/day |
| GitHub Actions compute | **$0** | Well within free tier |
| Google Sheets/Drive API | **$0** | Free tier quota far exceeds this volume |
| **Total** | **~$0.10–0.30/day** | **$10/month hard cap** |

---

## Checking if something went wrong

1. Open your Google Sheet → **Run Log** tab
2. Check the `errors` column for that day's run
3. Download the run log artifact from GitHub Actions (Repo → Actions → latest run → Artifacts)

---

## Anti-regression rules (never bypass these)

1. **No fabrication** — every resume bullet traces to `resume_base.json`. No new facts invented.
2. **No >4 YOE roles in main tracker** — they go to "Reach Roles" tab only.
3. **No duplicate job_ids** — SeenJobs ledger never pruned; content hash persists forever.
4. **No budget overrun** — MonthlyBudgetGuard degrades gracefully when ceiling hit.
5. **No auto-apply** — this system surfaces and prepares; Nagesh clicks Apply.
6. **No silent failures** — every run writes to Run Log, even failed ones.
