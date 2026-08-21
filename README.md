# JobRadar v2 — Automated Daily Job Intelligence Engine

Personal job-finding engine for **Nagesh Khichade** (AI/Data Science, ~1.4 YOE).
Scrapes listings across LinkedIn, Naukri, Indeed, Glassdoor, Google Jobs, and ZipRecruiter, scores them with a 9-component formula, cross-validates against company career pages, tailors ATS resumes, tracks weekly quotas, and updates Google Sheets.

---

## What's New in v2

1. **6-Tier Role Priority System**: Replaces the previous 2-tier title list with 6 structured tiers:
   - Tier 1: Data Engineering (#1 Priority)
   - Tier 2: Data / Product Analytics (#2 Priority)
   - Tier 3: Core AI/ML (#3 Priority)
   - Tier 4: AI + Product/Business (#4 Priority)
   - Tier 5: Founder's Office / Strategy / Startup Generalist (#5 Priority)
   - Tier 6: AI Startups / AI+GTM / Growth (#6 Priority)
2. **9-Component Scoring Formula (0–100 Scale)**:
   - Data/AI Skill Match (25%): TF-IDF cosine similarity against candidate skills + tier keywords
   - Role Priority Match (20%): Direct score from Priority Tier (Tier 1 = 100)
   - Fresher Compatibility (10%): Experience gate (0 YOE = 100, 1 YOE = 90)
   - AI/Technology Exposure (10%): AI/GenAI/LLM keyword density
   - Company Opportunity Score (10%): Funding lookup from `funded_companies.yaml` + ownership keywords
   - Location Fit (10%): Region-based (Mumbai metro > Navi Mumbai > Thane > Pune > India remote > Global remote)
   - Job Freshness (5%): 0–3 days = 100, 4–7 days = 80
   - Product/Business Exposure (5%): Keyword hits (product, roadmap, GTM, ownership)
   - Startup/Ownership Potential (5%): Keyword hits (founder, 0-to-1, fast-paced)
3. **Company Opportunity Score Module (`src/company_opportunity.py`)**: Uses `funded_companies.yaml` (30+ curated Indian tech/AI companies) + JD keyword analysis.
4. **AI Signal Detector Module (`src/ai_signal_detector.py`)**: Regex & density analysis for 30+ AI/GenAI/LLM terms.
5. **Explainable Score Breakdown (`src/explainer.py`)**: Generates per-job human-readable breakdown + compact sheet summary.
6. **Geographic Expansion**: Pune added (`pune_local`, `naukri_pune`) alongside Mumbai Metropolitan Region coverage (Mumbai, Navi Mumbai, Thane).
7. **10-Category Output Structure**: Categorizes jobs across 6 tiers + 4 cross-cutting categories (Established Company, Remote, Unconventional).
8. **Company Watchlist (`src/company_watchlist.py`)**: Direct career-page monitoring configured via `company_watchlist.yaml`.
9. **Weekly Quota Tracker (`src/quota_tracker.py`)**: Tracks applied jobs by priority tier against target weekly distributions on the `Weekly Quota` sheet tab.
10. **"Applied" Tracking Loop-back**: Preserves manual `Applied` checkmarks on re-runs without clobbering.
11. **Daily Digest (`src/digest.py`)**: 10-section categorized digest delivered via Email or Webhook.

---

## Anti-Regression Rules (Non-Negotiable)

1. **No fabrication**: Resume generator never invents facts not in `resume_base.json`.
2. **No >4 YOE in main tracker**: Route to `Reach Roles (5yr+)` tab.
3. **No duplicate `job_id`s**: Content-hash ledger in `SeenJobs` tab.
4. **No budget overrun**: Strict $10/month hard cap enforced via `MonthlyBudgetGuard`.
5. **No auto-apply**: JobRadar discovers, scores, validates, and stages. Nagesh applies manually.
6. **No silent failures**: All non-fatal errors logged to `Run Log` tab in Google Sheets.

---

## Configuration (`config.yaml`)

Key sections:
- `candidate_profile`: Skills, YOE rules, compensation thresholds
- `role_priorities`: 6 tiers with titles and signal keywords
- `sources`: Scraper targets (Mumbai, Pune, India Remote, Naukri, Global Remote)
- `scoring_v2`: Component weights, tier scores, location scores, freshness scores
- `weekly_quota`: Target allocation percentages (e.g. 40% T1, 30% T2, 20% T3, 10% T4-6)
- `company_watchlist`: Watchlist settings and cache limits
- `digest`: Daily digest settings and caps

---

## Running Locally

```bash
# Run pytest tests
pytest tests/ -v

# Run fresh scan
python main.py

# Clear all previous scraped jobs and caches
python clear_and_reset.py
```
