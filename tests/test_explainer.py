"""
Tests for the Explainer module.
"""
import pytest
from src.explainer import generate_explanation, generate_explanation_dict, generate_compact_explanation


def _make_scored_job(**overrides):
    """Create a scored job dict with default sub-scores."""
    base = {
        "job_id": "test123",
        "title": "Data Engineer",
        "company": "TestCo",
        "overall_score": 85.0,
        "stage_a_score": 85.0,
        "priority_tier": 1,
        "priority_tier_name": "Data Engineering",
        "skill_match_score": 72.5,
        "experience_fit_score": 100.0,
        "ai_exposure_score": 45.0,
        "company_opportunity_score": 60.0,
        "location_fit_score": 100.0,
        "freshness_score": 80.0,
        "product_business_score": 25.0,
        "startup_ownership_score": 40.0,
        "matched_skills": ["Python", "SQL", "PySpark", "AWS", "ETL"],
        "missing_skills": ["Kafka", "Airflow", "Docker"],
    }
    base.update(overrides)
    return base


class TestGenerateExplanation:
    def test_contains_overall_score(self):
        job = _make_scored_job()
        explanation = generate_explanation(job)
        assert "Overall Match: 85/100" in explanation

    def test_contains_tier_info(self):
        job = _make_scored_job()
        explanation = generate_explanation(job)
        assert "Data Engineering" in explanation
        assert "#1" in explanation

    def test_contains_matched_skills(self):
        job = _make_scored_job()
        explanation = generate_explanation(job)
        assert "Python ✓" in explanation
        assert "SQL ✓" in explanation

    def test_contains_missing_skills(self):
        job = _make_scored_job()
        explanation = generate_explanation(job)
        assert "Kafka" in explanation
        assert "Airflow" in explanation

    def test_handles_empty_skills(self):
        job = _make_scored_job(matched_skills=[], missing_skills=[])
        explanation = generate_explanation(job)
        assert "None" in explanation  # Should say "None" for empty

    def test_handles_missing_fields_gracefully(self):
        """Should not crash if some scoring fields are missing."""
        job = {"job_id": "minimal"}
        explanation = generate_explanation(job)
        assert "Overall Match: 0/100" in explanation


class TestGenerateExplanationDict:
    def test_returns_all_expected_keys(self):
        job = _make_scored_job()
        result = generate_explanation_dict(job)
        expected_keys = [
            "overall_match", "priority_tier", "priority_tier_name",
            "skill_match", "fresher_fit", "ai_exposure", "company_fit",
            "location_fit", "freshness", "product_business", "startup_ownership",
            "matched_skills", "missing_skills",
        ]
        for key in expected_keys:
            assert key in result

    def test_values_match_job_data(self):
        job = _make_scored_job()
        result = generate_explanation_dict(job)
        assert result["overall_match"] == 85
        assert result["priority_tier"] == 1
        assert result["skill_match"] == 72  # rounded


class TestCompactExplanation:
    def test_compact_format(self):
        job = _make_scored_job()
        compact = generate_compact_explanation(job)
        assert "85/100" in compact
        assert "T1" in compact
        assert "Data Engineering" in compact

    def test_compact_handles_missing(self):
        job = {"job_id": "minimal"}
        compact = generate_compact_explanation(job)
        assert "0/100" in compact
