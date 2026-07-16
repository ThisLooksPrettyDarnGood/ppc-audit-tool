"""Ticket T5 - a paused account is not a broken account.

Zero recorded conversions never proves broken conversion tracking on its own. These tests
pin the four evidence states the engine now distinguishes, and guard against the two ways
the fix could go wrong: claiming a pause we cannot prove, or suppressing a genuinely proven
problem just because the account is quiet.

  - No meaningful traffic + a proven pause   -> Amber, pause + exact date stated, unverifiable
  - No meaningful traffic + pause NOT proven -> Amber, "no traffic", cause not guessed
  - Traffic present + zero conversions        -> Amber VALIDATION finding, never "broken"
  - Zero conversion actions (query ok)        -> Red config finding (reworded, precise)
  - Zero conversion actions (query FAILED)    -> insufficient evidence, never a conclusion

The engine, not the narration, is under test here (deterministic, no network, no GPT). The
committed paused fixture stands in for Oilfast; the live-traffic / Hampton-shaped cases are
built as small synthetic dicts so no client data is needed.

Run:
    python3 -m unittest test_paused_account
"""
import copy
import json
import os
import unittest

import analyse_account as A
import generate_narrative as G

FIXTURE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "fixtures", "data_paused_smart_lead_gen.json")

# The engine's own deck-selection floor: findings below it never headline.
STRONG_FLOOR = 55.0


def load_fixture():
    with open(FIXTURE_PATH) as fh:
        return json.load(fh)


def analyse_fixture():
    """The paused fixture through the whole engine (fresh copy - analyse_account mutates)."""
    findings = A.analyse_account(copy.deepcopy(load_fixture()))
    top = A.select_top_issues(findings)
    ranked = A.select_top_issues(findings, max_issues=40, apply_floor=False)
    return findings, top, ranked


def score(**overrides):
    """score_conversion_tracking on a minimal, deliberately-benign synthetic account.

    Defaults: live (not paused), meaningful traffic, one enabled+primary lead action.
    Override account_summary_30d / performance_summary / conversion_actions / _query_failures
    to build each evidence state."""
    d = {
        "conversion_actions": [{
            "name": "Lead form", "status": "ENABLED", "category": "SUBMIT_LEAD_FORM",
            "type": "WEBPAGE", "primary_for_goal": True, "include_in_conversions": True,
            "counting_type": "ONE_PER_CLICK", "has_tag_snippet": True,
            "conversions_30d": 0.0, "attributed_conversions_30d": 0.0,
        }],
        "account_summary_30d": {"spend": 500.0, "clicks": 300, "conversions": 0.0,
                                "impressions": 8000},
        "performance_summary": {"is_paused": False, "last_active": None, "days_dark": 0},
        "campaigns": [], "conversion_volume_by_month": {},
    }
    d.update(overrides)
    return A.score_conversion_tracking(d)


def sev_of(detail):
    """The severity the ranking layer assigns this wording (None if filtered out)."""
    meta = A._classify_issue(detail, "Conversion Tracking", "amber")
    return None if meta is None else meta["severity"]


def joined(section):
    return " ".join(section.get("issues", []))


BROKEN_PHRASES = ("Tags may be broken", "firing incorrectly", "broken or missing",
                  "recorded 0 conversions in the last 30", "tracking is not set up")


def says_broken(text):
    return any(p in text for p in BROKEN_PHRASES)


# ══════════════════════════════════════════════════════════════════════════════
# A. Oilfast-shaped paused fixture
# ══════════════════════════════════════════════════════════════════════════════

class A_PausedFixture(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.findings, cls.top, cls.ranked = analyse_fixture()
        cls.ct = cls.findings["conversion_tracking"]

    def test_no_severity_122_and_no_broken_tracking_wording_anywhere(self):
        for i in self.ranked:
            self.assertNotEqual(i["severity"], 122.0)
        for sec in ("conversion_tracking", "account_structure", "targeting_keywords",
                    "bidding_strategy", "efficiency"):
            self.assertFalse(says_broken(joined(self.findings.get(sec, {}))),
                             f"broken-tracking wording leaked into {sec}")

    def test_section_headline_is_uncertain_not_broken(self):
        self.assertEqual(self.ct["headline"], "Conversion tracking cannot currently be verified")
        self.assertTrue(self.ct.get("tracking_unverifiable"))

    def test_pause_stated_with_exact_date_and_unassessable_wording(self):
        blob = joined(self.ct)
        self.assertIn("paused on 4 March 2026", blob)            # exact last-active date
        self.assertIn("cannot be assessed reliably", blob)
        self.assertIn("cannot verify whether conversion tracking", blob)
        self.assertNotIn("healthy", blob.lower())

    def test_overall_status_is_amber_not_red(self):
        self.assertEqual(A.overall_rag_from_issues(self.top), "amber")
        self.assertEqual([i for i in self.ranked if i["rag"] == "red"], [])

    def test_pause_is_a_low_ranked_amber_context_not_a_scored_fault(self):
        """CHECK 4: there is NO numerical account score in this tool. Severity only orders
        findings and selects the headline set; the dial is the worst RAG among the SELECTED
        findings (overall_rag_from_issues). So 'no penalty' means the pause is a low-ranked Amber
        context that neither headlines nor drives the RAG - not a scored performance fault.
        """
        paused = next(i for i in self.ranked if "were paused on 4 March 2026" in i["detail"])
        self.assertEqual(paused["rag"], "amber")                       # never Red
        self.assertLess(paused["severity"], STRONG_FLOOR)              # never a headline
        self.assertNotEqual(self.top[0]["detail"], paused["detail"])   # not the leading issue
        # Direct proof the pause imposes no penalty on status: the dial is identical with and
        # without the pause finding present - the Amber comes from the genuine unassessable
        # findings, not from scoring the pause as a fault.
        without_pause = [i for i in self.top if i["detail"] != paused["detail"]]
        self.assertEqual(A.overall_rag_from_issues(self.top),
                         A.overall_rag_from_issues(without_pause))
        self.assertEqual(A.overall_rag_from_issues(self.top), "amber")

    def test_independently_proven_findings_remain(self):
        blob = joined(self.ct)
        # The dead genuine actions (12-month evidence, window-independent) still fire.
        self.assertIn("appear set up but have recorded nothing", blob)
        self.assertEqual(sev_of(next(i for i in self.ct["issues"]
                                     if "appear set up but have recorded nothing" in i)), 58.0)
        # T4's low-value confirmed-zero still fires (see class F for the full guard).
        self.assertTrue(any("not been learning from it during the period reviewed" in i
                            for i in self.ct["issues"]))

    def test_the_confidently_wrong_lead_setup_headline_no_longer_fires(self):
        """24 lead actions ARE configured (just idle), so "set up lead conversion actions"
        would be confidently wrong on this empty window."""
        self.assertNotIn("lead side of the business is invisible", joined(self.ct))


# ══════════════════════════════════════════════════════════════════════════════
# B. Hampton over-correction guard (is_paused True, but the window has real traffic)
# ══════════════════════════════════════════════════════════════════════════════

class B_OverCorrectionGuard(unittest.TestCase):
    """Hampton is flagged is_paused (14 days dark) yet its today-anchored window still caught
    real spend, clicks and conversions. T5 must NOT treat it as a zero-traffic window."""

    def _hampton_shaped(self):
        return score(
            performance_summary={"is_paused": True, "last_active": "2026-06-03", "days_dark": 14},
            account_summary_30d={"spend": 343.14, "clicks": 579, "conversions": 6.0,
                                 "impressions": 9812},
        )

    def test_is_paused_alone_does_not_trigger_the_paused_window_finding(self):
        r = self._hampton_shaped()
        self.assertFalse(r.get("tracking_unverifiable"))
        self.assertNotIn("PPC performance cannot be assessed reliably", joined(r))
        self.assertNotIn("recorded no conversions during the review period", joined(r))

    def test_no_broken_or_paused_claim_and_headline_untouched(self):
        r = self._hampton_shaped()
        self.assertFalse(says_broken(joined(r)))
        self.assertNotEqual(r["headline"], "Conversion tracking cannot currently be verified")


# ══════════════════════════════════════════════════════════════════════════════
# C. Active account with meaningful traffic and zero conversions -> Amber validation
# ══════════════════════════════════════════════════════════════════════════════

class C_TrafficZeroConversions(unittest.TestCase):

    def setUp(self):
        self.r = score(account_summary_30d={"spend": 500.0, "clicks": 300,
                                             "conversions": 0.0, "impressions": 8000})

    def test_amber_validation_finding_not_a_broken_conclusion(self):
        self.assertIn("recorded no conversions during the review period", joined(self.r))
        self.assertFalse(says_broken(joined(self.r)))
        self.assertNotEqual(self.r["rag"], "red")
        self.assertEqual(sev_of("The account generated ad traffic but recorded no conversions "
                                "during the review period."), 62.0)

    def test_wording_distinguishes_performance_from_tracking(self):
        blob = joined(self.r)
        self.assertIn("checking both campaign performance and the conversion", blob)
        self.assertIn("does not prove that tracking is broken", blob)

    def test_headline_is_uncertain(self):
        self.assertEqual(self.r["headline"], "Conversion tracking cannot currently be verified")


# ══════════════════════════════════════════════════════════════════════════════
# D. Very limited traffic (one or two clicks) - never a confident broken conclusion
# ══════════════════════════════════════════════════════════════════════════════

class D_LimitedTraffic(unittest.TestCase):

    def test_two_clicks_zero_conversions_stays_non_conclusive(self):
        r = score(account_summary_30d={"spend": 1.20, "clicks": 2, "conversions": 0.0,
                                       "impressions": 40})
        self.assertFalse(says_broken(joined(r)))
        self.assertNotEqual(r["rag"], "red")
        # Honest validation wording, explicitly non-conclusive (rule 4).
        self.assertIn("does not prove that tracking is broken", joined(r))

    def test_one_click_is_no_more_conclusive_than_many(self):
        r1 = score(account_summary_30d={"spend": 0.6, "clicks": 1, "conversions": 0.0,
                                        "impressions": 12})
        rN = score(account_summary_30d={"spend": 900.0, "clicks": 4000, "conversions": 0.0,
                                        "impressions": 60000})
        # Same non-conclusive amber finding at both extremes - no traffic threshold flips it.
        self.assertEqual(sev_of(next(i for i in r1["issues"] if "review period" in i)),
                         sev_of(next(i for i in rN["issues"] if "review period" in i)))
        self.assertNotEqual(r1["rag"], "red")
        self.assertNotEqual(rN["rag"], "red")


# ══════════════════════════════════════════════════════════════════════════════
# E. Paused account with an independently proven problem -> proven issue survives, Red possible
# ══════════════════════════════════════════════════════════════════════════════

class E_ProvenIssueSurvivesPause(unittest.TestCase):

    def test_proven_dead_action_is_not_suppressed_by_the_pause(self):
        findings, _, ranked = analyse_fixture()
        ct = findings["conversion_tracking"]
        dead = next((i for i in ct["issues"]
                     if "appear set up but have recorded nothing" in i), None)
        self.assertIsNotNone(dead, "the 12-month dead-action evidence must still fire")
        self.assertEqual(sev_of(dead), 58.0)                       # still scored
        self.assertIn("were paused on 4 March 2026", joined(ct))   # pause context also present

    def test_red_remains_possible_on_a_paused_account_with_a_config_problem(self):
        """A paused account with an independent, proven config fault (no actions configured,
        query succeeded) can still be Red - the pause handling suppresses nothing."""
        r = score(conversion_actions=[],
                  performance_summary={"is_paused": True, "last_active": "2026-03-04",
                                       "days_dark": 132},
                  account_summary_30d={"spend": 0.0, "clicks": 0, "conversions": 0.0,
                                       "impressions": 0})
        self.assertEqual(r["rag"], "red")
        self.assertIn("No conversion actions are configured", joined(r))


# ══════════════════════════════════════════════════════════════════════════════
# F. Paused account with T4 low-value primaries -> T4 output unchanged by the pause
# ══════════════════════════════════════════════════════════════════════════════

class F_T4Unchanged(unittest.TestCase):

    def test_t4_confirmed_zero_finding_still_present_and_scored(self):
        findings, top, ranked = analyse_fixture()
        low = next((i for i in ranked
                    if "not been learning from it during the period reviewed" in i["detail"]), None)
        self.assertIsNotNone(low, "T4 low-value confirmed-zero must still fire")
        self.assertEqual(low["severity"], 35.0)
        self.assertEqual(low["rag"], "amber")
        # Unchanged from its T4 characterisation: an observation, never the paused headline.
        self.assertNotIn(low["detail"], [i["detail"] for i in top])
        self.assertNotIn("Bidding will be learning from the wrong actions", low["detail"])


# ══════════════════════════════════════════════════════════════════════════════
# G. Enabled campaigns with zero delivery, pause NOT proven -> no pause claim, no broken claim
# ══════════════════════════════════════════════════════════════════════════════

class G_NoTrafficPauseNotProven(unittest.TestCase):

    def setUp(self):
        # No traffic at all, but the pause-detection signal did not flag a pause.
        self.r = score(account_summary_30d={"spend": 0.0, "clicks": 0, "conversions": 0.0,
                                            "impressions": 0},
                       performance_summary={"is_paused": False, "last_active": None})

    def test_states_no_traffic_without_claiming_a_pause(self):
        blob = joined(self.r)
        self.assertIn("no meaningful ad traffic", blob)
        self.assertNotIn("paused", blob.lower())

    def test_does_not_guess_the_cause_and_is_amber_not_broken(self):
        blob = joined(self.r)
        self.assertFalse(says_broken(blob))
        self.assertNotEqual(self.r["rag"], "red")
        self.assertEqual(self.r["headline"], "Conversion tracking cannot currently be verified")
        for cause in ("budget", "billing", "policy", "eligib"):
            self.assertNotIn(cause, blob.lower(), f"must not guess the cause ({cause})")


# ══════════════════════════════════════════════════════════════════════════════
# H. Mixed active + paused campaigns -> not treated as fully paused
# ══════════════════════════════════════════════════════════════════════════════

class H_MixedCampaigns(unittest.TestCase):

    def test_account_with_some_live_spend_is_not_called_fully_paused(self):
        # Some campaigns live (real spend/clicks/conversions), some paused: is_paused is False
        # because there was spend in the window. The whole account must not read as paused.
        r = score(
            performance_summary={"is_paused": False, "last_active": "2026-07-14", "days_dark": 1},
            account_summary_30d={"spend": 220.0, "clicks": 140, "conversions": 4.0,
                                 "impressions": 5200},
            campaigns=[{"name": "Live", "status": "ENABLED", "type": "SEARCH",
                        "spend_30d": 220.0, "clicks_30d": 140, "conversions_30d": 4.0},
                       {"name": "Off", "status": "PAUSED", "type": "SEARCH",
                        "spend_30d": 0.0, "clicks_30d": 0, "conversions_30d": 0.0}],
        )
        self.assertFalse(r.get("tracking_unverifiable"))
        self.assertNotIn("PPC performance cannot be assessed reliably", joined(r))
        self.assertFalse(says_broken(joined(r)))


# ══════════════════════════════════════════════════════════════════════════════
# I. Conversion-action query FAILURE -> insufficient evidence, never a conclusion
# ══════════════════════════════════════════════════════════════════════════════

class I_QueryFailure(unittest.TestCase):

    def setUp(self):
        self.r = score(conversion_actions=[],
                       _query_failures=[{"fetch": "conversion-actions",
                                         "error": "DeadlineExceeded", "query": None}])

    def test_no_no_actions_configured_conclusion(self):
        self.assertNotIn("No conversion actions are configured", joined(self.r))
        self.assertNotEqual(self.r["rag"], "red")

    def test_honest_insufficient_evidence_wording_and_no_broken_claim(self):
        blob = joined(self.r)
        self.assertIn("could not be retrieved in this audit", blob)
        self.assertFalse(says_broken(blob))
        self.assertTrue(self.r.get("tracking_unverifiable"))


# ══════════════════════════════════════════════════════════════════════════════
# J. Confirmed successful query returning zero conversion actions -> precise Red config finding
# ══════════════════════════════════════════════════════════════════════════════

class J_GenuinelyZeroActions(unittest.TestCase):

    def test_precise_red_configuration_finding_remains(self):
        r = score(conversion_actions=[])          # no recorded query failure -> genuine zero
        self.assertEqual(r["rag"], "red")
        self.assertIn("No conversion actions are configured in Google Ads", joined(r))
        self.assertEqual(sev_of("No conversion actions are configured in Google Ads, so the "
                                "account cannot report or optimise towards conversions."), 130.0)

    def test_reworded_away_from_the_old_vague_wording(self):
        r = score(conversion_actions=[])
        self.assertNotIn("tracking is not set up", joined(r))     # old wording gone


# ══════════════════════════════════════════════════════════════════════════════
# CHECK 3 (state C) - the conversion_actions field is ABSENT / unconfirmed
# ══════════════════════════════════════════════════════════════════════════════

class K_ConversionActionFieldMissing(unittest.TestCase):
    """State C: the field is absent (result never captured). Missing evidence must not be read
    as a successful zero result - the absence of a `_query_failures` entry is not enough."""

    def test_absent_field_does_not_trigger_the_red_config_finding(self):
        d = {"account_summary_30d": {"spend": 0.0, "clicks": 0, "conversions": 0.0,
                                     "impressions": 0},
             "performance_summary": {"is_paused": False, "last_active": None},
             "campaigns": [], "conversion_volume_by_month": {}}
        self.assertNotIn("conversion_actions", d)                 # field genuinely absent
        r = A.score_conversion_tracking(d)
        self.assertNotIn("No conversion actions are configured", joined(r))
        self.assertNotEqual(r["rag"], "red")
        self.assertIn("could not be retrieved", joined(r))        # honest insufficient wording
        self.assertTrue(r.get("tracking_unverifiable"))

    def test_present_empty_list_with_no_failure_is_the_only_red_state(self):
        # State A: field present + empty + no failure -> the precise Red config finding.
        r = A.score_conversion_tracking({
            "conversion_actions": [],
            "account_summary_30d": {"spend": 0.0, "clicks": 0, "conversions": 0.0, "impressions": 0},
            "performance_summary": {"is_paused": False}, "campaigns": [],
            "conversion_volume_by_month": {}})
        self.assertEqual(r["rag"], "red")
        self.assertIn("No conversion actions are configured", joined(r))


# ══════════════════════════════════════════════════════════════════════════════
# CHECK 2 - an image-extension recommendation can never headline
# ══════════════════════════════════════════════════════════════════════════════

class L_ImageExtensionsCannotHeadline(unittest.TestCase):

    IMG = ("Image extensions are a minor extra worth adding when you have time: a few images on "
           "your Search ads can lift click-through a little, at no extra cost per click.")
    HIGH_VALUE = ("Your ads are missing high-value extension types: sitelinks. Extensions make "
                  "ads bigger and more clickable and feed Ad Rank.")

    def test_image_needle_sits_below_the_headline_floor(self):
        self.assertEqual(sev_of(self.IMG), 30.0)
        self.assertLess(sev_of(self.IMG), STRONG_FLOOR)          # can never be a headline

    def test_image_is_a_separate_needle_from_the_high_value_60_finding(self):
        self.assertEqual(sev_of(self.HIGH_VALUE), 60.0)          # real high-value gaps stay 60
        self.assertNotEqual(sev_of(self.IMG), 60.0)              # image is never 60

    def test_on_the_paused_fixture_image_is_a_minor_observation_not_the_leader(self):
        findings, top, ranked = analyse_fixture()
        img = next((i for i in ranked if "Image extensions are a minor extra" in i["detail"]), None)
        self.assertIsNotNone(img, "image extensions remain a minor recommendation")
        self.assertEqual(img["severity"], 30.0)
        self.assertNotIn(img["detail"], [i["detail"] for i in top])          # not narrated as a headline
        self.assertNotIn("Image extensions", top[0]["detail"])               # did not become the leader
        # No severity-60 high-value extension headline survives on this account.
        self.assertFalse(any("missing high-value extension types" in i["detail"] for i in top))


# ══════════════════════════════════════════════════════════════════════════════
# CHECK 1 - the executive overview LEADS with the pause context (deterministic directive)
# ══════════════════════════════════════════════════════════════════════════════

class M_OverviewLeadsWithPauseContext(unittest.TestCase):
    """The rendered exec-summary copy is GPT-generated (not deterministically testable), but the
    directive that forces it to lead with the pause IS deterministic - test that."""

    def test_paused_fixture_directive_leads_with_pause_before_performance(self):
        findings, _, _ = analyse_fixture()
        lead = G._overview_lead_context(findings)
        self.assertTrue(lead, "a paused account must produce a leading pause directive")
        for token in ("PAUSED", "4 March 2026", "cannot be assessed reliably",
                      "cannot be verified", "EXEC_HEADLINE", "COMMERCIAL_IMPACT", "BEFORE"):
            self.assertIn(token, lead)
        # It explicitly forbids the misleading 'solid foundations' amber framing on a paused account.
        self.assertIn("solid foundations", lead)     # named only to forbid it
        self.assertIn("Do NOT describe the account as having 'solid foundations'", lead)

    def test_no_traffic_unproven_directive_states_no_traffic_and_not_a_pause(self):
        findings = {"conversion_tracking": {"tracking_unverifiable": True},
                    "performance_summary": {"is_paused": False, "last_active": None}}
        lead = G._overview_lead_context(findings)
        self.assertIn("NO MEANINGFUL AD TRAFFIC", lead)
        self.assertIn("cannot be assessed reliably", lead)
        self.assertNotIn("PAUSED", lead)             # must not claim a pause
        self.assertIn("Do NOT guess", lead)          # the cause must not be guessed

    def test_assessable_window_produces_no_lead_directive(self):
        self.assertEqual(G._overview_lead_context(
            {"conversion_tracking": {"tracking_unverifiable": False}}), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
