# video_widget.py
#
# Copyright 2026 rusmikev / Diego Povliuk
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Custom GTK 4 video rendering widget.

Integrates libmpv OpenGL rendering with GTK's GdkGLTextureBuilder and GLArea.
Delegates HDR state management and tone mapping rules to HdrController, and
OpenGL resource lifecycle to GLFramebufferResource.
"""

import logging
import sys
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gtk, Gdk, GLib, Graphene
import mpv
from typing import Any, Optional

from .gl_bindings import (
    GL_FRAMEBUFFER,
    GL_RGBA16F,
    GL_RGBA8,
    glFenceSync,  # noqa: F401
    glFlush,  # noqa: F401
    glClientWaitSync,  # noqa: F401
    glDeleteSync,  # noqa: F401
    glBindFramebuffer,
    get_gl_string,
    get_proc_address,
)

from .gl_renderer import GLFramebufferPool
from .hdr_controller import HdrController
from .hdr_detection import check_hdr_support
from .render_backend import (
    ProcessRenderSelection,
    RenderBackend,
    RenderSelectionSource,
    create_render_context,
    install_python_mpv_depth_compat,
    media_frame_evidence,
    render_target_spec,
    render_runtime_diagnostics,
    should_render_frame,
    target_configuration_requires_redraw,
)
from .utils import get_display_param, idle_add_once, mpv_prop
from .video_widget_lifecycle import (
    capture_video_selection_for_context_restore,
    connect_realize_signals,
    handle_first_frame_confirmed,
    handle_hdr_controller_change,
    push_output_hint,
    record_realized_runtime,
    reset_realized_frame_flags,
    reset_render_session_state,
    update_cached_hdr_support,
    update_cached_scale_and_limits,
)
from .video_widget_render import (
    attach_or_wait_fence,
    dispatch_first_media_frame,
    fence_and_check_superseded,
    handle_render_error,
    is_publication_blocked,
    log_first_frame,
    notify_fbo_pixel_oracle,
    sample_fbo_pixel_oracle,
)

_MODULE = sys.modules[__name__]

# CPU-side wait budget for the GTK < 4.16 fallback path (no
# GdkGLTextureBuilder.set_sync). glClientWaitSync takes a real timeout in
# nanoseconds — GL_TIMEOUT_IGNORED is only valid for glWaitSync and would
# block the UI thread indefinitely on a stalled driver (F5).
GL_CLIENT_WAIT_TIMEOUT_NS = 100_000_000  # 100 ms


class MpvVideoWidget(Gtk.Widget):
    """Custom GTK4 video widget integrating libmpv with GdkGLTextureBuilder and HDR."""

    __gtype_name__ = "MpvVideoWidget"

    _fbo_pixel_oracle_cb: Optional[Any] = None
    _fbo_pixel_oracle_sampler: Optional[Any] = None
    _fbo_pixel_oracle_error_cb: Optional[Any] = None

    def __init__(
        self,
        mpv_player: mpv.MPV,
        render_backend_selection: Optional[ProcessRenderSelection] = None,
    ):
        super().__init__()
        self.mpv = mpv_player

        # GLArea to manage context initialization and life-cycle
        self.gl_area = Gtk.GLArea()
        self.gl_area.set_parent(self)

        self.gl_area.connect("realize", self._on_realize)
        self.gl_area.connect("unrealize", self._on_unrealize)

        self.fbo_pool = GLFramebufferPool(size=5)
        self.mpv_ctx: Optional[mpv.MpvRenderContext] = None
        self.render_backend_selection = render_backend_selection or ProcessRenderSelection(
            configured=RenderBackend.LEGACY,
            requested=RenderBackend.LEGACY,
            source=RenderSelectionSource.SETTINGS,
            allow_creation_fallback=False,
        )
        self.render_backend_configured = self.render_backend_selection.configured
        self.render_backend_requested = self.render_backend_selection.requested
        self.render_backend_source = self.render_backend_selection.source
        self.render_backend_active: Optional[str] = None
        self.render_context_generation = 0
        self.render_backend_fallback_reason: Optional[str] = None
        self.render_session_status = "not-initialized"
        self.render_failure_reason: Optional[str] = None
        self.render_runtime: dict[str, str] = {}
        self.render_target_format: Optional[str] = None
        self.render_target_depth: Optional[int] = None
        self.render_color_state: Optional[str] = None
        self.render_frame_generation = 0
        self.current_texture: Optional[Gdk.Texture] = None

        self._shutting_down = False
        self._render_pending = False
        self._force_redraw_pending = False
        self._last_target_configuration = (0, 0, 0)
        self._render_failed = False
        self._render_terminal_failure = False
        self._first_frame_logged = False
        self._first_media_frame_logged = False
        self._first_media_frame_exported = False
        self._exported_media_generation: Optional[tuple[int, int]] = None
        self._committed_render_epoch: Optional[int] = None
        self._fbo_pixel_oracle_cb: Optional[Any] = None
        self._fbo_pixel_oracle_sampler: Optional[Any] = None
        self._fbo_pixel_oracle_error_cb: Optional[Any] = None
        self._cached_hdr_support = False
        self._cached_hdr_support_valid = False
        self._monitor_signal_id: Optional[int] = None
        self._connected_monitors: Optional[Any] = None
        self._surface_signal_ids: list[int] = []
        self._connected_surface: Optional[Any] = None
        self._fallback_slot: Optional[Any] = None
        self._render_restart_required_reason: Optional[str] = None
        self._hdr_controller_disconnected = False
        self._video_selection_for_context_restore: Optional[str] = None
        self._cached_scale: float = 1.0
        self._cached_max_width: int = 0
        self._cached_max_height: int = 0

        # Delegate HDR state and mpv property observers to HdrController
        self.hdr_controller = HdrController(
            mpv_player,
            on_change_cb=lambda: idle_add_once(self._on_hdr_controller_change)
        )
        self._window = None
        try:
            self.connect("notify::scale-factor", lambda *_: self._update_cached_scale_and_limits())
        except Exception:
            pass

    def setup_window_integration(self, window):
        """Clean integration hook for window-level HDR UI (e.g. hdr_menu_btn)."""
        self._window = window

    def realize_render_context(self) -> None:
        """Initialize the GLArea before opening media in an unmapped window."""
        # Widget.realize() realizes ancestors, but not children.
        if not self.gl_area.get_realized():
            self.gl_area.realize()

    def _update_cached_hdr_support(self, *args):
        update_cached_hdr_support(self, check_hdr_support)

    def _update_cached_scale_and_limits(self):
        update_cached_scale_and_limits(self)

    def _on_hdr_controller_change(self):
        """Consume a completed controller probe without starting another one."""
        handle_hdr_controller_change(self, check_hdr_support)

    def _push_output_hint(self):
        """Tell HdrController which monitor the widget currently sits on."""
        push_output_hint(self)

    @property
    def is_hdr_supported(self) -> bool:
        if not self._cached_hdr_support_valid:
            self._update_cached_hdr_support()
        return self._cached_hdr_support

    # Public delegator properties for HdrController
    @property
    def hdr_mode(self) -> str:
        return self.hdr_controller.hdr_mode

    @hdr_mode.setter
    def hdr_mode(self, value: str):
        self.hdr_controller.hdr_mode = value

    @property
    def hdr_enabled(self) -> bool:
        return self.hdr_controller.hdr_enabled

    @hdr_enabled.setter
    def hdr_enabled(self, value: bool):
        self.hdr_controller.hdr_enabled = value

    @property
    def hdr_target_peak(self) -> Any:
        return self.hdr_controller.hdr_target_peak

    @hdr_target_peak.setter
    def hdr_target_peak(self, value: Any):
        self.hdr_controller.hdr_target_peak = value

    def apply_hdr_settings(self):
        self.hdr_controller.apply_hdr_settings()

    def _latch_render_restart(
        self, reason: str, *, status: str = "lifecycle-failure"
    ):
        """Stop this renderer instance after an unsafe lifecycle condition."""
        self._shutting_down = True
        self._render_failed = True
        self._render_terminal_failure = True
        self.render_session_status = status
        self.render_failure_reason = reason
        self._render_restart_required_reason = reason
        if self.mpv_ctx:
            try:
                self.mpv_ctx.update_cb = None
            except Exception:
                logging.exception("Failed to disable the stale libmpv callback")
        logging.error("Video renderer stopped; restart CineHDR: %s", reason)

    def _disconnect_realize_handlers(self):
        """Disconnect monitor/surface handlers owned by the last realization."""
        if self._connected_monitors and self._monitor_signal_id:
            try:
                self._connected_monitors.disconnect(self._monitor_signal_id)
            except Exception:
                logging.exception("Failed to disconnect monitor change handler")
        self._monitor_signal_id = None
        self._connected_monitors = None

        if self._connected_surface:
            for signal_id in self._surface_signal_ids:
                try:
                    self._connected_surface.disconnect(signal_id)
                except Exception:
                    logging.exception("Failed to disconnect surface monitor handler")
        self._surface_signal_ids = []
        self._connected_surface = None

    def _capture_video_selection_for_context_restore(self):
        """Remember the active video selection before libmpv tears its VO down."""
        capture_video_selection_for_context_restore(self, mpv_prop)

    def _restore_video_selection_after_context_recreation(self) -> bool:
        """Reattach the video chain removed by mpv_render_context_free()."""
        selection = self._video_selection_for_context_restore
        self._video_selection_for_context_restore = None
        if selection is None:
            return True
        try:
            # mpv_render_context_free() intentionally tears down the VO and
            # deselects its video track. Force a real option transition so the
            # new Render API context receives a newly initialized video chain.
            self.mpv.command("set", "vid", "no")
            self.mpv.command("set", "vid", selection)
        except Exception as error:
            self._latch_render_restart(
                f"video track restoration after GL context recreation failed: "
                f"{error}"
            )
            return False
        logging.info(
            "Restored video selection after GL context recreation: %s",
            selection,
        )
        return True

    def _on_realize(self, area: Gtk.GLArea):
        # A context surviving unrealize has uncertain GL/hwdec ownership. Do
        # not free or replace it from a newly-created GLArea context.
        self._disconnect_realize_handlers()
        if self._render_terminal_failure:
            logging.error(
                "Video renderer remains stopped until CineHDR exits: %s",
                self.render_failure_reason or "unknown renderer failure",
            )
            return
        if self.mpv_ctx:
            self._latch_render_restart(
                "a stale libmpv render context survived GLArea unrealize"
            )
            return

        # Clear facts owned by the previous context before touching a new GL
        # context. If make_current itself fails, diagnostics must not continue
        # to describe the previous renderer or render target as active.
        reset_render_session_state(self)

        area.make_current()
        area_error = area.get_error()
        if area_error is not None:
            self._latch_render_restart(
                f"Gtk.GLArea could not make its context current: {area_error}"
            )
            return

        reset_realized_frame_flags(self)
        self._update_cached_hdr_support()
        connect_realize_signals(self)

        proc_address_fn = mpv.MpvGlGetProcAddressFn(
            lambda _inst, name: get_proc_address(name)
        )
        display_param = get_display_param()
        if install_python_mpv_depth_compat(mpv):
            logging.info("Enabled python-mpv depth render-parameter compatibility")

        def create_mpv_context(api_type: str) -> mpv.MpvRenderContext:
            return mpv.MpvRenderContext(
                self.mpv,
                api_type,
                opengl_init_params={
                    "get_proc_address": proc_address_fn,
                },
                **display_param,
            )

        try:
            self.mpv_ctx, backend_state = create_render_context(
                self.render_backend_requested,
                create_mpv_context,
                allow_gpu_next_fallback=(
                    self.render_backend_selection.allow_creation_fallback
                ),
            )
        except Exception as error:
            self._latch_render_restart(
                f"render context creation failed: {error}",
                status="initialization-failure",
            )
            raise
        self.render_context_generation += 1
        self.render_backend_active = backend_state.active
        record_realized_runtime(
            self,
            mpv,
            backend_state,
            render_runtime_diagnostics_fn=render_runtime_diagnostics,
            get_gl_string_fn=get_gl_string,
        )

        def on_mpv_update():
            self._schedule_render()

        self.mpv_ctx.update_cb = on_mpv_update
        self._restore_video_selection_after_context_recreation()

    def _schedule_render(self, *, force_redraw: bool = False):
        """Coalesce mpv frame updates and GTK target-size redraws."""
        if force_redraw:
            self._force_redraw_pending = True
        if (
            self._shutting_down
            or self._render_pending
            or not self.mpv_ctx
        ):
            return
        self._render_pending = True
        GLib.idle_add(
            self._render_pending_frame,
            priority=GLib.PRIORITY_HIGH_IDLE,
        )

    def do_measure(self, orientation: int, for_size: int) -> tuple[int, int, int, int]:
        if getattr(self, "gl_area", None):
            return self.gl_area.measure(orientation, for_size)
        return (0, 0, -1, -1)

    def do_size_allocate(self, width: int, height: int, baseline: int):
        """Allocate children and redraw a paused frame for the new target."""
        if hasattr(self, "_update_cached_scale_and_limits"):
            self._update_cached_scale_and_limits()
        previous = getattr(self, "_last_target_configuration", (0, 0, 0))
        # Allocate our dummy child context holder
        if getattr(self, "gl_area", None):
            alloc = Gdk.Rectangle()
            alloc.width = width
            alloc.height = height
            self.gl_area.size_allocate(alloc, baseline)

        current = (int(width), int(height), int(self.props.scale_factor))
        self._last_target_configuration = current
        if target_configuration_requires_redraw(
            previous,
            current,
            has_texture=getattr(self, "current_texture", None) is not None,
        ):
            self._schedule_render(force_redraw=True)

    def _collect_first_media_frame(self):
        window = getattr(self, "_window", None)
        raw_ctx_gen = getattr(self, "render_context_generation", 0)
        ctx_gen = raw_ctx_gen if isinstance(raw_ctx_gen, int) else 0
        raw_load_gen = getattr(window, "_media_load_generation", 0) if window else 0
        load_gen = raw_load_gen if isinstance(raw_load_gen, int) else 0
        exporter = getattr(window, "_diagnostics_exporter", None) if window else None
        export_completed = getattr(exporter, "export_completed", False) is True if exporter else False
        shutting_down = getattr(window, "_shutting_down", False) is True if window else False

        if not export_completed and not shutting_down:
            prev_gen = getattr(self, "_exported_media_generation", None)
            if prev_gen is None or prev_gen[0] != ctx_gen or prev_gen[1] != load_gen:
                self._first_media_frame_exported = False

        if not self._first_media_frame_exported or not self._first_media_frame_logged:
            rendered_media_frame = media_frame_evidence(self.mpv)
            if rendered_media_frame is not None and not self._first_media_frame_logged:
                logging.info(
                    "Rendered first media frame backend=%s source=%dx%d "
                    "time_pos=%.6f path_token=%s",
                    self.render_backend_active,
                    rendered_media_frame.source_width,
                    rendered_media_frame.source_height,
                    rendered_media_frame.time_pos,
                    rendered_media_frame.path_token,
                )
                self._first_media_frame_logged = True
            return rendered_media_frame
        return None

    def _publish_rendered_slot(
        self,
        slot,
        scaled_w: int,
        scaled_h: int,
        use_float: bool,
        color_state_target: str,
        render_epoch: Optional[int],
        pending_pixel_samples,
        rendered_media_frame,
    ) -> bool:
        builder = Gdk.GLTextureBuilder()
        builder.set_context(self.gl_area.get_context())
        builder.set_id(slot.resource.texture_id.value)
        builder.set_width(scaled_w)
        builder.set_height(scaled_h)
        builder.set_format(
            Gdk.MemoryFormat.R16G16B16A16_FLOAT if use_float else Gdk.MemoryFormat.R8G8B8A8
        )

        if color_state_target == "rec2100-pq":
            try:
                builder.set_color_state(Gdk.ColorState.get_rec2100_pq())
                color_state = "rec2100-pq"
            except AttributeError:
                color_state = "unavailable"
        else:
            try:
                builder.set_color_state(Gdk.ColorState.get_srgb())
                color_state = "srgb"
            except AttributeError:
                color_state = "unavailable"

        if not attach_or_wait_fence(self, builder, slot, GL_CLIENT_WAIT_TIMEOUT_NS, _MODULE):
            return GLib.SOURCE_REMOVE

        def on_texture_release(user_data):
            self.fbo_pool.release_buffer(slot)

        published_fallback: Optional[Any] = None
        try:
            texture = builder.build(destroy=on_texture_release, data=id(slot))
        except (TypeError, ValueError):
            logging.warning(
                "Gdk.GLTextureBuilder.build with destroy-notify not supported. "
                "Using fallback release mechanism."
            )
            texture = builder.build()
            published_fallback = slot

        # Fallback path (no destroy-notify): release the *previous* fallback
        # slot only after the new texture has been published.
        prev_fallback = self._fallback_slot
        self._fallback_slot = published_fallback
        self.current_texture = texture
        self.render_color_state = color_state
        self.render_frame_generation += 1
        if render_epoch is not None:
            self._committed_render_epoch = render_epoch

        notify_fbo_pixel_oracle(
            self, slot, render_epoch, use_float, scaled_w, scaled_h, pending_pixel_samples
        )
        if rendered_media_frame is not None:
            dispatch_first_media_frame(self, rendered_media_frame, _MODULE)
        if prev_fallback is not None and prev_fallback is not published_fallback:
            self.fbo_pool.release_buffer(prev_fallback)

        self.queue_draw()
        return GLib.SOURCE_REMOVE

    def _consume_render_trigger(self) -> bool:
        force_redraw = self._force_redraw_pending
        self._force_redraw_pending = False
        self._render_pending = False
        if self._shutting_down or self._render_failed or not self.mpv_ctx:
            return False
        try:
            update_flags = self.mpv_ctx.update()
            return should_render_frame(update_flags, force_redraw=force_redraw)
        except Exception:
            return False

    def _render_pending_frame(self) -> bool:
        if not self._consume_render_trigger():
            return GLib.SOURCE_REMOVE

        w, h = self.get_width(), self.get_height()
        if w <= 0 or h <= 0:
            return GLib.SOURCE_REMOVE

        scale = getattr(self, "_cached_scale", float(getattr(self.props, "scale_factor", 1.0)))
        scaled_w = max(1, min(int(round(w * scale)), getattr(self, "_cached_max_width", 0) or sys.maxsize))
        scaled_h = max(1, min(int(round(h * scale)), getattr(self, "_cached_max_height", 0) or sys.maxsize))

        controller = getattr(self, "hdr_controller", None)
        if is_publication_blocked(controller):
            return GLib.SOURCE_REMOVE

        committed = getattr(controller, "committed_output_state", None) if controller else None
        if committed is not None:
            use_float = committed.is_float
            color_state_target = committed.gdk_color_state
            render_epoch = committed.epoch
        else:
            is_hdr = self.is_hdr_supported and (self.hdr_controller.is_hdr_active if hasattr(self, "hdr_controller") and self.hdr_controller else False)
            use_float = is_hdr and hasattr(Gdk.MemoryFormat, "R16G16B16A16_FLOAT")
            color_state_target = "rec2100-pq" if is_hdr else "srgb"
            render_epoch = None

        self.gl_area.make_current()
        slot = self.fbo_pool.acquire(scaled_w, scaled_h, is_float=use_float)
        if not slot:
            logging.warning("FBO pool exhausted (total dropped: %d), dropping frame", self.fbo_pool.dropped_frames)
            return GLib.SOURCE_REMOVE

        render_error = None
        pending_pixel_samples = None
        rendered_media_frame = None
        try:
            glBindFramebuffer(GL_FRAMEBUFFER, slot.resource.fbo_id.value)
            target_spec = render_target_spec(use_float, rgba16f=GL_RGBA16F, rgba8=GL_RGBA8)
            self.render_target_format = (
                committed.fbo_format if committed is not None
                else "GL_RGBA16F" if use_float else "GL_RGBA8"
            )
            self.render_target_depth = target_spec.depth
            self.mpv_ctx.render(
                flip_y=False,
                block_for_target_time=False,
                depth=target_spec.depth,
                opengl_fbo={
                    "w": scaled_w,
                    "h": scaled_h,
                    "fbo": slot.resource.fbo_id.value,
                    "internal_format": target_spec.internal_format,
                },
            )
            pending_pixel_samples = sample_fbo_pixel_oracle(self, slot, scaled_w, scaled_h, use_float, _MODULE)
            log_first_frame(self, scaled_w, scaled_h, target_spec)
            rendered_media_frame = self._collect_first_media_frame()
        except Exception as e:
            render_error = e
        finally:
            try:
                glBindFramebuffer(GL_FRAMEBUFFER, 0)
            except Exception as e:
                render_error = render_error or e

        if render_error is not None:
            self._latch_render_restart(f"frame rendering failed: {render_error}", status="runtime-failure")
            handle_render_error(self, render_error, slot, _MODULE)
            return GLib.SOURCE_REMOVE

        if fence_and_check_superseded(self, slot, controller, render_epoch, _MODULE):
            return GLib.SOURCE_REMOVE

        return self._publish_rendered_slot(
            slot, scaled_w, scaled_h, use_float, color_state_target, render_epoch, pending_pixel_samples, rendered_media_frame
        )

    def _handle_first_frame_confirmed(self):
        handle_first_frame_confirmed(self, idle_add_once)

    def _shutdown_render_context_for_area(self, area: Gtk.GLArea) -> bool:
        """Free GL resources using the GLArea context that owns them."""
        self._shutting_down = True
        self._render_pending = False
        self._force_redraw_pending = False
        render_context = self.mpv_ctx
        if render_context:
            self._capture_video_selection_for_context_restore()
            try:
                render_context.update_cb = None
            except Exception:
                logging.exception("Failed to disable the libmpv render callback")

        try:
            area.make_current()
        except Exception as error:
            self._latch_render_restart(
                f"could not make the owning GLArea context current during "
                f"shutdown: {error}"
            )
            return False

        area_error = area.get_error()
        if area_error is not None:
            self._latch_render_restart(
                f"owning Gtk.GLArea context failed during renderer shutdown: "
                f"{area_error}"
            )
            return False

        if render_context:
            try:
                render_context.free()
            except Exception as e:
                logging.error(f"Error freeing mpv render context during shutdown: {e}")
            finally:
                self.mpv_ctx = None

        # Drop every GTK-visible reference before deleting the GL textures it
        # may wrap. The fallback texture has no destroy-notify, so return its
        # slot explicitly while the owning context is still current.
        self.current_texture = None
        if self._fallback_slot is not None:
            self.fbo_pool.release_buffer(self._fallback_slot)
            self._fallback_slot = None

        if hasattr(self, "fbo_pool") and self.fbo_pool:
            try:
                self.fbo_pool.release_all()
            except Exception as e:
                logging.error(f"Error releasing FBO pool during shutdown: {e}")
        return True

    def shutdown_render_context(self):
        """Free the render context before mpv exits while GLArea still lives."""
        self._shutdown_render_context_for_area(self.gl_area)

    def _on_unrealize(self, area: Gtk.GLArea):
        self._disconnect_realize_handlers()
        self._shutdown_render_context_for_area(area)

    def do_dispose(self):
        if hasattr(self, "gl_area") and self.gl_area and self.gl_area.get_parent() == self:
            self.gl_area.unparent()
        if (
            hasattr(self, "hdr_controller")
            and self.hdr_controller
            and not self._hdr_controller_disconnected
        ):
            self.hdr_controller.disconnect()
            self._hdr_controller_disconnected = True
        Gtk.Widget.do_dispose(self)

    def do_snapshot(self, snapshot: Gtk.Snapshot):
        if self._shutting_down or not self.current_texture:
            return

        w = self.get_width()
        h = self.get_height()
        if w <= 0 or h <= 0:
            return

        rect = Graphene.Rect.alloc()
        rect.init(0, 0, w, h)
        snapshot.append_texture(self.current_texture, rect)

    def queue_render(self):
        self.queue_draw()

    def clear_frame(self):
        """Drop the published texture on video stop/idle (P1-4).

        This releases the GTK-side texture wrapper (and, via its
        destroy-notify, returns the slot to the pool). The pool itself keeps
        its GL buffers allocated for reuse until the widget is unrealized —
        it does NOT free the FBO VRAM immediately.
        """
        self.current_texture = None
        if self._fallback_slot is not None:
            # No destroy-notify on this texture; nothing renders after a
            # stop, so returning the slot to the pool here is safe.
            self.fbo_pool.release_buffer(self._fallback_slot)
            self._fallback_slot = None
        self.queue_draw()
