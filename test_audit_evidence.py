"""Tests for the audit evidence bundle (T2).

The bundle is the thing that lets us replay an audit six months later. These tests pin the
three properties that make it worth having, and the three that make it safe:

  worth having - every section is present, it round-trips through gzip, and the account data
                 that comes back out can be fed straight into the engine again (which is what
                 makes a bundle a fixture)
  safe         - no secret ever reaches Drive, a Drive failure never costs us a deck, and the
                 90-day cleanup only ever trashes files inside the evidence folder

No network, no Drive, no client data: a fake Drive service records what it was asked to do,
and the account is the same synthetic broken-tracking account the T1 tests use.

    python3 test_audit_evidence.py
"""
import gzip
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import audit_evidence as ae
import audit_log as al
from analyse_account import analyse_account


# ── A fake Drive ──────────────────────────────────────────────────────────────

class _Call:
    """googleapiclient's shape: drive.files().create(...).execute()"""

    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeDrive:
    """Records every call. Raises on demand, so we can prove a Drive outage is survivable."""

    FOLDER_MIME = "application/vnd.google-apps.folder"

    def __init__(self, folders=(), files=(), fail_on=()):
        self._folders = list(folders)      # dicts: id, name, createdTime
        self._files = list(files)          # dicts: id, name, createdTime, parents
        self.fail_on = set(fail_on)        # any of: list, folder_create, upload, trash
        self.uploads = []                  # (body, mimetype, bytes)
        self.folders_created = []
        self.trashed = []
        self.permanently_deleted = []      # must stay empty, always
        self.permissions_granted = []      # must stay empty, always
        self.queries = []

    # drive.files() → self
    def files(self):
        return self

    def permissions(self):
        return self

    def list(self, q=None, **kw):
        self.queries.append(q)

        def _run():
            if "list" in self.fail_on:
                raise RuntimeError("Drive list is down")
            if self.FOLDER_MIME in (q or ""):
                return {"files": list(self._folders)}
            return {"files": list(self._files)}

        return _Call(_run)

    def create(self, body=None, media_body=None, fields=None, **kw):
        body = body or {}

        def _run():
            # A folder create and a file upload both come through files().create.
            if body.get("mimeType") == self.FOLDER_MIME:
                if "folder_create" in self.fail_on:
                    raise RuntimeError("no permission to create folders")
                fid = f"folder-{len(self.folders_created) + 1}"
                self.folders_created.append(body)
                self._folders.append({"id": fid, "name": body.get("name")})
                return {"id": fid}
            if media_body is not None:
                if "upload" in self.fail_on:
                    raise RuntimeError("Drive upload is down")
                fid = f"file-{len(self.uploads) + 1}"
                raw = media_body.getbytes(0, media_body.size())
                self.uploads.append({"body": body, "mimetype": media_body.mimetype(),
                                     "bytes": raw})
                return {"id": fid, "webViewLink": f"https://drive.google.com/file/d/{fid}/view"}
            # A permissions().create() would land here - it never should.
            self.permissions_granted.append(body)
            return {"id": "perm"}

        return _Call(_run)

    def update(self, fileId=None, body=None, **kw):
        def _run():
            if "trash" in self.fail_on:
                raise RuntimeError("Drive update is down")
            if (body or {}).get("trashed"):
                self.trashed.append(fileId)
            return {"id": fileId}

        return _Call(_run)

    def delete(self, fileId=None, **kw):
        # Nothing in T2 may ever call this: 90-day retention TRASHES, it does not delete.
        def _run():
            self.permanently_deleted.append(fileId)
            return {}

        return _Call(_run)


# ── The synthetic account (same shape as the T1 tests; not a client) ──────────

def _account_data():
    return {
        "client_cid": "999-888-7777",
        "account_summary_30d": {"spend": 1500.0, "clicks": 500, "conversions": 0,
                                "impressions": 27000, "ctr_pct": 1.85, "avg_cpc": 3.0,
                                "cpa": None},
        "campaigns": [
            {"id": "1", "name": "Search - Services", "status": "ENABLED", "type": "SEARCH",
             "bid_strategy": "MAXIMIZE_CONVERSIONS", "daily_budget_gbp": 25.0,
             "spend_30d": 1500.0, "clicks_30d": 500, "conversions_30d": 0,
             "impressions_30d": 27000, "target_cpa_gbp": None, "target_roas": None},
        ],
        "campaign_types_active": ["SEARCH"],
        "ad_groups": [{"id": "100", "name": "AG", "status": "ENABLED",
                       "campaign_resource": "x", "spend_30d": 1500.0}],
        "conversion_actions": [
            {"name": "generate_lead", "status": "ENABLED", "counting_type": "ONE_PER_CLICK",
             "include_in_conversions": True, "category": "SUBMIT_LEAD_FORM",
             "has_tag_snippet": True, "conversions_30d": 0.0},
        ],
        "top_search_terms": [], "location_targeting": [], "audience_signals": [],
        "quality_scores": [], "rsa_ad_strength": None, "paused_campaign_history": [],
        "negative_keyword_count": 5, "auto_apply_recommendations": False,
        "auto_apply_types": [],
        "performance_summary": {
            "spend_30d": "£1,500", "convs_30d": "0",
            "is_paused": True, "last_active": "2026-03-04", "days_dark": 132,
            "window_end": "2026-03-04",
        },
        "_warnings": ["    (geo query failed: boom)"],
        "_query_failures": [{"fetch": "geo", "error": "boom", "query": "SELECT x FROM y"}],
    }


def _narrative():
    return {
        "client_name": "Test Co",
        "account_cid": "999-888-7777",
        "overall_rag": "amber_red",
        "issues": [{"title": "Tracking is not recording leads"}],
        "executive_summary": {"headline": "Needs attention"},
        "_tokens_used": 12345,
        "_warnings": ["style: em-dash in issue 1"],
        "_selected_issues": [
            {"detail": "Conversion tracking is not recording leads.", "severity": 122,
             "rag": "red", "theme": "tracking"},
        ],
    }


def _bundle(**over):
    data = over.pop("account_data", None) or _account_data()
    narr = over.pop("narrative", None) or _narrative()
    kwargs = dict(
        account_data=data,
        findings=analyse_account(data),
        narrative=narr,
        client_name="Test Co",
        cid="999-888-7777",
        deck_url="https://docs.google.com/presentation/d/abc123/edit",
        tokens_used=narr.get("_tokens_used", 0),
        gpt_model="gpt-5.5",
        git_sha="deadbeef",
        now=datetime(2026, 7, 14, 14, 32),
    )
    kwargs.update(over)
    return ae.build_bundle(**kwargs)


def _save(drive, **over):
    """save_audit_evidence with the synthetic account, against a fake Drive."""
    data = over.pop("account_data", None) or _account_data()
    narr = over.pop("narrative", None) or _narrative()
    kwargs = dict(
        account_data=data,
        findings=analyse_account(data),
        narrative=narr,
        client_name="Test Co",
        cid="999-888-7777",
        deck_url="https://docs.google.com/presentation/d/abc123/edit",
        tokens_used=12345,
        gpt_model="gpt-5.5",
        drive=drive,
        now=datetime(2026, 7, 14, 14, 32, tzinfo=timezone.utc),
    )
    kwargs.update(over)
    return ae.save_audit_evidence(**kwargs)


# ── The bundle ────────────────────────────────────────────────────────────────

class BundleContents(unittest.TestCase):

    def test_every_section_the_brief_asks_for_is_present_and_populated(self):
        b = _bundle()

        # The run itself
        self.assertTrue(b["account_data"]["campaigns"])          # raw fetched data
        self.assertTrue(b["findings"])                           # deterministic findings
        self.assertTrue(b["selected_findings"])                  # what made the deck
        self.assertTrue(b["narrative"]["issues"])                # final narration
        self.assertEqual(b["warnings"]["fetch"], ["    (geo query failed: boom)"])
        self.assertEqual(b["warnings"]["narrative"], ["style: em-dash in issue 1"])
        self.assertEqual(b["query_failures"][0]["fetch"], "geo")

        # Reporting dates available today, and the pause
        self.assertEqual(b["reporting"]["run_date"], "2026-07-14")
        self.assertTrue(b["reporting"]["is_paused"])
        self.assertEqual(b["reporting"]["last_active"], "2026-03-04")
        self.assertEqual(b["reporting"]["window_end"], "2026-03-04")

        # Run metadata
        run = b["run"]
        self.assertEqual(run["client_name"], "Test Co")
        self.assertEqual(run["cid"], "999-888-7777")
        self.assertEqual(run["audit_timestamp"], "2026-07-14 14:32 UK")
        self.assertEqual(run["git_sha"], "deadbeef")
        self.assertEqual(run["gpt_model"], "gpt-5.5")
        self.assertEqual(run["token_count"], 12345)
        self.assertEqual(run["deck_url"], "https://docs.google.com/presentation/d/abc123/edit")

    def test_the_real_git_sha_is_recorded_when_none_is_passed(self):
        b = _bundle(git_sha=None)
        sha = b["run"]["git_sha"]
        self.assertTrue(sha)
        # 40-char hex, or an honest "unknown" - never a crash, never a lie.
        self.assertTrue(sha == "unknown" or (len(sha) == 40 and int(sha, 16) >= 0))

    def test_it_round_trips_through_gzip_and_back_into_the_engine(self):
        b = _bundle()
        blob = ae.compress_bundle(b)

        # It really is gzip, and it really is smaller.
        self.assertEqual(blob[:2], b"\x1f\x8b")
        self.assertLess(len(blob), len(json.dumps(b).encode()))

        back = ae.decompress_bundle(blob)
        self.assertEqual(back, json.loads(json.dumps(b, default=str)))

        # The point of the whole ticket: the data that comes back out is a fixture. Feed it
        # to the engine again and you get the identical findings - which is what "replay the
        # Oilfast run" will mean when we have a real bundle to replay.
        replayed = analyse_account(back["account_data"])
        self.assertEqual(json.dumps(replayed, default=str, sort_keys=True),
                         json.dumps(b["findings"], default=str, sort_keys=True))

    def test_the_filename_carries_the_date_the_client_and_the_cid(self):
        name = ae.evidence_filename("Kents Premier Coins & Bullion", "539-263-1535",
                                    when=datetime(2026, 7, 14, 14, 32))
        self.assertEqual(name, "2026-07-14_kents-premier-coins-bullion_5392631535_1432.json.gz")
        self.assertTrue(name.endswith(".json.gz"))


# ── Secrets ───────────────────────────────────────────────────────────────────

class SecretSafety(unittest.TestCase):

    def test_secret_named_keys_are_stripped_out_of_the_bundle(self):
        data = _account_data()
        data["refresh_token"] = "1//0gLONGLIVEDREFRESHTOKENVALUEHERE"
        data["campaigns"][0]["api_key"] = "sk-proj-AAAABBBBCCCCDDDDEEEEFFFF1234"

        b = _bundle(account_data=data)

        self.assertEqual(b["account_data"]["refresh_token"], "[redacted]")
        self.assertEqual(b["account_data"]["campaigns"][0]["api_key"], "[redacted]")
        self.assertEqual(ae.find_secrets(b), [])                      # provably clean
        self.assertIn("account_data.refresh_token", b["evidence_meta"]["redacted_keys"])

    def test_a_secret_hiding_in_an_innocent_key_is_detected_and_blocks_the_upload(self):
        data = _account_data()
        # Not a secret-NAMED key, so scrubbing cannot save us. Only the value check can.
        data["campaigns"][0]["name"] = "Search - sk-proj-AAAABBBBCCCCDDDDEEEEFFFF1234"

        b = _bundle(account_data=data)
        self.assertTrue(ae.find_secrets(b))
        with self.assertRaises(ae.SecretLeakError):
            ae.assert_no_secrets(b)

        drive = FakeDrive()
        res = _save(drive, account_data=data)

        self.assertEqual(drive.uploads, [])                 # nothing left the building
        self.assertEqual(res["url"], "")
        self.assertIn("safety check", res["warning"])

    def test_a_live_secret_value_is_caught_even_with_no_pattern_for_it(self):
        """A developer token has no recognisable shape. The literal value check is what
        catches it - that is why app.py passes the live secrets in."""
        dev_token = "Xy7_developer_token_value_9Zq"
        data = _account_data()
        data["notes"] = f"debug: {dev_token}"

        b = _bundle(account_data=data)
        self.assertEqual(ae.find_secrets(b), [])                             # no shape to see
        self.assertTrue(ae.find_secrets(b, extra_secret_values=[dev_token]))  # but we know it

        drive = FakeDrive()
        res = _save(drive, account_data=data, extra_secret_values=[dev_token])
        self.assertEqual(drive.uploads, [])
        self.assertIn("safety check", res["warning"])

    def test_the_warning_never_quotes_the_secret_it_found(self):
        data = _account_data()
        data["campaigns"][0]["name"] = "sk-proj-AAAABBBBCCCCDDDDEEEEFFFF1234"
        res = _save(FakeDrive(), account_data=data)
        self.assertNotIn("sk-proj", res["warning"])

    def test_a_clean_bundle_is_not_flagged(self):
        """The guard must not cry wolf: real search terms and campaign names go nowhere near
        a key shape."""
        data = _account_data()
        data["top_search_terms"] = [{"term": "sk-ii moisturiser", "spend": 12.0},
                                    {"term": "password manager for teams", "spend": 8.0}]
        b = _bundle(account_data=data)
        self.assertEqual(ae.find_secrets(b), [])


# ── Drive ─────────────────────────────────────────────────────────────────────

class DriveUpload(unittest.TestCase):

    def test_a_successful_save_uploads_one_private_gzip_into_the_evidence_folder(self):
        drive = FakeDrive()
        res = _save(drive)

        self.assertEqual(res["warning"], "")
        self.assertTrue(res["url"].startswith("https://drive.google.com/file/d/"))

        self.assertEqual(len(drive.uploads), 1)
        up = drive.uploads[0]
        self.assertEqual(up["mimetype"], "application/gzip")
        self.assertEqual(up["body"]["parents"], ["folder-1"])
        self.assertTrue(up["body"]["name"].startswith("2026-07-14_test-co_9998887777"))
        self.assertTrue(up["body"]["name"].endswith(".json.gz"))

        # It is the real bundle, and it is still readable.
        back = ae.decompress_bundle(up["bytes"])
        self.assertEqual(back["run"]["deck_url"],
                         "https://docs.google.com/presentation/d/abc123/edit")

        # Private: we never grant a permission on evidence (the logo upload does; this must not).
        self.assertEqual(drive.permissions_granted, [])

    def test_an_existing_folder_is_reused_never_duplicated(self):
        drive = FakeDrive(folders=[{"id": "existing-folder", "name": ae.EVIDENCE_FOLDER_NAME}])
        res = _save(drive)

        self.assertEqual(drive.folders_created, [])                 # nothing new created
        self.assertEqual(drive.uploads[0]["body"]["parents"], ["existing-folder"])
        self.assertEqual(res["warning"], "")

    def test_the_folder_is_created_when_it_does_not_exist_yet(self):
        drive = FakeDrive()
        _save(drive)

        self.assertEqual(len(drive.folders_created), 1)
        created = drive.folders_created[0]
        self.assertEqual(created["name"], "PPC Team Audit Evidence")
        self.assertEqual(created["mimeType"], FakeDrive.FOLDER_MIME)
        self.assertNotIn("parents", created)      # My Drive root → private, not in a shared tree


class DriveFailureNeverStopsTheAudit(unittest.TestCase):

    def test_an_upload_failure_warns_and_returns_no_url(self):
        drive = FakeDrive(fail_on=["upload"])
        res = _save(drive)                          # must not raise

        self.assertEqual(res["url"], "")
        self.assertIn("upload failed", res["warning"])

    def test_a_folder_that_cannot_be_created_gives_a_clear_warning(self):
        drive = FakeDrive(fail_on=["folder_create"])
        res = _save(drive)                          # must not raise

        self.assertEqual(res["url"], "")
        self.assertIn("PPC Team Audit Evidence", res["warning"])
        self.assertIn("could not be found or created", res["warning"])

    def test_drive_being_down_entirely_is_survivable(self):
        drive = FakeDrive(fail_on=["list"])
        res = _save(drive)                          # must not raise
        self.assertEqual(res["url"], "")
        self.assertTrue(res["warning"])

    def test_a_cleanup_failure_does_not_lose_the_upload(self):
        """Retention is housekeeping. It must never cost us the evidence we just saved."""
        drive = FakeDrive(files=[{"id": "old", "name": "old.json.gz", "parents": ["folder-1"],
                                  "createdTime": "2026-01-01T00:00:00.000Z"}],
                          fail_on=["trash"])
        res = _save(drive)

        self.assertTrue(res["url"])                 # the bundle still landed
        self.assertEqual(res["warning"], "")
        self.assertEqual(drive.trashed, [])


# ── 90-day retention ──────────────────────────────────────────────────────────

class Retention(unittest.TestCase):

    NOW = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)

    def _aged(self, days, **over):
        stamp = (self.NOW - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        f = {"id": f"f{days}", "name": f"{days}-days-old.json.gz",
             "parents": ["evidence-folder"], "createdTime": stamp}
        f.update(over)
        return f

    def test_files_older_than_90_days_are_moved_to_trash(self):
        drive = FakeDrive(files=[self._aged(91), self._aged(200)])
        out = ae.cleanup_old_evidence(drive, "evidence-folder", now=self.NOW)

        self.assertEqual(sorted(drive.trashed), ["f200", "f91"])
        self.assertEqual(len(out["trashed"]), 2)
        self.assertEqual(out["errors"], [])

    def test_files_newer_than_90_days_are_kept(self):
        drive = FakeDrive(files=[self._aged(1), self._aged(89), self._aged(90)])
        ae.cleanup_old_evidence(drive, "evidence-folder", now=self.NOW)
        self.assertEqual(drive.trashed, [])

    def test_nothing_is_ever_permanently_deleted(self):
        drive = FakeDrive(files=[self._aged(365)])
        ae.cleanup_old_evidence(drive, "evidence-folder", now=self.NOW)

        self.assertEqual(drive.trashed, ["f365"])           # trashed…
        self.assertEqual(drive.permanently_deleted, [])     # …never deleted

    def test_cleanup_only_ever_looks_inside_the_evidence_folder(self):
        drive = FakeDrive(files=[self._aged(365)])
        ae.cleanup_old_evidence(drive, "evidence-folder", now=self.NOW)

        self.assertEqual(len(drive.queries), 1)
        self.assertIn("'evidence-folder' in parents", drive.queries[0])
        self.assertIn("trashed = false", drive.queries[0])

    def test_a_file_outside_the_folder_is_never_touched_even_if_drive_returns_it(self):
        """Belt and braces. If the query ever came back wrong, the parent re-check is what
        stands between a bug and someone's deck being trashed."""
        stray = self._aged(365, id="stray", name="someone-elses-deck",
                           parents=["a-completely-different-folder"])
        drive = FakeDrive(files=[stray, self._aged(365)])

        ae.cleanup_old_evidence(drive, "evidence-folder", now=self.NOW)

        self.assertEqual(drive.trashed, ["f365"])           # ours went
        self.assertNotIn("stray", drive.trashed)            # theirs did not
        self.assertEqual(drive.permanently_deleted, [])

    def test_a_file_with_an_unreadable_date_is_kept_not_trashed(self):
        drive = FakeDrive(files=[self._aged(365, createdTime="not-a-date")])
        ae.cleanup_old_evidence(drive, "evidence-folder", now=self.NOW)
        self.assertEqual(drive.trashed, [])

    def test_retention_runs_as_part_of_a_normal_save(self):
        old = {"id": "old", "name": "old.json.gz", "parents": ["folder-1"],
               "createdTime": "2026-01-01T00:00:00.000Z"}       # >90 days before 14 Jul
        drive = FakeDrive(files=[old])
        res = _save(drive)

        self.assertTrue(res["url"])
        self.assertEqual(drive.trashed, ["old"])
        self.assertEqual(res["trashed"], ["old.json.gz"])


# ── No client data left on this machine ───────────────────────────────────────

class NoLocalEvidence(unittest.TestCase):

    def test_saving_evidence_writes_no_local_file_at_all(self):
        """The bundle is streamed to Drive from memory. There is no temp file to clean up,
        which is the strongest version of 'clean up your temp files'."""
        tool_dir = os.path.dirname(os.path.abspath(ae.__file__))
        before_tool = set(os.listdir(tool_dir))
        before_tmp = set(os.listdir(tempfile.gettempdir()))

        drive = FakeDrive()
        res = _save(drive)
        self.assertTrue(res["url"])                       # it really did save

        self.assertEqual(set(os.listdir(tool_dir)) - before_tool, set())
        self.assertEqual(set(os.listdir(tempfile.gettempdir())) - before_tmp, set())

    def test_no_evidence_bundle_is_committable(self):
        """The .gitignore backstop, in case a bundle is ever dumped locally for debugging."""
        with open(os.path.join(os.path.dirname(os.path.abspath(ae.__file__)),
                               ".gitignore")) as f:
            ignored = f.read()
        self.assertIn("*.json.gz", ignored)


# ── The audit log ─────────────────────────────────────────────────────────────

class FakeSheets:
    """Just enough Sheets to prove the new column is added without disturbing the old ones."""

    def __init__(self, header=None, rows=None):
        self.header = list(header) if header else None
        self.rows = [list(r) for r in (rows or [])]
        self.appended = []
        self.header_writes = []

    # service.spreadsheets().values().get(...).execute()
    def spreadsheets(self):
        return self

    def values(self):
        return self

    def get(self, spreadsheetId=None, range=None, **kw):
        if range and range.endswith("!1:1"):
            return _Call(lambda: {"values": [self.header] if self.header else []})
        vals = ([self.header] if self.header else []) + self.rows
        return _Call(lambda: {"values": vals})

    def update(self, spreadsheetId=None, range=None, body=None, **kw):
        def _run():
            self.header_writes.append(body["values"][0])
            self.header = list(body["values"][0])
            return {}

        return _Call(_run)

    def append(self, spreadsheetId=None, range=None, body=None, **kw):
        def _run():
            self.appended.append({"range": range, "row": body["values"][0]})
            self.rows.append(body["values"][0])
            return {}

        return _Call(_run)

    # _ensure_tab's metadata read
    def batchUpdate(self, **kw):
        return _Call(lambda: {})


class AuditLogColumn(unittest.TestCase):

    OLD_HEADER = ["Timestamp", "Client Name", "CID", "Duration (mins)",
                  "Slides URL", "Tokens Used"]
    OLD_ROW = ["2026-07-01 09:00 UK", "Old Client", "111-222-3333", 4.2,
               "https://slides/old", 9000]

    def setUp(self):
        os.environ["AUDIT_LOG_SHEET_ID"] = "sheet-123"
        self.sheets = FakeSheets(header=list(self.OLD_HEADER), rows=[list(self.OLD_ROW)])
        self._real_service = al._get_sheets_service
        self._real_tab = al._ensure_tab
        al._get_sheets_service = lambda creds: self.sheets
        al._ensure_tab = lambda service, sheet_id: None

    def tearDown(self):
        al._get_sheets_service = self._real_service
        al._ensure_tab = self._real_tab
        os.environ.pop("AUDIT_LOG_SHEET_ID", None)

    def test_the_evidence_url_is_written_as_the_last_column(self):
        err = al.log_audit(None, "Test Co", "999-888-7777", 300.0,
                           "https://slides/new", 12345,
                           evidence_url="https://drive.google.com/file/d/ev1/view")
        self.assertEqual(err, "")

        row = self.sheets.appended[0]["row"]
        self.assertEqual(row[0][:4], "2026")
        self.assertEqual(row[1:], ["Test Co", "999-888-7777", 5.0, "https://slides/new",
                                   12345, "https://drive.google.com/file/d/ev1/view"])
        self.assertTrue(self.sheets.appended[0]["range"].endswith("A:G"))

    def test_the_header_gains_the_column_and_the_old_rows_are_untouched(self):
        al.log_audit(None, "Test Co", "999-888-7777", 300.0, "https://slides/new", 12345,
                     evidence_url="https://drive.google.com/file/d/ev1/view")

        self.assertEqual(self.sheets.header_writes, [al.HEADER_ROW])
        self.assertEqual(self.sheets.header[-1], "Evidence URL")
        # The pre-existing audit is exactly as it was, six columns and all.
        self.assertEqual(self.sheets.rows[0], self.OLD_ROW)

    def test_an_already_widened_header_is_left_alone(self):
        self.sheets.header = list(al.HEADER_ROW)
        al.log_audit(None, "Test Co", "999-888-7777", 300.0, "https://slides/new", 12345,
                     evidence_url="https://drive.google.com/file/d/ev1/view")
        self.assertEqual(self.sheets.header_writes, [])          # no pointless rewrite

    def test_an_unrecognised_first_row_is_never_clobbered(self):
        self.sheets.header = ["Someone", "Else's", "Data"]
        al.log_audit(None, "Test Co", "999-888-7777", 300.0, "https://slides/new", 12345,
                     evidence_url="https://drive.google.com/file/d/ev1/view")
        self.assertEqual(self.sheets.header_writes, [])
        self.assertEqual(self.sheets.header, ["Someone", "Else's", "Data"])

    def test_an_audit_with_no_evidence_still_logs_a_complete_row(self):
        """Evidence upload failed → the cell is empty, and nothing else about the log moves."""
        err = al.log_audit(None, "Test Co", "999-888-7777", 300.0, "https://slides/new", 12345)
        self.assertEqual(err, "")
        self.assertEqual(self.sheets.appended[0]["row"][-1], "")

    def test_a_header_failure_never_costs_us_the_log_row(self):
        def _boom(service, sheet_id):
            raise RuntimeError("header read failed")

        real = al._ensure_header
        al._ensure_header = _boom
        try:
            err = al.log_audit(None, "Test Co", "999-888-7777", 300.0, "https://slides/new",
                               12345, evidence_url="https://drive/ev")
        finally:
            al._ensure_header = real

        self.assertEqual(err, "")
        self.assertEqual(len(self.sheets.appended), 1)           # the audit was still logged


if __name__ == "__main__":
    unittest.main(verbosity=2)
