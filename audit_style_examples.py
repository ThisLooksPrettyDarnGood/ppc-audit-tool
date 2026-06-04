"""
audit_style_examples.py
-----------------------
Few-shot reference distilled from ~20 real human-written PPC Geeks / PPC Team
audits (May 2026 template). These teach the model the TEAM'S house voice and the
level of specificity expected — not generic AI copy.

All examples are anonymised: no client names, only illustrative account facts.
The model is told to MATCH the voice and specificity, NOT to reuse the facts.

Wired into generate_narrative.py:
  - STYLE_NOTES is appended to the shared SYSTEM_PROMPT.
  - EXAMPLES[...] blocks are appended to each section prompt.
"""

# ── Distilled house style (appended to the system prompt) ─────────────────────
STYLE_NOTES = """
HOUSE STYLE — how our audit team actually writes. Match this voice closely:
- Speak directly to the client as "you" / "your account". Warm but straight-talking.
- Lead with the COMMERCIAL consequence — in pounds, performance and growth — not a
  technical description. Name the mechanism, e.g. "the account then starts optimising
  for spam calls, creating an expensive feedback loop".
- Use SPECIFIC numbers from the data whenever they exist (spend, conversions, %,
  counts, daily budgets, conversion rates). Specificity is what makes it feel human.
- Plain English. Always explain the "so what" — never just state a setting.
- Vary tone by traffic light: RED is urgent and direct; AMBER is "good start, but…";
  GREEN validates what they do well and may simply say "no change is needed".
- Never alarmist, never salesy in the analysis. Confident and consultative.
- British English spelling throughout (optimise, behaviour, prioritise).
""".strip()


def example_block(text: str) -> str:
    """Wrap an example with framing so the model copies the STYLE, not the facts."""
    return (
        "WORKED EXAMPLE from a past audit — match this voice and level of specificity, "
        "but write about THIS account's actual facts (do not reuse the example's details):\n"
        + text.strip()
    )


# ── Per-section worked examples (in the model's exact output format) ──────────
EXAMPLES = {
    "conversion_tracking": """
[Example A — AMBER, GA4 imports]
HEADLINE: Tracking works but isn't capturing the full picture
WHATS_HAPPENING_1: Conversions are set up for Purchases and Forms, but imported from GA4 rather than tracked directly in Google Ads.
WHATS_HAPPENING_2: There is no Enhanced Conversions or Consent Mode set up on the site.
WHY_IT_MATTERS_1: GA4 imports are a reasonable solution, but tracking directly through Google Ads with GTM feeds the bidding algorithm cleaner, faster signals.
WHY_IT_MATTERS_2: Without Consent Mode and Enhanced Conversions you're likely losing conversion data, which weakens every bidding decision Google makes.
REC1: Set up new conversion tracking through Google Ads directly using Google Tag Manager.
REC2: Implement Consent Mode v2 to meet current best practice.
REC3: Enable Enhanced Conversions on your Form and Purchase conversions.

[Example B — RED, only call-click tracking]
HEADLINE: Tracking only call clicks is feeding the account spam
WHATS_HAPPENING_1: The only primary conversion triggering in the account is Calls from Ads.
WHATS_HAPPENING_2: Form, website-call and email conversions are not being tracked at all.
WHY_IT_MATTERS_1: Calls from Ads is a low-value conversion — users often tap the number without reading the ad, so you collect a lot of spam.
WHY_IT_MATTERS_2: The account then optimises towards those spam calls, creating an expensive feedback loop that drives up cost.
REC1: Track the contact form on the site as a primary conversion.
REC2: Add website call tracking and email tracking alongside it.
REC3: Once richer conversions are live, let the bid strategy relearn from better-quality data.
""".strip(),

    "account_structure": """
[Example A — GREEN, lean but appropriate]
HEADLINE: A lean structure is fine while you gather data
WHATS_HAPPENING_1: Structure is minimal, with one Search campaign and one Performance Max campaign.
WHATS_HAPPENING_2: There's little segmentation, but the account is still in its data-gathering phase.
WHY_IT_MATTERS_1: While you're testing campaign types, a simple structure lets each campaign gather enough data to optimise rather than splitting spend too thin.
WHY_IT_MATTERS_2: Once one campaign proves itself, consolidating spend behind it will compound performance faster.
REC1: Continue with this setup while both campaigns build a baseline.
REC2: Once one campaign reaches performance you're happy with, pause the other and build out from the winner.

[Example B — AMBER, overlapping campaigns]
HEADLINE: Overlapping campaigns are competing with each other
WHATS_HAPPENING_1: There are three Search campaigns with similar keywords, ads and landing pages.
WHATS_HAPPENING_2: Their geographic targeting overlaps heavily, so they compete for the same searches.
WHY_IT_MATTERS_1: The overlap means the campaigns cannibalise each other, making all of them less efficient.
WHY_IT_MATTERS_2: Splitting the same demand across three campaigns also slows learning, because the data is fragmented.
REC1: Merge campaigns that advertise the same service to the same area into one core campaign.
REC2: Keep one campaign per distinct service to avoid internal competition.
""".strip(),

    "targeting_keywords": """
[Example A — RED, broad match + brand-led spend]
HEADLINE: Broad targeting is spending your budget on brand and spam
WHATS_HAPPENING_1: Almost all keywords are broad match, and two of three campaigns have no negative keywords.
WHATS_HAPPENING_2: Most of your spend and conversions are coming from brand search terms.
WHY_IT_MATTERS_1: Spending most of your budget on brand means you're paying for people who already know you, not finding new customers.
WHY_IT_MATTERS_2: Broad match with no negatives wastes money on irrelevant searches and feeds the account low-quality conversions.
REC1: Build out a thorough negative keyword list, including brand exclusions.
REC2: Replace most broad match with phrase and exact match.
REC3: Run focused keyword research to rebuild the strategy around non-brand demand.

[Example B — RED, PMax far too broad]
HEADLINE: PMax is running far too broad for the budget
WHATS_HAPPENING_1: The Performance Max campaign is targeting the whole UK rather than your local catchment.
WHATS_HAPPENING_2: There are no audience signals guiding PMax, so Google has little direction.
WHY_IT_MATTERS_1: On a small daily budget, targeting the whole country spreads spend too thin to convert efficiently.
WHY_IT_MATTERS_2: Without audience signals, PMax wastes budget working out who your customer is instead of being pointed at them.
REC1: Reduce PMax location targeting to your actual service radius.
REC2: Add audience signals built from your best customers to steer Google.
""".strip(),

    "bidding_strategy": """
[Example A — AMBER, CPA target too low]
HEADLINE: Your CPA target is throttling conversions
WHATS_HAPPENING_1: Campaigns are optimising for conversions, which is the right goal.
WHATS_HAPPENING_2: The Target CPA is set well below actual performance.
WHY_IT_MATTERS_1: When a campaign can't hit an unrealistic target, it bids lower and chases cheaper clicks, throttling both spend and conversion volume.
WHY_IT_MATTERS_2: The result is less volume than the budget could deliver — not a lower true cost per lead.
REC1: Raise the Target CPA to a realistic level close to current performance.
REC2: Bring the target down gradually once volume is stable, rather than all at once.

[Example B — GREEN, strategy right, inputs wrong]
HEADLINE: Bidding is set up right — the inputs need fixing
WHATS_HAPPENING_1: All campaigns use Maximise Conversions with a Target CPA, which is recommended.
WHATS_HAPPENING_2: However, the strategy is currently learning from poor-quality tracking and targeting data.
WHY_IT_MATTERS_1: The bid strategy will faithfully optimise towards whatever you feed it — right now that's brand traffic and spam.
WHY_IT_MATTERS_2: Fix the tracking and targeting and the same strategy will start driving genuine new business.
REC1: Fix the conversion tracking and targeting issues first.
REC2: Leave the bid strategy as it is — no change is needed once the inputs are clean.
""".strip(),
}


# ── Executive Summary example ─────────────────────────────────────────────────
EXEC_SUMMARY_EXAMPLE = """
EXAMPLE (match the voice and specificity, not the facts):
EXEC_HEADLINE: Broad targeting and brand-led spend are limiting growth
BULLET_1: Only Calls from Ads is tracked, so the account optimises towards spam calls.
BULLET_2: Mostly broad match with few negatives means most spend lands on brand and irrelevant searches.
BULLET_3: Three overlapping campaigns cannibalise each other and slow learning.
COMMERCIAL_IMPACT: Budget is being spent reaching people who already know you and filtering out spam, while genuine new-customer demand goes uncaptured — capping both lead volume and quality.
""".strip()


# ── Key Takeaways example ─────────────────────────────────────────────────────
TAKEAWAYS_EXAMPLE = """
EXAMPLE (match the voice and specificity, not the facts):
TK1_CURRENT: Only call-click tracking, so data quality is poor and optimisation suffers.
TK1_CHANGES: Implement on-site form, call and email tracking.
TK1_FUTURE: Accurate, richer data enabling smarter bidding and better lead quality.
TK2_CURRENT: Campaigns optimising mostly for brand traffic.
TK2_CHANGES: Rebuild targeting and add negative keywords.
TK2_FUTURE: Spend focused on driving new leads, not remarketing to existing fans.
TK3_CURRENT: Three overlapping campaigns fragmenting the data.
TK3_CHANGES: Merge into one core service campaign.
TK3_FUTURE: More efficient spend with faster learning.
""".strip()


# ── Key Opportunities example ─────────────────────────────────────────────────
OPPORTUNITIES_EXAMPLE = """
EXAMPLE (match the punchy, outcome-led voice, not the facts):
Recover budget currently lost to brand clicks and spam calls by fixing tracking and negatives.
Unlock new-customer demand by shifting from broad match to a focused keyword strategy.
Cut wasted spend by merging three overlapping campaigns into one efficient core.
Scale confidently once clean conversion data lets smart bidding work as intended.
""".strip()
