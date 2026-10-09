from __future__ import annotations

import hashlib
import dataclasses
import json
import os
from pathlib import Path
import subprocess
import sys
import shutil
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages/razr-pi"))

from adapter.core_subprocess import (_environment, _git_head, _readback, _reject_dirty_checkout,
                                      _source_digest, _extract_work_root, _workflow_projection, invoke_core)
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

    def test_full_porcelain_dirty_and_head_identity_policy(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory)
            env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                   "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                   "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
            subprocess.run(["git", "init", "-q", str(root)], check=True, env=env)
            (root / "tracked").write_text("one")
            subprocess.run(["git", "-C", str(root), "add", "tracked"], check=True, env=env)
            subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "fixture"], check=True, env=env)
            head = _git_head(root)
            self.assertEqual(len(head), 40)
            (root / "source.py").write_text("untracked")
            with self.assertRaises(ProtocolError) as caught:
                _reject_dirty_checkout(root, ".razr/work")
            self.assertEqual(caught.exception.code, "SOURCE_CHANGED")
            (root / "source.py").unlink()
            (root / ".razr/work").mkdir(parents=True)
            (root / ".razr/work/cache").write_text("owned")
            _reject_dirty_checkout(root, ".razr/work")
            (root / "tracked").write_text("dirty")
            with self.assertRaises(ProtocolError):
                _reject_dirty_checkout(root, ".razr/work")

    def test_core_config_shape_is_closed_and_typed(self):
        valid = {"schema_version": 1, "work_root": ".razr/work", "retention": "commit",
                 "adapter": {"mode": "process-local", "max_active_work_items": 5,
                 "queue_order": ["priority", "approved_at", "id"]}}
        self.assertEqual(_extract_work_root(valid), ".razr/work")
        for bad in ({"schema_version": 1}, {**valid, "work_root": "../escape"},
                    {**valid, "work_root": 7}, {**valid, "work_root": ""}):
            with self.assertRaises(ValueError):
                _extract_work_root(bad)

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
        projected = _workflow_projection(row, state, "work", "node", raw, response, ROOT, "r")
        self.assertEqual(projected.raw_state_digest, raw.digest)
        self.assertEqual(projected.raw_state_bytes, 12)
        self.assertEqual(projected.raw_state_root, ROOT)
        row.pop("candidate")
        with self.assertRaises(ProtocolError):
            _workflow_projection(row, state, "work", "node", raw, response, ROOT, "r")

    def test_status_validate_raw_projection_uses_internal_state_path(self):
        state = {"schema_version": 1, "id": "work", "stage": "build", "status": "active",
                 "generation": 2, "created_at": "2026-01-01T00:00:00+00:00", "depth": "tiny",
                 "priority": 4, "approved_at": None, "spec": {}, "executable": {},
                 "nodes": {"node": {"status": "running", "attempt": 1, "result_ref": None}},
                 "trajectory": {}, "candidate": {"repository": None, "branch": None,
                 "commit": None, "source_digest": None}, "critics": {},
                 "delivery": {"pull_request": None}, "last_transition": {}}
        state_bytes = (json.dumps(state, separators=(",", ":")) + "\n").encode()
        status = CoreResponse("r-status", 0, b'[{"id":"work","stage":"build","status":"active","depth":"tiny","priority":4,"approved_at":null,"candidate":null,"pull_request":null}]', b"", 1, "sha256:" + "b" * 64, (), "sha256:" + "c" * 64, ())
        validation = CoreResponse("r-validate", 0, b"PASS work\n", b"", 1, status.core_identity, (), status.source_digest, ())
        raw = ReadbackResult(".razr/work/work/state.yaml", "raw_file", raw_file_digest(state_bytes), len(state_bytes), None, state_bytes)
        state_response = CoreResponse("r-state", 0, b"", b"", 1, status.core_identity, (), status.source_digest, (raw,))
        request = CoreRequest("r", ROOT, (), ROOT, ROOT / "packages/razr-pi", Path(__import__("sys").executable).resolve(),
                              ROOT / "packages/razr-pi/runtime/scripts/razr.py", "0" * 40, status.source_digest,
                              status.core_identity, (), (), 1, 1, 1, 1)
        with patch("adapter.core_subprocess._core_work_root", return_value=".razr/work"), patch(
                "adapter.core_subprocess.invoke_core", side_effect=[status, validation, state_response]) as called:
            result = __import__("adapter.core_subprocess", fromlist=["read_workflow"]).read_workflow(request, "work", "node")
        self.assertEqual(called.call_count, 3)
        self.assertEqual([call.args[0].request_id for call in called.call_args_list], ["r-status", "r-validate", "r-state"])
        self.assertEqual(called.call_args_list[2].args[0].expected_readback[0].path, ".razr/work/work/state.yaml")
        self.assertEqual(result.raw_state_digest, raw.digest)
        self.assertEqual(result.raw_state_bytes, len(state_bytes))
        with patch("adapter.core_subprocess.invoke_core", return_value=dataclasses.replace(status, request_id="wrong")):
            with self.assertRaises(ProtocolError) as caught:
                __import__("adapter.core_subprocess", fromlist=["read_workflow"]).read_workflow(request, "work", "node")
            self.assertEqual(caught.exception.code, "IDENTITY_MISMATCH")
        with patch("adapter.core_subprocess.invoke_core") as never:
            with self.assertRaises(ProtocolError):
                __import__("adapter.core_subprocess", fromlist=["read_workflow"]).read_workflow(request, "../bad", "node")
            never.assert_not_called()

    def test_actual_subprocess_limits_exit_timeout_and_signal_codes(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            root = Path(directory) / "repo"; root.mkdir()
            package = Path(directory) / "package"
            shutil.copytree(ROOT / "packages/razr-pi", package)
            script = package / "runtime/scripts/razr.py"
            script.write_text("import os, signal, sys, time\nmode=sys.argv[1]\nif mode == 'timeout': time.sleep(2)\nelif mode == 'stdout': print('x' * 100)\nelif mode == 'stderr': print('x' * 100, file=sys.stderr)\nelif mode == 'exit': sys.exit(7)\nelif mode == 'signal': os.kill(os.getpid(), signal.SIGTERM)\nelif mode == 'malformed': print('not-json')\n")
            declaration_path = package / "runtime/provenance.json"
            declaration = json.loads(declaration_path.read_bytes())
            declaration["files"][0]["sha256"] = __import__("hashlib").sha256(script.read_bytes()).hexdigest()
            declaration_path.write_text(json.dumps(declaration) + "\n")
            env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                   "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
                   "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid"}
            subprocess.run(["git", "init", "-q", str(root)], check=True, env=env)
            config = {"schema_version": 1, "work_root": ".razr/work", "retention": "commit",
                      "adapter": {"mode": "process-local", "max_active_work_items": 5,
                      "queue_order": ["priority", "approved_at", "id"]}}
            (root / "razr.yaml").write_text(json.dumps(config) + "\n")
            subprocess.run(["git", "-C", str(root), "add", "razr.yaml"], check=True, env=env)
            subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "fixture"], check=True, env=env)
            commit = _git_head(root)
            pins = tuple(ProvenancePin(row["path"], row["sha256"]) for row in declaration["files"])
            def request(mode, timeout=1000, stdout=1_048_576, stderr=262_144):
                return CoreRequest("run-" + mode, root, (mode,), root, package,
                    Path(sys.executable).resolve(), script.resolve(), commit, _source_digest(root, commit),
                    raw_file_digest(declaration_path.read_bytes()), pins, (), timeout, 100, stdout, stderr)
            forbidden = {name: os.environ.pop(name) for name in list(os.environ)
                         if name.startswith("GIT_") and name not in {"GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL", "GIT_OPTIONAL_LOCKS", "GIT_TERMINAL_PROMPT"}}
            try:
                for mode, code in (("timeout", "TIMEOUT"), ("stdout", "OUTPUT_LIMIT"),
                                   ("stderr", "OUTPUT_LIMIT"), ("exit", "CORE_EXIT"), ("signal", "INTERRUPTED")):
                    kwargs = ({"timeout": 5000, "stdout": 10, "stderr": 10} if mode in {"stdout", "stderr"}
                              else {} if mode == "timeout" else {"timeout": 5000})
                    with self.assertRaises(ProtocolError) as caught:
                        invoke_core(request(mode, **kwargs))
                    self.assertEqual(caught.exception.code, code)
                response = invoke_core(request("malformed", timeout=5000))
                self.assertEqual(response.stdout, b"not-json\n")
                duplicate = dataclasses.replace(request("malformed"), expected_provenance=pins + (pins[0],))
                with self.assertRaises(ProtocolError) as caught:
                    invoke_core(duplicate)
                self.assertEqual(caught.exception.code, "PROVENANCE_MISMATCH")
                bad_pin = dataclasses.replace(pins[0], sha256="0" * 64)
                mismatch = dataclasses.replace(request("malformed"), expected_provenance=(bad_pin,) + pins[1:])
                with self.assertRaises(ProtocolError) as caught:
                    invoke_core(mismatch)
                self.assertEqual(caught.exception.code, "PROVENANCE_MISMATCH")
            finally:
                os.environ.update(forbidden)

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
        substitute = dataclasses.replace(request, runtime_script=(package / "runtime/AGENTS.project-razr.md").resolve())
        forbidden = {name: os.environ.pop(name) for name in list(os.environ)
                     if name.startswith("GIT_") and name not in {"GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL", "GIT_OPTIONAL_LOCKS", "GIT_TERMINAL_PROMPT"}}
        try:
            with self.assertRaises(ProtocolError) as substituted:
                invoke_core(substitute)
        finally:
            os.environ.update(forbidden)
        self.assertEqual(substituted.exception.code, "INVALID_REQUEST")


if __name__ == "__main__":
    unittest.main()
