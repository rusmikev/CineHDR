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
from unittest.mock import MagicMock

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


if __name__ == "__main__":
    unittest.main()
