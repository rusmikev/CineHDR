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
import gi
import mpv
from gettext import gettext as _
from typing import Any, Optional
from gi.repository import Gtk, Gdk, GLib, GObject

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from .gl_bindings import (
    GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D,
    GL_TEXTURE_MIN_FILTER, GL_TEXTURE_MAG_FILTER, GL_LINEAR,
    GL_RGBA, GL_FLOAT, GL_RGBA16F, GL_FRAMEBUFFER_COMPLETE,
    glGenFramebuffers, glDeleteFramebuffers, glBindFramebuffer,
    glFramebufferTexture2D, glGenTextures, glDeleteTextures,
    glBindTexture, glTexImage2D, glTexParameteri, glCheckFramebufferStatus,
    check_gl_error, egl_get_proc_address, get_proc_address
)

from .gl_renderer import GLFramebufferResource
from .hdr_controller import HdrController
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

        self.fbo_resource = GLFramebufferResource()
        self.mpv_ctx: Optional[mpv.MpvRenderContext] = None
        self.current_texture: Optional[Gdk.Texture] = None

        # Delegate HDR state and mpv property observers to HdrController
        self.hdr_controller = HdrController(
            mpv_player,
            on_change_cb=lambda: idle_add_once(self.queue_draw)
        )

    @property
    def texture_id(self) -> Any:
        return getattr(self, "fbo_resource", GLFramebufferResource()).texture_id

    @property
    def fbo_id(self) -> Any:
        return getattr(self, "fbo_resource", GLFramebufferResource()).fbo_id

    @property
    def tex_width(self) -> int:
        return getattr(self, "fbo_resource", GLFramebufferResource()).width

    @property
    def tex_height(self) -> int:
        return getattr(self, "fbo_resource", GLFramebufferResource()).height

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
        area.make_current()

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

        self.mpv_ctx.update_cb = lambda: idle_add_once(self.queue_draw)
        self.fbo_resource.release()

    def _on_unrealize(self, area: Gtk.GLArea):
        area.make_current()
        if self.mpv_ctx:
            self.mpv_ctx.update_cb = None
            self.mpv_ctx.free()
            self.mpv_ctx = None

        if hasattr(self, "hdr_controller") and self.hdr_controller:
            self.hdr_controller.disconnect()

        self.fbo_resource.release()

    def do_unroot(self):
        # Allow GTK standard lifecycle signal emission ("unrealize") without double execution (Audit Finding 12)
        Gtk.Widget.do_unroot(self)

    def do_dispose(self):
        # Cleanly unparent child GLArea to prevent GTK reference leaks (Risk P-4)
        if hasattr(self, "gl_area") and self.gl_area and self.gl_area.get_parent() == self:
            self.gl_area.unparent()
        Gtk.Widget.do_dispose(self)

    def setup_fbo(self, w: int, h: int, is_float: bool = True):
        self.gl_area.make_current()
        self.fbo_resource.ensure(w, h, is_float=is_float)

    def do_snapshot(self, snapshot: Gtk.Snapshot):
        if not self.gl_area.get_realized():
            return

        w = self.get_width()
        h = self.get_height()
        if w <= 0 or h <= 0:
            return

        scale = self.props.scale_factor
        scaled_w = int(w * scale)
        scaled_h = int(h * scale)

        # Determine color state and float format support via HdrController (Audit Finding 2)
        try:
            is_hdr = self.hdr_controller.is_hdr_active
            self.hdr_controller.check_unsupported_warning(Gdk.Display.get_default())
        except Exception:
            is_hdr = False

        has_float = hasattr(Gdk.MemoryFormat, "R16G16B16A16_FLOAT")
        use_float = is_hdr and has_float

        # Recreate texture if size changed (> 1px), not initialized, or format changed (Risk P-5 / Audit Finding 2)
        if (
            self.texture_id.value == 0
            or abs(self.tex_width - scaled_w) > 1
            or abs(self.tex_height - scaled_h) > 1
            or getattr(self.fbo_resource, "is_float", True) != use_float
        ):
            self.setup_fbo(scaled_w, scaled_h, is_float=use_float)

        if self.mpv_ctx and self.fbo_id.value != 0:
            self.gl_area.make_current()

            glBindFramebuffer(GL_FRAMEBUFFER, self.fbo_id.value)

            self.mpv_ctx.render(
                flip_y=False,
                opengl_fbo={
                    "w": scaled_w,
                    "h": scaled_h,
                    "fbo": self.fbo_id.value,
                },
            )

            glBindFramebuffer(GL_FRAMEBUFFER, 0)

            builder = Gdk.GLTextureBuilder()
            builder.set_context(self.gl_area.get_context())
            builder.set_id(self.texture_id.value)
            builder.set_width(scaled_w)
            builder.set_height(scaled_h)

            if use_float:
                builder.set_format(Gdk.MemoryFormat.R16G16B16A16_FLOAT)
            else:
                builder.set_format(Gdk.MemoryFormat.B8G8R8A8)

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

            self.current_texture = builder.build()

        if self.current_texture:
            from gi.repository import Graphene

            rect = Graphene.Rect.alloc()
            rect.init(0, 0, w, h)
            snapshot.append_texture(self.current_texture, rect)

    def queue_render(self):
        self.queue_draw()
