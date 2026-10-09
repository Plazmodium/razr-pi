import unittest
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "packages/razr-pi"))
from adapter import github_delivery as delivery


class GithubDeliveryTests(unittest.TestCase):
    def test_remote_and_head_are_strict(self):
        with patch.object(delivery, "_run", side_effect=["git@github.com:Plazmodium/razr-pi.git", "a" * 40 + "  refs/heads/feature\n"]):
            self.assertEqual(delivery.remote_identity(Path(".")), "Plazmodium/razr-pi")
            self.assertEqual(delivery.pushed_head(Path("."), "feature"), "a" * 40)

    def test_snapshot_has_source_identity_and_no_state_write(self):
        with patch.object(delivery, "remote_identity", return_value="o/r"), \
             patch.object(delivery, "pushed_head", return_value="b" * 40), \
             patch.object(delivery, "source_identity", return_value="sha256:" + "c" * 64):
            self.assertEqual(delivery.snapshot(Path("."), branch="feature")["commit"], "b" * 40)

    def test_pr_readback_rejects_short_sha(self):
        with patch.object(delivery, "_json", return_value={"url": "https://github.com/o/r/pull/1", "number": 1,
                "baseRefOid": "x", "headRefOid": "d" * 40, "reviews": [], "comments": [],
                "reviewRequests": [], "statusCheckRollup": []}):
            with self.assertRaises(delivery.DeliveryError):
                delivery.read_pr(Path("."), "https://github.com/o/r/pull/1")

    def test_auth_output_is_not_returned(self):
        with patch.object(delivery, "_run", return_value="secret-looking status") as run:
            self.assertIsNone(delivery.auth_preflight(Path(".")))
            run.assert_called_once_with("gh", ["auth", "status"], Path("."))

    def test_create_is_narrow_and_readback_bound(self):
        with patch.object(delivery, "_run", return_value="https://github.com/o/r/pull/2"), \
             patch.object(delivery, "read_pr", return_value={"number": 2}) as read:
            self.assertEqual(delivery.create_pr(Path("."), base="campaign/razr-pi-next", head="feature",
                                                title="candidate", body="body")["number"], 2)
            args = delivery._run.call_args.args[1]
            self.assertNotIn("--force", args)
            read.assert_called_once()


if __name__ == "__main__":
    unittest.main()
