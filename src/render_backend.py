# render_backend.py
#
# Copyright 2026 rusmikev / Diego Povliuk
# SPDX-License-Identifier: GPL-3.0-or-later

"""Process-level libmpv render-backend selection for CineHDR.

``CINEHDR_RENDER_BACKEND`` remains a development override. It accepts
``legacy``, ``gpu-next``, or ``auto`` and takes priority over the persistent
``render-backend`` preference.

The selector only falls back while constructing a render context.  Once a
context exists, its renderer is immutable for that CineHDR run.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import ctypes
import ctypes.util
from dataclasses import dataclass
from enum import Enum
import os
from typing import TypeVar


RENDER_BACKEND_ENV = "CINEHDR_RENDER_BACKEND"


class RenderBackend(str, Enum):
    LEGACY = "legacy"
    GPU_NEXT = "gpu-next"
    AUTO = "auto"


class RenderBackendConfigurationError(ValueError):
    """The development backend selector was set to an unsupported value."""


class RenderBackendUnavailableError(RuntimeError):
    """The explicitly requested experimental renderer could not be created."""


@dataclass(frozen=True)
class RenderBackendState:
    requested: RenderBackend
    active: str
    fallback_reason: str | None = None


class RenderSelectionSource(str, Enum):
    SETTINGS = "gsettings"
    ENVIRONMENT = "environment"


@dataclass(frozen=True)
class ProcessRenderSelection:
    """Immutable renderer choice captured once when the process starts."""

    configured: RenderBackend
    requested: RenderBackend
    source: RenderSelectionSource
    allow_creation_fallback: bool


@dataclass(frozen=True)
class RenderTargetSpec:
    """Format parameters split as required by python-mpv's Render API."""

    internal_format: int
    depth: int


@dataclass(frozen=True)
class DolbyVisionRenderCapability:
    """Version-qualified knowledge about the experimental RPU render path.

    ``rpu_path_available`` means source review covers this exact dependency
    stack and the corresponding backend is running. It deliberately does not
    mean that CineHDR has completed its pixel/color validation gate.
    """

    rpu_path_available: bool
    reason: str


PINNED_DOVI_MPV_REVISION = "g97179bce7"
PINNED_DOVI_LIBPLACEBO_VERSION = "7.360.1"
PINNED_DOVI_FFMPEG_VERSION = "8.1.2"


T = TypeVar("T")


def requested_backend(environ: Mapping[str, str] | None = None) -> RenderBackend:
    """Return the requested backend, defaulting safely to legacy."""
    source = os.environ if environ is None else environ
    value = source.get(RENDER_BACKEND_ENV, RenderBackend.LEGACY.value).strip().lower()
    try:
        return RenderBackend(value)
    except ValueError as error:
        supported = ", ".join(backend.value for backend in RenderBackend)
        raise RenderBackendConfigurationError(
            f"{RENDER_BACKEND_ENV} must be one of: {supported}; got {value!r}"
        ) from error


def select_process_backend(
    configured: str,
    environ: Mapping[str, str] | None = None,
) -> ProcessRenderSelection:
    """Resolve the immutable process selection from preference and override.

    A saved GPU Next preference is allowed one legacy fallback during initial
    context creation so an unpatched installation remains usable. An explicit
    ``gpu-next`` environment override is strict; development ``auto`` retains
    its creation-only fallback behavior.
    """
    try:
        configured_backend = RenderBackend(configured.strip().lower())
    except ValueError as error:
        raise RenderBackendConfigurationError(
            f"render-backend must be legacy or gpu-next; got {configured!r}"
        ) from error
    if configured_backend is RenderBackend.AUTO:
        raise RenderBackendConfigurationError(
            "render-backend cannot persist the development-only auto mode"
        )

    source = os.environ if environ is None else environ
    if RENDER_BACKEND_ENV in source:
        requested = requested_backend(source)
        return ProcessRenderSelection(
            configured=configured_backend,
            requested=requested,
            source=RenderSelectionSource.ENVIRONMENT,
            allow_creation_fallback=requested is RenderBackend.AUTO,
        )

    return ProcessRenderSelection(
        configured=configured_backend,
        requested=configured_backend,
        source=RenderSelectionSource.SETTINGS,
        allow_creation_fallback=configured_backend is RenderBackend.GPU_NEXT,
    )


def renderer_preference_requires_restart(
    selection: ProcessRenderSelection, configured: str
) -> bool:
    """Whether a changed saved choice needs a restart to take effect.

    A development environment override is process-external and continues to
    win after a restart.  In that case the saved preference is merely waiting
    for the override to be removed, not for CineHDR to restart.
    """
    if selection.source is RenderSelectionSource.ENVIRONMENT:
        return False
    try:
        next_backend = RenderBackend(configured.strip().lower())
    except ValueError:
        return True
    return next_backend is not selection.configured


def create_render_context(
    requested: RenderBackend,
    create: Callable[[str], T],
    *,
    allow_gpu_next_fallback: bool = False,
) -> tuple[T, RenderBackendState]:
    """Create a context using the requested API type.

    Development ``auto`` always has a legacy fallback. A saved ``gpu-next``
    preference may opt into the same one-time fallback via
    ``allow_gpu_next_fallback``; an explicit environment ``gpu-next`` request
    remains strict. This function must only be called for initial context
    construction, never from the frame-rendering path.
    """
    if requested is RenderBackend.LEGACY:
        return create("opengl"), RenderBackendState(requested, "opengl")

    if requested is RenderBackend.GPU_NEXT:
        try:
            context = create("opengl-next")
        except Exception as error:
            if allow_gpu_next_fallback:
                reason = f"opengl-next context creation failed: {error}"
                try:
                    context = create("opengl")
                except Exception as legacy_error:
                    raise RenderBackendUnavailableError(
                        "gpu-next preference could not create opengl-next or "
                        f"legacy opengl render contexts; {reason}; legacy "
                        f"failure: {legacy_error}"
                    ) from legacy_error
                return context, RenderBackendState(
                    requested, "opengl", fallback_reason=reason
                )
            raise RenderBackendUnavailableError(
                "gpu-next was explicitly requested, but libmpv could not create "
                f"an opengl-next render context: {error}"
            ) from error
        return context, RenderBackendState(requested, "opengl-next")

    try:
        context = create("opengl-next")
    except Exception as next_error:
        reason = f"opengl-next context creation failed: {next_error}"
        try:
            context = create("opengl")
        except Exception as legacy_error:
            raise RenderBackendUnavailableError(
                "auto backend could not create opengl-next or legacy opengl "
                f"render contexts; {reason}; legacy failure: {legacy_error}"
            ) from legacy_error
        return context, RenderBackendState(
            requested, "opengl", fallback_reason=reason
        )
    return context, RenderBackendState(requested, "opengl-next")


def render_target_spec(
    use_float: bool, *, rgba16f: int, rgba8: int
) -> RenderTargetSpec:
    """Map CineHDR's target allocation to libmpv's FBO format and depth."""
    if use_float:
        return RenderTargetSpec(internal_format=rgba16f, depth=16)
    return RenderTargetSpec(internal_format=rgba8, depth=8)


def should_render_frame(update_flags: object, *, force_redraw: bool) -> bool:
    """Allow an explicit target-size redraw without inventing a new frame.

    libmpv's Render API redraws the previous frame when no new frame is
    available. CineHDR needs that path when a paused GTK surface changes size;
    otherwise the old GDK texture is merely scaled and the FBO is never
    reconfigured for the new target.
    """
    return bool(update_flags) or force_redraw


def target_configuration_requires_redraw(
    previous: tuple[int, int, int],
    current: tuple[int, int, int],
    *,
    has_texture: bool,
) -> bool:
    """Redraw an existing frame when logical size or scale factor changes."""
    return has_texture and previous != current and current[0] > 0 and current[1] > 0


def dolby_vision_render_capability(
    active_api: str | None,
    session_status: str,
    runtime: Mapping[str, object] | None,
) -> DolbyVisionRenderCapability:
    """Return conservative, version-qualified Dolby Vision path knowledge.

    The accepted architecture review covers only the pinned local stack. An
    ``opengl-next`` API name from another build is insufficient evidence
    because the experimental Render API has no stable capability contract.
    """
    if active_api != "opengl-next":
        return DolbyVisionRenderCapability(False, "inactive-backend")
    if session_status != "active":
        return DolbyVisionRenderCapability(False, "renderer-not-active")

    values = runtime or {}
    mpv_version = str(values.get("mpv_version", ""))
    libplacebo_version = str(values.get("libplacebo_version", "")).removeprefix("v")
    ffmpeg_version = str(values.get("ffmpeg_version", "")).removeprefix("n")
    if PINNED_DOVI_MPV_REVISION not in mpv_version:
        return DolbyVisionRenderCapability(False, "unvalidated-mpv-build")
    if libplacebo_version != PINNED_DOVI_LIBPLACEBO_VERSION:
        return DolbyVisionRenderCapability(False, "unvalidated-libplacebo-build")
    if ffmpeg_version != PINNED_DOVI_FFMPEG_VERSION:
        return DolbyVisionRenderCapability(False, "unvalidated-ffmpeg-build")
    return DolbyVisionRenderCapability(True, "pinned-source-reviewed-path")


def install_python_mpv_depth_compat(mpv_module) -> bool:
    """Work around python-mpv 1.0.8's broken integer render parameters.

    ``MPV_RENDER_PARAM_DEPTH`` is an ``int *`` in libmpv.  python-mpv 1.0.8
    declares that correctly, but then tries to construct every non-special
    parameter with ``cons(**value)``.  Passing an integer therefore fails
    before libmpv sees the frame.  Keep the workaround narrow and remove it
    when the installed binding can construct ``MpvRenderParam("depth", 8)``.
    """
    render_param = getattr(mpv_module, "MpvRenderParam", None)
    if render_param is None:
        raise RuntimeError("python-mpv does not expose MpvRenderParam")

    marker = "_cinehdr_depth_int_compat"
    if getattr(render_param, marker, False):
        return False

    try:
        render_param("depth", 8)
    except TypeError:
        depth_type = getattr(render_param, "TYPES", {}).get("depth")
        if depth_type != (5, int):
            raise RuntimeError(
                "Unsupported python-mpv depth render-parameter layout"
            )

        original_init = render_param.__init__

        def compatible_init(self, name, value=None):
            if name == "depth" and isinstance(value, int):
                self.type_id = 5
                self.value = ctypes.c_int(value)
                self.data = ctypes.cast(
                    ctypes.pointer(self.value), ctypes.c_void_p
                )
                return
            original_init(self, name, value)

        render_param.__init__ = compatible_init
        setattr(render_param, marker, True)
        return True

    return False


def mapped_library_path(
    library_name: str, *, maps_path: str = "/proc/self/maps"
) -> str | None:
    """Return the real path of a loaded Linux shared library, if available."""
    try:
        with open(maps_path, encoding="utf-8") as maps_file:
            for line in maps_file:
                fields = line.rstrip().split(maxsplit=5)
                if len(fields) != 6:
                    continue
                candidate = fields[5]
                if f"/{library_name}.so" in candidate:
                    return os.path.realpath(candidate)
    except OSError:
        pass
    return None


def render_runtime_diagnostics(mpv_module, player) -> dict[str, str]:
    """Collect dependency identity used by the active render process."""
    backend = getattr(mpv_module, "backend", None)
    libmpv_fallback = getattr(backend, "_name", "unknown")
    diagnostics = {
        "libmpv_path": mapped_library_path("libmpv") or str(libmpv_fallback),
        "mpv_version": "unknown",
        "ffmpeg_version": "unknown",
        "libplacebo_version": "unknown",
        "mpv_configuration": "unknown",
    }

    get_property = getattr(player, "_get_property", None)
    if get_property:
        for property_name, field_name in (
            ("mpv-version", "mpv_version"),
            ("ffmpeg-version", "ffmpeg_version"),
            ("mpv-configuration", "mpv_configuration"),
        ):
            try:
                value = get_property(property_name)
            except Exception:
                value = None
            if value is not None:
                diagnostics[field_name] = str(value)

    try:
        placebo_name = ctypes.util.find_library("placebo")
        if placebo_name:
            placebo = ctypes.CDLL(placebo_name)
            placebo.pl_version.restype = ctypes.c_char_p
            version = placebo.pl_version()
            if version:
                diagnostics["libplacebo_version"] = version.decode(
                    "utf-8", errors="replace"
                )
    except (AttributeError, OSError):
        pass

    return diagnostics
