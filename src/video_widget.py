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
from gi.repository import Gtk, Gdk, GLib, GObject, Gio

import os
from .utils import get_display_param, idle_add_once
from .gl_bindings import (
    GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D,
    GL_TEXTURE_MIN_FILTER, GL_TEXTURE_MAG_FILTER, GL_LINEAR,
    GL_RGBA, GL_FLOAT, GL_RGBA16F, GL_FRAMEBUFFER_COMPLETE,
    glGenFramebuffers, glDeleteFramebuffers, glBindFramebuffer,
    glFramebufferTexture2D, glGenTextures, glDeleteTextures,
    glBindTexture, glTexImage2D, glTexParameteri, glCheckFramebufferStatus,
    check_gl_error, egl_get_proc_address, get_proc_address
)


def _get_hdr_settings():
    return Gio.Settings.new("io.github.rusmikev.CineHDR")


def load_hdr_config():
    try:
        settings = _get_hdr_settings()
        return {
            "hdr_enabled": settings.get_boolean("hdr-enabled"),
            "hdr_target_peak": settings.get_string("hdr-target-peak"),
            "hdr_target_prim": settings.get_string("hdr-target-prim")
        }
    except Exception as e:
        print(f"Error loading HDR config from GSettings: {e}")
    return {
        "hdr_enabled": True,
        "hdr_target_peak": "auto",
        "hdr_target_prim": "auto"
    }


def save_hdr_config(config):
    try:
        settings = _get_hdr_settings()
        if "hdr_enabled" in config:
            settings.set_boolean("hdr-enabled", bool(config["hdr_enabled"]))
        if "hdr_target_peak" in config:
            settings.set_string("hdr-target-peak", str(config["hdr_target_peak"]))
        if "hdr_target_prim" in config:
            settings.set_string("hdr-target-prim", str(config["hdr_target_prim"]))
    except Exception as e:
        print(f"Error saving HDR config to GSettings: {e}")


def load_hdr_setting():
    try:
        settings = _get_hdr_settings()
        return settings.get_boolean("hdr-enabled")
    except Exception:
        return True


def save_hdr_setting(enabled):
    try:
        settings = _get_hdr_settings()
        settings.set_boolean("hdr-enabled", bool(enabled))
    except Exception as e:
        print(f"Error saving hdr-enabled to GSettings: {e}")


def check_hdr_support():
    try:
        if not hasattr(Gdk.ColorState, "get_rec2100_pq"):
            return False
        if not hasattr(Gdk.MemoryFormat, "R16G16B16A16_FLOAT"):
            return False
        display = Gdk.Display.get_default()
        if not display:
            return False
        display_name = getattr(display, "get_name", lambda: "")()
        is_wayland = ("wayland" in str(display_name).lower() or "wayland" in display.__class__.__name__.lower())
        if not is_wayland:
            return False
        # Verify that the display/compositor supports RGBA / color management
        if hasattr(display, "is_composited") and not display.is_composited():
            return False
        if hasattr(display, "is_rgba") and not display.is_rgba():
            return False
        # Check if dmabuf formats are available (indicates modern Wayland buffer sharing and protocol support)
        if hasattr(display, "get_dmabuf_formats"):
            dmabuf = display.get_dmabuf_formats()
            if dmabuf is not None and hasattr(dmabuf, "get_n_formats") and dmabuf.get_n_formats() == 0:
                return False
        return True
    except Exception:
        return False


def is_hdr_content(params):
    if not params or not isinstance(params, dict):
        return False
    gamma = params.get("gamma", "")
    sig_peak = params.get("sig-peak", 1.0)
    try:
        sig_peak = float(sig_peak) if sig_peak is not None else 1.0
    except (ValueError, TypeError):
        sig_peak = 1.0
    return (gamma in ("pq", "hlg", "st2084", "slog", "slog2", "slog3")) or (sig_peak > 1.0)


class GLFramebufferResource:
    """RAII wrapper for OpenGL Framebuffer and 16-bit Float Texture."""
    def __init__(self, width=0, height=0):
        self.texture_id = ctypes.c_uint(0)
        self.fbo_id = ctypes.c_uint(0)
        self.width = width
        self.height = height
        self._initialized = False

    def ensure(self, w, h):
        """Ensure FBO and texture exist and match dimensions w x h without re-generating IDs."""
        if w <= 0 or h <= 0:
            return

        if not self._initialized or self.texture_id.value == 0 or self.fbo_id.value == 0:
            glGenTextures(1, ctypes.byref(self.texture_id))
            check_gl_error("glGenTextures")
            glGenFramebuffers(1, ctypes.byref(self.fbo_id))
            check_gl_error("glGenFramebuffers")
            self._initialized = True

            glBindTexture(GL_TEXTURE_2D, self.texture_id.value)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            check_gl_error("glTexParameteri")

            glTexImage2D(
                GL_TEXTURE_2D, 0, GL_RGBA16F, w, h, 0,
                GL_RGBA, GL_FLOAT, None
            )
            check_gl_error("glTexImage2D initial")

            glBindFramebuffer(GL_FRAMEBUFFER, self.fbo_id.value)
            glFramebufferTexture2D(
                GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D,
                self.texture_id.value, 0
            )
            check_gl_error("glFramebufferTexture2D")

            status = glCheckFramebufferStatus(GL_FRAMEBUFFER)
            if status != GL_FRAMEBUFFER_COMPLETE:
                print(f"Error: Framebuffer is not complete: {hex(status)}")
            glBindFramebuffer(GL_FRAMEBUFFER, 0)
            glBindTexture(GL_TEXTURE_2D, 0)
            self.width = w
            self.height = h
        elif self.width != w or self.height != h:
            # Resize existing texture without deleting/re-creating IDs!
            glBindTexture(GL_TEXTURE_2D, self.texture_id.value)
            glTexImage2D(
                GL_TEXTURE_2D, 0, GL_RGBA16F, w, h, 0,
                GL_RGBA, GL_FLOAT, None
            )
            check_gl_error("glTexImage2D resize")
            glBindTexture(GL_TEXTURE_2D, 0)
            self.width = w
            self.height = h

    def release(self):
        """Free OpenGL resources cleanly."""
        if self.fbo_id and self.fbo_id.value != 0:
            glDeleteFramebuffers(1, ctypes.byref(self.fbo_id))
            self.fbo_id = ctypes.c_uint(0)
        if self.texture_id and self.texture_id.value != 0:
            glDeleteTextures(1, ctypes.byref(self.texture_id))
            self.texture_id = ctypes.c_uint(0)
        self._initialized = False
        self.width = 0
        self.height = 0

    def __del__(self):
        try:
            self.release()
        except Exception:
            pass


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

        self.fbo_resource = GLFramebufferResource()
        self.mpv_ctx = None
        self.current_texture = None

    @property
    def texture_id(self):
        return getattr(self, "fbo_resource", GLFramebufferResource()).texture_id

    @property
    def fbo_id(self):
        return getattr(self, "fbo_resource", GLFramebufferResource()).fbo_id

    @property
    def tex_width(self):
        return getattr(self, "fbo_resource", GLFramebufferResource()).width

    @property
    def tex_height(self):
        return getattr(self, "fbo_resource", GLFramebufferResource()).height

        config = load_hdr_config()
        self._hdr_enabled = config["hdr_enabled"]
        self._hdr_target_peak = config["hdr_target_peak"]
        self._hdr_target_prim = config["hdr_target_prim"]
        self._is_hdr_content = False

        try:
            self._gsettings = _get_hdr_settings()
            self._gsettings.connect("changed::hdr-enabled", self._on_gsettings_changed)
            self._gsettings.connect("changed::hdr-target-prim", self._on_gsettings_changed)
            self._gsettings.connect("changed::hdr-target-peak", self._on_gsettings_changed)
        except Exception:
            self._gsettings = None

        @self.mpv.property_observer("video-params")
        def _on_video_params(_name, params):
            is_hdr = is_hdr_content(params)
            if getattr(self, "_is_hdr_content", None) != is_hdr:
                self._is_hdr_content = is_hdr
                idle_add_once(self.apply_hdr_settings)

        @self.mpv.property_observer("display-hdr")
        @self.mpv.property_observer("target-trc")
        @self.mpv.property_observer("icc-profile")
        def _on_mpv_color_param_changed(_name, _value):
            idle_add_once(self.apply_hdr_settings)

        self.apply_hdr_settings()

    def _on_gsettings_changed(self, settings, key):
        if key == "hdr-enabled":
            self._hdr_enabled = settings.get_boolean("hdr-enabled")
        elif key == "hdr-target-prim":
            self._hdr_target_prim = settings.get_string("hdr-target-prim")
        elif key == "hdr-target-peak":
            self._hdr_target_peak = settings.get_string("hdr-target-peak")
        self.apply_hdr_settings()
        self.queue_draw()

    def apply_hdr_settings(self):
        # Apply tone mapping parameters and target primaries for HDR playback
        try:
            # Check if HDR output is fully supported by GTK and Wayland
            hdr_supported = check_hdr_support()

            # Only apply HDR targets if BOTH user enabled HDR in UI AND content is actually HDR (Risk P-1)
            # AND the system/compositor fully supports Rec2100 PQ signaling
            if self._hdr_enabled and getattr(self, "_is_hdr_content", False) and hdr_supported:
                self.mpv["target-colorspace-hint"] = "yes"
                self.mpv["target-trc"] = "pq"
                self.mpv["target-prim"] = self._hdr_target_prim
                self.mpv["hdr-compute-peak"] = "yes"
                target_peak = self._hdr_target_peak
                if target_peak not in ("auto", "200", "400", "600", "1000", "1600"):
                    target_peak = "auto"

                if target_peak == "auto":
                    self.mpv["target-peak"] = "auto"
                else:
                    self.mpv["target-peak"] = int(float(target_peak))
            else:
                # Safe SDR fallback: do not force PQ tone mapping on SDR content,
                # or if the session does not support Wayland HDR signaling!
                self.mpv["target-colorspace-hint"] = "no"
                self.mpv["target-prim"] = "auto"
                self.mpv["target-peak"] = "auto"
                self.mpv["target-trc"] = "auto"
                self.mpv["hdr-compute-peak"] = "auto"
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

    def _on_unrealize(self, area):
        area.make_current()
        if self.mpv_ctx:
            self.mpv_ctx.update_cb = None
            self.mpv_ctx.free()
            self.mpv_ctx = None

        if getattr(self, "_gsettings", None):
            try:
                self._gsettings.disconnect_by_func(self._on_gsettings_changed)
            except Exception:
                pass
            self._gsettings = None

        self.fbo_resource.release()

    def do_unroot(self):
        # Guarantee unrealize and OpenGL resource cleanup when removed from root/window (Risk P-4)
        if hasattr(self, "gl_area") and self.gl_area and self.gl_area.get_realized():
            self._on_unrealize(self.gl_area)
        Gtk.Widget.do_unroot(self)

    def do_dispose(self):
        # Cleanly unparent child GLArea to prevent GTK reference leaks (Risk P-4)
        if hasattr(self, "gl_area") and self.gl_area and self.gl_area.get_parent() == self:
            self.gl_area.unparent()
        Gtk.Widget.do_dispose(self)

    def setup_fbo(self, w, h):
        self.gl_area.make_current()
        self.fbo_resource.ensure(w, h)

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

        # Recreate texture only if size changed significantly (> 1px) or not initialized (Risk P-5)
        if (
            self.texture_id.value == 0
            or abs(self.tex_width - scaled_w) > 1
            or abs(self.tex_height - scaled_h) > 1
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
            try:
                texture_format = Gdk.MemoryFormat.R16G16B16A16_FLOAT
            except AttributeError:
                texture_format = Gdk.MemoryFormat.B8G8R8A8
            builder.set_format(texture_format)

            # Determine color state (HDR vs SDR)
            try:
                content_hdr = is_hdr_content(self.mpv.video_params)
                if getattr(self, "_is_hdr_content", None) != content_hdr:
                    self._is_hdr_content = content_hdr
                    idle_add_once(self.apply_hdr_settings)
                hdr_supported = check_hdr_support()
                is_hdr = self.hdr_enabled and content_hdr and hdr_supported

                if self.hdr_enabled and content_hdr and not hdr_supported:
                    if not getattr(self, "_hdr_support_warned", False):
                        display_obj = Gdk.Display.get_default()
                        reason = "Gdk.ColorState is not available (requires GTK >= 4.16)"
                        if hasattr(Gdk.ColorState, "get_rec2100_pq"):
                            if display_obj and "Wayland" not in display_obj.__class__.__name__:
                                reason = f"HDR signaling is not supported under {display_obj.__class__.__name__} (requires Wayland)"
                            else:
                                reason = "Wayland compositor does not support HDR/color management"
                        print(f"WARNING: HDR playback is active but target output is unsupported: {reason}. Falling back to SDR tonemapping.")
                        self._hdr_support_warned = True
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
