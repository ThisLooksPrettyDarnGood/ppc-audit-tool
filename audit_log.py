"""
audit_log.py
------------
Logs each completed audit to a Google Sheet and provides stats for the dashboard.

Sheet columns: Timestamp | Client Name | CID | Duration (mins) | Slides URL | Tokens Used
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


def _get_sheets_service(creds):
    return build("sheets", "v4", credentials=creds)


def log_audit(creds, client_name: str, cid: str, duration_secs: float,
              slides_url: str = "", tokens_used: int = 0) -> str:
    """
    Append one row to the audit log sheet.
    Returns "" on success, or a human-readable error string on failure
    (so the caller can surface it instead of failing silently).
    """
    sheet_id = _sheet_id()
    if not sheet_id:
        return "AUDIT_LOG_SHEET_ID is not set — log skipped."

    try:
        service = _get_sheets_service(creds)
        now = _uk_now().strftime("%Y-%m-%d %H:%M UK")
        duration_mins = round(duration_secs / 60, 1)
        row = [[now, client_name, cid, duration_mins, slides_url, tokens_used]]

        service.spreadsheets().values().append(
            spreadsheetId=sheet_id,
            range=f"{SHEET_NAME}!A:F",
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
