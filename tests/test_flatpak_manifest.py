"""Static checks for the local GPU Next Flatpak candidate manifest."""

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "build-aux/flatpak/io.github.rusmikev.CineHDR.json"
CARGO_SOURCES_PATH = ROOT / "build-aux/flatpak/cargo-sources-dolby_vision-3.3.2.json"
SCHEMA_PATH = ROOT / "data/io.github.rusmikev.CineHDR.gschema.xml"


class FlatpakManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with MANIFEST_PATH.open(encoding="utf-8") as manifest_file:
            cls.manifest = json.load(manifest_file)
        with CARGO_SOURCES_PATH.open(encoding="utf-8") as sources_file:
            cls.cargo_sources = json.load(sources_file)
        cls.libmpv = next(module for module in cls.manifest["modules"] if module["name"] == "libmpv")
        cls.modules = cls.libmpv["modules"]
        cls.module_by_name = {
            module["name"]: module for module in cls.modules if isinstance(module, dict)
        }

    def test_manifest_is_json_and_uses_rust_sdk_extension(self):
        self.assertEqual(self.manifest["id"], "io.github.rusmikev.CineHDR")
        self.assertIn("org.freedesktop.Sdk.Extension.rust-stable", self.manifest["sdk-extensions"])

    def test_mpv_is_exact_gpu_next_commit_without_screenshot_patch(self):
        self.assertEqual(
            self.libmpv["sources"],
            [{
                "type": "git",
                "url": "https://github.com/lhc70000/mpv.git",
                "branch": "libmpv-gpu-next",
                "commit": "97179bce7ed980c53647d6344916f632fe689e9e",
            }],
        )
        self.assertNotIn("fix-screenshots.diff", json.dumps(self.libmpv["sources"]))
        self.assertTrue((ROOT / "build-aux/flatpak/fix-screenshots.diff").is_file())
        self.assertTrue(
            {
                "-Dcplayer=false",
                "-Dtests=false",
                "-Ddrm=enabled",
                "-Dvaapi=enabled",
                "-Dvaapi-drm=enabled",
            }.issubset(self.libmpv["config-opts"])
        )
        self.assertNotIn("-Dvaapi-wayland=disabled", self.libmpv["config-opts"])
        linkage_checks = " ".join(self.libmpv["post-install"])
        for dependency in (
            "libavcodec.so.62",
            "libavutil.so.60",
            "libplacebo.so.360",
            "libdrm.so.2",
            "libdisplay-info.so.3",
            "libva-drm.so.2",
            "libdovi.so.3",
        ):
            self.assertIn(dependency, linkage_checks)

    def test_pinned_dependency_order_and_features(self):
        names = [module["name"] for module in self.modules if isinstance(module, dict)]
        self.assertLess(names.index("hwdata"), names.index("libdisplay-info"))
        self.assertLess(names.index("libdisplay-info"), names.index("libplacebo"))
        self.assertLess(names.index("ffmpeg"), names.index("libdovi"))
        self.assertLess(names.index("libdovi"), names.index("libplacebo"))

        hwdata = self.module_by_name["hwdata"]
        self.assertEqual(
            hwdata["sources"],
            [{
                "type": "git",
                "url": "https://github.com/vcrhonek/hwdata.git",
                "commit": "8bb6c48dc2e683777e625d1c9ef02c1b25b741cd",
            }],
        )
        hwdata_commands = " ".join(hwdata["build-commands"])
        self.assertIn("--prefix=/app --disable-blacklist", hwdata_commands)
        self.assertIn("/app/share/hwdata/pnp.ids", hwdata_commands)
        self.assertIn("pkg-config --atleast-version=0.410 hwdata", hwdata_commands)
        self.assertIn("/share/hwdata", self.manifest["cleanup"])

        display_info = self.module_by_name["libdisplay-info"]
        self.assertEqual(
            display_info["sources"],
            [{
                "type": "git",
                "url": "https://gitlab.freedesktop.org/emersion/libdisplay-info.git",
                "commit": "47a5590e9c4eb35d67651b8c05a55f1a48259329",
            }],
        )
        display_info_checks = " ".join(display_info["post-install"])
        self.assertIn("libdisplay-info.so.3", display_info_checks)

        uchardet = self.module_by_name["uchardet"]
        uchardet_source = uchardet["sources"][0]
        self.assertEqual(
            uchardet_source["mirror-urls"],
            [
                "https://deb.debian.org/debian/pool/main/u/uchardet/"
                "uchardet_0.0.8.orig.tar.xz"
            ],
        )
        self.assertEqual(
            uchardet_source["sha256"],
            "e97a60cfc00a1c147a674b097bb1422abd9fa78a2d9ce3f3fdcc2e78a34ac5f0",
        )

        ffmpeg = self.module_by_name["ffmpeg"]
        ffmpeg_source = ffmpeg["sources"][0]
        self.assertEqual(ffmpeg_source["url"], "https://ffmpeg.org/releases/ffmpeg-8.1.2.tar.xz")
        self.assertEqual(ffmpeg_source["sha256"], "464beb5e7bf0c311e68b45ae2f04e9cc2af88851abb4082231742a74d97b524c")
        ffmpeg_commands = " ".join(ffmpeg["build-commands"])
        for option in ("--enable-vaapi", "--enable-libdrm", "--enable-vulkan", "--disable-libplacebo"):
            self.assertIn(option, ffmpeg_commands)
        self.assertIn("libavcodec.so.62", ffmpeg_commands)
        self.assertIn("ffmpeg version 8.1.2", ffmpeg_commands)

        placebo = self.module_by_name["libplacebo"]
        self.assertEqual(placebo["sources"][0]["commit"], "cee9b076f2c63104ccfd497fa79c39a867293ec4")
        self.assertTrue({"-Ddovi=enabled", "-Dlibdovi=enabled"}.issubset(placebo["config-opts"]))
        placebo_checks = " ".join(placebo["post-install"])
        self.assertIn("pl_has_dovi", placebo_checks)
        self.assertIn("pl_has_libdovi", placebo_checks)

    def test_libdovi_source_and_cargo_inputs_are_pinned(self):
        libdovi = self.module_by_name["libdovi"]
        source = libdovi["sources"][0]
        self.assertEqual(source["url"], "https://static.crates.io/crates/dolby_vision/dolby_vision-3.3.2.crate")
        self.assertEqual(source["sha256"], "533d9f32336f4d03822e87fcee32277fd4a52e3f1278549bf42c8b28e3b0f798")
        self.assertEqual(source["archive-type"], "tar-gzip")
        self.assertIn("cargo-sources-dolby_vision-3.3.2.json", libdovi["sources"])
        commands = " ".join(libdovi["build-commands"])
        self.assertIn("--frozen --offline", commands)
        self.assertIn("--features capi", commands)
        self.assertEqual(
            libdovi["build-options"]["env"],
            {
                "CARGO_HOME": "/run/build/libdovi/cargo",
                "CARGO_NET_OFFLINE": "true",
            },
        )

        archive_sources = [
            cargo_source
            for cargo_source in self.cargo_sources
            if cargo_source["type"] == "archive"
        ]
        checksum_sources = [
            cargo_source
            for cargo_source in self.cargo_sources
            if cargo_source.get("dest-filename") == ".cargo-checksum.json"
        ]
        config_sources = [
            cargo_source
            for cargo_source in self.cargo_sources
            if cargo_source.get("dest-filename") == "config"
        ]
        self.assertEqual(len(archive_sources), 83)
        self.assertEqual(len(checksum_sources), 83)
        self.assertEqual(len(config_sources), 1)
        self.assertIn("replace-with = \"vendored-sources\"", config_sources[0]["contents"])

        for cargo_source in archive_sources:
            self.assertEqual(cargo_source["type"], "archive")
            self.assertEqual(cargo_source["archive-type"], "tar-gzip")
            self.assertTrue(cargo_source["url"].startswith("https://static.crates.io/crates/"))
            self.assertRegex(cargo_source["sha256"], r"^[0-9a-f]{64}$")
            self.assertTrue(cargo_source["dest"].startswith("cargo/vendor/"))
        archive_destinations = {source["dest"] for source in archive_sources}
        self.assertEqual(len(archive_destinations), 83)
        self.assertEqual(
            {source["dest"] for source in checksum_sources},
            archive_destinations,
        )
        sources = {source["url"]: source["sha256"] for source in archive_sources}
        self.assertEqual(
            sources["https://static.crates.io/crates/bitvec/bitvec-1.0.1.crate"],
            "1bc2832c24239b0141d5674bb9174f9d68a8b5b3f2753311927c172ca46f7e9c",
        )
        self.assertEqual(
            sources["https://static.crates.io/crates/libc/libc-0.2.172.crate"],
            "d750af042f7ef4f724306de029d18836c26c1765a54a6a3f094cbd23a7267ffa",
        )

    def test_legacy_remains_the_gsettings_default(self):
        schema = SCHEMA_PATH.read_text(encoding="utf-8")
        self.assertIn('<key name="render-backend" enum="io.github.rusmikev.CineHDR.RenderBackend">', schema)
        self.assertIn("<default>'legacy'</default>", schema)


if __name__ == "__main__":
    unittest.main()
