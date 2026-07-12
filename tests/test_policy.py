import json
import tempfile
import unittest
from pathlib import Path

from limbo.engine import LocalExecutor
from limbo.errors import ExecutionError, SpecError
from limbo.observability import redact_text
from limbo.policy import CommandPolicy, EnvPolicy, SandboxProfile, parse_policy
from limbo.spec import load_pipeline


class CommandPolicyTests(unittest.TestCase):
    def test_denylist_blocks(self):
        policy = CommandPolicy(deny=["rm *", "curl*"])
        self.assertIsNotNone(policy.violation("rm -rf /tmp/x"))
        self.assertIsNotNone(policy.violation("curl http://evil"))
        self.assertIsNone(policy.violation("echo hi"))

    def test_allowlist_is_fail_closed(self):
        policy = CommandPolicy(allow=["echo*", "python3*"])
        self.assertIsNone(policy.violation("echo hello"))
        self.assertIsNone(policy.violation("python3 -m limbo"))
        self.assertIsNotNone(policy.violation("wget http://x"))  # not allowed

    def test_deny_wins_over_allow(self):
        policy = CommandPolicy(allow=["python3*"], deny=["python3 -c*"])
        self.assertIsNone(policy.violation("python3 script.py"))
        self.assertIsNotNone(policy.violation("python3 -c 'evil'"))

    def test_empty_policy_allows_everything(self):
        self.assertIsNone(CommandPolicy().violation("anything at all"))

    def test_matches_by_first_token(self):
        policy = CommandPolicy(allow=["make"])
        self.assertIsNone(policy.violation("make build"))


class EnvPolicyTests(unittest.TestCase):
    def test_inherit_all_is_default(self):
        env = EnvPolicy().resolve({"A": "1", "B": "2"}, {"C": "3"})
        self.assertEqual({"A": "1", "B": "2", "C": "3"}, env)

    def test_inherit_none(self):
        env = EnvPolicy(inherit="none").resolve({"A": "1"}, {"C": "3"})
        self.assertEqual({"C": "3"}, env)

    def test_explicit_allowlist(self):
        env = EnvPolicy(allow=["KEEP"]).resolve({"KEEP": "1", "DROP": "2"}, {"T": "3"})
        self.assertEqual({"KEEP": "1", "T": "3"}, env)

    def test_task_env_overrides_inherited(self):
        env = EnvPolicy().resolve({"A": "parent"}, {"A": "task"})
        self.assertEqual("task", env["A"])


class RedactTextTests(unittest.TestCase):
    def test_scrubs_token_shapes(self):
        cases = [
            "sk-0123456789abcdef",
            "ghp_ABCDEFGH12345678",
            "gho_ABCDEFGH12345678",
            "github_pat_ABCDEFGH_1234",
            "xoxb-1234567890-abcdef",
            "AKIAIOSFODNN7EXAMPLE",
        ]
        for token in cases:
            self.assertEqual("***redacted***", redact_text(token), token)

    def test_leaves_plain_text(self):
        self.assertEqual("exit code 1", redact_text("exit code 1"))
        self.assertIsNone(redact_text(None))

    def test_scrubs_token_inside_message(self):
        self.assertEqual(
            "auth failed with ***redacted*** here",
            redact_text("auth failed with sk-abcdef123456 here"),
        )


class PolicyParsingTests(unittest.TestCase):
    def test_parse_full_policy(self):
        policy = parse_policy({
            "commands": {"allow": ["echo*"], "deny": ["rm *"]},
            "env": {"inherit": "none", "allow": ["PATH"]},
            "sandbox_profiles": {"strict": {"network": False, "allow_paths": ["/tmp"], "description": "no net"}},
        })
        self.assertEqual(("echo*",), policy.commands.allow)
        self.assertEqual("none", policy.env.inherit)
        self.assertEqual(("PATH",), policy.env.allow)
        self.assertIsInstance(policy.sandbox_profiles["strict"], SandboxProfile)
        self.assertFalse(policy.sandbox_profiles["strict"].network)

    def test_rejects_unknown_fields(self):
        with self.assertRaises(SpecError):
            parse_policy({"bogus": 1})
        with self.assertRaises(SpecError):
            parse_policy({"commands": {"nope": []}})

    def test_rejects_bad_env_mode(self):
        with self.assertRaisesRegex(SpecError, "inherit must be"):
            parse_policy({"env": {"inherit": "some"}})

    def test_rejects_non_string_lists(self):
        with self.assertRaises(SpecError):
            parse_policy({"commands": {"deny": [1, 2]}})


class PolicySpecIntegrationTests(unittest.TestCase):
    def write_spec(self, base, payload):
        path = base / "limbo.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_task_sandbox_must_reference_defined_profile(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self.write_spec(Path(tmpdir), {"version": 1, "tasks": [
                {"id": "a", "command": "true", "sandbox": "ghost"},
            ]})
            with self.assertRaisesRegex(SpecError, "sandbox 'ghost' is not defined"):
                load_pipeline(path)

    def test_task_sandbox_ok_when_defined(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self.write_spec(Path(tmpdir), {
                "version": 1,
                "policy": {"sandbox_profiles": {"strict": {"network": False}}},
                "tasks": [{"id": "a", "command": "true", "sandbox": "strict"}],
            })
            pipeline = load_pipeline(path)
            self.assertEqual("strict", pipeline.tasks[0].sandbox)
            self.assertIn("strict", pipeline.policy.sandbox_profiles)


class PolicyEngineTests(unittest.TestCase):
    def write_spec(self, base, payload):
        path = base / "limbo.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return load_pipeline(path)

    def test_denied_command_fails_closed_and_does_not_execute(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            pipeline = self.write_spec(base, {
                "version": 1,
                "policy": {"commands": {"deny": ["curl*"]}},
                "tasks": [{"id": "bad", "command": "curl http://x > loot.txt", "outputs": ["loot.txt"]}],
            })
            with self.assertRaises(ExecutionError):
                LocalExecutor(base / ".limbo", max_workers=1).run(pipeline)
            # Fail-closed: the command never ran, so its output does not exist.
            self.assertFalse((base / "loot.txt").exists())
            run_id = next((base / ".limbo" / "runs").iterdir()).name
            manifest = json.loads((base / ".limbo" / "runs" / run_id / "manifest.json").read_text())
            self.assertEqual("failed", manifest["results"][0]["status"])
            self.assertIn("policy:", manifest["results"][0]["reason"])

    def test_allowlist_permits_matching_command(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            pipeline = self.write_spec(base, {
                "version": 1,
                "policy": {"commands": {"allow": ["printf*"]}},
                "tasks": [{"id": "ok", "command": "printf ok > out.txt", "outputs": ["out.txt"]}],
            })
            result = LocalExecutor(base / ".limbo", max_workers=1).run(pipeline)
            self.assertEqual("succeeded", result.results[0].status)
            self.assertEqual("ok", (base / "out.txt").read_text())

    def test_env_inheritance_none_hides_parent_vars(self):
        import os
        os.environ["LIMBO_POLICY_TEST_VAR"] = "leaky"
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                base = Path(tmpdir)
                pipeline = self.write_spec(base, {
                    "version": 1,
                    "policy": {"env": {"inherit": "none"}},
                    "tasks": [{"id": "e",
                               "command": "printf '%s' \"${LIMBO_POLICY_TEST_VAR:-UNSET}\" > out.txt",
                               "outputs": ["out.txt"]}],
                })
                LocalExecutor(base / ".limbo", max_workers=1).run(pipeline)
                self.assertEqual("UNSET", (base / "out.txt").read_text())
        finally:
            os.environ.pop("LIMBO_POLICY_TEST_VAR", None)


if __name__ == "__main__":
    unittest.main()
