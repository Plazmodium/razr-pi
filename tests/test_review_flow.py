"""General candidate-to-dual-review tracer using the real Razr core."""
import importlib.util
import json
from pathlib import Path
import os
import shutil
import subprocess
import sys
import uuid
from unittest.mock import patch

from test_adoption import FixtureTest
from test_github_delivery import pr_value
from run_public import PACKAGE


SCRIPT = PACKAGE / "scripts/review-flow.py"
SPEC = importlib.util.spec_from_file_location("public_review_flow", SCRIPT)
review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review)
DELIVERY_SPEC = importlib.util.spec_from_file_location("public_github_delivery", PACKAGE / "adapter/github_delivery.py")
delivery = importlib.util.module_from_spec(DELIVERY_SPEC)
DELIVERY_SPEC.loader.exec_module(delivery)


class ReviewFlowTests(FixtureTest):
    def candidate(self, name="review-project", work_root=".razr/work", shell_validation=False,
                  repository="fixture/review-project", work_id="public-probe", branch="main"):
        root = self.repo(name)
        if branch != "main":
            self.invoke([self.tools["git"], "-C", root, "branch", "-m", branch], root)
        (root / "history-base.txt").write_text("committed history base\n")
        (root / ".gitignore").write_text(".ignored-review\n")
        self.invoke([self.tools["git"], "-C", root, "add", "history-base.txt", ".gitignore"], root)
        self.invoke([self.tools["git"], "-C", root, "commit", "-qm", "history base"], root)
        self.adopt(root, work_root)
        self.core(root, "idea", work_id, "--title", "Synthetic review probe", "--depth", "tiny")
        item = root / work_root / work_id
        fixture = self.pair(name + "-pair")
        text = (fixture / "spec.md").read_text().replace("id: public-probe\n", f"id: {work_id}\n")
        text = text.replace("condition: draft\n", "condition: approved\n")
        text = text.replace("approved_by: null\n", "approved_by: SYNTHETIC-test-only\n")
        text = text.replace("approved_at: null\n", "approved_at: 2000-01-01T00:00:00+00:00\n")
        text = text.replace("approval_ref: null\n", "approval_ref: synthetic-fixture-not-human-approval\n")
        if shell_validation:
            text = text.replace("The local Python assertion evaluates two plus two as four.",
                                "The local shell command evaluates two plus two as four.")
        (item / "spec.md").write_text(text)
        executable = json.loads((fixture / "executable.yaml").read_text())
        executable["id"] = work_id
        if shell_validation:
            executable["contract"][0]["statement"] = "The local shell command evaluates two plus two as four."
            executable["nodes"][0]["validation"] = ["/bin/sh -c 'test 4 -eq $((2 + 2))'"]
        import hashlib
        executable["source_spec"]["digest"] = "sha256:" + hashlib.sha256((item / "spec.md").read_bytes()).hexdigest()
        (item / "executable.yaml").write_text(json.dumps(executable, indent=2) + "\n")
        self.core(root, "approve", work_id)
        state = json.loads((item / "state.yaml").read_text())
        node = next(iter(state["nodes"]))
        self.core(root, "transition", work_id, "--node", node, "--node-status", "running",
                  "--expect-generation", str(state["generation"]))
        command = executable["nodes"][0]["validation"][0]
        execution_env = dict(self.env); execution_env["PATH"] += os.pathsep + os.defpath
        executed = subprocess.run(command, cwd=root, env=execution_env, shell=True, capture_output=True, text=True)
        self.assertEqual(executed.returncode, 0, executed.stdout + executed.stderr)
        with (item / "evidence.md").open("a") as handle:
            handle.write(f"\n## public-review-task\n\n- {command} => PASS\n")
        state = json.loads((item / "state.yaml").read_text())
        self.core(root, "transition", work_id, "--node", node, "--node-status", "complete",
                  "--result-ref", "evidence.md#public-review-task", "--expect-generation", str(state["generation"]))
        # Observable test substitute for the first-task human checkpoint:
        # completion and its evidence are read back before candidate preparation.
        checkpoint_state = json.loads((item / "state.yaml").read_text())
        self.assertEqual(checkpoint_state["nodes"][node]["status"], "complete")
        self.assertIn(command + " => PASS", (item / "evidence.md").read_text())
        checkpoint_decision = self.commands / f"{name}-{work_id}-first-task-acceptance.json"
        review.save_new(checkpoint_decision, {"schema_version": 1, "work_id": work_id,
                                              "decision": "SYNTHETIC-accepted-for-no-model-test",
                                              "candidate_preparation_authorized": True})
        self.assertTrue(json.loads(checkpoint_decision.read_text())["candidate_preparation_authorized"])
        (root / "product.txt").write_text("portable review fixture\n")
        (root / work_root / "shared-review-input.md").write_text("committed excluded work-root input\n")
        self.invoke([self.tools["git"], "-C", root, "add", "AGENTS.md", "razr.yaml", "product.txt",
                     str(Path(work_root) / "shared-review-input.md"),
                     str(Path(work_root) / work_id / "spec.md"),
                     str(Path(work_root) / work_id / "executable.yaml"),
                     str(Path(work_root) / work_id / "state.yaml"),
                     str(Path(work_root) / work_id / "evidence.md")], root)
        self.invoke([self.tools["git"], "-C", root, "commit", "-qm", "review candidate"], root)
        commit = self.invoke([self.tools["git"], "-C", root, "rev-parse", "HEAD"], root).stdout.strip()
        base_commit = self.invoke([self.tools["git"], "-C", root, "rev-parse", "HEAD^"], root).stdout.strip()
        self.invoke([self.tools["git"], "-C", root, "tag", "fixture-history-base", base_commit], root)
        self.invoke([self.tools["git"], "-C", root, "tag", "release/HEAD", base_commit], root)
        self.invoke([self.tools["git"], "-C", root, "update-ref", "refs/remotes/upstream/base", base_commit], root)
        self.invoke([self.tools["git"], "-C", root, "symbolic-ref", "refs/remotes/upstream/HEAD",
                     "refs/remotes/upstream/base"], root)
        self.invoke([self.tools["git"], "-C", root, "update-ref", "refs/custom/important", base_commit], root)
        state = json.loads((item / "state.yaml").read_text())
        self.core(root, "candidate", work_id, "--repository", repository,
                  "--branch", branch, "--commit", commit, "--expect-generation", str(state["generation"]))
        state = json.loads((item / "state.yaml").read_text())
        self.core(root, "transition", work_id, "--to", "verify", "--status", "active",
                  "--expect-stage", "build", "--decision", "synthetic-test-candidate",
                  "--expect-generation", str(state["generation"]))
        return root, item

    def report(self, common, common_digest, role, context=None, status="complete", verdict="PASS"):
        context = context or str(uuid.uuid4())
        checks = [{"id": row["id"], "command": row["command"], "exit_code": 0,
                   "summary": "SYNTHETIC test observation"} for row in common["declared_validations"]]
        return {
            "schema_version": 1, "protocol": review.REPORT_PROTOCOL, "role": role, "context": context,
            "identity": review.report_identity(common, common_digest), "review_status": status,
            "verdict": verdict if status == "complete" else None, "reproduced_checks": checks,
            "findings": [], "limitations": ["SYNTHETIC transport; not independent model review."],
            "evidence_access": ["common.json", "candidate source"],
        }

    def fake_pi(self):
        fake = self.lab / "fake-pi.py"
        fake.write_text("""#!/usr/bin/env python3
import json, pathlib, re, subprocess, sys
context = sys.argv[sys.argv.index('--session-id') + 1]
provider = sys.argv[sys.argv.index('--provider') + 1]
model = sys.argv[sys.argv.index('--model') + 1]
session_dir = pathlib.Path(sys.argv[sys.argv.index('--session-dir') + 1])
prompt = sys.argv[-1]
match = re.search(r'in (.+) \\(redacted packet digest (sha256:[0-9a-f]{64}); authoritative common digest (sha256:[0-9a-f]{64})\\)', prompt)
common_path, common_digest = match.group(1), match.group(3)
common = json.loads(pathlib.Path(common_path).read_text())
launch = json.loads((pathlib.Path.cwd().parent / 'launch.json').read_text())
role = launch['role']; events = [{'type': 'session', 'version': 3, 'id': context, 'cwd': str(pathlib.Path.cwd())}]
read_content = [{'type': 'text', 'text': 'read frozen common input'}]
candidate_path = str(pathlib.Path(launch['candidate_view']) / common['source_inventory'][0]['path'])
candidate_content = [{'type': 'text', 'text': 'read candidate source'}]
events.extend([{'type': 'tool_execution_start', 'toolCallId': 'common-read', 'toolName': 'read',
                'args': {'path': common_path}},
               {'type': 'tool_execution_end', 'toolCallId': 'common-read', 'toolName': 'read',
                'result': {'content': read_content}, 'isError': False},
               {'type': 'tool_execution_start', 'toolCallId': 'candidate-read', 'toolName': 'read',
                'args': {'path': candidate_path}},
               {'type': 'tool_execution_end', 'toolCallId': 'candidate-read', 'toolName': 'read',
                'result': {'content': candidate_content}, 'isError': False}])
checks = []; tool_contents = []
for index, invocation in enumerate(launch['check_invocations']):
 call = 'call-' + str(index)
 events.append({'type': 'tool_execution_start', 'toolCallId': call, 'toolName': 'bash',
                'args': {'command': invocation['bash']}})
 result = subprocess.run(invocation['bash'], shell=True, capture_output=True, text=True)
 content = [{'type': 'text', 'text': result.stdout + result.stderr}]; tool_contents.append(content)
 events.append({'type': 'tool_execution_end', 'toolCallId': call, 'toolName': 'bash',
                'result': {'content': content}, 'isError': result.returncode != 0})
 checks.append({'id': invocation['id'], 'command': invocation['command'], 'exit_code': result.returncode,
                'summary': 'fake transport observed exit ' + str(result.returncode)})
candidate = common['candidate']
identity = {'work_id': common['work_id'], 'repository': candidate['repository'], 'branch': candidate['branch'],
 'commit': candidate['commit'], 'source_digest': candidate['source_digest'], 'common_digest': common_digest,
 'spec_digest': common['contract']['spec']['sha256'],
 'executable_digest': common['canonical']['state']['executable']['digest']}
report = {'schema_version': 1, 'protocol': 'razr-general-review-report-v1', 'role': role, 'context': context,
 'identity': identity, 'review_status': 'complete', 'verdict': 'PASS', 'reproduced_checks': checks,
 'findings': [], 'limitations': ['fake transport only'], 'evidence_access': ['common.json', 'isolated candidate']}
message = {'role': 'assistant', 'content': [{'type': 'text', 'text': json.dumps(report)}], 'stopReason': 'stop',
 'provider': provider, 'model': model}
events.extend([{'type': 'message_end', 'message': message}, {'type': 'agent_settled', 'aborted': False}])
for row in events: print(json.dumps(row), flush=True)
session_dir.mkdir(parents=True, exist_ok=True)
saved = [{'type': 'session', 'version': 3, 'id': context, 'cwd': str(pathlib.Path.cwd())},
 {'type': 'message', 'message': {'role': 'user', 'content': prompt}},
 {'type': 'message', 'message': {'role': 'assistant', 'content': [
  {'type': 'toolCall', 'id': 'common-read', 'name': 'read', 'arguments': {'path': common_path}}]}},
 {'type': 'message', 'message': {'role': 'toolResult', 'toolCallId': 'common-read', 'toolName': 'read',
  'content': read_content, 'isError': False}},
 {'type': 'message', 'message': {'role': 'assistant', 'content': [
  {'type': 'toolCall', 'id': 'candidate-read', 'name': 'read', 'arguments': {'path': candidate_path}}]}},
 {'type': 'message', 'message': {'role': 'toolResult', 'toolCallId': 'candidate-read', 'toolName': 'read',
  'content': candidate_content, 'isError': False}}]
for index, invocation in enumerate(launch['check_invocations']):
 call = 'call-' + str(index)
 saved.append({'type': 'message', 'message': {'role': 'assistant', 'content': [
  {'type': 'toolCall', 'id': call, 'name': 'bash', 'arguments': {'command': invocation['bash']}}]}})
 saved.append({'type': 'message', 'message': {'role': 'toolResult', 'toolCallId': call, 'toolName': 'bash',
  'content': tool_contents[index], 'isError': checks[index]['exit_code'] != 0}})
saved.append({'type': 'message', 'message': message})
(session_dir / 'session.jsonl').write_text(''.join(json.dumps(row) + '\\n' for row in saved))
""")
        fake.chmod(0o755)
        return fake

    def create_through_readback(self, root, repository, url, base, head, base_sha, head_sha, source_digest):
        full = pr_value(url=url, number=int(url.rsplit('/', 1)[1]), baseRefName=base, baseRefOid=base_sha,
                        headRefName=head, headRefOid=head_sha)
        fence_keys = {"number", "url", "state", "isDraft", "baseRefName", "baseRefOid", "headRefName",
                      "headRefOid", "mergeStateStatus", "mergeable", "reviewDecision", "mergedAt", "closedAt",
                      "updatedAt"}
        fence = {key: full[key] for key in fence_keys}
        with patch.object(delivery, "remote_identity", return_value=repository), \
             patch.object(delivery, "pushed_head", side_effect=[base_sha, head_sha] * 5), \
             patch.object(delivery, "source_identity", side_effect=[source_digest] * 5), \
             patch.object(delivery, "_json", side_effect=[[], json.loads(json.dumps(full)),
                                                            json.loads(json.dumps(full)), fence]), \
             patch.object(delivery, "_run", return_value=url), \
             patch.object(delivery, "_required_checks", side_effect=[([], "none"), ([], "none")]), \
             patch.object(delivery, "_threads", side_effect=[[], []]):
            return delivery.create_pr(root, base=base, head=head, title="Synthetic PR", body="fixture",
                                      expected_repository=repository, expected_base_sha=base_sha,
                                      expected_head_sha=head_sha, expected_source_digest=source_digest)

    def test_general_freeze_dual_record_and_review_transition(self):
        root, item = self.candidate()
        self.invoke([self.tools["git"], "-C", root, "remote", "add", "origin",
                     "https://github.com/fixture/review-project.git"], root)
        output = self.lab / "retained-review"
        common_path, common_digest = review.freeze(str(root), "public-probe", str(output))
        common = review.validate_common(common_path, common_digest)
        self.assertEqual(common["candidate"]["repository"], "fixture/review-project")
        self.assertIn("origin", common["remotes"])
        excluded = {row["path"]: row for row in common["excluded_inputs"]["work_root"]}
        shared = ".razr/work/shared-review-input.md"
        self.assertIn(shared, excluded)
        self.assertTrue(excluded[shared]["live_matches_candidate"])
        self.assertEqual(excluded[shared]["candidate_sha256"], excluded[shared]["live_sha256"])
        state_path = ".razr/work/public-probe/state.yaml"
        self.assertIn(state_path, excluded)
        self.assertFalse(excluded[state_path]["live_matches_candidate"])
        fake = self.fake_pi()
        runs = {}
        primary_context = str(uuid.uuid4())
        reproduction_dir = self.lab / "primary-reproduction"
        review.reproduce(str(common_path), common_digest, str(reproduction_dir), primary_context, 120, 1048576)
        reproduction = reproduction_dir / "reproduction.json"
        self.assertEqual(json.loads(reproduction.read_text())["environment"]["path_digest"],
                         review.digest(os.environ["PATH"].encode()))
        for role in review.ROLES:
            run = self.lab / f"{role}-tracer-run"
            review.launch(str(common_path), common_digest, role, str(run), "fixture", "fixture",
                          "off", 30, 1024 * 1024, str(fake))
            _, audit = review.audit_run(str(common_path), common_digest, str(run), role)
            self.assertTrue(audit["structurally_valid"])
            reproduced = json.loads(reproduction.read_text())
            reproduced["checks"][0]["execution"]["reason"] = "timeout" if role == "technical" else "output_limit"
            reproduction.write_text(json.dumps(reproduced))
            with self.assertRaisesRegex(review.Refusal, "reproduction result"):
                review.support_run(str(common_path), common_digest, str(run), role, primary_context,
                                   "SYNTHETIC primary support", str(reproduction))
            reproduced["checks"][0]["execution"]["reason"] = None
            reproduction.write_text(json.dumps(reproduced))
            review.support_run(str(common_path), common_digest, str(run), role, primary_context,
                               "SYNTHETIC primary support", str(reproduction))
            runs[role] = run
        reproduced = json.loads(reproduction.read_text()); stdout_path = Path(reproduced["checks"][0]["stdout"])
        original_stdout = stdout_path.read_bytes(); stdout_path.write_bytes(original_stdout + b"drift")
        with self.assertRaisesRegex(review.Refusal, "reproduction output drift"):
            review.record(str(common_path), common_digest, str(runs["technical"]), str(runs["contract"]))
        stdout_path.write_bytes(original_stdout)
        result = review.record(str(common_path), common_digest, str(runs["technical"]), str(runs["contract"]))
        self.assertEqual(result["human_review_transition"], "pending")
        self.assertTrue(all(row["outcome"] == "PASS" for row in result["critics"].values()))
        state = json.loads((item / "state.yaml").read_text())
        self.core(root, "transition", "public-probe", "--to", "review", "--status", "active",
                  "--expect-stage", "verify", "--decision", "SYNTHETIC-human-review-approval",
                  "--expect-generation", str(state["generation"]))
        state = json.loads((item / "state.yaml").read_text())
        self.assertEqual((state["stage"], state["status"]), ("review", "active"))
        self.assertIsNone(state["delivery"]["pull_request"])

        commit = state["candidate"]["commit"]
        source_digest = state["candidate"]["source_digest"]
        created = self.create_through_readback(root, "fixture/review-project",
                                               "https://github.com/fixture/review-project/pull/1",
                                               "dev", "main", "b" * 40, commit, source_digest)
        self.assertEqual(created["url"], "https://github.com/fixture/review-project/pull/1")
        gh_dir = self.lab / "fake-gh-bin"; gh_dir.mkdir()
        gh = gh_dir / "gh"
        gh.write_text(f"""#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
if args[:2] == ['auth', 'status']:
 sys.exit(0)
if args[:2] == ['pr', 'view']:
 print(json.dumps({{'number': 1, 'url': 'https://github.com/fixture/review-project/pull/1', 'state': 'OPEN',
  'isDraft': False, 'headRefOid': '{commit}', 'reviewDecision': None, 'statusCheckRollup': [],
  'comments': [], 'reviews': [], 'mergedAt': None, 'closedAt': None, 'updatedAt': '2000-01-01T00:00:00Z'}})); sys.exit(0)
if args[:2] == ['pr', 'checks']:
 print('[]'); sys.exit(1)
if args[:2] == ['api', 'graphql']:
 print(json.dumps({{'data': {{'repository': {{'pullRequest': {{'reviewThreads': {{'nodes': [],
  'pageInfo': {{'hasNextPage': False, 'endCursor': None}}}}}}}}}}}})); sys.exit(0)
print('unsupported fake gh command', file=sys.stderr); sys.exit(2)
""")
        gh.chmod(0o755)
        old_path = self.env["PATH"]
        self.env["PATH"] = str(gh_dir) + os.pathsep + old_path
        try:
            self.core(root, "pr", "public-probe", "--url", "https://github.com/fixture/review-project/pull/1",
                      "--head-commit", commit, "--outcome", "pending",
                      "--expect-generation", str(state["generation"]))
        finally:
            self.env["PATH"] = old_path
        state = json.loads((item / "state.yaml").read_text())
        self.assertEqual(state["delivery"]["pull_request"], "https://github.com/fixture/review-project/pull/1")
        self.assertEqual(state["delivery"]["head_commit"], commit)
        self.assertEqual(state["delivery"]["outcome"], "pending")

    def test_second_unrelated_repository_completes_simulated_handoff(self):
        repository = "fixture/shell-project"; work_id = "shell-probe"
        branch = "delivery/topic"; base = "stable"
        root, item = self.candidate("shell-lifecycle", ".workflow/items", shell_validation=True,
                                    repository=repository, work_id=work_id, branch=branch)
        self.invoke([self.tools["git"], "-C", root, "remote", "add", "origin",
                     f"https://github.com/{repository}.git"], root)
        output = self.lab / "shell-retained-review"
        common_path, common_digest = review.freeze(str(root), work_id, str(output))
        fake = self.fake_pi()
        primary_context = str(uuid.uuid4())
        reproduction_dir = self.lab / "shell-primary-reproduction"
        review.reproduce(str(common_path), common_digest, str(reproduction_dir), primary_context, 120, 1048576)
        runs = {}
        for role in review.ROLES:
            run = self.lab / f"shell-{role}-run"
            review.launch(str(common_path), common_digest, role, str(run), "fixture", "fixture",
                          "off", 30, 1048576, str(fake))
            review.support_run(str(common_path), common_digest, str(run), role, primary_context,
                               "SYNTHETIC second-fixture support", str(reproduction_dir / "reproduction.json"))
            runs[role] = run
        review.record(str(common_path), common_digest, str(runs["technical"]), str(runs["contract"]))
        state = json.loads((item / "state.yaml").read_text())
        self.core(root, "transition", work_id, "--to", "review", "--status", "active",
                  "--expect-stage", "verify", "--decision", "SYNTHETIC-second-human-approval",
                  "--expect-generation", str(state["generation"]))
        state = json.loads((item / "state.yaml").read_text())
        commit = state["candidate"]["commit"]; source_digest = state["candidate"]["source_digest"]
        url = f"https://github.com/{repository}/pull/2"
        self.assertEqual(self.create_through_readback(root, repository, url, base, branch,
                                                       "b" * 40, commit, source_digest)["url"], url)
        gh_dir = self.lab / "fake-gh-bin-second"; gh_dir.mkdir(); gh = gh_dir / "gh"
        gh.write_text(f"""#!/usr/bin/env python3
import json, sys
args=sys.argv[1:]
if args[:2]==['auth','status']: sys.exit(0)
if args[:2]==['pr','view']:
 print(json.dumps({{'number':2,'url':'{url}','state':'OPEN','isDraft':False,'headRefOid':'{commit}',
 'reviewDecision':None,'statusCheckRollup':[],'comments':[],'reviews':[],'mergedAt':None,'closedAt':None,
 'updatedAt':'2000-01-01T00:00:00Z'}})); sys.exit(0)
if args[:2]==['pr','checks']: print('[]'); sys.exit(1)
if args[:2]==['api','graphql']:
 print(json.dumps({{'data':{{'repository':{{'pullRequest':{{'reviewThreads':{{'nodes':[],
 'pageInfo':{{'hasNextPage':False,'endCursor':None}}}}}}}}}}}})); sys.exit(0)
sys.exit(2)
"""); gh.chmod(0o755)
        old_path = self.env["PATH"]; self.env["PATH"] = str(gh_dir) + os.pathsep + old_path
        try:
            self.core(root, "pr", work_id, "--url", url, "--head-commit", commit,
                      "--outcome", "pending", "--expect-generation", str(state["generation"]))
        finally:
            self.env["PATH"] = old_path
        delivered = json.loads((item / "state.yaml").read_text())["delivery"]
        self.assertEqual((delivered["pull_request"], delivered["head_commit"], delivered["outcome"]),
                         (url, commit, "pending"))

    def test_duplicate_commands_bind_by_validation_id_and_fail_can_be_supported(self):
        context = str(uuid.uuid4()); common_digest = "sha256:" + "c" * 64
        common = {"work_id": "duplicate-probe",
                  "candidate": {"repository": "fixture/duplicate", "branch": "main", "commit": "a" * 40,
                                "source_digest": "sha256:" + "b" * 64},
                  "contract": {"spec": {"sha256": "sha256:" + "d" * 64}},
                  "canonical": {"state": {"executable": {"digest": "sha256:" + "e" * 64}}},
                  "declared_validations": [{"id": "one:1", "command": "true"},
                                           {"id": "two:1", "command": "true"}]}
        report = self.report(common, common_digest, "technical", context)
        no_evidence = json.loads(json.dumps(report)); no_evidence["evidence_access"] = []
        with self.assertRaisesRegex(review.Refusal, "accessed-evidence summary"):
            review.validate_report(no_evidence, common, common_digest, "technical", context)
        contradictory = json.loads(json.dumps(report))
        contradictory["reproduced_checks"].append({"id": "one:1", "command": "true", "exit_code": 1,
                                                     "summary": "contradictory duplicate"})
        with self.assertRaisesRegex(review.Refusal, "duplicate reproduced check"):
            review.validate_report(contradictory, common, common_digest, "technical", context)
        duplicate_field = json.dumps(report).replace('"verdict": "PASS"',
                                                       '"verdict": "FAIL", "verdict": "PASS"')
        with self.assertRaisesRegex(review.Refusal, "duplicate JSON object field"):
            review.strict_json(duplicate_field)
        with self.assertRaisesRegex(review.Refusal, "non-finite JSON number"):
            review.strict_json('{"value":NaN}')
        with self.assertRaisesRegex(review.Refusal, "non-finite JSON number"):
            review.strict_json('{"value":1e999}')
        boolean_schema = json.loads(json.dumps(report)); boolean_schema["schema_version"] = True
        with self.assertRaisesRegex(review.Refusal, "unknown report protocol"):
            review.validate_report(boolean_schema, common, common_digest, "technical", context)
        duplicate_event = b'{"type":"session","type":"message_end"}\n'
        with self.assertRaisesRegex(review.Refusal, "duplicate JSON object field"):
            review.parse_jsonl(duplicate_event)
        duplicate_record = self.lab / "duplicate-record.json"
        duplicate_record.write_text('{"schema_version":1,"schema_version":2}')
        with self.assertRaisesRegex(review.Refusal, "duplicate JSON object field"):
            review.load(duplicate_record)
        bool_exit = json.loads(json.dumps(report)); bool_exit["reproduced_checks"][0]["exit_code"] = False
        with self.assertRaisesRegex(review.Refusal, "invalid check result"):
            review.validate_report(bool_exit, common, common_digest, "technical", context)
        markers = [review.check_exit_marker(context, "one:1"), review.check_exit_marker(context, "two:1")]
        shells = ["wrapped-one", "wrapped-two"]
        rows = [{"type": "session", "id": context}]
        for index, command in enumerate(shells):
            rows += [{"type": "tool_execution_start", "toolCallId": str(index), "toolName": "bash",
                      "args": {"command": command}},
                     {"type": "tool_execution_end", "toolCallId": str(index), "isError": False,
                      "result": {"content": [{"type": "text", "text": markers[index] + "=0\n"}]}}]
        rows += [{"type": "message_end", "message": {"role": "assistant",
                  "content": [{"type": "text", "text": json.dumps(report)}]}},
                 {"type": "agent_settled", "aborted": False}]
        raw = ("\n".join(json.dumps(row) for row in rows) + "\n").encode()
        review.audit_events(raw, common, common_digest, "technical", context, shells)
        duplicate_rows = rows[:2] + [json.loads(json.dumps(rows[1]))] + rows[2:]
        with self.assertRaisesRegex(review.Refusal, "duplicate tool execution record"):
            review.audit_events(("\n".join(json.dumps(row) for row in duplicate_rows) + "\n").encode(),
                                common, common_digest, "technical", context, shells)
        one_only = ("\n".join(json.dumps(row) for row in rows if row.get("toolCallId") != "1") + "\n").encode()
        with self.assertRaisesRegex(review.Refusal, "each observed exactly once"):
            review.audit_events(one_only, common, common_digest, "technical", context, shells)

        stdout = self.lab / "fail-stdout"; stderr = self.lab / "fail-stderr"
        stdout.write_bytes(b""); stderr.write_bytes(b"expected failure\n")
        common["declared_validations"] = [{"id": "one:1", "command": "false"}]
        fail_report = self.report(common, common_digest, "technical", context, verdict="FAIL")
        fail_report["reproduced_checks"][0]["exit_code"] = 1
        reproduction = {"checks": [{"id": "one:1", "command": "false", "exit_code": 1,
                                      "stdout": str(stdout), "stdout_digest": review.digest(b""),
                                      "stderr": str(stderr), "stderr_digest": review.digest(stderr.read_bytes()),
                                      "execution": {"reason": None, "exit_code": 1}}]}
        review.validate_primary_reproduction(reproduction, common, fail_report)
        boolean_reproduction = json.loads(json.dumps(reproduction))
        boolean_reproduction["checks"][0]["exit_code"] = True
        boolean_reproduction["checks"][0]["execution"]["exit_code"] = True
        with self.assertRaisesRegex(review.Refusal, "reproduction result mismatch"):
            review.validate_primary_reproduction(boolean_reproduction, common, fail_report)
        fail_report["verdict"] = "PASS"
        with self.assertRaisesRegex(review.Refusal, "contradicts PASS"):
            review.validate_primary_reproduction(reproduction, common, fail_report)

    def test_linked_worktree_supported_and_unsupported_entries_refuse(self):
        root, item = self.candidate("linked-source")
        linked = self.lab / "linked-source-worktree"
        self.invoke([self.tools["git"], "-C", root, "worktree", "add", "-q", "--detach", str(linked), "HEAD"], root)
        linked_item = linked / ".razr/work/public-probe"
        shutil.copy2(item / "state.yaml", linked_item / "state.yaml")
        shutil.copy2(item / "evidence.md", linked_item / "evidence.md")
        common_path, common_digest = review.freeze(str(linked), "public-probe", str(self.lab / "linked-review"))
        self.assertEqual(review.validate_common(common_path, common_digest)["candidate"]["commit"],
                         self.invoke([self.tools["git"], "-C", root, "rev-parse", "HEAD"], root).stdout.strip())

        symlink_root = self.repo("tracked-symlink")
        (symlink_root / "target.txt").write_text("target\n")
        (symlink_root / "linked.txt").symlink_to("target.txt")
        self.invoke([self.tools["git"], "-C", symlink_root, "add", "."], symlink_root)
        self.invoke([self.tools["git"], "-C", symlink_root, "commit", "-qm", "symlink"], symlink_root)
        symlink_commit = self.invoke([self.tools["git"], "-C", symlink_root, "rev-parse", "HEAD"],
                                     symlink_root).stdout.strip()
        with self.assertRaisesRegex(review.Refusal, "unsupported source entry"):
            review.source_inventory(symlink_root, symlink_commit, ".razr/work")

        excluded_symlink_root = self.repo("excluded-tracked-symlink")
        (excluded_symlink_root / ".razr/work").mkdir(parents=True)
        (excluded_symlink_root / "target.txt").write_text("target\n")
        (excluded_symlink_root / ".razr/work/linked.txt").symlink_to("../../target.txt")
        self.invoke([self.tools["git"], "-C", excluded_symlink_root, "add", "."], excluded_symlink_root)
        self.invoke([self.tools["git"], "-C", excluded_symlink_root, "commit", "-qm", "excluded symlink"],
                    excluded_symlink_root)
        excluded_symlink_commit = self.invoke(
            [self.tools["git"], "-C", excluded_symlink_root, "rev-parse", "HEAD"],
            excluded_symlink_root).stdout.strip()
        with self.assertRaisesRegex(review.Refusal, "unsupported excluded work-root entry"):
            review.excluded_work_root_inventory(excluded_symlink_root, excluded_symlink_commit,
                                                ".razr/work", set())

        submodule_root = self.repo("gitlink-source")
        (submodule_root / "base.txt").write_text("base\n")
        self.invoke([self.tools["git"], "-C", submodule_root, "add", "base.txt"], submodule_root)
        self.invoke([self.tools["git"], "-C", submodule_root, "commit", "-qm", "base"], submodule_root)
        object_id = self.invoke([self.tools["git"], "-C", submodule_root, "rev-parse", "HEAD"],
                                submodule_root).stdout.strip()
        self.invoke([self.tools["git"], "-C", submodule_root, "update-index", "--add", "--cacheinfo",
                     f"160000,{object_id},vendor"], submodule_root)
        self.invoke([self.tools["git"], "-C", submodule_root, "commit", "-qm", "gitlink"], submodule_root)
        gitlink_commit = self.invoke([self.tools["git"], "-C", submodule_root, "rev-parse", "HEAD"],
                                     submodule_root).stdout.strip()
        with self.assertRaisesRegex(review.Refusal, "unsupported source entry"):
            review.source_inventory(submodule_root, gitlink_commit, ".razr/work")

        broken_root = self.repo("broken-symbolic-ref")
        (broken_root / "base.txt").write_text("base\n")
        self.invoke([self.tools["git"], "-C", broken_root, "add", "base.txt"], broken_root)
        self.invoke([self.tools["git"], "-C", broken_root, "commit", "-qm", "base"], broken_root)
        self.invoke([self.tools["git"], "-C", broken_root, "symbolic-ref", "refs/custom/broken",
                     "refs/custom/missing"], broken_root)
        with self.assertRaisesRegex(review.Refusal, "unsupported or corrupt committed-history refs"):
            review.history_identity(broken_root)

    def test_reports_refuse_incomplete_reuse_drift_and_peer_context(self):
        root, _ = self.candidate("refusals", ".workflow/items", shell_validation=True)
        output = self.lab / "refusal-review"
        common_path, common_digest = review.freeze(str(root), "public-probe", str(output))
        common = review.validate_common(common_path, common_digest)
        shared = str(uuid.uuid4())
        technical = self.report(common, common_digest, "technical", shared)
        contract = self.report(common, common_digest, "contract", shared)
        review.validate_report(technical, common, common_digest, "technical")
        review.validate_report(contract, common, common_digest, "contract")
        technical_path, contract_path = output / "technical.json", output / "contract.json"
        review.save_new(technical_path, technical); review.save_new(contract_path, contract)
        with self.assertRaisesRegex(review.Refusal, "run directory"):
            review.record(str(common_path), common_digest, str(technical_path), str(contract_path))
        incomplete = self.report(common, common_digest, "contract", status="incomplete")
        checked = review.validate_report(incomplete, common, common_digest, "contract")
        self.assertIsNone(checked["verdict"])
        ignored = root / ".ignored-review"; ignored.write_text("hidden input\n")
        with self.assertRaisesRegex(review.Refusal, "ignored files require resolution"):
            review.validate_common(common_path, common_digest)
        ignored.unlink()
        (root / "untracked.txt").write_text("drift\n")
        with self.assertRaisesRegex(review.Refusal, "untracked"):
            review.validate_common(common_path, common_digest)

    def test_bounded_launcher_uses_fresh_claim_and_retains_report(self):
        root, _ = self.candidate("launcher")
        output = self.lab / "launcher-review"
        common_path, common_digest = review.freeze(str(root), "public-probe", str(output))
        history_names = {row["name"] for row in review.validate_common(common_path, common_digest)["committed_history"]["refs"]}
        self.assertTrue({"refs/tags/fixture-history-base", "refs/tags/release/HEAD",
                         "refs/remotes/upstream/base", "refs/custom/important"} <= history_names)
        self.assertIn("refs/remotes/upstream/HEAD", history_names)
        fake = self.fake_pi()
        run = self.lab / "technical-live-shape"
        result = review.launch(str(common_path), common_digest, "technical", str(run), "fixture", "fixture",
                               "off", 30, 1024 * 1024, str(fake))
        self.assertEqual((result["review_status"], result["verdict"]), ("complete", "PASS"))
        self.assertTrue((run / "events.jsonl").is_file())
        self.assertTrue((run / "report.json").is_file())
        candidate_view = run / "candidate"
        self.assertGreaterEqual(int(review.git_text(candidate_view, "rev-list", "--count", "HEAD")), 2)
        shallow = candidate_view / ".git/shallow"
        shallow.write_text(review.git_text(candidate_view, "rev-parse", "HEAD") + "\n")
        with self.assertRaisesRegex(review.Refusal, "shallow candidate history"):
            review.audit_run(str(common_path), common_digest, str(run), "technical")
        shallow.unlink()
        events = run / "events.jsonl"; original_events = events.read_bytes()
        rows = [json.loads(line) for line in original_events.splitlines()]
        common_end = next(row for row in rows if row.get("type") == "tool_execution_end"
                          and row.get("toolCallId") == "common-read")
        common_end["isError"] = True
        events.write_text("".join(json.dumps(row) + "\n" for row in rows))
        with self.assertRaisesRegex(review.Refusal, "successfully read required evidence"):
            review.audit_run(str(common_path), common_digest, str(run), "technical")
        events.write_bytes(original_events)
        rows = [json.loads(line) for line in original_events.splitlines()]
        next(row for row in rows if row.get("toolCallId") == "common-read" and row.get("type") == "tool_execution_start")["args"]["path"] += ".not-the-file"
        events.write_text("".join(json.dumps(row) + "\n" for row in rows))
        with self.assertRaisesRegex(review.Refusal, "successfully read required evidence"):
            review.audit_run(str(common_path), common_digest, str(run), "technical")
        events.write_bytes(original_events)
        rows = [json.loads(line) for line in original_events.splitlines()]
        next(row for row in rows if row.get("type") == "tool_execution_end" and row.get("toolName") == "bash")["isError"] = True
        events.write_text("".join(json.dumps(row) + "\n" for row in rows))
        with self.assertRaisesRegex(review.Refusal, "exit contradicts"):
            review.audit_run(str(common_path), common_digest, str(run), "technical")
        events.write_bytes(original_events)
        rows = [json.loads(line) for line in original_events.splitlines()]
        ended = next(row for row in rows if row.get("type") == "tool_execution_end" and row.get("toolName") == "bash")
        ended["result"]["content"][0]["text"] = ended["result"]["content"][0]["text"].replace("=0\n", "=1\n")
        events.write_text("".join(json.dumps(row) + "\n" for row in rows))
        with self.assertRaisesRegex(review.Refusal, "exact tool result"):
            review.audit_run(str(common_path), common_digest, str(run), "technical")
        events.write_bytes(original_events)
        rows = [json.loads(line) for line in original_events.splitlines()]
        ended = next(row for row in rows if row.get("type") == "tool_execution_end" and row.get("toolName") == "bash")
        ended["result"]["content"][0]["text"] = ended["result"]["content"][0]["text"].replace(
            "__RAZR_REVIEW_EXIT_", "bogus-prefix__RAZR_REVIEW_EXIT_", 1)
        events.write_text("".join(json.dumps(row) + "\n" for row in rows))
        with self.assertRaisesRegex(review.Refusal, "exact exit marker"):
            review.audit_run(str(common_path), common_digest, str(run), "technical")
        events.write_bytes(original_events)
        execution_path = run / "execution.json"; original_execution = execution_path.read_bytes()
        execution = json.loads(original_execution); execution["exit_code"] = False
        execution_path.write_text(json.dumps(execution))
        with self.assertRaisesRegex(review.Refusal, "execution incomplete"):
            review.audit_run(str(common_path), common_digest, str(run), "technical")
        execution_path.write_bytes(original_execution)
        session = next((run / "sessions").rglob("*.jsonl")); original_session = session.read_bytes()
        session.write_bytes(original_session.replace(b'"cwd":', b'"cwD":', 1))
        with self.assertRaisesRegex(review.Refusal, "evidence drift"):
            review.audit_run(str(common_path), common_digest, str(run), "technical")
        session.write_bytes(original_session + json.dumps({"type": "session", "id": str(uuid.uuid4())}).encode() + b"\n")
        with self.assertRaisesRegex(review.Refusal, "saved session header"):
            review.audit_run(str(common_path), common_digest, str(run), "technical")
        session.write_bytes(original_session)
        with self.assertRaisesRegex(review.Refusal, "consumed"):
            review.launch(str(common_path), common_digest, "technical", str(self.lab / "retry"),
                          "fixture", "fixture", "off", 30, 1024 * 1024, str(fake))

    def test_json_event_audit_requires_fresh_settled_session(self):
        root, _ = self.candidate("events")
        output = self.lab / "event-review"
        common_path, common_digest = review.freeze(str(root), "public-probe", str(output))
        common = review.validate_common(common_path, common_digest)
        context = str(uuid.uuid4())
        report = self.report(common, common_digest, "technical", context)
        message = {"role": "assistant", "content": [{"type": "text", "text": json.dumps(report)}],
                   "stopReason": "stop", "provider": "fixture", "model": "fixture"}
        rows = [{"type": "session", "version": 3, "id": context, "cwd": str(output)},
                {"type": "message_end", "message": message}, {"type": "agent_settled", "aborted": False}]
        raw = "".join(json.dumps(row) + "\n" for row in rows).encode()
        observed, audit = review.audit_events(raw, common, common_digest, "technical", context)
        self.assertEqual(observed["verdict"], "PASS")
        self.assertEqual(audit["context"], context)
        duplicate_header_rows = rows[:1] + [{"type": "session", "id": str(uuid.uuid4())}] + rows[1:]
        with self.assertRaisesRegex(review.Refusal, "exactly one leading"):
            review.audit_events("".join(json.dumps(row) + "\n" for row in duplicate_header_rows).encode(),
                                common, common_digest, "technical", context)
        rows[0]["parentSession"] = "/builder/session.jsonl"
        with self.assertRaisesRegex(review.Refusal, "forked"):
            review.audit_events("".join(json.dumps(row) + "\n" for row in rows).encode(),
                                common, common_digest, "technical", context)


if __name__ == "__main__":
    import unittest
    unittest.main()
