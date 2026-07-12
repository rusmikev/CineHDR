# gl_renderer.py
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
OpenGL Framebuffer and Texture resource management for HDR video rendering.

This module provides RAII-style management of OpenGL Framebuffer Objects (FBOs)
and 16-bit floating-point RGBA textures (GL_RGBA16F) used by libmpv to render frames
before wrapping them into Gdk.GLTexture for GTK 4 presentation.
"""

import ctypes
import logging
from .gl_bindings import (
    GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D,
    GL_TEXTURE_MIN_FILTER, GL_TEXTURE_MAG_FILTER, GL_LINEAR,
    GL_RGBA, GL_FLOAT, GL_RGBA16F, GL_RGBA8, GL_UNSIGNED_BYTE, GL_FRAMEBUFFER_COMPLETE,
    glGenFramebuffers, glDeleteFramebuffers, glBindFramebuffer,
    glFramebufferTexture2D, glGenTextures, glDeleteTextures,
    glBindTexture, glTexImage2D, glTexParameteri, glCheckFramebufferStatus,
    check_gl_error
)

logger = logging.getLogger(__name__)


class GLFramebufferResource:
    """
    RAII wrapper for OpenGL Framebuffer Object (FBO) and Texture (16-bit Float or 8-bit Int).

    Manages the lifecycle of GPU textures and framebuffers to prevent VRAM leaks.
    Optimizes resizing by reusing existing OpenGL texture/FBO handles when possible,
    only issuing glTexImage2D to reallocate GPU storage without handle regeneration.
    """
    def __init__(self, width: int = 0, height: int = 0):
        self.texture_id = ctypes.c_uint(0)
        self.fbo_id = ctypes.c_uint(0)
        self.width = width
        self.height = height
        self.is_float = True
        self._initialized = False

    def ensure(self, w: int, h: int, is_float: bool = True):
        """
        Ensure FBO and texture exist and match dimensions w x h and format without re-generating IDs.

        If uninitialized, generates texture and FBO handles and binds attachment.
        If already initialized but dimensions or format changed, updates texture storage in-place.

        Args:
            w (int): Target width in pixels.
            h (int): Target height in pixels.
            is_float (bool): True for GL_RGBA16F (HDR float), False for GL_RGBA8 (SDR fallback int).
        """
        if w <= 0 or h <= 0:
            return

        internal_format = GL_RGBA16F if is_float else GL_RGBA8
        data_type = GL_FLOAT if is_float else GL_UNSIGNED_BYTE

        if not self._initialized or self.texture_id.value == 0 or self.fbo_id.value == 0:
            glGenTextures(1, ctypes.byref(self.texture_id))
            check_gl_error("glGenTextures")
            glGenFramebuffers(1, ctypes.byref(self.fbo_id))
            check_gl_error("glGenFramebuffers")
            self._initialized = True
            self.is_float = is_float

            glBindTexture(GL_TEXTURE_2D, self.texture_id.value)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            check_gl_error("glTexParameteri")

            glTexImage2D(
                GL_TEXTURE_2D, 0, internal_format, w, h, 0,
                GL_RGBA, data_type, None
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
                logger.error(f"Error: Framebuffer is not complete: {hex(status)}")
                glBindFramebuffer(GL_FRAMEBUFFER, 0)
                glBindTexture(GL_TEXTURE_2D, 0)
                self.release()
                return

            glBindFramebuffer(GL_FRAMEBUFFER, 0)
            glBindTexture(GL_TEXTURE_2D, 0)
            self.width = w
            self.height = h
        elif self.width != w or self.height != h or self.is_float != is_float:
            # Resize or reformat existing texture without deleting/re-creating IDs!
            self.is_float = is_float
            glBindTexture(GL_TEXTURE_2D, self.texture_id.value)
            glTexImage2D(
                GL_TEXTURE_2D, 0, internal_format, w, h, 0,
                GL_RGBA, data_type, None
            )
            check_gl_error("glTexImage2D resize/reformat")
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


class FramebufferSlot:
    """A single slot in the OpenGL framebuffer ring pool."""
    def __init__(self, index: int):
        self.index = index
        self.resource = GLFramebufferResource()
        self.in_use = False
        self.fence = None


class GLFramebufferPool:
    """
    Ring buffer pool of OpenGL Framebuffer resources with GL fence synchronization.

    Prevents GTK compositor / GPU rendering race conditions (tearing/stuttering) by ensuring
    libmpv only renders into buffers that are not currently being sampled by GTK.
    """
    def __init__(self, size: int = 3):
        self.size = size
        self.slots = [FramebufferSlot(i) for i in range(size)]
        self.dropped_frames = 0

    def acquire(self, w: int, h: int, is_float: bool = True) -> FramebufferSlot | None:
        """Acquire an available buffer slot from the ring pool."""
        from .gl_bindings import glDeleteSync
        for slot in self.slots:
            if not slot.in_use:
                slot.in_use = True
                if slot.fence and glDeleteSync:
                    try:
                        glDeleteSync(slot.fence)
                    except Exception:
                        pass
                    slot.fence = None
                slot.resource.ensure(w, h, is_float=is_float)
                if not slot.resource._initialized or slot.resource.fbo_id.value == 0:
                    slot.in_use = False
                    return None
                return slot

        # Pool exhausted: all buffers are currently held by GTK compositor
        self.dropped_frames += 1
        return None

    def release_buffer(self, slot: FramebufferSlot):
        """Release a buffer slot back to the pool."""
        slot.in_use = False

    def release_all(self):
        """Free all OpenGL resources and sync objects across all slots."""
        from .gl_bindings import glDeleteSync
        for slot in self.slots:
            if slot.fence and glDeleteSync:
                try:
                    glDeleteSync(slot.fence)
                except Exception:
                    pass
                slot.fence = None
            slot.resource.release()
            slot.in_use = False


