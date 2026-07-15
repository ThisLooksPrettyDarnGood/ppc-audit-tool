"""CHARACTERISATION TESTS OF KNOWN-INCORRECT BEHAVIOUR (Phase 1, ticket T3).

    ⚠  EVERY ASSERTION IN THE `Characterisation*` CLASSES BELOW PINS BEHAVIOUR WE
       BELIEVE IS WRONG. They are here to prove the failure exists BEFORE we fix it,
       and to make each fix visible when it lands. A failing test in this file is not
       automatically a bug: check the ticket it names first, then flip the assertion
       deliberately.

The fixture
-----------
`fixtures/data_paused_smart_lead_gen.json` is a sanitised copy of a real prospect audit
that went out with several confidently-wrong claims in it. Every identifying string was
replaced (see `_fixture_note` inside the file); every number, status, count, category and
type that the engine reads was preserved. Verified: the analyser produces the same account
type, the same overall RAG, the same severity ladder and the same strengths on this fixture
as it did on the real account data.

The account: a lead-gen advertiser, dark for 132 days (last active 4 March 2026), whose
six campaigns (five Search, one Smart) are all PAUSED. Nothing spent, nothing clicked and
nothing converted in the today-anchored 30-day window the engine reads.

What the engine currently does with it, and which ticket flips each one:

  1. Reads the empty 30-day window as broken tracking: severity 122, RED, and tells the
     client "Tags may be broken or firing incorrectly."                             (T5)
  2. [T4 LANDED] The low-value-primary severity inversion is fixed. On THIS paused account
     the honest classification is a confirmed-zero minor tidy-up (severity 35) - nothing
     fired, so we do not claim inflation. The proven-firing 84 headline is exercised
     against real firing evidence in test_low_value_primary.py.                     (T4)
  3. Headlines missing image extensions (severity 60) on an account whose ads have been
     switched off for four months.                                                  (T8a)
  4. Praises a 9,455-strong negative keyword list on row count alone.               (T8c)
  5. Never tells the reader the account runs Smart Campaigns.                    (T6, T7)
  6. Describes the switched-off March structure in the present tense.              (T8d)

What this account CANNOT show
-----------------------------
The PROVEN-firing branch of the low-value-primary check (the one that fires when low-value
actions are recording ad-attributed conversions) cannot fire here: a paused account records
nothing, so the check reads a CONFIRMED ZERO and classifies it as a severity-35 minor
tidy-up. The two are mutually exclusive, and we do not invent conversions to force the
headline. The corrected ladder is pinned at the classifier, against the wordings the code
emits, in `CharacterisationOfTheLowValueSeverityLadder` below.

Run:
    python3 test_paused_smart_characterisation.py
"""
import copy
import json
import os
import re
import unittest

import analyse_account as A

FIXTURE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "fixtures", "data_paused_smart_lead_gen.json")

# What the fixture was scrubbed of, and what it was replaced with. The guard class at the
# bottom of this file enforces both halves.
FIXTURE_CID = "1234567890"
FIXTURE_ACCOUNT_NAME = "Example Fuel Co Ltd"
ALLOWED_DOMAIN = "example.com"

# The engine's own deck-selection floor. Anything below it only reaches Additional
# Observations, and Key Opportunities are generated from the narrated top findings only.
STRONG_FLOOR = 55.0

_SECTIONS = ("conversion_tracking", "account_structure", "targeting_keywords",
             "bidding_strategy", "efficiency")


def load_fixture():
    with open(FIXTURE_PATH) as fh:
        return json.load(fh)


def analyse():
    """The fixture through the real engine. Fresh copy each time: analyse_account mutates."""
    findings = A.analyse_account(copy.deepcopy(load_fixture()))
    top = A.select_top_issues(findings)
    ranked = A.select_top_issues(findings, max_issues=30, apply_floor=False)
    return findings, top, ranked


def issue_texts(findings, sections=_SECTIONS):
    out = []
    for sec in sections:
        out += [str(t) for t in (findings.get(sec) or {}).get("issues", [])]
    return out


def find_issue(items, needle):
    """The first ranked issue whose detail contains `needle`, or None."""
    return next((i for i in items if needle in i["detail"]), None)


# ══════════════════════════════════════════════════════════════════════════════
# The account conditions the fixture exists to preserve. If one of these breaks,
# the characterisation tests below stop meaning anything.
# ══════════════════════════════════════════════════════════════════════════════

class FixtureShape(unittest.TestCase):

    def setUp(self):
        self.d = load_fixture()

    def test_account_is_long_paused(self):
        perf = self.d["performance_summary"]
        self.assertTrue(perf["is_paused"])
        self.assertEqual(perf["last_active"], "2026-03-04")
        self.assertEqual(perf["days_dark"], 132)

    def test_current_30_day_window_is_completely_empty(self):
        """The bit no other account in the corpus has: a fully zero today-anchored window."""
        s = self.d["account_summary_30d"]
        self.assertEqual(s["spend"], 0.0)
        self.assertEqual(s["clicks"], 0)
        self.assertEqual(s["conversions"], 0.0)
        self.assertEqual(s["impressions"], 0)

    def test_estate_is_paused_with_a_smart_campaign_and_no_active_search_or_pmax(self):
        camps = self.d["campaigns"]
        self.assertEqual(len(camps), 6)
        self.assertTrue(all(c["status"] == "PAUSED" for c in camps))
        self.assertEqual(sum(1 for c in camps if c["type"] == "SMART"), 1)
        self.assertEqual([c for c in camps if c["type"] == "PERFORMANCE_MAX"], [])
        # Nothing is running at all, so no Search or PMax campaign is active either.
        self.assertEqual(self.d["campaign_types_active"], [])
        self.assertFalse(self.d["audience_signals"]["has_pmax"])

    def test_many_conversion_actions_many_primaries_and_ga4_imports(self):
        actions = self.d["conversion_actions"]
        self.assertEqual(len(actions), 47)
        self.assertEqual(sum(1 for a in actions if a["primary_for_goal"]), 26)
        # Every action reads zero in the 30-day window, because the ads are off.
        self.assertTrue(all((a["conversions_30d"] or 0) == 0 for a in actions))
        self.assertTrue(any(a["type"].startswith("GOOGLE_ANALYTICS_4") for a in actions))

    def test_low_value_actions_are_set_as_primary(self):
        low_value = [a for a in self.d["conversion_actions"]
                     if a["primary_for_goal"] and a["category"] in ("PAGE_VIEW", "ENGAGEMENT")]
        self.assertGreaterEqual(len(low_value), 2)

    def test_large_negative_list_no_image_assets_and_a_paused_ad_group_estate(self):
        self.assertEqual(self.d["negative_keyword_count"], 9455)
        self.assertNotIn("AD_IMAGE", self.d["ad_assets"])
        self.assertEqual(len(self.d["ad_groups"]), 127)


# ══════════════════════════════════════════════════════════════════════════════
# ⚠  KNOWN-INCORRECT BEHAVIOUR. Each test names the ticket that will flip it.
# ══════════════════════════════════════════════════════════════════════════════

class CharacterisationOfWrongBehaviour(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.findings, cls.top, cls.ranked = analyse()

    # ── T5 ────────────────────────────────────────────────────────────────────
    def test_WRONG_paused_account_triggers_the_severity_122_red_zero_conversion_rule(self):
        """A switched-off account is scored as a broken one, and it sets the RED dial.

        WRONG: the only thing we actually know is that the ads are off. T5 fixes this.
        """
        headline = self.top[0]
        self.assertEqual(headline["severity"], 122.0)
        self.assertEqual(headline["rag"], "red")
        self.assertIn("47 conversion action(s) exist but recorded 0 conversions",
                      headline["detail"])
        # And it is what turns the whole deck red.
        self.assertEqual(A.overall_rag_from_issues(self.top), "red")

    def test_WRONG_the_wording_tells_the_client_their_tags_may_be_broken(self):
        """The flagship 'never confidently wrong' violation. T5 fixes this."""
        headline = self.top[0]["detail"]
        self.assertIn("Tags may be broken or firing incorrectly", headline)
        # It does not mention the pause at all - which is the whole problem.
        self.assertNotIn("paused", headline.lower())
        self.assertNotIn("2026-03-04", headline)

    # ── T4 (LANDED) ─────────────────────────────────────────────────────────
    def test_T4_low_value_primary_on_a_paused_account_is_a_confirmed_zero_minor(self):
        """T4 has landed. On THIS account the honest classification is a minor tidy-up.

        A paused account records nothing, so the low-value primaries are a CONFIRMED
        ZERO (Classification 2): included in bidding, but they did not fire, so bidding
        has not been learning from them. That is genuinely a severity-35 minor tidy-up
        for Additional Observations - not a headline, because nothing was proven to fire.
        We do NOT invent firing to force the 84 headline (that would be confidently
        wrong on a switched-off account). The proven-firing 84 branch is exercised
        against real firing evidence in test_low_value_primary.py instead.
        """
        low_value = find_issue(self.ranked, "not been learning from it during the period reviewed")
        self.assertIsNotNone(low_value, "the low-value primary check should still fire")
        self.assertEqual(low_value["severity"], 35.0)
        self.assertEqual(low_value["rag"], "amber")
        self.assertLess(low_value["severity"], STRONG_FLOOR)
        self.assertNotIn(low_value["detail"], [i["detail"] for i in self.top])
        # It never claims bidding learned from these actions during the empty window.
        self.assertNotIn("Bidding will be learning from the wrong actions", low_value["detail"])

    # ── T8a ───────────────────────────────────────────────────────────────────
    def test_WRONG_missing_image_extensions_score_60_and_reach_the_top_findings(self):
        """A gate-free rule headlines an account whose ads have been off for 132 days.

        Nothing about spend, materiality or account activity gates this rule, so on an
        account where every money rule is silent it floats up by default. T8a fixes it.
        """
        extensions = find_issue(self.top, "missing high-value extension types")
        self.assertIsNotNone(extensions)
        self.assertEqual(extensions["severity"], 60.0)
        self.assertGreaterEqual(extensions["severity"], STRONG_FLOOR)
        self.assertIn("image extensions", extensions["detail"])
        # It reaches the narrated findings, which is what feeds Key Opportunities.
        self.assertIn(extensions["detail"], [i["detail"] for i in self.top])

    # ── T8c ───────────────────────────────────────────────────────────────────
    def test_WRONG_negative_keywords_are_praised_on_row_count_alone(self):
        """9,455 negatives becomes a strength with no quality test of any kind.

        The list is not read, the account is paused, and the 'we cross-checked and found
        no conflicts' observation is checked against an EMPTY converting-terms list. T8c
        deletes or gates both.
        """
        strengths = self.findings.get("strengths") or []
        self.assertTrue(any("well-maintained negative keyword list" in s for s in strengths),
                        f"expected the count-based praise, got {strengths}")
        self.assertTrue(any("9,455" in s for s in strengths))

        # The vacuous cross-check: there is nothing to cross-check against.
        self.assertEqual(load_fixture()["converting_unkeyworded_terms"], [])
        observation = find_issue(self.ranked, "an unusually large list")
        self.assertIsNotNone(observation)
        self.assertIn("found no conflicts", observation["detail"])

    # ── T6 / T7 ───────────────────────────────────────────────────────────────
    def test_WRONG_smart_campaigns_are_never_identified(self):
        """The account runs a Smart campaign and the deck cannot say so.

        The engine has no concept of a Smart campaign: it reports the absence of the types
        it does know about, which tells the client nothing about what they ARE running.
        T6 identifies them; T7 stops Search-only rules being applied to them.
        """
        smart = [c for c in load_fixture()["campaigns"] if c["type"] == "SMART"]
        self.assertEqual(len(smart), 1)

        structure = issue_texts(self.findings, ("account_structure",))
        self.assertTrue(any("No Search or Performance Max campaigns active" in t
                            for t in structure))

        # Nothing outside the conversion-action names (which are Google's own wording)
        # tells the reader this is a Smart Campaign estate.
        elsewhere = issue_texts(self.findings, ("account_structure", "targeting_keywords",
                                                "bidding_strategy", "efficiency"))
        self.assertFalse(any("smart campaign" in t.lower() for t in elsewhere),
                         "a Smart estate is described without ever naming Smart Campaigns")
        # And the engine has no helper to identify one (T6 adds it).
        self.assertFalse(hasattr(A, "is_smart_campaign"))

    # ── T8d ───────────────────────────────────────────────────────────────────
    def test_WRONG_the_paused_march_structure_is_described_as_a_current_problem(self):
        """'127 ad groups across 6 campaigns' - present tense, on an estate that is off.

        Every one of those campaigns and most of those ad groups have been switched off
        since March. We recommend consolidating them without saying so. T8d reframes it.
        """
        structure = find_issue(self.ranked, "ad groups across")
        self.assertIsNotNone(structure)
        self.assertEqual(structure["severity"], 45.0)
        self.assertIn("127 ad groups across 6 campaigns", structure["detail"])
        self.assertIn("Consider consolidating", structure["detail"])
        # No pause, no date, no past tense anywhere in it.
        self.assertNotIn("paused", structure["detail"].lower())
        self.assertNotIn("2026-03-04", structure["detail"])


class CharacterisationOfTheLowValueSeverityLadder(unittest.TestCase):
    """The severity ladder, pinned at the classifier - CORRECTED by T4.

    Before T4 the low-value-primary check inverted its own severities: the branch that
    PROVED the problem took the unsignatured 40 fallback, while the branch that admitted
    it could not measure anything scored 82. Confidence beat measurement, backwards.

    After T4 the ladder follows the evidence. Each state is pinned here against the exact
    wording classify_low_value_primary_conversions emits, built from fictional actions so
    all three states can be exercised (the fixture itself is a confirmed zero).
    """

    # One fictional action per evidence state (shape matches fetch_account_data).
    def _ca(self, **kw):
        base = {"name": "Homepage view", "category": "PAGE_VIEW", "status": "ENABLED",
                "primary_for_goal": True, "include_in_conversions": True}
        base.update(kw)
        return base

    def _severity(self, detail):
        meta = A._classify_issue(detail, "Conversion Tracking", "amber")
        self.assertIsNotNone(meta, "the wording must classify, not be filtered")
        return meta["severity"]

    def test_T4_measurement_now_beats_confidence(self):
        res_proven = A.classify_low_value_primary_conversions(
            [self._ca(attributed_conversions_30d=6.0, conversions_30d=20.0)])
        res_zero = A.classify_low_value_primary_conversions(
            [self._ca(attributed_conversions_30d=0.0, conversions_30d=0.0)])
        res_unconf = A.classify_low_value_primary_conversions([self._ca()])  # no volume keys

        # PROVEN firing is the root cause - the highest rung, and a headline.
        self.assertEqual(res_proven["proven"]["severity"], 84.0)
        self.assertEqual(res_proven["proven"]["rag"], "amber_red")
        self.assertEqual(self._severity(res_proven["proven"]["detail"]), 84.0)

        # CONFIRMED ZERO is a minor tidy-up.
        self.assertEqual(res_zero["zero"]["severity"], 35.0)
        self.assertEqual(self._severity(res_zero["zero"]["detail"]), 35.0)

        # UNCONFIRMED carries no score at all - informational only.
        self.assertIsNone(res_unconf["proven"])
        self.assertIsNone(res_unconf["zero"])
        self.assertEqual(len(res_unconf["informational"]), 1)

        # Measurement beats confidence: proven > zero, and the unmeasured state scores nothing.
        self.assertGreater(res_proven["proven"]["severity"], res_zero["zero"]["severity"])

    def test_T4_the_orphaned_needle_is_gone_and_the_new_needles_are_matched(self):
        """The orphaned 84/amber_red needle matched no emitted wording (the same bug family
        as the budget-capped IS needle). T4 removes it and adds needles that DO match."""
        orphan = "PRIMARY conversions are dominated by low-value"
        self.assertFalse(any(needle == orphan for needle, *_ in A._ISSUE_SIGNATURES),
                         "the orphaned needle should have been removed")

        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "analyse_account.py")) as fh:
            source = fh.read()
        self.assertEqual(source.count(orphan), 0, "no trace of the orphaned needle remains")

        # Every new needle matches a wording the classifier actually emits.
        proven = A.classify_low_value_primary_conversions(
            [self._ca(attributed_conversions_30d=6.0, conversions_30d=20.0)])["proven"]["detail"]
        zero = A.classify_low_value_primary_conversions(
            [self._ca(attributed_conversions_30d=0.0, conversions_30d=0.0)])["zero"]["detail"]
        self.assertIn("learning from the wrong actions", proven)
        self.assertIn("not been learning from it during the period reviewed", zero)


# ══════════════════════════════════════════════════════════════════════════════
# The fixture is committed. This is the test that keeps it committable.
# ══════════════════════════════════════════════════════════════════════════════

class FixtureContainsNoClientIdentifiers(unittest.TestCase):
    """Fails if any obvious client identifier survives in the committed fixture.

    Two halves, because a deny-list alone is only as good as its imagination:
      - nothing from the real account (its name, its CID, its domains, any email);
      - and every name, ID and location in the file matches a synthetic pattern, so a
        real one reintroduced by a careless re-sanitise cannot pass by being unlisted.
    """

    # The client's own name. The one deny-list entry worth having: it is the string a
    # botched re-sanitise would leave behind.
    BANNED_TOKENS = ("oilfast",)

    # Campaign names are checked against an exact allowlist rather than a pattern: a real
    # campaign name is shaped exactly like a synthetic one ("Search - " plus a product
    # word), so only an allowlist can tell the two apart.
    SYNTHETIC_CAMPAIGN_NAMES = {
        "Search - Product A", "Search - Product B", "Search - Product C",
        "Search - Product D", "Search - Product E", "Smart - Local Calls",
        "PMax - Product B (removed)",       # a long-removed campaign, history only
    }

    @classmethod
    def setUpClass(cls):
        with open(FIXTURE_PATH) as fh:
            cls.raw = fh.read()
        cls.d = json.loads(cls.raw)

    def test_no_client_name_and_no_real_customer_id(self):
        for token in self.BANNED_TOKENS:
            self.assertNotIn(token, self.raw.lower(), f"client identifier '{token}' in fixture")

        self.assertEqual(self.d["client_cid"], FIXTURE_CID)
        self.assertEqual(self.d["account_name"], FIXTURE_ACCOUNT_NAME)

        # Every Google resource name must carry the synthetic CID, never a real one.
        for cid in set(re.findall(r"customers/(\d+)", self.raw)):
            self.assertEqual(cid, FIXTURE_CID, "a real customer ID is embedded in a resource name")
        # ... and no CID in the dashed form a human would paste in.
        self.assertEqual(re.findall(r"\b\d{3}-\d{3}-\d{4}\b", self.raw), [])

    def test_no_real_urls_domains_or_email_addresses(self):
        domains = set(re.findall(r"\b[a-z0-9][a-z0-9\-.]*\.(?:co\.uk|com|net|org|io|uk)\b",
                                 self.raw, re.I))
        self.assertEqual(domains, {ALLOWED_DOMAIN}, f"unexpected domain(s): {domains}")
        self.assertEqual(re.findall(r"[\w.+-]+@[\w-]+\.\w+", self.raw), [])
        self.assertNotIn("http", self.raw.lower())

    def test_campaign_ad_group_and_location_names_are_all_synthetic(self):
        for c in self.d["campaigns"]:
            self.assertIn(c["name"], self.SYNTHETIC_CAMPAIGN_NAMES)
            self.assertRegex(c["id"], r"^\d{11}$")
        for ag in self.d["ad_groups"]:
            self.assertRegex(ag["name"], r"^Ad group \d{3}( \| (Phrase|Exact|Broad))?$")
        for lt in self.d["location_targeting"]:
            self.assertRegex(lt["location_name"], r"^(Region [A-Z]|Excluded area \d{2})$")

        # A campaign name reaches the deck through half a dozen other structures too.
        # Every one of them must be drawn from the same allowlist.
        referenced = {lt["campaign_name"] for lt in self.d["location_targeting"]}
        referenced |= {e["campaign"] for e in self.d["negative_keywords"]["campaign"]}
        referenced |= set(self.d["negative_keywords"]["ad_group_counts"])
        referenced |= {c for camps in self.d["negative_keywords"]["shared_campaigns"].values()
                       for c in camps}
        referenced |= set(self.d["campaign_conversion_split"])
        referenced |= {h["name"] for h in self.d["paused_campaign_history"]}
        referenced |= {s["name"] for s in self.d["shopping_history_alltime"]}
        self.assertTrue(referenced <= self.SYNTHETIC_CAMPAIGN_NAMES,
                        f"non-synthetic campaign name(s): "
                        f"{sorted(referenced - self.SYNTHETIC_CAMPAIGN_NAMES)}")

    def test_search_terms_negative_keywords_and_third_parties_are_all_synthetic(self):
        # No search terms at all in this account (paused + Smart), so nothing to leak.
        self.assertEqual(self.d["top_search_terms"], [])
        self.assertEqual(self.d["converting_unkeyworded_terms"], [])

        negatives = self.d["negative_keywords"]
        for entry in negatives["campaign"] + negatives["shared"]:
            self.assertRegex(entry["text"], r"^neg_term_\d{3}$")
        for name in negatives["shared_sizes"]:
            self.assertRegex(name, r"^Shared set \d+$")

        for kw in self.d["client_keywords"]:
            self.assertRegex(kw, r"^product term \d+$")
        for comp in self.d["competitors"]:
            self.assertRegex(comp, r"^competitor \d+$")

    def test_conversion_action_names_carry_no_brand(self):
        for a in self.d["conversion_actions"]:
            name = a["name"]
            for token in self.BANNED_TOKENS:
                self.assertNotIn(token, name.lower())
            # Any URL inside an action name must be the placeholder domain.
            for domain in re.findall(r"\b[a-z0-9][a-z0-9\-.]*\.(?:co\.uk|com|net|org)\b",
                                     name, re.I):
                self.assertEqual(domain, ALLOWED_DOMAIN)


if __name__ == "__main__":
    unittest.main(verbosity=2)
