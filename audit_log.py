"""
audit_log.py
------------
Logs each completed audit to a Google Sheet and provides stats for the dashboard.

Sheet columns: Timestamp | Client Name | CID | Duration (mins) | Slides URL | Tokens Used
               | Evidence URL

Evidence URL (T2, 14 Jul 2026) is the Drive link to that run's evidence bundle, so any deck
in the log can be traced back to the data it was built from. It is the last column on
purpose: older rows simply have an empty cell there, and nothing about them moves.
"""

import os
from datetime import datetime, timezone, timedelta
from googleapiclient.discovery import build

# UK time: GMT in winter, BST (UTC+1) in summer
def _uk_now() -> datetime:
    import time
    # Use UTC offset: BST is last Sun Mar → last Sun Oct
    utc_now = datetime.now(timezone.utc)
    # Simple DST check: last Sunday of March to last Sunday of October
    month = utc_now.month
    if month < 3 or month > 10:
        offset = 0
    elif month > 3 and month < 10:
        offset = 1
    else:
        # March or October — check if past last Sunday
        day = utc_now.day
        dow = utc_now.weekday()  # 0=Mon, 6=Sun
        last_sun = day - ((dow + 1) % 7)
        if month == 3:
            offset = 1 if day >= last_sun and last_sun > 0 else 0
        else:  # October
            offset = 0 if day >= last_sun and last_sun > 0 else 1
    return utc_now + timedelta(hours=offset)

# Google Sheet ID is read at CALL TIME (not import time) via _sheet_id().
# Reading it at import time was a bug: app.py sets the env var a moment AFTER
# importing this module, so the old module-level constant was always blank,
# which made every log write and stats read silently skip.
SHEET_NAME = "Audit Log"
MINUTES_PER_AUDIT_SAVED = 60  # assumed time saved vs manual audit


def _sheet_id() -> str:
    """Read the audit log sheet ID fresh each call (never cached at import)."""
    return os.environ.get("AUDIT_LOG_SHEET_ID", "")


HEADER_ROW = ["Timestamp", "Client Name", "CID", "Duration (mins)",
              "Slides URL", "Tokens Used", "Evidence URL"]


def _get_sheets_service(creds):
    return build("sheets", "v4", credentials=creds)


def _ensure_tab(service, sheet_id: str):
    """
    Make sure a tab named SHEET_NAME exists. If it's missing, create it and
    write the header row. This self-heals the 'Unable to parse range' error
    that happens when the spreadsheet only has a default 'Sheet1' tab.
    """
    meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    titles = [s["properties"]["title"] for s in meta.get("sheets", [])]
    if SHEET_NAME in titles:
        return

    # Create the tab
    service.spreadsheets().batchUpdate(
        spreadsheetId=sheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": SHEET_NAME}}}]},
    ).execute()
    # Write the header row
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"{SHEET_NAME}!A1",
        valueInputOption="RAW",
        body={"values": [HEADER_ROW]},
    ).execute()


def _ensure_header(service, sheet_id: str):
    """
    Widen the header row when a new column is added, WITHOUT touching a single data row.

    The live sheet predates the Evidence URL column: its header has six cells. Appending a
    seven-value row is safe on its own (older rows just have an empty G), but the header
    would still say six, and a column of URLs with no name is how a log stops being read.

    This rewrites row 1 and nothing else, and only when row 1 is recognisably OUR header and
    is short. An unrecognised row 1 is left exactly as it is - if someone has put their own
    data up there, that is theirs, and clobbering it to add a column heading would be a poor
    trade.
    """
    res = service.spreadsheets().values().get(
        spreadsheetId=sheet_id,
        range=f"{SHEET_NAME}!1:1",
    ).execute()
    rows = res.get("values", [])
    header = rows[0] if rows else []

    if not header or header[0] != HEADER_ROW[0]:
        return                                  # empty, or not our header → leave it alone
    if len(header) >= len(HEADER_ROW):
        return                                  # already wide enough

    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"{SHEET_NAME}!A1",
        valueInputOption="RAW",
        body={"values": [HEADER_ROW]},
    ).execute()


def log_audit(creds, client_name: str, cid: str, duration_secs: float,
              slides_url: str = "", tokens_used: int = 0,
              evidence_url: str = "") -> str:
    """
    Append one row to the audit log sheet.
    Returns "" on success, or a human-readable error string on failure
    (so the caller can surface it instead of failing silently).

    `evidence_url` defaults to "" so an audit whose evidence upload failed still logs a
    complete row - the evidence cell is simply empty, and the deck is still traceable by
    its Slides URL.
    """
    sheet_id = _sheet_id()
    if not sheet_id:
        return "AUDIT_LOG_SHEET_ID is not set — log skipped."

    try:
        service = _get_sheets_service(creds)
        _ensure_tab(service, sheet_id)
        # Cosmetic, and never worth losing a log row over: if widening the header fails,
        # the append below still writes the audit exactly as it always has.
        try:
            _ensure_header(service, sheet_id)
        except Exception as he:
            print(f"  ⚠ Audit log header widen skipped: {he}")

        now = _uk_now().strftime("%Y-%m-%d %H:%M UK")
        duration_mins = round(duration_secs / 60, 1)
        row = [[now, client_name, cid, duration_mins, slides_url, tokens_used, evidence_url]]

        service.spreadsheets().values().append(
            spreadsheetId=sheet_id,
            range=f"{SHEET_NAME}!A:G",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": row},
        ).execute()
        return ""
    except Exception as e:
        print(f"  ⚠ Audit log write failed: {e}")
        return f"Audit log write failed: {e}"


def get_stats(creds) -> dict:
    """
    Read the log sheet and return dashboard stats.
    Returns empty stats if sheet not configured or unreadable.
    """
    empty = {
        "total_audits": 0,
        "audits_today": 0,
        "hours_saved_total": 0,
        "hours_saved_month": 0,
    }
    sheet_id = _sheet_id()
    if not sheet_id:
        return empty

    try:
        service = _get_sheets_service(creds)
        result = service.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range=f"{SHEET_NAME}!A:F",
        ).execute()
        rows = result.get("values", [])
        if not rows:
            return empty

        # Skip header row if present
        data_rows = [r for r in rows if r and not r[0].startswith("Timestamp")]

        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        month_str = datetime.now(timezone.utc).strftime("%Y-%m")

        total = len(data_rows)
        today = sum(1 for r in data_rows if r[0].startswith(today_str))
        month = sum(1 for r in data_rows if r[0].startswith(month_str))

        return {
            "total_audits":      total,
            "audits_today":      today,
            "hours_saved_total": round(total * MINUTES_PER_AUDIT_SAVED / 60, 1),
            "hours_saved_month": round(month * MINUTES_PER_AUDIT_SAVED / 60, 1),
        }
    except Exception as e:
        print(f"  ⚠ Audit log read failed: {e}")
        return empty
