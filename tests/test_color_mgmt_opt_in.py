# Copyright 2026 rusmikev
# SPDX-License-Identifier: GPL-3.0-or-later
"""The GDK_DEBUG=color-mgmt opt-in must reach GDK, and the HDR gate must agree
with what GDK actually parsed.

GDK reads GDK_DEBUG exactly once, in gdk_pre_parse() during Gtk.init(), and
PyGObject calls Gtk.init_check() as soon as gi.repository.Gtk is imported.
Setting GDK_DEBUG after that is ignored by GTK but still seen by
gtk_cm_opted_in(), which made the gate allow PQ while GTK had no color
management (d6f8a2b..ee2d333).
"""

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.gtk_cm_policy import (  # noqa: E402
    COLOR_MGMT_OPT_OUT_ENV,
    FEAT_PARAMETRIC,
    FEAT_SET_PRIMARIES,
    INTENT_PERCEPTUAL,
    OPT_IN_ALREADY_ENABLED,
    OPT_IN_APPLIED,
    OPT_IN_NOT_REQUESTED,
    OPT_IN_TOO_LATE,
    PRIM_BT2020,
    PRIM_SRGB,
    TF_COMPOUND_POWER_2_4,
    TF_EXT_LINEAR,
    TF_GAMMA22,
    TF_PQ,
    TF_SRGB,
    CmCaps,
    apply_color_mgmt_opt_in,
    color_mgmt_opt_out_requested,
    gdk_debug_excludes_color_mgmt,
    gdk_debug_enables_color_mgmt,
    gtk_can_tag_hdr,
    gtk_cm_opted_in,
    gtk_color_managed,
    with_color_mgmt,
)


class TestGdkDebugParity(unittest.TestCase):
    """Semantics of gdk_parse_debug_var() in gdk/gdk.c."""

    def test_values(self):
        cases = {
            "": False,
            "misc": False,
            "color-mgmtx": False,
            "color-mgmt": True,
            "COLOR-MGMT": True,
            "misc:color-mgmt": True,
            "misc,color-mgmt": True,
            "misc;color-mgmt": True,
            "misc color-mgmt": True,
            "misc\tcolor-mgmt": True,
            "::color-mgmt::": True,
            "all": True,
            "ALL": True,
            "all:color-mgmt": False,  # "all" subtracts listed flags
            "all:misc": True,
        }
        for value, expected in cases.items():
            with self.subTest(GDK_DEBUG=value):
                self.assertIs(gdk_debug_enables_color_mgmt(value), expected)
                self.assertIs(gtk_cm_opted_in({"GDK_DEBUG": value}), expected)

    def test_unset(self):
        self.assertFalse(gtk_cm_opted_in({}))

    def test_with_color_mgmt_always_enables_and_keeps_other_flags(self):
        cases = {
            "": "color-mgmt",
            "misc": "misc:color-mgmt",
            "misc,opengl": "misc:opengl:color-mgmt",
            "color-mgmt": "color-mgmt",
            "all": "all",
            "all:color-mgmt": "all",
            "all:misc:COLOR-MGMT": "all:misc",
        }
        for value, expected in cases.items():
            with self.subTest(GDK_DEBUG=value):
                result = with_color_mgmt(value)
                self.assertEqual(result, expected)
                self.assertTrue(gdk_debug_enables_color_mgmt(result))


class TestApplyColorMgmtOptIn(unittest.TestCase):
    def test_not_requested_leaves_env_alone(self):
        env = {"GDK_DEBUG": "misc"}
        self.assertEqual(apply_color_mgmt_opt_in(env, ["cinehdr"], False), OPT_IN_NOT_REQUESTED)
        self.assertEqual(env, {"GDK_DEBUG": "misc"})

    def test_env_var_before_gtk_init(self):
        env = {"CINEHDR_EXPERIMENTAL_COLOR_MGMT": "1", "GDK_DEBUG": "misc"}
        self.assertEqual(apply_color_mgmt_opt_in(env, ["cinehdr"], False), OPT_IN_APPLIED)
        self.assertEqual(env["GDK_DEBUG"], "misc:color-mgmt")
        self.assertTrue(gtk_cm_opted_in(env))

    def test_cli_flag_before_gtk_init(self):
        env = {}
        argv = ["cinehdr", "--experimental-color-mgmt", "video.mkv"]
        self.assertEqual(apply_color_mgmt_opt_in(env, argv, False), OPT_IN_APPLIED)
        self.assertEqual(env["GDK_DEBUG"], "color-mgmt")

    def test_env_var_other_than_1_is_not_an_opt_in(self):
        env = {"CINEHDR_EXPERIMENTAL_COLOR_MGMT": "0"}
        self.assertEqual(apply_color_mgmt_opt_in(env, ["cinehdr"], False), OPT_IN_NOT_REQUESTED)
        self.assertNotIn("GDK_DEBUG", env)

    def test_already_enabled_is_reported_even_after_gtk_init(self):
        env = {"CINEHDR_EXPERIMENTAL_COLOR_MGMT": "1", "GDK_DEBUG": "color-mgmt"}
        self.assertEqual(apply_color_mgmt_opt_in(env, ["cinehdr"], True), OPT_IN_ALREADY_ENABLED)
        self.assertEqual(env["GDK_DEBUG"], "color-mgmt")

    def test_all_minus_color_mgmt_is_repaired(self):
        env = {"CINEHDR_EXPERIMENTAL_COLOR_MGMT": "1", "GDK_DEBUG": "all:color-mgmt"}
        self.assertEqual(apply_color_mgmt_opt_in(env, ["cinehdr"], False), OPT_IN_APPLIED)
        self.assertTrue(gtk_cm_opted_in(env))

    def test_too_late_keeps_env_and_gate_honest(self):
        """The d6f8a2b bug: GDK_DEBUG written after Gtk.init() fooled the gate."""
        env = {"CINEHDR_EXPERIMENTAL_COLOR_MGMT": "1", "GDK_DEBUG": "misc"}
        self.assertEqual(apply_color_mgmt_opt_in(env, ["cinehdr"], True), OPT_IN_TOO_LATE)
        self.assertEqual(env["GDK_DEBUG"], "misc")
        self.assertFalse(gtk_cm_opted_in(env))


def _parse(path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _first_opt_in_call_line(tree):
    lines = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", getattr(node.func, "attr", None)) == "apply_color_mgmt_opt_in"
    ]
    return min(lines) if lines else None


def _first_gtk_or_app_import_line(tree):
    gtk_names = {"Gtk", "Gdk", "Adw"}
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names = {alias.name for alias in node.names}
            if node.module == "gi.repository" and names & gtk_names:
                lines.append(node.lineno)
            elif node.module in ("cinehdr", "src") and "main" in names:
                lines.append(node.lineno)
            elif node.module in ("cinehdr.main", "src.main"):
                lines.append(node.lineno)
        elif isinstance(node, ast.Import):
            if any(alias.name in ("cinehdr.main", "src.main") for alias in node.names):
                lines.append(node.lineno)
    return min(lines) if lines else None


class TestOptInOrdering(unittest.TestCase):
    def assert_opt_in_precedes_gtk(self, path):
        tree = _parse(path)
        call_line = _first_opt_in_call_line(tree)
        import_line = _first_gtk_or_app_import_line(tree)
        self.assertIsNotNone(call_line, f"{path.name}: opt-in is not applied")
        self.assertIsNotNone(import_line, f"{path.name}: no Gtk/app import found")
        self.assertLess(
            call_line, import_line,
            f"{path.name}: apply_color_mgmt_opt_in (line {call_line}) must run "
            f"before the first Gtk/Adw/Gdk or app import (line {import_line})",
        )

    def test_installed_launcher(self):
        self.assert_opt_in_precedes_gtk(ROOT / "src" / "cinehdr.in")

    def test_dev_launcher(self):
        self.assert_opt_in_precedes_gtk(ROOT / "run_dev.py")

    def test_main_module_never_writes_gdk_debug(self):
        tree = _parse(ROOT / "src" / "main.py")
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                targets = [node.target]
            for target in targets:
                if (isinstance(target, ast.Subscript)
                        and isinstance(target.slice, ast.Constant)
                        and target.slice.value == "GDK_DEBUG"):
                    self.fail(f"main.py:{node.lineno} writes GDK_DEBUG after Gtk import")

    def test_main_module_passes_real_gtk_state(self):
        tree = _parse(ROOT / "src" / "main.py")
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "apply_color_mgmt_opt_in"
        ]
        self.assertEqual(len(calls), 1)
        keyword = {kw.arg: kw.value for kw in calls[0].keywords}.get("gtk_initialized")
        self.assertIsNotNone(keyword)
        self.assertEqual(ast.unparse(keyword), "Gtk.is_initialized()")


class TestFlatpakDefaultBehavior(unittest.TestCase):
    """Behavioral tests for Flatpak default color management, opt-out, and flag preservation."""

    def test_opt_out_helper(self):
        self.assertEqual(COLOR_MGMT_OPT_OUT_ENV, "CINEHDR_NO_COLOR_MGMT")
        self.assertTrue(color_mgmt_opt_out_requested({"CINEHDR_NO_COLOR_MGMT": "1"}))
        self.assertFalse(color_mgmt_opt_out_requested({"CINEHDR_NO_COLOR_MGMT": "0"}))
        self.assertFalse(color_mgmt_opt_out_requested({}))

    def test_gdk_debug_excludes_color_mgmt(self):
        self.assertTrue(gdk_debug_excludes_color_mgmt("all:color-mgmt"))
        self.assertTrue(gdk_debug_excludes_color_mgmt("ALL:COLOR-MGMT"))
        self.assertTrue(gdk_debug_excludes_color_mgmt("all:misc:color-mgmt"))
        self.assertFalse(gdk_debug_excludes_color_mgmt("all"))
        self.assertFalse(gdk_debug_excludes_color_mgmt("color-mgmt"))
        self.assertFalse(gdk_debug_excludes_color_mgmt("misc"))
        self.assertFalse(gdk_debug_excludes_color_mgmt(""))

    def test_default_launch_enables_color_mgmt(self):
        env = {}
        res = apply_color_mgmt_opt_in(env, ["cinehdr"], gtk_initialized=False, default_enabled=True)
        self.assertEqual(res, OPT_IN_APPLIED)
        self.assertEqual(env.get("GDK_DEBUG"), "color-mgmt")
        self.assertTrue(gtk_cm_opted_in(env))

    def test_default_launch_preserves_existing_flags(self):
        cases = [
            ("misc", "misc:color-mgmt"),
            ("misc,opengl", "misc:opengl:color-mgmt"),
            ("misc;opengl", "misc:opengl:color-mgmt"),
        ]
        for initial, expected in cases:
            with self.subTest(initial=initial):
                env = {"GDK_DEBUG": initial}
                res = apply_color_mgmt_opt_in(env, ["cinehdr"], gtk_initialized=False, default_enabled=True)
                self.assertEqual(res, OPT_IN_APPLIED)
                self.assertEqual(env.get("GDK_DEBUG"), expected)
                self.assertTrue(gtk_cm_opted_in(env))

    def test_default_launch_already_enabled_leaves_env_alone(self):
        for val in ("color-mgmt", "all", "misc:color-mgmt", "ALL"):
            with self.subTest(gdk_debug=val):
                env = {"GDK_DEBUG": val}
                res = apply_color_mgmt_opt_in(env, ["cinehdr"], gtk_initialized=False, default_enabled=True)
                self.assertEqual(res, OPT_IN_ALREADY_ENABLED)
                self.assertEqual(env.get("GDK_DEBUG"), val)
                self.assertTrue(gtk_cm_opted_in(env))

    def test_default_launch_preserves_all_minus_color_mgmt_semantics(self):
        for val in ("all:color-mgmt", "ALL:COLOR-MGMT", "all:misc:color-mgmt"):
            with self.subTest(gdk_debug=val):
                env = {"GDK_DEBUG": val}
                res = apply_color_mgmt_opt_in(env, ["cinehdr"], gtk_initialized=False, default_enabled=True)
                self.assertEqual(res, OPT_IN_NOT_REQUESTED)
                self.assertEqual(env.get("GDK_DEBUG"), val)
                self.assertFalse(gtk_cm_opted_in(env))

    def test_opt_out_disables_automatic_color_mgmt(self):
        env = {"CINEHDR_NO_COLOR_MGMT": "1"}
        res = apply_color_mgmt_opt_in(env, ["cinehdr"], gtk_initialized=False, default_enabled=True)
        self.assertEqual(res, OPT_IN_NOT_REQUESTED)
        self.assertNotIn("GDK_DEBUG", env)
        self.assertFalse(gtk_cm_opted_in(env))

    def test_opt_out_preserves_existing_user_gdk_debug(self):
        env = {"CINEHDR_NO_COLOR_MGMT": "1", "GDK_DEBUG": "misc"}
        res = apply_color_mgmt_opt_in(env, ["cinehdr"], gtk_initialized=False, default_enabled=True)
        self.assertEqual(res, OPT_IN_NOT_REQUESTED)
        self.assertEqual(env.get("GDK_DEBUG"), "misc")
        self.assertFalse(gtk_cm_opted_in(env))

    def test_opt_out_does_not_override_explicit_color_mgmt_in_gdk_debug(self):
        env = {"CINEHDR_NO_COLOR_MGMT": "1", "GDK_DEBUG": "color-mgmt"}
        res = apply_color_mgmt_opt_in(env, ["cinehdr"], gtk_initialized=False, default_enabled=True)
        self.assertEqual(res, OPT_IN_ALREADY_ENABLED)
        self.assertEqual(env.get("GDK_DEBUG"), "color-mgmt")
        self.assertTrue(gtk_cm_opted_in(env))

    def test_opt_out_env_other_than_1_is_ignored(self):
        env = {"CINEHDR_NO_COLOR_MGMT": "0"}
        res = apply_color_mgmt_opt_in(env, ["cinehdr"], gtk_initialized=False, default_enabled=True)
        self.assertEqual(res, OPT_IN_APPLIED)
        self.assertEqual(env.get("GDK_DEBUG"), "color-mgmt")
        self.assertTrue(gtk_cm_opted_in(env))

    def test_opt_out_overrides_stale_experimental_env(self):
        env = {
            "CINEHDR_NO_COLOR_MGMT": "1",
            "CINEHDR_EXPERIMENTAL_COLOR_MGMT": "1",
        }
        res = apply_color_mgmt_opt_in(env, ["cinehdr"], gtk_initialized=False, default_enabled=True)
        self.assertEqual(res, OPT_IN_NOT_REQUESTED)
        self.assertNotIn("GDK_DEBUG", env)
        self.assertFalse(gtk_cm_opted_in(env))

    def test_dev_launcher_defaults_to_opt_in_only(self):
        env = {}
        res = apply_color_mgmt_opt_in(env, ["cinehdr"], gtk_initialized=False, default_enabled=False)
        self.assertEqual(res, OPT_IN_NOT_REQUESTED)
        self.assertNotIn("GDK_DEBUG", env)
        self.assertFalse(gtk_cm_opted_in(env))

    def test_installed_launcher_passes_default_enabled(self):
        tree = _parse(ROOT / "src" / "cinehdr.in")
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", getattr(node.func, "attr", None)) == "apply_color_mgmt_opt_in"
        ]
        self.assertEqual(len(calls), 1)
        keyword = {kw.arg: kw.value for kw in calls[0].keywords}.get("default_enabled")
        self.assertIsNotNone(keyword, "cinehdr.in must pass default_enabled=True")
        self.assertIsInstance(keyword, ast.Constant)
        self.assertIs(keyword.value, True)

    def test_dev_launcher_does_not_pass_default_enabled(self):
        tree = _parse(ROOT / "run_dev.py")
        calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", getattr(node.func, "attr", None)) == "apply_color_mgmt_opt_in"
        ]
        self.assertEqual(len(calls), 1)
        keyword = {kw.arg: kw.value for kw in calls[0].keywords}.get("default_enabled")
        if keyword is not None:
            self.assertIsInstance(keyword, ast.Constant)
            self.assertFalse(keyword.value)

    def test_subprocess_launcher_behavior(self):
        import os
        import subprocess
        cases = [
            ({}, "color-mgmt", "True"),
            ({"GDK_DEBUG": "misc"}, "misc:color-mgmt", "True"),
            ({"GDK_DEBUG": "all:color-mgmt"}, "all:color-mgmt", "False"),
            ({"CINEHDR_NO_COLOR_MGMT": "1"}, "", "False"),
            ({"CINEHDR_NO_COLOR_MGMT": "1", "GDK_DEBUG": "misc"}, "misc", "False"),
            ({"CINEHDR_NO_COLOR_MGMT": "1", "GDK_DEBUG": "color-mgmt"}, "color-mgmt", "True"),
        ]
        cmd = [
            sys.executable,
            "-c",
            "import os, sys; from src.gtk_cm_policy import apply_color_mgmt_opt_in, gtk_cm_opted_in; "
            "apply_color_mgmt_opt_in(os.environ, sys.argv, False, default_enabled=True); "
            "print(os.environ.get('GDK_DEBUG', '') + '|' + str(gtk_cm_opted_in()))",
        ]
        for extra_env, expected_debug, expected_opted in cases:
            with self.subTest(env=extra_env):
                test_env = os.environ.copy()
                test_env.pop("GDK_DEBUG", None)
                test_env.pop("CINEHDR_NO_COLOR_MGMT", None)
                test_env.pop("CINEHDR_EXPERIMENTAL_COLOR_MGMT", None)
                test_env.update(extra_env)
                res = subprocess.run(
                    cmd,
                    cwd=str(ROOT),
                    env=test_env,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                output = res.stdout.strip()
                debug_val, opted_val = output.split("|", 1)
                self.assertEqual(debug_val, expected_debug)
                self.assertEqual(opted_val, expected_opted)


class TestGtkColorManagedCaps(unittest.TestCase):
    """Hermetic tests for gtk_color_managed() and gtk_can_tag_hdr()."""

    def setUp(self):
        self.mutter_caps = CmCaps(
            intents=frozenset([INTENT_PERCEPTUAL]),
            features=frozenset([FEAT_PARAMETRIC, FEAT_SET_PRIMARIES]),
            tfs=frozenset([TF_SRGB, TF_PQ, TF_EXT_LINEAR]),
            primaries=frozenset([PRIM_SRGB, PRIM_BT2020]),
        )
        self.kwin_caps = CmCaps(
            intents=frozenset([INTENT_PERCEPTUAL]),
            features=frozenset([FEAT_PARAMETRIC, FEAT_SET_PRIMARIES]),
            tfs=frozenset([TF_GAMMA22, TF_PQ, TF_COMPOUND_POWER_2_4]),
            primaries=frozenset([PRIM_SRGB, PRIM_BT2020]),
        )

    def test_mutter_gnome_caps_success(self):
        ok, reason = gtk_color_managed(self.mutter_caps, env={"GDK_DEBUG": "color-mgmt"})
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")
        self.assertTrue(gtk_can_tag_hdr(self.mutter_caps, TF_PQ))
        self.assertTrue(gtk_can_tag_hdr(self.mutter_caps, TF_EXT_LINEAR))

    def test_kwin_caps_missing_srgb_fails_closed(self):
        ok, reason = gtk_color_managed(self.kwin_caps, env={"GDK_DEBUG": "color-mgmt"})
        self.assertFalse(ok)
        self.assertIn("missing TF srgb", reason)

    def test_missing_caps_fails_closed(self):
        ok, reason = gtk_color_managed(None, env={"GDK_DEBUG": "color-mgmt"})
        self.assertFalse(ok)
        self.assertIn("wp_color_manager_v1", reason)
        self.assertFalse(gtk_can_tag_hdr(None))

    def test_opt_in_missing_or_opt_out_rejected(self):
        ok, reason = gtk_color_managed(self.mutter_caps, env={})
        self.assertFalse(ok)
        self.assertIn("GDK_DEBUG=color-mgmt", reason)

        ok, reason = gtk_color_managed(self.mutter_caps, env={"CINEHDR_NO_COLOR_MGMT": "1"})
        self.assertFalse(ok)
        self.assertIn("GDK_DEBUG=color-mgmt", reason)

    def test_missing_perceptual_intent_fails_closed(self):
        caps = CmCaps(
            intents=frozenset([1]),  # relative only, missing perceptual
            features=frozenset([FEAT_PARAMETRIC, FEAT_SET_PRIMARIES]),
            tfs=frozenset([TF_SRGB, TF_PQ]),
            primaries=frozenset([PRIM_SRGB, PRIM_BT2020]),
        )
        ok, reason = gtk_color_managed(caps, env={"GDK_DEBUG": "color-mgmt"})
        self.assertFalse(ok)
        self.assertIn("perceptual", reason)

    def test_missing_parametric_feature_fails_closed(self):
        caps = CmCaps(
            intents=frozenset([INTENT_PERCEPTUAL]),
            features=frozenset([FEAT_SET_PRIMARIES]),  # missing FEAT_PARAMETRIC
            tfs=frozenset([TF_SRGB, TF_PQ]),
            primaries=frozenset([PRIM_SRGB, PRIM_BT2020]),
        )
        ok, reason = gtk_color_managed(caps, env={"GDK_DEBUG": "color-mgmt"})
        self.assertFalse(ok)
        self.assertIn("missing TF srgb", reason)

    def test_missing_srgb_primaries_and_set_primaries_fails_closed(self):
        caps = CmCaps(
            intents=frozenset([INTENT_PERCEPTUAL]),
            features=frozenset([FEAT_PARAMETRIC]),  # missing FEAT_SET_PRIMARIES
            tfs=frozenset([TF_SRGB, TF_PQ]),
            primaries=frozenset([PRIM_BT2020]),  # missing PRIM_SRGB
        )
        ok, reason = gtk_color_managed(caps, env={"GDK_DEBUG": "color-mgmt"})
        self.assertFalse(ok)
        self.assertIn("missing TF srgb", reason)

    def test_set_primaries_allows_custom_primaries_for_srgb_and_hdr(self):
        caps = CmCaps(
            intents=frozenset([INTENT_PERCEPTUAL]),
            features=frozenset([FEAT_PARAMETRIC, FEAT_SET_PRIMARIES]),
            tfs=frozenset([TF_SRGB, TF_PQ]),
            primaries=frozenset(),  # no named primaries, but FEAT_SET_PRIMARIES present
        )
        ok, reason = gtk_color_managed(caps, env={"GDK_DEBUG": "color-mgmt"})
        self.assertTrue(ok)
        self.assertTrue(gtk_can_tag_hdr(caps, TF_PQ))

    def test_hdr_tf_variations(self):
        # Only PQ
        pq_only = CmCaps(
            intents=frozenset([INTENT_PERCEPTUAL]),
            features=frozenset([FEAT_PARAMETRIC, FEAT_SET_PRIMARIES]),
            tfs=frozenset([TF_SRGB, TF_PQ]),
            primaries=frozenset([PRIM_SRGB, PRIM_BT2020]),
        )
        self.assertTrue(gtk_can_tag_hdr(pq_only, TF_PQ))
        self.assertFalse(gtk_can_tag_hdr(pq_only, TF_EXT_LINEAR))

        # Only ext_linear
        linear_only = CmCaps(
            intents=frozenset([INTENT_PERCEPTUAL]),
            features=frozenset([FEAT_PARAMETRIC, FEAT_SET_PRIMARIES]),
            tfs=frozenset([TF_SRGB, TF_EXT_LINEAR]),
            primaries=frozenset([PRIM_SRGB, PRIM_BT2020]),
        )
        self.assertFalse(gtk_can_tag_hdr(linear_only, TF_PQ))
        self.assertTrue(gtk_can_tag_hdr(linear_only, TF_EXT_LINEAR))

    def test_empty_or_unknown_caps_fails_closed(self):
        empty_caps = CmCaps(
            intents=frozenset(),
            features=frozenset(),
            tfs=frozenset(),
            primaries=frozenset(),
        )
        ok, reason = gtk_color_managed(empty_caps, env={"GDK_DEBUG": "color-mgmt"})
        self.assertFalse(ok)
        self.assertFalse(gtk_can_tag_hdr(empty_caps))


if __name__ == "__main__":
    unittest.main()
