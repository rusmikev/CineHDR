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
    GL_SYNC_GPU_COMMANDS_COMPLETE,
    GL_SYNC_FLUSH_COMMANDS_BIT,
    GL_TIMEOUT_EXPIRED,
    GL_WAIT_FAILED,
    glFenceSync,
    glFlush,
    glClientWaitSync,
    glBindFramebuffer,
    get_proc_address,
)

from .gl_renderer import GLFramebufferPool, FramebufferSlot
from .hdr_controller import HdrController
from .hdr_detection import check_hdr_support
from .utils import idle_add_once, get_display_param

# CPU-side wait budget for the GTK < 4.16 fallback path (no
# GdkGLTextureBuilder.set_sync). glClientWaitSync takes a real timeout in
# nanoseconds — GL_TIMEOUT_IGNORED is only valid for glWaitSync and would
# block the UI thread indefinitely on a stalled driver (F5).
GL_CLIENT_WAIT_TIMEOUT_NS = 100_000_000  # 100 ms


class MpvVideoWidget(Gtk.Widget):
    """Custom GTK4 video widget integrating libmpv with GdkGLTextureBuilder and HDR."""

    __gtype_name__ = "MpvVideoWidget"

    def __init__(self, mpv_player: mpv.MPV):
        super().__init__()
        self.mpv = mpv_player
        self.set_layout_manager(Gtk.BinLayout())

        # GLArea to manage context initialization and life-cycle
        self.gl_area = Gtk.GLArea()
        self.gl_area.set_parent(self)

        self.gl_area.connect("realize", self._on_realize)
        self.gl_area.connect("unrealize", self._on_unrealize)

        self.fbo_pool = GLFramebufferPool(size=3)
        self.mpv_ctx: Optional[mpv.MpvRenderContext] = None
        self.current_texture: Optional[Gdk.Texture] = None

        self._shutting_down = False
        self._render_pending = False
        self._cached_hdr_support = False
        self._cached_hdr_support_valid = False
        self._monitor_signal_id: Optional[int] = None
        self._connected_monitors: Optional[Any] = None
        self._fallback_slot: Optional[Any] = None

        # Delegate HDR state and mpv property observers to HdrController
        self.hdr_controller = HdrController(
            mpv_player,
            on_change_cb=lambda: idle_add_once(self.queue_draw)
        )

    def _update_cached_hdr_support(self, *args):
        from .hdr_detection import invalidate_hdr_support_cache
        invalidate_hdr_support_cache()
        old_support = getattr(self, "_cached_hdr_support", None)
        self._cached_hdr_support = check_hdr_support()
        self._cached_hdr_support_valid = True
        if old_support is not None and old_support != self._cached_hdr_support and hasattr(self, "hdr_controller"):
            self.hdr_controller.apply_hdr_settings()
            self.queue_draw()

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

    def _on_realize(self, area: Gtk.GLArea):
        self._shutting_down = False
        if self.mpv_ctx:
            try:
                self.mpv_ctx.free()
            except Exception:
                pass
            self.mpv_ctx = None

        area.make_current()
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
                        surface.connect("enter-monitor", self._update_cached_hdr_support)
                        surface.connect("leave-monitor", self._update_cached_hdr_support)
        except Exception:
            pass

        proc_address_fn = mpv.MpvGlGetProcAddressFn(
            lambda _inst, name: get_proc_address(name)
        )
        display_param = get_display_param()

        self.mpv_ctx = mpv.MpvRenderContext(
            self.mpv,
            "opengl",
            opengl_init_params={
                "get_proc_address": proc_address_fn,
            },
            **display_param,
        )

        def on_mpv_update():
            if not self._shutting_down and not self._render_pending:
                self._render_pending = True
                idle_add_once(self._render_pending_frame)

        self.mpv_ctx.update_cb = on_mpv_update

    def _render_pending_frame(self) -> bool:
        self._render_pending = False
        if self._shutting_down or not self.mpv_ctx:
            return GLib.SOURCE_REMOVE

        try:
            if not self.mpv_ctx.update():
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

        try:
            glBindFramebuffer(GL_FRAMEBUFFER, slot.resource.fbo_id.value)
            self.mpv_ctx.render(
                flip_y=False,
                opengl_fbo={
                    "w": scaled_w,
                    "h": scaled_h,
                    "fbo": slot.resource.fbo_id.value,
                },
            )
            glBindFramebuffer(GL_FRAMEBUFFER, 0)
        except Exception as e:
            logging.error(f"Error rendering mpv frame: {e}")
            self.fbo_pool.release_buffer(slot)
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
            except AttributeError:
                pass
        else:
            try:
                builder.set_color_state(Gdk.ColorState.get_srgb())
            except AttributeError:
                pass

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
            # is bounded so a stalled driver cannot freeze the UI thread; on
            # timeout the frame is published anyway (worst case is a torn
            # frame — same as having no synchronization at all) (F5).
            try:
                wait_result = glClientWaitSync(
                    slot.fence, GL_SYNC_FLUSH_COMMANDS_BIT, GL_CLIENT_WAIT_TIMEOUT_NS
                )
                if wait_result in (GL_TIMEOUT_EXPIRED, GL_WAIT_FAILED):
                    logging.warning(
                        f"glClientWaitSync returned 0x{wait_result:04x}; "
                        "publishing frame without GPU sync"
                    )
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
        if prev_fallback is not None and prev_fallback is not published_fallback:
            self.fbo_pool.release_buffer(prev_fallback)

        self.queue_draw()
        return GLib.SOURCE_REMOVE

    def shutdown_render_context(self):
        """Cleanly shutdown render context and free GPU resources before mpv exits (Stage 4)."""
        self._shutting_down = True
        if self.mpv_ctx:
            self.mpv_ctx.update_cb = None
            try:
                self.gl_area.make_current()
                self.mpv_ctx.free()
            except Exception as e:
                logging.error(f"Error freeing mpv render context during shutdown: {e}")
            finally:
                self.mpv_ctx = None
        if hasattr(self, "fbo_pool") and self.fbo_pool:
            try:
                self.gl_area.make_current()
                self.fbo_pool.release_all()
            except Exception as e:
                logging.error(f"Error releasing FBO pool during shutdown: {e}")
        if hasattr(self, "hdr_controller") and self.hdr_controller:
            self.hdr_controller.disconnect()

    def _on_unrealize(self, area: Gtk.GLArea):
        if self._connected_monitors and self._monitor_signal_id:
            try:
                self._connected_monitors.disconnect(self._monitor_signal_id)
            except Exception:
                pass
            self._monitor_signal_id = None
            self._connected_monitors = None
        self.shutdown_render_context()

    def do_dispose(self):
        if hasattr(self, "gl_area") and self.gl_area and self.gl_area.get_parent() == self:
            self.gl_area.unparent()
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


