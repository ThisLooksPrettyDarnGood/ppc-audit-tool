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
                       tokens_used: int = 0, recipient: str = "",
                       findings_lines: list = None) -> str:
    """
    Send a brief audit completion email.
    Returns "" on success, or a human-readable error string on failure
    (so the caller can surface it instead of failing silently).
    Never raises  -  will not block the audit pipeline.
    """
    # The runner sees only their own address in To. Dan always gets a copy, but as a
    # Bcc so it's invisible to whoever ran the audit. If no runner email was given (Dan
    # running it himself), it just goes to Dan with no Bcc.
    runner = (recipient or "").strip()
    if runner and runner.lower() != RECIPIENT.lower():
        to_addr  = runner
        bcc_addr = RECIPIENT
    else:
        to_addr  = RECIPIENT
        bcc_addr = ""
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

        # Full findings call-out list (internal) — everything detected, incl. items below
        # the 6-slide cut, so whoever presents can reference them on the call.
        findings_block = ""
        if findings_lines:
            numbered = "\n".join(f"  {n}. {ln}" for n, ln in enumerate(findings_lines, 1))
            findings_block = (
                f"\n\nALL FINDINGS DETECTED ({len(findings_lines)}) - top items are on the deck; "
                f"the rest are extra talking points for the call:\n{numbered}\n"
            )

        body = f"""Audit complete for {client_name} (CID: {cid}).

Completed: {time_str}
Duration:  {duration_str}{token_line}{deck_line}{findings_block}

 -  PPC Audit Tool
"""

        msg = MIMEText(body)
        msg["To"]      = to_addr
        if bcc_addr:
            msg["Bcc"] = bcc_addr      # invisible copy to Dan; not seen by the runner
        msg["From"]    = SENDER
        msg["Subject"] = f"Audit complete  -  {client_name}"

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
