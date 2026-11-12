# window_diagnostics.py
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Diagnostics export lifecycle and mpv log helpers for CineWindow."""

import hashlib
import logging
import os
from collections.abc import Callable
from typing import Any

from .diagnostics_report import (
    DIAGNOSTICS_EXPORT_DIR_ENV,
    DIAGNOSTICS_EXPORT_PATH_ENV,
    get_mpv_prop,
)
from .render_backend import media_frame_evidence

logger = logging.getLogger(__name__)


def init_diagnostics_exporter(
    package_name: str | None,
    win_logger: logging.Logger,
    loader: Callable[..., Any],
):
    """Initialize the optional diagnostics exporter if requested via env vars."""
    if not (
        os.environ.get(DIAGNOSTICS_EXPORT_PATH_ENV, "").strip()
        or os.environ.get(DIAGNOSTICS_EXPORT_DIR_ENV, "").strip()
    ):
        return None
    diag_export_mod = loader(
        package_name,
        "diagnostics_export",
        "Diagnostics export is unavailable; install with -Dvalidation=true",
        win_logger,
    )
    return diag_export_mod.exporter_from_env()


def _reset_current_area_state(
    win: Any,
    video_area: Any,
    context_generation: int | None,
    load_generation: int | None,
) -> None:
    if video_area and not getattr(win, "_shutting_down", False):
        if context_generation is not None and load_generation is not None:
            expected_gen = (context_generation, load_generation)
            if getattr(video_area, "_exported_media_generation", None) == expected_gen:
                video_area._first_media_frame_exported = False
                setattr(video_area, "_exported_media_generation", None)


def _verify_media_frame_identity(win: Any, media_frame: Any) -> bool:
    mpv_obj = getattr(win, "mpv", None)
    if mpv_obj is None and hasattr(win, "player"):
        mpv_obj = getattr(win.player, "mpv", None)
    if mpv_obj is None:
        return False

    try:
        current_token = None
        path = get_mpv_prop(mpv_obj, "path")
        if isinstance(path, str) and path.strip():
            canonical_path = os.path.realpath(os.path.abspath(path))
            current_token = hashlib.sha256(os.fsencode(canonical_path)).hexdigest()
        else:
            current_evidence = media_frame_evidence(mpv_obj)
            if current_evidence is not None and getattr(current_evidence, "path_token", None):
                current_token = current_evidence.path_token

        return bool(current_token and current_token == media_frame.path_token)
    except Exception:
        logging.exception("Failed media identity verification on first media frame callback")
        return False


def handle_first_media_frame_published(
    win: Any,
    media_frame: Any,
    context_generation: int | None = None,
    load_generation: int | None = None,
) -> None:
    """Export diagnostics after validating generations and media identity."""
    if getattr(win, "_shutting_down", False):
        return
    exporter = getattr(win, "_diagnostics_exporter", None)
    if exporter is None or getattr(exporter, "export_completed", False) is True:
        return

    video_area = getattr(win, "_video_area", None) or getattr(win, "gl_area", None)
    current_ctx_gen = getattr(video_area, "render_context_generation", None) if video_area else None
    current_load_gen = getattr(win, "_media_load_generation", 0)

    if context_generation is not None and current_ctx_gen is not None and current_ctx_gen != context_generation:
        return
    if load_generation is not None and current_load_gen != load_generation:
        return

    if not hasattr(media_frame, "path_token") or not media_frame.path_token:
        _reset_current_area_state(win, video_area, context_generation, load_generation)
        return

    if not _verify_media_frame_identity(win, media_frame):
        _reset_current_area_state(win, video_area, context_generation, load_generation)
        return

    latest_ctx_gen = getattr(video_area, "render_context_generation", None) if video_area else None
    latest_load_gen = getattr(win, "_media_load_generation", 0)
    if (
        getattr(win, "_shutting_down", False)
        or (context_generation is not None and latest_ctx_gen is not None and latest_ctx_gen != context_generation)
        or (load_generation is not None and latest_load_gen != load_generation)
    ):
        return

    try:
        exporter.on_first_media_frame(
            win,
            media_frame,
            context_generation=context_generation,
            load_generation=load_generation,
        )
    except Exception:
        logging.exception("Failed to export diagnostics snapshot on first media frame")


def handle_first_video_frame(win: Any) -> None:
    """Export diagnostics on the first rendered frame when enabled."""
    if getattr(win, "_shutting_down", False):
        return
    exporter = getattr(win, "_diagnostics_exporter", None)
    if exporter is None or getattr(exporter, "export_completed", False) is True:
        return
    try:
        exporter.on_first_frame(win)
    except Exception:
        logging.exception("Failed to export diagnostics snapshot on first frame")


def notify_diagnostics_shutdown(win: Any) -> None:
    """Flush diagnostics exporter state cleanly during window close."""
    if win._diagnostics_exporter:
        try:
            win._diagnostics_exporter.on_shutdown(win)
        except Exception:
            logging.exception("Failed to record diagnostics snapshot on shutdown")
