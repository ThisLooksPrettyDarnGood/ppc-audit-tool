import os
"""
fetch_account_data.py
Step 3 — PPC Team Audit Tool
"""

import json
from pathlib import Path
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

CREDENTIALS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials.json")
TOKEN_PATH = "token_ads.json"
MCC_CID = "539-263-1535"

SCOPES = ["https://www.googleapis.com/auth/adwords"]


def get_credentials():
    creds = None
    if Path(TOKEN_PATH).exists():
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    return creds


def build_client(creds):
    config = {
        "developer_token": os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN", ""),
        "login_customer_id": MCC_CID.replace("-", ""),
        "use_proto_plus": True,
    }
    if os.path.exists(CREDENTIALS_PATH):
        secret_data = json.loads(Path(CREDENTIALS_PATH).read_text())["installed"]
        config["client_id"] = secret_data["client_id"]
        config["client_secret"] = secret_data["client_secret"]
    else:
        config["client_id"] = os.environ.get("GOOGLE_CLIENT_ID", "")
        config["client_secret"] = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    config["refresh_token"] = creds.refresh_token
    return GoogleAdsClient.load_from_dict(config, version="v21")


def run_query(client, customer_id, gaql):
    service = client.get_service("GoogleAdsService")
    request = client.get_type("SearchGoogleAdsRequest")
    request.customer_id = customer_id.replace("-", "")
    request.query = gaql
    rows = []
    try:
        for row in service.search(request=request):
            rows.append(row)
    except GoogleAdsException as ex:
        print(f"  [ERROR] Query failed for {customer_id}: {ex}")
    return rows


def get_conversion_actions(client, cid):
    gaql = """
        SELECT
            conversion_action.name,
            conversion_action.status,
            conversion_action.counting_type,
            conversion_action.include_in_conversions_metric,
            conversion_action.tag_snippets,
            conversion_action.category
        FROM conversion_action
        WHERE conversion_action.status != 'REMOVED'
    """
    rows = run_query(client, cid, gaql)
    actions = []
    for row in rows:
        ca = row.conversion_action
        actions.append({
            "name": ca.name,
            "status": ca.status.name,
            "counting_type": ca.counting_type.name,
            "include_in_conversions": ca.include_in_conversions_metric,
            "category": ca.category.name,
            # False = no native tag = likely imported from GA4 or another source
            "has_tag_snippet": len(list(ca.tag_snippets)) > 0,
        })
    return actions


def get_conversion_action_volume(client, cid):
    """
    Conversions recorded PER conversion action over the last 30 days. Lets the analyser
    tell whether a low-value primary action (e.g. a page-view) is actually firing and
    skewing bidding, versus merely being misconfigured but recording nothing - so we
    don't over-claim. Caller wraps in try/except.
    """
    gaql = """
        SELECT segments.conversion_action_name, metrics.all_conversions
        FROM customer
        WHERE segments.date DURING LAST_30_DAYS
    """
    rows = run_query(client, cid, gaql)
    vol = {}
    for row in rows:
        name = row.segments.conversion_action_name
        vol[name] = vol.get(name, 0) + row.metrics.all_conversions
    return vol


def get_campaigns(client, cid):
    gaql = """
        SELECT
            campaign.id,
            campaign.name,
            campaign.status,
            campaign.advertising_channel_type,
            campaign.bidding_strategy_type,
            campaign.target_cpa.target_cpa_micros,
            campaign.target_roas.target_roas,
            campaign_budget.amount_micros,
            campaign_budget.delivery_method,
            metrics.cost_micros,
            metrics.clicks,
            metrics.conversions,
            metrics.impressions
        FROM campaign
        WHERE campaign.status != 'REMOVED'
          AND segments.date DURING LAST_30_DAYS
    """
    rows = run_query(client, cid, gaql)
    campaigns = []
    for row in rows:
        c = row.campaign
        b = row.campaign_budget
        m = row.metrics

        tcpa_micros = c.target_cpa.target_cpa_micros
        target_cpa_gbp = round(tcpa_micros / 1_000_000, 2) if tcpa_micros else None

        troas = c.target_roas.target_roas
        target_roas = round(troas, 3) if troas else None

        campaigns.append({
            "id": str(c.id),
            "name": c.name,
            "status": c.status.name,
            "type": c.advertising_channel_type.name,
            "bid_strategy": c.bidding_strategy_type.name,
            "daily_budget_gbp": round(b.amount_micros / 1_000_000, 2),
            "spend_30d": round(m.cost_micros / 1_000_000, 2),
            "clicks_30d": m.clicks,
            "conversions_30d": round(m.conversions, 2),
            "impressions_30d": m.impressions,
            "target_cpa_gbp": target_cpa_gbp,
            "target_roas": target_roas,
        })
    return campaigns


def get_ad_groups(client, cid):
    gaql = """
        SELECT
            ad_group.id,
            ad_group.name,
            ad_group.status,
            ad_group.campaign,
            metrics.cost_micros
        FROM ad_group
        WHERE ad_group.status != 'REMOVED'
          AND segments.date DURING LAST_30_DAYS
    """
    rows = run_query(client, cid, gaql)
    ad_groups = []
    for row in rows:
        ag = row.ad_group
        m = row.metrics
        ad_groups.append({
            "id": str(ag.id),
            "name": ag.name,
            "status": ag.status.name,
            "campaign_resource": ag.campaign,
            "spend_30d": round(m.cost_micros / 1_000_000, 2),
        })
    return ad_groups


def get_keyword_match_breakdown(client, cid):
    gaql = """
        SELECT
            ad_group_criterion.keyword.match_type,
            metrics.cost_micros,
            metrics.clicks,
            metrics.impressions
        FROM keyword_view
        WHERE ad_group_criterion.status != 'REMOVED'
          AND segments.date DURING LAST_30_DAYS
    """
    rows = run_query(client, cid, gaql)
    breakdown = {"BROAD": {"spend": 0, "clicks": 0},
                 "PHRASE": {"spend": 0, "clicks": 0},
                 "EXACT": {"spend": 0, "clicks": 0}}
    for row in rows:
        mt = row.ad_group_criterion.keyword.match_type.name
        if mt in breakdown:
            breakdown[mt]["spend"] += row.metrics.cost_micros / 1_000_000
            breakdown[mt]["clicks"] += row.metrics.clicks
    for mt in breakdown:
        breakdown[mt]["spend"] = round(breakdown[mt]["spend"], 2)
    return breakdown


def get_top_search_terms(client, cid, limit=30):
    gaql = f"""
        SELECT
            search_term_view.search_term,
            search_term_view.status,
            metrics.clicks,
            metrics.cost_micros,
            metrics.conversions,
            metrics.impressions
        FROM search_term_view
        WHERE segments.date DURING LAST_30_DAYS
        ORDER BY metrics.clicks DESC
        LIMIT {limit}
    """
    rows = run_query(client, cid, gaql)
    terms = []
    for row in rows:
        st = row.search_term_view
        m = row.metrics
        terms.append({
            "term": st.search_term,
            "status": st.status.name,
            "clicks": m.clicks,
            "spend": round(m.cost_micros / 1_000_000, 2),
            "conversions": round(m.conversions, 2),
            "impressions": m.impressions,
        })
    return terms


def get_converting_unkeyworded_terms(client, cid, lookback_days=90, limit=25):
    """
    Search terms that have CONVERTED over a longer window (default 90 days) but are NOT
    added as keywords (search_term_view.status = NONE). This catches proven demand the
    account is paying for via broad/loose matching instead of capturing directly — and,
    crucially, the 'dropped ball' case where a product used to convert but a page/keyword
    change quietly stopped it being captured. The 30-day top-terms pull would miss those.
    Caller wraps in try/except.
    """
    from datetime import datetime, timedelta
    today = datetime.today()
    start = (today - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")
    gaql = f"""
        SELECT
            search_term_view.search_term,
            search_term_view.status,
            metrics.clicks,
            metrics.cost_micros,
            metrics.conversions
        FROM search_term_view
        WHERE segments.date BETWEEN '{start}' AND '{end}'
          AND metrics.conversions > 0
        ORDER BY metrics.conversions DESC
        LIMIT {limit}
    """
    rows = run_query(client, cid, gaql)
    terms = []
    for row in rows:
        st = row.search_term_view
        if st.status.name != "NONE":      # already added as a keyword (or excluded)
            continue
        m = row.metrics
        terms.append({
            "term": st.search_term,
            "status": st.status.name,
            "clicks": m.clicks,
            "spend": round(m.cost_micros / 1_000_000, 2),
            "conversions": round(m.conversions, 2),
        })
    return terms


def get_location_targeting(client, cid):
    gaql = """
        SELECT
            campaign.id,
            campaign.name,
            campaign_criterion.location.geo_target_constant,
            campaign_criterion.negative,
            campaign_criterion.type
        FROM campaign_criterion
        WHERE campaign_criterion.type = 'LOCATION'
          AND campaign.status != 'REMOVED'
    """
    rows = run_query(client, cid, gaql)
    locations = []
    for row in rows:
        cc = row.campaign_criterion
        locations.append({
            "campaign_id": str(row.campaign.id),
            "campaign_name": row.campaign.name,
            "geo_target": cc.location.geo_target_constant,
            "is_negative": cc.negative,
        })
    return locations


def get_audience_signals(client, cid):
    gaql = """
        SELECT
            campaign.id,
            campaign.name,
            campaign.advertising_channel_type,
            asset_group.id,
            asset_group.name
        FROM asset_group
        WHERE campaign.advertising_channel_type = 'PERFORMANCE_MAX'
          AND campaign.status != 'REMOVED'
    """
    rows = run_query(client, cid, gaql)
    pmax_groups = []
    for row in rows:
        pmax_groups.append({
            "campaign_id": str(row.campaign.id),
            "campaign_name": row.campaign.name,
            "asset_group_id": str(row.asset_group.id),
            "asset_group_name": row.asset_group.name,
        })

    gaql2 = """
        SELECT
            campaign.id,
            campaign.name,
            asset_group_signal.audience.audience
        FROM asset_group_signal
        WHERE campaign.advertising_channel_type = 'PERFORMANCE_MAX'
          AND campaign.status != 'REMOVED'
    """
    signal_rows = run_query(client, cid, gaql2)
    signals = [{"campaign_id": str(r.campaign.id),
                "campaign_name": r.campaign.name,
                "audience": r.asset_group_signal.audience.audience}
               for r in signal_rows]

    return {
        "pmax_asset_groups": pmax_groups,
        "audience_signals": signals,
        "has_pmax": len(pmax_groups) > 0,
        "has_audience_signals": len(signals) > 0,
    }


def get_quality_scores(client, cid):
    gaql = """
        SELECT
            ad_group_criterion.keyword.text,
            ad_group_criterion.quality_info.quality_score,
            ad_group_criterion.quality_info.creative_quality_score,
            ad_group_criterion.quality_info.post_click_quality_score,
            ad_group_criterion.quality_info.search_predicted_ctr
        FROM keyword_view
        WHERE ad_group_criterion.status != 'REMOVED'
          AND ad_group_criterion.quality_info.quality_score > 0
    """
    rows = run_query(client, cid, gaql)
    qs_list = []
    for row in rows:
        qs = row.ad_group_criterion.quality_info
        qs_list.append({
            "keyword": row.ad_group_criterion.keyword.text,
            "qs": qs.quality_score,
            "ad_relevance": qs.creative_quality_score.name,
            "landing_page": qs.post_click_quality_score.name,
            "expected_ctr": qs.search_predicted_ctr.name,
        })
    return qs_list


def get_rsa_ad_strength(client, cid):
    """
    Responsive Search Ad strength across the account (last 30 days).
    Ad Strength is Google's rating of how well-built an RSA is (headline/description
    variety + relevance). POOR/AVERAGE ads tend to win less impression share and pay
    higher CPCs, so weak ad strength is a genuine efficiency leak worth surfacing.

    Returns a summary dict. Only ENABLED RSAs count toward the quality picture
    (paused ads aren't serving). Wrapped by the caller in try/except — if the query
    fails for any reason it must not break the pipeline.
    """
    gaql = """
        SELECT
            ad_group.name,
            ad_group_ad.ad.id,
            ad_group_ad.status,
            ad_group_ad.ad_strength,
            metrics.cost_micros
        FROM ad_group_ad
        WHERE ad_group_ad.status = 'ENABLED'
          AND ad_group_ad.ad.type = 'RESPONSIVE_SEARCH_AD'
          AND segments.date DURING LAST_30_DAYS
    """
    rows = run_query(client, cid, gaql)

    by_strength = {}
    low_examples = []   # POOR / AVERAGE ads with their ad group + 30d spend
    low_spend = 0.0
    total = 0

    for row in rows:
        total += 1
        strength = row.ad_group_ad.ad_strength.name  # e.g. POOR / AVERAGE / GOOD / EXCELLENT
        by_strength[strength] = by_strength.get(strength, 0) + 1
        spend = round(row.metrics.cost_micros / 1_000_000, 2)
        if strength in ("POOR", "AVERAGE"):
            low_spend += spend
            low_examples.append({
                "ad_group": row.ad_group.name,
                "strength": strength.title(),   # "Poor" / "Average" for client-facing copy
                "spend": spend,
            })

    # Surface the highest-spend weak ads first (most commercially relevant)
    low_examples.sort(key=lambda e: e["spend"], reverse=True)
    low_count = by_strength.get("POOR", 0) + by_strength.get("AVERAGE", 0)

    return {
        "total_rsas": total,
        "by_strength": by_strength,
        "low_strength_count": low_count,
        "low_strength_spend": round(low_spend, 2),
        "low_strength_examples": low_examples[:5],
    }


def get_paused_campaign_history(client, cid, lookback_days=365):
    """
    Paused campaigns and how they performed over a longer window (default 12 months),
    so the analyser can spot efficient campaigns that were switched off. The standard
    30-day campaign pull shows paused campaigns with ~0 recent metrics, so we need this
    longer look-back to recover their historic CPA. 12 months catches campaigns that ran
    earlier in the year and were paused. Caller wraps in try/except.
    """
    from datetime import datetime, timedelta
    today = datetime.today()
    start = (today - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")
    gaql = f"""
        SELECT
            campaign.id,
            campaign.name,
            campaign.advertising_channel_type,
            metrics.cost_micros,
            metrics.conversions
        FROM campaign
        WHERE campaign.status = 'PAUSED'
          AND segments.date BETWEEN '{start}' AND '{end}'
    """
    rows = run_query(client, cid, gaql)

    # Rows are segmented by date — aggregate per campaign id.
    agg = {}
    for row in rows:
        c = row.campaign
        m = row.metrics
        key = str(c.id)
        if key not in agg:
            agg[key] = {"id": key, "name": c.name,
                        "type": c.advertising_channel_type.name,
                        "spend": 0.0, "conversions": 0.0}
        agg[key]["spend"] += m.cost_micros / 1_000_000
        agg[key]["conversions"] += m.conversions

    history = []
    for v in agg.values():
        v["spend"] = round(v["spend"], 2)
        v["conversions"] = round(v["conversions"], 2)
        v["cpa"] = round(v["spend"] / v["conversions"], 2) if v["conversions"] > 0 else None
        history.append(v)
    return history


def get_account_summary(client, cid):
    gaql = """
        SELECT
            metrics.cost_micros,
            metrics.clicks,
            metrics.conversions,
            metrics.impressions,
            metrics.ctr,
            metrics.average_cpc
        FROM customer
        WHERE segments.date DURING LAST_30_DAYS
    """
    rows = run_query(client, cid, gaql)
    total = {"spend": 0, "clicks": 0, "conversions": 0, "impressions": 0}
    for row in rows:
        m = row.metrics
        total["spend"] += m.cost_micros / 1_000_000
        total["clicks"] += m.clicks
        total["conversions"] += m.conversions
        total["impressions"] += m.impressions
    total["spend"] = round(total["spend"], 2)
    total["conversions"] = round(total["conversions"], 2)
    total["cpa"] = round(total["spend"] / total["conversions"], 2) if total["conversions"] > 0 else None
    total["ctr_pct"] = round(total["clicks"] / total["impressions"] * 100, 2) if total["impressions"] > 0 else None
    total["avg_cpc"] = round(total["spend"] / total["clicks"], 2) if total["clicks"] > 0 else None
    return total


def get_performance_summary(client, cid):
    """Fetch account-level metrics for last 30 days and last 12 months, including SIS."""
    from datetime import datetime, timedelta
    today = datetime.today()
    date_30d_start  = (today - timedelta(days=30)).strftime("%Y-%m-%d")
    date_12m_start  = (today - timedelta(days=365)).strftime("%Y-%m-%d")
    date_today      = today.strftime("%Y-%m-%d")

    def _totals(rows):
        t = {"spend": 0, "clicks": 0, "conversions": 0, "impressions": 0,
             "sis_sum": 0.0, "sis_count": 0}
        for row in rows:
            m = row.metrics
            t["spend"]       += m.cost_micros / 1_000_000
            t["clicks"]      += m.clicks
            t["conversions"] += m.conversions
            t["impressions"] += m.impressions
            sis = m.search_impression_share
            if sis and sis > 0:
                t["sis_sum"]   += sis
                t["sis_count"] += 1
        t["spend"]       = round(t["spend"], 2)
        t["conversions"] = round(t["conversions"], 2)
        t["cpa"]  = round(t["spend"] / t["conversions"], 2) if t["conversions"] > 0 else None
        t["cvr"]  = round(t["conversions"] / t["clicks"] * 100, 2) if t["clicks"] > 0 else None
        t["sis"]  = round(t["sis_sum"] / t["sis_count"] * 100, 1) if t["sis_count"] > 0 else None
        return t

    # Core metrics — no SIS (works for all campaign types including PMax)
    gaql_30d = f"""
        SELECT
            metrics.cost_micros,
            metrics.clicks,
            metrics.conversions,
            metrics.impressions
        FROM campaign
        WHERE campaign.status != 'REMOVED'
          AND segments.date BETWEEN '{date_30d_start}' AND '{date_today}'
    """
    gaql_12m = f"""
        SELECT
            metrics.cost_micros,
            metrics.clicks,
            metrics.conversions,
            metrics.impressions
        FROM campaign
        WHERE campaign.status != 'REMOVED'
          AND segments.date BETWEEN '{date_12m_start}' AND '{date_today}'
    """
    # SIS — Search campaigns only (PMax doesn't support this metric)
    # Impression share trio: overall SIS, absolute-top (very first ad), and top-of-page.
    # A falling absolute-top / top share signals losing visibility on your best terms.
    _sis_cols = ("metrics.search_impression_share, "
                 "metrics.search_absolute_top_impression_share, "
                 "metrics.search_top_impression_share")
    gaql_sis_30d = f"""
        SELECT {_sis_cols}
        FROM campaign
        WHERE campaign.status != 'REMOVED'
          AND campaign.advertising_channel_type = 'SEARCH'
          AND segments.date BETWEEN '{date_30d_start}' AND '{date_today}'
    """
    gaql_sis_12m = f"""
        SELECT {_sis_cols}
        FROM campaign
        WHERE campaign.status != 'REMOVED'
          AND campaign.advertising_channel_type = 'SEARCH'
          AND segments.date BETWEEN '{date_12m_start}' AND '{date_today}'
    """

    rows_30d = run_query(client, cid, gaql_30d)
    rows_12m = run_query(client, cid, gaql_12m)

    t30 = _totals(rows_30d)
    t12 = _totals(rows_12m)

    def _avg_share(t, rows):
        sis_s = sis_n = abt_s = abt_n = top_s = top_n = 0
        for row in rows:
            m = row.metrics
            if m.search_impression_share and m.search_impression_share > 0:
                sis_s += m.search_impression_share; sis_n += 1
            if m.search_absolute_top_impression_share and m.search_absolute_top_impression_share > 0:
                abt_s += m.search_absolute_top_impression_share; abt_n += 1
            if m.search_top_impression_share and m.search_top_impression_share > 0:
                top_s += m.search_top_impression_share; top_n += 1
        t["sis"]     = round(sis_s / sis_n * 100, 1) if sis_n else None
        t["abs_top"] = round(abt_s / abt_n * 100, 1) if abt_n else None
        t["top"]     = round(top_s / top_n * 100, 1) if top_n else None

    # Overlay impression-share metrics separately — safe to fail
    try:
        _avg_share(t30, run_query(client, cid, gaql_sis_30d))
        _avg_share(t12, run_query(client, cid, gaql_sis_12m))
    except Exception as e:
        print(f"  ⚠ Impression-share query failed (non-fatal): {e}")

    return {
        # 30 days  (money shown in whole pounds, no pence)
        "spend_30d":  f"£{int(t30['spend']):,}",
        "impr_30d":   f"{t30['impressions']:,}",
        "clicks_30d": f"{t30['clicks']:,}",
        "convs_30d":  f"{int(round(t30['conversions'])):,}",
        "cvr_30d":    f"{t30['cvr']}%" if t30["cvr"] is not None else "N/A",
        "cpa_30d":    f"£{int(t30['cpa']):,}" if t30["cpa"] else "N/A",
        "sis_30d":    f"{t30['sis']}%" if t30["sis"] else "N/A",
        # 12 months
        "spend_12m":  f"£{int(t12['spend']):,}",
        "impr_12m":   f"{t12['impressions']:,}",
        "clicks_12m": f"{t12['clicks']:,}",
        "convs_12m":  f"{int(round(t12['conversions'])):,}",
        "cvr_12m":    f"{t12['cvr']}%" if t12["cvr"] is not None else "N/A",
        "cpa_12m":    f"£{int(t12['cpa']):,}" if t12["cpa"] else "N/A",
        "sis_12m":    f"{t12['sis']}%" if t12["sis"] else "N/A",
        # Raw numbers for GPT commentary
        "_raw": {"t30": t30, "t12": t12},
    }


def fetch_account_data(client_cid: str) -> dict:
    print(f"\n🔐 Authenticating...")
    creds = get_credentials()
    client = build_client(creds)
    cid = client_cid.replace("-", "")
    print(f"📡 Connected. Pulling data for CID: {client_cid}")

    print("  → Conversion actions...")
    conversion_actions = get_conversion_actions(client, cid)

    print("  → Conversion volume per action (30d)...")
    try:
        _ca_vol = get_conversion_action_volume(client, cid)
        for ca in conversion_actions:
            ca["conversions_30d"] = round(_ca_vol.get(ca["name"], 0), 2)
        _firing = [ca["name"] for ca in conversion_actions if ca.get("conversions_30d", 0) > 0]
        print(f"    {len(_firing)} action(s) recording conversions")
    except Exception as e:
        print(f"    (per-action volume query failed: {e})")
        # leave conversions_30d unset → analyser treats volume as unknown (cautious wording)

    print("  → Campaigns...")
    campaigns = get_campaigns(client, cid)

    print("  → Ad groups...")
    ad_groups = get_ad_groups(client, cid)

    print("  → Keyword match type breakdown...")
    keyword_match_breakdown = get_keyword_match_breakdown(client, cid)

    print("  → Negative keyword count...")
    neg_kw_total = 0
    try:
        ga_service = client.get_service("GoogleAdsService")
        neg_response = ga_service.search(
            customer_id=cid,
            query="""
                SELECT ad_group_criterion.keyword.text
                FROM ad_group_criterion
                WHERE ad_group_criterion.negative = TRUE
                  AND ad_group_criterion.status != 'REMOVED'
            """
        )
        for row in neg_response:
            neg_kw_total += 1
        neg_response2 = ga_service.search(
            customer_id=cid,
            query="""
                SELECT campaign_criterion.keyword.text
                FROM campaign_criterion
                WHERE campaign_criterion.negative = TRUE
                  AND campaign_criterion.status != 'REMOVED'
            """
        )
        for row in neg_response2:
            neg_kw_total += 1
    except Exception as e:
        print(f"    (negative keyword query failed: {e})")
        neg_kw_total = None

    print("  → Auto-apply recommendations...")
    auto_apply_enabled = False
    auto_apply_types = []
    try:
        ga_service = client.get_service("GoogleAdsService")
        aar_response = ga_service.search(
            customer_id=cid,
            query="""
                SELECT recommendation_subscription.type,
                       recommendation_subscription.status
                FROM recommendation_subscription
                WHERE recommendation_subscription.status = 'ENABLED'
            """
        )
        rows = list(aar_response)
        auto_apply_enabled = len(rows) > 0
        # Capture WHICH recommendation types are auto-applied (enum names) so the
        # analyser can flag only types outside the team's approved set.
        auto_apply_types = sorted({r.recommendation_subscription.type.name for r in rows})
        if auto_apply_types:
            print(f"    auto-apply types enabled: {auto_apply_types}")
    except Exception as e:
        print(f"    (auto-apply query failed: {e})")
        auto_apply_enabled = None
        auto_apply_types = []

    print("  → Top search terms...")
    top_search_terms = get_top_search_terms(client, cid)

    print("  → Converting search terms not added as keywords (90d)...")
    try:
        converting_unkeyworded_terms = get_converting_unkeyworded_terms(client, cid)
        if converting_unkeyworded_terms:
            print(f"    {len(converting_unkeyworded_terms)} converting term(s) not added as keywords")
    except Exception as e:
        print(f"    (converting-unkeyworded query failed: {e})")
        converting_unkeyworded_terms = None

    print("  → Location targeting...")
    location_targeting = get_location_targeting(client, cid)

    print("  → Audience signals...")
    audience_signals = get_audience_signals(client, cid)

    print("  → Quality scores...")
    quality_scores = get_quality_scores(client, cid)

    print("  → RSA ad strength...")
    try:
        rsa_ad_strength = get_rsa_ad_strength(client, cid)
        if rsa_ad_strength.get("total_rsas"):
            print(f"    {rsa_ad_strength['total_rsas']} RSAs; "
                  f"{rsa_ad_strength['low_strength_count']} Poor/Average")
    except Exception as e:
        print(f"    (RSA ad strength query failed: {e})")
        rsa_ad_strength = None

    print("  → Paused campaign history (12 months)...")
    try:
        paused_campaign_history = get_paused_campaign_history(client, cid)
        if paused_campaign_history:
            print(f"    {len(paused_campaign_history)} paused campaign(s) with history")
    except Exception as e:
        print(f"    (paused campaign history query failed: {e})")
        paused_campaign_history = None

    print("  → 30-day account summary...")
    account_summary = get_account_summary(client, cid)

    print("  → Performance summary (30d vs 12M)...")
    performance_summary = get_performance_summary(client, cid)

    campaign_types = list({c["type"] for c in campaigns if c["status"] == "ENABLED"})

    data = {
        "client_cid": client_cid,
        "account_summary_30d": account_summary,
        "campaigns": campaigns,
        "campaign_types_active": campaign_types,
        "ad_groups": ad_groups,
        "conversion_actions": conversion_actions,
        "keyword_match_breakdown": keyword_match_breakdown,
        "top_search_terms": top_search_terms,
        "converting_unkeyworded_terms": converting_unkeyworded_terms,
        "location_targeting": location_targeting,
        "audience_signals": audience_signals,
        "quality_scores": quality_scores,
        "rsa_ad_strength": rsa_ad_strength,
        "paused_campaign_history": paused_campaign_history,
        "negative_keyword_count": neg_kw_total,
        "auto_apply_recommendations": auto_apply_enabled,
        "auto_apply_types": auto_apply_types,
        "performance_summary": performance_summary,
    }

    print(f"\n✅ Data pull complete. {len(campaigns)} campaigns, {len(ad_groups)} ad groups, "
          f"{len(conversion_actions)} conversion actions found.")
    return data


if __name__ == "__main__":
    TEST_CID = "981-476-6301"
    result = fetch_account_data(TEST_CID)
    print("\n── SAMPLE OUTPUT ──")
    print(json.dumps(result["account_summary_30d"], indent=2))
    print(f"\nCampaigns found: {len(result['campaigns'])}")
    for c in result["campaigns"][:5]:
        tcpa = c.get("target_cpa_gbp")
        print(f"  {c['name']} ({c['type']}) — £{c['spend_30d']} spend / tCPA: {tcpa}")
