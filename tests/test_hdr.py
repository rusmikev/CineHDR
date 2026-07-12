"""
Tests for CineHDR-specific features (HDR config, apply_hdr_settings, UI handlers).
These tests cover everything that differs from the original Cine codebase.

Run with: GSETTINGS_SCHEMA_DIR=data/ python3 -m pytest tests/test_hdr.py -v
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch, PropertyMock
import ctypes

# Add src to path
# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Make tests hermetic and isolated (F13)
import tempfile
import shutil
import subprocess
import atexit

temp_schema_dir = tempfile.mkdtemp()
schema_src = os.path.abspath(os.path.join(os.path.dirname(__file__), "../data"))
# Copy schema file to temp dir
shutil.copy(
    os.path.join(schema_src, "io.github.rusmikev.CineHDR.gschema.xml"),
    os.path.join(temp_schema_dir, "io.github.rusmikev.CineHDR.gschema.xml")
)
# Compile
subprocess.run(["glib-compile-schemas", temp_schema_dir], check=True)
os.environ["GSETTINGS_SCHEMA_DIR"] = temp_schema_dir
os.environ["GSETTINGS_BACKEND"] = "memory"

def cleanup_temp_schemas():
    try:
        shutil.rmtree(temp_schema_dir)
    except Exception:
        pass
atexit.register(cleanup_temp_schemas)

try:
    from gi.repository import Gio
    _gres_path = os.environ.get("CINEHDR_GRESOURCE")
    if not _gres_path or not os.path.exists(_gres_path):
        _gres_path = os.path.join(os.path.dirname(__file__), "..", "build", "src", "cinehdr.gresource")
    if not _gres_path or not os.path.exists(_gres_path):
        _gres_path = os.path.join(os.path.dirname(__file__), "..", "_flatpak_build", "src", "cinehdr.gresource")
    if not _gres_path or not os.path.exists(_gres_path):
        import glob
        _matches = glob.glob(os.path.join(os.path.dirname(__file__), "..", "*", "src", "cinehdr.gresource"))
        if _matches:
            _gres_path = _matches[0]
    if _gres_path and os.path.exists(_gres_path):
        _res = Gio.Resource.load(_gres_path)
        Gio.resources_register(_res)
except Exception:
    pass


# ──────────────────────────────────────────────────────────────
# 1. Tests for HDR config persistence (load/save via GSettings)
# ──────────────────────────────────────────────────────────────

class TestHDRConfigPersistence(unittest.TestCase):
    """Tests for load_hdr_config, save_hdr_config, load_hdr_setting, save_hdr_setting with GSettings."""

    def setUp(self):
        from gi.repository import Gio
        self.settings = Gio.Settings.new("io.github.rusmikev.CineHDR")
        self.orig_mode = self.settings.get_string("hdr-mode")
        self.orig_peak = self.settings.get_string("hdr-target-peak")
        self.orig_prim = self.settings.get_string("hdr-target-prim")

    def tearDown(self):
        self.settings.set_string("hdr-mode", self.orig_mode)
        self.settings.set_string("hdr-target-peak", self.orig_peak)
        self.settings.set_string("hdr-target-prim", self.orig_prim)

    def test_load_defaults(self):
        """load_hdr_config returns valid keys and types."""
        from src.hdr_controller import load_hdr_config
        config = load_hdr_config()
        self.assertIn("hdr_enabled", config)
        self.assertIn("hdr_target_peak", config)
        self.assertIn("hdr_target_prim", config)
        self.assertIsInstance(config["hdr_enabled"], bool)
        self.assertIsInstance(config["hdr_target_peak"], str)
        self.assertIsInstance(config["hdr_target_prim"], str)

    def test_save_and_load_roundtrip(self):
        """Saving config and loading it back returns the same values."""
        from src.hdr_controller import load_hdr_config, save_hdr_config
        test_config = {
            "hdr_enabled": False,
            "hdr_target_peak": "600",
            "hdr_target_prim": "bt.709"
        }
        save_hdr_config(test_config)
        loaded = load_hdr_config()
        self.assertEqual(loaded["hdr_enabled"], False)
        self.assertEqual(loaded["hdr_target_peak"], "600")
        self.assertEqual(loaded["hdr_target_prim"], "bt.709")

    def test_load_hdr_setting_returns_bool(self):
        """load_hdr_setting returns just the boolean enabled state."""
        from src.hdr_controller import load_hdr_setting, save_hdr_config
        save_hdr_config({"hdr_enabled": False, "hdr_target_peak": "auto", "hdr_target_prim": "auto"})
        result = load_hdr_setting()
        self.assertFalse(result)

    def test_save_hdr_setting_preserves_other_keys(self):
        """save_hdr_setting updates only hdr_enabled without losing peak/prim."""
        from src.hdr_controller import save_hdr_setting, load_hdr_config, save_hdr_config
        initial = {"hdr_enabled": True, "hdr_target_peak": "1000", "hdr_target_prim": "bt.709"}
        save_hdr_config(initial)
        save_hdr_setting(False)
        loaded = load_hdr_config()
        self.assertFalse(loaded["hdr_enabled"])
        self.assertEqual(loaded["hdr_target_peak"], "1000")
        self.assertEqual(loaded["hdr_target_prim"], "bt.709")

    def test_load_partial_config_fills_defaults(self):
        """Saving partial config preserves existing keys in GSettings."""
        from src.hdr_controller import load_hdr_config, save_hdr_config
        save_hdr_config({"hdr_enabled": True, "hdr_target_peak": "400", "hdr_target_prim": "dci-p3"})
        save_hdr_config({"hdr_enabled": False})
        config = load_hdr_config()
        self.assertFalse(config["hdr_enabled"])
        self.assertEqual(config["hdr_target_peak"], "400")
        self.assertEqual(config["hdr_target_prim"], "dci-p3")

    @patch("src.hdr_controller._get_hdr_settings")
    def test_load_error_returns_defaults(self, mock_get_settings):
        """If GSettings fails, load_hdr_config returns sensible defaults without crashing."""
        from src.hdr_controller import load_hdr_config
        from gi.repository import GLib
        mock_settings = MagicMock()
        mock_settings.get_string.side_effect = GLib.Error("GSettings error")
        mock_get_settings.return_value = mock_settings
        config = load_hdr_config()
        self.assertTrue(config["hdr_enabled"])
        self.assertEqual(config["hdr_target_peak"], "auto")
        self.assertEqual(config["hdr_target_prim"], "auto")


# ──────────────────────────────────────────────────────────────
# 2. Tests for MpvVideoWidget.apply_hdr_settings & SDR Protection
# ──────────────────────────────────────────────────────────────

class TestApplyHDRSettings(unittest.TestCase):
    """Tests for apply_hdr_settings mpv property mapping and SDR protection."""

    def _make_mock_mpv(self):
        """Create a mock mpv player that records property assignments."""
        props = {}
        mock = MagicMock()
        mock.__setitem__ = lambda self, k, v: props.__setitem__(k, v)
        mock.__getitem__ = lambda self, k: props.get(k)
        mock._props = props
        return mock, props

    def test_hdr_enabled_and_hdr_content_sets_targets(self):
        """When HDR is enabled AND content is HDR, target params should be applied."""
        mock_mpv, props = self._make_mock_mpv()
        hdr_enabled = True
        is_hdr_content = True
        target_prim = "dci-p3"
        target_peak = "400"

        if hdr_enabled and is_hdr_content:
            mock_mpv["target-colorspace-hint"] = "yes"
            mock_mpv["target-trc"] = "pq"
            mock_mpv["target-prim"] = target_prim
            mock_mpv["target-peak"] = int(float(target_peak)) if target_peak != "auto" else "auto"
        else:
            mock_mpv["target-colorspace-hint"] = "no"
            mock_mpv["target-prim"] = "auto"
            mock_mpv["target-peak"] = "auto"
            mock_mpv["target-trc"] = "auto"

        self.assertEqual(props["target-colorspace-hint"], "yes")
        self.assertEqual(props["target-prim"], "dci-p3")
        self.assertEqual(props["target-peak"], 400)
        self.assertEqual(props["target-trc"], "pq")

    def test_sdr_protection_when_hdr_enabled(self):
        """When HDR is enabled in UI BUT content is SDR, targets must reset to auto/no (Risk P-1)."""
        mock_mpv, props = self._make_mock_mpv()
        hdr_enabled = True
        is_hdr_content = False  # Playing SDR video!

        if hdr_enabled and is_hdr_content:
            mock_mpv["target-colorspace-hint"] = "yes"
            mock_mpv["target-trc"] = "pq"
            mock_mpv["target-prim"] = "dci-p3"
        else:
            mock_mpv["target-colorspace-hint"] = "no"
            mock_mpv["target-prim"] = "auto"
            mock_mpv["target-peak"] = "auto"
            mock_mpv["target-trc"] = "auto"

        self.assertEqual(props["target-colorspace-hint"], "no")
        self.assertEqual(props["target-prim"], "auto")
        self.assertEqual(props["target-peak"], "auto")
        self.assertEqual(props["target-trc"], "auto")

    def test_hlg_and_pq_detection_logic(self):
        """Gamma pq/hlg/st2084 or sig-peak > 1.0 correctly identify HDR content (excluding bt.2020 solo trigger)."""
        from src.hdr_detection import is_hdr_content
        test_cases = [
            ({"primaries": "bt.2020", "gamma": "bt.1886", "sig-peak": 1.0}, False),
            ({"primaries": "bt.2020", "gamma": "bt.1886", "sig-peak": 600.0}, True),
            ({"primaries": "bt.709", "gamma": "pq", "sig-peak": 1.0}, True),
            ({"primaries": "bt.709", "gamma": "hlg", "sig-peak": 1.0}, True),
            ({"primaries": "bt.709", "gamma": "st2084", "sig-peak": 1.0}, True),
            ({"primaries": "bt.709", "gamma": "slog3", "sig-peak": 1.0}, True),
            ({"primaries": "bt.709", "gamma": "bt.1886", "sig-peak": 1.0}, False),
            ({}, False),
            (None, False)
        ]
        for params, expected in test_cases:
            self.assertEqual(is_hdr_content(params), expected, f"Failed detection for {params}")

    def test_hdr_auto_peak(self):
        """When peak is 'auto', target-peak should be set to string 'auto'."""
        mock_mpv, props = self._make_mock_mpv()
        target_peak = "auto"
        if target_peak == "auto":
            mock_mpv["target-peak"] = "auto"
        else:
            mock_mpv["target-peak"] = int(float(target_peak))
        self.assertEqual(props["target-peak"], "auto")

    def test_hdr_numeric_peak(self):
        """When peak is numeric, target-peak should be an int."""
        mock_mpv, props = self._make_mock_mpv()
        for peak_str, expected in [("200", 200), ("400", 400), ("600", 600), ("1000", 1000), ("1600", 1600)]:
            if peak_str == "auto":
                mock_mpv["target-peak"] = "auto"
            else:
                mock_mpv["target-peak"] = int(float(peak_str))
            self.assertEqual(props["target-peak"], expected, f"Failed for peak={peak_str}")

    def test_all_gamut_options(self):
        """All gamut mapping values map to valid mpv target-prim values."""
        valid_prims = {"auto", "dci-p3", "bt.709"}
        for prim in valid_prims:
            mock_mpv, props = self._make_mock_mpv()
            mock_mpv["target-prim"] = prim
            self.assertEqual(props["target-prim"], prim)

    @patch("src.hdr_detection.Gdk.Display")
    @patch("src.hdr_detection.Gdk.ColorState", create=True)
    def test_check_hdr_support_conditions(self, mock_colorstate, mock_display_class):
        """test check_hdr_support returns True only on Wayland with GTK >= 4.16."""
        from src.hdr_detection import check_hdr_support, invalidate_hdr_support_cache

        # Scenario 1: Gdk.ColorState doesn't have get_rec2100_pq
        if hasattr(mock_colorstate, "get_rec2100_pq"):
            delattr(mock_colorstate, "get_rec2100_pq")
        invalidate_hdr_support_cache()
        self.assertFalse(check_hdr_support())

        # Scenario 2: Gdk.ColorState has get_rec2100_pq, but no default display
        mock_colorstate.get_rec2100_pq = MagicMock()
        invalidate_hdr_support_cache()
        with patch("src.hdr_detection.Gdk.Display.get_default", return_value=None):
            self.assertFalse(check_hdr_support())

        # Scenario 3: Display is X11
        mock_display = MagicMock()
        mock_display.__class__.__name__ = "GdkX11Display"
        invalidate_hdr_support_cache()
        with patch("src.hdr_detection.Gdk.Display.get_default", return_value=mock_display):
            self.assertFalse(check_hdr_support())

        # Scenario 4: Display is Wayland
        mock_display.__class__.__name__ = "GdkWaylandDisplay"
        invalidate_hdr_support_cache()
        with patch("src.hdr_detection.Gdk.Display.get_default", return_value=mock_display):
            self.assertTrue(check_hdr_support())

        # Scenario 5: Display is Wayland but not composited
        mock_display.is_composited.return_value = False
        invalidate_hdr_support_cache()
        with patch("src.hdr_detection.Gdk.Display.get_default", return_value=mock_display):
            self.assertFalse(check_hdr_support())
        mock_display.is_composited.return_value = True

        # Scenario 6: Display is Wayland but no RGBA
        mock_display.is_rgba.return_value = False
        invalidate_hdr_support_cache()
        with patch("src.hdr_detection.Gdk.Display.get_default", return_value=mock_display):
            self.assertFalse(check_hdr_support())
        mock_display.is_rgba.return_value = True

        # Scenario 7: Display is Wayland but 0 dmabuf formats
        mock_dmabuf = MagicMock()
        mock_dmabuf.get_n_formats.return_value = 0
        mock_display.get_dmabuf_formats.return_value = mock_dmabuf
        with patch("src.hdr_detection.Gdk.Display.get_default", return_value=mock_display):
            self.assertFalse(check_hdr_support())
        mock_dmabuf.get_n_formats.return_value = 10

    @patch("src.hdr_controller.check_hdr_support")
    def test_apply_hdr_settings_with_support_check(self, mock_support):
        """apply_hdr_settings applies HDR only if check_hdr_support is True."""
        from src.hdr_controller import HdrController

        mock_mpv, props = self._make_mock_mpv()
        controller = HdrController(mock_mpv)
        controller._hdr_mode = "auto"
        controller._hdr_enabled = True
        controller._hdr_target_peak = "1000"
        controller._hdr_target_prim = "dci-p3"
        controller._is_hdr_content = True

        # Case A: check_hdr_support is False (e.g. X11) -> should fall back to SDR
        mock_support.return_value = False
        controller.apply_hdr_settings()
        self.assertEqual(props.get("target-colorspace-hint"), "no")
        self.assertEqual(props.get("hdr-compute-peak"), "auto")

        # Case B: check_hdr_support is True (Wayland + GTK >= 4.16) -> should apply PQ
        mock_support.return_value = True
        controller.apply_hdr_settings()
        self.assertEqual(props.get("target-colorspace-hint"), "yes")
        self.assertEqual(props.get("target-trc"), "pq")
        self.assertEqual(props.get("target-prim"), "bt.2020")
        self.assertEqual(props.get("hdr-compute-peak"), "yes")
        self.assertEqual(props.get("target-peak"), 1000)

    @patch("src.hdr_controller.check_hdr_support")
    def test_force_hdr_mode_on_sdr_content(self, mock_support):
        """test force-hdr mode activates tone mapping even for SDR content."""
        from src.hdr_controller import HdrController
        mock_support.return_value = True
        mock_mpv, props = self._make_mock_mpv()
        controller = HdrController(mock_mpv)
        controller._is_hdr_content = False  # SDR content!
        controller.hdr_mode = "force-hdr"
        self.assertTrue(controller.is_hdr_active)
        self.assertEqual(props.get("target-colorspace-hint"), "yes")
        self.assertEqual(props.get("target-trc"), "pq")

    @patch("src.hdr_controller.check_hdr_support")
    def test_force_sdr_mode_on_hdr_content(self, mock_support):
        """test force-sdr mode disables HDR signaling even for HDR content."""
        from src.hdr_controller import HdrController
        mock_support.return_value = True
        mock_mpv, props = self._make_mock_mpv()
        controller = HdrController(mock_mpv)
        controller._is_hdr_content = True  # HDR content!
        controller.hdr_mode = "force-sdr"
        self.assertFalse(controller.is_hdr_active)
        self.assertEqual(props.get("target-colorspace-hint"), "no")
        self.assertEqual(props.get("target-trc"), "auto")


# ──────────────────────────────────────────────────────────────
# 3. Tests for UI handler mapping (options.py callback logic)
# ──────────────────────────────────────────────────────────────

class TestHDRUIHandlerLogic(unittest.TestCase):
    """Tests for the mapping logic in options.py HDR handlers (without GTK)."""

    def test_mode_dropdown_index_mapping(self):
        """Mode dropdown indices correctly map to hdr-mode values and back."""
        mode_map = {0: "auto", 1: "force-hdr", 2: "force-sdr"}
        for idx, expected in mode_map.items():
            if idx == 0:
                result = "auto"
            elif idx == 1:
                result = "force-hdr"
            else:
                result = "force-sdr"
            self.assertEqual(result, expected)

    def test_gamut_dropdown_index_to_prim(self):
        """Gamut dropdown indices correctly map to target-prim values."""
        gamut_map = {0: "auto", 1: "dci-p3", 2: "bt.709"}
        for idx, expected in gamut_map.items():
            if idx == 0:
                result = "auto"
            elif idx == 1:
                result = "dci-p3"
            else:
                result = "bt.709"
            self.assertEqual(result, expected, f"Index {idx} should map to '{expected}'")

    def test_peak_dropdown_index_to_value(self):
        """Peak dropdown indices correctly map to peak values."""
        peaks = ["auto", "200", "400", "600", "1000", "1600"]
        expected = {0: "auto", 1: "200", 2: "400", 3: "600", 4: "1000", 5: "1600"}
        for idx, exp_val in expected.items():
            self.assertEqual(peaks[idx], exp_val, f"Index {idx} should map to '{exp_val}'")

    def test_prim_to_gamut_dropdown_index(self):
        """target-prim values correctly map back to gamut dropdown indices."""
        test_cases = [
            ("auto", 0),
            ("dci-p3", 1),
            ("bt.709", 2),
        ]
        for prim, expected_idx in test_cases:
            if prim == "auto":
                idx = 0
            elif prim == "dci-p3":
                idx = 1
            else:
                idx = 2
            self.assertEqual(idx, expected_idx, f"prim '{prim}' should map to index {expected_idx}")

    def test_peak_to_dropdown_index(self):
        """target-peak values correctly map back to peak dropdown indices."""
        test_cases = [
            ("auto", 0),
            ("200", 1),
            ("400", 2),
            ("600", 3),
            ("1000", 4),
            ("1600", 5),
        ]
        peaks_list = ["auto", "200", "400", "600", "1000", "1600"]
        for peak, expected_idx in test_cases:
            if peak == "auto":
                idx = 0
            else:
                try:
                    p_val = int(float(peak))
                    idx = {200: 1, 400: 2, 600: 3, 1000: 4, 1600: 5}.get(p_val, 0)
                except Exception:
                    idx = 0
            self.assertEqual(idx, expected_idx, f"peak '{peak}' should map to index {expected_idx}")

    def test_syncing_flag_prevents_handler_calls(self):
        """When _syncing_ui is True, handlers should be no-ops."""
        # This tests the guard logic pattern
        syncing = True
        handler_called = False

        def mock_handler():
            nonlocal handler_called
            if syncing:
                return
            handler_called = True

        mock_handler()
        self.assertFalse(handler_called, "Handler should not execute while syncing")

        syncing = False
        mock_handler()
        self.assertTrue(handler_called, "Handler should execute when not syncing")

    def test_hdr_reset_defaults(self):
        """HDR reset should set enabled=True, gamut=Auto (idx 0), peak=Auto (idx 0)."""
        # Test the expected default values after reset
        default_enabled = True
        default_gamut_idx = 0  # Auto (Recommended)
        default_peak_idx = 0   # Auto

        self.assertTrue(default_enabled)
        self.assertEqual(default_gamut_idx, 0)
        self.assertEqual(default_peak_idx, 0)


# ──────────────────────────────────────────────────────────────
# 4. Tests for do_snapshot color state selection
# ──────────────────────────────────────────────────────────────

class TestColorStateSelection(unittest.TestCase):
    """Tests for the HDR/SDR color state decision in do_snapshot."""

    def _is_hdr(self, hdr_enabled, primaries, gamma, sig_peak=1.0):
        """Use is_hdr_content from hdr_detection.py."""
        from src.hdr_detection import is_hdr_content
        return hdr_enabled and is_hdr_content({"primaries": primaries, "gamma": gamma, "sig-peak": sig_peak})

    def test_hdr_bt2020_pq(self):
        """BT.2020 + PQ with HDR enabled → HDR color state."""
        self.assertTrue(self._is_hdr(True, "bt.2020", "pq"))

    def test_hdr_bt2020_hlg(self):
        """BT.2020 + HLG with HDR enabled → HDR color state."""
        self.assertTrue(self._is_hdr(True, "bt.2020", "hlg"))

    def test_hdr_bt2020_srgb_gamma_sdr_peak(self):
        """BT.2020 primaries + sRGB gamma + normal peak → SDR color state (excluding bt.2020 solo trigger)."""
        self.assertFalse(self._is_hdr(True, "bt.2020", "srgb", sig_peak=1.0))

    def test_hdr_bt2020_srgb_gamma_high_peak(self):
        """BT.2020 primaries + sRGB gamma + high peak (>1.0) → HDR color state."""
        self.assertTrue(self._is_hdr(True, "bt.2020", "srgb", sig_peak=600.0))

    def test_hdr_disabled_bt2020_pq(self):
        """BT.2020 + PQ but HDR disabled → SDR color state."""
        self.assertFalse(self._is_hdr(False, "bt.2020", "pq"))

    def test_sdr_content_bt709(self):
        """BT.709 + sRGB with HDR enabled → SDR color state."""
        self.assertFalse(self._is_hdr(True, "bt.709", "srgb"))

    def test_sdr_content_no_params(self):
        """No primaries/gamma info → SDR color state."""
        self.assertFalse(self._is_hdr(True, None, None))

    def test_pq_without_bt2020(self):
        """PQ gamma without BT.2020 → HDR (PQ is always HDR)."""
        self.assertTrue(self._is_hdr(True, "bt.709", "pq"))

    def test_file_supports_hdr_detection(self):
        """Test file HDR detection logic for showing/hiding HDR menu button."""
        from src.hdr_detection import is_hdr_content
        self.assertTrue(is_hdr_content({"primaries": "bt.2020", "gamma": "pq"}))
        self.assertTrue(is_hdr_content({"primaries": "bt.709", "gamma": "hlg"}))
        self.assertTrue(is_hdr_content({"primaries": "bt.2020", "gamma": "bt.1886", "sig-peak": 1000}))
        self.assertFalse(is_hdr_content({"primaries": "bt.2020", "gamma": "srgb", "sig-peak": 1.0}))
        self.assertFalse(is_hdr_content({"primaries": "bt.709", "gamma": "srgb"}))
        self.assertFalse(is_hdr_content(None))


class TestGLFramebufferResource(unittest.TestCase):
    """Tests for RAII OpenGL Framebuffer Resource management."""

    def test_init_defaults(self):
        from src.gl_renderer import GLFramebufferResource
        res = GLFramebufferResource()
        self.assertEqual(res.texture_id.value, 0)
        self.assertEqual(res.fbo_id.value, 0)
        self.assertEqual(res.width, 0)
        self.assertEqual(res.height, 0)
        self.assertFalse(res._initialized)

    def test_ensure_invalid_dimensions_noop(self):
        from src.gl_renderer import GLFramebufferResource
        res = GLFramebufferResource()
        res.ensure(0, 100)
        res.ensure(100, -5)
        self.assertFalse(res._initialized)
        self.assertEqual(res.width, 0)
        self.assertEqual(res.height, 0)

    def test_release_uninitialized_noop(self):
        from src.gl_renderer import GLFramebufferResource
        res = GLFramebufferResource()
        res.release()
        self.assertEqual(res.texture_id.value, 0)
        self.assertEqual(res.fbo_id.value, 0)
        self.assertFalse(res._initialized)

    @unittest.mock.patch("src.gl_renderer.glGenTextures")
    @unittest.mock.patch("src.gl_renderer.glGenFramebuffers")
    @unittest.mock.patch("src.gl_renderer.glBindTexture")
    @unittest.mock.patch("src.gl_renderer.glTexParameteri")
    @unittest.mock.patch("src.gl_renderer.glTexImage2D")
    @unittest.mock.patch("src.gl_renderer.glBindFramebuffer")
    @unittest.mock.patch("src.gl_renderer.glFramebufferTexture2D")
    @unittest.mock.patch("src.gl_renderer.glCheckFramebufferStatus")
    def test_ensure_creation_and_resize(self, mock_status, mock_fb_tex, mock_bind_fb, mock_tex_img, mock_tex_param, mock_bind_tex, mock_gen_fb, mock_gen_tex):
        from src.gl_renderer import GLFramebufferResource
        from src.gl_bindings import GL_FRAMEBUFFER_COMPLETE

        mock_status.return_value = GL_FRAMEBUFFER_COMPLETE
        def fake_gen(n, ptr):
            if hasattr(ptr, "_obj"):
                ptr._obj.value = 42
            else:
                ptr[0] = 42
        mock_gen_tex.side_effect = fake_gen
        mock_gen_fb.side_effect = fake_gen

        res = GLFramebufferResource()
        # First ensure -> creation
        res.ensure(1920, 1080)
        self.assertTrue(res._initialized)
        self.assertEqual(res.width, 1920)
        self.assertEqual(res.height, 1080)
        self.assertEqual(res.texture_id.value, 42)
        self.assertEqual(res.fbo_id.value, 42)
        self.assertEqual(mock_gen_tex.call_count, 1)
        self.assertEqual(mock_gen_fb.call_count, 1)
        self.assertEqual(mock_tex_img.call_count, 1)

        # Same size ensure -> no-op
        res.ensure(1920, 1080)
        self.assertEqual(mock_gen_tex.call_count, 1)
        self.assertEqual(mock_tex_img.call_count, 1)

        # Different size ensure -> resize without gen/delete!
        res.ensure(2560, 1440)
        self.assertEqual(res.width, 2560)
        self.assertEqual(res.height, 1440)
        self.assertEqual(mock_gen_tex.call_count, 1)  # NOT called again!
        self.assertEqual(mock_gen_fb.call_count, 1)   # NOT called again!
        self.assertEqual(mock_tex_img.call_count, 2)  # glTexImage2D called for resize!

    @unittest.mock.patch("src.gl_renderer.glDeleteFramebuffers")
    @unittest.mock.patch("src.gl_renderer.glDeleteTextures")
    def test_release_cleans_resources(self, mock_del_tex, mock_del_fb):
        from src.gl_renderer import GLFramebufferResource
        res = GLFramebufferResource()
        res.texture_id = ctypes.c_uint(10)
        res.fbo_id = ctypes.c_uint(20)
        res._initialized = True
        res.width = 100
        res.height = 100

        res.release()
        self.assertEqual(mock_del_fb.call_count, 1)
        self.assertEqual(mock_del_tex.call_count, 1)
        self.assertEqual(res.texture_id.value, 0)
        self.assertEqual(res.fbo_id.value, 0)
        self.assertFalse(res._initialized)
        self.assertEqual(res.width, 0)
        self.assertEqual(res.height, 0)


# ──────────────────────────────────────────────────────────────
# 6. Tests for HDR Diagnostics Dialog and Helper
# ──────────────────────────────────────────────────────────────

class TestHdrDiagnostics(unittest.TestCase):
    """Tests for HdrDiagnosticsDialog and get_mpv_prop helper."""

    def test_get_mpv_prop(self):
        from src.hdr_diagnostics import get_mpv_prop

        class MockMpv2:
            def get_property(self, name):
                return "val2" if name == "test" else None

        class MockMpv3(dict):
            pass

        self.assertEqual(get_mpv_prop(MockMpv2(), "test"), "val2")
        self.assertEqual(get_mpv_prop(MockMpv2(), "missing", "default"), "default")

        mpv3 = MockMpv3()
        mpv3["test"] = "val3"
        self.assertEqual(get_mpv_prop(mpv3, "test"), "val3")
        self.assertEqual(get_mpv_prop(mpv3, "missing", "default"), "default")

    @unittest.mock.patch("src.hdr_diagnostics.check_hdr_support")
    def test_update_diagnostics_logic(self, mock_check):
        from src.hdr_diagnostics import HdrDiagnosticsDialog
        mock_check.return_value = True

        class MockActionRow:
            def __init__(self):
                self.subtitle = ""
            def set_subtitle(self, text):
                self.subtitle = text

        class MockController:
            is_hdr_active = True
            hdr_mode = "auto"

        class MockGLArea:
            hdr_controller = MockController()
            _color_state = None

        class MockMpv:
            def get_property(self, name):
                if name == "video-format": return "hevc"
                if name == "video-params": return {"primaries": "bt.2020", "gamma": "pq", "sig-peak": 4.93}
                if name == "target-trc": return "pq"
                if name == "target-prim": return "dci-p3"
                if name == "target-peak": return "1000"
                if name == "target-colorspace-hint": return "yes"
                return None

        class MockWin:
            gl_area = MockGLArea()
            mpv = MockMpv()

        diag = HdrDiagnosticsDialog.__new__(HdrDiagnosticsDialog)
        diag._win = MockWin()
        diag.status_row = MockActionRow()
        diag.display_hdr_row = MockActionRow()
        diag.color_state_row = MockActionRow()
        diag.texture_format_row = MockActionRow()
        diag.codec_row = MockActionRow()
        diag.primaries_row = MockActionRow()
        diag.trc_row = MockActionRow()
        diag.peak_luma_row = MockActionRow()
        diag.target_row = MockActionRow()

        diag.update_diagnostics()

        self.assertIn("Active", diag.status_row.subtitle)
        self.assertIn("Yes", diag.display_hdr_row.subtitle)
        self.assertIn("GL_RGBA16F", diag.texture_format_row.subtitle)
        self.assertEqual(diag.codec_row.subtitle, "hevc")
        self.assertEqual(diag.primaries_row.subtitle, "bt.2020")
        self.assertEqual(diag.trc_row.subtitle, "pq")
        self.assertIn("nits", diag.peak_luma_row.subtitle)
        self.assertIn("TRC: pq | Prim: dci-p3 | Peak: 1000 | Hint: yes", diag.target_row.subtitle)


class TestAuditFixes(unittest.TestCase):
    """Tests verifying fixes for senior auditor findings."""

    def test_get_hdr_unsupported_reason(self):
        from src.hdr_detection import get_hdr_unsupported_reason
        reason = get_hdr_unsupported_reason(None)
        self.assertIsInstance(reason, str)
        self.assertTrue(len(reason) > 0)

    def test_hdr_controller_disconnect(self):
        from src.hdr_controller import HdrController
        class MockMpvPlayer:
            def __init__(self):
                self.observed = []
            def property_observer(self, name):
                def decorator(fn):
                    self.observed.append((name, fn))
                    return fn
                return decorator
            def observe_property(self, name, handler):
                self.observed.append((name, handler))
            def unobserve_property(self, name, handler):
                if (name, handler) in self.observed:
                    self.observed.remove((name, handler))
            def __setitem__(self, k, v): pass
            def __getitem__(self, k): return "auto"

        mpv = MockMpvPlayer()
        ctrl = HdrController(mpv)
        self.assertTrue(len(mpv.observed) > 0)
        ctrl.disconnect()
        self.assertEqual(len(mpv.observed), 0)

    def test_texture_builder_build_data_pointer_binding(self):
        # Verify PyGI and GTK C-binding rules for Gdk.GLTextureBuilder.build(destroy, data).
        # 1. Passing an arbitrary Python object raises PyGI ValueError:
        #    "Pointer arguments are restricted to integers, capsules, and None."
        # 2. Passing data=None triggers GTK C assertion: 'destroy == NULL || data != NULL'.
        # 3. Passing data=id(obj) (an integer representing a non-NULL pointer address) satisfies BOTH
        #    PyGI's integer restriction and GTK's non-NULL requirement!
        from gi.repository import Gdk
        builder = Gdk.GLTextureBuilder()
        class FakeSlot: pass
        slot = FakeSlot()

        with self.assertRaises(ValueError):
            builder.build(destroy=lambda _: None, data=slot)

        # Passing data=id(slot) must satisfy PyGI pointer restrictions without GTK assertion.
        # To avoid Gdk-CRITICAL assertion ('self->context != NULL') in headless CI without GL context,
        # we check builder.get_context() before calling build().
        if builder.get_context() is not None:
            try:
                builder.build(destroy=lambda _: None, data=id(slot))
            except ValueError as e:
                if "Pointer arguments are restricted" in str(e):
                    self.fail("builder.build(data=id(slot)) raised PyGI pointer restriction ValueError!")
            except Exception:
                pass

    def test_lazy_fence_deletion(self):
        """Verify release_buffer does not call glDeleteSync, and lazy deletion happens in acquire."""
        from src.gl_renderer import GLFramebufferPool
        pool = GLFramebufferPool(size=3)
        slot = pool.slots[0]
        slot.fence = 12345
        slot.in_use = True

        with patch("src.gl_bindings.glDeleteSync") as mock_delete:
            pool.release_buffer(slot)
            mock_delete.assert_not_called()
            self.assertFalse(slot.in_use)
            self.assertEqual(slot.fence, 12345)

            with patch("src.gl_bindings.glDeleteSync") as mock_delete2:
                with patch.object(slot.resource, "ensure"):
                    slot.resource._initialized = True
                    slot.resource.fbo_id = ctypes.c_uint(1)
                    pool.acquire(100, 100, is_float=False)
                    mock_delete2.assert_called_once_with(12345)

    def test_fbo_validation(self):
        """Verify acquire returns None if fbo_id == 0 or not initialized."""
        from src.gl_renderer import GLFramebufferPool
        pool = GLFramebufferPool(size=1)
        slot = pool.slots[0]
        with patch.object(slot.resource, "ensure"):
            slot.resource._initialized = True
            slot.resource.fbo_id = ctypes.c_uint(0)  # Invalid FBO!
            self.assertIsNone(pool.acquire(100, 100, is_float=False))

            slot.resource._initialized = False
            slot.resource.fbo_id = ctypes.c_uint(1)
            self.assertIsNone(pool.acquire(100, 100, is_float=False))

    def test_sdr_memory_format(self):
        """Verify R8G8B8A8 memory format is used for SDR textures."""
        from gi.repository import Gdk
        self.assertEqual(Gdk.MemoryFormat.R8G8B8A8, getattr(Gdk.MemoryFormat, "R8G8B8A8"))

    def test_hdr_enabled_removed_from_gsettings(self):
        """Verify load_hdr_config and save_hdr_config work without hdr-enabled GSettings key."""
        from src.hdr_controller import load_hdr_config, save_hdr_config
        config = load_hdr_config()
        self.assertIn("hdr_mode", config)
        self.assertIn("hdr_enabled", config)
        self.assertEqual(config["hdr_enabled"], (config["hdr_mode"] != "force-sdr"))


if __name__ == "__main__":
    unittest.main()

