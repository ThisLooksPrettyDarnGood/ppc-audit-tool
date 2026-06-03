# analyse_account.py
# Step 4: Analysis & Scoring Engine

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

        if len(conversion_actions) > 10:
            issues.append(
                f"{len(conversion_actions)} conversion actions detected. "
                "Too many actions can dilute reporting — review for duplicates or test tags."
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
            ca.get("name", "Unknown") for ca in conversion_actions
            if ca.get("include_in_conversions")
            and not ca.get("has_tag_snippet", True)   # default True = don't flag if data missing
            and ca.get("category", "") in web_categories
        ]
        if ga4_imported:
            issues.append(
                "Primary conversion(s) appear to be imported from GA4 rather than tracked "
                "via a native Google Ads tag: " + ", ".join(ga4_imported) + ". "
                "GA4 imports prevent Enhanced Conversions from working and may under-report "
                "conversions for users who declined cookie consent."
            )
            if rag == "green":
                rag = "amber"

        # Spammable/low-value categories set as primary optimisation goal
        spammable_categories = {"PAGE_VIEW", "ENGAGEMENT", "DOWNLOAD"}
        primary_spammable = [
            ca.get("name", "Unknown") for ca in conversion_actions
            if ca.get("include_in_conversions")
            and ca.get("category", "") in spammable_categories
        ]
        if primary_spammable:
            issues.append(
                "Low-value conversion action(s) included in primary optimisation: "
                + ", ".join(primary_spammable) + ". "
                "Campaigns may be optimising towards clicks or page views rather than real leads or sales."
            )
            if rag != "red":
                rag = "red"

        # Conversion count type — MANY_PER_CLICK on lead gen actions inflates numbers
        lead_categories = {
            "CONTACT", "SUBMIT_LEAD_FORM", "BOOK_APPOINTMENT", "REQUEST_QUOTE",
            "SIGNUP", "LEAD", "PHONE_CALL_LEAD", "IMPORTED_LEAD", "DEFAULT", "OTHER"
        }
        many_per_click_leads = [
            ca.get("name", "Unknown") for ca in conversion_actions
            if ca.get("include_in_conversions")
            and ca.get("counting_type") == "MANY_PER_CLICK"
            and ca.get("category", "") in lead_categories
        ]
        if many_per_click_leads:
            issues.append(
                "Lead conversion(s) set to count 'Every' instead of 'Once': "
                + ", ".join(many_per_click_leads) + ". "
                "This inflates conversion numbers and confuses smart bidding — switch to 'One per click'."
            )
            if rag == "green":
                rag = "amber"

    # Campaigns spending with zero conversions
    for c in campaigns:
        c_conv = c.get("conversions_30d", 0)
        c_cost = c.get("spend_30d", 0)
        c_name = c.get("name", "Unknown campaign")
        c_status = c.get("status", "")
        if c_status == "ENABLED" and c_cost > 50 and c_conv == 0:
            issues.append(
                f"Campaign '{c_name}' spent £{c_cost:.2f} with 0 conversions recorded."
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

    # Auto-apply recommendations — important flag but doesn't override structure RAG on its own
    auto_apply = data.get("auto_apply_recommendations", None)
    if auto_apply:
        issues.append(
            "Auto-Apply Recommendations are enabled on this account. "
            "These can make changes to keywords, bids, and ad copy without human review — "
            "we strongly recommend switching them off."
        )
        # Only escalate to amber if structure is currently green (don't override other issues)
        if rag == "green":
            rag = "amber"

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
    cpa = summary.get("cpa", 0)

    smart_bidding_strategies = {
        "TARGET_CPA", "TARGET_ROAS", "MAXIMIZE_CONVERSIONS",
        "MAXIMIZE_CONVERSION_VALUE", "TARGET_IMPRESSION_SHARE"
    }
    manual_strategies = {"MANUAL_CPC", "MANUAL_CPM", "MANUAL_CPV", "MAXIMIZE_CLICKS"}

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
    max_clicks = [n for n, s in manual_campaigns if s == "MAXIMIZE_CLICKS"]
    true_manual = [n for n, s in manual_campaigns if s != "MAXIMIZE_CLICKS"]

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

    # Smart bidding with too few conversions
    if smart_campaigns and total_conversions < 15:
        issues.append(
            f"{len(smart_campaigns)} campaign(s) on smart bidding but only "
            f"{total_conversions:.0f} conversions this month. "
            "Smart bidding needs 30–50 conversions/month to learn effectively — it may be in a learning state."
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
