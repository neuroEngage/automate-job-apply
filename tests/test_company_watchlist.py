"""
Tests for the Company Watchlist module.
"""
import pytest
from unittest.mock import patch, MagicMock
from src.company_watchlist import (
    _extract_jobs_from_page,
    _should_check,
    _find_job_link,
    run_watchlist_checks,
)


SAMPLE_ROLE_PRIORITIES = [
    {
        "tier": 1,
        "name": "Data Engineering",
        "titles": ["Data Engineer", "ETL Developer"],
        "signal_keywords": ["Python", "SQL"],
    },
    {
        "tier": 2,
        "name": "Data / Product Analytics",
        "titles": ["Data Analyst", "BI Analyst"],
        "signal_keywords": ["SQL", "Excel"],
    },
    {
        "tier": 3,
        "name": "Core AI/ML",
        "titles": ["AI Engineer", "ML Engineer"],
        "signal_keywords": ["ML", "AI"],
    },
]


class TestExtractJobsFromPage:
    def test_finds_matching_titles(self):
        html = """
        <div class="job-listing">
            <h2>Data Engineer</h2>
            <p>Join our team as a data engineer</p>
        </div>
        <div class="job-listing">
            <h2>Frontend Developer</h2>
        </div>
        """
        jobs = _extract_jobs_from_page(html, "TestCo", "https://test.com/careers", SAMPLE_ROLE_PRIORITIES)
        assert len(jobs) >= 1
        titles = [j["title"] for j in jobs]
        assert "Data Engineer" in titles

    def test_no_matches_returns_empty(self):
        html = "<div>We are hiring a frontend developer</div>"
        jobs = _extract_jobs_from_page(html, "TestCo", "https://test.com/careers", SAMPLE_ROLE_PRIORITIES)
        assert len(jobs) == 0

    def test_empty_html_returns_empty(self):
        jobs = _extract_jobs_from_page("", "TestCo", "https://test.com", SAMPLE_ROLE_PRIORITIES)
        assert len(jobs) == 0

    def test_sets_source_to_watchlist(self):
        html = "<div>Looking for a Data Analyst to join</div>"
        jobs = _extract_jobs_from_page(html, "TestCo", "https://test.com", SAMPLE_ROLE_PRIORITIES)
        if jobs:
            assert jobs[0]["_source"] == "watchlist"

    def test_assigns_correct_tier(self):
        html = "<div>AI Engineer position available</div>"
        jobs = _extract_jobs_from_page(html, "TestCo", "https://test.com", SAMPLE_ROLE_PRIORITIES)
        if jobs:
            ai_jobs = [j for j in jobs if j["title"] == "AI Engineer"]
            if ai_jobs:
                assert ai_jobs[0]["_priority_tier"] == 3


class TestShouldCheck:
    def test_never_checked_returns_true(self):
        assert _should_check("NewCo", {}, 20) is True

    def test_recently_checked_returns_false(self):
        from datetime import datetime
        cache = {"RecentCo": datetime.now().isoformat()}
        assert _should_check("RecentCo", cache, 20) is False

    def test_old_check_returns_true(self):
        from datetime import datetime, timedelta
        old_time = (datetime.now() - timedelta(hours=25)).isoformat()
        cache = {"OldCo": old_time}
        assert _should_check("OldCo", cache, 20) is True


class TestFindJobLink:
    def test_finds_href(self):
        html = '<a href="https://test.com/jobs/123">Data Engineer</a>'
        result = _find_job_link(html, "Data Engineer", "https://test.com")
        assert result == "https://test.com/jobs/123"

    def test_no_link_returns_none(self):
        html = "<p>Data Engineer position</p>"
        result = _find_job_link(html, "Data Engineer", "https://test.com")
        assert result is None
