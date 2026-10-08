from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages/razr-pi"))

from adapter.core_subprocess import _source_digest, invoke_core  # noqa: E402
from adapter.protocol import (CoreRequest, ProtocolError, ProvenancePin, ReadbackSpec,
                              canonical_json, raw_file_digest, validate_git_object_id,
                              validate_source_digest)


class AdapterFoundationTests(unittest.TestCase):
    def test_canonical_vectors_and_digest_domains(self):
        self.assertEqual(canonical_json({"b": 2, "a": [True, "x"]}), b'{"a":[true,"x"],"b":2}')
        self.assertEqual(raw_file_digest(b"{}"), "sha256:" + hashlib.sha256(b"{}").hexdigest())
        with self.assertRaises(ProtocolError):
            canonical_json(float("nan"))
        with self.assertRaises(ProtocolError):
            validate_git_object_id("0" * 39)
        self.assertEqual(raw_file_digest(b"{}"), raw_file_digest(canonical_json({})))
        validate_source_digest("sha256:" + "a" * 64)

    def test_closed_readback_and_provenance_request(self):
        with self.assertRaises(TypeError):
            ReadbackSpec(path="x", required=True, max_bytes=1, parse_as="bytes", extra=True)  # type: ignore[call-arg]
        package = ROOT / "packages/razr-pi"
        declaration = json.loads((package / "runtime/provenance.json").read_bytes())
        pins = tuple(ProvenancePin(row["path"], row["sha256"]) for row in declaration["files"])
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            fixture = Path(directory)
            env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                   "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                   "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
            subprocess.run(["git", "init", "-q", str(fixture)], check=True, env=env)
            (fixture / "input.txt").write_text("fixture\n")
            subprocess.run(["git", "-C", str(fixture), "add", "input.txt"], check=True, env=env)
            subprocess.run(["git", "-C", str(fixture), "commit", "-q", "-m", "fixture"], check=True, env=env)
            request = CoreRequest(
                request_id="adapter-test", repository_root=fixture, cwd=fixture,
                package_root=package, python_executable=Path(sys.executable).resolve(),
                runtime_script=(package / "runtime/scripts/razr.py").resolve(), argv=("--help",),
                expected_source_digest=_source_digest(fixture),
                expected_core_identity=raw_file_digest((package / "runtime/provenance.json").read_bytes()),
                expected_provenance=pins, expected_readback=(), timeout_ms=120_000,
                termination_grace_ms=5_000, max_stdout_bytes=1_048_576, max_stderr_bytes=262_144)
            response = invoke_core(request)
            self.assertEqual(response.request_id, request.request_id)
            self.assertEqual(response.returncode, 0)
            self.assertEqual(response.source_digest, request.expected_source_digest)
            self.assertEqual(response.readback, ())

    def test_provenance_refusal_precedes_launch(self):
        package = ROOT / "packages/razr-pi"
        request = CoreRequest(
            request_id="adapter-test", repository_root=ROOT, cwd=ROOT,
            package_root=package, python_executable=Path(sys.executable).resolve(),
            runtime_script=(package / "runtime/scripts/razr.py").resolve(), argv=("--help",),
            expected_source_digest="sha256:" + "0" * 64,
            expected_core_identity="sha256:" + "0" * 64, expected_provenance=(), expected_readback=(),
            timeout_ms=1, termination_grace_ms=1, max_stdout_bytes=1, max_stderr_bytes=1)
        with self.assertRaises(ProtocolError) as caught:
            invoke_core(request)
        self.assertEqual(caught.exception.code, "PROVENANCE_MISMATCH")


if __name__ == "__main__":
    unittest.main()
