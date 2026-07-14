"""Tests for the audit evidence collectors (T1).

A failed optional query must never stop the audit - but it must never be invisible
either. These tests pin both halves of that: the fetch still completes when a query
blows up, the failure is recorded on the data dict, and one audit's warnings can
never leak into the next one's.

No network, no Google Ads API, no client data: a fake client returns zero rows for
every query, so the REAL fetch code runs end to end.

    python3 test_evidence_collectors.py
"""
import contextlib
import json
import unittest
from unittest import mock

from google.ads.googleads.errors import GoogleAdsException

import fetch_account_data as fad
import generate_narrative as gn
from analyse_account import analyse_account, select_top_issues


# ── Fakes ─────────────────────────────────────────────────────────────────────

class _FakeRequest:
    """Stand-in for SearchGoogleAdsRequest (run_query just sets two fields on it)."""
    customer_id = ""
    query = ""


class _FakeService:
    def search(self, request=None, timeout=None, customer_id=None, query=None):
        return []                       # every query comes back empty, none of them hang


class _FakeClient:
    def get_service(self, name):
        return _FakeService()

    def get_type(self, name):
        return _FakeRequest()


class _FakeAdsException(GoogleAdsException):
    """A malformed-query error, without building the real error protos. Caught by the
    same `except GoogleAdsException` branch as the real thing."""

    def __init__(self, message):
        Exception.__init__(self, message)
        self._message = message

    def __str__(self):
        return self._message


def _fake_client_patches():
    return (mock.patch.object(fad, "get_credentials", lambda: None),
            mock.patch.object(fad, "build_client", lambda creds: _FakeClient()))


# ── The account used for the narrative tests (synthetic, not a client) ────────

def _test_findings():
    """A small broken-tracking account: spend, no conversions. Enough to rank issues."""
    data = {
        "client_cid": "999-888-7777",
        "account_summary_30d": {"spend": 1500.0, "clicks": 500, "conversions": 0,
                                "impressions": 27000, "ctr_pct": 1.85, "avg_cpc": 3.0,
                                "cpa": None},
        "campaigns": [
            {"id": "1", "name": "Search - Services", "status": "ENABLED", "type": "SEARCH",
             "bid_strategy": "MAXIMIZE_CONVERSIONS", "daily_budget_gbp": 25.0,
             "spend_30d": 900.0, "clicks_30d": 300, "conversions_30d": 0,
             "impressions_30d": 18000, "target_cpa_gbp": None, "target_roas": None},
            {"id": "2", "name": "Search - Brand", "status": "ENABLED", "type": "SEARCH",
             "bid_strategy": "MAXIMIZE_CONVERSIONS", "daily_budget_gbp": 15.0,
             "spend_30d": 600.0, "clicks_30d": 200, "conversions_30d": 0,
             "impressions_30d": 9000, "target_cpa_gbp": None, "target_roas": None},
        ],
        "campaign_types_active": ["SEARCH"],
        "ad_groups": [{"id": str(100 + i), "name": f"AG {i}", "status": "ENABLED",
                       "campaign_resource": "x", "spend_30d": 100.0} for i in range(6)],
        "conversion_actions": [
            {"name": "generate_lead", "status": "ENABLED", "counting_type": "ONE_PER_CLICK",
             "include_in_conversions": True, "category": "SUBMIT_LEAD_FORM",
             "has_tag_snippet": True, "conversions_30d": 0.0},
        ],
        "keyword_match_breakdown": {"BROAD": {"spend": 700.0, "clicks": 350},
                                    "PHRASE": {"spend": 500.0, "clicks": 100},
                                    "EXACT": {"spend": 300.0, "clicks": 50}},
        "top_search_terms": [], "location_targeting": [], "audience_signals": [],
        "quality_scores": [], "rsa_ad_strength": None, "paused_campaign_history": [],
        "negative_keyword_count": 5, "auto_apply_recommendations": False,
        "auto_apply_types": [],
        "performance_summary": {
            "spend_30d": "£1,500", "impr_30d": "27,000", "clicks_30d": "500",
            "convs_30d": "0", "cvr_30d": "0.0%", "cpa_30d": "N/A", "sis_30d": "40.0%",
            "spend_12m": "£16,000", "impr_12m": "320,000", "clicks_12m": "6,000",
            "convs_12m": "0", "cvr_12m": "0.0%", "cpa_12m": "N/A", "sis_12m": "42.0%",
            "_raw": {"t30": {"spend": 1500.0, "clicks": 500, "conversions": 0,
                             "impressions": 27000, "cpa": None, "cvr": 0.0, "sis": 40.0},
                     "t12": {"spend": 16000.0, "clicks": 6000, "conversions": 0,
                             "impressions": 320000, "cpa": None, "cvr": 0.0, "sis": 42.0}},
        },
    }
    findings = analyse_account(data)
    findings["account_cid"] = data["client_cid"]
    return findings


# A £ figure with pence (style-lint warning) that the engine never produced
# (fact-fidelity warning): one stubbed line, both linters fire.
BAD_NUMBER_LINE = "Spend of £77777.58 was wasted last month."


@contextlib.contextmanager
def _stub_gpt():
    """Replace every GPT call in generate_narrative with fixed copy, so the REAL
    selection, lint and return path run offline."""
    issue = {"title": "Tracking is not recording leads", "whats_happening": "No conversions.",
             "why_it_matters": BAD_NUMBER_LINE, "recommendations": ["Fix the tag."]}
    exec_sum = {"headline": "Needs attention", "bullet_1": "One", "bullet_2": "Two",
                "bullet_3": "Three", "commercial_impact": "Impact.", "score_summary": "Verdict."}
    takeaways = [{"current_state": "a", "changes_needed": "b", "future_state": "c"}]
    stubs = {
        "_narrative_issue": lambda *a, **k: dict(issue),
        "_narrative_executive_summary": lambda *a, **k: dict(exec_sum),
        "_narrative_key_opportunities": lambda *a, **k: "An opportunity.",
        "_narrative_additional_observations": lambda *a, **k: ["An observation."],
        "_narrative_takeaways": lambda *a, **k: list(takeaways),
        "_narrative_perf_commentary": lambda *a, **k: "Commentary.",
        "_sensecheck_terms": lambda *a, **k: "",
    }
    with contextlib.ExitStack() as stack:
        for name, stub in stubs.items():
            stack.enter_context(mock.patch.object(gn, name, stub))
        yield


# ── Fetch-side collectors ─────────────────────────────────────────────────────

class QueryFailureCollection(unittest.TestCase):

    def test_failed_optional_query_is_collected_and_the_audit_still_completes(self):
        def _boom(client, cid):
            raise RuntimeError("ad-assets exploded")

        creds, client = _fake_client_patches()
        with creds, client, mock.patch.object(fad, "get_ad_assets", _boom):
            data = fad.fetch_account_data("123-456-7890")

        failures = data["_query_failures"]
        self.assertEqual([f["fetch"] for f in failures], ["ad-assets"])
        self.assertIn("ad-assets exploded", failures[0]["error"])
        self.assertTrue(any("ad-assets exploded" in w for w in data["_warnings"]))

        # Graceful degradation: the audit finished, and the failed pull is simply absent.
        self.assertIsNone(data["ad_assets"])
        self.assertIn("campaigns", data)
        self.assertIn("performance_summary", data)
        self.assertIn("conversion_actions", data)

    def test_the_collector_is_empty_at_the_start_of_the_next_audit(self):
        def _boom(client, cid):
            raise RuntimeError("ad-assets exploded")

        creds, client = _fake_client_patches()
        with creds, client, mock.patch.object(fad, "get_ad_assets", _boom):
            first = fad.fetch_account_data("123-456-7890")
        self.assertTrue(first["_query_failures"])          # the first audit did fail a query

        creds, client = _fake_client_patches()             # second audit, nothing failing
        with creds, client:
            second = fad.fetch_account_data("222-333-4444")

        self.assertEqual(second["_query_failures"], [])
        self.assertFalse(any("ad-assets exploded" in w for w in second["_warnings"]))
        # ...and the first audit's record is untouched by the second run.
        self.assertTrue(first["_query_failures"])

    def test_run_query_records_the_query_that_failed(self):
        service = _FakeService()
        service.search = mock.Mock(side_effect=_FakeAdsException("INVALID_ARGUMENT: bad field"))
        client = _FakeClient()
        client.get_service = lambda name: service

        fad.reset_collectors()
        rows = fad.run_query(client, "123-456-7890", "SELECT campaign.id FROM nonsense")

        self.assertEqual(rows, [])                         # partial rows, no crash
        self.assertEqual(len(fad._QUERY_FAILURES), 1)
        recorded = fad._QUERY_FAILURES[0]
        self.assertIn("bad field", recorded["error"])
        self.assertIn("FROM nonsense", recorded["query"])

    def test_an_access_error_still_stops_the_audit(self):
        """The guardrail in run_query: no data is not the same as a clean account."""
        service = _FakeService()
        service.search = mock.Mock(side_effect=_FakeAdsException("USER_PERMISSION_DENIED"))
        client = _FakeClient()
        client.get_service = lambda name: service

        fad.reset_collectors()
        with self.assertRaises(fad.AccountAccessError):
            fad.run_query(client, "123-456-7890", "SELECT campaign.id FROM campaign")


# ── Narrative-side collectors ─────────────────────────────────────────────────

class NarrativeWarnings(unittest.TestCase):

    def test_style_lint_and_fact_fidelity_warnings_are_retained(self):
        findings = _test_findings()
        narr = gn._lint_narrative(
            {"executive_summary": {"commercial_impact": BAD_NUMBER_LINE},
             "performance_summary": findings.get("performance_summary", {})},
            "", findings=findings)

        kinds = {w["type"] for w in narr["_warnings"]}
        self.assertIn("style_lint", kinds)                 # pence in a £ figure
        self.assertIn("fact_fidelity", kinds)              # a number the engine never produced

        # The warnings are the raw record: the copy rules must not rewrite them.
        fact = next(w for w in narr["_warnings"] if w["type"] == "fact_fidelity")
        self.assertEqual(fact["sentence"], BAD_NUMBER_LINE)

    def test_a_clean_narrative_carries_an_empty_warning_list(self):
        findings = _test_findings()
        narr = gn._lint_narrative({"executive_summary": {"commercial_impact": "All clear."}},
                                  "", findings=findings)
        self.assertEqual(narr["_warnings"], [])

    def test_generate_narrative_keeps_the_warnings_and_the_selected_issues(self):
        findings = _test_findings()
        expected = select_top_issues(findings, max_issues=8)
        self.assertTrue(expected, "the test account must produce at least one issue")

        with _stub_gpt():
            narr = gn.generate_narrative(findings, "sk-test-not-used", client_name="Test Co")

        # The findings that made the deck, kept with their severity and RAG.
        selected = narr["_selected_issues"]
        self.assertTrue(selected)
        self.assertEqual([i["detail"] for i in selected],
                         [i["detail"] for i in expected][:len(selected)])
        self.assertTrue(all("severity" in i and "rag" in i for i in selected))

        # The stubbed copy carries a bad number, so both linters must have flagged it.
        kinds = {w["type"] for w in narr["_warnings"]}
        self.assertIn("style_lint", kinds)
        self.assertIn("fact_fidelity", kinds)

        # The whole narrative still round-trips to narrative_output.json.
        json.loads(json.dumps(narr))

    def test_the_new_keys_do_not_touch_the_deck_fields(self):
        """populate_slides fills slides from named keys only. The evidence keys are
        additions, not changes: every deck-facing key must be exactly as before."""
        findings = _test_findings()
        with _stub_gpt():
            narr = gn.generate_narrative(findings, "sk-test-not-used", client_name="Test Co")

        deck_keys = {
            "client_name", "account_cid", "overall_rag", "section_rags", "_tokens_used",
            "issues", "additional_observations", "executive_summary", "objectives",
            "key_opportunities", "takeaways", "performance_summary", "perf_commentary",
            "account_type", "revenue_artifact", "table", "geo_table", "website_url",
        }
        self.assertEqual(set(narr) - deck_keys, {"_warnings", "_selected_issues"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
