"""
populate_slides.py
------------------
Reads narrative_output.json and populates the PPC Team Google Slides
template with the generated audit content.
"""

import os
import json
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# ── CONFIGURATION ─────────────────────────────────────────────────────────────

TEMPLATE_PRESENTATION_ID = "14fI3Vh_W06-ZoBo1UfTf64aOxoZD1Mp_TcqtT_JAJFE"

CREDENTIALS_FILE = os.path.expanduser("~/Desktop/ppc-audit-tool/credentials.json")
TOKEN_FILE        = os.path.expanduser("~/Desktop/ppc-audit-tool/token.json")
NARRATIVE_FILE    = os.path.expanduser("~/Desktop/ppc-audit-tool/narrative_output.json")

SCOPES = [
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/drive",
]

# ── AUTH ──────────────────────────────────────────────────────────────────────

def get_credentials():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as token:
            token.write(creds.to_json())
    return creds

# ── HELPERS ───────────────────────────────────────────────────────────────────

def replace(old, new):
    return {
        "replaceAllText": {
            "containsText": {"text": old, "matchCase": True},
            "replaceText": str(new) if new else "",
        }
    }

RAG_DOT = {
    "RED":   "🔴",
    "AMBER": "🟠",
    "GREEN": "🟢",
}

SECTION_NAMES = [
    "Conversion Tracking",
    "Account Structure",
    "Targeting & Keywords",
    "Bidding Strategy",
]

def bullets(items):
    if isinstance(items, list):
        return "\n".join(f"• {item}" for item in items)
    return str(items)

def rag_dot(rag_str):
    return RAG_DOT.get(str(rag_str).upper(), "🟠")

def to_bullets(text):
    """Convert a plain multi-line string to bullet-prefixed lines."""
    if isinstance(text, list):
        return "\n".join(f"• {item}" for item in text if item.strip())
    lines = [l.strip() for l in str(text).splitlines() if l.strip()]
    return "\n".join(f"• {l}" for l in lines)

# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    if not os.path.exists(NARRATIVE_FILE):
        print(f"\nERROR: Could not find {NARRATIVE_FILE}")
        print("Please run generate_narrative.py first.\n")
        return

    with open(NARRATIVE_FILE, "r") as f:
        data = json.load(f)

    client_name   = data.get("client_name", "Your Business")
    account_cid   = data.get("account_cid", "")
    issues        = data.get("issues", [])
    exec_sum      = data.get("executive_summary", {})
    objectives    = data.get("objectives", {})
    takeaways     = data.get("takeaways", [])
    opportunities = data.get("key_opportunities", "")

    print(f"Loaded narrative for: {client_name} (CID: {account_cid})")
    print(f"Issues found: {len(issues)}")

    print("\nAuthenticating with Google...")
    creds = get_credentials()
    slides_service = build("slides", "v1", credentials=creds)
    drive_service  = build("drive",  "v3", credentials=creds)

    deck_title = f"PPC Audit — {client_name}"
    print(f"Creating copy of template: '{deck_title}'...")
    copied = drive_service.files().copy(
        fileId=TEMPLATE_PRESENTATION_ID,
        body={"name": deck_title},
    ).execute()
    new_id = copied["id"]
    print(f"New deck created (ID: {new_id})")

    requests = []

    # ── Client name ──
    requests.append(replace("{{CLIENT_NAME}}", client_name))

    # ── Objectives slide ──
    requests.append(replace("{{OBJECTIVES_TEXT}}",  objectives.get("objectives_text", "")))
    requests.append(replace("{{SUCCESS_METRIC}}",   objectives.get("success_metric", "")))
    requests.append(replace("{{PAIN_POINTS_TEXT}}", objectives.get("pain_points_text", "")))

    # ── Executive Summary slide ──
    exec_headline = exec_sum.get("headline", "")
    overall_rag   = data.get("overall_rag", "RED")
    dot           = rag_dot(overall_rag)
    rag_label     = overall_rag.capitalize()
    exec_headline_formatted = f"Account Performance: {exec_headline} ({dot} {rag_label})"

    requests.append(replace("{{EXEC_HEADLINE}}",     exec_headline_formatted))
    requests.append(replace("{{EXEC_BULLET_1}}",     exec_sum.get("bullet_1", "")))
    requests.append(replace("{{EXEC_BULLET_2}}",     exec_sum.get("bullet_2", "")))
    requests.append(replace("{{EXEC_BULLET_3}}",     exec_sum.get("bullet_3", "")))
    requests.append(replace("{{COMMERCIAL_IMPACT}}", exec_sum.get("commercial_impact", "")))

    # ── Issue slides (up to 4) ──
    for i in range(1, 5):
        issue        = issues[i - 1] if i <= len(issues) else {}
        n            = str(i)
        section_name = SECTION_NAMES[i - 1]
        issue_rag    = issue.get("rag", "AMBER")
        dot          = rag_dot(issue_rag)

        requests.append(replace(f"{{{{ISSUE_{n}_TITLE}}}}",          section_name))
        requests.append(replace(f"{{{{ISSUE_{n}_RAG}}}}",            dot))
        requests.append(replace(f"{{{{ISSUE_{n}_HAPPENING}}}}",      issue.get("whats_happening", "")))
        requests.append(replace(f"{{{{ISSUE_{n}_MATTERS}}}}",        issue.get("why_it_matters", "")))
        requests.append(replace(f"{{{{ISSUE_{n}_RECOMMENDATION}}}}", bullets(issue.get("recommendations", []))))

    # ── Key Opportunities slide ──
    requests.append(replace("{{KEY_OPPORTUNITIES}}", to_bullets(opportunities)))

    # ── Key Takeaways slide (3 rows × 3 columns) ──
    for i in range(1, 4):
        tk = takeaways[i - 1] if i <= len(takeaways) else {}
        n  = str(i)
        requests.append(replace(f"{{{{TK_{n}_CURRENT}}}}", tk.get("current_state", "")))
        requests.append(replace(f"{{{{TK_{n}_CHANGES}}}}", tk.get("changes_needed", "")))
        requests.append(replace(f"{{{{TK_{n}_FUTURE}}}}",  tk.get("future_state", "")))

    print("Populating slides...")
    result = slides_service.presentations().batchUpdate(
        presentationId=new_id,
        body={"requests": requests},
    ).execute()

    replaced = sum(
        r.get("replaceAllTextResponse", {}).get("occurrencesChanged", 0)
        for r in result.get("replies", [])
    )
    print(f"Done. {replaced} placeholder(s) replaced.")

    url = f"https://docs.google.com/presentation/d/{new_id}/edit"
    print(f"\n✅ Deck is ready:\n   {url}\n")
    return url


if __name__ == "__main__":
    main()
