# Copyright 2026 rusmikev
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit and regression tests for GNOME Wayland pipeline trace validation."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_DIR))

from tools.validation.parse_wayland_color_trace import (
    PRIMARY_COMPOSITE_REVISION,
    validate_composite_window_trace,
    validate_trace,
)
from src.gtk_cm_policy import (
    CmCaps,
    gtk_color_managed,
    gtk_can_tag_hdr,
    TF_PQ,
    TF_SRGB,
    TF_GAMMA22,
    TF_EXT_LINEAR,
    TF_COMPOUND_POWER_2_4,
    PRIM_BT2020,
    PRIM_SRGB,
    INTENT_PERCEPTUAL,
    FEAT_PARAMETRIC,
    FEAT_SET_PRIMARIES,
)
from tools.validation.run_gnome_full_trace_validation import (
    parse_wayland_info_color_manager,
    build_cm_caps_from_wayland_info,
    compile_tagger,
    run_bounded_session,
    TAGGER_SRC,
    HDR_FIXTURE_DEFAULT,
    SDR_FIXTURE_DEFAULT,
)


class TestGnomePipelineTrace(unittest.TestCase):
    """Test suite verifying the CineHDR pipeline on GNOME / Mutter with Wayland Color Management v2."""

    def setUp(self):
        # GNOME 51.1 Mutter reference capability set
        self.gnome51_caps = CmCaps(
            intents=frozenset({INTENT_PERCEPTUAL}),
            features=frozenset({FEAT_PARAMETRIC, FEAT_SET_PRIMARIES}),
            tfs=frozenset({TF_SRGB, TF_PQ, TF_GAMMA22, TF_EXT_LINEAR}),
            primaries=frozenset({PRIM_SRGB, PRIM_BT2020}),
        )

    def test_gnome51_mutter_caps_classification(self):
        """GNOME 51 / Mutter capability set must satisfy GTK color management and HDR tagging."""
        ok, reason = gtk_color_managed(self.gnome51_caps, env={"GDK_DEBUG": "color-mgmt"})
        self.assertTrue(ok, f"Expected gtk_color_managed to be True, got {reason}")
        self.assertEqual(reason, "ok")

        can_hdr_pq = gtk_can_tag_hdr(self.gnome51_caps, TF_PQ)
        self.assertTrue(can_hdr_pq, "GNOME 51 caps must allow Rec.2100 PQ tagging")

    def test_kde_plasma_missing_tf_srgb_still_blocks_cleanly(self):
        """KWin Plasma missing TF_SRGB (Decision D8) must report clear missing TF srgb fallback."""
        kwin_caps = CmCaps(
            intents=frozenset({INTENT_PERCEPTUAL}),
            features=frozenset({FEAT_PARAMETRIC, FEAT_SET_PRIMARIES}),
            tfs=frozenset({TF_PQ, TF_GAMMA22, TF_COMPOUND_POWER_2_4}),  # Omits TF_SRGB
            primaries=frozenset({PRIM_SRGB, PRIM_BT2020}),
        )
        ok, reason = gtk_color_managed(kwin_caps, env={"GDK_DEBUG": "color-mgmt"})
        self.assertFalse(ok)
        self.assertIn("missing TF srgb", reason)

    def test_synthetic_gnome51_wayland_trace_passes_composite_oracle(self):
        """Synthetic Mutter v2 trace matching GNOME 51 event ordering passes composite window oracle."""
        trace = """
[conn#1 fd#9] [ 1042.001] -> wl_display#1.get_registry(new id wl_registry#2)
[conn#1 fd#9] [ 1042.002] wl_registry#2.global(1, "wl_compositor", 6)
[conn#1 fd#9] [ 1042.003] -> wl_registry#2.bind(1, "wl_compositor", 6, new id [unknown]#4)
[conn#1 fd#9] [ 1042.004] wl_registry#2.global(9, "xdg_wm_base", 7)
[conn#1 fd#9] [ 1042.005] -> wl_registry#2.bind(9, "xdg_wm_base", 6, new id [unknown]#5)
[conn#1 fd#9] [ 1042.006] wl_registry#2.global(32, "wp_color_manager_v1", 2)
[conn#1 fd#9] [ 1042.007] -> wl_registry#2.bind(32, "wp_color_manager_v1", 1, new id [unknown]#6)
[conn#1 fd#9] [ 1042.008] wp_color_manager_v1#6.supported_intent(0)
[conn#1 fd#9] [ 1042.009] wp_color_manager_v1#6.done()
[conn#1 fd#9] [ 1042.010] -> wl_compositor#4.create_surface(new id wl_surface#49)
[conn#1 fd#9] [ 1042.011] -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#61, wl_surface#49)
[conn#1 fd#9] [ 1042.012] -> xdg_surface#61.get_toplevel(new id xdg_toplevel#62)
[conn#1 fd#9] [ 1042.013] -> xdg_toplevel#62.set_app_id("io.github.rusmikev.CineHDR")
[conn#1 fd#9] [ 1042.014] -> wp_color_manager_v1#6.get_surface(new id wp_color_management_surface_v1#63, wl_surface#49)
[conn#1 fd#9] [ 1042.015] -> wp_color_manager_v1#6.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[conn#1 fd#9] [ 1042.016] -> wp_image_description_creator_params_v1#50.set_primaries_named(6)
[conn#1 fd#9] [ 1042.017] -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[conn#1 fd#9] [ 1042.018] -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#54)
[conn#1 fd#9] [ 1042.019] wp_image_description_v1#54.ready(15)
[conn#1 fd#9] [ 1042.020] -> wp_color_management_surface_v1#63.set_image_description(wp_image_description_v1#54, 0)
[conn#1 fd#9] [ 1042.021] -> wl_surface#49.attach(wl_buffer#79, 0, 0)
[conn#1 fd#9] [ 1042.022] -> wl_surface#49.commit()
""".strip()
        ev = validate_composite_window_trace(trace, expected_app_id="io.github.rusmikev.CineHDR")
        self.assertEqual(ev["status"], "PASS")
        self.assertEqual(ev["oracle"], "primary_composite_window")
        matched = ev["matched"]
        self.assertEqual(matched["surface"]["wl_surface_id"], 49)
        self.assertEqual(matched["surface"]["color_management_surface_id"], 63)
        self.assertEqual(matched["image_description"]["id"], 54)
        self.assertEqual(matched["image_description"]["color_type"], "rec2100_pq")
        self.assertEqual(matched["surface"]["effective_buffer_id"], 79)

    def test_live_gnome_wayland_trace_validation(self):
        """Execute live bounded trace on actual running GNOME session if available."""
        if not os.environ.get("WAYLAND_DISPLAY"):
            self.skipTest("No active WAYLAND_DISPLAY in test environment")
        if "GNOME" not in os.environ.get("XDG_CURRENT_DESKTOP", ""):
            self.skipTest(f"Not a GNOME desktop (XDG_CURRENT_DESKTOP={os.environ.get('XDG_CURRENT_DESKTOP')})")
        if not HDR_FIXTURE_DEFAULT.exists():
            self.skipTest(f"HDR fixture not found: {HDR_FIXTURE_DEFAULT}")

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            tagger_so = tmp / "libwayland_conn_tagger.so"
            compile_tagger(tagger_so)

            session_dir = tmp / "live_hdr"
            session_dir.mkdir()
            res = run_bounded_session(
                video_path=HDR_FIXTURE_DEFAULT,
                output_dir=session_dir,
                tagger_so=tagger_so,
                duration_seconds=3.5,
            )

            ev = res["evidence"]
            self.assertEqual(ev.get("status"), "PASS", f"Trace validation failed: {ev.get('reason')}")
            self.assertEqual(ev.get("oracle"), "primary_composite_window")
            matched = ev.get("matched", {})
            self.assertIn("surface", matched)
            self.assertEqual(matched["surface"].get("app_id"), "io.github.rusmikev.CineHDR")
            self.assertEqual(matched["image_description"].get("color_type"), "rec2100_pq")


if __name__ == "__main__":
    unittest.main()
