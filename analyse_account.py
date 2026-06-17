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
    if has_purchase and data.get("claims_ecom"):
        # Nothing money-shaped is recording, but purchase actions exist AND the client
        # says the business is pure ecommerce: that's an ecommerce account whose sales
        # tracking is broken, not a lead-gen account (The Beatles Story, June 2026 -
        # only £1-valued page-view actions recorded; the ticket purchase action was silent).
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


def _decorate_perf_trends(perf):
    """Add '*_trend' keys ('▲ +12%' / '▼ -8%' / '▶ +2%') to the performance summary,
    comparing the last 30 days against the 12-month run-rate so the deck can anchor
    'improving vs declining' at a glance (Dan, 11 June 2026).

    Volume metrics (spend, impressions, clicks, conversions) compare against 1/12th of
    the 12-month total; rate metrics (CVR, CPA, SIS, ROAS) compare against the 12-month
    value directly. Skipped entirely for young accounts (12m spend < 2.5x 30d spend),
    where 'a twelfth of the total' would scream growth that is really just account age.
    """
    raw = perf.get("_raw") or {}
    t30, t12 = raw.get("t30") or {}, raw.get("t12") or {}
    if not t30 or not t12:
        return perf
    if (t12.get("spend") or 0) < 2.5 * (t30.get("spend") or 0):
        return perf

    def _trend(cur, base):
        if not cur or not base:
            return ""
        pct = (cur - base) / base * 100
        arrow = "▲" if pct >= 3 else ("▼" if pct <= -3 else "▶")
        # Beyond +200% a percentage stops reading naturally ('+3414%') - show a multiple.
        if pct >= 200:
            return f"{arrow} {cur / base:.1f}x"
        return f"{arrow} {pct:+.0f}%"

    for key, formatted in (("spend", "spend_30d"), ("impressions", "impr_30d"),
                           ("clicks", "clicks_30d"), ("conversions", "convs_30d")):
        if perf.get(formatted) not in (None, "", "N/A"):
            perf[f"{formatted}_trend"] = _trend(t30.get(key), (t12.get(key) or 0) / 12.0)
    for key, formatted in (("cvr", "cvr_30d"), ("cpa", "cpa_30d"),
                           ("sis", "sis_30d"), ("roas", "roas_30d")):
        if perf.get(formatted) not in (None, "", "N/A"):
            perf[f"{formatted}_trend"] = _trend(t30.get(key), t12.get(key))
    return perf


def analyse_account(data, raw_questionnaire=""):
    # The questionnaire carries the client's stated competitors + product value; the analyser
    # needs both - competitors to flag rival search terms (don't sell a rival's name as "new
    # demand"), and LTV to judge CPA against value rather than as a number in isolation.
    if "competitors" not in data:
        data["competitors"] = parse_competitors_from_questionnaire(raw_questionnaire)
    if "ltv_note" not in data:
        data["ltv_note"] = parse_ltv_note(raw_questionnaire)
    if "claims_lead_gen" not in data:
        # Does the client SAY they do lead gen (or both)? If so and nothing lead-shaped
        # is recording, that half of the business is invisible - a top-tier finding.
        import re as _re
        _m = _re.search(r'e[- ]?com(?:merce)?\s+or\s+lead\s*gen[^:]*:\s*(.+)',
                        str(raw_questionnaire or ""), _re.I)
        data["claims_lead_gen"] = bool(_m and _re.search(r'lead|both', _m.group(1), _re.I))
        # Pure-ecommerce claim - used by detect_account_type when conversion volumes
        # can't decide (e.g. only page views are recording).
        data["claims_ecom"] = bool(_m and _re.search(r'e[- ]?com', _m.group(1), _re.I)
                                   and not _re.search(r'lead|both', _m.group(1), _re.I))
    if not str(data.get("account_name") or "").strip() and raw_questionnaire:
        # The API's descriptive_name can come back blank - fall back to the client name
        # on the questionnaire's first line so brand detection still works (Beatles
        # Story, June 2026: blank name meant every brand search read as a competitor's).
        _first = next((ln.strip() for ln in str(raw_questionnaire).splitlines()
                       if ln.strip()), "")
        if _first and len(_first) <= 60 and ":" not in _first:
            data["account_name"] = _first
    if "client_keywords" not in data:
        # The keywords the client SAYS they want ("Keywords:" questionnaire line). These
        # are their own demand by definition - the competitor-term check must never
        # classify them as somebody else's business name.
        import re as _re
        _km = _re.search(r'^Keywords?\s*:\s*(.+)$', str(raw_questionnaire or ""),
                         _re.I | _re.M)
        data["client_keywords"] = ([k.strip().lower() for k in _km.group(1).split(",")
                                    if k.strip()] if _km else [])
    if "international_audience" not in data:
        # Does the client say their audience is INTERNATIONAL / they sell to visitors from
        # abroad? A destination attraction or tourist business genuinely wants overseas
        # interest, so out-of-country clicks are not automatically "wasted" - the geo
        # findings below reframe to "decide, then run a dedicated international campaign
        # and budget" instead of "cut it" (The Beatles Story, June 2026: questionnaire
        # said "Our audience is international ... they book when in the country").
        import re as _re
        _geo_q = _re.search(r'Geograph\w*\s*Target[^:]*:\s*(.+)', str(raw_questionnaire or ""), _re.I)
        _aud_q = _re.search(r'Audience\s+to\s+target[^:]*:\s*(.+)', str(raw_questionnaire or ""), _re.I)
        _intl_blob = " ".join(m.group(1) for m in (_geo_q, _aud_q) if m).lower()
        data["international_audience"] = bool(
            _re.search(r'\b(international|worldwide|overseas|abroad|tourist|visitor|holiday|'
                       r'travel)\w*', _intl_blob))
    if "stated_roas_target" not in data:
        # The client's own ROAS bar (e.g. "Target ROAS 4:1 minimum") - lets bidding
        # findings anchor recommendations to THEIR number rather than a generic one.
        import re as _re
        _rt = _re.search(r'(?:target\s+)?ROAS\s+(\d+(?:\.\d+)?)\s*:\s*1',
                         str(raw_questionnaire or ""), _re.I)
        data["stated_roas_target"] = float(_rt.group(1)) if _rt else None
    if "stated_margin_pct" not in data:
        # Profit margin + LTV from the questionnaire power the break-even ROAS check:
        # at a 30% margin, break-even is ~3.3x - a 1.9x ROAS (or worse, a tROAS target
        # of 1.6) means every first order loses money, which is only fine if it is a
        # deliberate LTV play. These are THEIR numbers, so the maths lands personally.
        import re as _re
        _mg = _re.search(r'profit\s+margin[^:]*:\s*(\d+(?:\.\d+)?)\s*%',
                         str(raw_questionnaire or ""), _re.I)
        data["stated_margin_pct"] = float(_mg.group(1)) if _mg else None
        _lv = _re.search(r'\bLTV[^:]*:\s*£?\s*([\d,]+)', str(raw_questionnaire or ""), _re.I)
        data["stated_ltv_gbp"] = float(_lv.group(1).replace(",", "")) if _lv else None
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
        "stated_margin_pct":   data.get("stated_margin_pct"),
        "performance_summary": _decorate_perf_trends(data.get("performance_summary", {})),
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
                      "offline conversion imports (OCI)",
                      # App accounts track one action per platform (Android + iOS) by design,
                      # so same-category primaries are parallel coverage, not double-counting.
                      "Possible conversion double-counting",
                      "call or contact actions set as primary conversions",
                      "appear to track different parts of the business")
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

    # The single tabular finding that wins the dedicated table slide (highest severity,
    # geo first on ties). None when no finding has table data. generate_narrative reads
    # this both to fill the slide and to drop the chosen finding from the issue slides.
    findings["table"] = select_table(findings, data)

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
    ("counting orders without passing their value", 75, "amber_red", "Conversion Tracking"), # £0-value purchase action - ROAS understated
    ("Possible conversion double-counting",        74, "amber_red", "Conversion Tracking"),  # data integrity - undermines all CPAs (volumes move TOGETHER)
    ("call or contact actions set as primary conversions", 45, "amber", "Conversion Tracking"),  # multiple call actions, volumes INDEPENDENT -> tidy-up, not confirmed double-count
    ("appear to track different parts of the business", 58, "amber", "Conversion Tracking"),  # parallel streams UNCONFIRMED (could be migration tag) - verify; CONFIRMED two-site is suppressed as structure
    ("paid some very expensive single clicks",     50, "amber",     "Bidding Strategy"),  # CPC spike = exposure/risk, not measured waste -> below substantive findings (Dan, 13 Jun)
    # Bidding  -  Maximise Clicks branches (specific first; severity follows the money)
    ("Maximise Clicks with no maximum CPC limit set", 82, "amber",  "Bidding Strategy"),  # material spend, uncapped = real leak
    ("uses Maximise Clicks (optimising for traffic",  60, "amber",  "Bidding Strategy"),  # material spend, capped, wrong strategy
    ("uses Maximise Clicks but is a small",           33, "amber",  "Bidding Strategy"),  # tiny/starved -> Additional Observations
    ("is new, so Maximise Clicks is sensible",        30, "amber",  "Bidding Strategy"),  # new/low-data -> not a problem yet
    ("on Maximise Clicks",                         78, "amber",     "Bidding Strategy"),
    ("paused campaign(s) historically delivered",  66, "amber",     "Bidding Strategy"),
    ("restricted by their target ROAS",            65, "amber",     "Bidding Strategy"),  # tROAS above achieved = bids squeezed
    ("bidding target is set below break-even",     72, "amber_red", "Bidding Strategy"),  # tROAS below margin break-even - buying losses by design
    ("below break-even on your own numbers",       63, "amber",     "Revenue & Value"),   # actual ROAS below margin break-even
    ("Most of the product catalogue is not being advertised", 69, "amber", "Account Structure"),  # benched in-stock products
    ("Shopping and Performance Max campaigns are all switched off", 64, "amber", "Account Structure"),  # dormant feed channel on an ecom store - ask why
    ("gone quiet despite past revenue",            56, "amber",     "Account Structure"),  # dark proven sellers only
    ("split between Performance Max and a standard Shopping", 55, "amber", "Account Structure"),  # split estate
    ("steer bidding on an ecommerce account",      57, "amber",     "Conversion Tracking"),  # call/lead primary on pure ecom
    ("share a single campaign budget",             56, "amber",     "Budget & Coverage"),  # shared pool decides priority
    ("receiving spend from more than one campaign", 61, "amber",    "Account Structure"),  # product overlap / self-competition
    ("triggering ads in more than one campaign",   59, "amber",     "Account Structure"),  # search-term cannibalisation (material)
    ("A small overlap of search terms",            35, "amber",     "Account Structure"),  # cannibalisation watch-item -> Observations
    ("still on Manual CPC despite",                62, "amber",     "Bidding Strategy"),
    ("on Manual CPC.",                             58, "amber",     "Bidding Strategy"),
    ("has a target CPA of",                        55, "amber",     "Bidding Strategy"),
    ("on smart bidding recorded only",             50, "amber",     "Bidding Strategy"),
    ("using inconsistent bid strategies",          46, "amber",     "Bidding Strategy"),
    ("Cost per conversion is",                     48, "amber",     "Bidding Strategy"),
    # Efficiency / coverage / settings (expert checks)
    ("set to 'Presence or interest' on low-spend", 34, "amber",     "Budget & Coverage"),  # small leak -> Observations
    ("the actual leak is small so far",            42, "amber",     "Budget & Coverage"),  # POI measured-small -> Observations
    ("use the 'Presence or interest' location",    76, "amber",     "Budget & Coverage"),  # #1 local waste leak (confirmed big or unmeasured)
    ("A network setting worth tidying",            36, "amber",     "Budget & Coverage"),  # opt-in confirmed tiny -> Observations
    ("opted into",                                 62, "amber",     "Budget & Coverage"),  # Search Partners / Display
    ("lost impressions to budget",                 66, "amber",     "Budget & Coverage"),  # IS lost to budget
    ("reach only a small slice of the demand that is already out there", 58, "amber", "Ad Rank & Quality"),  # IS lost to rank (SIS/TAM framing)
    ("disapproved and silently not serving",       78, "amber_red", "Ads & Assets"),       # disapproved ads - dark ad groups
    ("approved but LIMITED by policy",             38, "amber",     "Ads & Assets"),       # policy-limited -> Observations
    ("No changes have been made to the account",   67, "amber",     "Account Structure"),  # unmanaged account (neglect)
    ("ran between midnight and 6am",               55, "amber",     "Budget & Coverage"),  # overnight waste (lead gen)
    ("a device that is not converting",            52, "amber",     "Budget & Coverage"),  # device gap
    ("usually comes from an automated feed",       30, "amber",     "Account Structure"),  # SKU-scale structure -> Observations
    ("missing high-value extension types",         60, "amber",     "Ads & Assets"),       # missing extensions
    ("Call extensions are not set up",             42, "amber",     "Ads & Assets"),       # ecom: optional, Observation tier (Dan, 13 Jun)
    ("have a LOW score (4 or below)",              54, "amber",     "Ad Rank & Quality"),  # low Quality Score
    # Targeting & keywords
    ("look like competitor business names",         63, "amber",     "Targeting & Keywords"),  # competitor terms (reframe)
    ("Average order value has dropped hard",       70, "amber",     "Revenue & Value"),      # AOV collapse - explains a falling ROAS
    ("Fading winner spotted by comparing",         72, "amber",     "Targeting & Keywords"),  # cross-window pattern (high value)
    ("A small amount of non-converting spend",     34, "amber",     "Targeting & Keywords"),  # tiny leak -> Observations
    ("without converting",                         63, "amber",     "Targeting & Keywords"),  # wasted SQR spend (material)
    ("are blocking searches that have CONVERTED",  73, "amber_red", "Targeting & Keywords"),  # negative conflict - silent sabotage
    ("an unusually large list",                    33, "amber",     "Targeting & Keywords"),  # huge negative list, no conflicts -> Observations
    ("repeatedly generated page-view 'conversions'", 45, "amber",   "Targeting & Keywords"),  # keyword candidates, but evidence is page views (purchase tracking silent)
    ("are NOT added as active keywords",           68, "amber",     "Targeting & Keywords"),  # converting queries, 3+ conv (high-value "dropped ball")
    ("early signals rather than statistical proof", 52, "amber",    "Targeting & Keywords"),  # converting queries, 1-2 conv each (watch-and-test)
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
    ("No purchases are reaching Google Ads",       88, "red",       "Conversion Tracking"),  # ecom purchase tag silent all year; page views steer bidding (Beatles Story)
    ("offline conversion imports (OCI)",           70, "amber",     "Conversion Tracking"),  # the elephant (lead gen) - paramount
    ("Revenue feedback loop",                      64, "amber",     "Conversion Tracking"),  # the ecommerce OCI equivalent
    ("appear set up but have recorded nothing",    58, "amber",     "Conversion Tracking"),  # dead genuine action (broken tag)
    ("Enhanced conversions for leads is not enabled", 50, "amber",  "Conversion Tracking"),  # EC-for-leads off (API-visible half)
    ("no lead-type conversion action",              63, "amber",    "Conversion Tracking"),  # claims lead gen, lead side invisible
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
    ("No Customer Match list is set up",           46, "amber",     "Account Structure"),  # first-party audience opportunity (Observation)
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
    # Zero-value purchase tag vs the generic revenue-feedback finding: same client story
    # ("order value isn't flowing back"), so show only the sharper, evidence-based one.
    "counting orders without passing their value": "revenue_value_feedback",
    "Revenue feedback loop":                   "revenue_value_feedback",
}


def _theme_for(detail):
    for needle, theme in _ISSUE_THEMES.items():
        if needle in detail:
            return theme
    return None

import re as _re

# Findings that quote HISTORICAL or projected pounds as evidence (not a current measured
# leak) must NOT get the money bump in select_top_issues - otherwise past performance inflates
# present severity, breaking the "severity follows MEASURED money" rule. Match on a distinctive
# substring of the finding (e.g. a paused channel's past sales value).
_NO_MONEY_BUMP_SIGNATURES = (
    "Shopping and Performance Max campaigns are all switched off",  # dormant-feed: pounds are past sales
)


def _largest_pound(text):
    """Largest £ figure in a finding, used as a small commercial-magnitude tie-break."""
    vals = []
    for m in _re.findall(r"£([\d,]+)", text or ""):
        try:
            vals.append(float(m.replace(",", "")))
        except ValueError:
            pass
    return max(vals) if vals else 0.0


def _material_domains(data, min_share=0.10, min_count=5):
    """Destination domains the account MATERIALLY advertises (from enabled ad final URLs).
    Filters out stray off-domain links (a redirect, a one-off partner page) so two domains
    here is solid proof of two storefronts. Returns the domains, busiest first."""
    doms = data.get("ad_destination_domains") or {}
    total = sum(doms.values())
    if not total:
        return []
    return [d for d, c in doms.items() if c >= min_count and c / total >= min_share]


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
            if any(sig in detail for sig in _NO_MONEY_BUMP_SIGNATURES):
                mag = 0.0   # pounds here are historical evidence, not a current leak
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


# ─────────────────────────────────────────────────────────────────────────────
# DATA-TABLE EMITTERS
# The deck has ONE dedicated table slide. A single tabular finding wins it: the
# highest-severity finding that can be shown as a table fills it and is dropped
# from the 6 issue slides (the next-ranked finding backfills) - exactly how geo
# has always worked. Each emitter returns a table payload
# {title, happening, header (3 cols), rows (<=4 data rows), recommendation} or None.
# The template table is 5 rows x 3 cols (header + 4 data rows), so emitters MUST
# cap at 4 data rows and 3 columns, and truncate long cell text.
# ─────────────────────────────────────────────────────────────────────────────

def _truncate_cell(text, n=28):
    """Collapse whitespace and trim a cell value so it stays on ONE line in a table cell.
    Wrapping doubles a row's height and the table then collides with the recommendation
    box below it, so keep the cap tight (~28 chars fits the 3-column template's width)."""
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[:n].rstrip() + "..."


def _product_overlap_table(data, account_type):
    """Emitter: products taking spend in more than one campaign at once (Shopping/PMax).
    Ecommerce only. Caps at 4 data rows (top 3 + a '...and N more' row when there are
    more), 3 columns. Returns a table payload or None."""
    if account_type != "ecommerce":
        return None
    overlap = data.get("product_overlap") or []
    if not overlap:
        return None
    overlap = sorted(overlap, key=lambda p: p.get("total_spend", 0) or 0, reverse=True)
    # Row cap: the template holds 4 data rows. Show the top 4 when that's all there is,
    # otherwise the top 3 plus a summary row so the count is never hidden.
    if len(overlap) <= 4:
        shown, extra = overlap, 0
    else:
        shown, extra = overlap[:3], len(overlap) - 3
    rows = []
    for p in shown:
        n_camp = len(p.get("campaigns") or [])
        rows.append([
            _truncate_cell(p.get("title", "")),     # one-line cap (default 28)
            f"{n_camp} campaigns",
            f"£{(p.get('total_spend', 0) or 0):,.0f}",
        ])
    if extra:
        rows.append([f"...and {extra} more product(s)", "", ""])
    worst = overlap[0]
    worst_n = len(worst.get("campaigns") or [])
    happening = (
        f"{len(overlap)} product(s) are taking spend from more than one campaign at the "
        f"same time, so your own campaigns bid against each other. The worst, "
        f"'{_truncate_cell(worst.get('title', ''), 40)}', ran in {worst_n} campaigns at once:"
    )
    recommendation = (
        "Give each product a single home campaign and exclude it from the others. That "
        "pools its performance data and stops your campaigns competing in the same "
        "auctions, which brings click costs back down."
    )
    return {
        "title": "Products splitting spend across campaigns",
        "happening": happening,
        "header": ["Product", "Campaigns", "Spend (30 days)"],
        "rows": rows,
        "recommendation": recommendation,
    }


def _sis_rag(s):
    """RAG for a search impression share % (Dan's bands, 15 Jun 2026): red below 50%,
    amber 50-70%, green above 70%. Empty for unknown."""
    if s is None:
        return ""
    if s < 50:
        return "🔴"
    if s <= 70:
        return "🟠"
    return "🟢"


def _impression_share_table(data):
    """Emitter: Search campaigns appearing on only a fraction of the searches they are
    eligible for (low search impression share). Uses the team's house terminology - SIS
    (search impression share), SLIB (lost to budget), SLIR (lost to rank) - with a RAG on
    each campaign's SIS. Caps at 4 data rows, 3 columns. Any account type. Returns a table
    payload or None. Materiality matches the two IS findings: lost-to-budget >= 10% or
    lost-to-rank >= 30%."""
    isl = data.get("impression_share_lost") or []
    material = [c for c in isl
               if (c.get("lost_budget", 0) or 0) >= 10 or (c.get("lost_rank", 0) or 0) >= 30]
    if not material:
        return None
    # Worst visibility first: the lowest share of eligible searches.
    material.sort(key=lambda c: (c.get("sis") if c.get("sis") is not None else 100))
    if len(material) <= 4:
        shown, extra = material, 0
    else:
        shown, extra = material[:3], len(material) - 3
    rows = []
    for c in shown:
        s = c.get("sis")
        b = c.get("lost_budget", 0) or 0
        r = c.get("lost_rank", 0) or 0
        sis_cell = f"{s:.0f}% {_sis_rag(s)}".strip() if s is not None else "-"
        # Name the loss the way the team does: SLIB (budget) and SLIR (rank). Show the material
        # ones so the fix is obvious; both fit the cell at any realistic percentages.
        losses = []
        if b >= 10:
            losses.append(f"{b:.0f}% SLIB")
        if r >= 10:
            losses.append(f"{r:.0f}% SLIR")
        rows.append([_truncate_cell(c.get("campaign", "")), sis_cell, " / ".join(losses) or "-"])
    if extra:
        rows.append([f"...and {extra} more campaign(s)", "", ""])
    worst = shown[0]
    ws = worst.get("sis")
    worst_share = f"{ws:.0f}%" if ws is not None else "a small share"
    happening = (
        f"Search impression share (SIS) is your share of the relevant searches your ads could show on - "
        f"higher is better, except for deliberately broad campaigns. We rate it red below 50%, amber 50-70%, "
        f"green above 70%. '{_truncate_cell(worst.get('campaign', ''))}' shows on just {worst_share} {_sis_rag(ws)}. "
        "SLIB is share lost to budget, SLIR is lost to rank:"
    )
    recommendation = (
        "Where a campaign loses share to budget (SLIB) and converts well, raise its budget to capture the "
        "demand. Where it loses to rank (SLIR), that is a quality and bid problem, not money - improve "
        "keyword-to-ad relevance, ad copy and landing pages. Both recover relevant searches you are already "
        "eligible to win, without simply spending more."
    )
    return {
        "title": f"Search impression share (SIS) {_sis_rag(ws)}".strip(),
        "happening": happening,
        "header": ["Campaign", "SIS", "Lost to"],
        "rows": rows,
        "recommendation": recommendation,
    }


_CONV_CATEGORY_LABELS = {
    "PURCHASE": "Purchase", "SUBMIT_LEAD_FORM": "Lead form", "LEAD": "Lead",
    "CONTACT": "Contact", "PHONE_CALL_LEAD": "Phone call", "BOOK_APPOINTMENT": "Booking",
    "REQUEST_QUOTE": "Quote request", "SIGNUP": "Sign-up", "PAGE_VIEW": "Page view",
    "ENGAGEMENT": "Engagement", "DOWNLOAD": "Download", "OUTBOUND_CLICK": "Outbound click",
    "GET_DIRECTIONS": "Get directions", "BEGIN_CHECKOUT": "Begin checkout",
    "ADD_TO_CART": "Add to cart", "IMPORTED_LEAD": "Imported lead", "STORE_VISIT": "Store visit",
}


def _pretty_category(cat):
    """Plain-English label for a conversion-action category (PAGE_VIEW -> Page view)."""
    return _CONV_CATEGORY_LABELS.get(cat) or (str(cat or "").replace("_", " ").capitalize() or "Other")


def _primary_actions_table(data):
    """Emitter: too many conversion actions set as primary, so bidding chases them all
    equally. Fires on the same threshold as the finding (>10 primary). Any account type.
    Cols: Conversion action | Type | Count (30 days). Caps at 4 data rows. Returns a
    payload or None."""
    cas = data.get("conversion_actions") or []
    active = [ca for ca in cas if ca.get("status") == "ENABLED"]
    primary = [ca for ca in active if (ca.get("primary_for_goal") or ca.get("include_in_conversions"))]
    if len(primary) <= 10:
        return None

    def _vol(ca):
        v = ca.get("attributed_conversions_30d")
        if v is None:
            v = ca.get("conversions_30d")
        return v or 0

    primary.sort(key=_vol, reverse=True)
    # Show the actions that actually record volume (a 0-count row reads as broken under a
    # 'busiest' framing); the summary row absorbs the rest. Fall back to the top few if none
    # record anything. The gate is >10 primary, so there is always a '...and N more' row.
    nonzero = [ca for ca in primary if _vol(ca) > 0]
    pool = nonzero if nonzero else primary
    shown = pool[:3]
    extra = len(primary) - len(shown)
    rows = []
    for ca in shown:
        rows.append([_truncate_cell(ca.get("name", "")),
                     _pretty_category(ca.get("category", "")),
                     f"{_vol(ca):,.0f}"])
    if extra > 0:
        rows.append([f"...and {extra} more action(s)", "", ""])
    happening = (
        f"{len(primary)} conversion actions are all set as primary, so bidding treats them as "
        "equally important when it decides what to chase. The ones recording the most:"
    )
    recommendation = (
        "Keep the single action that represents a real sale or enquiry as your one primary "
        "'Conversion' and move the rest to secondary. Bidding then optimises towards what actually "
        "makes you money, and your reporting stops counting soft signals as conversions."
    )
    return {
        "title": "Too many actions set as primary conversions",
        "happening": happening,
        "header": ["Conversion action", "Type", "Count (30 days)"],
        "rows": rows,
        "recommendation": recommendation,
    }


def _converting_terms_table(findings, data):
    """Emitter: search terms that converted in the last 90 days but are NOT added as active
    keywords, so proven demand is captured loosely (or not at all). Mirrors the converting-
    terms finding's severity split: 68 when at least one term has 3+ conversions (proven),
    else 52 (early signals, 1-2 conversions each). Any account type. Cols: Term | Conversions
    | Caught by keyword. Caps at 4 data rows. Returns a payload or None.

    When purchase tracking is silent the counts are page-view 'conversions' (interest, not
    sales), so the wording softens to match - we never sell page views as money-making demand."""
    terms = (findings.get("targeting_keywords") or {}).get("converting_terms") or []
    if not terms:
        return None
    silent = bool(data.get("_purchase_silent"))
    terms = sorted(terms, key=lambda t: (t.get("conversions", 0) or 0), reverse=True)
    proven = any((t.get("conversions", 0) or 0) >= 3 for t in terms)

    def _caught(t):
        kw = (t.get("keyword") or "").strip()
        mt = str(t.get("keyword_match_type") or "").strip().lower()
        if not kw:
            return "-"
        return _truncate_cell(f"{mt}: {kw}" if mt else kw)

    if len(terms) <= 4:
        shown, extra = terms, 0
    else:
        shown, extra = terms[:3], len(terms) - 3
    rows = [[_truncate_cell(t.get("term", "")), f"{int(t.get('conversions', 0) or 0):,}", _caught(t)]
            for t in shown]
    if extra:
        rows.append([f"...and {extra} more term(s)", "", ""])

    n = len(terms)
    conv_word = "page-view 'conversions'" if silent else "conversions"
    if proven:
        happening = (
            f"{n} search term(s) recorded {conv_word} in the last 90 days but are not added as "
            "active keywords, so the demand is captured loosely or not at all. The busiest, and the "
            "keyword (if any) loosely catching each:"
        )
    else:
        happening = (
            f"{n} search term(s) recorded {conv_word} in the last 90 days without being added as "
            "active keywords. None has more than a couple yet, so these are early signals to test, "
            "not proof. The pattern is what matters - nobody is harvesting the report:"
        )
    if silent:
        recommendation = (
            "These counts show interest, not sales, while purchase tracking is disconnected, so judge "
            "them properly once real orders are tracked. Even so the terms are keyword candidates: add "
            "the closest fits to gain control over bids, ad copy and landing pages, and make mining the "
            "search-term report a monthly habit."
        )
    else:
        recommendation = (
            "Promote the proven terms into dedicated keywords where search volume supports it (very "
            "low-volume terms are better captured by a closely related theme), to control bids, ad copy "
            "and landing pages directly. Make mining the search-term report part of the monthly routine "
            "so winners stop slipping through."
        )
    return {
        "title": "Converting searches not yet keywords",
        "happening": happening,
        "header": ["Search term", "Conversions", "Caught by keyword"],
        "rows": rows,
        "recommendation": recommendation,
        "_severity": 68 if proven else 52,
    }


def collect_table_candidates(findings, data):
    """Gather every per-finding table payload available for this account. Each candidate
    carries the severity it would headline at, a fixed priority for deterministic
    tie-breaks (geo > product-overlap > ...), and the detail substrings that identify the
    finding so the CHOSEN one can be excluded from the issue slides + observations."""
    account_type = findings.get("account_type") or detect_account_type(data)
    candidates = []

    # geo (priority 0) - the original emitter, built in score_efficiency.
    geo_table = (findings.get("efficiency") or {}).get("geo_table")
    if geo_table:
        candidates.append({**geo_table, "severity": 76, "priority": 0,
                           "topic_signatures": ["Presence or interest",
                                                "reached people physically located OUTSIDE"]})

    # product overlap (priority 1) - ecommerce self-competition.
    overlap_table = _product_overlap_table(data, account_type)
    if overlap_table:
        candidates.append({**overlap_table, "severity": 61, "priority": 1,
                           "topic_signatures": ["receiving spend from more than one campaign"]})

    # impression share / SIS split (priority 2) - covers BOTH IS findings (budget + rank),
    # so exclude both. Severity follows the dominant loss: budget (66) outranks rank (58).
    is_table = _impression_share_table(data)
    if is_table:
        _isl = data.get("impression_share_lost") or []
        _has_budget = any((c.get("lost_budget", 0) or 0) >= 10 for c in _isl)
        candidates.append({**is_table, "severity": 66 if _has_budget else 58, "priority": 2,
                           "topic_signatures": ["reach only a small slice of the demand that is already out there",
                                                "lost impressions to budget"]})

    # converting terms not yet keywords (priority 3) - severity mirrors the finding: 68 when a
    # term has 3+ conversions (proven), else 52 (early signals). So it outranks the IS table
    # (66) only on proven accounts, and never inflates an early-signal account onto the slide.
    # Exclude BOTH finding variants (proven "are NOT added as active keywords" + the early
    # "early signals rather than statistical proof").
    conv_table = _converting_terms_table(findings, data)
    if conv_table:
        sev = conv_table.pop("_severity")
        candidates.append({**conv_table, "severity": sev, "priority": 3,
                           "topic_signatures": ["are NOT added as active keywords",
                                                "early signals rather than statistical proof"]})

    # too many primary conversion actions (priority 4) - Observation-tier (45), so it only
    # wins the slide when no higher tabular finding exists. Exclude the matching finding.
    primary_table = _primary_actions_table(data)
    if primary_table:
        candidates.append({**primary_table, "severity": 45, "priority": 4,
                           "topic_signatures": ["set as primary 'Conversions'"]})

    return candidates


def select_table(findings, data):
    """Pick the single table that wins the dedicated table slide: highest severity, then
    the fixed priority for ties. Returns the payload (with its metadata) or None."""
    candidates = collect_table_candidates(findings, data)
    if not candidates:
        return None
    return sorted(candidates, key=lambda c: (-c["severity"], c["priority"]))[0]


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
    revenue_undertracked = []   # (name, orders) of purchase actions counting orders at £0 value
    # True when an ecommerce account's purchase actions recorded NOTHING all year while
    # low-value primaries hold all the volume - reported revenue/ROAS is then an artifact
    # of their default values, and the dedicated check below owns the whole story.
    _purchase_silent = False
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

        # ── Ecommerce account whose PURCHASE tracking is silent (owns the headline) ──
        # Purchase actions exist but recorded nothing across 12 months while other
        # primaries (page views and similar) hold ALL the volume: bidding optimises
        # towards page views and the reported 'revenue' is their default values, not
        # sales. The low-value-primary, dead-action and revenue-feedback checks stand
        # down where they overlap so the deck tells ONE story (The Beatles Story,
        # June 2026: ticket purchase action silent all year; 'Checked opening times' and
        # 'Checked prices' page views at about £1 each made up all 14,909 'conversions').
        _monthly_all = data.get("conversion_volume_by_month") or {}
        _purch_actions = [ca for ca in conversion_actions if ca.get("category") == "PURCHASE"]
        if (_purch_actions and detect_account_type(data) == "ecommerce"
                and (total_conversions or 0) >= 50 and _monthly_all):
            _p_12m = sum(sum((_monthly_all.get(ca.get("name")) or {}).values())
                         for ca in _purch_actions)
            # AD-ATTRIBUTED purchases only: 0.0 is a real reading, so never let it fall
            # through to the site-event count (all_conversions) - the whole point is that
            # those two can differ (Beatles Story: 321 site-event purchases, 0 attributed).
            def _p_attr(ca):
                v = ca.get("attributed_conversions_30d")
                return (ca.get("conversions_30d") if v is None else v) or 0
            _p_30d = sum(_p_attr(ca) for ca in _purch_actions)
            # Purchases firing as SITE events on those same actions (GA4/website tag):
            # proof the business takes orders even though no ad click gets the credit.
            _p_site_30d = sum((ca.get("conversions_30d") or 0) for ca in _purch_actions
                              if ca.get("attributed_conversions_30d") is not None)
            if _p_12m < 1 and _p_30d < 1:
                _purchase_silent = True
                conversions_inflated = True
                # Later sections (evaluated after this one) read the flag off the data
                # dict to soften any "converting term" claims built on page-view counts.
                data["_purchase_silent"] = True
                _enabled_p = [ca for ca in _purch_actions
                              if str(ca.get("status", "")) == "ENABLED"] or _purch_actions
                _pname = _enabled_p[0].get("name", "purchase")
                _others = len(_purch_actions) - 1
                _rec = sorted([ca for ca in conversion_actions
                               if ca.get("category") != "PURCHASE"
                               and ((_attr(ca) if _has_attr else ca.get("conversions_30d")) or 0) >= 1],
                              key=lambda ca: (_attr(ca) if _has_attr else ca.get("conversions_30d")) or 0,
                              reverse=True)
                _rec_str = ", ".join(
                    f"'{ca.get('name')}' "
                    f"({int(round((_attr(ca) if _has_attr else ca.get('conversions_30d')) or 0)):,})"
                    for ca in _rec[:3])
                # Per-conversion value across 12 months - when it is pocket change (a £1
                # default, say), name it so the revenue artifact is concrete on the slide.
                _t12_all = sum(sum(m.values()) for m in _monthly_all.values())
                _ps_raw = data.get("performance_summary") or {}
                _t12v = ((_ps_raw.get("t12") or {}).get("value")
                         or ((_ps_raw.get("_raw") or {}).get("t12") or {}).get("value") or 0)
                _val_note = ""
                if _t12_all >= 50 and _t12v and 0 < (_t12v / _t12_all) <= 5:
                    _val_note = (f", each carrying a small default value of about "
                                 f"£{max(1, round(_t12v / _t12_all))}")
                _other_note = (f", and the other {_others} purchase action(s) in the account are "
                               "switched off or silent too" if _others else "")
                if _p_site_30d >= 5:
                    # Purchases ARE firing as website events - the break is between the ad
                    # CLICK and the purchase (cross-domain checkout, GA4 link or similar).
                    # State the two measured facts; hedge the cause.
                    _opening = (
                        f"No purchases are reaching Google Ads: the '{_pname}' conversion action "
                        f"records purchases as website events (about {int(round(_p_site_30d)):,} in "
                        "the last 30 days), but not one has been attributed to a Google Ads click in "
                        "12 months. The trail between the ad click and the checkout is being lost "
                        "along the way - on third-party booking or checkout domains this is usually a "
                        "cross-domain tracking gap, which a tag specialist can confirm. "
                    )
                else:
                    _opening = (
                        f"No purchases are reaching Google Ads: the '{_pname}' conversion action has "
                        f"recorded nothing in the last 12 months{_other_note}. "
                    )
                issues.append(
                    _opening +
                    f"What Google Ads IS recording is page activity: {_rec_str} 'conversions' in the "
                    f"last 30 days{_val_note}. Two knock-on effects. First, smart bidding is "
                    "optimising towards page views, not buyers. Second, the account's reported "
                    "revenue and ROAS are built from those page-view values, not sales money - so "
                    "they cannot be read as a real return. Reconnecting the purchase tag so real "
                    "orders and order values flow into Google Ads is the single most important fix "
                    "in this audit; until then the account cannot see which ads actually sell."
                )
                rag = "red"

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
        # DOWNLOAD is excluded: one install action per platform (Android + iOS) is how app
        # tracking is MEANT to be set up - two of them is parallel coverage, not duplication.
        _primary_cats = _Counter(ca.get("category", "") for ca in _basis
                                 if ca.get("category") and ca.get("category") != "DOWNLOAD")
        _dup_cats = {c: n for c, n in _primary_cats.items() if n >= 2}
        if _purchase_silent:
            # The purchase-silent headline already covers the page-view/engagement junk -
            # flagging those same actions as 'double-counting' would tell the junk story
            # twice, and 'the same lead counted twice' is the wrong frame for page views.
            _dup_cats = {c: n for c, n in _dup_cats.items()
                         if c not in {"PAGE_VIEW", "ENGAGEMENT", "OUTBOUND_CLICK"}}
        # Call/contact dups are owned by the dedicated call-cluster check below (it runs its own
        # volume-overlap evidence test), so exclude them here to avoid a duplicate message and the
        # wrong "two websites/product lines" framing for phone calls (Dan, 14 Jun 2026).
        _dup_cats = {c: n for c, n in _dup_cats.items()
                     if c not in {"PHONE_CALL_LEAD", "CONTACT", "CALL"}}

        # Same-category primaries are only DOUBLE-counting if both tags fire on the same
        # journey. The monthly series is the tell: tags on the same checkout rise and fall
        # together; tags on different parts of the business (two storefronts, two product
        # lines - SAIC's 'SAIC Purchase' vs 'DynaShop Purchase') move independently. Score
        # overlap as sum(per-month minima) / sum(per-month maxima): ~1 = mirrored, ~0 =
        # disjoint. Only trust the verdict with enough volume to be a real pattern.
        def _dup_series_overlap(cat):
            _monthly = data.get("conversion_volume_by_month") or {}
            series = [_monthly.get(ca.get("name")) for ca in _basis if ca.get("category") == cat]
            series = [s for s in series if s]
            if len(series) < 2:
                return None
            months = set().union(*(set(s) for s in series))
            lo = sum(min(s.get(m, 0) for s in series) for m in months)
            hi = sum(max(s.get(m, 0) for s in series) for m in months)
            total = sum(v for s in series for v in s.values())
            if not hi or total < 12:
                return None
            return lo / hi
        _dup_divergent = {}   # cat -> action names; independent streams, NOT double-counting
        _dup_overlap = {}     # cat -> overlap score (None = not enough data to judge)
        for _c in list(_dup_cats):
            _ov = _dup_series_overlap(_c)
            _dup_overlap[_c] = _ov
            if _ov is not None and _ov < 0.5:
                _dup_divergent[_c] = [ca.get("name") for ca in _basis if ca.get("category") == _c]
                del _dup_cats[_c]
        if _dup_divergent:
            # Dan's red-herring rule (11 June 2026): if each campaign only ever records ONE
            # of the actions, the one-tag-per-storefront read is supported and this is just
            # a labelling note; if a single campaign records BOTH, its traffic converts
            # through both tags and the overlap genuinely needs confirming.
            _camp_split = data.get("campaign_conversion_split") or {}
            for _c, _names in _dup_divergent.items():
                _pretty = _cat_pretty.get(_c, _c.replace("_", " ").lower())
                _named = " and ".join(f"'{n}'" for n in _names[:3])
                _evidence = ""
                if _camp_split:
                    _both = [(camp, acts) for camp, acts in _camp_split.items()
                             if sum(1 for n in _names if (acts.get(n) or 0) > 0) >= 2]
                    if _both:
                        _bcamp, _bacts = max(_both, key=lambda x: sum(x[1].get(n, 0) for n in _names))
                        _counts = " and ".join(f"{int(round(_bacts.get(n, 0)))}x '{n}'"
                                               for n in _names if (_bacts.get(n) or 0) > 0)
                        _evidence = (f" Notably, the '{_bcamp}' campaign has recorded BOTH actions over the "
                                     f"last 12 months ({_counts}). The UNEQUAL counts matter: if both tags "
                                     "fired on every order the numbers would match, so this is not blanket "
                                     "double-counting. The two likeliest explanations are that buyers from "
                                     "this campaign genuinely purchase through both sites, or that one "
                                     "action is a newer tag running alongside an older one that was never "
                                     "switched off after a migration - which one it is changes the fix, so "
                                     "it needs confirming, not just labelling.")
                    else:
                        _evidence = (" Each campaign records only one of these actions, which supports the "
                                     "separate-storefronts read - most likely fine as set up.")
                # If the ad destination URLs show two materially-advertised domains, the
                # separate-storefronts read is CONFIRMED from evidence - state it plainly and name
                # the sites instead of asking the client to "visit the destination sites" (Dan,
                # 15 Jun 2026: SAIC's Dynabrade Tools -> dynashop.co.uk, abrasives -> saic-uk.co.uk).
                # If the ad destination URLs CONFIRM two separate storefronts, this is normal account
                # STRUCTURE, not an issue: you cannot promote two sites in one ad group, so two sites
                # simply means separate campaigns, keywords and per-site tags. With the sites proven and
                # the tags cleanly labelled per site, there is nothing to flag - one site's campaign being
                # credited with the other's sale is normal cross-sell attribution, not tag double-counting.
                # Suppress it (Dan, 15 Jun 2026: most accounts run one site, this one runs two - structure).
                # The unconfirmed case keeps the careful hedge below (could be a leftover migration tag).
                # Genuine DOUBLE-COUNTING (volumes moving TOGETHER) is a different branch and still fires.
                if len(_material_domains(data)) >= 2:
                    continue
                issues.append(
                    f"{len(_names)} primary '{_pretty}' actions ({_named}) both feed the Conversions column, "
                    "but their monthly volumes move independently, so they appear to track different parts of "
                    "the business (for example two websites or product lines) rather than double-counting the "
                    f"same orders.{_evidence} Two quick confirmations settle it: visit the destination sites "
                    "behind each conversion action (separate storefronts is usually obvious within a minute), "
                    "and check inside the account that each order can only ever fire one of these tags - if "
                    "both can fire on the same checkout, sales and revenue are overstated and CPAs "
                    "understated. If they are genuinely separate, keep both but label them clearly so "
                    "reporting can split performance by site."
                )
                if rag == "green":
                    rag = "amber"
        # True call/contact actions only - GET_DIRECTIONS is deliberately excluded: a directions
        # tap isn't a phone enquiry, so counting it here would overstate the call double-counting.
        _call_cluster = [ca for ca in _basis
                         if ca.get("category") in {"PHONE_CALL_LEAD", "CONTACT", "CALL"}]
        # EVIDENCE GATE (Dan, 14 Jun 2026): never assert "the same call counted twice" on action
        # COUNT alone. Two call actions whose monthly volumes move INDEPENDENTLY are tracking
        # different call routes (Smart-campaign calls vs call-asset calls), not one call doubled -
        # Mermaid had exactly this. Assert double-counting only when volumes move TOGETHER;
        # otherwise it is a tidy-up to confirm, not measured waste.
        def _cluster_overlap(actions):
            _monthly = data.get("conversion_volume_by_month") or {}
            series = [s for s in (_monthly.get(a.get("name")) for a in actions) if s]
            if len(series) < 2:
                return None
            months = set().union(*(set(s) for s in series))
            hi = sum(max(s.get(m, 0) for s in series) for m in months)
            lo = sum(min(s.get(m, 0) for s in series) for m in months)
            total = sum(v for s in series for v in s.values())
            return (lo / hi) if hi and total >= 12 else None
        _call_overlap = _cluster_overlap(_call_cluster) if len(_call_cluster) >= 2 else None
        _mirrored = (any(v is not None and v >= 0.6 for c, v in _dup_overlap.items() if c in _dup_cats)
                     or (_call_overlap is not None and _call_overlap >= 0.6))
        if _mirrored:
            # Volumes move TOGETHER -> positive evidence the same lead is counted twice.
            _dup_txt = "; ".join(
                f"{n} separate primary '{_cat_pretty.get(c, c.replace('_', ' ').lower())}' actions"
                for c, n in sorted(_dup_cats.items(), key=lambda x: -x[1]))
            _lead_txt = _dup_txt or f"{len(_call_cluster)} call/contact actions"
            _cnames = ", ".join(f"'{ca.get('name')}'" for ca in _call_cluster[:3])
            _call_note = (f" In particular, {len(_call_cluster)} call/contact actions record conversions "
                          f"({_cnames})." if len(_call_cluster) >= 2 else "")
            issues.append(
                f"Possible conversion double-counting: {_lead_txt} record conversions and their monthly volumes "
                f"rise and fall together, the pattern of the same lead being counted more than once.{_call_note} "
                "This makes cost per lead look artificially LOW, so historic figures (including the paused PMax "
                "CPAs) may be around half the true cost. Consolidate to one clean primary action per genuine lead "
                "type (ideally the form fill), move the rest to secondary, and reconcile against the back-end count."
            )
            conversions_inflated = True
            if rag not in ("red",):
                rag = "amber_red"
        elif _dup_cats or len(_call_cluster) >= 2:
            # Multiple primary call/lead actions, but volumes move INDEPENDENTLY (or too little
            # data to tell). Probably different routes, not one lead twice - a tidy-up to confirm,
            # NOT asserted waste, so do not claim inflation or that CPAs are halved.
            _cnames = ", ".join(f"'{ca.get('name')}'" for ca in _call_cluster[:3])
            _n = len(_call_cluster) if len(_call_cluster) >= 2 else sum(_dup_cats.values())
            issues.append(
                f"You have {_n} call or contact actions set as primary conversions ({_cnames}). Their monthly "
                "volumes move independently, so they look like different call routes (for example Smart-campaign "
                "calls versus call-asset calls) rather than the same call counted twice. It is still worth "
                "confirming one enquiry cannot trigger more than one of them, and consolidating to a single "
                "primary call action keeps reporting and bidding clean."
            )
            if rag == "green":
                rag = "amber"

        # ── Orders counted, revenue not passed (ROAS understated) ────────────────
        # A primary PURCHASE action recording material orders but £0 of conversion value
        # over 12 months means part of the business is invisible to every revenue figure
        # (SAIC's 'DynaShop Purchase': 41 orders, £0 - found 11 June 2026, the real cause
        # of its 'ROAS halved' read). ROAS reads low, value bidding optimises on partial
        # data, and the reported revenue trend bends with the order MIX, not performance.
        # Only meaningful when some OTHER action does record value (otherwise the account
        # simply doesn't use value tracking, which the revenue-loop finding covers).
        _vals = data.get("conversion_value_by_action") or {}
        _any_value = any((v.get("value_12m") or 0) > 0 for v in _vals.values())
        for ca in active_actions:
            if not (_is_primary(ca) and ca.get("category") == "PURCHASE"):
                continue
            _v = _vals.get(ca.get("name"))
            if (_any_value and _v and (_v.get("conversions_12m") or 0) >= 10
                    and (_v.get("value_12m") or 0) == 0):
                revenue_undertracked.append((ca.get("name"), _v["conversions_12m"]))
        if revenue_undertracked:
            _zn = "; ".join(f"'{n}' recorded {c:.0f} orders but £0 of revenue"
                            for n, c in revenue_undertracked[:2])
            issues.append(
                f"Part of the business is invisible to the revenue figures: over the last 12 months {_zn} - "
                "the tag is counting orders without passing their value. Every revenue-based figure (ROAS, "
                "revenue trends, value-based bidding) only sees the orders that DO carry value, so true "
                "return on ad spend is HIGHER than reported - and as this action's share of orders grows, "
                "reported ROAS falls even if nothing actually got worse. Fix the value parameter on this "
                "conversion tag so it passes the order total at purchase, and treat ROAS trends as "
                "unreliable until a clean period has been recorded. Once order values flow correctly, the "
                "next upgrade is feeding back profit rather than top-line revenue (margin, returns - POAS), "
                "so bidding chases the orders that actually pay."
            )
            if rag not in ("red",):
                rag = "amber_red"

        # ── Call/lead actions steering bidding on a PURE ecommerce account ─────────
        # The client says ecommerce only (no lead gen), yet call/lead actions are set
        # as PRIMARY and recording - so they sit in the same Conversions column as
        # purchases and smart bidding treats a phone call like an order. Unless calls
        # genuinely are sales (phone orders), they belong in secondary.
        if (detect_account_type(data) == "ecommerce" and data.get("claims_lead_gen") is False):
            _lead_primary = [ca for ca in active_actions if _is_primary(ca) and _is_counting(ca)
                             and ca.get("category") in {"PHONE_CALL_LEAD", "CONTACT", "SUBMIT_LEAD_FORM",
                                                        "BOOK_APPOINTMENT", "REQUEST_QUOTE", "LEAD", "CALL"}]
            if _lead_primary:
                _ln = ", ".join(f"'{ca.get('name')}'" for ca in _lead_primary[:2])
                issues.append(
                    f"Call/lead conversion action(s) ({_ln}) are set as PRIMARY and steer bidding on an "
                    "ecommerce account. They count in the same Conversions column as purchases, so smart "
                    "bidding treats a phone call like an order when deciding what to chase. If phone "
                    "orders are a real sales channel, keep them primary but assign realistic values; "
                    "otherwise move them to secondary so bidding optimises purely towards purchases."
                )
                if rag == "green":
                    rag = "amber"

        if len(primary_actions) > 10:
            issues.append(
                f"{len(primary_actions)} conversion actions are set as primary 'Conversions' that "
                "bidding optimises towards. Too many primary actions can dilute reporting and confuse "
                "bidding  -  review for duplicates, test tags or low-value actions."
            )
            if rag == "green":
                rag = "amber"

        # GA4 import detection  -  read it straight off the conversion action TYPE,
        # which states it outright (GOOGLE_ANALYTICS_4_*). The old proxy ("no website
        # tag snippet = imported from GA4") was confidently wrong: native call
        # conversions (AD_CALL) and legacy Universal Analytics goals legitimately
        # carry no website snippet but are NOT GA4 imports (the SAIC lesson - it
        # mislabelled UA transactions and a Calls-from-Ads action as GA4). Only flag
        # a primary action whose type is genuinely a GA4 import.
        ga4_imported = [
            ca.get("name", "Unknown") for ca in active_actions
            if ca.get("include_in_conversions")
            and str(ca.get("type", "")).startswith("GOOGLE_ANALYTICS_4")
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
        # When the purchase-silent check above fired, it already tells the page-view
        # story - running this block too would put the same fact on two slides.
        if primary_spammable and not _purchase_silent:
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
                "For most lead actions 'Once' is more accurate (calls can be a fair exception) - "
                "worth confirming these are counting the way you intend. Purchases are the "
                "opposite: they should stay on 'Every', because every order counts."
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
        elif not has_oci and detect_account_type(data) == "ecommerce" and not _purchase_silent:
            # (When the purchase action is silent, the copy below would falsely say
            # "conversion tracking records orders" - the purchase-silent finding owns it.)
            # The ecommerce equivalent of the OCI elephant: orders are tracked, but what
            # they were WORTH after the click (margin, returns, new-vs-returning) is not
            # fed back, so value bidding steers on top-line order value only.
            issues.append(
                "Revenue feedback loop: conversion tracking records orders, but we could not find any "
                "offline or enhanced revenue import feeding back what those orders were worth AFTER the "
                "click - actual margin, returns and cancellations, or new-versus-returning customer "
                "value. (First check what the purchase tag passes today: most platforms send the full "
                "order total, not profit - if yours already sends margin-adjusted values, this is "
                "covered.) Importing true order outcomes (even a periodic spreadsheet upload) lets smart "
                "bidding optimise towards profit rather than top-line order value, which matters most "
                "when target-ROAS bidding is steering spend. We roll this out for clients as POAS "
                "(profit on ad spend): if a £100 order carries a 40% margin, bidding optimises towards "
                "the £40 of profit rather than the £100 of revenue - so Google stops chasing "
                "high-revenue, low-margin products and you see clearly which products actually pay."
            )
            if rag == "green":
                rag = "amber"

        # The client SAYS they do lead generation (or both), but nothing lead-shaped is
        # recording - quote forms, contact forms and calls are invisible to the account,
        # so bidding chases only the tracked sales and 'improve lead quality' cannot
        # even be measured. (SAIC: 'Both' on the questionnaire, only purchases record.)
        _LEAD_CATS = {"LEAD", "CONTACT", "SUBMIT_LEAD_FORM", "BOOK_APPOINTMENT",
                      "REQUEST_QUOTE", "PHONE_CALL_LEAD", "IMPORTED_LEAD"}
        if data.get("claims_lead_gen"):
            _lead_recording = any(_is_counting(ca) for ca in active_actions
                                  if ca.get("category") in _LEAD_CATS)
            if not _lead_recording:
                issues.append(
                    "You told us in your questionnaire that lead generation matters to the business "
                    "alongside online sales - yet no lead-type conversion action (quote request, "
                    "contact form, call) is recording anything, so the lead side of the business is "
                    "invisible to Google Ads. Bidding can only optimise towards what it can see, and "
                    "the lead goals you described cannot even be measured until enquiries are "
                    "tracked. Setting up lead conversion actions is the first step."
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
                     and sum((_monthly.get(ca.get("name")) or {}).values()) < 1
                     # the purchase-silent finding already names silent purchase actions
                     and not (_purchase_silent and ca.get("category") == "PURCHASE")]
            if _dead:
                _dnames = ", ".join(f"'{ca.get('name')}'" for ca in _dead[:3])
                issues.append(
                    f"{len(_dead)} genuine conversion action(s) appear set up but have recorded nothing "
                    f"in the last 12 months: {_dnames}. The rest of the account records conversions "
                    "fine, so each of these is either a broken or misfiring tag (worth a manual test "
                    "conversion if customers DO convert this way), or a leftover default action that is "
                    "no longer used - either way it should not sit as a primary conversion. (If the "
                    "action was only created recently, this is expected and can be ignored.)"
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
        "revenue_undertracked": [n for n, _ in revenue_undertracked],
        # Purchase actions silent all year while page-view primaries hold the volume:
        # drives the performance-commentary caveat that revenue/ROAS is NOT sales money.
        "revenue_artifact": _purchase_silent,
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

    # Ad groups per campaign ratio. At feed scale (50+ per campaign on a Shopping/PMax
    # account) this is almost always an automated SKU-level structure from a bid
    # management platform (e.g. Bidnamic) - intentional, not sprawl. Don't tell an
    # automated account to "consolidate"; ask them to confirm the tool instead.
    if num_ad_groups > 0 and num_campaigns > 0:
        ratio = num_ad_groups / num_campaigns
        shopping_count = sum(1 for c in enabled_campaigns if c.get("type") == "SHOPPING")
        if ratio > 50 and (shopping_count or pmax_count):
            issues.append(
                f"{num_ad_groups:,} ad groups across {num_campaigns} campaigns - this scale "
                "usually comes from an automated feed/SKU-level structure built by a bid "
                "management platform rather than hand-built sprawl. Worth confirming the tool "
                "and its fees are still earning their keep; if no automation platform is in "
                "place, the structure needs consolidating into tighter themes."
            )
        elif ratio > 10:
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

    # ── Account neglect: zero changes in the last 28 days on a spending account means
    # nobody is actively managing it. This is the client's usual stated pain ('lack of
    # proactivity') made measurable - a strong, honest audit point.
    _activity = data.get("change_activity") or {}
    _spend_30d = (data.get("account_summary_30d", {}) or {}).get("spend", 0) or 0
    _changes = _activity.get("changes") if _activity else None
    if _changes is not None and _changes <= 2 and _spend_30d >= 200:
        _ch_txt = ("No changes have been made to the account" if _changes == 0
                   else f"Only {_changes} change(s) have been made to the account")
        # Anchor it in time: name the most recent change in the window, or be honest that
        # Google only exposes ~30 days of change history, so the last real change is older
        # than that - we cannot see how much older (Dan, 11 June 2026).
        # (Presenter context, not slide copy: the change_event API only exposes ~30 days,
        # so with 0 changes the last real change is simply older than the window.)
        _when_txt = ""
        if _changes and _activity.get("last_change"):
            try:
                from datetime import datetime as _dt2
                _lc = _dt2.strptime(_activity["last_change"], "%Y-%m-%d").strftime("%-d %B %Y")
                _when_txt = f" The most recent change was on {_lc}."
            except (ValueError, TypeError):
                _when_txt = ""
        issues.append(
            f"{_ch_txt} in the last {_activity.get('days', 28)} days, "
            f"while it spent £{_spend_30d:,.0f}.{_when_txt} Google Ads accounts need regular attention - "
            "search term reviews, bid and budget adjustments, negative keywords, ad tests. Money is "
            "being spent largely on autopilot with nobody steering."
        )
        if rag == "green":
            rag = "amber"

    # ── Campaign cannibalisation: the same search term triggering ads in 2+ campaigns
    # means the campaigns compete in the same auctions (split data, bid against each
    # other). Severity follows the money: a couple of stray terms is a watch-item, a
    # material overlap is a structural problem (the human decks lead with the latter).
    _ts = data.get("top_search_terms") or []
    _t_camps, _t_spend = {}, {}
    for _r in _ts:
        _t, _c = _r.get("term"), _r.get("campaign_name")
        if _t and _c:
            _t_camps.setdefault(_t, set()).add(_c)
            _t_spend[_t] = _t_spend.get(_t, 0) + (_r.get("spend") or 0)
    _multi = {t: c for t, c in _t_camps.items() if len(c) >= 2}
    if _multi:
        _ov_spend = sum(_t_spend[t] for t in _multi)
        _worst = max(_multi, key=lambda t: _t_spend[t])
        _eg = (f"'{_worst}' triggered ads from {len(_multi[_worst])} campaigns "
               f"({', '.join(sorted(_multi[_worst])[:3])})")
        if _ov_spend >= 50 or len(_multi) >= 3:
            issues.append(
                f"{len(_multi)} search term(s) are triggering ads in more than one campaign at the "
                f"same time, about £{_ov_spend:,.0f} of spend in 30 days. For example {_eg}. "
                "Overlapping campaigns compete against each other in the same auctions and split "
                "their performance data, so both learn more slowly and CPCs are pushed up. Tightening "
                "the boundaries between campaigns (keywords, locations, negatives) removes the "
                "self-competition."
            )
            if rag == "green":
                rag = "amber"
        else:
            issues.append(
                f"A small overlap of search terms between campaigns: {len(_multi)} term(s) "
                f"(about £{_ov_spend:,.0f}) triggered ads from more than one campaign, e.g. {_eg}. "
                "Not material yet, but it is a sign the campaigns share auction space - worth "
                "keeping the boundaries (keywords, locations, negatives) clean as spend grows."
            )

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
    # Drop UNKNOWN/UNSPECIFIED enum values from the labels - they'd render as a bare
    # "Unknown" on the client deck (a recommendation type newer than the API client's
    # enum). But REMEMBER they existed: an unidentified type means we cannot say
    # "nothing risky is on", so the all-clear wording below has to hedge.
    _unidentified_aar = [t for t in auto_apply_types
                         if str(t).upper() in ("UNKNOWN", "UNSPECIFIED")]
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
        elif _unidentified_aar:
            issues.append(
                f"Auto-Apply is enabled for low-risk types ({labelled}), plus at least one setting "
                "our reporting could not identify by name. Worth a quick look at the Auto-Apply "
                "settings page (Recommendations > Auto-Apply) to confirm nothing higher-risk is "
                "switched on."
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

    # ── Customer Match: the one first-party audience that feeds Smart Bidding ─────
    # Customer Match (uploading your own customer list) is the audience type that gives
    # Smart Bidding and PMax/Demand Gen Audience Signals real first-party data - auto-
    # created remarketing and rule-based lists do not. An established account with NONE
    # set up is leaving a free, low-effort signal on the table (the human PPC Geeks deck
    # flags "only automatically created Customer Lists"). An opportunity, not waste - so
    # it sits at Observation level and never escalates the section RAG on its own.
    _cm = data.get("customer_match")
    if (isinstance(_cm, dict) and _cm.get("customer_match_lists") == 0
            and (summary.get("spend") or 0) >= 300):
        issues.append(
            "No Customer Match list is set up: the account uses only auto-created and remarketing "
            "audiences, not a list of your own customers. Customer Match (uploading the email or "
            "phone list of past ticket buyers, securely hashed) is the one first-party signal that "
            "sharpens Smart Bidding and gives Performance Max and Demand Gen a real Audience Signal "
            "to learn from - it also opens up retargeting past visitors and finding new people who "
            "look like them. It is a low-effort, one-off upload for a measurable gain in how well "
            "bidding understands your customers."
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
    # One source of truth for brand tokens (fetch_account_data filters generic words AND
    # human first names - 'Mark - Dynashop' must label as 'dynashop', not 'mark').
    try:
        from fetch_account_data import _brand_tokens_from as _btf
        brand_tokens = _btf(data.get("account_name", ""))
    except Exception:
        _generic = {"ltd", "limited", "pool", "pools", "leisure", "group", "services", "company",
                    "uk", "the", "ads", "account", "marketing", "co", "and"}
        brand_tokens = [w.lower() for w in str(data.get("account_name", "")).split()
                        if len(w) > 3 and w.lower() not in _generic]

    # The account's OWN advertised domains are, by definition, its brands. Add their name
    # tokens so a second storefront is recognised as brand too, not just the brand in the
    # account name (the SAIC lesson: the account name 'Mark - Dynashop' only yielded
    # 'dynashop', so 'saic' brand terms from the saic-uk.co.uk storefront slipped through).
    brand_tokens = list(brand_tokens or [])
    try:
        import re as _re_dom
        _generic_dom = {"www", "co", "uk", "com", "net", "org", "shop", "store", "online", "ltd"}
        for _dom in (data.get("ad_destination_domains") or {}):
            for _tok in _re_dom.split(r"[.\-]", str(_dom).lower()):
                if len(_tok) > 3 and _tok not in _generic_dom and _tok not in brand_tokens:
                    brand_tokens.append(_tok)
    except Exception:
        pass

    # Brands the storefront actually sells, discovered from its own brand/manufacturer pages
    # at fetch time (data["site_brands"]). This is what lets the SQR tell a resold brand name
    # (e.g. 'dynabrade', 'sait') from genuine new demand, instead of leaning on the caveat alone
    # (Mark's SAIC note: 5 of 8 'converting terms' were brand/supplier names). Empty/absent on
    # older saved fetches, so behaviour is unchanged there.
    for _sb in (data.get("site_brands") or []):
        _sb = str(_sb).lower().strip()
        if len(_sb) > 3 and _sb not in brand_tokens:
            brand_tokens.append(_sb)

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

    # The client's stated keywords and brand words are THEIR demand - a competitor token
    # match must never be built from them. (Beatles Story, June 2026: competitor
    # 'Liverpool Beatles Museum' shares every word with the client's own keywords, so
    # token matching flagged 'beatles museum' and even 'museums in liverpool' - the
    # client's stated keyword - as competitor names.)
    _client_kw = [k for k in (data.get("client_keywords") or []) if k]
    _client_kw_tokens = {tok for k in _client_kw for tok in k.split()}

    def _competitor_reason(term):
        """Return 'listed' (named competitor), 'possible' (heuristic), or None."""
        t = str(term).lower()
        if _is_brand(t):
            return None
        for k in _client_kw:
            if k in t or t in k:
                return None
        for full in _comp_full:
            if full in t:
                return "listed"
        toks = t.split()
        if any(tok in _comp_tokens and tok not in _client_kw_tokens
               and tok not in brand_tokens for tok in toks):
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
            "as negative keywords to stop paying for clicks meant for the other company."
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
        # Name the top converting terms with their numbers AND the keyword that loosely caught
        # them ('sait sanding belts' arriving via phrase-match 'sanding belts' confused the
        # client until the route was spelled out - Dan, 11 June 2026).
        _sorted_conv = sorted(converting_not_added, key=lambda t: (t.get("conversions", 0) or 0), reverse=True)
        def _ck_eg(t):
            conv = int(round(t.get("conversions", 0) or 0))
            spend = t.get("spend", 0) or 0
            kw, mt = t.get("keyword"), str(t.get("keyword_match_type") or "").lower()
            via = (f", currently caught loosely by the {mt}-match keyword '{kw}'" if kw and mt else "")
            cpl = ""
            if conv:
                _pp = spend / conv
                # Sub-£1 unit costs read as pence, never a rounded-to-zero "~£0".
                cpl = (f" at ~£{round(_pp)} each" if _pp >= 1
                       else f" at ~{max(1, round(_pp * 100))}p each")
            return f"'{t.get('term', '?')}' ({conv} conversion{'s' if conv != 1 else ''}{cpl}{via})"
        # Statistical honesty (Dan, 11 June 2026): one conversion from a couple of clicks is an
        # early SIGNAL, not proof - a £1 click that converted once can read as a £1 CPA and be
        # a false positive over 90 days. Only 3+ conversions earns the confident framing.
        _proven = [t for t in _sorted_conv if (t.get("conversions", 0) or 0) >= 3]
        _early = [t for t in _sorted_conv if (t.get("conversions", 0) or 0) < 3]
        # Mark's SAIC note (16 Jun 2026): converting terms can include the client's own brand,
        # or a manufacturer/supplier they resell, already converting inside a NON-brand campaign
        # (e.g. 'dynabrade ...' for a Dynabrade reseller). The API cannot reliably tell a resold
        # brand name from genuine new demand, so we caveat rather than overclaim a "new demand"
        # win - those belong in brand/non-brand separation, not blanket keyword adds.
        _brand_caveat = (" Before adding any of these, check each one: some may be your own brand, or "
                         "a manufacturer or supplier you resell, already converting inside a non-brand "
                         "campaign. Those are better handled by a dedicated brand campaign (with the brand "
                         "name excluded as a negative elsewhere) than counted as new non-brand demand.")
        if _proven:
            eg_text = " For example " + ", ".join(_ck_eg(t) for t in _proven[:3]) + "."
            _early_note = (f" A further {len(_early)} term(s) converted only once or twice - "
                           "early signals worth watching for a repeat, not yet proof.") if _early else ""
            _n, _s = len(_proven), ("s" if len(_proven) != 1 else "")
            _hv = "have" if len(_proven) != 1 else "has"
            if data.get("_purchase_silent"):
                # The 'conversions' behind these terms are page views (the tracking finding
                # owns that story) - so this is interest evidence, not money evidence, and
                # must not be sold as "proven, money-making demand".
                sqr_issues.append(
                    f"{_n} search term{_s} {_hv} repeatedly generated page-view 'conversions' over the "
                    f"last 90 days but are NOT added as active keywords.{eg_text} With purchase tracking "
                    "disconnected these counts show interest, not sales - but the terms are still "
                    "keyword candidates: adding the closest fits gives control over bids, ad copy and "
                    f"landing pages, and they can be judged properly once real orders are tracked.{_early_note}{_brand_caveat}"
                )
            else:
                sqr_issues.append(
                    f"{_n} search term{_s} {_hv} repeatedly generated conversions over the last 90 days "
                    f"but are NOT added as active keywords.{eg_text} Proven, money-making demand is being captured "
                    "loosely (or not at all) rather than controlled directly. Promote these into dedicated keywords "
                    "where search volume supports it - very low-volume terms (under roughly 10 searches a month) "
                    "cannot be added and are better captured by a closely related theme - to gain control over bids, "
                    f"ad copy and landing pages.{_early_note}{_quality_caveat}{_brand_caveat}"
                )
        else:
            eg_text = " For example " + ", ".join(_ck_eg(t) for t in _sorted_conv[:3]) + "."
            sqr_issues.append(
                f"{len(converting_not_added)} search terms converted in the last 90 days without being added "
                "as active keywords - though none has more than a couple of conversions, so these are early "
                f"signals rather than statistical proof.{eg_text} The pattern matters more than any single "
                "term: the search query report is producing keyword candidates and nobody is harvesting them. "
                "Add the closest fits as keywords to test against more data, and make mining the report part "
                f"of the monthly routine.{_quality_caveat}{_brand_caveat}"
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
    # ── Negative-conflict check (Dan, 11 June 2026): "you'd be surprised how often a
    # negative has been added that sabotages the entire account." Cross-check every
    # search term that CONVERTED (30d top terms + 90d unkeyworded list) against the
    # campaign-level and shared-set negatives applying to its campaign. A match means
    # a term that was paying is now blocked (the negative arrived after the clicks).
    # Standard negative semantics: no close variants; phrase = ordered subsequence;
    # broad = all words present in any order. Ad-group negatives are skipped - we
    # cannot tell which ad group served the term, so flagging them would guess.
    _negs = data.get("negative_keywords") or {}
    if _negs and (_negs.get("campaign") or _negs.get("shared")):
        _shared_map = _negs.get("shared_campaigns") or {}

        def _negs_for(camp):
            for n in _negs.get("campaign") or []:
                if n.get("campaign") == camp:
                    yield n.get("text"), n.get("match_type"), f"campaign '{camp}'"
            for n in _negs.get("shared") or []:
                if camp in (_shared_map.get(n.get("set")) or []):
                    yield n.get("text"), n.get("match_type"), f"shared list '{n.get('set')}'"

        def _neg_blocks(neg_text, neg_mt, ttoks):
            ntoks = str(neg_text or "").lower().strip().strip('"[]').split()
            if not ntoks:
                return False
            mt = str(neg_mt or "").upper()
            if mt == "EXACT":
                return ttoks == ntoks
            if mt == "PHRASE":
                return any(ttoks[i:i + len(ntoks)] == ntoks
                           for i in range(len(ttoks) - len(ntoks) + 1))
            return all(w in ttoks for w in ntoks)

        _conv_terms = {}
        for t in ((data.get("converting_unkeyworded_terms") or [])
                  + [t for t in (data.get("top_search_terms") or [])
                     if (t.get("conversions") or 0) > 0]):
            _key = (str(t.get("term", "")).lower().strip(), t.get("campaign_name"))
            if _key[0] and _key[1]:
                _conv_terms[_key] = max(_conv_terms.get(_key, 0), t.get("conversions") or 0)
        _conflicts = []
        for (_term, _camp), _conv in _conv_terms.items():
            _ttoks = _term.split()
            for _ntext, _nmt, _where in _negs_for(_camp):
                if _neg_blocks(_ntext, _nmt, _ttoks):
                    _conflicts.append((_term, _conv, _ntext, str(_nmt).lower(), _where))
                    break
        if _conflicts:
            _egs = "; ".join(
                f"the {nmt}-match negative '{ntext}' ({where}) blocks '{term}', which converted "
                f"{int(round(conv))} time(s) recently"
                for term, conv, ntext, nmt, where in _conflicts[:3])
            sqr_issues.append(
                f"{len(_conflicts)} negative keyword(s) are blocking searches that have CONVERTED recently: "
                f"{_egs}. A negative added after a term has proven itself silently cuts off demand that was "
                "paying - one of the most damaging quiet mistakes in account management. Review each against "
                "the conversion history and remove or tighten the negative."
            )
            if rag == "green":
                rag = "amber"
        elif (data.get("negative_keyword_count") or 0) >= 5000:
            sqr_issues.append(
                f"{data['negative_keyword_count']:,} negative keywords are active across the account - an "
                "unusually large list. We cross-checked them against every search term that converted "
                "recently and found no conflicts, which is reassurance worth having in writing - but lists "
                "this size often hide a blocker as they grow, so repeat the cross-check whenever negatives "
                "are added in bulk."
            )

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
            # Sub-£1 average CPCs render as pence ("19p"), never a rounded-to-zero "£0".
            _cpc_label = (f"£{_acct_cpc:.0f}" if _acct_cpc >= 1
                          else f"{max(1, round(_acct_cpc * 100))}p")
            egs = []
            for p in spikes:
                mult = (p["cpc"] / _acct_cpc) if _acct_cpc else 0
                single = "a single click" if (p.get("clicks") or 0) == 1 else f"{p.get('clicks')} clicks"
                # NO "produced no conversions" clause: a single click almost never converts
                # regardless, so 0 conversions on 1 click is not evidence of waste - and on an
                # account with broken/low-value conversion tracking it means nothing at all
                # (Dan, 13 Jun 2026: a 7x click on a relevant term is money well spent, not junk).
                egs.append(
                    f"'{p['term']}' paid £{p['cpc']:.0f} for {single} on {_pretty_date(p.get('date'))} "
                    f"({mult:.0f}x the account's ~{_cpc_label} average CPC)"
                )
            issues.append(
                "Automated bidding paid some very expensive single clicks last month that the average CPC "
                "hides: " + "; ".join(egs) + ". The search term report only shows an average CPC per term, so "
                f"a one-off £{spikes[0]['cpc']:.0f} click sits unnoticed beside cheaper ones. These may well be "
                "relevant searches worth bidding on - the point is not the search, it is the price: with no "
                "sensible maximum-CPC ceiling in place, automated bidding can occasionally pay far more for a "
                "single click than it is worth. Worth a maximum-CPC sense-check so an outlier click cannot "
                "quietly cost many times your average."
            )
            if rag == "green":
                rag = "amber"

    if true_manual:
        # Ecommerce path (Dan, 11 June 2026): the destination is VALUE-based bidding
        # (target ROAS), tested via a campaign experiment against the client's own
        # stated target - never a hard cutover. But sequencing matters: if a purchase
        # tag records orders at £0 value, campaign ROAS reads are corrupted, so the
        # value signal must be fixed BEFORE any value-bidding test can be judged.
        _ecom_note = ""
        if detect_account_type(data) == "ecommerce":
            _tgt = data.get("stated_roas_target")
            _tgt_txt = f" against your stated {_tgt:g}:1 target" if _tgt else ""
            _vals_mc = data.get("conversion_value_by_action") or {}
            _ca_mc = {ca.get("name"): ca for ca in (data.get("conversion_actions") or [])}
            _zero_val_mc = any(
                (v.get("conversions_12m") or 0) >= 10 and (v.get("value_12m") or 0) == 0
                and _ca_mc.get(n, {}).get("category") == "PURCHASE"
                and _ca_mc.get(n, {}).get("status") == "ENABLED"
                for n, v in _vals_mc.items())
            if _zero_val_mc:
                # Lead with the RISK, not the destination (Mark's SAIC note): on a manual
                # account the words 'target ROAS' jar unless it's clear WHY it isn't the move
                # yet. Switching to value bidding while the purchase tag records £0 orders would
                # let it optimise from bad data and scale the wrong sales.
                _ecom_note = (
                    " The eventual destination is VALUE-based bidding (target ROAS)"
                    f"{_tgt_txt}, but the revenue signal must be trustworthy first: move to it while the "
                    "purchase tag records £0 orders and bidding would optimise from bad data and scale the "
                    "wrong sales. Fix the £0-value purchase tag, let a clean period record, then trial target "
                    "ROAS through a campaign experiment (a controlled A/B test) rather than a hard switch, so "
                    "the move is judged on real data."
                )
            else:
                _ecom_note = (
                    " For an ecommerce account the natural next step is VALUE-based bidding (target ROAS)"
                    f"{_tgt_txt}, trialled through a campaign experiment (a controlled A/B test) rather "
                    "than a hard switch, so the move is judged on real data."
                )
        if total_conversions >= 30:
            issues.append(
                f"{len(true_manual)} campaign(s) still on Manual CPC despite "
                f"{total_conversions:.0f} conversions/month. "
                f"Smart bidding should outperform manual at this volume.{_ecom_note}"
            )
            rag = "amber"
        else:
            issues.append(
                f"{len(true_manual)} campaign(s) on Manual CPC. "
                f"As conversion volume builds into a steadier stream, smart bidding becomes worth "
                f"testing.{_ecom_note}"
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
        # Vocabulary follows the business: an ecommerce account's conversions are orders,
        # not leads/enquiries (Gastronomica review, 11 June 2026). Name the unit plainly,
        # drop the vague "genuine"/"real" label from client copy, and state the day each
        # campaign was switched off (Dan, 17 Jun 2026, Hampton).
        _is_ecom_pw = detect_account_type(data) == "ecommerce"
        _up = "orders" if _is_ecom_pw else "enquiries"
        from datetime import datetime as _dt_pw
        for p in efficient_paused[:3]:
            rc, g, gp = p.get("real_cpa"), p.get("genuine_conv"), p.get("genuine_pct")
            _off = ""
            if p.get("last_active"):
                try:
                    _dd = _dt_pw.strptime(p["last_active"], "%Y-%m-%d")
                    _off = f", switched off {_dd.day} {_dd.strftime('%B')}"
                except Exception:
                    _off = ""
            if rc and g:
                d = f"the '{p['name']}' campaign ({int(round(g))} {_up} at £{rc:.0f} each{_off}"
                if gp is not None and gp < 70:
                    d += (f"; only {gp:.0f}% of its tracked conversions were genuine, so its "
                          f"headline CPA of £{p.get('cpa', 0):.0f} flatters it")
                d += ")"
            else:
                d = f"the '{p['name']}' campaign (historic CPA £{p.get('cpa', 0):,.0f}, {int(round(p.get('conversions', 0)))} conv{_off})"
            descs.append(d)
        names = ", ".join(descs)

        _have_quality = [p for p in efficient_paused[:3] if p.get("genuine_pct") is not None]
        _genuine_what = ("purchases" if _is_ecom_pw else "form fills, calls and contacts")
        if _have_quality and all((p.get("genuine_pct") or 0) >= 70 for p in _have_quality):
            verify = (f" We checked the conversion quality: these were {_genuine_what}, "
                      "not page views or engagement actions - so this is real efficient activity "
                      "that was switched off, not vanity metrics.")
        else:
            verify = (" Worth confirming the conversion quality before reactivating - some of the apparent "
                      "efficiency leans on low-value actions (page views, engagement) rather than "
                      + ("actual orders." if _is_ecom_pw else "actual enquiries."))

        # If the conversion setup shows possible double-counting, those "cheap" historic CPAs
        # may be ~half the true cost - caveat it rather than presenting them at face value.
        _dup_caveat = ""
        _primary = [ca for ca in (data.get("conversion_actions") or [])
                    if ca.get("status") == "ENABLED" and ca.get("include_in_conversions")]
        _call_cluster_n = sum(1 for ca in _primary
                              if ca.get("category") in {"PHONE_CALL_LEAD", "CONTACT", "GET_DIRECTIONS", "CALL"})
        from collections import Counter as _C
        _dupcat = any(n >= 2 for n in _C(ca.get("category") for ca in _primary).values())
        # Volume-concentration guard (Dan, 17 Jun 2026, Hampton): multiple primary call/contact
        # actions only actually DOUBLE-count when their recorded volumes overlap. When one action
        # carries the vast majority of conversions, the others are negligible and cannot be
        # inflating the count - so do NOT hedge the historic CPAs (Hampton: 98% from one action).
        # Only suppress when we can PROVE concentration from data; absent it, hedge (cautious).
        _act_totals = sorted((sum(m.values()) for m in (data.get("conversion_volume_by_month") or {}).values()
                              if m), reverse=True)
        _concentrated = bool(_act_totals) and sum(_act_totals) > 0 and _act_totals[0] / sum(_act_totals) >= 0.75
        if (_dupcat or _call_cluster_n >= 2) and not _concentrated:
            _dup_caveat = (" Important: the conversion setup shows possible double-counting, so these historic "
                           "CPAs may be roughly half the true cost - verify against the back-end enquiry count "
                           "before trusting them.")
        issues.append(
            f"{len(efficient_paused)} paused campaign(s) historically delivered "
            f"{'orders' if _is_ecom_pw else 'enquiries'} below the "
            f"account's current £{cpa:.0f} CPA: {names}.{verify}{_dup_caveat} Worth reviewing whether "
            f"{'product profitability' if _is_ecom_pw else 'lead quality'}, not cost, drove the pause "
            "before deciding on reactivation."
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
    geo_table = None   # optional structured geo breakdown for an on-slide table
    campaigns = data.get("campaigns", [])
    account_type = detect_account_type(data)
    AWARENESS = {"DISPLAY", "VIDEO", "DEMAND_GEN", "MULTI_CHANNEL"}

    # ── Disapproved / policy-limited ads: a disapproved ad silently stops serving,
    # and its ad group can sit dark for months. The first thing every expert checks.
    _pol = data.get("ad_policy_status") or {}
    if _pol.get("disapproved"):
        _bad = _pol["disapproved"]
        _camps = sorted({e["campaign"] for e in _bad})
        issues.append(
            f"{len(_bad)} enabled ad(s) are disapproved and silently not serving, in: "
            f"{', '.join(_camps[:3])}{'...' if len(_camps) > 3 else ''}. A disapproved ad shows "
            "no error anywhere a client normally looks - the ad group just goes dark. Review the "
            "policy reason in Ads > Status and fix or replace the ads."
        )
        if rag != "red":
            rag = "amber_red"
    elif _pol.get("limited"):
        issues.append(
            f"{len(_pol['limited'])} enabled ad(s) are approved but LIMITED by policy, which "
            "restricts where or how often they can show. Worth reviewing the policy detail - "
            "small wording changes often lift the restriction."
        )

    # ── Overnight waste (lead gen): spend running midnight-6am with zero conversions
    # and no schedule trimming it. Ecommerce converts around the clock, so this check
    # deliberately stays silent on ecommerce/mixed accounts.
    _hours = data.get("hourly_performance") or {}
    if _hours and account_type in ("lead_gen", "unknown"):
        _night_spend = sum((_hours.get(h) or {}).get("spend", 0) for h in range(0, 6))
        _night_conv = sum((_hours.get(h) or {}).get("conv", 0) for h in range(0, 6))
        _total_spend = sum(v.get("spend", 0) for v in _hours.values())
        if (_night_spend >= 50 and _total_spend > 0
                and _night_spend / _total_spend >= 0.05 and _night_conv < 1):
            issues.append(
                f"About £{_night_spend:,.0f} ({_night_spend / _total_spend:.0%} of spend) ran between "
                "midnight and 6am in the last 30 days with zero conversions. For a lead business "
                "those clicks rarely become enquiries - an ad schedule that trims the dead hours "
                "moves that budget to the times that actually convert."
            )
            if rag == "green":
                rag = "amber"

    # ── Device performance gap: one device taking a material share of spend while
    # recording nothing, when another device IS converting. Humble framing - device
    # mix can be deliberate, so recommend a review, not a verdict.
    _devices = data.get("device_performance") or {}
    if _devices:
        _dev_total = sum(v.get("spend", 0) for v in _devices.values())
        _conv_devs = [d for d, v in _devices.items() if v.get("conv", 0) >= 3]
        for _d, _v in _devices.items():
            if (_dev_total > 0 and _v.get("spend", 0) >= 100
                    and _v["spend"] / _dev_total >= 0.20
                    and _v.get("conv", 0) == 0 and _conv_devs):
                issues.append(
                    f"A meaningful share of spend is going to a device that is not converting: "
                    f"£{_v['spend']:,.0f} ({_v['spend'] / _dev_total:.0%} of spend) on "
                    f"{_d.title()} recorded no conversions in 30 days, while "
                    f"{_conv_devs[0].title()} converts fine. Worth reviewing device performance "
                    "and applying a negative bid adjustment if the pattern holds."
                )
                if rag == "green":
                    rag = "amber"
                break

    # ── Average order value collapse (ecommerce) ──────────────────────────────
    # Dan (11 June 2026, SAIC): conversions UP, conversion rate UP, ROAS HALVED - the deck
    # must say WHY. When revenue per tracked order falls hard while orders hold up, the
    # ROAS story is about order VALUE (product mix, discounting, or a value-tracking
    # change), not about the ads converting worse. Ecommerce only: on mixed accounts the
    # conversions total includes lead actions, which would corrupt the order-value maths.
    _praw = (data.get("performance_summary") or {}).get("_raw") or {}
    _pt30, _pt12 = _praw.get("t30") or {}, _praw.get("t12") or {}
    # Skip when a purchase action records orders at £0 value: blended order-value maths is
    # then a measurement artifact, and the zero-value finding owns the ROAS explanation.
    _ca_by_name = {ca.get("name"): ca for ca in (data.get("conversion_actions") or [])}
    _zero_val_purchase = any(
        (v.get("conversions_12m") or 0) >= 10 and (v.get("value_12m") or 0) == 0
        and _ca_by_name.get(n, {}).get("category") == "PURCHASE"
        and _ca_by_name.get(n, {}).get("status") == "ENABLED"
        for n, v in (data.get("conversion_value_by_action") or {}).items())
    if (account_type == "ecommerce" and not _zero_val_purchase
            and (_pt30.get("conversions") or 0) >= 5 and (_pt12.get("conversions") or 0) >= 30
            and (_pt30.get("value") or 0) > 0 and (_pt12.get("value") or 0) > 0):
        _aov30 = _pt30["value"] / _pt30["conversions"]
        _aov12 = _pt12["value"] / _pt12["conversions"]
        if _aov30 < 0.65 * _aov12:
            issues.append(
                f"Average order value has dropped hard: the last 30 days averaged about £{_aov30:.0f} of "
                f"tracked revenue per order, versus about £{_aov12:.0f} over the last 12 months. This - not "
                "the ads converting worse - is what is pulling ROAS down: orders are still coming in, but "
                "each one is worth roughly "
                + ("half" if _aov30 <= 0.55 * _aov12 else "a third less than")
                + " what it used to be. The three usual causes, in rough order of likelihood: the product "
                "mix has shifted towards cheaper items, heavier discounting or promotions, or a change in "
                "how order values are passed to the conversion tag. Segment recent sales by product line "
                "and sanity-check the conversion value settings to pin down which it is - and if cheaper "
                "products are genuinely taking over, margin-based bidding (POAS) matters all the more."
            )
            if rag == "green":
                rag = "amber"

    # ── Product coverage: how much of the catalogue is on the bench (Shopping/PMax) ──
    # Two reads from the Merchant Center estate: IN-STOCK products not eligible to serve
    # anywhere (catalogue sitting idle - out-of-stock exclusions are fine), and products
    # with real 12-month revenue that now get NO impressions ('gone dark' - proven
    # sellers quietly switched off). Gastronomica head-to-head, 11 June 2026.
    # Only meaningful when the account actually runs a product-feed channel (Shopping or
    # PMax). A Search-only account can still have a Merchant Center catalogue linked, which
    # reads as "0 of N products eligible" - but those products are not "benched" from a
    # Shopping strategy that exists, the account simply does not do Shopping. Claiming they
    # were dropped and saying "reintroduce the proven sellers" would be confidently wrong
    # (SAIC live, 15 Jun 2026: 2,448-product catalogue, 0 eligible, but Search-only).
    _pc = data.get("product_coverage") or {}
    _feed_channel = any(t in ("SHOPPING", "PERFORMANCE_MAX")
                        for t in (data.get("campaign_types_active") or []))
    if account_type in ("ecommerce", "mixed") and _feed_channel and (_pc.get("total") or 0) >= 20:
        _instock = max((_pc["total"] - (_pc.get("out_of_stock") or 0)), 1)
        _benched = _pc.get("not_eligible_in_stock") or 0
        _dark_txt = ""
        if (_pc.get("dark_count") or 0) >= 3 and (_pc.get("dark_revenue_12m") or 0) >= 100:
            _top = (_pc.get("dark_products") or [{}])[0]
            # Keep the magnitude honest: £1,452/year is about £120 a month - real, not huge.
            _per_month = _pc["dark_revenue_12m"] / 12.0
            _still = (" It is still in stock on your site - shoppers can buy it, your ads just "
                      "no longer show it." if _top.get("availability") == "IN_STOCK" else "")
            _dark_txt = (f" On top of that, {_pc['dark_count']} products that sold in the last 12 months "
                         f"(about £{_pc['dark_revenue_12m']:.0f} of revenue, roughly £{_per_month:.0f} a "
                         f"month) have had no impressions at all in the last 30 days. The biggest, "
                         f"'{_top.get('title', '?')}', earned £{_top.get('revenue_12m', 0):.0f} in the past "
                         f"year.{_still}")
        if _benched >= 0.3 * _instock:
            issues.append(
                f"Most of the product catalogue is not being advertised: only {_pc.get('eligible', 0)} of "
                f"{_pc['total']} products are eligible to serve, and {_benched} IN-STOCK products are not in "
                f"any live campaign ({_pc.get('out_of_stock', 0)} more are out of stock, which is fine to "
                f"exclude).{_dark_txt} Every benched product is demand the account cannot capture. Review why "
                "they were excluded and reintroduce the proven sellers first."
            )
            if rag == "green":
                rag = "amber"
        elif _dark_txt:
            issues.append(
                "Proven sellers have gone quiet despite past revenue." + _dark_txt +
                " Check stock, feed status and campaign inclusion for these first - they are the "
                "lowest-risk revenue to win back."
            )
            if rag == "green":
                rag = "amber"

    # ── Dormant Shopping/PMax on an ecommerce store (Dan, 15 Jun 2026, SAIC) ──────
    # An online store running Search only, with its Shopping/PMax campaigns all switched off,
    # is a question worth posing: Shopping/PMax is usually the PRIMARY channel for ecommerce.
    # We cannot know WHY from the API, so the finding ASKS rather than asserts - and backs the
    # question with the all-time track record where we have it (a channel that once produced
    # real sales is a very different story from one that never worked). Sized as a material
    # OPPORTUNITY, not a confirmed-money headline, because the upside is unmeasured today.
    if account_type in ("ecommerce", "mixed"):
        _enabled_feed = any(c.get("status") == "ENABLED"
                            and c.get("type") in ("SHOPPING", "PERFORMANCE_MAX") for c in campaigns)
        _paused_feed = [c for c in campaigns if c.get("status") == "PAUSED"
                        and c.get("type") in ("SHOPPING", "PERFORMANCE_MAX")]
        if _paused_feed and not _enabled_feed:
            _pc2 = data.get("product_coverage") or {}
            _feed_txt = (f" The Merchant Center feed is live with {_pc2['total']:,} products."
                         if (_pc2.get("total") or 0) >= 1 else "")
            # Name the historical EARNERS as proof the channel worked: the biggest by recorded
            # value, plus a second, stronger-RETURNING campaign if there is one (the channel can
            # pay well, not just at scale - argues for a SELECTIVE revival). All figures come
            # from the account's own conversion tracking, which we have flagged as possibly
            # inaccurate, so they are caveated as indicative, not exact (Dan, 15 Jun 2026).
            _hist = [h for h in (data.get("shopping_history_alltime") or [])
                     if (h.get("conversion_value") or 0) >= 1 and (h.get("spend") or 0) >= 100]
            _proof = ""
            if _hist:
                _earner = sorted(_hist, key=lambda h: h["conversion_value"], reverse=True)[0]
                _ev_s, _ev_v = _earner["spend"], _earner["conversion_value"]
                _roas_bit = f" (roughly {_ev_v / _ev_s:.0f}:1 by revenue)" if _ev_s else ""
                _proof = (f" The history shows the channel produced real sales: its biggest, "
                          f"'{_earner['name']}', recorded about £{_ev_v:,.0f} of value on £{_ev_s:,.0f} "
                          f"of spend over its life{_roas_bit}.")
                # Second example: best revenue-to-spend ratio among the OTHER campaigns, if strong.
                _others = sorted((h for h in _hist if h is not _earner and h["spend"]),
                                 key=lambda h: h["conversion_value"] / h["spend"], reverse=True)
                if _others and (_others[0]["conversion_value"] / _others[0]["spend"]) >= 3:
                    _b = _others[0]
                    _proof += (f" Another, '{_b['name']}', returned more strongly still: about "
                               f"£{_b['conversion_value']:,.0f} of value on £{_b['spend']:,.0f} of spend "
                               f"(roughly {_b['conversion_value'] / _b['spend']:.0f}:1).")
                _proof += (" These figures come from the account's own conversion tracking, which may "
                           "not have been fully accurate, so treat them as indicative rather than exact.")
            issues.append(
                f"This is an online store, but its Shopping and Performance Max campaigns are all "
                f"switched off and the account runs on Search alone.{_feed_txt}{_proof} For an "
                "ecommerce business, Shopping and Performance Max are usually the main way products get "
                "found, so it is worth asking why they are off and whether they should return. Do not "
                "switch them on blind: establish why each was paused, confirm conversion-value tracking "
                "is solid first (see the tracking finding), then trial the products that sold before, "
                "checking the return against your margins."
            )
            if rag == "green":
                rag = "amber"

    # ── PMax + standard Shopping running side by side ──────────────────────────
    # Product data learnings split across two formats; usually one (typically PMax)
    # clearly outperforms and the estate should consolidate towards it deliberately.
    _en_camps = [c for c in campaigns if c.get("status") == "ENABLED"]
    _pmax_c = [c for c in _en_camps if c.get("type") == "PERFORMANCE_MAX" and (c.get("spend_30d") or 0) >= 25]
    _shop_c = [c for c in _en_camps if c.get("type") == "SHOPPING" and (c.get("spend_30d") or 0) >= 25]
    if _pmax_c and _shop_c:
        _pm = max(_pmax_c, key=lambda c: c.get("spend_30d") or 0)
        _sh = max(_shop_c, key=lambda c: c.get("spend_30d") or 0)

        def _camp_roas(c):
            s = c.get("spend_30d") or 0
            v = c.get("conv_value_30d") or 0
            return (v / s) if s and v else None
        _pr, _sr = _camp_roas(_pm), _camp_roas(_sh)
        _cmp = ""
        if _pr and _sr:
            _better = "Performance Max" if _pr >= _sr else "the Shopping campaign"
            _cmp = (f" On tracked revenue, '{_pm['name']}' (PMax) runs at {_pr:.1f}x against "
                    f"{_sr:.1f}x for '{_sh['name']}' - {_better} is currently the stronger format.")
        issues.append(
            "Products are split between Performance Max and a standard Shopping campaign running side by "
            f"side.{_cmp} Split estates split the learning data, so products take longer to reach their "
            "potential, and the weaker format quietly absorbs budget. Consolidate the estate towards the "
            "better performer deliberately - keeping a deliberate test split is fine, an accidental one "
            "is not."
        )
        if rag == "green":
            rag = "amber"

    # ── Break-even ROAS vs the client's own margin (ecommerce) ────────────────
    # The questionnaire gives profit margin (and often LTV). Break-even ROAS is
    # 100/margin: at 30% margin every £1 of spend needs £3.33 back just to break
    # even on the first order. Two failure modes, worst first: the BIDDING TARGET
    # itself is set below break-even (Google is being asked to buy loss-making
    # orders), or actual ROAS is below break-even (fine only as a deliberate
    # LTV/new-customer play - a decision, not a drift).
    _margin = data.get("stated_margin_pct")
    if account_type == "ecommerce" and _margin and 5 <= _margin <= 95:
        _be = 100.0 / _margin
        _ltv = data.get("stated_ltv_gbp")
        _ltv_txt = (f" With your £{_ltv:,.0f} customer lifetime value, first-order loss can be a "
                    "deliberate acquisition play - but it should be a decision made with the numbers "
                    "in view, not a setting nobody revisits." if _ltv else "")
        _low_tgt = [c for c in (data.get("campaigns") or [])
                    if c.get("status") == "ENABLED" and (c.get("spend_30d") or 0) >= 50
                    and c.get("target_roas") and float(c["target_roas"]) < 0.85 * _be]
        _roas30 = ((data.get("performance_summary") or {}).get("_raw", {}).get("t30", {}) or {}).get("roas")
        if _low_tgt:
            _c = max(_low_tgt, key=lambda c: c.get("spend_30d") or 0)
            _tgt_v = float(_c["target_roas"])
            # Is the campaign already beating its own target? Then there is headroom to
            # raise it NOW - and the path is small steps, never a jump to break-even.
            _ach = ((_c.get("conv_value_30d") or 0) / (_c.get("spend_30d") or 1)) or None
            _ach_txt = (f" The campaign is actually delivering about {_ach:.1f}x on tracked revenue - "
                        "already beating its own target - so there is headroom to raise it now."
                        if _ach and _ach > _tgt_v else "")
            # When was the target last touched? A recent adjustment to a below-break-even
            # level is a judgement problem; no adjustment in the visible window suggests
            # set-and-forget (the API only shows ~30 days of change history).
            _lt = (data.get("change_activity") or {}).get("last_target_change")
            _when_tgt = ""
            if _lt:
                try:
                    from datetime import datetime as _dt3
                    _when_tgt = (" The target was last adjusted on "
                                 f"{_dt3.strptime(_lt, '%Y-%m-%d').strftime('%-d %B %Y')} - so it is "
                                 "being reviewed, but to a level that still buys losses.")
                except (ValueError, TypeError):
                    _when_tgt = ""
            _steps = f"{_tgt_v + 0.2:.1f}, then {_tgt_v + 0.4:.1f}"
            issues.append(
                f"The bidding target is set below break-even: at your stated {_margin:.0f}% profit margin, "
                f"break-even ROAS is about {_be:.1f}x, yet the '{_c['name']}' campaign has a target ROAS of "
                f"{_tgt_v:.1f}x - Google is being asked to chase orders that lose money on the "
                f"margin.{_ach_txt}{_when_tgt}{_ltv_txt} Do not jump straight to {_be:.1f}x - raise the "
                f"target in small steps ({_steps}, and so on), watching volume at each step, or document "
                "why a below-break-even target is intentional."
            )
            if rag == "green":
                rag = "amber"
        elif _roas30 and _roas30 < _be:
            issues.append(
                f"Return is below break-even on your own numbers: at your stated {_margin:.0f}% margin, "
                f"break-even ROAS is about {_be:.1f}x, and the last 30 days delivered {_roas30:.1f}x - so "
                f"each first order is currently bought at a loss.{_ltv_txt} Anchor the account's ROAS "
                "target to this break-even line and track progress against it."
            )
            if rag == "green":
                rag = "amber"

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
        # Always label the entity ("the X campaign", never a bare name) and, when these
        # campaigns already convert, say what the missed share IS: proven demand being
        # left to competitors - profitable volume, not extra spend (Dan, 11 June 2026).
        # Quantify Search impression share (SIS) as "about X in 10" - the share of the demand
        # ALREADY out there that the campaign actually reaches. SIS is the addressable market
        # made concrete: people typing these searches who never see the ad (Dan, 13 Jun 2026 -
        # always state SIS, split it into its two elements, and give it in plain X-in-10 terms).
        def _in_ten(pct):
            n = pct / 10.0
            return f"{n:.1f}".rstrip("0").rstrip(".")
        def _rank_eg(c):
            r = c.get("lost_rank", 0) or 0
            s = c.get("sis")
            if s is not None:
                return (f"the '{c['campaign']}' campaign shows on only about {s:.0f}% of the searches it is "
                        f"eligible for (roughly {_in_ten(s)} in 10), losing {r:.0f}% of the rest to Ad Rank")
            return f"the '{c['campaign']}' campaign loses {r:.0f}% of impressions to Ad Rank"
        names = "; ".join(_rank_eg(c) for c in rank_lost[:3])
        _conv_by_camp = {c.get("name"): (c.get("conversions_30d") or 0) for c in campaigns}
        _opp = ("" if not any(_conv_by_camp.get(c["campaign"], 0) > 0 for c in rank_lost[:3]) else
                " These campaigns already record conversions, so the missed impressions are demand you are "
                "proven to win - recovering rank unlocks volume from budget already being spent, not new spend.")
        issues.append(
            f"{len(rank_lost)} Search campaign(s) reach only a small slice of the demand that is already out "
            f"there, because they lose impressions to Ad Rank rather than budget: {names}. Search impression "
            "share is your share of the searches people are ALREADY making for what you offer - that is your "
            "addressable market, and a low share means most of those people never see your ad at all. Ad Rank "
            "is driven by bids, ad relevance and Quality Score, so this is a quality and bid problem, not a "
            f"money one.{_opp} Tighter keyword-to-ad relevance, stronger ad copy and better landing pages "
            "recover this visibility without simply spending more."
        )
        if rag == "green":
            rag = "amber"

    # ── Location targeting setting (Presence vs Presence-or-interest) ─────────
    loc = data.get("location_target_types") or []
    poi = [c for c in loc if c.get("geo") == "PRESENCE_OR_INTEREST" and c.get("type") not in AWARENESS]
    if poi:
        names = ", ".join(f"'{c['campaign']}'" for c in poi[:3])
        # Name WHAT the account targets - the client asks "what are my target locations?" and
        # "90% out of area" is useless without saying what the area is (Dan, 13 Jun 2026). Built
        # from the resolved location names; ordered by how many campaigns use each, named up to 4.
        # _target_lead opens the finding so the named locations survive GPT's 2-bullet
        # condensation (a buried "for reference" sentence was dropped - Dan, 13 Jun 2026).
        _target_lead = ""
        _ordered = []
        _lt = data.get("location_targeting") or []
        _pos_locs = [l.get("location_name") for l in _lt
                     if not l.get("is_negative") and l.get("location_name")]
        if _pos_locs:
            from collections import Counter as _LC
            _ordered = [n for n, _ in _LC(_pos_locs).most_common()]
            _top = ", ".join(_ordered[:4])
            _more = f", and {len(_ordered) - 4} more" if len(_ordered) > 4 else ""
            _plc = "location" if len(_ordered) == 1 else "locations"
            _target_lead = (f"Across the account your campaigns target {len(_ordered)} different {_plc} - "
                            f"most often {_top}{_more}. ")
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
            _tgt = _geo.get("target_country") or "your target country"
            # The two figures measure DIFFERENT things and must never be juxtaposed bare, or
            # the slide reads as a contradiction (Dan, 13 Jun 2026: "90% then 10% - which is
            # right?"). out_of_area = not in the targeted LOCATIONS (the headline). The named
            # countries are the top of the smaller ABROAD subset - state that nesting plainly.
            _fspend = _geo.get("foreign_country_spend") or 0
            _fpct = _geo.get("foreign_country_pct") or 0
            # Explain what MAKES UP the 90% (abroad vs in-country interest) WITHOUT listing
            # individual countries here - a top-3 country list (~£187) sitting next to a
            # 90%-of-spend figure reads as a contradiction. The named countries live in the
            # separate cross-border finding, which owns the overseas detail.
            _areas = ""
            if _fspend >= 1:
                _areas = (f" That 90% is made up of two parts: about a third of it (£{_fspend:.0f}, "
                          f"{_fpct:.0%} of all spend) reached people physically in other countries, and "
                          f"the rest reached people inside {_tgt} who were shown your ads through the "
                          "'interest' setting without being in the locations you target.")
            if _ooa_spend >= max(20.0, 0.02 * _geo["total_spend"]):
                _real = (f" The geographic report confirms the leak is real: in the last 30 days "
                         f"£{_ooa_spend:.0f} ({_ooa_pct:.0%} of spend) went to clicks from people "
                         f"NOT physically inside the specific locations these campaigns target.{_areas}")
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
        # Severity follows the MEASURED money (Dan, 11 June 2026, SAIC review): 'Presence or
        # interest' across 100% of spend is exposure, but if the geographic report says only
        # ~5% actually leaked, that is a tidy-up - not the deck's headline issue. Only an
        # unmeasured leak or a confirmed-big one (>=10% of spend or >=£150/30d) leads.
        _measured = _ooa_spend is not None and bool(_geo.get("total_spend"))
        _leak_big = _measured and (_ooa_pct >= 0.10 or _ooa_spend >= 150)
        if material and _measured and not _leak_big:
            _size = (f"£{_ooa_spend:.0f} ({_ooa_pct:.0%} of spend) reached people outside your "
                     "targeted area in the last 30 days" if _ooa_spend >= 1 else
                     "effectively none of your spend reached people outside your targeted area "
                     "in the last 30 days")
            _route = ""
            if _geo.get("target_country"):
                _route = (f" The campaigns target {_geo['target_country']}, so these clicks arrive via the "
                          "'interest' route - people elsewhere showing interest in the targeted area.")
            issues.append(
                f"{_target_lead}{len(poi)} campaign(s) use the 'Presence or interest' location setting - "
                f"Google's default: {names}. The geographic report shows the actual leak is small so far: "
                f"{_size}.{_areas}"
                f"{_route} Not a needle-mover at today's spend - switch to 'Presence (people in, or regularly "
                "in, your locations)' as a free tidy-up that stops the leak growing as budgets scale."
            )
        elif material:
            local_note = (" For a local business this is a silent leak worth closing."
                          if account_type in ("lead_gen", "unknown", "mixed") else "")
            # Buyers of an ecommerce/booking business do NOT have to be near the venue:
            # blanket "switch to Presence" advice on a city-level target would cut off
            # customers planning ahead from elsewhere. Anchor the fix to matching the
            # TARGETS to where customers are when they buy (Beatles Story, June 2026).
            catchment_note = (
                " One check before switching: make sure the targeted locations match where "
                "customers actually are when they order or book - if buyers purchase ahead "
                "from elsewhere (a visitor attraction, say), the right fix is 'Presence' "
                "across the whole catchment country, not just the venue's own town."
                if account_type == "ecommerce" else "")
            if data.get("international_audience"):
                # Client told us their audience is international - don't imply the overseas
                # share is pure waste. Point to a dedicated, separately-budgeted campaign.
                catchment_note = (
                    " A nuance here: you have told us your audience is international, so some of "
                    "this interest from outside the area will be people planning a visit - real "
                    "prospects. The fix is not to cut it blindly but to separate it: set the "
                    "home-market campaigns to 'Presence' so they stop paying for distant 'interest' "
                    "clicks, and if you want the overseas demand, run it as its own campaign with "
                    "its own budget and trip-planning messaging so the two never compete for the "
                    "same money.")
            _poi_open = (f"{_target_lead}But {len(poi)} of these campaign(s) use" if _target_lead
                         else f"{len(poi)} campaign(s) use")
            issues.append(
                f"{_poi_open} the 'Presence or interest' location "
                f"setting - Google's default: {names}.{_mag} It shows your ads to people merely INTERESTED in "
                f"those places, not only those actually in them - so with it active across {_pct:.0%} of your "
                f"spend, your whole budget is EXPOSED to out-of-area clicks.{_real}"
                f"{local_note} Switching to 'Presence (people in, or regularly in, your locations)' is one of "
                f"the highest-ROI fixes there is - it typically cuts wasted spend and lowers cost per "
                f"{'sale' if account_type == 'ecommerce' else 'lead'}.{catchment_note}"
            )
        else:
            issues.append(
                f"{_target_lead}Location targeting is set to 'Presence or interest' on low-spend campaigns: "
                f"{names}.{_mag} The leak is small for now, but worth switching to 'Presence (people in, or "
                "regularly in, your locations)' as a tidy-up - and revisit it if you scale these budgets."
            )
        # Structured geo breakdown for an OPTIONAL on-slide table (populate_slides renders it
        # only when the geo finding makes the deck). Same numbers as the prose, as label/value
        # rows - the "perfect table" Dan asked to see on the slide (13 Jun 2026).
        # Only build the table when the leak is material (it shares the dedicated table
        # slide, which is trimmed when empty) and we have the spend split.
        # Only build the geo super-slide table when the leak actually warrants headlining -
        # i.e. the big-or-unmeasured path that also carries severity 76. On the measured-SMALL
        # path (e.g. SAIC: £61 / 5%) the finding is an Observation, so the table must not seize
        # the marquee slide with a "leaking 5% of spend" headline (the £68/5% lesson, 15 Jun 2026).
        _gt_total = _geo.get("total_spend") or 0
        if (_gt_total and (_geo.get("out_of_area_spend") or 0) >= max(50.0, 0.05 * _gt_total)
                and (_leak_big or not _measured)):
            _gt_in = _geo.get("in_area_spend") or 0
            _gt_ooa = _geo.get("out_of_area_spend") or 0
            _gt_for = _geo.get("foreign_country_spend") or 0
            _rows = [
                ["Inside your targeted locations", f"£{_gt_in:,.0f}", f"{_gt_in / _gt_total:.0%}"],
                ["Shown on 'interest', outside them", f"£{_gt_ooa:,.0f}", f"{_gt_ooa / _gt_total:.0%}"],
            ]
            if _gt_for >= 1:
                _rows.append(["...of which, overseas", f"£{_gt_for:,.0f}", f"{_gt_for / _gt_total:.0%}"])
            # This is now the single 'super slide' for geo (no separate issue slide), so the
            # context line carries the real insight: the locations are not SPLIT OUT - distant
            # markets are lumped into the home-market campaigns and budgets, so nobody can see
            # whether they pay. The recommendation names the fix AND the assess-each-market step
            # (Dan, 13 Jun 2026: targeting Brazil is fine, but give it its own budget and check
            # it is profitable, do not just leave it lumped in).
            # Copy kept TIGHT so it fits the fixed template boxes: a one-line headline, a ~2-line
            # context line (locations + the interest mechanism), and the real insight (markets
            # not split out -> own budget + profitability check) in the recommendation, which
            # has more room below the table (Dan, 13 Jun 2026).
            _intl_eg = next((c for c in (_ordered[1:] if _ordered else []) if c != "Liverpool"), "")
            if _ordered:
                _n3 = ", ".join(_ordered[:3])
                _extra = len(_ordered) - 3
                _hap = (f"You target {len(_ordered)} locations ({_n3}{', and %d more' % _extra if _extra > 0 else ''}), "
                        "but most of your budget reaches people only interested in them, not actually in them:")
            else:
                _hap = ("The 'Presence or interest' setting reaches people only interested in your targeted "
                        "places, not actually in them - so most of your spend lands outside them:")
            _eg = f" If you want somewhere like {_intl_eg}," if _intl_eg else " If you want a distant market,"
            _rec = ("The catch is these markets are not split out - they share one budget, so you cannot tell "
                    "which pay. Switch your home-market campaigns to 'Presence (people in, or regularly in, "
                    f"your locations)'.{_eg} give it its own campaign and budget and check it is profitable.")
            _ooa_pct_lbl = f"{_gt_ooa / _gt_total:.0%}"
            geo_table = {
                "title": f"Location targeting is leaking {_ooa_pct_lbl} of spend",
                "happening": _hap,
                "header": ["Where your budget went", "Spend (30 days)", "Share"],
                "rows": _rows,
                "recommendation": _rec,
            }
        rag = "amber"

    # ── Cross-border spend (users physically in a DIFFERENT country) ──────────
    # Distinct from the in-country interest leak above: this is budget reaching people in
    # an entirely different country. Only flag when it's material and named.
    _geo = data.get("geo_user_location") or {}
    _foreign = _geo.get("foreign_country_spend") or 0
    _total = _geo.get("total_spend") or 0
    # Gate at 5% / £50: below that the POI finding's named-countries note already covers it,
    # and a 3%-of-spend leak reported twice reads as padding (Dan, 11 June 2026).
    if _total and _foreign >= max(50.0, 0.05 * _total) and _geo.get("top_foreign_countries"):
        _fc = _geo["top_foreign_countries"]
        _named = ", ".join(f"{c['country']} (£{c['spend']:.0f})" for c in _fc[:3])
        _fpct = _geo.get("foreign_country_pct") or 0
        _country = _geo.get("target_country") or "your target country"
        if data.get("international_audience"):
            # The client TOLD us their audience is international (a destination/tourist
            # business). These overseas clicks may be people planning a visit - real
            # prospects, not waste - so the move is to decide deliberately, not blanket-cut.
            issues.append(
                f"£{_foreign:.0f} ({_fpct:.0%} of spend) in the last 30 days reached people physically located "
                f"OUTSIDE {_country} - top sources: {_named}. You have told us your audience is "
                "international, so some of this could be genuine interest from people planning a visit, not "
                "waste. The point is that it is happening by accident rather than by design: right now overseas "
                "clicks share the same campaigns and budget as your home-market ads, so the two compete for the "
                "same money and neither is tuned for its audience. If you want the international demand, give it "
                "its own campaign with its own budget and messaging (for people booking ahead of a trip) and "
                "keep it OUT of the home-market campaigns - then you can see what each market is worth and grow "
                "the one that pays. If you do not want it, tighten location targeting to your home market and "
                "add the worst offenders as negative locations."
            )
        else:
            issues.append(
                f"£{_foreign:.0f} ({_fpct:.0%} of spend) in the last 30 days reached people physically located "
                f"OUTSIDE {_country} - top sources: {_named}. Unless you "
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
        # Quantify what the opt-in actually cost (last 30 days) when the network split is
        # available - '£62 went to Display and produced 0 conversions' lands much harder
        # than 'some budget can go to lower-intent placements' (Dan, 11 June 2026).
        _split = data.get("network_split") or {}
        _spent_note = ""
        _d_spend = sum((_split.get(n) or {}).get("display_spend", 0) for n in disp)
        _d_conv = sum((_split.get(n) or {}).get("display_conversions", 0) for n in disp)
        _p_spend = sum((_split.get(n) or {}).get("partners_spend", 0) for n in sp)
        _p_conv = sum((_split.get(n) or {}).get("partners_conversions", 0) for n in sp)
        _parts = []
        if disp and _split:
            _parts.append(f"£{_d_spend:.0f} went to Display placements and produced "
                          f"{_d_conv:.0f} conversion(s)" if _d_spend >= 1 else
                          "effectively nothing has been spent on Display yet, so this is exposure rather than waste")
        if sp and _split:
            _parts.append(f"£{_p_spend:.0f} went to Search Partner sites and produced "
                          f"{_p_conv:.0f} conversion(s)" if _p_spend >= 1 else
                          "effectively nothing has been spent on Search Partners yet, so this is exposure rather than waste")
        if _parts:
            _spent_note = " In the last 30 days " + "; ".join(_parts) + "."
        # Severity follows the money: when the network split CONFIRMS the leak is still
        # tiny (<£10 in 30 days), this is a settings tidy-up for Additional Observations,
        # not a headline slide. No split data = can't confirm = keep the stronger framing.
        if _split and (_d_spend + _p_spend) < 10:
            issues.append(
                f"A network setting worth tidying: {len(set(sp) | set(disp))} Search campaign(s) include "
                + " and ".join(bits) +
                ", which can send budget to lower-intent placements beyond Google search results."
                f"{_spent_note} Untick the network boxes so it stays that way as budgets grow."
            )
        else:
            issues.append(
                f"{len(set(sp) | set(disp))} Search campaign(s) are opted into " + " and ".join(bits) + ". "
                "These send a share of your budget to lower-intent placements beyond Google search results, "
                f"often at a worse cost per lead.{_spent_note} Unless they are proven to convert, turn them off "
                "so budget concentrates on high-intent search traffic."
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
        _acct_type = detect_account_type(data)
        # Sitelinks, callouts, structured snippets and images help ANY account. Call
        # extensions are core for lead gen (a phone enquiry IS the conversion) but OPTIONAL
        # for an online-checkout/ticketing business, where most buyers never call - so we
        # never headline a missing CALL extension on an ecommerce account (Dan, 13 Jun 2026:
        # it is 50/50 and depends what the client wants). Hedge it there instead.
        missing_universal = [labels[t] for t in ("AD_IMAGE", "SITELINK", "CALLOUT", "STRUCTURED_SNIPPET")
                             if t not in present]
        call_missing = "CALL" not in present
        _call_is_core = call_missing and _acct_type in ("lead_gen", "mixed", "unknown")
        missing_core = ([labels["CALL"]] if _call_is_core else []) + missing_universal
        if missing_core:
            _booster = ("Call and image extensions in particular" if _call_is_core
                        else "Image and sitelink extensions in particular")
            _ecom_call_note = ""
            if _acct_type == "ecommerce" and call_missing:
                _ecom_call_note = (" Call extensions are absent too, but for an online-booking business "
                                   "those are optional - worth adding only if you want to encourage phone "
                                   "bookings (group visits or accessibility enquiries, say).")
            issues.append(
                f"Your ads are missing high-value extension types: {', '.join(missing_core)}. Extensions "
                "make ads bigger and more clickable and feed Ad Rank - all at no extra cost per click. "
                f"{_booster} tend to lift click-through rate by 10-20%. Add the missing types across your "
                f"campaigns.{_ecom_call_note}"
            )
            if rag == "green":
                rag = "amber"
        elif _acct_type == "ecommerce" and call_missing:
            # Only call extensions missing on an ecommerce account - genuinely optional, so a
            # light note rather than a "high-value gap". Never escalates the section RAG.
            issues.append(
                "Call extensions are not set up. For an online-booking business this is optional - most "
                "customers book online and never call - so add them only if phone bookings (group visits, "
                "school trips, accessibility enquiries) are something you actively want to encourage."
            )

    if not issues:
        issues.append("Coverage and settings look healthy: location targeting, impression share and ad "
                      "extensions are in good shape - no change needed here.")

    return {"rag": rag, "headline": "Coverage & settings", "issues": issues,
            "geo_table": geo_table, "data_points": {}}


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
    # Only call tracking a strength when something business-meaningful is recording
    # (sales or enquiries) - an account counting only page views must never be told
    # its tracking is a strength (The Beatles Story, June 2026).
    _BIZ_LEAD_CATS = {"LEAD", "CONTACT", "SUBMIT_LEAD_FORM", "BOOK_APPOINTMENT",
                      "REQUEST_QUOTE", "SIGNUP", "PHONE_CALL_LEAD", "IMPORTED_LEAD",
                      "DEFAULT", "OTHER"}  # DEFAULT/OTHER: webpage actions often land here
    def _vol30(ca):
        v = ca.get("attributed_conversions_30d")
        return (ca.get("conversions_30d") if v is None else v) or 0
    _cas = data.get("conversion_actions") or []
    _sales_vol = sum(_vol30(ca) for ca in _cas if ca.get("category") == "PURCHASE")
    _enq_vol = sum(_vol30(ca) for ca in _cas if ca.get("category") in _BIZ_LEAD_CATS)
    _install_vol = sum(_vol30(ca) for ca in _cas if ca.get("category") == "DOWNLOAD")
    if (summary.get("conversions", 0) or 0) > 0:
        if _sales_vol > 0:
            s.append("conversion tracking live and recording sales")
        elif _enq_vol > 0:
            s.append("conversion tracking live and recording enquiries")
        elif _install_vol > 0:
            s.append("conversion tracking live and recording app installs")
    # Spend discipline: when we LOOKED for the classic waste signals and found none, say
    # so - "no significant wasted spend" is a verified finding, not an omission (Dan,
    # 11 June 2026: a tracking-heavy deck must still show the waste angle was checked).
    _terms = data.get("top_search_terms") or []
    _acct = (summary.get("spend") or 0)
    if _terms and _acct >= 300:
        _nc_spend = sum((t.get("spend") or 0) for t in _terms
                        if (t.get("conversions") or 0) == 0)
        _avg_cpc = summary.get("avg_cpc") or 0
        _pricey = data.get("priciest_clicks") or []
        _max_cpc = max((p.get("cpc") or 0) for p in _pricey) if _pricey else None
        _no_spikes = (_max_cpc is None or not _avg_cpc or _max_cpc <= 2.5 * _avg_cpc)
        if _nc_spend <= 0.15 * _acct and _no_spikes:
            _spike_txt = (f" and no runaway CPCs (priciest single click £{_max_cpc:.2f} vs "
                          f"£{_avg_cpc:.2f} average)" if _max_cpc and _avg_cpc else "")
            s.append(f"spend discipline is good - non-converting search spend is only about "
                     f"£{_nc_spend:.0f} ({_nc_spend / _acct:.0%} of the month), spread thinly "
                     f"across small terms{_spike_txt}")
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
