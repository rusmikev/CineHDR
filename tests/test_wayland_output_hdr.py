"""Characterization tests for src/wayland_output_hdr.py before C24 splitting."""

import atexit
import ctypes
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_TEMP_SCHEMA_DIR = tempfile.mkdtemp(prefix="cinehdr-wayland-hdr-schema-")
atexit.register(shutil.rmtree, _TEMP_SCHEMA_DIR, ignore_errors=True)
shutil.copy2(
    _REPO_ROOT / "data" / "io.github.rusmikev.CineHDR.gschema.xml",
    Path(_TEMP_SCHEMA_DIR) / "io.github.rusmikev.CineHDR.gschema.xml",
)
subprocess.run(["glib-compile-schemas", _TEMP_SCHEMA_DIR], check=True)
os.environ["GSETTINGS_SCHEMA_DIR"] = _TEMP_SCHEMA_DIR
os.environ["GSETTINGS_BACKEND"] = "memory"


class TestClassificationAndModel(unittest.TestCase):
    """Pure classification logic and OutputHdrInfo fields/properties."""

    def test_classify_hdr_all_branches(self):
        from src.wayland_output_hdr import (
            TF_BT1886,
            TF_EXT_LINEAR,
            TF_GAMMA22,
            TF_HLG,
            TF_SRGB,
            TF_ST2084_PQ,
            classify_hdr,
        )

        # PQ and HLG are unconditionally HDR even with unknown luminances.
        self.assertTrue(classify_hdr(TF_ST2084_PQ, None, None, None))
        self.assertTrue(classify_hdr(TF_HLG, None, None, None))

        # Extended range luminance marks HDR regardless of transfer function.
        self.assertTrue(classify_hdr(TF_EXT_LINEAR, 0.005, 800.0, 203.0))
        self.assertTrue(classify_hdr(TF_GAMMA22, 0.05, 600.0, 200.0))

        # Equal/below reference white, zero reference, or missing luminances -> SDR.
        self.assertFalse(classify_hdr(TF_SRGB, 0.1, 200.0, 200.0))
        self.assertFalse(classify_hdr(TF_SRGB, 0.1, 150.0, 200.0))
        self.assertFalse(classify_hdr(TF_BT1886, 0.1, 400.0, 0.0))
        self.assertFalse(classify_hdr(TF_BT1886, 0.1, 400.0, -10.0))
        self.assertFalse(classify_hdr(TF_SRGB, 0.1, None, 200.0))
        self.assertFalse(classify_hdr(TF_SRGB, 0.1, 400.0, None))
        self.assertFalse(classify_hdr(None, None, None, None))

    def test_output_hdr_info_tf_name_and_fields(self):
        from src.wayland_output_hdr import (
            PRIMARIES_BT2020,
            TF_COMPOUND_POWER_2_4,
            TF_HLG,
            TF_SRGB,
            TF_ST2084_PQ,
            OutputHdrInfo,
        )

        self.assertEqual(OutputHdrInfo("DP-1", True, tf=TF_ST2084_PQ).tf_name, "st2084_pq")
        self.assertEqual(OutputHdrInfo("DP-1", True, tf=TF_HLG).tf_name, "hlg")
        self.assertEqual(OutputHdrInfo("DP-1", False, tf=TF_SRGB).tf_name, "srgb")
        self.assertEqual(
            OutputHdrInfo("DP-1", False, tf=TF_COMPOUND_POWER_2_4).tf_name,
            "compound_power_2_4",
        )
        self.assertEqual(OutputHdrInfo("DP-1", False, tf=None).tf_name, "unset")
        self.assertEqual(OutputHdrInfo("DP-1", False, tf=999).tf_name, "unknown(999)")

        info = OutputHdrInfo(
            connector="HDMI-A-1",
            hdr=True,
            tf=TF_ST2084_PQ,
            primaries=PRIMARIES_BT2020,
            min_lum=0.005,
            max_lum=1000.0,
            reference_lum=203.0,
            target_max_lum=4000.0,
            target_max_cll=1500.0,
        )
        self.assertEqual(info.connector, "HDMI-A-1")
        self.assertEqual(info.primaries, PRIMARIES_BT2020)
        self.assertEqual(info.min_lum, 0.005)
        self.assertEqual(info.max_lum, 1000.0)
        self.assertEqual(info.reference_lum, 203.0)
        self.assertEqual(info.target_max_lum, 4000.0)
        self.assertEqual(info.target_max_cll, 1500.0)


class TestCtypesProtocolTables(unittest.TestCase):
    """ctypes wl_interface/wl_message tables, listener arrays, destroy order."""

    def test_sig_arg_count(self):
        from src.wayland_output_hdr import _sig_arg_count

        self.assertEqual(_sig_arg_count(b""), 0)
        self.assertEqual(_sig_arg_count(b"u"), 1)
        self.assertEqual(_sig_arg_count(b"no"), 2)
        self.assertEqual(_sig_arg_count(b"hu"), 2)
        self.assertEqual(_sig_arg_count(b"uuu"), 3)
        self.assertEqual(_sig_arg_count(b"iiiiiiii"), 8)

    def test_interface_table_layout_and_pinned_buffers(self):
        from src.wayland_output_hdr import _InterfaceTable

        wl_output_addr = 0xCAFEBABE
        table = _InterfaceTable(wl_output_addr)

        self.assertEqual(table.mgr.name, b"wp_color_manager_v1")
        self.assertEqual(table.mgr.version, 1)
        self.assertEqual(table.mgr.method_count, 2)
        self.assertEqual(table.mgr.event_count, 5)
        self.assertEqual(table.mgr.methods[1].name, b"get_output")
        self.assertEqual(table.mgr.methods[1].signature, b"no")
        self.assertEqual(table.mgr.methods[1].types[0], ctypes.addressof(table.cm_output))
        self.assertEqual(table.mgr.methods[1].types[1], wl_output_addr)

        self.assertEqual(table.cm_output.name, b"wp_color_management_output_v1")
        self.assertEqual(table.cm_output.method_count, 2)
        self.assertEqual(table.cm_output.event_count, 1)
        self.assertEqual(table.cm_output.events[0].name, b"image_description_changed")
        self.assertEqual(table.cm_output.methods[1].types[0], ctypes.addressof(table.img))

        self.assertEqual(table.img.name, b"wp_image_description_v1")
        self.assertEqual(table.img.method_count, 2)
        self.assertEqual(table.img.event_count, 2)
        self.assertEqual(table.img.events[0].name, b"failed")
        self.assertEqual(table.img.events[0].signature, b"us")
        self.assertEqual(table.img.events[1].name, b"ready")
        self.assertEqual(table.img.events[1].signature, b"u")
        self.assertEqual(table.img.methods[1].types[0], ctypes.addressof(table.info))

        self.assertEqual(table.info.name, b"wp_image_description_info_v1")
        self.assertEqual(table.info.method_count, 0)
        self.assertEqual(table.info.event_count, 11)
        expected_info_events = [
            (b"done", b""),
            (b"icc_file", b"hu"),
            (b"primaries", b"iiiiiiii"),
            (b"primaries_named", b"u"),
            (b"tf_power", b"u"),
            (b"tf_named", b"u"),
            (b"luminances", b"uuu"),
            (b"target_primaries", b"iiiiiiii"),
            (b"target_luminance", b"uu"),
            (b"target_max_cll", b"u"),
            (b"target_max_fall", b"u"),
        ]
        for idx, (ename, esig) in enumerate(expected_info_events):
            self.assertEqual(table.info.events[idx].name, ename)
            self.assertEqual(table.info.events[idx].signature, esig)

        # All nested arrays are kept alive on table._keep.
        self.assertGreaterEqual(len(table._keep), 10)

    def test_make_listener_pins_callbacks_and_array(self):
        from src.wayland_output_hdr import _CB_U, _CB_VOID, _make_listener

        keep = []
        fn0 = _CB_VOID(lambda *_a: None)
        fn1 = _CB_U(lambda *_a: None)
        arr = _make_listener(keep, [fn0, fn1])
        self.assertEqual(len(arr), 2)
        self.assertIn(fn0, keep)
        self.assertIn(fn1, keep)
        self.assertIn(arr, keep)

    def test_marshal_destroy_order_and_resilience(self):
        from src.wayland_output_hdr import _DESTROY, _marshal_destroy

        calls = []
        lib = MagicMock()
        lib.wl_proxy_marshal.side_effect = lambda p, op: calls.append(("marshal", p.value, op.value))
        lib.wl_proxy_destroy.side_effect = lambda p: calls.append(("destroy", p.value))

        _marshal_destroy(lib, 0x1234)
        self.assertEqual(calls, [("marshal", 0x1234, _DESTROY), ("destroy", 0x1234)])

        # Safe even if marshal raises before destroy.
        lib.wl_proxy_marshal.side_effect = OSError("broken pipe")
        calls.clear()
        _marshal_destroy(lib, 0x5678)
        self.assertEqual(calls, [("destroy", 0x5678)])


def _unwrap_ptr(val):
    if isinstance(val, ctypes.c_void_p):
        return val.value
    if isinstance(val, ctypes.c_uint32):
        return val.value
    if isinstance(val, ctypes.c_char_p):
        return val.value
    return val


def _extract_callbacks(listener_ptr, signature_list):
    """Cast each slot in a void(**) listener array back to a Python-callable thunk."""
    raw = ctypes.cast(listener_ptr, ctypes.POINTER(ctypes.c_void_p))
    return [cb_type(raw[i]) for i, cb_type in enumerate(signature_list)]


class _FakeWaylandLib:
    """In-memory mock of libwayland-client driving listener callbacks in C order."""

    def __init__(self, globals_map, per_output_behaviors, mgr_caps=None):
        self.globals_map = globals_map
        self.per_output_behaviors = dict(per_output_behaviors)
        self.mgr_caps = mgr_caps or {
            "intents": [0],
            "features": [2, 3],
            "tfs": [9, 11],
            "primaries": [1, 6],
        }
        self.wl_output_interface = (ctypes.c_char * 64)()
        self.wl_callback_interface = (ctypes.c_char * 64)()
        self.destroyed = []
        self.marshaled_destroy = []
        self.bound_version = None
        self.listeners = {}

        self._next_id = 0x1000
        self._queue_ptr = 0xA001
        self._wrapper_ptr = 0xA002
        self._registry_ptr = 0xA003
        self._mgr_ptr = 0xA004
        self._proxy_meta = {}
        self._pending_sync_cbs = []

    def _alloc(self, kind, **meta):
        self._next_id += 1
        ptr = self._next_id
        self._proxy_meta[ptr] = {"kind": kind, **meta}
        return ptr

    def wl_display_create_queue(self, _display):
        return self._queue_ptr

    def wl_proxy_create_wrapper(self, _display):
        return self._wrapper_ptr

    def wl_proxy_set_queue(self, _wrapper, _queue):
        return None

    def wl_proxy_marshal_constructor(self, parent, opcode, _iface, *args):
        parent_val = _unwrap_ptr(parent)
        op_val = _unwrap_ptr(opcode)
        if parent_val == self._wrapper_ptr and op_val == 1:  # wl_display.get_registry
            return self._registry_ptr
        if parent_val == self._wrapper_ptr and op_val == 0:  # wl_display.sync
            ptr = self._alloc("sync_cb")
            self._pending_sync_cbs.append(ptr)
            return ptr
        if parent_val == self._mgr_ptr and op_val == 1:  # get_output
            out_ptr = _unwrap_ptr(args[1])
            return self._alloc("cm_out", out_ptr=out_ptr)
        if parent_val in self._proxy_meta and self._proxy_meta[parent_val]["kind"] == "cm_out":
            out_ptr = self._proxy_meta[parent_val]["out_ptr"]
            return self._alloc("img", out_ptr=out_ptr)
        if parent_val in self._proxy_meta and self._proxy_meta[parent_val]["kind"] == "img":
            out_ptr = self._proxy_meta[parent_val]["out_ptr"]
            return self._alloc("info", out_ptr=out_ptr)
        return 0

    def wl_proxy_marshal_constructor_versioned(self, _reg, _op, _iface, version, _name, _iface_name, _v2, _new_id):
        self.bound_version = _unwrap_ptr(version)
        return self._mgr_ptr

    def wl_proxy_add_listener(self, proxy, listener, _data):
        proxy_val = _unwrap_ptr(proxy)
        self.listeners[proxy_val] = listener
        return 0

    def wl_display_roundtrip_queue(self, _display, _queue):
        from src.wayland_output_hdr import (
            _CB_HU,
            _CB_I8,
            _CB_REG_GLOBAL,
            _CB_U,
            _CB_US,
            _CB_UU,
            _CB_UUU,
            _CB_VOID,
        )

        # Dispatch registry globals
        if self._registry_ptr in self.listeners and not getattr(self, "_reg_dispatched", False):
            self._reg_dispatched = True
            cbs = _extract_callbacks(self.listeners[self._registry_ptr], [_CB_REG_GLOBAL, _CB_U])
            for gname, (gid, gver) in self.globals_map.items():
                cbs[0](None, self._registry_ptr, gid, gname.encode(), gver)

        # Dispatch manager capabilities
        if self._mgr_ptr in self.listeners and not getattr(self, "_mgr_dispatched", False):
            self._mgr_dispatched = True
            cbs = _extract_callbacks(self.listeners[self._mgr_ptr], [_CB_U, _CB_U, _CB_U, _CB_U, _CB_VOID])
            for val in self.mgr_caps["intents"]:
                cbs[0](None, self._mgr_ptr, val)
            for val in self.mgr_caps["features"]:
                cbs[1](None, self._mgr_ptr, val)
            for val in self.mgr_caps["tfs"]:
                cbs[2](None, self._mgr_ptr, val)
            for val in self.mgr_caps["primaries"]:
                cbs[3](None, self._mgr_ptr, val)
            cbs[4](None, self._mgr_ptr)

        # Dispatch pending img / info listeners
        for ptr, meta in list(self._proxy_meta.items()):
            if ptr not in self.listeners or meta.get("dispatched"):
                continue
            if meta["kind"] not in ("img", "info"):
                continue
            meta["dispatched"] = True
            behavior = self.per_output_behaviors[meta["out_ptr"]]
            if meta["kind"] == "img":
                cbs = _extract_callbacks(self.listeners[ptr], [_CB_US, _CB_U])
                if behavior.get("failed"):
                    cbs[0](None, ptr, 1, b"unsupported")
                elif behavior.get("ready", True):
                    cbs[1](None, ptr, 42)
            elif meta["kind"] == "info":
                cbs = _extract_callbacks(
                    self.listeners[ptr],
                    [
                        _CB_VOID,
                        _CB_HU,
                        _CB_I8,
                        _CB_U,
                        _CB_U,
                        _CB_U,
                        _CB_UUU,
                        _CB_I8,
                        _CB_UU,
                        _CB_U,
                        _CB_U,
                    ],
                )
                if "icc_fd" in behavior:
                    cbs[1](None, ptr, behavior["icc_fd"], 128)
                if "primaries" in behavior:
                    cbs[3](None, ptr, behavior["primaries"])
                if "tf" in behavior:
                    cbs[5](None, ptr, behavior["tf"])
                if "luminances" in behavior:
                    mn, mx, ref = behavior["luminances"]
                    cbs[6](None, ptr, mn, mx, ref)
                if "target_luminance" in behavior:
                    mn, mx = behavior["target_luminance"]
                    cbs[8](None, ptr, mn, mx)
                if "target_max_cll" in behavior:
                    cbs[9](None, ptr, behavior["target_max_cll"])
                if behavior.get("info_done", True):
                    cbs[0](None, ptr)
        return 0

    def wl_display_flush(self, _display):
        return 0

    def wl_display_dispatch_queue_pending(self, display, queue):
        from src.wayland_output_hdr import _DONE_CB

        self.wl_display_roundtrip_queue(display, queue)
        while self._pending_sync_cbs:
            cb_ptr = self._pending_sync_cbs.pop(0)
            if cb_ptr in self.listeners:
                raw_listener = self.listeners[cb_ptr]
                if hasattr(raw_listener, "_obj") and hasattr(raw_listener._obj, "done"):
                    raw_listener._obj.done(None, cb_ptr, 1)
                else:
                    listener = ctypes.cast(raw_listener, ctypes.POINTER(_DONE_CB))
                    listener[0](None, cb_ptr, 1)
        return 0

    def wl_proxy_marshal(self, proxy, opcode):
        self.marshaled_destroy.append((_unwrap_ptr(proxy), _unwrap_ptr(opcode)))

    def wl_proxy_destroy(self, proxy):
        ptr = _unwrap_ptr(proxy)
        kind = self._proxy_meta.get(ptr, {}).get("kind")
        if ptr == self._registry_ptr:
            kind = "registry"
        elif ptr == self._mgr_ptr:
            kind = "mgr"
        self.destroyed.append((kind, ptr))

    def wl_proxy_wrapper_destroy(self, wrapper):
        self.destroyed.append(("wrapper", _unwrap_ptr(wrapper)))

    def wl_event_queue_destroy(self, queue):
        self.destroyed.append(("queue", _unwrap_ptr(queue)))


class _PatchedInDll:
    """Temporarily make ctypes.c_char.in_dll resolve symbols on _FakeWaylandLib."""

    @staticmethod
    def install(fake_lib):
        orig_in_dll = ctypes.c_char.in_dll

        def _fake_in_dll(lib_obj, name):
            if lib_obj is fake_lib and hasattr(fake_lib, name):
                return getattr(fake_lib, name)
            return orig_in_dll(lib_obj, name)

        return patch.object(ctypes.c_char, "in_dll", side_effect=_fake_in_dll)


class TestSynchronousProbe(unittest.TestCase):
    """Synchronous probe_outputs / _probe_outputs_unsafe / _query_one_output."""

    def setUp(self):
        from src import wayland_output_hdr as w

        self.w = w
        w._backend_disabled = False
        w._backend_error_count = 0
        w.invalidate(invalidate_caps=True)

    def tearDown(self):
        self.w._backend_disabled = False
        self.w._backend_error_count = 0
        self.w.invalidate(invalidate_caps=True)

    def test_sync_probe_returns_none_when_prerequisites_missing(self):
        w = self.w
        with patch.object(w, "_get_wl_display_ptr", return_value=None):
            self.assertIsNone(w.probe_outputs())
        with patch.object(w, "_get_wl_display_ptr", return_value=0x100), \
             patch.object(w, "_load_libwayland", return_value=None):
            self.assertIsNone(w.probe_outputs())
        with patch.object(w, "_get_wl_display_ptr", return_value=0x100), \
             patch.object(w, "_load_libwayland", return_value=MagicMock()), \
             patch.object(w, "_configure_symbols", return_value=None):
            self.assertIsNone(w.probe_outputs())
        with patch.object(w, "_get_wl_display_ptr", return_value=0x100), \
             patch.object(w, "_load_libwayland", return_value=MagicMock()), \
             patch.object(w, "_configure_symbols", return_value=0x200), \
             patch.object(w, "_get_monitor_outputs", return_value=None):
            self.assertIsNone(w.probe_outputs())

    def test_sync_probe_xx_color_manager_only_returns_none(self):
        w = self.w
        fake_lib = _FakeWaylandLib(
            globals_map={"xx_color_manager_v4": (1, 4)},
            per_output_behaviors={},
        )
        with _PatchedInDll.install(fake_lib), \
             patch.object(w, "_get_wl_display_ptr", return_value=0xD159), \
             patch.object(w, "_load_libwayland", return_value=fake_lib), \
             patch.object(w, "_configure_symbols", return_value=0xBEEF), \
             patch.object(w, "_get_monitor_outputs", return_value=[("DP-1", 0x901)]):
            self.assertIsNone(w.probe_outputs())
        self.assertEqual([k for k, _ in fake_lib.destroyed], ["registry", "wrapper", "queue"])

    def test_sync_probe_full_hdr_and_sdr_outputs_with_icc_fd_cleanup(self):
        from src.wayland_output_hdr import PRIMARIES_BT2020, TF_SRGB, TF_ST2084_PQ

        w = self.w
        r_fd, w_fd = os.pipe()
        os.close(w_fd)
        fake_lib = _FakeWaylandLib(
            globals_map={"wp_color_manager_v1": (7, 2)},
            mgr_caps={
                "intents": [0],
                "features": [2, 3],
                "tfs": [TF_SRGB, TF_ST2084_PQ],
                "primaries": [1, PRIMARIES_BT2020],
            },
            per_output_behaviors={
                0x901: {
                    "ready": True,
                    "icc_fd": r_fd,
                    "tf": TF_ST2084_PQ,
                    "primaries": PRIMARIES_BT2020,
                    "luminances": (50, 1000, 203),
                    "target_luminance": (50, 4000),
                    "target_max_cll": 1500,
                },
                0x902: {"failed": True},
            },
        )
        with _PatchedInDll.install(fake_lib), \
             patch("os.close", wraps=os.close) as close_spy, \
             patch.object(w, "_get_wl_display_ptr", return_value=0xD159), \
             patch.object(w, "_load_libwayland", return_value=fake_lib), \
             patch.object(w, "_configure_symbols", return_value=0xBEEF), \
             patch.object(
                 w,
                 "_get_monitor_outputs",
                 return_value=[("HDMI-A-1", 0x901), ("eDP-1", 0x902)],
             ):
            results = w.probe_outputs()

        self.assertEqual(fake_lib.bound_version, 1)
        self.assertIsNotNone(results)
        self.assertTrue(results["HDMI-A-1"].hdr)
        self.assertEqual(results["HDMI-A-1"].tf, TF_ST2084_PQ)
        self.assertEqual(results["HDMI-A-1"].primaries, PRIMARIES_BT2020)
        self.assertAlmostEqual(results["HDMI-A-1"].min_lum, 0.005)
        self.assertEqual(results["HDMI-A-1"].max_lum, 1000.0)
        self.assertEqual(results["HDMI-A-1"].reference_lum, 203.0)
        self.assertEqual(results["HDMI-A-1"].target_max_lum, 4000.0)
        self.assertEqual(results["HDMI-A-1"].target_max_cll, 1500.0)
        self.assertFalse(results["eDP-1"].hdr)

        # icc_file fd ownership transfer must have closed r_fd.
        close_spy.assert_any_call(r_fd)

        # _cache_cm_caps must record advertised capabilities.
        caps = w._cache_cm_caps
        self.assertIsNotNone(caps)
        self.assertIn(TF_ST2084_PQ, caps.tfs)
        self.assertIn(TF_SRGB, caps.tfs)
        self.assertIn(PRIMARIES_BT2020, caps.primaries)

        # Destruction order: per-output objects, then mgr -> registry -> wrapper -> queue.
        kinds = [k for k, _ in fake_lib.destroyed]
        self.assertEqual(
            kinds,
            ["info", "img", "cm_out", "img", "cm_out", "mgr", "registry", "wrapper", "queue"],
        )

    def test_circuit_breaker_disables_backend_after_three_errors(self):
        w = self.w
        with patch.object(w, "_probe_outputs_unsafe", side_effect=RuntimeError("ffi boom")) as mock_u:
            self.assertIsNone(w.probe_outputs())
            self.assertEqual(w._backend_error_count, 1)
            self.assertFalse(w._backend_disabled)
            self.assertIsNone(w.probe_outputs())
            self.assertEqual(w._backend_error_count, 2)
            self.assertFalse(w._backend_disabled)
            with self.assertLogs(level="ERROR"):
                self.assertIsNone(w.probe_outputs())
            self.assertEqual(w._backend_error_count, 3)
            self.assertTrue(w._backend_disabled)
            # Fourth call short-circuits without calling _probe_outputs_unsafe.
            self.assertIsNone(w.probe_outputs())
            self.assertEqual(mock_u.call_count, 3)

        # Success clears error count when not disabled.
        w._backend_disabled = False
        w._backend_error_count = 2
        with patch.object(w, "_probe_outputs_unsafe", return_value={}):
            self.assertEqual(w.probe_outputs(), {})
        self.assertEqual(w._backend_error_count, 0)


if __name__ == "__main__":
    unittest.main()
