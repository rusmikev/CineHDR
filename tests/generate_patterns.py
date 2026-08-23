#!/usr/bin/env python3
"""Create deterministic, disposable Gate 2 RGB/FFV1 and HEVC Main10 fixtures.

The generated files are deliberately local artifacts: they provide controlled
input to the embedded FBO probe, not media checked into the source tree.  The
numeric patch layout and ffmpeg stream metadata are written alongside each
fixture so a report can prove what it rendered.
"""

from __future__ import annotations

from dataclasses import dataclass
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Iterable


FIXTURE_SCHEMA = "cinehdr.gate2.fixture.v1"
GENERATOR_REVISION = "ffv1-bitexact-v1"
RGB_FFV1_PROFILE = "rgb-ffv1"
HEVC_MAIN10_YUV420P10_PROFILE = "hevc-main10-yuv420p10-lossless"
FIXTURE_PROFILES = (RGB_FFV1_PROFILE, HEVC_MAIN10_YUV420P10_PROFILE)
DEFAULT_FIXTURE_PROFILE = RGB_FFV1_PROFILE
HEVC_FIXTURE_SCHEMA = "cinehdr.gate2.yuv.fixture.v1"
HEVC_GENERATOR_REVISION = "hevc-main10-yuv420p10-mp4-v2"
HEVC_PIXEL_FORMAT = "yuv420p10le"
HEVC_CHROMA_LOCATION = "left"
HEVC_X265_PRESET = "medium"
HEVC_X265_PARAMS = "lossless=1:pools=none:frame-threads=1:wpp=0"
HEVC_CONTAINER = "iso-bmff-mp4"
HEVC_MUXER = "mp4"
HEVC_MP4_OUTPUT_OPTIONS = (
    "-tag:v", "hvc1",
    "-movflags", "+faststart",
    "-video_track_timescale", "24000",
    "-f", HEVC_MUXER,
)
EXPECTED_FFMPEG_VERSION = "8.1.2"
WIDTH = 320
HEIGHT = 180
FPS = 24
FRAME_COUNT = 24
PATCH_COLUMNS = 4
PATCH_ROWS = 2


class FixtureError(RuntimeError):
    """The fixture cannot be created or no longer proves its requested inputs."""


def validate_fixture_profile(profile: str) -> str:
    if profile not in FIXTURE_PROFILES:
        raise FixtureError(f"unsupported fixture profile: {profile!r}")
    return profile


def fixture_profile_from_metadata(metadata: dict[str, Any]) -> str:
    """Infer the accepted RGB profile for legacy v1 metadata without rewriting it."""

    profile = metadata.get("fixture_profile")
    if profile is None and metadata.get("schema") == FIXTURE_SCHEMA:
        return RGB_FFV1_PROFILE
    if not isinstance(profile, str):
        raise FixtureError("fixture provenance has no declared profile")
    return validate_fixture_profile(profile)


@dataclass(frozen=True)
class Patch:
    """One deterministic grid cell, with linear-light RGB components."""

    name: str
    row: int
    column: int
    rgb: tuple[float, float, float]
    gradient_end: tuple[float, float, float] | None = None


@dataclass(frozen=True)
class FixtureSpec:
    mode: str
    pixel_format: str
    color_range: str
    color_space: str
    color_primaries: str
    color_transfer: str
    target_primaries: str
    target_transfer: str
    target_peak_nits: float
    sample_bits: int
    patches: tuple[Patch, ...]


def linear_to_pq(luminance_nits: float) -> float:
    """Encode absolute luminance using SMPTE ST 2084 (0--10,000 cd/m²)."""

    normalized = min(max(luminance_nits / 10000.0, 0.0), 1.0)
    m1 = 2610.0 / 16384.0
    m2 = 2523.0 / 32.0
    c1 = 3424.0 / 4096.0
    c2 = 2413.0 / 128.0
    c3 = 2392.0 / 128.0
    power = normalized**m1
    return ((c1 + c2 * power) / (1.0 + c3 * power))**m2


def linear_to_hlg(value: float) -> float:
    """Encode relative scene light [0.0, 1.0] as ARIB STD-B67 (HLG)."""

    value = min(max(value, 0.0), 1.0)
    if value <= 1.0 / 12.0:
        return (3.0 * value) ** 0.5
    a = 0.17883277
    b = 0.28466892
    c = 0.55991073
    import math
    return a * math.log(12.0 * value - b) + c


def linear_to_srgb(value: float) -> float:
    """Encode a full-range, relative linear-light SDR component as sRGB."""

    value = min(max(value, 0.0), 1.0)
    if value <= 0.0031308:
        return value * 12.92
    return 1.055 * value ** (1.0 / 2.4) - 0.055


def _patches(values: Iterable[tuple[str, tuple[float, float, float], tuple[float, float, float] | None]]) -> tuple[Patch, ...]:
    patches = []
    for index, (name, rgb, gradient_end) in enumerate(values):
        patches.append(
            Patch(name, index // PATCH_COLUMNS, index % PATCH_COLUMNS, rgb, gradient_end)
        )
    if len(patches) != PATCH_COLUMNS * PATCH_ROWS:
        raise AssertionError("the Gate 2 fixture layout must be a complete 4x2 grid")
    return tuple(patches)


def fixture_spec(mode: str) -> FixtureSpec:
    """Return the complete, recorded layout for the requested fixture mode."""

    if mode == "hdr":
        return FixtureSpec(
            mode="hdr",
            pixel_format="gbrp10le",
            color_range="pc",
            # GBR is RGB matrix signalling; BT.2020 belongs in primaries.
            color_space="gbr",
            color_primaries="bt2020",
            color_transfer="smpte2084",
            target_primaries="bt.2020",
            target_transfer="pq",
            target_peak_nits=1000.0,
            sample_bits=10,
            patches=_patches((
                ("black", (0.0, 0.0, 0.0), None),
                ("near_black", (0.1, 0.1, 0.1), None),
                ("reference_white", (203.0, 203.0, 203.0), None),
                ("peak_white", (1000.0, 1000.0, 1000.0), None),
                ("red_primary", (1000.0, 0.0, 0.0), None),
                ("green_primary", (0.0, 1000.0, 0.0), None),
                ("blue_primary", (0.0, 0.0, 1000.0), None),
                ("neutral_gradient", (0.0, 0.0, 0.0), (1000.0, 1000.0, 1000.0)),
            )),
        )
    if mode == "hlg":
        return FixtureSpec(
            mode="hlg",
            pixel_format="gbrp10le",
            color_range="pc",
            color_space="gbr",
            color_primaries="bt2020",
            color_transfer="arib-std-b67",
            target_primaries="bt.2020",
            target_transfer="pq",
            target_peak_nits=1000.0,
            sample_bits=10,
            patches=_patches((
                ("black", (0.0, 0.0, 0.0), None),
                ("near_black", (0.05, 0.05, 0.05), None),
                ("reference_white", (0.75, 0.75, 0.75), None),
                ("peak_white", (1.0, 1.0, 1.0), None),
                ("red_primary", (1.0, 0.0, 0.0), None),
                ("green_primary", (0.0, 1.0, 0.0), None),
                ("blue_primary", (0.0, 0.0, 1.0), None),
                ("neutral_gradient", (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)),
            )),
        )
    if mode == "sdr":
        return FixtureSpec(
            mode="sdr",
            # FFV1 has no 8-bit planar GBR profile; 10-bit keeps the fixture
            # planar and lossless while the measured CineHDR target stays RGBA8.
            pixel_format="gbrp10le",
            color_range="pc",
            # GBR is RGB matrix signalling; BT.709 belongs in primaries.
            color_space="gbr",
            color_primaries="bt709",
            color_transfer="iec61966-2-1",
            target_primaries="bt.709",
            target_transfer="srgb",
            target_peak_nits=100.0,
            sample_bits=10,
            patches=_patches((
                ("black", (0.0, 0.0, 0.0), None),
                ("near_black", (1.0 / 255.0, 1.0 / 255.0, 1.0 / 255.0), None),
                ("reference_white", (1.0, 1.0, 1.0), None),
                ("peak_white", (1.0, 1.0, 1.0), None),
                ("red_primary", (1.0, 0.0, 0.0), None),
                ("green_primary", (0.0, 1.0, 0.0), None),
                ("blue_primary", (0.0, 0.0, 1.0), None),
                ("neutral_gradient", (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)),
            )),
        )
    raise FixtureError(f"unsupported fixture mode: {mode!r}; expected hdr, hlg, or sdr")


def patch_centers(*, readback_origin: str = "top-left") -> dict[str, tuple[int, int]]:
    """Return named grid centres with the declared image/readback origin.

    Fixture rows are written top-to-bottom. OpenGL readback has a bottom-left
    origin, so flipped render targets must request that mapping explicitly.
    """

    if readback_origin not in {"top-left", "bottom-left"}:
        raise FixtureError("readback origin must be top-left or bottom-left")

    centers: dict[str, tuple[int, int]] = {}
    patch_width = WIDTH // PATCH_COLUMNS
    patch_height = HEIGHT // PATCH_ROWS
    for patch in fixture_spec("hdr").patches:
        row = patch.row
        if readback_origin == "bottom-left":
            row = PATCH_ROWS - 1 - row
        centers[patch.name] = (
            patch.column * patch_width + patch_width // 2,
            row * patch_height + patch_height // 2,
        )
    return centers


def _encode_component(spec: FixtureSpec, value: float) -> int:
    if spec.mode == "hdr":
        encoded = linear_to_pq(value)
    elif spec.mode == "hlg":
        encoded = linear_to_hlg(value)
    else:
        encoded = linear_to_srgb(value)
    maximum = (1 << spec.sample_bits) - 1
    return min(max(int(round(encoded * maximum)), 0), maximum)


def _component_at(spec: FixtureSpec, patch: Patch, x_in_patch: int, patch_width: int) -> tuple[int, int, int]:
    rgb = patch.rgb
    if patch.gradient_end is not None:
        interpolation = x_in_patch / max(patch_width - 1, 1)
        rgb = tuple(
            start + (end - start) * interpolation
            for start, end in zip(patch.rgb, patch.gradient_end)
        )
    return tuple(_encode_component(spec, value) for value in rgb)


def raw_frame(spec: FixtureSpec) -> bytes:
    """Build one planar GBR frame, keeping every source value deterministic."""

    patch_width = WIDTH // PATCH_COLUMNS
    patch_height = HEIGHT // PATCH_ROWS
    plane_size = WIDTH * HEIGHT
    bytes_per_component = 2 if spec.sample_bits > 8 else 1
    planes = [bytearray(plane_size * bytes_per_component) for _ in range(3)]
    for patch in spec.patches:
        for y in range(patch.row * patch_height, (patch.row + 1) * patch_height):
            for x in range(patch.column * patch_width, (patch.column + 1) * patch_width):
                red, green, blue = _component_at(spec, patch, x % patch_width, patch_width)
                # FFmpeg gbrp stores planes G, B, then R.
                for plane, value in zip(planes, (green, blue, red)):
                    offset = (y * WIDTH + x) * bytes_per_component
                    if bytes_per_component == 2:
                        plane[offset:offset + 2] = value.to_bytes(2, "little")
                    else:
                        plane[offset] = value
    return b"".join(planes)


def repeated_raw_frames(spec: FixtureSpec) -> bytes:
    """Return the exact deterministic RGB source used by both fixture profiles."""

    return raw_frame(spec) * FRAME_COUNT


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def yuv_matrix(spec: FixtureSpec) -> str:
    return "bt2020nc" if spec.mode in ("hdr", "hlg") else "bt709"


def yuv_conversion_filter(spec: FixtureSpec) -> str:
    """Explicit GBR 10-bit full-range to 4:2:0 YUV conversion recipe."""

    return (
        "zscale="
        f"rangein=full:range=full:primariesin={spec.color_primaries}:"
        f"transferin={spec.color_transfer}:matrixin=gbr:"
        f"primaries={spec.color_primaries}:transfer={spec.color_transfer}:"
        f"matrix={yuv_matrix(spec)}:dither=none,format={HEVC_PIXEL_FORMAT}"
    )


def yuv_inverse_filter(spec: FixtureSpec) -> str:
    """Recorded inverse conversion used only to quantify fixture error."""

    return (
        "zscale="
        f"rangein=full:range=full:primariesin={spec.color_primaries}:"
        f"transferin={spec.color_transfer}:matrixin={yuv_matrix(spec)}:"
        f"primaries={spec.color_primaries}:transfer={spec.color_transfer}:"
        "matrix=gbr:dither=none,format=gbrp10le"
    )


def yuv_frame_size() -> int:
    return WIDTH * HEIGHT * 3


def decoded_yuv_frame_content(decoded_yuv: bytes) -> dict[str, Any]:
    """Prove that continuous capture cannot select a different HEVC frame."""

    frame_size = yuv_frame_size()
    expected_size = frame_size * FRAME_COUNT
    if len(decoded_yuv) != expected_size:
        raise FixtureError(
            f"decoded HEVC YUV has {len(decoded_yuv)} bytes, expected {expected_size}"
        )
    first_frame = decoded_yuv[:frame_size]
    for index in range(1, FRAME_COUNT):
        offset = index * frame_size
        if decoded_yuv[offset:offset + frame_size] != first_frame:
            raise FixtureError("decoded HEVC frames are not byte-identical")
    return {
        "policy": "all-decoded-frames-byte-identical",
        "frame_count": FRAME_COUNT,
        "frame_size": frame_size,
        "first_frame_sha256": sha256_bytes(first_frame),
    }


def hevc_fixture_stem(mode: str) -> str:
    return f"cinehdr-gate2-{HEVC_MAIN10_YUV420P10_PROFILE}-{mode}"


def hevc_media_path(cache_dir: Path, mode: str) -> Path:
    """Return the canonical direct ISO-BMFF fixture, never a remuxed stream."""

    return cache_dir / f"{hevc_fixture_stem(mode)}.mp4"


def hevc_metadata_path(cache_dir: Path, mode: str) -> Path:
    return cache_dir / f"{hevc_fixture_stem(mode)}.json"


def hevc_raw_paths(cache_dir: Path, mode: str) -> dict[str, Path]:
    stem = hevc_fixture_stem(mode)
    return {
        "source_rgb": cache_dir / f"{stem}-source-gbrp10le.raw",
        "encoder_yuv": cache_dir / f"{stem}-encoder-yuv420p10le.raw",
        "decoded_yuv": cache_dir / f"{stem}-decoded-yuv420p10le.raw",
        "reconstructed_rgb": cache_dir / f"{stem}-reconstructed-gbrp10le.raw",
    }


def _run_checked(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, text=True, capture_output=True, check=True)
    except FileNotFoundError as error:
        raise FixtureError(f"required executable is unavailable: {command[0]}") from error
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip() or error.stdout.strip() or str(error)
        raise FixtureError(f"command failed: {' '.join(command)}\n{detail}") from error


def tool_version(executable: str) -> str:
    output = _run_checked([executable, "-version"]).stdout.splitlines()
    if not output:
        raise FixtureError(f"{executable} -version returned no provenance")
    return output[0]


def ffprobe_stream(path: Path, ffprobe: str) -> dict[str, Any]:
    completed = _run_checked([
        ffprobe, "-v", "error", "-select_streams", "v:0", "-count_frames",
        "-show_entries", "stream=codec_name,width,height,pix_fmt,color_range,color_space,color_transfer,color_primaries,r_frame_rate,nb_read_frames",
        "-of", "json", str(path),
    ])
    try:
        streams = json.loads(completed.stdout)["streams"]
        stream = streams[0]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise FixtureError(f"ffprobe did not return one video stream for {path}") from error
    return stream


def ffprobe_yuv_stream(path: Path, ffprobe: str) -> dict[str, Any]:
    """Read every stream field required by the HEVC Main10 profile contract."""

    completed = _run_checked([
        ffprobe, "-v", "error", "-select_streams", "v:0", "-count_frames",
        "-show_entries", (
            "stream=codec_name,profile,width,height,pix_fmt,color_range,color_space,"
            "color_transfer,color_primaries,chroma_location,r_frame_rate,avg_frame_rate,"
            "time_base,duration,nb_read_frames:format=format_name"
        ),
        "-of", "json", str(path),
    ])
    try:
        payload = json.loads(completed.stdout)
        streams = payload["streams"]
        stream = streams[0]
        stream["format_name"] = payload["format"]["format_name"]
        return stream
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise FixtureError(f"ffprobe did not return one HEVC video stream for {path}") from error


def _expected_stream(spec: FixtureSpec) -> dict[str, Any]:
    return {
        "codec_name": "ffv1",
        "width": WIDTH,
        "height": HEIGHT,
        "pix_fmt": spec.pixel_format,
        "color_range": spec.color_range,
        "color_space": spec.color_space,
        "color_transfer": spec.color_transfer,
        "color_primaries": spec.color_primaries,
        "r_frame_rate": f"{FPS}/1",
        "nb_read_frames": str(FRAME_COUNT),
    }


def validate_stream_metadata(spec: FixtureSpec, stream: dict[str, Any]) -> None:
    """Reject fixtures that ffmpeg tagged differently from the declared input."""

    expected = _expected_stream(spec)
    missing = [field for field in expected if field not in stream or stream[field] in (None, "unknown")]
    if missing:
        raise FixtureError(f"ffprobe stream metadata is incomplete: {', '.join(missing)}")
    mismatches = {
        field: (expected_value, stream[field])
        for field, expected_value in expected.items()
        if stream[field] != expected_value
    }
    if mismatches:
        raise FixtureError(f"ffprobe stream metadata differs from requested fixture: {mismatches}")


def expected_yuv_stream(spec: FixtureSpec) -> dict[str, Any]:
    return {
        "codec_name": "hevc",
        "profile": "Main 10",
        "width": WIDTH,
        "height": HEIGHT,
        "pix_fmt": HEVC_PIXEL_FORMAT,
        "color_range": "pc",
        "color_space": yuv_matrix(spec),
        "color_transfer": spec.color_transfer,
        "color_primaries": spec.color_primaries,
        "chroma_location": HEVC_CHROMA_LOCATION,
        "r_frame_rate": f"{FPS}/1",
        "avg_frame_rate": f"{FPS}/1",
        "time_base": "1/24000",
        "duration": "1.000000",
        "nb_read_frames": str(FRAME_COUNT),
    }


def validate_yuv_stream_metadata(spec: FixtureSpec, stream: dict[str, Any]) -> None:
    expected = expected_yuv_stream(spec)
    missing = [field for field in expected if field not in stream or stream[field] in (None, "unknown")]
    if missing:
        raise FixtureError(f"ffprobe HEVC stream metadata is incomplete: {', '.join(missing)}")
    mismatches = {
        field: (expected_value, stream[field])
        for field, expected_value in expected.items()
        if stream[field] != expected_value
    }
    if mismatches:
        raise FixtureError(f"ffprobe HEVC stream metadata differs from requested fixture: {mismatches}")
    format_name = stream.get("format_name")
    if not isinstance(format_name, str) or not {"mov", "mp4"}.issubset(
        set(format_name.split(","))
    ):
        raise FixtureError(f"ffprobe HEVC format is not an MP4 family: {format_name!r}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fixture_metadata_path(cache_dir: Path, mode: str) -> Path:
    return cache_dir / f"cinehdr-gate2-{mode}.json"


def fixture_media_path(cache_dir: Path, mode: str) -> Path:
    return cache_dir / f"cinehdr-gate2-{mode}.mkv"


def serialized_patches(spec: FixtureSpec) -> list[dict[str, Any]]:
    """Return the JSON-canonical patch contract (lists, never tuples)."""

    return [
        {
            "name": patch.name,
            "row": patch.row,
            "column": patch.column,
            "rgb": list(patch.rgb),
            "gradient_end": list(patch.gradient_end) if patch.gradient_end else None,
        }
        for patch in spec.patches
    ]


def _metadata(spec: FixtureSpec, path: Path, ffmpeg: str, ffprobe: str) -> dict[str, Any]:
    stream = ffprobe_stream(path, ffprobe)
    validate_stream_metadata(spec, stream)
    return {
        "schema": FIXTURE_SCHEMA,
        "generator_revision": GENERATOR_REVISION,
        "mode": spec.mode,
        "dimensions": {"width": WIDTH, "height": HEIGHT},
        "fps": FPS,
        "frame_count": FRAME_COUNT,
        "ffmpeg_version": tool_version(ffmpeg),
        "ffprobe_version": tool_version(ffprobe),
        "sha256": sha256_file(path),
        "stream": stream,
        "patches": serialized_patches(spec),
    }


def _valid_cached_fixture(spec: FixtureSpec, media: Path, metadata_path: Path, ffprobe: str) -> dict[str, Any] | None:
    if not media.is_file() or not metadata_path.is_file():
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("schema") != FIXTURE_SCHEMA or \
           metadata.get("generator_revision") != GENERATOR_REVISION or \
           metadata.get("mode") != spec.mode:
            return None
        if metadata.get("dimensions") != {"width": WIDTH, "height": HEIGHT} or \
           metadata.get("fps") != FPS or metadata.get("frame_count") != FRAME_COUNT:
            return None
        if metadata.get("patches") != serialized_patches(spec):
            return None
        for field in ("ffmpeg_version", "ffprobe_version"):
            if not str(metadata.get(field, "")).startswith(
                f"{field.removesuffix('_version')} version {EXPECTED_FFMPEG_VERSION} "
            ):
                return None
        if metadata.get("sha256") != sha256_file(media):
            return None
        stream = ffprobe_stream(media, ffprobe)
        validate_stream_metadata(spec, stream)
        if metadata.get("stream") != stream:
            return None
        return metadata
    except (FixtureError, OSError, json.JSONDecodeError):
        return None


def _raw_artifact(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FixtureError(f"required raw fixture artifact is missing: {path}")
    return {"filename": path.name, "sha256": sha256_file(path), "size": path.stat().st_size}


def _require_raw_artifact(path: Path, recorded: Any, *, expected_size: int | None = None) -> None:
    if not isinstance(recorded, dict):
        raise FixtureError(f"raw provenance is missing for {path.name}")
    actual = _raw_artifact(path)
    if actual != recorded:
        raise FixtureError(f"raw provenance does not match {path.name}")
    if expected_size is not None and actual["size"] != expected_size:
        raise FixtureError(f"unexpected raw size for {path.name}: {actual['size']}")


def _write_raw(path: Path, data: bytes) -> None:
    path.write_bytes(data)


def _rawvideo_command(
    ffmpeg: str,
    input_path: Path,
    input_format: str,
    output_path: Path,
    output_format: str,
    video_filter: str,
) -> list[str]:
    return [
        ffmpeg, "-y", "-bitexact", "-v", "error", "-fflags", "+bitexact",
        "-f", "rawvideo", "-pixel_format", input_format,
        "-video_size", f"{WIDTH}x{HEIGHT}", "-framerate", str(FPS),
        "-i", str(input_path), "-vf", video_filter,
        "-map", "0:v:0", "-an", "-frames:v", str(FRAME_COUNT),
        "-pix_fmt", output_format, "-threads", "1", "-map_metadata", "-1",
        "-f", "rawvideo", str(output_path),
    ]


def _decode_hevc_to_yuv(ffmpeg: str, media: Path, output: Path) -> None:
    _run_checked([
        ffmpeg, "-y", "-bitexact", "-v", "error", "-fflags", "+bitexact", "-i", str(media),
        "-map", "0:v:0", "-an", "-frames:v", str(FRAME_COUNT),
        "-pix_fmt", HEVC_PIXEL_FORMAT, "-threads", "1", "-map_metadata", "-1",
        "-f", "rawvideo", str(output),
    ])


def _x265_identity(encode_stderr: str) -> dict[str, str]:
    identity = {}
    for line in encode_stderr.splitlines():
        normalized = line.strip()
        if "x265 [info]: HEVC encoder version" in normalized:
            identity["encoder"] = normalized
        elif "x265 [info]: build info" in normalized:
            identity["build"] = normalized
    if set(identity) != {"encoder", "build"}:
        raise FixtureError("libx265 encoder/build identity is missing from ffmpeg stderr")
    return identity


def _gbr_code_triple(frame: bytes, x: int, y: int) -> tuple[int, int, int]:
    plane_size = WIDTH * HEIGHT * 2
    offset = (y * WIDTH + x) * 2
    green = int.from_bytes(frame[offset:offset + 2], "little")
    blue = int.from_bytes(frame[plane_size + offset:plane_size + offset + 2], "little")
    red = int.from_bytes(frame[2 * plane_size + offset:2 * plane_size + offset + 2], "little")
    return red, green, blue


def rgb_yuv_error_model(source_rgb: bytes, reconstructed_rgb: bytes) -> dict[str, Any]:
    """Record first-frame 10-bit RGB reconstruction error without a pass threshold."""

    frame_size = WIDTH * HEIGHT * 3 * 2
    source = source_rgb[:frame_size]
    reconstructed = reconstructed_rgb[:frame_size]
    if len(source) != frame_size or len(reconstructed) != frame_size:
        raise FixtureError("RGB reconstruction evidence does not contain one complete frame")
    total = 0
    maximum = 0
    nonzero = 0
    component_count = WIDTH * HEIGHT * 3
    for offset in range(0, frame_size, 2):
        source_code = int.from_bytes(source[offset:offset + 2], "little")
        reconstructed_code = int.from_bytes(reconstructed[offset:offset + 2], "little")
        delta = abs(reconstructed_code - source_code)
        maximum = max(maximum, delta)
        total += delta
        nonzero += int(delta != 0)
    patch_samples = []
    for patch_name, (x, y) in patch_centers().items():
        original = _gbr_code_triple(source, x, y)
        rebuilt = _gbr_code_triple(reconstructed, x, y)
        patch_samples.append({
            "name": patch_name,
            "source_rgb_10bit": list(original),
            "reconstructed_rgb_10bit": list(rebuilt),
            "delta_rgb_10bit": [rebuilt[index] - original[index] for index in range(3)],
        })
    return {
        "source_pixel_format": "gbrp10le",
        "reconstructed_pixel_format": "gbrp10le",
        "component_count": component_count,
        "nonzero_component_count": nonzero,
        "maximum_absolute_10bit_code_error": maximum,
        "mean_absolute_10bit_code_error": total / component_count,
        "patch_samples": patch_samples,
        "policy": "diagnostic-only; no color tolerance is inferred from this conversion",
    }


def _hevc_setparams(spec: FixtureSpec) -> str:
    return (
        "setparams=range=full:"
        f"colorspace={yuv_matrix(spec)}:color_primaries={spec.color_primaries}:"
        f"color_trc={spec.color_transfer}:chroma_location={HEVC_CHROMA_LOCATION}"
    )


def _encode_hevc_main10(
    ffmpeg: str, spec: FixtureSpec, yuv_input: Path, media: Path
) -> tuple[list[str], dict[str, str]]:
    command = [
        ffmpeg, "-y", "-bitexact", "-v", "info", "-fflags", "+bitexact",
        "-f", "rawvideo", "-pixel_format", HEVC_PIXEL_FORMAT,
        "-video_size", f"{WIDTH}x{HEIGHT}", "-framerate", str(FPS),
        "-i", str(yuv_input), "-vf", _hevc_setparams(spec),
        "-map", "0:v:0", "-an", "-frames:v", str(FRAME_COUNT),
        "-c:v", "libx265", "-profile:v", "main10", "-preset", HEVC_X265_PRESET,
        "-x265-params", HEVC_X265_PARAMS, "-threads", "1", "-flags:v", "+bitexact",
        "-map_metadata", "-1", "-color_range", "pc", "-colorspace", yuv_matrix(spec),
        "-color_primaries", spec.color_primaries, "-color_trc", spec.color_transfer,
        "-chroma_sample_location", HEVC_CHROMA_LOCATION,
        *HEVC_MP4_OUTPUT_OPTIONS, str(media),
    ]
    completed = _run_checked(command)
    return command, _x265_identity(completed.stderr)


def _hevc_metadata(
    spec: FixtureSpec,
    media: Path,
    raw_paths: dict[str, Path],
    ffmpeg: str,
    ffprobe: str,
    conversion_filter: str,
    inverse_filter: str,
    encoder_command: list[str],
    x265_identity: dict[str, str],
) -> dict[str, Any]:
    stream = ffprobe_yuv_stream(media, ffprobe)
    validate_yuv_stream_metadata(spec, stream)
    source_rgb = raw_paths["source_rgb"].read_bytes()
    reconstructed_rgb = raw_paths["reconstructed_rgb"].read_bytes()
    encoder_yuv = _raw_artifact(raw_paths["encoder_yuv"])
    decoded_yuv = _raw_artifact(raw_paths["decoded_yuv"])
    if encoder_yuv["sha256"] != decoded_yuv["sha256"] or encoder_yuv["size"] != decoded_yuv["size"]:
        raise FixtureError("decoded HEVC YUV bytes differ from the encoder YUV input")
    return {
        "schema": HEVC_FIXTURE_SCHEMA,
        "generator_revision": HEVC_GENERATOR_REVISION,
        "fixture_profile": HEVC_MAIN10_YUV420P10_PROFILE,
        "mode": spec.mode,
        "dimensions": {"width": WIDTH, "height": HEIGHT},
        "fps": FPS,
        "frame_count": FRAME_COUNT,
        "ffmpeg_version": tool_version(ffmpeg),
        "ffprobe_version": tool_version(ffprobe),
        "x265_identity": x265_identity,
        "sha256": sha256_file(media),
        "stream": stream,
        "patches": serialized_patches(spec),
        "raw_artifacts": {
            "source_rgb": _raw_artifact(raw_paths["source_rgb"]),
            "encoder_yuv": encoder_yuv,
            "decoded_yuv": decoded_yuv,
            "reconstructed_rgb": _raw_artifact(raw_paths["reconstructed_rgb"]),
        },
        "decoded_yuv_frame_content": decoded_yuv_frame_content(
            raw_paths["decoded_yuv"].read_bytes()
        ),
        "conversion": {
            "source_pixel_format": "gbrp10le",
            "encoder_pixel_format": HEVC_PIXEL_FORMAT,
            "range": "full",
            "matrix": yuv_matrix(spec),
            "filter": conversion_filter,
            "inverse_filter": inverse_filter,
        },
        "encoding": {
            "codec": "libx265",
            "profile": "main10",
            "preset": HEVC_X265_PRESET,
            "x265_params": HEVC_X265_PARAMS,
            "threads": 1,
            "chroma_location": HEVC_CHROMA_LOCATION,
            "container": HEVC_CONTAINER,
            "muxer": HEVC_MUXER,
            "command": encoder_command,
        },
        "rgb_yuv_reconstruction_error": rgb_yuv_error_model(source_rgb, reconstructed_rgb),
    }


def _valid_cached_hevc_fixture(
    spec: FixtureSpec, cache_dir: Path, media: Path, metadata_path: Path,
    raw_paths: dict[str, Path], ffmpeg: str, ffprobe: str,
) -> dict[str, Any] | None:
    if not media.is_file() or not metadata_path.is_file():
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("schema") != HEVC_FIXTURE_SCHEMA or \
           metadata.get("generator_revision") != HEVC_GENERATOR_REVISION or \
           metadata.get("fixture_profile") != HEVC_MAIN10_YUV420P10_PROFILE or \
           metadata.get("mode") != spec.mode:
            return None
        if metadata.get("dimensions") != {"width": WIDTH, "height": HEIGHT} or \
           metadata.get("fps") != FPS or metadata.get("frame_count") != FRAME_COUNT or \
           metadata.get("patches") != serialized_patches(spec):
            return None
        if metadata.get("sha256") != sha256_file(media):
            return None
        for field in ("ffmpeg_version", "ffprobe_version"):
            if not str(metadata.get(field, "")).startswith(
                f"{field.removesuffix('_version')} version {EXPECTED_FFMPEG_VERSION} "
            ):
                return None
        x265_identity = metadata.get("x265_identity")
        if not isinstance(x265_identity, dict) or not all(
            isinstance(x265_identity.get(field), str) and x265_identity[field]
            for field in ("encoder", "build")
        ):
            return None
        conversion = metadata.get("conversion")
        if conversion != {
            "source_pixel_format": "gbrp10le",
            "encoder_pixel_format": HEVC_PIXEL_FORMAT,
            "range": "full",
            "matrix": yuv_matrix(spec),
            "filter": yuv_conversion_filter(spec),
            "inverse_filter": yuv_inverse_filter(spec),
        }:
            return None
        encoding = metadata.get("encoding")
        if not isinstance(encoding, dict) or encoding.get("codec") != "libx265" or \
           encoding.get("profile") != "main10" or encoding.get("preset") != HEVC_X265_PRESET or \
           encoding.get("x265_params") != HEVC_X265_PARAMS or encoding.get("threads") != 1 or \
           encoding.get("chroma_location") != HEVC_CHROMA_LOCATION or \
           encoding.get("container") != HEVC_CONTAINER or encoding.get("muxer") != HEVC_MUXER or \
           not isinstance(encoding.get("command"), list) or \
           "-bitexact" not in encoding["command"] or \
           tuple(encoding["command"][-(len(HEVC_MP4_OUTPUT_OPTIONS) + 1):-1]) != HEVC_MP4_OUTPUT_OPTIONS:
            return None
        expected_rgb = repeated_raw_frames(spec)
        _require_raw_artifact(raw_paths["source_rgb"], metadata.get("raw_artifacts", {}).get("source_rgb"), expected_size=len(expected_rgb))
        if raw_paths["source_rgb"].read_bytes() != expected_rgb:
            return None
        expected_yuv_size = yuv_frame_size() * FRAME_COUNT
        _require_raw_artifact(raw_paths["encoder_yuv"], metadata.get("raw_artifacts", {}).get("encoder_yuv"), expected_size=expected_yuv_size)
        _require_raw_artifact(raw_paths["decoded_yuv"], metadata.get("raw_artifacts", {}).get("decoded_yuv"), expected_size=expected_yuv_size)
        _require_raw_artifact(raw_paths["reconstructed_rgb"], metadata.get("raw_artifacts", {}).get("reconstructed_rgb"), expected_size=len(expected_rgb))
        encoder_yuv = _raw_artifact(raw_paths["encoder_yuv"])
        decoded_yuv = _raw_artifact(raw_paths["decoded_yuv"])
        if encoder_yuv["sha256"] != decoded_yuv["sha256"]:
            return None
        with tempfile.NamedTemporaryFile(
            prefix=f"{hevc_fixture_stem(spec.mode)}-cache-decode-", suffix=".raw", dir=cache_dir, delete=False
        ) as temporary:
            decoded_check = Path(temporary.name)
        try:
            _decode_hevc_to_yuv(ffmpeg, media, decoded_check)
            decoded_check_artifact = _raw_artifact(decoded_check)
            if decoded_check_artifact["sha256"] != decoded_yuv["sha256"] or \
               decoded_check_artifact["size"] != decoded_yuv["size"]:
                return None
        finally:
            decoded_check.unlink(missing_ok=True)
        stream = ffprobe_yuv_stream(media, ffprobe)
        validate_yuv_stream_metadata(spec, stream)
        if metadata.get("stream") != stream:
            return None
        error_model = metadata.get("rgb_yuv_reconstruction_error")
        if error_model != rgb_yuv_error_model(
            raw_paths["source_rgb"].read_bytes(),
            raw_paths["reconstructed_rgb"].read_bytes(),
        ):
            return None
        if metadata.get("decoded_yuv_frame_content") != decoded_yuv_frame_content(
            raw_paths["decoded_yuv"].read_bytes()
        ):
            return None
        return metadata
    except (FixtureError, OSError, json.JSONDecodeError, subprocess.SubprocessError):
        return None


def _ensure_hevc_fixture(
    mode: str, cache_dir: Path, *, ffmpeg: str, ffprobe: str
) -> dict[str, Any]:
    spec = fixture_spec(mode)
    media = hevc_media_path(cache_dir, mode)
    metadata_path = hevc_metadata_path(cache_dir, mode)
    raw_paths = hevc_raw_paths(cache_dir, mode)
    cached = _valid_cached_hevc_fixture(
        spec, cache_dir, media, metadata_path, raw_paths, ffmpeg, ffprobe
    )
    if cached is not None:
        return {**cached, "path": str(media), "metadata_path": str(metadata_path)}
    source_rgb = repeated_raw_frames(spec)
    _write_raw(raw_paths["source_rgb"], source_rgb)
    conversion_filter = yuv_conversion_filter(spec)
    _run_checked(_rawvideo_command(
        ffmpeg, raw_paths["source_rgb"], "gbrp10le", raw_paths["encoder_yuv"],
        HEVC_PIXEL_FORMAT, conversion_filter,
    ))
    expected_yuv_size = yuv_frame_size() * FRAME_COUNT
    if raw_paths["encoder_yuv"].stat().st_size != expected_yuv_size:
        raise FixtureError("RGB-to-YUV conversion produced an unexpected raw size")
    encoder_command, x265_identity = _encode_hevc_main10(
        ffmpeg, spec, raw_paths["encoder_yuv"], media
    )
    _decode_hevc_to_yuv(ffmpeg, media, raw_paths["decoded_yuv"])
    if _raw_artifact(raw_paths["encoder_yuv"])["sha256"] != _raw_artifact(raw_paths["decoded_yuv"])["sha256"]:
        raise FixtureError("HEVC decoded YUV is not byte-identical to encoder YUV input")
    inverse_filter = yuv_inverse_filter(spec)
    _run_checked(_rawvideo_command(
        ffmpeg, raw_paths["encoder_yuv"], HEVC_PIXEL_FORMAT, raw_paths["reconstructed_rgb"],
        "gbrp10le", inverse_filter,
    ))
    metadata = _hevc_metadata(
        spec, media, raw_paths, ffmpeg, ffprobe, conversion_filter, inverse_filter,
        encoder_command, x265_identity,
    )
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**metadata, "path": str(media), "metadata_path": str(metadata_path)}


def ensure_fixture(
    mode: str,
    cache_dir: Path,
    *,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
    profile: str = DEFAULT_FIXTURE_PROFILE,
) -> dict[str, Any]:
    """Create or verify one profile without changing the legacy RGB cache contract."""

    spec = fixture_spec(mode)
    profile = validate_fixture_profile(profile)
    if shutil.which(ffmpeg) is None or shutil.which(ffprobe) is None:
        raise FixtureError("ffmpeg and ffprobe are required to create Gate 2 fixtures")
    cache_dir = cache_dir.expanduser().resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    if profile == HEVC_MAIN10_YUV420P10_PROFILE:
        return _ensure_hevc_fixture(mode, cache_dir, ffmpeg=ffmpeg, ffprobe=ffprobe)
    media = fixture_media_path(cache_dir, mode)
    metadata_path = fixture_metadata_path(cache_dir, mode)
    cached = _valid_cached_fixture(spec, media, metadata_path, ffprobe)
    if cached is not None:
        return {**cached, "path": str(media), "metadata_path": str(metadata_path)}

    with tempfile.NamedTemporaryFile(
        mode="wb", prefix=f"cinehdr-gate2-{mode}-", suffix=".raw", dir=cache_dir, delete=False
    ) as raw_file:
        raw_path = Path(raw_file.name)
        frame = raw_frame(spec)
        for _ in range(FRAME_COUNT):
            raw_file.write(frame)
    try:
        command = [
            ffmpeg, "-y", "-v", "error",
            "-f", "rawvideo", "-pixel_format", spec.pixel_format,
            "-video_size", f"{WIDTH}x{HEIGHT}", "-framerate", str(FPS),
            "-i", str(raw_path), "-frames:v", str(FRAME_COUNT),
            # FFV1 receives colour fields from frame metadata; output options
            # alone do not preserve primaries/TRC in Matroska on FFmpeg 8.1.2.
            "-vf", (
                "setparams=range=full:colorspace=gbr:"
                f"color_primaries={spec.color_primaries}:color_trc={spec.color_transfer}"
            ),
            "-c:v", "ffv1", "-level", "3", "-pix_fmt", spec.pixel_format,
            "-flags:v", "+bitexact", "-threads", "1", "-map_metadata", "-1",
            "-fflags", "+bitexact",
            # FFmpeg names RGB matrix signalling "rgb"; ffprobe records it as gbr.
            "-color_range", spec.color_range, "-colorspace", "rgb",
            "-color_primaries", spec.color_primaries, "-color_trc", spec.color_transfer,
            str(media),
        ]
        _run_checked(command)
    finally:
        raw_path.unlink(missing_ok=True)
    metadata = _metadata(spec, media, ffmpeg, ffprobe)
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**metadata, "path": str(media), "metadata_path": str(metadata_path)}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create disposable CineHDR Gate 2 RGB/FFV1 or HEVC Main10 fixtures"
    )
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--mode", choices=("hdr", "hlg", "sdr", "all"), default="all")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--fixture-profile", choices=FIXTURE_PROFILES, default=DEFAULT_FIXTURE_PROFILE)
    args = parser.parse_args()
    modes = ("hdr", "hlg", "sdr") if args.mode == "all" else (args.mode,)
    try:
        generated = [
            ensure_fixture(
                mode, args.cache_dir, ffmpeg=args.ffmpeg, ffprobe=args.ffprobe,
                profile=args.fixture_profile,
            )
            for mode in modes
        ]
    except FixtureError as error:
        print(f"fixture generation failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps({
        "schema": FIXTURE_SCHEMA if args.fixture_profile == RGB_FFV1_PROFILE else HEVC_FIXTURE_SCHEMA,
        "fixture_profile": args.fixture_profile,
        "fixtures": generated,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
