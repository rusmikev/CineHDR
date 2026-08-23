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

import ctypes
import logging
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gtk, Gdk, GLib, GObject, Graphene
import mpv
from gettext import gettext as _
from typing import Any, Optional

from .gl_bindings import (
    GL_FRAMEBUFFER,
    GL_RENDERER,
    GL_RGBA16F,
    GL_RGBA8,
    GL_SHADING_LANGUAGE_VERSION,
    GL_SYNC_GPU_COMMANDS_COMPLETE,
    GL_SYNC_FLUSH_COMMANDS_BIT,
    GL_TIMEOUT_EXPIRED,
    GL_VENDOR,
    GL_VERSION,
    GL_WAIT_FAILED,
    glFenceSync,
    glFlush,
    glClientWaitSync,
    glBindFramebuffer,
    get_gl_string,
    get_proc_address,
)

from .gl_renderer import GLFramebufferPool, FramebufferSlot
from .hdr_controller import HdrController
from .hdr_detection import check_hdr_support
from .render_backend import (
    ProcessRenderSelection,
    RenderBackend,
    RenderSelectionSource,
    create_render_context,
    install_python_mpv_depth_compat,
    render_target_spec,
    render_runtime_diagnostics,
    should_render_frame,
    target_configuration_requires_redraw,
)
from .utils import idle_add_once, get_display_param

# CPU-side wait budget for the GTK < 4.16 fallback path (no
# GdkGLTextureBuilder.set_sync). glClientWaitSync takes a real timeout in
# nanoseconds — GL_TIMEOUT_IGNORED is only valid for glWaitSync and would
# block the UI thread indefinitely on a stalled driver (F5).
GL_CLIENT_WAIT_TIMEOUT_NS = 100_000_000  # 100 ms


class MpvVideoWidget(Gtk.Widget):
    """Custom GTK4 video widget integrating libmpv with GdkGLTextureBuilder and HDR."""

    __gtype_name__ = "MpvVideoWidget"

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

        self.fbo_pool = GLFramebufferPool(size=3)
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

        # Delegate HDR state and mpv property observers to HdrController
        self.hdr_controller = HdrController(
            mpv_player,
            on_change_cb=lambda: idle_add_once(self.queue_draw)
        )
        self._window = None

    def setup_window_integration(self, window):
        """Clean integration hook for window-level HDR UI (e.g. hdr_menu_btn)."""
        self._window = window

    def _update_cached_hdr_support(self, *args):
        from .hdr_detection import invalidate_hdr_support_cache
        invalidate_hdr_support_cache()
        old_support = getattr(self, "_cached_hdr_support", None)
        self._cached_hdr_support = check_hdr_support()
        self._cached_hdr_support_valid = True
        self._push_output_hint()
        if old_support is not None and old_support != self._cached_hdr_support and hasattr(self, "hdr_controller"):
            self.hdr_controller.apply_hdr_settings()
            self.queue_draw()

    def _push_output_hint(self):
        """Tell HdrController which monitor the widget currently sits on, so
        the auto-mode monitor gate reads the right output's image description
        on multi-monitor setups (SDR laptop panel + HDR TV)."""
        if not hasattr(self, "hdr_controller"):
            return
        connector = None
        try:
            native = self.get_native()
            surface = native.get_surface() if native and hasattr(native, "get_surface") else None
            display = self.get_display()
            if surface and display and hasattr(display, "get_monitor_at_surface"):
                monitor = display.get_monitor_at_surface(surface)
                if monitor and hasattr(monitor, "get_connector"):
                    connector = monitor.get_connector()
        except Exception:
            connector = None
        try:
            self.hdr_controller.set_output_hint(connector)
        except Exception:
            pass

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
        self._video_selection_for_context_restore = None
        try:
            current_video = getattr(self.mpv, "vid", None)
            if current_video in (False, None, "no", "auto") or (
                isinstance(current_video, (int, str)) and str(current_video).isdigit() is False
            ):
                return
            try:
                configured_video = str(
                    self.mpv._get_property("options/vid")
                ).strip().lower()
            except Exception:
                configured_video = ""
            self._video_selection_for_context_restore = (
                "auto"
                if configured_video in ("auto", "-1")
                else str(int(current_video))
            )
        except Exception:
            logging.exception(
                "Failed to capture the video track before context recreation"
            )

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
        self.render_backend_active = None
        self.render_backend_fallback_reason = None
        self.render_session_status = "not-initialized"
        self.render_failure_reason = None
        self.render_runtime = {}
        self.render_target_format = None
        self.render_target_depth = None
        self.render_color_state = None
        self._render_restart_required_reason = None

        area.make_current()
        area_error = area.get_error()
        if area_error is not None:
            self._latch_render_restart(
                f"Gtk.GLArea could not make its context current: {area_error}"
            )
            return

        self._shutting_down = False
        self._render_failed = False
        self._first_frame_logged = False
        self._update_cached_hdr_support()

        display = self.get_display()
        if display and hasattr(display, "get_monitors"):
            monitors = display.get_monitors()
            if monitors and hasattr(monitors, "connect"):
                try:
                    self._monitor_signal_id = monitors.connect("items-changed", self._update_cached_hdr_support)
                    self._connected_monitors = monitors
                except Exception:
                    pass

        try:
            native = self.get_native()
            if native and hasattr(native, "get_surface"):
                surface = native.get_surface()
                if surface:
                    if hasattr(surface, "connect"):
                        self._connected_surface = surface
                        for signal_name in ("enter-monitor", "leave-monitor"):
                            self._surface_signal_ids.append(
                                surface.connect(
                                    signal_name, self._update_cached_hdr_support
                                )
                            )
        except Exception:
            pass

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
        self.render_backend_fallback_reason = backend_state.fallback_reason
        self.render_session_status = (
            "startup-fallback"
            if backend_state.fallback_reason
            else "active"
        )
        logging.info(
            "libmpv render backend requested=%s active=%s fallback_reason=%s",
            backend_state.requested.value,
            backend_state.active,
            backend_state.fallback_reason or "none",
        )
        runtime = render_runtime_diagnostics(mpv, self.mpv)
        runtime.update(
            {
                "gl_vendor": get_gl_string(GL_VENDOR),
                "gl_renderer": get_gl_string(GL_RENDERER),
                "gl_version": get_gl_string(GL_VERSION),
                "glsl_version": get_gl_string(GL_SHADING_LANGUAGE_VERSION),
            }
        )
        self.render_runtime = runtime
        logging.info(
            "Render runtime libmpv=%s mpv=%s libplacebo=%s ffmpeg=%s "
            "configuration=%s OpenGL=%s / %s / %s",
            runtime["libmpv_path"],
            runtime["mpv_version"],
            runtime["libplacebo_version"],
            runtime["ffmpeg_version"],
            runtime["mpv_configuration"],
            runtime["gl_vendor"],
            runtime["gl_renderer"],
            runtime["gl_version"],
        )
        if backend_state.fallback_reason and self._window:
            app = getattr(self._window, "app", None)
            notify = getattr(app, "notify_render_fallback_once", None)
            if notify:
                notify(self._window, backend_state.fallback_reason)

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
        idle_add_once(self._render_pending_frame)

    def do_measure(self, orientation: int, for_size: int) -> tuple[int, int, int, int]:
        if getattr(self, "gl_area", None):
            return self.gl_area.measure(orientation, for_size)
        return (0, 0, -1, -1)

    def do_size_allocate(self, width: int, height: int, baseline: int):
        """Allocate children and redraw a paused frame for the new target."""
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

    def _render_pending_frame(self) -> bool:
        force_redraw = self._force_redraw_pending
        self._force_redraw_pending = False
        self._render_pending = False
        if self._shutting_down or self._render_failed or not self.mpv_ctx:
            return GLib.SOURCE_REMOVE

        try:
            update_flags = self.mpv_ctx.update()
            if not should_render_frame(
                update_flags, force_redraw=force_redraw
            ):
                return GLib.SOURCE_REMOVE
        except Exception:
            return GLib.SOURCE_REMOVE

        w = self.get_width()
        h = self.get_height()
        if w <= 0 or h <= 0:
            return GLib.SOURCE_REMOVE

        scale = self.props.scale_factor
        scaled_w = int(w * scale)
        scaled_h = int(h * scale)

        # NOTE: the "HDR requested but unsupported" warning is emitted by
        # HdrController.apply_hdr_settings(); calling it here behind
        # `if is_hdr:` made it unreachable (support was already proven true).
        is_hdr = self.is_hdr_supported and self.hdr_controller.is_hdr_active
        has_float = hasattr(Gdk.MemoryFormat, "R16G16B16A16_FLOAT")
        use_float = is_hdr and has_float

        self.gl_area.make_current()
        slot = self.fbo_pool.acquire(scaled_w, scaled_h, is_float=use_float)
        if not slot:
            logging.debug("FBO pool exhausted, dropping frame")
            return GLib.SOURCE_REMOVE

        render_error = None
        try:
            glBindFramebuffer(GL_FRAMEBUFFER, slot.resource.fbo_id.value)
            target_spec = render_target_spec(
                use_float, rgba16f=GL_RGBA16F, rgba8=GL_RGBA8
            )
            self.render_target_format = "GL_RGBA16F" if use_float else "GL_RGBA8"
            self.render_target_depth = target_spec.depth
            self.mpv_ctx.render(
                flip_y=False,
                depth=target_spec.depth,
                opengl_fbo={
                    "w": scaled_w,
                    "h": scaled_h,
                    "fbo": slot.resource.fbo_id.value,
                    "internal_format": target_spec.internal_format,
                },
            )
            if not self._first_frame_logged:
                logging.info(
                    "Rendered first frame backend=%s target=%dx%d "
                    "internal_format=0x%x depth=%d",
                    self.render_backend_active,
                    scaled_w,
                    scaled_h,
                    target_spec.internal_format,
                    target_spec.depth,
                )
                self._first_frame_logged = True
        except Exception as e:
            render_error = e
        finally:
            try:
                glBindFramebuffer(GL_FRAMEBUFFER, 0)
            except Exception as e:
                render_error = render_error or e

        if render_error is not None:
            failure_reason = f"frame rendering failed: {render_error}"
            self._latch_render_restart(
                failure_reason, status="runtime-failure"
            )
            logging.error(
                "Video renderer stopped after a frame-rendering failure; "
                "restart CineHDR in legacy mode: %s",
                render_error,
            )
            self.fbo_pool.release_buffer(slot)
            window = getattr(self, "_window", None)
            if window and hasattr(window, "show_toast"):
                idle_add_once(
                    window.show_toast,
                    _("Video renderer stopped. Restart CineHDR."),
                    True,
                )
            return GLib.SOURCE_REMOVE

        if glFenceSync:
            slot.fence = glFenceSync(GL_SYNC_GPU_COMMANDS_COMPLETE, 0)
        else:
            slot.fence = None

        # Submit the fence and pending GL commands to the GPU now: a fence
        # that was never flushed may never signal when another context
        # (GTK's renderer) waits on it via set_sync (F5).
        if glFlush:
            try:
                glFlush()
            except Exception:
                pass

        builder = Gdk.GLTextureBuilder()
        builder.set_context(self.gl_area.get_context())
        builder.set_id(slot.resource.texture_id.value)
        builder.set_width(scaled_w)
        builder.set_height(scaled_h)

        if use_float:
            builder.set_format(Gdk.MemoryFormat.R16G16B16A16_FLOAT)
        else:
            builder.set_format(Gdk.MemoryFormat.R8G8B8A8)

        if is_hdr:
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

        has_set_sync = False
        if slot.fence and hasattr(builder, "set_sync"):
            try:
                builder.set_sync(slot.fence)
                has_set_sync = True
            except AttributeError:
                pass

        if not has_set_sync and slot.fence and glClientWaitSync:
            # GTK < 4.16: no GdkGLTextureBuilder.set_sync, so wait for the
            # GPU on the CPU side before publishing the texture. The timeout
            # is bounded so a stalled driver cannot freeze the UI thread.
            try:
                wait_result = glClientWaitSync(
                    slot.fence, GL_SYNC_FLUSH_COMMANDS_BIT, GL_CLIENT_WAIT_TIMEOUT_NS
                )
                if wait_result in (GL_TIMEOUT_EXPIRED, GL_WAIT_FAILED):
                    logging.warning(
                        f"glClientWaitSync returned 0x{wait_result:04x}; "
                        "dropping frame to avoid tearing/artifacts"
                    )
                    self.fbo_pool.release_buffer(slot)
                    return GLib.SOURCE_REMOVE
            except Exception:
                pass

        def on_texture_release(user_data):
            self.fbo_pool.release_buffer(slot)

        published_fallback: Optional[Any] = None
        try:
            texture = builder.build(destroy=on_texture_release, data=id(slot))
        except (TypeError, ValueError):
            logging.warning("Gdk.GLTextureBuilder.build with destroy-notify not supported. Using fallback release mechanism.")
            texture = builder.build()
            published_fallback = slot

        # Fallback path (no destroy-notify): release the *previous* fallback
        # slot only after the new texture has been published. Releasing it at
        # the start of the frame allowed the pool to hand the still-displayed
        # texture back to mpv, which then rendered into it mid-composite.
        prev_fallback = self._fallback_slot
        self._fallback_slot = published_fallback
        self.current_texture = texture
        self.render_color_state = color_state
        self.render_frame_generation += 1
        if prev_fallback is not None and prev_fallback is not published_fallback:
            self.fbo_pool.release_buffer(prev_fallback)

        self.queue_draw()
        return GLib.SOURCE_REMOVE

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
