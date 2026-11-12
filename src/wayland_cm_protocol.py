# wayland_cm_protocol.py
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
ctypes Wayland color-management-v1 protocol tables, output model, and monitor helpers.

Separated from :mod:`wayland_output_hdr` as part of C24 without changing
protocol versions, event/method tables, callback signatures, or lifetime pins.
"""

import ctypes
from dataclasses import dataclass
import logging
import os
from typing import Any, List, Optional

import gi

gi.require_version("Gdk", "4.0")
from gi.repository import Gdk

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
_DONE_CB = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32)


class _CallbackListener(ctypes.Structure):
    _fields_ = [("done", _DONE_CB)]


_CB_REG_GLOBAL = ctypes.CFUNCTYPE(
    None, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32,
)


def _make_listener(keep: List[Any], funcs: List[Any]) -> ctypes.Array:
    """Pack CFUNCTYPE thunks into the void(**)() array libwayland expects."""
    arr = (ctypes.c_void_p * len(funcs))(
        *[ctypes.cast(f, ctypes.c_void_p) for f in funcs]
    )
    keep.extend(funcs)
    keep.append(arr)
    return arr


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


def _make_info_listener(keep: List[Any], state: dict) -> ctypes.Array:
    """Build and pin the 11-slot wp_image_description_info_v1 listener array."""

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

    def _on_target_luminance(_d, _p, mn, mx):
        if mx:
            state["target_max"] = float(mx)

    def _on_target_max_cll(_d, _p, cll):
        if cll:
            state["target_cll"] = float(cll)

    noop_u = _CB_U(lambda *_a: None)
    noop_i8 = _CB_I8(lambda *_a: None)
    return _make_listener(
        keep,
        [
            _CB_VOID(_on_done),           # 0 done
            _CB_HU(_on_icc),              # 1 icc_file
            noop_i8,                      # 2 primaries (raw CIE — unused)
            _CB_U(_on_primaries_named),   # 3 primaries_named
            noop_u,                       # 4 tf_power
            _CB_U(_on_tf_named),          # 5 tf_named
            _CB_UUU(_on_luminances),      # 6 luminances
            noop_i8,                      # 7 target_primaries
            _CB_UU(_on_target_luminance), # 8 target_luminance
            _CB_U(_on_target_max_cll),    # 9 target_max_cll
            noop_u,                       # 10 target_max_fall
        ],
    )


def _info_from_state(connector: str, state: dict) -> OutputHdrInfo:
    """Construct OutputHdrInfo from accumulated image description info events."""
    panel_max = state["max"]
    return OutputHdrInfo(
        connector=connector,
        hdr=classify_hdr(state["tf"], state["min"], panel_max, state["ref"]),
        tf=state["tf"],
        primaries=state["primaries"],
        min_lum=state["min"],
        max_lum=panel_max,
        reference_lum=state["ref"],
        target_max_lum=state.get("target_max"),
        target_max_cll=state.get("target_cll"),
    )


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
