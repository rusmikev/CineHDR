# Copyright 2026 rusmikev
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit and regression tests for GNOME Wayland pipeline trace validation."""

from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import subprocess
import copy
import importlib.util
from types import SimpleNamespace

REPO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_DIR))

from tools.validation.parse_wayland_color_trace import (
    validate_composite_window_trace,
)
from src.gtk_cm_policy import (
    CmCaps,
    gtk_color_managed,
    gtk_can_tag_hdr,
    TF_PQ,
    TF_SRGB,
    TF_GAMMA22,
    TF_EXT_LINEAR,
    TF_COMPOUND_POWER_2_4,
    PRIM_BT2020,
    PRIM_SRGB,
    INTENT_PERCEPTUAL,
    FEAT_PARAMETRIC,
    FEAT_SET_PRIMARIES,
)
from tools.validation import run_gnome_full_trace_validation as runner



class TestGnomePipelineTrace(unittest.TestCase):
    """Test suite verifying the CineHDR pipeline on GNOME / Mutter with Wayland Color Management v2."""

    def setUp(self):
        # GNOME 51.1 Mutter reference capability set
        self.gnome51_caps = CmCaps(
            intents=frozenset({INTENT_PERCEPTUAL}),
            features=frozenset({FEAT_PARAMETRIC, FEAT_SET_PRIMARIES}),
            tfs=frozenset({TF_SRGB, TF_PQ, TF_GAMMA22, TF_EXT_LINEAR}),
            primaries=frozenset({PRIM_SRGB, PRIM_BT2020}),
        )

    def test_gnome51_mutter_caps_classification(self):
        """GNOME 51 / Mutter capability set must satisfy GTK color management and HDR tagging."""
        ok, reason = gtk_color_managed(self.gnome51_caps, env={"GDK_DEBUG": "color-mgmt"})
        self.assertTrue(ok, f"Expected gtk_color_managed to be True, got {reason}")
        self.assertEqual(reason, "ok")

        can_hdr_pq = gtk_can_tag_hdr(self.gnome51_caps, TF_PQ)
        self.assertTrue(can_hdr_pq, "GNOME 51 caps must allow Rec.2100 PQ tagging")

    def test_kde_plasma_missing_tf_srgb_still_blocks_cleanly(self):
        """KWin Plasma missing TF_SRGB (Decision D8) must report clear missing TF srgb fallback."""
        kwin_caps = CmCaps(
            intents=frozenset({INTENT_PERCEPTUAL}),
            features=frozenset({FEAT_PARAMETRIC, FEAT_SET_PRIMARIES}),
            tfs=frozenset({TF_PQ, TF_GAMMA22, TF_COMPOUND_POWER_2_4}),  # Omits TF_SRGB
            primaries=frozenset({PRIM_SRGB, PRIM_BT2020}),
        )
        ok, reason = gtk_color_managed(kwin_caps, env={"GDK_DEBUG": "color-mgmt"})
        self.assertFalse(ok)
        self.assertIn("missing TF srgb", reason)

    def test_synthetic_gnome51_wayland_trace_passes_composite_oracle(self):
        """Synthetic Mutter v2 trace matching GNOME 51 event ordering passes composite window oracle."""
        trace = """
[conn#1 fd#9] [ 1042.001] -> wl_display#1.get_registry(new id wl_registry#2)
[conn#1 fd#9] [ 1042.002] wl_registry#2.global(1, "wl_compositor", 6)
[conn#1 fd#9] [ 1042.003] -> wl_registry#2.bind(1, "wl_compositor", 6, new id [unknown]#4)
[conn#1 fd#9] [ 1042.004] wl_registry#2.global(9, "xdg_wm_base", 7)
[conn#1 fd#9] [ 1042.005] -> wl_registry#2.bind(9, "xdg_wm_base", 6, new id [unknown]#5)
[conn#1 fd#9] [ 1042.006] wl_registry#2.global(32, "wp_color_manager_v1", 2)
[conn#1 fd#9] [ 1042.007] -> wl_registry#2.bind(32, "wp_color_manager_v1", 1, new id [unknown]#6)
[conn#1 fd#9] [ 1042.008] wp_color_manager_v1#6.supported_intent(0)
[conn#1 fd#9] [ 1042.009] wp_color_manager_v1#6.done()
[conn#1 fd#9] [ 1042.010] -> wl_compositor#4.create_surface(new id wl_surface#49)
[conn#1 fd#9] [ 1042.011] -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#61, wl_surface#49)
[conn#1 fd#9] [ 1042.012] -> xdg_surface#61.get_toplevel(new id xdg_toplevel#62)
[conn#1 fd#9] [ 1042.013] -> xdg_toplevel#62.set_app_id("io.github.rusmikev.CineHDR")
[conn#1 fd#9] [ 1042.014] -> wp_color_manager_v1#6.get_surface(new id wp_color_management_surface_v1#63, wl_surface#49)
[conn#1 fd#9] [ 1042.015] -> wp_color_manager_v1#6.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[conn#1 fd#9] [ 1042.016] -> wp_image_description_creator_params_v1#50.set_primaries_named(6)
[conn#1 fd#9] [ 1042.017] -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[conn#1 fd#9] [ 1042.018] -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#54)
[conn#1 fd#9] [ 1042.019] wp_image_description_v1#54.ready(15)
[conn#1 fd#9] [ 1042.020] -> wp_color_management_surface_v1#63.set_image_description(wp_image_description_v1#54, 0)
[conn#1 fd#9] [ 1042.021] -> wl_surface#49.attach(wl_buffer#79, 0, 0)
[conn#1 fd#9] [ 1042.022] -> wl_surface#49.commit()
""".strip()
        ev = validate_composite_window_trace(trace, expected_app_id="io.github.rusmikev.CineHDR")
        self.assertEqual(ev["status"], "PASS")
        self.assertEqual(ev["oracle"], "primary_composite_window")
        matched = ev["matched"]
        self.assertEqual(matched["surface"]["wl_surface_id"], 49)
        self.assertEqual(matched["surface"]["color_management_surface_id"], 63)
        self.assertEqual(matched["image_description"]["id"], 54)
        self.assertEqual(matched["image_description"]["color_type"], "rec2100_pq")
        self.assertEqual(matched["surface"]["effective_buffer_id"], 79)

    def test_wayland_info_parser_keeps_outputs_separate(self):
        raw = """interface: 'wp_color_manager_v1', version: 2, name: 32
\tsupported rendering intents:
\t\tperceptual
\tsupported features:
\t\tparametric
\t\tset_primaries
\tsupported named transfer functions:
\t\tsrgb
\t\tst2084_pq
\tsupported named primaries:
\t\tsrgb
\t\tbt2020
\toutput: 1
\t\ttf_named: st2084_pq
\t\tluminances (cd/m²): min 0.005 max 10000 reference 203
\toutput: 2
\t\ttf_named: srgb
"""
        info = runner.parse_wayland_info_color_manager(raw)
        self.assertEqual(info["version"], 2)
        self.assertEqual([o["tf_named"] for o in info["output_color_descriptions"]], ["st2084_pq", "srgb"])
        self.assertTrue(gtk_can_tag_hdr(runner.build_cm_caps_from_wayland_info(info), TF_PQ))
        self.assertFalse(runner.parse_wayland_info_color_manager("")["available"])

    def good_session(self, hdr=False):
        return {
            "telemetries": [{"source_hdr": hdr, "target_trc": "pq" if hdr else "auto", "hdr_mode": "auto"}],
            "telemetry_valid": True, "exit_code": 0, "timed_out": False, "process_errors": False,
            "runtime_log": "libplacebo=v7.360.1",
            "evidence": {"status": "PASS", "matched": {"color_type": "rec2100_pq"}},
            "diagnostics": {
                "first_frame_confirmed": True, "media_frame": {"source_width": 64, "source_height": 64},
                "summary": {"render_target_format": "GL_RGBA16F" if hdr else "GL_RGBA8",
                            "render_target_depth": 16 if hdr else 8,
                            "gdk_color_state": "Rec.2100 PQ (published texture)" if hdr else "sRGB (published texture)",
                            "active_api": "opengl", "session_status": "active", "startup_fallback_reason": "none"},
            },
        }

    def test_sdr_composite_pq_does_not_replace_texture_check(self):
        session = self.good_session()
        self.assertEqual(runner.evaluate_session(session, hdr=False)["status"], "PASS")
        session["diagnostics"]["summary"]["gdk_color_state"] = "Rec.2100 PQ (published texture)"
        self.assertEqual(runner.evaluate_session(session, hdr=False)["status"], "FAIL")

    def test_incomplete_or_malformed_sdr_telemetry_cannot_pass(self):
        for events in ([], [{}], [{"source_hdr": None, "target_trc": "auto"}],
                       [{"source_hdr": False, "target_trc": "hlg"}],
                       [{"source_hdr": False, "target_trc": "auto", "hdr_mode": "force-sdr"}]):
            with self.subTest(events=events):
                session = self.good_session()
                session["telemetries"] = events
                self.assertEqual(runner.evaluate_session(session, hdr=False)["status"], "FAIL")

    def test_exit_and_media_evidence_are_required_for_both_modes(self):
        for hdr in (True, False):
            good = self.good_session(hdr)
            self.assertEqual(runner.evaluate_session(good, hdr=hdr)["status"], "PASS")
            for key, value in (("exit_code", -11), ("exit_code", None), ("timed_out", True),
                               ("process_errors", True), ("diagnostics", {}), ("telemetry_valid", False)):
                with self.subTest(hdr=hdr, field=key):
                    session = copy.deepcopy(good)
                    session[key] = value
                    self.assertNotEqual(runner.evaluate_session(session, hdr=hdr)["status"], "PASS")
            for key, value in (("first_frame_confirmed", False), ("media_frame", {})):
                session = copy.deepcopy(good)
                session["diagnostics"][key] = value
                self.assertEqual(runner.evaluate_session(session, hdr=hdr)["status"], "FAIL")
            for key, value in (("render_target_format", "Unknown"), ("active_api", "opengl-next"),
                               ("session_status", "startup-fallback"), ("startup_fallback_reason", "failed")):
                session = copy.deepcopy(good)
                session["diagnostics"]["summary"][key] = value
                self.assertEqual(runner.evaluate_session(session, hdr=hdr)["status"], "FAIL")
        good["evidence"]["status"] = "UNKNOWN"
        self.assertEqual(runner.evaluate_session(good, hdr=True)["status"], "FAIL")

    def test_environment_isolates_settings_history_and_developer_overrides(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            hostile = {"WAYLAND_DISPLAY": "wayland-0", "DBUS_SESSION_BUS_ADDRESS": "session-bus",
                       "GSETTINGS_BACKEND": "dconf", "GSETTINGS_SCHEMA_DIR": "/untrusted",
                       "CINEHDR_GPU_VALIDATION": "1", "CINEHDR_RENDER_BACKEND": "gpu-next",
                       "CINEHDR_NO_COLOR_MGMT": "1", "CINEHDR_DIAGNOSTICS_EXPORT_DIR": "/user/export",
                       "GSK_RENDERER": "cairo"}
            with mock.patch.dict(os.environ, hostile, clear=True):
                env = runner.session_environment(root, root / "tagger.so", root)
            self.assertEqual(env["GSETTINGS_BACKEND"], "memory")
            self.assertEqual(env["CINEHDR_RENDER_BACKEND"], "legacy")
            for key in ("CINEHDR_GPU_VALIDATION", "GSETTINGS_SCHEMA_DIR", "CINEHDR_NO_COLOR_MGMT", "GSK_RENDERER"):
                self.assertNotIn(key, env)
            self.assertEqual(env["WAYLAND_DISPLAY"], "wayland-0")
            self.assertEqual(env["DBUS_SESSION_BUS_ADDRESS"], "session-bus")
            for name in ("CONFIG", "CACHE", "DATA", "STATE"):
                self.assertEqual(Path(env[f"XDG_{name}_HOME"]).parent, root)

    def test_timeout_remains_failure_even_if_termination_exits_zero(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            media = root / "fixture.mp4"
            media.write_bytes(b"fixture")
            proc = mock.Mock(pid=123, returncode=0)
            proc.wait.side_effect = [subprocess.TimeoutExpired("observer", 30), 0]
            proc.poll.return_value = None
            with mock.patch.object(runner.subprocess, "Popen", return_value=proc) as launch, \
                 mock.patch.object(runner, "read_session", return_value=self.good_session()), \
                 mock.patch.object(runner.os, "killpg") as kill:
                result = runner.run_bounded_session(media, root, root / "tagger.so")
            self.assertTrue(result["timed_out"])
            self.assertEqual((root / "timeout.txt").read_text().strip(), "true")
            self.assertEqual(runner.evaluate_session(result, hdr=False)["status"], "FAIL")
            self.assertTrue(launch.call_args.kwargs["start_new_session"])
            kill.assert_called_once_with(123, runner.signal.SIGTERM)

    def test_saved_artifacts_fail_closed_on_corrupt_diagnostics_or_telemetry(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "wayland-client.raw.log").write_text("")
            (root / "cinehdr.log").write_text("HDR Pipeline Telemetry: {}\nHDR Pipeline Telemetry: broken\n")
            (root / "copy-report.json").write_text("broken")
            session = runner.read_session(root)
            self.assertFalse(session["telemetry_valid"])
            self.assertNotEqual(runner.evaluate_session(session, hdr=False)["status"], "PASS")

    def test_invalid_duration_does_not_launch_any_process(self):
        for duration in (0, -1, float("nan"), float("inf")):
            with mock.patch.object(runner.subprocess, "Popen") as launch:
                with self.assertRaises(ValueError):
                    runner.run_bounded_session(Path("missing"), Path("unused"), Path("tagger"), duration)
                launch.assert_not_called()

    def test_observer_requests_normal_close_and_rejects_early_exit(self):
        window = mock.Mock()
        application = mock.Mock()
        application.get_windows.return_value = [window]
        glib = mock.Mock(SOURCE_CONTINUE=True, SOURCE_REMOVE=False)
        gio = mock.Mock()
        gio.Application.get_default.return_value = application
        spec = importlib.util.spec_from_file_location(
            "trace_observer_test", REPO_DIR / "tools/validation/gnome_trace_observer.py")
        observer = importlib.util.module_from_spec(spec)
        with mock.patch.dict(sys.modules, {"gi": mock.Mock(),
                             "gi.repository": SimpleNamespace(Gio=gio, GLib=glib)}):
            spec.loader.exec_module(observer)

        def launch(*args, **kwargs):
            poll = glib.timeout_add.call_args.args[1]
            self.assertTrue(poll())
            self.assertFalse(poll())
            raise SystemExit(0)

        with mock.patch.object(observer.runpy, "run_path", side_effect=launch), \
             mock.patch.object(observer.time, "monotonic", side_effect=[0, 0, 5]), \
             mock.patch.object(sys, "argv", []):
            self.assertEqual(observer.run_native_observation(Path("fixture"), 4.5), 0)
        window.close.assert_called_once_with()
        with mock.patch.object(observer.runpy, "run_path", side_effect=SystemExit(0)), \
             mock.patch.object(sys, "argv", []):
            self.assertEqual(observer.run_native_observation(Path("fixture"), 4.5), 1)

    def test_protocol_error_after_submission_rejects_sdr_too(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "wayland-client.raw.log").write_text('wl_display#1.error(2, 0, "rejected")')
            session = runner.read_session(root)
            self.assertTrue(session["process_errors"])
            self.assertEqual(runner.evaluate_session(session, hdr=False)["status"], "FAIL")

    def test_redacted_diagnostic_checksum_and_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = root / "copy-report.txt"
            manifest = root / "copy-report.json"
            report.write_text(str(Path.home() / "fixture.mp4"))
            diag = self.good_session()["diagnostics"]
            diag["report_sha256"] = hashlib.sha256(report.read_bytes()).hexdigest()
            manifest.write_text(json.dumps(diag))
            (root / "wayland-client.raw.log").write_text("")
            runner.redact_artifacts(root, Path.home() / "fixture.mp4")
            self.assertEqual(json.loads(manifest.read_text())["report_sha256"],
                             hashlib.sha256(report.read_bytes()).hexdigest())
            self.assertTrue(runner.read_session(root)["diagnostics"])
            report.write_text("tampered")
            self.assertEqual(runner.read_session(root)["diagnostics"], {})

    def test_redaction_preserves_json_and_removes_personal_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            media = Path.home() / "private-video.mp4"
            report = root / "report.json"
            report.write_text(json.dumps({"path": str(media), "root": str(runner.REPO_DIR)}))
            runner.redact_artifacts(root, media)
            self.assertEqual(json.loads(report.read_text()), {"path": "<FIXTURE>", "root": "<REPO>"})


if __name__ == "__main__":
    unittest.main()
