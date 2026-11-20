"""Characterization tests for src.diagnostics_report (C24/R4)."""

import atexit
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_TEMP_SCHEMA_DIR = tempfile.mkdtemp(prefix="cinehdr-diag-report-schema-")
atexit.register(shutil.rmtree, _TEMP_SCHEMA_DIR, ignore_errors=True)
shutil.copy2(
    _REPO_ROOT / "data" / "io.github.rusmikev.CineHDR.gschema.xml",
    Path(_TEMP_SCHEMA_DIR) / "io.github.rusmikev.CineHDR.gschema.xml",
)
subprocess.run(["glib-compile-schemas", _TEMP_SCHEMA_DIR], check=True)
os.environ["GSETTINGS_SCHEMA_DIR"] = _TEMP_SCHEMA_DIR
os.environ["GSETTINGS_BACKEND"] = "memory"

import gi  # noqa: E402

gi.require_version("Gio", "2.0")
from gi.repository import Gio  # noqa: E402

if _gresource_path := os.environ.get("CINEHDR_GRESOURCE"):
    Gio.resources_register(Gio.Resource.load(_gresource_path))

from src import diagnostics_report  # noqa: E402
from src.gtk_cm_policy import REASON_MISSING_TF_SRGB  # noqa: E402
from src.diagnostics_report import (  # noqa: E402
    DIAGNOSTICS_EXPORT_DIR_ENV,
    DIAGNOSTICS_EXPORT_PATH_ENV,
    DIAGNOSTICS_SESSION_ID_ENV,
    EXPECTED_APP_COMMIT_ENV,
    EXPECTED_FLATPAK_INSTANCE_ENV,
    EXPECTED_RUNTIME_COMMIT_ENV,
    DiagnosticSnapshot,
    _MEDIA_PATH_FIELDS,
    _hdr_status_text,
    _positive_float,
    _single_line,
    _source_peak_nits,
    build_video_output_report,
    collect_performance_facts,
    collect_video_output_facts,
    format_dovi_rpu_warnings,
    generate_video_output_report,
    get_dovi_rpu_warning_state,
    get_mpv_prop,
    read_flatpak_info,
)
from src.render_backend import (  # noqa: E402
    ProcessRenderSelection,
    RenderBackend,
    RenderSelectionSource,
)


GOLDEN_FIXED_SNAPSHOT_REPORT = (
    "CineHDR Video Output Diagnostics\n"
    "\n"
    "[Diagnostics Evidence & Session Identity]\n"
    "Evidence status: CONFIRMED\n"
    "Session ID: session-fixed-001\n"
    "\n"
    "[Video Renderer]\n"
    "Configured at process start: legacy\n"
    "Active API: opengl\n"
    "Session status: active\n"
    "Empty field: unknown\n"
    "Multiline note: line1 line2\n"
    "\n"
    "[Output & Color State]\n"
    "HDR Status: Active (Rec.2100 PQ target prepared; compositor submission unverified)\n"
    "Auto gate: allowed\n"
    "Gdk.ColorState: Rec.2100 PQ (published texture)\n"
    "\n"
    "[Video Signal]\n"
    "Video Codec / Format: hevc\n"
    "Color Primaries: bt.2020\n"
    "Transfer Characteristics: pq\n"
    "\n"
    "[Playback Performance & Drops]\n"
    "Sample time (UTC): 2026-11-12T10:00:00+00:00\n"
    "Process ID: 4242\n"
    "VO frame drops: 0\n"
    "Published textures (includes redraws): 120\n"
)


GOLDEN_HDR10_COLLECTED_REPORT = (
    "CineHDR Video Output Diagnostics\n"
    "\n"
    "[Video Renderer]\n"
    "Selected for next launch: legacy\n"
    "Configured at process start: legacy\n"
    "Requested this session: legacy\n"
    "Request source: gsettings\n"
    "Creation fallback allowed: False\n"
    "Active API: opengl\n"
    "Session status: active\n"
    "Preference restart pending: False\n"
    "Startup fallback reason: none\n"
    "Renderer failure reason: none\n"
    "Lifecycle restart reason: none\n"
    "GPU Next capability: Not requested in this session\n"
    "Dolby Vision capability: Unsupported by the active standard renderer\n"
    "Dolby Vision RPU path evidence: opengl-backend\n"
    "Dolby Vision color validation: unavailable\n"
    "Dolby Vision reference A/B: unavailable\n"
    "Render target format: GL_RGBA16F\n"
    "Render target depth: 16\n"
    "OpenGL device: AMD Radeon RX 9060 XT\n"
    "OpenGL vendor: AMD\n"
    "OpenGL renderer: AMD Radeon RX 9060 XT\n"
    "OpenGL version: 4.6\n"
    "GLSL version: 4.60\n"
    "libmpv path: /app/lib/libmpv.so.2\n"
    "mpv version: 0.41.0\n"
    "libplacebo version: v7.351.0\n"
    "FFmpeg version: 7.1\n"
    "mpv configuration: --enable-libmpv-shared\n"
    "Dolby Vision frame metadata: none\n"
    "\n"
    "[Output & Color State]\n"
    "HDR Status: Active (Rec.2100 PQ target prepared · tone-mapped ~1000 → 600 nits; compositor submission unverified)\n"
    "Auto gate: allowed\n"
    "Display HDR Supported: Yes (Wayland + GTK 4.16+) — final output depends on the compositor/monitor HDR mode\n"
    "Compositor Color Management: Yes (wp_color_manager_v1 advertised)\n"
    "Monitor HDR State: Compositor reports HDR on DP-1 (pq, reported color-volume max ~1000 nits; panel luminance not measured; reported target max 1000 nits; reported target MaxCLL 800 nits)\n"
    "Graphics Offload: Enabled (subsurface / direct scanout possible)\n"
    "Gdk.ColorState: Rec.2100 PQ (published texture)\n"
    "FBO Format: GL_RGBA16F (16-bit render target; not the Wayland surface format)\n"
    "\n"
    "[Video Signal]\n"
    "Video Codec / Format: hevc\n"
    "Resolution & Pixel Format: 3840x2160 (yuv420p10le)\n"
    "Hardware Acceleration: vaapi (GPU Acceleration active; configured: auto-safe)\n"
    "Dolby Vision Profile: No (Standard HDR10 / HLG)\n"
    "Dolby Vision RPU Warnings: None (0 warning messages logged)\n"
    "Color Primaries: bt.2020\n"
    "Transfer Characteristics: pq\n"
    "Peak Luminance: 4.93 (~1000 nits)\n"
    "Target Tone Mapping: TRC: pq | Prim: bt.2020 | Peak: 600 (auto)\n"
    "Configured decoder: auto-safe\n"
    "Active decoder: vaapi\n"
    "Hardware decoding device: Unknown (not reported by libmpv)\n"
    "\n"
    "[Playback Performance & Drops]\n"
    "Sample time (UTC): 2026-11-12T10:00:00+00:00\n"
    "Process ID: 12345\n"
    "Filter FPS estimate (estimated-vf-fps): 23.976\n"
    "Container FPS: 23.976\n"
    "Display refresh estimate (Hz): 120.000\n"
    "Measured presentation FPS: unavailable (no presentation timing capture)\n"
    "Counter scope: cumulative; measurement interval/reset identity unknown; not a drop rate\n"
    "Counter baseline: Unknown (not sampled)\n"
    "Counter measurement interval: Unknown (point-in-time sample)\n"
    "Counter observed growth: Unknown (single sample; delta unmeasured)\n"
    "VO frame drops: 1\n"
    "Decoder frame drops: 0\n"
    "Pipeline (FBO) drops: 0\n"
    "FBO allocation failures: 0\n"
    "VO delayed frames: 2\n"
    "Mistimed frames: 0\n"
    "Published textures (includes redraws): 450\n"
    "Presented frames: unavailable (texture publication is not presentation)\n"
    "A/V sync offset: +1.5 ms (drift: -0.002 s)\n"
    "Total A/V sync change: -0.002 s\n"
    "Pipeline status: Active · GL_RGBA16F (16-bit) · Scale 1.00x · max 16384x16384\n"
    "FBO render target (not Wayland surface): GL_RGBA16F (16-bit)\n"
    "Effective scale: 1.00x\n"
)


GOLDEN_KDE_FALLBACK_REPORT = (
    "CineHDR Video Output Diagnostics\n"
    "\n"
    "[Video Renderer]\n"
    "Selected for next launch: legacy\n"
    "Configured at process start: legacy\n"
    "Requested this session: gpu-next\n"
    "Request source: gsettings\n"
    "Creation fallback allowed: True\n"
    "Active API: opengl\n"
    "Session status: startup-fallback\n"
    "Preference restart pending: False\n"
    "Startup fallback reason: opengl-next rejected\n"
    "Renderer failure reason: none\n"
    "Lifecycle restart reason: none\n"
    "GPU Next capability: Unavailable in this session; standard renderer active\n"
    "Dolby Vision capability: Unsupported by the active standard renderer\n"
    "Dolby Vision RPU path evidence: unavailable\n"
    "Dolby Vision color validation: unavailable\n"
    "Dolby Vision reference A/B: unavailable\n"
    "Render target format: GL_RGBA8\n"
    "Render target depth: 8\n"
    "OpenGL device: AMD Radeon Graphics\n"
    "OpenGL vendor: AMD\n"
    "OpenGL renderer: AMD Radeon Graphics\n"
    "OpenGL version: 4.6\n"
    "GLSL version: 4.60\n"
    "libmpv path: /app/lib/libmpv.so.2\n"
    "mpv version: 0.41.0\n"
    "libplacebo version: unknown\n"
    "FFmpeg version: 7.1\n"
    "mpv configuration: --enable-libmpv-shared\n"
    "Dolby Vision frame metadata: detected; RPU render mapping not verified\n"
    "Dolby Vision base-layer compatibility ID: not exposed by mpv\n"
    "\n"
    "[Output & Color State]\n"
    "HDR Status: Active (SDR Tone Mapping enabled)\n"
    "Auto gate: blocked: " + REASON_MISSING_TF_SRGB + "\n"
    "Display HDR Supported: No (Fallback to SDR / 8-bit)\n"
    "Compositor Color Management: Yes (wp_color_manager_v1 advertised)\n"
    "Monitor HDR State: Compositor reports HDR on: HDMI-A-1; current output unknown\n"
    "Graphics Offload: Disabled — output goes through GTK compositing\n"
    "System HDR Limitation: " + REASON_MISSING_TF_SRGB + "\n"
    "Gdk.ColorState: sRGB (published texture)\n"
    "FBO Format: GL_RGBA8 (8-bit render target; not the Wayland surface format)\n"
    "Publication State Gap: Publication suppressed: waiting for committed output state\n"
    "\n"
    "[Video Signal]\n"
    "Video Codec / Format: hevc\n"
    "Resolution & Pixel Format: 1920x1080 (yuv420p10le)\n"
    "Hardware Acceleration: Software / CPU Decoding (active: no; configured: no)\n"
    "Dolby Vision Profile: Profile 8 — base-layer fallback (RPU not processed; compatibility ID unknown) · Level 6\n"
    "Dolby Vision RPU Warnings: 2 warning messages logged (dovi: Multiple Dolby Vision RPUs)\n"
    "Color Primaries: bt.2020\n"
    "Transfer Characteristics: pq\n"
    "Peak Luminance: Standard / SDR (1.00)\n"
    "Target Tone Mapping: TRC: bt.1886 | Prim: bt.709 | Peak: 203 (manual)\n"
    "Configured decoder: no\n"
    "Active decoder: no\n"
    "Hardware decoding device: Unknown (not reported by libmpv)\n"
    "\n"
    "[Playback Performance & Drops]\n"
    "Sample time (UTC): 2026-11-12T10:00:00+00:00\n"
    "Process ID: 12345\n"
    "Filter FPS estimate (estimated-vf-fps): unknown\n"
    "Container FPS: 29.970\n"
    "Display refresh estimate (Hz): unknown\n"
    "Measured presentation FPS: unavailable (no presentation timing capture)\n"
    "Counter scope: cumulative; measurement interval/reset identity unknown; not a drop rate\n"
    "Counter baseline: Unknown (not sampled)\n"
    "Counter measurement interval: Unknown (point-in-time sample)\n"
    "Counter observed growth: Unknown (single sample; delta unmeasured)\n"
    "VO frame drops: unknown\n"
    "Decoder frame drops: unknown\n"
    "Pipeline (FBO) drops: 0\n"
    "FBO allocation failures: 1\n"
    "VO delayed frames: unknown\n"
    "Mistimed frames: unknown\n"
    "Published textures (includes redraws): 12\n"
    "Presented frames: unavailable (texture publication is not presentation)\n"
    "A/V sync offset: Unknown (A/V sync property unavailable)\n"
    "Total A/V sync change: unknown\n"
    "Pipeline status: Warning: 1 FBO allocation failures\n"
    "FBO render target (not Wayland surface): GL_RGBA8 (8-bit)\n"
    "Effective scale: unknown\n"
)


class _DictMpv(dict):
    def get_property(self, name):
        if name not in self:
            raise AttributeError(name)
        return self[name]


class DiagnosticsReportCharacterizationTests(unittest.TestCase):
    """Golden snapshot and facade parity tests locking down C24/R4 behavior."""

    maxDiff = None

    def test_fixed_snapshot_golden_copy_report_byte_exact(self):
        snapshot = DiagnosticSnapshot(
            sample_time_utc="2026-11-12T10:00:00+00:00",
            renderer={
                "Configured at process start": "legacy",
                "Active API": "opengl",
                "Session status": "active",
                "Empty field": "",
                "Multiline note": "line1\nline2\r",
                "filename": "/secret/movie.mkv",
            },
            output={
                "HDR Status": "Active (Rec.2100 PQ target prepared; compositor submission unverified)",
                "Auto gate": "allowed",
                "Gdk.ColorState": "Rec.2100 PQ (published texture)",
                "media_path": "/secret/movie.mkv",
            },
            video={
                "Video Codec / Format": "hevc",
                "Color Primaries": "bt.2020",
                "Transfer Characteristics": "pq",
                "Media Path": "/secret/movie.mkv",
                "file": "/secret/movie.mkv",
            },
            performance={
                "Sample time (UTC)": "2026-11-12T10:00:00+00:00",
                "Process ID": 4242,
                "VO frame drops": 0,
                "Published textures (includes redraws)": 120,
            },
            ui_subtitles={},
            ui_visibilities={},
        )
        renderer, output, video, performance = snapshot
        self.assertIs(renderer, snapshot.renderer)
        self.assertIs(output, snapshot.output)
        self.assertIs(video, snapshot.video)
        self.assertIs(performance, snapshot.performance)

        report = build_video_output_report(
            renderer,
            output,
            video,
            performance=performance,
            session_identity={
                "Evidence status": "CONFIRMED",
                "Session ID": "session-fixed-001",
                "Media Path": "/secret/movie.mkv",
            },
        )
        self.assertEqual(report, GOLDEN_FIXED_SNAPSHOT_REPORT)

        minimal = build_video_output_report({"A": "1"}, {"B": "2"}, {"C": "3"})
        self.assertEqual(
            minimal,
            "CineHDR Video Output Diagnostics\n"
            "\n"
            "[Video Renderer]\n"
            "A: 1\n"
            "\n"
            "[Output & Color State]\n"
            "B: 2\n"
            "\n"
            "[Video Signal]\n"
            "C: 3\n",
        )

    def test_collect_facts_and_format_hdr10_golden_byte_exact(self):
        fake_settings = SimpleNamespace(get_string=lambda _k: "legacy")
        selection = ProcessRenderSelection(
            configured=RenderBackend.LEGACY,
            requested=RenderBackend.LEGACY,
            source=RenderSelectionSource.SETTINGS,
            allow_creation_fallback=False,
        )
        controller = SimpleNamespace(
            is_hdr_active=True,
            is_hdr_content=True,
            hdr_mode="auto",
            output_hint="DP-1",
            state_gap_reason=None,
            is_publication_suppressed=False,
            effective_peak_source="auto",
        )
        gl_area = SimpleNamespace(
            hdr_controller=controller,
            render_backend_selection=selection,
            render_backend_requested=RenderBackend.LEGACY,
            render_backend_source=RenderSelectionSource.SETTINGS,
            render_backend_active="opengl",
            render_backend_fallback_reason=None,
            _render_restart_required_reason=None,
            render_session_status="active",
            render_failure_reason=None,
            render_runtime={
                "gl_renderer": "AMD Radeon RX 9060 XT",
                "gl_vendor": "AMD",
                "gl_version": "4.6",
                "glsl_version": "4.60",
                "libmpv_path": "/app/lib/libmpv.so.2",
                "mpv_version": "0.41.0",
                "libplacebo_version": "v7.351.0",
                "ffmpeg_version": "7.1",
                "mpv_configuration": "--enable-libmpv-shared",
            },
            render_target_format="GL_RGBA16F",
            render_target_depth=16,
            render_color_state="rec2100-pq",
            _cached_scale=1.0,
            _cached_max_width=16384,
            _cached_max_height=16384,
            render_frame_generation=450,
            fbo_pool=SimpleNamespace(size=5, dropped_frames=0, allocation_failures=0),
        )
        mpv_props = _DictMpv(
            {
                "video-params": {
                    "w": 3840,
                    "h": 2160,
                    "pixelformat": "yuv420p10le",
                    "primaries": "bt.2020",
                    "gamma": "pq",
                    "sig-peak": 1000.0 / 203.0,
                    "colormatrix": "bt.2020-ncl",
                },
                "video-format": "hevc",
                "hwdec-current": "vaapi",
                "hwdec": "auto-safe",
                "target-peak": "600",
                "target-trc": "pq",
                "target-prim": "bt.2020",
                "estimated-vf-fps": 23.976,
                "container-fps": 23.976,
                "display-fps": 120.0,
                "frame-drop-count": 1,
                "decoder-frame-drop-count": 0,
                "vo-delayed-frame-count": 2,
                "mistimed-frame-count": 0,
                "avsync": 0.0015,
                "total-avsync-change": -0.002,
            }
        )
        win = SimpleNamespace(
            _video_area=gl_area,
            mpv=mpv_props,
            offload=SimpleNamespace(get_enabled=lambda: "GtkGraphicsOffloadEnabled.ENABLED"),
            dovi_rpu_warning_count=0,
            dovi_rpu_warning_text=None,
        )
        dp1_info = SimpleNamespace(
            hdr=True,
            max_lum=1000.0,
            target_max_lum=1000.0,
            target_max_cll=800.0,
            tf_name="pq",
        )

        with patch("src.preferences.settings", fake_settings), \
             patch("src.hdr_detection.check_hdr_support", return_value=True), \
             patch("src.hdr_detection.get_compositor_cm_support", return_value=True), \
             patch("src.hdr_detection.hdr_output_allowed", return_value=(True, "allowed")), \
             patch("src.hdr_detection.get_dovi_info", return_value=None), \
             patch("src.wayland_output_hdr.get_output_hdr_states", return_value={"DP-1": dp1_info}), \
             patch("src.render_backend.dolby_vision_render_capability", return_value=SimpleNamespace(rpu_path_available=False, reason="opengl-backend")), \
             patch("os.getpid", return_value=12345):
            snap = collect_video_output_facts(
                win, sample_time_utc="2026-11-12T10:00:00+00:00"
            )
            report = generate_video_output_report(
                win, sample_time_utc="2026-11-12T10:00:00+00:00"
            )

        self.assertEqual(report, GOLDEN_HDR10_COLLECTED_REPORT)
        self.assertEqual(snap.ui_visibilities, {
            "unsupported_reason_row": False,
            "dovi_profile_row": True,
            "dovi_rpu_row": True,
        })
        self.assertEqual(
            snap.ui_subtitles["renderer_target_row"],
            "RGBA16F · 16-bit float",
        )
        self.assertEqual(
            snap.ui_subtitles["perf_fps_row"],
            "23.98 fps (filter estimate, not presentation) (container: 23.98 fps · display: 120.0 Hz)",
        )

    def test_collect_facts_and_format_kde_fallback_dovi_p8_byte_exact(self):
        fake_settings = SimpleNamespace(get_string=lambda _k: "legacy")
        selection = ProcessRenderSelection(
            configured=RenderBackend.LEGACY,
            requested=RenderBackend.GPU_NEXT,
            source=RenderSelectionSource.SETTINGS,
            allow_creation_fallback=True,
        )
        controller = SimpleNamespace(
            is_hdr_active=False,
            is_hdr_content=True,
            hdr_mode="auto",
            output_hint="DP-2",
            state_gap_reason="waiting for committed output state",
            is_publication_suppressed=True,
            effective_peak_source="manual",
        )
        gl_area = SimpleNamespace(
            hdr_controller=controller,
            render_backend_selection=selection,
            render_backend_requested=RenderBackend.GPU_NEXT,
            render_backend_source=RenderSelectionSource.SETTINGS,
            render_backend_active="opengl",
            render_backend_fallback_reason="opengl-next rejected",
            _render_restart_required_reason=None,
            render_session_status="startup-fallback",
            render_failure_reason=None,
            render_runtime={
                "gl_renderer": "AMD Radeon Graphics",
                "gl_vendor": "AMD",
                "gl_version": "4.6",
                "glsl_version": "4.60",
                "libmpv_path": "/app/lib/libmpv.so.2",
                "mpv_version": "0.41.0",
                "libplacebo_version": "unknown",
                "ffmpeg_version": "7.1",
                "mpv_configuration": "--enable-libmpv-shared",
            },
            render_target_format="GL_RGBA8",
            render_target_depth=8,
            render_color_state="srgb",
            render_frame_generation=12,
            fbo_pool=SimpleNamespace(size=5, dropped_frames=0, allocation_failures=1),
        )
        mpv_props = _DictMpv(
            {
                "video-params": {
                    "w": 1920,
                    "h": 1080,
                    "pixelformat": "yuv420p10le",
                    "primaries": "bt.2020",
                    "gamma": "pq",
                    "sig-peak": 1.0,
                    "colormatrix": "dolbyvision",
                },
                "video-format": "hevc",
                "hwdec-current": "no",
                "hwdec": "no",
                "target-peak": "203",
                "target-trc": "bt.1886",
                "target-prim": "bt.709",
                "container-fps": 29.97,
            }
        )
        win = SimpleNamespace(
            gl_area=gl_area,
            player=SimpleNamespace(mpv=mpv_props),
            offload=SimpleNamespace(get_enabled=lambda: "GtkGraphicsOffloadEnabled.DISABLED"),
            dovi_rpu_warning_count=2,
            dovi_rpu_warning_text="dovi: Multiple Dolby Vision RPUs",
        )
        reason_missing_srgb = REASON_MISSING_TF_SRGB

        with patch("src.preferences.settings", fake_settings), \
             patch("src.hdr_detection.check_hdr_support", return_value=False), \
             patch("src.hdr_detection.get_hdr_unsupported_reason", return_value=reason_missing_srgb), \
             patch("src.hdr_detection.get_compositor_cm_support", return_value=True), \
             patch("src.hdr_detection.hdr_output_allowed", return_value=(False, reason_missing_srgb)), \
             patch("src.hdr_detection.get_dovi_info", return_value={"profile": 8, "level": 6, "unsupported": False}), \
             patch("src.wayland_output_hdr.get_output_hdr_states", return_value={"HDMI-A-1": SimpleNamespace(hdr=True)}), \
             patch("src.render_backend.dolby_vision_render_capability", return_value=SimpleNamespace(rpu_path_available=False, reason="unavailable")), \
             patch("os.getpid", return_value=12345):
            snap = collect_video_output_facts(
                win, sample_time_utc="2026-11-12T10:00:00+00:00"
            )
            report = build_video_output_report(
                snap.renderer, snap.output, snap.video, performance=snap.performance
            )

        self.assertEqual(report, GOLDEN_KDE_FALLBACK_REPORT)
        self.assertEqual(snap.ui_visibilities, {
            "unsupported_reason_row": True,
            "dovi_profile_row": True,
            "dovi_rpu_row": True,
        })
        self.assertEqual(snap.ui_subtitles["perf_fps_row"], "29.97 fps (container)")
        self.assertEqual(snap.ui_subtitles["perf_pipeline_row"], "Warning: 1 FBO allocation failures")

    def test_facade_compatibility_and_patch_points(self):
        fake_snap = DiagnosticSnapshot(
            sample_time_utc="2026-11-12T10:00:00+00:00",
            renderer={"Active API": "opengl"},
            output={"Auto gate": "allowed"},
            video={"Transfer Characteristics": "pq"},
            performance={"VO frame drops": 0},
            ui_subtitles={},
            ui_visibilities={},
        )
        with patch("src.diagnostics_report.collect_video_output_facts", return_value=fake_snap) as mock_collect, \
             patch("src.diagnostics_report.build_video_output_report", return_value="PATCHED") as mock_build:
            res = generate_video_output_report(SimpleNamespace(), sample_time_utc="2026-11-12T10:00:00+00:00")

        self.assertEqual(res, "PATCHED")
        mock_collect.assert_called_once()
        mock_build.assert_called_once_with(
            fake_snap.renderer,
            fake_snap.output,
            fake_snap.video,
            performance=fake_snap.performance,
            session_identity=None,
        )

        # Verify all expected exports remain present on src.diagnostics_report
        for name, obj in (
            ("DIAGNOSTICS_EXPORT_PATH_ENV", DIAGNOSTICS_EXPORT_PATH_ENV),
            ("DIAGNOSTICS_EXPORT_DIR_ENV", DIAGNOSTICS_EXPORT_DIR_ENV),
            ("EXPECTED_FLATPAK_INSTANCE_ENV", EXPECTED_FLATPAK_INSTANCE_ENV),
            ("EXPECTED_APP_COMMIT_ENV", EXPECTED_APP_COMMIT_ENV),
            ("EXPECTED_RUNTIME_COMMIT_ENV", EXPECTED_RUNTIME_COMMIT_ENV),
            ("DIAGNOSTICS_SESSION_ID_ENV", DIAGNOSTICS_SESSION_ID_ENV),
            ("_MEDIA_PATH_FIELDS", _MEDIA_PATH_FIELDS),
            ("DiagnosticSnapshot", DiagnosticSnapshot),
            ("_single_line", _single_line),
            ("_positive_float", _positive_float),
            ("_source_peak_nits", _source_peak_nits),
            ("get_mpv_prop", get_mpv_prop),
            ("format_dovi_rpu_warnings", format_dovi_rpu_warnings),
            ("get_dovi_rpu_warning_state", get_dovi_rpu_warning_state),
            ("_hdr_status_text", _hdr_status_text),
            ("read_flatpak_info", read_flatpak_info),
            ("collect_performance_facts", collect_performance_facts),
            ("collect_video_output_facts", collect_video_output_facts),
            ("build_video_output_report", build_video_output_report),
            ("generate_video_output_report", generate_video_output_report),
        ):
            self.assertIs(getattr(diagnostics_report, name), obj)

        with tempfile.TemporaryDirectory() as tmp_dir:
            p = Path(tmp_dir) / "flatpak-info"
            p.write_text("[Instance]\ninstance-id=abc\n[Application]\nname=io.github.rusmikev.CineHDR\n", encoding="utf-8")
            self.assertEqual(read_flatpak_info(p), {"instance_id": "abc", "app_name": "io.github.rusmikev.CineHDR"})


if __name__ == "__main__":
    unittest.main()
