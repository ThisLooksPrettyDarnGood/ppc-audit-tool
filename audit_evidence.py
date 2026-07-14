"""
audit_evidence.py
-----------------
T2: persist one evidence bundle per audit run, so a finished deck can always be replayed.

Every "INFERRED" and "UNTRACEABLE" label in the Oilfast evidence review exists because the
run artefacts were gone: Streamlit Cloud's disk is ephemeral and narrative_output.json is
overwritten by the next audit. This module writes the whole run - raw data, findings, the
narration, the warnings, what we could not see - to a PRIVATE Drive folder, keyed to the
deck URL that came out of it.

Three rules hold this module together:

1. **It can never break an audit.** Every entry point is wrapped; save_audit_evidence()
   returns a warning string instead of raising. A deck that rendered is a deck that ships.
2. **No secrets, ever.** The bundle is scrubbed of secret-named keys and then CHECKED for
   secret material (shapes, plus the literal live secret values the caller passes in)
   BEFORE it is uploaded. If the check finds anything, we abort the upload rather than
   risk writing a key to Drive.
3. **Cleanup only ever touches this folder, and only ever trashes.** Files older than
   RETENTION_DAYS are moved to Drive Trash (recoverable), never permanently deleted, and
   nothing outside the evidence folder is looked at, let alone touched.

The folder is PRIVATE (owned by the account the tool signs in as, no permissions granted).
It is deliberately NOT a sibling of the deck output folder: that folder lives in a tree
shared with ~20 people including external addresses, and inheriting that sharing would
expose full raw client account data. Evidence and client-facing decks stay in separate
trees on purpose.
"""

import gzip
import io
import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone

from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# UK timestamp, shared with the audit log so the two can never drift apart.
from audit_log import _uk_now

# ── Configuration ─────────────────────────────────────────────────────────────

EVIDENCE_FOLDER_NAME = "PPC Team Audit Evidence"   # private; Dan, 14 Jul 2026
RETENTION_DAYS       = 90                          # then → Drive Trash, not deleted
SCHEMA_VERSION       = 1

_FOLDER_MIME = "application/vnd.google-apps.folder"
_GZIP_MIME   = "application/gzip"


class SecretLeakError(Exception):
    """Secret material was found in a bundle. The upload is abandoned, not sanitised."""


# ── Secret safety ─────────────────────────────────────────────────────────────
# Two independent layers, because assuming account_data is clean is exactly the kind of
# assumption that ships a key to Drive. Layer 1 removes anything whose KEY names a secret.
# Layer 2 hunts for secret material by SHAPE and by literal value, wherever it is hiding.

_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|client[_-]?secret|refresh[_-]?token|access[_-]?token|id[_-]?token"
    r"|developer[_-]?token|private[_-]?key|password|passwd|credential|oauth|bearer"
    r"|authorization|secret)",
    re.I,
)

# Deliberately strict, so a search term like "sk-ii moisturiser" or a password-manager
# query can never trip the guard. Only real key shapes match.
_SECRET_VALUE_RES = [
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),                 # OpenAI
    re.compile(r"GOCSPX-[A-Za-z0-9_\-]{10,}"),             # Google OAuth client secret
    re.compile(r"ya29\.[A-Za-z0-9_\-]{20,}"),              # Google access token
    re.compile(r"1//[A-Za-z0-9_\-]{20,}"),                 # Google refresh token
    re.compile(r"AIza[A-Za-z0-9_\-]{30,}"),                # Google API key
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),     # PEM
]

_REDACTED = "[redacted]"
# Short values are config, not secrets. Stops an empty or trivial env var matching
# everything in the bundle.
_MIN_SECRET_LEN = 12


def _env_secret_values():
    """Live secret values visible to this process, by env-var NAME. app.py also passes the
    Streamlit Secrets values in explicitly, because on Cloud they are not env vars."""
    out = []
    for key, val in os.environ.items():
        if _SECRET_KEY_RE.search(key) and isinstance(val, str) and len(val) >= _MIN_SECRET_LEN:
            out.append(val)
    return out


def scrub(obj):
    """Return a copy of obj with every secret-NAMED key redacted. Returns (clean, paths)."""
    redacted = []

    def _walk(node, path):
        if isinstance(node, dict):
            clean = {}
            for k, v in node.items():
                here = f"{path}.{k}" if path else str(k)
                if isinstance(k, str) and _SECRET_KEY_RE.search(k):
                    clean[k] = _REDACTED
                    redacted.append(here)
                else:
                    clean[k] = _walk(v, here)
            return clean
        if isinstance(node, list):
            return [_walk(v, f"{path}[{i}]") for i, v in enumerate(node)]
        return node

    clean = _walk(obj, "")
    return clean, redacted


def find_secrets(bundle, extra_secret_values=()):
    """Every reason this bundle must not be uploaded. Empty list = safe.

    Reasons name the FINDING, never the secret itself - a warning that quotes the key it
    found is a warning that leaks the key into the Streamlit UI and the logs.
    """
    reasons = []

    def _walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                here = f"{path}.{k}" if path else str(k)
                if isinstance(k, str) and _SECRET_KEY_RE.search(k) and v != _REDACTED:
                    reasons.append(f"secret-named key still present at {here}")
                _walk(v, here)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                _walk(v, f"{path}[{i}]")
        elif isinstance(node, str):
            for rx in _SECRET_VALUE_RES:
                if rx.search(node):
                    reasons.append(f"key-shaped string at {path}")
                    break

    _walk(bundle, "")

    # The strongest check available: the literal values of the secrets this process holds.
    # If any of them appears anywhere in the serialised bundle, something has gone wrong
    # that no pattern list would have caught.
    blob = json.dumps(bundle, default=str)
    for val in list(extra_secret_values) + _env_secret_values():
        if isinstance(val, str) and len(val) >= _MIN_SECRET_LEN and val in blob:
            reasons.append("a live secret value appears in the bundle")

    return reasons


def assert_no_secrets(bundle, extra_secret_values=()):
    """Raise SecretLeakError unless the bundle is provably free of secret material."""
    reasons = find_secrets(bundle, extra_secret_values)
    if reasons:
        raise SecretLeakError("; ".join(sorted(set(reasons))))


# ── Bundle ────────────────────────────────────────────────────────────────────

def _git_sha():
    """The exact tool version that produced this deck. 'unknown' is an acceptable answer;
    a crashed audit is not."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass
    return "unknown"


def build_bundle(*, account_data, findings, narrative, client_name, cid, deck_url,
                 tokens_used=0, gpt_model="", git_sha=None, now=None):
    """One dict holding everything needed to replay this audit run.

    Secret-named keys are stripped here; the bundle is CHECKED separately, before upload.
    """
    now = now or _uk_now()
    account_data = account_data or {}
    findings = findings or {}
    narrative = narrative or {}

    perf = account_data.get("performance_summary") or {}

    bundle = {
        "schema_version": SCHEMA_VERSION,
        # ── run metadata ──────────────────────────────────────────────────────
        "run": {
            "client_name":     client_name,
            "cid":             cid,
            "audit_timestamp": now.strftime("%Y-%m-%d %H:%M UK"),
            "git_sha":         git_sha if git_sha is not None else _git_sha(),
            "gpt_model":       gpt_model,
            "token_count":     tokens_used,
            "deck_url":        deck_url,
        },
        # ── which dates this run could actually see ───────────────────────────
        # T9 replaces this with one labelled window in the account's timezone. Until then
        # we record what the fetch knows: the server run date, and the pause anchoring that
        # get_performance_summary already computes.
        "reporting": {
            "run_date":    now.strftime("%Y-%m-%d"),
            "is_paused":   perf.get("is_paused"),
            "last_active": perf.get("last_active"),
            "days_dark":   perf.get("days_dark"),
            "window_end":  perf.get("window_end"),
        },
        # ── the run itself ────────────────────────────────────────────────────
        "account_data":       account_data,                              # raw fetch
        "findings":           findings,                                  # deterministic engine
        "selected_findings":  narrative.get("_selected_issues") or [],   # what made the deck
        "narrative":          narrative,                                 # final narration
        "warnings": {
            "fetch":     list(account_data.get("_warnings") or []),
            "narrative": list(narrative.get("_warnings") or []),
        },
        "query_failures":     list(account_data.get("_query_failures") or []),
    }

    bundle, redacted = scrub(bundle)
    bundle["evidence_meta"] = {
        # Paths only. Never the values.
        "redacted_keys": redacted,
        "retention_days": RETENTION_DAYS,
    }
    return bundle


def compress_bundle(bundle):
    """gzipped UTF-8 JSON. The raw data dict runs to several MB on a big account; gzip takes
    roughly 90% of that back."""
    raw = json.dumps(bundle, default=str).encode("utf-8")
    return gzip.compress(raw)


def decompress_bundle(blob):
    """Round-trip partner of compress_bundle - this is what makes a bundle a fixture."""
    return json.loads(gzip.decompress(blob).decode("utf-8"))


def evidence_filename(client_name, cid, when=None):
    """Date first so the folder sorts chronologically, then client, then CID. The time
    suffix keeps two runs of the same client on the same day apart."""
    when = when or _uk_now()
    slug = re.sub(r"[^a-z0-9]+", "-", str(client_name or "").lower()).strip("-") or "client"
    digits = re.sub(r"\D", "", str(cid or "")) or "nocid"
    return f"{when:%Y-%m-%d}_{slug}_{digits}_{when:%H%M}.json.gz"


# ── Drive ─────────────────────────────────────────────────────────────────────

def find_or_create_folder(drive, name=EVIDENCE_FOLDER_NAME):
    """The private evidence folder, reused if it already exists.

    Reuse is by name, among folders this account OWNS - so if Dan moves the folder, we
    follow it rather than quietly creating a second one. A new folder is created in My
    Drive root with NO permissions granted, which is what makes it private: everything in
    it is visible only to the account the tool signs in as.
    """
    safe = str(name).replace("\\", "\\\\").replace("'", "\\'")
    res = drive.files().list(
        q=(f"name = '{safe}' and mimeType = '{_FOLDER_MIME}' "
           f"and trashed = false and 'me' in owners"),
        fields="files(id, name, createdTime)",
        orderBy="createdTime",
        pageSize=10,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute()
    files = res.get("files") or []
    if files:
        return files[0]["id"], False

    created = drive.files().create(
        body={"name": name, "mimeType": _FOLDER_MIME},   # no parents → My Drive root
        fields="id",
        supportsAllDrives=True,
    ).execute()
    return created["id"], True


def upload_bundle(drive, folder_id, filename, blob):
    """Upload the gzipped bundle. Streamed straight from memory: no temp file is written,
    so there is no local copy of client data to clean up, or to forget to clean up.

    Note what is NOT here: permissions().create. The file inherits the private folder's
    access and nothing else. (populate_slides makes the LOGO public; evidence never is.)
    """
    media = MediaIoBaseUpload(io.BytesIO(blob), mimetype=_GZIP_MIME, resumable=False)
    f = drive.files().create(
        body={"name": filename, "parents": [folder_id]},
        media_body=media,
        fields="id, webViewLink",
        supportsAllDrives=True,
    ).execute()
    url = f.get("webViewLink") or f"https://drive.google.com/file/d/{f.get('id')}/view"
    return url, f.get("id")


def _parse_drive_time(value):
    """Drive's RFC-3339 createdTime → aware datetime, or None if it is unreadable (in which
    case the file is KEPT: we never trash something whose age we could not establish)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def cleanup_old_evidence(drive, folder_id, retention_days=RETENTION_DAYS, now=None):
    """Move evidence older than retention_days to Drive Trash.

    Three hard guarantees, each of which has a test:
      - only files whose parents include folder_id are ever considered, AND each candidate
        is re-checked against folder_id before it is touched;
      - files are TRASHED (files().update trashed=True), never deleted - Drive Trash keeps
        them for 30 more days, so a mistake here is recoverable;
      - a failure returns an error string. It never raises, and it never stops an audit.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=retention_days)
    trashed, errors = [], []
    page_token = None

    while True:
        res = drive.files().list(
            q=f"'{folder_id}' in parents and trashed = false",
            fields="nextPageToken, files(id, name, createdTime, parents)",
            pageSize=100,
            pageToken=page_token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()

        for f in res.get("files") or []:
            # Belt and braces: the query already scopes to the folder, but a file we cannot
            # PROVE is in the evidence folder is a file we do not touch.
            if folder_id not in (f.get("parents") or []):
                continue
            created = _parse_drive_time(f.get("createdTime"))
            if created is None or created >= cutoff:
                continue
            try:
                drive.files().update(
                    fileId=f["id"],
                    body={"trashed": True},
                    supportsAllDrives=True,
                ).execute()
                trashed.append(f.get("name") or f["id"])
            except Exception as e:
                errors.append(f"{f.get('name') or f['id']}: {e}")

        page_token = res.get("nextPageToken")
        if not page_token:
            break

    return {"trashed": trashed, "errors": errors}


# ── The one call app.py makes ─────────────────────────────────────────────────

def save_audit_evidence(creds=None, *, account_data, findings, narrative, client_name, cid,
                        deck_url, tokens_used=0, gpt_model="", extra_secret_values=(),
                        drive=None, now=None, retention_days=RETENTION_DAYS):
    """Build, check, compress and upload one evidence bundle, then prune old ones.

    NEVER RAISES. Returns a result dict; `warning` is a human-readable string when
    something went wrong, and "" when everything worked. The deck has already been built
    and shown by the time this runs, and nothing in here is allowed to take that away.
    """
    result = {"url": "", "file_id": "", "filename": "", "bytes": 0,
              "trashed": [], "warning": ""}

    try:
        bundle = build_bundle(
            account_data=account_data, findings=findings, narrative=narrative,
            client_name=client_name, cid=cid, deck_url=deck_url,
            tokens_used=tokens_used, gpt_model=gpt_model, now=now,
        )
    except Exception as e:
        result["warning"] = f"Evidence bundle could not be built: {e}"
        return result

    # Gate. Nothing below this line runs if the bundle is not provably clean.
    try:
        assert_no_secrets(bundle, extra_secret_values)
    except SecretLeakError as e:
        result["warning"] = (
            f"Evidence bundle NOT uploaded - the safety check found secret material ({e}). "
            f"The deck and the audit are unaffected. Please tell Dan."
        )
        return result
    except Exception as e:
        result["warning"] = f"Evidence bundle NOT uploaded - the safety check failed: {e}"
        return result

    try:
        drive = drive or build("drive", "v3", credentials=creds)
    except Exception as e:
        result["warning"] = f"Evidence not saved - could not reach Google Drive: {e}"
        return result

    try:
        folder_id, created = find_or_create_folder(drive)
    except Exception as e:
        result["warning"] = (
            f"Evidence not saved - the '{EVIDENCE_FOLDER_NAME}' folder could not be found "
            f"or created ({e}). Check this Google account can create folders in its Drive. "
            f"The deck and the audit are unaffected."
        )
        return result
    if created:
        print(f"  Created private evidence folder '{EVIDENCE_FOLDER_NAME}' (id={folder_id})")

    try:
        blob = compress_bundle(bundle)
        filename = evidence_filename(client_name, cid, when=now)
        url, file_id = upload_bundle(drive, folder_id, filename, blob)
        result.update({"url": url, "file_id": file_id, "filename": filename,
                       "bytes": len(blob)})
        print(f"  Evidence bundle saved: {filename} ({len(blob) / 1024:.0f} KB) → {url}")
    except Exception as e:
        result["warning"] = (
            f"Evidence bundle upload failed: {e}. The deck and the audit are unaffected."
        )
        # Fall through: retention still runs. A failed upload is no reason to let old
        # client data sit in Drive past its 90 days.

    try:
        cleaned = cleanup_old_evidence(drive, folder_id, retention_days=retention_days, now=now)
        result["trashed"] = cleaned["trashed"]
        if cleaned["trashed"]:
            print(f"  Retention: moved {len(cleaned['trashed'])} bundle(s) older than "
                  f"{retention_days} days to Drive Trash")
        if cleaned["errors"]:
            print(f"  Retention: {len(cleaned['errors'])} file(s) could not be trashed")
    except Exception as e:
        # Retention is housekeeping. It is never allowed to matter more than the audit.
        print(f"  (evidence retention skipped: {e})")

    return result
