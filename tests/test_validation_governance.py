"""Static regression checks for bounded GPU validation governance."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent.parent


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


class ValidationGovernanceTests(unittest.TestCase):
    def test_agent_rules_require_bounded_hardware_attempts(self):
        agents = read("AGENTS.md")
        self.assertIn("0006-bounded-validation-governance.md", agents)
        self.assertIn("at most two real-hardware executions", agents)
        self.assertIn("external test agent is an Operator", agents)
        self.assertIn("Never repeat an unchanged soak", agents)

    def test_model_policy_defines_external_operator_boundary(self):
        policy = read("docs/MODEL_POLICY.md")
        self.assertIn("External test Operator", policy)
        self.assertIn("Antigravity / Gemini Flash 7", policy)
        self.assertIn("Put the evidence row on `HOLD`", policy)
        self.assertIn("Gate 2W is the external Wayland/compositor", policy)

    def test_adr_defines_attempt_budget_and_claim_oracles(self):
        adr = read("docs/adr/0006-bounded-validation-governance.md")
        for required in (
            "default budget of two real-hardware executions",
            "one repeat after an evidence review identifies a single material change",
            "A third execution is exceptional",
            "explicit user approval for that third run",
            "External test executor",
            "Claim-to-oracle rule",
            "Machine-readable evidence must be sanitized",
        ):
            self.assertIn(required, adr)

    def test_operator_template_is_immutable_and_single_run(self):
        template = read("docs/GPU_TEST_HANDOFF_TEMPLATE.md")
        self.assertIn("executes it unchanged", template)
        self.assertIn("Execute the command at most once", template)
        self.assertIn("PREFLIGHT_BLOCKED", template)
        self.assertIn("Required return package", template)

    def test_unsupported_gate_signoff_is_not_restored(self):
        development = read("docs/GPU_NEXT_DEVELOPMENT.md")
        self.assertNotIn("Gate 2 & Gate 3 Hardware Validation Sign-Off", development)
        self.assertNotIn("All 16 process runs started, rendered frames", development)
        self.assertIn("Gate 2 is `OPEN`", development)
        self.assertIn("Gate 3 is `OPEN`", development)

    def test_public_test_matrix_has_no_private_file_url(self):
        matrix = read("TEST_MATRIX.md")
        self.assertNotIn("file:///home/", matrix)
        self.assertIn("PROVISIONAL", matrix)
        self.assertIn("STUB_UNSUPPORTED", matrix)


if __name__ == "__main__":
    unittest.main()
