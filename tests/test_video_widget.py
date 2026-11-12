"""Characterization tests for src/video_widget.py before C24 decomposition."""

import atexit
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_TEMP_SCHEMA_DIR = tempfile.mkdtemp(prefix="cinehdr-vw-schema-")
atexit.register(shutil.rmtree, _TEMP_SCHEMA_DIR, ignore_errors=True)
shutil.copy2(
    _REPO_ROOT / "data" / "io.github.rusmikev.CineHDR.gschema.xml",
    Path(_TEMP_SCHEMA_DIR) / "io.github.rusmikev.CineHDR.gschema.xml",
)
subprocess.run(["glib-compile-schemas", _TEMP_SCHEMA_DIR], check=True)
os.environ["GSETTINGS_SCHEMA_DIR"] = _TEMP_SCHEMA_DIR
os.environ["GSETTINGS_BACKEND"] = "memory"

import gi  # noqa: E402

gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gdk, Gio, GLib  # noqa: E402

if _gresource_path := os.environ.get("CINEHDR_GRESOURCE"):
    if os.path.exists(_gresource_path):
        Gio.resources_register(Gio.Resource.load(_gresource_path))

from src.diagnostics_report import generate_video_output_report  # noqa: E402
from src.gl_bindings import GL_FRAMEBUFFER, GL_TIMEOUT_EXPIRED  # noqa: E402
from src.hdr_controller import CommittedOutputState  # noqa: E402
from src.render_backend import ProcessRenderSelection, RenderBackend, RenderSelectionSource  # noqa: E402
from src.video_widget import MpvVideoWidget  # noqa: E402


def _make_render_widget(*, committed=None, is_hdr_active=False):
    widget = MpvVideoWidget.__new__(MpvVideoWidget)
    widget.mpv = MagicMock()
    widget.mpv.__getitem__ = MagicMock(side_effect=KeyError)
    widget.mpv_ctx = MagicMock()
    widget.mpv_ctx.update.return_value = 1
    widget.gl_area = MagicMock()
    widget.fbo_pool = MagicMock()
    widget.queue_draw = MagicMock()
    widget.get_width = MagicMock(return_value=640)
    widget.get_height = MagicMock(return_value=360)
    widget._cached_scale = 1.0
    widget._cached_max_width = 0
    widget._cached_max_height = 0
    widget._shutting_down = False
    widget._render_failed = False
    widget._render_terminal_failure = False
    widget._render_pending = True
    widget._force_redraw_pending = False
    widget._first_frame_logged = True
    widget._first_media_frame_logged = True
    widget._first_media_frame_exported = True
    widget._exported_media_generation = None
    widget._committed_render_epoch = None
    widget._fallback_slot = None
    widget._window = None
    widget._cached_hdr_support = bool(is_hdr_active)
    widget._cached_hdr_support_valid = True
    widget.current_texture = None
    widget.render_backend_selection = ProcessRenderSelection(
        configured=RenderBackend.LEGACY,
        requested=RenderBackend.LEGACY,
        source=RenderSelectionSource.SETTINGS,
        allow_creation_fallback=False,
    )
    widget.render_backend_configured = RenderBackend.LEGACY
    widget.render_backend_requested = RenderBackend.LEGACY
    widget.render_backend_source = RenderSelectionSource.SETTINGS
    widget.render_backend_active = "opengl"
    widget.render_context_generation = 1
    widget.render_backend_fallback_reason = None
    widget.render_session_status = "active"
    widget.render_failure_reason = None
    widget._render_restart_required_reason = None
    widget.render_runtime = {
        "mpv_version": "mpv 0.41.0",
        "libplacebo_version": "7.360.0",
        "gl_renderer": "AMD Radeon Graphics",
    }
    widget.render_target_format = None
    widget.render_target_depth = None
    widget.render_color_state = None
    widget.render_frame_generation = 0

    ctrl = MagicMock()
    ctrl._shutting_down = False
    ctrl.is_publication_pending = False
    ctrl.is_publication_suppressed = False
    ctrl.is_hdr_active = is_hdr_active
    ctrl.hdr_mode = "auto"
    ctrl.effective_peak_source = "auto"
    ctrl.output_hint = "DP-1"
    ctrl.last_video_params = {"colormatrix": "bt.2020-ncl", "gamma": "pq", "primaries": "bt.2020"}
    ctrl.committed_output_state = committed
    widget.hdr_controller = ctrl
    return widget


def _make_slot(fbo_id=7, texture_id=19):
    slot = MagicMock()
    slot.resource.fbo_id.value = fbo_id
    slot.resource.texture_id.value = texture_id
    slot.fence = None
    return slot


class TestVideoWidgetRenderAndPublication(unittest.TestCase):
    """Snapshot, clear_frame, frame rendering, fence, fallback, and Copy Report."""

    def test_do_snapshot_and_clear_frame(self):
        widget = _make_render_widget()
        snapshot = MagicMock()
        widget.do_snapshot(snapshot)
        snapshot.append_texture.assert_not_called()

        tex = MagicMock()
        widget.current_texture = tex
        widget._shutting_down = True
        widget.do_snapshot(snapshot)
        snapshot.append_texture.assert_not_called()

        widget._shutting_down = False
        widget.do_snapshot(snapshot)
        snapshot.append_texture.assert_called_once()
        self.assertIs(snapshot.append_texture.call_args[0][0], tex)

        fallback_slot = _make_slot()
        widget._fallback_slot = fallback_slot
        widget.clear_frame()
        self.assertIsNone(widget.current_texture)
        self.assertIsNone(widget._fallback_slot)
        widget.fbo_pool.release_buffer.assert_called_once_with(fallback_slot)
        widget.queue_draw.assert_called_once_with()

    def test_render_pending_frame_guards(self):
        widget = _make_render_widget()
        widget._shutting_down = True
        self.assertEqual(widget._render_pending_frame(), GLib.SOURCE_REMOVE)
        widget.mpv_ctx.update.assert_not_called()

        widget = _make_render_widget()
        widget.get_width.return_value = 0
        self.assertEqual(widget._render_pending_frame(), GLib.SOURCE_REMOVE)
        widget.fbo_pool.acquire.assert_not_called()

        widget = _make_render_widget()
        widget.hdr_controller.is_publication_pending = True
        self.assertEqual(widget._render_pending_frame(), GLib.SOURCE_REMOVE)
        widget.fbo_pool.acquire.assert_not_called()

    def test_hdr_publication_updates_widget_and_copy_report_baseline(self):
        committed = CommittedOutputState(
            epoch=3,
            target_trc="pq",
            target_prim="bt.2020",
            target_peak="auto",
            fbo_format="GL_RGBA16F",
            gdk_color_state="rec2100-pq",
            is_float=True,
            is_hdr=True,
        )
        widget = _make_render_widget(committed=committed, is_hdr_active=True)
        slot = _make_slot(fbo_id=11, texture_id=22)
        widget.fbo_pool.acquire.return_value = slot
        mock_builder = MagicMock()
        mock_tex = MagicMock()
        mock_builder.build.return_value = mock_tex

        with patch("src.video_widget.should_render_frame", return_value=True), \
             patch("src.video_widget.glBindFramebuffer") as mock_bind, \
             patch("src.video_widget.glFenceSync", return_value=0xBEEF), \
             patch("src.video_widget.glFlush") as mock_flush, \
             patch("src.video_widget.Gdk.GLTextureBuilder", return_value=mock_builder):
            res = widget._render_pending_frame()

        self.assertEqual(res, GLib.SOURCE_REMOVE)
        self.assertEqual(mock_bind.call_args_list[0][0], (GL_FRAMEBUFFER, 11))
        self.assertEqual(mock_bind.call_args_list[-1][0], (GL_FRAMEBUFFER, 0))
        mock_flush.assert_called_once_with()
        mock_builder.set_format.assert_called_once_with(Gdk.MemoryFormat.R16G16B16A16_FLOAT)
        mock_builder.set_sync.assert_called_once_with(0xBEEF)
        destroy_cb = mock_builder.build.call_args.kwargs["destroy"]
        self.assertIs(widget.current_texture, mock_tex)
        self.assertEqual(widget.render_target_format, "GL_RGBA16F")
        self.assertEqual(widget.render_target_depth, 16)
        self.assertEqual(widget.render_color_state, "rec2100-pq")
        self.assertEqual(widget.render_frame_generation, 1)
        self.assertEqual(widget._committed_render_epoch, 3)

        destroy_cb(None)
        widget.fbo_pool.release_buffer.assert_called_once_with(slot)

        win = SimpleNamespace(gl_area=widget, _video_area=widget)
        with patch("src.hdr_detection.check_hdr_support", return_value=True), \
             patch("src.hdr_detection.get_compositor_cm_support", return_value=True), \
             patch("src.hdr_detection.hdr_output_allowed", return_value=(True, "allowed")), \
             patch("src.wayland_output_hdr.get_output_hdr_states", return_value=None), \
             patch("src.diagnostics_facts.read_flatpak_info", return_value={}):
            report = generate_video_output_report(win)

        self.assertIn("Active API: opengl", report)
        self.assertIn("Render target format: GL_RGBA16F", report)
        self.assertIn("Render target depth: 16", report)
        self.assertIn("Gdk.ColorState: Rec.2100 PQ (published texture)", report)
        self.assertIn("Published textures (includes redraws): 1", report)

    def test_superseded_render_deletes_fence_and_releases_slot(self):
        committed = CommittedOutputState(
            epoch=2,
            target_trc="auto",
            target_prim="auto",
            target_peak="auto",
            fbo_format="GL_RGBA8",
            gdk_color_state="srgb",
            is_float=False,
            is_hdr=False,
        )
        widget = _make_render_widget(committed=committed)
        slot = _make_slot()
        widget.fbo_pool.acquire.return_value = slot

        def _supersede(*_a, **_kw):
            widget.hdr_controller.committed_output_state = CommittedOutputState(
                epoch=3,
                target_trc="pq",
                target_prim="bt.2020",
                target_peak="auto",
                fbo_format="GL_RGBA16F",
                gdk_color_state="rec2100-pq",
                is_float=True,
                is_hdr=True,
            )

        widget.mpv_ctx.render.side_effect = _supersede
        with patch("src.video_widget.should_render_frame", return_value=True), \
             patch("src.video_widget.glBindFramebuffer"), \
             patch("src.video_widget.glFenceSync", return_value=0xCAFE), \
             patch("src.video_widget.glFlush"), \
             patch("src.video_widget.glDeleteSync") as mock_del:
            widget._render_pending_frame()

        mock_del.assert_called_once_with(0xCAFE)
        self.assertIsNone(slot.fence)
        widget.fbo_pool.release_buffer.assert_called_once_with(slot)
        self.assertIsNone(widget.current_texture)
        self.assertEqual(widget.render_frame_generation, 0)

    def test_client_wait_sync_timeout_drops_frame(self):
        widget = _make_render_widget()
        slot = _make_slot()
        widget.fbo_pool.acquire.return_value = slot
        mock_builder = SimpleNamespace(
            set_context=MagicMock(),
            set_id=MagicMock(),
            set_width=MagicMock(),
            set_height=MagicMock(),
            set_format=MagicMock(),
            set_color_state=MagicMock(),
            build=MagicMock(),
        )
        with patch("src.video_widget.should_render_frame", return_value=True), \
             patch("src.video_widget.glBindFramebuffer"), \
             patch("src.video_widget.glFenceSync", return_value=0xF00D), \
             patch("src.video_widget.glFlush"), \
             patch("src.video_widget.glClientWaitSync", return_value=GL_TIMEOUT_EXPIRED), \
             patch("src.video_widget.Gdk.GLTextureBuilder", return_value=mock_builder):
            widget._render_pending_frame()

        mock_builder.build.assert_not_called()
        widget.fbo_pool.release_buffer.assert_called_once_with(slot)

    def test_fallback_build_releases_previous_slot_only_after_new_publish(self):
        widget = _make_render_widget()
        prev_slot = _make_slot(fbo_id=1, texture_id=2)
        new_slot = _make_slot(fbo_id=3, texture_id=4)
        widget._fallback_slot = prev_slot
        widget.fbo_pool.acquire.return_value = new_slot
        tex = MagicMock()
        mock_builder = MagicMock()
        mock_builder.build.side_effect = [TypeError("no destroy"), tex]

        with patch("src.video_widget.should_render_frame", return_value=True), \
             patch("src.video_widget.glBindFramebuffer"), \
             patch("src.video_widget.glFenceSync", return_value=None), \
             patch("src.video_widget.glFlush"), \
             patch("src.video_widget.Gdk.GLTextureBuilder", return_value=mock_builder):
            widget._render_pending_frame()

        self.assertIs(widget._fallback_slot, new_slot)
        self.assertIs(widget.current_texture, tex)
        widget.fbo_pool.release_buffer.assert_called_once_with(prev_slot)

    def test_render_exception_restores_fbo_latches_and_notifies_toast(self):
        widget = _make_render_widget()
        slot = _make_slot()
        widget.fbo_pool.acquire.return_value = slot
        widget.mpv_ctx.render.side_effect = RuntimeError("gl boom")
        widget._window = MagicMock()

        with patch("src.video_widget.should_render_frame", return_value=True), \
             patch("src.video_widget.glBindFramebuffer") as mock_bind, \
             patch("src.video_widget.idle_add_once") as mock_idle:
            widget._render_pending_frame()

        self.assertEqual(mock_bind.call_args_list[-1][0], (GL_FRAMEBUFFER, 0))
        self.assertTrue(widget._render_terminal_failure)
        self.assertEqual(widget.render_session_status, "runtime-failure")
        widget.fbo_pool.release_buffer.assert_called_once_with(slot)
        mock_idle.assert_called_once()


if __name__ == "__main__":
    unittest.main()
