"""Current-routing status, clean adoption and safe routing-removal coverage."""
import json
from pathlib import Path
import stat
import subprocess
import sys

from test_adoption import FixtureTest, surface
from run_public import ROOT, PACKAGE


class ProjectRemovalTests(FixtureTest):
    def status(self, root, work_id):
        env = dict(self.env)
        allowed_git = {"GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL", "GIT_OPTIONAL_LOCKS", "GIT_TERMINAL_PROMPT"}
        for key in list(env):
            if key.startswith("GIT_") and key not in allowed_git:
                env.pop(key)
        result = subprocess.run(
            [sys.executable, "-I", "-B", PACKAGE / "scripts/pi-surface.py", "status", work_id],
            cwd=root, env=env, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        return result

    def test_discovery_status_refuses_unknown_routing_and_is_read_only(self):
        root = self.repo()
        self.adopt(root)
        agents = root / "AGENTS.md"
        current = (PACKAGE / "runtime/AGENTS.project-razr.md").read_text().strip()
        unknown = "<!-- RAZR:START -->\nunknown managed routing\n<!-- RAZR:END -->"
        agents.write_text(agents.read_text().replace(current, unknown))
        self.core(root, "idea", "routing-probe", "--title", "Routing probe", "--depth", "tiny")
        self.invoke([self.tools["git"], "-C", root, "add", "AGENTS.md", "razr.yaml"], root)
        self.invoke([self.tools["git"], "-C", root, "-c", "user.name=fixture", "-c",
                     "user.email=fixture@example.invalid", "commit", "-qm", "fixture"], root)
        before = surface(root)
        result = json.loads(self.status(root, "routing-probe").stdout)
        self.assertEqual(surface(root), before)
        self.assertEqual((result["stage"], result["status"], result["generation"]),
                         ("spec", "exploring", None))
        self.assertIsNone(result["raw_state"])
        self.assertEqual(result["adapter"]["routing"], "modified_or_unknown")
        self.assertEqual(result["adapter"]["blocked_by"], "project_routing")
        self.assertIn("still exploring", result["adapter"]["reason"])
        self.assertIn("unknown or modified block", result["adapter"]["next_action"])
        self.assertNotIn("routing_profile", result["adapter"])

        agents.write_text(unknown + "\n" + unknown + "\n")
        self.invoke([self.tools["git"], "-C", root, "add", "AGENTS.md"], root)
        self.invoke([self.tools["git"], "-C", root, "-c", "user.name=fixture", "-c",
                     "user.email=fixture@example.invalid", "commit", "-qm", "ambiguous routing"], root)
        before = surface(root)
        ambiguous = json.loads(self.status(root, "routing-probe").stdout)
        self.assertEqual(surface(root), before)
        self.assertEqual(ambiguous["adapter"]["routing"], "ambiguous")
        self.assertEqual(ambiguous["adapter"]["blocked_by"], "project_routing")
        self.assertIn("duplicated or out of order", ambiguous["adapter"]["next_action"])
        self.assertNotIn("migration", ambiguous["adapter"]["next_action"].lower())

    def test_de_adopt_prepares_exact_files_and_retains_project_data(self):
        root = self.repo()
        prefix = "# User instructions\n\n"
        suffix = "\n\nKeep this ending.\n"
        (root / "AGENTS.md").write_text(prefix)
        self.adopt(root)
        (root / "AGENTS.md").write_text((root / "AGENTS.md").read_text() + suffix)
        original_agents = (root / "AGENTS.md").read_bytes().replace(b"# User instructions\n", b"# User instructions\r\n")
        (root / "AGENTS.md").write_bytes(original_agents)
        (root / "AGENTS.md").chmod(0o640)
        start = original_agents.find(b"<!-- RAZR:START -->")
        end = original_agents.find(b"<!-- RAZR:END -->") + len(b"<!-- RAZR:END -->")
        expected_agents = original_agents[:start] + original_agents[end:]
        self.core(root, "idea", "retained", "--title", "Retained", "--depth", "tiny")
        config = (root / "razr.yaml").read_bytes()
        spec = (root / ".razr/work/retained/spec.md").read_bytes()
        result = self.helper("de-adopt.py", "--target", str(root), cwd=root)
        self.assertIn("proposal prepared; AGENTS.md was not changed", result.stdout)
        snapshots = list(root.glob(".AGENTS.md.razr-deadopt-original-*"))
        proposals = list(root.glob(".AGENTS.md.razr-deadopt-proposed-*"))
        self.assertEqual((len(snapshots), len(proposals)), (1, 1))
        self.assertEqual(snapshots[0].read_bytes(), original_agents)
        self.assertEqual(proposals[0].read_bytes(), expected_agents)
        self.assertEqual(stat.S_IMODE(snapshots[0].stat().st_mode), 0o640)
        self.assertEqual(stat.S_IMODE(proposals[0].stat().st_mode), 0o640)
        self.assertEqual((root / "AGENTS.md").read_bytes(), original_agents)
        self.assertEqual((root / "razr.yaml").read_bytes(), config)
        self.assertEqual((root / ".razr/work/retained/spec.md").read_bytes(), spec)

        (root / "AGENTS.md").write_bytes(proposals[0].read_bytes())
        (root / "AGENTS.md").chmod(0o640)
        before_readopt = surface(root)
        refused = self.adopt(root, expected=2)
        self.assertIn("use a clean target or resolve the retained data explicitly", refused.stderr)
        self.assertEqual(surface(root), before_readopt)
        second = self.helper("de-adopt.py", "--target", str(root), cwd=root)
        self.assertIn("No managed Razr routing present", second.stdout)

    def test_de_adopt_never_overwrites_concurrent_agent_changes(self):
        root = self.repo("concurrent-proposal")
        self.adopt(root)
        agents = root / "AGENTS.md"
        original = agents.read_bytes()
        import importlib.util
        script = PACKAGE / "scripts/de-adopt.py"
        spec = importlib.util.spec_from_file_location("de_adopt_concurrent", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        real_write = module.write_exact
        calls = 0

        def concurrent_write(path, raw, mode):
            nonlocal calls
            result = real_write(path, raw, mode)
            calls += 1
            if calls == 1:
                agents.write_bytes(b"CONCURRENT INSTRUCTIONS\n")
            return result

        module.write_exact = concurrent_write
        old_argv = sys.argv
        try:
            sys.argv = [str(script), "--target", str(root)]
            self.assertEqual(module.main(), 0)
        finally:
            sys.argv = old_argv
            module.write_exact = real_write
        self.assertEqual(agents.read_bytes(), b"CONCURRENT INSTRUCTIONS\n")
        snapshots = list(root.glob(".AGENTS.md.razr-deadopt-original-*"))
        proposals = list(root.glob(".AGENTS.md.razr-deadopt-proposed-*"))
        self.assertEqual((len(snapshots), len(proposals)), (1, 1))
        self.assertEqual(snapshots[0].read_bytes(), original)
        self.assertNotEqual(proposals[0].read_bytes(), original)

    def test_de_adopt_refuses_unknown_ambiguous_and_linked_routing_without_changes(self):
        start, end = "<!-- RAZR:START -->", "<!-- RAZR:END -->"
        cases = (start + "\nmodified\n" + end, start, (start + "\nx\n" + end + "\n") * 2)
        for index, text in enumerate(cases):
            with self.subTest(case=index):
                root = self.repo("refusal-" + str(index))
                (root / "AGENTS.md").write_text(text)
                before = surface(root)
                result = self.helper("de-adopt.py", "--target", str(root), cwd=root, expected=2)
                self.assertIn("DE-ADOPT ERROR", result.stderr)
                self.assertEqual(surface(root), before)
        root = self.repo("linked")
        outside = self.lab / "outside-agents"
        outside.write_text("protected\n")
        (root / "AGENTS.md").symlink_to(outside)
        before = surface(root)
        result = self.helper("de-adopt.py", "--target", str(root), cwd=root, expected=2)
        self.assertIn("symlinked", result.stderr)
        self.assertEqual(surface(root), before)
        self.assertEqual(outside.read_text(), "protected\n")

    def test_public_product_surfaces_use_clean_install_framing_and_keep_safe_exit(self):
        surfaces = {
            "root_readme": (ROOT / "README.md").read_text(),
            "package_readme": (PACKAGE / "README.md").read_text(),
            "current": (ROOT / "docs/CURRENT.md").read_text(),
            "capabilities": (ROOT / "docs/capabilities.md").read_text(),
            "skill": (PACKAGE / "skills/razr/SKILL.md").read_text(),
            "adoption": (PACKAGE / "skills/razr/references/adoption.md").read_text(),
            "de_adoption": (PACKAGE / "skills/razr/references/de-adoption.md").read_text(),
            "pi_surface": (PACKAGE / "scripts/pi-surface.py").read_text(),
        }
        for text in surfaces.values():
            self.assertNotIn("scripts/upgrade-routing.py", text)
            lowered = text.lower()
            self.assertNotIn("breaking release", lowered)
            self.assertNotIn("previous release", lowered)
            self.assertNotIn("earlier release", lowered)
            self.assertNotIn("incompatible previous", lowered)
            self.assertNotIn("migration", lowered)
        self.assertIn("scripts/de-adopt.py", surfaces["root_readme"])
        self.assertIn("scripts/de-adopt.py", surfaces["package_readme"])
        self.assertIn("experimental primary-led end-to-end path", surfaces["root_readme"])
        self.assertIn("final human PR handoff", surfaces["root_readme"])
        self.assertIn("safe de-adoption", surfaces["package_readme"].lower())
        self.assertIn("remove Razr routing", surfaces["package_readme"])
        self.assertIn("never overwrite `AGENTS.md`", surfaces["package_readme"])
