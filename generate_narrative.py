"""
Step 5 — AI Narrative Layer
Takes the findings dict from analyse_account.py and generates slide copy
for each of the 4 audit sections via OpenAI.

Also generates:
  - Executive Summary
  - Key Opportunities
  - Key Takeaways

Saves output to: ~/Desktop/ppc-audit-tool/narrative_output.json
"""

import os
import json
from openai import OpenAI

# ── Helpers ──────────────────────────────────────────────────────────────────

def _call_openai(client: OpenAI, system_prompt: str, user_prompt: str) -> str:
    response = client.chat.completions.create(
        model="gpt-5.5",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        max_completion_tokens=2000,
    )
    return response.choices[0].message.content.strip()


def _parse_response(raw: str) -> dict:
    """
    Expects the model to return exactly this format:
    HEADLINE: ...
    WHATS_HAPPENING_1: ...
    WHATS_HAPPENING_2: ...
    WHY_IT_MATTERS_1: ...
    WHY_IT_MATTERS_2: ...
    REC1: ...
    REC2: ...
    REC3: ...
    """
    lines = {}
    for line in raw.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            lines[key.strip().upper()] = val.strip()

    recs = [r for r in [
        lines.get("REC1", ""),
        lines.get("REC2", ""),
        lines.get("REC3", ""),
    ] if r]

    # Build bullet strings for whats_happening and why_it_matters
    wh_bullets = [lines.get("WHATS_HAPPENING_1", ""), lines.get("WHATS_HAPPENING_2", "")]
    wh_bullets = [b for b in wh_bullets if b]
    whats_happening = "\n".join(f"• {b}" for b in wh_bullets)

    wm_bullets = [lines.get("WHY_IT_MATTERS_1", ""), lines.get("WHY_IT_MATTERS_2", "")]
    wm_bullets = [b for b in wm_bullets if b]
    why_it_matters = "\n".join(f"• {b}" for b in wm_bullets)

    return {
        "headline":        lines.get("HEADLINE", ""),
        "whats_happening": whats_happening,
        "why_it_matters":  why_it_matters,
        "recommendations": recs,
    }


# ── System prompt (shared) ────────────────────────────────────────────────────

SYSTEM_PROMPT = """
You are a senior Google Ads auditor writing copy for a client-facing audit presentation.
Your tone is professional, direct, and consultative — not salesy.
You write for non-technical business owners who care about results, not jargon.
Always use British English spelling (e.g. optimise, recognise, behaviour, prioritise).

Each section of the audit follows this exact 3-part structure used by the audit team:
1. What's happening — 2 short bullet points describing the current account situation in plain English.
2. Why it matters commercially — 2 short bullet points on the real business impact. What is this costing them? What are they missing out on? Keep it grounded in pounds, performance, and growth.
3. Our recommendation — 2 to 3 specific, actionable steps starting with a verb.

Keep the headline under 12 words.
Each WHATS_HAPPENING bullet must be under 25 words — factual and plain.
Each WHY_IT_MATTERS bullet must be under 30 words — commercial, urgent but not alarmist.
Each recommendation must be a single actionable sentence starting with a verb.

Respond in EXACTLY this format (no extra text, no markdown):
HEADLINE: <headline>
WHATS_HAPPENING_1: <first bullet point — what is happening in the account>
WHATS_HAPPENING_2: <second bullet point — what is happening in the account>
WHY_IT_MATTERS_1: <first bullet point — commercial impact in plain English>
WHY_IT_MATTERS_2: <second bullet point — commercial impact in plain English>
REC1: <first recommendation>
REC2: <second recommendation>
REC3: <third recommendation>
""".strip()


# ── Section generators ────────────────────────────────────────────────────────

def _narrative_conversion_tracking(client: OpenAI, findings: dict) -> dict:
    section = findings.get("conversion_tracking", {})
    rag     = section.get("rag", "AMBER")
    issues  = section.get("issues", [])
    data    = section.get("data", {})

    issues_text = "\n".join(f"- {i}" for i in issues) if issues else "- No major issues detected."

    prompt = f"""
Write slide copy for the Conversion Tracking section of a Google Ads audit.

RAG status: {rag}
Issues found:
{issues_text}

Supporting data: {data}

Key rules for this section:
- If the primary conversion is imported from GA4 rather than a native web tag, flag this explicitly — it prevents Enhanced Conversions from working.
- Always recommend replacing GA4 imports with a direct web-based conversion tag.
- Always recommend checking that Enhanced Conversions is correctly configured.
- If there is a phone number on the website, recommend reviewing call tracking.
- The WHY_IT_MATTERS bullets must explain what the missing data means for the bidding algorithm, not just say "data is missing".
""".strip()

    raw = _call_openai(client, SYSTEM_PROMPT, prompt)
    return _parse_response(raw)


def _narrative_account_structure(client: OpenAI, findings: dict) -> dict:
    section = findings.get("account_structure", {})
    rag     = section.get("rag", "AMBER")
    issues  = section.get("issues", [])
    data    = section.get("data", {})

    issues_text = "\n".join(f"- {i}" for i in issues) if issues else "- No major issues detected."

    prompt = f"""
Write slide copy for the Account Structure section of a Google Ads audit.

RAG status: {rag}
Issues found:
{issues_text}

Supporting data: {data}

Key rules for this section:
- If all keywords are in a single campaign or ad group, explain that this means a handful of keywords dominate the budget and others get starved of spend.
- If budget is spread too thin across too many campaigns, flag the risk of no campaign having enough data to optimise.
- If the account has a shared budget across campaigns, flag that this restricts individual campaign growth.
- If GREEN, write encouraging copy that validates their structure and gives 1–2 tips to maintain it.
- The WHY_IT_MATTERS bullets must be about money and growth, not technical structure.
""".strip()

    raw = _call_openai(client, SYSTEM_PROMPT, prompt)
    return _parse_response(raw)


def _narrative_targeting_keywords(client: OpenAI, findings: dict) -> dict:
    section = findings.get("targeting_keywords", {})
    rag     = section.get("rag", "AMBER")
    issues  = section.get("issues", [])
    data    = section.get("data", {})

    issues_text = "\n".join(f"- {i}" for i in issues) if issues else "- No major issues detected."

    neg_kw_count = data.get("negative_keyword_count", None)
    neg_kw_note  = f"The account has {neg_kw_count} negative keywords applied." if neg_kw_count is not None else ""

    prompt = f"""
Write slide copy for the Targeting & Keywords section of a Google Ads audit.

RAG status: {rag}
Issues found:
{issues_text}

Supporting data: {data}
{neg_kw_note}

Key rules for this section:
- If the account has very few negative keywords (under 50), flag this explicitly and explain that irrelevant searches are likely wasting budget.
- If the account uses predominantly broad match keywords, explain the risk of serving irrelevant traffic and driving up costs.
- If the account uses only exact match, explain the risk of limiting reach and missing relevant search variations.
- If PMax campaigns are running with few negative keywords, escalate the concern — PMax runs broadly by default.
- The WHY_IT_MATTERS bullets must explain the financial consequence: wasted spend on irrelevant clicks, missed qualified leads, inflated CPCs.
""".strip()

    raw = _call_openai(client, SYSTEM_PROMPT, prompt)
    return _parse_response(raw)


def _narrative_bidding_strategy(client: OpenAI, findings: dict) -> dict:
    section = findings.get("bidding_strategy", {})
    rag     = section.get("rag", "AMBER")
    issues  = section.get("issues", [])
    data    = section.get("data", {})

    issues_text = "\n".join(f"- {i}" for i in issues) if issues else "- No major issues detected."

    account_type = findings.get("account_type", "unknown")

    prompt = f"""
Write slide copy for the Bidding Strategy section of a Google Ads audit.

RAG status: {rag}
Issues found:
{issues_text}

Supporting data: {data}
Account type: {account_type}

Key rules for this section:
- For lead gen accounts: the correct bid strategy is Maximise Conversions (moving to tCPA once there is sufficient conversion data). Never recommend reverting to Manual CPC — that is a step backwards.
- For eCommerce accounts: the correct bid strategy is Maximise Conversion Value (moving to tROAS once there is sufficient data). Maximise Conversions is not appropriate for eCommerce as it ignores revenue value.
- If smart bidding has too few conversions to learn (under 30/month), the recommendation is NOT to switch to manual — it is to build conversion volume first, or temporarily lower the conversion goal threshold so more signals feed the algorithm.
- If the account type is unknown, default to lead gen recommendations. Do NOT include a generic disclaimer about eCommerce — just give the lead gen advice confidently.
- If Max Clicks is running, always recommend switching to Maximise Conversions — frame it as "your budget is optimising for website visits, not customers".
- If bid strategies are inconsistent across campaigns, flag this as sending conflicting signals to Google.
- The WHY_IT_MATTERS bullets must explain what the wrong or under-powered bidding strategy is costing them in missed conversions or revenue — be specific about the learning state problem if conversion volume is low.
""".strip()

    raw = _call_openai(client, SYSTEM_PROMPT, prompt)
    return _parse_response(raw)


def _narrative_executive_summary(client: OpenAI, findings: dict, issues: list) -> dict:
    """Generates the Executive Summary slide content."""

    section_names = ["Conversion Tracking", "Account Structure", "Targeting & Keywords", "Bidding Strategy"]

    issues_detail = "\n".join(
        f"- {s} ({i.get('rag','AMBER').upper()}):\n"
        f"  What's happening: {i.get('whats_happening','').replace(chr(10), ' ')}\n"
        f"  Why it matters: {i.get('why_it_matters','').replace(chr(10), ' ')}"
        for s, i in zip(section_names, issues)
    )

    prompt = f"""
Write the Executive Summary slide for a Google Ads audit presentation.

Here are the full findings from the audit:
{issues_detail}

Rules:
- The headline must be a single punchy sentence — maximum 10 words. Name the actual problem, not the section. E.g. "Blind bidding and wasted spend are limiting growth" not "Account tracking and structure need improvement".
- The 3 bullets must each reference a specific finding from above — use real details (numbers, named issues, specific tools like Enhanced Conversions or Auto-Apply). No generic statements.
- COMMERCIAL_IMPACT: 1–2 sentences on what this is costing the business right now if nothing changes. Be specific about the mechanisms (e.g. wasted spend on broad match, bidding in learning state, missed conversions).
- Use British English spelling.

Respond in EXACTLY this format (no extra text, no markdown):
EXEC_HEADLINE: <punchy specific headline — under 15 words>
BULLET_1: <specific key finding 1 with real details>
BULLET_2: <specific key finding 2 with real details>
BULLET_3: <specific key finding 3 with real details>
COMMERCIAL_IMPACT: <specific commercial impact — 1–2 sentences>
""".strip()

    raw = _call_openai(client, SYSTEM_PROMPT, prompt)

    lines = {}
    for line in raw.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            lines[key.strip().upper()] = val.strip()

    return {
        "headline":          lines.get("EXEC_HEADLINE", ""),
        "bullet_1":          lines.get("BULLET_1", ""),
        "bullet_2":          lines.get("BULLET_2", ""),
        "bullet_3":          lines.get("BULLET_3", ""),
        "commercial_impact": lines.get("COMMERCIAL_IMPACT", ""),
    }


def _narrative_key_opportunities(client: OpenAI, findings: dict, issues: list) -> str:
    """Generates the Key Opportunities slide content."""

    section_names = ["Conversion Tracking", "Account Structure", "Targeting & Keywords", "Bidding Strategy"]

    issues_detail = "\n".join(
        f"- {s} ({i.get('rag','AMBER')}):\n"
        f"  What's happening: {i.get('whats_happening','').replace(chr(10), ' ')}\n"
        f"  Why it matters: {i.get('why_it_matters','').replace(chr(10), ' ')}"
        for s, i in zip(section_names, issues)
    )

    prompt = f"""
Write the Key Opportunities section for a Google Ads audit presentation.

Here are the full details of what was found in this account:
{issues_detail}

Write exactly 4 key opportunities — what could this client unlock if they fix these issues?
Rules:
- Each opportunity must be directly tied to a specific finding above — reference real details (e.g. "12 conversion actions", "88% broad match", "9 conversions/month").
- Start each with a strong verb (e.g. "Recover", "Unlock", "Cut", "Scale", "Gain", "Eliminate").
- Focus on tangible business outcomes: more leads, lower CPA, recovered wasted spend, scalable growth.
- Be punchy and specific — these are punchy value statements, not generic descriptions. Under 18 words each.
- Use British English spelling.

Respond with just the 4 opportunities, one per line, no bullet points or numbering.
""".strip()

    return _call_openai(client, "You are a concise copywriter. Follow the user's instructions exactly.", prompt)


def _narrative_takeaways(client: OpenAI, findings: dict, issues: list) -> list:
    """Generates the 3-row Key Takeaways table content."""

    section_names = ["Conversion Tracking", "Account Structure", "Targeting & Keywords", "Bidding Strategy"]

    issues_detail = "\n".join(
        f"- {s} ({i.get('rag','AMBER')}):\n"
        f"  What's happening: {i.get('whats_happening','').replace(chr(10), ' ')}\n"
        f"  Why it matters: {i.get('why_it_matters','').replace(chr(10), ' ')}\n"
        f"  Recommendations: {'; '.join(i.get('recommendations', []))}"
        for s, i in zip(section_names, issues)
    )

    prompt = f"""
Write the Key Takeaways table for a Google Ads audit presentation.
The table has 3 rows. Each row has 3 columns: Current State, Changes Needed, Future State.

Pick the 3 most impactful issues from the full audit detail below and write one row for each.
Each row must be grounded in specific details from the findings — no generic copy.

Full audit findings:
{issues_detail}

Rules:
- Current State: describe the specific problem in plain English. Reference real details (numbers, percentages, specific issues). Under 20 words.
- Changes Needed: one concrete action starting with a verb. Under 15 words.
- Future State: the specific business benefit that unlocks. Under 15 words.
- Use British English spelling.
- Do NOT just repeat the section name — describe the actual situation.

Respond in EXACTLY this format (no extra text, no markdown):
TK1_CURRENT: <current state>
TK1_CHANGES: <changes needed>
TK1_FUTURE: <future state>
TK2_CURRENT: <current state>
TK2_CHANGES: <changes needed>
TK2_FUTURE: <future state>
TK3_CURRENT: <current state>
TK3_CHANGES: <changes needed>
TK3_FUTURE: <future state>
""".strip()

    raw = _call_openai(client, SYSTEM_PROMPT, prompt)

    lines = {}
    for line in raw.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            lines[key.strip().upper()] = val.strip()

    return [
        {
            "current_state":  lines.get("TK1_CURRENT", ""),
            "changes_needed": lines.get("TK1_CHANGES", ""),
            "future_state":   lines.get("TK1_FUTURE", ""),
        },
        {
            "current_state":  lines.get("TK2_CURRENT", ""),
            "changes_needed": lines.get("TK2_CHANGES", ""),
            "future_state":   lines.get("TK2_FUTURE", ""),
        },
        {
            "current_state":  lines.get("TK3_CURRENT", ""),
            "changes_needed": lines.get("TK3_CHANGES", ""),
            "future_state":   lines.get("TK3_FUTURE", ""),
        },
    ]


# ── Main entry point ──────────────────────────────────────────────────────────

def generate_narrative(findings: dict, openai_api_key: str, client_name: str = "") -> dict:
    """
    Args:
        findings:       the dict returned by analyse_account.py's run_analysis()
        openai_api_key: your OpenAI API key
        client_name:    the client's business name (used on the slides)

    Returns:
        Full narrative dict ready to be saved as narrative_output.json
    """
    client = OpenAI(api_key=openai_api_key)

    print("  → Generating Conversion Tracking narrative...")
    conv  = _narrative_conversion_tracking(client, findings)
    conv["rag"] = findings.get("conversion_tracking", {}).get("rag", "AMBER")

    print("  → Generating Account Structure narrative...")
    struc = _narrative_account_structure(client, findings)
    struc["rag"] = findings.get("account_structure", {}).get("rag", "AMBER")

    print("  → Generating Targeting & Keywords narrative...")
    targ  = _narrative_targeting_keywords(client, findings)
    targ["rag"] = findings.get("targeting_keywords", {}).get("rag", "AMBER")

    print("  → Generating Bidding Strategy narrative...")
    bid   = _narrative_bidding_strategy(client, findings)
    bid["rag"] = findings.get("bidding_strategy", {}).get("rag", "AMBER")

    issues = [conv, struc, targ, bid]

    # Overall RAG = worst of the four
    rag_order = {"RED": 0, "AMBER": 1, "GREEN": 2}
    overall_rag = min(
        [conv["rag"], struc["rag"], targ["rag"], bid["rag"]],
        key=lambda r: rag_order.get(r.upper(), 1)
    )

    print("  → Generating Executive Summary...")
    exec_sum = _narrative_executive_summary(client, findings, issues)

    print("  → Generating Key Opportunities...")
    opps = _narrative_key_opportunities(client, findings, issues)

    print("  → Generating Key Takeaways...")
    takeaways = _narrative_takeaways(client, findings, issues)

    return {
        "client_name": client_name,
        "account_cid": findings.get("account_cid", ""),
        "overall_rag": overall_rag,
        "issues": issues,
        "executive_summary": exec_sum,
        "objectives": {
            "objectives_text":  "",   # filled in manually before the meeting
            "success_metric":   "",
            "pain_points_text": "",
        },
        "key_opportunities": opps,
        "takeaways": takeaways,
    }


# ── Quick test ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from fetch_account_data import fetch_account_data
    from analyse_account    import analyse_account as run_analysis

    API_KEY = os.environ.get("OPENAI_API_KEY", "")
    if not API_KEY:
        raise ValueError("Set OPENAI_API_KEY environment variable before running.")

    CID         = "981-476-6301"
    CLIENT_NAME = input("Enter client name (e.g. Acme Ltd): ").strip()

    print("\nFetching account data...")
    data = fetch_account_data(CID)

    print("Running analysis engine...")
    findings = run_analysis(data)
    findings["account_cid"] = CID

    print("Generating AI narratives...")
    narrative = generate_narrative(findings, API_KEY, CLIENT_NAME)

    # Save to JSON
    output_path = os.path.expanduser("~/Desktop/ppc-audit-tool/narrative_output.json")
    with open(output_path, "w") as f:
        json.dump(narrative, f, indent=2)

    print(f"\n✅ Narrative saved to {output_path}")
    print("\nPreview:")
    for i, issue in enumerate(narrative["issues"], 1):
        print(f"\n  Issue {i}: {issue['headline']}")
