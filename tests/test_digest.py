"""
Tests for the Daily Digest module.
"""
import pytest
from src.digest import compose_digest, ALL_CATEGORIES


def _make_tracker_job(category="Data Engineering", score=80, **overrides):
    base = {
        "job_id": "test123",
        "title": "Data Engineer",
        "company": "TestCo",
        "location": "Mumbai",
        "url": "https://example.com/job/123",
        "category_label": category,
        "overall_score": score,
        "stage_a_score": score,
        "priority_tier": 1,
        "priority_tier_name": "Data Engineering",
        "skill_match_score": 70,
        "experience_fit_score": 100,
        "ai_exposure_score": 40,
        "company_opportunity_score": 55,
        "location_fit_score": 100,
        "freshness_score": 80,
        "product_business_score": 20,
        "startup_ownership_score": 30,
        "matched_skills": ["Python", "SQL"],
        "missing_skills": ["Kafka"],
    }
    base.update(overrides)
    return base


class TestComposeDigest:
    def test_contains_header(self):
        digest = compose_digest([], [], {"jobs_scraped": 0, "jobs_new": 0}, {})
        assert "JobRadar Daily Digest" in digest

    def test_contains_all_sections(self):
        digest = compose_digest([], [], {"jobs_scraped": 0, "jobs_new": 0}, {})
        for category in ALL_CATEGORIES:
            assert category in digest

    def test_contains_job_details(self):
        jobs = [_make_tracker_job()]
        digest = compose_digest(jobs, [], {"jobs_scraped": 10, "jobs_new": 5}, {})
        assert "Data Engineer" in digest
        assert "TestCo" in digest
        assert "Overall Match:" in digest

    def test_contains_multiple_categories(self):
        jobs = [
            _make_tracker_job(category="Data Engineering"),
            _make_tracker_job(category="Core AI/ML", title="ML Engineer"),
        ]
        digest = compose_digest(jobs, [], {"jobs_scraped": 10, "jobs_new": 2}, {})
        assert "Data Engineering (1 jobs)" in digest
        assert "Core AI/ML (1 jobs)" in digest

    def test_contains_reach_roles(self):
        reach = [_make_tracker_job(title="Senior Data Engineer")]
        digest = compose_digest([], reach, {"jobs_scraped": 5, "jobs_new": 1}, {})
        assert "Reach Roles" in digest
        assert "Senior Data Engineer" in digest

    def test_contains_footer_stats(self):
        stats = {"jobs_scraped": 100, "jobs_new": 20, "spend_usd": 0.15, "errors": []}
        digest = compose_digest([], [], stats, {})
        assert "$0.15" in digest

    def test_shows_errors_if_present(self):
        stats = {"jobs_scraped": 0, "jobs_new": 0, "errors": ["Scraping failed"]}
        digest = compose_digest([], [], stats, {})
        assert "Scraping failed" in digest

    def test_empty_jobs_shows_no_jobs_message(self):
        digest = compose_digest([], [], {"jobs_scraped": 0, "jobs_new": 0}, {})
        assert "No new jobs" in digest
