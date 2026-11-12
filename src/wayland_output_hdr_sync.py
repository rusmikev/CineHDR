# wayland_output_hdr_sync.py
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
Synchronous blocking Wayland color-management-v1 per-output probe.

Separated from :mod:`wayland_output_hdr` as part of C24 without changing
protocol bind versions, destruction order, error circuit-breaker behaviour,
or tri-state fallback semantics.
"""

import ctypes
import logging
import os
from typing import Any, Dict, List, Optional

from .gtk_cm_policy import CmCaps
from .wayland_cm_probe import _WL_DISPLAY_GET_REGISTRY
from .wayland_cm_protocol import (
    _CB_HU,
    _CB_I8,
    _CB_REG_GLOBAL,
    _CB_U,
    _CB_US,
    _CB_UU,
    _CB_UUU,
    _CB_VOID,
    _CM_OUTPUT_GET_IMAGE_DESCRIPTION,
    _IMG_GET_INFORMATION,
    _MGR_GET_OUTPUT,
    _WL_REGISTRY_BIND,
    OutputHdrInfo,
    _InterfaceTable,
    _make_listener,
    _marshal_destroy,
    classify_hdr,
)


def _resolve_facade(facade: Any) -> Any:
    if facade is not None:
        return facade
    from . import wayland_output_hdr

    return wayland_output_hdr


def probe_outputs(facade: Any = None) -> Optional[Dict[str, OutputHdrInfo]]:
    target = _resolve_facade(facade)
    if target._backend_disabled:
        return None
    try:
        res = target._probe_outputs_unsafe()
        target._backend_error_count = 0
        return res
    except Exception as e:
        target._backend_error_count += 1
        if target._backend_error_count >= 3:
            target._backend_disabled = True
            logging.error(f"wayland_output_hdr: Backend disabled due to repeated protocol/FFI errors: {e}")
        else:
            logging.warning(f"wayland_output_hdr: probe failed: {e}")
        return None


def _probe_outputs_unsafe(facade: Any = None) -> Optional[Dict[str, OutputHdrInfo]]:
    target = _resolve_facade(facade)
    display_ptr = target._get_wl_display_ptr()
    if not display_ptr:
        return None
    lib = target._load_libwayland()
    if lib is None:
        return None
    registry_iface_addr = target._configure_symbols(lib)
    if registry_iface_addr is None:
        return None
    monitors = target._get_monitor_outputs()
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

        temp_cm_caps = CmCaps(
            intents=frozenset(supported_intents),
            features=frozenset(supported_features),
            tfs=frozenset(supported_tfs),
            primaries=frozenset(supported_primaries),
        )

        results: Dict[str, OutputHdrInfo] = {}
        for connector, out_ptr in monitors:
            info = target._query_one_output(lib, display, queue, mgr, table, keep, out_ptr)
            if info is None:
                return None  # a broken dispatch invalidates the whole answer
            info.connector = connector
            results[connector] = info

        target._cache_cm_caps = temp_cm_caps
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
