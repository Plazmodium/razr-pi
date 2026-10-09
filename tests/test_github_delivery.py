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
        with patch.object(delivery, "_run", return_value="git@github.com:Plazmodium/razr-pi.git"):
            self.assertEqual(delivery.remote_identity(Path(".")), "Plazmodium/razr-pi")
        with patch.object(delivery, "remote_identity", return_value="Plazmodium/razr-pi"), \
             patch.object(delivery, "_run", return_value=SHA):
            self.assertEqual(delivery.pushed_head(Path("."), "feature"), SHA)

    def test_source_identity_matches_core_for_default_and_custom_work_roots(self):
        from adapter.core_subprocess import _source_digest
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
            config = {"schema_version": 1, "work_root": ".razr/work", "retention": "commit",
                      "adapter": {"mode": "process-local", "max_active_work_items": 5,
                                  "queue_order": ["priority", "approved_at", "id"]}}
            (root / "razr.yaml").write_text(json.dumps(config) + "\n")
            (root / "source.txt").write_text("source")
            (root / ".razr/work").mkdir(parents=True)
            (root / ".razr/work/hidden.txt").write_text("hidden")
            (root / "AGENTS.md").write_text("instructions")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)
            commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
            self.assertEqual(delivery.source_identity(root, commit), _source_digest(root, commit))
            config["work_root"] = ".workflow/data"
            (root / "razr.yaml").write_text(json.dumps(config) + "\n")
            (root / ".workflow/data").mkdir(parents=True)
            (root / ".workflow/data/hidden.txt").write_text("hidden")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "custom"], cwd=root, check=True)
            commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
            self.assertEqual(delivery.source_identity(root, commit), _source_digest(root, commit))
            # The live checkout may have no config (or a different config); the
            # candidate commit's custom config still defines the digest domain.
            (root / "razr.yaml").unlink()
            from runtime.scripts.razr import source_tree_digest
            self.assertEqual(delivery.source_identity(root, commit),
                             source_tree_digest(root, commit, ".workflow/data"))

    def test_snapshot_has_source_identity_and_base_head(self):
        with patch.object(delivery, "remote_identity", return_value="o/r"), \
             patch.object(delivery, "pushed_head", side_effect=[SHA, "b" * 40]), \
             patch.object(delivery, "source_identity", return_value=DIGEST):
            result = delivery.snapshot(Path("."), branch="feature")
            self.assertEqual(result["commit"], SHA)
            self.assertEqual(result["base_commit"], "b" * 40)

    def test_readback_binds_exact_identity_and_source(self):
        with patch.object(delivery, "_required_checks", return_value=([], "none")), \
             patch.object(delivery, "_threads", return_value=[]), \
             patch.object(delivery, "remote_identity", return_value="o/r"), \
             patch.object(delivery, "pushed_head", side_effect=["b" * 40, SHA, "b" * 40, SHA, "b" * 40, SHA]), \
             patch.object(delivery, "source_identity", side_effect=[DIGEST, DIGEST, DIGEST]), \
             patch.object(delivery, "_json", side_effect=[pr_value(), pr_value(),
                 {"number": 1, "url": URL, "state": "OPEN", "isDraft": False,
                  "baseRefName": "campaign/razr-pi-next", "baseRefOid": "b" * 40,
                  "headRefName": "feature", "headRefOid": SHA, "mergeStateStatus": "CLEAN",
                  "mergeable": "MERGEABLE", "reviewDecision": "", "mergedAt": None,
                  "closedAt": None, "updatedAt": "2026-01-01T00:00:00Z"}]):
            result = delivery.read_pr(Path("."), URL, expected_repository="o/r",
                                      expected_base="campaign/razr-pi-next", expected_base_sha="b" * 40,
                                      expected_head="feature", expected_head_sha=SHA,
                                      expected_source_digest=DIGEST)
            self.assertEqual(result["repository"], "o/r")
            self.assertEqual(result["sourceDigest"], DIGEST)
            self.assertFalse(result["collectionCompleteness"]["reviews"]["complete"])
            self.assertEqual(delivery._stable_collection("truncated", list(range(101)), list(range(101))), list(range(101)))

    def test_provider_review_and_reaction_enums_are_closed(self):
        for decision in ("", "APPROVED", "CHANGES_REQUESTED", "REVIEW_REQUIRED"):
            delivery._validate_pr(pr_value(reviewDecision=decision), URL, "o", "r", 1)
        with self.assertRaises(delivery.DeliveryError):
            delivery._validate_pr(pr_value(reviewDecision="DISMISSED"), URL, "o", "r", 1)
        good = pr_value(comments=[{"id": "c", "author": None, "authorAssociation": "NONE", "body": "x",
                                   "createdAt": None, "includesCreatedEdit": False, "isMinimized": False,
                                   "minimizedReason": None, "reactionGroups":[{"content":"HEART","users":{"totalCount":0}}],
                                   "updatedAt": None, "url": None, "viewerDidAuthor": False}])
        delivery._validate_pr(good, URL, "o", "r", 1)
        bad = pr_value(comments=[{**good["comments"][0], "reactionGroups":[{"content":"UNKNOWN","users":{"totalCount":0}}]}])
        with self.assertRaises(delivery.DeliveryError):
            delivery._validate_pr(bad, URL, "o", "r", 1)

    def test_merge_state_uses_live_graphql_enum(self):
        delivery._validate_pr(pr_value(mergeStateStatus="HAS_HOOKS"), URL, "o", "r", 1)
        with self.assertRaises(delivery.DeliveryError):
            delivery._validate_pr(pr_value(mergeStateStatus="HAS_HOOK_ERRORS"), URL, "o", "r", 1)
        with self.assertRaises(delivery.DeliveryError):
            delivery._validate_pr(pr_value(mergeStateStatus="DRAFT"), URL, "o", "r", 1)

    def test_realistic_populated_pr_schema_validates(self):
        realistic = pr_value(
            reviewDecision="APPROVED",
            reviews=[{"id": "rv1", "author": {"login": "reviewer"}, "authorAssociation": "MEMBER", "body": "ok", "commit": {"oid": SHA},
                      "createdAt": "2026-01-01T00:00:00Z", "includesCreatedEdit": False,
                      "reactionGroups": [], "state": "APPROVED", "submittedAt": "2026-01-01T00:00:01Z", "updatedAt": "2026-01-01T00:00:01Z"}],
            comments=[{"id": "cm1", "author": {"login": "reviewer"}, "authorAssociation": "MEMBER", "body": "note", "createdAt": "2026-01-01T00:00:00Z",
                       "includesCreatedEdit": False, "isMinimized": False, "minimizedReason": None, "reactionGroups": [], "updatedAt": "2026-01-01T00:00:00Z", "url": "https://github.com/o/r/pull/1#issuecomment-1", "viewerDidAuthor": False}],
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
            with self.subTest(kwargs=kwargs), patch.object(delivery, "_json", side_effect=[pr_value(), pr_value()]), \
                 patch.object(delivery, "_required_checks", return_value=([], "none")), \
                 patch.object(delivery, "_threads", return_value=[]), \
                 patch.object(delivery, "remote_identity", return_value="o/r"), \
                 patch.object(delivery, "pushed_head", side_effect=["b" * 40, SHA]), \
                 patch.object(delivery, "source_identity", return_value=DIGEST):
                with self.assertRaises(delivery.DeliveryError):
                    delivery.read_pr(Path("."), URL, **kwargs)

    def test_final_readback_fence_rejects_dynamic_collection_change(self):
        changed = pr_value(comments=[{"id": "c", "author": None, "authorAssociation": "NONE", "body": "x",
                                      "createdAt": None, "includesCreatedEdit": False, "isMinimized": False,
                                      "minimizedReason": None, "reactionGroups": [], "updatedAt": None,
                                      "url": None, "viewerDidAuthor": False}])
        with patch.object(delivery, "_json", side_effect=[pr_value(), changed]), \
             patch.object(delivery, "_required_checks", return_value=([], "none")), \
             patch.object(delivery, "_threads", return_value=[]), \
             patch.object(delivery, "remote_identity", return_value="o/r"), \
             patch.object(delivery, "pushed_head", side_effect=["b" * 40, SHA, "b" * 40, SHA]), \
             patch.object(delivery, "source_identity", side_effect=[DIGEST, DIGEST]):
            with self.assertRaises(delivery.CandidateChanged):
                delivery.read_pr(Path("."), URL, expected_repository="o/r")

    def test_final_readback_fence_rejects_moved_head(self):
        moved = "d" * 40
        final = pr_value(headRefOid=moved)
        with patch.object(delivery, "_json", side_effect=[pr_value(), final]), \
             patch.object(delivery, "_required_checks", return_value=([], "none")), \
             patch.object(delivery, "_threads", return_value=[]), \
             patch.object(delivery, "remote_identity", return_value="o/r"), \
             patch.object(delivery, "pushed_head", side_effect=["b" * 40, SHA, "b" * 40, moved]), \
             patch.object(delivery, "source_identity", side_effect=[DIGEST, DIGEST]):
            with self.assertRaises(delivery.CandidateChanged):
                delivery.read_pr(Path("."), URL, expected_repository="o/r", expected_base="campaign/razr-pi-next",
                                 expected_base_sha="b" * 40, expected_head="feature", expected_head_sha=SHA,
                                 expected_source_digest=DIGEST)

    def test_trailing_fence_rejects_head_move_during_final_checks(self):
        fence = {"number": 1, "url": URL, "state": "OPEN", "isDraft": False,
                 "baseRefName": "campaign/razr-pi-next", "baseRefOid": "b" * 40,
                 "headRefName": "feature", "headRefOid": SHA, "mergeStateStatus": "CLEAN",
                 "mergeable": "MERGEABLE", "reviewDecision": "", "mergedAt": None,
                 "closedAt": None, "updatedAt": "2026-01-01T00:00:00Z"}
        with patch.object(delivery, "_json", side_effect=[pr_value(), pr_value(), fence]), \
             patch.object(delivery, "_required_checks", side_effect=[([], "none"), ([], "none")]), \
             patch.object(delivery, "_threads", side_effect=[[], []]), \
             patch.object(delivery, "remote_identity", return_value="o/r"), \
             patch.object(delivery, "pushed_head", side_effect=["b" * 40, SHA, "b" * 40, SHA, "b" * 40, "d" * 40]), \
             patch.object(delivery, "source_identity", side_effect=[DIGEST, DIGEST, DIGEST]):
            with self.assertRaises(delivery.CandidateChanged):
                delivery.read_pr(Path("."), URL, expected_repository="o/r")

    def test_hostile_gh_host_is_overridden(self):
        with patch.dict(delivery.os.environ, {"GH_HOST": "evil.invalid"}):
            self.assertEqual(delivery._env()["GH_HOST"], "github.com")

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
                                                title="candidate", body="body", expected_repository="o/r",
                                                expected_base_sha="b" * 40, expected_head_sha=SHA,
                                                expected_source_digest=DIGEST), existing)
            run.assert_not_called()
            read.assert_called_once()

    def test_create_rejects_malformed_required_expectations_before_provider(self):
        cases = [{"expected_repository": ""}, {"expected_repository": "not-a-repo"},
                 {"expected_base_sha": ""}, {"expected_base_sha": "bad"},
                 {"expected_head_sha": ""}, {"expected_head_sha": "bad"},
                 {"expected_source_digest": ""}, {"expected_source_digest": "sha256:bad"}]
        for override in cases:
            kwargs = {"expected_repository": "o/r", "expected_base_sha": "b" * 40,
                      "expected_head_sha": SHA, "expected_source_digest": DIGEST}
            kwargs.update(override)
            with self.subTest(override=override), patch.object(delivery, "remote_identity") as remote, \
                 patch.object(delivery, "pushed_head") as heads, patch.object(delivery, "source_identity") as source, \
                 patch.object(delivery, "_json") as query, patch.object(delivery, "_run") as mutate:
                with self.assertRaises(delivery.InvalidRequest):
                    delivery.create_pr(Path("."), base="base", head="feature", title="t", body="b", **kwargs)
                remote.assert_not_called(); heads.assert_not_called(); source.assert_not_called()
                query.assert_not_called(); mutate.assert_not_called()

    def test_create_source_mismatch_prevents_mutation(self):
        with patch.object(delivery, "remote_identity", return_value="o/r"), \
             patch.object(delivery, "pushed_head", side_effect=["b" * 40, SHA]), \
             patch.object(delivery, "source_identity", return_value="sha256:" + "d" * 64), \
             patch.object(delivery, "_json") as query, patch.object(delivery, "_run") as mutate:
            with self.assertRaises(delivery.IdentityMismatch):
                delivery.create_pr(Path("."), base="base", head="feature", title="t", body="b",
                                   expected_repository="o/r", expected_base_sha="b" * 40,
                                   expected_head_sha=SHA, expected_source_digest=DIGEST)
            query.assert_not_called()
            mutate.assert_not_called()

    def test_create_refuses_remote_expectation_override(self):
        with patch.object(delivery, "remote_identity", return_value="o/r"):
            with self.assertRaisesRegex(delivery.DeliveryError, "repository expectation"):
                delivery.create_pr(Path("."), base="base", head="feature", title="t", body="b",
                                   expected_repository="other/repo", expected_base_sha="b" * 40,
                                   expected_head_sha=SHA, expected_source_digest=DIGEST)

    def test_create_recovers_one_racing_server_pr(self):
        with patch.object(delivery, "remote_identity", return_value="o/r"), \
             patch.object(delivery, "pushed_head", side_effect=["b" * 40, SHA, "b" * 40, SHA]), \
             patch.object(delivery, "source_identity", side_effect=[DIGEST, DIGEST]), \
             patch.object(delivery, "_json", side_effect=[[], [{"url": URL}]]), \
             patch.object(delivery, "_run", side_effect=delivery.DeliveryError("create failed")), \
             patch.object(delivery, "read_pr", return_value={"number": 1}):
            self.assertEqual(delivery.create_pr(Path("."), base="base", head="feature", title="t", body="b",
                                                 expected_repository="o/r", expected_base_sha="b" * 40,
                                                 expected_head_sha=SHA, expected_source_digest=DIGEST)["number"], 1)

    def test_create_refuses_branch_movement_before_mutation(self):
        moved = "d" * 40
        with patch.object(delivery, "remote_identity", return_value="o/r"), \
             patch.object(delivery, "pushed_head", side_effect=["b" * 40, SHA, "b" * 40, moved]), \
             patch.object(delivery, "source_identity", side_effect=[DIGEST, DIGEST]), \
             patch.object(delivery, "_json", return_value=[]), patch.object(delivery, "_run") as mutate:
            with self.assertRaises(delivery.CandidateChanged):
                delivery.create_pr(Path("."), base="base", head="feature", title="t", body="b",
                                   expected_repository="o/r", expected_base_sha="b" * 40,
                                   expected_head_sha=SHA, expected_source_digest=DIGEST)
            mutate.assert_not_called()

    def test_new_candidate_identity_mismatch_is_compensated(self):
        with patch.object(delivery, "remote_identity", return_value="o/r"), \
             patch.object(delivery, "pushed_head", side_effect=["b" * 40, SHA, "b" * 40, SHA]), \
             patch.object(delivery, "source_identity", side_effect=[DIGEST, DIGEST]), \
             patch.object(delivery, "_json", return_value=[]), patch.object(delivery, "_run", return_value=URL), \
             patch.object(delivery, "read_pr", side_effect=delivery.IdentityMismatch("PR head commit mismatch")), \
             patch.object(delivery, "_close_created", side_effect=delivery.DeliveryError("close failed")) as close:
            with self.assertRaises(delivery.IdentityMismatch) as raised:
                delivery.create_pr(Path("."), base="base", head="feature", title="t", body="b",
                                   expected_repository="o/r", expected_base_sha="b" * 40,
                                   expected_head_sha=SHA, expected_source_digest=DIGEST)
            self.assertIn("uncertain", str(raised.exception))
            close.assert_called_once()

    def test_general_post_create_failure_is_compensated(self):
        with patch.object(delivery, "remote_identity", return_value="o/r"), \
             patch.object(delivery, "pushed_head", side_effect=["b" * 40, SHA, "b" * 40, SHA]), \
             patch.object(delivery, "source_identity", side_effect=[DIGEST, DIGEST]), \
             patch.object(delivery, "_json", return_value=[]), patch.object(delivery, "_run", return_value=URL), \
             patch.object(delivery, "read_pr", side_effect=delivery.DeliveryError("checks unavailable")), \
             patch.object(delivery, "_close_created", return_value="confirmed") as close:
            with self.assertRaisesRegex(delivery.DeliveryError, "compensation confirmed"):
                delivery.create_pr(Path("."), base="base", head="feature", title="t", body="b",
                                   expected_repository="o/r", expected_base_sha="b" * 40,
                                   expected_head_sha=SHA, expected_source_digest=DIGEST)
            close.assert_called_once()

    def test_malformed_create_output_is_explicitly_unverified(self):
        with patch.object(delivery, "remote_identity", return_value="o/r"), \
             patch.object(delivery, "pushed_head", side_effect=["b" * 40, SHA, "b" * 40, SHA]), \
             patch.object(delivery, "source_identity", side_effect=[DIGEST, DIGEST]), \
             patch.object(delivery, "_json", return_value=[]), patch.object(delivery, "_run", return_value="not-a-url") as run:
            with self.assertRaisesRegex(delivery.DeliveryError, "created-but-unverified"):
                delivery.create_pr(Path("."), base="base", head="feature", title="t", body="b",
                                   expected_repository="o/r", expected_base_sha="b" * 40,
                                   expected_head_sha=SHA, expected_source_digest=DIGEST)
            self.assertEqual(run.call_count, 1)

    def test_create_transport_failure_is_explicit_when_recovery_is_missing_or_fails(self):
        common = dict(base="base", head="feature", title="t", body="b", expected_repository="o/r",
                      expected_base_sha="b" * 40, expected_head_sha=SHA, expected_source_digest=DIGEST)
        with patch.object(delivery, "remote_identity", return_value="o/r"), \
             patch.object(delivery, "pushed_head", side_effect=["b" * 40, SHA, "b" * 40, SHA]), \
             patch.object(delivery, "source_identity", side_effect=[DIGEST, DIGEST]), \
             patch.object(delivery, "_json", return_value=[]), \
             patch.object(delivery, "_run", side_effect=delivery.DeliveryError("create timeout")):
            with self.assertRaisesRegex(delivery.DeliveryError, "created-but-unverified.*unavailable"):
                delivery.create_pr(Path("."), **common)
        with patch.object(delivery, "remote_identity", return_value="o/r"), \
             patch.object(delivery, "pushed_head", side_effect=["b" * 40, SHA, "b" * 40, SHA]), \
             patch.object(delivery, "source_identity", side_effect=[DIGEST, DIGEST]), \
             patch.object(delivery, "_json", side_effect=[[], delivery.DeliveryError("lookup timeout")]), \
             patch.object(delivery, "_run", side_effect=delivery.DeliveryError("create timeout")):
            with self.assertRaisesRegex(delivery.DeliveryError, "created-but-unverified.*uncertain"):
                delivery.create_pr(Path("."), **common)

    def test_wrong_repository_create_output_recovers_exact_candidate(self):
        recovered = {"number": 1}
        with patch.object(delivery, "remote_identity", return_value="o/r"), \
             patch.object(delivery, "pushed_head", side_effect=["b" * 40, SHA, "b" * 40, SHA]), \
             patch.object(delivery, "source_identity", side_effect=[DIGEST, DIGEST]), \
             patch.object(delivery, "_json", side_effect=[[], [{"url": URL}]]), \
             patch.object(delivery, "_run", return_value="https://github.com/other/r/pull/99"), \
             patch.object(delivery, "read_pr", return_value=recovered):
            self.assertIs(delivery.create_pr(Path("."), base="base", head="feature", title="t", body="b",
                                             expected_repository="o/r", expected_base_sha="b" * 40,
                                             expected_head_sha=SHA, expected_source_digest=DIGEST), recovered)

    def test_new_mismatched_pr_is_closed_but_race_recovery_is_not(self):
        with patch.object(delivery, "remote_identity", return_value="o/r"), \
             patch.object(delivery, "pushed_head", side_effect=["b" * 40, SHA, "b" * 40, SHA]), \
             patch.object(delivery, "source_identity", side_effect=[DIGEST, DIGEST]), \
             patch.object(delivery, "_json", side_effect=[[],
                 {"number": 1, "url": URL, "state": "OPEN", "baseRefName": "base", "headRefName": "feature",
                  "baseRefOid": "b" * 40, "headRefOid": SHA},
                 {"number": 1, "url": URL, "state": "CLOSED"}]), \
             patch.object(delivery, "_run", side_effect=[URL, ""]) as run, \
             patch.object(delivery, "read_pr", side_effect=delivery.CandidateChanged("moved")):
            with self.assertRaises(delivery.CandidateChanged):
                delivery.create_pr(Path("."), base="base", head="feature", title="t", body="b",
                                   expected_repository="o/r", expected_base_sha="b" * 40,
                                   expected_head_sha=SHA, expected_source_digest=DIGEST)
            self.assertIn("close", run.call_args_list[-1].args[1])
            self.assertNotIn("--delete-branch", run.call_args_list[-1].args[1])

    def test_create_refuses_ambiguous_existing_prs(self):
        with patch.object(delivery, "remote_identity", return_value="o/r"), \
             patch.object(delivery, "pushed_head", side_effect=["b" * 40, SHA]), \
             patch.object(delivery, "source_identity", return_value=DIGEST), \
             patch.object(delivery, "_json", return_value=[{"url": URL}, {"url": URL.replace("/1", "/2")}]):
            with self.assertRaisesRegex(delivery.DeliveryError, "ambiguous"):
                delivery.create_pr(Path("."), base="campaign/razr-pi-next", head="feature",
                                   title="candidate", body="body", expected_repository="o/r",
                                   expected_base_sha="b" * 40, expected_head_sha=SHA,
                                   expected_source_digest=DIGEST)

    def test_required_check_states_are_truthful(self):
        cases = [("", "none", 0), ('[{"name":"ci","state":"SUCCESS","bucket":"pass"}]', "passing", 0),
                 ('[{"name":"ci","state":"QUEUED","bucket":"PENDING"}]', "pending", 8),
                 ('[{"name":"ci","state":"COMPLETED","bucket":"FAIL"}]', "failing", 1),
                 ('[{"name":"ci","state":"COMPLETED","bucket":"PASS"}]', "passing", 0),
                 ('[{"name":"ci","state":"SKIPPED","bucket":"skipping"}]', "skipping", 0),
                 ('[{"name":"ci","state":"CANCELLED","bucket":"cancel"}]', "failing", 0)]
        for raw, expected, code in cases:
            with self.subTest(expected=expected), patch.object(delivery, "_run_result", return_value=(raw, "", code)):
                self.assertEqual(delivery._required_checks(Path("."), URL, "o/r")[1], expected)

    def test_required_check_accepts_shipped_state_vocabulary(self):
        for state, bucket, code, expected in (
                ("SKIPPED", "skipping", 0, "skipping"), ("NEUTRAL", "pass", 0, "passing"),
                ("CANCELLED", "cancel", 0, "failing"), ("TIMED_OUT", "fail", 1, "failing"),
                ("ACTION_REQUIRED", "fail", 1, "failing"), ("STALE", "pending", 8, "pending"),
                ("STARTUP_FAILURE", "fail", 1, "failing")):
            raw = json.dumps([{"name": "ci", "state": state, "bucket": bucket}])
            with self.subTest(state=state), patch.object(delivery, "_run_result", return_value=(raw, "", code)):
                self.assertEqual(delivery._required_checks(Path("."), URL, "o/r")[1], expected)

    def test_required_check_mixed_failure_and_pending_keeps_all_rows(self):
        raw = json.dumps([
            {"name": "failed", "state": "FAILURE", "bucket": "FAIL"},
            {"name": "running", "state": "IN_PROGRESS", "bucket": "PENDING"},
        ])
        with patch.object(delivery, "_run_result", return_value=(raw, "", 1)):
            rows, status = delivery._required_checks(Path("."), URL, "o/r")
        self.assertEqual(status, "failing")
        self.assertEqual([row["name"] for row in rows], ["failed", "running"])

    def test_required_check_exit_one_requires_exact_no_checks_diagnostic(self):
        for diagnostic in ("no checks reported on the 'feature' branch\n", "no required checks reported on the 'feature' branch\n"):
            with patch.object(delivery, "_run_result", return_value=("", diagnostic, 1)):
                self.assertEqual(delivery._required_checks(Path("."), URL, "o/r")[1], "none")
        failing = '[{"name":"ci","state":"FAILURE","bucket":"fail"}]'
        with patch.object(delivery, "_run_result", return_value=(failing, "", 1)):
            self.assertEqual(delivery._required_checks(Path("."), URL, "o/r")[1], "failing")
        with patch.object(delivery, "_run_result", return_value=("", "permission denied\n", 1)):
            with self.assertRaises(delivery.DeliveryError):
                delivery._required_checks(Path("."), URL, "o/r")

    def test_required_check_rejects_unsupported_and_contradictory_exits(self):
        passing = '[{"name":"ci","state":"SUCCESS","bucket":"pass"}]'
        for code, stderr in ((2, "fatal provider error"), (128, "permission denied"), (8, "fatal provider error")):
            with self.subTest(code=code), patch.object(delivery, "_run_result", return_value=(passing, stderr, code)):
                with self.assertRaises(delivery.DeliveryError):
                    delivery._required_checks(Path("."), URL, "o/r")

    def test_required_check_schema_rejects_unknown_fields(self):
        with patch.object(delivery, "_run_result", return_value=('[{"name":"ci","state":"COMPLETED","bucket":"PASS","x":1}]', "", 0)):
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

    def test_selector_setup_failures_cleanup_child(self):
        script = "import time; time.sleep(10)"
        with patch.object(delivery.selectors, "DefaultSelector", side_effect=RuntimeError("selector")):
            with self.assertRaises(delivery.DeliveryError):
                delivery._capture(sys.executable, ["-c", script], Path("."), timeout=.2)
        class BrokenSelector:
            def register(self, *_args):
                raise RuntimeError("register")
        with patch.object(delivery.selectors, "DefaultSelector", return_value=BrokenSelector()):
            with self.assertRaises(delivery.DeliveryError):
                delivery._capture(sys.executable, ["-c", script], Path("."), timeout=.2)

    def test_streaming_overflow_timeout_and_process_group_cleanup(self):
        script = "import sys; sys.stderr.write('credential-secret'); sys.stdout.write('x'*100); sys.stdout.flush()"
        with patch.object(delivery, "MAX_STDOUT", 10):
            with self.assertRaises(delivery.DeliveryError):
                delivery._capture(sys.executable, ["-c", script], Path("."))
        with tempfile.TemporaryDirectory() as directory:
            pidfile = Path(directory) / "child.pid"
            script = ("import os,subprocess,sys,time; child=subprocess.Popen([sys.executable,'-c',"
                      "'import os,sys,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); open(sys.argv[1],\\\"w\\\").write(str(os.getpid())); time.sleep(10)',"
                      "'" + str(pidfile) + "']); os.write(1,b'x'); time.sleep(10)")
            started = time.monotonic()
            with self.assertRaisesRegex(delivery.DeliveryError, "timed out"):
                delivery._capture(sys.executable, ["-c", script], Path("."), timeout=.1)
            self.assertLess(time.monotonic() - started, 2.0)
            for _ in range(50):
                if pidfile.exists() and pidfile.read_text():
                    try:
                        os.kill(int(pidfile.read_text()), 0)
                    except ProcessLookupError:
                        break
                time.sleep(.02)
            if pidfile.exists() and pidfile.read_text():
                try:
                    os.kill(int(pidfile.read_text()), 0)
                except ProcessLookupError:
                    pass
                else:
                    self.fail("surviving descendant after timeout cleanup")
        with tempfile.TemporaryDirectory() as directory:
            pidfile = Path(directory) / "success-child.pid"
            script = ("import os,sys,subprocess; subprocess.Popen([sys.executable,'-c',"
                      "'import os,sys,time; os.close(1); os.close(2); open(sys.argv[1],\\\"w\\\").write(str(os.getpid())); time.sleep(10)',"
                      "'" + str(pidfile) + "'])")
            with self.assertRaises(delivery.DeliveryError):
                delivery._capture(sys.executable, ["-c", script], Path("."), timeout=.5)
            for _ in range(20):
                if pidfile.exists() and pidfile.read_text():
                    break
                time.sleep(.01)
            if pidfile.exists() and pidfile.read_text():
                pid = int(pidfile.read_text())
                for _ in range(50):
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(.02)
                else:
                    self.fail("surviving descendant after successful leader cleanup")

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
        valid_read = '{"schema_version":1,"operation":"read","url":"https://github.com/o/r/pull/1"}'
        for error, code in ((cli.delivery.CandidateChanged("moved"), "CANDIDATE_CHANGED"),
                            (cli.delivery.IdentityMismatch("identity"), "IDENTITY_MISMATCH"),
                            (cli.delivery.DeliveryError("network"), "PROVIDER_UNAVAILABLE")):
            with self.subTest(code=code), patch.object(cli.delivery, "auth_preflight"), \
                 patch.object(cli.delivery, "read_pr", side_effect=error), patch.object(sys, "stdin", io.StringIO(valid_read)), \
                 patch.object(sys, "stdout", io.StringIO()) as stream:
                self.assertEqual(cli.main(), 1)
                self.assertIn('"code": "' + code + '"', stream.getvalue())

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
             patch.object(delivery, "pushed_head", side_effect=["b" * 40, SHA, "b" * 40, SHA]), \
             patch.object(delivery, "source_identity", side_effect=[DIGEST, DIGEST]), \
             patch.object(delivery, "_json", return_value=[]), \
             patch.object(delivery, "_run", return_value=URL) as run, \
             patch.object(delivery, "read_pr", return_value=pr_value()):
            delivery.create_pr(Path("."), base="campaign/razr-pi-next", head="feature", title="t", body="b",
                               expected_repository="o/r", expected_base_sha="b" * 40,
                               expected_head_sha=SHA, expected_source_digest=DIGEST)
            args = run.call_args.args[1]
            self.assertIn("--repo", args)
            self.assertIn("github.com/o/r", args)
            for prohibited in ("--force", "--merge", "--auto", "--delete-branch", "--admin", "--squash", "--rebase", "--deploy"):
                self.assertNotIn(prohibited, args)


if __name__ == "__main__":
    unittest.main()
