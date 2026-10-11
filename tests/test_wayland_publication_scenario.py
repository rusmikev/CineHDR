#!/usr/bin/env python3
# Copyright 2026 rusmikev / Diego Povliuk
# SPDX-License-Identifier: GPL-3.0-or-later

"""Offline unit tests for Gate 2W publication matrix validation scenario.

Verifies:
1. Shell syntax check of run_wayland_publication_validation.sh.
2. CLI dry-run execution without GPU/application launch.
3. Preflight failure on flatpak ps query failure.
4. Preflight failure on Candidate v4 commit mismatch.
5. Strict rejection of missing publication report.
6. Strict rejection of failed publication report (Overall result: FAIL).
7. Strict rejection of backend mismatch.
8. Strict rejection of renderer API mismatch.
9. Strict rejection of missing/incomplete publication states.
10. Strict rejection of missing instance ID.
11. Strict rejection of missing instance_info file.
12. Strict rejection of instance_info missing sections.
13. Strict rejection of instance_info app name mismatch.
14. Strict rejection of instance_info instance ID mismatch.
15. Strict rejection of instance_info app-commit mismatch or short SHA.
16. Strict rejection of instance_info runtime-commit mismatch or short SHA.
17. Strict rejection of missing process_exit_code.txt.
18. Strict rejection of non-zero process exit code.
19. Strict rejection of invalid process exit code format.
20. Strict rejection of disabled protocol trace checking (--no-protocol-trace).
21. Strict rejection of missing trace log.
22. Strict rejection of invalid/corrupt protocol trace.
23. Strict rejection of protocol trace containing fatal compositor error.
24. Strict protection and refusal in cleanup_instance for user-owned instances.
25. Strict protection and refusal in cleanup_instance for metadata mismatch.
26. Strict acceptance and machine-readable session_facts.json generation when
    valid publication report, trace, process exit code 0, and instance metadata match.
"""

from __future__ import annotations

import configparser
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

REPO_DIR = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_DIR / "run_wayland_publication_validation.sh"

EXP_APP_ID = "io.github.rusmikev.CineHDR"
CANDIDATE_V4_COMMIT = "8ed86c0b54694b75d1aaa9be75956d1d3a9d075901d90695e4e0d3d144970813"
EXP_APP_COMMIT = "a" * 64
EXP_RT_COMMIT = "0bd5091046708e64bc882e8349877cfbae713f6a1e2d0487a5f10af820a2d792"

VALID_TRACE_SNIPPET = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.012]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.013]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.014]  -> wp_image_description_creator_params_v1#50.set_primaries_named(1)
[ 1042.015]  -> wp_image_description_creator_params_v1#50.set_tf_named(9)
[ 1042.016]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.017]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#52)
[ 1042.018]  -> wp_image_description_creator_params_v1#52.set_primaries_named(6)
[ 1042.019]  -> wp_image_description_creator_params_v1#52.set_tf_named(11)
[ 1042.020]  -> wp_image_description_creator_params_v1#52.create(new id wp_image_description_v1#32)
[ 1042.021]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#54)
[ 1042.022]  -> wp_image_description_creator_params_v1#54.set_primaries_named(1)
[ 1042.023]  -> wp_image_description_creator_params_v1#54.set_tf_named(9)
[ 1042.024]  -> wp_image_description_creator_params_v1#54.create(new id wp_image_description_v1#33)
[ 1042.025]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#56)
[ 1042.026]  -> wp_image_description_creator_params_v1#56.set_primaries_named(6)
[ 1042.027]  -> wp_image_description_creator_params_v1#56.set_tf_named(11)
[ 1042.028]  -> wp_image_description_creator_params_v1#56.create(new id wp_image_description_v1#34)
[ 1042.029]  wp_image_description_v1#31.ready(101)
[ 1042.030]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.031]  -> wl_surface#61.attach(wl_buffer#81, 0, 0)
[ 1042.032]  -> wl_surface#61.commit()
[ 1042.033]  wp_image_description_v1#32.ready(102)
[ 1042.034]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#32, 0)
[ 1042.035]  -> wl_surface#61.attach(wl_buffer#82, 0, 0)
[ 1042.036]  -> wl_surface#61.commit()
[ 1042.037]  wp_image_description_v1#33.ready(103)
[ 1042.038]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#33, 0)
[ 1042.039]  -> wl_surface#61.attach(wl_buffer#83, 0, 0)
[ 1042.040]  -> wl_surface#61.commit()
[ 1042.041]  wp_image_description_v1#34.ready(104)
[ 1042.042]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#34, 0)
[ 1042.043]  -> wl_surface#61.attach(wl_buffer#84, 0, 0)
[ 1042.044]  -> wl_surface#61.commit()
""".strip()


def make_publication_report(
    *,
    result="PASS",
    expected_backend="legacy",
    active_api="opengl",
    pass_steps: list[str] | None = None,
    session_id: str | None = "test-session-12345",
    instance_id: str | None = "248765847",
    process_id: int | None = 2,
    override: bool = True,
    scope: bool = True,
    fbo_pixel_artifact: str | None = None,
    fbo_pixel_sha: str | None = None,
    fbo_pixel_verified: bool = True,
) -> str:
    if pass_steps is None:
        pass_steps = [
            "actual initial SDR publication",
            "switch SDR to HDR publication",
            "switch HDR to SDR publication",
            "switch SDR to HDR publication again",
        ]
    lines = [
        "CineHDR Gate 2 Wayland GDK Publication Validation Report",
        "",
        f"Overall result: {result}",
        "Mode: publication",
        f"Expected backend: {expected_backend}",
        f"Expected renderer API: {active_api}",
        f"Active renderer API: {active_api}",
        "Renderer status: active",
        "Hardware decoding: vaapi-copy",
    ]
    if override:
        lines.append("Surface submission test override: enabled")
    if scope:
        lines.append(
            "Override scope: test setup; not proof of Wayland submission or normal auto-mode behavior"
        )
    if session_id is not None:
        lines.append(f"Session ID: {session_id}")
    if instance_id is not None:
        lines.append(f"Flatpak instance ID: {instance_id}")
    if process_id is not None:
        lines.append(f"Process ID: {process_id}")
    lines.extend([
        "",
        "[Publication states]",
    ])
    for step in pass_steps:
        lines.append(f"PASS: {step} — ok")

    if fbo_pixel_artifact is not None:
        lines.extend([
            "",
            "[Paused transition FBO pixel validation]",
            "FBO pixel oracle status: PASS",
            f"FBO pixel artifact: {fbo_pixel_artifact}",
            f"FBO pixel artifact SHA-256: {fbo_pixel_sha or 'none'}",
            f"FBO pixel artifact verified: {fbo_pixel_verified}",
            "Tolerance (repeat SDR): 0.020 (preliminary)",
            "Min HDR distinction: 0.150",
            "Readback errors count: 0",
            "Captures count: 3",
            "  capture[initial_sdr]: epoch=1 fbo=GL_RGBA8 is_float=False dims=1920x1080 samples_hash=none",
            "  capture[hdr]: epoch=2 fbo=GL_RGBA16F is_float=True dims=1920x1080 samples_hash=none",
            "  capture[return_sdr]: epoch=3 fbo=GL_RGBA8 is_float=False dims=1920x1080 samples_hash=none",
        ])

    return "\n".join(lines) + "\n"


class WaylandPublicationScenarioTests(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="cinehdr_pub_test_")
        self.backend_dir = Path(self.test_dir) / "legacy"
        self.backend_dir.mkdir(parents=True)
        self.session_id = "test-session-12345"
        self.instance_id = "248765847"
        # Default process exit code to 0
        (self.backend_dir / "process_exit_code.txt").write_text("0\n", encoding="utf-8")
        # Default cleanup status to CLEAN and instance_terminated to True
        (self.backend_dir / "cleanup_status.json").write_text(
            json.dumps({"status": "CLEAN", "instance_terminated": True}),
            encoding="utf-8",
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _write_instance_info(
        self,
        app_id=EXP_APP_ID,
        app_commit=EXP_APP_COMMIT,
        runtime_commit=EXP_RT_COMMIT,
        instance_id=None,
    ):
        inst = instance_id or self.instance_id
        cp = configparser.ConfigParser()
        cp["Application"] = {"name": app_id}
        cp["Instance"] = {
            "instance-id": inst,
            "app-commit": app_commit,
            "runtime-commit": runtime_commit,
        }
        with open(self.backend_dir / "instance_info", "w", encoding="utf-8") as f:
            cp.write(f)

    def _write_valid_fbo_pixel_artifact(
        self,
        backend="legacy",
        stamp="20260926",
        overall_result="PASS",
        tolerance=0.020,
        min_distinction=0.150,
        modify_fn=None,
    ) -> tuple[Path, str, dict]:
        from src.gpu_validation import CANONICAL_PATCH_NAMES, compute_samples_hash
        sdr_samples = {
            name: (1.0, 1.0, 1.0, 1.0) if "white" in name else (0.0, 0.0, 0.0, 1.0)
            for name in CANONICAL_PATCH_NAMES
        }
        hdr_samples = {
            name: (0.58, 0.58, 0.58, 1.0) if "white" in name else (0.0, 0.0, 0.0, 1.0)
            for name in CANONICAL_PATCH_NAMES
        }
        phases = {}
        for phase, is_hdr, epoch, fbo_fmt, is_flt in (
            ("initial_sdr", False, 1, "GL_RGBA8", False),
            ("hdr", True, 2, "GL_RGBA16F", True),
            ("return_sdr", False, 3, "GL_RGBA8", False),
        ):
            smp = hdr_samples if is_hdr else sdr_samples
            samples_list = {k: list(v) for k, v in smp.items()}
            phases[phase] = {
                "status": "CAPTURED",
                "phase": phase,
                "slot_id": epoch - 1,
                "epoch": epoch,
                "frame_generation": epoch,
                "target_properties": (
                    {"target-trc": "pq", "target-prim": "bt.2020", "target-peak": 1000}
                    if is_hdr else
                    {"target-trc": "auto", "target-prim": "auto", "target-peak": "auto"}
                ),
                "fbo_format": fbo_fmt,
                "is_float": is_flt,
                "width": 1920,
                "height": 1080,
                "fixture_hash": "a" * 64,
                "raw_samples_hash": compute_samples_hash(smp),
                "patch_samples": samples_list,
            }
        data = {
            "schema": "cinehdr.gate2w.fbo-pixel-measurements.v1",
            "backend": backend,
            "session_id": self.session_id,
            "created_at": "2026-09-26 10:00:00 UTC",
            "overall_result": overall_result,
            "tolerance": float(tolerance),
            "tolerance_status": "preliminary",
            "min_hdr_distinction": float(min_distinction),
            "readback_errors": [],
            "provenance_errors": [],
            "phases": phases,
        }
        if modify_fn is not None:
            modify_fn(data)
        artifact_path = self.backend_dir / f"gate2-wayland-fbo-pixels-{backend}-{stamp}.json"
        artifact_text = json.dumps(data, indent=2, sort_keys=True) + "\n"
        artifact_path.write_text(artifact_text, encoding="utf-8")
        artifact_sha = hashlib.sha256(artifact_text.encode("utf-8")).hexdigest()
        return artifact_path, artifact_sha, data

    def _run_eval(self, backend="legacy", instance_id=None, check_trace=True, expected_app=EXP_APP_COMMIT):
        inst = self.instance_id if instance_id is None else instance_id
        cmd = [
            "bash",
            "-c",
            f'source "{SCRIPT_PATH}"\n'
            f'expected_app_commit="{expected_app}"\n'
            f'expected_runtime_commit="{EXP_RT_COMMIT}"\n'
            f'evaluate_session_results "{self.backend_dir}" "{backend}" "{self.session_id}" "{inst}" "{str(check_trace).lower()}"',
        ]
        return subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_DIR)

    def test_script_syntax(self):
        res = subprocess.run(["bash", "-n", str(SCRIPT_PATH)], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Syntax error in script: {res.stderr}")

    def test_preflight_blocks_when_candidate_v5_unpinned(self):
        """Preflight must block if Candidate v5 commit is empty / unpinned."""
        env = os.environ.copy()
        env["EXPECTED_APP_COMMIT"] = ""
        res = subprocess.run([str(SCRIPT_PATH), "--dry-run"], capture_output=True, text=True, env=env, cwd=REPO_DIR)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("PREFLIGHT_BLOCKED: Candidate v5 app commit is unpinned", res.stderr)

    def test_preflight_blocks_when_targeting_candidate_v4(self):
        """Preflight must block if Candidate v4 is targeted (forbidden for Run 4)."""
        env = os.environ.copy()
        env["EXPECTED_APP_COMMIT"] = CANDIDATE_V4_COMMIT
        res = subprocess.run([str(SCRIPT_PATH), "--dry-run"], capture_output=True, text=True, env=env, cwd=REPO_DIR)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("PREFLIGHT_BLOCKED: expected_app_commit points to Candidate v4", res.stderr)

    def test_dry_run_execution(self):
        mock_bin_dir = Path(self.test_dir) / "mock_bin_dryrun"
        mock_bin_dir.mkdir()
        mock_flatpak = mock_bin_dir / "flatpak"
        mock_flatpak.write_text(
            '#!/usr/bin/env bash\n'
            'if [[ "$1" == "ps" ]]; then\n'
            '    echo "instance application"\n'
            '    exit 0\n'
            'fi\n'
            'if [[ "$1" == "info" && "$2" == "--show-commit" ]]; then\n'
            '    if [[ "$3" == *"Platform"* ]]; then\n'
            f'        echo "{EXP_RT_COMMIT}"\n'
            '    else\n'
            f'        echo "{EXP_APP_COMMIT}"\n'
            '    fi\n'
            '    exit 0\n'
            'fi\n'
            'if [[ "$1" == "info" && "$2" == "--show-runtime" ]]; then\n'
            '    echo "org.gnome.Platform//50"\n'
            '    exit 0\n'
            'fi\n'
            'if [[ "$1" == "info" ]]; then\n'
            '    exit 0\n'
            'fi\n'
            'exec /usr/bin/flatpak "$@"\n'
        )
        mock_flatpak.chmod(mock_flatpak.stat().st_mode | stat.S_IEXEC)
        env = os.environ.copy()
        env["PATH"] = f"{mock_bin_dir}:{env.get('PATH', '')}"
        env["EXPECTED_APP_COMMIT"] = EXP_APP_COMMIT
        env["EXPECTED_RUNTIME_COMMIT"] = EXP_RT_COMMIT

        res = subprocess.run([str(SCRIPT_PATH), "--dry-run"], capture_output=True, text=True, env=env, cwd=REPO_DIR)
        self.assertEqual(res.returncode, 0, f"Dry run failed: {res.stderr}")
        self.assertIn("=== CineHDR Gate 2W Dry Run ===", res.stdout)
        self.assertIn("Flatpak Candidate v5: VERIFIED", res.stdout)
        self.assertIn("Active user instances protected:", res.stdout)
        self.assertIn("HDR fixture:", res.stdout)
        self.assertIn("SDR fixture:", res.stdout)
        self.assertIn("Dry run successful", res.stdout)

    def test_dry_run_help(self):
        res = subprocess.run([str(SCRIPT_PATH), "--help"], capture_output=True, text=True, cwd=REPO_DIR)
        self.assertEqual(res.returncode, 0)
        self.assertIn("Usage:", res.stderr)

    def test_invalid_argument(self):
        res = subprocess.run([str(SCRIPT_PATH), "--backend", "unknown_backend"], capture_output=True, text=True, cwd=REPO_DIR)
        self.assertEqual(res.returncode, 2)
        self.assertIn("Invalid --backend choice", res.stderr)

    def test_preflight_fails_on_flatpak_ps_failure(self):
        """Preflight must immediately fail if flatpak ps query fails."""
        mock_bin_dir = Path(self.test_dir) / "mock_bin"
        mock_bin_dir.mkdir()
        mock_flatpak = mock_bin_dir / "flatpak"
        mock_flatpak.write_text(
            '#!/usr/bin/env bash\n'
            'if [[ "$1" == "ps" ]]; then\n'
            '    echo "error: daemon connection lost" >&2\n'
            '    exit 1\n'
            'fi\n'
            'exec /usr/bin/flatpak "$@"\n'
        )
        mock_flatpak.chmod(mock_flatpak.stat().st_mode | stat.S_IEXEC)

        env = os.environ.copy()
        env["PATH"] = f"{mock_bin_dir}:{env.get('PATH', '')}"
        env["EXPECTED_APP_COMMIT"] = EXP_APP_COMMIT

        res = subprocess.run([str(SCRIPT_PATH), "--dry-run"], capture_output=True, text=True, env=env, cwd=REPO_DIR)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("PREFLIGHT_BLOCKED: flatpak ps failed to query running instances", res.stderr)

    def test_preflight_fails_on_candidate_commit_mismatch(self):
        """Preflight must fail if Candidate v5 commits do not match (no local dev fallback)."""
        mock_bin_dir = Path(self.test_dir) / "mock_bin_mismatch"
        mock_bin_dir.mkdir()
        mock_flatpak = mock_bin_dir / "flatpak"
        mock_flatpak.write_text(
            '#!/usr/bin/env bash\n'
            'if [[ "$1" == "info" && "$2" == "--show-commit" ]]; then\n'
            '    echo "foreign_untrusted_commit_sha12345"\n'
            '    exit 0\n'
            'fi\n'
            'exec /usr/bin/flatpak "$@"\n'
        )
        mock_flatpak.chmod(mock_flatpak.stat().st_mode | stat.S_IEXEC)

        env = os.environ.copy()
        env["PATH"] = f"{mock_bin_dir}:{env.get('PATH', '')}"
        env["EXPECTED_APP_COMMIT"] = EXP_APP_COMMIT

        res = subprocess.run([str(SCRIPT_PATH), "--dry-run"], capture_output=True, text=True, env=env, cwd=REPO_DIR)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("PREFLIGHT_BLOCKED: CineHDR Flatpak Candidate v5 commit mismatch", res.stderr)

    def test_rejection_missing_publication_report(self):
        (self.backend_dir / "wayland-client.raw.log").write_text(VALID_TRACE_SNIPPET, encoding="utf-8")
        self._write_instance_info()
        res = self._run_eval(backend="legacy")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("expected exactly 1 publication report", res.stderr)

    def test_rejection_failed_publication_report(self):
        rep = make_publication_report(result="FAIL", expected_backend="legacy", active_api="opengl")
        (self.backend_dir / "gate2-wayland-publication-legacy-20260926.txt").write_text(rep, encoding="utf-8")
        (self.backend_dir / "wayland-client.raw.log").write_text(VALID_TRACE_SNIPPET, encoding="utf-8")
        self._write_instance_info()
        res = self._run_eval(backend="legacy")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("did not PASS", res.stderr)

    def test_rejection_backend_mismatch(self):
        rep = make_publication_report(result="PASS", expected_backend="legacy", active_api="opengl")
        (self.backend_dir / "gate2-wayland-publication-gpu-next-20260926.txt").write_text(rep, encoding="utf-8")
        (self.backend_dir / "wayland-client.raw.log").write_text(VALID_TRACE_SNIPPET, encoding="utf-8")
        self._write_instance_info()
        res = self._run_eval(backend="gpu-next")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("expected backend mismatch", res.stderr)

    def test_rejection_renderer_api_mismatch(self):
        rep = make_publication_report(result="PASS", expected_backend="legacy", active_api="vulkan")
        (self.backend_dir / "gate2-wayland-publication-legacy-20260926.txt").write_text(rep, encoding="utf-8")
        (self.backend_dir / "wayland-client.raw.log").write_text(VALID_TRACE_SNIPPET, encoding="utf-8")
        self._write_instance_info()
        res = self._run_eval(backend="legacy")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("active renderer API mismatch", res.stderr)

    def test_rejection_incomplete_publication_states(self):
        # 3 states instead of 4
        rep = make_publication_report(result="PASS", expected_backend="legacy", active_api="opengl", pass_steps=[
            "actual initial SDR publication",
            "switch SDR to HDR publication",
            "switch HDR to SDR publication",
        ])
        (self.backend_dir / "gate2-wayland-publication-legacy-20260926.txt").write_text(rep, encoding="utf-8")
        (self.backend_dir / "wayland-client.raw.log").write_text(VALID_TRACE_SNIPPET, encoding="utf-8")
        self._write_instance_info()
        res = self._run_eval(backend="legacy")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("publication matrix incomplete", res.stderr)

    def test_rejection_unordered_publication_states(self):
        # 4 states but wrong order
        rep = make_publication_report(result="PASS", expected_backend="legacy", active_api="opengl", pass_steps=[
            "switch SDR to HDR publication",
            "actual initial SDR publication",
            "switch HDR to SDR publication",
            "switch SDR to HDR publication again",
        ])
        (self.backend_dir / "gate2-wayland-publication-legacy-20260926.txt").write_text(rep, encoding="utf-8")
        (self.backend_dir / "wayland-client.raw.log").write_text(VALID_TRACE_SNIPPET, encoding="utf-8")
        self._write_instance_info()
        res = self._run_eval(backend="legacy")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("sequence mismatch", res.stderr)

    def test_rejection_missing_instance_id(self):
        rep = make_publication_report(result="PASS", expected_backend="legacy", active_api="opengl")
        (self.backend_dir / "gate2-wayland-publication-legacy-20260926.txt").write_text(rep, encoding="utf-8")
        (self.backend_dir / "wayland-client.raw.log").write_text(VALID_TRACE_SNIPPET, encoding="utf-8")
        self._write_instance_info()
        res = self._run_eval(backend="legacy", instance_id="")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("missing instance ID", res.stderr)

    def test_rejection_missing_instance_info_file(self):
        rep = make_publication_report(result="PASS", expected_backend="legacy", active_api="opengl")
        (self.backend_dir / "gate2-wayland-publication-legacy-20260926.txt").write_text(rep, encoding="utf-8")
        (self.backend_dir / "wayland-client.raw.log").write_text(VALID_TRACE_SNIPPET, encoding="utf-8")
        # Do not create instance_info
        res = self._run_eval(backend="legacy")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("instance_info file missing", res.stderr)

    def test_rejection_instance_info_missing_sections(self):
        rep = make_publication_report(result="PASS", expected_backend="legacy", active_api="opengl")
        (self.backend_dir / "gate2-wayland-publication-legacy-20260926.txt").write_text(rep, encoding="utf-8")
        (self.backend_dir / "wayland-client.raw.log").write_text(VALID_TRACE_SNIPPET, encoding="utf-8")
        (self.backend_dir / "instance_info").write_text("[Unknown]\nfoo=bar\n", encoding="utf-8")
        res = self._run_eval(backend="legacy")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("missing required [Application] or [Instance] section", res.stderr)

    def test_rejection_instance_info_app_name_mismatch(self):
        rep = make_publication_report(result="PASS", expected_backend="legacy", active_api="opengl")
        (self.backend_dir / "gate2-wayland-publication-legacy-20260926.txt").write_text(rep, encoding="utf-8")
        (self.backend_dir / "wayland-client.raw.log").write_text(VALID_TRACE_SNIPPET, encoding="utf-8")
        self._write_instance_info(app_id="org.gnome.Totem")
        res = self._run_eval(backend="legacy")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("instance_info application mismatch", res.stderr)

    def test_rejection_instance_info_instance_id_mismatch(self):
        rep = make_publication_report(result="PASS", expected_backend="legacy", active_api="opengl")
        (self.backend_dir / "gate2-wayland-publication-legacy-20260926.txt").write_text(rep, encoding="utf-8")
        (self.backend_dir / "wayland-client.raw.log").write_text(VALID_TRACE_SNIPPET, encoding="utf-8")
        self._write_instance_info(instance_id="99999999")
        res = self._run_eval(backend="legacy", instance_id=self.instance_id)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("instance-id mismatch", res.stderr)

    def test_rejection_instance_info_app_commit_mismatch(self):
        rep = make_publication_report(result="PASS", expected_backend="legacy", active_api="opengl")
        (self.backend_dir / "gate2-wayland-publication-legacy-20260926.txt").write_text(rep, encoding="utf-8")
        (self.backend_dir / "wayland-client.raw.log").write_text(VALID_TRACE_SNIPPET, encoding="utf-8")
        self._write_instance_info(app_commit="0000000000000000000000000000000000000000000000000000000000000000")
        res = self._run_eval(backend="legacy")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("app-commit mismatch", res.stderr)

    def test_rejection_instance_info_short_commit_sha(self):
        rep = make_publication_report(result="PASS", expected_backend="legacy", active_api="opengl")
        (self.backend_dir / "gate2-wayland-publication-legacy-20260926.txt").write_text(rep, encoding="utf-8")
        (self.backend_dir / "wayland-client.raw.log").write_text(VALID_TRACE_SNIPPET, encoding="utf-8")
        self._write_instance_info(app_commit=EXP_APP_COMMIT[:16])
        res = self._run_eval(backend="legacy")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("app-commit mismatch", res.stderr)

    def test_rejection_instance_info_runtime_commit_mismatch(self):
        rep = make_publication_report(result="PASS", expected_backend="legacy", active_api="opengl")
        (self.backend_dir / "gate2-wayland-publication-legacy-20260926.txt").write_text(rep, encoding="utf-8")
        (self.backend_dir / "wayland-client.raw.log").write_text(VALID_TRACE_SNIPPET, encoding="utf-8")
        self._write_instance_info(runtime_commit="1111111111111111111111111111111111111111111111111111111111111111")
        res = self._run_eval(backend="legacy")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("runtime-commit mismatch", res.stderr)

    def test_rejection_missing_process_exit_code_file(self):
        rep = make_publication_report(result="PASS", expected_backend="legacy", active_api="opengl")
        (self.backend_dir / "gate2-wayland-publication-legacy-20260926.txt").write_text(rep, encoding="utf-8")
        (self.backend_dir / "wayland-client.raw.log").write_text(VALID_TRACE_SNIPPET, encoding="utf-8")
        self._write_instance_info()
        (self.backend_dir / "process_exit_code.txt").unlink()
        res = self._run_eval(backend="legacy")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("process_exit_code.txt missing", res.stderr)

    def test_rejection_nonzero_process_exit_code(self):
        rep = make_publication_report(result="PASS", expected_backend="legacy", active_api="opengl")
        (self.backend_dir / "gate2-wayland-publication-legacy-20260926.txt").write_text(rep, encoding="utf-8")
        (self.backend_dir / "wayland-client.raw.log").write_text(VALID_TRACE_SNIPPET, encoding="utf-8")
        self._write_instance_info()
        (self.backend_dir / "process_exit_code.txt").write_text("143\n", encoding="utf-8")
        res = self._run_eval(backend="legacy")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("non-zero exit code 143", res.stderr)

    def test_rejection_invalid_process_exit_code_format(self):
        rep = make_publication_report(result="PASS", expected_backend="legacy", active_api="opengl")
        (self.backend_dir / "gate2-wayland-publication-legacy-20260926.txt").write_text(rep, encoding="utf-8")
        (self.backend_dir / "wayland-client.raw.log").write_text(VALID_TRACE_SNIPPET, encoding="utf-8")
        self._write_instance_info()
        (self.backend_dir / "process_exit_code.txt").write_text("corrupted_val\n", encoding="utf-8")
        res = self._run_eval(backend="legacy")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("invalid process_exit_code format", res.stderr)

    def test_rejection_missing_cleanup_status_json(self):
        rep = make_publication_report(result="PASS", expected_backend="legacy", active_api="opengl")
        (self.backend_dir / "gate2-wayland-publication-legacy-20260926.txt").write_text(rep, encoding="utf-8")
        (self.backend_dir / "wayland-client.raw.log").write_text(VALID_TRACE_SNIPPET, encoding="utf-8")
        self._write_instance_info()
        (self.backend_dir / "cleanup_status.json").unlink()
        res = self._run_eval(backend="legacy")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("cleanup_status.json missing", res.stderr)

    def test_rejection_cleanup_status_not_clean(self):
        rep = make_publication_report(result="PASS", expected_backend="legacy", active_api="opengl")
        (self.backend_dir / "gate2-wayland-publication-legacy-20260926.txt").write_text(rep, encoding="utf-8")
        (self.backend_dir / "wayland-client.raw.log").write_text(VALID_TRACE_SNIPPET, encoding="utf-8")
        self._write_instance_info()
        (self.backend_dir / "cleanup_status.json").write_text(
            json.dumps({"status": "DIRTY", "instance_terminated": True}),
            encoding="utf-8",
        )
        res = self._run_eval(backend="legacy")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("cleanup_status is not CLEAN", res.stderr)

    def test_rejection_cleanup_status_instance_not_terminated(self):
        rep = make_publication_report(result="PASS", expected_backend="legacy", active_api="opengl")
        (self.backend_dir / "gate2-wayland-publication-legacy-20260926.txt").write_text(rep, encoding="utf-8")
        (self.backend_dir / "wayland-client.raw.log").write_text(VALID_TRACE_SNIPPET, encoding="utf-8")
        self._write_instance_info()
        (self.backend_dir / "cleanup_status.json").write_text(
            json.dumps({"status": "CLEAN", "instance_terminated": False, "error": "timeout"}),
            encoding="utf-8",
        )
        res = self._run_eval(backend="legacy")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("Flatpak test instance was not verified terminated in cleanup_status.json", res.stderr)

    def test_rejection_publication_report_session_id_mismatch(self):
        rep = make_publication_report(result="PASS", expected_backend="legacy", active_api="opengl", session_id="foreign-session-id")
        (self.backend_dir / "gate2-wayland-publication-legacy-20260926.txt").write_text(rep, encoding="utf-8")
        (self.backend_dir / "wayland-client.raw.log").write_text(VALID_TRACE_SNIPPET, encoding="utf-8")
        self._write_instance_info()
        res = self._run_eval(backend="legacy")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("publication report session ID mismatch", res.stderr)

    def test_rejection_publication_report_instance_id_mismatch(self):
        rep = make_publication_report(result="PASS", expected_backend="legacy", active_api="opengl", instance_id="999999999")
        (self.backend_dir / "gate2-wayland-publication-legacy-20260926.txt").write_text(rep, encoding="utf-8")
        (self.backend_dir / "wayland-client.raw.log").write_text(VALID_TRACE_SNIPPET, encoding="utf-8")
        self._write_instance_info()
        res = self._run_eval(backend="legacy")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("publication report Flatpak instance ID mismatch", res.stderr)

    def test_rejection_publication_report_missing_session_id(self):
        rep = make_publication_report(result="PASS", expected_backend="legacy", active_api="opengl", session_id=None)
        (self.backend_dir / "gate2-wayland-publication-legacy-20260926.txt").write_text(rep, encoding="utf-8")
        (self.backend_dir / "wayland-client.raw.log").write_text(VALID_TRACE_SNIPPET, encoding="utf-8")
        self._write_instance_info()
        res = self._run_eval(backend="legacy")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("publication report missing Session ID", res.stderr)

    def test_rejection_publication_report_missing_instance_id(self):
        rep = make_publication_report(result="PASS", expected_backend="legacy", active_api="opengl", instance_id=None)
        (self.backend_dir / "gate2-wayland-publication-legacy-20260926.txt").write_text(rep, encoding="utf-8")
        (self.backend_dir / "wayland-client.raw.log").write_text(VALID_TRACE_SNIPPET, encoding="utf-8")
        self._write_instance_info()
        res = self._run_eval(backend="legacy")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("publication report missing Flatpak instance ID", res.stderr)

    def test_rejection_publication_report_missing_process_id(self):
        rep = make_publication_report(result="PASS", expected_backend="legacy", active_api="opengl", process_id=None)
        (self.backend_dir / "gate2-wayland-publication-legacy-20260926.txt").write_text(rep, encoding="utf-8")
        (self.backend_dir / "wayland-client.raw.log").write_text(VALID_TRACE_SNIPPET, encoding="utf-8")
        self._write_instance_info()
        res = self._run_eval(backend="legacy")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("publication report missing or invalid Process ID", res.stderr)

    def test_rejection_disabled_protocol_trace_check(self):
        """Forbids PASS and container_session_correlated when protocol trace is not checked."""
        rep = make_publication_report(result="PASS", expected_backend="legacy", active_api="opengl")
        (self.backend_dir / "gate2-wayland-publication-legacy-20260926.txt").write_text(rep, encoding="utf-8")
        (self.backend_dir / "wayland-client.raw.log").write_text(VALID_TRACE_SNIPPET, encoding="utf-8")
        self._write_instance_info()
        res = self._run_eval(backend="legacy", check_trace=False)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("Gate 2W requires protocol trace checking", res.stderr)

    def test_rejection_missing_trace_file(self):
        rep = make_publication_report(result="PASS", expected_backend="legacy", active_api="opengl")
        (self.backend_dir / "gate2-wayland-publication-legacy-20260926.txt").write_text(rep, encoding="utf-8")
        self._write_instance_info()
        res = self._run_eval(backend="legacy", check_trace=True)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("wayland trace log missing", res.stderr)

    def test_rejection_broken_wayland_trace(self):
        rep = make_publication_report(result="PASS", expected_backend="legacy", active_api="opengl")
        (self.backend_dir / "gate2-wayland-publication-legacy-20260926.txt").write_text(rep, encoding="utf-8")
        broken_trace = "some random log line without color management\n"
        (self.backend_dir / "wayland-client.raw.log").write_text(broken_trace, encoding="utf-8")
        self._write_instance_info()
        res = self._run_eval(backend="legacy")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("Wayland color trace rejected", res.stderr)

    def test_rejection_fatal_error_in_wayland_trace(self):
        rep = make_publication_report(result="PASS", expected_backend="legacy", active_api="opengl")
        (self.backend_dir / "gate2-wayland-publication-legacy-20260926.txt").write_text(rep, encoding="utf-8")
        trace_with_error = VALID_TRACE_SNIPPET + '\nwl_display#1.error(wl_surface#71, 1, "protocol error")\n'
        (self.backend_dir / "wayland-client.raw.log").write_text(trace_with_error, encoding="utf-8")
        self._write_instance_info()
        res = self._run_eval(backend="legacy")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("Wayland color trace rejected", res.stderr)

    def test_rejection_trace_on_unrelated_surface(self):
        """Trace where color management is bound to an unrelated surface instead of CineHDR window fails."""
        rep = make_publication_report(result="PASS", expected_backend="legacy", active_api="opengl")
        (self.backend_dir / "gate2-wayland-publication-legacy-20260926.txt").write_text(rep, encoding="utf-8")
        unrelated_surface_trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wl_compositor#1.create_surface(new id wl_surface#99)
[ 1042.010]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#99)
[ 1042.011]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.012]  -> wp_image_description_creator_params_v1#50.set_primaries_named(1)
[ 1042.013]  -> wp_image_description_creator_params_v1#50.set_tf_named(9)
[ 1042.014]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.015]  wp_image_description_v1#31.ready(101)
[ 1042.016]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.017]  -> wl_surface#99.commit()
        """.strip()
        (self.backend_dir / "wayland-client.raw.log").write_text(unrelated_surface_trace, encoding="utf-8")
        self._write_instance_info()
        res = self._run_eval(backend="legacy")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("wp_color_manager_v1.get_surface was not called for CineHDR's toplevel window surface", res.stderr)

    def test_cleanup_instance_refuses_user_instance(self):
        """cleanup_instance must refuse to kill any user-owned instance recorded in preflight."""
        cmd = [
            "bash",
            "-c",
            f'source "{SCRIPT_PATH}"\n'
            'user_instances["2266424554"]=1\n'
            'cleanup_instance "2266424554"',
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_DIR)
        self.assertEqual(res.returncode, 1)
        self.assertIn("CRITICAL: refusal to kill user-owned CineHDR instance: 2266424554", res.stderr)

    def test_cleanup_instance_refuses_non_numeric_id(self):
        """cleanup_instance must reject non-numeric instance ID."""
        cmd = [
            "bash",
            "-c",
            f'source "{SCRIPT_PATH}"\n'
            'cleanup_instance "abc;killall"',
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_DIR)
        self.assertEqual(res.returncode, 1)
        self.assertIn("SECURITY ERROR: cleanup_instance received invalid instance ID", res.stderr)

    def test_cleanup_instance_refuses_metadata_mismatch(self):
        """cleanup_instance re-verifies metadata and refuses to kill on mismatch."""
        fake_runtime = Path(self.test_dir) / "run_user"
        fake_inst_dir = fake_runtime / ".flatpak" / "12345"
        fake_inst_dir.mkdir(parents=True)
        (fake_inst_dir / "info").write_text(
            "[Application]\nname=io.github.rusmikev.CineHDR\n"
            "[Instance]\ninstance-id=12345\napp-commit=foreign_commit_000\nruntime-commit=rt\n"
        )
        cmd = [
            "bash",
            "-c",
            f'source "{SCRIPT_PATH}"\n'
            f'runtime_base="{fake_runtime}"\n'
            f'expected_app_commit="{EXP_APP_COMMIT}"\n'
            'cleanup_instance "12345"',
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_DIR)
        self.assertEqual(res.returncode, 1)
        self.assertIn("SECURITY ERROR: metadata mismatch on instance 12345, refusing to kill!", res.stderr)

    def test_cleanup_instance_fails_closed_when_flatpak_ps_fails(self):
        """cleanup_instance must fail-closed (return 1) if flatpak ps query fails during polling."""
        fake_runtime = Path(self.test_dir) / "run_user_ps_fail"
        fake_inst_dir = fake_runtime / ".flatpak" / "12345"
        fake_inst_dir.mkdir(parents=True)
        (fake_inst_dir / "info").write_text(
            f"[Application]\nname={EXP_APP_ID}\n"
            f"[Instance]\ninstance-id=12345\napp-commit={EXP_APP_COMMIT}\nruntime-commit={EXP_RT_COMMIT}\n"
        )
        mock_bin_dir = Path(self.test_dir) / "mock_bin_cleanup_fail"
        mock_bin_dir.mkdir()
        mock_flatpak = mock_bin_dir / "flatpak"
        mock_flatpak.write_text(
            '#!/usr/bin/env bash\n'
            'if [[ "$1" == "kill" ]]; then exit 0; fi\n'
            'if [[ "$1" == "ps" ]]; then echo "daemon error" >&2; exit 1; fi\n'
            'exec /usr/bin/flatpak "$@"\n'
        )
        mock_flatpak.chmod(mock_flatpak.stat().st_mode | stat.S_IEXEC)

        cmd = [
            "bash",
            "-c",
            f'source "{SCRIPT_PATH}"\n'
            f'export PATH="{mock_bin_dir}:$PATH"\n'
            f'runtime_base="{fake_runtime}"\n'
            f'expected_app_commit="{EXP_APP_COMMIT}"\n'
            f'expected_runtime_commit="{EXP_RT_COMMIT}"\n'
            'cleanup_instance "12345"',
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_DIR)
        self.assertEqual(res.returncode, 1)
        self.assertIn("WARNING: Flatpak instance 12345 was not confirmed terminated", res.stderr)

    def test_cleanup_instance_fails_closed_when_instance_still_listed(self):
        """cleanup_instance must fail-closed if flatpak ps continues to list the instance."""
        fake_runtime = Path(self.test_dir) / "run_user_still_listed"
        fake_inst_dir = fake_runtime / ".flatpak" / "12345"
        fake_inst_dir.mkdir(parents=True)
        (fake_inst_dir / "info").write_text(
            f"[Application]\nname={EXP_APP_ID}\n"
            f"[Instance]\ninstance-id=12345\napp-commit={EXP_APP_COMMIT}\nruntime-commit={EXP_RT_COMMIT}\n"
        )
        mock_bin_dir = Path(self.test_dir) / "mock_bin_still_listed"
        mock_bin_dir.mkdir()
        mock_flatpak = mock_bin_dir / "flatpak"
        mock_flatpak.write_text(
            '#!/usr/bin/env bash\n'
            'if [[ "$1" == "kill" ]]; then exit 0; fi\n'
            'if [[ "$1" == "ps" ]]; then echo "12345"; exit 0; fi\n'
            'exec /usr/bin/flatpak "$@"\n'
        )
        mock_flatpak.chmod(mock_flatpak.stat().st_mode | stat.S_IEXEC)

        cmd = [
            "bash",
            "-c",
            f'source "{SCRIPT_PATH}"\n'
            f'export PATH="{mock_bin_dir}:$PATH"\n'
            f'runtime_base="{fake_runtime}"\n'
            f'expected_app_commit="{EXP_APP_COMMIT}"\n'
            f'expected_runtime_commit="{EXP_RT_COMMIT}"\n'
            'cleanup_instance "12345"',
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_DIR)
        self.assertEqual(res.returncode, 1)
        self.assertIn("WARNING: Flatpak instance 12345 was not confirmed terminated", res.stderr)

    def test_report_without_override_or_scope_is_rejected(self):
        for override, scope in ((False, True), (True, False)):
            with self.subTest(override=override, scope=scope):
                report = make_publication_report(override=override, scope=scope)
                (self.backend_dir / "gate2-wayland-publication-legacy-20260926.txt").write_text(
                    report, encoding="utf-8"
                )
                result = self._run_eval(backend="legacy")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("override", result.stderr.lower())

    def test_acceptance_valid_same_session_evidence(self):
        """Positive test verifying full proven identity, metadata, exit code 0, trace PASS, and FBO pixel artifact."""
        pix_path, pix_sha, pix_data = self._write_valid_fbo_pixel_artifact(backend="legacy")
        rep = make_publication_report(
            result="PASS",
            expected_backend="legacy",
            active_api="opengl",
            fbo_pixel_artifact=pix_path.name,
            fbo_pixel_sha=pix_sha,
            fbo_pixel_verified=True,
        )
        (self.backend_dir / "gate2-wayland-publication-legacy-20260926.txt").write_text(rep, encoding="utf-8")
        (self.backend_dir / "wayland-client.raw.log").write_text(VALID_TRACE_SNIPPET, encoding="utf-8")
        self._write_instance_info()

        res = self._run_eval(backend="legacy")
        self.assertEqual(res.returncode, 0, f"Evaluation failed: {res.stderr}")
        self.assertIn("PASS (legacy)", res.stdout)

        facts_file = self.backend_dir / "session_facts.json"
        self.assertTrue(facts_file.exists())
        facts = json.loads(facts_file.read_text(encoding="utf-8"))
        self.assertEqual(facts["backend_result"], "PASS")
        self.assertFalse(facts["gate2w_overall_closed"])
        self.assertEqual(facts["backend"], "legacy")
        self.assertEqual(facts["session_id"], self.session_id)
        self.assertEqual(facts["observed_instance_id"], self.instance_id)
        self.assertEqual(facts["observed_process_exit_code"], 0)
        self.assertTrue(facts["container_session_correlated"])
        self.assertFalse(facts["same_process_wire_correlated"])
        self.assertIn("process_correlation_scope", facts)
        self.assertFalse(facts["process_correlation_scope"]["same_process_wire_proven"])
        self.assertFalse(facts["process_correlation_scope"]["per_frame_presentation_certified"])
        self.assertFalse(facts["process_correlation_scope"]["compositor_scanout_certified"])
        self.assertTrue(facts["instance_metadata_verified"])
        self.assertEqual(facts["expected_app_commit"], EXP_APP_COMMIT)
        self.assertEqual(facts["expected_runtime_commit"], EXP_RT_COMMIT)
        self.assertEqual(facts["publication_report"]["states_passed"], 4)
        self.assertEqual(facts["wayland_trace"]["protocol_status"], "PASS")
        self.assertEqual(facts["wayland_trace"]["oracle"], "primary_composite_window")
        self.assertTrue(facts["wayland_trace"]["composite_window_surface_proven"])
        self.assertEqual(facts["wayland_trace"]["toplevel_wl_surface_id"], 61)
        self.assertIn("fbo_pixel_measurements", facts)
        self.assertEqual(facts["fbo_pixel_measurements"]["path"], pix_path.name)
        self.assertEqual(facts["fbo_pixel_measurements"]["sha256"], pix_sha)
        self.assertEqual(facts["fbo_pixel_measurements"]["status"], "PASS")
        self.assertEqual(facts["fbo_pixel_measurements"]["phases_captured"], ["initial_sdr", "hdr", "return_sdr"])
        self.assertEqual(facts["fbo_pixel_measurements"]["tolerance_status"], "preliminary")

    def test_eval_fails_when_fbo_pixel_artifact_is_missing(self):
        """Evaluation strictly rejects session when mandatory FBO pixel artifact is missing."""
        rep = make_publication_report(
            result="PASS",
            expected_backend="legacy",
            active_api="opengl",
            fbo_pixel_artifact="gate2-wayland-fbo-pixels-legacy-none.json",
            fbo_pixel_sha="a" * 64,
            fbo_pixel_verified=True,
        )
        (self.backend_dir / "gate2-wayland-publication-legacy-20260926.txt").write_text(rep, encoding="utf-8")
        (self.backend_dir / "wayland-client.raw.log").write_text(VALID_TRACE_SNIPPET, encoding="utf-8")
        self._write_instance_info()

        res = self._run_eval(backend="legacy")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("expected exactly 1 FBO pixel measurements artifact for legacy, found 0", res.stderr)

    def test_eval_fails_when_multiple_fbo_pixel_artifacts_found(self):
        """Evaluation strictly rejects session when ambiguous multiple pixel artifacts exist."""
        self._write_valid_fbo_pixel_artifact(backend="legacy", stamp="20260926-1")
        self._write_valid_fbo_pixel_artifact(backend="legacy", stamp="20260926-2")
        rep = make_publication_report(
            result="PASS",
            expected_backend="legacy",
            active_api="opengl",
            fbo_pixel_artifact="gate2-wayland-fbo-pixels-legacy-20260926-1.json",
            fbo_pixel_sha="a" * 64,
            fbo_pixel_verified=True,
        )
        (self.backend_dir / "gate2-wayland-publication-legacy-20260926.txt").write_text(rep, encoding="utf-8")
        (self.backend_dir / "wayland-client.raw.log").write_text(VALID_TRACE_SNIPPET, encoding="utf-8")
        self._write_instance_info()

        res = self._run_eval(backend="legacy")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("expected exactly 1 FBO pixel measurements artifact for legacy, found 2", res.stderr)

    def test_eval_fails_when_fbo_pixel_artifact_is_incomplete(self):
        """Evaluation strictly rejects session when FBO pixel artifact has missing phase or invalid schema."""
        def make_incomplete(d):
            d["phases"]["return_sdr"]["status"] = "MISSING"
            d["phases"]["return_sdr"]["epoch"] = None
        pix_path, pix_sha, _ = self._write_valid_fbo_pixel_artifact(backend="legacy", modify_fn=make_incomplete)
        rep = make_publication_report(
            result="PASS",
            expected_backend="legacy",
            active_api="opengl",
            fbo_pixel_artifact=pix_path.name,
            fbo_pixel_sha=pix_sha,
            fbo_pixel_verified=True,
        )
        (self.backend_dir / "gate2-wayland-publication-legacy-20260926.txt").write_text(rep, encoding="utf-8")
        (self.backend_dir / "wayland-client.raw.log").write_text(VALID_TRACE_SNIPPET, encoding="utf-8")
        self._write_instance_info()

        res = self._run_eval(backend="legacy")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("mandatory FBO pixel measurements artifact is incomplete or invalid", res.stderr)

    def test_eval_fails_when_fbo_pixel_artifact_hash_mismatched(self):
        """Evaluation strictly rejects session when pixel data was tampered with (raw_samples_hash mismatch)."""
        def tamper_pixel(d):
            # Change red component without updating raw_samples_hash
            d["phases"]["hdr"]["patch_samples"]["reference_white"][0] += 0.05
        pix_path, pix_sha, _ = self._write_valid_fbo_pixel_artifact(backend="legacy", modify_fn=tamper_pixel)
        rep = make_publication_report(
            result="PASS",
            expected_backend="legacy",
            active_api="opengl",
            fbo_pixel_artifact=pix_path.name,
            fbo_pixel_sha=pix_sha,
            fbo_pixel_verified=True,
        )
        (self.backend_dir / "gate2-wayland-publication-legacy-20260926.txt").write_text(rep, encoding="utf-8")
        (self.backend_dir / "wayland-client.raw.log").write_text(VALID_TRACE_SNIPPET, encoding="utf-8")
        self._write_instance_info()

        res = self._run_eval(backend="legacy")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("raw_samples_hash mismatch", res.stderr)

    def test_eval_fails_when_publication_report_lacks_fbo_pixel_reference(self):
        """Evaluation rejects session when publication report lacks reference to the FBO pixel artifact."""
        pix_path, pix_sha, _ = self._write_valid_fbo_pixel_artifact(backend="legacy")
        # make report without fbo_pixel_artifact
        rep = make_publication_report(result="PASS", expected_backend="legacy", active_api="opengl")
        (self.backend_dir / "gate2-wayland-publication-legacy-20260926.txt").write_text(rep, encoding="utf-8")
        (self.backend_dir / "wayland-client.raw.log").write_text(VALID_TRACE_SNIPPET, encoding="utf-8")
        self._write_instance_info()

        res = self._run_eval(backend="legacy")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("publication report missing reference to FBO pixel artifact", res.stderr)

    def test_eval_fails_when_publication_report_fbo_pixel_sha_mismatched(self):
        """Evaluation rejects session when report records mismatched SHA-256 for pixel artifact."""
        pix_path, pix_sha, _ = self._write_valid_fbo_pixel_artifact(backend="legacy")
        rep = make_publication_report(
            result="PASS",
            expected_backend="legacy",
            active_api="opengl",
            fbo_pixel_artifact=pix_path.name,
            fbo_pixel_sha="f" * 64,  # mismatched SHA
            fbo_pixel_verified=True,
        )
        (self.backend_dir / "gate2-wayland-publication-legacy-20260926.txt").write_text(rep, encoding="utf-8")
        (self.backend_dir / "wayland-client.raw.log").write_text(VALID_TRACE_SNIPPET, encoding="utf-8")
        self._write_instance_info()

        res = self._run_eval(backend="legacy")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("publication report FBO pixel artifact SHA-256 mismatch", res.stderr)

    def test_eval_fails_when_publication_report_fbo_pixel_not_verified(self):
        """Evaluation rejects session when report records 'FBO pixel artifact verified: False'."""
        pix_path, pix_sha, _ = self._write_valid_fbo_pixel_artifact(backend="legacy")
        rep = make_publication_report(
            result="PASS",
            expected_backend="legacy",
            active_api="opengl",
            fbo_pixel_artifact=pix_path.name,
            fbo_pixel_sha=pix_sha,
            fbo_pixel_verified=False,
        )
        (self.backend_dir / "gate2-wayland-publication-legacy-20260926.txt").write_text(rep, encoding="utf-8")
        (self.backend_dir / "wayland-client.raw.log").write_text(VALID_TRACE_SNIPPET, encoding="utf-8")
        self._write_instance_info()

        res = self._run_eval(backend="legacy")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("publication report does not certify FBO pixel artifact verification", res.stderr)

    def test_gate2w_summary_single_backend_remains_open(self):
        """Single backend run writes gate2w_summary.json with status OPEN / PARTIAL_NOT_CLOSED."""
        run_dir = Path(self.test_dir) / "run_summary_test"
        legacy_dir = run_dir / "legacy"
        legacy_dir.mkdir(parents=True)
        facts = {
            "backend": "legacy",
            "backend_result": "PASS",
            "container_session_correlated": True,
            "same_process_wire_correlated": False,
        }
        (legacy_dir / "session_facts.json").write_text(json.dumps(facts), encoding="utf-8")

        cmd = [
            "bash",
            "-c",
            f'source "{SCRIPT_PATH}"\n'
            f'run_dir="{run_dir}"\n'
            'write_gate2w_summary "legacy"',
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_DIR)
        self.assertEqual(res.returncode, 0, res.stderr)
        summary_file = run_dir / "gate2w_summary.json"
        self.assertTrue(summary_file.exists())
        summary = json.loads(summary_file.read_text(encoding="utf-8"))
        self.assertEqual(summary["gate2w_status"], "OPEN")
        self.assertFalse(summary["gate2w_closed"])
        self.assertIn("PARTIAL_NOT_CLOSED", summary["reason"])
        self.assertEqual(summary["backends"]["legacy"]["status"], "PASS")
        self.assertEqual(summary["backends"]["gpu-next"]["status"], "NOT_RUN")
        self.assertTrue(summary["claim_boundaries"]["container_session_correlated"])
        self.assertFalse(summary["claim_boundaries"]["same_process_wire_correlated"])
        self.assertFalse(summary["claim_boundaries"]["per_frame_presentation_correlated"])
        self.assertFalse(summary["claim_boundaries"]["compositor_scanout_certified"])

    def test_gate2w_summary_both_backends_gathered_not_closed(self):
        """Both backends passing gathers evidence but keeps per-frame / hardware presentation OPEN."""
        run_dir = Path(self.test_dir) / "run_summary_test2"
        legacy_dir = run_dir / "legacy"
        legacy_dir.mkdir(parents=True)
        gpu_next_dir = run_dir / "gpu-next"
        gpu_next_dir.mkdir(parents=True)

        (legacy_dir / "session_facts.json").write_text(json.dumps({"backend_result": "PASS"}), encoding="utf-8")
        (gpu_next_dir / "session_facts.json").write_text(json.dumps({"backend_result": "PASS"}), encoding="utf-8")

        cmd = [
            "bash",
            "-c",
            f'source "{SCRIPT_PATH}"\n'
            f'run_dir="{run_dir}"\n'
            'write_gate2w_summary "all"',
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_DIR)
        self.assertEqual(res.returncode, 0, res.stderr)
        summary_file = run_dir / "gate2w_summary.json"
        self.assertTrue(summary_file.exists())
        summary = json.loads(summary_file.read_text(encoding="utf-8"))
        self.assertEqual(summary["gate2w_status"], "GATE2W_EVIDENCE_GATHERED")
        self.assertFalse(summary["gate2w_closed"])
        self.assertIn("non-claims, not additional criteria", summary["reason"])
        self.assertEqual(summary["backends"]["legacy"]["status"], "PASS")
        self.assertEqual(summary["backends"]["gpu-next"]["status"], "PASS")
        self.assertTrue(summary["claim_boundaries"]["container_session_correlated"])
        self.assertFalse(summary["claim_boundaries"]["same_process_wire_correlated"])
        self.assertFalse(summary["claim_boundaries"]["per_frame_presentation_correlated"])
        self.assertFalse(summary["claim_boundaries"]["compositor_scanout_certified"])

    def test_cleanup_handler_writes_cleanup_status(self):
        """cleanup_handler records status and signal in cleanup_status.json."""
        target_dir = Path(self.test_dir) / "cleanup_target"
        target_dir.mkdir()
        cmd = [
            "bash",
            "-c",
            f'source "{SCRIPT_PATH}"\n'
            f'current_backend_dir="{target_dir}"\n'
            'cleanup_handler "TERM"',
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_DIR)
        status_file = target_dir / "cleanup_status.json"
        self.assertTrue(status_file.exists())
        status_data = json.loads(status_file.read_text(encoding="utf-8"))
        self.assertEqual(status_data["trigger_signal"], "TERM")
        self.assertEqual(status_data["status"], "CLEAN")
        self.assertIn("timestamp_utc", status_data)

    def test_cleanup_handler_does_not_overwrite_existing_cleanup_status(self):
        """cleanup_handler EXIT must NOT overwrite an existing cleanup_status.json (e.g. from NORMAL)."""
        target_dir = Path(self.test_dir) / "cleanup_target_no_overwrite"
        target_dir.mkdir()
        initial_status = {
            "schema": "cinehdr.gate2w.cleanup-status.v1",
            "timestamp_utc": "2026-10-04T12:00:00.000000Z",
            "trigger_signal": "NORMAL",
            "cleaned_instance_id": "12345",
            "instance_terminated": True,
            "runner_pid_terminated": True,
            "user_instances_preserved": 0,
            "status": "CLEAN",
            "error": "",
        }
        status_file = target_dir / "cleanup_status.json"
        status_file.write_text(json.dumps(initial_status), encoding="utf-8")

        cmd = [
            "bash",
            "-c",
            f'source "{SCRIPT_PATH}"\n'
            f'current_backend_dir="{target_dir}"\n'
            f'current_test_instance_id="12345"\n'
            'cleanup_handler "EXIT"',
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_DIR)
        self.assertEqual(res.returncode, 0, res.stderr)
        saved_status = json.loads(status_file.read_text(encoding="utf-8"))
        self.assertEqual(saved_status["trigger_signal"], "NORMAL")
        self.assertEqual(saved_status["cleaned_instance_id"], "12345")
        self.assertEqual(saved_status["timestamp_utc"], "2026-10-04T12:00:00.000000Z")

    def test_cleanup_instance_fails_closed_when_info_missing_and_instance_still_running(self):
        """cleanup_instance must fail-closed if info file is missing but flatpak ps still reports instance running."""
        fake_runtime = Path(self.test_dir) / "run_user_missing_info_running"
        fake_runtime.mkdir(parents=True)

        mock_bin_dir = Path(self.test_dir) / "mock_bin_missing_info_running"
        mock_bin_dir.mkdir()
        mock_flatpak = mock_bin_dir / "flatpak"
        mock_flatpak.write_text(
            '#!/usr/bin/env bash\n'
            'if [[ "$1" == "ps" ]]; then echo "12345"; exit 0; fi\n'
            'exit 0\n'
        )
        mock_flatpak.chmod(mock_flatpak.stat().st_mode | stat.S_IEXEC)

        cmd = [
            "bash",
            "-c",
            f'source "{SCRIPT_PATH}"\n'
            f'export PATH="{mock_bin_dir}:$PATH"\n'
            f'runtime_base="{fake_runtime}"\n'
            'cleanup_instance "12345"',
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_DIR)
        self.assertEqual(res.returncode, 1)
        self.assertIn("info file is missing but instance is STILL RUNNING", res.stderr)

    def test_cleanup_instance_fails_closed_when_info_missing_and_ps_fails(self):
        """cleanup_instance must fail-closed if info file is missing and flatpak ps query fails."""
        fake_runtime = Path(self.test_dir) / "run_user_missing_info_ps_fails"
        fake_runtime.mkdir(parents=True)

        mock_bin_dir = Path(self.test_dir) / "mock_bin_missing_info_ps_fails"
        mock_bin_dir.mkdir()
        mock_flatpak = mock_bin_dir / "flatpak"
        mock_flatpak.write_text(
            '#!/usr/bin/env bash\n'
            'if [[ "$1" == "ps" ]]; then exit 1; fi\n'
            'exit 0\n'
        )
        mock_flatpak.chmod(mock_flatpak.stat().st_mode | stat.S_IEXEC)

        cmd = [
            "bash",
            "-c",
            f'source "{SCRIPT_PATH}"\n'
            f'export PATH="{mock_bin_dir}:$PATH"\n'
            f'runtime_base="{fake_runtime}"\n'
            'cleanup_instance "12345"',
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_DIR)
        self.assertEqual(res.returncode, 1)
        self.assertIn("info file is missing and flatpak ps query failed", res.stderr)

    def test_cleanup_instance_succeeds_when_info_missing_and_ps_confirms_absent(self):
        """cleanup_instance returns 0 when info file is missing and flatpak ps confirms instance is absent."""
        fake_runtime = Path(self.test_dir) / "run_user_missing_info_absent"
        fake_runtime.mkdir(parents=True)

        mock_bin_dir = Path(self.test_dir) / "mock_bin_missing_info_absent"
        mock_bin_dir.mkdir()
        mock_flatpak = mock_bin_dir / "flatpak"
        mock_flatpak.write_text(
            '#!/usr/bin/env bash\n'
            'if [[ "$1" == "ps" ]]; then echo "99999"; exit 0; fi\n'
            'exit 0\n'
        )
        mock_flatpak.chmod(mock_flatpak.stat().st_mode | stat.S_IEXEC)

        cmd = [
            "bash",
            "-c",
            f'source "{SCRIPT_PATH}"\n'
            f'export PATH="{mock_bin_dir}:$PATH"\n'
            f'runtime_base="{fake_runtime}"\n'
            'cleanup_instance "12345"',
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_DIR)
        self.assertEqual(res.returncode, 0, res.stderr)


if __name__ == "__main__":
    unittest.main()
