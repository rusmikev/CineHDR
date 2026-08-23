#!/usr/bin/env python3
"""Environment-independent checks for the ADR-0004 Gate 2 harness contract."""

from __future__ import annotations

import copy
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import generate_patterns
import run_pixel_pipeline


def fixture_provenance(mode: str) -> dict:
    spec = generate_patterns.fixture_spec(mode)
    stream = {
        "codec_name": "ffv1",
        "width": generate_patterns.WIDTH,
        "height": generate_patterns.HEIGHT,
        "pix_fmt": spec.pixel_format,
        "color_range": spec.color_range,
        "color_space": spec.color_space,
        "color_transfer": spec.color_transfer,
        "color_primaries": spec.color_primaries,
        "r_frame_rate": f"{generate_patterns.FPS}/1",
        "nb_read_frames": str(generate_patterns.FRAME_COUNT),
    }
    return {
        "schema": generate_patterns.FIXTURE_SCHEMA,
        "generator_revision": generate_patterns.GENERATOR_REVISION,
        "mode": mode,
        "dimensions": {"width": generate_patterns.WIDTH, "height": generate_patterns.HEIGHT},
        "fps": generate_patterns.FPS,
        "frame_count": generate_patterns.FRAME_COUNT,
        "ffmpeg_version": "ffmpeg version 8.1.2",
        "ffprobe_version": "ffprobe version 8.1.2",
        "sha256": "0" * 64,
        "stream": stream,
        "patches": generate_patterns.serialized_patches(spec),
        "path": f"/tmp/cinehdr-gate2-{mode}.mkv",
    }


def yuv_fixture_provenance(mode: str) -> dict:
    spec = generate_patterns.fixture_spec(mode)
    fixture = fixture_provenance(mode)
    stream = generate_patterns.expected_yuv_stream(spec)
    stream["format_name"] = "mov,mp4"
    fixture.update({
        "schema": generate_patterns.HEVC_FIXTURE_SCHEMA,
        "generator_revision": generate_patterns.HEVC_GENERATOR_REVISION,
        "fixture_profile": generate_patterns.HEVC_MAIN10_YUV420P10_PROFILE,
        "stream": stream,
        "path": f"/tmp/cinehdr-gate2-hevc-{mode}.mp4",
        "x265_identity": {
            "encoder": "x265 [info]: HEVC encoder version test",
            "build": "x265 [info]: build info test 10bit",
        },
        "raw_artifacts": {
            "source_rgb": {"filename": "source.raw", "sha256": "1" * 64, "size": 8294400},
            "encoder_yuv": {"filename": "encoder.raw", "sha256": "2" * 64, "size": 4147200},
            "decoded_yuv": {"filename": "decoded.raw", "sha256": "2" * 64, "size": 4147200},
            "reconstructed_rgb": {"filename": "reconstructed.raw", "sha256": "3" * 64, "size": 8294400},
        },
        "decoded_yuv_frame_content": {
            "policy": "all-decoded-frames-byte-identical",
            "frame_count": generate_patterns.FRAME_COUNT,
            "frame_size": generate_patterns.yuv_frame_size(),
            "first_frame_sha256": "4" * 64,
        },
        "conversion": {
            "source_pixel_format": "gbrp10le",
            "encoder_pixel_format": generate_patterns.HEVC_PIXEL_FORMAT,
            "range": "full",
            "matrix": generate_patterns.yuv_matrix(spec),
            "filter": generate_patterns.yuv_conversion_filter(spec),
            "inverse_filter": generate_patterns.yuv_inverse_filter(spec),
        },
        "encoding": {
            "codec": "libx265",
            "profile": "main10",
            "preset": generate_patterns.HEVC_X265_PRESET,
            "x265_params": generate_patterns.HEVC_X265_PARAMS,
            "threads": 1,
            "chroma_location": generate_patterns.HEVC_CHROMA_LOCATION,
            "container": generate_patterns.HEVC_CONTAINER,
            "muxer": generate_patterns.HEVC_MUXER,
            "command": [
                "ffmpeg", "-c:v", "libx265",
                *generate_patterns.HEVC_MP4_OUTPUT_OPTIONS, "fixture.mp4",
            ],
        },
        "rgb_yuv_reconstruction_error": {
            "source_pixel_format": "gbrp10le",
            "reconstructed_pixel_format": "gbrp10le",
            "component_count": generate_patterns.WIDTH * generate_patterns.HEIGHT * 3,
            "nonzero_component_count": 1,
            "maximum_absolute_10bit_code_error": 1,
            "mean_absolute_10bit_code_error": 0.1,
            "policy": "diagnostic-only; no color tolerance is inferred from this conversion",
            "patch_samples": [
                {
                    "name": patch.name,
                    "source_rgb_10bit": [1, 2, 3],
                    "reconstructed_rgb_10bit": [1, 2, 3],
                    "delta_rgb_10bit": [0, 0, 0],
                }
                for patch in spec.patches
            ],
        },
    })
    return fixture


def probe_result(
    mode: str,
    api: str,
    decode_mode: str = "no",
    fixture_profile: str = generate_patterns.RGB_FFV1_PROFILE,
) -> dict:
    spec = generate_patterns.fixture_spec(mode)
    ref_white = 0.5807 if mode in ("hdr", "hlg") else 1.0
    values = {
        "black": (0.0001, 0.0001, 0.0001) if mode in ("hdr", "hlg") else (0.0, 0.0, 0.0),
        "near_black": (0.01, 0.01, 0.01),
        "reference_white": (ref_white, ref_white, ref_white),
        "peak_white": (0.95, 0.95, 0.95) if mode in ("hdr", "hlg") else (1.0, 1.0, 1.0),
        "red_primary": (0.9, 0.005, 0.005),
        "green_primary": (0.005, 0.9, 0.005),
        "blue_primary": (0.005, 0.005, 0.9),
        "neutral_gradient": (0.4, 0.4, 0.4),
    }
    input_metadata = {
        "primaries": spec.color_primaries,
        "transfer": spec.color_transfer,
        "matrix": spec.color_space,
        "levels": spec.color_range,
        "pixel_format": spec.pixel_format,
    }
    if fixture_profile == generate_patterns.HEVC_MAIN10_YUV420P10_PROFILE:
        input_metadata.update({
            "matrix": generate_patterns.yuv_matrix(spec),
            "levels": "pc",
            "pixel_format": "yuv420p10le",
        })
    delivery_mode = run_pixel_pipeline.delivery_for_profile(fixture_profile)
    return {
        "schema": run_pixel_pipeline.PROBE_SCHEMA,
        "requested_api": api,
        "active_api": api,
        "fixture_mode": mode,
        "frame_timestamp": 0.0,
        "capture": {
            "delivery_mode": delivery_mode,
            "time_pos": 0.0,
            "pause": "yes" if delivery_mode == "paused" else "no",
            "loop_file": "no" if delivery_mode == "paused" else "inf",
            "update_callback_observed": True,
        },
        "loaded": {
            "libmpv_path": "/pinned/lib64/libmpv.so.2",
            "libplacebo_path": "/usr/lib64/libplacebo.so.360",
            "mpv_version": run_pixel_pipeline.EXPECTED_MPV_VERSION,
            "libplacebo_version": "v" + run_pixel_pipeline.EXPECTED_LIBPLACEBO_VERSION,
            "ffmpeg_version": "n" + run_pixel_pipeline.EXPECTED_FFMPEG_VERSION,
        },
        "input": input_metadata,
        "output": {
            "target_primaries": spec.target_primaries,
            "target_transfer": spec.target_transfer,
            "target_peak": str(int(spec.target_peak_nits)),
        },
        "fbo": {
            "width": generate_patterns.WIDTH,
            "height": generate_patterns.HEIGHT,
            "internal_format": "GL_RGBA16F" if mode == "hdr" else "GL_RGBA8",
            "depth": 16 if mode == "hdr" else 8,
            "complete": True,
        },
        "gl": {"vendor": "Mesa", "renderer": "llvmpipe", "version": "4.6"},
        "decode": {"requested": decode_mode, "active": decode_mode},
        "readback": {
            "nonempty": True,
            "gl_error": 0,
            "flip_y": False,
            "gl_readback_origin": "bottom-left",
            "source_grid_mapping": "source-row-0-to-gl-row-0",
            "patches": [
                {
                    "name": patch.name,
                    "row": patch.row,
                    "column": patch.column,
                    "r": values[patch.name][0],
                    "g": values[patch.name][1],
                    "b": values[patch.name][2],
                }
                for patch in spec.patches
            ],
        },
        "errors": {"mpv": None, "fbo": None, "gl": None},
        "rendered_frames": 1,
    }


class TestFixtureMathAndLayout(unittest.TestCase):
    def test_pq_reference_values_are_stable(self) -> None:
        self.assertAlmostEqual(generate_patterns.linear_to_pq(0.0), 7.309559025783966e-07, places=12)
        self.assertAlmostEqual(generate_patterns.linear_to_pq(203.0), 0.5806888810416109, places=12)
        self.assertAlmostEqual(generate_patterns.linear_to_pq(10000.0), 1.0, places=12)
        self.assertEqual(generate_patterns.linear_to_pq(-1.0), generate_patterns.linear_to_pq(0.0))

    def test_srgb_full_range_math_and_patch_layout(self) -> None:
        self.assertEqual(generate_patterns.linear_to_srgb(0.0), 0.0)
        self.assertAlmostEqual(generate_patterns.linear_to_srgb(1.0), 1.0)
        self.assertAlmostEqual(generate_patterns.linear_to_srgb(0.18), 0.4613561295, places=9)
        for mode, pixel_format, transfer in (
            ("hdr", "gbrp10le", "smpte2084"),
            ("hlg", "gbrp10le", "arib-std-b67"),
            ("sdr", "gbrp10le", "iec61966-2-1"),
        ):
            spec = generate_patterns.fixture_spec(mode)
            self.assertEqual(spec.pixel_format, pixel_format)
            self.assertEqual(spec.color_transfer, transfer)
            self.assertEqual(len(spec.patches), 8)
            self.assertEqual({patch.name for patch in spec.patches}, {
                "black", "near_black", "reference_white", "peak_white",
                "red_primary", "green_primary", "blue_primary", "neutral_gradient",
            })
            self.assertIsNotNone(next(patch for patch in spec.patches if patch.name == "neutral_gradient").gradient_end)

    def test_raw_frames_are_planar_and_deterministic(self) -> None:
        hdr = generate_patterns.fixture_spec("hdr")
        sdr = generate_patterns.fixture_spec("sdr")
        self.assertEqual(generate_patterns.raw_frame(hdr), generate_patterns.raw_frame(hdr))
        self.assertEqual(len(generate_patterns.raw_frame(hdr)), generate_patterns.WIDTH * generate_patterns.HEIGHT * 3 * 2)
        self.assertEqual(len(generate_patterns.raw_frame(sdr)), generate_patterns.WIDTH * generate_patterns.HEIGHT * 3 * 2)
        self.assertEqual(generate_patterns.raw_frame(hdr)[:6], b"\0" * 6)
        self.assertEqual(generate_patterns.patch_centers()["reference_white"], (200, 45))
        self.assertEqual(
            generate_patterns.patch_centers(readback_origin="bottom-left")["reference_white"],
            (200, 135),
        )

    def test_stream_metadata_requires_full_range_rgb_tags(self) -> None:
        fixture = fixture_provenance("hdr")
        generate_patterns.validate_stream_metadata(generate_patterns.fixture_spec("hdr"), fixture["stream"])
        invalid = copy.deepcopy(fixture["stream"])
        invalid["color_range"] = "tv"
        with self.assertRaises(generate_patterns.FixtureError):
            generate_patterns.validate_stream_metadata(generate_patterns.fixture_spec("hdr"), invalid)
        invalid = copy.deepcopy(fixture["stream"])
        del invalid["color_transfer"]
        with self.assertRaises(generate_patterns.FixtureError):
            generate_patterns.validate_stream_metadata(generate_patterns.fixture_spec("hdr"), invalid)

    def test_fixture_profiles_preserve_rgb_default_and_declare_yuv_contract(self) -> None:
        rgb = fixture_provenance("hdr")
        self.assertNotIn("fixture_profile", rgb)
        self.assertEqual(
            generate_patterns.fixture_profile_from_metadata(rgb),
            generate_patterns.RGB_FFV1_PROFILE,
        )
        yuv = yuv_fixture_provenance("hdr")
        self.assertEqual(
            yuv["fixture_profile"], generate_patterns.HEVC_MAIN10_YUV420P10_PROFILE
        )
        self.assertEqual(yuv["stream"]["codec_name"], "hevc")
        self.assertEqual(yuv["stream"]["profile"], "Main 10")
        self.assertEqual(yuv["stream"]["pix_fmt"], "yuv420p10le")
        self.assertEqual(yuv["stream"]["color_space"], "bt2020nc")
        self.assertEqual(yuv["stream"]["avg_frame_rate"], "24/1")
        self.assertEqual(yuv["stream"]["time_base"], "1/24000")
        self.assertEqual(yuv["stream"]["duration"], "1.000000")
        self.assertIn("mp4", yuv["stream"]["format_name"].split(","))
        self.assertIn("lossless=1", yuv["encoding"]["x265_params"])
        self.assertEqual(yuv["encoding"]["container"], "iso-bmff-mp4")
        self.assertEqual(yuv["encoding"]["muxer"], "mp4")
        self.assertEqual(
            yuv["decoded_yuv_frame_content"],
            {
                "policy": "all-decoded-frames-byte-identical",
                "frame_count": generate_patterns.FRAME_COUNT,
                "frame_size": generate_patterns.yuv_frame_size(),
                "first_frame_sha256": "4" * 64,
            },
        )
        self.assertEqual(
            generate_patterns.hevc_media_path(Path("/tmp"), "hdr").suffix, ".mp4"
        )
        self.assertEqual(
            generate_patterns.fixture_media_path(Path("/tmp"), "hdr").suffix, ".mkv"
        )
        self.assertIn("matrix=bt2020nc", yuv["conversion"]["filter"])
        self.assertIn("matrix=gbr", yuv["conversion"]["inverse_filter"])

    def test_hevc_frame_content_requires_every_decoded_frame_to_match(self) -> None:
        frame = b"\x5a" * generate_patterns.yuv_frame_size()
        content = frame * generate_patterns.FRAME_COUNT
        self.assertEqual(
            generate_patterns.decoded_yuv_frame_content(content),
            {
                "policy": "all-decoded-frames-byte-identical",
                "frame_count": generate_patterns.FRAME_COUNT,
                "frame_size": generate_patterns.yuv_frame_size(),
                "first_frame_sha256": generate_patterns.sha256_bytes(frame),
            },
        )
        mismatched = bytearray(content)
        mismatched[-1] ^= 1
        with self.assertRaisesRegex(generate_patterns.FixtureError, "byte-identical"):
            generate_patterns.decoded_yuv_frame_content(bytes(mismatched))

    def test_cached_hevc_rejects_tampered_error_model_without_ffmpeg(self) -> None:
        """The cache must rederive diagnostic evidence rather than trust JSON."""

        spec = generate_patterns.fixture_spec("hdr")
        metadata = yuv_fixture_provenance("hdr")
        metadata["encoding"]["command"] = [
            "ffmpeg", "-bitexact", "-c:v", "libx265",
            *generate_patterns.HEVC_MP4_OUTPUT_OPTIONS, "fixture.mp4",
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            cache_dir = Path(temporary_directory)
            media = generate_patterns.hevc_media_path(cache_dir, spec.mode)
            media.write_bytes(b"iso-bmff-mp4")
            metadata_path = cache_dir / "fixture.json"
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            raw_paths = generate_patterns.hevc_raw_paths(cache_dir, spec.mode)
            raw_paths["source_rgb"].write_bytes(b"source")
            raw_paths["reconstructed_rgb"].write_bytes(b"reconstructed")

            yuv_artifact = {
                "filename": "raw", "sha256": "2" * 64,
                "size": generate_patterns.yuv_frame_size() * generate_patterns.FRAME_COUNT,
            }
            with mock.patch.object(generate_patterns, "sha256_file", return_value="0" * 64), \
                 mock.patch.object(generate_patterns, "repeated_raw_frames", return_value=b"source"), \
                 mock.patch.object(generate_patterns, "_require_raw_artifact"), \
                 mock.patch.object(generate_patterns, "_raw_artifact", return_value=yuv_artifact), \
                 mock.patch.object(generate_patterns, "_decode_hevc_to_yuv"), \
                 mock.patch.object(
                     generate_patterns, "ffprobe_yuv_stream", return_value=metadata["stream"]
                 ), \
                 mock.patch.object(
                     generate_patterns, "rgb_yuv_error_model", return_value={"computed": "not-json"}
                 ):
                self.assertIsNone(generate_patterns._valid_cached_hevc_fixture(
                    spec, cache_dir, media, metadata_path, raw_paths, "ffmpeg", "ffprobe"
                ))

    def test_cached_hevc_rejects_tampered_frame_content_without_ffmpeg(self) -> None:
        """The cache must recalculate the continuous-delivery frame proof."""

        spec = generate_patterns.fixture_spec("hdr")
        metadata = yuv_fixture_provenance("hdr")
        metadata["encoding"]["command"] = [
            "ffmpeg", "-bitexact", "-c:v", "libx265",
            *generate_patterns.HEVC_MP4_OUTPUT_OPTIONS, "fixture.mp4",
        ]
        with tempfile.TemporaryDirectory() as temporary_directory:
            cache_dir = Path(temporary_directory)
            media = generate_patterns.hevc_media_path(cache_dir, spec.mode)
            media.write_bytes(b"iso-bmff-mp4")
            metadata_path = cache_dir / "fixture.json"
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            raw_paths = generate_patterns.hevc_raw_paths(cache_dir, spec.mode)
            raw_paths["source_rgb"].write_bytes(b"source")
            raw_paths["reconstructed_rgb"].write_bytes(b"reconstructed")
            raw_paths["decoded_yuv"].write_bytes(
                b"\x5a" * (generate_patterns.yuv_frame_size() * generate_patterns.FRAME_COUNT)
            )
            yuv_artifact = {
                "filename": "raw", "sha256": "2" * 64,
                "size": generate_patterns.yuv_frame_size() * generate_patterns.FRAME_COUNT,
            }
            with mock.patch.object(generate_patterns, "sha256_file", return_value="0" * 64), \
                 mock.patch.object(generate_patterns, "repeated_raw_frames", return_value=b"source"), \
                 mock.patch.object(generate_patterns, "_require_raw_artifact"), \
                 mock.patch.object(generate_patterns, "_raw_artifact", return_value=yuv_artifact), \
                 mock.patch.object(generate_patterns, "_decode_hevc_to_yuv"), \
                 mock.patch.object(
                     generate_patterns, "ffprobe_yuv_stream", return_value=metadata["stream"]
                 ), \
                 mock.patch.object(
                     generate_patterns,
                     "rgb_yuv_error_model",
                     return_value=metadata["rgb_yuv_reconstruction_error"],
                 ):
                self.assertIsNone(generate_patterns._valid_cached_hevc_fixture(
                    spec, cache_dir, media, metadata_path, raw_paths, "ffmpeg", "ffprobe"
                ))


class TestProbeContract(unittest.TestCase):
    def test_parser_rejects_missing_or_nonobject_json(self) -> None:
        with self.assertRaises(run_pixel_pipeline.PipelineError):
            run_pixel_pipeline.parse_probe_json("not json")
        with self.assertRaises(run_pixel_pipeline.PipelineError):
            run_pixel_pipeline.parse_probe_json("[]")
        with self.assertRaises(run_pixel_pipeline.PipelineError):
            run_pixel_pipeline.parse_probe_json(json.dumps({"schema": "wrong"}))
        fixture = fixture_provenance("hdr")
        parsed = probe_result("hdr", "opengl")
        completed = subprocess.CompletedProcess(
            ["pixel-validator"], 0, stdout=json.dumps(parsed), stderr="warm-up stderr"
        )
        with mock.patch.object(
            run_pixel_pipeline.subprocess, "run", return_value=completed
        ), mock.patch.object(run_pixel_pipeline.time, "monotonic", side_effect=[10.0, 10.25]):
            warmup = run_pixel_pipeline._run_shader_warmup(
                Path("/tmp/pixel-validator"), fixture, "opengl", "0", "surfaceless",
                "no", "paused", generate_patterns.RGB_FFV1_PROFILE,
            )
        self.assertEqual(warmup["purpose"], run_pixel_pipeline.SHADER_WARMUP_PURPOSE)
        self.assertEqual(warmup["exit_status"], 0)
        self.assertEqual(warmup["duration_seconds"], 0.25)
        self.assertTrue(warmup["probe_completed"])
        self.assertEqual(warmup["result"]["capture"], parsed["capture"])
        self.assertEqual(warmup["result"]["loaded"], parsed["loaded"])
        self.assertNotIn("readback", warmup["result"])
        invalid_json = subprocess.CompletedProcess(
            ["pixel-validator"], 0, stdout="not JSON", stderr="bad output"
        )
        with mock.patch.object(
            run_pixel_pipeline.subprocess, "run", return_value=invalid_json
        ), mock.patch.object(run_pixel_pipeline.time, "monotonic", side_effect=[10.0, 10.1]):
            malformed_warmup = run_pixel_pipeline._run_shader_warmup(
                Path("/tmp/pixel-validator"), fixture, "opengl", "0", "surfaceless",
                "no", "paused", generate_patterns.RGB_FFV1_PROFILE,
            )
        self.assertFalse(malformed_warmup["probe_completed"])
        self.assertIn("json_error", malformed_warmup)
        self.assertNotIn("result", malformed_warmup)
        with mock.patch.object(
            run_pixel_pipeline.subprocess, "run", side_effect=OSError("missing binary")
        ):
            with self.assertRaisesRegex(run_pixel_pipeline.PipelineError, "could not launch"):
                run_pixel_pipeline._run_shader_warmup(
                    Path("/tmp/pixel-validator"), fixture, "opengl", "0", "surfaceless",
                    "no", "paused", generate_patterns.RGB_FFV1_PROFILE,
                )

    def test_complete_probe_evidence_is_accepted_without_color_threshold(self) -> None:
        fixture = fixture_provenance("hdr")
        result = probe_result("hdr", "opengl")
        run_pixel_pipeline.validate_fixture_provenance(fixture)
        run_pixel_pipeline.validate_probe_result(result, fixture, "opengl", mpv_prefix=Path("/pinned"))
        diagnostics = run_pixel_pipeline.diagnostic_invariants(result)
        self.assertTrue(diagnostics["finite"])
        self.assertTrue(diagnostics["black_distinction_observed"])
        self.assertTrue(diagnostics["black_floor_pass"])
        self.assertTrue(diagnostics["neutral_axis_pass"])
        self.assertTrue(diagnostics["reference_white_pass"])
        self.assertEqual(diagnostics["verdict"], "PASS")
        self.assertIn("ADR-0005", diagnostics["threshold_policy"])

    def test_synthetic_threshold_violations_fail_verdict(self) -> None:
        result = probe_result("hdr", "opengl")
        # Exceed black threshold
        result["readback"]["patches"][0]["r"] = 0.01
        diagnostics = run_pixel_pipeline.diagnostic_invariants(result)
        self.assertFalse(diagnostics["black_floor_pass"])
        self.assertEqual(diagnostics["verdict"], "FAIL")

        # Exceed neutral axis divergence threshold
        result = probe_result("hdr", "opengl")
        result["readback"]["patches"][2]["r"] = result["readback"]["patches"][2]["g"] + 0.02
        diagnostics = run_pixel_pipeline.diagnostic_invariants(result)
        self.assertFalse(diagnostics["neutral_axis_pass"])
        self.assertEqual(diagnostics["verdict"], "FAIL")

    def test_yuv_fixture_provenance_and_semantic_input_layout_are_strict(self) -> None:
        fixture = yuv_fixture_provenance("hdr")
        result = probe_result(
            "hdr", "opengl", "vaapi-copy",
            generate_patterns.HEVC_MAIN10_YUV420P10_PROFILE,
        )
        result["input"]["pixel_format"] = "p010"
        result["input"]["matrix"] = "bt2020-ncl"
        run_pixel_pipeline.validate_fixture_provenance(
            fixture, expected_profile=generate_patterns.HEVC_MAIN10_YUV420P10_PROFILE
        )
        run_pixel_pipeline.validate_probe_result(
            result, fixture, "opengl", expected_decode="vaapi-copy"
        )
        result["input"]["pixel_format"] = "gbrp10le"
        with self.assertRaisesRegex(run_pixel_pipeline.PipelineError, "10-bit 4:2:0"):
            run_pixel_pipeline.validate_probe_result(
                result, fixture, "opengl", expected_decode="vaapi-copy"
            )

    def test_yuv_fixture_rejects_missing_or_mismatched_identity_evidence(self) -> None:
        fixture = yuv_fixture_provenance("sdr")
        invalid = copy.deepcopy(fixture)
        invalid["raw_artifacts"]["decoded_yuv"]["sha256"] = "4" * 64
        with self.assertRaisesRegex(run_pixel_pipeline.PipelineError, "decoded YUV SHA"):
            run_pixel_pipeline.validate_fixture_provenance(
                invalid, expected_profile=generate_patterns.HEVC_MAIN10_YUV420P10_PROFILE
            )
        invalid = copy.deepcopy(fixture)
        del invalid["x265_identity"]["build"]
        with self.assertRaisesRegex(run_pixel_pipeline.PipelineError, "x265_identity.build"):
            run_pixel_pipeline.validate_fixture_provenance(
                invalid, expected_profile=generate_patterns.HEVC_MAIN10_YUV420P10_PROFILE
            )
        invalid = copy.deepcopy(fixture)
        invalid["stream"]["profile"] = "Main"
        with self.assertRaisesRegex(run_pixel_pipeline.PipelineError, "HEVC stream metadata"):
            run_pixel_pipeline.validate_fixture_provenance(
                invalid, expected_profile=generate_patterns.HEVC_MAIN10_YUV420P10_PROFILE
            )
        invalid = copy.deepcopy(fixture)
        invalid["stream"]["time_base"] = "1/1000"
        with self.assertRaisesRegex(run_pixel_pipeline.PipelineError, "HEVC stream metadata"):
            run_pixel_pipeline.validate_fixture_provenance(
                invalid, expected_profile=generate_patterns.HEVC_MAIN10_YUV420P10_PROFILE
            )
        invalid = copy.deepcopy(fixture)
        invalid["encoding"]["muxer"] = "matroska"
        with self.assertRaisesRegex(run_pixel_pipeline.PipelineError, "HEVC encoding provenance"):
            run_pixel_pipeline.validate_fixture_provenance(
                invalid, expected_profile=generate_patterns.HEVC_MAIN10_YUV420P10_PROFILE
            )
        invalid = copy.deepcopy(fixture)
        invalid["encoding"]["container"] = "elementary-hevc"
        with self.assertRaisesRegex(run_pixel_pipeline.PipelineError, "HEVC encoding provenance"):
            run_pixel_pipeline.validate_fixture_provenance(
                invalid, expected_profile=generate_patterns.HEVC_MAIN10_YUV420P10_PROFILE
            )
        invalid = copy.deepcopy(fixture)
        invalid["stream"]["format_name"] = "matroska,webm"
        with self.assertRaisesRegex(run_pixel_pipeline.PipelineError, "HEVC stream metadata"):
            run_pixel_pipeline.validate_fixture_provenance(
                invalid, expected_profile=generate_patterns.HEVC_MAIN10_YUV420P10_PROFILE
            )
        invalid = copy.deepcopy(fixture)
        del invalid["rgb_yuv_reconstruction_error"]["policy"]
        with self.assertRaisesRegex(run_pixel_pipeline.PipelineError, "reconstruction error provenance"):
            run_pixel_pipeline.validate_fixture_provenance(
                invalid, expected_profile=generate_patterns.HEVC_MAIN10_YUV420P10_PROFILE
            )
        invalid = copy.deepcopy(fixture)
        invalid["decoded_yuv_frame_content"]["policy"] = "first-frame-only"
        with self.assertRaisesRegex(run_pixel_pipeline.PipelineError, "frame-content provenance"):
            run_pixel_pipeline.validate_fixture_provenance(
                invalid, expected_profile=generate_patterns.HEVC_MAIN10_YUV420P10_PROFILE
            )

    def test_missing_evidence_is_an_error_not_a_skip(self) -> None:
        fixture = fixture_provenance("sdr")
        invalid_fixture = copy.deepcopy(fixture)
        invalid_fixture["schema"] = "wrong"
        with self.assertRaisesRegex(run_pixel_pipeline.PipelineError, "schema"):
            run_pixel_pipeline.validate_fixture_provenance(invalid_fixture)
        invalid_fixture = copy.deepcopy(fixture)
        invalid_fixture["generator_revision"] = "stale"
        with self.assertRaisesRegex(run_pixel_pipeline.PipelineError, "generator revision"):
            run_pixel_pipeline.validate_fixture_provenance(invalid_fixture)
        result = probe_result("sdr", "opengl")
        result["loaded"]["libplacebo_path"] = ""
        with self.assertRaisesRegex(run_pixel_pipeline.PipelineError, "libplacebo"):
            run_pixel_pipeline.validate_probe_result(result, fixture, "opengl", mpv_prefix=Path("/pinned"))
        for field, invalid_value in (
            ("mpv_version", "mpv v0.41.0"),
            ("libplacebo_version", "v7.349.0"),
            ("ffmpeg_version", "n8.0"),
        ):
            result = probe_result("sdr", "opengl")
            result["loaded"][field] = invalid_value
            with self.assertRaisesRegex(run_pixel_pipeline.PipelineError, "unexpected loaded"):
                run_pixel_pipeline.validate_probe_result(
                    result, fixture, "opengl", mpv_prefix=Path("/pinned")
                )
        result = probe_result("sdr", "opengl")
        result["readback"]["nonempty"] = False
        with self.assertRaisesRegex(run_pixel_pipeline.PipelineError, "readback"):
            run_pixel_pipeline.validate_probe_result(result, fixture, "opengl", mpv_prefix=Path("/pinned"))

    def test_api_fbo_and_decode_contracts_are_strict(self) -> None:
        fixture = fixture_provenance("hdr")
        result = probe_result("hdr", "opengl")
        result["active_api"] = "opengl-next"
        with self.assertRaisesRegex(run_pixel_pipeline.PipelineError, "render API"):
            run_pixel_pipeline.validate_probe_result(result, fixture, "opengl")
        result = probe_result("hdr", "opengl")
        result["fbo"]["depth"] = 8
        with self.assertRaisesRegex(run_pixel_pipeline.PipelineError, "FBO"):
            run_pixel_pipeline.validate_probe_result(result, fixture, "opengl")
        for decode_mode in run_pixel_pipeline.DECODE_MODES:
            result = probe_result("hdr", "opengl", decode_mode)
            run_pixel_pipeline.validate_probe_result(
                result, fixture, "opengl", expected_decode=decode_mode
            )
        result = probe_result("hdr", "opengl", "vaapi-copy")
        result["decode"]["active"] = "no"
        with self.assertRaisesRegex(
            run_pixel_pipeline.PipelineError,
            r"decode\.requested='vaapi-copy'.*decode\.active='no'.*expected='vaapi-copy'",
        ):
            run_pixel_pipeline.validate_probe_result(
                result, fixture, "opengl", expected_decode="vaapi-copy"
            )
        result = probe_result("hdr", "opengl", "no")
        result["decode"]["requested"] = "vaapi-copy"
        with self.assertRaisesRegex(run_pixel_pipeline.PipelineError, "decode"):
            run_pixel_pipeline.validate_probe_result(
                result, fixture, "opengl", expected_decode="no"
            )
        with self.assertRaises(run_pixel_pipeline.PipelineError):
            run_pixel_pipeline.validate_decode_mode("auto")

    def test_capture_delivery_follows_profile_and_records_live_state(self) -> None:
        rgb_fixture = fixture_provenance("hdr")
        rgb = probe_result("hdr", "opengl")
        run_pixel_pipeline.validate_probe_result(rgb, rgb_fixture, "opengl")
        self.assertEqual(rgb["capture"]["delivery_mode"], "paused")
        self.assertEqual(rgb["capture"]["pause"], "yes")
        self.assertEqual(rgb["capture"]["loop_file"], "no")
        self.assertTrue(rgb["capture"]["update_callback_observed"])

        hevc_fixture = yuv_fixture_provenance("hdr")
        hevc = probe_result(
            "hdr", "opengl", fixture_profile=generate_patterns.HEVC_MAIN10_YUV420P10_PROFILE
        )
        hevc["capture"]["time_pos"] = 0.5
        run_pixel_pipeline.validate_probe_result(hevc, hevc_fixture, "opengl")
        self.assertEqual(hevc["capture"]["delivery_mode"], "continuous-identical")
        self.assertEqual(hevc["capture"]["pause"], "no")
        self.assertEqual(hevc["capture"]["loop_file"], "inf")
        self.assertTrue(hevc["capture"]["update_callback_observed"])

        invalid = copy.deepcopy(hevc)
        invalid["capture"]["delivery_mode"] = "paused"
        with self.assertRaisesRegex(run_pixel_pipeline.PipelineError, "delivery mode"):
            run_pixel_pipeline.validate_probe_result(invalid, hevc_fixture, "opengl")
        invalid = copy.deepcopy(hevc)
        invalid["capture"]["pause"] = "yes"
        with self.assertRaisesRegex(run_pixel_pipeline.PipelineError, "pause state"):
            run_pixel_pipeline.validate_probe_result(invalid, hevc_fixture, "opengl")
        invalid = copy.deepcopy(hevc)
        invalid["capture"]["loop_file"] = "no"
        with self.assertRaisesRegex(run_pixel_pipeline.PipelineError, "loop-file state"):
            run_pixel_pipeline.validate_probe_result(invalid, hevc_fixture, "opengl")
        invalid = copy.deepcopy(hevc)
        invalid["capture"]["time_pos"] = 1.001
        with self.assertRaisesRegex(run_pixel_pipeline.PipelineError, "fixture duration"):
            run_pixel_pipeline.validate_probe_result(invalid, hevc_fixture, "opengl")
        invalid = copy.deepcopy(hevc)
        invalid["capture"]["update_callback_observed"] = False
        with self.assertRaisesRegex(run_pixel_pipeline.PipelineError, "update_callback_observed"):
            run_pixel_pipeline.validate_probe_result(invalid, hevc_fixture, "opengl")
        invalid = copy.deepcopy(hevc)
        invalid["capture"]["update_callback_observed"] = "true"
        with self.assertRaisesRegex(run_pixel_pipeline.PipelineError, "true boolean evidence"):
            run_pixel_pipeline.validate_probe_result(invalid, hevc_fixture, "opengl")

    def test_pair_comparability_has_no_hot_swap_or_input_drift(self) -> None:
        fixture = fixture_provenance("hdr")
        legacy = probe_result("hdr", "opengl")
        next_api = probe_result("hdr", "opengl-next")
        run_pixel_pipeline.ensure_comparable(legacy, next_api, fixture)
        next_api["output"]["target_peak"] = "279"
        with self.assertRaisesRegex(run_pixel_pipeline.PipelineError, "comparable"):
            run_pixel_pipeline.ensure_comparable(legacy, next_api, fixture)
        for section, field, value in (
            ("loaded", "libplacebo_path", "/different/libplacebo.so.360"),
            ("gl", "renderer", "different renderer"),
            ("readback", "flip_y", True),
        ):
            next_api = probe_result("hdr", "opengl-next")
            next_api[section][field] = value
            with self.assertRaisesRegex(run_pixel_pipeline.PipelineError, "comparable"):
                run_pixel_pipeline.ensure_comparable(legacy, next_api, fixture)
        legacy = probe_result("hdr", "opengl", "vaapi-copy")
        next_api = probe_result("hdr", "opengl-next", "vaapi-copy")
        run_pixel_pipeline.ensure_comparable(
            legacy, next_api, fixture, expected_decode="vaapi-copy"
        )
        next_api["decode"]["active"] = "no"
        with self.assertRaisesRegex(run_pixel_pipeline.PipelineError, "decode mode"):
            run_pixel_pipeline.ensure_comparable(
                legacy, next_api, fixture, expected_decode="vaapi-copy"
            )
        hevc_fixture = yuv_fixture_provenance("hdr")
        legacy = probe_result(
            "hdr", "opengl", fixture_profile=generate_patterns.HEVC_MAIN10_YUV420P10_PROFILE
        )
        next_api = probe_result(
            "hdr", "opengl-next", fixture_profile=generate_patterns.HEVC_MAIN10_YUV420P10_PROFILE
        )
        legacy["capture"]["time_pos"] = 0.1
        next_api["capture"]["time_pos"] = 0.9
        run_pixel_pipeline.ensure_comparable(legacy, next_api, hevc_fixture)
        next_api["capture"]["delivery_mode"] = "paused"
        with self.assertRaisesRegex(run_pixel_pipeline.PipelineError, "delivery mode"):
            run_pixel_pipeline.ensure_comparable(legacy, next_api, hevc_fixture)

    def test_cli_exposes_only_accepted_decode_modes(self) -> None:
        completed_report = {"schema": run_pixel_pipeline.REPORT_SCHEMA, "status": "complete"}
        with mock.patch.object(run_pixel_pipeline, "run_pipeline", return_value=completed_report) as run, \
             mock.patch.object(run_pixel_pipeline, "_write_report"):
            self.assertEqual(run_pixel_pipeline.main([
                "--hwdec", "vaapi-copy", "--fixture-profile", "hevc-main10-yuv420p10-lossless",
            ]), 0)
            self.assertEqual(run_pixel_pipeline.main([]), 0)
        self.assertEqual(
            [call.args[-2:] for call in run.call_args_list],
            [("vaapi-copy", "hevc-main10-yuv420p10-lossless"), ("no", "rgb-ffv1")],
        )
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                run_pixel_pipeline.main(["--hwdec", "auto"])

        hdr_fixture = fixture_provenance("hdr")
        sdr_fixture = fixture_provenance("sdr")
        warmups = [
            {
                "purpose": run_pixel_pipeline.SHADER_WARMUP_PURPOSE,
                "api": api,
                "mode": "hdr",
                "fixture_profile": generate_patterns.RGB_FFV1_PROFILE,
                "decode_mode": "no",
                "delivery_mode": "paused",
                "exit_status": 1,
                "duration_seconds": 0.1,
                "probe_completed": False,
                "stdout": "",
                "stderr": "cold shader setup exited",
            }
            for api in ("opengl", "opengl-next")
        ]
        measured = [
            probe_result("hdr", "opengl"), probe_result("hdr", "opengl-next"),
            probe_result("sdr", "opengl"), probe_result("sdr", "opengl-next"),
        ]
        with mock.patch.object(
            run_pixel_pipeline, "validate_mpv_inputs",
            return_value=(Path("/headers"), Path("/pinned/lib64"), "pinned-commit"),
        ), mock.patch.object(
            generate_patterns, "ensure_fixture", side_effect=[hdr_fixture, sdr_fixture]
        ), mock.patch.object(run_pixel_pipeline, "compile_probe"), mock.patch.object(
            run_pixel_pipeline, "_run_shader_warmup", side_effect=warmups
        ) as warmup_run, mock.patch.object(
            run_pixel_pipeline, "_run_probe", side_effect=measured
        ) as measured_run:
            report = run_pixel_pipeline.run_pipeline(
                Path("/tmp/cache"), Path("/source"), Path("/pinned"), "cc", "0", "surfaceless"
            )
        self.assertEqual([call.args[2] for call in warmup_run.call_args_list], ["opengl", "opengl-next"])
        self.assertEqual(measured_run.call_count, 4)
        self.assertEqual(report["shader_warmup"], warmups)
        self.assertEqual(report["status"], "complete")

    def test_cli_failure_report_preserves_requested_decode_and_fixture_profile(self) -> None:
        warmups = [{"api": "opengl", "exit_status": 1, "probe_completed": False}]
        hdr_fixture = fixture_provenance("hdr")
        sdr_fixture = fixture_provenance("sdr")
        with mock.patch.object(
            run_pixel_pipeline, "validate_mpv_inputs",
            return_value=(Path("/headers"), Path("/pinned/lib64"), "pinned-commit"),
        ), mock.patch.object(
            generate_patterns, "ensure_fixture", side_effect=[hdr_fixture, sdr_fixture]
        ), mock.patch.object(run_pixel_pipeline, "compile_probe"), mock.patch.object(
            run_pixel_pipeline, "_run_shader_warmup", return_value=warmups[0]
        ), mock.patch.object(
            run_pixel_pipeline, "_run_probe",
            side_effect=run_pixel_pipeline.PipelineError("strict measured probe failed"),
        ):
            with self.assertRaises(run_pixel_pipeline.PipelineRunError) as raised:
                run_pixel_pipeline.run_pipeline(
                    Path("/tmp/cache"), Path("/source"), Path("/pinned"),
                    "cc", "0", "surfaceless",
                )
        self.assertIn("strict measured probe failed", str(raised.exception))
        self.assertEqual(raised.exception.shader_warmup, [warmups[0], warmups[0]])

        failure = run_pixel_pipeline.PipelineRunError(
            "vaapi-copy capability evidence failed", warmups
        )
        with mock.patch.object(run_pixel_pipeline, "run_pipeline", side_effect=failure), \
             mock.patch.object(run_pixel_pipeline, "_write_report") as write_report:
            self.assertEqual(run_pixel_pipeline.main([
                "--hwdec", "vaapi-copy", "--fixture-profile", "hevc-main10-yuv420p10-lossless",
            ]), 1)
        report = write_report.call_args.args[0]
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["decode_mode"], "vaapi-copy")
        self.assertEqual(report["fixture_profile"], "hevc-main10-yuv420p10-lossless")
        self.assertEqual(report["delivery_mode"], "continuous-identical")
        self.assertEqual(report["shader_warmup"], warmups)
        self.assertIn("capability evidence failed", report["error"])

    def test_compile_contract_names_sibling_headers_prefix_and_both_api_constants(self) -> None:
        source = Path(__file__).resolve().parent / "pixel_validator_egl.c"
        body = source.read_text(encoding="utf-8")
        self.assertIn("MPV_RENDER_API_TYPE_OPENGL", body)
        self.assertIn("MPV_RENDER_API_TYPE_OPENGL_NEXT", body)
        self.assertIn("update_flags & MPV_RENDER_UPDATE_FRAME", body)
        self.assertIn("requires_published_update", body)
        self.assertIn(
            "requires_published_update && !(update_flags & MPV_RENDER_UPDATE_FRAME)",
            body,
        )
        self.assertIn("proven-identical frame", body)
        self.assertIn("GL_RGBA16F", body)
        self.assertIn("GL_RGBA8", body)
        self.assertIn("MPV_RENDER_PARAM_BLOCK_FOR_TARGET_TIME", body)
        self.assertIn("int block_for_target_time = 0", body)
        self.assertIn("MPV_RENDER_PARAM_NEXT_FRAME_INFO", body)
        self.assertIn("mpv_render_context_get_info", body)
        self.assertIn("update_frame_count", body)
        self.assertIn("first_update_frame_flags", body)
        self.assertIn("last_update_target_time", body)
        self.assertIn("mpv_render_context_report_swap", body)
        self.assertIn("frame_primary_structure_ready", body)
        self.assertIn("last_primary_structure_ready", body)
        self.assertIn("primary-structure=%s", body)
        self.assertIn("readiness_pixels[0][0] > readiness_pixels[0][1]", body)
        self.assertIn("readiness_pixels[1][1] > readiness_pixels[1][0]", body)
        self.assertIn("readiness_pixels[2][2] > readiness_pixels[2][0]", body)
        self.assertIn('mpv_request_log_messages(mpv, "info")', body)
        self.assertNotIn('mpv_request_log_messages(mpv, "trace")', body)
        self.assertIn("atomic_bool", body)
        self.assertIn("mpv_render_context_set_update_callback", body)
        self.assertIn("atomic_exchange_explicit", body)
        self.assertIn("nanosleep", body)
        self.assertIn("update_callback_observed", body)
        self.assertIn('set_option(mpv, "loop-file", loop_file_mode)', body)
        self.assertIn("loop_file", body)
        self.assertNotIn("MPV_EVENT_PLAYBACK_RESTART", body)
        render_phase = body[
            body.index("while (monotonic_seconds() < deadline)"):
            body.index("const bool end_file_observed = drain_diagnostic_events")
        ]
        self.assertNotIn("mpv_wait_event", render_phase)
        self.assertNotIn("mpv_get_property", render_phase)
        self.assertNotIn("mpv_command", render_phase)
        self.assertNotIn("mpv_error_string", render_phase)
        self.assertIn('set_option(mpv, "hwdec", decode_mode)', body)
        self.assertNotIn('set_option(mpv, "correct-pts", "no")', body)
        self.assertNotIn('set_option(mpv, "container-fps-override", "24")', body)
        self.assertIn("argc != 7", body)
        self.assertIn("<no|vaapi-copy>", body)
        self.assertIn("<paused|continuous-identical>", body)
        self.assertIn('set_option(mpv, "pause", pause_mode)', body)
        self.assertIn("delivery_mode", body)
        self.assertNotIn("frame-step", body)
        self.assertNotIn("file_loaded", body)
        self.assertIn("never hot-swap", body)
        self.assertIn("sample_row = source_row", body)
        self.assertIn('"video-params/colorlevels"', body)
        self.assertNotIn('"video-params/levels"', body)
        self.assertIn('dlsym(RTLD_DEFAULT, "pl_version")', body)
        runner_body = Path(run_pixel_pipeline.__file__).read_text(encoding="utf-8")
        self.assertIn("mpv-gpu-next", runner_body)
        self.assertIn("mpv-gpu-next-prefix", runner_body)
        self.assertIn("-Wl,-rpath", runner_body)
        self.assertIn("--hwdec", runner_body)
        self.assertIn('DECODE_MODES = ("no", "vaapi-copy")', runner_body)
        self.assertIn('"decode_mode": hwdec', runner_body)
        self.assertIn("DELIVERY_BY_PROFILE", runner_body)
        self.assertIn('"delivery_mode": delivery_mode', runner_body)
        self.assertIn("capture.update_callback_observed", runner_body)
        self.assertIn("expected_loop_file", runner_body)
        self.assertIn("_run_shader_warmup", runner_body)
        self.assertIn("PipelineRunError", runner_body)
        self.assertIn("SHADER_WARMUP_PURPOSE", runner_body)
        self.assertNotIn("TIMING_BY_PROFILE", runner_body)
        self.assertNotIn("timing_mode", runner_body)
        self.assertIn("--fixture-profile", runner_body)
        self.assertIn('"fixture_profile": fixture_profile', runner_body)
        generator_body = Path(generate_patterns.__file__).read_text(encoding="utf-8")
        self.assertIn("setparams=range=full:colorspace=gbr", generator_body)
        self.assertIn('GENERATOR_REVISION = "ffv1-bitexact-v1"', generator_body)
        self.assertIn('"-fflags", "+bitexact"', generator_body)
        self.assertIn("hevc-main10-yuv420p10-lossless", generator_body)
        self.assertIn("lossless=1:pools=none:frame-threads=1:wpp=0", generator_body)
        self.assertIn("HEVC_MP4_OUTPUT_OPTIONS", generator_body)
        self.assertIn('"-tag:v", "hvc1"', generator_body)
        self.assertIn('"-video_track_timescale", "24000"', generator_body)
        self.assertIn('HEVC_GENERATOR_REVISION = "hevc-main10-yuv420p10-mp4-v2"', generator_body)
        self.assertIn("all-decoded-frames-byte-identical", generator_body)
        self.assertIn(".mp4", generator_body)
        self.assertNotIn('return cache_dir / f"{hevc_fixture_stem(mode)}.hevc"', generator_body)
        self.assertNotIn('return cache_dir / f"{hevc_fixture_stem(mode)}.mkv"', generator_body)


if __name__ == "__main__":
    unittest.main()
