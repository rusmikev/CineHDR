# wayland_output_hdr_async.py
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
Non-blocking GLib.Source state machine for Wayland output HDR discovery.

Separated from :mod:`wayland_output_hdr` as part of C24 without changing
protocol bind versions, state transitions, timeout expiry, or proxy cleanup.
"""

import ctypes
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib

from . import wayland_cm_probe
from .gtk_cm_policy import CmCaps
from .wayland_cm_probe import _WL_DISPLAY_GET_REGISTRY
from .wayland_cm_protocol import (
    _CB_REG_GLOBAL,
    _CB_U,
    _CB_US,
    _CB_VOID,
    _CM_OUTPUT_GET_IMAGE_DESCRIPTION,
    _DONE_CB,
    _IMG_GET_INFORMATION,
    _MGR_GET_OUTPUT,
    _WL_REGISTRY_BIND,
    OutputHdrInfo,
    _CallbackListener,
    _info_from_state,
    _InterfaceTable,
    _make_info_listener,
    _make_listener,
    _marshal_destroy,
)

STATE_INIT = 0
STATE_WAIT_REGISTRY = 1
STATE_WAIT_DESCRIPTIONS = 2
STATE_WAIT_INFOS = 3
STATE_DONE = 4


def _facade():
    from . import wayland_output_hdr

    return wayland_output_hdr


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

        facade = _facade()
        try:
            display_ptr = facade._get_wl_display_ptr()
            if not display_ptr:
                self._finish(None)
                return
            self.display = display_ptr
            lib = facade._load_libwayland()
            if not lib:
                self._finish(None)
                return
            self.lib = lib
            reg_iface = facade._configure_symbols(lib)
            if not reg_iface:
                self._finish(None)
                return
            wl_output_iface = ctypes.addressof(ctypes.c_char.in_dll(lib, "wl_output_interface"))
            self.callback_iface_addr = ctypes.addressof(ctypes.c_char.in_dll(lib, "wl_callback_interface"))
            self.table = _InterfaceTable(wl_output_iface)
            self.keep.append(self.table)
            self.monitors = facade._get_monitor_outputs()
            if not self.monitors:
                self._finish({})
                return
            if not self._setup_wayland_queue_and_registry(lib, reg_iface):
                self._finish(None)
                return

            self._post_sync()
            self.state = STATE_WAIT_REGISTRY
        except Exception as e:
            logging.debug("wayland_output_hdr: async probe init failed: %s", e)
            self._finish(None)

    def _setup_wayland_queue_and_registry(self, lib: Any, reg_iface: int) -> bool:
        """Create isolated Wayland queue, wrapper, and registry global listener."""
        self.queue = lib.wl_display_create_queue(ctypes.c_void_p(self.display))
        if not self.queue:
            return False
        self.wrapper = lib.wl_proxy_create_wrapper(ctypes.c_void_p(self.display))
        if not self.wrapper:
            return False
        lib.wl_proxy_set_queue(ctypes.c_void_p(self.wrapper), ctypes.c_void_p(self.queue))

        self.registry = lib.wl_proxy_marshal_constructor(
            ctypes.c_void_p(self.wrapper),
            ctypes.c_uint32(_WL_DISPLAY_GET_REGISTRY),
            ctypes.c_void_p(reg_iface),
            ctypes.c_void_p(None),
        )
        if not self.registry:
            return False

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
        return lib.wl_proxy_add_listener(ctypes.c_void_p(self.registry), reg_listener, None) == 0

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

    def _bind_color_manager(self, mgr_name: int) -> bool:
        """Bind wp_color_manager_v1 (version 1) and attach capability listeners."""
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
            return False

        self._cm_intents = set()
        self._cm_features = set()
        self._cm_tfs = set()
        self._cm_primaries = set()

        def _on_intent(_d, _p, val):
            self._cm_intents.add(int(val))

        def _on_feature(_d, _p, val):
            self._cm_features.add(int(val))

        def _on_tf_named(_d, _p, tf):
            self._cm_tfs.add(int(tf))

        def _on_primaries_named(_d, _p, p):
            self._cm_primaries.add(int(p))

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
        if self.lib.wl_proxy_add_listener(ctypes.c_void_p(self.mgr), mgr_listener, None) != 0:
            raise RuntimeError("add color-manager listener failed")
        return True

    def _queue_output_descriptions(self) -> None:
        """Request color-management output and image description proxies per monitor."""
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
            if self.lib.wl_proxy_add_listener(ctypes.c_void_p(img), img_listener, None) != 0:
                raise RuntimeError(
                    f"add image-description listener failed for {connector}"
                )

    def _dispatch_registry_state(self, facade: Any) -> bool:
        """Advance state machine after registry globals have settled."""
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
                facade._update_cache({})
            return self._remove_completed_source()

        mgr_name, _mgr_ver = wp_cm
        if not self._bind_color_manager(mgr_name):
            self._finish(None)
            return self._remove_completed_source()

        self._queue_output_descriptions()
        if not self.output_states:
            self._finish({})
            facade._update_cache({})
            return self._remove_completed_source()

        self._post_sync()
        self.state = STATE_WAIT_DESCRIPTIONS
        return GLib.SOURCE_CONTINUE

    def _dispatch_descriptions_state(self, facade: Any) -> bool:
        """Advance state machine once per-output image descriptions become ready/failed."""
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
                continue
            info_obj = self.lib.wl_proxy_marshal_constructor(
                ctypes.c_void_p(st["img"]),
                ctypes.c_uint32(_IMG_GET_INFORMATION),
                ctypes.byref(self.table.info),
                ctypes.c_void_p(None),
            )
            if not info_obj:
                st["hdr_info"] = OutputHdrInfo(connector=connector, hdr=False)
                continue
            st["info_obj"] = info_obj
            has_info = True
            info_listener = _make_info_listener(self.keep, st)
            if self.lib.wl_proxy_add_listener(ctypes.c_void_p(info_obj), info_listener, None) != 0:
                raise RuntimeError(
                    f"add image-description-info listener failed for {connector}"
                )

        if not has_info:
            res = {c: st["hdr_info"] for c, st in self.output_states.items()}
            self._finish(res)
            facade._update_cache(res, self.temp_cm_caps)
            return self._remove_completed_source()

        self._post_sync()
        self.state = STATE_WAIT_INFOS
        return GLib.SOURCE_CONTINUE

    def _dispatch_infos_state(self, facade: Any) -> bool:
        """Finalize OutputHdrInfo entries once info roundtrip completes."""
        for connector, st in self.output_states.items():
            if "hdr_info" not in st:
                if st.get("done"):
                    st["hdr_info"] = _info_from_state(connector, st)
                else:
                    st["hdr_info"] = OutputHdrInfo(connector=connector, hdr=False)
        res = {c: st["hdr_info"] for c, st in self.output_states.items()}
        self._finish(res)
        facade._update_cache(res, self.temp_cm_caps)
        return self._remove_completed_source()

    def dispatch(self, callback: Any, args: Any) -> bool:
        self._expire_if_needed()
        if self._done:
            return self._remove_completed_source()

        if not self._sync_done:
            return GLib.SOURCE_CONTINUE

        facade = _facade()
        try:
            if self.state == STATE_WAIT_REGISTRY:
                return self._dispatch_registry_state(facade)
            if self.state == STATE_WAIT_DESCRIPTIONS:
                return self._dispatch_descriptions_state(facade)
            if self.state == STATE_WAIT_INFOS:
                return self._dispatch_infos_state(facade)
        except Exception as e:
            logging.debug("wayland_output_hdr: async state transition failed: %s", e)
            self._finish(None)
            facade._update_cache(None)
            return self._remove_completed_source()
        return GLib.SOURCE_CONTINUE

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
