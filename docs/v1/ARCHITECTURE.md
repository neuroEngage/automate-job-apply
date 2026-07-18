# JobRadar v1 — Architecture & Execution Flow

## System Architecture

```mermaid
graph TB
    GH["GitHub Actions\n(cron 7:30 AM IST / manual)"]
    
    GH --> MAIN["main.py\nOrchestrator"]
    
    subgraph SCRAPERS["Scraping Layer — scraper.py"]
        LI["Apify LinkedIn\nvalig/linkedin-jobs-scraper\n(Mumbai, 16+12 titles)"]
        NA["Apify Naukri\nepic-scrapers/naukri-scraper\n(0-3 YOE filtered)"]
        IR["JobSpy India Remote\nIndeed + Google\n(free)"]
        GR["JobSpy Global Remote\nIndeed + Glassdoor +\nGoogle + ZipRecruiter\n(free)"]
    end

    subgraph PIPELINE["Processing Pipeline"]
        NORM["normalizer.py\nUnified schema\nYOE extraction\nJob ID hash"]
        DEDUP["dedup.py\nSeenJobs ledger\nFilter duplicates\nTrack re-sightings"]
        SA["scorer.py Stage A\nSkill match\nExp gate\nComp fit\nRecency + Region bonus"]
        VAL["validator.py\nCompany page HTTP check\nFlag stale listings\n(score >= 5.0 only)"]
        SB["scorer.py Stage B\nClaude Haiku\nLLM refinement\nFit notes + Red flags"]
        RESUME["resume_generator.py\nClaude Haiku\nTailored .docx\nMax 5/day"]
    end

    subgraph STORAGE["Persistence Layer"]
        SHEET["Google Sheets\n5 tabs"]
        DRIVE["Google Drive\nResumes folder"]
    end

    subgraph GUARD["Budget Guard"]
        BG["budget_guard.py\n$10/month hard cap\nDegraded mode"]
    end

    MAIN --> SCRAPERS
    SCRAPERS --> NORM
    NORM --> DEDUP
    DEDUP --> SA
    SA --> VAL
    VAL --> SB
    SB --> RESUME
    RESUME --> STORAGE

    BG -.->|"check before each paid call"| LI
    BG -.->|"check before each paid call"| NA
    BG -.->|"check before each paid call"| SB
    BG -.->|"check before each paid call"| RESUME

    SHEET -.->|"SeenJobs + Run Log state"| DEDUP
    SHEET -.->|"monthly spend"| BG
```

---

## Execution Flow (Step by Step)

```mermaid
flowchart TD
    START(["GitHub Actions Trigger\n7:30 AM IST daily"])
    
    S1["Step 1\nLoad config.yaml\nInit budget guard\nConnect Google Sheet\nEnsure 5 tabs exist"]
    
    S2["Step 2 — SCRAPE\nLinkedIn Mumbai x2 tiers\nNaukri x2 tiers\nJobSpy India Remote x2 tiers\nJobSpy Global Remote x2 tiers"]
    
    S2_CHECK{{"Any jobs scraped?"}}
    
    S3["Step 3 — NORMALIZE\nMap to unified schema\nExtract YOE via regex\nGenerate SHA-256 job_id\nResolve role tier"]
    
    S4["Step 4 — DEDUP\nLoad SeenJobs from Sheet\nFilter: new vs re-sighted\nAppend new IDs immediately"]
    
    S5["Step 5 — STAGE A SCORE\nSkill match 0-10\nExperience gate\nComp fit 0-10\nRecency bonus +0 to +2\nRegion bonus +0 to +1.5\nRoute: Tracker / Reach / Skip"]
    
    S6["Step 6 — VALIDATE\nHTTP check company pages\nFlag stale listings\nscore >= 5.0 only"]
    
    S7{{"Budget OK?\nAPI key set?"}}
    
    S8["Step 7 — STAGE B SCORE\nClaude Haiku batch\n10 JDs per API call\nOnly score >= 6.0\nAdds fit_note red_flags\nFinal = avg of A and B"]
    
    S9["Step 8 — RESUME GEN\nClaude Haiku\nTop jobs score >= 7.5\nMax 5 per day\nUpload to Google Drive"]
    
    S10["Step 9 — WRITE TO SHEET\nSort by overall_score desc\nAppend to Job Tracker tab\nAppend to Reach Roles tab"]
    
    S11["Step 10 — MAINTENANCE\nUpdate last_seen for re-sighted\nArchive jobs > 30 days old"]
    
    S12["Step 11 — RUN LOG\nWrite stats row\nscraped new scored\nspend_usd errors"]
    
    END(["Run Complete"])
    EARLY_END(["Early Exit\nlog Run Log"])

    START --> S1
    S1 --> S2
    S2 --> S2_CHECK
    S2_CHECK -->|"No jobs"| EARLY_END
    S2_CHECK -->|"Has jobs"| S3
    S3 --> S4
    S4 --> S5
    S5 --> S6
    S6 --> S7
    S7 -->|"No"| S10
    S7 -->|"Yes"| S8
    S8 --> S9
    S9 --> S10
    S10 --> S11
    S11 --> S12
    S12 --> END
```

---

## Job Routing Decision Tree

```mermaid
flowchart LR
    JOB["New Job"]
    
    EXP{{"Min YOE required?"}}
    LT2{{"Stage A score < 2.0?"}}
    GTE6{{"Stage A score >= 6.0?"}}
    GTE75{{"Final score >= 7.5?"}}
    
    REACH["Reach Roles Tab\n5yr+ jobs"]
    SKIP["Skipped\nnot written anywhere"]
    TRACKER["Job Tracker Tab"]
    RESUME["Resume Generated\nDrive Link in sheet"]
    
    JOB --> EXP
    EXP -->|"0-4 YOE or unknown"| LT2
    EXP -->|"> 4 YOE"| REACH
    LT2 -->|"Yes"| SKIP
    LT2 -->|"No"| GTE6
    GTE6 -->|"Yes Stage B runs"| TRACKER
    GTE6 -->|"No skip Stage B"| TRACKER
    TRACKER --> GTE75
    GTE75 -->|"Yes and budget OK"| RESUME
    GTE75 -->|"No"| TRACKER
```

---

## Stage A Scoring Formula

```mermaid
graph LR
    subgraph INPUTS["Inputs"]
        JD["Job Description Text"]
        EXP["Experience Required"]
        SAL["Salary / Currency"]
        AGE["Days Since Posted"]
        CAT["Category\nMumbai/India/Global"]
        TIER["Role Tier\nTier 1 or Tier 2"]
    end

    subgraph COMPONENTS["Score Components"]
        SK["Skill Match\n0-10\nkeyword overlap\nwith 50-skill list"]
        EF["Exp Fit\n0-10\nfresher=10\n1yr=9 2yr=6\n3-4yr=3 gt4yr=0"]
        CF["Comp Fit\n0-10\nUSD/EUR=8-10\nINR 12LPA=10"]
        RB["Recency Bonus\n0-2.0\n0-3 days plus2.0\n4-7 days plus1.5"]
        REG["Region Bonus\n0-1.5\nMumbai plus1.5\nNaukri plus1.0\nIN Remote plus0.75"]
        T2P["Tier 2 Penalty\nminus0.5 if Tier 2"]
    end

    subgraph FORMULA["Final Formula"]
        CALC["score = 0.35 x skill_adj\nplus 0.25 x exp_fit\nplus 0.25 x comp_fit\nplus recency_bonus\nplus region_bonus\nminus tier2_penalty\n\nClamped to 0 to 10"]
    end

    JD --> SK
    EXP --> EF
    SAL --> CF
    AGE --> RB
    CAT --> REG
    TIER --> T2P
    TIER -->|"Tier 2: multiply 0.85"| SK

    SK --> CALC
    EF --> CALC
    CF --> CALC
    RB --> CALC
    REG --> CALC
    T2P --> CALC
```

---

## Google Sheet Tab Structure

```mermaid
graph TD
    SHEET["Google Sheet: jobs found"]
    
    SHEET --> JT["Job Tracker\n30 columns\nAll scored jobs\nSorted: overall_score desc"]
    SHEET --> RR["Reach Roles 5yr+\nSame 30 columns\nJobs needing > 4 YOE"]
    SHEET --> SJ["SeenJobs\n3 columns\njob_id / first_seen / last_seen\nDedup ledger — never deleted"]
    SHEET --> AR["Archive\nSame 30 columns\nJobs > 30 days old\nAuto-moved from Job Tracker"]
    SHEET --> RL["Run Log\n13 columns\nOne row per run\nOperational dashboard"]

    JT --> JTC["# / job_id / Category / Role Tier\nJob Title / Company / Location\nPosted Date / Days Old / Recency Bucket\nExp Required / Exp Gate / Startup?\nPay / Currency\nSkill Match / Exp Fit / Comp Fit\nRecency Bonus / Region Bonus\nStage A / Stage B / Overall Score\nApply Link / Resume Link / Validation\nFit Note / Red Flags / Source / First Seen"]

    RL --> RLC["run_date / run_timestamp\njobs_scraped / jobs_new\njobs_scored_stage_a / jobs_scored_stage_b\nresumes_generated / reach_roles_added\narchived / spend_usd\nbudget_degraded / errors / notes"]
```

---

## Secrets & Environment Variables

```mermaid
graph LR
    GHS["GitHub Repository Secrets"]

    GHS --> GSI["GOOGLE_SHEET_ID\nsheets.py: open_by_key()"]
    GHS --> GSA["GOOGLE_SERVICE_ACCOUNT_JSON\nsheets.py: service_account_from_dict()\nresume_generator.py: Drive upload"]
    GHS --> AT["APIFY_TOKEN\nscraper.py: ApifyClient()"]
    GHS --> ANT["ANTHROPIC_API_KEY\nscorer.py: Stage B Claude Haiku\nresume_generator.py: resume tailoring"]
    GHS --> GDF["GOOGLE_DRIVE_FOLDER_ID\nresume_generator.py: upload target"]
```
