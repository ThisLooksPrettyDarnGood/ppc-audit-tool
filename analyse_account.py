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

# AAR types that change BIDDING or TARGETING by themselves - the team's view (and the
# human decks') is these should never sit quietly enabled: a bid-strategy switch or new
# broad-match keywords applied without sign-off can reshape an account overnight.
RISKY_AAR_TYPES = {
    "MAXIMIZE_CLICKS_OPT_IN", "MAXIMIZE_CONVERSIONS_OPT_IN",
    "MAXIMIZE_CONVERSION_VALUE_OPT_IN", "TARGET_CPA_OPT_IN", "TARGET_ROAS_OPT_IN",
    "ENHANCED_CPC_OPT_IN", "USE_BROAD_MATCH_KEYWORD", "KEYWORD_MATCH_TYPE",
    "DISPLAY_EXPANSION_OPT_IN", "SEARCH_PARTNERS_OPT_IN",
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
    Infer whether this is a lead gen or eCommerce account. Decide by where the
    AD-ATTRIBUTED conversions actually sit rather than by which action types merely
    exist: a retailer almost always has a contact or newsletter action lying around,
    and existence-based logic labelled a 13x-ROAS Shopping account 'lead_gen'
    (Powertool, June 2026). Purchases get a 2x weight because a store's purchase
    count is naturally lower than its enquiry-style event count. Falls back to the
    existence test when nothing has recorded recently.
    Returns 'ecommerce', 'lead_gen', or 'unknown'.
    """
    conversion_actions = data.get("conversion_actions", [])
    ecommerce_categories = {"PURCHASE"}
    lead_gen_categories = {
        "LEAD", "CONTACT", "SUBMIT_LEAD_FORM", "BOOK_APPOINTMENT",
        "REQUEST_QUOTE", "SIGNUP", "PHONE_CALL_LEAD", "IMPORTED_LEAD",
    }

    def _vol(ca):
        v = ca.get("attributed_conversions_30d")
        if v is None:
            v = ca.get("conversions_30d")
        return v or 0

    purchase_vol = sum(_vol(ca) for ca in conversion_actions
                       if ca.get("category", "") in ecommerce_categories)
    lead_vol = sum(_vol(ca) for ca in conversion_actions
                   if ca.get("category", "") in lead_gen_categories)
    if purchase_vol or lead_vol:
        # Some accounts genuinely do BOTH (a store with a trade-quote form, say). When
        # both sides record material volume and neither dominates, call it 'mixed' so
        # the deck reads both lenses (ROAS for the sales side, CPA/OCI for the leads).
        _p_weighted = purchase_vol * 2
        _p_share = _p_weighted / (_p_weighted + lead_vol)
        if purchase_vol >= 5 and lead_vol >= 5 and 0.25 <= _p_share <= 0.75:
            return "mixed"
        return "ecommerce" if _p_share >= 0.5 else "lead_gen"

    has_purchase = any(ca.get("category", "") in ecommerce_categories
                       for ca in conversion_actions)
    has_lead = any(ca.get("category", "") in lead_gen_categories
                   for ca in conversion_actions)
    if has_purchase and not has_lead:
        return "ecommerce"
    if has_lead:
        return "lead_gen"
    return "unknown"


def _app_only(data):
    """True when every ENABLED campaign is an App campaign (MULTI_CHANNEL)."""
    enabled = [c for c in data.get("campaigns", []) if c.get("status") == "ENABLED"]
    return bool(enabled) and all(c.get("type") == "MULTI_CHANNEL" for c in enabled)


def parse_competitors_from_questionnaire(text):
    """Pull named competitors from the questionnaire's 'Competition:' line, e.g.
    'Competition: XL Pools, Tanby Pools, Compass Pools' -> ['xl pools', 'tanby pools', ...].
    Returns a list of lowercased competitor names, or [] if the line is absent/blank.
    """
    if not text:
        return []
    import re
    for line in str(text).splitlines():
        if re.match(r'\s*competit', line, re.IGNORECASE) and ':' in line:
            after = line.split(':', 1)[1]
            parts = re.split(r'[,;/]|\band\b|\bor\b', after, flags=re.IGNORECASE)
            names = [p.strip().strip('.').strip().lower() for p in parts]
            return [n for n in names if len(n) >= 3 and n not in ("n/a", "none", "na")]
    return []


def parse_ltv_note(text):
    """Pull a customer lifetime / project value figure from the questionnaire, e.g.
    'LTV £: a pool lead would be £150k+' -> '£150k+'. Returns '' if not found. Used to
    judge CPA against value (a high CPA is fine for a high-LTV product IF quality is there).
    """
    if not text:
        return ""
    import re
    for line in str(text).splitlines():
        if re.search(r'\b(ltv|lifetime|deal value|project value|order value|customer value|aov)\b',
                     line, re.IGNORECASE):
            m = re.search(r'£\s?\d[\d,]*\s?k?\+?', line, re.IGNORECASE)
            if m:
                return m.group(0).replace(' ', '')
    return ""


def analyse_account(data, raw_questionnaire=""):
    # The questionnaire carries the client's stated competitors + product value; the analyser
    # needs both - competitors to flag rival search terms (don't sell a rival's name as "new
    # demand"), and LTV to judge CPA against value rather than as a number in isolation.
    if "competitors" not in data:
        data["competitors"] = parse_competitors_from_questionnaire(raw_questionnaire)
    if "ltv_note" not in data:
        data["ltv_note"] = parse_ltv_note(raw_questionnaire)
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

    # ── App-only accounts (Dan's call, 10 June 2026): don't build full App support.
    # Most checks here are built for Search/Shopping/PMax and either can't see App
    # campaigns or actively misfire (recommending extensions App campaigns can't have,
    # keyword checks with no keywords). Mute the misfires and tell the auditor plainly.
    if _app_only(data):
        findings["app_only"] = True
        _APP_MUTED = ("missing high-value extension types", "keyword click",
                      "come from Broad Match", "come from Exact Match", "Exact Match only",
                      "No Exact Match keyword clicks", "keyword spend is on Broad Match",
                      "looks well-structured", "No Search or Performance Max",
                      # OCI wording is lead-gen/web framed; app conversion depth (in-app
                      # events, Firebase) is covered by the manual-review banner instead.
                      "offline conversion imports (OCI)")
        for _sec in ("targeting_keywords", "efficiency", "account_structure", "conversion_tracking"):
            _d = findings.get(_sec) or {}
            if _d.get("issues"):
                _d["issues"] = [i for i in _d["issues"]
                                if not any(n in i for n in _APP_MUTED)]
        findings["account_structure"].setdefault("issues", []).insert(0,
            "This account runs Google App campaigns only. Most of this audit's checks are built "
            "for Search, Shopping and Performance Max, so they have limited visibility into App "
            "campaign performance, and some standard recommendations do not apply (App campaigns "
            "choose their own placements and assets automatically). Full App campaign support is "
            "coming in a future update of this tool. For now treat this report as partial and "
            "review the App campaigns manually - especially in-app conversion depth, in-app "
            "action value bidding, creative variety and audience signals.")
        if findings["account_structure"].get("rag") == "green":
            findings["account_structure"]["rag"] = "amber"

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
    ("PRIMARY conversions are dominated by low-value", 84, "amber_red", "Conversion Tracking"),  # quantified inflation - root cause
    ("Low-value conversion action",               82, "amber_red", "Conversion Tracking"),
    ("Possible conversion double-counting",        74, "amber_red", "Conversion Tracking"),  # data integrity - undermines all CPAs
    ("paid some very expensive single clicks",     60, "amber",     "Bidding Strategy"),  # CPC spikes hidden by averages
    # Bidding  -  Maximise Clicks branches (specific first; severity follows the money)
    ("Maximise Clicks with no maximum CPC limit set", 82, "amber",  "Bidding Strategy"),  # material spend, uncapped = real leak
    ("uses Maximise Clicks (optimising for traffic",  60, "amber",  "Bidding Strategy"),  # material spend, capped, wrong strategy
    ("uses Maximise Clicks but is a small",           33, "amber",  "Bidding Strategy"),  # tiny/starved -> Additional Observations
    ("is new, so Maximise Clicks is sensible",        30, "amber",  "Bidding Strategy"),  # new/low-data -> not a problem yet
    ("on Maximise Clicks",                         78, "amber",     "Bidding Strategy"),
    ("paused campaign(s) historically delivered",  66, "amber",     "Bidding Strategy"),
    ("restricted by their target ROAS",            65, "amber",     "Bidding Strategy"),  # tROAS above achieved = bids squeezed
    ("share a single campaign budget",             56, "amber",     "Budget & Coverage"),  # shared pool decides priority
    ("receiving spend from more than one campaign", 61, "amber",    "Account Structure"),  # product overlap / self-competition
    ("still on Manual CPC despite",                62, "amber",     "Bidding Strategy"),
    ("on Manual CPC.",                             58, "amber",     "Bidding Strategy"),
    ("has a target CPA of",                        55, "amber",     "Bidding Strategy"),
    ("on smart bidding recorded only",             50, "amber",     "Bidding Strategy"),
    ("using inconsistent bid strategies",          46, "amber",     "Bidding Strategy"),
    ("Cost per conversion is",                     48, "amber",     "Bidding Strategy"),
    # Efficiency / coverage / settings (expert checks)
    ("set to 'Presence or interest' on low-spend", 34, "amber",     "Budget & Coverage"),  # small leak -> Observations
    ("use the 'Presence or interest' location",    76, "amber",     "Budget & Coverage"),  # #1 local waste leak (material)
    ("opted into",                                 62, "amber",     "Budget & Coverage"),  # Search Partners / Display
    ("are capped by budget",                       66, "amber",     "Budget & Coverage"),  # IS lost to budget
    ("losing a large share of impressions to Ad Rank", 58, "amber", "Ad Rank & Quality"),  # IS lost to rank
    ("missing high-value extension types",         60, "amber",     "Ads & Assets"),       # missing extensions
    ("have a LOW score (4 or below)",              54, "amber",     "Ad Rank & Quality"),  # low Quality Score
    # Targeting & keywords
    ("look like competitor business names",         63, "amber",     "Targeting & Keywords"),  # competitor terms (reframe)
    ("Fading winner spotted by comparing",         72, "amber",     "Targeting & Keywords"),  # cross-window pattern (high value)
    ("A small amount of non-converting spend",     34, "amber",     "Targeting & Keywords"),  # tiny leak -> Observations
    ("without converting",                         63, "amber",     "Targeting & Keywords"),  # wasted SQR spend (material)
    ("are NOT added as active keywords",           68, "amber",     "Targeting & Keywords"),  # converting queries (high-value "dropped ball")
    ("without audience signals",                   56, "amber",     "Targeting & Keywords"),
    ("targeting the whole UK",                     55, "amber",     "Targeting & Keywords"),
    ("A minor point on ad strength",               30, "amber",     "Targeting & Keywords"),  # weak RSAs, trivial spend -> Observations
    ("responsive search ads are rated",            54, "amber",     "Targeting & Keywords"),  # RSA ad strength
    ("of keyword clicks come from Broad Match",    58, "amber",     "Targeting & Keywords"),
    ("of keyword spend is on Broad Match",         57, "amber",     "Targeting & Keywords"),
    ("negative keywords applied across",           50, "amber",     "Targeting & Keywords"),
    ("Exact Match only",                           45, "amber",     "Targeting & Keywords"),
    ("come from Exact Match",                      44, "amber",     "Targeting & Keywords"),  # majority-exact volume restriction
    ("No Exact Match keyword clicks",              44, "amber",     "Targeting & Keywords"),
    ("No keyword click data",                      48, "amber",     "Targeting & Keywords"),
    ("CTR is",                                     40, "amber",     "Targeting & Keywords"),
    # Conversion tracking (amber)
    ("offline conversion imports (OCI)",           70, "amber",     "Conversion Tracking"),  # the elephant (lead gen) - paramount
    ("Revenue feedback loop",                      64, "amber",     "Conversion Tracking"),  # the ecommerce OCI equivalent
    ("appear set up but have recorded nothing",    58, "amber",     "Conversion Tracking"),  # dead genuine action (broken tag)
    ("Enhanced conversions for leads is not enabled", 50, "amber",  "Conversion Tracking"),  # EC-for-leads off (API-visible half)
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
    ("change bidding and targeting automatically", 62, "amber",     "Account Structure"),  # risky auto-applies (bid/targeting)
    ("runs Google App campaigns only",             85, "amber",     "Account Structure"),  # App-only banner - leads the deck
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


def _campaign_age_days(start_date):
    """Days since a campaign's start_date ('YYYY-MM-DD'), or None if unparseable."""
    if not start_date:
        return None
    from datetime import datetime
    try:
        return (datetime.today() - datetime.strptime(str(start_date), "%Y-%m-%d")).days
    except (ValueError, TypeError):
        return None


def _campaign_age_phrase(start_date):
    """Human phrase for a campaign's age, e.g. 'in July 2025 (about 11 months ago)'."""
    days = _campaign_age_days(start_date)
    if days is None:
        return ""
    from datetime import datetime
    try:
        d = datetime.strptime(str(start_date), "%Y-%m-%d")
    except (ValueError, TypeError):
        return ""
    months = round(days / 30.4)
    if days < 60:
        rough = f"about {days} days ago"
    elif months < 12:
        rough = f"about {months} months ago"
    else:
        yrs = days / 365.0
        rough = "about a year ago" if yrs < 1.5 else f"about {yrs:.0f} years ago"
    return f"in {d.strftime('%B %Y')} ({rough})"


def _clean_action_label(name, fallback=""):
    """'(GA4) scroll_75' -> 'scroll 75'. Readable conversion-action label for the deck."""
    import re as _r
    n = _r.sub(r'^\(GA4\)\s*', '', str(name or "")).replace('_', ' ').strip()
    return n or fallback


def _action_kind(name):
    """Classify a conversion-action NAME as genuine vs low-value, for the per-term breakdown."""
    n = str(name).lower()
    if any(k in n for k in ("scroll", "page_view", "pageview", "page view", "engag", "view",
                            "impression", "outbound")):
        return "low-value page activity"
    if any(k in n for k in ("form", "formularz", "lead", "contact", "enquir", "quote",
                            "appoint", "submit", "call", "kontakt")):
        return "a genuine enquiry"
    return ""


def _term_breakdown_str(term, data):
    """Plain-English breakdown of WHICH conversion actions a term's conversions came through,
    e.g. " Its 6 conversions came through: 6 via 'Przeslany formularz kontaktowy' (a genuine
    enquiry)." - answers 'were these form fills or page scrolls?'. '' if no data."""
    bd = (data.get("term_conversion_breakdown") or {}).get(str(term).strip().lower())
    if not bd:
        return ""
    parts = []
    for action, conv in bd[:3]:
        kind = _action_kind(action)
        kind_txt = f" ({kind})" if kind else ""
        parts.append(f"{int(round(conv))} via '{_clean_action_label(action)}'{kind_txt}")
    return f" Its conversions came through: {', '.join(parts)}." if parts else ""


def _pretty_date(d):
    """'2026-06-05' -> '5 June'. Returns the input unchanged if unparseable."""
    from datetime import datetime
    try:
        return datetime.strptime(str(d), "%Y-%m-%d").strftime("%-d %B")
    except (ValueError, TypeError):
        return str(d)


def _account_search_cpc(campaigns):
    """One consistent 'typical click cost' for the whole deck: the blended CPC across
    ENABLED Search campaigns (spend / clicks). Search-only so cheap Display/PMax clicks
    don't distort it. Returns None if there are no Search clicks."""
    spend = clicks = 0.0
    for c in campaigns:
        if c.get("status") == "ENABLED" and c.get("type") == "SEARCH":
            spend += c.get("spend_30d") or 0
            clicks += c.get("clicks_30d") or 0
    return (spend / clicks) if clicks else None


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

def detect_tracking_change(monthly):
    """
    Detect a conversion-tracking change INSIDE the 12-month window from the per-action
    monthly ad-attributed series ({action: {"YYYY-MM": conversions}}). Two signals:
      - a now-material action (>=15% of the last-3-months total, >=5 conversions) whose
        first recording month is 3+ months into the window, while the account was already
        recording conversions before it appeared (a setup change, not a new account); or
      - a previously material action (>=15% of the earlier total, >=10 conversions) that
        has recorded nothing in the last 3 months.
    15% not 50%: the genuine action is often the MINORITY of recorded conversions
    precisely because junk engagement actions dominate (IB: real form ~21% of recent).
    Both guards keep tiny accounts and new accounts from false-flagging. Returns None,
    or {"changed": True, "month": "November 2025", "detail": "..."} for the caveat.
    """
    if not monthly:
        return None
    all_months = sorted({m for s in monthly.values() for m in s})
    if len(all_months) < 6:
        return None   # window too short to call anything "mid-window"
    month_totals = {m: sum(s.get(m, 0) for s in monthly.values()) for m in all_months}
    if sum(month_totals.values()) < 20:
        return None   # too little volume to infer a setup change
    recent, earlier = all_months[-3:], all_months[:-3]
    recent_total = sum(month_totals[m] for m in recent)
    earlier_total = sum(month_totals[m] for m in earlier)

    def _label(month_key):
        from datetime import datetime as _dt
        return _dt.strptime(month_key, "%Y-%m").strftime("%B %Y")

    appeared, stopped = [], []
    for name, series in monthly.items():
        active = [m for m in all_months if series.get(m, 0) >= 1]
        if not active:
            continue
        rec = sum(series.get(m, 0) for m in recent)
        ear = sum(series.get(m, 0) for m in earlier)
        first, last = active[0], active[-1]
        idx = all_months.index(first)
        pre_total = sum(month_totals[m] for m in all_months[:idx])
        if (recent_total > 0 and rec >= 5 and rec / recent_total >= 0.15
                and idx >= 3 and pre_total >= 10):
            appeared.append((name, first, rec / recent_total))
        if (earlier_total > 0 and ear >= 10 and ear / earlier_total >= 0.15
                and rec < 1 and last not in recent):
            stopped.append((name, last))
    if not appeared and not stopped:
        return None

    parts = []
    for name, first, share in sorted(appeared, key=lambda a: -a[2])[:2]:
        parts.append(f"'{name}' (about {share:.0%} of recent conversions) only began "
                     f"recording in {_label(first)}")
    for name, last in stopped[:2]:
        parts.append(f"'{name}' recorded conversions until {_label(last)}, then stopped")
    # The change month: when the new counting action appeared, or just after the old one stopped.
    change_month = (min(a[1] for a in appeared) if appeared
                    else min(s[1] for s in stopped))
    return {"changed": True, "month": _label(change_month), "detail": "; ".join(parts)}


def score_conversion_tracking(data):
    issues = []
    rag = "green"
    # True only when conversions are ACTUALLY inflated right now (low-value actions recording
    # ad-attributed conversions, or real multi-counting) - drives the downstream "don't celebrate
    # the CPA" caveat. Stays False for latent setup risk where the reported numbers are genuine.
    conversions_inflated = False
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

        # "Primary" = optimised towards. On goal-based accounts include_in_conversions_metric can
        # be False on an action that is still primary and counting (IB's lead form is exactly this),
        # so accept EITHER signal.
        def _is_primary(ca):
            return bool(ca.get("primary_for_goal") or ca.get("include_in_conversions"))
        # AD-ATTRIBUTED conversions are the ground truth for what is actually being counted/optimised
        # (metrics.conversions), as opposed to conversions_30d which is all_conversions (site activity
        # incl. non-ad GA4 events). None when the per-action volume query was unavailable.
        def _attr(ca):
            return ca.get("attributed_conversions_30d")
        _has_attr = any(ca.get("attributed_conversions_30d") is not None for ca in active_actions)
        # Does this action actually drive the reported/optimised Conversions number right now?
        def _is_counting(ca):
            return (_attr(ca) or 0) > 0 if _has_attr else bool((ca.get("conversions_30d") or 0) > 0)

        # Count only PRIMARY actions (the ones bidding actually optimises towards).
        primary_actions = [ca for ca in active_actions if _is_primary(ca)]

        # ── Possible double-counting: multiple PRIMARY actions of the same category, or an
        # overlapping cluster of call/contact actions, can count one interaction several times -
        # a classic cause of an artificially LOW CPA. It undermines every CPA/ROAS figure, so
        # flag it clearly (and it directly explains "too cheap" historic numbers).
        from collections import Counter as _Counter
        _cat_pretty = {"PHONE_CALL_LEAD": "phone call", "CONTACT": "click-to-call/contact",
                       "GET_DIRECTIONS": "get-directions", "SUBMIT_LEAD_FORM": "lead form",
                       "REQUEST_QUOTE": "quote request", "BOOK_APPOINTMENT": "appointment"}
        # Only actions that ACTUALLY record ad-attributed conversions can double-count a real lead.
        # A pile of primary actions recording nothing is a setup risk (the low-value finding below
        # covers it), NOT active double-counting - so when we have attribution data, base this on the
        # counting actions only. (Falls back to all primary actions when attribution is unavailable.)
        _basis = [ca for ca in primary_actions if _is_counting(ca)] if _has_attr else primary_actions
        _primary_cats = _Counter(ca.get("category", "") for ca in _basis if ca.get("category"))
        _dup_cats = {c: n for c, n in _primary_cats.items() if n >= 2}
        # True call/contact actions only - GET_DIRECTIONS is deliberately excluded: a directions
        # tap isn't a phone enquiry, so counting it here would overstate the call double-counting.
        _call_cluster = [ca for ca in _basis
                         if ca.get("category") in {"PHONE_CALL_LEAD", "CONTACT", "CALL"}]
        if _dup_cats or len(_call_cluster) >= 2:
            _dup_txt = "; ".join(
                f"{n} separate primary '{_cat_pretty.get(c, c.replace('_', ' ').lower())}' actions"
                for c, n in sorted(_dup_cats.items(), key=lambda x: -x[1]))
            _count_word = "recording" if _has_attr else "set up as"
            _call_note = ""
            if len(_call_cluster) >= 2:
                _cnames = ", ".join(f"'{ca.get('name')}'" for ca in _call_cluster[:3])
                _more = "" if len(_call_cluster) <= 3 else " among others"
                _call_note = (f" In particular, {len(_call_cluster)} call/contact actions are {_count_word} "
                              f"conversions ({_cnames}{_more}), so a single phone enquiry can be counted several times.")
            _lead_txt = (_dup_txt if _dup_txt else f"{len(_call_cluster)} overlapping call/contact actions")
            issues.append(
                f"Possible conversion double-counting: {_lead_txt} are {_count_word} conversions, which can count "
                f"the same lead more than once.{_call_note} Double-counting makes cost per lead look artificially "
                "LOW, so historic figures (including the paused PMax CPAs) may be around half the true cost. "
                "Consolidate to one clean primary action per genuine lead type (ideally the form fill), move the "
                "rest to secondary, and reconcile against the back-end enquiry count so there is a single source of truth."
            )
            conversions_inflated = True
            if rag not in ("red",):
                rag = "amber_red"

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
        spammable_categories = {"PAGE_VIEW", "ENGAGEMENT", "DOWNLOAD", "OUTBOUND_CLICK"}
        _lowval_plain = {"PAGE_VIEW": "a page-view action", "ENGAGEMENT": "an engagement action",
                         "DOWNLOAD": "a download action", "OUTBOUND_CLICK": "an outbound-click action"}
        primary_spammable = [ca for ca in active_actions
                             if _is_primary(ca) and ca.get("category", "") in spammable_categories]
        if primary_spammable:
            plain = ", ".join(_lowval_plain.get(ca.get("category", ""), "a low-value action")
                              for ca in primary_spammable)
            _vols_all = [ca.get("conversions_30d") for ca in primary_spammable]   # all_conversions
            _recording_site = any((v is not None and v > 0) for v in _vols_all)
            _all_known_zero = bool(_vols_all) and all((v is not None and v == 0) for v in _vols_all)
            _attr_low = sum((_attr(ca) or 0) for ca in primary_spammable) if _has_attr else None

            # The genuine lead action currently driving the reported conversions (if any) - used both
            # to flag a true inversion and, in the softer case, to reassure that the headline number
            # already reflects real enquiries.
            _genuine_counting = [ca for ca in active_actions
                                 if ca.get("category", "") in {"SUBMIT_LEAD_FORM", "LEAD", "REQUEST_QUOTE",
                                                               "BOOK_APPOINTMENT"} and _is_counting(ca)]
            _g = max(_genuine_counting,
                     key=lambda ca: (_attr(ca) if _has_attr else ca.get("conversions_30d")) or 0) \
                 if _genuine_counting else None
            _gv = int(round(((_attr(_g) if _has_attr else _g.get("conversions_30d")) or 0))) if _g else 0
            _g_label = _clean_action_label(_g.get("name")) if _g else ""

            # ACTIVE inflation: low-value actions are actually recording AD-ATTRIBUTED conversions
            # (or we have no attribution data and they're firing, so we can't rule it out).
            _active = _recording_site and (not _has_attr or (_attr_low or 0) >= 1)
            if _active:
                _use_attr = _has_attr and (_attr_low or 0) >= 1
                _named = sorted(
                    [(_clean_action_label(ca.get("name"), _lowval_plain.get(ca.get("category"), "a low-value action")),
                      (_attr(ca) if _use_attr else ca.get("conversions_30d")) or 0)
                     for ca in primary_spammable
                     if ((_attr(ca) if _use_attr else ca.get("conversions_30d")) or 0) > 0],
                    key=lambda x: x[1], reverse=True)
                _total_low = int(round(sum(v for _, v in _named)))
                _egs = ", ".join(f"'{lbl}' ({int(round(v))})" for lbl, v in _named[:3])
                _attr_word = "ad-attributed " if _use_attr else ""
                # Inversion only when the genuine action is genuinely NOT primary (truly sidelined).
                _inv = ""
                if _g and not _is_primary(_g):
                    _inv = (f" Worse, your genuine enquiry action ('{_g_label}', {_gv} in 30 days) is NOT set as a "
                            "primary conversion - so the real lead is not even what bidding optimises towards.")
                issues.append(
                    f"Your PRIMARY conversions include low-value website activity that is being counted, not real "
                    f"enquiries: {_egs} recorded about {_total_low} {_attr_word}'conversions' in the last 30 days - "
                    f"page scrolls, clicks and page views, not genuine leads.{_inv} Google optimises towards this "
                    "activity, inflating your reported numbers. Move these to secondary and make the genuine enquiry "
                    "(form submission) the single primary conversion."
                )
                conversions_inflated = True
                if rag != "red":
                    rag = "amber_red"
            elif _recording_site:
                # Set as primary and firing as SITE events, but ~0 ad-attributed → NOT inflating the
                # reported/optimised Conversions today (which reflect genuine enquiries). Real setup
                # risk, but we must not claim the numbers are inflated when they are not.
                _site_named = sorted(
                    [(_clean_action_label(ca.get("name"), _lowval_plain.get(ca.get("category"), "a low-value action")),
                      ca.get("conversions_30d") or 0) for ca in primary_spammable if (ca.get("conversions_30d") or 0) > 0],
                    key=lambda x: x[1], reverse=True)
                _egs = ", ".join(f"'{lbl}'" for lbl, _ in _site_named[:3])
                _g_note = (f" Encouragingly, your reported conversions are currently driven by the genuine enquiry "
                           f"action ('{_g_label}', {_gv} in 30 days), so the headline numbers are not inflated today."
                           if _g else "")
                issues.append(
                    f"Low-value website actions are set as PRIMARY conversions ({_egs}) and fire often as site "
                    "events, but they are not currently attributed to your Google Ads clicks, so they are not "
                    f"inflating your reported Conversions right now.{_g_note} It is still a misconfiguration that "
                    "points bidding at the wrong goals and would distort your numbers if that traffic grows: move "
                    "these actions to secondary and keep the genuine form submission as the single primary conversion."
                )
                if rag == "green":
                    rag = "amber"
            elif _all_known_zero:
                # Primary but recording nothing at all → a latent misconfiguration, not active harm.
                issues.append(
                    f"A low-value action is set as a primary conversion but is recording no conversions in the "
                    f"last 30 days: {plain}. It isn't skewing bidding right now, but it should be removed or set "
                    "to secondary so it never can - and a low-value action sitting in the primary 'Conversions' "
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
        if not has_oci and detect_account_type(data) in ("lead_gen", "unknown", "mixed"):
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
        elif not has_oci and detect_account_type(data) == "ecommerce":
            # The ecommerce equivalent of the OCI elephant: orders are tracked, but what
            # they were WORTH after the click (margin, returns, new-vs-returning) is not
            # fed back, so value bidding steers on top-line order value only.
            issues.append(
                "Revenue feedback loop: conversion tracking records orders, but we could not find any "
                "offline or enhanced revenue import feeding back what those orders were worth AFTER the "
                "click - actual margin, returns and cancellations, or new-versus-returning customer "
                "value. Importing true order outcomes (even a periodic spreadsheet upload) lets smart "
                "bidding optimise towards profit rather than top-line order value, which matters most "
                "when target-ROAS bidding is steering spend."
            )
            if rag == "green":
                rag = "amber"

        # Enhanced Conversions for LEADS (the API-visible half of EC - the web/purchase
        # side lives in the tag and is not exposed, so we say nothing about it). A
        # lead-gen account with this off is leaving measurement accuracy on the table.
        _cts = data.get("conversion_tracking_setting") or {}
        if (_cts and not _cts.get("ec_for_leads")
                and detect_account_type(data) in ("lead_gen", "mixed", "unknown")):
            issues.append(
                "Enhanced conversions for leads is not enabled at account level. It sends securely "
                "hashed first-party data (the email or phone number a lead submits) alongside the "
                "conversion, recovering conversions that cookies alone now miss and sharpening what "
                "smart bidding learns from. It is a settings-level switch plus a tag tweak - low "
                "effort for a measurable accuracy gain."
            )
            if rag == "green":
                rag = "amber"

        # A genuine lead/purchase action that is ENABLED and PRIMARY but has recorded
        # ~nothing across 12 months looks like broken tracking (e.g. a 'Book an
        # Appointment' tag that never fires). Only fires when the account as a whole
        # IS recording - so it's the action that's dead, not the account. SIGNUP and
        # CONTACT are deliberately excluded (often legitimately dormant).
        _monthly = data.get("conversion_volume_by_month") or {}
        if _monthly and sum(sum(s.values()) for s in _monthly.values()) >= 20:
            _GENUINE_CATS = {"PURCHASE", "SUBMIT_LEAD_FORM", "BOOK_APPOINTMENT",
                             "REQUEST_QUOTE", "PHONE_CALL_LEAD", "LEAD", "IMPORTED_LEAD"}
            _dead = [ca for ca in primary_actions
                     if ca.get("category") in _GENUINE_CATS
                     and sum((_monthly.get(ca.get("name")) or {}).values()) < 1]
            if _dead:
                _dnames = ", ".join(f"'{ca.get('name')}'" for ca in _dead[:3])
                issues.append(
                    f"{len(_dead)} genuine conversion action(s) appear set up but have recorded nothing "
                    f"in the last 12 months: {_dnames}. The rest of the account records conversions "
                    "fine, so if customers do convert this way the tag is likely broken or misfiring - "
                    "worth a manual test conversion to confirm. (If the action was only created "
                    "recently, this is expected and can be ignored.)"
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
                f"Campaign '{c_name}' spent £{c_cost:,.0f} with 0 conversions recorded  -  "
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

    # Mid-window tracking change (e.g. counting action replaced partway through the year):
    # structured flag only - it drives the performance-commentary caveat so a 30d-vs-12m
    # "trend" is never presented as like-for-like across two measurement setups.
    tracking_change = None
    try:
        tracking_change = detect_tracking_change(data.get("conversion_volume_by_month") or {})
    except Exception:
        pass

    return {
        "rag": rag,
        "headline": _ct_headline(rag, total_conversions, len(conversion_actions)),
        "issues": issues,
        "conversions_inflated": conversions_inflated,
        "tracking_change": tracking_change,
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
                    f"£{total_budget:,.0f}/day total budget is split across {len(enabled_campaigns)} campaigns "
                    f"(avg £{avg_budget:,.0f} each). Campaigns need sufficient budget to gather data and learn  -  "
                    "consider consolidating into fewer campaigns."
                )
                rag = "amber"
            elif avg_budget < 5 and len(enabled_campaigns) > 1:
                issues.append(
                    f"Average daily budget per campaign is just £{avg_budget:,.0f}. "
                    "At this level smart bidding cannot learn effectively."
                )
                if rag == "green":
                    rag = "amber"

    # ── Products advertised in more than one campaign (Shopping/PMax): learnings are
    # split and the account bids against itself, raising CPCs. Names the worst products.
    _overlap = data.get("product_overlap") or []
    if _overlap:
        _top = _overlap[0]
        _camps = _top["campaigns"]
        _eg = (f"'{_top['title']}' took spend in {len(_camps)} campaigns at once "
               f"({', '.join(n for n, _ in _camps[:4])}) - about £{_top['total_spend']:,.0f} in 30 days")
        issues.append(
            f"{len(_overlap)} product(s) are receiving spend from more than one campaign at the same "
            f"time. For example {_eg}. When the same product runs in several campaigns its performance "
            "data is split between them and the campaigns can compete against each other in the same "
            "auctions, pushing CPCs up. Reviewing the campaign structure so each product lives in the "
            "most relevant campaign (and is excluded elsewhere) consolidates the learnings and removes "
            "the self-competition."
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
    # Drop UNKNOWN/UNSPECIFIED enum values - they'd render as a bare "Unknown" on the client
    # deck (a recommendation type newer than the API client's enum). No actionable meaning.
    auto_apply_types = [t for t in auto_apply_types
                        if str(t).upper() not in ("UNKNOWN", "UNSPECIFIED")]
    if auto_apply_types:
        labelled = ", ".join(_aar_label(t) for t in auto_apply_types)
        risky = [t for t in auto_apply_types if t in RISKY_AAR_TYPES]
        non_approved = [t for t in auto_apply_types
                        if t not in APPROVED_AAR_TYPES and t not in RISKY_AAR_TYPES]
        if risky:
            # Bid-strategy / targeting changes applied automatically are never "fine to
            # leave" - the human decks lead with this, and the team's approved list
            # (Max's red boxes) contains none of these. Name them and escalate.
            risky_labelled = ", ".join(_aar_label(t) for t in risky)
            other_note = (f" (alongside lower-risk types: {', '.join(_aar_label(t) for t in non_approved)})"
                          if non_approved else "")
            issues.append(
                f"Google is allowed to change bidding and targeting automatically: Auto-Apply is "
                f"switched on for {risky_labelled}{other_note}. These types can switch a campaign's "
                "bid strategy or alter keywords and reach without anyone signing it off, based on "
                "Google's recommendations rather than your strategy. Most accounts are better with "
                "these off, applying such changes deliberately after review."
            )
            if rag == "green":
                rag = "amber"
        elif non_approved:
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
        elif exact_pct >= 0.7:
            # Majority-exact accounts are volume-restricted too, even with a few phrase/broad
            # clicks present - the human decks flag "majority Exact Match" (Maitri, HGV Med).
            issues.append(
                f"{exact_pct:.0%} of keyword clicks come from Exact Match. Exact match gives control "
                "but restricts search volume - the account may be missing demand that Phrase Match "
                "keywords (paired with good negatives) would capture. Consider widening the proven themes."
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
                    f"(£{broad_spend:,.0f}). Consider shifting budget to more controlled match types."
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
                                   "clicks": 0, "status": t.get("status", "NONE"),
                                   "campaign_name": t.get("campaign_name", ""),
                                   "keyword": t.get("keyword", ""),
                                   "keyword_match_type": t.get("keyword_match_type", "")})
            a["spend"] += t.get("spend", 0) or 0
            a["conversions"] += t.get("conversions", 0) or 0
            a["clicks"] += t.get("clicks", 0) or 0
            # Keep a triggering keyword if this row carries one and we don't have it yet.
            if not a.get("keyword") and t.get("keyword"):
                a["keyword"] = t.get("keyword")
                a["keyword_match_type"] = t.get("keyword_match_type", "")
        return list(agg.values())

    converting_not_added = _agg_by_term(converting_not_added)
    wasted_terms = _agg_by_term(wasted_terms)

    # ── Competitor terms: a rival's business name is NOT new demand. A competitor-name
    # search that "converts" in a normal (non-competitor) campaign is usually a low-value
    # accidental contact - someone trying to reach the other company - so it must not be
    # sold as a keyword to add. We classify from the client's stated competitor list
    # (questionnaire) for certainty, plus a hedged "Name + pool(s)" heuristic for unlisted
    # ones, then pull these out of the converting/wasted lists into their own finding.
    competitors = data.get("competitors", []) or []
    _comp_generic = {"pool", "pools", "swimming", "ltd", "limited", "leisure", "company",
                     "services", "group", "uk", "the", "and", "spas", "spa", "covers", "saunas"}
    _comp_full = [c for c in competitors if c]
    _comp_tokens = {tok for c in competitors for tok in c.split()
                    if len(tok) >= 4 and tok not in _comp_generic}
    # Product head-nouns for the "Name + <noun>" heuristic, taken from the client's own
    # account name generics (pool/pools here) so it stays vertical-aware without a hard list.
    _product_nouns = {g for g in _comp_generic if g in str(data.get("account_name", "")).lower()
                      or g in {"pool", "pools"}}
    _generic_mods = {"swimming", "indoor", "outdoor", "luxury", "fibreglass", "fiberglass",
                     "concrete", "near", "best", "cheap", "local", "new", "used", "small",
                     "large", "above", "ground", "inground", "infinity", "plunge", "natural",
                     "heated", "endless", "lap", "garden", "home", "domestic", "commercial",
                     "residential", "portable", "plastic", "mini", "kids", "childrens", "my",
                     "bespoke", "custom", "modern", "traditional", "cost", "price", "prices"}

    def _competitor_reason(term):
        """Return 'listed' (named competitor), 'possible' (heuristic), or None."""
        t = str(term).lower()
        if _is_brand(t):
            return None
        for full in _comp_full:
            if full in t:
                return "listed"
        toks = t.split()
        if any(tok in _comp_tokens for tok in toks):
            return "listed"
        # Heuristic: "<proper-noun-ish modifier> pool(s) ..." (e.g. 'southern pools heathfield')
        for i, w in enumerate(toks):
            if w in _product_nouns and i > 0:
                mod = toks[i - 1]
                if (len(mod) >= 4 and mod not in _generic_mods
                        and mod not in _product_nouns and mod not in brand_tokens):
                    return "possible"
        return None

    competitor_terms = []
    for src in (converting_not_added, wasted_terms):
        for t in src:
            reason = _competitor_reason(t.get("term", ""))
            if reason:
                competitor_terms.append({**t, "reason": reason})
    _comp_names = {str(t.get("term", "")).strip().lower() for t in competitor_terms}
    converting_not_added = [t for t in converting_not_added
                            if str(t.get("term", "")).strip().lower() not in _comp_names]
    wasted_terms = [t for t in wasted_terms
                    if str(t.get("term", "")).strip().lower() not in _comp_names]

    # If low-value actions are primary AND recording AD-ATTRIBUTED conversions, ANY "converted/lead"
    # count below may be page activity, not genuine enquiries - caveat it. But only when they actually
    # count: an action firing 72 site scrolls with 0 ad-attributed isn't polluting the search-term
    # conversions (those came through the genuine action), so we must not add a misleading caveat.
    def _lv_attr(ca):
        a = ca.get("attributed_conversions_30d")
        return a if a is not None else (ca.get("conversions_30d") or 0)
    _lv_primary_firing = any(
        ca.get("status") == "ENABLED"
        and (ca.get("primary_for_goal") or ca.get("include_in_conversions"))
        and ca.get("category") in {"PAGE_VIEW", "ENGAGEMENT", "DOWNLOAD", "OUTBOUND_CLICK"}
        and (_lv_attr(ca) or 0) > 0
        for ca in (data.get("conversion_actions") or []))
    _quality_caveat = (" Note: low-value actions (page views, scrolls, clicks) are currently counted as "
                       "primary conversions, so some of these 'leads' may be page activity rather than "
                       "genuine enquiries - confirm once tracking is corrected." if _lv_primary_firing else "")

    sqr_issues = []
    if competitor_terms:
        _ranked_ct = sorted(competitor_terms,
                            key=lambda x: (x.get("conversions", 0) or 0, x.get("spend", 0) or 0),
                            reverse=True)
        egs = []
        for t in _ranked_ct[:3]:
            conv = t.get("conversions", 0) or 0
            spend = t.get("spend", 0) or 0
            tag = "a named competitor" if t.get("reason") == "listed" else "likely a competitor"
            if conv:
                egs.append(f"'{t['term']}' ({int(round(conv))} 'conversion{'s' if round(conv) != 1 else ''}'"
                           f" at ~£{round(spend / conv)}, {tag})")
            else:
                egs.append(f"'{t['term']}' (£{round(spend)} spent, no conversions, {tag})")
        eg_text = "; ".join(egs)
        camps = {t.get("campaign_name", "") for t in competitor_terms if t.get("campaign_name")}
        noncomp = sorted(c for c in camps if "competitor" not in c.lower() and "comp" not in c.lower())
        camp_note = (f" These are firing inside non-competitor campaigns (e.g. '{noncomp[0]}'), "
                     "where they should not be." if noncomp else "")
        # Tot up the last-30-days spend on competitor terms (a clean 30d figure: prefer the
        # 30d top-terms total per term, else sum the daily priciest-click rows for that term).
        _spend30 = {}
        for _t in (data.get("top_search_terms") or []):
            _k = str(_t.get("term", "")).strip().lower()
            _spend30[_k] = _spend30.get(_k, 0) + (_t.get("spend", 0) or 0)   # SUM dup ad-group rows
        _priciest_by_term = {}
        for _p in (data.get("priciest_clicks") or []):
            _k = str(_p.get("term", "")).strip().lower()
            _priciest_by_term[_k] = _priciest_by_term.get(_k, 0) + (_p.get("spend", 0) or 0)
        _comp_30d = 0.0
        for _ct in competitor_terms:
            _k = str(_ct.get("term", "")).strip().lower()
            _comp_30d += _spend30.get(_k, _priciest_by_term.get(_k, 0))
        tally_note = (f" In total, about £{_comp_30d:.0f} has gone on these competitor-name searches in the "
                      "last 30 days." if _comp_30d >= 1 else "")
        sqr_issues.append(
            f"{len(competitor_terms)} search term(s) look like competitor business names rather than new "
            f"demand: {eg_text}.{camp_note}{tally_note} A competitor-name search that 'converts' in a normal "
            "campaign is usually a low-value accidental contact  -  someone trying to reach the other company  "
            "-  not a genuine enquiry, and without offline conversion import (OCI) you cannot tell which (if "
            "any) became real jobs. Rather than adding these as keywords, decide deliberately: target "
            "competitors only in a dedicated campaign with tailored messaging and landing pages, or add them "
            "as negative keywords to stop paying for misdirected clicks."
        )
        if rag == "green":
            rag = "amber"
    if fading_winners:
        f = max(fading_winners, key=lambda x: x["spend_30d"])
        c90 = int(round(f["conv_90d"]))
        sqr_issues.append(
            f"Fading winner spotted by comparing time windows: '{f['term']}' generated {c90} "
            f"lead{'s' if c90 != 1 else ''} over the LAST 90 DAYS, but in the LAST 30 DAYS it has spent "
            f"about £{f['spend_30d']:.0f} with no leads. A proven term going quiet like this usually means "
            "something changed - a page rename, a dropped bid, or a competitor moving in. Catching it needs "
            "exactly this 30 vs 90-day comparison, and it's where quietly dropped balls are recovered."
            # Show what those 90-day "leads" actually were (form fills vs page scrolls), else caveat.
            + (_term_breakdown_str(f["term"], data) or _quality_caveat)
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
            "loosely (or not at all) rather than controlled directly. Promote these into dedicated keywords "
            "where search volume supports it - very low-volume terms (under roughly 10 searches a month) "
            "cannot be added and are better captured by a closely related theme - to gain control over bids, "
            f"ad copy and landing pages.{_quality_caveat}"
        )
        if rag == "green":
            rag = "amber"
    if wasted_terms:
        wasted_spend = round(sum((t.get("spend", 0) or 0) for t in wasted_terms), 2)
        # Always judge a leak in PROPORTION to account spend - £64 on a £2.5k account is a
        # minor tidy-up, not a headline. Small leaks get softened wording AND a low severity
        # so they fall to Additional Observations rather than leading the deck.
        _acct_spend = (data.get("account_summary_30d") or {}).get("spend") or sum(
            (c.get("spend_30d") or 0) for c in campaigns if c.get("status") == "ENABLED")
        _pct = (wasted_spend / _acct_spend) if _acct_spend else 0
        _pct_txt = f" - about {_pct:.0%} of the account's £{_acct_spend:,.0f} monthly spend" if _acct_spend else ""
        _top_waste = sorted(wasted_terms, key=lambda t: (t.get("spend", 0) or 0), reverse=True)[:3]
        # Show click count so 1 click at £64 is never confused with 64 clicks at £1.
        def _waste_eg(t):
            clk = t.get("clicks") or 0
            clk_txt = f", {clk} click{'s' if clk != 1 else ''}" if clk else ""
            return f"'{t.get('term', '?')}' (£{round(t.get('spend', 0) or 0)}{clk_txt}, no conversions)"
        _weg = "; ".join(_waste_eg(t) for t in _top_waste)
        _weg_text = (f" The biggest: {_weg}.") if _weg else ""
        material = wasted_spend >= max(150.0, 0.05 * (_acct_spend or 0))
        if material:
            sqr_issues.append(
                f"{len(wasted_terms)} search term(s) have spent about £{wasted_spend:.0f} "
                f"in the last 30 days without converting{_pct_txt}.{_weg_text} Reviewing these for negative "
                "keywords would cut wasted spend - and some may be competitor or brand names being matched by "
                "broad keywords without you realising, which is a common and costly leak."
            )
        else:
            sqr_issues.append(
                f"A small amount of non-converting spend: about £{wasted_spend:.0f} across "
                f"{len(wasted_terms)} search term(s) in the last 30 days{_pct_txt}.{_weg_text} It is minor in "
                "the context of total spend, but worth a quick check - confirm the term is statistically "
                "meaningful (not just a click or two) before adding a negative, and watch that similar terms "
                "do not quietly scale."
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
                # Dedupe by ad group so we never list the same ad group twice (two weak RSAs
                # in one ad group would otherwise read as a repeated example).
                seen_ag, uniq = set(), []
                for e in examples:
                    ag = e.get("ad_group")
                    if ag in seen_ag:
                        continue
                    seen_ag.add(ag)
                    uniq.append(e)
                names = ", ".join(f"the '{e['ad_group']}' ad group ({e['strength']})" for e in uniq[:2])
                eg = f" For example {names}."
            # Show the weak-ad spend as a share of total spend so it's easy to weigh.
            total_spend = (data.get("account_summary_30d", {}) or {}).get("spend", 0) or 0
            pct = f" - around {round(rsa_low_spend / total_spend * 100)}% of total account spend" if total_spend else ""
            # Severity follows the money: weak ads carrying under 5% of account spend are a
            # tidy-up, not a slide (Powertool: a "£0 of spend" RSA point made the main deck).
            _share = (rsa_low_spend / total_spend) if total_spend else None
            if _share is not None and _share < 0.05:
                issues.append(
                    f"A minor point on ad strength: {rsa_low} of {rsa_total} live responsive search "
                    f"ads are rated Poor or Average, but they carry only about £{rsa_low_spend:,.0f} "
                    f"of spend{pct}, so the commercial impact is small. Worth tidying when convenient "
                    "rather than as a priority."
                )
            else:
                issues.append(
                    f"{rsa_low} of {rsa_total} live responsive search ads are rated Poor or Average "
                    f"ad strength, carrying about £{rsa_low_spend:,.0f} of spend{pct}.{eg} "
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
        # Flagged competitor/odd term names so the narrative layer can web-sense-check them
        # (e.g. confirm 'giles pools lewes' is Giles Leisure, a Lewes pool retailer/public pool).
        "competitor_terms": [{"term": t.get("term"), "reason": t.get("reason")}
                             for t in competitor_terms],
        # Converting-but-unkeyworded terms we'd otherwise recommend adding - the narrative layer
        # sense-checks these too, so we never recommend a misdirected/other-brand term (e.g.
        # 'british council', 'macmillan') as a keyword for an unrelated advertiser. Full dicts
        # (term + conversions + TOTAL spend) so the deck can give a per-term breakdown.
        "converting_terms": [{"term": t.get("term"),
                              "conversions": int(round(t.get("conversions", 0) or 0)),
                              "spend": round(t.get("spend", 0) or 0, 2),
                              "keyword": t.get("keyword", ""),
                              "keyword_match_type": t.get("keyword_match_type", "")}
                             for t in converting_not_added if t.get("term")],
        # Fading winners are sense-checked too: a competitor like 'astra ai' must be reframed as a
        # rival, not presented as "lost demand to recover".
        "fading_winner_terms": [f.get("term") for f in fading_winners if f.get("term")],
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

    # ── Max Clicks: explain the WHY with hard facts, and rank by spend ──────────
    # "Maximise Clicks" is reported as TARGET_SPEND in the API. A human auditor judges it
    # on the campaign's own facts, not as a blanket red flag: a tiny or low-cap campaign is
    # a minor note (it belongs in Additional Observations); a big-budget UNCAPPED one is a
    # genuine money leak that leads the deck; a brand-new campaign is fine for now. We branch
    # on age, spend, and the CPC ceiling vs the account's typical CPC so severity follows the
    # money (the per-case detail strings map to different severities in _ISSUE_SIGNATURES).
    max_clicks_campaigns = [
        c for c in campaigns
        if c.get("status") == "ENABLED"
        and c.get("bid_strategy", "").upper() in ("MAXIMIZE_CLICKS", "TARGET_SPEND")
    ]
    true_manual = [n for n, s in manual_campaigns if s not in ("MAXIMIZE_CLICKS", "TARGET_SPEND")]

    if max_clicks_campaigns:
        rag = "amber"
        # Account-typical CPC = blended Search CPC (consistent across the whole deck).
        typical_cpc = _account_search_cpc(campaigns)
        account_spend = total_cost or sum((c.get("spend_30d") or 0) for c in campaigns)
        costly_terms = data.get("max_clicks_costly_terms", {}) or {}

        for c in max_clicks_campaigns:
            name = c.get("name", "Unknown")
            spend = c.get("spend_30d") or 0
            conv = c.get("conversions_30d") or 0
            clicks = c.get("clicks_30d") or 0
            ceiling = c.get("cpc_ceiling_gbp")
            age_phrase = _campaign_age_phrase(c.get("start_date"))
            age_days = _campaign_age_days(c.get("start_date"))
            is_new = age_days is not None and age_days < 90
            small = spend < max(100.0, 0.05 * (account_spend or 0))
            cap_low = (ceiling and typical_cpc and ceiling < 0.5 * typical_cpc)

            if is_new and conv < 15:
                issues.append(
                    f"The '{name}' campaign is new, so Maximise Clicks is sensible for now: it started "
                    f"{age_phrase} and has only {clicks} clicks and {conv:.0f} conversions  -  too little data "
                    "for conversion-based bidding yet. Leave it on Maximise Clicks (ideally with a sensible "
                    "maximum CPC) and move it to Maximise Conversions once it has steady conversion data. "
                    "This is not a current problem."
                )
            elif small:
                cap_clause = (f", with a £{ceiling:.2f} maximum CPC" if ceiling
                              else ", with no maximum CPC set")
                age_clause = f" It started {age_phrase}." if age_phrase else ""
                if cap_low:
                    market_clause = (f" That £{ceiling:.2f} cap sits well below the account's typical click "
                                     f"cost of about £{typical_cpc:.0f}, so it only wins the cheapest, "
                                     "lowest-intent clicks.")
                    fix = "raise or remove the low CPC cap so it can compete, or pause it"
                else:
                    market_clause = ""
                    fix = "review whether it earns its place, or move it to Maximise Conversions later"
                issues.append(
                    f"The '{name}' campaign uses Maximise Clicks but is a small, low-spend campaign: "
                    f"£{spend:.0f} spent and {conv:.0f} conversions in the last 30 days{cap_clause}."
                    f"{age_clause}{market_clause} It is a minor campaign rather than a core issue  -  {fix}  -  "
                    "but it is not where the account's budget is being lost."
                )
            elif not ceiling:
                ct = costly_terms.get(c.get("id")) or {}
                costly_clause = ""
                if ct.get("cpc"):
                    term_bit = f" on the search term '{ct['term']}'" if ct.get("term") else ""
                    costly_clause = (f"  -  the priciest click in the last 30 days cost about "
                                     f"£{ct['cpc']:.0f}{term_bit}")
                issues.append(
                    f"The '{name}' campaign uses Maximise Clicks with no maximum CPC limit set, and has spent "
                    f"£{spend:.0f} in the last 30 days. Without a CPC ceiling Google can pay far more per click "
                    f"than a lead is worth{costly_clause}. Set a sensible maximum CPC now, then move to "
                    "Maximise Conversions once conversion data is solid so spend follows leads, not visits."
                )
            else:
                issues.append(
                    f"The '{name}' campaign uses Maximise Clicks (optimising for traffic, not leads) and has "
                    f"spent £{spend:.0f} with {conv:.0f} conversions in the last 30 days; it started {age_phrase}, "
                    f"with a £{ceiling:.2f} maximum CPC. With this much history it is established enough to move "
                    "to Maximise Conversions once tracking is solid, so spend follows leads rather than visits."
                )

    # ── Priciest single clicks: averages hide the spikes. The SQR shows only an average
    # CPC per term, so a one-off very expensive click sits unnoticed next to cheap ones.
    # By segmenting daily, a term-day with one click reveals the TRUE single-click cost.
    # We surface the biggest single clicks that are a large MULTIPLE of the account's
    # average CPC - a concrete illustration of how automated bidding quietly spends budget.
    priciest = data.get("priciest_clicks") or []
    _acct_cpc = _account_search_cpc(campaigns)
    if priciest and _acct_cpc and _acct_cpc > 0:
        spikes = [p for p in priciest
                  if (p.get("cpc", 0) or 0) >= 3 * _acct_cpc and (p.get("clicks") or 0) <= 3]
        spikes = sorted(spikes, key=lambda x: x.get("cpc", 0) or 0, reverse=True)[:3]
        if spikes:
            egs = []
            for p in spikes:
                mult = (p["cpc"] / _acct_cpc) if _acct_cpc else 0
                single = "a single click" if (p.get("clicks") or 0) == 1 else f"{p.get('clicks')} clicks"
                conv_note = " and produced no conversions" if (p.get("conversions") or 0) == 0 else ""
                egs.append(
                    f"'{p['term']}' paid £{p['cpc']:.0f} for {single} on {_pretty_date(p.get('date'))} "
                    f"({mult:.0f}x the account's ~£{_acct_cpc:.0f} average CPC){conv_note}"
                )
            issues.append(
                "Automated bidding paid some very expensive single clicks last month that the average CPC "
                "hides: " + "; ".join(egs) + ". The search term report only shows an average CPC per term, so "
                f"a one-off £{spikes[0]['cpc']:.0f} click sits unnoticed beside cheaper ones. This is how smart "
                "bidding can quietly spend budget - worth a maximum-CPC sense-check and tighter negatives so the "
                "algorithm cannot overpay for low-intent or competitor clicks."
            )
            if rag == "green":
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
                    f"Campaign '{c.get('name')}' has a target CPA of £{tcpa:,.0f} but actual CPA is £{cpa:,.0f}. "
                    "When the target is set much lower than actual performance, Google throttles spend "
                    "chasing an unachievable goal  -  raise the target CPA closer to actual performance, "
                    "then reduce it incrementally once volume is stable."
                )
                if rag == "green":
                    rag = "amber"

    # CPA check (cpa is None when there are 0 conversions  -  guard against it).
    # Reframe: for a high-LTV product, cost-per-lead in isolation is the wrong lens. The
    # client already states the goal is a lower CPL, so don't just say "confirm it aligns" -
    # make the QUALITY point: tightening the account may RAISE CPA while improving the
    # enquiries that become revenue, and without OCI quality is invisible.
    if cpa and cpa > 150:
        _ltv = data.get("ltv_note", "")
        _ltv_clause = (f"Given the product's lifetime value ({_ltv} per customer), a higher cost "
                       "per lead is justified IF the enquiries are genuine"
                       if _ltv else
                       "For a high-value product a higher cost per lead can be justified IF the "
                       "enquiries are genuine")
        issues.append(
            f"Cost per conversion is £{cpa:.0f}. The stated goal is a lower cost per lead, but cost "
            f"alone is the wrong lens. {_ltv_clause}  -  so the priority is lead QUALITY, not just a "
            "lower number. Without offline conversion import (OCI) that quality is invisible. Tightening "
            "the account (lifting Quality Score, removing non-converting competitor terms) may even RAISE "
            f"the cost per lead while improving the enquiries that become revenue  -  paying around £{cpa:.0f} "
            "for a misdirected competitor enquiry is the real waste, not the headline number."
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
                d = f"the '{p['name']}' campaign (historic CPA £{p.get('cpa', 0):,.0f}, {int(round(p.get('conversions', 0)))} conv)"
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

        # If the conversion setup shows possible double-counting, those "cheap" historic CPAs
        # may be ~half the true cost - caveat it rather than presenting them at face value.
        _dup_caveat = ""
        _primary = [ca for ca in (data.get("conversion_actions") or [])
                    if ca.get("status") == "ENABLED" and ca.get("include_in_conversions")]
        _call_cluster_n = sum(1 for ca in _primary
                              if ca.get("category") in {"PHONE_CALL_LEAD", "CONTACT", "GET_DIRECTIONS", "CALL"})
        from collections import Counter as _C
        _dupcat = any(n >= 2 for n in _C(ca.get("category") for ca in _primary).values())
        if _dupcat or _call_cluster_n >= 2:
            _dup_caveat = (" Important: the conversion setup shows possible double-counting, so these historic "
                           "CPAs may be roughly half the true cost - verify against the back-end enquiry count "
                           "before trusting them.")
        issues.append(
            f"{len(efficient_paused)} paused campaign(s) historically delivered genuine leads below the "
            f"account's current £{cpa:.0f} CPA: {names}.{verify}{_dup_caveat} Worth reviewing whether lead "
            "quality, not cost, drove the pause before deciding on reactivation."
        )
        if rag == "green":
            rag = "amber"

    # ── Target ROAS restricting delivery (ecommerce): a campaign told to hit a higher
    # return than it is actually achieving gets its bids squeezed - Google holds spend
    # back rather than 'fail' the target. Classic on value-bid Shopping/PMax accounts
    # where one blanket tROAS is applied across campaigns with different economics.
    _troas_restricted = []
    for c in campaigns:
        if c.get("status") != "ENABLED":
            continue
        tgt = c.get("target_roas")
        spend = c.get("spend_30d", 0) or 0
        value = c.get("conv_value_30d")
        if tgt and value is not None and spend >= 100:
            actual = value / spend if spend else 0
            if actual < tgt * 0.8:
                _troas_restricted.append((c.get("name"), tgt, actual, spend))
    if _troas_restricted:
        _troas_restricted.sort(key=lambda x: -x[3])
        _egs = "; ".join(f"'{n}' targets {t:.0f}x but is achieving about {a:.1f}x"
                         for n, t, a, _s in _troas_restricted[:3])
        _same_tgt = len({round(t) for _n, t, _a, _s in _troas_restricted}) == 1 and len(_troas_restricted) >= 2
        issues.append(
            f"{len(_troas_restricted)} campaign(s) look restricted by their target ROAS - the target is "
            f"set well above what the campaign actually achieves, so bidding holds spend back to protect "
            f"the target: {_egs}." +
            (" The same target is applied across campaigns with different actual returns, which squeezes "
             "the weaker ones hardest." if _same_tgt else "") +
            " Testing a target closer to the achieved figure (then walking it up) usually unlocks volume."
        )
        if rag == "green":
            rag = "amber"

    # ── Shared budgets: campaigns drawing from one pool let the budget decide spend
    # priority, not the strategy. Worth surfacing whenever 2+ enabled campaigns share.
    _shared = {}
    for c in campaigns:
        if c.get("status") == "ENABLED" and c.get("shared_budget") and c.get("budget_resource"):
            _shared.setdefault(c["budget_resource"], []).append(c.get("name"))
    _shared_groups = [v for v in _shared.values() if len(v) >= 2]
    if _shared_groups:
        _g = max(_shared_groups, key=len)
        issues.append(
            f"{sum(len(g) for g in _shared_groups)} campaigns share a single campaign budget "
            f"(e.g. {', '.join(_g[:4])}{'...' if len(_g) > 4 else ''}). A shared pool decides spend "
            "priority between campaigns by itself, so stronger campaigns can starve the others and "
            "individual budget control is lost. Testing independent budgets for the priority "
            "campaigns gives back control over where money goes."
        )
        if rag == "green":
            rag = "amber"

    # Zero conversions with spend
    if total_conversions == 0 and total_cost > 50:
        issues.append(
            f"£{total_cost:,.0f} spent with 0 conversions. "
            "Resolve conversion tracking before optimising bidding strategy."
        )
        rag = "red"

    if not issues:
        cpa_note = f", CPA £{cpa:,.0f}" if cpa else ""
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
        # Pair each capped campaign with its Ad Rank loss too, so we never imply budget is the
        # only lever - a campaign also losing share to rank needs Quality Score work, not just money.
        def _cap_eg(c):
            b = c.get("lost_budget", 0) or 0
            r = c.get("lost_rank", 0) or 0
            rank_bit = f", and {r:.0f}% to Ad Rank" if r >= 10 else ""
            return f"'{c['campaign']}' (lost {b:.0f}% to budget{rank_bit})"
        names = ", ".join(_cap_eg(c) for c in budget_capped[:3])
        issues.append(
            f"{len(budget_capped)} Search campaign(s) lost impressions to budget - they stop showing because "
            f"the budget runs out, not because demand dries up: {names}. Where these convert efficiently you "
            "are leaving leads on the table every day; raise their budget or reallocate from weaker activity. "
            "Where a campaign is ALSO losing share to Ad Rank, budget alone will not fix it - improving Quality "
            "Score and ad relevance matters there too."
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
        # Magnitude: how much do these campaigns actually spend? A leak on a £20/mo campaign is
        # a footnote; on the account's main campaigns it's a headline. Severity follows the money.
        _camps = data.get("campaigns", [])
        _poi_names = {c.get("campaign") for c in poi}
        _poi_spend = sum((c.get("spend_30d") or 0) for c in _camps
                         if c.get("name") in _poi_names and c.get("status") == "ENABLED")
        _acct_spend = (data.get("account_summary_30d") or {}).get("spend") or sum(
            (c.get("spend_30d") or 0) for c in _camps if c.get("status") == "ENABLED")
        _pct = (_poi_spend / _acct_spend) if _acct_spend else 0
        _mag = (f" These campaigns carry about £{_poi_spend:.0f} of spend in the last 30 days"
                + (f" - {_pct:.0%} of the account" if _acct_spend else "") + ".")
        # The REAL out-of-area figure from user_location_view (targeting_location=False = users
        # not physically in a targeted location). Turns the old "exposure, can't confirm" caveat
        # into a measured number - either a hard leak to quantify, or honest reassurance.
        _geo = data.get("geo_user_location") or {}
        _ooa_spend = _geo.get("out_of_area_spend")
        _ooa_pct = _geo.get("out_of_area_pct") or 0
        if _ooa_spend is not None and _geo.get("total_spend"):
            # Name where the out-of-area spend actually landed - concrete substance for the slide
            # (and it lets the client decide which areas, if any, they want to keep).
            _fc = _geo.get("top_foreign_countries") or []
            _areas = ""
            if _fc:
                _areas = (f" The top countries reached outside {_geo.get('target_country') or 'your target country'} "
                          "were " + ", ".join(f"{c['country']} (£{c['spend']:.0f})" for c in _fc[:3]) + ".")
            if _ooa_spend >= max(20.0, 0.02 * _geo["total_spend"]):
                _real = (f" The geographic report confirms the leak is real: in the last 30 days "
                         f"£{_ooa_spend:.0f} ({_ooa_pct:.0%} of spend) went to clicks from people "
                         f"NOT physically in your targeted area.{_areas}")
            elif _ooa_spend < 1:
                _real = (" Encouragingly, the geographic report shows this exposure has not turned "
                         "into waste yet - effectively none of your spend reached people outside "
                         "your targeted area in the last 30 days. The risk is live, though, so it is "
                         "still worth closing.")
            else:
                _real = (f" Encouragingly, the geographic report shows this exposure has barely "
                         f"converted into waste so far - only about £{_ooa_spend:.0f} "
                         f"({_ooa_pct:.0%} of spend) came from people outside your targeted area in "
                         f"the last 30 days. The risk is live, though, so it is still worth closing.")
        else:
            _real = (" (The exact out-of-area share needs a geographic report to confirm.)")
        material = _poi_spend >= max(100.0, 0.10 * (_acct_spend or 0))
        if material:
            local_note = (" For a local business this is a silent leak worth closing."
                          if account_type in ("lead_gen", "unknown", "mixed") else "")
            issues.append(
                f"{len(poi)} campaign(s) use the 'Presence or interest' location setting - Google's default: "
                f"{names}.{_mag} The setting shows your ads to people merely INTERESTED in your area, not only "
                f"those actually in it - so with it active across {_pct:.0%} of your spend, your whole budget is "
                f"EXPOSED to out-of-area clicks.{_real}"
                f"{local_note} Switching to 'Presence (people in, or regularly in, your locations)' is one of "
                "the highest-ROI fixes there is - it typically cuts wasted spend and lowers cost per lead."
            )
        else:
            issues.append(
                f"Location targeting is set to 'Presence or interest' on low-spend campaigns: {names}.{_mag} "
                "The leak is small for now, but worth switching to 'Presence (people in, or regularly in, your "
                "locations)' as a tidy-up - and revisit it if you scale these budgets."
            )
        rag = "amber"

    # ── Cross-border spend (users physically in a DIFFERENT country) ──────────
    # Distinct from the in-country interest leak above: this is budget reaching people in
    # an entirely different country. Only flag when it's material and named.
    _geo = data.get("geo_user_location") or {}
    _foreign = _geo.get("foreign_country_spend") or 0
    _total = _geo.get("total_spend") or 0
    if _total and _foreign >= max(30.0, 0.03 * _total) and _geo.get("top_foreign_countries"):
        _fc = _geo["top_foreign_countries"]
        _named = ", ".join(f"{c['country']} (£{c['spend']:.0f})" for c in _fc[:3])
        _fpct = _geo.get("foreign_country_pct") or 0
        issues.append(
            f"£{_foreign:.0f} ({_fpct:.0%} of spend) in the last 30 days reached people physically located "
            f"OUTSIDE {_geo.get('target_country') or 'your target country'} - top sources: {_named}. Unless you "
            "knowingly sell abroad, this is wasted reach. Tighten location targeting to your service country and "
            "add the worst offenders as negative locations."
        )
        if rag == "green":
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
