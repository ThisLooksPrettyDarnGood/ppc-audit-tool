"""
feedback_report.py
-------------------
Reads the audit-email feedback (the Google Form responses Sheet) and summarises
it: how many Yes vs No, the recent comments, and which audits drew a thumbs-down.

This is the bit that lets the tool's owner be told "3 people flagged the same
thing" instead of fielding one-off replies. Reads with the same Google creds and
Sheets plumbing as audit_log.py.

The Form is expected to collect (in order): Timestamp, Helpful (Yes/No),
Comment, Client, Auditor. Column order is detected from the header row, so it
still works if the Form's questions are reordered.
"""

import os
import json
from collections import Counter
from googleapiclient.discovery import build


def _config() -> dict:
    cfg = {}
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feedback_config.json")
    try:
        with open(path) as f:
            cfg = json.load(f)
    except Exception:
        pass
    if os.environ.get("FEEDBACK_RESPONSES_SHEET_ID"):
        cfg["responses_sheet_id"] = os.environ["FEEDBACK_RESPONSES_SHEET_ID"]
    if os.environ.get("FEEDBACK_RESPONSES_TAB"):
        cfg["responses_tab"] = os.environ["FEEDBACK_RESPONSES_TAB"]
    return cfg


def _col(header: list, *needles: str) -> int:
    """Find the column whose header contains any needle (case-insensitive)."""
    for i, h in enumerate(header):
        hl = (h or "").lower()
        if any(n in hl for n in needles):
            return i
    return -1


def get_feedback(creds) -> dict:
    """
    Returns a structured summary:
      {"configured": bool, "total": int, "yes": int, "no": int,
       "comments": [{"helpful","comment","client","auditor","when"}...],
       "error": str}
    Never raises.
    """
    out = {"configured": False, "total": 0, "yes": 0, "no": 0,
           "comments": [], "error": ""}
    cfg = _config()
    sheet_id = (cfg.get("responses_sheet_id") or "").strip()
    if not sheet_id:
        out["error"] = "Feedback Sheet not configured yet (responses_sheet_id is blank)."
        return out
    out["configured"] = True
    tab = cfg.get("responses_tab") or "Form Responses 1"
    try:
        service = build("sheets", "v4", credentials=creds)
        result = service.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=f"'{tab}'!A:Z",
        ).execute()
        rows = result.get("values", [])
        if len(rows) < 2:
            return out  # header only, or empty
        header, data = rows[0], rows[1:]
        i_when    = _col(header, "timestamp", "time")
        i_helpful = _col(header, "helpful", "useful")
        i_comment = _col(header, "improve", "comment", "why")
        i_client  = _col(header, "client")
        i_auditor = _col(header, "auditor", "who")

        def cell(r, idx):
            return r[idx].strip() if 0 <= idx < len(r) and r[idx] else ""

        for r in data:
            helpful = cell(r, i_helpful).lower()
            is_yes = helpful.startswith("y")
            out["total"] += 1
            out["yes" if is_yes else "no"] += 1
            comment = cell(r, i_comment)
            if comment or not is_yes:
                out["comments"].append({
                    "helpful": "Yes" if is_yes else "No",
                    "comment": comment,
                    "client":  cell(r, i_client),
                    "auditor": cell(r, i_auditor),
                    "when":    cell(r, i_when),
                })
        return out
    except Exception as e:
        out["error"] = f"Could not read feedback Sheet: {e}"
        return out


def format_report(summary: dict) -> str:
    """Human-readable summary for the terminal or a chat reply."""
    if summary.get("error"):
        return f"⚠ {summary['error']}"
    total, yes, no = summary["total"], summary["yes"], summary["no"]
    if total == 0:
        return "No feedback responses yet."
    pct = round(yes / total * 100)
    lines = [f"Feedback so far: {total} response(s) - {yes} Yes / {no} No ({pct}% helpful).", ""]
    nos = [c for c in summary["comments"] if c["helpful"] == "No"]
    if nos:
        lines.append(f"Thumbs-down ({len(nos)}):")
        for c in nos:
            who = f" - {c['auditor']}" if c["auditor"] else ""
            on  = f" on {c['client']}" if c["client"] else ""
            txt = f": \"{c['comment']}\"" if c["comment"] else " (no reason given)"
            lines.append(f"  • {c['when']}{on}{who}{txt}")
        lines.append("")
    # Surface repeated words in comments as a rough theme signal.
    words = Counter()
    for c in summary["comments"]:
        for w in c["comment"].lower().split():
            w = w.strip(".,!?'\"")
            if len(w) > 4:
                words[w] += 1
    common = [w for w, n in words.most_common(5) if n > 1]
    if common:
        lines.append("Recurring words in comments: " + ", ".join(common))
    return "\n".join(lines)


if __name__ == "__main__":
    # Local run: build creds from token.json (same as populate_slides) and print.
    from populate_slides import get_credentials
    print(format_report(get_feedback(get_credentials())))
