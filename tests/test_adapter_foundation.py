from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages/razr-pi"))

from adapter.core_subprocess import (_environment, _readback, _source_digest, _validate_core_config,
                                      _workflow_projection, invoke_core)
from adapter.protocol import (CoreRequest, CoreResponse, ProtocolError, ProvenancePin, ReadbackResult,
                              ReadbackSpec, canonical_json, raw_file_digest, validate_git_object_id,
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
            (fixture / "razr.yaml").write_text(json.dumps({"schema_version": 1, "work_root": ".razr/work",
                "retention": "commit", "adapter": {"mode": "process-local",
                "max_active_work_items": 5, "queue_order": ["priority", "approved_at", "id"]}}) + "\n")
            subprocess.run(["git", "-C", str(fixture), "add", "input.txt", "razr.yaml"], check=True, env=env)
            subprocess.run(["git", "-C", str(fixture), "commit", "-q", "-m", "fixture"], check=True, env=env)
            commit = subprocess.check_output(["git", "-C", str(fixture), "rev-parse", "HEAD"], env=env, text=True).strip()
            request = CoreRequest(
                request_id="adapter-test", repository_root=fixture, cwd=fixture,
                package_root=package, python_executable=Path(sys.executable).resolve(),
                runtime_script=(package / "runtime/scripts/razr.py").resolve(), argv=("--help",),
                expected_commit=commit,
                expected_source_digest=_source_digest(fixture, commit),
                expected_core_identity=raw_file_digest((package / "runtime/provenance.json").read_bytes()),
                expected_provenance=pins, expected_readback=(), timeout_ms=120_000,
                termination_grace_ms=5_000, max_stdout_bytes=1_048_576, max_stderr_bytes=262_144)
            forbidden = {name: os.environ.pop(name) for name in list(os.environ)
                         if name.startswith("GIT_") and name not in {"GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL", "GIT_OPTIONAL_LOCKS", "GIT_TERMINAL_PROMPT"}}
            try:
                response = invoke_core(request)
            finally:
                os.environ.update(forbidden)
            self.assertEqual(response.request_id, request.request_id)
            self.assertEqual(response.returncode, 0)
            self.assertEqual(response.source_digest, request.expected_source_digest)
            self.assertEqual(response.readback, ())

    def test_environment_is_allowlisted_and_strips_ambient_git(self):
        os.environ["GIT_DIR"] = "/ambient"
        try:
            env = _environment()
        finally:
            os.environ.pop("GIT_DIR", None)
        self.assertNotIn("GIT_DIR", env)
        self.assertNotIn("PYTHONPATH", env)
        self.assertEqual(env["GIT_CONFIG_NOSYSTEM"], "1")

    def test_core_config_shape_is_closed_and_typed(self):
        valid = {"schema_version": 1, "work_root": ".razr/work", "retention": "commit",
                 "adapter": {"mode": "process-local", "max_active_work_items": 5,
                 "queue_order": ["priority", "approved_at", "id"]}}
        self.assertEqual(_validate_core_config(valid), ".razr/work")
        for bad in ({**valid, "extra": 1}, {**valid, "schema_version": True},
                    {**valid, "work_root": "../escape"},
                    {**valid, "adapter": {"mode": "process-local"}}):
            with self.assertRaises(ValueError):
                _validate_core_config(bad)

    def test_raw_readback_preserves_bytes_and_rejects_malformed_json(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            (root / "state.yaml").write_bytes(b"{bad\n")
            raw = _readback(root, (ReadbackSpec("state.yaml", True, 100, "bytes"),))[0]
            self.assertEqual(raw.digest_domain, "raw_file")
            self.assertEqual(raw.bytes, 5)
            self.assertEqual(raw.raw_bytes, b"{bad\n")
            self.assertIsNone(raw.parsed)
            with self.assertRaises(ProtocolError) as caught:
                _readback(root, (ReadbackSpec("state.yaml", True, 100, "json"),))
            self.assertEqual(caught.exception.code, "MALFORMED_RESPONSE")

    def test_workflow_projection_keeps_raw_identity_and_checks_closed_fields(self):
        state = {"schema_version": 1, "id": "work", "stage": "build", "status": "active",
                 "generation": 2, "created_at": "2026-01-01T00:00:00+00:00", "depth": "tiny",
                 "priority": 4, "approved_at": None, "spec": {}, "executable": {},
                 "nodes": {"node": {"status": "running", "attempt": 1, "result_ref": None}},
                 "trajectory": {}, "candidate": {"repository": None, "branch": None,
                 "commit": None, "source_digest": None}, "critics": {},
                 "delivery": {"pull_request": None}, "last_transition": {}}
        row = {"id": "work", "stage": "build", "status": "active", "depth": "tiny",
               "priority": 4, "approved_at": None, "candidate": None, "pull_request": None}
        raw = ReadbackResult(".razr/work/work/state.yaml", "raw_file", "sha256:" + "a" * 64, 12, None, b"raw-state!!")
        response = CoreResponse("r", 0, b"", b"", 1, "sha256:" + "b" * 64, (), "sha256:" + "c" * 64, (raw,))
        projected = _workflow_projection(row, state, "work", "node", raw, response, ROOT)
        self.assertEqual(projected.raw_state_digest, raw.digest)
        self.assertEqual(projected.raw_state_bytes, 12)
        self.assertEqual(projected.raw_state_root, ROOT)
        row.pop("candidate")
        with self.assertRaises(ProtocolError):
            _workflow_projection(row, state, "work", "node", raw, response, ROOT)

    def test_status_validate_raw_projection_uses_internal_state_path(self):
        state = {"schema_version": 1, "id": "work", "stage": "build", "status": "active",
                 "generation": 2, "created_at": "2026-01-01T00:00:00+00:00", "depth": "tiny",
                 "priority": 4, "approved_at": None, "spec": {}, "executable": {},
                 "nodes": {"node": {"status": "running", "attempt": 1, "result_ref": None}},
                 "trajectory": {}, "candidate": {"repository": None, "branch": None,
                 "commit": None, "source_digest": None}, "critics": {},
                 "delivery": {"pull_request": None}, "last_transition": {}}
        state_bytes = (json.dumps(state, separators=(",", ":")) + "\n").encode()
        status = CoreResponse("r", 0, b'[{"id":"work","stage":"build","status":"active","depth":"tiny","priority":4,"approved_at":null,"candidate":null,"pull_request":null}]', b"", 1, "sha256:" + "b" * 64, (), "sha256:" + "c" * 64, ())
        validation = CoreResponse("r", 0, b"PASS work\n", b"", 1, status.core_identity, (), status.source_digest, ())
        raw = ReadbackResult(".razr/work/work/state.yaml", "raw_file", raw_file_digest(state_bytes), len(state_bytes), None, state_bytes)
        state_response = CoreResponse("r", 0, b"", b"", 1, status.core_identity, (), status.source_digest, (raw,))
        request = CoreRequest("r", ROOT, (), ROOT, ROOT / "packages/razr-pi", Path(__import__("sys").executable).resolve(),
                              ROOT / "packages/razr-pi/runtime/scripts/razr.py", "0" * 40, status.source_digest,
                              status.core_identity, (), (), 1, 1, 1, 1)
        with patch("adapter.core_subprocess._core_work_root", return_value=".razr/work"), patch(
                "adapter.core_subprocess.invoke_core", side_effect=[status, validation, state_response]) as called:
            result = __import__("adapter.core_subprocess", fromlist=["read_workflow"]).read_workflow(request, "work", "node")
        self.assertEqual(called.call_count, 3)
        self.assertEqual(called.call_args_list[2].args[0].expected_readback[0].path, ".razr/work/work/state.yaml")
        self.assertEqual(result.raw_state_digest, raw.digest)
        self.assertEqual(result.raw_state_bytes, len(state_bytes))
        with patch("adapter.core_subprocess.invoke_core") as never:
            with self.assertRaises(ProtocolError):
                __import__("adapter.core_subprocess", fromlist=["read_workflow"]).read_workflow(request, "../bad", "node")
            never.assert_not_called()

    def test_provenance_refusal_precedes_launch(self):
        package = ROOT / "packages/razr-pi"
        request = CoreRequest(
            request_id="adapter-test", repository_root=ROOT, cwd=ROOT,
            package_root=package, python_executable=Path(sys.executable).resolve(),
            runtime_script=(package / "runtime/scripts/razr.py").resolve(), argv=("--help",),
            expected_commit="0" * 40,
            expected_source_digest="sha256:" + "0" * 64,
            expected_core_identity="sha256:" + "0" * 64, expected_provenance=(), expected_readback=(),
            timeout_ms=1, termination_grace_ms=1, max_stdout_bytes=1, max_stderr_bytes=1)
        forbidden = {name: os.environ.pop(name) for name in list(os.environ)
                     if name.startswith("GIT_") and name not in {"GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL", "GIT_OPTIONAL_LOCKS", "GIT_TERMINAL_PROMPT"}}
        try:
            with self.assertRaises(ProtocolError) as caught:
                invoke_core(request)
        finally:
            os.environ.update(forbidden)
        self.assertEqual(caught.exception.code, "PROVENANCE_MISMATCH")


if __name__ == "__main__":
    unittest.main()
