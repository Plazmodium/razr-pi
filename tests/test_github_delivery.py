import contextlib
import importlib.util
import io
import json
import os
import tempfile
import time
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / "packages/razr-pi"))
from adapter import github_delivery as delivery

URL = "https://github.com/o/r/pull/1"
SHA = "a" * 40
DIGEST = "sha256:" + "c" * 64


def pr_value(**changes):
    value = {"url": URL, "number": 1, "state": "OPEN", "isDraft": False,
             "baseRefName": "campaign/razr-pi-next", "baseRefOid": "b" * 40,
             "headRefName": "feature", "headRefOid": SHA, "mergeStateStatus": "CLEAN",
             "mergeable": "MERGEABLE", "reviewDecision": "",
             "reviewRequests": [], "reviews": [], "comments": [], "statusCheckRollup": [],
             "mergedAt": None, "closedAt": None, "updatedAt": "2026-01-01T00:00:00Z"}
    value.update(changes)
    return value


class GithubDeliveryTests(unittest.TestCase):
    def test_remote_and_head_are_strict(self):
        with patch.object(delivery, "_run", side_effect=["git@github.com:Plazmodium/razr-pi.git", SHA + "  refs/heads/feature\n"]):
            self.assertEqual(delivery.remote_identity(Path(".")), "Plazmodium/razr-pi")
            self.assertEqual(delivery.pushed_head(Path("."), "feature"), SHA)

    def test_snapshot_has_source_identity_and_base_head(self):
        with patch.object(delivery, "remote_identity", return_value="o/r"), \
             patch.object(delivery, "pushed_head", side_effect=[SHA, "b" * 40]), \
             patch.object(delivery, "source_identity", return_value=DIGEST):
            result = delivery.snapshot(Path("."), branch="feature")
            self.assertEqual(result["commit"], SHA)
            self.assertEqual(result["base_commit"], "b" * 40)

    def test_readback_binds_exact_identity_and_source(self):
        with patch.object(delivery, "_json", return_value=pr_value()), \
             patch.object(delivery, "_required_checks", return_value=([], "none")), \
             patch.object(delivery, "_threads", return_value=[]), \
             patch.object(delivery, "remote_identity", return_value="o/r"), \
             patch.object(delivery, "pushed_head", side_effect=["b" * 40, SHA]), \
             patch.object(delivery, "source_identity", return_value=DIGEST):
            result = delivery.read_pr(Path("."), URL, expected_repository="o/r",
                                      expected_base="campaign/razr-pi-next", expected_base_sha="b" * 40,
                                      expected_head="feature", expected_head_sha=SHA,
                                      expected_source_digest=DIGEST)
            self.assertEqual(result["repository"], "o/r")
            self.assertEqual(result["sourceDigest"], DIGEST)

    def test_realistic_populated_pr_schema_validates(self):
        realistic = pr_value(
            reviewDecision="APPROVED",
            reviews=[{"id": "rv1", "author": {"login": "reviewer"}, "body": "ok", "commit": {"oid": SHA},
                      "createdAt": "2026-01-01T00:00:00Z", "includesCreatedEdit": False,
                      "reactionGroups": [], "state": "APPROVED", "submittedAt": "2026-01-01T00:00:01Z", "updatedAt": "2026-01-01T00:00:01Z"}],
            comments=[{"id": "cm1", "author": {"login": "reviewer"}, "body": "note", "createdAt": "2026-01-01T00:00:00Z",
                       "isMinimized": False, "reactions": {"totalCount": 0}, "updatedAt": "2026-01-01T00:00:00Z", "url": "https://github.com/o/r/pull/1#issuecomment-1"}],
            statusCheckRollup=[{"__typename": "CheckRun", "name": "ci", "status": "COMPLETED", "conclusion": "SUCCESS",
                                "completedAt": "2026-01-01T00:00:00Z", "detailsUrl": "https://example.invalid/ci", "workflowName": "CI"}])
        delivery._validate_pr(realistic, URL, "o", "r", 1)

    def test_readback_rejects_nested_numeric_values(self):
        bad = pr_value(comments=[{"id": 7, "author": None, "body": "x", "isMinimized": False}])
        with self.assertRaises(delivery.DeliveryError):
            delivery._validate_pr(bad, URL, "o", "r", 1)

    def test_readback_rejects_branch_sha_and_source_mismatches(self):
        for kwargs in ({"expected_base": "wrong"}, {"expected_head_sha": "d" * 40},
                       {"expected_source_digest": "sha256:" + "d" * 64}):
            with self.subTest(kwargs=kwargs), patch.object(delivery, "_json", return_value=pr_value()), \
                 patch.object(delivery, "_required_checks", return_value=([], "none")), \
                 patch.object(delivery, "_threads", return_value=[]), \
                 patch.object(delivery, "remote_identity", return_value="o/r"), \
                 patch.object(delivery, "pushed_head", side_effect=["b" * 40, SHA]), \
                 patch.object(delivery, "source_identity", return_value=DIGEST):
                with self.assertRaises(delivery.DeliveryError):
                    delivery.read_pr(Path("."), URL, **kwargs)

    def test_auth_output_is_not_returned(self):
        with patch.object(delivery, "_run", return_value="secret-looking status") as run:
            self.assertIsNone(delivery.auth_preflight(Path(".")))
            run.assert_called_once_with("gh", ["auth", "status"], Path("."))

    def test_create_is_idempotent_and_does_not_duplicate(self):
        existing = {"number": 1}
        with patch.object(delivery, "remote_identity", return_value="o/r"), \
             patch.object(delivery, "pushed_head", side_effect=["b" * 40, SHA]), \
             patch.object(delivery, "source_identity", return_value=DIGEST), \
             patch.object(delivery, "_json", return_value=[{"url": URL}]), \
             patch.object(delivery, "read_pr", return_value=existing) as read, \
             patch.object(delivery, "_run") as run:
            self.assertEqual(delivery.create_pr(Path("."), base="campaign/razr-pi-next", head="feature",
                                                title="candidate", body="body"), existing)
            run.assert_not_called()
            read.assert_called_once()

    def test_create_refuses_remote_expectation_override(self):
        with patch.object(delivery, "remote_identity", return_value="o/r"):
            with self.assertRaisesRegex(delivery.DeliveryError, "repository expectation"):
                delivery.create_pr(Path("."), base="base", head="feature", title="t", body="b",
                                   expected_repository="other/repo")

    def test_create_recovers_one_racing_server_pr(self):
        with patch.object(delivery, "remote_identity", return_value="o/r"), \
             patch.object(delivery, "pushed_head", side_effect=["b" * 40, SHA]), \
             patch.object(delivery, "source_identity", return_value=DIGEST), \
             patch.object(delivery, "_json", side_effect=[[], [{"url": URL}]]), \
             patch.object(delivery, "_run", side_effect=delivery.DeliveryError("create failed")), \
             patch.object(delivery, "read_pr", return_value={"number": 1}):
            self.assertEqual(delivery.create_pr(Path("."), base="base", head="feature", title="t", body="b")["number"], 1)

    def test_create_refuses_ambiguous_existing_prs(self):
        with patch.object(delivery, "remote_identity", return_value="o/r"), \
             patch.object(delivery, "pushed_head", side_effect=["b" * 40, SHA]), \
             patch.object(delivery, "source_identity", return_value=DIGEST), \
             patch.object(delivery, "_json", return_value=[{"url": URL}, {"url": URL.replace("/1", "/2")}]):
            with self.assertRaisesRegex(delivery.DeliveryError, "ambiguous"):
                delivery.create_pr(Path("."), base="campaign/razr-pi-next", head="feature",
                                   title="candidate", body="body")

    def test_required_check_states_are_truthful(self):
        cases = [("", "none"), ('[{"name":"ci","state":"QUEUED","bucket":"PENDING"}]', "pending"),
                 ('[{"name":"ci","state":"COMPLETED","bucket":"FAIL"}]', "failing"),
                 ('[{"name":"ci","state":"COMPLETED","bucket":"PASS"}]', "passing"),
                 ('[{"name":"ci","state":"completed","bucket":"skipping"}]', "skipping")]
        for raw, expected in cases:
            with self.subTest(expected=expected), patch.object(delivery, "_run", return_value=raw):
                self.assertEqual(delivery._required_checks(Path("."), URL, "o/r")[1], expected)

    def test_required_check_schema_rejects_unknown_fields(self):
        with patch.object(delivery, "_run", return_value='[{"name":"ci","state":"COMPLETED","bucket":"PASS","x":1}]'):
            with self.assertRaises(delivery.DeliveryError):
                delivery._required_checks(Path("."), URL, "o/r")

    def test_thread_pagination_is_bounded_and_validated(self):
        page = {"data": {"repository": {"pullRequest": {"reviewThreads": {
            "nodes": [{"id": "t1", "isResolved": False, "comments": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}}],
            "pageInfo": {"hasNextPage": True, "endCursor": "c1"}}}}}}
        last = {"data": {"repository": {"pullRequest": {"reviewThreads": {
            "nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}}
        with patch.object(delivery, "_json", side_effect=[page, last]):
            self.assertEqual(len(delivery._threads(Path("."), "o", "r", 1)), 1)
        malformed = {"data": {"repository": {"pullRequest": {"reviewThreads": {
            "nodes": [{"id": "t", "isResolved": False, "comments": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}, "extra": 1}],
            "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}}
        with patch.object(delivery, "_json", return_value=malformed), self.assertRaises(delivery.DeliveryError):
            delivery._threads(Path("."), "o", "r", 1)

    def test_streaming_overflow_timeout_and_process_group_cleanup(self):
        script = "import sys; sys.stderr.write('credential-secret'); sys.stdout.write('x'*100); sys.stdout.flush()"
        with patch.object(delivery, "MAX_STDOUT", 10):
            with self.assertRaises(delivery.DeliveryError):
                delivery._capture(sys.executable, ["-c", script], Path("."))
        with tempfile.TemporaryDirectory() as directory:
            pidfile = Path(directory) / "child.pid"
            script = ("import os,subprocess,sys,time; child=subprocess.Popen([sys.executable,'-c',"
                      "'import os,sys,time; open(sys.argv[1],\\\"w\\\").write(str(os.getpid())); time.sleep(10)',"
                      "'" + str(pidfile) + "']); os.write(1,b'x'); time.sleep(10)")
            started = time.monotonic()
            with self.assertRaisesRegex(delivery.DeliveryError, "timed out"):
                delivery._capture(sys.executable, ["-c", script], Path("."), timeout=.1)
            self.assertLess(time.monotonic() - started, 2.0)
            if pidfile.exists():
                pid = int(pidfile.read_text())
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)

    def test_cli_rejects_malformed_input_and_wraps_snapshot(self):
        spec = importlib.util.spec_from_file_location("github_delivery_cli", Path(__file__).parents[1] / "packages/razr-pi/scripts/github-delivery.py")
        cli = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(cli)
        output = io.StringIO()
        with patch.object(cli, "sys", sys), patch.object(sys, "stdin", io.StringIO("[]")), patch.object(sys, "stdout", output):
            self.assertEqual(cli.main(), 1)
        self.assertIn('"ok": false', output.getvalue())
        output = io.StringIO()
        with patch.object(cli.delivery, "auth_preflight"), patch.object(cli.delivery, "snapshot", return_value={"commit": SHA}), \
             patch.object(sys, "stdin", io.StringIO('{"schema_version":1,"operation":"snapshot","branch":"feature"}')), \
             patch.object(sys, "stdout", output):
            self.assertEqual(cli.main(), 0)
        self.assertIn('"commit": "' + SHA, output.getvalue())
        for malformed in (
                '{"schema_version":1,"operation":"snapshot","branch":7}',
                '{"schema_version":1,"operation":"read","url":null}',
                '{"schema_version":1,"operation":"create","base":"b","branch":"h","title":"t","body":"x","extra":1}',
                '{"schema_version":1,"operation":"create","base":"b","branch":"h","title":"t","body":null}'):
            with self.subTest(malformed=malformed), patch.object(cli.delivery, "auth_preflight") as auth, \
                 patch.object(sys, "stdin", io.StringIO(malformed)), patch.object(sys, "stdout", io.StringIO()) as stream:
                self.assertEqual(cli.main(), 1)
                self.assertIn('"code": "INVALID_REQUEST"', stream.getvalue())
                auth.assert_not_called()

    def test_thread_comment_pagination_reads_multiple_pages(self):
        first = {"data": {"repository": {"pullRequest": {"reviewThreads": {
            "nodes": [{"id": "t1", "isResolved": False, "comments": {"nodes": [{"id": "c1", "body": "one", "author": None, "updatedAt": None, "path": None}], "pageInfo": {"hasNextPage": True, "endCursor": "c1"}}}],
            "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}}
        second = {"data": {"node": {"comments": {"nodes": [{"id": "c2", "body": "two", "author": None, "updatedAt": None, "path": None}], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}
        with patch.object(delivery, "_json", side_effect=[first, second]):
            result = delivery._threads(Path("."), "o", "r", 1)
        self.assertEqual([c["id"] for c in result[0]["comments"]["nodes"]], ["c1", "c2"])

    def test_nested_numeric_comment_id_refuses(self):
        with self.assertRaises(delivery.DeliveryError):
            delivery._validate_thread_comment({"id": 1, "body": "x", "author": None, "updatedAt": None, "path": None})

    def test_create_has_no_lifecycle_flags(self):
        with patch.object(delivery, "remote_identity", return_value="o/r"), \
             patch.object(delivery, "pushed_head", side_effect=["b" * 40, SHA]), \
             patch.object(delivery, "source_identity", return_value=DIGEST), \
             patch.object(delivery, "_json", return_value=[]), \
             patch.object(delivery, "_run", return_value=URL) as run, \
             patch.object(delivery, "read_pr", return_value=pr_value()):
            delivery.create_pr(Path("."), base="campaign/razr-pi-next", head="feature", title="t", body="b")
            args = run.call_args.args[1]
            self.assertIn("--repo", args)
            self.assertIn("o/r", args)
            self.assertNotIn("--force", args + ["--merge", "--auto", "--delete-branch"])


if __name__ == "__main__":
    unittest.main()
