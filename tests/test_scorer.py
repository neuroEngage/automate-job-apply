"""
Tests for the JobRadar scoring engine.

Validates that the score calibration matches the manual scoring already
proven in the spreadsheet work:

 1. BNP Paribas Jr. AML-type fresher role → scores HIGH (exp_fit=10, high overall)
 2. CodeVyasa 4+YOE Data Engineer role → capped/flagged as stretch/exclude
 3. Tier 1 Data Analyst outranks mediocre Tier 2 Product Associate
 4. Strong-fit Tier 2 (AI Product Associate with data chops) CAN beat weak Tier 1
 5. 0–3 day old posting gets recency bonus; >30 day old gets none
 6. "exclude" gate (5YOE) means routing='reach_roles', not 'job_tracker'
"""
import yaml
import pytest

from src.scorer import (
    experience_gate,
    skill_match_score,
    recency_bonus,
    compute_stage_a,
    score_all_stage_a,
)

# ─────────────────────────────────────────────────────────────────────────────
# Load real config for integration-style tests
# ─────────────────────────────────────────────────────────────────────────────
with open("config.yaml", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests: experience_gate
# ─────────────────────────────────────────────────────────────────────────────

class TestExperienceGate:
    def test_fresher_scores_10(self):
        label, score = experience_gate(0)
        assert label == "ideal_fresher"
        assert score == 10

    def test_1yr_scores_9(self):
        label, score = experience_gate(1)
        assert label == "ideal_1yr"
        assert score == 9

    def test_2yr_scores_6(self):
        label, score = experience_gate(2)
        assert label == "pass"
        assert score == 6

    def test_3yr_is_stretch(self):
        label, score = experience_gate(3)
        assert label == "stretch"
        assert score == 3

    def test_5yr_excluded(self):
        label, score = experience_gate(5)
        assert label == "exclude"
        assert score == 0

    def test_none_is_unknown_neutral(self):
        label, score = experience_gate(None)
        assert label == "unknown"
        assert score == 5

    def test_fresher_beats_2yr_on_exp_fit(self):
        """Fresher posting (0 YOE) must score higher than 2YOE posting."""
        _, fresher_score = experience_gate(0)
        _, two_yr_score = experience_gate(2)
        assert fresher_score > two_yr_score


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests: skill_match_score
# ─────────────────────────────────────────────────────────────────────────────

class TestSkillMatch:
    SKILLS = ["Python", "SQL", "Power BI", "machine learning", "EDA", "pandas"]

    def test_no_match_returns_zero(self):
        assert skill_match_score("We need Java developers", self.SKILLS) == 0.0

    def test_partial_match(self):
        score = skill_match_score("Python and SQL required", self.SKILLS)
        assert 0 < score < 10

    def test_high_overlap_near_10(self):
        jd = "Python, SQL, Power BI, machine learning, EDA, pandas, data analysis"
        score = skill_match_score(jd, self.SKILLS)
        assert score >= 9.0

    def test_empty_jd_returns_zero(self):
        assert skill_match_score("", self.SKILLS) == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests: recency_bonus
# ─────────────────────────────────────────────────────────────────────────────

class TestRecencyBonus:
    def test_fresh_0_days_gets_max_bonus(self):
        bonus = recency_bonus(0, CONFIG)
        assert bonus == CONFIG["scoring"]["recency_bonus"]["days_0_to_3"]

    def test_3_days_still_max_bonus(self):
        assert recency_bonus(3, CONFIG) == CONFIG["scoring"]["recency_bonus"]["days_0_to_3"]

    def test_4_days_lower_bonus(self):
        assert recency_bonus(4, CONFIG) < recency_bonus(3, CONFIG)

    def test_over_30_days_no_bonus(self):
        assert recency_bonus(31, CONFIG) == 0.0

    def test_unknown_age_small_neutral(self):
        bonus = recency_bonus(None, CONFIG)
        assert 0 <= bonus <= 1.0

    def test_recency_decreases_monotonically(self):
        """Newer always beats older for recency bonus."""
        assert recency_bonus(1, CONFIG) >= recency_bonus(5, CONFIG)
        assert recency_bonus(5, CONFIG) >= recency_bonus(10, CONFIG)
        assert recency_bonus(10, CONFIG) >= recency_bonus(20, CONFIG)
        assert recency_bonus(20, CONFIG) >= recency_bonus(35, CONFIG)


# ─────────────────────────────────────────────────────────────────────────────
# Integration tests: Stage A scoring
# ─────────────────────────────────────────────────────────────────────────────

def _make_job(
    title="Data Analyst",
    company="TestCo",
    category="mumbai",
    role_tier="tier1_core_data",
    exp_min=0,
    days_old=1,
    salary_text="12 LPA",
    salary_currency="INR",
    jd="Python SQL EDA machine learning Power BI data analysis fresher",
):
    return {
        "job_id": "test",
        "title": title,
        "company": company,
        "category": category,
        "role_tier": role_tier,
        "experience_required_min": exp_min,
        "experience_required_text": f"{exp_min} years",
        "days_old": days_old,
        "salary_text": salary_text,
        "salary_currency": salary_currency,
        "description_text": jd,
        "is_startup": False,
        "source": "linkedin",
    }


class TestStageAIntegration:

    def test_bnp_paribas_jr_role_scores_high(self):
        """BNP Paribas Jr. AML type fresher role should score well (exp_fit=10)."""
        job = _make_job(
            title="Junior Data Analyst",
            company="BNP Paribas",
            category="mumbai",
            role_tier="tier1_core_data",
            exp_min=0,
            days_old=2,
            jd="Fresher / 0-1 years. Python, SQL, Excel, Data Analyst, analytical skills, finance"
        )
        result = compute_stage_a(job, CONFIG)
        assert result["experience_gate_label"] == "ideal_fresher"
        assert result["experience_fit_score"] == 10
        assert result["stage_a_score"] >= 5.0  # should score decently overall
        assert result["routing"] == "job_tracker"

    def test_codevyasa_4yr_role_gets_excluded_or_stretch(self):
        """CodeVyasa 4+ YOE Data Engineer should be excluded or capped."""
        job = _make_job(
            title="Data Engineer",
            company="CodeVyasa",
            category="india_remote",
            role_tier="tier1_core_data",
            exp_min=5,
            days_old=3,
            jd="Requires 5+ years experience. PySpark, Scala, advanced Hadoop. Senior Data Engineer."
        )
        result = compute_stage_a(job, CONFIG)
        assert result["experience_gate_label"] == "exclude"
        assert result["routing"] == "reach_roles"

    def test_tier1_outranks_mediocre_tier2(self):
        """A decent Tier 1 Data Analyst role should outscore a mediocre Tier 2 Product Associate."""
        tier1_job = _make_job(
            title="Data Analyst",
            role_tier="tier1_core_data",
            category="mumbai",
            exp_min=1,
            days_old=2,
            jd="Python SQL pandas EDA data visualization power bi machine learning 1 year experience"
        )
        tier2_job = _make_job(
            title="Product Associate",
            role_tier="tier2_broader",
            category="mumbai",
            exp_min=1,
            days_old=5,
            jd="Product management communication stakeholder liaison presentations"  # no data skills
        )
        t1_scored = compute_stage_a(tier1_job, CONFIG)
        t2_scored = compute_stage_a(tier2_job, CONFIG)
        assert t1_scored["stage_a_score"] > t2_scored["stage_a_score"], (
            f"Expected Tier1 ({t1_scored['stage_a_score']}) > Tier2 ({t2_scored['stage_a_score']})"
        )

    def test_strong_tier2_can_beat_weak_tier1(self):
        """
        A genuinely data-heavy Tier 2 role (AI Product Associate with Python/SQL/analytics JD)
        should be able to outscore a very weak Tier 1 role (poor skill match, older posting).
        """
        strong_tier2 = _make_job(
            title="AI Product Associate",
            role_tier="tier2_broader",
            category="mumbai",
            exp_min=0,      # fresher
            days_old=1,     # very fresh
            jd="Python SQL data analytics LLM prompt engineering EDA machine learning GenAI "
               "product thinking business intelligence freshers welcome 0-1 years"
        )
        weak_tier1 = _make_job(
            title="Data Engineer",
            role_tier="tier1_core_data",
            category="global_remote",
            exp_min=2,
            days_old=28,    # old posting
            salary_text="",
            jd="Java Scala Hadoop enterprise data warehouse 2 years minimum"  # low overlap with Nagesh
        )
        t2_scored = compute_stage_a(strong_tier2, CONFIG)
        t1_scored = compute_stage_a(weak_tier1, CONFIG)
        assert t2_scored["stage_a_score"] > t1_scored["stage_a_score"], (
            f"Strong Tier2 ({t2_scored['stage_a_score']}) should beat weak Tier1 ({t1_scored['stage_a_score']})"
        )

    def test_fresh_posting_beats_old_same_job(self):
        """Same job, same score components — newer posting should always score higher."""
        fresh = _make_job(days_old=1)
        old = _make_job(days_old=25)
        fresh_scored = compute_stage_a(fresh, CONFIG)
        old_scored = compute_stage_a(old, CONFIG)
        assert fresh_scored["stage_a_score"] > old_scored["stage_a_score"]

    def test_exclude_gate_routes_to_reach_roles(self):
        """Jobs with >4 YOE required must route to reach_roles, not job_tracker."""
        job = _make_job(exp_min=5)
        result = compute_stage_a(job, CONFIG)
        assert result["routing"] == "reach_roles"

    def test_score_all_stage_a_routing(self):
        """score_all_stage_a should correctly split jobs into 3 buckets."""
        jobs = [
            _make_job(title="Data Analyst", exp_min=0, days_old=1),     # tracker
            _make_job(title="ML Engineer", exp_min=5, days_old=2),       # reach_roles (5yr)
            _make_job(title="BI Analyst", exp_min=1, days_old=3),        # tracker
        ]
        tracker, reach, skipped = score_all_stage_a(jobs, CONFIG)
        reach_ids = [j["experience_required_min"] for j in reach]
        assert 5 in reach_ids, "5-YOE job should be in reach_roles"
        assert len(tracker) >= 2, "At least 2 jobs should reach the tracker"


# ─────────────────────────────────────────────────────────────────────────────
# Tier miscalibration guards
# ─────────────────────────────────────────────────────────────────────────────

class TestTierCalibration:
    """
    Per the brief: if Tier 2 results never appear in the top 10, the penalty
    is too high. If Tier 2 results dominate, it's too low.
    These tests enforce the calibration at a boundary level.
    """

    def test_tier2_penalty_not_zero(self):
        """Tier 2 must have some penalty vs Tier 1 on identical jobs."""
        base_jd = "Python SQL pandas EDA data analysis machine learning"
        tier1 = _make_job(title="Data Analyst", role_tier="tier1_core_data", jd=base_jd)
        tier2 = _make_job(title="Business Analyst", role_tier="tier2_broader", jd=base_jd)
        t1 = compute_stage_a(tier1, CONFIG)
        t2 = compute_stage_a(tier2, CONFIG)
        assert t1["stage_a_score"] > t2["stage_a_score"], "Tier 1 should outscore Tier 2 on identical content"

    def test_tier2_penalty_not_total_exclusion(self):
        """Tier 2 jobs should NOT be totally crushed — a great-fit Tier 2 must still reach the tracker."""
        great_tier2 = _make_job(
            title="AI Product Associate",
            role_tier="tier2_broader",
            category="mumbai",
            exp_min=0,
            days_old=1,
            jd="Python SQL pandas machine learning LLM EDA GenAI data analytics product management"
        )
        result = compute_stage_a(great_tier2, CONFIG)
        assert result["routing"] == "job_tracker", (
            "A great-fit Tier 2 role should still reach Job Tracker, not be skipped"
        )
