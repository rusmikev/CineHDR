# wayland_output_hdr.py
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
Per-output HDR state via the color-management-v1 protocol.

wayland_cm_probe.py answers "does the compositor *speak* color management?".
This module answers the follow-up question that decides picture quality:
"is the monitor actually *in* HDR mode right now?". A capable compositor
with monitor HDR switched off still converts a Rec.2100 PQ surface to SDR
itself — a plain colorimetric transform that looks worse than mpv's tone
mapping. Auto mode must therefore prefer mpv tone mapping until the output
really is HDR.

Mechanism (all on GTK's existing ``wl_display``, private event queue, same
connection-sharing pattern as wayland_cm_probe):

  1. enumerate the registry, bind ``wp_color_manager_v1`` (a no-op listener
     swallows its ``supported_*``/``done`` capability burst);
  2. for every ``GdkMonitor``, obtain GTK's own ``wl_output`` proxy via
     ``gdk_wayland_monitor_get_wl_output()`` and request
     ``get_output`` -> ``wp_color_management_output_v1``;
  3. ``get_image_description`` -> wait for ``ready`` (or ``failed``);
  4. ``get_information`` -> collect ``tf_named`` / ``primaries_named`` /
     ``luminances`` until ``done``.

Because libwayland-client dispatches events for foreign interfaces through
libffi using the ``wl_interface``/``wl_message`` tables, those tables for the
four ``wp_*`` interfaces are constructed here in ctypes. Only the request
opcodes this module actually marshals are declared (libwayland validates
``opcode < method_count`` and nothing else on the client side); event tables
are complete and exact — a wrong event signature would corrupt the dispatch.

Classification of one output (:func:`classify_hdr`):

  * transfer function ``st2084_pq`` or ``hlg``  -> HDR;
  * otherwise, ``luminances`` with ``max_lum > reference_lum`` -> HDR
    (covers a hypothetical extended-linear HDR description);
  * otherwise -> SDR.

Return contract of :func:`probe_outputs`: a dict keyed by connector name
(``DP-1``...) on success, or ``None`` whenever anything at all prevents a
definitive answer (not Wayland, no color management, libwayland or libgtk
symbols missing, dispatch failure). Callers must treat ``None`` as unknown
and change nothing — the same tri-state discipline as wayland_cm_probe.

The result is cached for a short TTL (:data:`CACHE_TTL_SECONDS`): the state
*does* change at runtime when the user toggles monitor HDR in system
settings, ``HdrController.is_hdr_active`` is evaluated per rendered frame,
and one probe costs three compositor round-trips per output. A persistent
``image_description_changed`` listener integrated into the GLib main loop is
the follow-up planned in docs/ROADMAP.md.
"""

import ctypes
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple
from gi.repository import GLib

from . import wayland_cm_probe
from .wayland_cm_probe import (
    _configure_symbols,
    _get_wl_display_ptr,
    _load_libwayland,
    _WL_DISPLAY_GET_REGISTRY,
)

from .gtk_cm_policy import CmCaps

# wp_color_manager_v1 enum transfer_function (color-management-v1.xml)
TF_BT1886 = 1
TF_GAMMA22 = 2
TF_EXT_LINEAR = 5
TF_SRGB = 9
TF_ST2084_PQ = 11
TF_HLG = 13
TF_COMPOUND_POWER_2_4 = 14

# wp_color_manager_v1 enum primaries
PRIMARIES_SRGB = 1
PRIMARIES_BT2020 = 6

# Request opcodes actually marshalled by this module.
_WL_REGISTRY_BIND = 0
_MGR_GET_OUTPUT = 1
_CM_OUTPUT_GET_IMAGE_DESCRIPTION = 1
_IMG_GET_INFORMATION = 1
_DESTROY = 0

CACHE_TTL_SECONDS = 2.0

_TF_NAMES = {
    TF_BT1886: "bt1886",
    TF_GAMMA22: "gamma22",
    3: "gamma28",
    4: "st240",
    TF_EXT_LINEAR: "ext_linear",
    6: "log_100",
    7: "log_316",
    8: "xvycc",
    TF_SRGB: "srgb",
    10: "ext_srgb",
    TF_ST2084_PQ: "st2084_pq",
    12: "st428",
    TF_HLG: "hlg",
    TF_COMPOUND_POWER_2_4: "compound_power_2_4",
}


@dataclass
class OutputHdrInfo:
    """Distilled image description of one Wayland output."""

    connector: str
    hdr: bool
    tf: Optional[int] = None
    primaries: Optional[int] = None
    min_lum: Optional[float] = None       # cd/m² (protocol sends 1e-4 cd/m² units)
    max_lum: Optional[float] = None       # cd/m²
    reference_lum: Optional[float] = None  # cd/m²
    target_max_lum: Optional[float] = None  # cd/m²
    target_max_cll: Optional[float] = None  # cd/m²

    @property
    def tf_name(self) -> str:
        return _TF_NAMES.get(self.tf, f"unknown({self.tf})") if self.tf is not None else "unset"


def classify_hdr(
    tf: Optional[int],
    min_lum: Optional[float],
    max_lum: Optional[float],
    reference_lum: Optional[float],
) -> bool:
    """Decide whether an output image description denotes an HDR mode.

    Pure function, unit-tested directly. PQ/HLG transfer functions are HDR by
    definition; otherwise a luminance range whose peak exceeds the reference
    white marks an HDR (extended-range) description. Everything else — and
    everything unknown — is SDR: the conservative answer, because treating an
    SDR output as HDR is the failure mode this module exists to prevent.
    """
    if tf in (TF_ST2084_PQ, TF_HLG):
        return True
    if (
        max_lum is not None
        and reference_lum is not None
        and reference_lum > 0
        and max_lum > reference_lum
    ):
        return True
    return False


# ──────────────────────────────────────────────────────────────
# wl_interface / wl_message tables (ctypes)
# ──────────────────────────────────────────────────────────────

class _WlMessage(ctypes.Structure):
    _fields_ = [
        ("name", ctypes.c_char_p),
        ("signature", ctypes.c_char_p),
        ("types", ctypes.POINTER(ctypes.c_void_p)),
    ]


class _WlInterface(ctypes.Structure):
    _fields_ = [
        ("name", ctypes.c_char_p),
        ("version", ctypes.c_int),
        ("method_count", ctypes.c_int),
        ("methods", ctypes.POINTER(_WlMessage)),
        ("event_count", ctypes.c_int),
        ("events", ctypes.POINTER(_WlMessage)),
    ]


def _sig_arg_count(signature: bytes) -> int:
    return sum(1 for ch in signature.decode() if ch in "uifsonah")


class _InterfaceTable:
    """Builds and owns the ctypes wl_interface graph.

    Every nested object (name buffers, types arrays, message arrays) is
    pinned on ``self`` — libwayland keeps raw pointers into these for the
    lifetime of the proxies, so letting Python collect them would crash.
    """

    def __init__(self, wl_output_iface_addr: int):
        self._keep: List[Any] = []
        self.mgr = _WlInterface()
        self.cm_output = _WlInterface()
        self.img = _WlInterface()
        self.info = _WlInterface()

        mgr_p = ctypes.addressof(self.mgr)
        cm_output_p = ctypes.addressof(self.cm_output)
        img_p = ctypes.addressof(self.img)
        info_p = ctypes.addressof(self.info)

        # Truncated request tables: only opcodes we marshal need to exist.
        self._fill(
            self.mgr,
            b"wp_color_manager_v1",
            methods=[
                (b"destroy", b"", []),
                (b"get_output", b"no", [cm_output_p, wl_output_iface_addr]),
            ],
            events=[
                (b"supported_intent", b"u", [None]),
                (b"supported_feature", b"u", [None]),
                (b"supported_tf_named", b"u", [None]),
                (b"supported_primaries_named", b"u", [None]),
                (b"done", b"", []),
            ],
        )
        self._fill(
            self.cm_output,
            b"wp_color_management_output_v1",
            methods=[
                (b"destroy", b"", []),
                (b"get_image_description", b"n", [img_p]),
            ],
            events=[(b"image_description_changed", b"", [])],
        )
        self._fill(
            self.img,
            b"wp_image_description_v1",
            methods=[
                (b"destroy", b"", []),
                (b"get_information", b"n", [info_p]),
            ],
            events=[
                (b"failed", b"us", [None, None]),
                (b"ready", b"u", [None]),
            ],
        )
        self._fill(
            self.info,
            b"wp_image_description_info_v1",
            methods=[],
            events=[
                (b"done", b"", []),
                (b"icc_file", b"hu", [None, None]),
                (b"primaries", b"iiiiiiii", [None] * 8),
                (b"primaries_named", b"u", [None]),
                (b"tf_power", b"u", [None]),
                (b"tf_named", b"u", [None]),
                (b"luminances", b"uuu", [None] * 3),
                (b"target_primaries", b"iiiiiiii", [None] * 8),
                (b"target_luminance", b"uu", [None, None]),
                (b"target_max_cll", b"u", [None]),
                (b"target_max_fall", b"u", [None]),
            ],
        )

    def _messages(self, entries) -> ctypes.POINTER(_WlMessage):
        arr = (_WlMessage * max(len(entries), 1))()
        for i, (name, sig, types) in enumerate(entries):
            n = _sig_arg_count(sig)
            assert n == len(types), (name, sig, types)
            tarr = (ctypes.c_void_p * max(n, 1))()
            for j, t in enumerate(types):
                tarr[j] = ctypes.c_void_p(t) if t else None
            self._keep.append(tarr)
            arr[i].name = name
            arr[i].signature = sig
            arr[i].types = ctypes.cast(tarr, ctypes.POINTER(ctypes.c_void_p))
        self._keep.append(arr)
        return ctypes.cast(arr, ctypes.POINTER(_WlMessage))

    def _fill(self, iface: _WlInterface, name: bytes, methods, events):
        iface.name = name
        iface.version = 1
        iface.method_count = len(methods)
        iface.methods = self._messages(methods)
        iface.event_count = len(events)
        iface.events = self._messages(events)


# Event handler prototypes matching the signatures above. libwayland-client
# invokes listener slots through libffi with exactly these C prototypes.
_CB_VOID = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)
_CB_U = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32)
_CB_US = ctypes.CFUNCTYPE(
    None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p
)
_CB_HU = ctypes.CFUNCTYPE(
    None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int32, ctypes.c_uint32
)
_CB_I8 = ctypes.CFUNCTYPE(
    None, ctypes.c_void_p, ctypes.c_void_p, *([ctypes.c_int32] * 8)
)
_CB_UU = ctypes.CFUNCTYPE(
    None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32
)
_CB_UUU = ctypes.CFUNCTYPE(
    None, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
)


def _make_listener(keep: List[Any], funcs: List[Any]) -> ctypes.Array:
    """Pack CFUNCTYPE thunks into the void(**)() array libwayland expects."""
    arr = (ctypes.c_void_p * len(funcs))(
        *[ctypes.cast(f, ctypes.c_void_p) for f in funcs]
    )
    keep.extend(funcs)
    keep.append(arr)
    return arr


# ──────────────────────────────────────────────────────────────
# GTK side: monitors and their wl_output proxies
# ──────────────────────────────────────────────────────────────

def _get_monitor_outputs() -> Optional[List[Any]]:
    """Return [(connector, wl_output_ptr)] for every GdkMonitor, or None.

    Reuses GTK's own wl_output proxies (gdk_wayland_monitor_get_wl_output):
    passing a foreign-queue proxy as a request *argument* is the standard
    embedding pattern (identical to handing GTK's wl_surface to mpv/EGL) and
    never touches GTK's event queue.
    """
    try:
        import gi
        gi.require_version("Gdk", "4.0")
        from gi.repository import Gdk
        from .utils import gtk as libgtk
    except Exception:
        return None
    try:
        display = Gdk.Display.get_default()
        if display is None or "wayland" not in display.__class__.__name__.lower():
            return None
        libgtk.gdk_wayland_monitor_get_wl_output.restype = ctypes.c_void_p
        libgtk.gdk_wayland_monitor_get_wl_output.argtypes = [ctypes.c_void_p]
        ctypes.pythonapi.PyCapsule_GetPointer.restype = ctypes.c_void_p
        ctypes.pythonapi.PyCapsule_GetPointer.argtypes = (ctypes.py_object,)

        result = []
        monitors = display.get_monitors()
        for i in range(monitors.get_n_items()):
            monitor = monitors.get_item(i)
            if monitor is None:
                continue
            try:
                gptr = ctypes.pythonapi.PyCapsule_GetPointer(monitor.__gpointer__, None)
                out_ptr = libgtk.gdk_wayland_monitor_get_wl_output(gptr)
            except Exception:
                continue
            if not out_ptr:
                continue
            connector = None
            try:
                connector = monitor.get_connector()
            except Exception:
                pass
            if not connector:
                connector = f"output-{i}"
            result.append((str(connector), int(out_ptr)))
        return result or None
    except Exception as e:
        logging.debug(f"wayland_output_hdr: monitor enumeration failed: {e}")
        return None


# ──────────────────────────────────────────────────────────────
# The probe
# ──────────────────────────────────────────────────────────────


_backend_disabled = False
_backend_error_count = 0

def probe_outputs() -> Optional[Dict[str, OutputHdrInfo]]:
    global _backend_disabled, _backend_error_count
    if _backend_disabled:
        return None
    try:
        res = _probe_outputs_unsafe()
        _backend_error_count = 0
        return res
    except Exception as e:
        _backend_error_count += 1
        if _backend_error_count >= 3:
            _backend_disabled = True
            logging.error(f"wayland_output_hdr: Backend disabled due to repeated protocol/FFI errors: {e}")
        else:
            logging.warning(f"wayland_output_hdr: probe failed: {e}")
        return None


def _probe_outputs_unsafe() -> Optional[Dict[str, OutputHdrInfo]]:
    display_ptr = _get_wl_display_ptr()
    if not display_ptr:
        return None
    lib = _load_libwayland()
    if lib is None:
        return None
    registry_iface_addr = _configure_symbols(lib)
    if registry_iface_addr is None:
        return None
    monitors = _get_monitor_outputs()
    if not monitors:
        return None

    try:
        wl_output_iface_addr = ctypes.addressof(
            ctypes.c_char.in_dll(lib, "wl_output_interface")
        )
    except (AttributeError, ValueError):
        return None

    table = _InterfaceTable(wl_output_iface_addr)
    keep: List[Any] = [table]
    display = ctypes.c_void_p(display_ptr)
    queue = wrapper = registry = mgr = None
    globals_found: Dict[str, Any] = {}

    def _on_global(_d, _r, name, interface, version):
        if interface:
            try:
                globals_found[interface.decode("utf-8", "replace")] = (
                    int(name),
                    int(version),
                )
            except Exception:
                pass

    # wl_registry events: global "usu", global_remove "u"
    _CB_REG_GLOBAL = ctypes.CFUNCTYPE(
        None, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32,
    )
    reg_listener = _make_listener(
        keep,
        [
            _CB_REG_GLOBAL(_on_global),
            _CB_U(lambda *_a: None),  # global_remove
        ],
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
            ctypes.c_void_p(None),
        )
        if not registry:
            return None
        if lib.wl_proxy_add_listener(
            ctypes.c_void_p(registry), reg_listener, None
        ) != 0:
            return None
        if lib.wl_display_roundtrip_queue(display, ctypes.c_void_p(queue)) < 0:
            return None

        cm = globals_found.get("wp_color_manager_v1")
        if not cm:
            # xx_color_manager_v4 has diverging event tables — out of scope
            # here; the plain capability probe still recognises it.
            return None
        cm_numeric_name, cm_version = cm

        # bind: wl_registry.bind(name, interface_str, version, new_id)
        mgr = lib.wl_proxy_marshal_constructor_versioned(
            ctypes.c_void_p(registry),
            ctypes.c_uint32(_WL_REGISTRY_BIND),
            ctypes.byref(table.mgr),
            ctypes.c_uint32(1),
            ctypes.c_uint32(cm_numeric_name),
            ctypes.c_char_p(b"wp_color_manager_v1"),
            ctypes.c_uint32(1),
            ctypes.c_void_p(None),
        )
        if not mgr:
            return None

        supported_intents = set()
        supported_features = set()
        supported_tfs = set()
        supported_primaries = set()

        def _on_intent(_d, _p, val): supported_intents.add(int(val))
        def _on_feature(_d, _p, val): supported_features.add(int(val))
        def _on_tf_named(_d, _p, tf): supported_tfs.add(int(tf))
        def _on_primaries_named(_d, _p, p): supported_primaries.add(int(p))

        mgr_listener = _make_listener(
            keep,
            [
                _CB_U(_on_intent),
                _CB_U(_on_feature),
                _CB_U(_on_tf_named),
                _CB_U(_on_primaries_named),
                _CB_VOID(lambda *_a: None),
            ],
        )
        if lib.wl_proxy_add_listener(ctypes.c_void_p(mgr), mgr_listener, None) != 0:
            return None

        # Roundtrip to get the supported capabilities
        if lib.wl_display_roundtrip_queue(display, ctypes.c_void_p(queue)) < 0:
            return None

        from .gtk_cm_policy import CmCaps
        temp_cm_caps = CmCaps(
            intents=frozenset(supported_intents),
            features=frozenset(supported_features),
            tfs=frozenset(supported_tfs),
            primaries=frozenset(supported_primaries)
        )

        results: Dict[str, OutputHdrInfo] = {}
        for connector, out_ptr in monitors:
            info = _query_one_output(lib, display, queue, mgr, table, keep, out_ptr)
            if info is None:
                return None  # a broken dispatch invalidates the whole answer
            info.connector = connector
            results[connector] = info

        global _cache_cm_caps
        _cache_cm_caps = temp_cm_caps
        return results
    finally:
        try:
            if mgr:
                _marshal_destroy(lib, mgr)
            if registry:
                lib.wl_proxy_destroy(ctypes.c_void_p(registry))
            if wrapper:
                lib.wl_proxy_wrapper_destroy(ctypes.c_void_p(wrapper))
            if queue:
                lib.wl_event_queue_destroy(ctypes.c_void_p(queue))
        except Exception:
            pass


def _marshal_destroy(lib: ctypes.CDLL, proxy: int):
    """Send request 0 (destroy "") and drop the client-side proxy."""
    try:
        lib.wl_proxy_marshal(ctypes.c_void_p(proxy), ctypes.c_uint32(_DESTROY))
    except Exception:
        pass
    try:
        lib.wl_proxy_destroy(ctypes.c_void_p(proxy))
    except Exception:
        pass


def _query_one_output(lib, display, queue, mgr, table, keep, out_ptr) -> Optional[OutputHdrInfo]:
    state = {
        "ready": False,
        "failed": False,
        "done": False,
        "tf": None,
        "primaries": None,
        "min": None,
        "max": None,
        "ref": None,
    }

    cm_out = img = info_obj = None
    try:
        cm_out = lib.wl_proxy_marshal_constructor(
            ctypes.c_void_p(mgr),
            ctypes.c_uint32(_MGR_GET_OUTPUT),
            ctypes.byref(table.cm_output),
            ctypes.c_void_p(None),          # 'n' new_id placeholder
            ctypes.c_void_p(out_ptr),       # 'o' wl_output
        )
        if not cm_out:
            return None
        img = lib.wl_proxy_marshal_constructor(
            ctypes.c_void_p(cm_out),
            ctypes.c_uint32(_CM_OUTPUT_GET_IMAGE_DESCRIPTION),
            ctypes.byref(table.img),
            ctypes.c_void_p(None),
        )
        if not img:
            return None

        def _on_failed(_d, _p, cause, msg):
            state["failed"] = True
            logging.debug(
                "wayland_output_hdr: image description failed (cause=%s, %s)",
                cause, msg,
            )

        def _on_ready(_d, _p, _identity):
            state["ready"] = True

        img_listener = _make_listener(keep, [_CB_US(_on_failed), _CB_U(_on_ready)])
        if lib.wl_proxy_add_listener(ctypes.c_void_p(img), img_listener, None) != 0:
            return None
        if lib.wl_display_roundtrip_queue(display, ctypes.c_void_p(queue)) < 0:
            return None
        if state["failed"] or not state["ready"]:
            # No description for this output (protocol allows it) — report
            # the conservative SDR answer rather than aborting the probe.
            return OutputHdrInfo(connector="", hdr=False)

        info_obj = lib.wl_proxy_marshal_constructor(
            ctypes.c_void_p(img),
            ctypes.c_uint32(_IMG_GET_INFORMATION),
            ctypes.byref(table.info),
            ctypes.c_void_p(None),
        )
        if not info_obj:
            return None

        def _on_done(_d, _p):
            state["done"] = True

        def _on_icc(_d, _p, fd, _size):
            try:
                if fd >= 0:
                    os.close(fd)  # protocol transfers ownership — never leak it
            except OSError:
                pass

        def _on_tf_named(_d, _p, tf):
            state["tf"] = int(tf)

        def _on_primaries_named(_d, _p, p):
            state["primaries"] = int(p)

        def _on_luminances(_d, _p, mn, mx, ref):
            state["min"] = mn / 10000.0
            state["max"] = float(mx)
            state["ref"] = float(ref)

        noop_u = _CB_U(lambda *_a: None)
        noop_i8 = _CB_I8(lambda *_a: None)

        def _on_target_luminance(_d, _p, mn, mx):
            if mx:
                state["target_max"] = float(mx)

        def _on_target_max_cll(_d, _p, cll):
            if cll:
                state["target_cll"] = float(cll)

        info_listener = _make_listener(
            keep,
            [
                _CB_VOID(_on_done),          # 0 done
                _CB_HU(_on_icc),             # 1 icc_file
                noop_i8,                     # 2 primaries (raw CIE — unused)
                _CB_U(_on_primaries_named),  # 3 primaries_named
                noop_u,                      # 4 tf_power
                _CB_U(_on_tf_named),         # 5 tf_named
                _CB_UUU(_on_luminances),     # 6 luminances
                noop_i8,                     # 7 target_primaries
                _CB_UU(_on_target_luminance),# 8 target_luminance
                _CB_U(_on_target_max_cll),   # 9 target_max_cll
                noop_u,                      # 10 target_max_fall
            ],
        )
        if lib.wl_proxy_add_listener(
            ctypes.c_void_p(info_obj), info_listener, None
        ) != 0:
            return None
        if lib.wl_display_roundtrip_queue(display, ctypes.c_void_p(queue)) < 0:
            return None
        if not state["done"]:
            return None

        panel_max = state["max"]
        return OutputHdrInfo(
            connector="",
            hdr=classify_hdr(state["tf"], state["min"], panel_max, state["ref"]),
            tf=state["tf"],
            primaries=state["primaries"],
            min_lum=state["min"],
            max_lum=panel_max,
            reference_lum=state["ref"],
            target_max_lum=state.get("target_max"),
            target_max_cll=state.get("target_cll"),
        )
    finally:
        if info_obj:
            try:
                lib.wl_proxy_destroy(ctypes.c_void_p(info_obj))
            except Exception:
                pass
        if img:
            _marshal_destroy(lib, img)
        if cm_out:
            _marshal_destroy(lib, cm_out)


# ──────────────────────────────────────────────────────────────
# Non-blocking async probe state machine (GLib.Source)
# ──────────────────────────────────────────────────────────────

_DONE_CB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32)


class _CallbackListener(ctypes.Structure):
    _fields_ = [("done", _DONE_CB)]


_CB_REG_GLOBAL = ctypes.CFUNCTYPE(
    None, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32,
)

STATE_INIT = 0
STATE_WAIT_REGISTRY = 1
STATE_WAIT_DESCRIPTIONS = 2
STATE_WAIT_INFOS = 3
STATE_DONE = 4


class WaylandOutputHdrSource(GLib.Source):
    """Non-blocking GLib.Source driving Wayland output HDR discovery.

    Dispatches on the GLib main loop using wl_display_dispatch_queue_pending()
    and wl_display.sync callbacks, eliminating synchronous roundtrips that freeze UI.
    """

    def __init__(
        self,
        on_complete: Optional[Callable[[Optional[Dict[str, OutputHdrInfo]]], None]] = None,
        timeout_sec: float = 2.0,
    ):
        super().__init__()
        self.on_complete = on_complete
        self.timeout_sec = float(timeout_sec)
        self.t0 = time.monotonic()
        self._cleaned_up = False
        self._done = False
        self._completion_notified = False
        self._sync_done = False
        self.state = STATE_INIT
        self.result: Optional[Dict[str, OutputHdrInfo]] = None
        self.keep: List[Any] = []

        self.display: Optional[int] = None
        self.lib: Optional[ctypes.CDLL] = None
        self.table: Optional[_InterfaceTable] = None
        self.callback_iface_addr: Optional[int] = None
        self.queue = None
        self.wrapper = None
        self.registry = None
        self.mgr = None
        self.sync_cb = None
        self.names: Dict[str, Tuple[int, int]] = {}
        self.monitors: List[Tuple[str, int]] = []
        self.output_states: Dict[str, dict] = {}
        self.temp_cm_caps: Optional[CmCaps] = None
        self._cm_intents: set = set()
        self._cm_features: set = set()
        self._cm_tfs: set = set()
        self._cm_primaries: set = set()

        try:
            display_ptr = _get_wl_display_ptr()
            if not display_ptr:
                self._finish(None)
                return
            self.display = display_ptr
            lib = _load_libwayland()
            if not lib:
                self._finish(None)
                return
            self.lib = lib
            reg_iface = _configure_symbols(lib)
            if not reg_iface:
                self._finish(None)
                return
            wl_output_iface = ctypes.addressof(ctypes.c_char.in_dll(lib, "wl_output_interface"))
            self.callback_iface_addr = ctypes.addressof(ctypes.c_char.in_dll(lib, "wl_callback_interface"))
            self.table = _InterfaceTable(wl_output_iface)
            self.keep.append(self.table)
            self.monitors = _get_monitor_outputs()
            if not self.monitors:
                self._finish({})
                return

            self.queue = lib.wl_display_create_queue(ctypes.c_void_p(self.display))
            if not self.queue:
                self._finish(None)
                return
            self.wrapper = lib.wl_proxy_create_wrapper(ctypes.c_void_p(self.display))
            if not self.wrapper:
                self._finish(None)
                return
            lib.wl_proxy_set_queue(ctypes.c_void_p(self.wrapper), ctypes.c_void_p(self.queue))

            self.registry = lib.wl_proxy_marshal_constructor(
                ctypes.c_void_p(self.wrapper),
                ctypes.c_uint32(_WL_DISPLAY_GET_REGISTRY),
                ctypes.c_void_p(reg_iface),
                ctypes.c_void_p(None),
            )
            if not self.registry:
                self._finish(None)
                return

            def _on_global(_d, _r, name, interface, version):
                if interface:
                    try:
                        self.names[interface.decode("utf-8", "replace")] = (int(name), int(version))
                    except Exception:
                        pass

            reg_listener = _make_listener(
                self.keep,
                [
                    _CB_REG_GLOBAL(_on_global),
                    _CB_U(lambda *_a: None),
                ],
            )
            if lib.wl_proxy_add_listener(ctypes.c_void_p(self.registry), reg_listener, None) != 0:
                self._finish(None)
                return

            self._post_sync()
            self.state = STATE_WAIT_REGISTRY
        except Exception as e:
            logging.debug("wayland_output_hdr: async probe init failed: %s", e)
            self._finish(None)

    def _post_sync(self):
        self._sync_done = False
        if self.sync_cb:
            try:
                self.lib.wl_proxy_destroy(ctypes.c_void_p(self.sync_cb))
            except Exception:
                pass
            self.sync_cb = None

        self.sync_cb = self.lib.wl_proxy_marshal_constructor(
            ctypes.c_void_p(self.wrapper),
            ctypes.c_uint32(0),
            ctypes.c_void_p(self.callback_iface_addr),
            ctypes.c_void_p(None),
        )
        if not self.sync_cb:
            raise RuntimeError("sync constructor returned null")

        def _on_sync(_d, _cb, _serial):
            self._sync_done = True

        cb_listener = _CallbackListener(_DONE_CB(_on_sync))
        self.keep.append(cb_listener)
        if self.lib.wl_proxy_add_listener(ctypes.c_void_p(self.sync_cb), ctypes.byref(cb_listener), None) != 0:
            raise RuntimeError("add sync listener failed")
        self.lib.wl_display_flush(ctypes.c_void_p(self.display))

    def _finish(self, result: Optional[Dict[str, OutputHdrInfo]]):
        self.result = result
        self.state = STATE_DONE
        self._done = True

    def _expire_if_needed(self) -> bool:
        if self._done:
            return True
        if (time.monotonic() - self.t0) < self.timeout_sec:
            return False
        logging.debug(
            "wayland_output_hdr: async probe timed out after %.2fs",
            self.timeout_sec,
        )
        self._finish(None)
        return True

    def _remove_completed_source(self) -> bool:
        """Clean up and deliver at most one guarded completion callback."""
        self.cleanup()
        if self.on_complete and not getattr(self, "_completion_notified", False):
            self._completion_notified = True
            try:
                self.on_complete(self.result)
            except Exception:
                logging.exception("wayland_output_hdr: on_complete callback failed")
        return GLib.SOURCE_REMOVE

    def cancel(self):
        """Cancel the in-flight probe immediately without calling on_complete."""
        self.on_complete = None
        self.state = STATE_DONE
        self._done = True
        self.cleanup()
        try:
            self.destroy()
        except Exception:
            pass

    def prepare(self) -> Tuple[bool, int]:
        if self._expire_if_needed():
            return True, 0
        return False, 10

    def check(self) -> bool:
        if self._expire_if_needed():
            return True
        if self.display and self.queue:
            try:
                self.lib.wl_display_dispatch_queue_pending(
                    ctypes.c_void_p(self.display), ctypes.c_void_p(self.queue)
                )
            except Exception as e:
                logging.debug("wayland_output_hdr: async dispatch failed: %s", e)
                self._finish(None)
                return True
        return self._sync_done or self._done

    def dispatch(self, callback: Any, args: Any) -> bool:
        self._expire_if_needed()
        if self._done:
            return self._remove_completed_source()

        if not self._sync_done:
            return GLib.SOURCE_CONTINUE

        try:
            if self.state == STATE_WAIT_REGISTRY:
                has_compositor_cm = any(g in self.names for g in wayland_cm_probe.CM_GLOBALS)
                wayland_cm_probe._cached_result = has_compositor_cm
                wayland_cm_probe._cache_valid = True

                wp_cm = self.names.get("wp_color_manager_v1")
                if not wp_cm:
                    if "xx_color_manager_v4" in self.names:
                        logging.info(
                            "wayland_output_hdr: xx_color_manager_v4 detected but has diverging "
                            "event tables; per-output HDR profile probe unavailable"
                        )
                        self._finish(None)
                    else:
                        logging.info(
                            "wayland_output_hdr: compositor advertises no color management global"
                        )
                        self._finish({})
                        _update_cache({})
                    return self._remove_completed_source()

                mgr_name, mgr_ver = wp_cm
                bind_version = 1
                self.mgr = self.lib.wl_proxy_marshal_constructor_versioned(
                    ctypes.c_void_p(self.registry),
                    ctypes.c_uint32(_WL_REGISTRY_BIND),
                    ctypes.byref(self.table.mgr),
                    ctypes.c_uint32(bind_version),
                    ctypes.c_uint32(mgr_name),
                    ctypes.c_char_p(b"wp_color_manager_v1"),
                    ctypes.c_uint32(bind_version),
                    ctypes.c_void_p(None),
                )
                if not self.mgr:
                    self._finish(None)
                    return self._remove_completed_source()

                self._cm_intents = set()
                self._cm_features = set()
                self._cm_tfs = set()
                self._cm_primaries = set()

                def _on_intent(_d, _p, val): self._cm_intents.add(int(val))
                def _on_feature(_d, _p, val): self._cm_features.add(int(val))
                def _on_tf_named(_d, _p, tf): self._cm_tfs.add(int(tf))
                def _on_primaries_named(_d, _p, p): self._cm_primaries.add(int(p))

                mgr_listener = _make_listener(
                    self.keep,
                    [
                        _CB_U(_on_intent),
                        _CB_U(_on_feature),
                        _CB_U(_on_tf_named),
                        _CB_U(_on_primaries_named),
                        _CB_VOID(lambda *_a: None),
                    ],
                )
                if self.lib.wl_proxy_add_listener(
                    ctypes.c_void_p(self.mgr), mgr_listener, None
                ) != 0:
                    raise RuntimeError("add color-manager listener failed")

                for connector, out_ptr in self.monitors:
                    cm_out = self.lib.wl_proxy_marshal_constructor(
                        ctypes.c_void_p(self.mgr),
                        ctypes.c_uint32(_MGR_GET_OUTPUT),
                        ctypes.byref(self.table.cm_output),
                        ctypes.c_void_p(None),
                        ctypes.c_void_p(out_ptr),
                    )
                    if not cm_out:
                        continue
                    img = self.lib.wl_proxy_marshal_constructor(
                        ctypes.c_void_p(cm_out),
                        ctypes.c_uint32(_CM_OUTPUT_GET_IMAGE_DESCRIPTION),
                        ctypes.byref(self.table.img),
                        ctypes.c_void_p(None),
                    )
                    if not img:
                        _marshal_destroy(self.lib, cm_out)
                        continue

                    st = {
                        "connector": connector,
                        "cm_out": cm_out,
                        "img": img,
                        "ready": False,
                        "failed": False,
                        "done": False,
                        "info_obj": None,
                        "tf": None,
                        "primaries": None,
                        "min": None,
                        "max": None,
                        "ref": None,
                    }

                    def _mk_failed(s):
                        def _on_failed(_d, _p, _c, _m):
                            s["failed"] = True
                        return _on_failed

                    def _mk_ready(s):
                        def _on_ready(_d, _p, _i):
                            s["ready"] = True
                        return _on_ready

                    img_listener = _make_listener(
                        self.keep,
                        [_CB_US(_mk_failed(st)), _CB_U(_mk_ready(st))],
                    )
                    self.output_states[connector] = st
                    if self.lib.wl_proxy_add_listener(
                        ctypes.c_void_p(img), img_listener, None
                    ) != 0:
                        raise RuntimeError(
                            f"add image-description listener failed for {connector}"
                        )

                if not self.output_states:
                    self._finish({})
                    _update_cache({})
                    return self._remove_completed_source()

                self._post_sync()
                self.state = STATE_WAIT_DESCRIPTIONS
                return GLib.SOURCE_CONTINUE

            elif self.state == STATE_WAIT_DESCRIPTIONS:
                self.temp_cm_caps = CmCaps(
                    intents=frozenset(self._cm_intents),
                    features=frozenset(self._cm_features),
                    tfs=frozenset(self._cm_tfs),
                    primaries=frozenset(self._cm_primaries),
                )
                has_info = False
                for connector, st in self.output_states.items():
                    if st["failed"] or not st["ready"]:
                        st["hdr_info"] = OutputHdrInfo(connector=connector, hdr=False)
                    else:
                        info_obj = self.lib.wl_proxy_marshal_constructor(
                            ctypes.c_void_p(st["img"]),
                            ctypes.c_uint32(_IMG_GET_INFORMATION),
                            ctypes.byref(self.table.info),
                            ctypes.c_void_p(None),
                        )
                        if not info_obj:
                            st["hdr_info"] = OutputHdrInfo(connector=connector, hdr=False)
                        else:
                            st["info_obj"] = info_obj
                            has_info = True

                            def _mk_done(s):
                                def _on_done(_d, _p):
                                    s["done"] = True
                                return _on_done

                            def _mk_icc(s):
                                def _on_icc(_d, _p, fd, _size):
                                    try:
                                        if fd >= 0:
                                            os.close(fd)
                                    except OSError:
                                        pass
                                return _on_icc

                            def _mk_tf(s):
                                def _on_tf(_d, _p, tf):
                                    s["tf"] = int(tf)
                                return _on_tf

                            def _mk_prim(s):
                                def _on_prim(_d, _p, p):
                                    s["primaries"] = int(p)
                                return _on_prim

                            def _mk_lum(s):
                                def _on_lum(_d, _p, mn, mx, ref):
                                    s["min"] = mn / 10000.0
                                    s["max"] = float(mx)
                                    s["ref"] = float(ref)
                                return _on_lum

                            def _mk_target_lum(s):
                                def _on_target_lum(_d, _p, mn, mx):
                                    if mx:
                                        s["target_max"] = float(mx)
                                return _on_target_lum

                            def _mk_target_cll(s):
                                def _on_target_cll(_d, _p, cll):
                                    if cll:
                                        s["target_cll"] = float(cll)
                                return _on_target_cll

                            noop_u = _CB_U(lambda *_a: None)
                            noop_i8 = _CB_I8(lambda *_a: None)
                            info_listener = _make_listener(
                                self.keep,
                                [
                                    _CB_VOID(_mk_done(st)),          # 0 done
                                    _CB_HU(_mk_icc(st)),             # 1 icc_file
                                    noop_i8,                         # 2 primaries
                                    _CB_U(_mk_prim(st)),             # 3 primaries_named
                                    noop_u,                          # 4 tf_power
                                    _CB_U(_mk_tf(st)),               # 5 tf_named
                                    _CB_UUU(_mk_lum(st)),            # 6 luminances
                                    noop_i8,                         # 7 target_primaries
                                    _CB_UU(_mk_target_lum(st)),      # 8 target_luminance
                                    _CB_U(_mk_target_cll(st)),       # 9 target_max_cll
                                    noop_u,                          # 10 target_max_fall
                                ],
                            )
                            if self.lib.wl_proxy_add_listener(
                                ctypes.c_void_p(info_obj), info_listener, None
                            ) != 0:
                                raise RuntimeError(
                                    f"add image-description-info listener failed for {connector}"
                                )

                if not has_info:
                    res = {c: st["hdr_info"] for c, st in self.output_states.items()}
                    self._finish(res)
                    _update_cache(res, self.temp_cm_caps)
                    return self._remove_completed_source()

                self._post_sync()
                self.state = STATE_WAIT_INFOS
                return GLib.SOURCE_CONTINUE

            elif self.state == STATE_WAIT_INFOS:
                for connector, st in self.output_states.items():
                    if "hdr_info" not in st:
                        if st.get("done"):
                            panel_max = st["max"]
                            st["hdr_info"] = OutputHdrInfo(
                                connector=connector,
                                hdr=classify_hdr(st["tf"], st["min"], panel_max, st["ref"]),
                                tf=st["tf"],
                                primaries=st["primaries"],
                                min_lum=st["min"],
                                max_lum=panel_max,
                                reference_lum=st["ref"],
                                target_max_lum=st.get("target_max"),
                                target_max_cll=st.get("target_cll"),
                            )
                        else:
                            st["hdr_info"] = OutputHdrInfo(connector=connector, hdr=False)
                res = {c: st["hdr_info"] for c, st in self.output_states.items()}
                self._finish(res)
                _update_cache(res, self.temp_cm_caps)
                return self._remove_completed_source()

        except Exception as e:
            logging.debug("wayland_output_hdr: async state transition failed: %s", e)
            self._finish(None)
            _update_cache(None)
            return self._remove_completed_source()

    def cleanup(self):
        if self._cleaned_up:
            return
        self._cleaned_up = True
        if self.sync_cb:
            try:
                self.lib.wl_proxy_destroy(ctypes.c_void_p(self.sync_cb))
            except Exception:
                pass
            self.sync_cb = None
        for st in self.output_states.values():
            if st.get("info_obj"):
                try:
                    self.lib.wl_proxy_destroy(ctypes.c_void_p(st["info_obj"]))
                except Exception:
                    pass
            if st.get("img"):
                _marshal_destroy(self.lib, st["img"])
            if st.get("cm_out"):
                _marshal_destroy(self.lib, st["cm_out"])
        self.output_states.clear()
        if self.mgr:
            _marshal_destroy(self.lib, self.mgr)
            self.mgr = None
        if self.registry:
            try:
                self.lib.wl_proxy_destroy(ctypes.c_void_p(self.registry))
            except Exception:
                pass
            self.registry = None
        if self.wrapper:
            try:
                self.lib.wl_proxy_wrapper_destroy(ctypes.c_void_p(self.wrapper))
            except Exception:
                pass
            self.wrapper = None
        if self.queue:
            try:
                self.lib.wl_event_queue_destroy(ctypes.c_void_p(self.queue))
            except Exception:
                pass
            self.queue = None
        self.keep.clear()

    def __del__(self):
        try:
            self.cleanup()
        except Exception:
            pass
        try:
            super().__del__()
        except Exception:
            pass


# ──────────────────────────────────────────────────────────────
# TTL cache + public accessor
# ──────────────────────────────────────────────────────────────

_cache_value: Optional[Dict[str, OutputHdrInfo]] = None
_cache_cm_caps: Optional[CmCaps] = None
_cache_time: float = 0.0
_cache_valid: bool = False


def _update_cache(results: Optional[Dict[str, OutputHdrInfo]], cm_caps: Optional[CmCaps] = None):
    global _cache_value, _cache_cm_caps, _cache_time, _cache_valid
    _cache_value = results
    if results is None:
        _cache_cm_caps = None
    elif cm_caps is not None:
        _cache_cm_caps = cm_caps
    _cache_time = time.monotonic()
    _cache_valid = True
    try:
        from . import hdr_detection
        hdr_detection._cached_support = None
    except Exception:
        pass


def invalidate(invalidate_caps: bool = True):
    global _cache_value, _cache_cm_caps, _cache_time, _cache_valid
    _cache_value = None
    if invalidate_caps:
        _cache_cm_caps = None
    _cache_time = 0.0
    _cache_valid = False


def get_cm_caps(allow_probe: bool = False) -> Optional[CmCaps]:
    global _cache_cm_caps
    if _cache_cm_caps is not None:
        return _cache_cm_caps
    if allow_probe:
        refresh_output_hdr_states()
    return _cache_cm_caps


def refresh_output_hdr_states() -> Optional[Dict[str, OutputHdrInfo]]:
    """Explicitly perform probe_outputs() and update the cache.

    Must be called from background timers or lifecycle event handlers,
    never directly from the per-frame render path.
    """
    global _cache_value, _cache_cm_caps, _cache_time, _cache_valid
    val = probe_outputs()
    _cache_value = val
    if val is None:
        _cache_cm_caps = None
    _cache_time = time.monotonic()
    _cache_valid = True
    return _cache_value


def async_refresh_output_hdr_states(
    on_complete: Optional[Callable[[Optional[Dict[str, OutputHdrInfo]]], None]] = None,
    timeout_sec: float = 2.0,
) -> Optional[GLib.Source]:
    """Asynchronously probe outputs using a non-blocking GLib.Source state machine.

    Never blocks the main thread with wl_display_roundtrip_queue. Updates the cache
    and invokes on_complete(results) upon completion.
    """
    try:
        source = WaylandOutputHdrSource(on_complete=on_complete, timeout_sec=timeout_sec)
        source.attach(None)
        return source
    except Exception as e:
        logging.debug("wayland_output_hdr: failed to create async probe source: %s", e)
        if on_complete:
            try:
                on_complete(None)
            except Exception:
                pass
        return None


def get_output_hdr_states(allow_probe: bool = False) -> Optional[Dict[str, OutputHdrInfo]]:
    """Return output HDR states from the cache.

    If allow_probe is True and the cache is expired/uninitialized, probe_outputs()
    is executed. If allow_probe is False (default, for hot render paths), only the
    existing cached value is returned without performing any blocking Wayland roundtrips.
    """
    global _cache_value, _cache_time, _cache_valid
    now = time.monotonic()
    if _cache_valid and (now - _cache_time) < CACHE_TTL_SECONDS:
        return _cache_value
    if allow_probe:
        return refresh_output_hdr_states()
    return _cache_value if _cache_valid else None


def get_monitor_hdr_state(
    connector: Optional[str] = None, allow_probe: bool = False
) -> Optional[bool]:
    """Tri-state HDR answer for one connector (or the aggregate).

    * connector known in the probe result -> that output's state;
    * no connector hint -> True if *any* output is HDR (never block a window
      that might be on the HDR screen), False only when every output is SDR;
    * probe unavailable -> None (callers change nothing).
    """
    states = get_output_hdr_states(allow_probe=allow_probe)
    if states is None:
        return None
    if connector and connector in states:
        return states[connector].hdr
    if not states:
        return None
    return any(info.hdr for info in states.values())
