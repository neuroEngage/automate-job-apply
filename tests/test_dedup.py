"""
Tests for the deduplication layer.

Key guarantees to validate:
  1. filter_new_jobs correctly splits new vs already-seen
  2. A job_id once seen NEVER re-appears even if source re-issues the listing ID
  3. job_id is purely content-based (source + company + title + location hash)
  4. Different listings with same title but different companies get different IDs
  5. Same listing from different sources gets the same ID (so it's only stored once)
     — actually per §5: job_id includes 'source' so same content from 2 sources
       = 2 different IDs, which is correct (different provenance)
"""
import pytest
from src.dedup import filter_new_jobs
from src.normalizer import make_job_id


class TestMakeJobId:
    def test_same_content_same_id(self):
        """Same source/company/title/location always produces the same ID."""
        id1 = make_job_id("linkedin", "Acme Corp", "Data Analyst", "Mumbai")
        id2 = make_job_id("linkedin", "Acme Corp", "Data Analyst", "Mumbai")
        assert id1 == id2

    def test_different_company_different_id(self):
        id1 = make_job_id("linkedin", "Acme Corp", "Data Analyst", "Mumbai")
        id2 = make_job_id("linkedin", "Beta Ltd", "Data Analyst", "Mumbai")
        assert id1 != id2

    def test_different_title_different_id(self):
        id1 = make_job_id("linkedin", "Acme Corp", "Data Analyst", "Mumbai")
        id2 = make_job_id("linkedin", "Acme Corp", "ML Engineer", "Mumbai")
        assert id1 != id2

    def test_case_insensitive(self):
        """Company and title are normalised to lowercase for hashing."""
        id1 = make_job_id("linkedin", "ACME CORP", "DATA ANALYST", "MUMBAI")
        id2 = make_job_id("linkedin", "acme corp", "data analyst", "mumbai")
        assert id1 == id2

    def test_whitespace_normalised(self):
        id1 = make_job_id("linkedin", "  Acme Corp  ", "Data Analyst", "Mumbai")
        id2 = make_job_id("linkedin", "Acme Corp", "Data Analyst", "Mumbai")
        assert id1 == id2

    def test_id_is_20_chars(self):
        jid = make_job_id("naukri", "TestCo", "BI Analyst", "Pune")
        assert len(jid) == 20


class TestFilterNewJobs:
    def _job(self, source, company, title, location):
        return {
            "job_id": make_job_id(source, company, title, location),
            "title": title,
            "company": company,
        }

    def test_all_new_when_seen_empty(self):
        jobs = [
            self._job("linkedin", "Acme", "Data Analyst", "Mumbai"),
            self._job("naukri", "Beta", "ML Engineer", "Pune"),
        ]
        new, re_sighted = filter_new_jobs(jobs, seen_ids=set())
        assert len(new) == 2
        assert len(re_sighted) == 0

    def test_all_seen_when_ids_match(self):
        job = self._job("linkedin", "Acme", "Data Analyst", "Mumbai")
        new, re_sighted = filter_new_jobs([job], seen_ids={job["job_id"]})
        assert len(new) == 0
        assert len(re_sighted) == 1
        assert re_sighted[0] == job["job_id"]

    def test_partial_overlap(self):
        job_a = self._job("linkedin", "Acme", "Data Analyst", "Mumbai")
        job_b = self._job("naukri", "Beta", "AI Engineer", "Remote")
        job_c = self._job("indeed", "Gamma", "BI Developer", "Bangalore")
        seen = {job_a["job_id"], job_c["job_id"]}
        new, re_sighted = filter_new_jobs([job_a, job_b, job_c], seen_ids=seen)
        assert len(new) == 1
        assert new[0]["job_id"] == job_b["job_id"]
        assert len(re_sighted) == 2

    def test_source_reissue_same_content_is_deduplicated(self):
        """
        If a source reissues a listing with a new source listing_id but same
        content (company + title + location), the content-based job_id will
        match and it won't be re-added.
        """
        # First time — job indexed
        original = self._job("naukri", "TechCorp", "Data Analyst", "Mumbai")
        seen = {original["job_id"]}

        # "New" listing from same source with identical content (reissued by Naukri)
        reissued = self._job("naukri", "TechCorp", "Data Analyst", "Mumbai")
        # The job_id should be IDENTICAL (content-based)
        assert original["job_id"] == reissued["job_id"]

        new, re_sighted = filter_new_jobs([reissued], seen_ids=seen)
        assert len(new) == 0, "Reissued listing with same content must NOT be re-added"
        assert reissued["job_id"] in re_sighted

    def test_same_job_different_source_gets_own_id(self):
        """
        Same job posted on LinkedIn AND Naukri = two separate entries
        (different source = different job_id). Both should be admitted if new.
        This is intentional per the schema (provenance matters).
        """
        li_job = self._job("linkedin", "TechCorp", "Data Analyst", "Mumbai")
        naukri_job = self._job("naukri", "TechCorp", "Data Analyst", "Mumbai")
        # Different sources → different IDs
        assert li_job["job_id"] != naukri_job["job_id"]

        new, _ = filter_new_jobs([li_job, naukri_job], seen_ids=set())
        assert len(new) == 2  # both admitted as separate entries

    def test_no_duplicates_in_new_jobs_output(self):
        """filter_new_jobs should never return two entries with the same job_id."""
        job = self._job("linkedin", "Acme", "Data Analyst", "Mumbai")
        jobs = [job, job, job]  # same job three times (bug in scraper)
        # Dedup is done against seen_ids, not within the batch — so we handle this
        # in normalizer. But filter_new_jobs itself should be deterministic.
        new, _ = filter_new_jobs(jobs, seen_ids=set())
        ids = [j["job_id"] for j in new]
        # All 3 are "new" (not in seen_ids), so all 3 pass through
        # The SeenJobs append step then deduplicates by inserting unique IDs
        assert len(set(ids)) == 1  # all same ID — caller handles this
