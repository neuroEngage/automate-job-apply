"""
JobRadar v2 — Scoring Engine (2-stage)

Stage A — free, rule-based (runs on 100% of new jobs):
  9-component weighted formula (all 0–100 scale):
    1. Data/AI Skill Match          25%  (TF-IDF cosine similarity)
    2. Role Priority Match          20%  (from priority_tier 1–6)
    3. Fresher Compatibility        10%  (experience gate)
    4. AI/Technology Exposure       10%  (AI keyword density)
    5. Company Opportunity Score    10%  (funding + ownership signals)
    6. Location Fit                 10%  (region-based)
    7. Job Freshness                 5%  (recency)
    8. Product/Business Exposure     5%  (keyword hits)
    9. Startup/Ownership Potential   5%  (keyword hits)

Stage B — Claude Haiku (paid, only for score >= threshold):
  Batches up to 10 JDs per API call for cost efficiency.
  Returns refined score + 1-sentence "why this fits" note + red flag flag.
  Optional polish only — never a dependency for producing a score.

Sort rule: primary by priority_tier ASC, secondary by overall_score DESC.
"""
import logging
import json
import re
from datetime import date

import anthropic

from src.ai_signal_detector import compute_ai_exposure
from src.company_opportunity import compute_company_opportunity

logger = logging.getLogger(__name__)

# Try to import sklearn for TF-IDF; fall back to keyword overlap if unavailable
try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    _HAS_SKLEARN = True
except ImportError:
    logger.warning("scikit-learn not available — using keyword overlap for skill matching")
    _HAS_SKLEARN = False


# ─────────────────────────────────────────────────────────────────────────────
# Component 1: Data/AI Skill Match (25%) — TF-IDF or keyword overlap
# ─────────────────────────────────────────────────────────────────────────────

def skill_match_score(jd_text: str, skills: list[str], signal_keywords: list[str] | None = None) -> float:
    """
    Computes skill match score (0–100) using TF-IDF cosine similarity
    between candidate skills + tier signal keywords vs JD text.
    Falls back to keyword overlap if sklearn is unavailable.
    """
    if not jd_text or not skills:
        return 0.0

    # Combine candidate skills with tier-specific signal keywords
    all_keywords = list(skills)
    if signal_keywords:
        all_keywords.extend(signal_keywords)
    # Deduplicate (case-insensitive)
    seen = set()
    unique_keywords = []
    for kw in all_keywords:
        kw_lower = kw.lower()
        if kw_lower not in seen:
            seen.add(kw_lower)
            unique_keywords.append(kw)

    if _HAS_SKLEARN:
        return _tfidf_skill_match(jd_text, unique_keywords)
    return _keyword_skill_match(jd_text, unique_keywords)


def _tfidf_skill_match(jd_text: str, keywords: list[str]) -> float:
    """TF-IDF cosine similarity approach."""
    try:
        candidate_text = " ".join(keywords)
        vectorizer = TfidfVectorizer(stop_words="english", lowercase=True)
        tfidf_matrix = vectorizer.fit_transform([candidate_text, jd_text])
        similarity = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:2])[0][0]
        # Scale from 0–1 similarity to 0–100, with a boost curve
        # 0.05 similarity → ~20, 0.15 → ~50, 0.3 → ~80, 0.5+ → ~95+
        score = min(100.0, similarity * 200)
        return round(score, 1)
    except Exception as e:
        logger.warning(f"TF-IDF failed, falling back to keyword overlap: {e}")
        return _keyword_skill_match(jd_text, keywords)


def _keyword_skill_match(jd_text: str, keywords: list[str]) -> float:
    """Keyword overlap fallback (same logic as v1, rescaled to 0–100)."""
    jd_lower = jd_text.lower()
    matched = sum(1 for s in keywords if s.lower() in jd_lower)
    ratio = matched / len(keywords) if keywords else 0
    if ratio == 0:
        return 0.0
    if ratio < 0.1:
        return round(ratio * 300, 1)
    if ratio < 0.2:
        return round(30 + ratio * 200, 1)
    if ratio < 0.4:
        return min(90.0, round(50 + ratio * 100, 1))
    return 100.0


def get_matched_and_missing_skills(jd_text: str, keywords: list[str]) -> tuple[list[str], list[str]]:
    """Returns (matched_skills, missing_skills) for explainer use."""
    if not jd_text or not keywords:
        return [], list(keywords) if keywords else []
    jd_lower = jd_text.lower()
    matched = [s for s in keywords if s.lower() in jd_lower]
    missing = [s for s in keywords if s.lower() not in jd_lower]
    return matched, missing


# ─────────────────────────────────────────────────────────────────────────────
# Component 2: Role Priority Match (20%)
# ─────────────────────────────────────────────────────────────────────────────

def role_priority_score(priority_tier: int, config: dict) -> float:
    """Returns 0–100 based on priority tier. Tier 1 = full marks."""
    tier_scores = config.get("scoring_v2", {}).get("tier_scores", {})
    return float(tier_scores.get(priority_tier, tier_scores.get(str(priority_tier), 15)))


# ─────────────────────────────────────────────────────────────────────────────
# Component 3: Fresher Compatibility (10%)
# ─────────────────────────────────────────────────────────────────────────────

def experience_gate(min_yoe_required: int | None, candidate_yoe: float = 1.4) -> tuple[str, float]:
    """
    Returns (gate_label, experience_fit_score_0_to_100).
    Deliberately NOT flat across the pass range — fresher postings score highest.
    """
    if min_yoe_required is None:
        return "unknown", 50.0             # keep, flagged, neutral
    if min_yoe_required == 0:
        return "ideal_fresher", 100.0      # explicit fresher/entry-level — BEST match
    if min_yoe_required == 1:
        return "ideal_1yr", 90.0           # 1 yr required — squarely Nagesh's band
    if min_yoe_required == 2:
        return "pass", 60.0               # slightly above, no hard penalty
    if min_yoe_required <= 4:
        return "stretch", 30.0            # keep, tagged "stretch"
    return "exclude", 0.0                 # >4 yrs → Reach Roles tab


# ─────────────────────────────────────────────────────────────────────────────
# Component 6: Location Fit (10%)
# ─────────────────────────────────────────────────────────────────────────────

def location_fit_score(region: str, config: dict) -> float:
    """Returns 0–100 based on region. Mumbai metro = highest."""
    location_scores = config.get("scoring_v2", {}).get("location_scores", {})
    return float(location_scores.get(region, 30))


# ─────────────────────────────────────────────────────────────────────────────
# Component 7: Job Freshness (5%)
# ─────────────────────────────────────────────────────────────────────────────

def freshness_score(days_old: int | None, config: dict) -> float:
    """Returns 0–100 based on how recently the job was posted."""
    if days_old is None:
        return 40.0  # unknown age → small neutral value

    scores = config.get("scoring_v2", {}).get("freshness_scores", {})
    if days_old <= 3:
        return float(scores.get("days_0_to_3", 100))
    if days_old <= 7:
        return float(scores.get("days_4_to_7", 80))
    if days_old <= 14:
        return float(scores.get("days_8_to_14", 50))
    if days_old <= 30:
        return float(scores.get("days_15_to_30", 20))
    return float(scores.get("days_over_30", 0))


# ─────────────────────────────────────────────────────────────────────────────
# Component 8: Product/Business Exposure (5%)
# ─────────────────────────────────────────────────────────────────────────────

def product_business_score(jd_text: str, config: dict) -> float:
    """Returns 0–100 based on product/business keyword hits."""
    if not jd_text:
        return 0.0
    keywords = config.get("scoring_v2", {}).get("product_business_keywords", [
        "product", "roadmap", "customer", "GTM", "go-to-market",
        "strategy", "ownership", "stakeholder", "business impact", "revenue",
    ])
    jd_lower = jd_text.lower()
    hits = sum(1 for kw in keywords if kw.lower() in jd_lower)
    if hits == 0:
        return 0.0
    ratio = hits / len(keywords)
    return round(min(100.0, ratio * 250), 1)


# ─────────────────────────────────────────────────────────────────────────────
# Component 9: Startup/Ownership Potential (5%)
# ─────────────────────────────────────────────────────────────────────────────

def startup_ownership_score(jd_text: str, config: dict) -> float:
    """Returns 0–100 based on startup/ownership keyword hits."""
    if not jd_text:
        return 0.0
    keywords = config.get("scoring_v2", {}).get("startup_ownership_keywords", [
        "founder", "ownership", "fast-paced", "0-to-1", "0 to 1",
        "cross-functional", "wear many hats", "early stage", "seed stage",
        "high-growth", "startup", "start-up",
    ])
    jd_lower = jd_text.lower()
    hits = sum(1 for kw in keywords if kw.lower() in jd_lower)
    if hits == 0:
        return 0.0
    ratio = hits / len(keywords)
    return round(min(100.0, ratio * 250), 1)


# ─────────────────────────────────────────────────────────────────────────────
# v2 Category Mapping (10-category output)
# ─────────────────────────────────────────────────────────────────────────────

TIER_CATEGORY_MAP = {
    1: "Data Engineering",
    2: "Data/Product Analytics",
    3: "Core AI/ML",
    4: "AI + Product/Business",
    5: "Founder's Office/Strategy",
    6: "AI Startup/GTM",
}


def compute_category(job: dict) -> str:
    """
    Maps a job to one of 10 categories.
    Tiers 1–6 map directly. Cross-cutting categories checked independently.
    """
    tier = job.get("priority_tier", 6)
    region = job.get("region", "")
    company_opp = job.get("company_opportunity_score", 50)

    # Cross-cutting: Remote
    if region in ("india_remote", "global_remote"):
        return "Remote"

    # Cross-cutting: Established Company (high company opportunity + large company signals)
    if company_opp >= 75 and tier >= 4:
        return "Established Company"

    # Cross-cutting: Unconventional (scored well but no clean tier match)
    if job.get("_tier_match_method") == "keyword_fallback" and tier >= 5:
        return "Unconventional"

    # Default: map from tier
    return TIER_CATEGORY_MAP.get(tier, "AI Startup/GTM")


# ─────────────────────────────────────────────────────────────────────────────
# Stage A: Master Score Function (v2 — 9-component formula)
# ─────────────────────────────────────────────────────────────────────────────

def compute_stage_a(job: dict, config: dict) -> dict:
    """
    Computes the full Stage A score for a single normalised job.
    Adds scoring fields to the job dict in place and returns it.

    v2 Formula: 9-component weighted sum (0–100 scale).
    """
    profile = config["candidate_profile"]
    scoring = config.get("scoring_v2", {})

    # Get weights
    w_skill = scoring.get("data_ai_skill_match", 0.25)
    w_priority = scoring.get("role_priority_match", 0.20)
    w_fresher = scoring.get("fresher_compatibility", 0.10)
    w_ai = scoring.get("ai_technology_exposure", 0.10)
    w_company = scoring.get("company_opportunity", 0.10)
    w_location = scoring.get("location_fit", 0.10)
    w_fresh = scoring.get("job_freshness", 0.05)
    w_product = scoring.get("product_business_exposure", 0.05)
    w_startup = scoring.get("startup_ownership_potential", 0.05)

    jd = job.get("description_text", "")

    # ── 1. Data/AI Skill Match (25%) ──────────────────────────────────────────
    candidate_skills = profile.get("skills", [])
    # Get tier-specific signal keywords
    tier = job.get("priority_tier", 6)
    signal_kw = _get_tier_signal_keywords(tier, config)
    s_skill = skill_match_score(jd, candidate_skills, signal_kw)

    # ── 2. Role Priority Match (20%) ──────────────────────────────────────────
    s_priority = role_priority_score(tier, config)

    # ── 3. Fresher Compatibility (10%) ────────────────────────────────────────
    min_yoe = job.get("experience_required_min")
    gate_label, s_fresher = experience_gate(min_yoe, profile.get("total_yoe", 1.4))

    # ── 4. AI/Technology Exposure (10%) ───────────────────────────────────────
    ai_result = compute_ai_exposure(jd)
    s_ai = ai_result["ai_exposure_score"]

    # ── 5. Company Opportunity Score (10%) ────────────────────────────────────
    company_result = compute_company_opportunity(job.get("company", ""), jd)
    s_company = company_result["company_opportunity_score"]

    # ── 6. Location Fit (10%) ─────────────────────────────────────────────────
    region = job.get("region", "global_remote")
    s_location = location_fit_score(region, config)

    # ── 7. Job Freshness (5%) ─────────────────────────────────────────────────
    days_old = job.get("days_old")
    s_fresh = freshness_score(days_old, config)

    # ── 8. Product/Business Exposure (5%) ─────────────────────────────────────
    s_product = product_business_score(jd, config)

    # ── 9. Startup/Ownership Potential (5%) ───────────────────────────────────
    s_startup = startup_ownership_score(jd, config)

    # ── Weighted sum ──────────────────────────────────────────────────────────
    raw_total = (
        w_skill * s_skill
        + w_priority * s_priority
        + w_fresher * s_fresher
        + w_ai * s_ai
        + w_company * s_company
        + w_location * s_location
        + w_fresh * s_fresh
        + w_product * s_product
        + w_startup * s_startup
    )
    stage_a = round(max(0.0, min(100.0, raw_total)), 1)

    # ── Enrich job dict ──────────────────────────────────────────────────────
    job["skill_match_score"] = round(s_skill, 1)
    job["role_priority_score"] = round(s_priority, 1)
    job["experience_fit_score"] = round(s_fresher, 1)
    job["experience_gate_label"] = gate_label
    job["ai_exposure_score"] = round(s_ai, 1)
    job["ai_terms_found"] = ai_result.get("ai_terms_found", [])
    job["company_opportunity_score"] = round(s_company, 1)
    job["funded_company_match"] = company_result.get("funded_company_match")
    job["location_fit_score"] = round(s_location, 1)
    job["freshness_score"] = round(s_fresh, 1)
    job["product_business_score"] = round(s_product, 1)
    job["startup_ownership_score"] = round(s_startup, 1)
    job["stage_a_score"] = stage_a

    # v1-compat fields (used by some existing code paths)
    job["comp_fit_score"] = 0.0  # removed from v2 formula but field still expected
    job["recency_bonus"] = round(s_fresh * 0.02, 2)  # approximate v1-scale for compat
    job["region_bonus"] = round(s_location * 0.015, 2)

    # Startup flag (enhanced with company_opportunity data)
    jd_lower = jd.lower()
    job["is_startup"] = any(
        kw in jd_lower for kw in ["startup", "start-up", "series a", "series b", "early stage", "seed stage"]
    ) or (company_result.get("funded_company_match") and
          company_result.get("funding_score", 50) < 55)

    # ── Category label ────────────────────────────────────────────────────────
    job["category_label"] = compute_category(job)

    # ── Matched/Missing skills (for explainer) ────────────────────────────────
    all_kw = list(candidate_skills) + (signal_kw or [])
    # Deduplicate
    seen_kw = set()
    unique_kw = []
    for k in all_kw:
        if k.lower() not in seen_kw:
            seen_kw.add(k.lower())
            unique_kw.append(k)
    matched, missing = get_matched_and_missing_skills(jd, unique_kw)
    job["matched_skills"] = matched[:15]  # cap for display
    job["missing_skills"] = missing[:10]

    # ── Routing decision ─────────────────────────────────────────────────────
    if gate_label == "exclude":
        job["routing"] = "reach_roles"
    elif stage_a < 20.0:  # v2: 20/100 threshold (was 2.0/10)
        job["routing"] = "skip"
    else:
        job["routing"] = "job_tracker"

    return job


def _get_tier_signal_keywords(tier: int, config: dict) -> list[str]:
    """Get signal keywords for a specific tier from config."""
    for tier_config in config.get("role_priorities", []):
        if tier_config.get("tier") == tier:
            return tier_config.get("signal_keywords", [])
    return []


def score_all_stage_a(jobs: list[dict], config: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Runs Stage A on all jobs.
    Returns (tracker_jobs, reach_role_jobs, skipped_jobs).
    """
    tracker, reach, skipped = [], [], []
    for job in jobs:
        scored = compute_stage_a(job, config)
        routing = scored.get("routing", "job_tracker")
        if routing == "reach_roles":
            reach.append(scored)
        elif routing == "skip":
            skipped.append(scored)
        else:
            tracker.append(scored)
    logger.info(
        f"Stage A: {len(tracker)} → Job Tracker, {len(reach)} → Reach Roles, {len(skipped)} skipped"
    )
    return tracker, reach, skipped


# ─────────────────────────────────────────────────────────────────────────────
# Stage B: Claude Haiku — batched LLM refinement
# ─────────────────────────────────────────────────────────────────────────────

STAGE_B_SYSTEM_PROMPT = """You are a job-matching assistant for Nagesh Khichade, an AI/Data Science professional with ~1.4 years of experience (fresher/junior level). His core skills: Python, SQL, LLM fine-tuning (Gemma), RAG, prompt engineering, Power BI, PySpark, AWS, scikit-learn, EDA. He is CDAC PGCP-BDA certified.

You will receive a batch of job listings as JSON. For each job, output a JSON object with:
- "job_id": the job_id from input (string)
- "refined_score": float 0-100 (adjust the rule-based score by ±10 max based on JD nuance)
- "fit_note": string (1 concise sentence explaining why this role fits Nagesh, or why it doesn't)
- "red_flags": list of strings (max 3; e.g. "vague company", "no salary listed", "annotation-only role disguised as Data Analyst", "requires 5+ YOE despite fresher label")
- "is_genuine_data_role": boolean (false if title says "Data Analyst" but JD is actually annotation/tagging/non-analytical work)

Return ONLY a JSON array, no other text."""


def stage_b_score_batch(
    jobs: list[dict],
    anthropic_client: anthropic.Anthropic,
    budget_guard,
    config: dict,
) -> list[dict]:
    """
    Runs Stage B (Claude Haiku) on jobs where stage_a_score >= threshold.
    Batches up to max_jds_per_batch per API call.
    Updates each job with stage_b fields in-place.
    """
    llm_cfg = config.get("llm", {})
    model = llm_cfg.get("scoring_model", "claude-haiku-4-5")
    batch_size = llm_cfg.get("max_jds_per_batch", 10)
    max_jd_chars = llm_cfg.get("max_jd_chars", 4000)
    threshold = config.get("scoring_v2", {}).get("stage_b_min_score", 60.0)

    eligible = [j for j in jobs if j.get("stage_a_score", 0) >= threshold]
    logger.info(f"Stage B: {len(eligible)} jobs eligible (score >= {threshold})")

    if not eligible:
        return jobs

    # Process in batches
    for i in range(0, len(eligible), batch_size):
        batch = eligible[i : i + batch_size]
        try:
            _run_stage_b_batch(batch, anthropic_client, budget_guard, model, max_jd_chars)
        except Exception as e:
            logger.error(f"Stage B batch {i//batch_size + 1} failed: {e}. Jobs keep Stage A scores.")

    # For jobs that got Stage B results, compute final_score
    for job in jobs:
        if "stage_b_refined_score" in job:
            # Final score = average of Stage A and Stage B, clamped
            job["overall_score"] = round(
                min(100.0, (job["stage_a_score"] + job["stage_b_refined_score"]) / 2), 1
            )
        else:
            job["overall_score"] = job.get("stage_a_score", 0.0)

    return jobs


def _run_stage_b_batch(
    batch: list[dict],
    client: anthropic.Anthropic,
    budget_guard,
    model: str,
    max_jd_chars: int,
) -> None:
    """Sends one batch to Claude Haiku and updates job dicts in-place."""
    # Estimate cost: ~500 input tokens per job + ~150 output tokens per job
    est_cost = len(batch) * 0.001  # very rough: Haiku is $0.25/M input, $1.25/M output
    budget_guard.check_and_debit("claude", est_cost)

    batch_input = []
    for job in batch:
        batch_input.append({
            "job_id": job["job_id"],
            "title": job.get("title", ""),
            "company": job.get("company", ""),
            "experience_required": job.get("experience_required_text", ""),
            "salary": job.get("salary_text", ""),
            "stage_a_score": job.get("stage_a_score", 0),
            "description": (job.get("description_text", "") or "")[:max_jd_chars],
        })

    prompt = f"Score these {len(batch_input)} job listings for Nagesh:\n\n{json.dumps(batch_input, indent=2)}"

    response = client.messages.create(
        model=model,
        max_tokens=1024,
        system=STAGE_B_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = re.sub(r"```[a-z]*\n?", "", raw).strip().rstrip("```").strip()

    results = json.loads(raw)
    result_map = {r["job_id"]: r for r in results}

    for job in batch:
        r = result_map.get(job["job_id"])
        if r:
            job["stage_b_refined_score"] = float(r.get("refined_score", job["stage_a_score"]))
            job["fit_note"] = r.get("fit_note", "")
            job["red_flags"] = "; ".join(r.get("red_flags", []))
            job["is_genuine_data_role"] = r.get("is_genuine_data_role", True)
            # Downgrade routing if LLM flags role as not a genuine data role
            if not r.get("is_genuine_data_role", True) and job.get("priority_tier", 6) <= 3:
                job["routing_flag"] = "⚠️ Title may not match actual role"
