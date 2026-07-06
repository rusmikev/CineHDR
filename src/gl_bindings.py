# gl_bindings.py
#
# Copyright 2026 rusmikev / Diego Povliuk
# SPDX-License-Identifier: GPL-3.0-or-later

import ctypes

# Load OpenGL libraries and helper
libgl = ctypes.CDLL("libGL.so.1")
libegl = ctypes.CDLL("libEGL.so.1")
egl_get_proc_address = libegl.eglGetProcAddress
egl_get_proc_address.restype = ctypes.c_void_p
egl_get_proc_address.argtypes = [ctypes.c_char_p]


def get_proc_address(name):
    if isinstance(name, str):
        name = name.encode("utf-8")
    return egl_get_proc_address(name)


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
GL_NO_ERROR = 0
GL_FRAMEBUFFER = 0x8D40
GL_COLOR_ATTACHMENT0 = 0x8CE0
GL_TEXTURE_2D = 0xDE1
GL_TEXTURE_MIN_FILTER = 0x2801
GL_TEXTURE_MAG_FILTER = 0x2800
GL_LINEAR = 0x2601
GL_RGBA = 0x1908
GL_FLOAT = 0x1406
GL_RGBA16F = 0x881A
GL_RGBA8 = 0x8058
GL_UNSIGNED_BYTE = 0x1401
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
glGetError = get_gl_func(
    "glGetError", ctypes.c_uint, []
)


def check_gl_error(step=""):
    if not glGetError:
        return
    err = glGetError()
    if err != GL_NO_ERROR:
        print(f"OpenGL error at {step}: 0x{err:04x}")
