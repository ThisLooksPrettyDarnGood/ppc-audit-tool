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

CREDENTIALS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "client_secret.json")
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
    secret_data = json.loads(Path(CREDENTIALS_PATH).read_text())["installed"]
    config["client_id"] = secret_data["client_id"]
    config["client_secret"] = secret_data["client_secret"]
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


def fetch_account_data(client_cid: str) -> dict:
    print(f"\n🔐 Authenticating...")
    creds = get_credentials()
    client = build_client(creds)
    cid = client_cid.replace("-", "")
    print(f"📡 Connected. Pulling data for CID: {client_cid}")

    print("  → Conversion actions...")
    conversion_actions = get_conversion_actions(client, cid)

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
    try:
        ga_service = client.get_service("GoogleAdsService")
        aar_response = ga_service.search(
            customer_id=cid,
            query="""
                SELECT recommendation_subscription.status
                FROM recommendation_subscription
                WHERE recommendation_subscription.status = 'ENABLED'
            """
        )
        rows = list(aar_response)
        auto_apply_enabled = len(rows) > 0
    except Exception as e:
        print(f"    (auto-apply query failed: {e})")
        auto_apply_enabled = None

    print("  → Top search terms...")
    top_search_terms = get_top_search_terms(client, cid)

    print("  → Location targeting...")
    location_targeting = get_location_targeting(client, cid)

    print("  → Audience signals...")
    audience_signals = get_audience_signals(client, cid)

    print("  → Quality scores...")
    quality_scores = get_quality_scores(client, cid)

    print("  → 30-day account summary...")
    account_summary = get_account_summary(client, cid)

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
        "location_targeting": location_targeting,
        "audience_signals": audience_signals,
        "quality_scores": quality_scores,
        "negative_keyword_count": neg_kw_total,
        "auto_apply_recommendations": auto_apply_enabled,
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
