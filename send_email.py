"""
send_email.py
-------------
Sends a post-audit summary email via Gmail API.
Sends from dan@ppcgeeks.co.uk to dan@ppcgeeks.co.uk.
"""

import base64
from email.mime.text import MIMEText
from datetime import datetime, timezone
from googleapiclient.discovery import build


RECIPIENT = "dan@ppcgeeks.co.uk"
SENDER    = "dan@ppcgeeks.co.uk"


def send_audit_summary(creds, client_name: str, cid: str,
                       duration_secs: float, slides_url: str = "",
                       tokens_used: int = 0, recipient: str = "") -> str:
    """
    Send a brief audit completion email.
    Returns "" on success, or a human-readable error string on failure
    (so the caller can surface it instead of failing silently).
    Never raises — will not block the audit pipeline.
    """
    to_addr = recipient.strip() if recipient and recipient.strip() else RECIPIENT
    try:
        service = build("gmail", "v1", credentials=creds)

        from audit_log import _uk_now
        now        = _uk_now()
        time_str   = now.strftime("%d %b %Y at %H:%M UK time")
        mins       = int(duration_secs // 60)
        secs       = int(duration_secs % 60)
        duration_str = f"{mins}m {secs}s"

        deck_line = f"\nDeck: {slides_url}" if slides_url else ""
        token_line = f"\nOpenAI tokens used: {tokens_used:,}" if tokens_used else ""

        body = f"""Audit complete for {client_name} (CID: {cid}).

Completed: {time_str}
Duration:  {duration_str}{token_line}{deck_line}

— PPC Audit Tool
"""

        msg = MIMEText(body)
        msg["To"]      = to_addr
        msg["From"]    = SENDER
        msg["Subject"] = f"Audit complete — {client_name}"

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(
            userId="me",
            body={"raw": raw},
        ).execute()

        print(f"  Email sent to {to_addr}.")
        return ""
    except Exception as e:
        print(f"  ⚠ Email send failed: {e}")
        return f"Email send failed: {e}"
