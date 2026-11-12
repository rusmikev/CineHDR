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


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
