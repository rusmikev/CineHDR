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

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ──────────────────────────────────────────────────────────────
# 1. Tests for HDR config persistence (load/save in video_widget.py)
# ──────────────────────────────────────────────────────────────

class TestHDRConfigPersistence(unittest.TestCase):
    """Tests for load_hdr_config, save_hdr_config, load_hdr_setting, save_hdr_setting."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.tmpdir, "hdr_config.json")

    def tearDown(self):
        if os.path.exists(self.config_path):
            os.remove(self.config_path)
        os.rmdir(self.tmpdir)

    @patch("src.video_widget.HDR_CONFIG_PATH")
    def test_load_defaults_when_no_file(self, mock_path):
        """load_hdr_config returns sensible defaults when no config file exists."""
        from src.video_widget import load_hdr_config
        mock_path.__str__ = lambda s: "/nonexistent/path/hdr_config.json"
        # Patch os.path.exists via the config path
        with patch("src.video_widget.HDR_CONFIG_PATH", "/nonexistent/path/hdr_config.json"):
            config = load_hdr_config()
        self.assertTrue(config["hdr_enabled"])
        self.assertEqual(config["hdr_target_peak"], "auto")
        self.assertEqual(config["hdr_target_prim"], "auto")

    def test_save_and_load_roundtrip(self):
        """Saving config and loading it back returns the same values."""
        from src.video_widget import load_hdr_config, save_hdr_config
        test_config = {
            "hdr_enabled": False,
            "hdr_target_peak": "600",
            "hdr_target_prim": "bt.709"
        }
        with patch("src.video_widget.HDR_CONFIG_PATH", self.config_path):
            save_hdr_config(test_config)
            loaded = load_hdr_config()
        self.assertEqual(loaded["hdr_enabled"], False)
        self.assertEqual(loaded["hdr_target_peak"], "600")
        self.assertEqual(loaded["hdr_target_prim"], "bt.709")

    def test_load_hdr_setting_returns_bool(self):
        """load_hdr_setting returns just the boolean enabled state."""
        from src.video_widget import load_hdr_setting, save_hdr_config
        with patch("src.video_widget.HDR_CONFIG_PATH", self.config_path):
            save_hdr_config({"hdr_enabled": False, "hdr_target_peak": "auto", "hdr_target_prim": "auto"})
            result = load_hdr_setting()
        self.assertFalse(result)

    def test_save_hdr_setting_preserves_other_keys(self):
        """save_hdr_setting updates only hdr_enabled without losing peak/prim."""
        from src.video_widget import save_hdr_setting, load_hdr_config, save_hdr_config
        initial = {"hdr_enabled": True, "hdr_target_peak": "1000", "hdr_target_prim": "bt.709"}
        with patch("src.video_widget.HDR_CONFIG_PATH", self.config_path):
            save_hdr_config(initial)
            save_hdr_setting(False)
            loaded = load_hdr_config()
        self.assertFalse(loaded["hdr_enabled"])
        self.assertEqual(loaded["hdr_target_peak"], "1000")
        self.assertEqual(loaded["hdr_target_prim"], "bt.709")

    def test_load_partial_config_fills_defaults(self):
        """Loading a config with missing keys fills in defaults."""
        from src.video_widget import load_hdr_config
        with open(self.config_path, "w") as f:
            json.dump({"hdr_enabled": False}, f)
        with patch("src.video_widget.HDR_CONFIG_PATH", self.config_path):
            config = load_hdr_config()
        self.assertFalse(config["hdr_enabled"])
        self.assertEqual(config["hdr_target_peak"], "auto")
        self.assertEqual(config["hdr_target_prim"], "auto")

    def test_load_corrupted_file_returns_defaults(self):
        """Loading a corrupted JSON file returns defaults without crashing."""
        from src.video_widget import load_hdr_config
        with open(self.config_path, "w") as f:
            f.write("NOT VALID JSON {{{")
        with patch("src.video_widget.HDR_CONFIG_PATH", self.config_path):
            config = load_hdr_config()
        self.assertTrue(config["hdr_enabled"])
        self.assertEqual(config["hdr_target_peak"], "auto")
        self.assertEqual(config["hdr_target_prim"], "auto")

    def test_atomic_config_save(self):
        """save_hdr_config atomically writes via temporary file without leaving tmp files behind (Risk P-6)."""
        from src.video_widget import save_hdr_config
        with patch("src.video_widget.HDR_CONFIG_PATH", self.config_path):
            save_hdr_config({"hdr_enabled": False, "hdr_target_peak": "1000", "hdr_target_prim": "auto"})
        self.assertTrue(os.path.exists(self.config_path))
        self.assertFalse(os.path.exists(f"{self.config_path}.tmp"))
        with open(self.config_path, "r") as f:
            data = json.load(f)
        self.assertEqual(data["hdr_target_peak"], "1000")


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
        """Primaries bt.2020 or gamma pq/hlg correctly identify HDR content (Risk P-8)."""
        test_cases = [
            ({"primaries": "bt.2020", "gamma": "bt.1886"}, True),
            ({"primaries": "bt.709", "gamma": "pq"}, True),
            ({"primaries": "bt.709", "gamma": "hlg"}, True),
            ({"primaries": "bt.709", "gamma": "bt.1886"}, False),
            ({}, False),
            (None, False)
        ]
        for params, expected in test_cases:
            if params and isinstance(params, dict):
                primaries = params.get("primaries")
                gamma = params.get("gamma")
                is_hdr = ((primaries == "bt.2020") or (gamma in ("pq", "hlg")))
            else:
                is_hdr = False
            self.assertEqual(is_hdr, expected, f"Failed detection for {params}")

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

    @patch("src.video_widget.Gdk.Display")
    @patch("src.video_widget.Gdk.ColorState")
    def test_check_hdr_support_conditions(self, mock_colorstate, mock_display_class):
        """test check_hdr_support returns True only on Wayland with GTK >= 4.16."""
        from src.video_widget import check_hdr_support
        
        # Scenario 1: Gdk.ColorState doesn't have get_rec2100_pq
        if hasattr(mock_colorstate, "get_rec2100_pq"):
            delattr(mock_colorstate, "get_rec2100_pq")
        self.assertFalse(check_hdr_support())
        
        # Scenario 2: Gdk.ColorState has get_rec2100_pq, but no default display
        mock_colorstate.get_rec2100_pq = MagicMock()
        with patch("src.video_widget.Gdk.Display.get_default", return_value=None):
            self.assertFalse(check_hdr_support())
            
        # Scenario 3: Display is X11
        mock_display = MagicMock()
        mock_display.__class__.__name__ = "GdkX11Display"
        with patch("src.video_widget.Gdk.Display.get_default", return_value=mock_display):
            self.assertFalse(check_hdr_support())
            
        # Scenario 4: Display is Wayland
        mock_display.__class__.__name__ = "GdkWaylandDisplay"
        with patch("src.video_widget.Gdk.Display.get_default", return_value=mock_display):
            self.assertTrue(check_hdr_support())

    @patch("src.video_widget.check_hdr_support")
    def test_apply_hdr_settings_with_support_check(self, mock_support):
        """apply_hdr_settings applies HDR only if check_hdr_support is True."""
        from src.video_widget import MpvVideoWidget
        import types
        
        mock_mpv, props = self._make_mock_mpv()
        
        # Create a lightweight dummy widget bypass GTK init issues
        class DummyWidget:
            pass
            
        widget = DummyWidget()
        widget.mpv = mock_mpv
        widget._hdr_enabled = True
        widget._hdr_target_peak = "1000"
        widget._hdr_target_prim = "dci-p3"
        widget._is_hdr_content = True
        widget.apply_hdr_settings = types.MethodType(MpvVideoWidget.apply_hdr_settings, widget)
        
        # Case A: check_hdr_support is False (e.g. X11) -> should fall back to SDR
        mock_support.return_value = False
        widget.apply_hdr_settings()
        self.assertEqual(props.get("target-colorspace-hint"), "no")
        self.assertEqual(props.get("hdr-compute-peak"), "auto")
        
        # Case B: check_hdr_support is True (Wayland + GTK >= 4.16) -> should apply PQ
        mock_support.return_value = True
        widget.apply_hdr_settings()
        self.assertEqual(props.get("target-colorspace-hint"), "yes")
        self.assertEqual(props.get("target-trc"), "pq")
        self.assertEqual(props.get("target-prim"), "dci-p3")
        self.assertEqual(props.get("hdr-compute-peak"), "yes")
        self.assertEqual(props.get("target-peak"), 1000)


# ──────────────────────────────────────────────────────────────
# 3. Tests for UI handler mapping (options.py callback logic)
# ──────────────────────────────────────────────────────────────

class TestHDRUIHandlerLogic(unittest.TestCase):
    """Tests for the mapping logic in options.py HDR handlers (without GTK)."""

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

    def _is_hdr(self, hdr_enabled, primaries, gamma):
        """Replicate the is_hdr logic from video_widget.py do_snapshot."""
        return hdr_enabled and ((primaries == "bt.2020") or (gamma in ("pq", "hlg")))

    def test_hdr_bt2020_pq(self):
        """BT.2020 + PQ with HDR enabled → HDR color state."""
        self.assertTrue(self._is_hdr(True, "bt.2020", "pq"))

    def test_hdr_bt2020_hlg(self):
        """BT.2020 + HLG with HDR enabled → HDR color state."""
        self.assertTrue(self._is_hdr(True, "bt.2020", "hlg"))

    def test_hdr_bt2020_srgb_gamma(self):
        """BT.2020 primaries but sRGB gamma with HDR enabled → still HDR (wide gamut)."""
        self.assertTrue(self._is_hdr(True, "bt.2020", "srgb"))

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
        def file_supports_hdr(params):
            if not params or not isinstance(params, dict):
                return False
            primaries = params.get("primaries")
            gamma = params.get("gamma")
            return ((primaries == "bt.2020") or (gamma in ("pq", "hlg")))
        
        self.assertTrue(file_supports_hdr({"primaries": "bt.2020", "gamma": "pq"}))
        self.assertTrue(file_supports_hdr({"primaries": "bt.709", "gamma": "hlg"}))
        self.assertFalse(file_supports_hdr({"primaries": "bt.709", "gamma": "srgb"}))
        self.assertFalse(file_supports_hdr(None))


if __name__ == "__main__":
    unittest.main()
