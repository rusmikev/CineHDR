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

import ctypes
import gi
import mpv
from gettext import gettext as _

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gtk, Gdk, GLib, GObject

import os
import json
from .utils import get_display_param, idle_add_once, CONFIG_DIR

# Load OpenGL libraries and helper
libgl = ctypes.CDLL("libGL.so.1")
libegl = ctypes.CDLL("libEGL.so.1")
egl_get_proc_address = libegl.eglGetProcAddress
egl_get_proc_address.restype = ctypes.c_void_p
egl_get_proc_address.argtypes = [ctypes.c_char_p]


def get_gl_func(name, restype, argtypes):
    addr = egl_get_proc_address(name.encode("utf-8"))
    if addr:
        prototype = ctypes.CFUNCTYPE(restype, *argtypes)
        return prototype(addr)
    try:
        func = getattr(libgl, name)
        func.restype = restype
        func.argtypes = argtypes
        return func
    except AttributeError:
        return None


# OpenGL Constants
GL_FRAMEBUFFER = 0x8D40
GL_COLOR_ATTACHMENT0 = 0x8CE0
GL_TEXTURE_2D = 0xDE1
GL_TEXTURE_MIN_FILTER = 0x2801
GL_TEXTURE_MAG_FILTER = 0x2800
GL_LINEAR = 0x2601
GL_RGBA = 0x1908
GL_FLOAT = 0x1406
GL_RGBA16F = 0x881A
GL_FRAMEBUFFER_COMPLETE = 0x8CD5

# OpenGL Bindings
glGenFramebuffers = get_gl_func(
    "glGenFramebuffers", None, [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
)
glDeleteFramebuffers = get_gl_func(
    "glDeleteFramebuffers", None, [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
)
glBindFramebuffer = get_gl_func(
    "glBindFramebuffer", None, [ctypes.c_uint, ctypes.c_uint]
)
glFramebufferTexture2D = get_gl_func(
    "glFramebufferTexture2D",
    None,
    [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_int],
)
glGenTextures = get_gl_func(
    "glGenTextures", None, [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
)
glDeleteTextures = get_gl_func(
    "glDeleteTextures", None, [ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
)
glBindTexture = get_gl_func(
    "glBindTexture", None, [ctypes.c_uint, ctypes.c_uint]
)
glTexImage2D = get_gl_func(
    "glTexImage2D",
    None,
    [
        ctypes.c_uint,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.c_void_p,
    ],
)
glTexParameteri = get_gl_func(
    "glTexParameteri", None, [ctypes.c_uint, ctypes.c_uint, ctypes.c_int]
)
glCheckFramebufferStatus = get_gl_func(
    "glCheckFramebufferStatus", ctypes.c_uint, [ctypes.c_uint]
)


HDR_CONFIG_PATH = os.path.join(CONFIG_DIR, "hdr_config.json")


def load_hdr_config():
    try:
        if os.path.exists(HDR_CONFIG_PATH):
            with open(HDR_CONFIG_PATH, "r") as f:
                data = json.load(f)
                return {
                    "hdr_enabled": data.get("hdr_enabled", True),
                    "hdr_target_peak": data.get("hdr_target_peak", "auto"),
                    "hdr_target_prim": data.get("hdr_target_prim", "dci-p3")
                }
    except Exception as e:
        print(f"Error loading HDR config: {e}")
    return {
        "hdr_enabled": True,
        "hdr_target_peak": "auto",
        "hdr_target_prim": "dci-p3"
    }


def save_hdr_config(config):
    try:
        with open(HDR_CONFIG_PATH, "w") as f:
            json.dump(config, f)
    except Exception as e:
        print(f"Error saving HDR config: {e}")


def load_hdr_setting():
    return load_hdr_config()["hdr_enabled"]


def save_hdr_setting(enabled):
    cfg = load_hdr_config()
    cfg["hdr_enabled"] = enabled
    save_hdr_config(cfg)


class MpvVideoWidget(Gtk.Widget):
    """Custom GTK4 video widget integrating libmpv with GdkGLTextureBuilder and HDR."""

    __gtype_name__ = "MpvVideoWidget"

    def __init__(self, mpv_player):
        super().__init__()
        self.mpv = mpv_player
        self.set_layout_manager(Gtk.BinLayout())

        # GLArea to manage context initialization and life-cycle
        self.gl_area = Gtk.GLArea()
        self.gl_area.set_parent(self)

        self.gl_area.connect("realize", self._on_realize)
        self.gl_area.connect("unrealize", self._on_unrealize)

        self.texture_id = None
        self.fbo_id = None
        self.tex_width = 0
        self.tex_height = 0
        self.mpv_ctx = None
        self.current_texture = None

        config = load_hdr_config()
        self._hdr_enabled = config["hdr_enabled"]
        self._hdr_target_peak = config["hdr_target_peak"]
        self._hdr_target_prim = config["hdr_target_prim"]
        self.apply_hdr_settings()

    def apply_hdr_settings(self):
        try:
            self.mpv["target-colorspace-hint"] = "yes" if self._hdr_enabled else "no"
            if self._hdr_enabled:
                self.mpv["target-prim"] = self._hdr_target_prim
                if self._hdr_target_peak == "auto":
                    self.mpv["target-peak"] = "auto"
                else:
                    self.mpv["target-peak"] = float(self._hdr_target_peak)
                self.mpv["target-trc"] = "pq"
            else:
                self.mpv["target-prim"] = "auto"
                self.mpv["target-peak"] = "auto"
                self.mpv["target-trc"] = "auto"
        except Exception as e:
            print(f"Error applying HDR settings: {e}")

    @property
    def hdr_enabled(self):
        return self._hdr_enabled

    @hdr_enabled.setter
    def hdr_enabled(self, value):
        self._hdr_enabled = value
        self.apply_hdr_settings()

    @property
    def hdr_target_peak(self):
        return self._hdr_target_peak

    @hdr_target_peak.setter
    def hdr_target_peak(self, value):
        self._hdr_target_peak = value
        self.apply_hdr_settings()

    @property
    def hdr_target_prim(self):
        return self._hdr_target_prim

    @hdr_target_prim.setter
    def hdr_target_prim(self, value):
        self._hdr_target_prim = value
        self.apply_hdr_settings()

    def _on_realize(self, area):
        area.make_current()

        proc_address_fn = mpv.MpvGlGetProcAddressFn(
            lambda _inst, name: egl_get_proc_address(name)
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

        self.texture_id = ctypes.c_uint(0)
        self.fbo_id = ctypes.c_uint(0)

    def _on_unrealize(self, area):
        area.make_current()
        if self.mpv_ctx:
            self.mpv_ctx.update_cb = None
            self.mpv_ctx.free()
            self.mpv_ctx = None

        if self.fbo_id and self.fbo_id.value != 0:
            glDeleteFramebuffers(1, ctypes.byref(self.fbo_id))
            self.fbo_id = None

        if self.texture_id and self.texture_id.value != 0:
            glDeleteTextures(1, ctypes.byref(self.texture_id))
            self.texture_id = None

    def setup_fbo(self, w, h):
        self.gl_area.make_current()

        # Delete existing FBO and texture if size changes
        if self.fbo_id.value != 0:
            glDeleteFramebuffers(1, ctypes.byref(self.fbo_id))
            self.fbo_id = ctypes.c_uint(0)

        if self.texture_id.value != 0:
            glDeleteTextures(1, ctypes.byref(self.texture_id))
            self.texture_id = ctypes.c_uint(0)

        # Generate new texture
        glGenTextures(1, ctypes.byref(self.texture_id))
        glBindTexture(GL_TEXTURE_2D, self.texture_id.value)

        # Use 16-bit float format (GL_RGBA16F)
        # to preserve HDR color space precision (10-bit or higher)
        glTexImage2D(
            GL_TEXTURE_2D,
            0,
            GL_RGBA16F,
            w,
            h,
            0,
            GL_RGBA,
            GL_FLOAT,
            None,
        )

        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)

        # Generate FBO
        glGenFramebuffers(1, ctypes.byref(self.fbo_id))
        glBindFramebuffer(GL_FRAMEBUFFER, self.fbo_id.value)

        # Attach texture to FBO
        glFramebufferTexture2D(
            GL_FRAMEBUFFER,
            GL_COLOR_ATTACHMENT0,
            GL_TEXTURE_2D,
            self.texture_id.value,
            0,
        )

        status = glCheckFramebufferStatus(GL_FRAMEBUFFER)
        if status != GL_FRAMEBUFFER_COMPLETE:
            print(f"Error: Framebuffer is not complete: {hex(status)}")

        glBindFramebuffer(GL_FRAMEBUFFER, 0)
        glBindTexture(GL_TEXTURE_2D, 0)

        self.tex_width = w
        self.tex_height = h

    def do_snapshot(self, snapshot):
        if not self.gl_area.get_realized():
            return

        w = self.get_width()
        h = self.get_height()
        if w <= 0 or h <= 0:
            return

        scale = self.props.scale_factor
        scaled_w = int(w * scale)
        scaled_h = int(h * scale)

        # Recreate texture if size changed
        if (
            self.texture_id.value == 0
            or self.tex_width != scaled_w
            or self.tex_height != scaled_h
        ):
            self.setup_fbo(scaled_w, scaled_h)

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
            builder.set_format(Gdk.MemoryFormat.R16G16B16A16_FLOAT)

            # Determine color state (HDR vs SDR)
            try:
                params = self.mpv.video_params
                if params:
                    primaries = params.get("primaries")
                    gamma = params.get("gamma")
                    is_hdr = self.hdr_enabled and ((primaries == "bt.2020") or (gamma in ("pq", "hlg")))
                else:
                    is_hdr = False
            except Exception as e:
                is_hdr = False

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
