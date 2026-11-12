# diagnostics_report.py
#
# Copyright 2026 rusmikev / Diego Povliuk
# SPDX-License-Identifier: GPL-3.0-or-later

"""Compatibility facade re-exporting diagnostics collectors and formatters."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .diagnostics_facts import (
    DiagnosticSnapshot as DiagnosticSnapshot,
    collect_performance_facts as collect_performance_facts,
    collect_video_output_facts as collect_video_output_facts,
    get_dovi_rpu_warning_state as get_dovi_rpu_warning_state,
    get_mpv_prop as get_mpv_prop,
    read_flatpak_info as read_flatpak_info,
)
from .diagnostics_format import (
    DIAGNOSTICS_EXPORT_DIR_ENV as DIAGNOSTICS_EXPORT_DIR_ENV,
    DIAGNOSTICS_EXPORT_PATH_ENV as DIAGNOSTICS_EXPORT_PATH_ENV,
    DIAGNOSTICS_SESSION_ID_ENV as DIAGNOSTICS_SESSION_ID_ENV,
    EXPECTED_APP_COMMIT_ENV as EXPECTED_APP_COMMIT_ENV,
    EXPECTED_FLATPAK_INSTANCE_ENV as EXPECTED_FLATPAK_INSTANCE_ENV,
    EXPECTED_RUNTIME_COMMIT_ENV as EXPECTED_RUNTIME_COMMIT_ENV,
    _MEDIA_PATH_FIELDS as _MEDIA_PATH_FIELDS,
    _hdr_status_text as _hdr_status_text,
    _positive_float as _positive_float,
    _single_line as _single_line,
    _source_peak_nits as _source_peak_nits,
    build_video_output_report as build_video_output_report,
    format_dovi_rpu_warnings as format_dovi_rpu_warnings,
)


def generate_video_output_report(
    win: Any,
    gl_area: Any = None,
    mpv: Any = None,
    session_identity: Mapping[str, object] | None = None,
    sample_time_utc: str | None = None,
) -> str:
    """Convenience helper: collect facts and format the complete text report."""
    snapshot = collect_video_output_facts(win, gl_area, mpv, sample_time_utc=sample_time_utc)
    return build_video_output_report(
        snapshot.renderer,
        snapshot.output,
        snapshot.video,
        performance=snapshot.performance,
        session_identity=session_identity,
    )
