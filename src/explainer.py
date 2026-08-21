"""
JobRadar v2 — Explainable Per-Job Output

Generates a structured explanation block per job from actual sub-scores.
Never fabricated — reads real computed values only.

Output format:
    Overall Match: 91/100
    Role Priority: Data Engineering — #1
    Skill Match: 88%   Fresher Fit: 95%   AI Exposure: 72%
    Company Fit: 90%   Location Fit: 100%   Freshness: 100%
    Matched Skills: Python ✓  SQL ✓  PySpark ✓  AWS ✓  ETL ✓
    Missing/Nice-to-have: Kafka, Airflow, Docker
"""
import logging

logger = logging.getLogger(__name__)


def generate_explanation(job: dict) -> str:
    """
    Generates a human-readable explanation block from actual sub-scores.
    Returns a multi-line string suitable for sheet column or digest.
    """
    overall = job.get("overall_score", job.get("stage_a_score", 0))
    tier = job.get("priority_tier", 6)
    tier_name = job.get("priority_tier_name", "Unknown")
    skill = job.get("skill_match_score", 0)
    fresher = job.get("experience_fit_score", 0)
    ai = job.get("ai_exposure_score", 0)
    company = job.get("company_opportunity_score", 0)
    location = job.get("location_fit_score", 0)
    freshness = job.get("freshness_score", 0)
    product = job.get("product_business_score", 0)
    startup = job.get("startup_ownership_score", 0)

    matched = job.get("matched_skills", [])
    missing = job.get("missing_skills", [])

    # Format matched skills with checkmarks
    matched_str = "  ".join(f"{s} ✓" for s in matched[:8]) if matched else "None"
    missing_str = ", ".join(missing[:8]) if missing else "None"

    lines = [
        f"Overall Match: {round(overall)}/100",
        f"Role Priority: {tier_name} — #{tier}",
        f"Skill Match: {round(skill)}%   Fresher Fit: {round(fresher)}%   AI Exposure: {round(ai)}%",
        f"Company Fit: {round(company)}%   Location Fit: {round(location)}%   Freshness: {round(freshness)}%",
        f"Product/Biz: {round(product)}%   Startup/Own: {round(startup)}%",
        f"Matched Skills: {matched_str}",
        f"Missing/Nice-to-have: {missing_str}",
    ]

    return "\n".join(lines)


def generate_explanation_dict(job: dict) -> dict:
    """
    Returns a structured dict of the explanation for programmatic use.
    """
    return {
        "overall_match": round(job.get("overall_score", job.get("stage_a_score", 0))),
        "priority_tier": job.get("priority_tier", 6),
        "priority_tier_name": job.get("priority_tier_name", "Unknown"),
        "skill_match": round(job.get("skill_match_score", 0)),
        "fresher_fit": round(job.get("experience_fit_score", 0)),
        "ai_exposure": round(job.get("ai_exposure_score", 0)),
        "company_fit": round(job.get("company_opportunity_score", 0)),
        "location_fit": round(job.get("location_fit_score", 0)),
        "freshness": round(job.get("freshness_score", 0)),
        "product_business": round(job.get("product_business_score", 0)),
        "startup_ownership": round(job.get("startup_ownership_score", 0)),
        "matched_skills": job.get("matched_skills", []),
        "missing_skills": job.get("missing_skills", []),
    }


def generate_compact_explanation(job: dict) -> str:
    """
    Generates a compact one-line explanation for sheet columns.
    """
    overall = round(job.get("overall_score", job.get("stage_a_score", 0)))
    tier = job.get("priority_tier", 6)
    tier_name = job.get("priority_tier_name", "Unknown")
    skill = round(job.get("skill_match_score", 0))
    fresher = round(job.get("experience_fit_score", 0))
    ai = round(job.get("ai_exposure_score", 0))

    return f"{overall}/100 | T{tier} {tier_name} | Skill:{skill}% Fresh:{fresher}% AI:{ai}%"
