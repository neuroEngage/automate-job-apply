"""
JobRadar — Scoring Engine (2-stage)

Stage A — free, rule-based (runs on 100% of new jobs):
  score = (skill_w * skill * tier_mult)
        + (exp_w   * exp_fit)
        + (comp_w  * comp_fit)
        + recency_bonus        ← NEW: highest for 0-3 day old postings
        + region_bonus
        - tier2_flat_penalty

Stage B — Claude Haiku (paid, only for score >= threshold):
  Batches up to 10 JDs per API call for cost efficiency.
  Returns refined score + 1-sentence "why this fits" note + red flag flag.
"""
import logging
import json
import re
from datetime import date

import anthropic

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Stage A: Experience Gate (§7 of brief — codified verbatim)
# ─────────────────────────────────────────────────────────────────────────────

def experience_gate(min_yoe_required: int | None, candidate_yoe: float = 1.4) -> tuple[str, int]:
    """
    Returns (gate_label, experience_fit_score_0_to_10).
    Deliberately NOT flat across the pass range — fresher postings score highest.
    """
    if min_yoe_required is None:
        return "unknown", 5              # keep, flagged, neutral
    if min_yoe_required == 0:
        return "ideal_fresher", 10       # explicit fresher/entry-level — BEST match
    if min_yoe_required == 1:
        return "ideal_1yr", 9            # 1 yr required — squarely Nagesh's band
    if min_yoe_required == 2:
        return "pass", 6                 # slightly above, no hard penalty
    if min_yoe_required <= 4:
        return "stretch", 3              # keep, tagged "stretch"
    return "exclude", 0                  # >4 yrs → Reach Roles tab


# ─────────────────────────────────────────────────────────────────────────────
# Stage A: Skill Match
# ─────────────────────────────────────────────────────────────────────────────

def skill_match_score(jd_text: str, skills: list[str]) -> float:
    """Keyword overlap between JD text and resume skill list → 0.0–10.0"""
    if not jd_text or not skills:
        return 0.0
    jd_lower = jd_text.lower()
    matched = sum(1 for s in skills if s.lower() in jd_lower)
    ratio = matched / len(skills)
    # Scale: 0% → 0, 20%+ → 7, 40%+ → 9, 60%+ → 10
    if ratio == 0:
        return 0.0
    if ratio < 0.1:
        return round(ratio * 30, 1)      # 0–3
    if ratio < 0.2:
        return round(3 + ratio * 20, 1)  # 3–7
    if ratio < 0.4:
        return min(9.0, round(5 + ratio * 10, 1))
    return 10.0


# ─────────────────────────────────────────────────────────────────────────────
# Stage A: Comp Fit
# ─────────────────────────────────────────────────────────────────────────────

def comp_fit_score(salary_text: str, salary_currency: str, category: str) -> float:
    """
    0–10 compensation fit score.
    - Global remote with USD/EUR/GBP: high score (7–10)
    - India/Mumbai: score based on whether salary hints at ≥8 LPA
    - No salary stated: neutral 5
    """
    if not salary_text or salary_text.strip() in ("", "nan", "None"):
        return 5.0  # not stated — neutral

    cur = salary_currency.upper() if salary_currency else ""
    sal_lower = salary_text.lower()

    # International currencies → high comp likelihood
    if cur in ("USD", "EUR", "GBP") or any(c in sal_lower for c in ["usd", "eur", "gbp", "$", "€", "£"]):
        # Try to extract numeric value
        nums = re.findall(r"[\d,]+", salary_text.replace(",", ""))
        if nums:
            try:
                amount = int(nums[0])
                if amount >= 50000:
                    return 10.0
                if amount >= 30000:
                    return 8.0
                if amount >= 20000:
                    return 6.0
            except ValueError:
                pass
        return 8.0  # USD/EUR without parseable number → assume decent

    # INR — try to detect LPA
    if "lpa" in sal_lower or "lakh" in sal_lower or cur == "INR":
        nums = re.findall(r"[\d.]+", salary_text)
        if nums:
            try:
                lpa = float(nums[0])
                if lpa >= 12:
                    return 10.0
                if lpa >= 8:
                    return 7.0
                if lpa >= 5:
                    return 4.0
                return 2.0
            except ValueError:
                pass
        return 5.0  # INR but can't parse

    return 5.0  # unknown currency / format


# ─────────────────────────────────────────────────────────────────────────────
# Stage A: Recency Bonus (new — per user decision)
# ─────────────────────────────────────────────────────────────────────────────

def recency_bonus(days_old: int | None, config: dict) -> float:
    """
    Returns a bonus score based on how recently the job was posted.
    0-3 days = highest bonus (freshest = most likely still open).
    Config-driven thresholds from config.yaml scoring.recency_bonus.
    """
    if days_old is None:
        return 0.5  # unknown age → small neutral bonus
    
    rb = config.get("scoring", {}).get("recency_bonus", {})
    if days_old <= 3:
        return rb.get("days_0_to_3", 2.0)
    if days_old <= 7:
        return rb.get("days_4_to_7", 1.5)
    if days_old <= 14:
        return rb.get("days_8_to_14", 0.75)
    if days_old <= 30:
        return rb.get("days_15_to_30", 0.25)
    return rb.get("days_over_30", 0.0)


# ─────────────────────────────────────────────────────────────────────────────
# Stage A: Region Bonus
# ─────────────────────────────────────────────────────────────────────────────

def region_bonus_score(category: str, config: dict) -> float:
    bonuses = config.get("scoring", {}).get("region_bonus", {})
    return float(bonuses.get(category, 0.0))


# ─────────────────────────────────────────────────────────────────────────────
# Stage A: Role Tier Adjustment
# ─────────────────────────────────────────────────────────────────────────────

def apply_tier_adjustment(
    skill_score: float, role_tier: str, config: dict
) -> tuple[float, float]:
    """
    Returns (adjusted_skill_score, flat_penalty).
    Tier 1: no adjustment.
    Tier 2: skill * 0.85, + 0.5 flat penalty on final score.
    """
    scoring = config.get("scoring", {})
    if role_tier == "tier2_broader":
        multiplier = scoring.get("tier2_skill_multiplier", 0.85)
        penalty = scoring.get("tier2_flat_penalty", 0.5)
        return skill_score * multiplier, penalty
    return skill_score, 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Stage A: Master Score Function
# ─────────────────────────────────────────────────────────────────────────────

def compute_stage_a(job: dict, config: dict) -> dict:
    """
    Computes the full Stage A score for a single normalised job.
    Adds scoring fields to the job dict in place and returns it.

    Formula:
      overall = (skill_w * skill_adj) + (exp_w * exp_fit) + (comp_w * comp_fit)
                + recency + region - tier2_penalty
      Clamped to [0, 10].
    """
    profile = config["candidate_profile"]
    scoring = config.get("scoring", {})

    skill_w = scoring.get("skill_weight", 0.35)
    exp_w = scoring.get("exp_weight", 0.25)
    comp_w = scoring.get("comp_weight", 0.25)

    # ── Skill Match ──────────────────────────────────────────────────────────
    jd = job.get("description_text", "")
    skills = profile.get("skills", [])
    raw_skill = skill_match_score(jd, skills)

    # ── Experience Fit ───────────────────────────────────────────────────────
    min_yoe = job.get("experience_required_min")
    gate_label, exp_fit = experience_gate(min_yoe, profile.get("total_yoe", 1.4))

    # ── Comp Fit ─────────────────────────────────────────────────────────────
    comp_fit = comp_fit_score(
        job.get("salary_text", ""),
        job.get("salary_currency", ""),
        job.get("category", ""),
    )

    # ── Recency Bonus ────────────────────────────────────────────────────────
    days_old = job.get("days_old")
    r_bonus = recency_bonus(days_old, config)

    # ── Region Bonus ─────────────────────────────────────────────────────────
    reg_bonus = region_bonus_score(job.get("category", ""), config)

    # ── Tier Adjustment ──────────────────────────────────────────────────────
    skill_adj, tier_penalty = apply_tier_adjustment(raw_skill, job.get("role_tier", "tier1_core_data"), config)

    # ── Final Score ──────────────────────────────────────────────────────────
    raw_total = (
        skill_w * skill_adj
        + exp_w * exp_fit
        + comp_w * comp_fit
        + r_bonus
        + reg_bonus
        - tier_penalty
    )
    stage_a = round(max(0.0, min(10.0, raw_total)), 2)

    # ── Enrich job dict ──────────────────────────────────────────────────────
    job["skill_match_score"] = round(raw_skill, 2)
    job["experience_fit_score"] = exp_fit
    job["experience_gate_label"] = gate_label
    job["comp_fit_score"] = round(comp_fit, 2)
    job["recency_bonus"] = round(r_bonus, 2)
    job["region_bonus"] = round(reg_bonus, 2)
    job["stage_a_score"] = stage_a

    # Startup flag (simple heuristic — refine if needed)
    jd_lower = jd.lower()
    job["is_startup"] = any(
        kw in jd_lower for kw in ["startup", "start-up", "series a", "series b", "early stage", "seed stage"]
    )

    # ── Routing decision ─────────────────────────────────────────────────────
    if gate_label == "exclude":
        job["routing"] = "reach_roles"
    elif stage_a < 2.0:
        job["routing"] = "skip"
    else:
        job["routing"] = "job_tracker"

    return job


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
- "refined_score": float 0-10 (adjust the rule-based score by ±1.5 max based on JD nuance)
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
    threshold = config.get("scoring", {}).get("stage_b_min_score", 6.0)

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
                min(10.0, (job["stage_a_score"] + job["stage_b_refined_score"]) / 2), 2
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
            if not r.get("is_genuine_data_role", True) and job.get("role_tier") == "tier1_core_data":
                job["routing_flag"] = "⚠️ Title may not match actual role"
