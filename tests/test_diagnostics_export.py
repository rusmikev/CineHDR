# test_diagnostics_export.py
#
# Copyright 2026 rusmikev / Diego Povliuk
# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for Video Output Diagnostics automated export and Copy Report parity."""

from __future__ import annotations

import atexit
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

# Ensure src is importable
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root))

# Hermetic GSettings setup
temp_schema_dir = None
try:
    temp_schema_dir = tempfile.mkdtemp()
    schema_src = repo_root / "data"
    shutil.copy(
        schema_src / "io.github.rusmikev.CineHDR.gschema.xml",
        Path(temp_schema_dir) / "io.github.rusmikev.CineHDR.gschema.xml",
    )
    subprocess.run(["glib-compile-schemas", temp_schema_dir], check=True)
    os.environ["GSETTINGS_SCHEMA_DIR"] = temp_schema_dir
except Exception:
    os.environ["GSETTINGS_SCHEMA_DIR"] = str(repo_root / "data")
    if temp_schema_dir:
        shutil.rmtree(temp_schema_dir, ignore_errors=True)
        temp_schema_dir = None

os.environ["GSETTINGS_BACKEND"] = "memory"


def cleanup_temp_schemas():
    if temp_schema_dir:
        shutil.rmtree(temp_schema_dir, ignore_errors=True)


atexit.register(cleanup_temp_schemas)

# Register gresource
try:
    from gi.repository import Gio
    gres_candidates = [
        os.environ.get("CINEHDR_GRESOURCE"),
        str(repo_root / "build" / "src" / "cinehdr.gresource"),
        str(repo_root / "builddir" / "src" / "cinehdr.gresource"),
        str(repo_root / "_build" / "snapshot" / "build" / "src" / "cinehdr.gresource"),
        str(repo_root / "src" / "cinehdr.gresource"),
    ]
    for candidate in gres_candidates:
        if candidate and os.path.exists(candidate):
            try:
                res = Gio.Resource.load(candidate)
                Gio.resources_register(res)
                Gio.resources_get_info(
                    "/io/github/rusmikev/CineHDR/hdr_diagnostics.ui",
                    Gio.ResourceLookupFlags.NONE,
                )
                break
            except Exception:
                continue
except Exception:
    pass

from src.diagnostics_report import (
    DIAGNOSTICS_EXPORT_DIR_ENV,
    DIAGNOSTICS_EXPORT_PATH_ENV,
    DIAGNOSTICS_SESSION_ID_ENV,
    EXPECTED_APP_COMMIT_ENV,
    EXPECTED_FLATPAK_INSTANCE_ENV,
    EXPECTED_RUNTIME_COMMIT_ENV,
    DiagnosticsExporter,
    build_video_output_report,
    collect_video_output_facts,
    generate_video_output_report,
    read_flatpak_info,
)
from src.hdr_diagnostics import HdrDiagnosticsDialog


class DiagnosticsExportTests(unittest.TestCase):
    """Test suite verifying diagnostics export and Copy Report parity."""

    def _create_mock_window(
        self,
        *,
        active_backend="opengl-next",
        session_status="active",
        requested_backend="gpu-next",
        hwdec="vaapi-copy",
        hwdec_current="vaapi-copy",
        color_state="rec2100-pq",
        target_format="GL_RGBA16F",
        target_depth=16,
        rpu_count=0,
        rpu_text=None,
    ) -> MagicMock:
        win = MagicMock()
        win.dovi_rpu_warning_count = rpu_count
        win.dovi_rpu_warning_text = rpu_text

        # GL Area
        gl_area = MagicMock()
        gl_area.render_backend_active = active_backend
        gl_area.render_backend_requested = SimpleNamespace(value=requested_backend)
        gl_area.render_backend_source = SimpleNamespace(value="gsettings")
        gl_area.render_session_status = session_status
        gl_area.render_backend_fallback_reason = None
        gl_area.render_failure_reason = None
        gl_area._render_restart_required_reason = None
        gl_area.render_target_format = target_format
        gl_area.render_target_depth = target_depth
        gl_area.render_color_state = color_state
        gl_area.render_frame_generation = 1
        gl_area._cached_scale = 1.0
        gl_area._cached_max_width = 3840
        gl_area._cached_max_height = 2160
        gl_area.render_runtime = {
            "gl_renderer": "AMD Radeon RX 7900 XTX",
            "gl_vendor": "AMD",
            "gl_version": "4.6 (Compatibility Profile) Mesa 24.1.0",
            "glsl_version": "4.60",
            "libmpv_path": "/app/lib/libmpv.so.2",
            "mpv_version": "0.38.0",
            "libplacebo_version": "6.338.2",
            "ffmpeg_version": "6.1.1",
            "mpv_configuration": "--enable-libplacebo",
        }
        gl_area.render_backend_selection = SimpleNamespace(
            configured=SimpleNamespace(value=requested_backend),
            allow_creation_fallback=True,
            source=SimpleNamespace(value="gsettings"),
        )
        gl_area.fbo_pool = SimpleNamespace(
            dropped_frames=0,
            allocation_failures=0,
            size=3,
        )

        controller = MagicMock()
        controller.is_hdr_active = True
        controller.is_hdr_content = True
        controller.hdr_mode = "force-hdr"
        controller.output_hint = "DP-1"
        controller.effective_peak_source = "metadata"
        gl_area.hdr_controller = controller

        # MPV
        mpv = MagicMock()
        mpv_props = {
            "video-params": {
                "w": 3840,
                "h": 2160,
                "pixelformat": "yuv420p10",
                "primaries": "bt.2020",
                "gamma": "pq",
                "sig-peak": 4.926,
            },
            "video-format": "hevc",
            "hwdec": hwdec,
            "hwdec-current": hwdec_current,
            "target-trc": "pq",
            "target-prim": "bt.2020",
            "target-peak": 1000,
            "estimated-vf-fps": 23.976,
            "container-fps": 23.976,
            "display-fps": 120.0,
            "frame-drop-count": 0,
            "decoder-frame-drop-count": 0,
            "vo-delayed-frame-count": 0,
            "mistimed-frame-count": 0,
            "avsync": 0.0012,
            "total-avsync-change": 0.000,
            "mpv-configuration": "--enable-libplacebo",
            "path": "/app/samples/test_video.mkv",
            "time-pos": 12.345,
        }

        def get_property(name, default=None):
            return mpv_props.get(name, default)

        def get_item(name):
            if name in mpv_props:
                return mpv_props[name]
            raise KeyError(name)

        mpv.get_property.side_effect = get_property
        mpv._get_property = None
        mpv.__getitem__.side_effect = get_item

        win._video_area = gl_area
        win.gl_area = gl_area
        win.mpv = mpv
        win.player = SimpleNamespace(mpv=mpv)
        win.offload = SimpleNamespace(get_enabled=lambda: 0)

        return win

    def test_parity_between_headless_export_and_copy_report(self):
        """Verify generate_video_output_report and HdrDiagnosticsDialog._copy_report return identical facts."""
        win = self._create_mock_window()

        # 1. Collect report headlessly
        headless_report = generate_video_output_report(win)

        # 2. Collect report through UI dialog instance
        diag = HdrDiagnosticsDialog.__new__(HdrDiagnosticsDialog)
        diag._win = win
        # Setup dummy rows to simulate UI
        diag.status_row = MagicMock()
        diag.display_hdr_row = MagicMock()
        diag.compositor_cm_row = MagicMock()
        diag.monitor_hdr_row = MagicMock()
        diag.unsupported_reason_row = MagicMock()
        diag.unsupported_reason_row.get_visible.return_value = False
        diag.offload_row = MagicMock()
        diag.color_state_row = MagicMock()
        diag.texture_format_row = MagicMock()
        diag.codec_row = MagicMock()
        diag.resolution_row = MagicMock()
        diag.hwdec_row = MagicMock()
        diag.dovi_profile_row = MagicMock()
        diag.dovi_profile_row.get_visible.return_value = False
        diag.dovi_rpu_row = MagicMock()
        diag.primaries_row = MagicMock()
        diag.trc_row = MagicMock()
        diag.peak_luma_row = MagicMock()
        diag.target_row = MagicMock()
        diag.renderer_configured_row = MagicMock()
        diag.renderer_requested_row = MagicMock()
        diag.renderer_active_row = MagicMock()
        diag.renderer_reason_row = MagicMock()
        diag.renderer_dependency_row = MagicMock()
        diag.renderer_target_row = MagicMock()
        diag.gpu_next_capability_row = MagicMock()
        diag.dovi_capability_row = MagicMock()
        diag.perf_fps_row = MagicMock()
        diag.perf_dropped_row = MagicMock()
        diag.perf_delayed_row = MagicMock()
        diag.perf_rendered_row = MagicMock()
        diag.perf_avsync_row = MagicMock()
        diag.perf_pipeline_row = MagicMock()
        diag._renderer_report_fields = {}
        diag._performance_report_fields = {}

        # Update dialog to populate internal fields
        diag.update_diagnostics()

        # Populate row subtitles from the updated rows
        for attr in dir(diag):
            if attr.endswith("_row"):
                row = getattr(diag, attr)
                if row and hasattr(row, "set_subtitle") and row.set_subtitle.call_args:
                    val = row.set_subtitle.call_args[0][0]
                    row.get_subtitle.return_value = val

        dialog_report = diag._copy_report()

        # Compare core facts across sections
        self.assertIn("Active API: opengl-next", headless_report)
        self.assertIn("Active API: opengl-next", dialog_report)
        self.assertIn("Requested this session: gpu-next", headless_report)
        self.assertIn("Requested this session: gpu-next", dialog_report)
        self.assertIn("Render target format: GL_RGBA16F", headless_report)
        self.assertIn("Render target format: GL_RGBA16F", dialog_report)
        self.assertIn("Gdk.ColorState: Rec.2100 PQ (published texture)", headless_report)
        self.assertIn("Gdk.ColorState: Rec.2100 PQ (published texture)", dialog_report)
        self.assertIn("Configured decoder: vaapi-copy", headless_report)
        self.assertIn("Configured decoder: vaapi-copy", dialog_report)
        self.assertIn("Active decoder: vaapi-copy", headless_report)
        self.assertIn("Active decoder: vaapi-copy", dialog_report)
        self.assertIn("Dolby Vision RPU Warnings: None (0 warning messages logged)", headless_report)
        self.assertIn("Dolby Vision RPU Warnings: None (0 warning messages logged)", dialog_report)

    def test_degradation_on_incomplete_data(self):
        """Verify graceful fallback to Unknown/none when window/mpv properties are missing."""
        minimal_win = SimpleNamespace(_video_area=None, gl_area=None, mpv=None, player=None)
        report = generate_video_output_report(minimal_win)

        self.assertIn("CineHDR Video Output Diagnostics", report)
        self.assertIn("Active API: not-initialized", report)
        self.assertIn("Session status: not-initialized", report)
        self.assertIn("Startup fallback reason: none", report)
        self.assertIn("Renderer failure reason: none", report)
        self.assertIn("Lifecycle restart reason: none", report)
        self.assertIn("Render target format: not-rendered", report)
        self.assertIn("Render target depth: not-rendered", report)
        self.assertIn("Gdk.ColorState: Unknown / no published color state", report)
        self.assertIn("Video Codec / Format: No video loaded", report)
        self.assertIn("Resolution & Pixel Format: Unknown", report)
        self.assertIn("Hardware Acceleration: Unknown", report)
        self.assertIn("Dolby Vision RPU Warnings: Unknown (warning observation unavailable)", report)
        self.assertIn("Published textures (includes redraws): unknown", report)
        self.assertIn("Presented frames: unavailable (texture publication is not presentation)", report)

    def test_exporter_missing_first_frame_produces_incomplete_status(self):
        """Verify that shutting down before the first frame records an incomplete evidence report."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            export_path = Path(tmp_dir) / "video_output_diagnostics.txt"
            exporter = DiagnosticsExporter(export_path=export_path)

            win = self._create_mock_window()

            # Process terminates before first frame
            ok, err = exporter.on_shutdown(win)
            self.assertTrue(ok)
            self.assertIsNone(err)

            self.assertTrue(export_path.exists())
            report_text = export_path.read_text(encoding="utf-8")
            self.assertIn("Evidence status: INCOMPLETE", report_text)
            self.assertIn("Trigger: shutdown-before-first-frame", report_text)
            self.assertIn("First frame confirmed: False", report_text)
            self.assertIn("first_frame_unconfirmed: no video frame was published before shutdown", report_text)

            json_path = export_path.with_suffix(".json")
            self.assertTrue(json_path.exists())
            manifest = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["evidence_status"], "INCOMPLETE")
            self.assertEqual(manifest["trigger"], "shutdown-before-first-frame")
            self.assertFalse(manifest["first_frame_confirmed"])
            self.assertIn("first_frame_unconfirmed", manifest["failure_reason"])

    def test_exporter_first_frame_confirmed_produces_confirmed_status(self):
        """Verify that confirmation of the first frame exports confirmed evidence when container metadata matches expectations."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            export_path = Path(tmp_dir) / "video_output_diagnostics.txt"
            mock_container_info = {
                "instance_id": "248765847",
                "app_commit": "8c6c861c4832346c611f95d8d1edbd3982c5c69a59dcdf114b144eca9982fc74",
                "runtime_commit": "b1935f7a673108616d4fd84564f15cefc9f637481472a8d4d2f0945375d41f4b",
            }
            exporter = DiagnosticsExporter(
                export_path=export_path,
                expected_app_commit=mock_container_info["app_commit"],
                expected_runtime_commit=mock_container_info["runtime_commit"],
            )

            win = self._create_mock_window()
            with patch("src.diagnostics_report.read_flatpak_info", return_value=mock_container_info):
                ok, err = exporter.on_first_frame(win)
                self.assertTrue(ok)
                self.assertIsNone(err)

                report_text = export_path.read_text(encoding="utf-8")
                self.assertIn("Evidence status: CONFIRMED", report_text)
                self.assertIn("Trigger: first-media-frame-confirmed", report_text)
                self.assertIn("First frame confirmed: True", report_text)
                self.assertIn("Evidence gaps: none", report_text)

                json_path = export_path.with_suffix(".json")
                manifest = json.loads(json_path.read_text(encoding="utf-8"))
                self.assertEqual(manifest["evidence_status"], "CONFIRMED")
                self.assertTrue(manifest["first_frame_confirmed"])
                self.assertIsNone(manifest["failure_reason"])

                # Subsequent shutdown must not overwrite the confirmed snapshot
                ok_shut, err_shut = exporter.on_shutdown(win)
                self.assertTrue(ok_shut)
                self.assertIsNone(err_shut)

                manifest_after = json.loads(json_path.read_text(encoding="utf-8"))
                self.assertEqual(manifest_after["evidence_status"], "CONFIRMED")
                self.assertEqual(manifest_after["trigger"], "first-media-frame-confirmed")

    def test_exporter_handles_write_error_gracefully(self):
        """Verify that write errors (e.g. read-only filesystem) do not crash and record the error."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            unwritable_dir = Path(tmp_dir) / "readonly_dir"
            unwritable_dir.mkdir(parents=True)
            # Make unwritable
            os.chmod(unwritable_dir, 0o400)

            export_path = unwritable_dir / "subdir" / "video_output_diagnostics.txt"
            exporter = DiagnosticsExporter(export_path=export_path)
            win = self._create_mock_window()

            try:
                ok, err = exporter.on_first_frame(win)
                self.assertFalse(ok)
                self.assertIsNotNone(err)
                self.assertIn("write_error", err)
                self.assertIsNotNone(exporter.last_write_error)
            finally:
                # Restore permissions for cleanup
                os.chmod(unwritable_dir, 0o700)

    def test_exporter_flatpak_identity_mismatch(self):
        """Verify that Flatpak instance/commit mismatch marks evidence as INCOMPLETE."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            export_path = Path(tmp_dir) / "video_output_diagnostics.txt"
            exporter = DiagnosticsExporter(
                export_path=export_path,
                expected_instance="248765847",
                expected_app_commit="8c6c861c4832346c611f95d8d1edbd3982c5c69a59dcdf114b144eca9982fc74",
                expected_runtime_commit="b1935f7a673108616d4fd84564f15cefc9f637481472a8d4d2f0945375d41f4b",
            )

            # Actual container info does not match expected instance ID
            mock_flatpak_info = {
                "instance_id": "999999999",
                "app_commit": "8c6c861c4832346c611f95d8d1edbd3982c5c69a59dcdf114b144eca9982fc74",
                "runtime_commit": "b1935f7a673108616d4fd84564f15cefc9f637481472a8d4d2f0945375d41f4b",
            }

            win = self._create_mock_window()
            exporter.first_frame_confirmed = True
            ok, err = exporter.export_snapshot(
                win, trigger="first-frame-confirmed", flatpak_info=mock_flatpak_info
            )
            self.assertTrue(ok)  # Atomic export succeeds, recording the discrepancy

            report_text = export_path.read_text(encoding="utf-8")
            self.assertIn("Evidence status: INCOMPLETE", report_text)
            self.assertIn("Identity verified: False", report_text)
            self.assertIn("instance mismatch: expected '248765847', actual '999999999'", report_text)

            json_path = export_path.with_suffix(".json")
            manifest = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["evidence_status"], "INCOMPLETE")
            self.assertFalse(manifest["identity"]["identity_verified"])
            self.assertIn("instance mismatch", manifest["failure_reason"])

    def test_read_flatpak_info_parser(self):
        """Verify parsing /.flatpak-info into structured metadata."""
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as f:
            f.write(
                "[Application]\n"
                "name=io.github.rusmikev.CineHDR\n\n"
                "[Instance]\n"
                "instance-id=123456789\n"
                "app-commit=commit_app_abc\n"
                "runtime-commit=commit_rt_xyz\n"
                "arch=x86_64\n"
                "flatpak-version=1.14.4\n"
            )
            temp_path = Path(f.name)

        try:
            info = read_flatpak_info(temp_path)
            self.assertEqual(info["instance_id"], "123456789")
            self.assertEqual(info["app_commit"], "commit_app_abc")
            self.assertEqual(info["runtime_commit"], "commit_rt_xyz")
            self.assertEqual(info["app_name"], "io.github.rusmikev.CineHDR")
            self.assertEqual(info["arch"], "x86_64")
            self.assertEqual(info["flatpak_version"], "1.14.4")
        finally:
            temp_path.unlink(missing_ok=True)

    def test_exporter_from_env_instantiation(self):
        """Verify DiagnosticsExporter.from_env() respects environment variables and defaults."""
        # When env is empty, returns None
        self.assertIsNone(DiagnosticsExporter.from_env({}))

        # When CINEHDR_DIAGNOSTICS_EXPORT_DIR is provided
        env = {
            DIAGNOSTICS_EXPORT_DIR_ENV: "/tmp/trace_dir",
            EXPECTED_FLATPAK_INSTANCE_ENV: "12345",
            EXPECTED_APP_COMMIT_ENV: "commit1",
            EXPECTED_RUNTIME_COMMIT_ENV: "commit2",
            DIAGNOSTICS_SESSION_ID_ENV: "session-xyz",
        }
        exporter = DiagnosticsExporter.from_env(env)
        self.assertIsNotNone(exporter)
        self.assertEqual(exporter.export_dir, Path("/tmp/trace_dir"))
        self.assertEqual(exporter.export_path, Path("/tmp/trace_dir/video_output_diagnostics.txt"))
        self.assertEqual(exporter.expected_instance, "12345")
        self.assertEqual(exporter.expected_app_commit, "commit1")
        self.assertEqual(exporter.expected_runtime_commit, "commit2")
        self.assertEqual(exporter.session_id, "session-xyz")

    def test_no_side_effects_on_window_or_playback(self):
        """Verify exporting diagnostics does not alter window attributes or mpv properties."""
        win = self._create_mock_window()

        # Capture state before
        initial_generation = win.gl_area.render_frame_generation
        initial_color_state = win.gl_area.render_color_state
        initial_backend = win.gl_area.render_backend_active

        report = generate_video_output_report(win)
        self.assertTrue(len(report) > 0)

        # State must remain unmodified
        self.assertEqual(win.gl_area.render_frame_generation, initial_generation)
        self.assertEqual(win.gl_area.render_color_state, initial_color_state)
        self.assertEqual(win.gl_area.render_backend_active, initial_backend)

    def test_gl_area_first_frame_notification(self):
        """Verify MpvVideoWidget notifies window via idle_add_once."""
        from src.video_widget import MpvVideoWidget

        widget = MpvVideoWidget.__new__(MpvVideoWidget)
        mock_win = MagicMock()
        widget._window = mock_win

        with patch("src.video_widget.idle_add_once") as mock_idle:
            widget._handle_first_frame_confirmed()
            mock_idle.assert_called_once_with(mock_win.on_first_video_frame)

    def test_window_first_frame_and_close_integration(self):
        """Verify CineWindow lifecycle calls DiagnosticsExporter on first frame and close."""
        from src.window import CineWindow

        win = CineWindow.__new__(CineWindow)
        mock_exporter = MagicMock()
        win._diagnostics_exporter = mock_exporter

        # 1. First frame trigger
        win.on_first_video_frame()
        mock_exporter.on_first_frame.assert_called_once_with(win)

        # 2. Close request trigger
        win._video_area = MagicMock()
        win.mpv = MagicMock()
        win.mpv.playlist = []
        win._is_gpu_validation = False
        win._inhibit_cookie = 0
        win.app = MagicMock()

        with patch("src.window.is_same_playlist", return_value=True), \
             patch("src.window.settings"):
            win.do_close_request()

        mock_exporter.on_shutdown.assert_called_once_with(win)

    def test_graphics_offload_both_enum_values(self):
        """Verify handling of both Gtk.GraphicsOffloadEnabled enum values (0=ENABLED, 1=DISABLED) and None."""
        from gi.repository import Gtk

        win = self._create_mock_window()

        # 1. Enabled (0 in PyGObject GTK4)
        win.offload = SimpleNamespace(get_enabled=lambda: Gtk.GraphicsOffloadEnabled.ENABLED)
        snap_enabled = collect_video_output_facts(win)
        self.assertEqual(snap_enabled.output["Graphics Offload"], "Enabled (subsurface / direct scanout possible)")
        self.assertEqual(snap_enabled.ui_subtitles["offload_row"], "Enabled (subsurface / direct scanout possible)")

        # 2. Disabled (1 in PyGObject GTK4)
        win.offload = SimpleNamespace(get_enabled=lambda: Gtk.GraphicsOffloadEnabled.DISABLED)
        snap_disabled = collect_video_output_facts(win)
        self.assertEqual(snap_disabled.output["Graphics Offload"], "Disabled — output goes through GTK compositing")
        self.assertEqual(snap_disabled.ui_subtitles["offload_row"], "Disabled — output goes through GTK compositing")

        # 3. None / Unknown
        win.offload = SimpleNamespace(get_enabled=lambda: None)
        snap_unknown = collect_video_output_facts(win)
        self.assertEqual(snap_unknown.output["Graphics Offload"], "Unknown")
        self.assertEqual(snap_unknown.ui_subtitles["offload_row"], "Unknown")

    def test_frame_sequence_empty_redraw_then_media_frame_then_repeat(self):
        """Sequence 'empty redraw -> media frame -> repeated frame': export is called exactly once on media frame."""
        from src.video_widget import MpvVideoWidget
        from src.render_backend import MediaFrameEvidence

        widget = MpvVideoWidget.__new__(MpvVideoWidget)
        widget._first_frame_logged = False
        widget._first_media_frame_logged = False
        widget._first_media_frame_exported = False
        widget.render_frame_generation = 0
        widget.mpv = MagicMock()
        widget._shutting_down = False
        widget._render_failed = False
        widget._force_redraw_pending = False
        widget._render_pending = False
        widget.mpv_ctx = MagicMock()
        widget.mpv_ctx.update.return_value = 1
        widget.get_width = MagicMock(return_value=1920)
        widget.get_height = MagicMock(return_value=1080)
        widget.props = SimpleNamespace(scale_factor=1)
        widget._cached_scale = 1.0
        widget._cached_max_width = 1920
        widget._cached_max_height = 1080
        widget._cached_hdr_support = False
        widget._cached_hdr_support_valid = True
        widget.hdr_controller = SimpleNamespace(is_hdr_active=False)
        widget.gl_area = MagicMock()
        widget.fbo_pool = MagicMock()
        mock_slot = MagicMock()
        mock_slot.resource.fbo_id.value = 1
        mock_slot.resource.texture_id.value = 2
        mock_slot.fence = None
        widget.fbo_pool.acquire.return_value = mock_slot
        widget.render_backend_active = "opengl-next"
        widget._fallback_slot = None
        widget.queue_draw = MagicMock()

        mock_window = MagicMock()
        widget._window = mock_window

        # Frame 1: empty redraw (media_frame_evidence returns None)
        with patch("src.video_widget.media_frame_evidence", return_value=None), \
             patch("src.video_widget.Gdk.GLTextureBuilder") as mock_builder_cls, \
             patch("src.video_widget.glBindFramebuffer"):
            mock_builder = mock_builder_cls.return_value
            mock_builder.build.return_value = MagicMock()
            widget._render_pending_frame()

        self.assertEqual(widget.render_frame_generation, 1)
        self.assertFalse(widget._first_media_frame_exported)
        mock_window.on_first_media_frame_published.assert_not_called()

        # Frame 2: media frame decoded and rendered
        real_media_frame = MediaFrameEvidence(
            path_token="token123", source_width=3840, source_height=2160, time_pos=1.5
        )
        scheduled_callbacks = []
        def fake_idle_add_once(cb, *args):
            scheduled_callbacks.append((cb, args))

        with patch("src.video_widget.media_frame_evidence", return_value=real_media_frame), \
             patch("src.video_widget.Gdk.GLTextureBuilder") as mock_builder_cls, \
             patch("src.video_widget.idle_add_once", side_effect=fake_idle_add_once), \
             patch("src.video_widget.glBindFramebuffer"):
            mock_builder = mock_builder_cls.return_value
            mock_builder.build.return_value = MagicMock()
            widget._render_pending_frame()

        self.assertEqual(widget.render_frame_generation, 2)
        self.assertTrue(widget._first_media_frame_exported)
        self.assertEqual(len(scheduled_callbacks), 1)
        cb, args = scheduled_callbacks[0]
        self.assertEqual(cb, mock_window.on_first_media_frame_published)
        self.assertEqual(args[0], real_media_frame)

        # Frame 3: repeated frame
        with patch("src.video_widget.media_frame_evidence", return_value=real_media_frame), \
             patch("src.video_widget.Gdk.GLTextureBuilder") as mock_builder_cls, \
             patch("src.video_widget.idle_add_once", side_effect=fake_idle_add_once), \
             patch("src.video_widget.glBindFramebuffer"):
            mock_builder = mock_builder_cls.return_value
            mock_builder.build.return_value = MagicMock()
            widget._render_pending_frame()

        self.assertEqual(widget.render_frame_generation, 3)
        # No additional export scheduled
        self.assertEqual(len(scheduled_callbacks), 1)

    def test_container_metadata_missing_vs_commit_mismatch_vs_verified(self):
        """Missing container metadata vs commit mismatch: identity_verified=False in both, but reasons and statuses differ."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            export_path = Path(tmp_dir) / "video_output_diagnostics.txt"
            win = self._create_mock_window()
            from src.render_backend import MediaFrameEvidence
            media_frame = MediaFrameEvidence("tok", 3840, 2160, 5.0)

            # Case A: Missing container metadata
            exp_missing = DiagnosticsExporter(export_path=export_path)
            verified_a, status_a, reasons_a, rec_a = exp_missing.check_identity(flatpak_info={})
            self.assertFalse(verified_a)
            self.assertEqual(status_a, "CONTAINER_METADATA_MISSING")
            self.assertTrue(any("container_metadata_missing" in r for r in reasons_a))

            ok, _ = exp_missing.export_snapshot(
                win, trigger="first-media-frame-confirmed", media_frame=media_frame, flatpak_info={}
            )
            self.assertTrue(ok)
            manifest_a = json.loads(export_path.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(manifest_a["evidence_status"], "INCOMPLETE")
            self.assertEqual(manifest_a["identity"]["identity_status"], "CONTAINER_METADATA_MISSING")
            self.assertFalse(manifest_a["identity"]["identity_verified"])

            # Case B: Commit mismatch
            exp_mismatch = DiagnosticsExporter(
                export_path=export_path,
                expected_app_commit="commit_expected_123",
            )
            flatpak_mismatch = {
                "instance_id": "11111",
                "app_commit": "commit_actual_456",
                "runtime_commit": "runtime_789",
            }
            verified_b, status_b, reasons_b, rec_b = exp_mismatch.check_identity(flatpak_info=flatpak_mismatch)
            self.assertFalse(verified_b)
            self.assertEqual(status_b, "IDENTITY_MISMATCH")
            self.assertTrue(any("app-commit mismatch" in r for r in reasons_b))

            ok, _ = exp_mismatch.export_snapshot(
                win, trigger="first-media-frame-confirmed", media_frame=media_frame, flatpak_info=flatpak_mismatch
            )
            self.assertTrue(ok)
            manifest_b = json.loads(export_path.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(manifest_b["evidence_status"], "INCOMPLETE")
            self.assertEqual(manifest_b["identity"]["identity_status"], "IDENTITY_MISMATCH")
            self.assertFalse(manifest_b["identity"]["identity_verified"])

            # Case C: Partial expected commit (app commit only, runtime commit missing) -> UNVERIFIED
            exp_partial = DiagnosticsExporter(
                export_path=export_path,
                expected_app_commit="commit_ok",
            )
            flatpak_valid = {
                "instance_id": "999888777",
                "app_commit": "commit_ok",
                "runtime_commit": "runtime_ok",
            }
            verified_c, status_c, reasons_c, rec_c = exp_partial.check_identity(flatpak_info=flatpak_valid)
            self.assertFalse(verified_c)
            self.assertEqual(status_c, "UNVERIFIED")
            self.assertTrue(any("partial_identity_check" in r for r in reasons_c))
            self.assertEqual(rec_c["flatpak_instance_id"], "999888777")

            exp_partial.first_frame_confirmed = True
            ok, _ = exp_partial.export_snapshot(
                win, trigger="first-media-frame-confirmed", media_frame=media_frame, flatpak_info=flatpak_valid
            )
            self.assertTrue(ok)
            manifest_c = json.loads(export_path.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(manifest_c["evidence_status"], "INCOMPLETE")
            self.assertEqual(manifest_c["identity"]["identity_status"], "UNVERIFIED")
            self.assertFalse(manifest_c["identity"]["identity_verified"])
            self.assertEqual(manifest_c["identity"]["flatpak_instance_id"], "999888777")

            # Case D: Both expected app and runtime commits provided and matched -> VERIFIED
            exp_verified = DiagnosticsExporter(
                export_path=export_path,
                expected_app_commit="commit_ok",
                expected_runtime_commit="runtime_ok",
            )
            verified_d, status_d, reasons_d, rec_d = exp_verified.check_identity(flatpak_info=flatpak_valid)
            self.assertTrue(verified_d)
            self.assertEqual(status_d, "VERIFIED")
            self.assertEqual(len(reasons_d), 0)
            self.assertEqual(rec_d["flatpak_instance_id"], "999888777")

            exp_verified.first_frame_confirmed = True
            ok, _ = exp_verified.export_snapshot(
                win, trigger="first-media-frame-confirmed", media_frame=media_frame, flatpak_info=flatpak_valid
            )
            self.assertTrue(ok)
            manifest_d = json.loads(export_path.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(manifest_d["evidence_status"], "CONFIRMED")
            self.assertEqual(manifest_d["identity"]["identity_status"], "VERIFIED")
            self.assertTrue(manifest_d["identity"]["identity_verified"])
            self.assertEqual(manifest_d["identity"]["flatpak_instance_id"], "999888777")

    def test_no_disk_io_inside_render_callback(self):
        """Zero file I/O operations occur inside the render callback; disk writes occur in idle callback."""
        from src.video_widget import MpvVideoWidget
        from src.render_backend import MediaFrameEvidence

        widget = MpvVideoWidget.__new__(MpvVideoWidget)
        widget._first_frame_logged = False
        widget._first_media_frame_logged = False
        widget._first_media_frame_exported = False
        widget.render_frame_generation = 0
        widget.mpv = MagicMock()
        widget._shutting_down = False
        widget._render_failed = False
        widget._force_redraw_pending = False
        widget._render_pending = False
        widget.mpv_ctx = MagicMock()
        widget.mpv_ctx.update.return_value = 1
        widget.get_width = MagicMock(return_value=1920)
        widget.get_height = MagicMock(return_value=1080)
        widget.props = SimpleNamespace(scale_factor=1)
        widget._cached_scale = 1.0
        widget._cached_max_width = 1920
        widget._cached_max_height = 1080
        widget._cached_hdr_support = False
        widget._cached_hdr_support_valid = True
        widget.hdr_controller = SimpleNamespace(is_hdr_active=False)
        widget.gl_area = MagicMock()
        widget.fbo_pool = MagicMock()
        mock_slot = MagicMock()
        mock_slot.resource.fbo_id.value = 1
        mock_slot.resource.texture_id.value = 2
        mock_slot.fence = None
        widget.fbo_pool.acquire.return_value = mock_slot
        widget.render_backend_active = "opengl-next"
        widget._fallback_slot = None
        widget.queue_draw = MagicMock()

        mock_window = MagicMock()
        widget._window = mock_window

        real_media_frame = MediaFrameEvidence("tok", 3840, 2160, 2.0)
        scheduled_idle = []

        with patch("src.video_widget.media_frame_evidence", return_value=real_media_frame), \
             patch("src.video_widget.Gdk.GLTextureBuilder") as mock_builder_cls, \
             patch("src.video_widget.idle_add_once", side_effect=lambda cb, *a: scheduled_idle.append((cb, a))), \
             patch("src.video_widget.glBindFramebuffer"), \
             patch("builtins.open") as mock_open, \
             patch("pathlib.Path.write_text") as mock_write_text:

            widget._render_pending_frame()

            # Zero disk writes occurred inside the render callback
            mock_open.assert_not_called()
            mock_write_text.assert_not_called()

        # The idle callback was queued
        self.assertEqual(len(scheduled_idle), 1)

    def test_regression_mutable_mpv_responses_no_reread(self):
        """UI subtitles and Copy Report share single atomic snapshot; mutating mpv properties does not desynchronize them."""
        win = self._create_mock_window()

        diag = HdrDiagnosticsDialog.__new__(HdrDiagnosticsDialog)
        diag._win = win
        for row_name in (
            "status_row", "display_hdr_row", "compositor_cm_row", "monitor_hdr_row",
            "unsupported_reason_row", "offload_row", "color_state_row", "texture_format_row",
            "codec_row", "resolution_row", "hwdec_row", "dovi_profile_row", "dovi_rpu_row",
            "primaries_row", "trc_row", "peak_luma_row", "target_row", "renderer_configured_row",
            "renderer_requested_row", "renderer_active_row", "renderer_reason_row",
            "renderer_dependency_row", "renderer_target_row", "gpu_next_capability_row",
            "dovi_capability_row", "perf_fps_row", "perf_dropped_row", "perf_delayed_row",
            "perf_rendered_row", "perf_avsync_row", "perf_pipeline_row"
        ):
            setattr(diag, row_name, MagicMock())
        diag.unsupported_reason_row.get_visible.return_value = False
        diag.dovi_profile_row.get_visible.return_value = False
        diag._renderer_report_fields = {}
        diag._performance_report_fields = {}

        # 1. Update diagnostics to take initial snapshot (0 drops)
        diag.update_diagnostics()

        for attr in dir(diag):
            if attr.endswith("_row"):
                row = getattr(diag, attr)
                if row and hasattr(row, "set_subtitle") and row.set_subtitle.call_args:
                    val = row.set_subtitle.call_args[0][0]
                    row.get_subtitle.return_value = val

        initial_ui_dropped = diag.perf_dropped_row.get_subtitle()
        self.assertIn("VO: 0", initial_ui_dropped)

        # 2. Mutate mpv property responses directly (simulating mpv drops increasing during playback)
        def mutated_get_property(name, default=None):
            if name == "frame-drop-count":
                return 999
            if name == "avsync":
                return 0.500
            try:
                return win.mpv[name]
            except Exception:
                return default

        win.mpv.get_property.side_effect = mutated_get_property

        # 3. Call _copy_report() and verify facts are from self._current_snapshot
        report_text = diag._copy_report()
        self.assertIn("VO frame drops: 0", report_text)
        self.assertNotIn("VO frame drops: 999", report_text)

        # Calling _update_performance_info reuses self._current_snapshot and does not re-read mutated mpv
        diag._update_performance_info(win.mpv, win.gl_area)
        self.assertIn("VO: 0", diag.perf_dropped_row.set_subtitle.call_args[0][0])

    def test_regression_evidence_status_rejections(self):
        """evidence_status is computed strictly after snapshot; opengl-cb, non-active session, GL_RGBA8, and missing video stream yield INCOMPLETE."""
        from src.render_backend import MediaFrameEvidence

        with tempfile.TemporaryDirectory() as tmp_dir:
            export_path = Path(tmp_dir) / "video_output_diagnostics.txt"
            mock_container = {
                "instance_id": "123",
                "app_commit": "app_abc",
                "runtime_commit": "rt_xyz",
            }
            exporter = DiagnosticsExporter(
                export_path=export_path,
                expected_app_commit="app_abc",
                expected_runtime_commit="rt_xyz",
            )
            exporter.first_frame_confirmed = True
            media_frame = MediaFrameEvidence("tok", 1920, 1080, 1.0)

            # 1. Fallback to opengl-cb
            win_cb = self._create_mock_window(active_backend="opengl-cb")
            ok, _ = exporter.export_snapshot(win_cb, media_frame=media_frame, flatpak_info=mock_container)
            self.assertTrue(ok)
            manifest = json.loads(export_path.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["evidence_status"], "INCOMPLETE")
            self.assertIn("backend_fallback", manifest["failure_reason"])

            exporter.export_completed = False

            # 2. Session status not active (startup-fallback)
            win_session = self._create_mock_window(session_status="startup-fallback")
            ok, _ = exporter.export_snapshot(win_session, media_frame=media_frame, flatpak_info=mock_container)
            self.assertTrue(ok)
            manifest = json.loads(export_path.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["evidence_status"], "INCOMPLETE")
            self.assertIn("session_not_active", manifest["failure_reason"])

            exporter.export_completed = False

            # 3. 8-bit render target (GL_RGBA8)
            win_rgba8 = self._create_mock_window(target_format="GL_RGBA8", target_depth=8)
            ok, _ = exporter.export_snapshot(win_rgba8, media_frame=media_frame, flatpak_info=mock_container)
            self.assertTrue(ok)
            manifest = json.loads(export_path.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["evidence_status"], "INCOMPLETE")
            self.assertIn("target_format_insufficient", manifest["failure_reason"])

            exporter.export_completed = False

            # 4. Missing video stream
            win_novideo = self._create_mock_window()
            win_novideo.mpv.get_property.side_effect = lambda n, d=None: None
            win_novideo.mpv.__getitem__.side_effect = KeyError
            ok, _ = exporter.export_snapshot(win_novideo, media_frame=media_frame, flatpak_info=mock_container)
            self.assertTrue(ok)
            manifest = json.loads(export_path.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["evidence_status"], "INCOMPLETE")
            self.assertIn("video_stream_unconfirmed", manifest["failure_reason"])

    def test_regression_deferred_export_invalidation(self):
        """Deferred export callback invalidates on shutdown, media change, same file reload, and context recreation."""
        import hashlib
        from src.render_backend import MediaFrameEvidence
        from src.window import CineWindow

        win = CineWindow.__new__(CineWindow)
        win._shutting_down = False
        win._media_load_generation = 1
        video_area = MagicMock()
        video_area.render_context_generation = 1
        win._video_area = video_area
        win.gl_area = video_area

        mock_mpv = MagicMock()
        mock_mpv.get_property.side_effect = lambda n, d=None: "/app/video.mkv" if n == "path" else d
        win.mpv = mock_mpv
        win.player = SimpleNamespace(mpv=mock_mpv)

        mock_exporter = MagicMock()
        mock_exporter.export_completed = False
        win._diagnostics_exporter = mock_exporter

        valid_path = "/app/video.mkv"
        canonical_path = os.path.realpath(os.path.abspath(valid_path))
        valid_token = hashlib.sha256(os.fsencode(canonical_path)).hexdigest()
        media_frame = MediaFrameEvidence(valid_token, 1920, 1080, 1.0)

        # Baseline: valid call passes through
        win.on_first_media_frame_published(media_frame, context_generation=1, load_generation=1)
        mock_exporter.on_first_media_frame.assert_called_once_with(
            win, media_frame, context_generation=1, load_generation=1
        )
        mock_exporter.reset_mock()

        # Invalidation 1: Window shutting down
        win._shutting_down = True
        win.on_first_media_frame_published(media_frame, context_generation=1, load_generation=1)
        mock_exporter.on_first_media_frame.assert_not_called()
        win._shutting_down = False

        # Invalidation 2: Context recreation (context_generation mismatch)
        win.on_first_media_frame_published(media_frame, context_generation=0, load_generation=1)
        mock_exporter.on_first_media_frame.assert_not_called()

        # Invalidation 3: Media change or same file reload (load_generation mismatch)
        win._media_load_generation = 2  # Incremented by start-file observer
        win.on_first_media_frame_published(media_frame, context_generation=1, load_generation=1)
        mock_exporter.on_first_media_frame.assert_not_called()
        win._media_load_generation = 1

        # Invalidation 4: Path token mismatch (different media playing)
        stale_frame = MediaFrameEvidence("different_token", 1920, 1080, 1.0)
        win.on_first_media_frame_published(stale_frame, context_generation=1, load_generation=1)
        mock_exporter.on_first_media_frame.assert_not_called()

    def test_regression_unknown_path_and_missing_evidence(self):
        """1. When path is unknown and media_frame_evidence is None, identity check fails and export is not called."""
        from src.render_backend import MediaFrameEvidence
        from src.window import CineWindow

        win = CineWindow.__new__(CineWindow)
        win._shutting_down = False
        win._media_load_generation = 1
        video_area = MagicMock()
        video_area.render_context_generation = 1
        video_area._first_media_frame_exported = True
        video_area._exported_media_generation = (1, 1)
        win._video_area = video_area
        win.gl_area = video_area

        mock_mpv = MagicMock()
        mock_mpv.get_property.return_value = None
        win.mpv = mock_mpv
        win.player = SimpleNamespace(mpv=mock_mpv)

        mock_exporter = MagicMock()
        mock_exporter.export_completed = False
        win._diagnostics_exporter = mock_exporter

        media_frame = MediaFrameEvidence("some_token", 1920, 1080, 1.0)

        with patch("src.render_backend.media_frame_evidence", return_value=None):
            win.on_first_media_frame_published(media_frame, context_generation=1, load_generation=1)

        mock_exporter.on_first_media_frame.assert_not_called()
        self.assertFalse(video_area._first_media_frame_exported)
        self.assertIsNone(video_area._exported_media_generation)

    def test_regression_identity_check_exception(self):
        """2. When an exception occurs during identity check, it is not swallowed to allow export."""
        from src.render_backend import MediaFrameEvidence
        from src.window import CineWindow

        win = CineWindow.__new__(CineWindow)
        win._shutting_down = False
        win._media_load_generation = 1
        video_area = MagicMock()
        video_area.render_context_generation = 1
        video_area._first_media_frame_exported = True
        video_area._exported_media_generation = (1, 1)
        win._video_area = video_area
        win.gl_area = video_area

        mock_mpv = MagicMock()
        mock_mpv.get_property.side_effect = RuntimeError("mpv property read failed")
        win.mpv = mock_mpv
        win.player = SimpleNamespace(mpv=mock_mpv)

        mock_exporter = MagicMock()
        mock_exporter.export_completed = False
        win._diagnostics_exporter = mock_exporter

        media_frame = MediaFrameEvidence("some_token", 1920, 1080, 1.0)

        win.on_first_media_frame_published(media_frame, context_generation=1, load_generation=1)

        mock_exporter.on_first_media_frame.assert_not_called()
        self.assertFalse(video_area._first_media_frame_exported)
        self.assertIsNone(video_area._exported_media_generation)

    def test_regression_load_change_discards_old_and_exports_new(self):
        """3. Callback queued -> media load changes -> old callback discarded -> suitable new frame -> exactly one export."""
        from src.render_backend import MediaFrameEvidence
        from src.video_widget import MpvVideoWidget
        from src.window import CineWindow
        import hashlib

        win = CineWindow.__new__(CineWindow)
        win._shutting_down = False
        win._media_load_generation = 1

        mock_exporter = MagicMock()
        mock_exporter.export_completed = False
        win._diagnostics_exporter = mock_exporter

        widget = MpvVideoWidget.__new__(MpvVideoWidget)
        widget._window = win
        widget.render_context_generation = 1
        widget.render_frame_generation = 0
        widget._first_frame_logged = False
        widget._first_media_frame_logged = False
        widget._first_media_frame_exported = False
        widget._exported_media_generation = None
        widget.mpv = MagicMock()
        widget._shutting_down = False
        widget._render_failed = False
        widget._force_redraw_pending = False
        widget._render_pending = False
        widget.mpv_ctx = MagicMock()
        widget.mpv_ctx.update.return_value = 1
        widget.get_width = MagicMock(return_value=1920)
        widget.get_height = MagicMock(return_value=1080)
        widget.props = SimpleNamespace(scale_factor=1)
        widget._cached_scale = 1.0
        widget._cached_max_width = 1920
        widget._cached_max_height = 1080
        widget._cached_hdr_support = False
        widget._cached_hdr_support_valid = True
        widget.hdr_controller = SimpleNamespace(is_hdr_active=False)
        widget.gl_area = MagicMock()
        widget.fbo_pool = MagicMock()
        mock_slot = MagicMock()
        mock_slot.resource.fbo_id.value = 1
        mock_slot.resource.texture_id.value = 2
        mock_slot.fence = None
        widget.fbo_pool.acquire.return_value = mock_slot
        widget.render_backend_active = "opengl-next"
        widget._fallback_slot = None
        widget.queue_draw = MagicMock()
        win._video_area = widget
        win.gl_area = widget

        scheduled_callbacks = []
        def fake_idle_add_once(cb, *args):
            scheduled_callbacks.append((cb, args))

        path_1 = "/app/file1.mkv"
        token_1 = hashlib.sha256(os.fsencode(os.path.realpath(os.path.abspath(path_1)))).hexdigest()
        frame_1 = MediaFrameEvidence(token_1, 1920, 1080, 1.0)

        path_2 = "/app/file2.mkv"
        token_2 = hashlib.sha256(os.fsencode(os.path.realpath(os.path.abspath(path_2)))).hexdigest()
        frame_2 = MediaFrameEvidence(token_2, 3840, 2160, 2.0)

        # 1. File 1 rendered: queues callback 1
        win.mpv = MagicMock()
        win.mpv.get_property.side_effect = lambda n, d=None: path_1 if n == "path" else d
        win.player = SimpleNamespace(mpv=win.mpv)

        with patch("src.video_widget.media_frame_evidence", return_value=frame_1), \
             patch("src.video_widget.Gdk.GLTextureBuilder") as mock_builder_cls, \
             patch("src.video_widget.idle_add_once", side_effect=fake_idle_add_once), \
             patch("src.video_widget.glBindFramebuffer"):
            mock_builder_cls.return_value.build.return_value = MagicMock()
            widget._render_pending_frame()

        self.assertEqual(len(scheduled_callbacks), 1)
        old_cb, old_args = scheduled_callbacks[0]

        # 2. Media load changes to File 2: load generation advances
        win._media_load_generation = 2
        win.mpv.get_property.side_effect = lambda n, d=None: path_2 if n == "path" else d

        # 3. Old callback 1 executes: must be dropped
        old_cb(*old_args)
        mock_exporter.on_first_media_frame.assert_not_called()

        # 4. File 2 renders: schedules callback 2
        with patch("src.video_widget.media_frame_evidence", return_value=frame_2), \
             patch("src.video_widget.Gdk.GLTextureBuilder") as mock_builder_cls, \
             patch("src.video_widget.idle_add_once", side_effect=fake_idle_add_once), \
             patch("src.video_widget.glBindFramebuffer"):
            mock_builder_cls.return_value.build.return_value = MagicMock()
            widget._render_pending_frame()

        self.assertEqual(len(scheduled_callbacks), 2)
        new_cb, new_args = scheduled_callbacks[1]

        # 5. Callback 2 executes: exports File 2
        def mark_exported(w, f, *args, **kwargs):
            mock_exporter.export_completed = True
        mock_exporter.on_first_media_frame.side_effect = mark_exported

        new_cb(*new_args)
        mock_exporter.on_first_media_frame.assert_called_once_with(
            win, frame_2, context_generation=1, load_generation=2
        )

        # 6. Subsequent frame of File 2 does not schedule further export
        with patch("src.video_widget.media_frame_evidence", return_value=frame_2), \
             patch("src.video_widget.Gdk.GLTextureBuilder") as mock_builder_cls, \
             patch("src.video_widget.idle_add_once", side_effect=fake_idle_add_once), \
             patch("src.video_widget.glBindFramebuffer"):
            mock_builder_cls.return_value.build.return_value = MagicMock()
            widget._render_pending_frame()

        self.assertEqual(len(scheduled_callbacks), 2)

    def test_regression_late_old_callback_does_not_cancel_new_notification(self):
        """4. A late old callback must not reset or cancel a newer notification."""
        from src.render_backend import MediaFrameEvidence
        from src.window import CineWindow
        import hashlib

        win = CineWindow.__new__(CineWindow)
        win._shutting_down = False
        win._media_load_generation = 2

        video_area = MagicMock()
        video_area.render_context_generation = 1
        video_area._first_media_frame_exported = True
        video_area._exported_media_generation = (1, 2)
        win._video_area = video_area
        win.gl_area = video_area

        path_2 = "/app/file2.mkv"
        token_2 = hashlib.sha256(os.fsencode(os.path.realpath(os.path.abspath(path_2)))).hexdigest()
        frame_2 = MediaFrameEvidence(token_2, 3840, 2160, 2.0)

        mock_mpv = MagicMock()
        mock_mpv.get_property.side_effect = lambda n, d=None: path_2 if n == "path" else d
        win.mpv = mock_mpv
        win.player = SimpleNamespace(mpv=mock_mpv)

        mock_exporter = MagicMock()
        mock_exporter.export_completed = False
        win._diagnostics_exporter = mock_exporter

        # Late old callback from load_generation=1 arrives
        old_frame = MediaFrameEvidence("old_token", 1920, 1080, 1.0)
        win.on_first_media_frame_published(old_frame, context_generation=1, load_generation=1)

        # Verify old callback did not export and did NOT reset video_area state for generation 2
        mock_exporter.on_first_media_frame.assert_not_called()
        self.assertTrue(video_area._first_media_frame_exported)
        self.assertEqual(video_area._exported_media_generation, (1, 2))

        # Now newer callback for load_generation=2 arrives and succeeds
        win.on_first_media_frame_published(frame_2, context_generation=1, load_generation=2)
        mock_exporter.on_first_media_frame.assert_called_once_with(
            win, frame_2, context_generation=1, load_generation=2
        )

    def test_regression_shutdown_and_completed_export_prevent_reexport(self):
        """5. Shutdown and completed export prevent any repeated export or callback queuing."""
        from src.render_backend import MediaFrameEvidence
        from src.video_widget import MpvVideoWidget
        from src.window import CineWindow
        import hashlib

        win = CineWindow.__new__(CineWindow)
        win._shutting_down = False
        win._media_load_generation = 1

        mock_exporter = MagicMock()
        mock_exporter.export_completed = True
        win._diagnostics_exporter = mock_exporter

        video_area = MagicMock()
        video_area.render_context_generation = 1
        video_area._first_media_frame_exported = True
        video_area._exported_media_generation = (1, 1)
        win._video_area = video_area
        win.gl_area = video_area

        path = "/app/file.mkv"
        token = hashlib.sha256(os.fsencode(os.path.realpath(os.path.abspath(path)))).hexdigest()
        frame = MediaFrameEvidence(token, 1920, 1080, 1.0)

        # 1. When export_completed is True: callback does not export
        win.on_first_media_frame_published(frame, context_generation=1, load_generation=1)
        mock_exporter.on_first_media_frame.assert_not_called()

        # 2. When export_completed is True: widget does not queue callback
        widget = MpvVideoWidget.__new__(MpvVideoWidget)
        widget._window = win
        widget.render_context_generation = 1
        widget.render_frame_generation = 0
        widget._first_frame_logged = True
        widget._first_media_frame_logged = True
        widget._first_media_frame_exported = False
        widget._exported_media_generation = None
        widget.mpv = MagicMock()
        widget._shutting_down = False
        widget._render_failed = False
        widget._force_redraw_pending = False
        widget._render_pending = False
        widget.mpv_ctx = MagicMock()
        widget.mpv_ctx.update.return_value = 1
        widget.get_width = MagicMock(return_value=1920)
        widget.get_height = MagicMock(return_value=1080)
        widget.props = SimpleNamespace(scale_factor=1)
        widget._cached_scale = 1.0
        widget._cached_max_width = 1920
        widget._cached_max_height = 1080
        widget._cached_hdr_support = False
        widget._cached_hdr_support_valid = True
        widget.hdr_controller = SimpleNamespace(is_hdr_active=False)
        widget.gl_area = MagicMock()
        widget.fbo_pool = MagicMock()
        mock_slot = MagicMock()
        mock_slot.resource.fbo_id.value = 1
        mock_slot.resource.texture_id.value = 2
        mock_slot.fence = None
        widget.fbo_pool.acquire.return_value = mock_slot
        widget.render_backend_active = "opengl-next"
        widget._fallback_slot = None
        widget.queue_draw = MagicMock()

        scheduled = []
        with patch("src.video_widget.media_frame_evidence", return_value=frame), \
             patch("src.video_widget.Gdk.GLTextureBuilder") as mock_builder_cls, \
             patch("src.video_widget.idle_add_once", side_effect=lambda cb, *a: scheduled.append(cb)), \
             patch("src.video_widget.glBindFramebuffer"):
            mock_builder_cls.return_value.build.return_value = MagicMock()
            widget._render_pending_frame()

        self.assertEqual(len(scheduled), 0)

        # 3. When shutting_down is True: callback and widget do not export
        mock_exporter.export_completed = False
        win._shutting_down = True
        win.on_first_media_frame_published(frame, context_generation=1, load_generation=1)
        mock_exporter.on_first_media_frame.assert_not_called()

        with patch("src.video_widget.media_frame_evidence", return_value=frame), \
             patch("src.video_widget.Gdk.GLTextureBuilder") as mock_builder_cls, \
             patch("src.video_widget.idle_add_once", side_effect=lambda cb, *a: scheduled.append(cb)), \
             patch("src.video_widget.glBindFramebuffer"):
            mock_builder_cls.return_value.build.return_value = MagicMock()
            widget._render_pending_frame()

        self.assertEqual(len(scheduled), 0)

    def test_stale_callback_clobbers_new_generation_state(self):
        """Blocker A: Old callback must not clobber newer generation state if token check fails."""
        from src.render_backend import MediaFrameEvidence
        from src.video_widget import MpvVideoWidget
        from src.window import CineWindow

        win = CineWindow.__new__(CineWindow)
        win._shutting_down = False
        win._media_load_generation = 1
        mock_exporter = MagicMock()
        mock_exporter.export_completed = False
        win._diagnostics_exporter = mock_exporter
        widget = MpvVideoWidget.__new__(MpvVideoWidget)
        widget._window = win
        widget.render_context_generation = 1
        widget._first_media_frame_exported = True
        widget._exported_media_generation = (1, 1)
        win._video_area = widget
        win.gl_area = widget
        old_token = "token_file1"
        old_frame = MediaFrameEvidence(old_token, 1920, 1080, 1.0)

        def prop_side_effect(name):
            if name == "path":
                win._media_load_generation = 2
                widget._exported_media_generation = (1, 2)
                widget._first_media_frame_exported = True
                return "/app/file2.mkv"
            return None

        mock_mpv = MagicMock()
        mock_mpv.get_property.side_effect = prop_side_effect
        win.mpv = mock_mpv
        win.player = SimpleNamespace(mpv=mock_mpv)
        win.on_first_media_frame_published(old_frame, context_generation=1, load_generation=1)
        self.assertEqual(
            widget._exported_media_generation, (1, 2),
            f"DEFECT: Old callback clobbered new generation state to {widget._exported_media_generation}"
        )

    def test_same_file_reload_race_causes_premature_export(self):
        """Blocker B: Reopening same file during callback must not prematurely trigger export."""
        import hashlib
        from src.render_backend import MediaFrameEvidence
        from src.video_widget import MpvVideoWidget
        from src.window import CineWindow

        win = CineWindow.__new__(CineWindow)
        win._shutting_down = False
        win._media_load_generation = 1
        mock_exporter = MagicMock()
        mock_exporter.export_completed = False
        win._diagnostics_exporter = mock_exporter
        widget = MpvVideoWidget.__new__(MpvVideoWidget)
        widget._window = win
        widget.render_context_generation = 1
        widget._first_media_frame_exported = True
        widget._exported_media_generation = (1, 1)
        win._video_area = widget
        win.gl_area = widget
        path = "/app/sample.mkv"
        canonical = os.path.realpath(os.path.abspath(path))
        token = hashlib.sha256(os.fsencode(canonical)).hexdigest()
        frame = MediaFrameEvidence(token, 1920, 1080, 1.0)

        def prop_side_effect(name):
            if name == "path":
                win._media_load_generation = 2
                return path
            return None

        mock_mpv = MagicMock()
        mock_mpv.get_property.side_effect = prop_side_effect
        win.mpv = mock_mpv
        win.player = SimpleNamespace(mpv=mock_mpv)
        win.on_first_media_frame_published(frame, context_generation=1, load_generation=1)
        mock_exporter.on_first_media_frame.assert_not_called()

    def test_generation_change_during_snapshot_collection_rejects_confirmed(self):
        """Generation advancing during snapshot collection marks report INCOMPLETE and not completed."""
        import hashlib
        import src.diagnostics_report
        from src.render_backend import MediaFrameEvidence

        with tempfile.TemporaryDirectory() as tmp_dir:
            export_path = Path(tmp_dir) / "diag.txt"
            exporter = DiagnosticsExporter(
                export_path=export_path,
                expected_instance="app.test.1",
                expected_app_commit="commit123",
                expected_runtime_commit="runtime123",
            )
            win = self._create_mock_window()
            win._shutting_down = False
            win._media_load_generation = 1
            gl_area = win.gl_area
            gl_area.render_context_generation = 1

            token = hashlib.sha256(b"/app/test.mkv").hexdigest()
            frame = MediaFrameEvidence(token, 1920, 1080, 1.0)

            original_collect = src.diagnostics_report.collect_video_output_facts

            def collect_with_race(w):
                w._media_load_generation = 2
                return original_collect(w)

            with patch("src.diagnostics_report.collect_video_output_facts", side_effect=collect_with_race), \
                 patch("src.diagnostics_report.read_flatpak_info", return_value={"instance": "app.test.1", "app-commit": "commit123", "runtime-commit": "runtime123"}):
                ok, err = exporter.on_first_media_frame(
                    win,
                    frame,
                    context_generation=1,
                    load_generation=1,
                )

            self.assertTrue(ok)
            self.assertFalse(exporter.export_completed)
            self.assertEqual(exporter.export_status, "INCOMPLETE")
            self.assertFalse(exporter.first_frame_confirmed)

            manifest_path = export_path.with_suffix(".json")
            self.assertTrue(manifest_path.exists())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["evidence_status"], "INCOMPLETE")
            self.assertTrue(
                any("generation_mismatch_during_collection" in gap for gap in manifest["evidence_gaps"])
            )

    def test_call_boundary_generation_advance_rejects_and_allows_next_generation_export(self):
        """Call boundary race: load_generation advancing on invocation boundary rejects mixed snapshot and allows next frame."""
        import hashlib
        from types import SimpleNamespace
        from src.diagnostics_report import DiagnosticSnapshot
        from src.render_backend import MediaFrameEvidence
        from src.window import CineWindow

        with tempfile.TemporaryDirectory() as tmp_dir:
            export_path = Path(tmp_dir) / "diag.txt"
            exporter = DiagnosticsExporter(
                export_path=export_path,
                expected_instance="app.test.1",
                expected_app_commit="commit123",
                expected_runtime_commit="runtime123",
            )

            win = CineWindow.__new__(CineWindow)
            win._shutting_down = False
            win._media_load_generation = 1
            win._diagnostics_exporter = exporter

            video_area = SimpleNamespace(
                render_context_generation=1,
                _first_media_frame_exported=True,
                _exported_media_generation=(1, 1),
            )
            win._video_area = video_area
            win.gl_area = video_area

            path = "/app/same_movie.mkv"
            token = hashlib.sha256(os.fsencode(os.path.realpath(os.path.abspath(path)))).hexdigest()
            frame_gen1 = MediaFrameEvidence(token, 1920, 1080, 1.0)

            mock_mpv = MagicMock()
            mock_mpv.get_property.side_effect = lambda n, d=None: path if n == "path" else d
            win.mpv = mock_mpv
            win.player = SimpleNamespace(mpv=mock_mpv)

            mock_snapshot = DiagnosticSnapshot(
                sample_time_utc="2026-09-25T12:00:00Z",
                renderer={
                    "Active API": "opengl-next",
                    "Session status": "active",
                    "Render target format": "GL_RGBA16F",
                    "Render target depth": 16,
                },
                output={"Gdk.ColorState": "Rec.2100 PQ"},
                video={"Video Codec / Format": "hevc"},
                performance={},
                ui_subtitles={},
                ui_visibilities={},
            )

            flatpak_info = {
                "instance_id": "app.test.1",
                "app_commit": "commit123",
                "runtime_commit": "runtime123",
            }

            # Simulate background start-file event advancing generation from 1 to 2 at boundary
            original_on_first_media_frame = exporter.on_first_media_frame

            def on_first_media_frame_with_race(*args, **kwargs):
                win._media_load_generation = 2
                return original_on_first_media_frame(*args, **kwargs)

            exporter.on_first_media_frame = on_first_media_frame_with_race

            with patch("src.diagnostics_report.collect_video_output_facts", return_value=mock_snapshot), \
                 patch("src.diagnostics_report.read_flatpak_info", return_value=flatpak_info):
                win.on_first_media_frame_published(
                    frame_gen1,
                    context_generation=1,
                    load_generation=1,
                )

            # Mixed snapshot rejected
            self.assertFalse(exporter.export_completed)
            self.assertEqual(exporter.export_status, "INCOMPLETE")
            self.assertFalse(exporter.first_frame_confirmed)

            manifest_path = export_path.with_suffix(".json")
            self.assertTrue(manifest_path.exists())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["evidence_status"], "INCOMPLETE")
            self.assertTrue(
                any("generation_mismatch_during_collection" in gap for gap in manifest["evidence_gaps"])
            )

            # Next generation frame arrives and successfully exports
            frame_gen2 = MediaFrameEvidence(token, 1920, 1080, 2.0)
            video_area._exported_media_generation = (1, 2)
            video_area._first_media_frame_exported = True
            exporter.on_first_media_frame = original_on_first_media_frame

            with patch("src.diagnostics_report.collect_video_output_facts", return_value=mock_snapshot), \
                 patch("src.diagnostics_report.read_flatpak_info", return_value=flatpak_info):
                win.on_first_media_frame_published(
                    frame_gen2,
                    context_generation=1,
                    load_generation=2,
                )

            self.assertTrue(exporter.export_completed)
            self.assertEqual(exporter.export_status, "CONFIRMED")
            self.assertTrue(exporter.first_frame_confirmed)
            manifest2 = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest2["evidence_status"], "CONFIRMED")
            self.assertEqual(manifest2["evidence_gaps"], [])


if __name__ == "__main__":
    unittest.main()
