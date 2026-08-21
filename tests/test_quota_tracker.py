"""
Tests for the Weekly Quota Tracker module.
"""
import pytest
from src.quota_tracker import _quota_notes, _empty_quota
from datetime import date


class TestQuotaNotes:
    def test_no_applications(self):
        quota = {
            "tier_1_applied": 0, "tier_2_applied": 0,
            "tier_3_applied": 0, "tiers_4_5_6_applied": 0,
            "total_applied": 0,
            "tier_1_target": 0.40, "tier_2_target": 0.30,
            "tier_3_target": 0.20, "tiers_4_5_6_target": 0.10,
        }
        assert "No applications" in _quota_notes(quota)

    def test_on_track(self):
        quota = {
            "tier_1_applied": 4, "tier_2_applied": 3,
            "tier_3_applied": 2, "tiers_4_5_6_applied": 1,
            "total_applied": 10,
            "tier_1_target": 0.40, "tier_2_target": 0.30,
            "tier_3_target": 0.20, "tiers_4_5_6_target": 0.10,
        }
        assert "on track" in _quota_notes(quota).lower()

    def test_tier1_underrepresented(self):
        quota = {
            "tier_1_applied": 0, "tier_2_applied": 5,
            "tier_3_applied": 3, "tiers_4_5_6_applied": 2,
            "total_applied": 10,
            "tier_1_target": 0.40, "tier_2_target": 0.30,
            "tier_3_target": 0.20, "tiers_4_5_6_target": 0.10,
        }
        note = _quota_notes(quota)
        assert "T1" in note and "under" in note.lower()


class TestEmptyQuota:
    def test_returns_all_zeros(self):
        today = date.today()
        result = _empty_quota(today, {"tier_1": 0.40, "tier_2": 0.30})
        assert result["total_applied"] == 0
        assert result["tier_1_applied"] == 0
        assert result["week_ending"] == str(today)
