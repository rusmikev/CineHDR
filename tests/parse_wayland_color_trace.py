#!/usr/bin/env python3
"""Strict, passive evidence parser for Wayland color-management submission.

The input is ``WAYLAND_DEBUG=client`` output merged with CineHDR's normal log.
Only canonical libwayland object-call records are interpreted; unrelated text is
ignored. This module never connects to, owns, or sends requests on Wayland.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
import re
import sys
from typing import Any


TRACE_SCHEMA = "cinehdr.gate2.wayland-color-management-trace.v1"
TRACE_REVISION = "wayland-client-submission-v1"
SCOPE = (
    "PASS proves a ready image description was submitted and committed by the "
    "Wayland client without a traced protocol rejection. It does not prove "
    "compositor transformation, display mode, luminance, pixels, or color accuracy."
)
FAIL_SCOPE = (
    "FAIL records hash-linked client-trace inputs that did not satisfy the "
    "Wayland color-management submission contract. It is not acceptance "
    "evidence and does not prove compositor or display behavior."
)


class TraceValidationError(ValueError):
    """The trace does not prove the accepted passive protocol contract."""


@dataclass(frozen=True)
class TraceCall:
    index: int
    interface: str
    object_id: int
    method: str
    args: str


CALL_RE = re.compile(
    # libwayland's canonical debug grammar uses ``interface#id``. Keep ``@``
    # as a compatibility tolerance for older captured logs, never as a test
    # fixture primary.
    r"(?P<interface>[A-Za-z_][A-Za-z0-9_]*)(?:#|@)(?P<object_id>\d+)\."
    r"(?P<method>[A-Za-z_][A-Za-z0-9_]*)\((?P<args>.*)\)"
)
GLOBAL_RE = re.compile(
    r'^\s*(?P<global_id>\d+)\s*,\s*"wp_color_manager_v1"\s*,\s*\d+\s*$'
)
BIND_RE = re.compile(
    r'^\s*(?P<global_id>\d+)\s*,\s*"wp_color_manager_v1"\s*,\s*\d+\s*,\s*'
    r"new id (?:wp_color_manager_v1|\[unknown\])(?:#|@)(?P<manager_id>\d+)\s*$"
)
GET_SURFACE_RE = re.compile(
    r"^\s*new id (?:wp_color_management_surface_v1|\[unknown\])(?:#|@)"
    r"(?P<color_surface_id>\d+)\s*,\s*wl_surface(?:#|@)(?P<wl_surface_id>\d+)\s*$"
)
SET_DESCRIPTION_RE = re.compile(
    r"^\s*wp_image_description_v1(?:#|@)(?P<description_id>\d+)\s*,\s*0\s*$"
)
UINT_RE = r"(?:0x[0-9A-Fa-f]+|[0-9]+)"
READY_RE = re.compile(rf"^\s*(?P<identity>{UINT_RE})\s*$")
READY2_RE = re.compile(
    rf"^\s*(?P<identity_hi>{UINT_RE})\s*,\s*"
    rf"(?P<identity_lo>{UINT_RE})\s*$"
)
FATAL_RE = re.compile(
    r"(?:\bwl_display(?:#|@)\d+\.error\(|\bwp_image_description_v1(?:#|@)\d+\.failed\(|"
    r"\bfatal\b|\bprotocol error\b|\bdisconnected(?: from display)?\b)",
    re.IGNORECASE,
)


def _calls(trace: str) -> list[TraceCall]:
    """Extract libwayland calls while preserving the original line ordering."""
    calls: list[TraceCall] = []
    for index, line in enumerate(trace.splitlines()):
        match = CALL_RE.search(line)
        if match is None:
            continue
        calls.append(
            TraceCall(
                index=index,
                interface=match["interface"],
                object_id=int(match["object_id"]),
                method=match["method"],
                args=match["args"].strip(),
            )
        )
    return calls


def _fatal_error(trace: str) -> TraceValidationError | None:
    for index, line in enumerate(trace.splitlines()):
        if FATAL_RE.search(line):
            return TraceValidationError(
                f"trace contains fatal/protocol rejection evidence at line {index}"
            )
    return None


def _description_ready_before(
    calls: list[TraceCall], description_id: int, before_index: int
) -> tuple[TraceCall, dict[str, object]] | None:
    """Accept ready(identity) or v2+ ready2(hi, lo) before submission."""
    for call in calls:
        if (
            call.interface != "wp_image_description_v1"
            or call.object_id != description_id
            or call.index >= before_index
        ):
            continue
        if call.method == "ready":
            match = READY_RE.match(call.args)
            if match is None:
                continue
            identity = int(match["identity"], 0)
            if identity > 0xFFFFFFFF:
                continue
            return call, {
                "event": "ready",
                "identity": identity,
                "identity_hi": None,
                "identity_lo": None,
                "identity_64": None,
            }
        if call.method == "ready2":
            match = READY2_RE.match(call.args)
            if match is None:
                continue
            identity_hi = int(match["identity_hi"], 0)
            identity_lo = int(match["identity_lo"], 0)
            if (
                identity_hi > 0xFFFFFFFF
                or identity_lo > 0xFFFFFFFF
                or (identity_hi == 0 and identity_lo == 0)
            ):
                continue
            return call, {
                "event": "ready2",
                "identity": None,
                "identity_hi": identity_hi,
                "identity_lo": identity_lo,
                "identity_64": (identity_hi << 32) | identity_lo,
            }
    return None


def _same_surface_commit_after(
    calls: list[TraceCall], wl_surface_id: int, after_index: int
) -> TraceCall | None:
    return next(
        (
            call
            for call in calls
            if call.interface == "wl_surface"
            and call.object_id == wl_surface_id
            and call.method == "commit"
            and call.args == ""
            and call.index > after_index
        ),
        None,
    )


def validate_trace(trace: str) -> dict[str, Any]:
    """Return deterministic positive evidence or raise on every missing link."""
    fatal = _fatal_error(trace)
    if fatal is not None:
        raise fatal
    calls = _calls(trace)
    globals_ = [
        (call, GLOBAL_RE.match(call.args))
        for call in calls
        if call.interface == "wl_registry" and call.method == "global"
    ]
    manager_globals = [
        (call, int(match["global_id"]))
        for call, match in globals_
        if match is not None
    ]
    if not manager_globals:
        raise TraceValidationError("wp_color_manager_v1 was not advertised by the registry")

    global_indexes = {global_id: call.index for call, global_id in manager_globals}
    bindings: list[tuple[TraceCall, int, int]] = []
    for call in calls:
        if call.interface != "wl_registry" or call.method != "bind":
            continue
        match = BIND_RE.match(call.args)
        if match is None:
            continue
        global_id = int(match["global_id"])
        manager_id = int(match["manager_id"])
        if global_id in global_indexes and global_indexes[global_id] < call.index:
            bindings.append((call, global_id, manager_id))
    if not bindings:
        raise TraceValidationError("wp_color_manager_v1 was advertised but not bound")

    for bind, global_id, manager_id in bindings:
        perceptual = next(
            (
                call
                for call in calls
                if call.interface == "wp_color_manager_v1"
                and call.object_id == manager_id
                and call.method == "supported_intent"
                and call.args == "0"
                and call.index > bind.index
            ),
            None,
        )
        if perceptual is None:
            continue
        done = next(
            (
                call
                for call in calls
                if call.interface == "wp_color_manager_v1"
                and call.object_id == manager_id
                and call.method == "done"
                and call.args == ""
                and call.index > perceptual.index
            ),
            None,
        )
        if done is None:
            continue

        surfaces: list[tuple[TraceCall, int, int]] = []
        for call in calls:
            if (
                call.interface != "wp_color_manager_v1"
                or call.object_id != manager_id
                or call.method != "get_surface"
                or call.index <= bind.index
            ):
                continue
            match = GET_SURFACE_RE.match(call.args)
            if match is None:
                continue
            surfaces.append(
                (
                    call,
                    int(match["color_surface_id"]),
                    int(match["wl_surface_id"]),
                )
            )
        for get_surface, color_surface_id, wl_surface_id in surfaces:
            for set_description in calls:
                if (
                    set_description.interface != "wp_color_management_surface_v1"
                    or set_description.object_id != color_surface_id
                    or set_description.method != "set_image_description"
                    or set_description.index <= get_surface.index
                ):
                    continue
                description_match = SET_DESCRIPTION_RE.match(set_description.args)
                if description_match is None:
                    continue
                description_id = int(description_match["description_id"])
                ready = _description_ready_before(
                    calls, description_id, set_description.index
                )
                if ready is None:
                    continue
                ready_call, readiness = ready
                commit = _same_surface_commit_after(
                    calls, wl_surface_id, set_description.index
                )
                if commit is None:
                    continue
                return {
                    "schema": TRACE_SCHEMA,
                    "revision": TRACE_REVISION,
                    "status": "PASS",
                    "scope": SCOPE,
                    "matched": {
                        "registry_global": {
                            "id": global_id,
                            "event_index": global_indexes[global_id],
                        },
                        "manager": {
                            "id": manager_id,
                            "bind_index": bind.index,
                            "perceptual_intent_index": perceptual.index,
                            "done_index": done.index,
                        },
                        "surface": {
                            "color_management_surface_id": color_surface_id,
                            "wl_surface_id": wl_surface_id,
                            "get_surface_index": get_surface.index,
                            "set_image_description_index": set_description.index,
                            "commit_index": commit.index,
                        },
                        "image_description": {
                            "id": description_id,
                            "ready_index": ready_call.index,
                            "readiness": readiness,
                        },
                    },
                }

    raise TraceValidationError(
        "no bound wp_color_manager_v1 proved perceptual intent, done, ready "
        "image-description submission, and a later commit on the same wl_surface"
    )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _input_reference(path: Path) -> dict[str, str]:
    """Return only a safe basename and content hash for a readable input."""
    return {"path": path.name, "sha256": _sha256_file(path)}


def _safe_failure_reason(error: TraceValidationError) -> str:
    """Keep CLI failure reports diagnostic without echoing input contents/paths."""
    reason = " ".join(str(error).split())
    if not reason or "/" in reason or "\\" in reason:
        return "Wayland color-management trace evidence was rejected"
    return reason[:240]


def create_evidence_report(
    trace_path: Path, publication_report_path: Path, backend: str
) -> dict[str, Any]:
    """Validate trace and prerequisite publication report without copying either."""
    if backend not in {"legacy", "gpu-next"}:
        raise TraceValidationError(f"unsupported publication backend: {backend!r}")
    report_lines = publication_report_path.read_text(encoding="utf-8").splitlines()
    if "Overall result: PASS" not in report_lines:
        raise TraceValidationError("publication prerequisite report did not PASS")
    if f"Expected backend: {backend}" not in report_lines:
        raise TraceValidationError("publication prerequisite report backend did not match")
    evidence = validate_trace(trace_path.read_text(encoding="utf-8", errors="replace"))
    evidence.update(
        {
            "backend": backend,
            "publication_report": _input_reference(publication_report_path),
            "trace": _input_reference(trace_path),
        }
    )
    return evidence


def create_failure_evidence(
    trace_path: Path,
    publication_report_path: Path,
    backend: str,
    error: TraceValidationError,
) -> dict[str, Any]:
    """Build hash-linked failure evidence without exposing a successful chain.

    This intentionally reads both inputs again. If either is no longer safely
    readable, callers must not emit a misleading hash-linked failure report.
    """
    return {
        "schema": TRACE_SCHEMA,
        "revision": TRACE_REVISION,
        "status": "FAIL",
        "backend": backend,
        "scope": FAIL_SCOPE,
        "error": _safe_failure_reason(error),
        "publication_report": _input_reference(publication_report_path),
        "trace": _input_reference(trace_path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate passive WAYLAND_DEBUG color-management evidence"
    )
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--publication-report", type=Path, required=True)
    parser.add_argument("--backend", choices=("legacy", "gpu-next"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        evidence = create_evidence_report(
            args.trace, args.publication_report, args.backend
        )
        args.output.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except TraceValidationError as error:
        print(f"Wayland color-management trace rejected: {error}", file=sys.stderr)
        try:
            failure_evidence = create_failure_evidence(
                args.trace, args.publication_report, args.backend, error
            )
            args.output.write_text(
                json.dumps(failure_evidence, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except (OSError, UnicodeError):
            pass
        return 1
    except (OSError, UnicodeError) as error:
        print(f"Wayland color-management trace rejected: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
