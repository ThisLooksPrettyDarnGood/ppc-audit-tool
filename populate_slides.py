"""
populate_slides.py
------------------
Reads narrative_output.json and populates the PPC Team Google Slides
template with the generated audit content.
"""

import io
import os
import json
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from fetch_logo import fetch_logo_bytes

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
    "RED":       "🔴",
    "AMBER_RED": "🟠🔴",   # "on the cusp"  -  tracking exists but a serious, red-leaning issue
    "AMBER":     "🟠",
    "GREEN":     "🟢",
}

RAG_LABEL = {
    "RED":       "Red",
    "AMBER_RED": "Amber/Red",
    "AMBER":     "Amber",
    "GREEN":     "Green",
}

# ── Dial image config ─────────────────────────────────────────────────────────
DIAL_IMAGE_OBJECT_ID = "g3979d9de3ed_0_228"   # the speedometer on slide 4

_DIAL_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dial_config.json")
try:
    with open(_DIAL_CONFIG_PATH) as _f:
        _DIAL_CONFIG = json.load(_f)
except FileNotFoundError:
    _DIAL_CONFIG = {}

def pick_dial(overall_rag: str) -> str:
    """
    Drive the dial straight from the headline overall RAG so it always matches the exec
    summary the client reads. A struggling account must never show a green dial - the old
    'sum the sections' approach let one healthy section pull an amber account into the green.
    Returns a Google Drive URL or empty string if config missing.
    """
    key = {
        "red":       "dial_red",
        "amber_red": "dial_orange",
        "amber":     "dial_amber",
        "green":     "dial_dark_green",
    }.get(str(overall_rag).lower(), "dial_amber")
    return _DIAL_CONFIG.get(key, {}).get("url", "")

# Object ID of the logo placeholder image on slide 1 (white box, bottom-right)
LOGO_IMAGE_OBJECT_ID = "g3d59bf3fc16_0_9"


def _insert_logo(slides_service, drive_service, presentation_id: str,
                 img_bytes: bytes, content_type: str) -> bool:
    """
    Upload logo bytes to Drive, make public, then use replaceImage to swap
    the logo placeholder on slide 1. Cleans up the temp Drive file afterwards.
    """
    drive_file_id = None
    try:
        ext = "png" if "png" in content_type else ("ico" if "ico" in content_type else "jpg")
        media = MediaIoBaseUpload(io.BytesIO(img_bytes), mimetype=content_type, resumable=False)
        uploaded = drive_service.files().create(
            body={"name": f"_ppc_audit_logo_tmp.{ext}"},
            media_body=media,
            fields="id",
        ).execute()
        drive_file_id = uploaded["id"]

        drive_service.permissions().create(
            fileId=drive_file_id,
            body={"role": "reader", "type": "anyone"},
        ).execute()

        logo_url = f"https://drive.google.com/uc?id={drive_file_id}&export=download"
        print(f"  Logo uploaded to Drive (id={drive_file_id})")

        slides_service.presentations().batchUpdate(
            presentationId=presentation_id,
            body={"requests": [{
                "replaceImage": {
                    "imageObjectId":      LOGO_IMAGE_OBJECT_ID,
                    "imageReplaceMethod": "CENTER_INSIDE",
                    "url":                logo_url,
                },
            }]},
        ).execute()
        print("  Logo placeholder replaced.")
        return True

    except Exception as e:
        print(f"  ⚠ Logo insertion failed: {e}")
        return False

    finally:
        if drive_file_id:
            try:
                drive_service.files().delete(fileId=drive_file_id).execute()
            except Exception:
                pass


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

def _delete_unused_issue_slides(slides_service, presentation_id):
    """Issue-led decks fill a variable number of issue slides. Any issue slide left
    with unfilled {{ISSUE_n_*}} placeholders is one we didn't need  -  delete the whole
    slide so the client never sees a blank. Also trims the Additional Observations slide
    when there were no below-cut findings (its {{ADDITIONAL_OBSERVATIONS}} placeholder is
    left unfilled). Identifies slides purely by the leftover placeholder, so it never
    touches a populated slide.
    """
    import re as _re
    try:
        deck = slides_service.presentations().get(presentationId=presentation_id).execute()
    except Exception as e:
        print(f"  ⚠ Could not fetch deck to trim issue slides: {e}")
        return

    issue_ph = _re.compile(
        r"\{\{(?:ISSUE_\d+_(?:TITLE|RAG|HAPPENING|MATTERS|RECOMMENDATION)|ADDITIONAL_OBSERVATIONS)\}\}"
    )
    delete_requests = []
    for slide in deck.get("slides", []):
        slide_id = slide.get("objectId")
        slide_text = ""
        for el in slide.get("pageElements", []):
            for te in el.get("shape", {}).get("text", {}).get("textElements", []):
                slide_text += te.get("textRun", {}).get("content", "")
        if issue_ph.search(slide_text):
            delete_requests.append({"deleteObject": {"objectId": slide_id}})

    if delete_requests:
        try:
            slides_service.presentations().batchUpdate(
                presentationId=presentation_id,
                body={"requests": delete_requests},
            ).execute()
            print(f"  Trimmed {len(delete_requests)} unused issue slide(s).")
        except Exception as e:
            print(f"  ⚠ Could not delete unused issue slides: {e}")


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
    perf          = data.get("performance_summary", {})
    perf_commentary = data.get("perf_commentary", "")
    objectives    = data.get("objectives", {})
    takeaways     = data.get("takeaways", [])
    opportunities = data.get("key_opportunities", "")
    observations  = data.get("additional_observations", [])
    website_url   = data.get("website_url", "")

    print(f"Loaded narrative for: {client_name} (CID: {account_cid})")
    print(f"Issues found: {len(issues)}")

    print("\nAuthenticating with Google...")
    creds = get_credentials()
    slides_service = build("slides", "v1", credentials=creds)
    drive_service  = build("drive",  "v3", credentials=creds)

    deck_title = f"PPC Audit  -  {client_name}"
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
    rag_label     = RAG_LABEL.get(str(overall_rag).upper(), str(overall_rag).capitalize())
    exec_headline_formatted = f"Account Performance: {exec_headline} ({dot} {rag_label})"

    requests.append(replace("{{EXEC_HEADLINE}}",     exec_headline_formatted))
    requests.append(replace("{{EXEC_BULLET_1}}",     exec_sum.get("bullet_1", "")))
    requests.append(replace("{{EXEC_BULLET_2}}",     exec_sum.get("bullet_2", "")))
    requests.append(replace("{{EXEC_BULLET_3}}",     exec_sum.get("bullet_3", "")))
    requests.append(replace("{{COMMERCIAL_IMPACT}}", exec_sum.get("commercial_impact", "")))
    # Score-slide verdict (the "verdict in a breath" next to the dial). Falls back to the
    # commercial impact if the model omitted it, so the slide is never left with a raw token.
    requests.append(replace("{{SCORE_SUMMARY}}",
                            exec_sum.get("score_summary") or exec_sum.get("commercial_impact", "")))

    # ── Performance Summary slide ──
    requests.append(replace("{{PERF_SPEND_30D}}",  perf.get("spend_30d",  "N/A")))
    requests.append(replace("{{PERF_IMPR_30D}}",   perf.get("impr_30d",   "N/A")))
    requests.append(replace("{{PERF_CLICKS_30D}}", perf.get("clicks_30d", "N/A")))
    requests.append(replace("{{PERF_CONVS_30D}}",  perf.get("convs_30d",  "N/A")))
    requests.append(replace("{{PERF_CVR_30D}}",    perf.get("cvr_30d",    "N/A")))
    requests.append(replace("{{PERF_CPA_30D}}",    perf.get("cpa_30d",    "N/A")))
    requests.append(replace("{{PERF_SIS_30D}}",    perf.get("sis_30d",    "N/A")))
    requests.append(replace("{{PERF_SPEND_12M}}",  perf.get("spend_12m",  "N/A")))
    requests.append(replace("{{PERF_IMPR_12M}}",   perf.get("impr_12m",   "N/A")))
    requests.append(replace("{{PERF_CLICKS_12M}}", perf.get("clicks_12m", "N/A")))
    requests.append(replace("{{PERF_CONVS_12M}}",  perf.get("convs_12m",  "N/A")))
    requests.append(replace("{{PERF_CVR_12M}}",    perf.get("cvr_12m",    "N/A")))
    requests.append(replace("{{PERF_CPA_12M}}",    perf.get("cpa_12m",    "N/A")))
    requests.append(replace("{{PERF_SIS_12M}}",    perf.get("sis_12m",    "N/A")))
    requests.append(replace("{{PERF_COMMENTARY}}", perf_commentary))

    # ── Issue slides (ISSUE-LED: up to 6, ranked by severity) ──
    # The template carries 6 issue slides. We fill as many as we have issues, using
    # each issue's own title (not a fixed category). Any slide left unfilled is
    # deleted after population (see _delete_unused_issue_slides) so the client never
    # sees a blank  -  find 3 issues, get 3 slides; find 6, get 6.
    MAX_ISSUE_SLIDES = 6
    for i in range(1, MAX_ISSUE_SLIDES + 1):
        n = str(i)
        if i <= len(issues):
            issue     = issues[i - 1]
            # No leading number: issues are already ordered by importance, and an unnumbered
            # title means removing one issue (e.g. on Max's review) never forces a renumber.
            title     = issue.get('title') or 'Issue'
            dot       = rag_dot(issue.get("rag", "AMBER"))
            requests.append(replace(f"{{{{ISSUE_{n}_TITLE}}}}",          title))
            requests.append(replace(f"{{{{ISSUE_{n}_RAG}}}}",            dot))
            requests.append(replace(f"{{{{ISSUE_{n}_HAPPENING}}}}",      issue.get("whats_happening", "")))
            requests.append(replace(f"{{{{ISSUE_{n}_MATTERS}}}}",        issue.get("why_it_matters", "")))
            requests.append(replace(f"{{{{ISSUE_{n}_RECOMMENDATION}}}}", bullets(issue.get("recommendations", []))))
        # else: leave this issue slide's placeholders unfilled  -  it gets deleted below.

    # ── Additional Observations slide (secondary findings below the 6-issue cut) ──
    # Only fill it when there are observations; otherwise leave the placeholder so the
    # whole slide is trimmed below (same pattern as unused issue slides).
    if observations:
        requests.append(replace("{{ADDITIONAL_OBSERVATIONS}}", to_bullets(observations)))

    # ── Key Opportunities slide ──
    requests.append(replace("{{KEY_OPPORTUNITIES}}", to_bullets(opportunities)))

    # ── Key Takeaways slide (5 rows × 3 columns) ──
    for i in range(1, 6):
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

    # ── Delete any unused issue slides (issue-led: found fewer than the template holds) ──
    _delete_unused_issue_slides(slides_service, new_id)

    # ── Swap the dial image based on the headline RAG ─────────────────────────
    dial_url = pick_dial(data.get("overall_rag", "amber"))
    if dial_url:
        print(f"Swapping dial image…")
        try:
            slides_service.presentations().batchUpdate(
                presentationId=new_id,
                body={"requests": [{
                    "replaceImage": {
                        "imageObjectId":    DIAL_IMAGE_OBJECT_ID,
                        "imageReplaceMethod": "CENTER_INSIDE",
                        "url": dial_url,
                    }
                }]},
            ).execute()
            print("  Dial image updated.")
        except Exception as e:
            print(f"  ⚠ Could not swap dial image: {e}")
    else:
        print("  ⚠ No dial config found  -  skipping image swap.")

    # ── Insert client logo on slide 1 ─────────────────────────────────────────
    if website_url:
        print(f"Fetching client logo from: {website_url}")
        img_bytes, content_type = fetch_logo_bytes(website_url)
        if img_bytes:
            print(f"  Logo downloaded ({len(img_bytes)} bytes, {content_type})")
            _insert_logo(slides_service, drive_service, new_id, img_bytes, content_type)
        else:
            print("  ⚠ Could not download a logo  -  skipping.")
    else:
        print("  ⚠ No website URL in narrative  -  skipping logo.")

    replaced = sum(
        r.get("replaceAllTextResponse", {}).get("occurrencesChanged", 0)
        for r in result.get("replies", [])
    )
    print(f"Done. {replaced} placeholder(s) replaced.")

    # ── Validation: check for any unfilled placeholders ──
    print("Validating deck for unfilled placeholders...")
    deck = slides_service.presentations().get(presentationId=new_id).execute()
    import re
    unfilled = []
    for slide in deck.get("slides", []):
        slide_num = deck["slides"].index(slide) + 1
        for el in slide.get("pageElements", []):
            for te in el.get("shape", {}).get("text", {}).get("textElements", []):
                text = te.get("textRun", {}).get("content", "")
                matches = re.findall(r"\{\{[A-Z0-9_]+\}\}", text)
                for m in matches:
                    unfilled.append((slide_num, m))
    if unfilled:
        print(f"\n⚠️  WARNING: {len(unfilled)} unfilled placeholder(s) found:")
        for slide_num, ph in unfilled:
            print(f"   Slide {slide_num}: {ph}")
    else:
        print("✅ All placeholders filled successfully.")

    url = f"https://docs.google.com/presentation/d/{new_id}/edit"
    print(f"\n✅ Deck is ready:\n   {url}\n")
    return url


if __name__ == "__main__":
    main()
