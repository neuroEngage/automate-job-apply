"""
Tests for the Company Opportunity Score module.
"""
import pytest
from src.company_opportunity import (
    compute_company_opportunity,
    _fuzzy_match_company,
    _funding_score,
    _ownership_keyword_score,
    reset_cache,
)


class TestFuzzyMatchCompany:
    FUNDED_LIST = [
        {"name": "Fractal Analytics", "last_round": "Series C+", "size": "large"},
        {"name": "Rubixe", "last_round": "Seed", "size": "small"},
        {"name": "Tiger Analytics", "last_round": "Series B", "size": "medium"},
    ]

    def test_exact_match(self):
        result = _fuzzy_match_company("Fractal Analytics", self.FUNDED_LIST)
        assert result is not None
        assert result["name"] == "Fractal Analytics"

    def test_case_insensitive_match(self):
        result = _fuzzy_match_company("fractal analytics", self.FUNDED_LIST)
        assert result is not None

    def test_with_suffix_stripped(self):
        result = _fuzzy_match_company("Fractal Analytics Pvt Ltd", self.FUNDED_LIST)
        assert result is not None

    def test_substring_match(self):
        result = _fuzzy_match_company("Fractal", self.FUNDED_LIST)
        assert result is not None

    def test_no_match_returns_none(self):
        result = _fuzzy_match_company("Totally Unknown Corp", self.FUNDED_LIST)
        assert result is None

    def test_empty_name_returns_none(self):
        assert _fuzzy_match_company("", self.FUNDED_LIST) is None
        assert _fuzzy_match_company(None, self.FUNDED_LIST) is None


class TestFundingScore:
    def test_series_c_plus_scores_high(self):
        score = _funding_score({"last_round": "Series C+", "amount_usd": 360000000, "year": 2022})
        assert score >= 80

    def test_seed_scores_lower(self):
        score = _funding_score({"last_round": "Seed", "amount_usd": 500000, "year": 2020})
        assert score < 60

    def test_unknown_company_scores_neutral(self):
        score = _funding_score(None)
        assert score == 50.0

    def test_bootstrapped_is_neutral(self):
        score = _funding_score({"last_round": "Bootstrapped"})
        assert score == 50.0

    def test_public_scores_high(self):
        score = _funding_score({"last_round": "Public", "amount_usd": 2000000000, "year": 2021})
        assert score >= 85


class TestOwnershipKeywords:
    def test_no_keywords_returns_zero(self):
        assert _ownership_keyword_score("We are looking for a software developer") == 0.0

    def test_one_keyword_scores_moderate(self):
        score = _ownership_keyword_score("Take ownership of the data pipeline")
        assert score > 0

    def test_many_keywords_scores_high(self):
        jd = ("Join our fast-paced startup. Take ownership of cross-functional projects. "
              "Build from scratch in a high-growth environment. Founder-led company.")
        score = _ownership_keyword_score(jd)
        assert score >= 70

    def test_empty_returns_zero(self):
        assert _ownership_keyword_score("") == 0.0


class TestComputeCompanyOpportunity:
    def setup_method(self):
        reset_cache()

    def test_known_funded_company_scores_above_neutral(self):
        """A company in funded_companies.yaml should score above the 50 midpoint."""
        result = compute_company_opportunity(
            "Fractal Analytics",
            "Join our high-growth team with ownership opportunities"
        )
        assert result["company_opportunity_score"] >= 50
        assert result["funded_company_match"] is not None

    def test_unknown_company_scores_around_midpoint(self):
        """Unknown companies should NOT score zero — neutral midpoint."""
        result = compute_company_opportunity(
            "Totally Random Unknown Corp XYZ",
            "Looking for a developer"
        )
        score = result["company_opportunity_score"]
        assert score >= 20, f"Unknown company scored too low: {score}"
        assert result["funded_company_match"] is None

    def test_unknown_company_with_ownership_jd_scores_higher(self):
        """Unknown company + ownership-rich JD should score higher than bland JD."""
        bland = compute_company_opportunity("Unknown Corp", "Software developer needed")
        rich = compute_company_opportunity(
            "Unknown Corp",
            "Fast-paced startup looking for someone with ownership mentality. "
            "Build from scratch, cross-functional, high-growth environment."
        )
        assert rich["company_opportunity_score"] > bland["company_opportunity_score"]

    def test_score_is_bounded(self):
        result = compute_company_opportunity("Fractal Analytics", "ownership " * 100)
        assert 0 <= result["company_opportunity_score"] <= 100

    def test_returns_all_expected_keys(self):
        result = compute_company_opportunity("Test", "test JD")
        assert "company_opportunity_score" in result
        assert "funding_score" in result
        assert "ownership_score" in result
        assert "stability_score" in result
        assert "funded_company_match" in result
