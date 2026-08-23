# gpu_validation.py
#
# Copyright 2026 rusmikev / Diego Povliuk
# SPDX-License-Identifier: GPL-3.0-or-later

"""Opt-in real-hardware validation session for the experimental renderer.

Normal CineHDR runs never import or start this session. The development
launcher enables it with
``CINEHDR_GPU_VALIDATION=quick|soak|sdr-soak|lifecycle|transition|publication|settling``.
All actions
run on GTK's main thread and use existing window/mpv controls; the render
backend, saved color policy, and production context ownership are not changed
by the validator.
"""

from __future__ import annotations

from dataclasses import dataclass
from gettext import gettext as _
import glob
import gi
import json
import logging
import os
from pathlib import Path
import time
from typing import Any, Mapping

gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, GLib

from .gl_bindings import get_gl_error_count


logger = logging.getLogger(__name__)

VALIDATION_MODE_ENV = "CINEHDR_GPU_VALIDATION"
VALIDATION_REPORT_DIR_ENV = "CINEHDR_GPU_VALIDATION_REPORT_DIR"
PUBLICATION_EXPECTED_BACKEND_ENV = "CINEHDR_PUBLICATION_EXPECTED_BACKEND"
PUBLICATION_FIXTURE_PROFILE_ENV = "CINEHDR_PUBLICATION_FIXTURE_PROFILE"
PUBLICATION_FIXTURE_REVISION_ENV = "CINEHDR_PUBLICATION_FIXTURE_REVISION"
PUBLICATION_HDR_SHA256_ENV = "CINEHDR_PUBLICATION_HDR_SHA256"
PUBLICATION_SDR_SHA256_ENV = "CINEHDR_PUBLICATION_SDR_SHA256"

PUBLICATION_FIXTURE_PROFILE = "hevc-main10-yuv420p10-lossless"
PUBLICATION_FIXTURE_REVISION = "hevc-main10-yuv420p10-mp4-v2"
PUBLICATION_HDR_SHA256 = (
    "bc437162b00b6565ca486ecc0a67704b6f099ebccf03cf671b2a0f87e94e021d"
)
PUBLICATION_SDR_SHA256 = (
    "41393a0ee9dd887a1f11d4b21946fef3d1f49e72127719eefd1ac40aa9d5e360"
)
PUBLICATION_MPV_VERSION = "mpv v0.41.0-dev-g97179bce7"
PUBLICATION_LIBPLACEBO_VERSION = "7.360.1"
PUBLICATION_FFMPEG_VERSION = "8.1.2"
PUBLICATION_LIBMPV_PREFIX_SUFFIX = "/mpv-gpu-next-prefix/lib64/"
PUBLICATION_LIBMPV_BASENAMES = ("libmpv.so.2", "libmpv.so.2.5.0")

VALIDATION_MODES = (
    "quick", "soak", "sdr-soak", "lifecycle", "transition", "publication",
    "settling",
)
READY_STABLE_SECONDS = 4.0
SETTLING_SCHEMA = "cinehdr.gate2.resource-settling.v1"
SETTLING_REVISION = "bounded-resource-settling-v3"
SETTLING_COOLDOWN_SECONDS = 10


def _uses_publication_fixture_contract(mode: str) -> bool:
    """Whether a development mode requires the exact paused HEVC inputs."""
    return mode in ("publication", "settling")


@dataclass(frozen=True)
class GpuValidationConfig:
    mode: str
    report_dir: Path
    expected_backend: str | None = None
    fixture_profile: str | None = None
    fixture_revision: str | None = None
    fixture_hdr_sha256: str | None = None
    fixture_sdr_sha256: str | None = None


@dataclass(frozen=True)
class ValidationStep:
    name: str
    action: str
    wait_seconds: int
    value: object = None


def validation_expected_playlist_count(mode: str) -> int | None:
    if mode in ("transition", "publication", "settling"):
        return 2
    if mode == "sdr-soak":
        return 1
    return None


def validation_config_from_env(
    environ: Mapping[str, str] | None = None,
) -> GpuValidationConfig | None:
    source = os.environ if environ is None else environ
    raw_mode = source.get(VALIDATION_MODE_ENV, "").strip().lower()
    if not raw_mode:
        return None
    if raw_mode not in VALIDATION_MODES:
        supported = ", ".join(VALIDATION_MODES)
        raise ValueError(
            f"{VALIDATION_MODE_ENV} must be one of: {supported}; got {raw_mode!r}"
        )
    report_dir = Path(
        source.get(VALIDATION_REPORT_DIR_ENV, "validation-reports")
    ).expanduser()
    if not _uses_publication_fixture_contract(raw_mode):
        return GpuValidationConfig(raw_mode, report_dir)

    expected_backend = source.get(PUBLICATION_EXPECTED_BACKEND_ENV, "").strip()
    if expected_backend not in ("legacy", "gpu-next"):
        raise ValueError(
            f"{PUBLICATION_EXPECTED_BACKEND_ENV} must be legacy or gpu-next "
            f"for {raw_mode}; got {expected_backend!r}"
        )

    required = (
        (PUBLICATION_FIXTURE_PROFILE_ENV, PUBLICATION_FIXTURE_PROFILE),
        (PUBLICATION_FIXTURE_REVISION_ENV, PUBLICATION_FIXTURE_REVISION),
        (PUBLICATION_HDR_SHA256_ENV, PUBLICATION_HDR_SHA256),
        (PUBLICATION_SDR_SHA256_ENV, PUBLICATION_SDR_SHA256),
    )
    publication_values: dict[str, str] = {}
    for name, expected in required:
        value = source.get(name, "").strip()
        if value != expected:
            raise ValueError(
                f"{name} must be the accepted fixture value {expected!r}; "
                f"got {value!r}"
            )
        publication_values[name] = value
    return GpuValidationConfig(
        raw_mode,
        report_dir,
        expected_backend=expected_backend,
        fixture_profile=publication_values[PUBLICATION_FIXTURE_PROFILE_ENV],
        fixture_revision=publication_values[PUBLICATION_FIXTURE_REVISION_ENV],
        fixture_hdr_sha256=publication_values[PUBLICATION_HDR_SHA256_ENV],
        fixture_sdr_sha256=publication_values[PUBLICATION_SDR_SHA256_ENV],
    )


def validation_steps(mode: str) -> tuple[ValidationStep, ...]:
    """Return the prescribed, non-destructive interaction sequence."""
    if mode not in VALIDATION_MODES:
        raise ValueError(f"unsupported GPU validation mode: {mode}")
    if mode == "lifecycle":
        return (
            ValidationStep("normalize start position", "seek", 4, 0.05),
            ValidationStep("warm playback", "play", 8),
            ValidationStep("pause before context cycle", "pause", 3),
            ValidationStep("GL context cycle 1", "context-cycle", 10, 1),
            ValidationStep("play after context cycle 1", "play", 8),
            ValidationStep("seek after context cycle 1", "seek", 7, 0.25),
            ValidationStep("GL context cycle 2", "context-cycle", 10, 2),
            ValidationStep("play after context cycle 2", "play", 8),
            ValidationStep("seek after context cycle 2", "seek", 7, 0.75),
        )
    if mode == "transition":
        return (
            ValidationStep("initial SDR output", "signal-state", 3, (0, "sdr")),
            ValidationStep("play initial SDR", "play", 6),
            ValidationStep("switch SDR to HDR", "switch-media", 12, (1, "hdr")),
            ValidationStep("play HDR", "play", 6),
            ValidationStep("seek HDR", "seek", 7, 0.25),
            ValidationStep("switch HDR to SDR", "switch-media", 12, (0, "sdr")),
            ValidationStep("play returned SDR", "play", 6),
            ValidationStep("seek returned SDR", "seek", 7, 0.25),
            ValidationStep("switch SDR to HDR again", "switch-media", 12, (1, "hdr")),
            ValidationStep("play returned HDR", "play", 6),
        )
    if mode == "publication":
        # The HEVC fixtures contain identical decoded frames. Keep them paused
        # and prove the GTK/GDK publication, rather than playback duration.
        return (
            ValidationStep("actual initial SDR publication", "publication-state", 6, (0, "sdr")),
            ValidationStep("switch SDR to HDR publication", "switch-media", 6, (1, "hdr")),
            ValidationStep("switch HDR to SDR publication", "switch-media", 6, (0, "sdr")),
            ValidationStep("switch SDR to HDR publication again", "switch-media", 6, (1, "hdr")),
        )
    if mode == "settling":
        # The first HDR arrival warms allocations but is not an endpoint.
        # Every measured cycle finishes in the same paused small-window HDR
        # state, so resource samples are comparable without any byte budget.
        steps: list[ValidationStep] = [
            ValidationStep(
                "unmeasured allocation warm-up",
                "settling-warmup",
                6,
                (1, "hdr"),
            ),
        ]
        for cycle in range(1, 4):
            steps.extend(
                (
                    ValidationStep(
                        f"cycle {cycle} resize HDR to 1280×720",
                        "settling-resize",
                        6,
                        (1, "hdr", 1280, 720),
                    ),
                    ValidationStep(
                        f"cycle {cycle} resize HDR to 960×540",
                        "settling-resize",
                        6,
                        (1, "hdr", 960, 540),
                    ),
                    ValidationStep(
                        f"cycle {cycle} switch HDR to SDR",
                        "settling-switch",
                        6,
                        (0, "sdr"),
                    ),
                    ValidationStep(
                        f"cycle {cycle} switch SDR to HDR",
                        "settling-switch",
                        6,
                        (1, "hdr"),
                    ),
                    ValidationStep(
                        f"cycle {cycle} small-window HDR endpoint",
                        "settling-endpoint",
                        6,
                        (cycle, 1, "hdr", 960, 540),
                    ),
                )
            )
        steps.extend(
            (
                ValidationStep("stop and unload media", "settling-unload", 2),
                ValidationStep(
                    "ten-second unload cooldown",
                    "settling-cooldown",
                    SETTLING_COOLDOWN_SECONDS,
                ),
            )
        )
        return tuple(steps)
    steps = [
        ValidationStep("normalize start position", "seek", 4, 0.05),
        ValidationStep("warm playback", "play", 8),
        ValidationStep("pause", "pause", 3),
        ValidationStep("resume", "play", 8),
        ValidationStep("seek to 25%", "seek", 7, 0.25),
        ValidationStep("resize to 960×540", "resize", 5, (960, 540)),
        ValidationStep("resize to 1280×720", "resize", 5, (1280, 720)),
        ValidationStep("enter fullscreen", "fullscreen", 7, True),
        ValidationStep("leave fullscreen", "fullscreen", 5, False),
        ValidationStep("seek to 75%", "seek", 7, 0.75),
        ValidationStep("seek back to 10%", "seek", 7, 0.10),
    ]
    if mode == "sdr-soak":
        steps.insert(
            0, ValidationStep("initial SDR output", "signal-state", 3, (0, "sdr"))
        )
    if mode in ("soak", "sdr-soak"):
        soak_index = 4 if mode == "sdr-soak" else 3
        steps.insert(
            soak_index,
            ValidationStep("30-minute soak playback", "play", 1800),
        )
    return tuple(steps)


def prepare_validation_player(player, mode: str | None = None) -> tuple[str, ...]:
    """Isolate a validation process from saved position and playback state."""
    failures = []
    properties = (
        ("resume-playback", False),
        ("save-position-on-quit", False),
        # CineHDR normally asks mpv to add matching files from the same
        # directory. A validator must load only the paths explicitly supplied
        # by the launcher so playlist cardinality and media transitions remain
        # deterministic.
        ("autocreate-playlist", "no"),
        ("speed", 1.0),
        ("loop-file", "no"),
        ("loop-playlist", "no"),
    )
    if _uses_publication_fixture_contract(mode):
        # These must be set before main.py appends the one-second files. The
        # session reasserts pause/keep-open later, but cannot repair an EOF or
        # replace an auto-selected direct hwdec path after decoding started.
        # Both renderer processes use the same explicit copy path so the
        # publication/settling comparisons do not silently change decoder
        # ownership.
        properties += (
            ("pause", "yes"),
            ("keep-open", "yes"),
            ("hwdec", "vaapi-copy"),
        )
    for name, value in properties:
        try:
            player[name] = value
        except Exception as error:
            logger.warning(
                "Could not isolate validation property %s: %s", name, error
            )
            failures.append(name)
    return tuple(failures)


def position_sample_is_stable(
    previous: float | None, current: float, elapsed: float
) -> bool:
    """Reject startup/watch-later jumps while allowing normal 1× playback."""
    if previous is None or elapsed <= 0:
        return False
    delta = current - previous
    return -0.5 <= delta <= max(1.5, elapsed * 3.0)


def _mpv_property(player, name: str, default=None):
    for getter_name in ("_get_property", "get_property"):
        getter = getattr(player, getter_name, None)
        if not callable(getter):
            continue
        try:
            value = getter(name)
        except Exception:
            continue
        if value is not None:
            return value
    try:
        value = player[name]
    except Exception:
        return default
    return default if value is None else value


def _number(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _read_process_rss_kib() -> int | None:
    try:
        with open("/proc/self/status", encoding="utf-8") as status_file:
            for line in status_file:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


def _drm_value_kib(raw_value: str) -> int | None:
    parts = raw_value.split()
    if not parts:
        return None
    try:
        value = int(parts[0])
    except ValueError:
        return None
    unit = parts[1].lower() if len(parts) > 1 else "bytes"
    if unit == "mib":
        return value * 1024
    if unit == "kib":
        return value
    if unit == "bytes":
        return value // 1024
    return None


def aggregate_drm_memory_kib(fdinfo_contents) -> dict[str, int]:
    """Aggregate per-client DRM resident memory without double counting FDs."""
    clients: dict[tuple[str, str], dict[str, int]] = {}
    for index, content in enumerate(fdinfo_contents):
        fields = {}
        for line in content.splitlines():
            key, separator, value = line.partition(":")
            if separator and key.startswith("drm-"):
                fields[key] = value.strip()
        client_id = fields.get("drm-client-id")
        if client_id is None:
            continue
        client_key = (fields.get("drm-pdev", "unknown"), client_id)
        regions: dict[str, int] = {}
        resident_regions = {
            key.removeprefix("drm-resident-")
            for key in fields
            if key.startswith("drm-resident-")
        }
        for key, raw_value in fields.items():
            if key.startswith("drm-resident-"):
                region = key.removeprefix("drm-resident-")
            elif key.startswith("drm-memory-"):
                region = key.removeprefix("drm-memory-")
                if region in resident_regions:
                    continue
            else:
                continue
            value = _drm_value_kib(raw_value)
            if value is not None:
                regions[region] = value
        if not regions:
            continue
        existing = clients.setdefault(client_key, {})
        for region, value in regions.items():
            existing[region] = max(existing.get(region, 0), value)

    totals: dict[str, int] = {}
    for regions in clients.values():
        for region, value in regions.items():
            totals[region] = totals.get(region, 0) + value
    return totals


def _read_process_drm_memory_kib() -> dict[str, int]:
    contents = []
    for filename in sorted(glob.glob("/proc/self/fdinfo/*")):
        try:
            content = Path(filename).read_text(encoding="utf-8")
        except OSError:
            continue
        if "drm-client-id:" in content:
            contents.append(content)
    return aggregate_drm_memory_kib(contents)


def _drm_region_total(regions: dict[str, int], kind: str) -> int | None:
    values = [
        value
        for region, value in regions.items()
        if kind in region.lower()
    ]
    return sum(values) if values else None


def _read_global_vram_kib() -> dict[str, int]:
    usage = {}
    pattern = "/sys/class/drm/card*/device/mem_info_vram_used"
    for filename in sorted(glob.glob(pattern)):
        try:
            value = int(Path(filename).read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
        card = Path(filename).parents[1].name
        usage[card] = value // 1024
    return usage


def _format_kib(value: int | None) -> str:
    if value is None:
        return "unavailable"
    return f"{value / 1024:.1f} MiB"


def evaluate_validation(
    *,
    active_api: str | None,
    session_status: str,
    hwdec: str,
    step_statuses: tuple[str, ...],
    fbo_drops: int,
    decoder_drops: int,
    vo_drops: int,
    rss_growth_kib: int | None,
    process_vram_growth_kib: int | None,
    process_vram_measured: bool,
    gl_errors: int,
    fbo_failures: int,
) -> tuple[str, tuple[str, ...]]:
    """Turn measured facts into a conservative PASS/WARN/FAIL summary."""
    warnings: list[str] = []
    if active_api != "opengl-next":
        return "FAIL", ("GPU Next was not the active renderer",)
    if session_status != "active":
        return "FAIL", (f"renderer session ended as {session_status}",)
    if "FAIL" in step_statuses:
        return "FAIL", ("one or more validation actions failed",)
    if gl_errors:
        return "FAIL", (f"CineHDR observed {gl_errors} OpenGL errors",)
    if fbo_failures:
        return "FAIL", (f"CineHDR observed {fbo_failures} FBO allocation failures",)
    if "WARN" in step_statuses:
        warnings.append("one or more window/playback actions need manual review")
    if not hwdec or hwdec.lower() in ("no", "none", "software", "unknown"):
        warnings.append("hardware decoding was not active")
    if fbo_drops > 3:
        warnings.append(f"CineHDR dropped {fbo_drops} frames because its FBO pool was busy")
    if decoder_drops > 3:
        warnings.append(f"mpv decoder reported {decoder_drops} dropped frames")
    if vo_drops > 3:
        warnings.append(f"mpv video output reported {vo_drops} dropped frames")
    if rss_growth_kib is not None and rss_growth_kib > 100 * 1024:
        warnings.append("process memory grew by more than 100 MiB")
    if (
        process_vram_growth_kib is not None
        and process_vram_growth_kib > 256 * 1024
    ):
        warnings.append("CineHDR DRM-client VRAM grew by more than 256 MiB")
    if not process_vram_measured:
        warnings.append(
            "the driver did not expose CineHDR DRM-client VRAM to the validator"
        )
    return ("WARN" if warnings else "PASS"), tuple(warnings)


def publication_expected_api(expected_backend: str) -> str:
    """Map the startup-only publication backend choice to libmpv's API."""
    try:
        return {"legacy": "opengl", "gpu-next": "opengl-next"}[expected_backend]
    except KeyError as error:
        raise ValueError(f"unsupported publication backend: {expected_backend!r}") from error


def _gtype_name(value: object) -> str | None:
    gtype = getattr(value, "__gtype__", None)
    name = getattr(gtype, "name", None)
    return name if isinstance(name, str) and name else None


def _memory_format_symbol(value: object) -> str:
    """Return a symbolic Gdk.MemoryFormat name without using intended state."""
    for name in ("R8G8B8A8", "R16G16B16A16_FLOAT"):
        if value == getattr(Gdk.MemoryFormat, name, object()):
            return name
    for attribute in ("value_name", "name", "value_nick"):
        candidate = getattr(value, attribute, None)
        if isinstance(candidate, str) and candidate:
            return candidate.removeprefix("GDK_MEMORY_")
    return str(value)


def publication_texture_evidence(video_area) -> dict[str, object]:
    """Read the actual published GDK object required by the publication ADR.

    This deliberately does not derive the answer from CineHDR's target-format
    fields. Missing GTK/GDK accessors are evidence unavailable, never a skip.
    """
    evidence: dict[str, object] = {
        "available": False,
        "texture_is_gl_texture": False,
        "texture_gtype": None,
        "width": None,
        "height": None,
        "format": None,
        "color_state_srgb": None,
        "color_state_rec2100_pq": None,
        "expected_width": None,
        "expected_height": None,
        "dimensions_match": False,
        "display_gtype": None,
        "surface_gtype": None,
        "display_is_wayland": False,
        "surface_is_wayland": False,
        "error": None,
    }
    texture = getattr(video_area, "current_texture", None)
    if not isinstance(texture, Gdk.GLTexture):
        evidence["error"] = "current_texture is not an actual Gdk.GLTexture"
        return evidence
    evidence["texture_is_gl_texture"] = True
    evidence["texture_gtype"] = _gtype_name(texture)
    try:
        get_width = getattr(texture, "get_width", None)
        get_height = getattr(texture, "get_height", None)
        get_format = getattr(texture, "get_format", None)
        get_color_state = getattr(texture, "get_color_state", None)
        if not all(callable(getter) for getter in (get_width, get_height, get_format, get_color_state)):
            raise AttributeError("Gdk.GLTexture getter is unavailable")
        width = int(get_width())
        height = int(get_height())
        memory_format = get_format()
        color_state = get_color_state()
        color_equal = getattr(color_state, "equal", None)
        if not callable(color_equal):
            raise AttributeError("Gdk.ColorState.equal is unavailable")
        # Match MpvVideoWidget._render_pending_frame(): the builder's size is
        # based on the widget allocation, not the child Gtk.GLArea allocation.
        expected_width = int(video_area.get_width()) * int(
            video_area.props.scale_factor
        )
        expected_height = int(video_area.get_height()) * int(
            video_area.props.scale_factor
        )
        native = video_area.get_native()
        surface = native.get_surface()
        display = surface.get_display()
        display_gtype = _gtype_name(display)
        surface_gtype = _gtype_name(surface)
        evidence.update(
            {
                "width": width,
                "height": height,
                "format": _memory_format_symbol(memory_format),
                "color_state_srgb": bool(color_equal(Gdk.ColorState.get_srgb())),
                "color_state_rec2100_pq": bool(
                    color_equal(Gdk.ColorState.get_rec2100_pq())
                ),
                "expected_width": expected_width,
                "expected_height": expected_height,
                "dimensions_match": width == expected_width and height == expected_height,
                "display_gtype": display_gtype,
                "surface_gtype": surface_gtype,
                "display_is_wayland": bool(display_gtype and display_gtype.startswith("GdkWayland")),
                "surface_is_wayland": bool(surface_gtype and surface_gtype.startswith("GdkWayland")),
                "available": True,
            }
        )
    except Exception as error:
        evidence["error"] = f"actual GDK texture evidence unavailable: {error}"
    return evidence


def publication_runtime_errors(runtime: object) -> tuple[str, ...]:
    """Return strict pinned-runtime identity failures for fixed-fixture modes."""
    if not isinstance(runtime, Mapping):
        return ("render runtime evidence was unavailable",)
    errors: list[str] = []
    libmpv_path = runtime.get("libmpv_path")
    if not isinstance(libmpv_path, str) or not any(
        libmpv_path.endswith(PUBLICATION_LIBMPV_PREFIX_SUFFIX + basename)
        for basename in PUBLICATION_LIBMPV_BASENAMES
    ):
        errors.append("libmpv was not loaded from the pinned GPU Next prefix")
    if runtime.get("mpv_version") != PUBLICATION_MPV_VERSION:
        errors.append("mpv runtime identity was not the accepted g97179bce7 build")
    raw_placebo = runtime.get("libplacebo_version")
    canonical_placebo = (
        raw_placebo[1:] if isinstance(raw_placebo, str) and raw_placebo.startswith("v") else raw_placebo
    )
    if canonical_placebo != PUBLICATION_LIBPLACEBO_VERSION:
        errors.append("libplacebo runtime identity was not 7.360.1")
    if runtime.get("ffmpeg_version") != PUBLICATION_FFMPEG_VERSION:
        errors.append("FFmpeg runtime identity was not 8.1.2")
    return tuple(errors)


def publication_state_errors(
    metrics: Mapping[str, object],
    *,
    expected_index: int,
    expected_signal: str,
    expected_api: str,
    expected_context_generation: int | None,
) -> tuple[str, ...]:
    """Validate the actual publication evidence for one SDR or HDR state."""
    expected_hdr = expected_signal == "hdr"
    expected_target = (
        ("GL_RGBA16F", 16, "rec2100-pq", "R16G16B16A16_FLOAT", False, True)
        if expected_hdr
        else ("GL_RGBA8", 8, "srgb", "R8G8B8A8", True, False)
    )
    target_format, target_depth, target_state, texture_format, wants_srgb, wants_pq = expected_target
    errors: list[str] = []
    if metrics.get("active_api") != expected_api:
        errors.append(f"active API was {metrics.get('active_api')!r}, expected {expected_api!r}")
    if metrics.get("render_status") != "active":
        errors.append(f"renderer session was {metrics.get('render_status')!r}, not active")
    if metrics.get("playlist_pos") != expected_index:
        errors.append(f"playlist entry was {metrics.get('playlist_pos')!r}, expected {expected_index}")
    if bool(metrics.get("source_hdr")) != expected_hdr:
        errors.append("source HDR state did not match the requested fixture")
    if bool(metrics.get("hdr_output_active")) != expected_hdr:
        errors.append("HDR output state did not follow the source in auto mode")
    if (
        metrics.get("render_target_format"),
        metrics.get("render_target_depth"),
        metrics.get("render_color_state"),
    ) != (target_format, target_depth, target_state):
        errors.append("CineHDR render target did not match the required state")
    if str(metrics.get("hwdec", "")).lower() != "vaapi-copy":
        errors.append("active hardware decode was not vaapi-copy")
    if int(metrics.get("gl_errors", 0)) != 0:
        errors.append("CineHDR observed OpenGL errors")
    if int(metrics.get("fbo_failures", 0)) != 0:
        errors.append("CineHDR observed FBO allocation failures")
    if expected_context_generation is not None and metrics.get("render_context_generation") != expected_context_generation:
        errors.append("renderer context changed during the publication matrix")
    texture = metrics.get("publication_texture")
    if not isinstance(texture, Mapping) or not texture.get("available"):
        errors.append("actual GDK texture evidence was unavailable")
    else:
        if not texture.get("texture_is_gl_texture"):
            errors.append("published texture was not an actual Gdk.GLTexture")
        if texture.get("format") != texture_format:
            errors.append("actual GDK texture format did not match the render target")
        if texture.get("color_state_srgb") is not wants_srgb or texture.get("color_state_rec2100_pq") is not wants_pq:
            errors.append("actual GDK texture color state did not match the render target")
        if texture.get("dimensions_match") is not True:
            errors.append("actual GDK texture dimensions did not match the scaled widget")
        if texture.get("display_is_wayland") is not True or texture.get("surface_is_wayland") is not True:
            errors.append("actual GDK display/surface was not Wayland")
    errors.extend(publication_runtime_errors(metrics.get("render_runtime")))
    return tuple(errors)


def evaluate_publication_validation(
    *,
    active_api: str | None,
    session_status: str,
    expected_api: str,
    final: Mapping[str, object],
    step_statuses: tuple[str, ...],
    state_sequence: tuple[tuple[int, str, str], ...],
    failure_reason: str | None,
    isolation_failures: tuple[str, ...],
) -> tuple[str, tuple[str, ...]]:
    """Publication has no warning path: all required evidence is a PASS gate."""
    errors: list[str] = []
    if active_api != expected_api:
        errors.append(f"active API was {active_api!r}, expected {expected_api!r}")
    if session_status != "active":
        errors.append(f"renderer session ended as {session_status!r}")
    if "FAIL" in step_statuses:
        errors.append("one or more publication states failed")
    if state_sequence != (
        (0, "sdr", "PASS"),
        (1, "hdr", "PASS"),
        (0, "sdr", "PASS"),
        (1, "hdr", "PASS"),
    ):
        errors.append("publication state sequence was incomplete or out of order")
    if failure_reason:
        errors.append(failure_reason)
    if isolation_failures:
        errors.append("validation process isolation was incomplete")
    if int(final.get("gl_errors", 0)) != 0:
        errors.append("CineHDR observed OpenGL errors")
    if int(final.get("fbo_failures", 0)) != 0:
        errors.append("CineHDR observed FBO allocation failures")
    if str(final.get("hwdec", "")).lower() != "vaapi-copy":
        errors.append("active hardware decode was not vaapi-copy")
    errors.extend(publication_runtime_errors(final.get("render_runtime")))
    return ("FAIL", tuple(errors)) if errors else ("PASS", ())


def _required_nonnegative_kib(metrics: Mapping[str, object], key: str) -> str | None:
    """Validate an endpoint resource observation without choosing a budget."""
    value = metrics.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        label = "RSS" if key == "rss_kib" else "process DRM VRAM"
        return f"{label} evidence was unavailable at the HDR endpoint"
    return None


def resource_settling_trend(values: tuple[int, ...]) -> str:
    """Classify only the ADR's sustained-growth shape, never a byte budget."""
    if len(values) != 3:
        return "unavailable"
    if all(after > before for before, after in zip(values, values[1:])):
        return "strictly-increasing"
    return "not-strictly-increasing"


def settling_endpoint_errors(
    metrics: Mapping[str, object],
    *,
    expected_api: str,
    expected_context_generation: int | None,
    expected_width: int | None = None,
    expected_height: int | None = None,
) -> tuple[str, ...]:
    """Validate one comparable small-window HDR resource endpoint."""
    errors = list(
        publication_state_errors(
            metrics,
            expected_index=1,
            expected_signal="hdr",
            expected_api=expected_api,
            expected_context_generation=expected_context_generation,
        )
    )
    if (
        expected_width is not None
        and expected_height is not None
        and (metrics.get("width"), metrics.get("height"))
        != (expected_width, expected_height)
    ):
        errors.append(
            "logical window size did not reach the comparable "
            f"{expected_width}×{expected_height} HDR endpoint"
        )
    for key in ("rss_kib", "drm_vram_kib"):
        error = _required_nonnegative_kib(metrics, key)
        if error:
            errors.append(error)
    return tuple(errors)


def evaluate_resource_settling_validation(
    *,
    active_api: str | None,
    session_status: str,
    expected_api: str,
    endpoint_results: tuple[Mapping[str, object], ...],
    step_results: tuple[Mapping[str, object], ...],
    final: Mapping[str, object],
    expected_context_generation: int | None,
    failure_reason: str | None,
    isolation_failures: tuple[str, ...],
) -> tuple[str, tuple[str, ...], dict[str, str]]:
    """Apply the bounded resource-settling acceptance matrix.

    This intentionally emits WARN only for three same-state, strictly rising
    endpoint samples. It neither fits a byte threshold nor claims that a
    passing workload is leak-free.
    """
    errors: list[str] = []
    if active_api != expected_api:
        errors.append(f"active API was {active_api!r}, expected {expected_api!r}")
    if session_status != "active":
        errors.append(f"renderer session ended as {session_status!r}")
    if failure_reason:
        errors.append(failure_reason)
    if isolation_failures:
        errors.append("validation process isolation was incomplete")

    expected_actions = tuple(step.action for step in validation_steps("settling"))
    actual_actions = tuple(str(result.get("action")) for result in step_results)
    if actual_actions != expected_actions:
        errors.append("resource-settling action sequence was incomplete or out of order")
    if any(result.get("status") != "PASS" for result in step_results):
        errors.append("one or more resource-settling actions failed")
    if len(endpoint_results) != 3:
        errors.append("three comparable small-window HDR endpoints were required")

    rss_values: list[int] = []
    drm_vram_values: list[int] = []
    endpoint_sizes: list[tuple[int, int]] = []
    for expected_cycle, result in enumerate(endpoint_results, start=1):
        expected_state = result.get("expected_state")
        if expected_state != (expected_cycle, 1, "hdr", 960, 540):
            errors.append("resource endpoint sequence was incomplete or out of order")
        metrics = result.get("metrics")
        if not isinstance(metrics, Mapping):
            errors.append("resource endpoint metrics were unavailable")
            continue
        errors.extend(
            settling_endpoint_errors(
                metrics,
                expected_api=expected_api,
                expected_context_generation=expected_context_generation,
            )
        )
        rss = metrics.get("rss_kib")
        drm_vram = metrics.get("drm_vram_kib")
        if isinstance(rss, int) and not isinstance(rss, bool) and rss >= 0:
            rss_values.append(rss)
        if (
            isinstance(drm_vram, int)
            and not isinstance(drm_vram, bool)
            and drm_vram >= 0
        ):
            drm_vram_values.append(drm_vram)
        width, height = metrics.get("width"), metrics.get("height")
        if (
            isinstance(width, int)
            and not isinstance(width, bool)
            and width > 0
            and isinstance(height, int)
            and not isinstance(height, bool)
            and height > 0
        ):
            endpoint_sizes.append((width, height))
        else:
            errors.append("actual small-window HDR endpoint size was unavailable")

    if len(endpoint_sizes) == 3 and len(set(endpoint_sizes)) != 1:
        errors.append(
            "the three actual small-window HDR endpoint sizes were not comparable"
        )

    if int(final.get("gl_errors", 0)) != 0:
        errors.append("CineHDR observed OpenGL errors")
    if int(final.get("fbo_failures", 0)) != 0:
        errors.append("CineHDR observed FBO allocation failures")
    if final.get("idle_active") is not True or final.get("time_pos_available") is not False:
        errors.append("final cooldown did not prove that media was unloaded")
    if (
        expected_context_generation is not None
        and final.get("render_context_generation") != expected_context_generation
    ):
        errors.append("renderer context changed during the resource-settling matrix")

    trends = {
        "rss_kib": resource_settling_trend(tuple(rss_values)),
        "drm_vram_kib": resource_settling_trend(tuple(drm_vram_values)),
    }
    if errors:
        return "FAIL", tuple(errors), trends
    warnings = tuple(
        f"three comparable HDR endpoint samples were strictly increasing for {key}"
        for key, trend in trends.items()
        if trend == "strictly-increasing"
    )
    return ("WARN" if warnings else "PASS"), warnings, trends


class GpuValidationSession:
    """Drive one opt-in validation run and write a path-safe text report."""

    def __init__(self, window, config: GpuValidationConfig):
        self.window = window
        self.config = config
        self.player = window.mpv
        self.video_area = window._video_area
        self.steps = validation_steps(config.mode)
        self.step_index = -1
        self.step_results: list[dict[str, object]] = []
        self.memory_samples: list[int] = []
        self.vram_samples: list[dict[str, int]] = []
        self.drm_vram_samples: list[int] = []
        self.drm_gtt_samples: list[int] = []
        self.resource_samples: list[dict[str, object]] = []
        self.started_at = time.monotonic()
        self.ready_deadline = self.started_at + 60
        self.ready_timer_id: int | None = None
        self.sample_timer_id: int | None = None
        self.step_timer_id: int | None = None
        self.lifecycle_timer_id: int | None = None
        self.finish_timer_id: int | None = None
        self.close_signal_id: int | None = None
        self.finished = False
        self.report_path: Path | None = None
        self.baseline: dict[str, object] = {}
        self._pending_step: ValidationStep | None = None
        self._pending_before_position = 0.0
        self._pending_position_available = False
        self._pending_target_position: float | None = None
        self._pending_before_size = (0, 0)
        self._pending_context_generation = 0
        self._pending_context = None
        self._pending_hdr_state = False
        self._pending_render_target = (None, None)
        self._pending_render_frame_generation = 0
        self._transition_context_generation: int | None = None
        self._transition_original_hdr_mode: str | None = None
        self._publication_context_generation: int | None = None
        self._settling_context_generation: int | None = None
        self._expected_playlist_count = validation_expected_playlist_count(
            config.mode
        )
        self._ready_last_position: float | None = None
        self._ready_last_sample_at: float | None = None
        self._ready_stable_since: float | None = None
        self._ready_last_playlist_count: int | None = None

    def start(self):
        logger.info("Starting %s GPU validation session", self.config.mode)
        toast = {
            "publication": _("Wayland publication validation is starting"),
            "settling": _("Wayland resource-settling validation is starting"),
        }.get(self.config.mode, _("GPU Next validation is starting"))
        self.window.show_toast(toast, True)
        self.close_signal_id = self.window.connect(
            "close-request", self._on_close_request
        )
        if _uses_publication_fixture_contract(self.config.mode):
            try:
                controller = self.video_area.hdr_controller
                self._transition_original_hdr_mode = controller.hdr_mode
                # The generated HEVC files contain 24 byte-identical frames.
                # Keep the matrix paused and alive: frame publication, not
                # elapsed playback time, is the evidence for each switch.
                controller.hdr_mode = "auto"
                self.player.command("set", "pause", "yes")
                self.player.command("set", "keep-open", "yes")
            except Exception as error:
                logger.exception("Could not prepare fixed-fixture validation")
                self._finish(
                    f"could not prepare {self.config.mode} validation: {error}"
                )
                return
        elif self.config.mode in ("transition", "sdr-soak"):
            controller = self.video_area.hdr_controller
            self._transition_original_hdr_mode = controller.hdr_mode
            # An SDR file intentionally stays on the HDR surface in force-hdr.
            # Use auto only in this isolated process so the matrix can measure
            # the actual SDR/HDR surface transition. The GSettings preference
            # is not written by this property assignment.
            controller.hdr_mode = "auto"
        self.sample_timer_id = GLib.timeout_add_seconds(1, self._sample_memory)
        self.ready_timer_id = GLib.timeout_add(500, self._wait_until_ready)

    def _sample_memory(self):
        rss = _read_process_rss_kib()
        if rss is not None:
            self.memory_samples.append(rss)
        global_vram = _read_global_vram_kib()
        if global_vram:
            self.vram_samples.append(global_vram)
        drm_regions = _read_process_drm_memory_kib()
        drm_vram = _drm_region_total(drm_regions, "vram")
        drm_gtt = _drm_region_total(drm_regions, "gtt")
        if drm_vram is not None:
            self.drm_vram_samples.append(drm_vram)
        if drm_gtt is not None:
            self.drm_gtt_samples.append(drm_gtt)
        self.resource_samples.append(
            {
                "sample_at": time.monotonic(),
                "rss_kib": rss,
                "global_vram_kib": (
                    sum(global_vram.values()) if global_vram else None
                ),
                "drm_vram_kib": drm_vram,
                "drm_gtt_kib": drm_gtt,
                "decoder_drops": int(
                    _number(
                        _mpv_property(
                            self.player, "decoder-frame-drop-count"
                        )
                    )
                ),
                "vo_drops": int(
                    _number(_mpv_property(self.player, "frame-drop-count"))
                ),
            }
        )
        return GLib.SOURCE_CONTINUE if not self.finished else GLib.SOURCE_REMOVE

    def _renderer_facts(self) -> tuple[str | None, str]:
        return (
            getattr(self.video_area, "render_backend_active", None),
            getattr(self.video_area, "render_session_status", "not-initialized"),
        )

    def _expected_active_api(self) -> str:
        config = getattr(self, "config", None)
        if config is not None and _uses_publication_fixture_contract(config.mode):
            if config.expected_backend is None:
                raise RuntimeError(f"{config.mode} has no expected renderer backend")
            return publication_expected_api(config.expected_backend)
        return "opengl-next"

    def _wait_until_ready(self):
        active_api, session_status = self._renderer_facts()
        if session_status in (
            "initialization-failure",
            "runtime-failure",
            "lifecycle-failure",
            "startup-fallback",
        ):
            self.ready_timer_id = None
            if _uses_publication_fixture_contract(self.config.mode):
                self._finish(
                    "renderer did not enter the expected fixed-fixture API "
                    f"{self._expected_active_api()}"
                )
            else:
                self._finish("renderer did not enter an active GPU Next session")
            return GLib.SOURCE_REMOVE

        duration = _number(_mpv_property(self.player, "duration"))
        raw_position = _mpv_property(self.player, "time-pos")
        position = _number(raw_position)
        seeking = bool(_mpv_property(self.player, "seeking", False))
        playlist_count = int(
            _number(_mpv_property(self.player, "playlist-count"), -1)
        )
        playlist_ready = (
            self._expected_playlist_count is None
            or playlist_count == self._expected_playlist_count
        )
        if (
            not playlist_ready
            and playlist_count != self._ready_last_playlist_count
        ):
            logger.info(
                "Waiting for playlist cardinality: observed=%s expected=%s",
                playlist_count,
                self._expected_playlist_count,
            )
        self._ready_last_playlist_count = playlist_count
        now = time.monotonic()
        first_frame = bool(getattr(self.video_area, "_first_frame_logged", False))
        if _uses_publication_fixture_contract(self.config.mode):
            # One-second paused fixtures are valid publication inputs. Do not
            # apply the normal playback-duration/position-settling heuristic.
            renderer_ready = (
                active_api == self._expected_active_api()
                and session_status == "active"
                and first_frame
                and playlist_ready
                and bool(self._metrics_snapshot()["publication_texture"].get("available"))
            )
        else:
            renderer_ready = (
                active_api == "opengl-next"
                and session_status == "active"
                and first_frame
                and duration > 1
                and raw_position is not None
                and not seeking
                and playlist_ready
            )
        if renderer_ready:
            elapsed = (
                now - self._ready_last_sample_at
                if self._ready_last_sample_at is not None
                else 0.0
            )
            stable = position_sample_is_stable(
                self._ready_last_position, position, elapsed
            )
            if self._ready_stable_since is None or not stable:
                if self._ready_last_position is not None and not stable:
                    logger.info(
                        "Waiting for playback position to settle: %.2f -> %.2f",
                        self._ready_last_position,
                        position,
                    )
                self._ready_stable_since = now
            self._ready_last_position = position
            self._ready_last_sample_at = now

        fixed_fixture_ready = (
            _uses_publication_fixture_contract(self.config.mode) and renderer_ready
        )
        if fixed_fixture_ready or (
            renderer_ready
            and self._ready_stable_since is not None
            and now - self._ready_stable_since >= READY_STABLE_SECONDS
        ):
            self.ready_timer_id = None
            # Startup allocation is expected and is not leak evidence. Begin
            # memory/VRAM growth measurement only after the first frame.
            self.memory_samples.clear()
            self.vram_samples.clear()
            self.drm_vram_samples.clear()
            self.drm_gtt_samples.clear()
            self.resource_samples.clear()
            self._sample_memory()
            self.baseline = self._metrics_snapshot()
            if self.config.mode in ("transition", "sdr-soak"):
                self._transition_context_generation = int(
                    getattr(self.video_area, "render_context_generation", 0)
                )
            if self.config.mode == "publication":
                self._publication_context_generation = int(
                    getattr(self.video_area, "render_context_generation", 0)
                )
            if self.config.mode == "settling":
                self._settling_context_generation = int(
                    getattr(self.video_area, "render_context_generation", 0)
                )
            self.window.show_toast(
                (
                    _("Wayland publication validation is running — do not interact")
                    if self.config.mode == "publication"
                    else (
                        _("Wayland resource-settling validation is running — do not interact")
                        if self.config.mode == "settling"
                        else _("GPU validation is running — do not interact")
                    )
                ),
                True,
            )
            self._advance_step()
            return GLib.SOURCE_REMOVE

        if not renderer_ready:
            self._ready_stable_since = None
            self._ready_last_position = None
            self._ready_last_sample_at = None

        if time.monotonic() >= self.ready_deadline:
            self.ready_timer_id = None
            if not playlist_ready:
                self._finish(
                    "timed out waiting for exactly "
                    f"{self._expected_playlist_count} playlist entries"
                )
            else:
                self._finish(
                    "timed out waiting for the first fixed-fixture texture"
                    if _uses_publication_fixture_contract(self.config.mode)
                    else "timed out waiting for the first GPU Next video frame"
                )
            return GLib.SOURCE_REMOVE
        return GLib.SOURCE_CONTINUE

    def _metrics_snapshot(self) -> dict[str, object]:
        area = self.video_area
        controller = area.hdr_controller
        raw_time_pos = _mpv_property(self.player, "time-pos")
        drm_regions = _read_process_drm_memory_kib()
        return {
            "sample_at": time.monotonic(),
            "time_pos": _number(raw_time_pos),
            "time_pos_available": raw_time_pos is not None,
            "idle_active": bool(_mpv_property(self.player, "idle-active", False)),
            "duration": _number(_mpv_property(self.player, "duration")),
            "pause": bool(_mpv_property(self.player, "pause", False)),
            "speed": _number(_mpv_property(self.player, "speed"), 1.0),
            "hwdec": str(_mpv_property(self.player, "hwdec-current", "unknown")),
            "decoder_drops": int(_number(_mpv_property(self.player, "decoder-frame-drop-count"))),
            "vo_drops": int(_number(_mpv_property(self.player, "frame-drop-count"))),
            "mistimed_frames": int(_number(_mpv_property(self.player, "mistimed-frame-count"))),
            "delayed_frames": int(_number(_mpv_property(self.player, "vo-delayed-frame-count"))),
            "fbo_drops": int(getattr(area.fbo_pool, "dropped_frames", 0)),
            "fbo_failures": int(
                getattr(area.fbo_pool, "allocation_failures", 0)
            ),
            "gl_errors": get_gl_error_count(),
            "render_status": getattr(area, "render_session_status", "unknown"),
            "render_context_generation": int(
                getattr(area, "render_context_generation", 0)
            ),
            "render_frame_generation": int(
                getattr(area, "render_frame_generation", 0)
            ),
            "render_target_format": getattr(area, "render_target_format", None),
            "render_target_depth": getattr(area, "render_target_depth", None),
            "render_color_state": getattr(area, "render_color_state", None),
            "active_api": getattr(area, "render_backend_active", None),
            "render_runtime": dict(getattr(area, "render_runtime", {}) or {}),
            "publication_texture": (
                publication_texture_evidence(area)
                if _uses_publication_fixture_contract(self.config.mode)
                else None
            ),
            "source_hdr": bool(controller.is_hdr_content),
            "hdr_output_active": bool(controller.is_hdr_active),
            "dovi_profile": getattr(controller, "dovi_profile", None),
            "playlist_pos": int(
                _number(_mpv_property(self.player, "playlist-pos"), -1)
            ),
            "target_trc": _mpv_property(self.player, "target-trc", "unknown"),
            "target_prim": _mpv_property(
                self.player, "target-prim", "unknown"
            ),
            "target_peak": _mpv_property(
                self.player, "target-peak", "unknown"
            ),
            "width": int(self.window.get_width()),
            "height": int(self.window.get_height()),
            "fullscreen": bool(self.window.props.fullscreened),
            "rss_kib": _read_process_rss_kib(),
            "global_vram_kib": _read_global_vram_kib(),
            "drm_vram_kib": _drm_region_total(drm_regions, "vram"),
            "drm_gtt_kib": _drm_region_total(drm_regions, "gtt"),
        }

    def _apply_step(self, step: ValidationStep):
        raw_position = _mpv_property(self.player, "time-pos")
        self._pending_before_position = _number(raw_position)
        self._pending_position_available = raw_position is not None
        self._pending_target_position = None
        self._pending_before_size = (
            int(self.window.get_width()),
            int(self.window.get_height()),
        )
        if step.action == "play":
            self.player.command("set", "pause", "no")
        elif step.action == "pause":
            # Set pause synchronously, then establish the position baseline.
            # Reading it before mpv acknowledges pause can race a pending
            # watch-later seek during file startup.
            self.player.command("set", "pause", "yes")
            raw_position = _mpv_property(self.player, "time-pos")
            self._pending_before_position = _number(raw_position)
            self._pending_position_available = raw_position is not None
        elif step.action == "seek":
            self.player.command("set", "pause", "yes")
            duration = _number(_mpv_property(self.player, "duration"))
            target = duration * float(step.value)
            self._pending_target_position = target
            self.player.command_async("seek", target, "absolute+exact")
        elif step.action == "resize":
            width, height = step.value
            self.window.unfullscreen()
            self.window.unmaximize()
            # Wait for the compositor to acknowledge unmaximize before the
            # new default size is requested.
            GLib.timeout_add(500, self._request_window_size, width, height)
        elif step.action == "fullscreen":
            self.player.fullscreen = bool(step.value)
        elif step.action == "context-cycle":
            self.player.command("set", "pause", "yes")
            gl_area = self.video_area.gl_area
            if not gl_area.get_realized():
                raise RuntimeError("Gtk.GLArea was not realized before the cycle")
            self._pending_context_generation = int(
                getattr(self.video_area, "render_context_generation", 0)
            )
            self._pending_context = self.video_area.mpv_ctx
            hdr_controller = self.video_area.hdr_controller
            self._pending_hdr_state = bool(hdr_controller.is_hdr_content)
            self._pending_render_target = (
                self.video_area.render_target_format,
                self.video_area.render_target_depth,
            )
            gl_area.unrealize()
            self.lifecycle_timer_id = GLib.timeout_add(
                500, self._realize_validation_gl_area, gl_area
            )
        elif step.action in ("signal-state", "publication-state"):
            self._pending_render_frame_generation = int(
                getattr(self.video_area, "render_frame_generation", 0)
            )
        elif step.action == "switch-media":
            playlist_index, _expected_signal = step.value
            self._pending_render_frame_generation = int(
                getattr(self.video_area, "render_frame_generation", 0)
            )
            self.player.command("playlist-play-index", int(playlist_index))
        elif step.action in ("settling-warmup", "settling-switch"):
            playlist_index, _expected_signal = step.value
            self._pending_render_frame_generation = int(
                getattr(self.video_area, "render_frame_generation", 0)
            )
            self.player.command("playlist-play-index", int(playlist_index))
        elif step.action == "settling-resize":
            _playlist_index, _expected_signal, width, height = step.value
            self._pending_render_frame_generation = int(
                getattr(self.video_area, "render_frame_generation", 0)
            )
            self.window.unfullscreen()
            self.window.unmaximize()
            # Request the exact logical size after unmaximize is observed.
            # A compositor may negotiate another size; the evaluator records
            # that actual size and requires all measured endpoints to match.
            GLib.timeout_add(500, self._request_window_size, width, height)
        elif step.action == "settling-endpoint":
            # The preceding resize/switch must already have published a newer
            # frame. This wait records the same paused HDR state, not a new
            # playback event.
            pass
        elif step.action == "settling-unload":
            self.player.command("stop")
        elif step.action == "settling-cooldown":
            pass
        else:
            raise ValueError(f"unknown validation action: {step.action}")

    def _request_window_size(self, width: int, height: int):
        self.window.set_default_size(width, height)
        return GLib.SOURCE_REMOVE

    def _realize_validation_gl_area(self, gl_area):
        self.lifecycle_timer_id = None
        if self.finished:
            return GLib.SOURCE_REMOVE
        if not gl_area.get_realized():
            gl_area.realize()
        self.video_area.queue_draw()
        return GLib.SOURCE_REMOVE

    def _verify_settling_step(
        self, step: ValidationStep, after: dict[str, object], expected_api: str
    ) -> tuple[str, str]:
        """Verify the paused fixed-fixture matrix without normal-mode rules."""
        if step.action in ("settling-unload", "settling-cooldown"):
            if after.get("idle_active") is not True or after.get(
                "time_pos_available"
            ) is not False:
                return "FAIL", "mpv did not prove that the media was unloaded"
            if int(after.get("gl_errors", 0)) != 0:
                return "FAIL", "CineHDR observed OpenGL errors"
            if int(after.get("fbo_failures", 0)) != 0:
                return "FAIL", "CineHDR observed FBO allocation failures"
            if (
                self._settling_context_generation is not None
                and after.get("render_context_generation")
                != self._settling_context_generation
            ):
                return "FAIL", "renderer context changed after media unload"
            return "PASS", "media remained unloaded while renderer context stayed active"

        if step.action == "settling-endpoint":
            cycle, expected_index, expected_signal, width, height = step.value
        elif step.action == "settling-resize":
            expected_index, expected_signal, width, height = step.value
            cycle = None
        else:
            expected_index, expected_signal = step.value
            width = height = None
            cycle = None

        if step.action == "settling-endpoint":
            errors = list(
                settling_endpoint_errors(
                    after,
                    expected_api=expected_api,
                    expected_context_generation=self._settling_context_generation,
                )
            )
        else:
            errors = list(
                publication_state_errors(
                    after,
                    expected_index=int(expected_index),
                    expected_signal=str(expected_signal),
                    expected_api=expected_api,
                    expected_context_generation=self._settling_context_generation,
                )
            )
        if step.action in (
            "settling-warmup",
            "settling-switch",
            "settling-resize",
        ) and int(after["render_frame_generation"]) <= self._pending_render_frame_generation:
            errors.append("no newer frame was published for the settling transition")
        if width is not None:
            actual_size = (after.get("width"), after.get("height"))
            if (
                not isinstance(actual_size[0], int)
                or not isinstance(actual_size[1], int)
                or actual_size[0] <= 0
                or actual_size[1] <= 0
            ):
                errors.append("window lost its drawable size during settling")
            elif step.action == "settling-resize" and actual_size == self._pending_before_size:
                errors.append(
                    "compositor did not apply the requested logical size transition"
                )
        if errors:
            return "FAIL", "; ".join(errors)
        if cycle is not None:
            return "PASS", (
                f"cycle {cycle} comparable small-window HDR endpoint; frame "
                f"generation {after['render_frame_generation']}"
            )
        return "PASS", f"actual {str(expected_signal).upper()} settling state confirmed"

    def _verify_step(self, step: ValidationStep, after: dict[str, object]) -> tuple[str, str]:
        active_api, session_status = self._renderer_facts()
        expected_api = self._expected_active_api()
        if active_api != expected_api or session_status != "active":
            return "FAIL", f"renderer became {active_api}/{session_status}"

        if (
            getattr(getattr(self, "config", None), "mode", None) == "settling"
            and step.action.startswith("settling-")
        ):
            return self._verify_settling_step(step, after, expected_api)

        if getattr(getattr(self, "config", None), "mode", None) == "publication" and step.action in (
            "publication-state", "switch-media"
        ):
            expected_index, expected_signal = step.value
            if step.action == "switch-media" and int(
                after["render_frame_generation"]
            ) <= self._pending_render_frame_generation:
                return "FAIL", "no newer frame was published after the media switch"
            errors = publication_state_errors(
                after,
                expected_index=int(expected_index),
                expected_signal=str(expected_signal),
                expected_api=expected_api,
                expected_context_generation=self._publication_context_generation,
            )
            if errors:
                return "FAIL", "; ".join(errors)
            return "PASS", (
                f"actual {str(expected_signal).upper()} Gdk.GLTexture publication "
                f"at playlist {expected_index}; frame generation "
                f"{after['render_frame_generation']}"
            )

        position = float(after["time_pos"])
        if step.action == "play":
            if not self._pending_position_available or not bool(
                after["time_pos_available"]
            ):
                return "FAIL", "playback position was unavailable"
            advanced = position - self._pending_before_position
            if bool(after["pause"]) or advanced < min(2.0, step.wait_seconds * 0.25):
                return "FAIL", f"playback advanced only {advanced:.2f}s"
            if advanced > step.wait_seconds * 1.5 + 2.0:
                return "FAIL", f"playback jumped forward by {advanced:.2f}s"
            if not 0.95 <= float(after["speed"]) <= 1.05:
                return "FAIL", f"playback speed became {after['speed']:.2f}×"
            return "PASS", f"playback advanced by {advanced:.2f}s"
        elif step.action == "pause":
            if not bool(after["pause"]):
                return "FAIL", "mpv did not enter the paused state"
            if not self._pending_position_available or not bool(
                after["time_pos_available"]
            ):
                return "WARN", "pause confirmed; playback position unavailable"
            drift = abs(position - self._pending_before_position)
            if drift > 1.0:
                return "FAIL", f"pause drifted by {drift:.2f}s"
            return "PASS", f"pause drift was {drift:.2f}s"
        elif step.action == "seek":
            if not bool(after["time_pos_available"]):
                return "FAIL", "playback position was unavailable after seek"
            target = self._pending_target_position or 0.0
            error = abs(position - target)
            if error > 2.0:
                return "FAIL", f"seek landed {error:.2f}s from target"
            return "PASS", f"seek landed {error:.2f}s from target"
        elif step.action == "resize":
            expected_width, expected_height = step.value
            before_width, before_height = self._pending_before_size
            if int(after["width"]) <= 0 or int(after["height"]) <= 0:
                return "FAIL", "window lost its drawable size"
            if (
                abs(int(after["width"]) - before_width) < 24
                and abs(int(after["height"]) - before_height) < 24
            ):
                return "WARN", (
                    f"compositor kept the previous {after['width']}×"
                    f"{after['height']} window size"
                )
            if (
                abs(int(after["width"]) - expected_width) > 64
                or abs(int(after["height"]) - expected_height) > 64
            ):
                return "WARN", (
                    f"compositor chose {after['width']}×{after['height']} "
                    f"instead of {expected_width}×{expected_height}"
                )
            return "PASS", f"window is {after['width']}×{after['height']}"
        elif step.action == "fullscreen":
            if bool(after["fullscreen"]) != bool(step.value):
                return "WARN", "fullscreen state was not confirmed by GTK"
            return "PASS", "fullscreen state confirmed by GTK"
        elif step.action == "context-cycle":
            generation = int(
                getattr(self.video_area, "render_context_generation", 0)
            )
            if generation <= self._pending_context_generation:
                return "FAIL", "libmpv render context was not recreated"
            if self.video_area.mpv_ctx is self._pending_context:
                return "FAIL", "the previous libmpv render context survived"
            if not self.video_area.gl_area.get_realized():
                return "FAIL", "Gtk.GLArea did not become realized again"
            if not getattr(self.video_area, "_first_frame_logged", False):
                return "FAIL", "no video frame rendered after context recreation"
            hdr_controller = self.video_area.hdr_controller
            if getattr(hdr_controller, "_disconnected", False):
                return "FAIL", "HDR property observers were disconnected"
            if bool(hdr_controller.is_hdr_content) != self._pending_hdr_state:
                return "FAIL", "HDR content state changed across context recreation"
            render_target = (
                self.video_area.render_target_format,
                self.video_area.render_target_depth,
            )
            if render_target != self._pending_render_target:
                return "FAIL", (
                    "render target changed from "
                    f"{self._pending_render_target} to {render_target}"
                )
            return "PASS", (
                "context generation advanced "
                f"{self._pending_context_generation}→{generation}; "
                "HDR observers and render target were preserved"
            )
        elif step.action in ("signal-state", "switch-media"):
            expected_index, expected_signal = step.value
            expected_hdr = expected_signal == "hdr"
            expected_target = (
                ("GL_RGBA16F", 16, "rec2100-pq")
                if expected_hdr
                else ("GL_RGBA8", 8, "srgb")
            )
            if int(after["playlist_pos"]) != int(expected_index):
                return "FAIL", (
                    f"playlist stayed at {after['playlist_pos']} instead of "
                    f"{expected_index}"
                )
            if step.action == "switch-media" and int(
                after["render_frame_generation"]
            ) <= self._pending_render_frame_generation:
                return "FAIL", "no new frame was published after the media switch"
            if bool(after["source_hdr"]) != expected_hdr:
                return "FAIL", (
                    f"source detector reported "
                    f"{'HDR' if after['source_hdr'] else 'SDR'} instead of "
                    f"{expected_signal.upper()}"
                )
            if bool(after["hdr_output_active"]) != expected_hdr:
                return "FAIL", (
                    "HDR output state did not follow the source in auto mode"
                )
            actual_target = (
                after["render_target_format"],
                after["render_target_depth"],
                after["render_color_state"],
            )
            if actual_target != expected_target:
                return "FAIL", (
                    f"published target was {actual_target}, expected "
                    f"{expected_target}"
                )
            if (
                self._transition_context_generation is not None
                and int(after["render_context_generation"])
                != self._transition_context_generation
            ):
                return "FAIL", "renderer context changed during a media transition"
            if not expected_hdr and after["dovi_profile"] is not None:
                return "FAIL", "Dolby Vision metadata remained attached to SDR"
            return "PASS", (
                f"playlist {expected_index}: {expected_signal.upper()} source → "
                f"{expected_target[0]}/{expected_target[1]}-bit, "
                f"{expected_target[2]}; hwdec={after['hwdec']}; "
                f"target={after['target_trc']}/{after['target_prim']}/"
                f"{after['target_peak']}"
            )
        return "PASS", "completed"

    def _advance_step(self):
        # A timeout source is removed automatically when this callback returns.
        # Clear its stored ID before _finish() removes the other live sources.
        self.step_timer_id = None
        if self.finished:
            return GLib.SOURCE_REMOVE
        if self._pending_step is not None:
            after = self._metrics_snapshot()
            status, detail = self._verify_step(self._pending_step, after)
            self.step_results.append(
                {
                    "name": self._pending_step.name,
                    "action": self._pending_step.action,
                    "expected_state": self._pending_step.value,
                    "wait_seconds": self._pending_step.wait_seconds,
                    "status": status,
                    "detail": detail,
                    "metrics": after,
                }
            )
            if self._pending_step.action in ("seek", "context-cycle") and status != "FAIL":
                self.player.command("set", "pause", "no")
            if self._pending_step.action == "context-cycle":
                self._pending_context = None
            if status == "FAIL":
                self._finish(f"validation action failed: {self._pending_step.name}")
                return GLib.SOURCE_REMOVE

        self.step_index += 1
        if self.step_index >= len(self.steps):
            self._finish(None)
            return GLib.SOURCE_REMOVE

        step = self.steps[self.step_index]
        self._pending_step = step
        logger.info("GPU validation step: %s", step.name)
        try:
            self._apply_step(step)
        except Exception as error:
            logger.exception("GPU validation action failed")
            self.step_results.append(
                {
                    "name": step.name,
                    "action": step.action,
                    "wait_seconds": step.wait_seconds,
                    "status": "FAIL",
                    "detail": str(error),
                }
            )
            self._finish(f"could not apply validation action: {step.name}")
            return GLib.SOURCE_REMOVE
        self.step_timer_id = GLib.timeout_add_seconds(
            step.wait_seconds, self._advance_step
        )
        return GLib.SOURCE_REMOVE

    def _counter_growth(self, final: dict[str, object], key: str) -> int:
        """Accumulate a counter even when mpv resets it after a seek."""
        snapshots = [self.baseline]
        snapshots.extend(getattr(self, "resource_samples", ()))
        snapshots.extend(
            result["metrics"]
            for result in self.step_results
            if isinstance(result.get("metrics"), dict)
        )
        snapshots.append(final)
        if snapshots and all("sample_at" in snapshot for snapshot in snapshots):
            snapshots.sort(key=lambda snapshot: float(snapshot["sample_at"]))
        values = [int(snapshot[key]) for snapshot in snapshots if key in snapshot]
        if len(values) < 2:
            return values[0] if values else 0
        growth = 0
        for before, after in zip(values, values[1:]):
            growth += after - before if after >= before else after
        return max(0, growth)

    def _global_vram_growth(self) -> int | None:
        if not self.vram_samples:
            return None
        cards = set().union(*(sample.keys() for sample in self.vram_samples))
        growth = 0
        found = False
        for card in cards:
            values = [sample[card] for sample in self.vram_samples if card in sample]
            if values:
                growth += values[-1] - values[0]
                found = True
        return growth if found else None

    @staticmethod
    def _sample_growth(samples: list[int]) -> int | None:
        return samples[-1] - samples[0] if samples else None

    def _resource_timeline(self) -> list[dict[str, object]]:
        if not self.resource_samples:
            return []
        first_at = float(self.resource_samples[0]["sample_at"])
        selected = [self.resource_samples[0]]
        next_minute = 5.0
        for sample in self.resource_samples[1:]:
            elapsed_minutes = (
                float(sample["sample_at"]) - first_at
            ) / 60.0
            if elapsed_minutes >= next_minute:
                selected.append(sample)
                next_minute += 5.0
        if selected[-1] is not self.resource_samples[-1]:
            selected.append(self.resource_samples[-1])
        return selected

    def _transition_repeated_hdr_growth(self, key: str) -> int | None:
        """Compare the first and second warmed HDR states, never SDR to HDR."""
        values = []
        for result in self.step_results:
            metrics = result.get("metrics")
            if (
                result.get("action") != "switch-media"
                or not isinstance(metrics, dict)
                or not metrics.get("source_hdr")
                or metrics.get(key) is None
            ):
                continue
            values.append(int(metrics[key]))
        return values[-1] - values[0] if len(values) >= 2 else None

    def _write_report(self, failure_reason: str | None) -> tuple[Path, str]:
        if self.config.mode == "publication":
            return self._write_publication_report(failure_reason)
        if self.config.mode == "settling":
            return self._write_resource_settling_report(failure_reason)
        final = self._metrics_snapshot()
        active_api, session_status = self._renderer_facts()
        fbo_drops = self._counter_growth(final, "fbo_drops")
        decoder_drops = self._counter_growth(final, "decoder_drops")
        vo_drops = self._counter_growth(final, "vo_drops")
        gl_errors = int(final.get("gl_errors", 0))
        fbo_failures = int(final.get("fbo_failures", 0))
        raw_rss_growth = None
        if self.memory_samples:
            raw_rss_growth = self.memory_samples[-1] - self.memory_samples[0]
        raw_process_vram_growth = self._sample_growth(self.drm_vram_samples)
        raw_process_gtt_growth = self._sample_growth(self.drm_gtt_samples)
        if self.config.mode == "transition":
            rss_growth = self._transition_repeated_hdr_growth("rss_kib")
            process_vram_growth = self._transition_repeated_hdr_growth(
                "drm_vram_kib"
            )
            process_gtt_growth = self._transition_repeated_hdr_growth(
                "drm_gtt_kib"
            )
        else:
            rss_growth = raw_rss_growth
            process_vram_growth = raw_process_vram_growth
            process_gtt_growth = raw_process_gtt_growth
        global_vram_growth = self._global_vram_growth()
        statuses = tuple(str(result.get("status")) for result in self.step_results)
        overall, warnings = evaluate_validation(
            active_api=active_api,
            session_status=session_status,
            hwdec=str(final.get("hwdec", "unknown")),
            step_statuses=statuses + (("FAIL",) if failure_reason else ()),
            fbo_drops=fbo_drops,
            decoder_drops=decoder_drops,
            # File reloads deliberately tear down video timing, so their VO
            # counter is not a stable-playback drop measurement. Keep the raw
            # count in the report; playback actions remain strict.
            vo_drops=(0 if self.config.mode == "transition" else vo_drops),
            rss_growth_kib=rss_growth,
            process_vram_growth_kib=process_vram_growth,
            process_vram_measured=(
                process_vram_growth is not None
                if self.config.mode == "transition"
                else bool(self.drm_vram_samples)
            ),
            gl_errors=gl_errors,
            fbo_failures=fbo_failures,
        )

        isolation_failures = tuple(
            getattr(self.window.app, "_gpu_validation_setup_failures", ())
        )
        if isolation_failures:
            overall = "WARN" if overall == "PASS" else overall
            warnings = warnings + (
                "validation process isolation was incomplete: "
                + ", ".join(isolation_failures),
            )

        runtime = getattr(self.video_area, "render_runtime", {}) or {}
        params = _mpv_property(self.player, "video-out-params", {}) or {}
        if not isinstance(params, dict):
            params = {}
        hdr_controller = getattr(self.video_area, "hdr_controller", None)
        profile = getattr(hdr_controller, "dovi_profile", None)
        level = getattr(hdr_controller, "dovi_level", None)
        profile = "none" if profile is None else profile
        level = "unknown" if level is None else level
        target_peak = _mpv_property(self.player, "target-peak", "auto")
        playback_seconds = sum(
            int(result.get("wait_seconds", 0))
            for result in self.step_results
            if result.get("action") in ("play", "switch-media")
            and result.get("status") != "FAIL"
        )
        video_fps = _number(
            _mpv_property(self.player, "estimated-vf-fps"), 0.0
        )
        estimated_frames = playback_seconds * video_fps

        def drop_summary(count: int) -> str:
            if estimated_frames <= 0:
                return str(count)
            percentage = count / estimated_frames * 100.0
            return f"{count} ({percentage:.3f}% of ~{estimated_frames:.0f} played frames)"

        elapsed = time.monotonic() - self.started_at
        lines = [
            "CineHDR GPU Next Validation Report",
            "",
            f"Overall result: {overall}",
            f"Mode: {self.config.mode}",
            f"Elapsed: {elapsed / 60:.1f} minutes",
            "Media path: omitted",
            "Saved-position restore/write: "
            + ("disabled" if not isolation_failures else "partially disabled"),
            f"Active renderer: {active_api or 'unknown'}",
            f"Renderer status: {session_status}",
            "Validation HDR mode: "
            + (
                "auto (temporary; saved preference unchanged)"
                if self.config.mode in ("transition", "sdr-soak")
                else "unchanged"
            ),
            "Render context generation: "
            f"{getattr(self.video_area, 'render_context_generation', 'unknown')}",
            f"Hardware decoding: {final.get('hwdec', 'unknown')}",
            f"Playback speed: {final.get('speed', 'unknown')}×",
            f"mpv: {runtime.get('mpv_version', 'unknown')}",
            f"libplacebo: {runtime.get('libplacebo_version', 'unknown')}",
            f"FFmpeg: {runtime.get('ffmpeg_version', 'unknown')}",
            f"OpenGL vendor: {runtime.get('gl_vendor', 'unknown')}",
            f"OpenGL renderer: {runtime.get('gl_renderer', 'unknown')}",
            f"OpenGL version: {runtime.get('gl_version', 'unknown')}",
            f"GLSL version: {runtime.get('glsl_version', 'unknown')}",
            f"Video format: {_mpv_property(self.player, 'video-format', 'unknown')}",
            f"Video resolution: {params.get('w', 'unknown')}×{params.get('h', 'unknown')}",
            f"Video pixel format: {params.get('pixelformat', 'unknown')}",
            f"Dolby Vision profile: {profile}",
            f"Dolby Vision level: {level}",
            f"Target peak: {target_peak}",
            f"Render target: {getattr(self.video_area, 'render_target_format', 'unknown')} / {getattr(self.video_area, 'render_target_depth', 'unknown')}-bit",
            f"Published color state: {getattr(self.video_area, 'render_color_state', 'unknown')}",
            "GPU Next context creation: "
            + ("PASS" if active_api == "opengl-next" else "FAIL"),
            "FBO complete/renderable: "
            + (
                "PASS"
                if getattr(self.video_area, "_first_frame_logged", False)
                and not fbo_failures
                else "FAIL"
            ),
            "",
            "[Actions]",
        ]
        for result in self.step_results:
            lines.append(
                f"{result.get('status', 'UNKNOWN')}: {result.get('name')} — "
                f"{result.get('detail', '')}"
            )
        if failure_reason:
            lines.append(f"FAIL: session — {failure_reason}")

        lines.extend(
            [
                "",
                "[Measurements]",
                f"CineHDR FBO-pool frame drops: {fbo_drops}",
                f"CineHDR FBO allocation failures: {fbo_failures}",
                f"CineHDR-observed OpenGL errors: {gl_errors}",
                f"mpv decoder frame drops: {drop_summary(decoder_drops)}",
                "mpv video-output frame drops"
                + (
                    " across file reloads (informational): "
                    if self.config.mode == "transition"
                    else ": "
                )
                + drop_summary(vo_drops),
                f"mpv mistimed frames observed: {self._counter_growth(final, 'mistimed_frames')}",
                f"mpv delayed frames observed: {self._counter_growth(final, 'delayed_frames')}",
                f"Process RSS start: {_format_kib(self.memory_samples[0] if self.memory_samples else None)}",
                f"Process RSS peak: {_format_kib(max(self.memory_samples) if self.memory_samples else None)}",
                f"Process RSS end: {_format_kib(self.memory_samples[-1] if self.memory_samples else None)}",
                f"Process RSS raw start-to-end growth: {_format_kib(raw_rss_growth)}",
                f"CineHDR DRM-client VRAM start: {_format_kib(self.drm_vram_samples[0] if self.drm_vram_samples else None)}",
                f"CineHDR DRM-client VRAM peak: {_format_kib(max(self.drm_vram_samples) if self.drm_vram_samples else None)}",
                f"CineHDR DRM-client VRAM end: {_format_kib(self.drm_vram_samples[-1] if self.drm_vram_samples else None)}",
                f"CineHDR DRM-client VRAM raw start-to-end growth: {_format_kib(raw_process_vram_growth)}",
                f"CineHDR DRM-client GTT raw start-to-end growth: {_format_kib(raw_process_gtt_growth)}",
                f"Global GPU VRAM growth (context only): {_format_kib(global_vram_growth)}",
                *(
                    [
                        "Repeated HDR state RSS growth: "
                        + _format_kib(rss_growth),
                        "Repeated HDR state CineHDR DRM-client VRAM growth: "
                        + _format_kib(process_vram_growth),
                        "Repeated HDR state CineHDR DRM-client GTT growth: "
                        + _format_kib(process_gtt_growth),
                    ]
                    if self.config.mode == "transition"
                    else []
                ),
                "",
                "[Five-minute resource timeline]",
            ]
        )
        timeline = self._resource_timeline()
        timeline_origin = (
            float(timeline[0]["sample_at"]) if timeline else 0.0
        )
        for sample in timeline:
            minute = (float(sample["sample_at"]) - timeline_origin) / 60.0
            lines.append(
                f"+{minute:.1f}m: RSS {_format_kib(sample.get('rss_kib'))}; "
                f"DRM VRAM {_format_kib(sample.get('drm_vram_kib'))}; "
                f"DRM GTT {_format_kib(sample.get('drm_gtt_kib'))}; "
                f"global VRAM {_format_kib(sample.get('global_vram_kib'))}; "
                f"decoder/VO counters {sample.get('decoder_drops', 0)}/"
                f"{sample.get('vo_drops', 0)}"
            )
        lines.extend(
            [
                "",
                "[Warnings]",
            ]
        )
        lines.extend(f"- {warning}" for warning in warnings)
        if not warnings:
            lines.append("- none")
        lines.extend(
            [
                "",
                "[Interpretation]",
                "PASS means this local interaction sequence completed without a measured regression.",
                "It is not a general compatibility or color-accuracy certification.",
            ]
        )

        self.config.report_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        report_path = self.config.report_dir / f"gpu-next-{self.config.mode}-{stamp}.txt"
        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return report_path, overall

    def _write_resource_settling_report(
        self, failure_reason: str | None
    ) -> tuple[Path, str]:
        """Write the separate, machine-readable bounded settling evidence."""
        active_api, session_status = self._renderer_facts()
        expected_api = self._expected_active_api()
        endpoint_results = tuple(
            result
            for result in self.step_results
            if result.get("action") == "settling-endpoint"
        )
        cooldown_result = next(
            (
                result
                for result in reversed(self.step_results)
                if result.get("action") == "settling-cooldown"
                and isinstance(result.get("metrics"), Mapping)
            ),
            None,
        )
        cooldown_metrics = (
            cooldown_result["metrics"]
            if isinstance(cooldown_result, Mapping)
            and isinstance(cooldown_result.get("metrics"), Mapping)
            else self._metrics_snapshot()
        )
        isolation_failures = tuple(
            getattr(self.window.app, "_gpu_validation_setup_failures", ())
        )
        overall, findings, trends = evaluate_resource_settling_validation(
            active_api=active_api,
            session_status=session_status,
            expected_api=expected_api,
            endpoint_results=endpoint_results,
            step_results=tuple(self.step_results),
            final=cooldown_metrics,
            expected_context_generation=self._settling_context_generation,
            failure_reason=failure_reason,
            isolation_failures=isolation_failures,
        )

        def endpoint_values(key: str) -> list[int | None]:
            values: list[int | None] = []
            for result in endpoint_results:
                metrics = result.get("metrics")
                value = metrics.get(key) if isinstance(metrics, Mapping) else None
                values.append(value if isinstance(value, int) and not isinstance(value, bool) else None)
            return values

        elapsed = time.monotonic() - self.started_at
        report = {
            "schema": SETTLING_SCHEMA,
            "revision": SETTLING_REVISION,
            "status": overall,
            "mode": "settling",
            "elapsed_seconds": elapsed,
            "fixture": {
                "profile": self.config.fixture_profile,
                "revision": self.config.fixture_revision,
                "sdr_sha256": self.config.fixture_sdr_sha256,
                "hdr_sha256": self.config.fixture_hdr_sha256,
            },
            "expected_backend": self.config.expected_backend,
            "expected_renderer_api": expected_api,
            "active_renderer_api": active_api,
            "renderer_status": session_status,
            "validation_hdr_mode": "auto (temporary; saved preference unchanged)",
            "saved_position_session_watch_later_playlist_writes": "disabled",
            "context_generation": self._settling_context_generation,
            "warm_up": next(
                (
                    result
                    for result in self.step_results
                    if result.get("action") == "settling-warmup"
                ),
                None,
            ),
            "actions": self.step_results,
            "hdr_endpoints": list(endpoint_results),
            "endpoint_requested_logical_size": {"width": 960, "height": 540},
            "endpoint_actual_logical_sizes": [
                (
                    {
                        "width": result.get("metrics", {}).get("width"),
                        "height": result.get("metrics", {}).get("height"),
                    }
                    if isinstance(result.get("metrics"), Mapping)
                    else None
                )
                for result in endpoint_results
            ],
            "cooldown": cooldown_result,
            "cooldown_metrics": cooldown_metrics,
            "endpoint_trends": {
                "rss_kib": {
                    "samples": endpoint_values("rss_kib"),
                    "classification": trends["rss_kib"],
                },
                "drm_vram_kib": {
                    "samples": endpoint_values("drm_vram_kib"),
                    "classification": trends["drm_vram_kib"],
                },
            },
            "diagnostic_only": {
                "process_drm_gtt_kib": endpoint_values("drm_gtt_kib"),
                "global_vram_kib": [
                    (
                        result.get("metrics", {}).get("global_vram_kib")
                        if isinstance(result.get("metrics"), Mapping)
                        else None
                    )
                    for result in endpoint_results
                ],
                "note": "GTT and global VRAM are recorded for context only and do not determine status.",
            },
            "findings": list(findings),
            "scope": (
                "PASS means this bounded repeated allocation workload had no "
                "strictly-increasing same-state RSS or process DRM VRAM signal "
                "and met structural checks. It is not a leak-free claim, does "
                "not fit a byte threshold, and does not certify long-media, "
                "compositor, display, or color behavior."
            ),
        }
        self.config.report_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        report_path = self.config.report_dir / (
            f"gate2-resource-settling-{self.config.expected_backend}-{stamp}.json"
        )
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return report_path, overall

    def _write_publication_report(
        self, failure_reason: str | None
    ) -> tuple[Path, str]:
        """Write the intentionally separate Gate 2 GTK/GDK publication report."""
        final = self._metrics_snapshot()
        active_api, session_status = self._renderer_facts()
        expected_api = self._expected_active_api()
        isolation_failures = tuple(
            getattr(self.window.app, "_gpu_validation_setup_failures", ())
        )
        statuses = tuple(str(result.get("status")) for result in self.step_results)
        state_sequence = tuple(
            (
                int(result["expected_state"][0]),
                str(result["expected_state"][1]),
                str(result.get("status")),
            )
            for result in self.step_results
            if isinstance(result.get("expected_state"), tuple)
            and len(result["expected_state"]) == 2
        )
        overall, failures = evaluate_publication_validation(
            active_api=active_api,
            session_status=session_status,
            expected_api=expected_api,
            final=final,
            step_statuses=statuses,
            state_sequence=state_sequence,
            failure_reason=failure_reason,
            isolation_failures=isolation_failures,
        )
        runtime = final.get("render_runtime", {})
        if not isinstance(runtime, Mapping):
            runtime = {}
        texture = final.get("publication_texture", {})
        if not isinstance(texture, Mapping):
            texture = {}

        elapsed = time.monotonic() - self.started_at
        lines = [
            "CineHDR Gate 2 Wayland GDK Publication Validation Report",
            "",
            f"Overall result: {overall}",
            "Mode: publication",
            f"Elapsed: {elapsed:.1f} seconds",
            "Media path: omitted",
            "Saved-position/session/watch-later/playlist writes: disabled",
            "Validation HDR mode: auto (temporary; saved preference unchanged)",
            f"Fixture profile: {self.config.fixture_profile}",
            f"Fixture revision: {self.config.fixture_revision}",
            f"Fixture SDR SHA-256: {self.config.fixture_sdr_sha256}",
            f"Fixture HDR SHA-256: {self.config.fixture_hdr_sha256}",
            f"Expected backend: {self.config.expected_backend}",
            f"Expected renderer API: {expected_api}",
            f"Active renderer API: {active_api or 'unknown'}",
            f"Renderer status: {session_status}",
            f"Hardware decoding: {final.get('hwdec', 'unknown')}",
            f"libmpv path: {runtime.get('libmpv_path', 'unknown')}",
            f"mpv: {runtime.get('mpv_version', 'unknown')}",
            f"libplacebo: {runtime.get('libplacebo_version', 'unknown')}",
            f"FFmpeg: {runtime.get('ffmpeg_version', 'unknown')}",
            "",
            "[Publication states]",
        ]
        for result in self.step_results:
            metrics = result.get("metrics")
            state_texture = (
                metrics.get("publication_texture", {})
                if isinstance(metrics, Mapping)
                else {}
            )
            if not isinstance(state_texture, Mapping):
                state_texture = {}
            lines.extend(
                [
                    f"{result.get('status', 'UNKNOWN')}: {result.get('name')} — {result.get('detail', '')}",
                    "  texture: "
                    f"{state_texture.get('texture_gtype', 'unavailable')} "
                    f"{state_texture.get('width', 'unknown')}×{state_texture.get('height', 'unknown')} "
                    f"{state_texture.get('format', 'unknown')}",
                    "  color equality: "
                    f"sRGB={state_texture.get('color_state_srgb', 'unavailable')} "
                    f"Rec2100PQ={state_texture.get('color_state_rec2100_pq', 'unavailable')}",
                    "  Wayland evidence: "
                    f"display={state_texture.get('display_gtype', 'unavailable')} "
                    f"surface={state_texture.get('surface_gtype', 'unavailable')}",
                ]
            )
        lines.extend(
            [
                "",
                "[Final actual texture evidence]",
                f"Gdk.GLTexture: {texture.get('texture_is_gl_texture', False)}",
                f"Texture GType: {texture.get('texture_gtype', 'unavailable')}",
                f"Texture dimensions: {texture.get('width', 'unknown')}×{texture.get('height', 'unknown')}",
                f"Expected scaled dimensions: {texture.get('expected_width', 'unknown')}×{texture.get('expected_height', 'unknown')}",
                f"Texture dimensions match: {texture.get('dimensions_match', False)}",
                f"Texture format: {texture.get('format', 'unavailable')}",
                f"Texture sRGB equality: {texture.get('color_state_srgb', 'unavailable')}",
                f"Texture Rec.2100 PQ equality: {texture.get('color_state_rec2100_pq', 'unavailable')}",
                f"GDK display GType: {texture.get('display_gtype', 'unavailable')}",
                f"GDK surface GType: {texture.get('surface_gtype', 'unavailable')}",
                f"GDK evidence error: {texture.get('error', 'none')}",
                "",
                "[Failures]",
                *(f"- {failure}" for failure in failures),
                *( [f"- session: {failure_reason}"] if failure_reason and failure_reason not in failures else [] ),
                "- none" if not failures else "",
                "",
                "[Scope]",
                "PASS validates live GTK/GDK object publication in a Wayland client only.",
                "It does not certify compositor image-description acceptance, display transformation, or color output.",
            ]
        )
        self.config.report_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        report_path = self.config.report_dir / (
            f"gate2-wayland-publication-{self.config.expected_backend}-{stamp}.txt"
        )
        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return report_path, overall

    def _finish(self, failure_reason: str | None):
        if self.finished:
            return
        self.finished = True
        for source_id in (
            self.ready_timer_id,
            self.sample_timer_id,
            self.step_timer_id,
            self.lifecycle_timer_id,
        ):
            if source_id:
                try:
                    GLib.source_remove(source_id)
                except Exception:
                    pass
        try:
            self.player.pause = True
        except Exception:
            logger.exception("Could not pause player while finishing validation")
        try:
            self.player.fullscreen = False
        except Exception:
            pass
        try:
            self.report_path, result = self._write_report(failure_reason)
        except Exception:
            logger.exception("Failed writing GPU validation report")
            self.window.show_toast(_("GPU validation report could not be written"), True)
            return
        logger.info("GPU validation %s: %s", result, self.report_path)
        self.window.show_toast(
            _("GPU validation {result}. Report saved; closing shortly.").format(
                result=result
            ),
            True,
        )
        self.finish_timer_id = GLib.timeout_add_seconds(
            4, self._close_after_finish
        )

    def _close_after_finish(self):
        self.window.close()
        return GLib.SOURCE_REMOVE

    def _on_close_request(self, *args):
        if not self.finished:
            self._finish("window closed before validation completed")
        return False

    def abort(self):
        if not self.finished:
            self._finish("application stopped before validation completed")
