"""Environment-independent tests for the bounded vendor smoke oracle."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "cinehdr_vendor_smoke", ROOT / "tests" / "test_nvidia.py"
)
smoke = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(smoke)


class VendorSmokeOracleTests(unittest.TestCase):
    def setUp(self):
        self.path_token = "a" * 64

    def valid_lines(self, *, api="opengl", mode="legacy", vendor="Intel", renderer="Iris"):
        return [
            f"INFO libmpv render backend requested={mode} active={api} fallback_reason=none",
            "INFO Render runtime libmpv=/app/lib/libmpv.so.2 "
            "mpv=mpv v0.41.0-dev-g97179bce7 "
            "libplacebo=v7.360.1 ffmpeg=8.1.2 configuration=pinned "
            f"OpenGL={vendor} / {renderer} / OpenGL 4.6",
            f"INFO Rendered first frame backend={api} target=1280x720 "
            "internal_format=0x881a depth=16",
            f"INFO Rendered first media frame backend={api} source=3840x2160 "
            f"time_pos=0.125000 path_token={self.path_token}",
            'INFO HDR Pipeline Telemetry: {"source_hdr": true, '
            '"target_trc": "pq", "dovi_profile": null}',
        ]

    def evaluated(self, lines, *, config_id="intel-legacy", fixture_id="hdr10"):
        parsed = smoke.parse_log_lines(lines)
        result = {
            "exit_code": -2,
            "exited_early": False,
            "forced_termination": False,
            "log_written": bool(parsed["lines"]),
            "errors": parsed["errors"],
            "file_open_errors": parsed["file_open_errors"],
            "backend_state": parsed["backend_state"],
            "first_frame": parsed["first_frame"],
            "media_frame": parsed["media_frame"],
            "telemetry": parsed["telemetry"],
            "runtime_line": parsed["runtime_line"],
        }
        return smoke.evaluate_result(
            result,
            smoke.TEST_CONFIGS[config_id],
            smoke.FIXTURE_SPECS[fixture_id],
            expected_path_token=self.path_token,
        )

    def test_valid_loaded_hdr_frame_passes_narrow_oracle(self):
        self.assertEqual(self.evaluated(self.valid_lines())["status"], "PASS")

    def test_backend_initialization_and_empty_render_are_not_media_evidence(self):
        lines = self.valid_lines()
        lines = [line for line in lines if "Rendered first media frame" not in line]
        result = self.evaluated(lines)
        self.assertEqual(result["status"], "MEDIA_FRAME_NOT_OBSERVED")

    def test_rendered_media_must_match_prescribed_path_token(self):
        lines = [line.replace("a" * 64, "b" * 64) for line in self.valid_lines()]
        result = self.evaluated(lines)
        self.assertEqual(result["status"], "MEDIA_IDENTITY_MISMATCH")

    def test_file_error_is_strict_even_when_other_observations_pass(self):
        lines = self.valid_lines() + ["WARNING File error path: /private/wrong.mkv"]
        result = self.evaluated(lines)
        self.assertEqual(result["status"], "FILE_PLAYBACK_ERROR")

    def test_backend_fallback_or_wrong_api_fails(self):
        lines = self.valid_lines()
        lines[0] = (
            "INFO libmpv render backend requested=gpu-next active=opengl "
            "fallback_reason=unpatched"
        )
        result = self.evaluated(lines)
        self.assertEqual(result["status"], "BACKEND_MISMATCH")

    def test_unpinned_runtime_fails(self):
        lines = [line.replace("ffmpeg=8.1.2", "ffmpeg=7.1.3") for line in self.valid_lines()]
        result = self.evaluated(lines)
        self.assertEqual(result["status"], "RUNTIME_MISMATCH")

    def test_source_hdr_and_dovi_contracts_are_checked(self):
        lines = [line.replace('"source_hdr": true', '"source_hdr": false') for line in self.valid_lines()]
        self.assertEqual(self.evaluated(lines)["status"], "TELEMETRY_MISMATCH")

        dovi_lines = self.valid_lines()
        dovi_lines[-1] = dovi_lines[-1].replace(
            '"dovi_profile": null', '"dovi_profile": 8'
        )
        self.assertEqual(
            self.evaluated(dovi_lines, fixture_id="dovi-p8")["status"],
            "PASS",
        )

    def test_stub_is_retained_as_noncoverage(self):
        lines = self.valid_lines() + [
            "ERROR render context creation failed: API function is a stub only"
        ]
        result = self.evaluated(lines)
        self.assertEqual(result["status"], "STUB_UNSUPPORTED")

    def test_missing_fixture_blocks_preflight_instead_of_skipping(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(smoke.PreflightError):
                smoke.resolve_fixture_paths(directory, ["hdr10"])

    def test_changed_fixture_hash_blocks_preflight(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "HDR.mp4"
            path.write_bytes(b"not the pinned fixture")
            with self.assertRaises(smoke.PreflightError):
                smoke.validate_fixture_hashes({"hdr10": path})

    def test_selection_is_one_gpu_family_and_bounded(self):
        smoke._validate_selection(["intel-legacy", "intel-gpu-next"], ["hdr10"])
        with self.assertRaises(smoke.PreflightError):
            smoke._validate_selection(["intel-legacy", "nvidia-legacy"], ["hdr10"])
        with self.assertRaises(smoke.PreflightError):
            smoke._validate_selection(
                ["intel-legacy"],
                ["hdr10", "hlg", "dovi-p8"],
            )

    def test_flatpak_command_has_only_path_scoped_filesystems(self):
        command = smoke._application_command(
            smoke.TEST_CONFIGS["intel-gpu-next"],
            Path("/media/HDR.mkv"),
            Path("/tmp/cinehdr-smoke-artifacts"),
            Path("/tmp/cinehdr-smoke-artifacts/application.log"),
            Path("/tmp/cinehdr-smoke-artifacts/state"),
        )
        joined = "\n".join(command)
        self.assertIn("--filesystem=/media/HDR.mkv:ro", joined)
        self.assertIn("--filesystem=/tmp/cinehdr-smoke-artifacts:rw", joined)
        self.assertIn("--env=CINEHDR_RENDER_BACKEND=gpu-next", joined)
        self.assertIn("--env=GDK_BACKEND=wayland", joined)
        self.assertNotIn("--filesystem=host", joined)
        self.assertNotIn("--device=dri", joined)

    def test_nvidia_uses_same_flatpak_with_explicit_prime_environment(self):
        command = smoke._application_command(
            smoke.TEST_CONFIGS["nvidia-gpu-next"],
            Path("/media/HDR.mkv"),
            Path("/tmp/cinehdr-smoke-artifacts"),
            Path("/tmp/cinehdr-smoke-artifacts/application.log"),
            Path("/tmp/cinehdr-smoke-artifacts/state"),
        )
        joined = "\n".join(command)
        self.assertEqual(smoke.TEST_CONFIGS["nvidia-gpu-next"]["mode"], "flatpak")
        self.assertIn("--env=__NV_PRIME_RENDER_OFFLOAD=1", joined)
        self.assertIn("--env=__GLX_VENDOR_LIBRARY_NAME=nvidia", joined)

    def test_report_line_sanitizer_removes_personal_paths(self):
        line = f"failure in {Path.home()}/Movies/HDR.mkv under {smoke.PROJECT_ROOT}"
        sanitized = smoke._sanitize_line(line, [])
        self.assertNotIn(str(Path.home()), sanitized)
        self.assertNotIn(str(smoke.PROJECT_ROOT), sanitized)

    def test_source_contract_cannot_silently_skip_or_adjudicate_gate(self):
        source = (ROOT / "tests" / "test_nvidia.py").read_text(encoding="utf-8")
        self.assertNotIn("SKIPPED", source)
        self.assertIn('"gate_adjudication": "NOT_PERFORMED_BY_OPERATOR"', source)
        self.assertIn("validate_fixture_hashes(fixture_paths)", source)
        self.assertIn("OBSERVATIONS_FAILED", source)

    def test_handoff_document_keeps_hardware_not_run_until_rebuilt(self):
        document = (ROOT / "docs" / "GPU_VENDOR_SMOKE_VALIDATION.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("real hardware `NOT_RUN`", document)
        self.assertIn("Both Intel and NVIDIA use the same installed pinned Flatpak", document)
        self.assertIn("Do not execute either command", document)
        self.assertIn("select only `hdr10`", document)


if __name__ == "__main__":
    unittest.main()
