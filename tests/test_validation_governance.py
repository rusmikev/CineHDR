"""Static regression checks for bounded GPU validation governance."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent.parent


def read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


class ValidationGovernanceTests(unittest.TestCase):
    def test_minimal_entities_and_live_verification_rules(self):
        agents = read("AGENTS.md")
        policy = read("docs/MODEL_POLICY.md")
        for text in (agents, policy):
            self.assertIn("Do not create entities beyond necessity", text)
            self.assertIn("Verify every runtime hypothesis live", text)
            self.assertIn("Video Output Diagnostics", text)
            self.assertIn("Copy Report", text)
        self.assertIn("unknown counters are not zero", policy)
        self.assertIn("texture publication from presentation", agents)

    def test_live_diagnostics_preserve_safety_and_unknown_evidence(self):
        policy = read("docs/MODEL_POLICY.md")
        self.assertIn("Live validation does not waive ADR-0006", policy)
        self.assertIn("never request /dev/dri write access", policy)
        self.assertIn("do not block GTK, modify playback", policy)
        self.assertIn("not proof of surface submission", policy)
        self.assertIn("counter", policy)

    def test_agent_rules_require_bounded_hardware_attempts(self):
        agents = read("AGENTS.md")
        self.assertIn("0006-bounded-validation-governance.md", agents)
        self.assertIn("at most two real-hardware executions", agents)
        self.assertIn("external test agent is an Operator", agents)
        self.assertIn("Never repeat an unchanged soak", agents)

    def test_model_policy_defines_external_operator_boundary(self):
        policy = read("docs/MODEL_POLICY.md")
        self.assertIn("External test Operator", policy)
        self.assertIn("Antigravity / Gemini Flash", policy)
        self.assertIn("Put the evidence row on `HOLD`", policy)
        self.assertIn("Gate 2W is the external Wayland/compositor", policy)

    def test_current_routing_separates_engineering_and_independent_review(self):
        agents = read("AGENTS.md")
        policy = read("docs/MODEL_POLICY.md")
        for current in (agents, policy):
            normalized = " ".join(current.split())
            self.assertIn("`gpt-6-sol`", current)
            self.assertIn("`gpt-6-astra`", current)
            self.assertIn("`gpt-6-luna`", current)
            self.assertNotIn("gpt-5.6-", current)
            self.assertIn("review-only", current)
            self.assertIn("one failed dispatch", normalized.lower())
            self.assertIn("two materially distinct local", normalized)
        self.assertIn("one active implementation agent", policy)
        self.assertIn("non-overlapping file ownership", policy)
        self.assertIn("Do not repeat an\nunchanged full suite", policy)

    def test_gemini_entry_is_operator_bounded_and_separate(self):
        agents = read("AGENTS.md")
        gemini = read("GEMINI.md")
        adr = read("docs/adr/0006-bounded-validation-governance.md")
        self.assertIn("ChatGPT/Codex entry point", agents)
        self.assertIn("Gemini entry point", gemini)
        self.assertIn("docs/MODEL_POLICY.md", gemini)
        self.assertIn("single immutable hand-off command", gemini)
        self.assertIn("including on failure", gemini)
        self.assertIn("do not silently omit artifacts or infer PASS", gemini)
        self.assertIn("self-repair", gemini)
        self.assertIn("cannot be combined with a hardware Operator", gemini)
        self.assertIn("`gpt-6-astra`, high", adr)
        self.assertNotIn("gpt-5.6-", adr)
        self.assertNotIn("GEMINI.md", read(".gitignore").splitlines())

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

    def test_local_data_isolation_and_anonymization(self):
        gitignore = read(".gitignore").splitlines()
        for rule in (
            "/test_report.json",
            "/benchmark_report.json",
            "/benchmark_report.md",
            "/tests/clips/",
            "/tests/pixel_validator",
            "/.local/",
            "/validation-reports/",
        ):
            self.assertIn(rule, gitignore)

        log = read("docs/MODEL_USAGE_LOG.md")
        self.assertNotIn("/mnt/", log)
        self.assertNotIn("/home/", log)
        self.assertNotIn("The.Pitt", log)
        self.assertNotIn("The Pitt", log)
        attempt_family = log.split("### 2026-09-23 Flatpak GPU Next validation attempt family")[-1]
        self.assertNotIn("RX 9060 XT", attempt_family)
        self.assertNotIn("0000:03:00.0", attempt_family)
        self.assertNotIn("pci-0000_03_00_0", attempt_family)
        self.assertIn("fd27b83024d9ecf86d27a9a1698ed28d3eb87ddbfe8692f7751b9db96da909c7", log)
        self.assertIn("WARN (exit 0)", log)

        agents = read("AGENTS.md")
        policy = read("docs/MODEL_POLICY.md")
        for text in (agents, policy):
            self.assertIn("personal paths", text.lower())
            self.assertIn("anonymized", text.lower())


if __name__ == "__main__":
    unittest.main()
