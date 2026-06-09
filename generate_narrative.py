"""
Step 5  -  AI Narrative Layer
Takes the findings dict from analyse_account.py and generates slide copy
for each of the 4 audit sections via OpenAI.

Also generates:
  - Executive Summary
  - Key Opportunities
  - Key Takeaways

Saves output to: ~/Desktop/ppc-audit-tool/narrative_output.json
"""

import os
import re
import json
from openai import OpenAI
from audit_style_examples import (
    STYLE_NOTES, EXAMPLES, example_block,
    EXEC_SUMMARY_EXAMPLE, TAKEAWAYS_EXAMPLE, OPPORTUNITIES_EXAMPLE,
)

# ── Helpers ──────────────────────────────────────────────────────────────────

_total_tokens = 0  # module-level token counter, reset per generate_narrative() call


def _call_openai(client: OpenAI, system_prompt: str, user_prompt: str) -> str:
    global _total_tokens
    response = client.chat.completions.create(
        model="gpt-5.5",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        max_completion_tokens=2000,
    )
    if response.usage:
        _total_tokens += response.usage.total_tokens
    text = response.choices[0].message.content.strip()
    # Remove em/en dashes (a common LLM tell) and replace with a spaced hyphen.
    # Unicode escapes so a source-wide dash scrub can't accidentally neutralise this.
    text = text.replace("\u2014", " - ").replace("\u2013", " - ")
    # Money is shown in whole pounds (no pence)  -  drop the decimals: £239.58 -> £239.
    # Safety net in case the model still writes pence despite the style rule.
    text = re.sub(r'(£\d[\d,]*)\.\d{1,2}\b', r'\1', text)
    return text


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
Your tone is professional, direct, and consultative  -  not salesy.
You write for non-technical business owners who care about results, not jargon.
Always use British English spelling (e.g. optimise, recognise, behaviour, prioritise).

Each section of the audit follows this exact 3-part structure used by the audit team:
1. What's happening  -  2 short bullet points describing the current account situation in plain English.
2. Why it matters commercially  -  2 short bullet points on the real business impact. What is this costing them? What are they missing out on? Keep it grounded in pounds, performance, and growth.
3. Our recommendation  -  2 to 3 specific, actionable steps starting with a verb.

Keep the headline under 10 words.
Each WHATS_HAPPENING bullet: aim 12-18 words, hard max 22  -  one fact, plainly stated.
Each WHY_IT_MATTERS bullet: aim 12-18 words, hard max 24  -  one commercial consequence.
Each recommendation must be a single actionable sentence starting with a verb (under 18 words).
Do not pack two ideas into one bullet. Shorter and sharper beats longer and complete.

Respond in EXACTLY this format (no extra text, no markdown):
HEADLINE: <headline>
WHATS_HAPPENING_1: <first bullet point  -  what is happening in the account>
WHATS_HAPPENING_2: <second bullet point  -  what is happening in the account>
WHY_IT_MATTERS_1: <first bullet point  -  commercial impact in plain English>
WHY_IT_MATTERS_2: <second bullet point  -  commercial impact in plain English>
REC1: <first recommendation>
REC2: <second recommendation>
REC3: <third recommendation>
""".strip()

# Fold the team's distilled house style into the shared system prompt.
SYSTEM_PROMPT = SYSTEM_PROMPT + "\n\n" + STYLE_NOTES


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
- If the primary conversion is imported from GA4 rather than a native web tag, flag this explicitly  -  it prevents Enhanced Conversions from working.
- Always recommend replacing GA4 imports with a direct web-based conversion tag.
- Always recommend checking that Enhanced Conversions is correctly configured.
- If there is a phone number on the website, recommend reviewing call tracking.
- The WHY_IT_MATTERS bullets must explain what the missing data means for the bidding algorithm, not just say "data is missing".
- Conversion action names like "generate_lead", "purchase" or "submit_lead_form" are Google's system names  -  refer to them in plain English (e.g. "your lead action", "your purchase conversion"). Never print the raw system name on a client slide.
""".strip()

    prompt += "\n\n" + example_block(EXAMPLES["conversion_tracking"])
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
- If GREEN, write encouraging copy that validates their structure and gives 1 - 2 tips to maintain it.
- The WHY_IT_MATTERS bullets must be about money and growth, not technical structure.
- IMPORTANT: if the findings include a description of the actual structure (e.g. a lean, appropriate setup), LEAD with that real structure. Treat Auto-Apply Recommendations as a secondary point, not the whole slide  -  unless it is genuinely the only finding.
""".strip()

    prompt += "\n\n" + example_block(EXAMPLES["account_structure"])
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
- If PMax campaigns are running with few negative keywords, escalate the concern  -  PMax runs broadly by default.
- The WHY_IT_MATTERS bullets must explain the financial consequence: wasted spend on irrelevant clicks, missed qualified leads, inflated CPCs.
- If the findings mention search terms that are converting but not yet added as keywords, or high-traffic terms spending without converting, treat these as high-value, specific points worth including  -  they reflect a proper search-query-report review.
- If the findings mention responsive search ads rated Poor or Average ad strength, include this as a clear, specific point (use the real counts/spend) and call them "responsive search ads". Frame it as an efficiency opportunity, not a crisis. IMPORTANT: Ad Strength is Google's guide to how well-built an RSA is - it is NOT itself an auction or Ad Rank factor, so do NOT claim weak ad strength directly "reduces impression share" or "raises CPCs". Instead explain that the distinct, relevant headlines and descriptions that earn a strong rating are what improve CTR and Quality Score - that is where the efficiency gain comes from. Recommend adding more distinct, keyword-relevant headlines and descriptions. Lead with the search-term/match-type story first if both are present.
""".strip()

    prompt += "\n\n" + example_block(EXAMPLES["targeting_keywords"])
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
- For lead gen accounts: the correct bid strategy is Maximise Conversions (moving to tCPA once there is sufficient conversion data). Never recommend reverting to Manual CPC  -  that is a step backwards.
- For eCommerce accounts: the correct bid strategy is Maximise Conversion Value (moving to tROAS once there is sufficient data). Maximise Conversions is not appropriate for eCommerce as it ignores revenue value.
- If smart bidding has too few conversions to learn (under 30/month), the recommendation is NOT to switch to manual  -  it is to build conversion volume first, or temporarily lower the conversion goal threshold so more signals feed the algorithm.
- If the account type is unknown, default to lead gen recommendations. Do NOT include a generic disclaimer about eCommerce  -  just give the lead gen advice confidently.
- If Max Clicks is running, always recommend switching to Maximise Conversions  -  frame it as "your budget is optimising for website visits, not customers".
- If bid strategies are inconsistent across campaigns, flag this as sending conflicting signals to Google.
- If the findings mention paused campaigns that historically converted below the account's current CPA, treat this as a specific, high-value point: efficient activity may have been switched off and budget shifted to pricier conversions. Be humble and consultative - the data can't show why it was paused, so frame it as "worth reviewing whether lead quality (not cost) drove the pause" and recommend reviewing the data before reactivating, NOT blindly turning campaigns back on.
- The WHY_IT_MATTERS bullets must explain what the wrong or under-powered bidding strategy is costing them in missed conversions or revenue  -  be specific about the learning state problem if conversion volume is low.
""".strip()

    prompt += "\n\n" + example_block(EXAMPLES["bidding_strategy"])
    raw = _call_openai(client, SYSTEM_PROMPT, prompt)
    return _parse_response(raw)


# ── Issue-led: narrate ONE discrete issue (title + 3-part structure) ──────────

CATEGORY_RULES = {
    "Conversion Tracking": (
        "- If the primary conversion is imported from GA4, say native Google Ads tags give the "
        "cleanest bidding signal and recommend confirming Enhanced Conversions is active. NEVER say "
        "GA4 'blocks' or 'prevents' Enhanced Conversions.\n"
        "- If a low-value action (e.g. a page-view action) is a primary conversion, NAME it plainly and "
        "explain it simply, as if to someone non-technical: Google records a 'success' when a visitor "
        "merely views a page, not when they actually enquire - so the algorithm chases page views, not "
        "leads. Add a soft hedge like '(worth confirming, in case it's been changed since the audit)'.\n"
        "- Conversion action names like 'generate_lead' are Google's system names - refer to them in "
        "plain English ('your lead action'). Never print the raw system name on a client slide.\n"
        "- The TITLE must name the TRACKING / measurement problem itself (e.g. 'Your lead action "
        "counts low-value activity', 'Conversions are imported, not tracked directly'). Do NOT frame "
        "the title as a bidding problem - bidding has its own separate slide.\n"
        "- If offline conversion imports (OCI) are missing, present it as a major OPPORTUNITY for a lead "
        "gen business (feeding which enquiries became real jobs/sales back via a CRM export), not a "
        "criticism - it's a forward-looking 'here's a big lever' point.\n"
        "- If last-click attribution actions are named with their 30-day counts, KEEP those exact names "
        "and counts (and which are call actions) - they let the auditor reference specific actions on "
        "the call. If a named action recorded 0 conversions, keep the note that it may be legacy."
    ),
    "Account Structure": (
        "- Lead with the real structure; treat Auto-Apply as a secondary point.\n"
        "- If budget is spread too thin, flag that no campaign gathers enough data to optimise.\n"
        "- The WHY_IT_MATTERS bullets must be about money and growth, not technical structure."
    ),
    "Targeting & Keywords": (
        "- Converting search terms not added as keywords, or high-traffic terms spending without "
        "converting, are high-value search-query-report findings - be specific with the numbers.\n"
        "- CRITICAL: when the finding names specific search terms, ad groups, lead counts, cost-per-lead "
        "or a percentage of spend, INCLUDE those exact details (in brackets where it reads naturally). "
        "Specifics like \"'fibreglass pool installation' (6 leads at ~£47 each)\" or \"the 'Pools "
        "Generic' ad group\" are exactly what make this land on a sales call - never strip them out.\n"
        "- If the finding contrasts time windows (e.g. 'last 90 days' vs 'last 30 days'), KEEP both "
        "window labels explicit - that 30-vs-90-day contrast is the insight, and shows the depth of "
        "analysis. Never merge them into one vague timeframe.\n"
        "- For weak responsive search ad strength: Ad Strength is NOT an auction or Ad Rank factor - "
        "do NOT say it 'reduces impression share' or 'raises CPCs'. The lever is distinct, relevant "
        "headlines and descriptions improving CTR and Quality Score.\n"
        "- For weak ad strength, the recommendation must go beyond 'rewrite the ads': audit which "
        "existing headlines and descriptions are already pulling their weight, feed those winners into "
        "fresh responsive search ads, and A/B test new variants - framed as small, compounding marginal "
        "gains over 3-6 months, not a one-off fix. Reference the example ad group(s) named.\n"
        "- Few negative keywords or heavy broad match wastes budget on irrelevant searches."
    ),
    "Bidding Strategy": (
        "- Lead gen: the correct strategy is Maximise Conversions (then Target CPA once there's data). "
        "eCommerce: Maximise Conversion Value (then Target ROAS). Never recommend Manual CPC.\n"
        "- Maximise Clicks: frame as 'optimising for website visits, not customers'; recommend "
        "Maximise Conversions.\n"
        "- There is no hard '30-50 conversions' minimum for smart bidding.\n"
        "- Paused campaigns that historically converted below the current CPA: frame as efficient "
        "activity possibly switched off; recommend reviewing lead quality before reactivating, not "
        "blindly turning campaigns back on."
    ),
}

CATEGORY_RULES["Budget & Coverage"] = (
    "- Keep the specific campaign names and the exact percentages from the finding (e.g. \"'Kent "
    "Pool builder' loses 19% to budget\").\n"
    "- Budget-capped (lost to budget): frame as opportunity - the account already WINS these searches "
    "but runs out of money; raising budget or reallocating captures more leads. Only push budget where "
    "it converts well.\n"
    "- 'Presence or interest' location: explain plainly that ads show to people merely interested in the "
    "area (not actually there), and that switching to 'Presence' is a high-ROI, low-effort fix.\n"
    "- The WHY_IT_MATTERS bullets must be about leads and wasted money, not technical settings."
)
CATEGORY_RULES["Ad Rank & Quality"] = (
    "- Keep the specific campaign names / keywords and percentages.\n"
    "- Lost-to-rank and low Quality Score are quality problems, NOT budget ones - the fix is ad "
    "relevance, keyword-to-ad tightness, and landing page experience, not spending more.\n"
    "- Be concrete about the lever (e.g. 'most of these keywords have below-average ad relevance')."
)
CATEGORY_RULES["Ads & Assets"] = (
    "- Name the exact missing extension types from the finding (e.g. call extensions, image extensions).\n"
    "- Frame as free upside: extensions make ads bigger and more clickable and feed Ad Rank at no extra "
    "cost per click. For a phone-driven or visual business, call and image assets matter most.\n"
    "- Recommend adding the missing types across campaigns and keeping at least 4 sitelinks, 4 callouts, "
    "2 structured snippets."
)

_CATEGORY_EXAMPLE_KEY = {
    "Conversion Tracking": "conversion_tracking",
    "Account Structure":   "account_structure",
    "Targeting & Keywords": "targeting_keywords",
    "Bidding Strategy":    "bidding_strategy",
    "Budget & Coverage":   "account_structure",
    "Ad Rank & Quality":   "targeting_keywords",
    "Ads & Assets":        "account_structure",
}


def _enforce_entity_labels(detail: str, text: str) -> str:
    """Deterministic override: GPT sometimes squishes "the 'Pools Generic' ad group" down to
    just "'Pools Generic'", losing the context of what it is. This re-attaches the label
    ('ad group' / 'campaign') to any quoted entity that the source finding labelled that way,
    so the deck always says what it's referring to. Never double-labels."""
    if not text or not detail:
        return text
    label_map = {}
    for label in ("ad group", "campaign"):
        lbl = label.replace(" ", r"\s+")
        for m in re.finditer(rf"'([^']+)'\s+{lbl}", detail):       # 'name' ad group
            label_map.setdefault(m.group(1), label)
        for m in re.finditer(rf"{lbl}\s+'([^']+)'", detail):       # ad group 'name'
            label_map.setdefault(m.group(1), label)
    for name, label in label_map.items():
        n = re.escape(name)
        # add the label after an unlabelled quoted mention (not already followed/preceded by a label)
        text = re.sub(
            rf"(?<!ad group )(?<!campaign )'{n}'(?!\s+(?:ad group|campaign))",
            f"'{name}' {label}", text,
        )
    return text


def _narrative_issue(client: OpenAI, issue: dict, account_type: str = "unknown") -> dict:
    """Generate slide copy for ONE discrete issue: a specific title + the 3-part structure."""
    cat = issue.get("category", "")
    rules = CATEGORY_RULES.get(cat, "")
    ex_key = _CATEGORY_EXAMPLE_KEY.get(cat)

    prompt = f"""
Write slide copy for ONE issue in a Google Ads audit. Focus ONLY on this single finding.

Finding: {issue.get('detail', '')}
RAG status: {issue.get('rag', 'amber')}
Topic area: {cat}
Account type: {account_type}

Key rules:
{rules}

Produce a TITLE: a specific, punchy headline that NAMES the actual problem the way a senior auditor
would - not the topic area. Under 9 words. E.g. "Maximise Clicks is buying traffic, not leads" or
"Weak ad strength is limiting your reach".

Respond in EXACTLY this format (no extra text, no markdown):
TITLE: <title>
WHATS_HAPPENING_1: <first bullet  -  what is happening>
WHATS_HAPPENING_2: <second bullet  -  what is happening>
WHY_IT_MATTERS_1: <first bullet  -  commercial impact>
WHY_IT_MATTERS_2: <second bullet  -  commercial impact>
REC1: <first recommendation>
REC2: <second recommendation>
REC3: <third recommendation>
""".strip()

    if ex_key:
        prompt += "\n\n" + example_block(EXAMPLES[ex_key])

    raw = _call_openai(client, SYSTEM_PROMPT, prompt)
    parsed = _parse_response(raw)

    title = ""
    for line in raw.splitlines():
        if line.strip().upper().startswith("TITLE:"):
            title = line.split(":", 1)[1].strip()
            break
    # Clean: drop surrounding quotes and any stray "Issue #n:" / "Title:" prefix.
    title = (title or cat).strip().strip('"\'').strip()
    title = re.sub(r'^(issue\s*#?\s*\d*\s*[:.\-]\s*|title\s*[:.\-]\s*)', '', title, flags=re.I).strip()
    parsed["title"] = title or cat

    # Deterministic label override  -  ensure ad groups / campaigns are named as such.
    detail = issue.get("detail", "")
    parsed["title"] = _enforce_entity_labels(detail, parsed["title"])
    parsed["whats_happening"] = _enforce_entity_labels(detail, parsed.get("whats_happening", ""))
    parsed["why_it_matters"] = _enforce_entity_labels(detail, parsed.get("why_it_matters", ""))
    parsed["recommendations"] = [
        _enforce_entity_labels(detail, r) for r in parsed.get("recommendations", [])
    ]

    parsed["rag"] = issue.get("rag", "amber")
    parsed["category"] = cat
    return parsed


def _narrative_executive_summary(client: OpenAI, findings: dict, issues: list,
                                 escalation_note: str = "") -> dict:
    """Generates the Executive Summary slide content.

    escalation_note: when the overall rating was escalated to RED because efficiency
    collapsed (CPA roughly doubled+ vs the 12-month average), this carries a directive
    + the real figures so the Commercial Impact explicitly states the "parts are amber,
    whole is red" reasoning the client should hear out loud. Empty otherwise.
    """

    issues_detail = "\n".join(
        f"- {i.get('title','Issue')} ({i.get('rag','AMBER').upper()}):\n"
        f"  What's happening: {i.get('whats_happening','').replace(chr(10), ' ')}\n"
        f"  Why it matters: {i.get('why_it_matters','').replace(chr(10), ' ')}"
        for i in issues
    )
    strengths = findings.get("strengths") or []
    strengths_note = ("\n\nThings the account already does WELL (verified): "
                      + "; ".join(strengths) + ".") if strengths else ""

    prompt = f"""
Write the Executive Summary slide for a Google Ads audit presentation.

Here are the full findings from the audit:
{issues_detail}{strengths_note}

Rules:
- The headline must be a single punchy sentence  -  maximum 10 words. Name the actual problem, not the section. E.g. "Blind bidding and wasted spend are limiting growth" not "Account tracking and structure need improvement".
- The 3 bullets must each reference a specific finding from above  -  use real details (numbers, named issues, specific tools like Enhanced Conversions or Auto-Apply). No generic statements.
- COMMERCIAL_IMPACT: 1 - 2 sentences on what this is costing the business right now if nothing changes. Be specific about the mechanisms (e.g. wasted spend on broad match, bidding in learning state, missed conversions). TENSE: for things that are demonstrably happening NOW per the findings (e.g. budget spent on named non-converting terms), use direct present tense - "budget is leaking into low-quality clicks", NOT "can leak" or "could leak". Reserve "risks"/"could" only for FUTURE projections of what happens if nothing changes. Say "genuine lead demand" rather than just "demand".
- If "Things the account already does WELL" are provided, OPEN the COMMERCIAL_IMPACT with a brief, genuine one-clause acknowledgement of 1-2 of them (e.g. "The fundamentals are sound - X and Y are well set up - but..."), then pivot. Where it is true from the findings, be explicit and direct that these good foundations are being HELD BACK or STRANGLED by the issues - e.g. solid groundwork is being throttled while budget is capped on winning campaigns and leaks into non-converting searches. Honest and pointed beats vague. Keep the strengths to one short clause; the focus stays on the opportunities being missed.
- FACTUAL ACCURACY: never say GA4 imports "block" or "prevent" Enhanced Conversions (GA4 has its own ECs  -  say "worth confirming Enhanced Conversions is active"); never state a hard "30-50 conversions" minimum for smart bidding.
- TERMINOLOGY: when referring to importing real lead outcomes (booked jobs / sales) back into Google Ads, name it "offline conversion import (OCI)" - not vague wording like "sales outcomes are not imported".
- Use British English spelling.{escalation_note}

Respond in EXACTLY this format (no extra text, no markdown):
EXEC_HEADLINE: <punchy specific headline  -  under 15 words>
BULLET_1: <specific key finding 1 with real details>
BULLET_2: <specific key finding 2 with real details>
BULLET_3: <specific key finding 3 with real details>
COMMERCIAL_IMPACT: <specific commercial impact  -  1 - 2 sentences>
""".strip()

    prompt += "\n\n" + EXEC_SUMMARY_EXAMPLE
    exec_system_prompt = (
        "You are a senior Google Ads auditor writing copy for a client-facing audit presentation. "
        "Your tone is professional, direct, and consultative  -  not salesy. "
        "Always use British English spelling. "
        "Respond ONLY in the exact key: value format requested. No extra text, no markdown."
    )
    raw = _call_openai(client, exec_system_prompt, prompt)

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

    issues_detail = "\n".join(
        f"- {i.get('title','Issue')} ({i.get('rag','AMBER')}):\n"
        f"  What's happening: {i.get('whats_happening','').replace(chr(10), ' ')}\n"
        f"  Why it matters: {i.get('why_it_matters','').replace(chr(10), ' ')}"
        for i in issues
    )

    prompt = f"""
Write the Key Opportunities section for a Google Ads audit presentation.

Here are the full details of what was found in this account:
{issues_detail}

Write exactly 5 key opportunities  -  what could this client unlock if they fix these issues?
Rules:
- Each opportunity must be directly tied to a specific finding above  -  reference real details (e.g. "12 conversion actions", "88% broad match", "9 conversions/month").
- Start each with a strong verb (e.g. "Recover", "Unlock", "Cut", "Scale", "Gain", "Eliminate").
- Focus on tangible business outcomes: more leads, lower CPA, recovered wasted spend, scalable growth.
- IMPORTANT: do NOT just restate the issue slides word-for-word. REMIX them into fresh, forward-looking value statements  -  the same substance framed as the upside/prize, so the closing slide doesn't feel repetitive after the audit.
- Be punchy and specific  -  these are punchy value statements, not generic descriptions. Under 18 words each.
- TERMINOLOGY: if referencing importing real lead outcomes back into Google Ads, call it "offline conversion import (OCI)", not "sales outcomes".
- Use British English spelling.

Respond with just the 5 opportunities, one per line, no bullet points or numbering.
""".strip()

    prompt += "\n\n" + OPPORTUNITIES_EXAMPLE
    raw = _call_openai(client, "You are a concise copywriter. Follow the user's instructions exactly.", prompt)
    # Strip any label prefixes GPT adds (e.g. "OPP1:", "HEADLINE:", "REC1:" etc.)
    import re
    lines = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        # Remove common label prefixes
        line = re.sub(r'^(OPP\d+|HEADLINE|WHATS_HAPPENING_\d+|WHY_IT_MATTERS_\d+|REC\d+)\s*:\s*', '', line, flags=re.IGNORECASE)
        if line:
            lines.append(line)
    # Return only the last 5 lines (the opportunities themselves)
    return "\n".join(lines[-5:])


def _narrative_additional_observations(client: OpenAI, below_cut: list) -> list:
    """Compress the genuine findings that ranked BELOW the 6-slide cut into a short
    list of crisp, client-facing one-line observations for the 'Additional
    Observations' slide. These are real but secondary points (Quality Score, brand
    leakage, RSA strength, etc.) that would otherwise only live in the internal email.
    Returns a list of strings (house-styled via _call_openai). Falls back to a plain
    first-sentence extraction if the model returns nothing usable.
    """
    if not below_cut:
        return []
    items = below_cut[:6]   # one slide stays scannable
    findings_text = "\n".join(
        f"- [{i.get('category', '')}] {i.get('detail', '')}" for i in items
    )
    prompt = f"""
Below are genuine SECONDARY findings from a Google Ads audit that did not make the main
issue slides (those already cover the biggest problems). Summarise each as ONE crisp,
client-facing observation for an "Additional Observations" slide.

Findings:
{findings_text}

Rules:
- One line per finding, in the SAME order. Maximum {len(items)} lines.
- Plain English for a non-technical business owner. Under 22 words each.
- Lead with the concrete fact (a number, percentage or specific item) where there is one.
- Neutral, observational tone - these are smaller notes worth flagging, not alarms. No hard sell.
- British English spelling. Whole pounds, no pence.
- Do NOT restate the main issue slides; keep each line tied to its finding above.

Respond with just the observations, one per line, with no bullets or numbering.
""".strip()
    raw = _call_openai(client, "You are a concise, precise Google Ads auditor.", prompt)
    lines = []
    for line in raw.splitlines():
        # Strip only genuine list markers ("- ", "• ", "1. ", "2) ") - NOT leading
        # numbers that are part of the content (e.g. "55 of 114 ads...").
        line = re.sub(r'^\s*(?:[-•*]\s+|\d{1,2}[.)]\s+)', '', line).strip()
        if line:
            lines.append(line)
    if not lines:
        # Fallback: first sentence of each finding so the slide is never empty by accident.
        lines = [str(i.get('detail', '')).split('. ')[0].strip() for i in items]
        lines = [l for l in lines if l]
    return lines[:len(items)]


def _narrative_takeaways(client: OpenAI, findings: dict, issues: list) -> list:
    """Generates the 5-row Key Takeaways table content (matches Max's 5-row format)."""

    issues_detail = "\n".join(
        f"- {i.get('title','Issue')} ({i.get('rag','AMBER')}):\n"
        f"  What's happening: {i.get('whats_happening','').replace(chr(10), ' ')}\n"
        f"  Why it matters: {i.get('why_it_matters','').replace(chr(10), ' ')}\n"
        f"  Recommendations: {'; '.join(i.get('recommendations', []))}"
        for i in issues
    )

    prompt = f"""
Write the Key Takeaways table for a Google Ads audit presentation.
The table has 5 rows. Each row has 3 columns: Current State, Changes Needed, Future State.

Pick the 5 most impactful issues from the full audit detail below and write one row for each.
(If there are fewer than 5 distinct issues, cover the strongest ones and leave the remaining rows blank.)
Each row must be grounded in specific details from the findings  -  no generic copy.

Full audit findings:
{issues_detail}

Rules:
- Current State: describe the specific problem in plain English. Reference real details (numbers, percentages, specific issues). Under 20 words.
- Changes Needed: one concrete action starting with a verb. Under 15 words.
- Future State: the specific business benefit that unlocks. Under 15 words.
- TERMINOLOGY: if a row is about importing real lead outcomes back into Google Ads, call it "offline conversion import (OCI)", not "sales outcomes are not imported".
- Use British English spelling.
- Do NOT just repeat the section name  -  describe the actual situation.

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
TK4_CURRENT: <current state>
TK4_CHANGES: <changes needed>
TK4_FUTURE: <future state>
TK5_CURRENT: <current state>
TK5_CHANGES: <changes needed>
TK5_FUTURE: <future state>
""".strip()

    prompt += "\n\n" + TAKEAWAYS_EXAMPLE
    takeaways_system_prompt = (
        "You are a senior Google Ads auditor writing copy for a client-facing audit presentation. "
        "Always use British English spelling. "
        "Respond ONLY in the exact key: value format requested. No extra text, no markdown."
    )
    raw = _call_openai(client, takeaways_system_prompt, prompt)

    lines = {}
    for line in raw.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            lines[key.strip().upper()] = val.strip()

    return [
        {
            "current_state":  lines.get(f"TK{n}_CURRENT", ""),
            "changes_needed": lines.get(f"TK{n}_CHANGES", ""),
            "future_state":   lines.get(f"TK{n}_FUTURE", ""),
        }
        for n in range(1, 6)
    ]


# ── Main entry point ──────────────────────────────────────────────────────────

def _narrative_objectives(client: OpenAI, raw_questionnaire: str) -> dict:
    """
    Takes raw pasted questionnaire text and returns clean slide-3 copy:
      objectives_text   -  what they want to achieve
      success_metric    -  how they measure success
      pain_points_text  -  what's frustrating them right now
    """
    prompt = f"""
A PPC agency has received the following client questionnaire. Extract the key information and rewrite it as clean, client-facing copy for a slide in a Google Ads audit presentation.

RAW QUESTIONNAIRE:
{raw_questionnaire}

Rules:
- objectives_text: List their main objectives as a short, readable sentence or comma-separated list. Start with "To ". E.g. "To increase lead volume, improve lead quality, and reduce cost per acquisition."
- success_metric: One punchy sentence on what success looks like to them. E.g. "3 good appointments a day at a sustainable CPA."
- pain_points_text: Summarise their challenges in 1 - 2 short sentences. Be empathetic but factual. E.g. "Campaign performance has dropped recently and the team lacks the time and expertise to diagnose why."
- website_url: Extract the client's website URL exactly as written. Include the full URL with protocol if present. If not found, leave blank.
- Use British English spelling.
- Keep it concise  -  this is slide copy, not a report.

Respond in EXACTLY this format (no extra text, no markdown):
OBJECTIVES: <objectives text>
SUCCESS_METRIC: <success metric text>
PAIN_POINTS: <pain points text>
WEBSITE_URL: <full website URL or blank>
""".strip()

    system = (
        "You are a senior PPC strategist extracting key facts from a client questionnaire "
        "and rewriting them as polished, client-facing slide copy. "
        "Always use British English. Respond only in the exact format requested."
    )
    raw = _call_openai(client, system, prompt)

    lines = {}
    for line in raw.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            lines[key.strip().upper()] = val.strip()

    return {
        "objectives_text":  lines.get("OBJECTIVES", ""),
        "success_metric":   lines.get("SUCCESS_METRIC", ""),
        "pain_points_text": lines.get("PAIN_POINTS", ""),
        "website_url":      lines.get("WEBSITE_URL", ""),
    }


def _narrative_perf_commentary(client: OpenAI, perf: dict, raw_questionnaire: str = "") -> str:
    """
    Write 2 - 3 sentences interpreting the 30-day vs 12-month performance numbers.
    Flags whether trend is positive, negative, or mixed.
    """
    raw = perf.get("_raw", {})
    t30 = raw.get("t30", {})
    t12 = raw.get("t12", {})

    def _share_line(t):
        parts = []
        for label, key in (("abs-top", "abs_top"), ("top-of-page", "top")):
            v = t.get(key)
            if v is not None:
                parts.append(f"{label} {v}%")
        return (" | " + " | ".join(parts)) if parts else ""

    context = f"""
Last 30 days:  Spend {perf.get('spend_30d','?')} | Clicks {perf.get('clicks_30d','?')} | Conversions {perf.get('convs_30d','?')} | CPA {perf.get('cpa_30d','?')} | SIS {perf.get('sis_30d','?')}{_share_line(t30)}
Last 12 months: Spend {perf.get('spend_12m','?')} | Clicks {perf.get('clicks_12m','?')} | Conversions {perf.get('convs_12m','?')} | CPA {perf.get('cpa_12m','?')} | SIS {perf.get('sis_12m','?')}{_share_line(t12)}
""".strip()

    client_context = f"\nClient context (from questionnaire):\n{raw_questionnaire[:500]}" if raw_questionnaire.strip() else ""

    prompt = f"""
You are writing 2 - 3 sentences for a Google Ads audit slide called "Performance Summary".
The slide shows last 30 days vs last 12 months metrics side by side.
Write a plain-English interpretation: is performance trending up, down, or mixed? What does it mean for the business?
Be specific  -  reference the actual numbers. Flag anything that looks concerning (rising CPA, falling conversions, low SIS).
If absolute-top or top-of-page impression share is provided and has fallen versus 12 months, note that the account may be losing visibility on its best, most relevant searches even while cheaper, lower-intent traffic grows.
Use British English. Be direct, not alarmist.
{client_context}

Performance data:
{context}

Write only the 2 - 3 sentence commentary. No labels, no bullet points.
""".strip()

    system = (
        "You are a senior Google Ads analyst writing plain-English commentary on account performance trends. "
        "Be specific, use the actual numbers, and keep it to 2 - 3 sentences."
    )
    return _call_openai(client, system, prompt).strip()


def _retry(fn, label: str, max_attempts: int = 3):
    """Call fn() up to max_attempts times, returning the first non-empty result."""
    for attempt in range(1, max_attempts + 1):
        result = fn()
        # Determine if result has real content
        if isinstance(result, dict):
            has_content = any(v for v in result.values() if isinstance(v, str) and v.strip())
        elif isinstance(result, list):
            # Rows may be dicts (e.g. takeaways) or plain strings (e.g. observations).
            def _row_has(row):
                if isinstance(row, dict):
                    return any(v for v in row.values() if isinstance(v, str) and v.strip())
                return bool(str(row).strip())
            has_content = any(_row_has(row) for row in result)
        else:
            has_content = bool(str(result).strip())

        if has_content:
            return result
        print(f"  ⚠ {label}: empty result on attempt {attempt}, retrying...")
    print(f"  ✗ {label}: still empty after {max_attempts} attempts  -  check GPT output format.")
    return result


def generate_narrative(findings: dict, openai_api_key: str, client_name: str = "", raw_questionnaire: str = "") -> dict:
    """
    Args:
        findings:       the dict returned by analyse_account.py's run_analysis()
        openai_api_key: your OpenAI API key
        client_name:    the client's business name (used on the slides)

    Returns:
        Full narrative dict ready to be saved as narrative_output.json
    """
    global _total_tokens
    _total_tokens = 0  # reset for this run
    client = OpenAI(api_key=openai_api_key)

    from analyse_account import select_top_issues, overall_rag_from_issues

    account_type = findings.get("account_type", "unknown")

    # ── ISSUE-LED: pick the top problems and narrate each one individually ────
    selected = select_top_issues(findings, max_issues=6)
    if not selected:
        # Genuinely clean account  -  fall back so the deck still has a slide.
        selected = [{
            "detail": "No material issues were detected  -  the account is in good health.",
            "category": "Account Structure", "rag": "green", "severity": 1.0,
        }]

    issues = []
    for n, iss in enumerate(selected, 1):
        print(f"  → Generating issue {n}/{len(selected)}: {iss['category']} (sev {iss['severity']:.0f})...")
        narrated = _retry(lambda i=iss: _narrative_issue(client, i, account_type),
                          f"Issue {n}")
        narrated["rag"] = iss["rag"]            # trust the analyser's RAG, not the model
        narrated["category"] = iss["category"]
        issues.append(narrated)

    # ── Additional observations: genuine findings that ranked BELOW the 6-slide cut ──
    # They were only living in the internal email; surface them on their own slide so
    # the client sees the full picture (the slide is auto-deleted when there are none).
    _all_ranked = select_top_issues(findings, max_issues=50, apply_floor=False)
    _selected_details = {i.get("detail") for i in selected}
    _below_cut = [i for i in _all_ranked if i.get("detail") not in _selected_details]
    if _below_cut:
        print(f"  → Summarising {min(len(_below_cut), 6)} additional observation(s)...")
        additional_observations = _retry(
            lambda: _narrative_additional_observations(client, _below_cut),
            "Additional Observations"
        )
    else:
        additional_observations = []

    overall_rag = overall_rag_from_issues(selected)

    # Holistic severity escalation: individual issues can each read "amber", but if the
    # account's efficiency has collapsed (30-day CPA has at least doubled vs the 12-month
    # average) AND there are several issues, the WHOLE is worse than the parts. A human
    # auditor calls that Red. Don't let a struggling account look merely amber.
    _perf_raw = (findings.get("performance_summary", {}) or {}).get("_raw", {})
    _cpa30 = (_perf_raw.get("t30", {}) or {}).get("cpa")
    _cpa12 = (_perf_raw.get("t12", {}) or {}).get("cpa")
    _escalation_note = ""
    if (_cpa30 and _cpa12 and _cpa30 >= 2 * _cpa12 and len(selected) >= 4
            and overall_rag in ("amber", "amber_red")):
        overall_rag = "red"
        print(f"  ↑ Overall escalated to RED (30d CPA £{_cpa30:.0f} ≥ 2× 12m CPA £{_cpa12:.0f}, {len(selected)} issues)")
        # Make the amber-to-red reasoning explicit on the slide so it can be read verbatim.
        _mult = _cpa30 / _cpa12
        _rise = ("nearly tripled" if _mult >= 2.6 else
                 "more than doubled" if _mult > 2.0 else "doubled")
        _escalation_note = (
            "\n- OVERALL RATING IS RED (escalated): each issue above is individually amber and "
            "fixable, but the account's efficiency has collapsed - cost per lead has "
            f"{_rise}, from about £{_cpa12:.0f} (12-month average) to about £{_cpa30:.0f} (last 30 "
            "days). END the COMMERCIAL_IMPACT with one clear, standalone sentence making this "
            f"explicit using those figures: that the individual issues are amber and fixable, but "
            f"because cost per lead has {_rise} from £{_cpa12:.0f} to £{_cpa30:.0f} the account as a "
            "whole sits in the red. Close on a crisp line such as 'The parts are amber; the whole is "
            "red.' Do not exaggerate beyond the figures given."
        )

    print("  → Generating Executive Summary...")
    exec_sum = _retry(
        lambda: _narrative_executive_summary(client, findings, issues, _escalation_note),
        "Executive Summary"
    )

    print("  → Generating Key Opportunities...")
    opps = _retry(lambda: _narrative_key_opportunities(client, findings, issues), "Key Opportunities")

    print("  → Generating Key Takeaways...")
    takeaways = _retry(lambda: _narrative_takeaways(client, findings, issues), "Key Takeaways")

    # ── Slide 3: Objectives  -  from raw questionnaire or left blank ───────────
    if raw_questionnaire.strip():
        print("  → Extracting client objectives from questionnaire...")
        objectives = _retry(
            lambda: _narrative_objectives(client, raw_questionnaire),
            "Objectives"
        )
    else:
        objectives = {"objectives_text": "", "success_metric": "", "pain_points_text": ""}

    # ── Performance summary commentary ──────────────────────────────────────────
    perf = findings.get("performance_summary", {})
    if perf:
        print("  → Writing performance commentary...")
        perf_commentary = _retry(
            lambda: _narrative_perf_commentary(client, perf, raw_questionnaire),
            "Performance Commentary"
        )
    else:
        perf_commentary = ""

    # Holistic section RAGs (incl. healthy ones)  -  drives the dial image, which must
    # reflect OVERALL account health, not just the problems shown on the issue slides.
    section_rags = [
        findings.get("conversion_tracking", {}).get("rag", "amber"),
        findings.get("account_structure", {}).get("rag", "amber"),
        findings.get("targeting_keywords", {}).get("rag", "amber"),
        findings.get("bidding_strategy", {}).get("rag", "amber"),
    ]

    return {
        "client_name":       client_name,
        "account_cid":       findings.get("account_cid", ""),
        "overall_rag":       overall_rag,
        "section_rags":      section_rags,
        "_tokens_used":      _total_tokens,
        "issues":            issues,
        "additional_observations": additional_observations,
        "executive_summary": exec_sum,
        "objectives":        objectives,
        "key_opportunities": opps,
        "takeaways":         takeaways,
        "performance_summary": perf,
        "perf_commentary":   perf_commentary,
        "website_url":       objectives.get("website_url", ""),
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
