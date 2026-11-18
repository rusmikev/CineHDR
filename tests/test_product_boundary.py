"""Check the installed product and import boundary without a display server."""

import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio


class ProductBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build_dir = Path(sys.argv[1])
        cls.source_root = Path(__file__).resolve().parent.parent
        cls.schema_dir = tempfile.TemporaryDirectory(prefix="cinehdr-c22-schema-")
        schemas = list((cls.source_root / "data").glob("*.gschema.xml"))
        if not schemas:
            raise RuntimeError(f"No .gschema.xml files found in {cls.source_root / 'data'}")
        for schema in schemas:
            shutil.copy2(schema, cls.schema_dir.name)
        subprocess.run(["glib-compile-schemas", cls.schema_dir.name], check=True)
        os.environ["GSETTINGS_SCHEMA_DIR"] = cls.schema_dir.name
        os.environ["GSETTINGS_BACKEND"] = "memory"
        resource = Gio.Resource.load(sys.argv[2])
        Gio.resources_register(resource)
        cls.resource = resource
        cls.gresource_path = sys.argv[2]

    @classmethod
    def tearDownClass(cls):
        cls.schema_dir.cleanup()

    def test_product_imports_do_not_load_validation(self):
        importlib.import_module("src.window")
        importlib.import_module("src.video_widget")
        self.assertNotIn("src.gpu_validation", sys.modules)
        self.assertNotIn("src.validation.gpu_validation", sys.modules)
        self.assertNotIn("src.validation.diagnostics_export", sys.modules)

    def test_default_install_excludes_validation_module(self):
        result = subprocess.run(
            ["meson", "introspect", "--installed", str(self.build_dir)],
            check=True,
            capture_output=True,
            text=True,
        )
        installed = json.loads(result.stdout)
        self.assertFalse(
            any("validation" in Path(destination).parts for destination in installed.values()),
            "validation modules must not be installed with -Dvalidation=false",
        )

    def _make_product_package_tree(self, root_dir: Path) -> Path:
        """Create a displayless product package copy without validation/ on disk."""
        pkg_dir = root_dir / "cinehdr"
        pkg_dir.mkdir()
        for py_file in (self.source_root / "src").glob("*.py"):
            shutil.copy2(py_file, pkg_dir / py_file.name)
        return root_dir

    def _run_product_subprocess(
        self, pkg_root: Path, xdg_dir: Path, snippet: str, extra_env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        """Run a displayless probe against a staged product package without validation/."""
        probe_code = (
            "import gi, sys; "
            "gi.require_version('Gio', '2.0'); "
            "from gi.repository import Gio; "
            f"Gio.resources_register(Gio.Resource.load({self.gresource_path!r})); "
            f"{snippet}"
        )
        proc_env = os.environ.copy()
        for drop_key in (
            "DISPLAY",
            "WAYLAND_DISPLAY",
            "CINEHDR_GPU_VALIDATION",
            "CINEHDR_DIAGNOSTICS_EXPORT_PATH",
            "CINEHDR_DIAGNOSTICS_EXPORT_DIR",
        ):
            proc_env.pop(drop_key, None)
        proc_env.update(
            {
                "PYTHONPATH": str(pkg_root),
                "GSETTINGS_SCHEMA_DIR": self.schema_dir.name,
                "GSETTINGS_BACKEND": "memory",
                "XDG_RUNTIME_DIR": str(xdg_dir),
                **extra_env,
            }
        )
        return subprocess.run(
            [sys.executable, "-c", probe_code],
            env=proc_env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_diagnostics_export_requests_fail_closed_before_gtk_callbacks(self):
        """Export requests must exit non-zero before app.run()/GTK callbacks without validation."""
        main_mod = importlib.import_module("src.main")
        app_cls = main_mod.CineApplication
        env_cases = (
            ("CINEHDR_DIAGNOSTICS_EXPORT_PATH", "/tmp/cinehdr-missing-export.txt"),
            ("CINEHDR_DIAGNOSTICS_EXPORT_DIR", "/tmp/cinehdr-missing-export-dir"),
        )
        cleared_env = {
            "CINEHDR_GPU_VALIDATION": "",
            "CINEHDR_DIAGNOSTICS_EXPORT_PATH": "",
            "CINEHDR_DIAGNOSTICS_EXPORT_DIR": "",
        }
        blocked_modules = {
            "src.validation": None,
            "src.validation.diagnostics_export": None,
            "src.validation.gpu_validation": None,
        }

        for env_key, env_value in env_cases:
            with self.subTest(mode="in_process", env_key=env_key):
                env_override = {**cleared_env, env_key: env_value}
                with (
                    patch.dict(os.environ, env_override, clear=False),
                    patch.dict(sys.modules, blocked_modules, clear=False),
                    patch.object(app_cls, "run", side_effect=AssertionError("run reached")) as run_mock,
                    patch.object(
                        app_cls, "do_startup", side_effect=AssertionError("do_startup reached")
                    ) as startup_mock,
                    patch.object(
                        app_cls, "do_activate", side_effect=AssertionError("do_activate reached")
                    ) as activate_mock,
                    patch.object(app_cls, "do_open", side_effect=AssertionError("do_open reached")) as open_mock,
                    self.assertLogs(main_mod.logger, level="ERROR") as logs,
                ):
                    with self.assertRaises(SystemExit) as exit_ctx:
                        main_mod.main("test")
                    self.assertEqual(exit_ctx.exception.code, 2)
                    run_mock.assert_not_called()
                    startup_mock.assert_not_called()
                    activate_mock.assert_not_called()
                    open_mock.assert_not_called()
                    self.assertTrue(
                        any("Diagnostics export is unavailable; install with -Dvalidation=true" in line for line in logs.output)
                    )

        with tempfile.TemporaryDirectory(prefix="cinehdr-c22-pkg-") as tmp_dir:
            tmp_path = Path(tmp_dir)
            pkg_root = self._make_product_package_tree(tmp_path)
            xdg_dir = tmp_path / "xdg-empty"
            xdg_dir.mkdir()
            snippet = (
                "import cinehdr.main as m; "
                "m.CineApplication.run = lambda *a, **k: print('GTK_CALLBACK_ENTERED:run'); "
                "m.CineApplication.do_startup = lambda *a, **k: print('GTK_CALLBACK_ENTERED:do_startup'); "
                "m.CineApplication.do_activate = lambda *a, **k: print('GTK_CALLBACK_ENTERED:do_activate'); "
                "m.CineApplication.do_open = lambda *a, **k: print('GTK_CALLBACK_ENTERED:do_open'); "
                "sys.exit(m.main('test'))"
            )
            for env_key, env_value in env_cases:
                with self.subTest(mode="subprocess", env_key=env_key):
                    proc = self._run_product_subprocess(pkg_root, xdg_dir, snippet, {env_key: env_value})
                    self.assertEqual(proc.returncode, 2, msg=proc.stderr)
                    self.assertIn("Diagnostics export is unavailable; install with -Dvalidation=true", proc.stderr)
                    self.assertNotIn("GTK_CALLBACK_ENTERED", proc.stdout + proc.stderr)

    def test_normal_startup_does_not_import_validation(self):
        """Normal startup without validation/export env must not import validation modules."""
        with tempfile.TemporaryDirectory(prefix="cinehdr-c22-normal-") as tmp_dir:
            tmp_path = Path(tmp_dir)
            pkg_root = self._make_product_package_tree(tmp_path)
            xdg_dir = tmp_path / "xdg-empty"
            xdg_dir.mkdir()
            snippet = (
                "import cinehdr.main as m; "
                "app = m.CineApplication(); "
                "assert app.gpu_validation_config is None; "
                "loaded = [name for name in sys.modules if 'validation' in name]; "
                "assert not loaded, loaded; "
                "print('NORMAL_STARTUP_OK')"
            )
            for label, extra_env in (
                ("unset", {}),
                ("whitespace", {"CINEHDR_DIAGNOSTICS_EXPORT_PATH": "   ", "CINEHDR_DIAGNOSTICS_EXPORT_DIR": " \t "}),
            ):
                with self.subTest(case=label):
                    proc = self._run_product_subprocess(pkg_root, xdg_dir, snippet, extra_env)
                    self.assertEqual(proc.returncode, 0, msg=proc.stderr)
                    self.assertIn("NORMAL_STARTUP_OK", proc.stdout)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
