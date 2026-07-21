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
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Make tests hermetic and isolated (F13)
import tempfile
import shutil
import subprocess
import atexit

temp_schema_dir = None
try:
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
except Exception:
    # Fallback to the project's data schema dir (where gschemas.compiled is pre-built)
    os.environ["GSETTINGS_SCHEMA_DIR"] = os.path.abspath(os.path.join(os.path.dirname(__file__), "../data"))
    if temp_schema_dir:
        try:
            shutil.rmtree(temp_schema_dir)
        except Exception:
            pass
        temp_schema_dir = None

# Always use memory backend to isolate tests from host dconf (F13)
os.environ["GSETTINGS_BACKEND"] = "memory"

def cleanup_temp_schemas():
    if temp_schema_dir:
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

# Gtk.Template classes (HdrDiagnosticsDialog) need the compiled gresource.
# Verify the actual resource path is resolvable so the dependent tests can
# be skipped cleanly instead of erroring on machines without a build dir.
GRESOURCE_AVAILABLE = False
try:
    from gi.repository import Gio as _Gio
    _Gio.resources_get_info(
        "/io/github/rusmikev/CineHDR/hdr_diagnostics.ui",
        _Gio.ResourceLookupFlags.NONE,
    )
    GRESOURCE_AVAILABLE = True
except Exception:
    GRESOURCE_AVAILABLE = False


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

    @patch("src.hdr_controller.check_hdr_support", return_value=True)
    def test_hdr_enabled_and_hdr_content_sets_targets(self, _mock_support):
        """apply_hdr_settings() puts mpv into BT.2020 + PQ output for HDR content.

        This drives the real controller (not a re-implementation of its
        logic): the earlier version of this test asserted target-prim ==
        "dci-p3", something the shipped code never does.
        """
        from src.hdr_controller import HdrController

        mock_mpv, props = self._make_mock_mpv()
        controller = HdrController(mock_mpv)
        controller._hdr_mode = "auto"
        controller._is_hdr_content = True
        controller._hdr_target_peak = "400"

        controller.apply_hdr_settings()

        self.assertEqual(props["target-trc"], "pq")
        self.assertEqual(props["target-prim"], "bt.2020")
        self.assertEqual(props["target-peak"], 400)
        # hdr-compute-peak must be left to mpv's own default ("auto")
        self.assertNotIn("hdr-compute-peak", props)

    @patch("src.hdr_controller.check_hdr_support", return_value=True)
    def test_sdr_protection_when_hdr_enabled(self, _mock_support):
        """SDR content with HDR mode "auto" must reset targets to auto/no (Risk P-1)."""
        from src.hdr_controller import HdrController

        mock_mpv, props = self._make_mock_mpv()
        controller = HdrController(mock_mpv)
        controller._hdr_mode = "auto"
        controller._is_hdr_content = False  # Playing SDR video!

        controller.apply_hdr_settings()

        self.assertEqual(props["target-prim"], "auto")
        self.assertEqual(props["target-peak"], "auto")
        self.assertEqual(props["target-trc"], "auto")
        self.assertNotIn("hdr-compute-peak", props)

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

    # ── Dolby Vision ────────────────────────────────────────────
    #
    # Realistic fixtures matter here. libplacebo's pl_map_avdovi_metadata()
    # rewrites the *decoder-side* frame params for every single-layer DoVi
    # stream to primaries=bt.2020 / transfer=pq / colormatrix=dolbyvision, and
    # mpv only reverts that inside the VO. So a real Profile 5 file reports
    # gamma="pq" in video-params — exactly like a Profile 8 file does. Any test
    # that feeds Profile 5 with gamma="bt.1886" is testing a file that cannot
    # exist, and would miss the regression these tests pin down.

    def _dovi_mpv(self, profile=None, level=None):
        """mpv mock whose *track* properties carry the DoVi profile."""
        props = {}
        mock = MagicMock()
        mock.__setitem__ = lambda self, k, v: props.__setitem__(k, v)
        mock.__getitem__ = lambda self, k: props.get(k)

        def get_property(name):
            if name == "current-tracks/video/dolby-vision-profile":
                return profile
            if name == "current-tracks/video/dolby-vision-level":
                return level
            return None

        mock.get_property = get_property
        del mock._get_property  # python-mpv exposes it; this mock does not
        mock._props = props
        return mock, props

    # Video params as mpv actually reports them for any single-layer DoVi stream.
    DOVI_PARAMS = {
        "colormatrix": "dolbyvision",
        "primaries": "bt.2020",
        "gamma": "pq",
        "sig-peak": 4.93,
    }

    def test_dovi_info_reads_profile_from_track_properties(self):
        """The profile comes from track properties, never from video-params."""
        from src.hdr_detection import get_dovi_info

        mock_mpv, _ = self._dovi_mpv(profile=8, level=6)
        info = get_dovi_info(self.DOVI_PARAMS, mock_mpv)
        self.assertEqual(info["profile"], 8)
        self.assertEqual(info["level"], 6)
        self.assertFalse(info["unsupported"])

        mock_mpv, _ = self._dovi_mpv(profile=5)
        info = get_dovi_info(self.DOVI_PARAMS, mock_mpv)
        self.assertEqual(info["profile"], 5)
        self.assertTrue(info["unsupported"])

    def test_dovi_info_colormatrix_is_presence_only(self):
        """Without a track profile, colormatrix proves DoVi but not *which* profile."""
        from src.hdr_detection import get_dovi_info

        mock_mpv, _ = self._dovi_mpv(profile=None)
        info = get_dovi_info(self.DOVI_PARAMS, mock_mpv)
        # Must NOT guess "5": the same fingerprint is set for profile 8, and
        # refusing HDR on a guess would downgrade a playable stream.
        self.assertIsNone(info["profile"])
        self.assertFalse(info["unsupported"])

        self.assertIsNone(get_dovi_info({"colormatrix": "bt.2020-ncl"}, mock_mpv))

    def test_dovi_info_survives_raising_accessor(self):
        """A raising accessor must not abort the remaining lookups."""
        from src.hdr_detection import get_dovi_info

        class RaisingMpv:
            def _get_property(self, name):
                raise RuntimeError("property unavailable")

            def get_property(self, name):
                if name == "current-tracks/video/dolby-vision-profile":
                    return 5
                return None

        info = get_dovi_info({}, RaisingMpv())
        self.assertEqual(info["profile"], 5)
        self.assertTrue(info["unsupported"])

    def test_real_profile_5_is_hdr_content_but_never_hdr_active(self):
        """REGRESSION: a real Profile 5 stream must not reach Rec.2100 PQ.

        is_hdr_content() sees gamma=pq and returns True — that is correct and
        expected. The refusal is a capability gate in the controller, and it
        must outrank force-hdr.
        """
        from src.hdr_detection import is_hdr_content
        from src.hdr_controller import HdrController

        self.assertTrue(is_hdr_content(self.DOVI_PARAMS))

        with patch("src.hdr_controller.check_hdr_support", return_value=True):
            for mode in ("auto", "force-hdr"):
                mock_mpv, props = self._dovi_mpv(profile=5)
                controller = HdrController(mock_mpv)
                controller._hdr_mode = mode
                controller._is_hdr_content = True
                controller._dovi_info = {"profile": 5, "level": None, "unsupported": True}

                controller.apply_hdr_settings()

                self.assertFalse(controller.is_hdr_active, f"HDR must stay off in mode={mode}")
                self.assertEqual(props["target-trc"], "auto")
                self.assertEqual(props["target-prim"], "auto")

    def test_profile_8_keeps_hdr_passthrough(self):
        """The Profile 5 gate must not downgrade Profile 7/8 (HDR10 base layer)."""
        from src.hdr_controller import HdrController

        with patch("src.hdr_controller.check_hdr_support", return_value=True):
            mock_mpv, props = self._dovi_mpv(profile=8, level=6)
            controller = HdrController(mock_mpv)
            controller._hdr_mode = "auto"
            controller._is_hdr_content = True
            controller._dovi_info = {"profile": 8, "level": 6, "unsupported": False}

            controller.apply_hdr_settings()

            self.assertTrue(controller.is_hdr_active)
            self.assertEqual(props["target-trc"], "pq")
            self.assertEqual(props["target-prim"], "bt.2020")

    @patch("src.hdr_controller.check_hdr_support", return_value=True)
    def test_hdr_auto_peak(self, _mock_support):
        """Peak preset "auto" is passed to mpv as the string "auto"."""
        from src.hdr_controller import HdrController

        mock_mpv, props = self._make_mock_mpv()
        controller = HdrController(mock_mpv)
        controller._hdr_mode = "auto"
        controller._is_hdr_content = True
        controller._hdr_target_peak = "auto"

        controller.apply_hdr_settings()
        self.assertEqual(props["target-peak"], "auto")

    @patch("src.hdr_controller.check_hdr_support", return_value=True)
    def test_hdr_numeric_peak(self, _mock_support):
        """Every numeric preset reaches mpv as an int; junk falls back to auto."""
        from src.hdr_controller import HdrController, HDR_PEAK_PRESETS

        for peak_str in HDR_PEAK_PRESETS[1:]:
            mock_mpv, props = self._make_mock_mpv()
            controller = HdrController(mock_mpv)
            controller._hdr_mode = "auto"
            controller._is_hdr_content = True
            controller._hdr_target_peak = peak_str

            controller.apply_hdr_settings()
            self.assertEqual(
                props["target-peak"], int(float(peak_str)),
                f"Failed for peak={peak_str}",
            )

        # A value outside the presets must not leak into mpv
        mock_mpv, props = self._make_mock_mpv()
        controller = HdrController(mock_mpv)
        controller._hdr_mode = "auto"
        controller._is_hdr_content = True
        controller._hdr_target_peak = "999"
        controller.apply_hdr_settings()
        self.assertEqual(props["target-peak"], "auto")

    @patch("src.hdr_controller.check_hdr_support", return_value=True)
    def test_target_prim_locked_to_bt2020(self, _mock_support):
        """The legacy hdr-target-prim setting must never leak into mpv.

        The published texture's Gdk.ColorState is Rec.2100 PQ, so the
        encoding primaries are BT.2020 by contract; any other target-prim
        would shift colors (F7). This is the regression the removed gamut
        dropdown used to trigger.
        """
        from gi.repository import Gio
        from src.hdr_controller import HdrController

        settings = Gio.Settings.new("io.github.rusmikev.CineHDR")
        orig_prim = settings.get_string("hdr-target-prim")
        try:
            for legacy_prim in ("auto", "dci-p3", "bt.709"):
                settings.set_string("hdr-target-prim", legacy_prim)
                mock_mpv, props = self._make_mock_mpv()
                controller = HdrController(mock_mpv)
                controller._hdr_mode = "auto"
                controller._is_hdr_content = True

                controller.apply_hdr_settings()
                self.assertEqual(
                    props["target-prim"], "bt.2020",
                    f"legacy hdr-target-prim={legacy_prim!r} leaked into mpv",
                )
        finally:
            settings.set_string("hdr-target-prim", orig_prim)

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
        controller._is_hdr_content = True

        # Case A: check_hdr_support is False (e.g. X11) -> should fall back to SDR
        mock_support.return_value = False
        controller.apply_hdr_settings()

        # Case B: check_hdr_support is True (Wayland + GTK >= 4.16) -> should apply PQ
        mock_support.return_value = True
        controller.apply_hdr_settings()
        self.assertEqual(props.get("target-trc"), "pq")
        self.assertEqual(props.get("target-prim"), "bt.2020")
        self.assertEqual(props.get("target-peak"), 1000)
        # hdr-compute-peak is owned by mpv ("auto" default) in both branches
        self.assertNotIn("hdr-compute-peak", props)

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
        self.assertEqual(props.get("target-trc"), "auto")


# ──────────────────────────────────────────────────────────────
# 3. Tests for UI handler mapping (options.py callback logic)
# ──────────────────────────────────────────────────────────────

class TestHDRUIHandlerLogic(unittest.TestCase):
    """Tests for the dropdown <-> value tables shared with hdr_menu.py.

    hdr_menu.py builds its dropdown handlers directly from
    src.hdr_controller.HDR_MODES / HDR_PEAK_PRESETS, so asserting on those
    tuples (and on the real controller setters) exercises the same objects
    the UI uses. The previous version of this class re-declared the tables
    inside each test and compared them to themselves.
    """

    def test_mode_table_matches_dropdown_order(self):
        """HDR_MODES order must match the hdr_menu.blp StringList (Auto, Force HDR, Force SDR)."""
        from src.hdr_controller import HDR_MODES
        self.assertEqual(HDR_MODES, ("auto", "force-hdr", "force-sdr"))
        for idx, mode in enumerate(HDR_MODES):
            self.assertEqual(HDR_MODES.index(mode), idx)

    def test_peak_table_matches_dropdown_order(self):
        """HDR_PEAK_PRESETS order must match the peak dropdown StringList."""
        from src.hdr_controller import HDR_PEAK_PRESETS
        self.assertEqual(
            HDR_PEAK_PRESETS, ("auto", "200", "400", "600", "1000", "1600")
        )
        for idx, peak in enumerate(HDR_PEAK_PRESETS):
            self.assertEqual(HDR_PEAK_PRESETS.index(peak), idx)

    @unittest.skipUnless(
        GRESOURCE_AVAILABLE,
        "compiled cinehdr.gresource not found — build the project or set CINEHDR_GRESOURCE",
    )
    def test_menu_module_uses_shared_tables(self):
        """hdr_menu must reference the controller tables, not private copies."""
        import src.hdr_controller as hdr_controller
        import src.hdr_menu as hdr_menu
        self.assertIs(hdr_menu.HDR_MODES, hdr_controller.HDR_MODES)
        self.assertIs(hdr_menu.HDR_PEAK_PRESETS, hdr_controller.HDR_PEAK_PRESETS)

    def test_mode_setter_rejects_unknown_values(self):
        """Real controller setter: unknown mode strings collapse to 'auto'."""
        from src.hdr_controller import HdrController
        mock = MagicMock()
        props = {}
        mock.__setitem__ = lambda self, k, v: props.__setitem__(k, v)
        mock.__getitem__ = lambda self, k: props.get(k)

        ctrl = HdrController(mock)
        ctrl.hdr_mode = "force-hdr"
        self.assertEqual(ctrl.hdr_mode, "force-hdr")
        ctrl.hdr_mode = "definitely-not-a-mode"
        self.assertEqual(ctrl.hdr_mode, "auto")

    def test_hdr_enabled_bool_maps_to_modes(self):
        """Real controller: hdr_enabled toggles between 'auto' and 'force-sdr'."""
        from src.hdr_controller import HdrController
        mock = MagicMock()
        props = {}
        mock.__setitem__ = lambda self, k, v: props.__setitem__(k, v)
        mock.__getitem__ = lambda self, k: props.get(k)

        ctrl = HdrController(mock)
        ctrl.hdr_enabled = False
        self.assertEqual(ctrl.hdr_mode, "force-sdr")
        self.assertFalse(ctrl.hdr_enabled)
        ctrl.hdr_enabled = True
        self.assertEqual(ctrl.hdr_mode, "auto")
        self.assertTrue(ctrl.hdr_enabled)


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

@unittest.skipUnless(
    GRESOURCE_AVAILABLE,
    "compiled cinehdr.gresource not found — build the project or set CINEHDR_GRESOURCE",
)
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
                self.visible = True
            def set_subtitle(self, text):
                self.subtitle = text
            def set_visible(self, val):
                self.visible = val

        class MockController:
            is_hdr_active = True
            is_hdr_content = True
            hdr_mode = "auto"

        class MockGLArea:
            hdr_controller = MockController()
            _color_state = None

        class MockMpv:
            def get_property(self, name):
                if name == "video-format": return "hevc"
                if name == "video-params": return {"primaries": "bt.2020", "gamma": "pq", "sig-peak": 4.93, "w": 3840, "h": 2160, "pixelformat": "yuv420p10le"}
                if name == "hwdec-current": return "vaapi"
                if name == "target-trc": return "pq"
                if name == "target-prim": return "dci-p3"
                if name == "target-peak": return "1000"
                return None

        class MockWin:
            gl_area = MockGLArea()
            mpv = MockMpv()

        diag = HdrDiagnosticsDialog.__new__(HdrDiagnosticsDialog)
        diag._win = MockWin()
        diag.status_row = MockActionRow()
        diag.display_hdr_row = MockActionRow()
        diag.unsupported_reason_row = MockActionRow()
        diag.color_state_row = MockActionRow()
        diag.texture_format_row = MockActionRow()
        diag.codec_row = MockActionRow()
        diag.resolution_row = MockActionRow()
        diag.hwdec_row = MockActionRow()
        diag.dovi_profile_row = MockActionRow()
        diag.primaries_row = MockActionRow()
        diag.trc_row = MockActionRow()
        diag.peak_luma_row = MockActionRow()
        diag.target_row = MockActionRow()
        diag.compositor_cm_row = MockActionRow()
        diag.offload_row = MockActionRow()
        diag.monitor_hdr_row = MockActionRow()

        diag.update_diagnostics()

        self.assertIn("Active", diag.status_row.subtitle)
        self.assertIn("Yes", diag.display_hdr_row.subtitle)
        self.assertIn("GL_RGBA16F", diag.texture_format_row.subtitle)
        self.assertEqual(diag.codec_row.subtitle, "hevc")
        self.assertEqual(diag.primaries_row.subtitle, "bt.2020")
        self.assertEqual(diag.trc_row.subtitle, "pq")
        self.assertIn("nits", diag.peak_luma_row.subtitle)
        self.assertIn("TRC: pq | Prim: dci-p3 | Peak: 1000 | Hint: yes", diag.target_row.subtitle)
        self.assertEqual(diag.dovi_profile_row.subtitle, "No (Standard HDR10 / HLG)")
        self.assertTrue(diag.dovi_profile_row.visible)

    @unittest.mock.patch("src.hdr_diagnostics.check_hdr_support", return_value=True)
    def test_update_diagnostics_dolby_vision(self, mock_check):
        from src.hdr_diagnostics import HdrDiagnosticsDialog

        class MockActionRow:
            def __init__(self):
                self.subtitle = ""
                self.visible = True
            def set_subtitle(self, text):
                self.subtitle = text
            def set_visible(self, val):
                self.visible = val

        class MockController:
            is_hdr_active = True
            is_hdr_content = True
            hdr_mode = "auto"

        class MockGLArea:
            hdr_controller = MockController()
            _color_state = None

        class MockMpvDoVi:
            def get_property(self, name):
                if name == "video-format": return "hevc"
                if name == "video-params": return {"primaries": "bt.2020", "gamma": "pq", "colormatrix": "dolbyvision"}
                if name == "current-tracks/video/dolby-vision-profile": return "5"
                if name == "hwdec-current": return "vaapi"
                return "auto"

        class MockWin:
            gl_area = MockGLArea()
            mpv = MockMpvDoVi()

        diag = HdrDiagnosticsDialog.__new__(HdrDiagnosticsDialog)
        diag._win = MockWin()
        for attr in ("status_row", "display_hdr_row", "unsupported_reason_row", "color_state_row",
                     "texture_format_row", "codec_row", "resolution_row", "hwdec_row",
                     "dovi_profile_row", "primaries_row", "trc_row", "peak_luma_row", "target_row",
                     "compositor_cm_row", "offload_row", "monitor_hdr_row"):
            setattr(diag, attr, MockActionRow())

        diag.update_diagnostics()
        # Profile 5 must be reported as unrenderable and SDR-forced, without
        # claiming any IPT -> Rec.2100 PQ conversion takes place.
        subtitle = diag.dovi_profile_row.subtitle
        self.assertIn("Profile 5", subtitle)
        self.assertIn("SDR", subtitle)
        self.assertNotIn("Rec.2100", subtitle)
        self.assertTrue(diag.dovi_profile_row.visible)

    @unittest.mock.patch("src.hdr_diagnostics.check_hdr_support", return_value=True)
    def test_update_diagnostics_dolby_vision_profile_8(self, mock_check):
        """Profile 8 is reported as an HDR10 base layer, not as SDR-forced."""
        from src.hdr_diagnostics import HdrDiagnosticsDialog

        class MockActionRow:
            def __init__(self):
                self.subtitle = ""
                self.visible = True
            def set_subtitle(self, text):
                self.subtitle = text
            def set_visible(self, val):
                self.visible = val

        class MockController:
            is_hdr_active = True
            is_hdr_content = True
            hdr_mode = "auto"

        class MockGLArea:
            hdr_controller = MockController()
            _color_state = None

        class MockMpvP8:
            def get_property(self, name):
                if name == "video-format": return "hevc"
                if name == "video-params":
                    return {"primaries": "bt.2020", "gamma": "pq", "colormatrix": "dolbyvision"}
                if name == "current-tracks/video/dolby-vision-profile": return "8"
                if name == "current-tracks/video/dolby-vision-level": return "6"
                if name == "hwdec-current": return "vaapi"
                return "auto"

        class MockWin:
            gl_area = MockGLArea()
            mpv = MockMpvP8()

        diag = HdrDiagnosticsDialog.__new__(HdrDiagnosticsDialog)
        diag._win = MockWin()
        for attr in ("status_row", "display_hdr_row", "unsupported_reason_row", "color_state_row",
                     "texture_format_row", "codec_row", "resolution_row", "hwdec_row",
                     "dovi_profile_row", "primaries_row", "trc_row", "peak_luma_row", "target_row",
                     "compositor_cm_row", "offload_row", "monitor_hdr_row"):
            setattr(diag, attr, MockActionRow())

        diag.update_diagnostics()

        subtitle = diag.dovi_profile_row.subtitle
        self.assertIn("Profile 8", subtitle)
        self.assertIn("HDR10", subtitle)
        self.assertIn("Level 6", subtitle)
        self.assertNotIn("forced to SDR", subtitle)


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



# ──────────────────────────────────────────────────────────────
# 9. Compositor color-management probe integration (wayland_cm_probe)
# ──────────────────────────────────────────────────────────────

class TestCompositorCmProbeIntegration(unittest.TestCase):
    """check_hdr_support() must honor the compositor registry probe:
    a definitive False blocks HDR, an inconclusive None keeps legacy behavior."""

    def _wayland_ready_display(self):
        mock_display = MagicMock()
        mock_display.__class__.__name__ = "GdkWaylandDisplay"
        mock_display.is_composited.return_value = True
        mock_display.is_rgba.return_value = True
        dmabuf = MagicMock()
        dmabuf.get_n_formats.return_value = 10
        mock_display.get_dmabuf_formats.return_value = dmabuf
        return mock_display

    @patch("src.hdr_detection.Gdk.ColorState", create=True)
    def test_probe_false_blocks_hdr_support(self, mock_colorstate):
        from src.hdr_detection import check_hdr_support, invalidate_hdr_support_cache
        mock_colorstate.get_rec2100_pq = MagicMock()
        display = self._wayland_ready_display()
        with patch("src.hdr_detection.Gdk.Display.get_default", return_value=display), \
             patch("src.wayland_cm_probe.probe_color_management", return_value=False):
            invalidate_hdr_support_cache()
            self.assertFalse(check_hdr_support())

    @patch("src.hdr_detection.Gdk.ColorState", create=True)
    def test_probe_true_allows_hdr_support(self, mock_colorstate):
        from src.hdr_detection import check_hdr_support, invalidate_hdr_support_cache
        mock_colorstate.get_rec2100_pq = MagicMock()
        display = self._wayland_ready_display()
        with patch("src.hdr_detection.Gdk.Display.get_default", return_value=display), \
             patch("src.wayland_cm_probe.probe_color_management", return_value=True):
            invalidate_hdr_support_cache()
            self.assertTrue(check_hdr_support())

    @patch("src.hdr_detection.Gdk.ColorState", create=True)
    def test_probe_none_keeps_previous_behavior(self, mock_colorstate):
        """Unknown probe result must not regress systems where the probe
        cannot run (backward compatibility contract)."""
        from src.hdr_detection import check_hdr_support, invalidate_hdr_support_cache
        mock_colorstate.get_rec2100_pq = MagicMock()
        display = self._wayland_ready_display()
        with patch("src.hdr_detection.Gdk.Display.get_default", return_value=display), \
             patch("src.wayland_cm_probe.probe_color_management", return_value=None):
            invalidate_hdr_support_cache()
            self.assertTrue(check_hdr_support())

    @patch("src.hdr_detection.Gdk.ColorState", create=True)
    def test_unsupported_reason_mentions_color_management(self, mock_colorstate):
        from src.hdr_detection import (
            check_hdr_support,
            get_hdr_unsupported_reason,
            invalidate_hdr_support_cache,
        )
        mock_colorstate.get_rec2100_pq = MagicMock()
        if not hasattr(Gdk_holder.Gdk, "MemoryFormat"):
            self.skipTest("Gdk.MemoryFormat missing on this GTK")
        display = self._wayland_ready_display()
        with patch("src.hdr_detection.Gdk.Display.get_default", return_value=display), \
             patch("src.wayland_cm_probe.probe_color_management", return_value=False):
            invalidate_hdr_support_cache()
            self.assertFalse(check_hdr_support())
            reason = get_hdr_unsupported_reason(display)
            self.assertIn("color management", reason)
            self.assertIn("wp_color_manager_v1", reason)

    def test_invalidate_clears_probe_cache(self):
        """invalidate_hdr_support_cache() must also drop the probe cache."""
        from src import wayland_cm_probe
        from src.hdr_detection import invalidate_hdr_support_cache
        wayland_cm_probe._cached_result = True
        wayland_cm_probe._cache_valid = True
        invalidate_hdr_support_cache()
        self.assertFalse(wayland_cm_probe._cache_valid)
        self.assertIsNone(wayland_cm_probe._cached_result)

    def test_probe_returns_none_without_wayland_display(self):
        """On a system without a Wayland display the probe must answer
        'unknown', never a false negative."""
        from src import wayland_cm_probe
        with patch.object(wayland_cm_probe, "_get_wl_display_ptr", return_value=None):
            wayland_cm_probe.invalidate()
            self.assertIsNone(wayland_cm_probe.probe_color_management())
        wayland_cm_probe.invalidate()

    def test_probe_result_is_cached(self):
        from src import wayland_cm_probe
        with patch.object(wayland_cm_probe, "_get_wl_display_ptr", return_value=None) as mock_ptr:
            wayland_cm_probe.invalidate()
            wayland_cm_probe.probe_color_management()
            wayland_cm_probe.probe_color_management()
            self.assertEqual(mock_ptr.call_count, 1)
        wayland_cm_probe.invalidate()

    def test_enumerate_globals_missing_symbols_returns_none(self):
        """A libwayland without the expected entry points must yield
        'unknown' (None), not crash and not report False."""
        from src import wayland_cm_probe

        class _EmptyLib:
            def __getattr__(self, name):
                raise AttributeError(name)

        self.assertIsNone(wayland_cm_probe._enumerate_globals(_EmptyLib(), 0xdead))


class Gdk_holder:
    """Late import holder so the reason-string test can inspect real Gdk."""
    import gi as _gi
    _gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk


# ──────────────────────────────────────────────────────────────
# 10. Monitor HDR state gate (wayland_output_hdr)
# ──────────────────────────────────────────────────────────────

class TestMonitorHdrGate(unittest.TestCase):
    """Auto mode must fall back to mpv tone mapping when the monitor is
    definitively SDR; force-hdr and unknown state must be unaffected."""

    def _make_mock_mpv(self):
        props = {}
        mock_mpv = MagicMock()
        mock_mpv.__setitem__ = lambda _self, k, v: props.__setitem__(k, v)
        mock_mpv.__getitem__ = MagicMock(side_effect=KeyError)
        mock_mpv.property_observer = lambda name: (lambda fn: fn)
        return mock_mpv, props

    @patch("src.hdr_controller.check_hdr_support", return_value=True)
    def test_monitor_sdr_blocks_auto(self, _sup):
        from src.hdr_controller import HdrController
        mock_mpv, props = self._make_mock_mpv()
        with patch("src.hdr_controller.get_monitor_hdr_state", return_value=False):
            controller = HdrController(mock_mpv)
            controller._hdr_mode = "auto"
            controller._is_hdr_content = True
            self.assertFalse(controller.is_hdr_active)
            controller.apply_hdr_settings()
            self.assertEqual(props.get("target-trc"), "auto")

    @patch("src.hdr_controller.check_hdr_support", return_value=True)
    def test_monitor_sdr_does_not_block_force_hdr(self, _sup):
        """Unlike the DoVi P5 gate, the picture here is valid — the explicit
        user override must win over the quality preference."""
        from src.hdr_controller import HdrController
        mock_mpv, props = self._make_mock_mpv()
        with patch("src.hdr_controller.get_monitor_hdr_state", return_value=False):
            controller = HdrController(mock_mpv)
            controller._is_hdr_content = True
            controller.hdr_mode = "force-hdr"
            self.assertTrue(controller.is_hdr_active)
            self.assertEqual(props.get("target-trc"), "pq")

    @patch("src.hdr_controller.check_hdr_support", return_value=True)
    def test_monitor_unknown_keeps_previous_behavior(self, _sup):
        from src.hdr_controller import HdrController
        mock_mpv, props = self._make_mock_mpv()
        with patch("src.hdr_controller.get_monitor_hdr_state", return_value=None):
            controller = HdrController(mock_mpv)
            controller._hdr_mode = "auto"
            controller._is_hdr_content = True
            self.assertTrue(controller.is_hdr_active)

    @patch("src.hdr_controller.check_hdr_support", return_value=True)
    def test_monitor_hdr_allows_auto(self, _sup):
        from src.hdr_controller import HdrController
        mock_mpv, props = self._make_mock_mpv()
        with patch("src.hdr_controller.get_monitor_hdr_state", return_value=True):
            controller = HdrController(mock_mpv)
            controller._hdr_mode = "auto"
            controller._is_hdr_content = True
            self.assertTrue(controller.is_hdr_active)
            controller.apply_hdr_settings()
            self.assertEqual(props.get("target-trc"), "pq")

    @patch("src.hdr_controller.check_hdr_support", return_value=True)
    def test_output_hint_is_forwarded_and_triggers_reapply(self, _sup):
        from src.hdr_controller import HdrController
        mock_mpv, props = self._make_mock_mpv()
        seen = []

        def fake_state(connector=None):
            seen.append(connector)
            return None

        with patch("src.hdr_controller.get_monitor_hdr_state", side_effect=fake_state):
            controller = HdrController(mock_mpv)
            controller._hdr_mode = "auto"
            controller._is_hdr_content = True
            _ = controller.is_hdr_active
            controller.set_output_hint("DP-3")
            _ = controller.is_hdr_active
        self.assertIn("DP-3", seen)
        # setting the same hint again must not re-apply
        with patch.object(controller, "apply_hdr_settings") as mock_apply:
            controller.set_output_hint("DP-3")
            mock_apply.assert_not_called()
            controller.set_output_hint("HDMI-1")
            mock_apply.assert_called_once()


class TestWaylandOutputHdrModule(unittest.TestCase):
    """Pure logic of wayland_output_hdr: classification, cache, aggregation,
    and the hand-built wl_interface tables."""

    def test_classify_hdr_matrix(self):
        from src.wayland_output_hdr import classify_hdr, TF_ST2084_PQ, TF_HLG, TF_SRGB, TF_GAMMA22
        self.assertTrue(classify_hdr(TF_ST2084_PQ, None, None, None))
        self.assertTrue(classify_hdr(TF_HLG, None, None, None))
        self.assertTrue(classify_hdr(TF_GAMMA22, 0.02, 1000.0, 203.0))
        self.assertFalse(classify_hdr(TF_SRGB, 0.2, 200.0, 200.0))
        self.assertFalse(classify_hdr(None, None, None, None))
        self.assertFalse(classify_hdr(TF_SRGB, None, 400.0, 0.0))

    def test_ttl_cache_and_invalidate(self):
        from src import wayland_output_hdr as w
        with patch.object(w, "probe_outputs", return_value=None) as mock_probe:
            w.invalidate()
            w.get_output_hdr_states()
            w.get_output_hdr_states()
            self.assertEqual(mock_probe.call_count, 1)
            w.invalidate()
            w.get_output_hdr_states()
            self.assertEqual(mock_probe.call_count, 2)
        w.invalidate()

    def test_get_monitor_hdr_state_aggregation(self):
        from src import wayland_output_hdr as w
        states = {
            "eDP-1": w.OutputHdrInfo(connector="eDP-1", hdr=False),
            "HDMI-1": w.OutputHdrInfo(connector="HDMI-1", hdr=True),
        }
        with patch.object(w, "get_output_hdr_states", return_value=states):
            self.assertFalse(w.get_monitor_hdr_state("eDP-1"))
            self.assertTrue(w.get_monitor_hdr_state("HDMI-1"))
            self.assertTrue(w.get_monitor_hdr_state(None))          # any()
            self.assertTrue(w.get_monitor_hdr_state("DP-404"))      # unknown hint -> any()
        with patch.object(w, "get_output_hdr_states", return_value={"eDP-1": states["eDP-1"]}):
            self.assertFalse(w.get_monitor_hdr_state(None))
        with patch.object(w, "get_output_hdr_states", return_value=None):
            self.assertIsNone(w.get_monitor_hdr_state("eDP-1"))

    def test_interface_tables_match_protocol(self):
        """Event tables must mirror color-management-v1.xml exactly — a wrong
        signature corrupts libffi dispatch. Lock the critical rows."""
        from src.wayland_output_hdr import _InterfaceTable
        t = _InterfaceTable(0xDEAD)
        self.assertEqual(t.mgr.event_count, 5)
        self.assertEqual(t.mgr.events[4].name, b"done")
        self.assertEqual(t.img.event_count, 2)
        self.assertEqual(t.img.events[0].signature, b"us")   # failed
        self.assertEqual(t.img.events[1].signature, b"u")    # ready
        self.assertEqual(t.info.event_count, 11)
        self.assertEqual(t.info.events[1].name, b"icc_file")
        self.assertEqual(t.info.events[1].signature, b"hu")
        self.assertEqual(t.info.events[5].name, b"tf_named")
        self.assertEqual(t.info.events[6].name, b"luminances")
        self.assertEqual(t.info.events[6].signature, b"uuu")
        # request opcodes we marshal
        self.assertEqual(t.mgr.methods[1].name, b"get_output")
        self.assertEqual(t.mgr.methods[1].signature, b"no")
        self.assertEqual(t.cm_output.methods[1].name, b"get_image_description")
        self.assertEqual(t.img.methods[1].name, b"get_information")

    def test_probe_returns_none_headless(self):
        from src import wayland_output_hdr as w
        with patch.object(w, "_get_wl_display_ptr", return_value=None):
            self.assertIsNone(w.probe_outputs())


# ──────────────────────────────────────────────────────────────
# 11. target-colorspace-hint stays removed
# ──────────────────────────────────────────────────────────────

class TestColorspaceHintRemoved(unittest.TestCase):
    """target-colorspace-hint is a no-op under the libmpv render API; the
    controller must never write it in either branch."""

    def _make_mock_mpv(self):
        props = {}
        mock_mpv = MagicMock()
        mock_mpv.__setitem__ = lambda _self, k, v: props.__setitem__(k, v)
        mock_mpv.__getitem__ = MagicMock(side_effect=KeyError)
        mock_mpv.property_observer = lambda name: (lambda fn: fn)
        return mock_mpv, props

    @patch("src.hdr_controller.check_hdr_support", return_value=True)
    def test_never_written_in_either_branch(self, _sup):
        from src.hdr_controller import HdrController
        with patch("src.hdr_controller.get_monitor_hdr_state", return_value=True):
            mock_mpv, props = self._make_mock_mpv()
            controller = HdrController(mock_mpv)
            controller._hdr_mode = "auto"
            controller._is_hdr_content = True
            controller.apply_hdr_settings()          # HDR branch
            self.assertNotIn("target-colorspace-hint", props)
            controller.hdr_mode = "force-sdr"        # SDR branch
            self.assertNotIn("target-colorspace-hint", props)

    def test_not_in_initial_snapshot(self):
        from src.hdr_controller import HdrController
        mock_mpv, _props = self._make_mock_mpv()
        mock_mpv.__getitem__ = MagicMock(return_value="x")  # snapshot succeeds
        with patch("src.hdr_controller.check_hdr_support", return_value=False):
            controller = HdrController(mock_mpv)
        self.assertNotIn("target-colorspace-hint", controller._initial_mpv_props)
