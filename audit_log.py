"""
audit_log.py
------------
Logs each completed audit to a Google Sheet and provides stats for the dashboard.

Sheet columns: Timestamp | Client Name | CID | Duration (mins) | Slides URL | Tokens Used
"""

import os
from datetime import datetime, timezone
from googleapiclient.discovery import build

# Google Sheet ID — create a blank sheet and paste its ID here
# The sheet must be shared with the Google service account or accessible via OAuth
AUDIT_LOG_SHEET_ID = os.environ.get("AUDIT_LOG_SHEET_ID", "")
SHEET_NAME = "Audit Log"
MINUTES_PER_AUDIT_SAVED = 60  # assumed time saved vs manual audit


def _get_sheets_service(creds):
    return build("sheets", "v4", credentials=creds)


def log_audit(creds, client_name: str, cid: str, duration_secs: float,
              slides_url: str = "", tokens_used: int = 0):
    """
    Append one row to the audit log sheet.
    Silently skips if AUDIT_LOG_SHEET_ID is not configured.
    """
    if not AUDIT_LOG_SHEET_ID:
        return

    try:
        service = _get_sheets_service(creds)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        duration_mins = round(duration_secs / 60, 1)
        row = [[now, client_name, cid, duration_mins, slides_url, tokens_used]]

        service.spreadsheets().values().append(
            spreadsheetId=AUDIT_LOG_SHEET_ID,
            range=f"{SHEET_NAME}!A:F",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": row},
        ).execute()
    except Exception as e:
        print(f"  ⚠ Audit log write failed: {e}")


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
    if not AUDIT_LOG_SHEET_ID:
        return empty

    try:
        service = _get_sheets_service(creds)
        result = service.spreadsheets().values().get(
            spreadsheetId=AUDIT_LOG_SHEET_ID,
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
