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
    OPT_IN_ALREADY_ENABLED,
    OPT_IN_APPLIED,
    OPT_IN_NOT_REQUESTED,
    OPT_IN_TOO_LATE,
    apply_color_mgmt_opt_in,
    gdk_debug_enables_color_mgmt,
    gtk_cm_opted_in,
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


if __name__ == "__main__":
    unittest.main()
