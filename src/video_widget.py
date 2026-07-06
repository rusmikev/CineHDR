# video_widget.py
#
# Copyright 2026 Diego Povliuk
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
import mpv
from gettext import gettext as _
from typing import Any, Optional
from gi.repository import Gtk, Gdk, GLib, GObject, Graphene

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from .gl_bindings import (
    GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D,
    GL_TEXTURE_MIN_FILTER, GL_TEXTURE_MAG_FILTER, GL_LINEAR,
    GL_RGBA, GL_FLOAT, GL_RGBA16F, GL_FRAMEBUFFER_COMPLETE,
    GL_SYNC_GPU_COMMANDS_COMPLETE, glFenceSync, glDeleteSync,
    glGenFramebuffers, glDeleteFramebuffers, glBindFramebuffer,
    glFramebufferTexture2D, glGenTextures, glDeleteTextures,
    glBindTexture, glTexImage2D, glTexParameteri, glCheckFramebufferStatus,
    check_gl_error, egl_get_proc_address, get_proc_address
)

from .gl_renderer import GLFramebufferPool, FramebufferSlot
from .hdr_controller import HdrController
from .hdr_detection import check_hdr_support
from .utils import idle_add_once, get_display_param


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
        self._cached_hdr_support = check_hdr_support()
        self._cached_hdr_support_valid = True

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

    @property
    def hdr_target_prim(self) -> str:
        return self.hdr_controller.hdr_target_prim

    @hdr_target_prim.setter
    def hdr_target_prim(self, value: str):
        self.hdr_controller.hdr_target_prim = value

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

        if getattr(self, "_fallback_slot", None):
            self.fbo_pool.release_buffer(self._fallback_slot)
            self._fallback_slot = None

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

        is_hdr = self.is_hdr_supported and self.hdr_controller.is_hdr_active
        if is_hdr:
            self.hdr_controller.check_unsupported_warning(self.get_display())
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

        if slot.fence and hasattr(builder, "set_sync"):
            try:
                builder.set_sync(slot.fence)
            except AttributeError:
                pass

        def on_texture_release(user_data):
            self.fbo_pool.release_buffer(slot)

        try:
            texture = builder.build(destroy=on_texture_release, data=id(slot))
        except (TypeError, ValueError):
            texture = builder.build()
            self._fallback_slot = slot

        self.current_texture = texture
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
        """Clear current texture and queue redraw to release VRAM on video stop/idle (P1-4)."""
        self.current_texture = None
        self.queue_draw()


