"""
Tests for the normalizer — schema mapping and YOE extraction.
"""
import pytest
from src.normalizer import extract_min_yoe, make_job_id, normalize, normalize_all


class TestExtractMinYoe:
    def test_explicit_plus_years(self):
        assert extract_min_yoe("4+ years required") == 4

    def test_range_returns_minimum(self):
        assert extract_min_yoe("2-5 years of experience") == 2

    def test_minimum_of(self):
        assert extract_min_yoe("minimum of 3 years") == 3

    def test_at_least(self):
        assert extract_min_yoe("at least 5 years") == 5

    def test_fresher_keyword_returns_0(self):
        assert extract_min_yoe("fresher graduates are welcome to apply") == 0

    def test_entry_level_returns_0(self):
        assert extract_min_yoe("entry-level position, no experience required") == 0

    def test_junior_returns_0(self):
        assert extract_min_yoe("Junior Data Analyst role") == 0

    def test_no_yoe_returns_none(self):
        result = extract_min_yoe("We need a passionate candidate to join our team.")
        assert result is None

    def test_prefers_fresher_signal_over_large_number(self):
        """If JD says 'fresher' but also mentions '5 years of domain context', min should be 0."""
        result = extract_min_yoe("Fresher or 0-1 year. Domain knowledge of 5 years is preferred.")
        assert result == 0

    def test_empty_returns_none(self):
        assert extract_min_yoe("") is None

    def test_none_returns_none(self):
        assert extract_min_yoe(None) is None


class TestNormalize:
    def _linkedin_raw(self, **overrides):
        base = {
            "_source": "linkedin",
            "_role_tier": "tier1_core_data",
            "_category": "mumbai",
            "title": "Data Analyst",
            "companyName": "Acme Corp",
            "location": "Mumbai, India",
            "url": "https://linkedin.com/jobs/view/12345",
            "postedDate": "2026-07-14",
            "experienceLevel": "Entry level",
            "salary": "",
            "description": "Python SQL EDA fresher welcome 0-1 years machine learning",
        }
        base.update(overrides)
        return base

    def test_linkedin_normalizes_correctly(self):
        raw = self._linkedin_raw()
        result = normalize(raw)
        assert result is not None
        assert result["title"] == "Data Analyst"
        assert result["company"] == "Acme Corp"
        assert result["source"] == "linkedin"
        assert result["role_tier"] == "tier1_core_data"
        assert result["category"] == "mumbai"
        assert "job_id" in result and len(result["job_id"]) == 20

    def test_experience_min_extracted_from_linkedin(self):
        raw = self._linkedin_raw(description="Fresher or 0-1 year experience required")
        result = normalize(raw)
        assert result["experience_required_min"] == 0

    def test_missing_title_returns_none(self):
        raw = self._linkedin_raw()
        raw["title"] = ""
        raw["companyName"] = "Acme"
        result = normalize(raw)
        assert result is None

    def test_missing_company_returns_none(self):
        raw = self._linkedin_raw()
        raw["companyName"] = ""
        result = normalize(raw)
        assert result is None

    def test_tier1_keyword_in_title_overrides_tier2_search(self):
        """If a Tier 2 search surfaces a 'Data Analyst' role, it should be upgraded to Tier 1."""
        raw = self._linkedin_raw()
        raw["_role_tier"] = "tier2_broader"  # surfaced by Tier 2 search
        raw["title"] = "Data Analyst"         # but title is clearly Tier 1
        result = normalize(raw)
        assert result["role_tier"] == "tier1_core_data"

    def test_normalize_all_skips_invalid(self):
        raws = [
            self._linkedin_raw(),           # valid
            {"_source": "linkedin"},        # missing title + company → skip
            self._linkedin_raw(companyName="Beta Ltd", title="ML Engineer"),  # valid
        ]
        results = normalize_all(raws)
        assert len(results) == 2

    def test_description_truncated_to_4000(self):
        long_desc = "x" * 5000
        raw = self._linkedin_raw(description=long_desc)
        result = normalize(raw)
        assert len(result["description_text"]) == 4000
