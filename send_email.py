"""
send_email.py
-------------
Sends a post-audit summary email via Gmail API.
Sends from dan@ppcgeeks.co.uk to the runner, Bcc dan@ppcgeeks.co.uk.

The email is written to read like a quick note from a colleague, not a system
log: a friendly opener, the deck link, the findings, a real quote of the day and
a true fun fact, plus a one-click 'was this helpful?' Yes/No (the flavour content
lives in email_flavour.py; the feedback buttons are mailto links back to Dan).
"""

import os
import json
import base64
import html as _html
from urllib.parse import quote as _urlquote, urlencode
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from googleapiclient.discovery import build

import email_flavour as _flavour


RECIPIENT = "dan@ppcgeeks.co.uk"
SENDER    = "dan@ppcgeeks.co.uk"
# Fallback only: if the feedback Google Form isn't configured yet, Yes/No fall
# back to a mailto here so the email never ships a dead button. Once the Form is
# wired (see feedback_config), feedback goes to the Form instead and never here.
FEEDBACK_ADDR = "dan@ppcgeeks.co.uk"


def _feedback_config() -> dict:
    """
    Feedback Google Form / responses Sheet settings. Read from environment first
    (so Streamlit Cloud secrets work), then a local feedback_config.json for local
    runs. All keys optional; if the Form isn't set up, the email falls back to a
    mailto. Keys: form_url, entry_helpful, entry_client, entry_auditor,
    responses_sheet_id, responses_tab.
    """
    cfg = {}
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feedback_config.json")
    try:
        with open(path) as f:
            cfg = json.load(f)
    except Exception:
        pass
    env_map = {
        "form_url":           "FEEDBACK_FORM_URL",
        "entry_helpful":      "FEEDBACK_ENTRY_HELPFUL",
        "entry_client":       "FEEDBACK_ENTRY_CLIENT",
        "entry_auditor":      "FEEDBACK_ENTRY_AUDITOR",
        "responses_sheet_id": "FEEDBACK_RESPONSES_SHEET_ID",
        "responses_tab":      "FEEDBACK_RESPONSES_TAB",
    }
    for key, env in env_map.items():
        if os.environ.get(env):
            cfg[key] = os.environ[env]
    return cfg


def _feedback_mailto(client_name: str, helpful: bool) -> str:
    """Fallback: a mailto link that pre-fills a feedback reply (used only until
    the Google Form is wired up)."""
    if helpful:
        subject = f"Audit tool feedback: helpful ({client_name})"
        body = ("Glad the audit was useful. "
                "(Feel free to add anything that stood out.)")
    else:
        subject = f"Audit tool feedback: not quite ({client_name})"
        body = ("What would have made this audit more useful? Type below and I'll "
                "take it on board, and possibly build it into a future version to "
                "improve your experience.\n\n")
    return (f"mailto:{FEEDBACK_ADDR}"
            f"?subject={_urlquote(subject)}&body={_urlquote(body)}")


def _feedback_links(client_name: str, auditor: str) -> tuple:
    """
    Returns (yes_url, no_url). If the feedback Form is configured, these are
    pre-filled Form links (rating selected, client + auditor quietly filled in so
    trends are attributable). Otherwise they fall back to mailto links.
    """
    cfg = _feedback_config()
    form_url = (cfg.get("form_url") or "").strip()
    entry_helpful = (cfg.get("entry_helpful") or "").strip()
    if form_url and entry_helpful:
        base = form_url.split("?")[0]

        def _link(answer: str) -> str:
            params = {"usp": "pp_url", f"entry.{entry_helpful}": answer}
            if cfg.get("entry_client"):
                params[f"entry.{cfg['entry_client']}"] = client_name
            if cfg.get("entry_auditor") and auditor:
                params[f"entry.{cfg['entry_auditor']}"] = auditor
            return f"{base}?{urlencode(params)}"

        return _link("Yes"), _link("No")
    return _feedback_mailto(client_name, True), _feedback_mailto(client_name, False)


def build_audit_email(client_name: str, cid: str, duration_secs: float,
                      slides_url: str, tokens_used: int,
                      findings_lines: list, today, auditor: str = "") -> tuple:
    """
    Build the (subject, plain_text, html) for the completion email.
    Pure function (no network), so it can be previewed offline.
    """
    mins = int(duration_secs // 60)
    secs = int(duration_secs % 60)
    duration_str = f"{mins}m {secs}s"

    opener   = _flavour.opener_line(today, client_name, cid)
    effort   = _flavour.effort_line(today, client_name, cid, duration_str, tokens_used)
    jest     = _flavour.token_jest(today, client_name, cid, tokens_used)
    quote, who = _flavour.quote_of_the_day(today)
    fun_fact = _flavour.fun_fact_of_the_day(today)
    n_find   = len(findings_lines or [])

    yes_link, no_link = _feedback_links(client_name, auditor)

    completed = today.strftime("%d %b %Y at %H:%M UK time")

    findings_intro = (
        f"I found {n_find} thing(s) worth talking about. The headline ones are on "
        f"the deck. Here's the full list, so you've got extra talking points for the call:"
    ) if n_find else "Nothing major leapt out, which is its own kind of good news."

    # ── Plain-text version (the reliable fallback) ─────────────────────────────
    plain_parts = [
        opener,
        "",
        effort,
    ]
    if jest:
        plain_parts.append(jest)
    plain_parts.append(f"Completed {completed} (CID: {cid}).")
    if slides_url:
        plain_parts += ["", f"Here's the deck: {slides_url}"]
    plain_parts += ["", findings_intro]
    if findings_lines:
        plain_parts += [f"  {n}. {ln}" for n, ln in enumerate(findings_lines, 1)]
    plain_parts += [
        "",
        f"Quote of the day: \"{quote}\"  - {who}",
        "",
        f"Fun fact: {fun_fact}",
        "",
        "Was this helpful?",
        f"  Yes: {yes_link}",
        f"  No:  {no_link}",
        "If you click No, type a line about why. I'll take it on board and may build "
        "it into a future version to improve your experience.",
        "",
        "- Your friendly PPC Audit Tool",
    ]
    plain = "\n".join(plain_parts)

    # ── HTML version ───────────────────────────────────────────────────────────
    esc = _html.escape
    findings_html = ""
    if findings_lines:
        items = "".join(
            f"<li style='margin:0 0 6px 0;'>{esc(ln)}</li>" for ln in findings_lines
        )
        findings_html = (
            f"<ol style='padding-left:20px;margin:8px 0 0 0;'>{items}</ol>"
        )

    deck_html = (
        f"<p style='margin:14px 0;'>Here's the deck: "
        f"<a href='{esc(slides_url)}' style='color:#1a73e8;'>open the audit</a>.</p>"
        if slides_url else ""
    )

    btn = ("display:inline-block;padding:9px 20px;border-radius:6px;"
           "text-decoration:none;font-weight:600;font-size:14px;")

    html_body = f"""\
<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
            font-size:15px;line-height:1.55;color:#202124;max-width:620px;">
  <p style="margin:0 0 14px 0;">{esc(opener)}</p>
  <p style="margin:0 0 4px 0;">{esc(effort)}</p>
  {f'<p style="margin:0 0 4px 0;color:#3c4043;">{esc(jest)}</p>' if jest else ''}
  <p style="margin:0 0 4px 0;color:#5f6368;font-size:13px;">
    Completed {esc(completed)} &middot; CID {esc(cid)}
  </p>
  {deck_html}
  <p style="margin:18px 0 0 0;">{esc(findings_intro)}</p>
  {findings_html}

  <div style="margin:22px 0;padding:14px 16px;background:#f1f3f4;border-radius:8px;">
    <div style="font-style:italic;color:#3c4043;">&ldquo;{esc(quote)}&rdquo;</div>
    <div style="margin-top:6px;color:#5f6368;font-size:13px;">&mdash; {esc(who)}</div>
  </div>

  <p style="margin:14px 0;"><strong>Fun fact:</strong> {esc(fun_fact)}</p>

  <div style="margin:24px 0 8px 0;border-top:1px solid #e0e0e0;padding-top:18px;">
    <p style="margin:0 0 12px 0;font-weight:600;">Did you find this audit helpful?</p>
    <a href="{esc(yes_link)}" style="{btn}background:#e6f4ea;color:#137333;margin-right:8px;">&#128077; Yes</a>
    <a href="{esc(no_link)}" style="{btn}background:#fce8e6;color:#c5221f;">&#128078; No</a>
    <p style="margin:12px 0 0 0;color:#5f6368;font-size:13px;">
      If you click No, type a line about why. I'll take it on board and may build it
      into a future version to improve your experience.
    </p>
  </div>

  <p style="margin:20px 0 0 0;color:#5f6368;font-size:13px;">&mdash; Your friendly PPC Audit Tool</p>
</div>"""

    subject = f"Audit done: {client_name}"
    return subject, plain, html_body


def send_audit_summary(creds, client_name: str, cid: str,
                       duration_secs: float, slides_url: str = "",
                       tokens_used: int = 0, recipient: str = "",
                       findings_lines: list = None) -> str:
    """
    Send the audit completion email.
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
        today = _uk_now()

        subject, plain, html_body = build_audit_email(
            client_name, cid, duration_secs, slides_url, tokens_used,
            findings_lines or [], today, auditor=to_addr,
        )

        msg = MIMEMultipart("alternative")
        msg["To"]      = to_addr
        if bcc_addr:
            msg["Bcc"] = bcc_addr      # invisible copy to Dan; not seen by the runner
        msg["From"]    = SENDER
        msg["Subject"] = subject
        # Plain part first, HTML second: mail clients show the last part they can render.
        msg.attach(MIMEText(plain, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

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
