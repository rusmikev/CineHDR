#!/usr/bin/env python3
"""Explicit runner for ADR-0004's offscreen embedded-FBO comparison.

This is intentionally outside Meson's default test suite.  It builds the C
probe against the sibling pinned mpv headers and prefix, then launches one
fresh process/context per renderer API.  It never exercises Wayland, DRM, or a
running CineHDR application.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterable

import generate_patterns


PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORK_ROOT = PROJECT_ROOT.parent
DEFAULT_MPV_SOURCE = WORK_ROOT / "mpv-gpu-next"
DEFAULT_MPV_PREFIX = WORK_ROOT / "mpv-gpu-next-prefix"
DEFAULT_CACHE_DIR = Path(tempfile.gettempdir()) / "cinehdr-gate2-fixtures"
EXPECTED_MPV_COMMIT = "97179bce7ed980c53647d6344916f632fe689e9e"
EXPECTED_MPV_VERSION = "mpv v0.41.0-dev-g97179bce7"
EXPECTED_LIBPLACEBO_VERSION = "7.360.1"
EXPECTED_FFMPEG_VERSION = "8.1.2"
REPORT_SCHEMA = "cinehdr.gate2.report.v1"
PROBE_SCHEMA = "cinehdr.gate2.probe.v1"
DECODE_MODES = ("no", "vaapi-copy")
FIXTURE_PROFILES = generate_patterns.FIXTURE_PROFILES
DEFAULT_FIXTURE_PROFILE = generate_patterns.DEFAULT_FIXTURE_PROFILE
YUV420P10_LAYOUTS = {"yuv420p10le", "yuv420p10", "p010"}
DELIVERY_BY_PROFILE = {
    generate_patterns.RGB_FFV1_PROFILE: "paused",
    generate_patterns.HEVC_MAIN10_YUV420P10_PROFILE: "continuous-identical",
}
SHADER_WARMUP_PURPOSE = (
    "shader setup only; not pixel evidence and not startup validation"
)
WARMUP_OUTPUT_LIMIT = 4096


class PipelineError(RuntimeError):
    """The run did not produce complete, comparable Gate 2 evidence."""


class PipelineRunError(PipelineError):
    """A measured run failed after accumulating non-evidence warm-up records."""

    def __init__(self, message: str, shader_warmup: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.shader_warmup = shader_warmup


def _nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PipelineError(f"missing required evidence: {label}")
    return value


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PipelineError(f"missing required object: {label}")
    return value


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise PipelineError(f"missing or non-finite numeric evidence: {label}")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as error:
        raise PipelineError(f"missing or non-finite numeric evidence: {label}") from error
    if not math.isfinite(numeric):
        raise PipelineError(f"missing or non-finite numeric evidence: {label}")
    return numeric


def _canonical_matches(actual: str, expected: str) -> bool:
    normalized = actual.lower().replace(".", "").replace("-", "").replace("_", "")
    wanted = expected.lower().replace(".", "").replace("-", "").replace("_", "")
    alias_groups = (
        {"rgb", "gbr"},
        {"gbrp10", "gbrp10le"},
        {"bt2020nc", "bt2020ncl"},
        {"pq", "smpte2084"},
        {"srgb", "iec6196621"},
        {"pc", "full", "fullrange"},
    )
    if any(normalized in aliases and wanted in aliases for aliases in alias_groups):
        return True
    return normalized == wanted


def pinned_commit(mpv_source: Path) -> str:
    """Read the checked-out source identity rather than trusting a directory name."""

    try:
        completed = subprocess.run(
            ["git", "-C", str(mpv_source), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise PipelineError(f"could not identify sibling mpv source at {mpv_source}") from error
    commit = completed.stdout.strip()
    if commit != EXPECTED_MPV_COMMIT:
        raise PipelineError(
            f"sibling mpv checkout is {commit or 'unknown'}, not pinned {EXPECTED_MPV_COMMIT}"
        )
    try:
        status = subprocess.run(
            ["git", "-C", str(mpv_source), "status", "--porcelain", "--untracked-files=no"],
            text=True,
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise PipelineError(f"could not verify sibling mpv checkout cleanliness at {mpv_source}") from error
    if status.stdout.strip():
        raise PipelineError("sibling mpv checkout has tracked changes; pinned headers are not proven")
    return commit


def validate_mpv_inputs(mpv_source: Path, mpv_prefix: Path) -> tuple[Path, Path, str]:
    headers = mpv_source / "include"
    libdir = mpv_prefix / "lib64"
    if not (headers / "mpv" / "render.h").is_file():
        raise PipelineError(f"pinned mpv render headers not found under: {headers}")
    if not (headers / "mpv" / "render_gl.h").is_file():
        raise PipelineError(f"pinned mpv OpenGL headers not found under: {headers}")
    if not any(libdir.glob("libmpv.so*")):
        raise PipelineError(f"pinned libmpv not found under: {libdir}")
    return headers, libdir, pinned_commit(mpv_source)


def compile_command(binary: Path, mpv_source: Path, mpv_prefix: Path, cc: str) -> list[str]:
    """Return the reproducible probe command using only the sibling pinned ABI."""

    headers, libdir, _ = validate_mpv_inputs(mpv_source, mpv_prefix)
    return [
        cc,
        "-std=c11",
        "-O2",
        "-Wall",
        "-Wextra",
        f"-I{headers}",
        str(PROJECT_ROOT / "tests" / "pixel_validator_egl.c"),
        f"-L{libdir}",
        f"-Wl,-rpath,{libdir}",
        "-lmpv",
        "-lEGL",
        "-lGL",
        "-ldl",
        "-lm",
        "-o",
        str(binary),
    ]


def compile_probe(binary: Path, mpv_source: Path, mpv_prefix: Path, cc: str) -> None:
    command = compile_command(binary, mpv_source, mpv_prefix, cc)
    try:
        subprocess.run(command, text=True, capture_output=True, check=True)
    except FileNotFoundError as error:
        raise PipelineError(f"C compiler is unavailable: {cc}") from error
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip() or error.stdout.strip() or str(error)
        raise PipelineError(f"Gate 2 probe compilation failed:\n{detail}") from error


def parse_probe_json(stdout: str) -> dict[str, Any]:
    """Accept exactly one successful probe object; logs belong on stderr."""

    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise PipelineError(f"probe did not emit one valid JSON object: {error}") from error
    if not isinstance(parsed, dict):
        raise PipelineError("probe JSON root must be an object")
    if parsed.get("schema") != PROBE_SCHEMA:
        raise PipelineError("probe JSON has an unexpected or missing schema")
    return parsed


def _require_no_errors(result: dict[str, Any]) -> None:
    errors = _mapping(result.get("errors"), "errors")
    for key in ("mpv", "fbo", "gl"):
        if key not in errors or errors[key] is not None:
            raise PipelineError(f"probe reported {key} error evidence: {errors.get(key)!r}")


def _expected_input(
    spec: generate_patterns.FixtureSpec, fixture_profile: str
) -> dict[str, str]:
    if fixture_profile == generate_patterns.HEVC_MAIN10_YUV420P10_PROFILE:
        return {
            "primaries": spec.color_primaries,
            "transfer": spec.color_transfer,
            "matrix": generate_patterns.yuv_matrix(spec),
            "levels": "pc",
        }
    return {
        "primaries": spec.color_primaries,
        "transfer": spec.color_transfer,
        "matrix": spec.color_space,
        "levels": spec.color_range,
        "pixel_format": spec.pixel_format,
    }


def validate_decode_mode(mode: str) -> str:
    if mode not in DECODE_MODES:
        raise PipelineError(f"unsupported decode mode: {mode!r}")
    return mode


def validate_fixture_profile(profile: str) -> str:
    try:
        return generate_patterns.validate_fixture_profile(profile)
    except generate_patterns.FixtureError as error:
        raise PipelineError(str(error)) from error


def delivery_for_profile(profile: str) -> str:
    """Return the accepted frame-delivery policy for one fixture profile."""

    profile = validate_fixture_profile(profile)
    return DELIVERY_BY_PROFILE[profile]


def _validate_fixture_version_provenance(fixture: dict[str, Any]) -> None:
    for executable in ("ffmpeg", "ffprobe"):
        version = _nonempty_string(
            fixture.get(f"{executable}_version"), f"fixture.{executable}_version"
        )
        parts = version.split()
        if len(parts) < 3 or parts[:2] != [executable, "version"] or parts[2] != EXPECTED_FFMPEG_VERSION:
            raise PipelineError(f"unexpected fixture {executable} version: {version}")


def _validate_common_fixture_provenance(fixture: dict[str, Any]) -> generate_patterns.FixtureSpec:
    mode = _nonempty_string(fixture.get("mode"), "fixture.mode")
    spec = generate_patterns.fixture_spec(mode)
    digest = _nonempty_string(fixture.get("sha256"), "fixture.sha256")
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest.lower()):
        raise PipelineError("fixture SHA-256 provenance is malformed")
    dimensions = _mapping(fixture.get("dimensions"), "fixture.dimensions")
    if dimensions.get("width") != generate_patterns.WIDTH or dimensions.get("height") != generate_patterns.HEIGHT:
        raise PipelineError("fixture dimensions are incomplete or unexpected")
    if fixture.get("fps") != generate_patterns.FPS or fixture.get("frame_count") != generate_patterns.FRAME_COUNT:
        raise PipelineError("fixture frame-rate/count provenance is incomplete or unexpected")
    if fixture.get("patches") != generate_patterns.serialized_patches(spec):
        raise PipelineError("fixture patch layout provenance is incomplete")
    _validate_fixture_version_provenance(fixture)
    return spec


def _validate_yuv_fixture_provenance(
    fixture: dict[str, Any], spec: generate_patterns.FixtureSpec
) -> None:
    stream = _mapping(fixture.get("stream"), "fixture.stream")
    try:
        generate_patterns.validate_yuv_stream_metadata(spec, stream)
    except generate_patterns.FixtureError as error:
        raise PipelineError(f"fixture HEVC stream metadata is invalid: {error}") from error
    x265_identity = _mapping(fixture.get("x265_identity"), "fixture.x265_identity")
    for field in ("encoder", "build"):
        _nonempty_string(x265_identity.get(field), f"fixture.x265_identity.{field}")
    raw_artifacts = _mapping(fixture.get("raw_artifacts"), "fixture.raw_artifacts")
    expected_sizes = {
        "source_rgb": generate_patterns.WIDTH * generate_patterns.HEIGHT * 3 * 2 * generate_patterns.FRAME_COUNT,
        "encoder_yuv": generate_patterns.yuv_frame_size() * generate_patterns.FRAME_COUNT,
        "decoded_yuv": generate_patterns.yuv_frame_size() * generate_patterns.FRAME_COUNT,
        "reconstructed_rgb": generate_patterns.WIDTH * generate_patterns.HEIGHT * 3 * 2 * generate_patterns.FRAME_COUNT,
    }
    for name, expected_size in expected_sizes.items():
        artifact = _mapping(raw_artifacts.get(name), f"fixture.raw_artifacts.{name}")
        _nonempty_string(artifact.get("filename"), f"fixture.raw_artifacts.{name}.filename")
        digest = _nonempty_string(artifact.get("sha256"), f"fixture.raw_artifacts.{name}.sha256")
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest.lower()):
            raise PipelineError(f"fixture raw SHA-256 provenance is malformed: {name}")
        if artifact.get("size") != expected_size:
            raise PipelineError(f"fixture raw size provenance is unexpected: {name}")
    if raw_artifacts["encoder_yuv"]["sha256"] != raw_artifacts["decoded_yuv"]["sha256"]:
        raise PipelineError("fixture decoded YUV SHA does not match encoder YUV SHA")
    frame_content = _mapping(
        fixture.get("decoded_yuv_frame_content"), "fixture.decoded_yuv_frame_content"
    )
    first_frame_sha256 = _nonempty_string(
        frame_content.get("first_frame_sha256"),
        "fixture.decoded_yuv_frame_content.first_frame_sha256",
    )
    if frame_content.get("policy") != "all-decoded-frames-byte-identical" or \
       frame_content.get("frame_count") != generate_patterns.FRAME_COUNT or \
       frame_content.get("frame_size") != generate_patterns.yuv_frame_size() or \
       len(first_frame_sha256) != 64 or \
       any(character not in "0123456789abcdef" for character in first_frame_sha256.lower()):
        raise PipelineError("fixture decoded HEVC frame-content provenance is incomplete")
    conversion = _mapping(fixture.get("conversion"), "fixture.conversion")
    expected_conversion = {
        "source_pixel_format": "gbrp10le",
        "encoder_pixel_format": generate_patterns.HEVC_PIXEL_FORMAT,
        "range": "full",
        "matrix": generate_patterns.yuv_matrix(spec),
        "filter": generate_patterns.yuv_conversion_filter(spec),
        "inverse_filter": generate_patterns.yuv_inverse_filter(spec),
    }
    if conversion != expected_conversion:
        raise PipelineError("fixture RGB-to-YUV conversion provenance differs from the accepted recipe")
    encoding = _mapping(fixture.get("encoding"), "fixture.encoding")
    if encoding.get("codec") != "libx265" or encoding.get("profile") != "main10" or \
       encoding.get("preset") != generate_patterns.HEVC_X265_PRESET or \
       encoding.get("x265_params") != generate_patterns.HEVC_X265_PARAMS or \
       encoding.get("threads") != 1 or \
       encoding.get("chroma_location") != generate_patterns.HEVC_CHROMA_LOCATION or \
       encoding.get("container") != generate_patterns.HEVC_CONTAINER or \
       encoding.get("muxer") != generate_patterns.HEVC_MUXER or \
       not isinstance(encoding.get("command"), list) or \
       tuple(encoding["command"][-(len(generate_patterns.HEVC_MP4_OUTPUT_OPTIONS) + 1):-1]) != \
       generate_patterns.HEVC_MP4_OUTPUT_OPTIONS:
        raise PipelineError("fixture HEVC encoding provenance differs from the accepted recipe")
    error_model = _mapping(fixture.get("rgb_yuv_reconstruction_error"), "fixture.rgb_yuv_reconstruction_error")
    component_count = generate_patterns.WIDTH * generate_patterns.HEIGHT * 3
    nonzero_count = error_model.get("nonzero_component_count")
    maximum_error = error_model.get("maximum_absolute_10bit_code_error")
    mean_error = error_model.get("mean_absolute_10bit_code_error")
    if error_model.get("source_pixel_format") != "gbrp10le" or \
       error_model.get("reconstructed_pixel_format") != "gbrp10le" or \
       error_model.get("component_count") != component_count or \
       not isinstance(nonzero_count, int) or not 0 <= nonzero_count <= component_count or \
       not isinstance(maximum_error, int) or not 0 <= maximum_error <= 1023 or \
       not isinstance(mean_error, (int, float)) or not math.isfinite(float(mean_error)) or \
       float(mean_error) < 0 or \
       error_model.get("policy") != "diagnostic-only; no color tolerance is inferred from this conversion":
        raise PipelineError("fixture RGB-to-YUV reconstruction error provenance is incomplete")
    patch_samples = error_model.get("patch_samples")
    if not isinstance(patch_samples, list) or len(patch_samples) != generate_patterns.PATCH_COLUMNS * generate_patterns.PATCH_ROWS:
        raise PipelineError("fixture RGB-to-YUV patch error provenance is incomplete")
    if {sample.get("name") for sample in patch_samples if isinstance(sample, dict)} != {
        patch.name for patch in spec.patches
    }:
        raise PipelineError("fixture RGB-to-YUV patch names differ from the fixture layout")
    for sample in patch_samples:
        sample_data = _mapping(sample, "fixture.rgb_yuv_reconstruction_error.patch")
        for field in ("source_rgb_10bit", "reconstructed_rgb_10bit", "delta_rgb_10bit"):
            values = sample_data.get(field)
            if not isinstance(values, list) or len(values) != 3 or not all(isinstance(value, int) for value in values):
                raise PipelineError(f"fixture RGB-to-YUV patch evidence is incomplete: {field}")
        source = sample_data["source_rgb_10bit"]
        reconstructed = sample_data["reconstructed_rgb_10bit"]
        delta = sample_data["delta_rgb_10bit"]
        if any(value < 0 or value > 1023 for value in source + reconstructed) or \
           delta != [reconstructed[index] - source[index] for index in range(3)]:
            raise PipelineError("fixture RGB-to-YUV patch evidence is inconsistent")


def validate_fixture_provenance(
    fixture: dict[str, Any], *, expected_profile: str = DEFAULT_FIXTURE_PROFILE
) -> None:
    """Require profile-specific fixture provenance before starting a probe."""

    expected_profile = validate_fixture_profile(expected_profile)
    if fixture.get("fixture_profile") is None and fixture.get("schema") != generate_patterns.FIXTURE_SCHEMA:
        raise PipelineError("fixture provenance has an unexpected or missing schema")
    try:
        profile = generate_patterns.fixture_profile_from_metadata(fixture)
    except generate_patterns.FixtureError as error:
        raise PipelineError(str(error)) from error
    if profile != expected_profile:
        raise PipelineError(f"fixture profile is {profile!r}, expected {expected_profile!r}")
    if profile == generate_patterns.RGB_FFV1_PROFILE:
        if fixture.get("schema") != generate_patterns.FIXTURE_SCHEMA:
            raise PipelineError("fixture provenance has an unexpected or missing schema")
        if fixture.get("generator_revision") != generate_patterns.GENERATOR_REVISION:
            raise PipelineError("fixture provenance has an unexpected generator revision")
    else:
        if fixture.get("schema") != generate_patterns.HEVC_FIXTURE_SCHEMA:
            raise PipelineError("fixture HEVC provenance has an unexpected or missing schema")
        if fixture.get("generator_revision") != generate_patterns.HEVC_GENERATOR_REVISION:
            raise PipelineError("fixture HEVC provenance has an unexpected generator revision")
        if fixture.get("fixture_profile") != generate_patterns.HEVC_MAIN10_YUV420P10_PROFILE:
            raise PipelineError("fixture HEVC provenance has an unexpected profile")
    spec = _validate_common_fixture_provenance(fixture)
    stream = _mapping(fixture.get("stream"), "fixture.stream")
    if profile == generate_patterns.RGB_FFV1_PROFILE:
        try:
            generate_patterns.validate_stream_metadata(spec, stream)
        except generate_patterns.FixtureError as error:
            raise PipelineError(f"fixture stream metadata is invalid: {error}") from error
    else:
        _validate_yuv_fixture_provenance(fixture, spec)


def _validate_loaded_stack(loaded: dict[str, Any], mpv_prefix: Path | None) -> None:
    libmpv_path = Path(_nonempty_string(loaded.get("libmpv_path"), "loaded.libmpv_path")).resolve()
    Path(_nonempty_string(loaded.get("libplacebo_path"), "loaded.libplacebo_path")).resolve()
    mpv_version = _nonempty_string(loaded.get("mpv_version"), "loaded.mpv_version")
    if mpv_version != EXPECTED_MPV_VERSION:
        raise PipelineError(f"unexpected loaded mpv version: {mpv_version}")
    libplacebo_version = _nonempty_string(
        loaded.get("libplacebo_version"), "loaded.libplacebo_version"
    ).removeprefix("v")
    if libplacebo_version != EXPECTED_LIBPLACEBO_VERSION:
        raise PipelineError(f"unexpected loaded libplacebo version: {libplacebo_version}")
    ffmpeg_version = _nonempty_string(loaded.get("ffmpeg_version"), "loaded.ffmpeg_version")
    if ffmpeg_version.removeprefix("n") != EXPECTED_FFMPEG_VERSION:
        raise PipelineError(f"unexpected loaded FFmpeg version: {ffmpeg_version}")
    if mpv_prefix is not None:
        expected_root = mpv_prefix.resolve()
        if not libmpv_path.is_relative_to(expected_root):
            raise PipelineError(f"loaded libmpv is outside pinned prefix: {libmpv_path}")


def validate_probe_result(
    result: dict[str, Any],
    fixture: dict[str, Any],
    api: str,
    *,
    mpv_prefix: Path | None = None,
    expected_decode: str = "no",
) -> None:
    """Fail on absent/inconsistent evidence, never on an unchosen color tolerance."""

    if result.get("schema") != PROBE_SCHEMA:
        raise PipelineError("missing probe schema")
    if result.get("requested_api") != api or result.get("active_api") != api:
        raise PipelineError(f"requested/active render API does not prove {api}")
    mode = fixture.get("mode")
    if result.get("fixture_mode") != mode:
        raise PipelineError("probe fixture mode differs from recorded fixture")
    _number(result.get("frame_timestamp"), "frame_timestamp")
    _validate_loaded_stack(_mapping(result.get("loaded"), "loaded"), mpv_prefix)
    try:
        fixture_profile = generate_patterns.fixture_profile_from_metadata(fixture)
    except generate_patterns.FixtureError as error:
        raise PipelineError(str(error)) from error
    input_metadata = _mapping(result.get("input"), "input")
    spec = generate_patterns.fixture_spec(str(mode))
    for field, expected in _expected_input(spec, fixture_profile).items():
        actual = _nonempty_string(input_metadata.get(field), f"input.{field}")
        if not _canonical_matches(actual, expected):
            raise PipelineError(f"input {field} is {actual!r}, not fixture metadata {expected!r}")
    actual_pixel_format = _nonempty_string(input_metadata.get("pixel_format"), "input.pixel_format")
    if fixture_profile == generate_patterns.HEVC_MAIN10_YUV420P10_PROFILE:
        if actual_pixel_format.lower() not in YUV420P10_LAYOUTS:
            raise PipelineError(
                "input pixel format is not a semantic 10-bit 4:2:0 YUV layout: "
                f"{actual_pixel_format!r}"
            )
    elif not _canonical_matches(actual_pixel_format, spec.pixel_format):
        raise PipelineError(
            f"input pixel_format is {actual_pixel_format!r}, not fixture metadata {spec.pixel_format!r}"
        )
    output = _mapping(result.get("output"), "output")
    if not _canonical_matches(_nonempty_string(output.get("target_primaries"), "output.target_primaries"), spec.target_primaries):
        raise PipelineError("output target primaries do not match the declared fixture run")
    if not _canonical_matches(_nonempty_string(output.get("target_transfer"), "output.target_transfer"), spec.target_transfer):
        raise PipelineError("output target transfer does not match the declared fixture run")
    if _number(output.get("target_peak"), "output.target_peak") != spec.target_peak_nits:
        raise PipelineError("output target peak does not match the declared fixture run")
    fbo = _mapping(result.get("fbo"), "fbo")
    required_format, required_depth = ("GL_RGBA16F", 16) if mode == "hdr" else ("GL_RGBA8", 8)
    if fbo.get("width") != generate_patterns.WIDTH or fbo.get("height") != generate_patterns.HEIGHT:
        raise PipelineError("probe FBO dimensions differ from the fixture dimensions")
    if fbo.get("internal_format") != required_format or fbo.get("depth") != required_depth or fbo.get("complete") is not True:
        raise PipelineError("probe did not use the declared complete FBO contract")
    gl = _mapping(result.get("gl"), "gl")
    for field in ("vendor", "renderer", "version"):
        _nonempty_string(gl.get(field), f"gl.{field}")
    decode = _mapping(result.get("decode"), "decode")
    expected_decode = validate_decode_mode(expected_decode)
    requested_decode = decode.get("requested")
    active_decode = decode.get("active")
    if requested_decode != expected_decode or active_decode != expected_decode:
        raise PipelineError(
            "decode evidence mismatch: "
            f"decode.requested={requested_decode!r}, "
            f"decode.active={active_decode!r}, expected={expected_decode!r}; "
            "no fallback accepted"
        )
    capture = _mapping(result.get("capture"), "capture")
    expected_delivery = delivery_for_profile(fixture_profile)
    if capture.get("delivery_mode") != expected_delivery:
        raise PipelineError(
            "capture delivery mode does not match fixture profile: "
            f"{capture.get('delivery_mode')!r}, expected {expected_delivery!r}"
        )
    capture_time_pos = _number(capture.get("time_pos"), "capture.time_pos")
    fixture_duration = generate_patterns.FRAME_COUNT / generate_patterns.FPS
    if not 0.0 <= capture_time_pos <= fixture_duration:
        raise PipelineError("capture time-pos lies outside the fixture duration")
    expected_pause = "yes" if expected_delivery == "paused" else "no"
    if capture.get("pause") != expected_pause:
        raise PipelineError(
            "capture pause state does not match delivery mode: "
            f"{capture.get('pause')!r}, expected {expected_pause!r}"
        )
    expected_loop_file = "no" if expected_delivery == "paused" else "inf"
    if capture.get("loop_file") != expected_loop_file:
        raise PipelineError(
            "capture loop-file state does not match delivery mode: "
            f"{capture.get('loop_file')!r}, expected {expected_loop_file!r}"
        )
    update_callback_observed = capture.get("update_callback_observed")
    if update_callback_observed is not True:
        raise PipelineError("capture.update_callback_observed must be true boolean evidence")
    readback = _mapping(result.get("readback"), "readback")
    if readback.get("nonempty") is not True or readback.get("gl_error") != 0:
        raise PipelineError("FBO readback is empty or recorded a GL error")
    if readback.get("flip_y") is not False or readback.get("gl_readback_origin") != "bottom-left" or \
       readback.get("source_grid_mapping") != "source-row-0-to-gl-row-0":
        raise PipelineError("probe did not record the explicit flipped FBO row mapping")
    patches = readback.get("patches")
    if not isinstance(patches, list) or len(patches) != generate_patterns.PATCH_COLUMNS * generate_patterns.PATCH_ROWS:
        raise PipelineError("readback does not contain the complete patch grid")
    names = set()
    for patch in patches:
        patch_data = _mapping(patch, "readback.patch")
        names.add(_nonempty_string(patch_data.get("name"), "readback.patch.name"))
        for channel in ("r", "g", "b"):
            _number(patch_data.get(channel), f"readback.patch.{channel}")
    expected_names = {patch.name for patch in spec.patches}
    if names != expected_names:
        raise PipelineError("readback patch names differ from the fixture layout")
    _require_no_errors(result)
    if result.get("rendered_frames") != 1:
        raise PipelineError("probe did not record exactly one requested frame")


def diagnostic_invariants(result: dict[str, Any]) -> dict[str, Any]:
    """Report catastrophic-signature evidence without inventing a color tolerance.

    A release threshold needs separate recorded transform/error justification,
    per ADR-0004.  These values remain measurable diagnostics in this first
    slice; missing evidence has already failed validation above.
    """

    patches = {patch["name"]: patch for patch in result["readback"]["patches"]}
    triples = {name: tuple(float(patch[channel]) for channel in ("r", "g", "b")) for name, patch in patches.items()}
    luminance = {name: sum(rgb) / 3.0 for name, rgb in triples.items()}
    neutral_names = ("black", "near_black", "reference_white", "peak_white")
    neutral_axis_delta = max(max(rgb) - min(rgb) for name, rgb in triples.items() if name in neutral_names)
    gradient = triples["neutral_gradient"]
    return {
        "finite": all(math.isfinite(component) for rgb in triples.values() for component in rgb),
        "unit_interval": all(0.0 <= component <= 1.0 for rgb in triples.values() for component in rgb),
        "black_luminance": luminance["black"],
        "near_black_luminance": luminance["near_black"],
        "black_distinction_observed": luminance["near_black"] > luminance["black"],
        "neutral_luminance": {name: luminance[name] for name in neutral_names},
        "neutral_monotonic_observed": all(
            luminance[left] <= luminance[right]
            for left, right in zip(neutral_names, neutral_names[1:])
        ),
        "neutral_axis_max_channel_delta": neutral_axis_delta,
        "primary_channel_values": {
            "red_primary": triples["red_primary"],
            "green_primary": triples["green_primary"],
            "blue_primary": triples["blue_primary"],
        },
        "primary_dominance_observed": (
            triples["red_primary"][0] > max(triples["red_primary"][1:]) and
            triples["green_primary"][1] > max(triples["green_primary"][0], triples["green_primary"][2]) and
            triples["blue_primary"][2] > max(triples["blue_primary"][:2])
        ),
        "neutral_gradient_center": gradient,
        "threshold_policy": "diagnostic-only; no release color tolerance is defined by ADR-0004",
    }


def ensure_comparable(
    left: dict[str, Any],
    right: dict[str, Any],
    fixture: dict[str, Any],
    *,
    expected_decode: str = "no",
) -> None:
    """Enforce every ADR-controlled A/B input except the separate API type."""

    left_readback = _mapping(left.get("readback"), "opengl.readback")
    right_readback = _mapping(right.get("readback"), "opengl_next.readback")
    left_capture = _mapping(left.get("capture"), "opengl.capture")
    right_capture = _mapping(right.get("capture"), "opengl_next.capture")
    orientation_keys = ("flip_y", "gl_readback_origin", "source_grid_mapping")
    left_orientation = {key: left_readback.get(key) for key in orientation_keys}
    right_orientation = {key: right_readback.get(key) for key in orientation_keys}
    expected_decode = validate_decode_mode(expected_decode)
    try:
        fixture_profile = generate_patterns.fixture_profile_from_metadata(fixture)
    except generate_patterns.FixtureError as error:
        raise PipelineError(str(error)) from error
    expected_delivery = delivery_for_profile(fixture_profile)
    compared = (
        (left.get("fixture_mode"), right.get("fixture_mode"), fixture.get("mode"), "fixture mode"),
        (left.get("frame_timestamp"), right.get("frame_timestamp"), left.get("frame_timestamp"), "timestamp"),
        (left.get("loaded"), right.get("loaded"), left.get("loaded"), "loaded stack"),
        (left.get("gl"), right.get("gl"), left.get("gl"), "GL implementation"),
        (left.get("fbo"), right.get("fbo"), left.get("fbo"), "FBO contract"),
        (left.get("input"), right.get("input"), left.get("input"), "input metadata"),
        (left.get("output"), right.get("output"), left.get("output"), "output metadata"),
        (
            left.get("decode"), right.get("decode"),
            {"requested": expected_decode, "active": expected_decode}, "decode mode",
        ),
        (
            left_capture.get("delivery_mode"), right_capture.get("delivery_mode"),
            expected_delivery, "delivery mode",
        ),
        (left_orientation, right_orientation, left_orientation, "readback orientation"),
    )
    for left_value, right_value, expected, name in compared:
        if left_value != right_value or left_value != expected:
            raise PipelineError(f"renderer pair is not comparable: {name}")
    if (
        left.get("requested_api") != "opengl" or left.get("active_api") != "opengl" or
        right.get("requested_api") != "opengl-next" or right.get("active_api") != "opengl-next"
    ):
        raise PipelineError("renderer pair must be separately captured as opengl then opengl-next")


def comparison_report(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_patches = {patch["name"]: patch for patch in left["readback"]["patches"]}
    right_patches = {patch["name"]: patch for patch in right["readback"]["patches"]}
    deltas: dict[str, list[float]] = {}
    all_deltas = []
    for name in sorted(left_patches):
        channel_deltas = [
            abs(float(left_patches[name][channel]) - float(right_patches[name][channel]))
            for channel in ("r", "g", "b")
        ]
        deltas[name] = channel_deltas
        all_deltas.extend(channel_deltas)
    return {
        "reference_white_absolute_delta": deltas["reference_white"],
        "patch_absolute_deltas": deltas,
        "mean_absolute_channel_delta": sum(all_deltas) / len(all_deltas),
        "maximum_absolute_channel_delta": max(all_deltas),
        "pixel_identity_required": False,
    }


def _run_probe(
    binary: Path,
    fixture: dict[str, Any],
    api: str,
    timestamp: str,
    egl_platform: str,
    hwdec: str,
    delivery_mode: str,
) -> dict[str, Any]:
    environment = os.environ.copy()
    environment["EGL_PLATFORM"] = egl_platform
    command = [
        binary.as_posix(), str(fixture["path"]), str(fixture["mode"]), api,
        timestamp, hwdec, delivery_mode,
    ]
    try:
        completed = subprocess.run(command, text=True, capture_output=True, env=environment, check=True)
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip() or error.stdout.strip() or str(error)
        raise PipelineError(f"{api} probe failed: {detail}") from error
    return parse_probe_json(completed.stdout)


def _concise_process_output(output: str) -> str:
    """Keep diagnostic reports bounded even if libmpv emits verbose logs."""

    if len(output) <= WARMUP_OUTPUT_LIMIT:
        return output
    return output[:WARMUP_OUTPUT_LIMIT] + "\n[truncated]"


def _run_shader_warmup(
    binary: Path,
    fixture: dict[str, Any],
    api: str,
    timestamp: str,
    egl_platform: str,
    hwdec: str,
    delivery_mode: str,
    fixture_profile: str,
) -> dict[str, Any]:
    """Run one non-evidence cold-shader setup process before strict capture."""

    environment = os.environ.copy()
    environment["EGL_PLATFORM"] = egl_platform
    command = [
        binary.as_posix(), str(fixture["path"]), str(fixture["mode"]), api,
        timestamp, hwdec, delivery_mode,
    ]
    started = time.monotonic()
    try:
        completed = subprocess.run(command, text=True, capture_output=True, env=environment)
    except OSError as error:
        raise PipelineError(f"{api} shader warm-up could not launch: {error}") from error
    elapsed_seconds = time.monotonic() - started
    evidence: dict[str, Any] = {
        "purpose": SHADER_WARMUP_PURPOSE,
        "api": api,
        "mode": fixture["mode"],
        "fixture_profile": fixture_profile,
        "decode_mode": hwdec,
        "delivery_mode": delivery_mode,
        "exit_status": completed.returncode,
        "duration_seconds": elapsed_seconds,
        "probe_completed": False,
        "stdout": _concise_process_output(completed.stdout),
        "stderr": _concise_process_output(completed.stderr),
    }
    if completed.returncode == 0:
        try:
            parsed = parse_probe_json(completed.stdout)
        except PipelineError as error:
            evidence["json_error"] = str(error)
        else:
            # Warm-up pixels are intentionally excluded from the report. This
            # preserves only the parsed process identity and capture state.
            evidence["result"] = {
                "capture": parsed.get("capture"),
                "loaded": parsed.get("loaded"),
            }
            evidence["probe_completed"] = True
    return evidence


def run_pipeline(
    cache_dir: Path,
    mpv_source: Path,
    mpv_prefix: Path,
    cc: str,
    timestamp: str,
    egl_platform: str,
    hwdec: str = "no",
    fixture_profile: str = DEFAULT_FIXTURE_PROFILE,
) -> dict[str, Any]:
    try:
        timestamp_number = float(timestamp)
    except ValueError as error:
        raise PipelineError("timestamp must be a finite number of seconds") from error
    if not math.isfinite(timestamp_number) or timestamp_number < 0:
        raise PipelineError("timestamp must be a non-negative finite number of seconds")
    hwdec = validate_decode_mode(hwdec)
    fixture_profile = validate_fixture_profile(fixture_profile)
    delivery_mode = delivery_for_profile(fixture_profile)
    _, _, source_commit = validate_mpv_inputs(mpv_source, mpv_prefix)
    fixtures = [
        generate_patterns.ensure_fixture(mode, cache_dir, profile=fixture_profile)
        for mode in ("hdr", "sdr")
    ]
    for fixture in fixtures:
        validate_fixture_provenance(fixture, expected_profile=fixture_profile)
    reports = []
    with tempfile.TemporaryDirectory(prefix="cinehdr-gate2-probe-") as temporary:
        binary = Path(temporary) / "pixel_validator_egl"
        compile_probe(binary, mpv_source, mpv_prefix, cc)
        shader_warmup: list[dict[str, Any]] = []
        try:
            for api in ("opengl", "opengl-next"):
                shader_warmup.append(_run_shader_warmup(
                    binary, fixtures[0], api, timestamp, egl_platform, hwdec,
                    delivery_mode, fixture_profile,
                ))
            for fixture in fixtures:
                legacy = _run_probe(
                    binary, fixture, "opengl", timestamp, egl_platform, hwdec, delivery_mode
                )
                next_api = _run_probe(
                    binary, fixture, "opengl-next", timestamp, egl_platform, hwdec, delivery_mode
                )
                validate_probe_result(
                    legacy, fixture, "opengl", mpv_prefix=mpv_prefix, expected_decode=hwdec
                )
                validate_probe_result(
                    next_api, fixture, "opengl-next", mpv_prefix=mpv_prefix, expected_decode=hwdec
                )
                ensure_comparable(legacy, next_api, fixture, expected_decode=hwdec)
                reports.append({
                    "fixture": fixture,
                    "opengl": legacy,
                    "opengl_next": next_api,
                    "opengl_diagnostics": diagnostic_invariants(legacy),
                    "opengl_next_diagnostics": diagnostic_invariants(next_api),
                    "comparison": comparison_report(legacy, next_api),
                })
        except PipelineError as error:
            raise PipelineRunError(str(error), shader_warmup) from error
    return {
        "schema": REPORT_SCHEMA,
        "status": "complete",
        "mpv_source": str(mpv_source.resolve()),
        "mpv_source_commit": source_commit,
        "mpv_prefix": str(mpv_prefix.resolve()),
        "timestamp": timestamp_number,
        "egl_platform": egl_platform,
        "decode_mode": hwdec,
        "fixture_profile": fixture_profile,
        "delivery_mode": delivery_mode,
        "shader_warmup": shader_warmup,
        "strict_evidence_policy": "missing evidence, metadata, API, FBO, decode, delivery, or comparability is a failure",
        "color_threshold_policy": "diagnostic-only; no release color tolerance is defined by ADR-0004",
        "results": reports,
    }


def _write_report(report: dict[str, Any], output: Path | None) -> None:
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if output is None:
        print(encoded, end="")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(encoded, encoding="utf-8")
    print(output)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the explicit CineHDR Gate 2 offscreen FBO comparison")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--mpv-source", type=Path, default=DEFAULT_MPV_SOURCE)
    parser.add_argument("--mpv-prefix", type=Path, default=DEFAULT_MPV_PREFIX)
    parser.add_argument("--cc", default=os.environ.get("CC", "cc"))
    parser.add_argument("--timestamp", default="0")
    parser.add_argument("--egl-platform", default="surfaceless")
    parser.add_argument("--hwdec", choices=DECODE_MODES, default="no")
    parser.add_argument("--fixture-profile", choices=FIXTURE_PROFILES, default=DEFAULT_FIXTURE_PROFILE)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        report = run_pipeline(
            args.cache_dir, args.mpv_source, args.mpv_prefix, args.cc,
            args.timestamp, args.egl_platform, args.hwdec, args.fixture_profile,
        )
        _write_report(report, args.output)
        return 0
    except (PipelineError, generate_patterns.FixtureError) as error:
        report = {
            "schema": REPORT_SCHEMA,
            "status": "failed",
            "decode_mode": args.hwdec,
            "fixture_profile": args.fixture_profile,
            "delivery_mode": delivery_for_profile(args.fixture_profile),
            "error": str(error),
        }
        if isinstance(error, PipelineRunError):
            report["shader_warmup"] = error.shader_warmup
        _write_report(report, args.output)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
