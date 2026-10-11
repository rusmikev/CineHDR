#!/usr/bin/env python3
"""Targeted offline regressions for Gate 2W Attempt 3 (GNOME / GPU Next).

Covers all 6 required criteria on the real logic of capture_wayland_trace_attempt3_corrected.sh:
1. Valid JSON appeared before commit: no early stop yet;
2. INCOMPLETE/corrupted JSON and foreign session are rejected;
3. Full correct bundle is accepted within the narrow claim;
4. SDR manifest along with valid protocol trace is rejected by client HDR check;
5. Late protocol error revokes preliminary success;
6. Deadline triggers on simulated hung process.
"""

import configparser
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

REPO_DIR = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_DIR / "validation-reports" / "capture_wayland_trace_attempt3_corrected.sh"

EXP_APP_COMMIT = "8ed86c0b54694b75d1aaa9be75956d1d3a9d075901d90695e4e0d3d144970813"
EXP_RT_COMMIT = "b1935f7a673108616d4fd84564f15cefc9f637481472a8d4d2f0945375d41f4b"
EXP_BASE_HEAD = "9010e5a4a473aa90ae35ef5bb22b27dacf373d6e"

VALID_TRACE_SNIPPET = """
[ 1042.001] {Default Queue}  -> wl_compositor#4.create_surface(new id wl_surface#8)
[ 1042.002] {Default Queue}  -> xdg_wm_base#42.get_xdg_surface(new id xdg_surface#15, wl_surface#8)
[ 1042.003]  -> xdg_surface#15.get_toplevel(new id xdg_toplevel#16)
[ 1042.004]  -> xdg_toplevel#16.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.005]  wl_registry#2.global(19, "wp_color_manager_v1", 1)
[ 1042.006]  -> wl_registry#2.bind(19, "wp_color_manager_v1", 1, new id [unknown]#11)
[ 1042.007]  wp_color_manager_v1#11.supported_intent(0)
[ 1042.008]  wp_color_manager_v1#11.done()
[ 1042.009]  -> wp_color_manager_v1#11.get_surface(new id wp_color_management_surface_v1#23, wl_surface#8)
[ 1042.010]  wp_image_description_v1#31.ready(123)
[ 1042.011]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.012]  -> wl_surface#8.commit()
""".strip()

PARTIAL_TRACE_SNIPPET_NO_COMMIT = """
[ 1042.001] {Default Queue}  -> wl_compositor#4.create_surface(new id wl_surface#8)
[ 1042.002] {Default Queue}  -> xdg_wm_base#42.get_xdg_surface(new id xdg_surface#15, wl_surface#8)
[ 1042.003]  -> xdg_surface#15.get_toplevel(new id xdg_toplevel#16)
[ 1042.004]  -> xdg_toplevel#16.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.005]  wl_registry#2.global(19, "wp_color_manager_v1", 1)
[ 1042.006]  -> wl_registry#2.bind(19, "wp_color_manager_v1", 1, new id [unknown]#11)
[ 1042.007]  wp_color_manager_v1#11.supported_intent(0)
[ 1042.008]  wp_color_manager_v1#11.done()
[ 1042.009]  -> wp_color_manager_v1#11.get_surface(new id wp_color_management_surface_v1#23, wl_surface#8)
[ 1042.010]  wp_image_description_v1#31.ready(123)
[ 1042.011]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
""".strip()


class Attempt3RegressionsTests(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="cinehdr_attempt3_reg_")
        self.trace_dir = Path(self.test_dir) / "wayland-trace-test"
        self.trace_dir.mkdir(parents=True)
        self.session_id = self.trace_dir.name
        self.inst_id = "248765847"

        # Base start and end times
        self.t_start = datetime.now(timezone.utc) - timedelta(seconds=10)
        self.t_end = datetime.now(timezone.utc) + timedelta(seconds=10)
        (self.trace_dir / "start_time.txt").write_text(self.t_start.isoformat() + "\n", encoding="utf-8")
        (self.trace_dir / "end_time.txt").write_text(self.t_end.isoformat() + "\n", encoding="utf-8")
        (self.trace_dir / "instance.txt").write_text(self.inst_id + "\n", encoding="utf-8")
        (self.trace_dir / "process_exit_code.txt").write_text("0\n", encoding="utf-8")

        # Mock instance_info
        info_cp = configparser.ConfigParser()
        info_cp["Application"] = {"name": "io.github.rusmikev.CineHDR"}
        info_cp["Instance"] = {
            "instance-id": self.inst_id,
            "app-commit": EXP_APP_COMMIT,
            "runtime-commit": EXP_RT_COMMIT,
        }
        with open(self.trace_dir / "instance_info", "w", encoding="utf-8") as f:
            info_cp.write(f)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _write_diagnostics(
        self,
        *,
        evidence_status="CONFIRMED",
        evidence_gaps=None,
        active_api="opengl-next",
        session_status="active",
        render_target_format="GL_RGBA16F",
        render_target_depth=16,
        gdk_color_state="rec2100-pq",
        first_frame_confirmed=True,
        media_frame=None,
        session_id=None,
        flatpak_instance_id=None,
        app_commit=EXP_APP_COMMIT,
        runtime_commit=EXP_RT_COMMIT,
        sample_time_utc=None,
        report_text="Mock Video Output Diagnostics Report\nOverall result: PASS\n",
    ):
        if evidence_gaps is None:
            evidence_gaps = []
        if media_frame is None:
            media_frame = {
                "source_width": 3840,
                "source_height": 2160,
                "time_pos": 0.0417,
                "path_token": "token123",
            }
        s_id = session_id or self.session_id
        inst_id = flatpak_instance_id or self.inst_id
        t_sample = sample_time_utc or (self.t_start + timedelta(seconds=2)).isoformat()

        txt_path = self.trace_dir / "video_output_diagnostics.txt"
        txt_path.write_text(report_text, encoding="utf-8")
        report_sha = hashlib.sha256(report_text.encode("utf-8")).hexdigest()

        diag_manifest = {
            "schema": "cinehdr.diagnostics.export.v1",
            "sample_time_utc": t_sample,
            "trigger": "first-media-frame-confirmed",
            "evidence_status": evidence_status,
            "evidence_gaps": evidence_gaps,
            "first_frame_confirmed": first_frame_confirmed,
            "media_frame": media_frame,
            "identity": {
                "process_id": 12345,
                "flatpak_instance_id": inst_id,
                "app_commit": app_commit,
                "runtime_commit": runtime_commit,
                "session_id": s_id,
                "identity_status": "VERIFIED",
                "identity_verified": True,
            },
            "summary": {
                "requested_api": "opengl-next",
                "active_api": active_api,
                "session_status": session_status,
                "render_target_format": render_target_format,
                "render_target_depth": render_target_depth,
                "gdk_color_state": gdk_color_state,
            },
            "report_sha256": report_sha,
            "target_file": str(txt_path),
        }

        json_path = self.trace_dir / "video_output_diagnostics.json"
        json_path.write_text(json.dumps(diag_manifest, indent=2), encoding="utf-8")

    def _run_check_readiness(self):
        cmd = [
            "bash",
            str(SCRIPT_PATH),
            "--check-readiness",
            str(self.trace_dir),
            self.session_id,
            EXP_APP_COMMIT,
            EXP_RT_COMMIT,
            str(REPO_DIR),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        return res.returncode == 0

    def _run_finalize_session(self):
        cmd = [
            "bash",
            str(SCRIPT_PATH),
            "--finalize-session",
            str(self.trace_dir),
            self.session_id,
            EXP_APP_COMMIT,
            EXP_RT_COMMIT,
            EXP_BASE_HEAD,
            str(REPO_DIR),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        return res.returncode, res.stdout, res.stderr

    # --- REGRESSION 1: Valid JSON appeared before commit: no early stop yet ---
    def test_regression1_valid_json_before_commit_no_early_stop(self):
        self._write_diagnostics()
        # Trace has all elements up to set_image_description, but NO commit
        (self.trace_dir / "wayland-client.raw.log").write_text(PARTIAL_TRACE_SNIPPET_NO_COMMIT + "\n", encoding="utf-8")

        # Readiness check MUST fail
        self.assertFalse(
            self._run_check_readiness(),
            "Readiness check must reject session when valid JSON exists but Wayland commit is missing"
        )

        # Now append the commit to the trace
        with open(self.trace_dir / "wayland-client.raw.log", "a", encoding="utf-8") as f:
            f.write("[ 1042.012]  -> wl_surface#8.commit()\n")

        # Readiness check MUST now pass
        self.assertTrue(
            self._run_check_readiness(),
            "Readiness check must pass once the commit line appears on the application-owned surface"
        )

    # --- REGRESSION 2: INCOMPLETE/corrupted JSON and foreign session are rejected ---
    def test_regression2_incomplete_corrupted_and_foreign_session_rejected(self):
        (self.trace_dir / "wayland-client.raw.log").write_text(VALID_TRACE_SNIPPET + "\n", encoding="utf-8")

        # 2a: Corrupted JSON
        txt_path = self.trace_dir / "video_output_diagnostics.txt"
        txt_path.write_text("dummy report\n", encoding="utf-8")
        json_path = self.trace_dir / "video_output_diagnostics.json"
        json_path.write_text("{corrupted json syntax... truncated", encoding="utf-8")

        self.assertFalse(self._run_check_readiness())
        code, out, err = self._run_finalize_session()
        self.assertNotEqual(code, 0)
        facts = json.loads((self.trace_dir / "session_facts.json").read_text(encoding="utf-8"))
        self.assertFalse(facts["claim_boundaries"]["client_publication_proven"])
        self.assertEqual(facts["narrow_row_status"], "FAIL")

        # 2b: INCOMPLETE JSON
        self._write_diagnostics(
            evidence_status="INCOMPLETE",
            evidence_gaps=["first_frame_unconfirmed: no video frame was published before shutdown"]
        )
        self.assertFalse(self._run_check_readiness())
        code, out, err = self._run_finalize_session()
        self.assertNotEqual(code, 0)
        facts = json.loads((self.trace_dir / "session_facts.json").read_text(encoding="utf-8"))
        self.assertFalse(facts["claim_boundaries"]["client_publication_proven"])
        self.assertEqual(facts["narrow_row_status"], "FAIL")

        # 2c: Foreign session ID
        self._write_diagnostics(session_id="foreign_session_xyz")
        self.assertFalse(self._run_check_readiness())
        code, out, err = self._run_finalize_session()
        self.assertNotEqual(code, 0)
        facts = json.loads((self.trace_dir / "session_facts.json").read_text(encoding="utf-8"))
        self.assertFalse(facts["session_id_matched"])
        self.assertEqual(facts["narrow_row_status"], "FAIL")

        # 2d: Foreign flatpak instance ID
        self._write_diagnostics(flatpak_instance_id="99999999")
        self.assertFalse(self._run_check_readiness())
        code, out, err = self._run_finalize_session()
        self.assertNotEqual(code, 0)
        facts = json.loads((self.trace_dir / "session_facts.json").read_text(encoding="utf-8"))
        self.assertFalse(facts["instance_id_correlated"])
        self.assertEqual(facts["narrow_row_status"], "FAIL")

    # --- REGRESSION 3: Full correct bundle is accepted within narrow claim ---
    def test_regression3_full_correct_bundle_accepted_within_narrow_claim(self):
        self._write_diagnostics()
        (self.trace_dir / "wayland-client.raw.log").write_text(VALID_TRACE_SNIPPET + "\n", encoding="utf-8")

        self.assertTrue(self._run_check_readiness())
        code, out, err = self._run_finalize_session()
        self.assertEqual(code, 0, f"finalize_session should exit 0 on full valid bundle. stderr: {err}")

        facts = json.loads((self.trace_dir / "session_facts.json").read_text(encoding="utf-8"))
        self.assertTrue(facts["claim_boundaries"]["client_publication_proven"])
        self.assertTrue(facts["claim_boundaries"]["wayland_color_protocol_submitted"])
        self.assertTrue(facts["claim_boundaries"]["narrow_row_correlated_submission_proven"])
        self.assertEqual(facts["narrow_row_status"], "PASS")

        # Non-claims strictly preserved
        self.assertFalse(facts["claim_boundaries"]["wayland_surface_video_linked"])
        self.assertFalse(facts["claim_boundaries"]["wayland_surface_submission_proven"])
        self.assertFalse(facts["claim_boundaries"]["physical_display_output_certified"])
        self.assertEqual(facts["wayland_surface_video_link_status"], "UNVERIFIED")

        # Verify media frame confirmed facts
        self.assertTrue(facts["first_frame_confirmed"])
        self.assertTrue(facts["media_frame_confirmed"])

    # --- REGRESSION 4: SDR manifest rejected by client HDR check ---
    def test_regression4_sdr_manifest_rejected_by_client_hdr_check(self):
        (self.trace_dir / "wayland-client.raw.log").write_text(VALID_TRACE_SNIPPET + "\n", encoding="utf-8")

        # Diagnostics manifest specifies 8-bit SDR sRGB
        self._write_diagnostics(
            render_target_format="GL_RGBA8",
            render_target_depth=8,
            gdk_color_state="srgb",
        )

        self.assertFalse(self._run_check_readiness(), "SDR manifest must fail readiness check")
        code, out, err = self._run_finalize_session()
        self.assertNotEqual(code, 0, "finalize_session must exit non-zero for SDR manifest")

        facts = json.loads((self.trace_dir / "session_facts.json").read_text(encoding="utf-8"))
        self.assertFalse(facts["claim_boundaries"]["client_publication_proven"])
        self.assertTrue(facts["claim_boundaries"]["wayland_color_protocol_submitted"])
        self.assertFalse(facts["claim_boundaries"]["narrow_row_correlated_submission_proven"])
        self.assertEqual(facts["narrow_row_status"], "FAIL")

    # --- REGRESSION 5: Late protocol error revokes preliminary success ---
    def test_regression5_late_protocol_error_revokes_preliminary_success(self):
        # Create a mock session script that starts, writes valid trace & JSON,
        # and on SIGINT appends a fatal protocol error before dying
        mock_runner = f"""
echo '{self.inst_id}' >&3
cat << 'TRACE_EOF' > "{self.trace_dir}/wayland-client.raw.log"
{VALID_TRACE_SNIPPET}
TRACE_EOF

python3 - << 'PY_DIAG'
import json, hashlib
from datetime import datetime, timezone
report = "Mock Video Output Diagnostics\\n"
report_sha = hashlib.sha256(report.encode("utf-8")).hexdigest()
open("{self.trace_dir}/video_output_diagnostics.txt", "w").write(report)
manifest = {{
    "schema": "cinehdr.diagnostics.export.v1",
    "sample_time_utc": datetime.now(timezone.utc).isoformat(),
    "evidence_status": "CONFIRMED",
    "evidence_gaps": [],
    "first_frame_confirmed": True,
    "media_frame": {{"source_width": 3840, "source_height": 2160, "time_pos": 0.0417, "path_token": "token123"}},
    "identity": {{
        "flatpak_instance_id": "{self.inst_id}",
        "app_commit": "{EXP_APP_COMMIT}",
        "runtime_commit": "{EXP_RT_COMMIT}",
        "session_id": "{self.session_id}"
    }},
    "summary": {{
        "active_api": "opengl-next",
        "session_status": "active",
        "render_target_format": "GL_RGBA16F",
        "render_target_depth": 16,
        "gdk_color_state": "rec2100-pq"
    }},
    "report_sha256": report_sha
}}
open("{self.trace_dir}/video_output_diagnostics.json", "w").write(json.dumps(manifest))
PY_DIAG

trap 'echo "[ 1042.020] wl_display#1.error(wl_surface#8, 2, \\"fatal protocol error\\")" >> "{self.trace_dir}/wayland-client.raw.log"; exit 0' INT TERM

while true; do
  sleep 0.1
done
"""
        cmd = [
            "bash",
            str(SCRIPT_PATH),
            "--run-test-session",
            str(self.trace_dir),
            mock_runner,
            "5",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)

        # Preliminary success must be revoked!
        self.assertNotEqual(res.returncode, 0, "Late protocol error must cause script to return non-zero")

        val_data = json.loads((self.trace_dir / "wayland-trace-validation.json").read_text(encoding="utf-8"))
        self.assertEqual(val_data["status"], "FAIL")
        self.assertIn("fatal/protocol rejection", val_data["error"])

        facts = json.loads((self.trace_dir / "session_facts.json").read_text(encoding="utf-8"))
        self.assertEqual(facts["wayland_parser_status"], "FAIL")
        self.assertFalse(facts["wayland_protocol_sequence_confirmed"])
        self.assertFalse(facts["claim_boundaries"]["wayland_color_protocol_submitted"])
        self.assertEqual(facts["narrow_row_status"], "FAIL")

    # --- REGRESSION 6: Deadline triggers on simulated hung process ---
    def test_regression6_deadline_triggers_on_simulated_hung_process(self):
        # A hung process that sleeps and never exports anything
        mock_hung_runner = "echo '248765847' >&3; while true; do sleep 1; done"

        t_before = time.time()
        cmd = [
            "bash",
            str(SCRIPT_PATH),
            "--run-test-session",
            str(self.trace_dir),
            mock_hung_runner,
            "2",  # 2 second max wait deadline for regression test
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        t_elapsed = time.time() - t_before

        self.assertGreaterEqual(t_elapsed, 1.8, "Should run until deadline")
        self.assertLessEqual(t_elapsed, 3.5, "Deadline must terminate within budget (upper bound)")
        self.assertEqual(res.returncode, 124, "Timeout must return exit code 124")

        exit_code_text = (self.trace_dir / "exit_code.txt").read_text(encoding="utf-8").strip()
        self.assertEqual(exit_code_text, "124")

        facts = json.loads((self.trace_dir / "session_facts.json").read_text(encoding="utf-8"))
        self.assertEqual(facts["narrow_row_status"], "FAIL")
        self.assertFalse(facts["claim_boundaries"]["narrow_row_correlated_submission_proven"])

    # --- REGRESSION 7: Slow and hung readiness checks respect deadline and upper bound ---
    def test_regression7_slow_and_hung_readiness_checks_respect_deadline_upper_bound(self):
        # 7a: Hung readiness check (simulated 60s sleep during readiness check)
        (self.trace_dir / "simulate_hung_readiness").write_text("1\n", encoding="utf-8")

        mock_runner = f"""
echo '{self.inst_id}' >&3
echo $$ > "{self.trace_dir}/mock_runner.pid"
cat << 'TRACE_EOF' > "{self.trace_dir}/wayland-client.raw.log"
{VALID_TRACE_SNIPPET}
TRACE_EOF

python3 - << 'PY_DIAG'
import json, hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

start_time_file = Path("{self.trace_dir}/start_time.txt")
if start_time_file.exists():
    t_start = datetime.fromisoformat(start_time_file.read_text().strip().replace("Z", "+00:00"))
else:
    t_start = datetime.now(timezone.utc)
t_sample = t_start + timedelta(milliseconds=100)

report = "Mock Video Output Diagnostics\\n"
report_sha = hashlib.sha256(report.encode("utf-8")).hexdigest()
open("{self.trace_dir}/video_output_diagnostics.txt", "w").write(report)
manifest = {{
    "schema": "cinehdr.diagnostics.export.v1",
    "sample_time_utc": t_sample.isoformat(),
    "evidence_status": "CONFIRMED",
    "evidence_gaps": [],
    "first_frame_confirmed": True,
    "media_frame": {{"source_width": 3840, "source_height": 2160, "time_pos": 0.0417, "path_token": "token123"}},
    "identity": {{
        "flatpak_instance_id": "{self.inst_id}",
        "app_commit": "{EXP_APP_COMMIT}",
        "runtime_commit": "{EXP_RT_COMMIT}",
        "session_id": "{self.session_id}"
    }},
    "summary": {{
        "active_api": "opengl-next",
        "session_status": "active",
        "render_target_format": "GL_RGBA16F",
        "render_target_depth": 16,
        "gdk_color_state": "rec2100-pq"
    }},
    "report_sha256": report_sha
}}
open("{self.trace_dir}/video_output_diagnostics.json", "w").write(json.dumps(manifest))
PY_DIAG

while true; do
  sleep 0.05
done
"""
        t_before = time.time()
        cmd = [
            "bash",
            str(SCRIPT_PATH),
            "--run-test-session",
            str(self.trace_dir),
            mock_runner,
            "2",  # 2 second deadline
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        t_elapsed = time.time() - t_before

        # Explicitly verify entry into hung check
        self.assertTrue(
            (self.trace_dir / "readiness_entered_hung.txt").exists(),
            "Hung readiness check must be entered"
        )
        self.assertGreaterEqual(t_elapsed, 1.8, "Hung check must run up to deadline")
        self.assertLessEqual(t_elapsed, 4.0, "Hung readiness check must not block termination past upper bound")
        self.assertEqual(res.returncode, 124, "Hung check reaching deadline must return code 124")
        self.assertEqual(
            (self.trace_dir / "sampler_status.txt").read_text().strip(),
            "INSTANCE_INFO_VERIFIED"
        )
        self.assertEqual(
            (self.trace_dir / "stop_reason.txt").read_text().strip(),
            "TIMEOUT"
        )

        pid_file = self.trace_dir / "mock_runner.pid"
        self.assertTrue(pid_file.exists(), "Mock runner PID must be recorded")
        runner_pid = int(pid_file.read_text().strip())
        with self.assertRaises(ProcessLookupError):
            os.kill(runner_pid, 0)

        # Cleanup marker file for 7b
        (self.trace_dir / "simulate_hung_readiness").unlink(missing_ok=True)
        (self.trace_dir / "readiness_entered_hung.txt").unlink(missing_ok=True)
        (self.trace_dir / "readiness_entered_slow.txt").unlink(missing_ok=True)
        pid_file.unlink(missing_ok=True)

        # 7b: Slow readiness check (simulated 0.3s sleep with partial trace)
        mock_runner_partial = f"""
echo '{self.inst_id}' >&3
echo $$ > "{self.trace_dir}/mock_runner.pid"
cat << 'TRACE_EOF' > "{self.trace_dir}/wayland-client.raw.log"
{PARTIAL_TRACE_SNIPPET_NO_COMMIT}
TRACE_EOF

python3 - << 'PY_DIAG'
import json, hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

start_time_file = Path("{self.trace_dir}/start_time.txt")
if start_time_file.exists():
    t_start = datetime.fromisoformat(start_time_file.read_text().strip().replace("Z", "+00:00"))
else:
    t_start = datetime.now(timezone.utc)
t_sample = t_start + timedelta(milliseconds=100)

report = "Mock Video Output Diagnostics\\n"
report_sha = hashlib.sha256(report.encode("utf-8")).hexdigest()
open("{self.trace_dir}/video_output_diagnostics.txt", "w").write(report)
manifest = {{
    "schema": "cinehdr.diagnostics.export.v1",
    "sample_time_utc": t_sample.isoformat(),
    "evidence_status": "CONFIRMED",
    "evidence_gaps": [],
    "first_frame_confirmed": True,
    "media_frame": {{"source_width": 3840, "source_height": 2160, "time_pos": 0.0417, "path_token": "token123"}},
    "identity": {{
        "flatpak_instance_id": "{self.inst_id}",
        "app_commit": "{EXP_APP_COMMIT}",
        "runtime_commit": "{EXP_RT_COMMIT}",
        "session_id": "{self.session_id}"
    }},
    "summary": {{
        "active_api": "opengl-next",
        "session_status": "active",
        "render_target_format": "GL_RGBA16F",
        "render_target_depth": 16,
        "gdk_color_state": "rec2100-pq"
    }},
    "report_sha256": report_sha
}}
open("{self.trace_dir}/video_output_diagnostics.json", "w").write(json.dumps(manifest))
PY_DIAG

while true; do
  sleep 0.05
done
"""
        env = os.environ.copy()
        env["CINEHDR_TEST_READINESS_DELAY"] = "0.3"
        t_before = time.time()
        cmd_slow = [
            "bash",
            str(SCRIPT_PATH),
            "--run-test-session",
            str(self.trace_dir),
            mock_runner_partial,
            "2",
        ]
        res_slow = subprocess.run(cmd_slow, capture_output=True, text=True, env=env)
        t_slow = time.time() - t_before

        # Explicitly verify entry into slow check
        self.assertTrue(
            (self.trace_dir / "readiness_entered_slow.txt").exists(),
            "Slow readiness check must be entered"
        )
        self.assertGreaterEqual(t_slow, 1.8, "Slow check must run up to deadline")
        self.assertLessEqual(t_slow, 3.5, "Slow check must not exceed deadline upper bound")
        self.assertEqual(res_slow.returncode, 124)
        self.assertEqual(
            (self.trace_dir / "sampler_status.txt").read_text().strip(),
            "INSTANCE_INFO_VERIFIED"
        )

        self.assertTrue(pid_file.exists(), "Mock runner PID must be recorded in 7b")
        runner_pid_slow = int(pid_file.read_text().strip())
        with self.assertRaises(ProcessLookupError):
            os.kill(runner_pid_slow, 0)

    # --- REGRESSION 8: Strict temporal correlation and window boundary regressions ---
    def test_regression8_temporal_correlation_strict_window_and_rejections(self):
        (self.trace_dir / "wayland-client.raw.log").write_text(VALID_TRACE_SNIPPET + "\n", encoding="utf-8")

        # 8a: sample = end + 3s rejected by finalization (previously erroneously passed)
        t_bad_sample = self.t_end + timedelta(seconds=3)
        self._write_diagnostics(sample_time_utc=t_bad_sample.isoformat())
        code, out, err = self._run_finalize_session()
        self.assertNotEqual(code, 0, "sample = end + 3s must be rejected by finalize_session")
        facts = json.loads((self.trace_dir / "session_facts.json").read_text(encoding="utf-8"))
        self.assertFalse(facts["sample_time_within_window"], "sample_time_within_window must be False when sample > end")
        self.assertEqual(facts["narrow_row_status"], "FAIL")

        # 8b: missing start_time.txt rejected by finalization
        (self.trace_dir / "start_time.txt").unlink(missing_ok=True)
        self._write_diagnostics(sample_time_utc=(self.t_start + timedelta(seconds=2)).isoformat())
        code, out, err = self._run_finalize_session()
        self.assertNotEqual(code, 0, "Missing start_time.txt must be rejected by finalize_session")
        facts = json.loads((self.trace_dir / "session_facts.json").read_text(encoding="utf-8"))
        self.assertFalse(facts["sample_time_within_window"])
        self.assertEqual(facts["narrow_row_status"], "FAIL")
        # Restore start_time.txt
        (self.trace_dir / "start_time.txt").write_text(self.t_start.isoformat() + "\n", encoding="utf-8")

        # 8c: missing end_time.txt rejected by finalization (previously erroneously passed)
        (self.trace_dir / "end_time.txt").unlink(missing_ok=True)
        self._write_diagnostics(sample_time_utc=(self.t_start + timedelta(seconds=2)).isoformat())
        code, out, err = self._run_finalize_session()
        self.assertNotEqual(code, 0, "Missing end_time.txt must be rejected by finalize_session")
        facts = json.loads((self.trace_dir / "session_facts.json").read_text(encoding="utf-8"))
        self.assertFalse(facts["sample_time_within_window"])
        self.assertEqual(facts["narrow_row_status"], "FAIL")
        # Restore end_time.txt
        (self.trace_dir / "end_time.txt").write_text(self.t_end.isoformat() + "\n", encoding="utf-8")

        # 8d: future sample rejected by readiness check
        t_future = datetime.now(timezone.utc) + timedelta(seconds=60)
        self._write_diagnostics(sample_time_utc=t_future.isoformat())
        self.assertFalse(
            self._run_check_readiness(),
            "Readiness check must reject sample timestamp from the future (t_sample > now)"
        )

        # 8e: valid interval [t_start <= t_sample <= t_end] accepted
        t_valid = self.t_start + timedelta(seconds=2)
        self._write_diagnostics(sample_time_utc=t_valid.isoformat())
        self.assertTrue(self._run_check_readiness(), "Readiness check must accept valid timestamp within window")
        code, out, err = self._run_finalize_session()
        self.assertEqual(code, 0, f"finalize_session must pass for valid timestamp within window: {err}")
        facts = json.loads((self.trace_dir / "session_facts.json").read_text(encoding="utf-8"))
        self.assertTrue(facts["sample_time_within_window"])
        self.assertEqual(facts["narrow_row_status"], "PASS")

    # --- REGRESSION 9: Child process non-zero exit code preserved and not masked ---
    def test_regression9_child_process_exit_code_preserved_and_not_masked(self):
        # Mock runner that produces complete valid trace and diagnostics,
        # traps SIGINT (sent after facts_ready is confirmed), writes confirmation, and exits with 42
        mock_failing_runner = f"""
echo '{self.inst_id}' >&3
echo $$ > "{self.trace_dir}/mock_runner.pid"
cat << 'TRACE_EOF' > "{self.trace_dir}/wayland-client.raw.log"
{VALID_TRACE_SNIPPET}
TRACE_EOF

python3 - << 'PY_DIAG'
import json, hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

start_time_file = Path("{self.trace_dir}/start_time.txt")
if start_time_file.exists():
    t_start = datetime.fromisoformat(start_time_file.read_text().strip().replace("Z", "+00:00"))
else:
    t_start = datetime.now(timezone.utc)
t_sample = t_start + timedelta(milliseconds=100)

report = "Mock Video Output Diagnostics\\n"
report_sha = hashlib.sha256(report.encode("utf-8")).hexdigest()
open("{self.trace_dir}/video_output_diagnostics.txt", "w").write(report)
manifest = {{
    "schema": "cinehdr.diagnostics.export.v1",
    "sample_time_utc": t_sample.isoformat(),
    "evidence_status": "CONFIRMED",
    "evidence_gaps": [],
    "first_frame_confirmed": True,
    "media_frame": {{"source_width": 3840, "source_height": 2160, "time_pos": 0.0417, "path_token": "token123"}},
    "identity": {{
        "flatpak_instance_id": "{self.inst_id}",
        "app_commit": "{EXP_APP_COMMIT}",
        "runtime_commit": "{EXP_RT_COMMIT}",
        "session_id": "{self.session_id}"
    }},
    "summary": {{
        "active_api": "opengl-next",
        "session_status": "active",
        "render_target_format": "GL_RGBA16F",
        "render_target_depth": 16,
        "gdk_color_state": "rec2100-pq"
    }},
    "report_sha256": report_sha
}}
open("{self.trace_dir}/video_output_diagnostics.json", "w").write(json.dumps(manifest))
PY_DIAG

trap 'echo "sigint_received" > "{self.trace_dir}/runner_received_sigint.txt"; exit 42' INT TERM

while true; do
  sleep 0.05
done
"""
        cmd = [
            "bash",
            str(SCRIPT_PATH),
            "--run-test-session",
            str(self.trace_dir),
            mock_failing_runner,
            "5",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)

        # Confirm runner received SIGINT after facts_ready was confirmed by controller
        self.assertTrue(
            (self.trace_dir / "runner_received_sigint.txt").exists(),
            "Runner must receive SIGINT after controller confirms facts_ready before exiting 42"
        )

        # Non-zero exit code 42 must NOT be masked with 0
        self.assertEqual(res.returncode, 42, f"Script exit code must preserve runner code 42, got {res.returncode}")

        exit_code_text = (self.trace_dir / "exit_code.txt").read_text(encoding="utf-8").strip()
        self.assertEqual(exit_code_text, "42", "exit_code.txt must preserve 42")

        proc_code_text = (self.trace_dir / "process_exit_code.txt").read_text(encoding="utf-8").strip()
        self.assertEqual(proc_code_text, "42", "process_exit_code.txt must preserve 42")

        facts = json.loads((self.trace_dir / "session_facts.json").read_text(encoding="utf-8"))
        self.assertEqual(facts["observed_process_exit_code"], 42)
        self.assertTrue(facts["sample_time_within_window"], "Sample time must be valid and within window")
        self.assertEqual(facts["stop_reason"], "FACTS_VERIFIED")
        self.assertFalse(facts["claim_boundaries"]["client_publication_proven"], "Non-zero exit must invalidate client publication")
        self.assertEqual(facts["narrow_row_status"], "FAIL", "Narrow row must fail when process exits abnormally")

    # --- REGRESSION 10: Controller interruption produces complete artifacts, exit 130, no lingering processes ---
    def test_regression10_controller_interruption(self):
        mock_runner = f"""
echo '{self.inst_id}' >&3
echo $$ > "{self.trace_dir}/mock_runner.pid"
while true; do
  sleep 0.05
done
"""
        cmd = [
            "bash",
            str(SCRIPT_PATH),
            "--run-test-session",
            str(self.trace_dir),
            mock_runner,
            "15",
        ]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        pid_file = self.trace_dir / "mock_runner.pid"
        for _ in range(50):
            if pid_file.exists():
                break
            time.sleep(0.05)
        self.assertTrue(pid_file.exists(), "Mock runner must have started")
        runner_pid = int(pid_file.read_text().strip())

        time.sleep(0.2)

        proc.send_signal(signal.SIGINT)
        stdout, stderr = proc.communicate(timeout=5.0)

        self.assertEqual(proc.returncode, 130, f"Interrupted controller must exit with 130, got {proc.returncode}. stderr: {stderr}")

        # Assert runner process is dead
        with self.assertRaises(ProcessLookupError):
            os.kill(runner_pid, 0)

        # Assert all artifacts are present and consistent
        self.assertTrue((self.trace_dir / "stop_reason.txt").exists())
        self.assertEqual((self.trace_dir / "stop_reason.txt").read_text().strip(), "INTERRUPTED_INT")

        self.assertTrue((self.trace_dir / "process_exit_code.txt").exists())
        self.assertTrue((self.trace_dir / "end_time.txt").exists())

        self.assertTrue((self.trace_dir / "exit_code.txt").exists())
        self.assertEqual((self.trace_dir / "exit_code.txt").read_text().strip(), "130")

        self.assertTrue((self.trace_dir / "session_facts.json").exists())
        facts = json.loads((self.trace_dir / "session_facts.json").read_text(encoding="utf-8"))
        self.assertEqual(facts.get("stop_reason"), "INTERRUPTED_INT")
        self.assertEqual(facts["narrow_row_status"], "FAIL")

        self.assertTrue((self.trace_dir / "SHA256SUMS").exists())

    # --- REGRESSION 11: Sub-10ms remaining budget rejects zero timeout and finalizes TIMEOUT / 124 ---
    def test_regression11_sub_10ms_budget_rejects_zero_timeout_and_finalizes_timeout(self):
        # 11a: Direct check_readiness_cmd rejection of 0.00 and negative timeouts
        (self.trace_dir / "simulate_hung_readiness").write_text("1\n", encoding="utf-8")
        cmd_zero = [
            "bash",
            str(SCRIPT_PATH),
            "--check-readiness",
            str(self.trace_dir),
            self.session_id,
            EXP_APP_COMMIT,
            EXP_RT_COMMIT,
            str(REPO_DIR),
            "0.00",
        ]
        t0 = time.time()
        res_zero = subprocess.run(cmd_zero, capture_output=True, text=True)
        self.assertNotEqual(res_zero.returncode, 0, "check_readiness_cmd must reject 0.00 timeout")
        self.assertLess(time.time() - t0, 1.0, "Rejection must be immediate without entering hung sleep")
        self.assertFalse(
            (self.trace_dir / "readiness_entered_hung.txt").exists(),
            "Hung readiness body must not be entered on 0.00 timeout"
        )

        cmd_neg = list(cmd_zero)
        cmd_neg[-1] = "-1.0"
        res_neg = subprocess.run(cmd_neg, capture_output=True, text=True)
        self.assertNotEqual(res_neg.returncode, 0, "check_readiness_cmd must reject negative timeout")

        # 11b: Session controller with rem_check_ns = 5ms (sub-10ms) and potentially hung readiness
        # Must NOT invoke readiness check with zero timeout, must finalize immediately with TIMEOUT / 124
        mock_runner = f"""
echo '{self.inst_id}' >&3
echo $$ > "{self.trace_dir}/mock_runner.pid"
cat << 'TRACE_EOF' > "{self.trace_dir}/wayland-client.raw.log"
{VALID_TRACE_SNIPPET}
TRACE_EOF

python3 - << 'PY_DIAG'
import json, hashlib
from datetime import datetime, timezone, timedelta
from pathlib import Path

start_time_file = Path("{self.trace_dir}/start_time.txt")
if start_time_file.exists():
    t_start = datetime.fromisoformat(start_time_file.read_text().strip().replace("Z", "+00:00"))
else:
    t_start = datetime.now(timezone.utc)
t_sample = t_start + timedelta(milliseconds=100)

report = "Mock Video Output Diagnostics\\n"
report_sha = hashlib.sha256(report.encode("utf-8")).hexdigest()
open("{self.trace_dir}/video_output_diagnostics.txt", "w").write(report)
manifest = {{
    "schema": "cinehdr.diagnostics.export.v1",
    "sample_time_utc": t_sample.isoformat(),
    "evidence_status": "CONFIRMED",
    "evidence_gaps": [],
    "first_frame_confirmed": True,
    "media_frame": {{"source_width": 3840, "source_height": 2160, "time_pos": 0.0417, "path_token": "token123"}},
    "identity": {{
        "flatpak_instance_id": "{self.inst_id}",
        "app_commit": "{EXP_APP_COMMIT}",
        "runtime_commit": "{EXP_RT_COMMIT}",
        "session_id": "{self.session_id}"
    }},
    "summary": {{
        "active_api": "opengl-next",
        "session_status": "active",
        "render_target_format": "GL_RGBA16F",
        "render_target_depth": 16,
        "gdk_color_state": "rec2100-pq"
    }},
    "report_sha256": report_sha
}}
open("{self.trace_dir}/video_output_diagnostics.json", "w").write(json.dumps(manifest))
PY_DIAG

while true; do
  sleep 0.05
done
"""
        env = os.environ.copy()
        env["CINEHDR_TEST_FORCE_REM_CHECK_NS"] = "5000000"  # 5ms remaining budget

        t_before = time.time()
        cmd_session = [
            "bash",
            str(SCRIPT_PATH),
            "--run-test-session",
            str(self.trace_dir),
            mock_runner,
            "5",
        ]
        res_session = subprocess.run(cmd_session, capture_output=True, text=True, env=env)
        t_elapsed = time.time() - t_before

        # Must exit 124 immediately
        self.assertEqual(res_session.returncode, 124, f"Controller must exit with 124 on sub-10ms budget, got {res_session.returncode}")
        self.assertLess(t_elapsed, 2.0, "Controller must finalize without hanging in 60s readiness check")

        # Readiness check must NOT have been called with zero timeout (hung check was never entered)
        self.assertFalse(
            (self.trace_dir / "readiness_entered_hung.txt").exists(),
            "Hung readiness check must not be entered when remaining budget is sub-10ms"
        )

        # Artifacts must be completely saved
        self.assertTrue((self.trace_dir / "stop_reason.txt").exists())
        self.assertEqual((self.trace_dir / "stop_reason.txt").read_text().strip(), "TIMEOUT")

        self.assertTrue((self.trace_dir / "exit_code.txt").exists())
        self.assertEqual((self.trace_dir / "exit_code.txt").read_text().strip(), "124")

        self.assertTrue((self.trace_dir / "process_exit_code.txt").exists())
        self.assertTrue((self.trace_dir / "end_time.txt").exists())
        self.assertTrue((self.trace_dir / "session_facts.json").exists())
        self.assertTrue((self.trace_dir / "SHA256SUMS").exists())

        facts = json.loads((self.trace_dir / "session_facts.json").read_text(encoding="utf-8"))
        self.assertEqual(facts.get("stop_reason"), "TIMEOUT")
        self.assertEqual(facts["narrow_row_status"], "FAIL")

        pid_file = self.trace_dir / "mock_runner.pid"
        self.assertTrue(pid_file.exists(), "Mock runner PID must be recorded")
        runner_pid = int(pid_file.read_text().strip())
        with self.assertRaises(ProcessLookupError):
            os.kill(runner_pid, 0)


if __name__ == "__main__":
    unittest.main()
