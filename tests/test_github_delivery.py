import json
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
             patch.object(delivery, "source_identity", return_value=DIGEST):
            result = delivery.read_pr(Path("."), URL, expected_repository="o/r",
                                      expected_base="campaign/razr-pi-next", expected_base_sha="b" * 40,
                                      expected_head="feature", expected_head_sha=SHA,
                                      expected_source_digest=DIGEST)
            self.assertEqual(result["repository"], "o/r")
            self.assertEqual(result["sourceDigest"], DIGEST)

    def test_readback_rejects_branch_sha_and_source_mismatches(self):
        for kwargs in ({"expected_base": "wrong"}, {"expected_head_sha": "d" * 40},
                       {"expected_source_digest": "sha256:" + "d" * 64}):
            with self.subTest(kwargs=kwargs), patch.object(delivery, "_json", return_value=pr_value()), \
                 patch.object(delivery, "_required_checks", return_value=([], "none")), \
                 patch.object(delivery, "_threads", return_value=[]), \
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
                 ('[{"name":"ci","state":"COMPLETED","bucket":"PASS"}]', "passing")]
        for raw, expected in cases:
            with self.subTest(expected=expected), patch.object(delivery, "_run", return_value=raw):
                self.assertEqual(delivery._required_checks(Path("."), URL)[1], expected)

    def test_required_check_schema_rejects_unknown_fields(self):
        with patch.object(delivery, "_run", return_value='[{"name":"ci","state":"PASS","bucket":"PASS","x":1}]'):
            with self.assertRaises(delivery.DeliveryError):
                delivery._required_checks(Path("."), URL)

    def test_thread_pagination_is_bounded_and_validated(self):
        page = {"data": {"repository": {"pullRequest": {"reviewThreads": {
            "nodes": [{"id": "t1", "isResolved": False, "comments": {"nodes": []}}],
            "pageInfo": {"hasNextPage": True, "endCursor": "c1"}}}}}}
        last = {"data": {"repository": {"pullRequest": {"reviewThreads": {
            "nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}}
        with patch.object(delivery, "_json", side_effect=[page, last]):
            self.assertEqual(len(delivery._threads(Path("."), "o", "r", 1)), 1)
        malformed = {"data": {"repository": {"pullRequest": {"reviewThreads": {
            "nodes": [{"id": "t", "isResolved": False, "comments": {"nodes": []}, "extra": 1}],
            "pageInfo": {"hasNextPage": False, "endCursor": None}}}}}}
        with patch.object(delivery, "_json", return_value=malformed), self.assertRaises(delivery.DeliveryError):
            delivery._threads(Path("."), "o", "r", 1)

    def test_streaming_overflow_timeout_and_stderr_suppression(self):
        script = "import sys,time; sys.stderr.write('credential-secret'); sys.stdout.write('x'*100); sys.stdout.flush()"
        with patch.object(delivery, "MAX_STDOUT", 10):
            with self.assertRaises(delivery.DeliveryError):
                delivery._capture(sys.executable, ["-c", script], Path("."))
        with self.assertRaisesRegex(delivery.DeliveryError, "timed out"):
            delivery._capture(sys.executable, ["-c", "import time; time.sleep(2)"], Path("."), timeout=.05)

    def test_create_has_no_lifecycle_flags(self):
        with patch.object(delivery, "remote_identity", return_value="o/r"), \
             patch.object(delivery, "pushed_head", side_effect=["b" * 40, SHA]), \
             patch.object(delivery, "source_identity", return_value=DIGEST), \
             patch.object(delivery, "_json", return_value=[]), \
             patch.object(delivery, "_run", return_value=URL) as run, \
             patch.object(delivery, "read_pr", return_value=pr_value()):
            delivery.create_pr(Path("."), base="campaign/razr-pi-next", head="feature", title="t", body="b")
            args = run.call_args.args[1]
            self.assertNotIn("--force", args + ["--merge", "--auto", "--delete-branch"])


if __name__ == "__main__":
    unittest.main()
