"""Focused regression tests for the low-value PRIMARY conversion classifier (ticket T4).

The classifier grades a low-value-category PRIMARY conversion action by the strength of the
evidence, and never judges "low value" from an action's free-text name:

  Classification 1 (proven firing)  -> severity 84, amber_red, HEADLINE, affects the score,
                                        and states plainly that bidding will be learning from
                                        the wrong actions.
  Classification 2 (confirmed zero) -> severity 35, amber, minor tidy-up (Additional
                                        Observations); never claims bidding learned from it.
  Classification 3 (unconfirmed)    -> a client-facing note, NO score impact, saying what to
                                        check. No auditor-review or escalation list.

These tests exercise the pure classifier, the signature/severity mapping the deck relies on,
and the section-level score impact through score_conversion_tracking. They use hand-built,
fully-fictional conversion actions plus the sanitised Oilfast-shaped fixture from ticket T3.

Run:
    python3 -m unittest test_low_value_primary
"""
import copy
import json
import os
import unittest

import analyse_account as A
from analyse_account import (
    classify_low_value_primary_conversions as classify,
    select_top_issues,
    _classify_issue,
)

STRONG_FLOOR = 55   # select_top_issues: findings at/above this can headline; below -> observations
FIXTURE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "fixtures", "data_paused_smart_lead_gen.json")

# The exact sentence the client-facing wording must carry for a proven-firing finding.
REQUIRED_PROVEN_WORDING = "Bidding will be learning from the wrong actions."


# ── Fictional conversion-action builders ─────────────────────────────────────
def action(name, category, *, status="ENABLED", primary=True, included=True,
           attributed=None, all_conv=None):
    """A conversion action shaped exactly like fetch_account_data emits. `attributed` /
    `all_conv` left as None models the per-action volume query being unavailable."""
    ca = {"name": name, "category": category, "status": status,
          "primary_for_goal": primary, "include_in_conversions": included,
          "counting_type": "ONE_PER_CLICK", "type": "WEBPAGE", "has_tag_snippet": True}
    if attributed is not None:
        ca["attributed_conversions_30d"] = float(attributed)
    if all_conv is not None:
        ca["conversions_30d"] = float(all_conv)
    return ca


def data_dict(conversion_actions, conversions=10.0, clicks=800):
    """Minimal data dict that score_conversion_tracking accepts. conversions>0 keeps the
    account off the account-zero RED branch so the low-value block's own RAG is observable."""
    return {
        "conversion_actions": conversion_actions,
        "account_summary_30d": {"conversions": conversions, "clicks": clicks},
        "campaigns": [],
    }


def find_ranked(findings, needle):
    for r in select_top_issues(findings, max_issues=50, apply_floor=False):
        if needle in r["detail"]:
            return r
    return None


# ══════════════════════════════════════════════════════════════════════════════
# Case 1: enabled, primary, clearly micro-action, and FIRED
# ══════════════════════════════════════════════════════════════════════════════
class ProvenFiring(unittest.TestCase):

    def setUp(self):
        self.res = classify([
            action("Homepage page view", "PAGE_VIEW", attributed=14, all_conv=60),
            action("Scroll depth", "ENGAGEMENT", attributed=6, all_conv=20),
            action("Enquiry form", "SUBMIT_LEAD_FORM", attributed=4, all_conv=4),  # genuine, ignored
        ])

    def test_severity_is_84_amber_red_headline(self):
        p = self.res["proven"]
        self.assertIsNotNone(p)
        self.assertEqual(p["severity"], 84.0)
        self.assertEqual(p["rag"], "amber_red")
        self.assertTrue(p["headline"])

    def test_signature_maps_the_wording_to_84_amber_red(self):
        meta = _classify_issue(self.res["proven"]["detail"], "Conversion Tracking", "amber")
        self.assertEqual(meta["severity"], 84.0)
        self.assertEqual(meta["rag"], "amber_red")

    def test_states_bidding_will_be_learning_from_the_wrong_actions(self):
        detail = self.res["proven"]["detail"]
        self.assertIn(REQUIRED_PROVEN_WORDING, detail)
        # Never weakened to uncertainty.
        self.assertNotIn("may be learning", detail.lower())
        self.assertNotIn("might be learning", detail.lower())

    def test_explains_the_required_points(self):
        detail = self.res["proven"]["detail"]
        self.assertIn("Homepage page view", detail)          # which action
        self.assertIn("page views", detail)                  # why low value
        self.assertIn("included in bidding", detail)          # that it is in bidding
        self.assertIn("ad-attributed", detail)                # evidence it fired
        self.assertIn("inflated", detail)                     # why it distorts reporting

    def test_it_affects_the_audit_score(self):
        # Score impact = the section RAG is pushed to amber_red and inflation is flagged.
        res = A.score_conversion_tracking(data_dict([
            action("Homepage page view", "PAGE_VIEW", attributed=14, all_conv=60),
        ]))
        self.assertEqual(res["rag"], "amber_red")
        self.assertTrue(res["conversions_inflated"])

    def test_it_reaches_the_headline_findings(self):
        findings = {"conversion_tracking": A.score_conversion_tracking(data_dict([
            action("Homepage page view", "PAGE_VIEW", attributed=14, all_conv=60),
        ]))}
        top = select_top_issues(findings, max_issues=6)
        self.assertTrue(any(REQUIRED_PROVEN_WORDING in i["detail"] for i in top))


# ══════════════════════════════════════════════════════════════════════════════
# Case 2: enabled, primary, clearly micro-action, and ZERO conversions
# ══════════════════════════════════════════════════════════════════════════════
class ConfirmedZero(unittest.TestCase):

    def test_severity_is_35_amber_minor(self):
        res = classify([action("Page view", "PAGE_VIEW", attributed=0, all_conv=0)])
        z = res["zero"]
        self.assertIsNotNone(z)
        self.assertEqual(z["severity"], 35.0)
        self.assertEqual(z["rag"], "amber")
        self.assertFalse(z["headline"])
        # Below the deck floor -> Additional Observations, not a headline slide.
        self.assertLess(z["severity"], STRONG_FLOOR)

    def test_signature_maps_the_wording_to_35_amber(self):
        res = classify([action("Page view", "PAGE_VIEW", attributed=0, all_conv=0)])
        meta = _classify_issue(res["zero"]["detail"], "Conversion Tracking", "amber")
        self.assertEqual(meta["severity"], 35.0)
        self.assertEqual(meta["rag"], "amber")

    def test_does_not_claim_bidding_learned_from_it(self):
        for all_conv in (0, 40):   # fully silent, and firing-as-site-event variants
            res = classify([action("Page view", "PAGE_VIEW", attributed=0, all_conv=all_conv)])
            detail = res["zero"]["detail"]
            self.assertIn("has not been learning from it during the period reviewed", detail)
            self.assertNotIn(REQUIRED_PROVEN_WORDING, detail)
            self.assertNotIn("inflating", detail.lower())

    def test_is_not_described_as_broken(self):
        res = classify([action("Page view", "PAGE_VIEW", attributed=0, all_conv=0)])
        self.assertNotIn("broken", res["zero"]["detail"].lower().replace("not a sign anything is broken", ""))

    def test_no_inflation_flag(self):
        res = A.score_conversion_tracking(data_dict([
            action("Page view", "PAGE_VIEW", attributed=0, all_conv=0)]))
        self.assertFalse(res["conversions_inflated"])


# ══════════════════════════════════════════════════════════════════════════════
# Case 3: ambiguous conversion NAME, no evidence confirming its purpose
# ══════════════════════════════════════════════════════════════════════════════
class AmbiguousNameNotJudgedFromName(unittest.TestCase):

    def test_lowvalue_sounding_name_but_genuine_category_is_not_flagged(self):
        # Name screams "click"/"view"/"form" but the CATEGORY is a genuine outcome.
        res = classify([
            action("Form submit button click view", "SUBMIT_LEAD_FORM", attributed=9, all_conv=9),
            action("Call now click", "PHONE_CALL_LEAD", attributed=3, all_conv=3),
        ])
        self.assertIsNone(res["proven"])
        self.assertIsNone(res["zero"])
        self.assertEqual(res["informational"], [])

    def test_unknown_category_is_not_proven_from_the_name(self):
        # An ambiguous name with a non-micro category is never a PROVEN low-value primary.
        res = classify([action("scroll_view_engagement", "DEFAULT", attributed=20, all_conv=50)])
        self.assertIsNone(res["proven"])


# ══════════════════════════════════════════════════════════════════════════════
# Case 4: missing / conflicting enabled, primary or recent-activity evidence
# ══════════════════════════════════════════════════════════════════════════════
class UnconfirmedIsInformationalOnly(unittest.TestCase):

    def test_volume_unavailable_is_informational_no_score(self):
        # Enabled + primary + micro-action, but the per-action volume query was unavailable
        # (no attributed/all_conv keys) -> cannot confirm firing.
        res = classify([action("Page view", "PAGE_VIEW")])   # no volume keys
        self.assertIsNone(res["proven"])
        self.assertIsNone(res["zero"])
        self.assertEqual(len(res["informational"]), 1)

    def test_unknown_status_is_informational(self):
        res = classify([action("Page view", "PAGE_VIEW", status="", attributed=0, all_conv=0)])
        self.assertEqual(len(res["informational"]), 1)
        self.assertIn("enabled status could not be read", res["informational"][0])

    def test_note_is_client_facing_and_states_what_to_check(self):
        note = classify([action("Page view", "PAGE_VIEW")])["informational"][0]
        self.assertIn("We could not confirm whether", note)
        self.assertIn("should be checked before drawing a conclusion", note)
        self.assertIn("goal settings, tag status and recent conversion activity", note)

    def test_note_carries_no_score(self):
        # The note is not a scored issue: the classifier never scores it, and if handed to the
        # deck's issue classifier (green section) it is filtered out, not ranked.
        note = classify([action("Page view", "PAGE_VIEW")])["informational"][0]
        self.assertIsNone(_classify_issue(note, "Conversion Tracking", "green"))

    def test_no_score_impact_on_the_section(self):
        # Adding an unconfirmed low-value primary changes neither the RAG nor the inflation flag.
        base = [action("Enquiry", "SUBMIT_LEAD_FORM", attributed=6, all_conv=6)]
        without = A.score_conversion_tracking(data_dict(copy.deepcopy(base)))
        with_unconf = A.score_conversion_tracking(
            data_dict(copy.deepcopy(base) + [action("Page view", "PAGE_VIEW")]))
        self.assertEqual(without["rag"], with_unconf["rag"])
        self.assertEqual(without["conversions_inflated"], with_unconf["conversions_inflated"])
        # The note lives on the no-score channel, never in the scored issue list.
        self.assertEqual(len(with_unconf["informational_notes"]), 1)
        self.assertTrue(all("We could not confirm whether" not in i for i in with_unconf["issues"]))

    def test_no_auditor_review_or_escalation_list_is_introduced(self):
        res = A.score_conversion_tracking(data_dict([action("Page view", "PAGE_VIEW")]))
        for banned in ("auditor_review", "auditor_review_list", "escalation",
                       "internal_escalation", "review_list", "manual_review"):
            self.assertNotIn(banned, res)


# ══════════════════════════════════════════════════════════════════════════════
# Case 5: a genuine sale / lead / completed enquiry
# ══════════════════════════════════════════════════════════════════════════════
class GenuineOutcomesAreNeverLowValue(unittest.TestCase):

    def test_genuine_outcomes_are_not_candidates(self):
        for cat in ("PURCHASE", "SUBMIT_LEAD_FORM", "LEAD", "QUALIFIED_LEAD",
                    "CONVERTED_LEAD", "BOOK_APPOINTMENT", "REQUEST_QUOTE", "PHONE_CALL_LEAD"):
            res = classify([action(f"{cat} action", cat, attributed=25, all_conv=30)])
            self.assertIsNone(res["proven"], f"{cat} wrongly classified as proven low-value")
            self.assertIsNone(res["zero"], f"{cat} wrongly classified as zero low-value")
            self.assertEqual(res["informational"], [], f"{cat} wrongly flagged informational")

    def test_app_installs_are_not_low_value(self):
        # DOWNLOAD is ambiguous: an app install / First open is the GENUINE conversion on an
        # App campaign, so it must never be auto-flagged as a low-value primary (would be a
        # confidently-wrong headline telling an app advertiser to keep a "form submission").
        res = classify([
            action("com.acme (Android) installs", "DOWNLOAD", attributed=159, all_conv=159),
            action("acme (iOS) First open", "DOWNLOAD", attributed=18, all_conv=18),
        ])
        self.assertIsNone(res["proven"])
        self.assertIsNone(res["zero"])
        self.assertEqual(res["informational"], [])


# ══════════════════════════════════════════════════════════════════════════════
# Case 6: clearly low-value but SECONDARY / excluded from bidding
# ══════════════════════════════════════════════════════════════════════════════
class SecondaryLowValueIsNotAPrimaryIssue(unittest.TestCase):

    def test_secondary_excluded_low_value_is_not_flagged(self):
        res = classify([action("Page view", "PAGE_VIEW", primary=False, included=False,
                               attributed=200, all_conv=500)])
        self.assertIsNone(res["proven"])
        self.assertIsNone(res["zero"])
        self.assertEqual(res["informational"], [])

    def test_disabled_low_value_is_not_flagged(self):
        for status in ("PAUSED", "HIDDEN", "REMOVED"):
            res = classify([action("Page view", "PAGE_VIEW", status=status,
                                   attributed=50, all_conv=90)])
            self.assertIsNone(res["proven"])
            self.assertIsNone(res["zero"])
            self.assertEqual(res["informational"], [])


# ══════════════════════════════════════════════════════════════════════════════
# Case 7: multiple actions with MIXED evidence
# ══════════════════════════════════════════════════════════════════════════════
class MixedEvidenceStaysSeparated(unittest.TestCase):

    def setUp(self):
        self.res = classify([
            action("Homepage view", "PAGE_VIEW", attributed=11, all_conv=40),    # proven
            action("Scroll 75", "ENGAGEMENT", attributed=5, all_conv=18),        # proven
            action("Outbound click", "OUTBOUND_CLICK", attributed=0, all_conv=0),  # zero
            action("Menu tap", "OUTBOUND_CLICK"),                                # unconfirmed (no volume)
            action("Enquiry form", "SUBMIT_LEAD_FORM", attributed=7, all_conv=7),  # genuine, excluded
            action("Old page view", "PAGE_VIEW", primary=False, included=False,
                   attributed=90, all_conv=90),                                  # secondary, excluded
        ])

    def test_proven_group_scores_84_and_names_only_firing_actions(self):
        p = self.res["proven"]
        self.assertEqual(p["severity"], 84.0)
        self.assertIn("Homepage view", p["detail"])
        self.assertIn("Scroll 75", p["detail"])
        self.assertNotIn("Outbound click", p["detail"])   # zero action not folded in
        self.assertNotIn("Downloads", p["detail"])        # unconfirmed action not folded in

    def test_zero_group_stays_minor_at_35(self):
        z = self.res["zero"]
        self.assertEqual(z["severity"], 35.0)
        self.assertIn("Outbound click", z["detail"])

    def test_unconfirmed_group_stays_informational(self):
        self.assertEqual(len(self.res["informational"]), 1)
        self.assertIn("Menu tap", self.res["informational"][0])

    def test_no_escalation_from_uncertain_or_excluded_actions(self):
        # The uncertain / excluded actions never lift the proven or zero severities.
        self.assertEqual(self.res["proven"]["severity"], 84.0)
        self.assertEqual(self.res["zero"]["severity"], 35.0)

    def test_findings_are_separate_with_no_duplicate_scoring(self):
        # Proven (84 headline) and zero (35 observation) are SEPARATE findings about different
        # actions. Each scores exactly once (disjoint groups = no duplicate scoring), and the
        # zero is never dropped just because a proven finding also fired.
        findings = {"conversion_tracking": {
            "rag": "amber_red",
            "issues": [self.res["proven"]["detail"], self.res["zero"]["detail"]],
        }}
        ranked = select_top_issues(findings, max_issues=50, apply_floor=False)
        sevs = sorted(r["severity"] for r in ranked)
        self.assertEqual(sevs, [35.0, 84.0], "both findings survive, each scored once")
        # The two findings name disjoint action sets - no action is scored in both.
        self.assertIn("Homepage view", self.res["proven"]["detail"])
        self.assertNotIn("Homepage view", self.res["zero"]["detail"])
        self.assertIn("Outbound click", self.res["zero"]["detail"])
        self.assertNotIn("Outbound click", self.res["proven"]["detail"])


# ══════════════════════════════════════════════════════════════════════════════
# Category coverage: every unambiguous micro-action category follows the same ladder.
# ══════════════════════════════════════════════════════════════════════════════
class EveryMicroActionCategoryFollowsTheLadder(unittest.TestCase):

    # Unambiguous micro-action categories (Google's own category enum): a funnel step or a
    # signal, never itself a sale, completed enquiry or qualified lead.
    CATS = ["PAGE_VIEW", "ENGAGEMENT", "OUTBOUND_CLICK",
            "ADD_TO_CART", "BEGIN_CHECKOUT", "GET_DIRECTIONS"]

    def test_the_agreed_categories_are_all_supported(self):
        for cat in self.CATS:
            self.assertIn(cat, A._LOW_VALUE_CATEGORIES)

    def test_fired_is_84_amber_red_headline(self):
        for cat in self.CATS:
            res = classify([action(f"{cat} action", cat, attributed=20, all_conv=40)])
            p = res["proven"]
            self.assertIsNotNone(p, f"{cat} firing should be proven")
            self.assertEqual(p["severity"], 84.0, cat)
            self.assertEqual(p["rag"], "amber_red", cat)
            self.assertTrue(p["headline"], cat)
            self.assertIn(REQUIRED_PROVEN_WORDING, p["detail"])
            self.assertEqual(_classify_issue(p["detail"], "Conversion Tracking", "amber")["severity"],
                             84.0, cat)

    def test_confirmed_zero_is_35_and_below_the_deck_floor(self):
        for cat in self.CATS:
            res = classify([action(f"{cat} action", cat, attributed=0, all_conv=0)])
            z = res["zero"]
            self.assertIsNotNone(z, f"{cat} zero should classify")
            self.assertEqual(z["severity"], 35.0, cat)
            self.assertLess(z["severity"], STRONG_FLOOR, cat)
            self.assertEqual(_classify_issue(z["detail"], "Conversion Tracking", "amber")["severity"],
                             35.0, cat)

    def test_missing_evidence_is_informational_only_no_score(self):
        for cat in self.CATS:
            res = classify([action(f"{cat} action", cat)])   # no volume keys
            self.assertIsNone(res["proven"], cat)
            self.assertIsNone(res["zero"], cat)
            self.assertEqual(len(res["informational"]), 1, cat)
            self.assertIsNone(_classify_issue(res["informational"][0], "Conversion Tracking", "green"),
                              cat)


# ══════════════════════════════════════════════════════════════════════════════
# Purchase-silent must NOT hide a separately proven firing low-value primary.
# ══════════════════════════════════════════════════════════════════════════════
class PurchaseSilentDoesNotHideProven(unittest.TestCase):

    def _ecom_purchase_silent(self, low_value):
        """A minimal ecommerce account whose PURCHASE tag is silent all year while a
        low-value primary holds the volume - the Beatles Story shape. Triggers the
        _purchase_silent guard inside score_conversion_tracking."""
        return {
            "conversion_actions": [
                action("Web purchase", "PURCHASE", attributed=0, all_conv=0),   # silent tag
                low_value,
            ],
            "account_summary_30d": {"conversions": 1200, "clicks": 6000},
            "campaigns": [],
            "conversion_volume_by_month": {"Web purchase": {}},   # non-empty; purchase history nil
        }

    def test_guard_is_actually_active(self):
        res = A.score_conversion_tracking(self._ecom_purchase_silent(
            action("Checked prices", "PAGE_VIEW", attributed=1200, all_conv=1400)))
        self.assertTrue(res["revenue_artifact"], "the purchase-silent guard must be engaged")

    def test_proven_pageview_still_scores_84_headline_under_purchase_silent(self):
        res = A.score_conversion_tracking(self._ecom_purchase_silent(
            action("Checked prices", "PAGE_VIEW", attributed=1200, all_conv=1400)))
        proven = [i for i in res["issues"] if REQUIRED_PROVEN_WORDING in i]
        self.assertEqual(len(proven), 1, "the proven firing finding must not be suppressed")
        self.assertTrue(res["conversions_inflated"])
        top = select_top_issues({"conversion_tracking": res}, max_issues=8)
        hit = next((i for i in top if REQUIRED_PROVEN_WORDING in i["detail"]), None)
        self.assertIsNotNone(hit, "the proven finding must reach the headline set")
        self.assertEqual(hit["severity"], 84.0)
        self.assertEqual(hit["rag"], "amber_red")

    def test_proven_add_to_cart_still_scores_84_under_purchase_silent(self):
        res = A.score_conversion_tracking(self._ecom_purchase_silent(
            action("Add to basket", "ADD_TO_CART", attributed=300, all_conv=350)))
        self.assertTrue(res["revenue_artifact"])
        self.assertTrue(any(REQUIRED_PROVEN_WORDING in i for i in res["issues"]))

    def test_confirmed_zero_still_scores_35_observation_under_purchase_silent(self):
        # A confirmed-zero low-value primary is ESTABLISHED evidence, not an unsupported
        # conclusion, so the purchase-silent state must NOT suppress it.
        res = A.score_conversion_tracking(self._ecom_purchase_silent(
            action("Product view", "PAGE_VIEW", attributed=0, all_conv=0)))
        self.assertTrue(res["revenue_artifact"], "purchase-silent guard must be engaged")
        zero = [i for i in res["issues"] if "not been learning from it during the period reviewed" in i]
        self.assertEqual(len(zero), 1, "confirmed-zero must still be emitted under purchase-silent")
        top = select_top_issues({"conversion_tracking": res}, max_issues=50, apply_floor=False)
        hit = next(r for r in top if "not been learning from it" in r["detail"])
        self.assertEqual(hit["severity"], 35.0)
        self.assertLess(hit["severity"], STRONG_FLOOR)   # Additional Observations, not a headline

    def test_unconfirmed_still_surfaces_as_informational_under_purchase_silent(self):
        # Classification 3 is NEVER silently omitted - even under purchase-silent the tool has
        # found relevant but incomplete evidence, and must say so as a client-facing note.
        # Baseline: the same purchase-silent account with NO low-value candidate at all.
        without = A.score_conversion_tracking({
            "conversion_actions": [action("Web purchase", "PURCHASE", attributed=0, all_conv=0)],
            "account_summary_30d": {"conversions": 1200, "clicks": 6000},
            "campaigns": [],
            "conversion_volume_by_month": {"Web purchase": {}},
        })
        res = A.score_conversion_tracking(self._ecom_purchase_silent(action("Some view", "PAGE_VIEW")))
        self.assertTrue(res["revenue_artifact"], "purchase-silent guard must be engaged")
        self.assertTrue(without["revenue_artifact"])

        notes = res["informational_notes"]
        self.assertEqual(len(notes), 1, "the uncertainty must not be silently omitted")
        note = notes[0]
        # No score impact: it is not a scored issue, and green-section classification filters it.
        self.assertFalse(any("Some view" in i for i in res["issues"]))
        self.assertIsNone(_classify_issue(note, "Conversion Tracking", "green"))
        # No RAG impact: adding the unconfirmed action does not change the section RAG.
        self.assertEqual(res["rag"], without["rag"])
        # Not promoted to a headline (it never enters the ranked issue list at all).
        top = select_top_issues({"conversion_tracking": res}, max_issues=50, apply_floor=False)
        self.assertFalse(any("Some view" in r["detail"] for r in top))
        # States what to check; draws no conclusion; states no score impact.
        self.assertIn("should be checked before drawing a conclusion", note)
        self.assertIn("has not counted for or against the account", note)
        # Does not claim the action is broken, inactive, low-value or influencing bidding.
        self.assertNotIn("broken", note.lower())
        self.assertNotIn("inactive", note.lower())
        self.assertNotIn("inflat", note.lower())
        self.assertNotIn("is a low-value", note.lower())
        # No auditor-review or escalation item is created anywhere in the result.
        for banned in ("auditor_review", "auditor_review_list", "escalation",
                       "internal_escalation", "review_list", "manual_review"):
            self.assertNotIn(banned, res)

    def test_purchase_silent_and_proven_remain_two_separate_findings(self):
        # The broken-purchase story and the low-value-bidding story are two distinct issues on
        # two slides - not merged, and with no shared recommendation.
        res = A.score_conversion_tracking(self._ecom_purchase_silent(
            action("Checked prices", "PAGE_VIEW", attributed=1200, all_conv=1400)))
        purchase_silent = [i for i in res["issues"] if "No purchases are reaching Google Ads" in i]
        proven = [i for i in res["issues"] if REQUIRED_PROVEN_WORDING in i]
        self.assertEqual(len(purchase_silent), 1)
        self.assertEqual(len(proven), 1)
        self.assertNotEqual(purchase_silent[0], proven[0], "the two findings are distinct strings")
        # The purchase-silent finding owns "reconnect the tag"; the proven finding must not
        # repeat that recommendation (no duplicate recommendation across the two slides).
        self.assertNotIn("Reconnecting the purchase tag", proven[0])
        self.assertNotIn("reconnect", proven[0].lower())


# ══════════════════════════════════════════════════════════════════════════════
# The sanitised Oilfast-shaped fixture (ticket T3): a fully-paused account whose
# low-value primaries recorded a confirmed zero -> Classification 2, not a headline.
# ══════════════════════════════════════════════════════════════════════════════
class OilfastFixtureIsConfirmedZero(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open(FIXTURE_PATH) as fh:
            cls.data = json.load(fh)
        cls.findings = A.analyse_account(copy.deepcopy(cls.data))

    def test_classifier_returns_confirmed_zero(self):
        res = classify(self.data["conversion_actions"])
        self.assertIsNone(res["proven"], "a paused account cannot PROVE firing")
        self.assertIsNotNone(res["zero"])
        self.assertEqual(res["zero"]["severity"], 35.0)
        self.assertEqual(res["informational"], [])

    def test_low_value_finding_is_minor_and_below_the_deck_floor(self):
        lv = find_ranked(self.findings, "not been learning from it during the period reviewed")
        self.assertIsNotNone(lv)
        self.assertEqual(lv["severity"], 35.0)
        self.assertEqual(lv["rag"], "amber")
        self.assertLess(lv["severity"], STRONG_FLOOR)

    def test_paused_account_is_not_claimed_to_be_inflating(self):
        self.assertFalse(self.findings["conversion_tracking"]["conversions_inflated"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
