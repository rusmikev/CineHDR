# diagnostics_report.py
#
# Copyright 2026 rusmikev / Diego Povliuk
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pure formatting helpers for copyable CineHDR diagnostics."""

from __future__ import annotations

from collections.abc import Mapping


_MEDIA_PATH_FIELDS = {"media path", "media_path", "file", "filename"}


def _single_line(value: object) -> str:
    if value is None or value == "":
        return "unknown"
    return str(value).replace("\r", " ").replace("\n", " ").strip()


def build_video_output_report(
    renderer: Mapping[str, object],
    output: Mapping[str, object],
    video: Mapping[str, object],
) -> str:
    """Build a stable plain-text report without exposing the media path."""
    lines = ["CineHDR Video Output Diagnostics"]
    for title, fields in (
        ("Video Renderer", renderer),
        ("Output & Color State", output),
        ("Video Signal", video),
    ):
        lines.extend(("", f"[{title}]"))
        for name, value in fields.items():
            if name.strip().lower() in _MEDIA_PATH_FIELDS:
                continue
            lines.append(f"{name}: {_single_line(value)}")
    return "\n".join(lines) + "\n"
