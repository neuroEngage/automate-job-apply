# JobRadar — Product Requirements Document
**Version:** 1.0  
**Author:** Nagesh Khichade (neuroEngage)  
**Date:** July 2026  
**Status:** Live (v1 deployed)

---

## 1. Problem Statement

Job searching manually is time-consuming, inconsistent, and emotionally draining. A fresher/junior candidate (0–2 YOE) applying in India's tech market faces:

- **Volume problem** — hundreds of new postings daily across LinkedIn, Naukri, Indeed, Glassdoor
- **Quality problem** — many listings mislabel experience requirements (says "fresher" but needs 5 YOE)
- **Speed problem** — good postings fill within 72 hours of going live
- **Effort problem** — tailoring a resume for each job description is 30+ minutes per application

**JobRadar solves all four.**

---

## 2. Product Vision

> A fully automated, daily-running pipeline that surfaces the top-scoring job opportunities for Nagesh, scores them intelligently using AI, and writes them directly into a structured Google Sheet — requiring zero manual searching.

**No auto-apply.** The system surfaces; the human submits. This ensures quality control on every application.

---

## 3. Target User

**Primary User:** Nagesh Khichade  
- CDAC PGCP-BDA certified
- ~1.4 years experience (Manthan LLC contract + 2 internships)
- Skills: Python, SQL, LLM fine-tuning, RAG, Prompt Engineering, Power BI, PySpark, AWS, scikit-learn, EDA
- Target roles: Data Analyst, Data Scientist, AI/ML Engineer, GenAI Engineer, LLM Engineer
- Target locations: Mumbai (preferred) + India Remote + Global Remote

---

## 4. Core Features (v1)

### 4.1 Multi-Source Job Scraping
| Source | Engine | Coverage |
|---|---|---|
| LinkedIn Mumbai | Apify (valig/linkedin-jobs-scraper) | Local Mumbai tech jobs |
| Naukri | Apify (epic-scrapers/naukri-scraper) | India-wide, 0-3 YOE filtered |
| Indeed + Google Jobs | JobSpy (free) | India Remote |
| Glassdoor + ZipRecruiter + Indeed | JobSpy (free) | Global Remote (USD/EUR/GBP) |

### 4.2 Two-Tier Title Targeting
- **Tier 1 (Core Data):** Data Analyst, Data Scientist, AI Engineer, ML Engineer, GenAI Engineer, LLM Engineer, Prompt Engineer, BI Developer, Analytics Engineer
- **Tier 2 (Broader):** Product Associate, Business Analyst, Growth Analyst, Founder's Office, Strategy Associate

### 4.3 Two-Stage Scoring
- **Stage A (Free):** Rule-based score using skill match, experience fit, comp fit, recency, region bonus
- **Stage B (Paid, Claude Haiku):** LLM refinement for jobs scoring >= 6.0 — adds fit notes, red flags, genuineness check

### 4.4 Smart Deduplication
- SHA-256 content hash as job_id (based on source + company + title + location)
- SeenJobs ledger in Google Sheets — never shows the same job twice
- Re-sighted jobs get last_seen_date updated

### 4.5 ATS Resume Generation
- Auto-generates tailored .docx resumes for jobs scoring >= 7.5
- Hard cap of 5 resumes/day
- Uploaded to Google Drive, link stored in sheet

### 4.6 Google Sheet Dashboard
5 tabs auto-created and maintained:
| Tab | Purpose |
|---|---|
| Job Tracker | All scored jobs, sorted by overall score desc |
| Reach Roles (5yr+) | Jobs requiring >4 YOE for future reference |
| SeenJobs | Dedup ledger (hidden operational tab) |
| Archive | Jobs >30 days old with no re-sighting |
| Run Log | One row per run — operational dashboard |

### 4.7 Monthly Budget Guard
- Hard cap: $10/month across all paid APIs (Apify + Claude)
- Degraded mode: free scraping + Stage A continues; Stage B + resume gen paused

### 4.8 Automated Daily Schedule
- GitHub Actions cron: 0 2 * * * (2:00 AM UTC = 7:30 AM IST)
- Manual trigger available via workflow_dispatch

---

## 5. Non-Goals (v1)

- No auto-apply (intentional — human reviews every application)
- No browser automation / form filling
- No email notifications (planned for v2)
- No multi-user support
- No mobile app

---

## 6. Success Metrics

| Metric | Target |
|---|---|
| Jobs scraped per run | 200-500 raw, 50-150 new after dedup |
| Stage A scored per run | 80-90% of new jobs |
| Stage B refined per run | Jobs with score >= 6.0 |
| Sheet write success rate | 100% (errors logged, run continues) |
| Monthly API cost | < $10 |
| Pipeline runtime | < 30 minutes |

---

## 7. Constraints

- Must run on GitHub Actions free tier (2,000 min/month)
- All secrets via GitHub Secrets (no .env in repo)
- Service account JSON never committed to git (.gitignore enforced)
- No database — Google Sheets IS the persistence layer

---

## 8. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Apify rate limits | Per-title delays (1s LinkedIn, 2s JobSpy) |
| Claude API cost spike | Budget guard hard cap + degraded mode |
| Google Sheets quota | Batched writes via append_rows() |
| Scraper schema changes | Per-source extractors, fallback to jobspy extractor |
| Stale job listings | Company page validator flags 404s as Possibly Stale |
