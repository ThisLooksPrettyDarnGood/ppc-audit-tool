# analyse_account.py
# Step 4: Analysis & Scoring Engine

# ── Auto-Apply Recommendation types the team is HAPPY to leave ON ─────────────
# Source: Max's Auto-Apply screen (the red-boxed items the team approves of).
# Verified against the official Google Ads API RecommendationType enum (v24).
# KEY FINDING: the team's approved "maintenance" toggles  -  Remove conflicting/
# redundant/non-serving keywords and Upgrade conversion tracking  -  are NOT in the
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
        "efficiency":          score_efficiency(data),
        "strengths":           build_strengths(data),
        "summary_stats":       build_summary_stats(data),
        "account_type":        account_type,
        "performance_summary": data.get("performance_summary", {}),
    }
    return findings


# ─────────────────────────────────────────────────────────────────────────────
# ISSUE-LED SELECTION LAYER
# The 4 scorers above produce all the diagnostics. A human auditor doesn't present
# 4 fixed category slides  -  they pick the most important PROBLEMS and lead with them.
# This layer turns the scorers' findings into a ranked, flat list of discrete issues
# so the deck can be issue-led (top-N named problems, one per slide). The scorers are
# left completely untouched  -  this only re-organises and prioritises their output.
# ─────────────────────────────────────────────────────────────────────────────

# (needle in the finding text, severity, per-issue RAG, slide category)
# Order matters: the first matching signature wins, so put the most severe / most
# specific needles first. Severity ~ how much a human auditor would lead with it.
_ISSUE_SIGNATURES = [
    # Critical  -  account fundamentally not working
    ("No conversion actions found",              130, "red",       "Conversion Tracking"),
    ("recorded 0 conversions in the last 30",    122, "red",       "Conversion Tracking"),
    ("spent with 0 conversions",                 116, "red",       "Bidding Strategy"),
    ("should be treated as urgent",              112, "red",       "Targeting & Keywords"),  # broad + weak negatives combo
    # On the cusp
    ("primary conversion but is recording no conversions", 52, "amber", "Conversion Tracking"),  # latent: set but not firing
    ("Low-value conversion action",               82, "amber_red", "Conversion Tracking"),
    # Bidding
    ("on Maximise Clicks",                         78, "amber",     "Bidding Strategy"),
    ("paused campaign(s) historically delivered",  66, "amber",     "Bidding Strategy"),
    ("still on Manual CPC despite",                62, "amber",     "Bidding Strategy"),
    ("on Manual CPC.",                             58, "amber",     "Bidding Strategy"),
    ("has a target CPA of",                        55, "amber",     "Bidding Strategy"),
    ("on smart bidding recorded only",             50, "amber",     "Bidding Strategy"),
    ("using inconsistent bid strategies",          46, "amber",     "Bidding Strategy"),
    ("Cost per conversion is",                     48, "amber",     "Bidding Strategy"),
    # Efficiency / coverage / settings (expert checks)
    ("use the 'Presence or interest' location",    76, "amber",     "Budget & Coverage"),  # #1 local waste leak
    ("opted into",                                 62, "amber",     "Budget & Coverage"),  # Search Partners / Display
    ("are capped by budget",                       66, "amber",     "Budget & Coverage"),  # IS lost to budget
    ("losing a large share of impressions to Ad Rank", 58, "amber", "Ad Rank & Quality"),  # IS lost to rank
    ("missing high-value extension types",         60, "amber",     "Ads & Assets"),       # missing extensions
    ("have a LOW score (4 or below)",              54, "amber",     "Ad Rank & Quality"),  # low Quality Score
    # Targeting & keywords
    ("Fading winner spotted by comparing",         72, "amber",     "Targeting & Keywords"),  # cross-window pattern (high value)
    ("without converting",                         63, "amber",     "Targeting & Keywords"),  # wasted SQR spend
    ("are NOT added as active keywords",           68, "amber",     "Targeting & Keywords"),  # converting queries (high-value "dropped ball")
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
    ("offline conversion imports (OCI)",           70, "amber",     "Conversion Tracking"),  # the elephant (lead gen) - paramount
    ("imported from GA4",                          56, "amber",     "Conversion Tracking"),
    ("still use last-click attribution",           50, "amber",     "Conversion Tracking"),
    ("being picked up by non-brand campaigns",     48, "amber",     "Targeting & Keywords"),  # brand leakage
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
    "is appropriate  - ", "set up and recording", "well-organised", "no action needed",
)

# Findings that are really the same story  -  keep only the highest-severity one so the
# deck doesn't show two near-identical slides (e.g. broad-match by clicks AND by spend).
_ISSUE_THEMES = {
    "of keyword clicks come from Broad Match": "broad_match",
    "of keyword spend is on Broad Match":      "broad_match",
    # Low negatives is flagged by both the tracking and targeting checks  -  one slide.
    "negative keyword(s) found across":        "negatives",
    "negative keywords applied across":        "negatives",
    # Per-campaign "spent with 0 conversions"  -  roll up to a single slide.
    "with 0 conversions recorded":             "zero_conv_campaign",
}


def _theme_for(detail):
    for needle, theme in _ISSUE_THEMES.items():
        if needle in detail:
            return theme
    return None

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


def select_top_issues(findings, max_issues=6, apply_floor=True):
    """Flatten the 4 scorers' findings into a ranked list of discrete issues,
    most important first, capped at max_issues. Each item:
        {detail, category, rag, severity}
    apply_floor=False returns the full ranked list (used for the internal email summary).
    """
    section_map = {
        "conversion_tracking": "Conversion Tracking",
        "account_structure":   "Account Structure",
        "targeting_keywords":  "Targeting & Keywords",
        "bidding_strategy":    "Bidding Strategy",
        "efficiency":          "Budget & Coverage",
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
                "theme": _theme_for(detail),
            })
    flat.sort(key=lambda x: x["severity"], reverse=True)
    # Drop lower-severity duplicates of the same theme (keep the first / strongest).
    seen_themes, deduped = set(), []
    for item in flat:
        th = item.get("theme")
        if th and th in seen_themes:
            continue
        if th:
            seen_themes.add(th)
        deduped.append(item)

    # Selection discipline (like a human auditor): if there's already a solid set of
    # high-impact issues, don't pad the deck with low-octane hygiene points. Keep all
    # "strong" issues (>= STRONG_FLOOR); only fall back to weaker ones to reach a minimum.
    STRONG_FLOOR, MIN_ISSUES = 55, 4
    if not apply_floor:
        return deduped[:max_issues]
    strong = [i for i in deduped if i["severity"] >= STRONG_FLOOR]
    chosen = strong if len(strong) >= MIN_ISSUES else deduped
    return chosen[:max_issues]


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
        issues.append("No conversion actions found  -  tracking is not set up.")
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
                        f"Conversion rate is {cvr:.2%}  -  unusually low. "
                        "Check for tracking gaps or low-quality traffic."
                    )
                    if rag == "green":
                        rag = "amber"

        # Only ACTIVE conversion actions matter  -  inactive/hidden ones aren't being
        # used by the account, so don't flag them at all (practitioner feedback).
        active_actions = [ca for ca in conversion_actions if ca.get("status") == "ENABLED"]
        # Count only PRIMARY actions (the ones bidding actually optimises towards).
        primary_actions = [ca for ca in active_actions if ca.get("include_in_conversions")]
        if len(primary_actions) > 10:
            issues.append(
                f"{len(primary_actions)} conversion actions are set as primary 'Conversions' that "
                "bidding optimises towards. Too many primary actions can dilute reporting and confuse "
                "bidding  -  review for duplicates, test tags or low-value actions."
            )
            if rag == "green":
                rag = "amber"

        # GA4 import detection  -  no native tag snippet = likely imported from GA4
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
                "for bidding  -  it's worth confirming Enhanced Conversions is active and that tracking "
                "is firing correctly."
            )
            if rag == "green":
                rag = "amber"

        # Spammable/low-value categories set as primary optimisation goal
        spammable_categories = {"PAGE_VIEW", "ENGAGEMENT", "DOWNLOAD"}
        _lowval_plain = {"PAGE_VIEW": "a page-view action", "ENGAGEMENT": "an engagement action",
                         "DOWNLOAD": "a download action"}
        primary_spammable = [
            (ca.get("name", "Unknown"), ca.get("category", ""), ca.get("conversions_30d"))
            for ca in active_actions
            if ca.get("include_in_conversions")
            and ca.get("category", "") in spammable_categories
        ]
        if primary_spammable:
            plain = ", ".join(_lowval_plain.get(c, "a low-value action") for _, c, _v in primary_spammable)
            vols = [v for _, _, v in primary_spammable]
            recording = any((v is not None and v > 0) for v in vols)
            all_known_zero = bool(vols) and all((v is not None and v == 0) for v in vols)
            if recording:
                # It's primary AND actually firing → it genuinely is skewing bidding.
                issues.append(
                    f"Low-value conversion action set as a primary 'Conversions' goal that bidding optimises "
                    f"towards, and it is actively recording conversions: {plain}. In plain terms, Google is "
                    "counting low-value website activity - someone simply viewing a page, not making an "
                    "enquiry - as a 'lead', so budget is steered towards activity rather than the genuine "
                    "enquiries that create revenue."
                )
                if rag != "red":
                    rag = "amber_red"
            elif all_known_zero:
                # Primary but recording nothing → a latent misconfiguration, not active harm.
                # Don't over-claim that Google "is" optimising towards it.
                issues.append(
                    f"A low-value action is set as a primary conversion but is recording no conversions in the "
                    f"last 30 days: {plain}. It isn't skewing bidding right now, but it should be moved to "
                    "secondary so it never can - and a low-value action sitting in the primary 'Conversions' "
                    "column is a sign the conversion setup needs a tidy-up."
                )
                if rag == "green":
                    rag = "amber"
            else:
                # Volume unknown (per-action query unavailable) → stay cautious, don't over-claim.
                issues.append(
                    f"Low-value conversion action set as a primary 'Conversions' goal: {plain}. It's worth "
                    "confirming whether it is currently recording conversions: if it is, Google optimises "
                    "towards low-value website activity (a page view, not an enquiry) rather than genuine "
                    "leads. Either way, a low-value action shouldn't sit in the primary 'Conversions' column."
                )
                if rag != "red":
                    rag = "amber_red"

        # Conversion count type  -  MANY_PER_CLICK on lead gen actions inflates numbers
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
                "For most lead actions 'Once' is more accurate (calls can be a fair exception)  -  "
                "worth confirming these are counting the way you intend."
            )
            if rag == "green":
                rag = "amber"

        # Attribution model  -  last-click is outdated; data-driven is Google's recommended default.
        # IMPORTANT: ignore map/directions actions (low-value, often junk for a business like this)
        # and only flag if a real action is ACTUALLY recording conversions on last-click. If every
        # last-click action recorded 0 conversions, there's nothing being mis-attributed - suppress
        # it (a 0-conv last-click action is just legacy clutter, not a live attribution problem).
        def _is_map(ca):
            blob = (str(ca.get("type", "")) + " " + str(ca.get("name", ""))).upper()
            return "MAP" in blob or "DIRECTION" in blob
        last_click_firing = [
            ca for ca in active_actions
            if ca.get("include_in_conversions")
            and ca.get("attribution_model") == "GOOGLE_ADS_LAST_CLICK"
            and not _is_map(ca)
            and (ca.get("conversions_30d") or 0) > 0
        ]
        if last_click_firing:
            parts, any_call = [], False
            for ca in last_click_firing:
                is_call = "CALL" in str(ca.get("type", "")).upper()
                any_call = any_call or is_call
                conv = int(round(ca.get("conversions_30d") or 0))
                parts.append(f"'{ca.get('name','Unknown')}'" + (" (a call action)" if is_call else "")
                             + f" - {conv} conv in 30d")
            call_note = (" Several of these are call actions, where the journey often spans several "
                         "visits, so last-click especially undervalues them.") if any_call else ""
            issues.append(
                f"{len(last_click_firing)} primary conversion action(s) that ARE recording conversions "
                f"still use last-click attribution: {', '.join(parts)}.{call_note} Last-click credits only "
                "the final click and ignores the earlier searches that helped create the enquiry, so smart "
                "bidding optimises on a partial picture. Switching them to data-driven attribution "
                "(Google's recommended default) lets bidding value the whole path to an enquiry."
            )
            if rag == "green":
                rag = "amber"

        # Offline Conversion Imports (OCI)  -  the elephant for lead gen. If enquiries that became
        # real jobs/sales aren't imported back, bidding optimises towards form fills, not revenue.
        OCI_TYPES = {"UPLOAD_CLICKS", "UPLOAD_CALLS", "STORE_SALES", "STORE_SALES_DIRECT_UPLOAD"}
        has_oci = any(str(ca.get("type", "")) in OCI_TYPES for ca in conversion_actions)
        if not has_oci and detect_account_type(data) in ("lead_gen", "unknown"):
            issues.append(
                "We checked for offline conversion imports (OCI) and could not find any set up. For a lead "
                "generation business this is one of the biggest opportunities there is: importing which "
                "enquiries actually became booked jobs or sales back into Google Ads (using the click ID "
                "and a simple CRM export - even a spreadsheet works) lets smart bidding optimise towards "
                "real revenue, not just form fills. In today's expensive paid media, feeding back genuine "
                "lead quality is often the single highest-leverage change an account can make."
            )
            if rag == "green":
                rag = "amber"

    # Campaigns spending with zero conversions. Skip awareness-style campaigns  - 
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
                f"Campaign '{c_name}' spent £{c_cost:.2f} with 0 conversions recorded  -  "
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
            f"Conversion tracking is healthy  -  {len(conversion_actions)} actions "
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
                    f"(avg £{avg_budget:.2f} each). Campaigns need sufficient budget to gather data and learn  -  "
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
    # FIRST  -  so the slide validates a lean-but-appropriate setup the way our team does,
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
            "and tests what works  -  no need to add complexity yet."
        )

    # Auto-apply recommendations  -  now TYPE-AWARE. The team is happy with a known set
    # of low-risk AAR types; flag only types enabled OUTSIDE that approved set.
    auto_apply = data.get("auto_apply_recommendations", None)
    auto_apply_types = data.get("auto_apply_types") or []
    if auto_apply_types:
        labelled = ", ".join(_aar_label(t) for t in auto_apply_types)
        non_approved = [t for t in auto_apply_types if t not in APPROVED_AAR_TYPES]
        if non_approved:
            # Name exactly what's enabled (self-documents on the deck) and invite review.
            # INFORMATIONAL ONLY  -  we do NOT escalate the RAG here: whether a given
            # auto-apply type is acceptable is a human judgement we can't make from here,
            # so flagging it as a "problem" risks a false positive. List it, let a human decide.
            issues.append(
                f"Auto-Apply is enabled for: {labelled}. Worth confirming each of these is a type "
                "you're happy to let Google change automatically  -  some can affect keywords, "
                "bidding, or where your ads show."
            )
        else:
            issues.append(
                f"Auto-Apply is enabled, but only for low-risk types ({labelled})  -  no action needed."
            )
    elif auto_apply:
        issues.append(
            "Auto-Apply Recommendations are enabled. Worth a quick check that only recommendation "
            "types you're comfortable with are active."
        )

    # CTR check
    impressions = summary.get("impressions", 0)
    if impressions > 0:
        ctr_pct = summary.get("ctr_pct", 0) or 0
        if ctr_pct < 1.0:
            issues.append(
                f"Overall CTR is {ctr_pct:.2f}%  -  below the 1% benchmark. "
                "Ad relevance or Quality Score may need improvement."
            )
            if rag == "green":
                rag = "amber"

    if not issues:
        issues.append(
            f"Account structure looks healthy  -  {num_campaigns} campaigns, "
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
                f"{exact_pct:.0%} of clicks come from Exact Match only  -  no Phrase or Broad match active. "
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

    # Escalation: heavy broad match AND weak negatives together is a RED combination  - 
    # the budget is wide open with little to filter waste (matches team judgement).
    if broad_pct > 0.8 and neg_kw_count is not None and neg_kw_count < 50:
        issues.append(
            "Most keyword spend is on broad match with very few negatives in place. "
            "Together these leave the budget wide open to irrelevant searches  -  this should be treated as urgent."
        )
        rag = "red"

    # ── Search term hygiene (uses the ALREADY-FETCHED top_search_terms) ───────
    # Mirrors the human SQR review: (a) converting queries not yet added as
    # keywords, and (b) spend on terms that aren't converting (negative candidates).
    search_terms = data.get("top_search_terms", []) or []

    # ── Brand vs non-brand: a top auditor never lets the client's OWN brand name be
    # presented as "proven new demand" - brand is cheap and already theirs. Derive brand
    # token(s) from the account name and exclude them from the SQR analysis below.
    _generic = {"ltd", "limited", "pool", "pools", "leisure", "group", "services", "company",
                "uk", "the", "ads", "account", "marketing", "co", "and"}
    brand_tokens = [w.lower() for w in str(data.get("account_name", "")).split()
                    if len(w) > 3 and w.lower() not in _generic]

    def _is_brand(term):
        t = str(term).lower()
        return any(tok in t for tok in brand_tokens)

    # Prefer the dedicated 90-day query (catches winners that have tailed off / a page or
    # keyword change quietly stopped capturing) and fall back to the 30-day top terms.
    dedicated_converting = data.get("converting_unkeyworded_terms")
    if dedicated_converting is not None:
        converting_not_added = dedicated_converting
    else:
        converting_not_added = [
            t for t in search_terms
            if (t.get("conversions", 0) or 0) >= 1
            and str(t.get("status", "")).upper() in ("NONE", "UNKNOWN", "")
        ]
    # Drop the client's own brand terms from the converting list (not new demand).
    if brand_tokens:
        converting_not_added = [t for t in converting_not_added if not _is_brand(t.get("term", ""))]
    # ── Fading winners: terms that CONVERTED over 90 days but have gone quiet in the
    # last 30 (spend, no leads). This isn't a contradiction - it's the cross-window
    # pattern a good auditor hunts for (a page rename, a bid drop, a competitor moving in).
    # We surface it explicitly with BOTH windows labelled, and pull these terms out of the
    # plain converting/wasted lists so each term tells its richest single story.
    _30d_by_term = {str(t.get("term", "")).strip().lower(): t for t in search_terms}
    fading_winners = []
    for ct in converting_not_added:
        nm = str(ct.get("term", "")).strip().lower()
        recent = _30d_by_term.get(nm)
        if recent and (recent.get("conversions", 0) or 0) == 0 and (recent.get("spend", 0) or 0) >= 10:
            fading_winners.append({
                "term": ct.get("term", "?"),
                "conv_90d": ct.get("conversions", 0) or 0,
                "spend_30d": recent.get("spend", 0) or 0,
            })
    _fading_names = {str(f["term"]).strip().lower() for f in fading_winners}

    # Plain converting list excludes the fading winners (they get their own richer finding).
    converting_not_added = [
        ct for ct in converting_not_added
        if str(ct.get("term", "")).strip().lower() not in _fading_names
    ]
    # A converting OR fading term must never also be called a "wasted, no-lead" term.
    _converting_names = {str(t.get("term", "")).strip().lower() for t in converting_not_added} | _fading_names
    wasted_terms = [
        t for t in search_terms
        if (t.get("conversions", 0) or 0) == 0
        and (t.get("spend", 0) or 0) >= 10
        and str(t.get("status", "")).upper() in ("NONE", "UNKNOWN", "")
        and str(t.get("term", "")).strip().lower() not in _converting_names
        and not _is_brand(t.get("term", ""))
    ]
    # The same search term can appear on several ad groups (separate rows)  -  aggregate by
    # term so counts and examples don't double-count (e.g. 'giles pool lewes' twice).
    def _agg_by_term(terms):
        agg = {}
        for t in terms:
            k = str(t.get("term", "")).strip().lower()
            if not k:
                continue
            a = agg.setdefault(k, {"term": t.get("term"), "spend": 0.0, "conversions": 0.0,
                                   "status": t.get("status", "NONE")})
            a["spend"] += t.get("spend", 0) or 0
            a["conversions"] += t.get("conversions", 0) or 0
        return list(agg.values())

    converting_not_added = _agg_by_term(converting_not_added)
    wasted_terms = _agg_by_term(wasted_terms)

    sqr_issues = []
    if fading_winners:
        f = max(fading_winners, key=lambda x: x["spend_30d"])
        c90 = int(round(f["conv_90d"]))
        sqr_issues.append(
            f"Fading winner spotted by comparing time windows: '{f['term']}' generated {c90} "
            f"lead{'s' if c90 != 1 else ''} over the LAST 90 DAYS, but in the LAST 30 DAYS it has spent "
            f"about £{f['spend_30d']:.0f} with no leads. A proven term going quiet like this usually means "
            "something changed - a page rename, a dropped bid, or a competitor moving in. Catching it needs "
            "exactly this 30 vs 90-day comparison, and it's where quietly dropped balls are recovered."
        )
        if rag == "green":
            rag = "amber"
    if converting_not_added:
        # Name the top converting terms with their leads + cost-per-lead so the slide is concrete.
        _top_conv = sorted(converting_not_added, key=lambda t: (t.get("conversions", 0) or 0), reverse=True)[:3]
        _egs = []
        for t in _top_conv:
            conv = t.get("conversions", 0) or 0
            spend = t.get("spend", 0) or 0
            cpl = f" at ~£{round(spend / conv)} per lead" if conv else ""
            _egs.append(f"'{t.get('term', '?')}' ({int(round(conv))} lead{'s' if round(conv) != 1 else ''}{cpl})")
        eg_text = (" For example " + ", ".join(_egs) + ".") if _egs else ""
        sqr_issues.append(
            f"{len(converting_not_added)} search terms have generated conversions over the last 90 days "
            f"but are NOT added as active keywords.{eg_text} Proven, money-making demand is being captured "
            "loosely (or not at all) rather than controlled directly. Promoting these into dedicated "
            "keywords gives control over bids, ad copy and landing pages."
        )
        if rag == "green":
            rag = "amber"
    if wasted_terms:
        wasted_spend = round(sum((t.get("spend", 0) or 0) for t in wasted_terms), 2)
        _top_waste = sorted(wasted_terms, key=lambda t: (t.get("spend", 0) or 0), reverse=True)[:3]
        _weg = ", ".join(f"'{t.get('term', '?')}' (£{round(t.get('spend', 0) or 0)})" for t in _top_waste)
        _weg_text = (f" The biggest are {_weg}.") if _weg else ""
        sqr_issues.append(
            f"{len(wasted_terms)} high-traffic search terms have spent about £{wasted_spend:.2f} "
            f"in the last 30 days without converting.{_weg_text} Reviewing these for negative keywords "
            "would cut wasted spend - and some may be competitor or brand names being matched by broad "
            "keywords without you realising, which is a common and costly leak."
        )
        if rag == "green":
            rag = "amber"
    # ── Quality Score (we already fetch it  -  now we use it) ───────────────────
    qs_list = data.get("quality_scores") or []
    scored = [q for q in qs_list if q.get("qs")]
    low_qs = [q for q in scored if (q.get("qs") or 10) <= 4]
    if scored and len(low_qs) >= max(5, round(0.25 * len(scored))):
        worst = sorted(low_qs, key=lambda q: q.get("qs", 10))[:3]
        egs = ", ".join(f"'{q.get('keyword', '?')}' (QS {q.get('qs')})" for q in worst)
        # Roll up the most common weak component (ad relevance / landing page / expected CTR).
        from collections import Counter as _C
        weak_parts = _C()
        for q in low_qs:
            for part, key in (("ad relevance", "ad_relevance"), ("landing page experience", "landing_page"),
                              ("expected CTR", "expected_ctr")):
                if str(q.get(key, "")).upper().startswith(("BELOW", "BELOW_AVERAGE")):
                    weak_parts[part] += 1
        driver = weak_parts.most_common(1)[0][0] if weak_parts else "ad relevance and landing pages"
        sqr_issues.append(
            f"{len(low_qs)} of {len(scored)} keywords have a LOW Quality Score (4 or below), e.g. {egs}. "
            f"Low Quality Score means you pay more per click and rank lower for the same bid - the most "
            f"common weak point here is {driver}. Tightening keyword-to-ad relevance, landing page "
            "experience and grouping keywords into tighter themes lifts Quality Score and lowers CPCs."
        )
        if rag == "green":
            rag = "amber"

    # ── Brand leaking into non-brand campaigns (missing brand/non-brand separation) ──
    leak = data.get("brand_leakage") or []
    material_leak = [l for l in leak if (l.get("spend", 0) or 0) >= 1]
    if material_leak and brand_tokens:
        names = ", ".join(f"the '{l['campaign']}' campaign (£{l['spend']:.0f})" for l in material_leak[:3])
        conv_total = sum((l.get("conversions", 0) or 0) for l in material_leak)
        conv_note = (f" and have produced {int(round(conv_total))} of your reported conversions"
                     if conv_total >= 1 else "")
        sqr_issues.append(
            f"Your own brand searches (for '{brand_tokens[0]}') are being picked up by non-brand "
            f"campaigns - {names}{conv_note} - rather than only a dedicated Brand campaign. It is small "
            "money on its own, but it shows brand isn't excluded as a negative in those campaigns, so "
            "brand and non-brand performance get blended in reporting and brand traffic quietly flatters "
            "non-brand numbers over time. Adding your brand name as a negative keyword in the non-brand "
            "campaigns keeps each campaign's data clean and is the kind of detail a well-run account gets right."
        )
        if rag == "green":
            rag = "amber"

    # Lead the section with the search-query story  -  for many accounts the SQR IS the
    # real issue, more than match-type distribution (practitioner feedback).
    issues[:0] = sqr_issues

    # ── RSA Ad Strength (Max's #2) ────────────────────────────────────────────
    # Ad Strength is Google's rating of how well an RSA is built. Poor/Average ads
    # win less impression share and pay higher CPCs, so weak strength is a real
    # efficiency leak  -  but it's a "worth improving" point, not an account emergency.
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
                names = ", ".join(f"the '{e['ad_group']}' ad group ({e['strength']})" for e in examples[:2])
                eg = f" For example {names}."
            # Show the weak-ad spend as a share of total spend so it's easy to weigh.
            total_spend = (data.get("account_summary_30d", {}) or {}).get("spend", 0) or 0
            pct = f" - around {round(rsa_low_spend / total_spend * 100)}% of total account spend" if total_spend else ""
            issues.append(
                f"{rsa_low} of {rsa_total} live responsive search ads are rated Poor or Average "
                f"ad strength, carrying about £{rsa_low_spend:.2f} of spend{pct}.{eg} "
                "Ad strength reflects how distinct and relevant the headlines and descriptions are - "
                "improving it tends to lift CTR and Quality Score."
            )
            if rag == "green":
                rag = "amber"

    # CTR check as proxy for relevance (ctr_pct is None when there are 0 impressions)
    ctr_pct = summary.get("ctr_pct", 0) or 0
    if ctr_pct > 0 and ctr_pct < 1.5 and has_search:
        issues.append(
            f"Search CTR is {ctr_pct:.2f}%  -  below the 2% benchmark. "
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
                "Audience signals help Google identify your ideal customer profile  -  "
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
                "For local businesses this wastes budget on out-of-area traffic  -  "
                "narrow location targeting to your service area."
            )
            rag = "amber"

    if not issues:
        issues.append(
            f"Keyword targeting looks well-structured  -  "
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
    # Note: in the Google Ads API, "Maximise Clicks" is reported as TARGET_SPEND  - 
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
            f"{len(max_clicks)} campaign(s) on Maximise Clicks  -  this optimises for traffic, not conversions. "
            "Two things worth checking: whether it's a newer or low-data campaign that hasn't moved on yet, "
            "and - importantly - whether a maximum CPC bid limit is set. Without a sensible CPC ceiling, "
            "Maximise Clicks can pay far more per click than needed. Once there is enough conversion data, "
            "move it to Maximise Conversions so spend is aligned with leads, not visits."
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
            "sends conflicting signals  -  align all campaigns to the same goal."
        )
        if rag == "green":
            rag = "amber"

    # Smart bidding with low conversion volume.
    # NOTE: there is NO hard 30-50/month minimum  -  modern smart bidding works at
    # lower volumes; more good-quality data simply helps. (Per practitioner feedback.)
    if smart_campaigns and total_conversions < 15:
        issues.append(
            f"{len(smart_campaigns)} campaign(s) on smart bidding recorded only "
            f"{total_conversions:.0f} conversions in the last 30 days. "
            "Smart bidding optimises better with more conversion data, so improving tracking quality "
            "and conversion volume will help  -  there's no hard minimum, more good data just helps."
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
                    "chasing an unachievable goal  -  raise the target CPA closer to actual performance, "
                    "then reduce it incrementally once volume is stable."
                )
                if rag == "green":
                    rag = "amber"

    # CPA check (cpa is None when there are 0 conversions  -  guard against it)
    if cpa and cpa > 150:
        issues.append(
            f"Cost per conversion is £{cpa:.2f}. "
            "Verify this aligns with the client's target CPA."
        )
        if rag == "green":
            rag = "amber"

    # ── Paused campaigns with strong historic CPA (Max's Issue #3) ────────────
    # If a campaign was paused despite historically converting more cheaply than the
    # account currently does, that's worth a look  -  budget may have shifted to pricier
    # conversions. Stay humble (the human audit does too): the pause may have been a
    # lead-quality call we can't see from the data, so recommend REVIEW, not blind
    # reactivation. Only fires with meaningful historic volume. Never escalates past amber.
    paused_hist = data.get("paused_campaign_history") or []

    def _is_efficient(p):
        # Prefer the GENUINE-lead CPA (conversion-quality dig). A campaign only counts as
        # efficient if it produced real enquiries below the current CPA - not page-view /
        # engagement "conversions". Fall back to total CPA only if quality data is missing.
        g = p.get("genuine_conv")
        rc = p.get("real_cpa")
        if g is not None and g >= 5:
            return bool(rc and cpa and rc < cpa)
        if p.get("genuine_pct") is None:   # quality data unavailable → old behaviour
            return (p.get("conversions", 0) or 0) >= 5 and p.get("cpa") and cpa and p["cpa"] < cpa
        return False

    efficient_paused = [p for p in paused_hist if _is_efficient(p)]
    if efficient_paused:
        efficient_paused.sort(key=lambda p: (p.get("real_cpa") or p.get("cpa") or 1e9))
        descs = []
        for p in efficient_paused[:3]:
            rc, g, gp = p.get("real_cpa"), p.get("genuine_conv"), p.get("genuine_pct")
            if rc and g:
                d = f"the '{p['name']}' campaign (£{rc:.0f} per genuine lead from {int(round(g))} real enquiries"
                if gp is not None and gp < 70:
                    d += (f"; note only {gp:.0f}% of its tracked conversions were genuine leads, so its "
                          f"headline CPA of £{p.get('cpa', 0):.0f} flatters it")
                d += ")"
            else:
                d = f"the '{p['name']}' campaign (historic CPA £{p.get('cpa', 0):.2f}, {int(round(p.get('conversions', 0)))} conv)"
            descs.append(d)
        names = ", ".join(descs)

        _have_quality = [p for p in efficient_paused[:3] if p.get("genuine_pct") is not None]
        if _have_quality and all((p.get("genuine_pct") or 0) >= 70 for p in _have_quality):
            verify = (" We checked the conversion quality: these were genuine leads (form fills, calls and "
                      "contacts), not page views or engagement actions - so this is real efficient activity "
                      "that was switched off, not vanity metrics.")
        else:
            verify = (" Worth confirming the conversion quality before reactivating - some of the apparent "
                      "efficiency leans on low-value actions (page views, engagement) rather than genuine enquiries.")

        issues.append(
            f"{len(efficient_paused)} paused campaign(s) historically delivered genuine leads below the "
            f"account's current £{cpa:.0f} CPA: {names}.{verify} Worth reviewing whether lead quality, not "
            "cost, drove the pause before deciding on reactivation."
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
        cpa_note = f", CPA £{cpa:.2f}" if cpa else ""
        issues.append(
            f"Bidding strategy is appropriate  -  {len(smart_campaigns)} smart bidding campaign(s)"
            f"{cpa_note}."
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
        return "Bidding cannot be assessed  -  fix conversion tracking first"
    if rag == "amber":
        return "Bidding strategy has room to improve"
    return f"Bidding strategy is appropriate ({smart} smart bidding, {manual} manual)"


# ─────────────────────────────────────────────
# SUMMARY STATS
# ─────────────────────────────────────────────

def score_efficiency(data):
    """
    Expert checks that sit outside the original 4 sections: impression share lost
    (budget vs rank), location targeting setting, and ad-extension coverage. Each is a
    standard senior-auditor check. All inputs may be None (live query failed) → skip safely.
    """
    issues = []
    rag = "green"
    campaigns = data.get("campaigns", [])
    account_type = detect_account_type(data)
    AWARENESS = {"DISPLAY", "VIDEO", "DEMAND_GEN", "MULTI_CHANNEL"}

    # ── Impression share lost to BUDGET (capped campaigns) ────────────────────
    isl = data.get("impression_share_lost") or []
    # Pair with conversions so we only push budget where it actually converts.
    conv_by_name = {c.get("name"): (c.get("conversions_30d", 0) or 0) for c in campaigns}
    budget_capped = [c for c in isl if (c.get("lost_budget", 0) or 0) >= 10]
    if budget_capped:
        budget_capped.sort(key=lambda c: c.get("lost_budget", 0), reverse=True)
        names = ", ".join(f"'{c['campaign']}' (losing {c['lost_budget']:.0f}% to budget)"
                          for c in budget_capped[:3])
        issues.append(
            f"{len(budget_capped)} Search campaign(s) are capped by budget - they stop showing because "
            f"the budget runs out, not because demand dries up: {names}. Where these convert efficiently, "
            "you are leaving leads on the table every day; raise their budget or reallocate from weaker "
            "activity to capture more of the searches you already win."
        )
        if rag == "green":
            rag = "amber"

    # ── Impression share lost to RANK (Ad Rank / quality, not money) ──────────
    rank_lost = [c for c in isl if (c.get("lost_rank", 0) or 0) >= 30]
    if rank_lost:
        rank_lost.sort(key=lambda c: c.get("lost_rank", 0), reverse=True)
        names = ", ".join(f"'{c['campaign']}' ({c['lost_rank']:.0f}%)" for c in rank_lost[:3])
        issues.append(
            f"{len(rank_lost)} Search campaign(s) are losing a large share of impressions to Ad Rank, "
            f"not budget: {names}. Ad Rank is driven by bids, ad relevance and Quality Score - so this is "
            "a quality/bid problem, not a money one. Tighter keyword-to-ad relevance, stronger ad copy and "
            "better landing pages recover this visibility without simply spending more."
        )
        if rag == "green":
            rag = "amber"

    # ── Location targeting setting (Presence vs Presence-or-interest) ─────────
    loc = data.get("location_target_types") or []
    poi = [c for c in loc if c.get("geo") == "PRESENCE_OR_INTEREST" and c.get("type") not in AWARENESS]
    if poi:
        names = ", ".join(f"'{c['campaign']}'" for c in poi[:3])
        local_note = (" For a local business this is a major silent leak."
                      if account_type in ("lead_gen", "unknown") else "")
        issues.append(
            f"{len(poi)} campaign(s) use the 'Presence or interest' location setting - Google's default: "
            f"{names}. This shows your ads to people merely INTERESTED in your area, including those who "
            f"are nowhere near it (e.g. someone who once searched your town).{local_note} Switching to "
            "'Presence (people in, or regularly in, your locations)' is one of the highest-ROI fixes there "
            "is - it typically cuts wasted spend and lowers cost per lead."
        )
        rag = "amber"

    # ── Search Partners / Display opt-in (classic budget leak) ────────────────
    nets = data.get("network_settings") or []
    sp = [c["campaign"] for c in nets if c.get("search_partners")]
    disp = [c["campaign"] for c in nets if c.get("display")]
    if sp or disp:
        bits = []
        if sp:
            bits.append("Search Partners (" + ", ".join("'%s'" % n for n in sp[:2]) + ")")
        if disp:
            bits.append("the Display Network (" + ", ".join("'%s'" % n for n in disp[:2]) + ")")
        issues.append(
            f"{len(set(sp) | set(disp))} Search campaign(s) are opted into " + " and ".join(bits) + ". "
            "These send a share of your budget to lower-intent placements beyond Google search results, "
            "often at a worse cost per lead. Unless they are proven to convert, turn them off so budget "
            "concentrates on high-intent search traffic."
        )
        if rag == "green":
            rag = "amber"

    # ── Ad extension (asset) coverage ─────────────────────────────────────────
    assets = data.get("ad_assets")
    if assets is not None:
        present = set(assets.keys())
        labels = {"SITELINK": "sitelinks", "CALLOUT": "callouts", "STRUCTURED_SNIPPET": "structured snippets",
                  "CALL": "call (click-to-call) extensions", "AD_IMAGE": "image extensions",
                  "LEAD_FORM": "lead-form extensions", "PRICE": "price extensions", "PROMOTION": "promotion extensions"}
        core = {"SITELINK", "CALLOUT", "STRUCTURED_SNIPPET", "CALL", "AD_IMAGE"}
        missing_core = [labels[t] for t in ("CALL", "AD_IMAGE", "SITELINK", "CALLOUT", "STRUCTURED_SNIPPET")
                        if t not in present]
        if missing_core:
            issues.append(
                f"Your ads are missing high-value extension types: {', '.join(missing_core)}. Extensions "
                "make ads bigger and more clickable and feed Ad Rank - all at no extra cost per click. "
                "Call and image extensions in particular tend to lift click-through rate by 10-20%. Add the "
                "missing types across your campaigns."
            )
            if rag == "green":
                rag = "amber"

    if not issues:
        issues.append("Coverage and settings look healthy: location targeting, impression share and ad "
                      "extensions are in good shape - no change needed here.")

    return {"rag": rag, "headline": "Coverage & settings", "issues": issues, "data_points": {}}


def build_strengths(data):
    """Things the account already does WELL (verified clean). A senior auditor opens by
    acknowledging strengths before the critique - it's honest and builds trust on the call.
    Returns a short list of plain-English strengths."""
    s = []
    neg = data.get("negative_keyword_count")
    if isinstance(neg, int) and neg >= 100:
        s.append(f"a well-maintained negative keyword list ({neg:,} negatives)")
    loc = data.get("location_target_types") or []
    if loc and not any(c.get("geo") == "PRESENCE_OR_INTEREST" for c in loc):
        s.append("location targeting set up correctly")
    nets = data.get("network_settings") or []
    if nets and not any(c.get("search_partners") or c.get("display") for c in nets):
        s.append("Search Partners and the Display Network correctly switched off on Search")
    assets = data.get("ad_assets")
    if assets and {"SITELINK", "CALLOUT", "CALL"}.issubset(set(assets.keys())):
        s.append("strong ad-extension coverage (sitelinks, callouts, call and more)")
    summary = data.get("account_summary_30d", {})
    if (summary.get("conversions", 0) or 0) > 0:
        s.append("conversion tracking live and recording enquiries")
    return s


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
