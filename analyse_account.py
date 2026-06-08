# analyse_account.py
# Step 4: Analysis & Scoring Engine

# ── Auto-Apply Recommendation types the team is HAPPY to leave ON ─────────────
# Source: Max's Auto-Apply screen (the red-boxed items the team approves of).
# Verified against the official Google Ads API RecommendationType enum (v24).
# KEY FINDING: the team's approved "maintenance" toggles — Remove conflicting/
# redundant/non-serving keywords and Upgrade conversion tracking — are NOT in the
# RecommendationType enum, so recommendation_subscription won't return them. They
# therefore CAN'T be false-flagged. The only approved option the API surfaces is
# ad rotation, so it's the sole entry here. Everything else the API returns
# (broad match, display expansion, bidding/Search-Partners opt-ins, RSA changes)
# is materially impactful and worth flagging.
APPROVED_AAR_TYPES = {
    "OPTIMIZE_AD_ROTATION",                 # "Use optimised ad rotation"
}

# Friendly labels for the client-facing slide (prettify unknowns automatically).
# Enum names confirmed from the official RecommendationType reference.
AAR_LABELS = {
    "OPTIMIZE_AD_ROTATION":                  "Optimise ad rotation",
    "RESPONSIVE_SEARCH_AD":                  "Add responsive search ads",
    "RESPONSIVE_SEARCH_AD_IMPROVE_AD_STRENGTH": "Improve responsive search ads",
    "RESPONSIVE_SEARCH_AD_ASSET":            "Add responsive search ad assets",
    "USE_BROAD_MATCH_KEYWORD":               "Add broad match keywords",
    "DISPLAY_EXPANSION_OPT_IN":              "Use Display Expansion",
    "MAXIMIZE_CLICKS_OPT_IN":                "Switch to Maximise Clicks",
    "MAXIMIZE_CONVERSIONS_OPT_IN":           "Switch to Maximise Conversions",
    "MAXIMIZE_CONVERSION_VALUE_OPT_IN":      "Switch to Maximise Conversion Value",
    "TARGET_CPA_OPT_IN":                     "Switch to Target CPA",
    "TARGET_ROAS_OPT_IN":                    "Switch to Target ROAS",
    "ENHANCED_CPC_OPT_IN":                   "Enable Enhanced CPC",
    "SEARCH_PARTNERS_OPT_IN":                "Opt in to Search Partners",
    "PERFORMANCE_MAX_OPT_IN":                "Opt in to Performance Max",
    "KEYWORD_MATCH_TYPE":                    "Change keyword match types",
}


def _aar_label(t: str) -> str:
    return AAR_LABELS.get(t, t.replace("_", " ").title())


def detect_account_type(data):
    """
    Infer whether this is a lead gen or eCommerce account from conversion action categories.
    Returns 'ecommerce', 'lead_gen', or 'unknown'.
    """
    conversion_actions = data.get("conversion_actions", [])
    ecommerce_categories = {"PURCHASE"}
    lead_gen_categories = {
        "LEAD", "CONTACT", "SUBMIT_LEAD_FORM", "BOOK_APPOINTMENT",
        "REQUEST_QUOTE", "SIGNUP", "PHONE_CALL_LEAD", "IMPORTED_LEAD",
    }
    has_purchase = any(
        ca.get("category", "") in ecommerce_categories
        for ca in conversion_actions
    )
    has_lead = any(
        ca.get("category", "") in lead_gen_categories
        for ca in conversion_actions
    )
    if has_purchase and not has_lead:
        return "ecommerce"
    if has_lead:
        return "lead_gen"
    return "unknown"


def analyse_account(data):
    account_type = detect_account_type(data)
    findings = {
        "conversion_tracking": score_conversion_tracking(data),
        "account_structure":   score_account_structure(data),
        "targeting_keywords":  score_targeting_keywords(data),
        "bidding_strategy":    score_bidding_strategy(data),
        "summary_stats":       build_summary_stats(data),
        "account_type":        account_type,
        "performance_summary": data.get("performance_summary", {}),
    }
    return findings


# ─────────────────────────────────────────────────────────────────────────────
# ISSUE-LED SELECTION LAYER
# The 4 scorers above produce all the diagnostics. A human auditor doesn't present
# 4 fixed category slides — they pick the most important PROBLEMS and lead with them.
# This layer turns the scorers' findings into a ranked, flat list of discrete issues
# so the deck can be issue-led (top-N named problems, one per slide). The scorers are
# left completely untouched — this only re-organises and prioritises their output.
# ─────────────────────────────────────────────────────────────────────────────

# (needle in the finding text, severity, per-issue RAG, slide category)
# Order matters: the first matching signature wins, so put the most severe / most
# specific needles first. Severity ~ how much a human auditor would lead with it.
_ISSUE_SIGNATURES = [
    # Critical — account fundamentally not working
    ("No conversion actions found",              130, "red",       "Conversion Tracking"),
    ("recorded 0 conversions in the last 30",    122, "red",       "Conversion Tracking"),
    ("spent with 0 conversions",                 116, "red",       "Bidding Strategy"),
    ("should be treated as urgent",              112, "red",       "Targeting & Keywords"),  # broad + weak negatives combo
    # On the cusp
    ("Low-value conversion action",               82, "amber_red", "Conversion Tracking"),
    # Bidding
    ("on Maximise Clicks",                         78, "amber",     "Bidding Strategy"),
    ("paused campaign(s) historically converted",  66, "amber",     "Bidding Strategy"),
    ("still on Manual CPC despite",                62, "amber",     "Bidding Strategy"),
    ("on Manual CPC.",                             58, "amber",     "Bidding Strategy"),
    ("has a target CPA of",                        55, "amber",     "Bidding Strategy"),
    ("on smart bidding recorded only",             50, "amber",     "Bidding Strategy"),
    ("using inconsistent bid strategies",          46, "amber",     "Bidding Strategy"),
    ("Cost per conversion is",                     48, "amber",     "Bidding Strategy"),
    # Targeting & keywords
    ("without converting",                         63, "amber",     "Targeting & Keywords"),  # wasted SQR spend
    ("not been added as keywords",                 60, "amber",     "Targeting & Keywords"),  # converting queries
    ("without audience signals",                   56, "amber",     "Targeting & Keywords"),
    ("targeting the whole UK",                     55, "amber",     "Targeting & Keywords"),
    ("responsive search ads are rated",            54, "amber",     "Targeting & Keywords"),  # RSA ad strength
    ("of keyword clicks come from Broad Match",    58, "amber",     "Targeting & Keywords"),
    ("of keyword spend is on Broad Match",         57, "amber",     "Targeting & Keywords"),
    ("negative keywords applied across",           50, "amber",     "Targeting & Keywords"),
    ("Exact Match only",                           45, "amber",     "Targeting & Keywords"),
    ("No Exact Match keyword clicks",              44, "amber",     "Targeting & Keywords"),
    ("No keyword click data",                      48, "amber",     "Targeting & Keywords"),
    ("CTR is",                                     40, "amber",     "Targeting & Keywords"),
    # Conversion tracking (amber)
    ("imported from GA4",                          56, "amber",     "Conversion Tracking"),
    ("set as primary 'Conversions'",               45, "amber",     "Conversion Tracking"),  # too many primary
    ("count 'Every' rather than 'Once'",           40, "amber",     "Conversion Tracking"),
    ("with 0 conversions recorded",                64, "amber",     "Conversion Tracking"),  # campaign spend, no conv
    ("negative keyword(s) found across",           50, "amber",     "Conversion Tracking"),
    ("Conversion rate is",                         48, "amber",     "Conversion Tracking"),
    # Account structure
    ("received zero impressions",                  46, "amber",     "Account Structure"),
    ("ad groups across",                           45, "amber",     "Account Structure"),
    ("No Search or Performance Max",               50, "amber",     "Account Structure"),
    ("split across",                               48, "amber",     "Account Structure"),  # budget too thin
    ("smart bidding cannot learn",                 50, "amber",     "Account Structure"),
    ("Auto-Apply is enabled for:",                 32, "amber",     "Account Structure"),
    ("Auto-Apply Recommendations are enabled",     30, "amber",     "Account Structure"),
]

# Positive / "all good" filler lines the scorers add when a section is clean.
# These are NOT issues and must never become a slide.
_FILLER_MARKERS = (
    "lean and focused", "is healthy", "looks well-structured", "looks healthy",
    "is appropriate —", "set up and recording", "well-organised", "no action needed",
)

import re as _re

def _largest_pound(text):
    """Largest £ figure in a finding, used as a small commercial-magnitude tie-break."""
    vals = []
    for m in _re.findall(r"£([\d,]+)", text or ""):
        try:
            vals.append(float(m.replace(",", "")))
        except ValueError:
            pass
    return max(vals) if vals else 0.0


def _classify_issue(detail, section_name, section_rag):
    if any(marker in detail for marker in _FILLER_MARKERS):
        return None
    for needle, sev, rag, cat in _ISSUE_SIGNATURES:
        if needle in detail:
            return {"severity": float(sev), "rag": rag, "category": cat}
    # Genuine but unsignatured finding: keep it (don't lose real issues) unless the
    # whole section came back green (then it's almost certainly positive filler).
    if section_rag == "green":
        return None
    rag = "amber" if section_rag == "amber_red" else section_rag
    return {"severity": 40.0, "rag": rag, "category": section_name}


def select_top_issues(findings, max_issues=6):
    """Flatten the 4 scorers' findings into a ranked list of discrete issues,
    most important first, capped at max_issues. Each item:
        {detail, category, rag, severity}
    """
    section_map = {
        "conversion_tracking": "Conversion Tracking",
        "account_structure":   "Account Structure",
        "targeting_keywords":  "Targeting & Keywords",
        "bidding_strategy":    "Bidding Strategy",
    }
    flat = []
    for key, name in section_map.items():
        sec = findings.get(key, {})
        srag = sec.get("rag", "amber")
        for detail in sec.get("issues", []):
            meta = _classify_issue(detail, name, srag)
            if not meta:
                continue
            mag = _largest_pound(detail)
            bump = min(mag / 200.0, 12.0) if mag else 0.0   # up to +12 for big money
            flat.append({
                "detail": detail,
                "category": meta["category"],
                "rag": meta["rag"],
                "severity": meta["severity"] + bump,
            })
    flat.sort(key=lambda x: x["severity"], reverse=True)
    return flat[:max_issues]


def overall_rag_from_issues(issues):
    """Worst RAG across the issues (amber_red sits between red and amber)."""
    order = {"red": 0, "amber_red": 0.5, "amber": 1, "green": 2}
    if not issues:
        return "green"
    return min((i.get("rag", "amber") for i in issues), key=lambda r: order.get(r, 1))

# ─────────────────────────────────────────────
# SECTION 1: CONVERSION TRACKING
# ─────────────────────────────────────────────

def score_conversion_tracking(data):
    issues = []
    rag = "green"
    conversion_actions = data.get("conversion_actions", [])
    summary = data.get("account_summary_30d", {})
    total_conversions = summary.get("conversions", 0)
    campaigns = data.get("campaigns", [])

    if len(conversion_actions) == 0:
        issues.append("No conversion actions found — tracking is not set up.")
        rag = "red"
    else:
        if total_conversions == 0:
            issues.append(
                f"{len(conversion_actions)} conversion action(s) exist but recorded 0 "
                "conversions in the last 30 days. Tags may be broken or firing incorrectly."
            )
            rag = "red"
        else:
            clicks = summary.get("clicks", 0)
            if clicks > 0:
                cvr = total_conversions / clicks
                if cvr < 0.005:
                    issues.append(
                        f"Conversion rate is {cvr:.2%} — unusually low. "
                        "Check for tracking gaps or low-quality traffic."
                    )
                    if rag == "green":
                        rag = "amber"

        # Only ACTIVE conversion actions matter — inactive/hidden ones aren't being
        # used by the account, so don't flag them at all (practitioner feedback).
        active_actions = [ca for ca in conversion_actions if ca.get("status") == "ENABLED"]
        # Count only PRIMARY actions (the ones bidding actually optimises towards).
        primary_actions = [ca for ca in active_actions if ca.get("include_in_conversions")]
        if len(primary_actions) > 10:
            issues.append(
                f"{len(primary_actions)} conversion actions are set as primary 'Conversions' that "
                "bidding optimises towards. Too many primary actions can dilute reporting and confuse "
                "bidding — review for duplicates, test tags or low-value actions."
            )
            if rag == "green":
                rag = "amber"

        # GA4 import detection — no native tag snippet = likely imported from GA4
        web_categories = {
            "PURCHASE", "SUBMIT_LEAD_FORM", "LEAD", "CONTACT",
            "BOOK_APPOINTMENT", "REQUEST_QUOTE", "SIGNUP",
            "PHONE_CALL_LEAD", "IMPORTED_LEAD", "DEFAULT", "OTHER"
        }
        ga4_imported = [
            ca.get("name", "Unknown") for ca in active_actions
            if ca.get("include_in_conversions")
            and not ca.get("has_tag_snippet", True)   # default True = don't flag if data missing
            and ca.get("category", "") in web_categories
        ]
        if ga4_imported:
            issues.append(
                "Some primary conversions appear to be imported from GA4 rather than tracked via a "
                "native Google Ads tag. Native Google Ads tags give the cleanest, most complete signal "
                "for bidding — it's worth confirming Enhanced Conversions is active and that tracking "
                "is firing correctly."
            )
            if rag == "green":
                rag = "amber"

        # Spammable/low-value categories set as primary optimisation goal
        spammable_categories = {"PAGE_VIEW", "ENGAGEMENT", "DOWNLOAD"}
        primary_spammable = [
            ca.get("name", "Unknown") for ca in active_actions
            if ca.get("include_in_conversions")
            and ca.get("category", "") in spammable_categories
        ]
        if primary_spammable:
            issues.append(
                "Low-value conversion action(s) included in primary optimisation: "
                + ", ".join(primary_spammable) + ". "
                "Campaigns may be optimising towards clicks or page views rather than real leads or sales."
            )
            # Tracking exists but a low-value action (e.g. page views) is a primary
            # conversion — serious, but not a total failure. Mark it "on the cusp"
            # (amber/red) rather than full red, unless tracking is already broken.
            if rag != "red":
                rag = "amber_red"

        # Conversion count type — MANY_PER_CLICK on lead gen actions inflates numbers
        lead_categories = {
            "CONTACT", "SUBMIT_LEAD_FORM", "BOOK_APPOINTMENT", "REQUEST_QUOTE",
            "SIGNUP", "LEAD", "PHONE_CALL_LEAD", "IMPORTED_LEAD", "DEFAULT", "OTHER"
        }
        many_per_click_leads = [
            ca.get("name", "Unknown") for ca in active_actions
            if ca.get("include_in_conversions")
            and ca.get("counting_type") == "MANY_PER_CLICK"
            and ca.get("category", "") in lead_categories
            and ca.get("category", "") != "PHONE_CALL_LEAD"   # calls counting 'every' is normal
        ]
        if many_per_click_leads:
            issues.append(
                "Some lead conversions are set to count 'Every' rather than 'Once'. "
                "For most lead actions 'Once' is more accurate (calls can be a fair exception) — "
                "worth confirming these are counting the way you intend."
            )
            if rag == "green":
                rag = "amber"

    # Campaigns spending with zero conversions. Skip awareness-style campaigns —
    # Display / Video / Demand Gen are often run for reach, so zero conversions is
    # expected and must NOT be flagged as wasted spend (per practitioner feedback).
    AWARENESS_TYPES = {"DISPLAY", "VIDEO", "DEMAND_GEN", "MULTI_CHANNEL"}
    for c in campaigns:
        c_conv = c.get("conversions_30d", 0)
        c_cost = c.get("spend_30d", 0)
        c_name = c.get("name", "Unknown campaign")
        c_status = c.get("status", "")
        c_type = c.get("type", "")
        if c_status == "ENABLED" and c_cost > 50 and c_conv == 0 and c_type not in AWARENESS_TYPES:
            issues.append(
                f"Campaign '{c_name}' spent £{c_cost:.2f} with 0 conversions recorded — "
                "worth confirming it isn't an awareness or brand campaign before treating this as wasted spend."
            )
            if rag == "green":
                rag = "amber"

    # Negative keyword count
    neg_kw_count = data.get("negative_keyword_count", None)
    if neg_kw_count is not None and neg_kw_count < 20:
        issues.append(
            f"Only {neg_kw_count} negative keyword(s) found across the account. "
            "With PMax and broad match running broadly, low negatives mean budget is likely wasted on irrelevant searches."
        )
        if rag == "green":
            rag = "amber"

    if not issues:
        issues.append(
            f"Conversion tracking is healthy — {len(conversion_actions)} actions "
            f"set up and recording {total_conversions:.0f} conversions."
        )

    return {
        "rag": rag,
        "headline": _ct_headline(rag, total_conversions, len(conversion_actions)),
        "issues": issues,
        "data_points": {
            "conversion_actions_count": len(conversion_actions),
            "total_conversions_30d": total_conversions,
        },
    }


def _ct_headline(rag, conversions, action_count):
    if rag == "red":
        return "Conversion tracking is broken or missing"
    if rag == "amber":
        return "Conversion tracking has gaps that need attention"
    return f"Conversion tracking is set up and recording ({conversions:.0f} conversions, {action_count} actions)"


# ─────────────────────────────────────────────
# SECTION 2: ACCOUNT STRUCTURE
# ─────────────────────────────────────────────

def score_account_structure(data):
    issues = []
    rag = "green"
    campaigns = data.get("campaigns", [])
    ad_groups = data.get("ad_groups", [])
    campaign_types_active = data.get("campaign_types_active", [])
    summary = data.get("account_summary_30d", {})

    num_campaigns = len(campaigns)
    num_ad_groups = len(ad_groups)

    if num_campaigns == 0:
        issues.append("No active campaigns found in this account.")
        return {"rag": "red", "headline": "No active campaigns found", "issues": issues, "data_points": {}}

    enabled_campaigns = [c for c in campaigns if c.get("status") == "ENABLED"]
    search_count = sum(1 for c in enabled_campaigns if c.get("type") == "SEARCH")
    pmax_count   = sum(1 for c in enabled_campaigns if c.get("type") == "PERFORMANCE_MAX")

    # Ad groups per campaign ratio
    if num_ad_groups > 0 and num_campaigns > 0:
        ratio = num_ad_groups / num_campaigns
        if ratio > 10:
            issues.append(
                f"{num_ad_groups} ad groups across {num_campaigns} campaigns "
                f"({ratio:.1f} per campaign). Consider consolidating into tighter themes."
            )
            if rag == "green":
                rag = "amber"

    # Campaigns with zero impressions (enabled only)
    zero_impression = [
        c.get("name", "Unknown") for c in campaigns
        if c.get("impressions_30d", 0) == 0 and c.get("status", "") == "ENABLED"
    ]
    if zero_impression:
        issues.append(
            f"{len(zero_impression)} enabled campaign(s) received zero impressions: "
            + ", ".join(zero_impression)
        )
        rag = "amber"

    # Campaign type mix
    has_search = "SEARCH" in campaign_types_active
    has_pmax = "PERFORMANCE_MAX" in campaign_types_active
    if not has_search and not has_pmax:
        issues.append(
            "No Search or Performance Max campaigns active. "
            "Verify the account is running the right campaign types."
        )
        if rag == "green":
            rag = "amber"

    # Budget spread too thin
    if enabled_campaigns:
        budgets = [
            c.get("daily_budget_gbp", 0) for c in enabled_campaigns
            if c.get("daily_budget_gbp", 0) > 0
        ]
        if budgets:
            total_budget = sum(budgets)
            avg_budget = total_budget / len(budgets)
            if len(enabled_campaigns) > 2 and avg_budget < 10:
                issues.append(
                    f"£{total_budget:.2f}/day total budget is split across {len(enabled_campaigns)} campaigns "
                    f"(avg £{avg_budget:.2f} each). Campaigns need sufficient budget to gather data and learn — "
                    "consider consolidating into fewer campaigns."
                )
                rag = "amber"
            elif avg_budget < 5 and len(enabled_campaigns) > 1:
                issues.append(
                    f"Average daily budget per campaign is just £{avg_budget:.2f}. "
                    "At this level smart bidding cannot learn effectively."
                )
                if rag == "green":
                    rag = "amber"

    # If no genuine structural problems surfaced, describe the structure positively
    # FIRST — so the slide validates a lean-but-appropriate setup the way our team does,
    # rather than letting the Auto-Apply note become the whole slide.
    if not issues:
        parts = []
        if search_count:
            parts.append(f"{search_count} Search")
        if pmax_count:
            parts.append(f"{pmax_count} Performance Max")
        desc = " and ".join(parts) if parts else f"{num_campaigns} campaign(s)"
        issues.append(
            f"Structure is lean and focused: {desc}. "
            "A simple structure like this is appropriate while the account gathers data "
            "and tests what works — no need to add complexity yet."
        )

    # Auto-apply recommendations — now TYPE-AWARE. The team is happy with a known set
    # of low-risk AAR types; flag only types enabled OUTSIDE that approved set.
    auto_apply = data.get("auto_apply_recommendations", None)
    auto_apply_types = data.get("auto_apply_types") or []
    if auto_apply_types:
        labelled = ", ".join(_aar_label(t) for t in auto_apply_types)
        non_approved = [t for t in auto_apply_types if t not in APPROVED_AAR_TYPES]
        if non_approved:
            # Name exactly what's enabled (self-documents on the deck) and invite review.
            # INFORMATIONAL ONLY — we do NOT escalate the RAG here: whether a given
            # auto-apply type is acceptable is a human judgement we can't make from here,
            # so flagging it as a "problem" risks a false positive. List it, let a human decide.
            issues.append(
                f"Auto-Apply is enabled for: {labelled}. Worth confirming each of these is a type "
                "you're happy to let Google change automatically — some can affect keywords, "
                "bidding, or where your ads show."
            )
        else:
            issues.append(
                f"Auto-Apply is enabled, but only for low-risk types ({labelled}) — no action needed."
            )
    elif auto_apply:
        issues.append(
            "Auto-Apply Recommendations are enabled. Worth a quick check that only recommendation "
            "types you're comfortable with are active."
        )

    # CTR check
    impressions = summary.get("impressions", 0)
    if impressions > 0:
        ctr_pct = summary.get("ctr_pct", 0)
        if ctr_pct < 1.0:
            issues.append(
                f"Overall CTR is {ctr_pct:.2f}% — below the 1% benchmark. "
                "Ad relevance or Quality Score may need improvement."
            )
            if rag == "green":
                rag = "amber"

    if not issues:
        issues.append(
            f"Account structure looks healthy — {num_campaigns} campaigns, "
            f"{num_ad_groups} ad groups."
        )

    return {
        "rag": rag,
        "headline": _as_headline(rag, num_campaigns, num_ad_groups),
        "issues": issues,
        "data_points": {
            "campaign_count": num_campaigns,
            "ad_group_count": num_ad_groups,
            "campaign_types_active": campaign_types_active,
        },
    }


def _as_headline(rag, campaigns, ad_groups):
    if rag == "red":
        return "Significant structural issues detected"
    if rag == "amber":
        return "Account structure has areas to improve"
    return f"Account structure is well-organised ({campaigns} campaigns, {ad_groups} ad groups)"


# ─────────────────────────────────────────────
# SECTION 3: TARGETING & KEYWORDS
# ─────────────────────────────────────────────

def score_targeting_keywords(data):
    issues = []
    rag = "green"
    kw_breakdown = data.get("keyword_match_breakdown", {})
    summary = data.get("account_summary_30d", {})
    campaign_types_active = data.get("campaign_types_active", [])
    audience_signals = data.get("audience_signals", [])
    location_targeting = data.get("location_targeting", [])
    campaigns = data.get("campaigns", [])

    broad = kw_breakdown.get("BROAD", {})
    phrase = kw_breakdown.get("PHRASE", {})
    exact = kw_breakdown.get("EXACT", {})
    broad_clicks = broad.get("clicks", 0)
    phrase_clicks = phrase.get("clicks", 0)
    exact_clicks = exact.get("clicks", 0)
    total_kw_clicks = broad_clicks + phrase_clicks + exact_clicks

    has_search = "SEARCH" in campaign_types_active
    has_pmax = "PERFORMANCE_MAX" in campaign_types_active

    broad_pct = 0.0  # share of keyword CLICKS on broad match (set below if data present)

    if has_search and total_kw_clicks == 0:
        issues.append(
            "No keyword click data found despite Search campaigns being active. "
            "Check that keywords are properly set up."
        )
        rag = "amber"
    elif total_kw_clicks > 0:
        broad_pct = broad_clicks / total_kw_clicks
        exact_pct = exact_clicks / total_kw_clicks

        if broad_pct > 0.6:
            issues.append(
                f"{broad_pct:.0%} of keyword clicks come from Broad Match "
                f"({broad_clicks} of {total_kw_clicks} clicks). "
                "Heavy broad match without strong negatives can waste budget on irrelevant searches."
            )
            rag = "amber"

        if exact_pct > 0.8 and broad_clicks == 0 and phrase_clicks == 0:
            issues.append(
                f"{exact_pct:.0%} of clicks come from Exact Match only — no Phrase or Broad match active. "
                "This restricts search volume and limits growth. Consider adding Phrase Match keywords."
            )
            if rag == "green":
                rag = "amber"

        if exact_clicks == 0:
            issues.append(
                "No Exact Match keyword clicks recorded. "
                "Adding exact match for core terms gives more control over spend."
            )
            if rag == "green":
                rag = "amber"

        broad_spend = broad.get("spend", 0)
        total_kw_spend = broad_spend + phrase.get("spend", 0) + exact.get("spend", 0)
        if total_kw_spend > 0:
            broad_spend_pct = broad_spend / total_kw_spend
            if broad_spend_pct > 0.7:
                issues.append(
                    f"{broad_spend_pct:.0%} of keyword spend is on Broad Match "
                    f"(£{broad_spend:.2f}). Consider shifting budget to more controlled match types."
                )
                if rag == "green":
                    rag = "amber"

    # Negative keywords
    neg_kw_count = data.get("negative_keyword_count", None)
    if neg_kw_count is not None and neg_kw_count < 50:
        issues.append(
            f"Only {neg_kw_count} negative keywords applied across the account. "
            "With broad match and PMax running, a low negative keyword count means budget is likely wasted on irrelevant searches."
        )
        if rag == "green":
            rag = "amber"

    # Escalation: heavy broad match AND weak negatives together is a RED combination —
    # the budget is wide open with little to filter waste (matches team judgement).
    if broad_pct > 0.8 and neg_kw_count is not None and neg_kw_count < 50:
        issues.append(
            "Most keyword spend is on broad match with very few negatives in place. "
            "Together these leave the budget wide open to irrelevant searches — this should be treated as urgent."
        )
        rag = "red"

    # ── Search term hygiene (uses the ALREADY-FETCHED top_search_terms) ───────
    # Mirrors the human SQR review: (a) converting queries not yet added as
    # keywords, and (b) spend on terms that aren't converting (negative candidates).
    search_terms = data.get("top_search_terms", []) or []
    converting_not_added = [
        t for t in search_terms
        if (t.get("conversions", 0) or 0) >= 1
        and str(t.get("status", "")).upper() in ("NONE", "UNKNOWN", "")
    ]
    wasted_terms = [
        t for t in search_terms
        if (t.get("conversions", 0) or 0) == 0
        and (t.get("spend", 0) or 0) >= 10
        and str(t.get("status", "")).upper() in ("NONE", "UNKNOWN", "")
    ]
    sqr_issues = []
    if converting_not_added:
        sqr_issues.append(
            f"{len(converting_not_added)} of your highest-traffic search terms are generating "
            "conversions but have not been added as keywords. Promoting proven converting queries "
            "into keywords gives more control over bids, ad copy and landing pages."
        )
        if rag == "green":
            rag = "amber"
    if wasted_terms:
        wasted_spend = round(sum((t.get("spend", 0) or 0) for t in wasted_terms), 2)
        sqr_issues.append(
            f"{len(wasted_terms)} high-traffic search terms have spent about £{wasted_spend:.2f} "
            "without converting. Reviewing these for negative keywords would cut wasted spend."
        )
        if rag == "green":
            rag = "amber"
    # Lead the section with the search-query story — for many accounts the SQR IS the
    # real issue, more than match-type distribution (practitioner feedback).
    issues[:0] = sqr_issues

    # ── RSA Ad Strength (Max's #2) ────────────────────────────────────────────
    # Ad Strength is Google's rating of how well an RSA is built. Poor/Average ads
    # win less impression share and pay higher CPCs, so weak strength is a real
    # efficiency leak — but it's a "worth improving" point, not an account emergency.
    # Stay humble: only flag when a MEANINGFUL share of live RSAs are weak, or weak
    # ads are carrying real spend. Never escalate past amber on ad strength alone.
    rsa = data.get("rsa_ad_strength") or {}
    rsa_total = rsa.get("total_rsas", 0)
    rsa_low = rsa.get("low_strength_count", 0)
    rsa_low_spend = rsa.get("low_strength_spend", 0)
    if rsa_total > 0 and rsa_low > 0:
        low_share = rsa_low / rsa_total
        if low_share >= 0.5 or rsa_low_spend >= 20:
            examples = rsa.get("low_strength_examples", [])
            eg = ""
            if examples:
                names = ", ".join(f"'{e['ad_group']}' ({e['strength']})" for e in examples[:2])
                eg = f" For example {names}."
            issues.append(
                f"{rsa_low} of {rsa_total} live responsive search ads are rated Poor or Average "
                f"ad strength, carrying about £{rsa_low_spend:.2f} of spend.{eg} "
                "Ad strength reflects how distinct and relevant the headlines and descriptions are - "
                "improving it tends to lift CTR and Quality Score."
            )
            if rag == "green":
                rag = "amber"

    # CTR check as proxy for relevance
    ctr_pct = summary.get("ctr_pct", 0)
    if ctr_pct > 0 and ctr_pct < 1.5 and has_search:
        issues.append(
            f"Search CTR is {ctr_pct:.2f}% — below the 2% benchmark. "
            "Ad copy or keyword relevance may need tightening."
        )
        if rag == "green":
            rag = "amber"

    # PMax audience signals check
    if has_pmax:
        pmax_enabled = [
            c for c in campaigns
            if c.get("type") == "PERFORMANCE_MAX" and c.get("status") == "ENABLED"
        ]
        if pmax_enabled and len(audience_signals) == 0:
            issues.append(
                "Performance Max campaign is running without audience signals. "
                "Audience signals help Google identify your ideal customer profile — "
                "without them PMax targets very broadly and learning is slower."
            )
            if rag == "green":
                rag = "amber"

    # PMax location targeting
    if has_pmax and location_targeting:
        national_indicators = ["United Kingdom", "UK", "England", "Great Britain"]
        national_targets = [
            lt for lt in location_targeting
            if any(ind.lower() in str(lt.get("location_name", "")).lower()
                   for ind in national_indicators)
        ]
        if national_targets:
            issues.append(
                "Performance Max appears to be targeting the whole UK. "
                "For local businesses this wastes budget on out-of-area traffic — "
                "narrow location targeting to your service area."
            )
            rag = "amber"

    if not issues:
        issues.append(
            f"Keyword targeting looks well-structured — "
            f"broad: {broad_clicks} clicks, phrase: {phrase_clicks}, exact: {exact_clicks}."
        )

    return {
        "rag": rag,
        "headline": _tk_headline(rag),
        "issues": issues,
        "data_points": {
            "broad_clicks": broad_clicks,
            "phrase_clicks": phrase_clicks,
            "exact_clicks": exact_clicks,
            "broad_spend_gbp": broad.get("spend", 0),
            "exact_spend_gbp": exact.get("spend", 0),
            "negative_keyword_count": neg_kw_count,
            "search_terms_converting_not_added": len(converting_not_added),
            "search_terms_wasted_count": len(wasted_terms),
            "rsa_total": rsa_total,
            "rsa_low_strength_count": rsa_low,
            "rsa_low_strength_spend_gbp": rsa_low_spend,
        },
    }


def _tk_headline(rag):
    if rag == "red":
        return "Targeting issues are costing budget"
    if rag == "amber":
        return "Keyword targeting has room for improvement"
    return "Keyword targeting is well-structured"


# ─────────────────────────────────────────────
# SECTION 4: BIDDING STRATEGY
# ─────────────────────────────────────────────

def score_bidding_strategy(data):
    issues = []
    rag = "green"
    campaigns = data.get("campaigns", [])
    summary = data.get("account_summary_30d", {})
    total_conversions = summary.get("conversions", 0)
    total_cost = summary.get("spend", 0)
    # Use the SAME 30-day CPA the deck shows on the Performance Summary table
    # (campaign-level, from get_performance_summary) so a client never sees two
    # different CPAs for the same period. Fall back to the customer-level figure.
    perf_t30 = (data.get("performance_summary", {}) or {}).get("_raw", {}).get("t30", {})
    cpa = perf_t30.get("cpa") or summary.get("cpa", 0)

    smart_bidding_strategies = {
        "TARGET_CPA", "TARGET_ROAS", "MAXIMIZE_CONVERSIONS",
        "MAXIMIZE_CONVERSION_VALUE", "TARGET_IMPRESSION_SHARE"
    }
    # Note: in the Google Ads API, "Maximise Clicks" is reported as TARGET_SPEND —
    # NOT "MAXIMIZE_CLICKS". Missing this made Max Clicks campaigns invisible to the tool.
    manual_strategies = {"MANUAL_CPC", "MANUAL_CPM", "MANUAL_CPV", "MAXIMIZE_CLICKS", "TARGET_SPEND"}

    smart_campaigns = []
    manual_campaigns = []

    for c in campaigns:
        strategy = c.get("bid_strategy", "").upper()
        name = c.get("name", "Unknown")
        status = c.get("status", "")
        if status != "ENABLED":
            continue
        if strategy in smart_bidding_strategies:
            smart_campaigns.append((name, strategy))
        elif strategy in manual_strategies:
            manual_campaigns.append((name, strategy))

    # Max Clicks / Manual CPC check
    max_clicks = [n for n, s in manual_campaigns if s in ("MAXIMIZE_CLICKS", "TARGET_SPEND")]
    true_manual = [n for n, s in manual_campaigns if s not in ("MAXIMIZE_CLICKS", "TARGET_SPEND")]

    if max_clicks:
        issues.append(
            f"{len(max_clicks)} campaign(s) on Maximise Clicks — this optimises for traffic, not conversions. "
            "Switch to Maximise Conversions to align spend with business goals."
        )
        rag = "amber"

    if true_manual:
        if total_conversions >= 30:
            issues.append(
                f"{len(true_manual)} campaign(s) still on Manual CPC despite "
                f"{total_conversions:.0f} conversions/month. "
                "Smart bidding should outperform manual at this volume."
            )
            rag = "amber"
        else:
            issues.append(
                f"{len(true_manual)} campaign(s) on Manual CPC. "
                "Once you reach 30+ conversions/month, switch to smart bidding."
            )
            if rag == "green":
                rag = "amber"

    # Inconsistent smart bidding strategies
    smart_strategy_types = set(s for _, s in smart_campaigns)
    if len(smart_strategy_types) > 1:
        issues.append(
            "Campaigns are using inconsistent bid strategies: "
            + ", ".join(smart_strategy_types) + ". "
            "Mixing strategies (e.g. Maximise Conversions and Maximise Conversion Value) "
            "sends conflicting signals — align all campaigns to the same goal."
        )
        if rag == "green":
            rag = "amber"

    # Smart bidding with low conversion volume.
    # NOTE: there is NO hard 30-50/month minimum — modern smart bidding works at
    # lower volumes; more good-quality data simply helps. (Per practitioner feedback.)
    if smart_campaigns and total_conversions < 15:
        issues.append(
            f"{len(smart_campaigns)} campaign(s) on smart bidding recorded only "
            f"{total_conversions:.0f} conversions in the last 30 days. "
            "Smart bidding optimises better with more conversion data, so improving tracking quality "
            "and conversion volume will help — there's no hard minimum, more good data just helps."
        )
        if rag == "green":
            rag = "amber"

    # tCPA set too low vs actual CPA
    if cpa and cpa > 0:
        for c in campaigns:
            if c.get("status") != "ENABLED":
                continue
            tcpa = c.get("target_cpa_gbp")
            if tcpa and tcpa > 0 and cpa > tcpa * 1.5:
                issues.append(
                    f"Campaign '{c.get('name')}' has a target CPA of £{tcpa:.2f} but actual CPA is £{cpa:.2f}. "
                    "When the target is set much lower than actual performance, Google throttles spend "
                    "chasing an unachievable goal — raise the target CPA closer to actual performance, "
                    "then reduce it incrementally once volume is stable."
                )
                if rag == "green":
                    rag = "amber"

    # CPA check
    if cpa > 0 and cpa > 150:
        issues.append(
            f"Cost per conversion is £{cpa:.2f}. "
            "Verify this aligns with the client's target CPA."
        )
        if rag == "green":
            rag = "amber"

    # ── Paused campaigns with strong historic CPA (Max's Issue #3) ────────────
    # If a campaign was paused despite historically converting more cheaply than the
    # account currently does, that's worth a look — budget may have shifted to pricier
    # conversions. Stay humble (the human audit does too): the pause may have been a
    # lead-quality call we can't see from the data, so recommend REVIEW, not blind
    # reactivation. Only fires with meaningful historic volume. Never escalates past amber.
    paused_hist = data.get("paused_campaign_history") or []
    efficient_paused = [
        p for p in paused_hist
        if (p.get("conversions", 0) or 0) >= 5
        and p.get("cpa") and cpa and p["cpa"] < cpa
    ]
    if efficient_paused:
        efficient_paused.sort(key=lambda p: p["cpa"])
        names = ", ".join(
            f"'{p['name']}' (historic CPA £{p['cpa']:.2f}, {int(round(p['conversions']))} conv)"
            for p in efficient_paused[:3]
        )
        issues.append(
            f"{len(efficient_paused)} paused campaign(s) historically converted below the account's "
            f"current £{cpa:.2f} CPA: {names}. From the account data alone there's no clear sign they "
            "underperformed on cost - worth checking whether lead quality, not cost, drove the pause "
            "before deciding on reactivation."
        )
        if rag == "green":
            rag = "amber"

    # Zero conversions with spend
    if total_conversions == 0 and total_cost > 50:
        issues.append(
            f"£{total_cost:.2f} spent with 0 conversions. "
            "Resolve conversion tracking before optimising bidding strategy."
        )
        rag = "red"

    if not issues:
        issues.append(
            f"Bidding strategy is appropriate — {len(smart_campaigns)} smart bidding campaign(s), "
            f"CPA £{cpa:.2f}."
        )

    return {
        "rag": rag,
        "headline": _bs_headline(rag, len(smart_campaigns), len(manual_campaigns)),
        "issues": issues,
        "data_points": {
            "smart_bidding_campaigns": len(smart_campaigns),
            "manual_cpc_campaigns": len(manual_campaigns),
            "total_conversions_30d": total_conversions,
            "cpa_gbp": cpa,
            "paused_efficient_count": len(efficient_paused),
        },
    }


def _bs_headline(rag, smart, manual):
    if rag == "red":
        return "Bidding cannot be assessed — fix conversion tracking first"
    if rag == "amber":
        return "Bidding strategy has room to improve"
    return f"Bidding strategy is appropriate ({smart} smart bidding, {manual} manual)"


# ─────────────────────────────────────────────
# SUMMARY STATS
# ─────────────────────────────────────────────

def build_summary_stats(data):
    s = data.get("account_summary_30d", {})
    return {
        "clicks": s.get("clicks", 0),
        "impressions": s.get("impressions", 0),
        "conversions": s.get("conversions", 0),
        "spend_gbp": s.get("spend", 0),
        "ctr_pct": s.get("ctr_pct", 0),
        "avg_cpc_gbp": s.get("avg_cpc", 0),
        "cpa_gbp": s.get("cpa", 0),
    }


# ─────────────────────────────────────────────
# TEST
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys, os
    sys.path.insert(0, os.path.dirname(__file__))
    from fetch_account_data import fetch_account_data
    TEST_CID = "981-476-6301"
    print(f"Fetching data for CID {TEST_CID}...")
    account_data = fetch_account_data(TEST_CID)
    print("Running analysis...")
    findings = analyse_account(account_data)
    for section, result in findings.items():
        if section == "summary_stats":
            print(f"\n{'='*50}")
            print("SUMMARY STATS")
            for k, v in result.items():
                print(f"  {k}: {v}")
        else:
            print(f"\n{'='*50}")
            print(f"SECTION: {section.upper().replace('_',' ')}")
            print(f"  RAG:      {result['rag'].upper()}")
            print(f"  Headline: {result['headline']}")
            print(f"  Issues:")
            for issue in result["issues"]:
                print(f"    • {issue}")
            print(f"  Data: {result['data_points']}")
