# wayland_cm_probe.py
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
Direct Wayland registry probe for compositor color-management support.

check_hdr_support() in hdr_detection.py can verify everything on *our* side of
the pipeline (Wayland session, GTK >= 4.16 ColorState API, float texture
formats), but none of that proves the *compositor* can accept a Rec.2100 PQ
surface. GTK does not currently expose "does the compositor speak a color
management protocol" as public API, so without this probe CineHDR happily
tags textures rec2100-pq on e.g. older wlroots compositors, where GTK then
silently down-converts PQ -> sRGB with a plain colorimetric transform — a
visibly worse picture than mpv's proper HDR tone mapping.

This module answers the question directly: it connects a private event queue
to GTK's existing wl_display, enumerates the registry globals, and reports
whether a color-management factory global is present:

  * ``wp_color_manager_v1``  — the ratified color-management-v1 protocol
                               (KWin / Plasma 6.3+, Mutter / GNOME 48+,
                               spoken by GTK 4.18+)
  * ``xx_color_manager_v4``  — the experimental predecessor spoken by
                               GTK 4.16/4.17 and Plasma 6.2

(The frog-color-management protocol is deliberately ignored: GTK never
implemented it, so its presence would not help a GTK client.)

Return contract of :func:`probe_color_management`:

  * ``True``  — registry enumerated, a color-management global is present.
  * ``False`` — registry enumerated, no color-management global. HDR
                pass-through is pointless on this compositor.
  * ``None``  — the probe could not run (not Wayland, no display yet,
                libwayland-client unavailable, unexpected error). Callers
                must treat this as "unknown" and keep their previous
                behaviour.

Thread-safety / re-entrancy notes: the probe uses the documented libwayland
pattern for sharing a connection with a toolkit — a proxy *wrapper* bound to
a private ``wl_event_queue`` — so registry events are dispatched only on our
queue and GTK's own queue is never touched. libwayland-client is thread-safe
for this usage; in practice CineHDR only calls the probe from the GTK main
thread (widget realize, monitor hot-plug, diagnostics), and the single
``wl_display_roundtrip_queue`` blocks only for one compositor round-trip.

The result is cached for the lifetime of the process (registry globals for a
given compositor connection do not come and go for this protocol); the cache
is dropped together with the hdr_detection cache via :func:`invalidate`.
"""

import ctypes
import ctypes.util
import logging
from typing import Optional

# Wayland core: wl_display request opcodes (see wayland.xml).
_WL_DISPLAY_GET_REGISTRY = 1

# Registry globals that let a GTK client negotiate an HDR color state.
CM_GLOBALS = ("wp_color_manager_v1", "xx_color_manager_v4")

_GLOBAL_CB = ctypes.CFUNCTYPE(
    None,
    ctypes.c_void_p,   # void *data
    ctypes.c_void_p,   # struct wl_registry *
    ctypes.c_uint32,   # uint32_t name
    ctypes.c_char_p,   # const char *interface
    ctypes.c_uint32,   # uint32_t version
)
_GLOBAL_REMOVE_CB = ctypes.CFUNCTYPE(
    None,
    ctypes.c_void_p,   # void *data
    ctypes.c_void_p,   # struct wl_registry *
    ctypes.c_uint32,   # uint32_t name
)


class _RegistryListener(ctypes.Structure):
    """Mirrors struct wl_registry_listener (two function pointers)."""

    _fields_ = [
        ("global_", _GLOBAL_CB),
        ("global_remove", _GLOBAL_REMOVE_CB),
    ]


_cached_result: Optional[bool] = None
_cache_valid: bool = False


def invalidate():
    """Drop the cached probe result (paired with invalidate_hdr_support_cache)."""
    global _cached_result, _cache_valid
    _cached_result = None
    _cache_valid = False


def _load_libwayland() -> Optional[ctypes.CDLL]:
    for name in ("libwayland-client.so.0", "libwayland-client.so"):
        try:
            return ctypes.CDLL(name)
        except OSError:
            continue
    found = ctypes.util.find_library("wayland-client")
    if found:
        try:
            return ctypes.CDLL(found)
        except OSError:
            pass
    return None


def _get_wl_display_ptr() -> Optional[int]:
    """Fetch the wl_display* of GTK's default display via utils.get_display_param.

    Imported lazily: utils pulls in GdkWayland/GdkX11 typelibs and libgtk via
    ctypes, and this module must stay importable in minimal test environments.
    """
    try:
        from .utils import get_display_param
    except Exception:
        return None
    try:
        param = get_display_param()
    except Exception:
        return None
    ptr = param.get("wl_display")
    return int(ptr) if ptr else None


def _configure_symbols(lib: ctypes.CDLL) -> Optional[int]:
    """Set ctypes prototypes for every libwayland entry point the probes use.

    Returns the address of the ``wl_registry_interface`` data symbol on
    success, or None when any symbol is missing (the caller must then report
    "unknown"). Shared with wayland_output_hdr, which additionally marshals
    registry binds and protocol requests on custom interfaces.
    """
    try:
        lib.wl_display_create_queue.restype = ctypes.c_void_p
        lib.wl_display_create_queue.argtypes = [ctypes.c_void_p]
        lib.wl_event_queue_destroy.restype = None
        lib.wl_event_queue_destroy.argtypes = [ctypes.c_void_p]
        lib.wl_proxy_create_wrapper.restype = ctypes.c_void_p
        lib.wl_proxy_create_wrapper.argtypes = [ctypes.c_void_p]
        lib.wl_proxy_wrapper_destroy.restype = None
        lib.wl_proxy_wrapper_destroy.argtypes = [ctypes.c_void_p]
        lib.wl_proxy_set_queue.restype = None
        lib.wl_proxy_set_queue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        lib.wl_proxy_add_listener.restype = ctypes.c_int
        lib.wl_proxy_add_listener.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        lib.wl_proxy_destroy.restype = None
        lib.wl_proxy_destroy.argtypes = [ctypes.c_void_p]
        lib.wl_display_roundtrip_queue.restype = ctypes.c_int
        lib.wl_display_roundtrip_queue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        # Variadic — leave argtypes unset and pass explicit ctypes instances.
        lib.wl_proxy_marshal_constructor.restype = ctypes.c_void_p
        lib.wl_proxy_marshal_constructor_versioned.restype = ctypes.c_void_p
        lib.wl_proxy_marshal.restype = None
        # Address of the wl_registry_interface data symbol inside the library.
        return ctypes.addressof(ctypes.c_char.in_dll(lib, "wl_registry_interface"))
    except (AttributeError, ValueError) as e:
        logging.debug(f"wayland_cm_probe: libwayland symbols unavailable: {e}")
        return None


def _enumerate_globals(lib: ctypes.CDLL, display_ptr: int):
    """List registry globals using a private event queue.

    Returns a dict mapping interface name -> (numeric registry name, version)
    so callers can later bind a global, or None when any libwayland entry
    point is missing or errors (the caller then reports "unknown" instead of
    a false negative).
    """
    registry_iface_addr = _configure_symbols(lib)
    if registry_iface_addr is None:
        return None

    display = ctypes.c_void_p(display_ptr)
    queue = None
    wrapper = None
    registry = None
    names = {}

    def _on_global(_data, _registry, name, interface, version):
        if interface:
            try:
                names[interface.decode("utf-8", "replace")] = (int(name), int(version))
            except Exception:
                pass

    def _on_global_remove(_data, _registry, _name):
        pass

    # Keep the CFUNCTYPE thunks referenced for the whole roundtrip: libwayland
    # stores raw pointers, and letting Python collect them mid-dispatch would
    # crash the process.
    listener = _RegistryListener(
        _GLOBAL_CB(_on_global), _GLOBAL_REMOVE_CB(_on_global_remove)
    )

    try:
        queue = lib.wl_display_create_queue(display)
        if not queue:
            return None
        wrapper = lib.wl_proxy_create_wrapper(display)
        if not wrapper:
            return None
        lib.wl_proxy_set_queue(ctypes.c_void_p(wrapper), ctypes.c_void_p(queue))

        registry = lib.wl_proxy_marshal_constructor(
            ctypes.c_void_p(wrapper),
            ctypes.c_uint32(_WL_DISPLAY_GET_REGISTRY),
            ctypes.c_void_p(registry_iface_addr),
            ctypes.c_void_p(None),  # new_id placeholder in the message signature
        )
        if not registry:
            return None

        if lib.wl_proxy_add_listener(
            ctypes.c_void_p(registry), ctypes.byref(listener), None
        ) != 0:
            return None

        # One compositor round-trip: guarantees every current global has been
        # advertised on our private queue before this returns.
        if lib.wl_display_roundtrip_queue(display, ctypes.c_void_p(queue)) < 0:
            return None
    except Exception as e:
        logging.debug(f"wayland_cm_probe: registry enumeration failed: {e}")
        return None
    finally:
        try:
            if registry:
                lib.wl_proxy_destroy(ctypes.c_void_p(registry))
            if wrapper:
                lib.wl_proxy_wrapper_destroy(ctypes.c_void_p(wrapper))
            if queue:
                lib.wl_event_queue_destroy(ctypes.c_void_p(queue))
        except Exception:
            pass

    return names


def probe_color_management() -> Optional[bool]:
    """Tri-state check: does the Wayland compositor offer color management?

    True / False are definitive answers from the compositor registry;
    None means the probe could not run and callers must not change their
    behaviour based on it. The result is cached; see invalidate().
    """
    global _cached_result, _cache_valid
    if _cache_valid:
        return _cached_result

    result: Optional[bool] = None
    try:
        display_ptr = _get_wl_display_ptr()
        if display_ptr:
            lib = _load_libwayland()
            if lib:
                names = _enumerate_globals(lib, display_ptr)
                if names is not None:
                    result = any(g in names for g in CM_GLOBALS)
                    if result:
                        logging.debug(
                            "wayland_cm_probe: compositor advertises %s",
                            sorted(set(CM_GLOBALS) & set(names)),
                        )
                    else:
                        logging.info(
                            "wayland_cm_probe: compositor advertises no color "
                            "management global (%s); HDR pass-through disabled",
                            ", ".join(CM_GLOBALS),
                        )
    except Exception as e:
        logging.debug(f"wayland_cm_probe: probe failed: {e}")
        result = None

    _cached_result = result
    _cache_valid = True
    return result
