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
MULTIPHASE_REVISION = "wayland-multiphase-publication-v1"
PRIMARY_COMPOSITE_REVISION = "wayland-composite-window-hdr-submission-v1"
SCOPE = (
    "PASS proves a ready image description was submitted and committed by the "
    "Wayland client without a traced protocol rejection. It does not prove "
    "compositor transformation, display mode, luminance, pixels, or color accuracy."
)
PRIMARY_COMPOSITE_SCOPE = (
    "PASS proves a recognized HDR image description was submitted and committed on CineHDR's "
    "dynamically identified composite window surface without a traced protocol rejection, "
    "satisfying ADR-0004. It does not prove continuous HDR, pixel correspondence to description, "
    "compositor transformation, display mode, luminance, or physical display output."
)
MULTIPHASE_SCOPE = (
    "Multiphase PASS proves 4 sequential ready image descriptions were submitted and "
    "committed on the proven video surface. UNKNOWN records that client-side toplevel "
    "submission occurred but video subsurface/offload identity cannot be distinguished "
    "from the window."
)
FAIL_SCOPE = (
    "FAIL records hash-linked client-trace inputs that did not satisfy the "
    "Wayland color-management submission contract. It is not acceptance "
    "evidence and does not prove compositor or display behavior."
)
UNKNOWN_SCOPE = (
    "UNKNOWN records that client-side Wayland color-management trace evidence "
    "was incomplete, ambiguous, or unverified. It is not acceptance evidence "
    "and does not prove compositor or physical display behavior."
)
TRACE_ONLY_L3P_SCOPE = (
    "L3p protocol evidence: PASS proves a recognized HDR image description was submitted "
    "and committed on the target application's Wayland window surface with a verified non-zero "
    "buffer attach and no subsurface offload ambiguity, without a traced protocol rejection. "
    "Attribution by app_id proves window ownership by the application but does not alone prove "
    "that the window carries video without a verified buffer attach. Launch parameters (e.g. mpv "
    "command-line flags) require independent confirmation from the runtime log (vo=v). "
    "PASS does not prove compositor transformation, display mode, luminance, pixels, or physical HDR display output."
)
TRACE_ONLY_UNKNOWN_SCOPE = (
    "L3p protocol evidence: UNKNOWN records that client-side Wayland color-management "
    "trace evidence was incomplete, ambiguous, or could not establish an unambiguous "
    "video surface. It is not acceptance evidence and does not prove compositor or "
    "physical display behavior."
)
TRACE_ONLY_FAIL_SCOPE = (
    "L3p protocol evidence: FAIL records a hash-linked raw client trace that did not "
    "satisfy the Wayland color-management submission contract. It is not acceptance "
    "evidence and does not prove compositor or physical display behavior."
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
    conn_id: int | None = None
    fd: int | None = None


CONN_TAG_RE = re.compile(
    r"^\s*\[conn#(?P<conn_id>\d+)(?:\s+fd#(?P<fd>\d+))?\]"
)
UNTRACKED_TAG_RE = re.compile(
    r"^\s*\[conn#(?:untracked|unknown)"
)

CALL_RE = re.compile(
    # libwayland's canonical debug grammar uses ``interface#id``. Keep ``@``
    # as a compatibility tolerance for older captured logs, never as a test
    # fixture primary.
    r"(?P<interface>[A-Za-z_][A-Za-z0-9_]*)(?:#|@)(?P<object_id>\d+)\."
    r"(?P<method>[A-Za-z_][A-Za-z0-9_]*)\((?P<args>.*)\)"
)
GLOBAL_RE = re.compile(
    r'^\s*(?P<global_id>\d+)\s*,\s*"wp_color_manager_v1"\s*,\s*(?P<version>\d+)\s*$'
)
BIND_RE = re.compile(
    r'^\s*(?P<global_id>\d+)\s*,\s*"wp_color_manager_v1"\s*,\s*(?P<version>\d+)\s*,\s*'
    r"new id (?:wp_color_manager_v1|\[unknown\])(?:#|@)(?P<manager_id>\d+)\s*$"
)
NEW_ID_RE = re.compile(
    r"\bnew id (?P<interface>[A-Za-z_][A-Za-z0-9_]*)(?:#|@)(?P<object_id>\d+)\b"
)
UNKNOWN_NEW_ID_RE = re.compile(r"\bnew id \[unknown\](?:#|@)(?P<object_id>\d+)\b")
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
GET_XDG_SURFACE_RE = re.compile(
    r"^\s*new id (?:xdg_surface|\[unknown\])(?:#|@)(?P<xdg_surface_id>\d+)\s*,\s*"
    r"wl_surface(?:#|@)(?P<wl_surface_id>\d+)\s*$"
)
GET_TOPLEVEL_RE = re.compile(
    r"^\s*new id (?:xdg_toplevel|\[unknown\])(?:#|@)(?P<toplevel_id>\d+)\s*$"
)
SET_APP_ID_RE = re.compile(r'^\s*"(?P<app_id>[^"]+)"\s*$')
GET_SUBSURFACE_RE = re.compile(
    r"^\s*new id (?:wl_subsurface|\[unknown\])(?:#|@)(?P<subsurface_id>\d+)\s*,\s*"
    r"wl_surface(?:#|@)(?P<child_surface_id>\d+)\s*,\s*wl_surface(?:#|@)(?P<parent_surface_id>\d+)\s*$"
)
ATTACH_RE = re.compile(
    r"^\s*(?:wl_buffer(?:#|@)(?P<buffer_id>\d+)|(?P<null>nil|NULL|null|0))\s*,\s*(?P<x>-?\d+)\s*,\s*(?P<y>-?\d+)\s*$"
)
GET_VIEWPORT_RE = re.compile(
    r"^\s*new id (?:wp_viewport|\[unknown\])(?:#|@)(?P<viewport_id>\d+)\s*,\s*"
    r"wl_surface(?:#|@)(?P<wl_surface_id>\d+)\s*$"
)
CREATE_PARAMETRIC_RE = re.compile(
    r"^\s*new id (?:wp_image_description_creator_params_v1|\[unknown\])(?:#|@)(?P<creator_id>\d+)\s*$"
)
SET_PRIMARIES_NAMED_RE = re.compile(r"^\s*(?P<primaries>\d+)\s*$")
SET_TF_NAMED_RE = re.compile(r"^\s*(?P<tf>\d+)\s*$")
CREATE_DESC_RE = re.compile(
    r"^\s*new id (?:wp_image_description_v1|\[unknown\])(?:#|@)(?P<description_id>\d+)\s*$"
)
GET_INFO_RE = re.compile(
    r"^\s*new id (?:wp_image_description_info_v1|\[unknown\])(?:#|@)(?P<info_id>\d+)\s*$"
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
        m_tag = CONN_TAG_RE.match(line.strip())
        conn_id = int(m_tag.group("conn_id")) if m_tag else None
        fd = int(m_tag.group("fd")) if m_tag and m_tag.group("fd") else None
        calls.append(
            TraceCall(
                index=index,
                interface=match["interface"],
                object_id=int(match["object_id"]),
                method=match["method"],
                args=match["args"].strip(),
                conn_id=conn_id,
                fd=fd,
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


def _description_ready_between(
    calls: list[TraceCall], description_id: int, after_index: int, before_index: int
) -> tuple[TraceCall, dict[str, object]] | None:
    """Accept ready(identity) or v2+ ready2(hi, lo) strictly between creation and submission."""
    for call in calls:
        if (
            call.interface != "wp_image_description_v1"
            or call.object_id != description_id
            or call.index <= after_index
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


def _is_valid_manager_generation(
    bindings: list[tuple[TraceCall, TraceCall, int, int, int, int]],
    bind: TraceCall,
    manager_id: int,
    calls: list[TraceCall],
) -> tuple[bool, int]:
    """Verify that this binding represents an isolated, non-overlapping generation of manager_id.

    Returns (is_valid, mgr_end_index).
    If an earlier generation of manager_id was not destroyed or deleted before bind.index,
    or if a subsequent generation starts before this generation terminates, or if calls
    occur before creation, is_valid is False (rejecting concurrent ID reuse and foreign overlaps).
    """
    all_manager_binds = [
        c
        for c in calls
        if c.interface == "wl_registry"
        and c.method == "bind"
        and (m := BIND_RE.match(c.args)) is not None
        and int(m["manager_id"]) == manager_id
    ]

    prior_binds = [c for c in all_manager_binds if c.index < bind.index]
    prior_death: int | None = None
    if prior_binds:
        last_prior = prior_binds[-1]
        prior_destroy = next(
            (
                c.index
                for c in calls
                if c.interface == "wp_color_manager_v1"
                and c.object_id == manager_id
                and c.method == "destroy"
                and last_prior.index < c.index < bind.index
            ),
            None,
        )
        prior_delete = next(
            (
                c.index
                for c in calls
                if c.interface == "wl_display"
                and c.method == "delete_id"
                and c.args == str(manager_id)
                and last_prior.index < c.index < bind.index
            ),
            None,
        )
        if prior_destroy is None and prior_delete is None:
            return False, 0
        prior_death = prior_destroy if prior_destroy is not None else prior_delete

    next_binds = [c for c in all_manager_binds if c.index > bind.index]
    next_boundary = next_binds[0].index if next_binds else len(calls)

    mgr_destroy = next(
        (
            c.index
            for c in calls
            if c.interface == "wp_color_manager_v1"
            and c.object_id == manager_id
            and c.method == "destroy"
            and bind.index < c.index < next_boundary
        ),
        None,
    )
    mgr_delete = next(
        (
            c.index
            for c in calls
            if c.interface == "wl_display"
            and c.method == "delete_id"
            and c.args == str(manager_id)
            and bind.index < c.index < next_boundary
        ),
        None,
    )

    if next_binds and mgr_destroy is None and mgr_delete is None:
        return False, 0

    if mgr_destroy is not None or mgr_delete is not None:
        mgr_end_index = min(idx for idx in (mgr_destroy, mgr_delete) if idx is not None)
    else:
        mgr_end_index = next_boundary

    # Any call to wp_color_manager_v1#manager_id after its termination and before next bind is invalid
    if any(
        c.interface == "wp_color_manager_v1"
        and c.object_id == manager_id
        and mgr_end_index < c.index < next_boundary
        for c in calls
    ):
        return False, 0

    start_boundary = prior_death if prior_death is not None else -1
    if any(
        c.interface == "wp_color_manager_v1"
        and c.object_id == manager_id
        and start_boundary < c.index < bind.index
        for c in calls
    ):
        return False, 0

    return True, mgr_end_index


def _has_active_id_collision(
    calls: list[TraceCall],
    creations_by_id: dict[int, list[tuple[str, int]]],
    object_id: int,
) -> tuple[bool, str | None]:
    """Check if object_id has concurrent active lifetimes across any interfaces."""
    cre_list = creations_by_id.get(object_id, [])
    if len(cre_list) <= 1:
        return False, None
    sorted_cres = sorted(cre_list, key=lambda x: x[1])
    for i in range(len(sorted_cres) - 1):
        if1, c1 = sorted_cres[i]
        if2, c2 = sorted_cres[i + 1]
        destroyed = any(
            c.object_id == object_id
            and c.method in ("destroy", "destroy_registry", "release")
            and c1 < c.index < c2
            for c in calls
        )
        deleted = any(
            c.interface == "wl_display"
            and c.method == "delete_id"
            and c.args == str(object_id)
            and c1 < c.index < c2
            for c in calls
        )
        if not destroyed and not deleted:
            return (
                True,
                f"concurrent active lifetime collision detected for object #{object_id} ({if1} and {if2})",
            )
    return False, None


def _find_registry_creations(
    calls: list[TraceCall], reg_id: int
) -> tuple[list[TraceCall], list[TraceCall]]:
    """Return lists of get_registry calls creating reg_id: (disp1_creations, other_disp_creations)."""
    disp1_creations: list[TraceCall] = []
    other_disp_creations: list[TraceCall] = []
    for c in calls:
        if c.method == "get_registry":
            m = NEW_ID_RE.search(c.args) or UNKNOWN_NEW_ID_RE.search(c.args)
            if m and int(m.group("object_id")) == reg_id:
                if c.interface == "wl_display" and c.object_id == 1:
                    disp1_creations.append(c)
                else:
                    other_disp_creations.append(c)
    return disp1_creations, other_disp_creations


def _check_registry_generations(
    calls: list[TraceCall], reg_id: int, disp1_creations: list[TraceCall]
) -> tuple[bool, str | None, bool]:
    """Verify that multiple creations of a registry ID are properly terminated."""
    has_unterminated = False
    for i in range(len(disp1_creations) - 1):
        c1 = disp1_creations[i]
        c2 = disp1_creations[i + 1]
        deleted = any(
            c.interface == "wl_display"
            and c.method == "delete_id"
            and c.args == str(reg_id)
            and c1.index < c.index < c2.index
            for c in calls
        )
        if not deleted:
            has_unterminated = True
            break
    if has_unterminated:
        return (
            False,
            f"multiple active creations of wl_registry#{reg_id} observed in trace without termination: "
            "connection belonging cannot be established in unified WAYLAND_DEBUG",
            True,
        )
    return (
        False,
        f"multiple generations of wl_registry#{reg_id} observed in trace: "
        "registry generation cannot be proven in unified WAYLAND_DEBUG",
        True,
    )


def _check_single_registry_origin(
    calls: list[TraceCall], reg_id: int, *, is_cross: bool
) -> tuple[bool, str | None, bool, TraceCall | None]:
    """Verify origin, lifetime, and usage order of a single registry ID."""
    disp1_creations, other_disp_creations = _find_registry_creations(calls, reg_id)

    if other_disp_creations and not disp1_creations:
        disp_info = f"{other_disp_creations[0].interface}#{other_disp_creations[0].object_id}"
        return (
            False,
            f"wl_registry#{reg_id} was not created by wl_display#1 (observed: {disp_info}.get_registry)",
            False,
            None,
        )

    if not disp1_creations:
        if is_cross:
            return False, f"wl_registry#{reg_id} was not created by wl_display on this connection", False, None
        return (
            False,
            f"creation of wl_registry#{reg_id} by wl_display#1 was not observed in trace: "
            "registry origin cannot be proven",
            True,
            None,
        )

    if len(disp1_creations) > 1:
        ok_gen, err_gen, unp_gen = _check_registry_generations(calls, reg_id, disp1_creations)
        return ok_gen, err_gen, unp_gen, None

    creation_call = disp1_creations[0]
    if any(
        c.interface == "wl_registry"
        and c.object_id == reg_id
        and c.index < creation_call.index
        for c in calls
    ):
        return (
            False,
            f"wl_registry#{reg_id} was used before its creation at index {creation_call.index}",
            False,
            None,
        )

    return True, None, False, creation_call


def _validate_cross_registry_tags(
    reg_a: int, creat_a: TraceCall, reg_b: int, creat_b: TraceCall
) -> tuple[bool, str | None, bool]:
    """Verify that creation calls for two distinct registries share connection ID and socket fd."""
    if creat_a.conn_id is None or creat_a.fd is None:
        return (
            False,
            f"creation of wl_registry#{reg_a} lacks verified connection or fd tag",
            True,
        )
    if creat_b.conn_id is None or creat_b.fd is None:
        return (
            False,
            f"creation of wl_registry#{reg_b} lacks verified connection or fd tag",
            True,
        )
    if creat_a.conn_id != creat_b.conn_id:
        return (
            False,
            f"wl_registry#{reg_a} (conn#{creat_a.conn_id}) and wl_registry#{reg_b} (conn#{creat_b.conn_id}) belong to different connections",
            True,
        )
    if creat_a.fd != creat_b.fd:
        return (
            False,
            f"wl_registry#{reg_a} (fd#{creat_a.fd}) and wl_registry#{reg_b} (fd#{creat_b.fd}) have different socket file descriptors",
            True,
        )
    return True, None, False


def _validate_connection_call_consistency(
    calls: list[TraceCall], reg_a: int, reg_b: int, expected_conn: int, expected_fd: int
) -> tuple[bool, str | None, bool]:
    """Verify that all calls on the registries, display, and entire trace match the expected connection."""
    for c in calls:
        if (
            (c.interface == "wl_registry" and c.object_id in (reg_a, reg_b))
            or (c.interface == "wl_display" and c.object_id == 1)
        ):
            if c.conn_id is None or c.fd is None:
                return (
                    False,
                    f"call {c.interface}#{c.object_id}.{c.method} at index {c.index} lacks verified connection or fd tag",
                    True,
                )
            if c.conn_id != expected_conn or c.fd != expected_fd:
                return (
                    False,
                    f"call {c.interface}#{c.object_id}.{c.method} at index {c.index} observed with inconsistent connection (conn#{c.conn_id} fd#{c.fd} vs conn#{expected_conn} fd#{expected_fd})",
                    True,
                )

    for c in calls:
        if c.conn_id is not None and (c.conn_id != expected_conn or (c.fd is not None and c.fd != expected_fd)):
            return (
                False,
                f"trace contains calls from multiple connections (conn#{c.conn_id} fd#{c.fd} vs conn#{expected_conn} fd#{expected_fd}): connection belonging cannot be established",
                True,
            )

    return True, None, False


def _verify_registry_connection_link(
    calls: list[TraceCall],
    reg_a: int,
    reg_b: int,
    manager_global_id: int,
    *,
    connection_tags_verified: bool = False,
) -> tuple[bool, str | None, bool]:
    """Verify that two registry IDs belong to the same proven connection.

    Returns (is_ok, reason, is_unproven_connection).
    """
    is_cross = (reg_a != reg_b)
    ok_a, err_a, unp_a, creat_a = _check_single_registry_origin(calls, reg_a, is_cross=is_cross)
    if not ok_a:
        return False, err_a, unp_a

    ok_b, err_b, unp_b, creat_b = _check_single_registry_origin(calls, reg_b, is_cross=is_cross)
    if not ok_b:
        return False, err_b, unp_b

    if reg_a == reg_b:
        return True, None, False

    if not connection_tags_verified:
        return (
            False,
            f"connection belonging between wl_registry#{reg_a} and wl_registry#{reg_b} cannot be established from trace: "
            "wl_display numeric ID does not prove a shared connection in unified WAYLAND_DEBUG",
            True,
        )

    assert creat_a is not None and creat_b is not None

    ok_tags, err_tags, unp_tags = _validate_cross_registry_tags(reg_a, creat_a, reg_b, creat_b)
    if not ok_tags:
        return False, err_tags, unp_tags

    assert creat_a.conn_id is not None and creat_a.fd is not None
    return _validate_connection_call_consistency(
        calls, reg_a, reg_b, creat_a.conn_id, creat_a.fd
    )


def _used_before_creation(
    calls: list[TraceCall], interface: str, object_id: int, creation_index: int
) -> bool:
    return any(
        call.interface == interface
        and call.object_id == object_id
        and call.index < creation_index
        for call in calls
    )


def _color_surface_generation_end(
    calls: list[TraceCall], creations: dict[tuple[str, int], list[int]],
    object_id: int, creation_index: int, *, connection_verified: bool,
) -> tuple[bool, int | None]:
    """Bound a color surface lifetime; reuse needs verified connection identity."""
    indices = creations.get(("wp_color_management_surface_v1", object_id), [])
    if len(indices) > 1 and not connection_verified:
        return False, None
    if creation_index not in indices or _used_before_creation(
        calls, "wp_color_management_surface_v1", object_id, indices[0]
    ):
        return False, None
    for previous, current in zip(indices, indices[1:]):
        destroyed = next((
            call.index for call in calls
            if call.interface == "wp_color_management_surface_v1"
            and call.object_id == object_id and call.method == "destroy"
            and previous < call.index < current
        ), None)
        if destroyed is None or not any(
            call.interface == "wl_display" and call.method == "delete_id"
            and call.args == str(object_id) and destroyed < call.index < current
            for call in calls
        ):
            return False, None
    end = next((
        call.index for call in calls
        if call.interface == "wp_color_management_surface_v1"
        and call.object_id == object_id and call.method == "destroy"
        and call.index > creation_index
    ), None)
    return True, end


def _is_created_by_manager(
    calls_by_index: dict[int, TraceCall],
    c_idx: int,
    manager_id: int,
    manager_creator_ids: set[int],
    color_surface_id: int,
) -> bool:
    """Verify that wp_image_description_v1 was created by manager_id, its creators, or color_surface_id."""
    c = calls_by_index.get(c_idx)
    if c is None:
        return False
    if c.interface in ("wp_image_description_creator_params_v1", "wp_image_description_creator_icc_v1"):
        return c.method == "create" and c.object_id in manager_creator_ids
    if c.interface == "wp_color_manager_v1":
        return c.method == "create_windows_scrgb" and c.object_id == manager_id
    if c.interface == "wp_color_management_surface_v1":
        return c.method == "get_image_description" and c.object_id == color_surface_id
    if c.interface == "wp_color_management_surface_feedback_v1":
        return c.method == "get_preferred_parametric" and c.object_id in manager_creator_ids
    if c.interface == "wp_color_management_output_v1":
        return c.method == "get_image_description" and c.object_id in manager_creator_ids
    return False


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
        (call, int(match["global_id"]), int(match["version"]))
        for call, match in globals_
        if match is not None
    ]
    if not manager_globals:
        raise TraceValidationError("wp_color_manager_v1 was not advertised by the registry")

    creations: dict[tuple[str, int], list[int]] = {}
    for call in calls:
        match = NEW_ID_RE.search(call.args)
        if match is not None:
            interface = match["interface"]
        else:
            match = UNKNOWN_NEW_ID_RE.search(call.args)
            interface = {
                ("wl_display", "get_registry"): "wl_registry",
                ("wl_compositor", "create_surface"): "wl_surface",
                ("wp_color_manager_v1", "get_surface"): "wp_color_management_surface_v1",
                ("wp_image_description_creator_params_v1", "create"): "wp_image_description_v1",
            }.get((call.interface, call.method))
        if match is not None and interface is not None:
            creations.setdefault((interface, int(match["object_id"])), []).append(call.index)

    bindings: list[tuple[TraceCall, TraceCall, int, int, int, int]] = []
    manager_bind_counts: dict[int, int] = {}
    for call in calls:
        if call.interface != "wl_registry" or call.method != "bind":
            continue
        match = BIND_RE.match(call.args)
        if match is None:
            continue
        global_id = int(match["global_id"])
        manager_id = int(match["manager_id"])
        manager_bind_counts[manager_id] = manager_bind_counts.get(manager_id, 0) + 1
        bound_version = int(match["version"])
        registry_creations = [
            index for index in creations.get(("wl_registry", call.object_id), [])
            if index < call.index
        ]
        # Reused registry IDs can belong to other Wayland connections. A bind
        # after a second creation cannot be assigned to one by numeric ID.
        if len(registry_creations) > 1:
            continue
        if registry_creations and any(
            earlier.interface == "wl_registry"
            and earlier.object_id == call.object_id
            and earlier.index < registry_creations[0]
            for earlier in calls
        ):
            continue
        advertisements = [
            (global_call, advertised_version)
            for global_call, advertised_id, advertised_version in manager_globals
            if global_call.object_id == call.object_id
            and advertised_id == global_id
            and global_call.index < call.index
            and (not registry_creations or global_call.index > registry_creations[0])
        ]
        if len(advertisements) != 1:
            continue
        global_call, advertised_version = advertisements[0]
        if not 1 <= bound_version <= advertised_version:
            continue
        bindings.append(
            (call, global_call, global_id, manager_id, advertised_version, bound_version)
        )
    if not bindings:
        raise TraceValidationError("wp_color_manager_v1 was advertised but not bound")

    for bind, global_call, global_id, manager_id, advertised_version, bound_version in bindings:
        valid_gen, mgr_end_index = _is_valid_manager_generation(bindings, bind, manager_id, calls)
        if not valid_gen:
            first_delete_after_bind = next(
                (
                    cd.index
                    for cd in calls
                    if cd.interface == "wl_display"
                    and cd.method == "delete_id"
                    and cd.args == str(manager_id)
                    and cd.index > bind.index
                ),
                None,
            )
            if first_delete_after_bind is not None and any(
                c.interface == "wp_color_manager_v1"
                and c.object_id == manager_id
                and c.index > first_delete_after_bind
                for c in calls
            ):
                raise TraceValidationError(
                    f"wp_color_manager_v1#{manager_id} was used after delete_id without a new bind"
                )
            continue
        perceptual = next(
            (
                call
                for call in calls
                if call.interface == "wp_color_manager_v1"
                and call.object_id == manager_id
                and call.method == "supported_intent"
                and call.args == "0"
                and bind.index < call.index <= mgr_end_index
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
                and perceptual.index < call.index <= mgr_end_index
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
                or not (bind.index < call.index <= mgr_end_index)
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
            wl_surface_creations = creations.get(("wl_surface", wl_surface_id), [])
            if (
                len(creations.get(("wp_color_management_surface_v1", color_surface_id), [])) != 1
                or _used_before_creation(
                    calls, "wp_color_management_surface_v1", color_surface_id,
                    get_surface.index,
                )
                or len(wl_surface_creations) > 1
                or (wl_surface_creations and wl_surface_creations[0] >= get_surface.index)
                or (
                    wl_surface_creations
                    and _used_before_creation(
                        calls, "wl_surface", wl_surface_id, wl_surface_creations[0]
                    )
                )
            ):
                continue
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
                description_creations = creations.get(
                    ("wp_image_description_v1", description_id), []
                )
                if len(description_creations) > 1:
                    continue
                if description_creations and _used_before_creation(
                    calls, "wp_image_description_v1", description_id,
                    description_creations[0],
                ):
                    continue
                ready = _description_ready_before(
                    calls, description_id, set_description.index
                )
                if ready is None:
                    continue
                ready_call, readiness = ready
                if description_creations and description_creations[0] >= ready_call.index:
                    continue
                commit = _same_surface_commit_after(
                    calls, wl_surface_id, set_description.index
                )
                if commit is None:
                    continue
                if any(
                    c.interface == "wp_color_management_surface_v1"
                    and c.object_id == color_surface_id
                    and c.method == "unset_image_description"
                    and set_description.index < c.index < commit.index
                    for c in calls
                ):
                    continue
                return {
                    "schema": TRACE_SCHEMA,
                    "revision": TRACE_REVISION,
                    "status": "PASS",
                    "scope": SCOPE,
                    "matched": {
                        "registry_global": {
                            "id": global_id,
                            "registry_id": global_call.object_id,
                            "event_index": global_call.index,
                            "advertised_version": advertised_version,
                        },
                        "manager": {
                            "id": manager_id,
                            "bind_index": bind.index,
                            "bound_version": bound_version,
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


def _surface_roles_and_attaches(
    calls: list[TraceCall],
) -> tuple[dict[int, str], dict[int, int], dict[int, list[int]], set[int]]:
    """Map wl_surface IDs to toplevel app_id, subsurface parent, attached buffer IDs, and viewport presence."""
    xdg_surface_to_wl: dict[int, int] = {}
    toplevel_to_xdg: dict[int, int] = {}
    toplevel_surfaces: dict[int, str] = {}  # wl_surface_id -> app_id
    subsurfaces: dict[int, int] = {}        # child_wl_surface_id -> parent_wl_surface_id
    surface_attaches: dict[int, list[int]] = {}  # wl_surface_id -> [buffer_id, ...]
    viewport_surfaces: set[int] = set()

    for call in calls:
        if call.interface == "xdg_wm_base" and call.method == "get_xdg_surface":
            match = GET_XDG_SURFACE_RE.match(call.args)
            if match is not None:
                xdg_surface_to_wl[int(match["xdg_surface_id"])] = int(match["wl_surface_id"])
        elif call.interface == "xdg_surface" and call.method == "get_toplevel":
            match = GET_TOPLEVEL_RE.match(call.args)
            if match is not None and call.object_id in xdg_surface_to_wl:
                toplevel_to_xdg[int(match["toplevel_id"])] = xdg_surface_to_wl[call.object_id]
        elif call.interface == "xdg_toplevel" and call.method == "set_app_id":
            match = SET_APP_ID_RE.match(call.args)
            if match is not None and call.object_id in toplevel_to_xdg:
                wl_surf = toplevel_to_xdg[call.object_id]
                toplevel_surfaces[wl_surf] = match["app_id"]
        elif call.interface == "wl_subcompositor" and call.method == "get_subsurface":
            match = GET_SUBSURFACE_RE.match(call.args)
            if match is not None:
                child = int(match["child_surface_id"])
                parent = int(match["parent_surface_id"])
                subsurfaces[child] = parent
        elif call.interface == "wl_surface" and call.method == "attach":
            match = ATTACH_RE.match(call.args)
            if match is not None:
                if match["buffer_id"] is not None:
                    buf_id = int(match["buffer_id"])
                else:
                    buf_id = 0
                surface_attaches.setdefault(call.object_id, []).append(buf_id)
        elif call.interface == "wp_viewporter" and call.method == "get_viewport":
            match = GET_VIEWPORT_RE.match(call.args)
            if match is not None:
                viewport_surfaces.add(int(match["wl_surface_id"]))

    return toplevel_surfaces, subsurfaces, surface_attaches, viewport_surfaces


def _find_toplevel_ancestor(
    wl_surface_id: int,
    subsurfaces: dict[int, int],
    toplevel_surfaces: dict[int, str],
) -> tuple[int | None, str | None]:
    """Traverse subsurface parent chain to locate the ancestor toplevel surface and its app_id."""
    curr = wl_surface_id
    seen = {curr}
    while curr in subsurfaces:
        curr = subsurfaces[curr]
        if curr in seen:
            break
        seen.add(curr)
        if curr in toplevel_surfaces:
            return curr, toplevel_surfaces[curr]
    if curr in toplevel_surfaces:
        return curr, toplevel_surfaces[curr]
    return None, None


def _surface_hierarchy_at_commit(
    calls: list[TraceCall],
    target_commit_index: int,
) -> tuple[dict[int, str], dict[int, int]]:
    """Determine toplevel surfaces (wl_surface -> app_id) and subsurface parentage

    (child_wl_surface -> parent_wl_surface) as effective at target_commit_index.
    """
    xdg_surface_to_wl: dict[int, int] = {}
    toplevel_to_xdg: dict[int, int] = {}
    toplevel_surfaces: dict[int, str] = {}
    subsurface_obj_to_child: dict[int, int] = {}
    subsurfaces: dict[int, int] = {}

    for call in calls:
        if call.index > target_commit_index:
            break

        if call.interface == "xdg_wm_base" and call.method == "get_xdg_surface":
            match = GET_XDG_SURFACE_RE.match(call.args)
            if match is not None:
                xdg_surface_to_wl[int(match["xdg_surface_id"])] = int(match["wl_surface_id"])
        elif call.interface == "xdg_surface" and call.method == "get_toplevel":
            match = GET_TOPLEVEL_RE.match(call.args)
            if match is not None and call.object_id in xdg_surface_to_wl:
                toplevel_to_xdg[int(match["toplevel_id"])] = xdg_surface_to_wl[call.object_id]
        elif call.interface == "xdg_toplevel" and call.method == "set_app_id":
            match = SET_APP_ID_RE.match(call.args)
            if match is not None and call.object_id in toplevel_to_xdg:
                wl_surf = toplevel_to_xdg[call.object_id]
                toplevel_surfaces[wl_surf] = match["app_id"]
        elif call.interface == "wl_subcompositor" and call.method == "get_subsurface":
            match = GET_SUBSURFACE_RE.match(call.args)
            if match is not None:
                sub_id = int(match["subsurface_id"])
                child = int(match["child_surface_id"])
                parent = int(match["parent_surface_id"])
                subsurfaces[child] = parent
                subsurface_obj_to_child[sub_id] = child
        elif call.interface == "wl_subsurface" and call.method == "destroy":
            sub_id = call.object_id
            child = subsurface_obj_to_child.pop(sub_id, None)
            if child is not None:
                subsurfaces.pop(child, None)
        elif call.interface == "wl_surface" and call.method == "destroy":
            surf_id = call.object_id
            toplevel_surfaces.pop(surf_id, None)
            subsurfaces.pop(surf_id, None)
            for c, p in list(subsurfaces.items()):
                if p == surf_id:
                    del subsurfaces[c]
        elif call.interface == "xdg_toplevel" and call.method == "destroy":
            if call.object_id in toplevel_to_xdg:
                wl_surf = toplevel_to_xdg.pop(call.object_id)
                toplevel_surfaces.pop(wl_surf, None)
        elif call.interface == "xdg_surface" and call.method == "destroy":
            if call.object_id in xdg_surface_to_wl:
                wl_surf = xdg_surface_to_wl.pop(call.object_id)
                toplevel_surfaces.pop(wl_surf, None)
        elif call.interface == "wl_display" and call.method == "delete_id":
            try:
                del_id = int(call.args.strip())
                child = subsurface_obj_to_child.pop(del_id, None)
                if child is not None:
                    subsurfaces.pop(child, None)
                if del_id in toplevel_to_xdg:
                    wl_surf = toplevel_to_xdg.pop(del_id)
                    toplevel_surfaces.pop(wl_surf, None)
                if del_id in xdg_surface_to_wl:
                    wl_surf = xdg_surface_to_wl.pop(del_id)
                    toplevel_surfaces.pop(wl_surf, None)
            except ValueError:
                pass

    return toplevel_surfaces, subsurfaces


def _buffer_state_at_commit(
    calls: list[TraceCall],
    target_surf: int,
    target_commit_index: int,
) -> tuple[int | None, int | None, bool]:
    """Determine effective current buffer and pending buffer state at target_commit_index.

    Returns (effective_current_buffer, pending_buffer, pending_attached).
    """
    current_buffer: int | None = None
    pending_buffer: int | None = None
    pending_attached = False

    for call in calls:
        if call.index > target_commit_index:
            break
        if call.interface == "wl_surface" and call.object_id == target_surf:
            if call.method == "destroy":
                current_buffer = None
                pending_buffer = None
                pending_attached = False
            elif call.method == "attach":
                match = ATTACH_RE.match(call.args)
                if match is not None:
                    pending_attached = True
                    if match["buffer_id"] is not None:
                        buf_val = int(match["buffer_id"])
                        pending_buffer = buf_val if buf_val != 0 else None
                    else:
                        pending_buffer = None
            elif call.method == "commit":
                if pending_attached:
                    current_buffer = pending_buffer
                    pending_attached = False
                    pending_buffer = None
                if call.index == target_commit_index:
                    return current_buffer, pending_buffer, pending_attached

    return current_buffer, pending_buffer, pending_attached


def _effective_buffer_at_commit(
    calls: list[TraceCall],
    target_surf: int,
    target_commit_index: int,
) -> int | None:
    """Determine the effective non-zero wl_buffer active at target_commit_index per Wayland double-buffering.

    wl_surface.attach updates the pending buffer state.
    wl_surface.commit applies pending state to the current state.
    If no attach occurred since the previous commit, the current buffer remains unchanged.
    attach(nil, ...) or attach(0, ...) sets pending buffer to None, unmapping the surface upon commit.
    Attaches occurring after target_commit_index have no effect on this commit.
    """
    return _buffer_state_at_commit(calls, target_surf, target_commit_index)[0]


def _diagnose_missing_buffer(
    calls: list[TraceCall],
    target_surf: int,
    target_commit_index: int,
    commit_label: str = "HDR",
) -> TraceValidationError:
    """Diagnose why an effective buffer was not present at target_commit_index and return a descriptive error."""
    has_post_commit_attach = any(
        c.interface == "wl_surface"
        and c.object_id == target_surf
        and c.method == "attach"
        and c.index > target_commit_index
        for c in calls
    )
    if has_post_commit_attach:
        return TraceValidationError(
            f"no non-zero wl_buffer was effective on target window surface (wl_surface#{target_surf}) "
            f"at {commit_label} commit (index {target_commit_index}); wl_surface.attach was observed only after this commit"
        )

    had_attach_prior = any(
        c.interface == "wl_surface"
        and c.object_id == target_surf
        and c.method == "attach"
        and c.index < target_commit_index
        for c in calls
    )
    if had_attach_prior:
        return TraceValidationError(
            f"no non-zero wl_buffer was effective on target window surface (wl_surface#{target_surf}) "
            f"at {commit_label} commit (index {target_commit_index}); buffer was detached or set to null prior to commit"
        )

    _, _, surface_attaches, _ = _surface_roles_and_attaches(calls)
    other_attaches = {
        s: [b for b in bufs if b != 0]
        for s, bufs in surface_attaches.items()
        if s != target_surf and any(b != 0 for b in bufs)
    }
    if other_attaches:
        other_names = ", ".join(f"wl_surface#{s}" for s in sorted(other_attaches))
        return TraceValidationError(
            f"no non-zero wl_surface.attach observed on target window surface (wl_surface#{target_surf}); "
            f"attach observed only on other surface(s) ({other_names})"
        )
    return TraceValidationError(
        f"no non-zero wl_surface.attach observed on target window surface (wl_surface#{target_surf})"
    )


# wp_color_manager_v1.transfer_function named enum values
# Source: /usr/share/wayland-protocols/staging/color-management/color-management-v1.xml:261-397
# Package: wayland-protocols-devel-1.49-1.fc44.noarch (host system)
TF_NAMED_BT1886 = 1
TF_NAMED_GAMMA22 = 2
TF_NAMED_GAMMA28 = 3
TF_NAMED_ST240 = 4
TF_NAMED_EXT_LINEAR = 5
TF_NAMED_LOG_100 = 6
TF_NAMED_LOG_316 = 7
TF_NAMED_XVYCC = 8
TF_NAMED_SRGB = 9
TF_NAMED_EXT_SRGB = 10
TF_NAMED_ST2084_PQ = 11
TF_NAMED_ST428 = 12
TF_NAMED_HLG = 13
TF_NAMED_COMPOUND_POWER_2_4 = 14

# wp_color_manager_v1.primaries named enum values
# Source: /usr/share/wayland-protocols/staging/color-management/color-management-v1.xml:170-259
# Package: wayland-protocols-devel-1.49-1.fc44.noarch (host system)
PRIM_NAMED_SRGB = 1
PRIM_NAMED_PAL_M = 2
PRIM_NAMED_PAL = 3
PRIM_NAMED_NTSC = 4
PRIM_NAMED_GENERIC_FILM = 5
PRIM_NAMED_BT2020 = 6
PRIM_NAMED_CIE1931_XYZ = 7
PRIM_NAMED_DCI_P3 = 8
PRIM_NAMED_DISPLAY_P3 = 9
PRIM_NAMED_ADOBE_RGB = 10

NAMED_PRIMARIES = frozenset({
    PRIM_NAMED_SRGB,
    PRIM_NAMED_PAL_M,
    PRIM_NAMED_PAL,
    PRIM_NAMED_NTSC,
    PRIM_NAMED_GENERIC_FILM,
    PRIM_NAMED_BT2020,
    PRIM_NAMED_CIE1931_XYZ,
    PRIM_NAMED_DCI_P3,
    PRIM_NAMED_DISPLAY_P3,
    PRIM_NAMED_ADOBE_RGB,
})

NAMED_HDR_TRANSFER_FUNCTIONS = frozenset({
    TF_NAMED_ST2084_PQ,
    TF_NAMED_HLG,
})

NAMED_SDR_TRANSFER_FUNCTIONS = frozenset({
    TF_NAMED_BT1886,
    TF_NAMED_GAMMA22,
    TF_NAMED_GAMMA28,
    TF_NAMED_ST240,
    TF_NAMED_SRGB,
    TF_NAMED_COMPOUND_POWER_2_4,
})

# wp_color_manager_v1.feature enum values
# Source: /usr/share/wayland-protocols/staging/color-management/color-management-v1.xml:140-169
# Package: wayland-protocols-devel-1.49-1.fc44.noarch (host system)
FEATURE_ICC_V2_V4 = 0
FEATURE_PARAMETRIC = 1
FEATURE_SET_PRIMARIES = 2
FEATURE_SET_TF_POWER = 3
FEATURE_SET_LUMINANCES = 4
FEATURE_SET_MASTERING_DISPLAY_PRIMARIES = 5
FEATURE_EXTENDED_TARGET_VOLUME = 6
FEATURE_WINDOWS_SCRGB = 7
FEATURE_WINDOWS_BT2100 = 8

SUPPORTED_FEATURE_RE = re.compile(
    r'^\s*(?:(?P<val>0x[0-9a-fA-F]+|\d+)|"(?P<name>[^"]+)"|(?P<ident>[A-Za-z_][A-Za-z0-9_]*))\s*$'
)
SET_LUMINANCES_RE = re.compile(
    r"^\s*(?P<min>[^,]+)\s*,\s*(?P<max>[^,]+)\s*,\s*(?P<ref>[^,]+)\s*$"
)
UINT32_MAX = 0xFFFFFFFF
UINT32_ASCII_RE = re.compile(r"^[0-9]+$")


def _parse_uint32(val_str: str) -> int | None:
    """Parse an unsigned 32-bit integer string strictly without signs."""
    val_str = val_str.strip()
    if not val_str or not UINT32_ASCII_RE.match(val_str):
        return None
    try:
        val = int(val_str, 10)
    except ValueError:
        return None
    if 0 <= val <= UINT32_MAX:
        return val
    return None


def _matches_feature(args: str, feature_val: int, feature_name: str) -> bool:
    """Check if supported_feature arguments advertise the given feature."""
    m = SUPPORTED_FEATURE_RE.match(args)
    if m is None:
        return False
    val = m.group("val")
    if val is not None:
        try:
            return int(val, 0) == feature_val
        except ValueError:
            return False
    name = m.group("name") or m.group("ident")
    return name == feature_name


def _matches_windows_scrgb_feature(args: str) -> bool:
    """Check if supported_feature arguments advertise windows_scrgb (feature 7)."""
    return _matches_feature(args, FEATURE_WINDOWS_SCRGB, "windows_scrgb")


def _classify_named_color_metadata(
    primaries_named: int | None,
    tf_named: int | None,
) -> tuple[str, str]:
    """Pure classification of named primaries and transfer function into (color_type, semantic_class)."""
    if primaries_named == PRIM_NAMED_SRGB and tf_named in (
        TF_NAMED_BT1886,
        TF_NAMED_GAMMA22,
        TF_NAMED_GAMMA28,
        TF_NAMED_SRGB,
        TF_NAMED_COMPOUND_POWER_2_4,
    ):
        color_type = "srgb_bt709"
    elif primaries_named == PRIM_NAMED_BT2020 and tf_named == TF_NAMED_ST2084_PQ:
        color_type = "rec2100_pq"
    elif primaries_named == PRIM_NAMED_BT2020 and tf_named == TF_NAMED_HLG:
        color_type = "rec2100_hlg"
    elif primaries_named is not None or tf_named is not None:
        color_type = f"custom_p{primaries_named}_tf{tf_named}"
    else:
        color_type = "unrevealed"

    if tf_named in NAMED_HDR_TRANSFER_FUNCTIONS:
        semantic_class = "hdr"
    elif tf_named in NAMED_SDR_TRANSFER_FUNCTIONS:
        semantic_class = "sdr"
    else:
        semantic_class = "unknown"

    return color_type, semantic_class


def _extract_image_descriptions_info(calls: list[TraceCall]) -> dict[int, dict[str, Any]]:
    """Extract color metadata (primaries, tf, color_type, semantic_class) for image descriptions.

    Understands:
    1. wp_image_description_creator_params_v1 (set_primaries_named, set_tf_named, create).
    2. wp_image_description_info_v1 (primaries_named, tf_named).

    Maintains internal classification of metadata semantics (semantic_class):
    - 'hdr': PQ (11) and HLG (13) transfer functions regardless of primaries;
    - 'sdr': Standard SDR transfer functions (BT.1886, gamma 2.2, gamma 2.8, ST 240, sRGB, compound_power_2_4);
    - 'unknown': missing/unsupported transfer function, ext_linear without luminance parsing, unrevealed.
    """
    creators: dict[int, dict[str, Any]] = {}
    descs: dict[int, dict[str, Any]] = {}
    info_to_desc: dict[int, int] = {}

    for call in calls:
        if call.interface == "wp_color_manager_v1" and call.method == "create_parametric_creator":
            m = CREATE_PARAMETRIC_RE.match(call.args)
            if m is not None:
                creators[int(m["creator_id"])] = {"primaries": None, "tf": None}
        elif call.interface == "wp_image_description_creator_params_v1":
            cid = call.object_id
            if call.method == "set_primaries_named":
                m = SET_PRIMARIES_NAMED_RE.match(call.args)
                if m is not None:
                    creators.setdefault(cid, {})["primaries"] = int(m["primaries"])
            elif call.method == "set_tf_named":
                m = SET_TF_NAMED_RE.match(call.args)
                if m is not None:
                    creators.setdefault(cid, {})["tf"] = int(m["tf"])
            elif call.method == "create":
                m = CREATE_DESC_RE.match(call.args)
                if m is not None:
                    did = int(m["description_id"])
                    cinfo = creators.get(cid, {})
                    descs[did] = {
                        "primaries_named": cinfo.get("primaries"),
                        "tf_named": cinfo.get("tf"),
                    }
        elif call.interface == "wp_image_description_v1" and call.method == "get_information":
            m = GET_INFO_RE.match(call.args)
            if m is not None:
                did = call.object_id
                info_to_desc[int(m["info_id"])] = did
                descs.setdefault(did, {"primaries_named": None, "tf_named": None})
        elif call.interface == "wp_image_description_info_v1":
            infoid = call.object_id
            did = info_to_desc.get(infoid)
            if did is not None:
                desc_entry = descs.setdefault(did, {"primaries_named": None, "tf_named": None})
                if call.method == "primaries_named":
                    m = SET_PRIMARIES_NAMED_RE.match(call.args)
                    if m is not None:
                        desc_entry["primaries_named"] = int(m["primaries"])
                elif call.method == "tf_named":
                    m = SET_TF_NAMED_RE.match(call.args)
                    if m is not None:
                        desc_entry["tf_named"] = int(m["tf"])

    for did, data in descs.items():
        data["color_type"], data["semantic_class"] = _classify_named_color_metadata(
            data.get("primaries_named"),
            data.get("tf_named"),
        )

    return descs


def _unknown_color_result(
    reason: str,
    *,
    primaries_named: int | None = None,
    tf_named: int | None = None,
    color_type: str = "unrevealed",
) -> dict[str, Any]:
    return {
        "semantic_class": "unknown",
        "reason": reason,
        "primaries_named": primaries_named,
        "tf_named": tf_named,
        "color_type": color_type,
    }


def _validate_creator_lifecycle(
    calls: list[TraceCall],
    calls_by_index: dict[int, TraceCall],
    creations: dict[tuple[str, int], list[int]],
    manager_id: int,
    description_id: int,
    creation_idx: int,
) -> tuple[int, list[TraceCall]] | dict[str, Any]:
    """Verify origin, lifetime, and usage bounds of the creator params object."""
    creation_call = calls_by_index.get(creation_idx)
    if (
        creation_call is None
        or creation_call.interface != "wp_image_description_creator_params_v1"
        or creation_call.method != "create"
    ):
        return _unknown_color_result(
            f"wp_image_description_v1#{description_id} was not created via "
            "wp_image_description_creator_params_v1.create"
        )

    creator_id = creation_call.object_id
    creator_creations = creations.get(
        ("wp_image_description_creator_params_v1", creator_id), []
    )
    if not creator_creations:
        return _unknown_color_result(
            f"creation of creator wp_image_description_creator_params_v1#{creator_id} not found"
        )

    if len(creator_creations) > 1:
        return _unknown_color_result(
            f"wp_image_description_creator_params_v1#{creator_id} was recreated in the trace "
            f"({len(creator_creations)} creations observed); creator ID reuse is rejected"
        )

    creator_create_idx = creator_creations[0]
    if creator_create_idx >= creation_idx:
        return _unknown_color_result(
            f"creator wp_image_description_creator_params_v1#{creator_id} at index {creator_create_idx} "
            f"was created after create() call at {creation_idx}"
        )

    creator_create_call = calls_by_index.get(creator_create_idx)
    if (
        creator_create_call is None
        or creator_create_call.interface != "wp_color_manager_v1"
        or creator_create_call.method != "create_parametric_creator"
        or creator_create_call.object_id != manager_id
    ):
        return _unknown_color_result(
            f"creator wp_image_description_creator_params_v1#{creator_id} was not created by "
            f"wp_color_manager_v1#{manager_id}"
        )

    creator_calls = [
        c
        for c in calls
        if c.interface == "wp_image_description_creator_params_v1"
        and c.object_id == creator_id
    ]

    for c in creator_calls:
        if c.index < creator_create_idx:
            return _unknown_color_result(
                f"creator wp_image_description_creator_params_v1#{creator_id} was used at index {c.index} "
                f"before its creation at {creator_create_idx}"
            )
        if c.index > creation_idx:
            return _unknown_color_result(
                f"creator wp_image_description_creator_params_v1#{creator_id} had method {c.method} "
                f"invoked at index {c.index} after create() destructor at {creation_idx}"
            )

    param_calls = [
        c for c in creator_calls if creator_create_idx < c.index < creation_idx
    ]
    return creator_id, param_calls


def _partition_creator_param_calls(
    param_calls: list[TraceCall],
) -> tuple[
    list[TraceCall],
    list[TraceCall],
    list[TraceCall],
    list[TraceCall],
    list[TraceCall],
    list[TraceCall],
]:
    """Group creator parameter calls by method type in a single pass."""
    tf_named: list[TraceCall] = []
    tf_power: list[TraceCall] = []
    tf_other: list[TraceCall] = []
    prim_named: list[TraceCall] = []
    prim_custom: list[TraceCall] = []
    prim_other: list[TraceCall] = []

    for c in param_calls:
        if c.method == "set_tf_named":
            tf_named.append(c)
        elif c.method == "set_tf_power":
            tf_power.append(c)
        elif c.method.startswith("set_tf_"):
            tf_other.append(c)
        elif c.method == "set_primaries_named":
            prim_named.append(c)
        elif c.method == "set_primaries":
            prim_custom.append(c)
        elif c.method.startswith("set_primaries_"):
            prim_other.append(c)

    return tf_named, tf_power, tf_other, prim_named, prim_custom, prim_other


def _validate_parametric_creator_primaries(
    creator_id: int,
    prim_named_calls: list[TraceCall],
    prim_custom_calls: list[TraceCall],
    other_prim_calls: list[TraceCall],
) -> int | dict[str, Any]:
    """Validate uniqueness, presence, and coordinates/enum of primaries."""
    total_prim_calls = len(prim_named_calls) + len(prim_custom_calls) + len(other_prim_calls)
    if total_prim_calls > 1:
        return _unknown_color_result(
            f"creator wp_image_description_creator_params_v1#{creator_id} had multiple primaries "
            "requests (already_set / contradictory)"
        )

    if total_prim_calls == 0:
        return _unknown_color_result(
            f"creator wp_image_description_creator_params_v1#{creator_id} did not set primaries "
            "(incomplete_set / unrevealed)"
        )

    if prim_custom_calls:
        return _unknown_color_result(
            f"creator wp_image_description_creator_params_v1#{creator_id} used custom set_primaries "
            "which is unverified for composite_window",
            color_type="custom_primaries",
        )

    if other_prim_calls:
        return _unknown_color_result(
            f"creator wp_image_description_creator_params_v1#{creator_id} used non-standard primaries "
            f"method {other_prim_calls[0].method}"
        )

    m_prim = SET_PRIMARIES_NAMED_RE.match(prim_named_calls[0].args)
    if m_prim is None:
        return _unknown_color_result(
            f"malformed set_primaries_named args on creator wp_image_description_creator_params_v1#{creator_id}"
        )

    prim_val = int(m_prim["primaries"])
    if prim_val not in NAMED_PRIMARIES:
        return _unknown_color_result(
            f"creator wp_image_description_creator_params_v1#{creator_id} set unknown named primaries "
            f"{prim_val} (invalid_primaries_named)",
            primaries_named=prim_val,
            color_type=f"unknown_p{prim_val}",
        )

    return prim_val


def _validate_parametric_creator_tf(
    creator_id: int,
    tf_named_calls: list[TraceCall],
    tf_power_calls: list[TraceCall],
    other_tf_calls: list[TraceCall],
    prim_val: int,
) -> int | dict[str, Any]:
    """Validate remaining presence and curve/enum checks of transfer function."""
    total_tf_calls = len(tf_named_calls) + len(tf_power_calls) + len(other_tf_calls)
    if total_tf_calls == 0:
        return _unknown_color_result(
            f"creator wp_image_description_creator_params_v1#{creator_id} did not set a transfer "
            "function (incomplete_set / unrevealed)",
            primaries_named=prim_val,
            color_type=f"custom_p{prim_val}_tfNone",
        )

    if tf_power_calls:
        return _unknown_color_result(
            f"creator wp_image_description_creator_params_v1#{creator_id} used set_tf_power "
            "which is unverified for composite_window",
            primaries_named=prim_val,
            color_type="custom_power_tf",
        )

    if other_tf_calls:
        return _unknown_color_result(
            f"creator wp_image_description_creator_params_v1#{creator_id} used non-standard transfer "
            f"method {other_tf_calls[0].method}",
            primaries_named=prim_val,
        )

    m_tf = SET_TF_NAMED_RE.match(tf_named_calls[0].args)
    if m_tf is None:
        return _unknown_color_result(
            f"malformed set_tf_named args on creator wp_image_description_creator_params_v1#{creator_id}",
            primaries_named=prim_val,
        )

    return int(m_tf["tf"])


def _validate_parametric_creator_parameters(
    creator_id: int,
    param_calls: list[TraceCall],
) -> tuple[int, int] | dict[str, Any]:
    """Validate mandatory primaries and transfer function parameters on creator."""
    tf_named, tf_power, tf_other, prim_named, prim_custom, prim_other = (
        _partition_creator_param_calls(param_calls)
    )

    if len(tf_named) + len(tf_power) + len(tf_other) > 1:
        return _unknown_color_result(
            f"creator wp_image_description_creator_params_v1#{creator_id} had multiple transfer "
            "characteristic requests (already_set / contradictory)"
        )

    prim_res = _validate_parametric_creator_primaries(
        creator_id, prim_named, prim_custom, prim_other
    )
    if isinstance(prim_res, dict):
        return prim_res

    tf_res = _validate_parametric_creator_tf(
        creator_id, tf_named, tf_power, tf_other, prim_res
    )
    if isinstance(tf_res, dict):
        return tf_res

    return prim_res, tf_res


def _format_parametric_color_result(prim_val: int, tf_val: int) -> dict[str, Any]:
    """Format final classification dictionary for a valid parametric description."""
    color_type, semantic_class = _classify_named_color_metadata(prim_val, tf_val)
    if semantic_class == "hdr":
        reason = None
    elif semantic_class == "sdr":
        reason = f"transfer function {tf_val} is SDR"
    elif tf_val == TF_NAMED_EXT_LINEAR:
        reason = "ext_linear transfer function without luminance parsing is unverified for composite_window"
    else:
        reason = f"transfer function {tf_val} is not a recognized named HDR transfer"

    return {
        "semantic_class": semantic_class,
        "reason": reason,
        "primaries_named": prim_val,
        "tf_named": tf_val,
        "color_type": color_type,
    }


def _parse_and_check_luminance_args(
    args: str, creator_id: int, prim_val: int, tf_val: int
) -> tuple[int, int, int] | dict[str, Any]:
    """Parse and check physical bounds for set_luminances arguments."""
    m = SET_LUMINANCES_RE.match(args)
    if m is None:
        return _unknown_color_result(
            f"malformed or out-of-range set_luminances arguments on creator "
            f"wp_image_description_creator_params_v1#{creator_id}",
            primaries_named=prim_val,
            tf_named=tf_val,
            color_type=f"custom_p{prim_val}_tf{tf_val}",
        )

    min_lum = _parse_uint32(m["min"])
    max_lum = _parse_uint32(m["max"])
    reference_lum = _parse_uint32(m["ref"])
    if min_lum is None or max_lum is None or reference_lum is None:
        return _unknown_color_result(
            f"malformed or out-of-range set_luminances arguments on creator "
            f"wp_image_description_creator_params_v1#{creator_id}",
            primaries_named=prim_val,
            tf_named=tf_val,
            color_type=f"custom_p{prim_val}_tf{tf_val}",
        )

    if not (max_lum * 10000 > min_lum and reference_lum * 10000 > min_lum):
        return _unknown_color_result(
            f"creator wp_image_description_creator_params_v1#{creator_id} has invalid luminances: "
            f"max_lum*10000 ({max_lum * 10000}) and reference_lum*10000 ({reference_lum * 10000}) "
            f"must be strictly greater than min_lum ({min_lum})",
            primaries_named=prim_val,
            tf_named=tf_val,
            color_type=f"custom_p{prim_val}_tf{tf_val}",
        )

    return min_lum, max_lum, reference_lum


def _validate_parametric_creator_luminances(
    calls: list[TraceCall],
    manager_id: int,
    creator_id: int,
    param_calls: list[TraceCall],
    prim_val: int,
    tf_val: int,
) -> dict[str, Any]:
    """Validate set_luminances on parametric creator for ext_linear HDR recognition."""
    lum_calls = [c for c in param_calls if c.method == "set_luminances"]
    if len(lum_calls) > 1:
        return _unknown_color_result(
            f"creator wp_image_description_creator_params_v1#{creator_id} had multiple set_luminances "
            "requests (already_set / contradictory)",
            primaries_named=prim_val,
            tf_named=tf_val,
            color_type=f"custom_p{prim_val}_tf{tf_val}",
        )

    if not lum_calls:
        return _unknown_color_result(
            "ext_linear transfer function without luminance parsing is unverified for composite_window",
            primaries_named=prim_val,
            tf_named=tf_val,
            color_type=f"custom_p{prim_val}_tf{tf_val}",
        )

    lum_call = lum_calls[0]
    has_feature = any(
        c.interface == "wp_color_manager_v1"
        and c.object_id == manager_id
        and c.method == "supported_feature"
        and c.index < lum_call.index
        and _matches_feature(c.args, FEATURE_SET_LUMINANCES, "set_luminances")
        for c in calls
    )
    if not has_feature:
        return _unknown_color_result(
            f"wp_color_manager_v1#{manager_id} did not advertise feature.set_luminances "
            f"(feature {FEATURE_SET_LUMINANCES}) before set_luminances at index {lum_call.index}",
            primaries_named=prim_val,
            tf_named=tf_val,
            color_type=f"custom_p{prim_val}_tf{tf_val}",
        )

    parsed_res = _parse_and_check_luminance_args(lum_call.args, creator_id, prim_val, tf_val)
    if isinstance(parsed_res, dict):
        return parsed_res
    min_lum, max_lum, reference_lum = parsed_res

    if max_lum <= reference_lum:
        return _unknown_color_result(
            f"creator wp_image_description_creator_params_v1#{creator_id} ext_linear has "
            f"max_lum ({max_lum}) <= reference_lum ({reference_lum}); "
            "HDR distinction criterion (max_lum > reference_lum per AGENTS.md §8) is not met",
            primaries_named=prim_val,
            tf_named=tf_val,
            color_type=f"custom_p{prim_val}_tf{tf_val}",
        )

    return {
        "semantic_class": "hdr",
        "reason": None,
        "primaries_named": prim_val,
        "tf_named": tf_val,
        "color_type": f"custom_p{prim_val}_tf{tf_val}",
    }


def _classify_parametric_description_for_window(
    calls: list[TraceCall],
    calls_by_index: dict[int, TraceCall],
    creations: dict[tuple[str, int], list[int]],
    manager_id: int,
    description_id: int,
    creation_idx: int,
) -> dict[str, Any]:
    """Strictly classify the color metadata for an image description candidate on the composite window.

    Enforces that metadata is sourced from the exact wp_image_description_creator_params_v1 object
    that created wp_image_description_v1#description_id, bound from manager_id.
    Parameters modified after create, foreign creators, reused creator IDs, or contradictory
    parameter calls invalidate the candidate.
    Non-parametric creators, ext_linear without luminances, unrevealed, or metadata only from
    get_information are classified as 'unknown' for composite_window.
    """
    lifecycle_res = _validate_creator_lifecycle(
        calls, calls_by_index, creations, manager_id, description_id, creation_idx
    )
    if isinstance(lifecycle_res, dict):
        return lifecycle_res
    creator_id, param_calls = lifecycle_res

    params_res = _validate_parametric_creator_parameters(creator_id, param_calls)
    if isinstance(params_res, dict):
        return params_res
    prim_val, tf_val = params_res

    if tf_val == TF_NAMED_EXT_LINEAR:
        return _validate_parametric_creator_luminances(
            calls, manager_id, creator_id, param_calls, prim_val, tf_val
        )

    return _format_parametric_color_result(prim_val, tf_val)


def _classify_windows_scrgb_description_for_window(
    calls: list[TraceCall],
    calls_by_index: dict[int, TraceCall],
    manager_id: int,
    description_id: int,
    creation_idx: int,
) -> dict[str, Any]:
    """Validate windows_scrgb image description against manager feature announcement and protocol rules.

    wp_color_manager_v1.create_windows_scrgb creates a pre-defined image description
    using sRGB primaries (primaries=1) and extended linear transfer function (tf=5).
    Requires feature.windows_scrgb (feature 7) to be advertised by the manager before creation.
    Source: /usr/share/wayland-protocols/staging/color-management/color-management-v1.xml:165-166, 474-524, 539-550
    """
    creation_call = calls_by_index.get(creation_idx)
    if (
        creation_call is None
        or creation_call.interface != "wp_color_manager_v1"
        or creation_call.method != "create_windows_scrgb"
        or creation_call.object_id != manager_id
    ):
        return _unknown_color_result(
            f"wp_image_description_v1#{description_id} was not created via "
            f"wp_color_manager_v1#{manager_id}.create_windows_scrgb",
            primaries_named=PRIM_NAMED_SRGB,
            tf_named=TF_NAMED_EXT_LINEAR,
            color_type="windows_scrgb",
        )

    m = CREATE_DESC_RE.match(creation_call.args)
    if m is None or int(m["description_id"]) != description_id:
        return _unknown_color_result(
            f"malformed create_windows_scrgb args for wp_image_description_v1#{description_id}",
            primaries_named=PRIM_NAMED_SRGB,
            tf_named=TF_NAMED_EXT_LINEAR,
            color_type="windows_scrgb",
        )

    has_feature = any(
        c.interface == "wp_color_manager_v1"
        and c.object_id == manager_id
        and c.method == "supported_feature"
        and c.index < creation_idx
        and _matches_windows_scrgb_feature(c.args)
        for c in calls
    )
    if not has_feature:
        return _unknown_color_result(
            f"wp_color_manager_v1#{manager_id} did not advertise feature.windows_scrgb "
            f"(feature {FEATURE_WINDOWS_SCRGB}) before create_windows_scrgb at index {creation_idx}",
            primaries_named=PRIM_NAMED_SRGB,
            tf_named=TF_NAMED_EXT_LINEAR,
            color_type="windows_scrgb",
        )

    return {
        "semantic_class": "hdr",
        "reason": None,
        "primaries_named": PRIM_NAMED_SRGB,
        "tf_named": TF_NAMED_EXT_LINEAR,
        "color_type": "windows_scrgb",
    }


def _classify_description_for_window(
    calls: list[TraceCall],
    calls_by_index: dict[int, TraceCall],
    creations: dict[tuple[str, int], list[int]],
    manager_id: int,
    description_id: int,
    creation_idx: int,
) -> dict[str, Any]:
    """Classify the color metadata for an image description candidate on the composite window."""
    creation_call = calls_by_index.get(creation_idx)
    if creation_call is not None:
        if (
            creation_call.interface == "wp_color_manager_v1"
            and creation_call.method == "create_windows_scrgb"
        ):
            return _classify_windows_scrgb_description_for_window(
                calls, calls_by_index, manager_id, description_id, creation_idx
            )
        if (
            creation_call.interface == "wp_image_description_creator_params_v1"
            and creation_call.method == "create"
        ):
            return _classify_parametric_description_for_window(
                calls, calls_by_index, creations, manager_id, description_id, creation_idx
            )

    return _unknown_color_result(
        f"wp_image_description_v1#{description_id} was not created via a supported creator or method"
    )


DEFAULT_APP_ID = "io.github.rusmikev.CineHDR"


def _validate_app_id(app_id: str | None) -> str:
    if app_id is None:
        return DEFAULT_APP_ID
    if not isinstance(app_id, str) or not app_id.strip():
        raise TraceValidationError("expected_app_id cannot be empty or whitespace-only")
    return app_id


def _unknown_composite_evidence(reason: str) -> dict[str, Any]:
    return {
        "schema": TRACE_SCHEMA,
        "revision": PRIMARY_COMPOSITE_REVISION,
        "status": "UNKNOWN",
        "oracle": "primary_composite_window",
        "scope": UNKNOWN_SCOPE,
        "reason": reason,
        "matched": {},
    }


def _partition_by_connection(
    trace: str,
    expected_app_id: str | None = None,
    *,
    trace_only: bool = False,
) -> tuple[str, int | None, int, dict[str, Any] | None]:
    """Inspect trace for verifiable connection tags [conn#<id> fd#<fd>].

    Returns (effective_trace, selected_conn_id, total_conns, unknown_evidence_dict).
    If connection tags are present:
    - Rejects untracked [conn#untracked] calls as UNKNOWN.
    - Rejects protocol messages with missing fd in tag (e.g. [conn#<id>]) as UNKNOWN.
    - Rejects inconsistent socket fd within any conn_id as UNKNOWN.
    - Rejects untagged calls in tagged trace as UNKNOWN.
    - Selects the unique connection claiming expected_app_id.
    - If no connection or multiple connections claim expected_app_id, returns UNKNOWN.
    - Isolates lines belonging to the chosen connection.
    If no connection tags are present:
    - If trace_only is True: returns UNKNOWN (cannot prove connection attribution for L3p).
    - If trace_only is False: returns (trace, None, 1, None) for legacy / untagged single-connection processing.
    """
    has_tagged_line = False
    tagged_lines_by_conn: dict[int, list[str]] = {}
    conn_fds: dict[int, int] = {}
    untagged_call_lines: list[str] = []
    untracked_call_lines: list[str] = []
    missing_fd_call_lines: list[str] = []
    inconsistent_fd_lines: list[tuple[int, int, int, str]] = []

    for line in trace.splitlines():
        trimmed = line.strip()
        if not trimmed:
            continue
        m_untracked = UNTRACKED_TAG_RE.match(trimmed)
        if m_untracked:
            has_tagged_line = True
            untracked_call_lines.append(trimmed)
            continue
        m_tag = CONN_TAG_RE.match(trimmed)
        if m_tag:
            has_tagged_line = True
            conn_id = int(m_tag.group("conn_id"))
            fd_str = m_tag.group("fd")
            if fd_str is None:
                missing_fd_call_lines.append(trimmed)
                continue
            fd = int(fd_str)
            if conn_id in conn_fds:
                if conn_fds[conn_id] != fd:
                    inconsistent_fd_lines.append((conn_id, conn_fds[conn_id], fd, trimmed))
                    continue
            else:
                conn_fds[conn_id] = fd
            tagged_lines_by_conn.setdefault(conn_id, []).append(line)
            continue
        if CALL_RE.search(trimmed):
            untagged_call_lines.append(trimmed)

    if not has_tagged_line:
        if trace_only:
            return (
                trace,
                None,
                1,
                _unknown_composite_evidence(
                    "trace-only L3p validation requires verified connection tags [conn#<id> fd#<fd>] for all Wayland calls; "
                    "untagged trace cannot prove connection attribution"
                ),
            )
        return trace, None, 1, None

    if untracked_call_lines:
        return (
            trace,
            None,
            len(tagged_lines_by_conn),
            _unknown_composite_evidence(
                f"trace contains untracked Wayland debug call without verified connection attribution: {untracked_call_lines[0]}"
            ),
        )

    if missing_fd_call_lines:
        return (
            trace,
            None,
            len(tagged_lines_by_conn),
            _unknown_composite_evidence(
                f"missing socket fd in connection tag: {missing_fd_call_lines[0]}"
            ),
        )

    if inconsistent_fd_lines:
        cid, exp_fd, obs_fd, bad_line = inconsistent_fd_lines[0]
        return (
            trace,
            None,
            len(tagged_lines_by_conn),
            _unknown_composite_evidence(
                f"inconsistent socket fd for conn#{cid}: expected fd#{exp_fd}, observed fd#{obs_fd} in line: {bad_line}"
            ),
        )

    if untagged_call_lines:
        return (
            trace,
            None,
            len(tagged_lines_by_conn),
            _unknown_composite_evidence(
                f"trace contains untagged Wayland debug call without verified connection attribution: {untagged_call_lines[0]}"
            ),
        )

    total_conns = len(tagged_lines_by_conn)
    if total_conns == 0:
        return (
            trace,
            None,
            0,
            _unknown_composite_evidence("trace contains connection tags but no valid connection calls found"),
        )

    if expected_app_id:
        matching_conns: list[int] = []
        for cid, lines in tagged_lines_by_conn.items():
            conn_trace = "\n".join(lines)
            conn_calls = _calls(conn_trace)
            has_app_id = any(
                c.interface == "xdg_toplevel"
                and c.method == "set_app_id"
                and (m := SET_APP_ID_RE.match(c.args)) is not None
                and m["app_id"] == expected_app_id
                for c in conn_calls
            )
            if has_app_id:
                matching_conns.append(cid)

        if len(matching_conns) == 0:
            return (
                trace,
                None,
                total_conns,
                _unknown_composite_evidence(
                    f"no connection with app-id {expected_app_id!r} observed among tagged connections: {sorted(tagged_lines_by_conn.keys())}"
                ),
            )
        if len(matching_conns) > 1:
            return (
                trace,
                None,
                total_conns,
                _unknown_composite_evidence(
                    f"multiple conflicting connections observed with app-id {expected_app_id!r}: {sorted(matching_conns)}"
                ),
            )
        selected_conn = matching_conns[0]
    else:
        if total_conns == 1:
            selected_conn = next(iter(tagged_lines_by_conn.keys()))
        else:
            return (
                trace,
                None,
                total_conns,
                _unknown_composite_evidence(
                    f"multiple connections observed ({sorted(tagged_lines_by_conn.keys())}) but no expected_app_id specified to disambiguate"
                ),
            )

    effective_trace = "\n".join(tagged_lines_by_conn[selected_conn])
    return effective_trace, selected_conn, total_conns, None


def validate_composite_window_trace(
    trace: str,
    expected_app_id: str = DEFAULT_APP_ID,
    *,
    trace_only: bool = False,
) -> dict[str, Any]:
    """Validate passive Wayland color-management submission for CineHDR's composite window.

    Full ADR-0004 criteria for dynamically found window surface:
    1. wp_color_manager_v1 advertised and bound;
    2. Manager capability burst includes perceptual intent (supported_intent(0)) and completes with done();
    3. CineHDR's toplevel window surface dynamically identified via:
       xdg_wm_base.get_xdg_surface(..., wl_surface#X) -> xdg_surface.get_toplevel(...) -> xdg_toplevel.set_app_id(expected_app_id);
    4. wp_color_manager_v1.get_surface called for that exact window surface;
    5. wp_image_description_v1 received ready (or ready2) event before set_image_description;
    6. set_image_description called with perceptual intent (intent=0);
    7. wl_surface.commit called on that exact window surface after set_image_description;
    8. Free of protocol errors, descriptor failed events, and fatal errors.
    """
    expected_app_id = _validate_app_id(expected_app_id)
    fatal = _fatal_error(trace)
    if fatal is not None:
        raise fatal

    effective_trace, selected_conn, total_conns, unknown_ev = _partition_by_connection(
        trace, expected_app_id, trace_only=trace_only
    )
    if unknown_ev is not None:
        return unknown_ev
    trace = effective_trace

    def _finalize(ev: dict[str, Any]) -> dict[str, Any]:
        if selected_conn is not None:
            ev["selected_connection_id"] = selected_conn
            ev["total_connections_observed"] = total_conns
            ev["connection_tags_verified"] = True
        return ev
    calls = _calls(trace)
    calls_by_index = {call.index: call for call in calls}
    globals_ = [
        (call, GLOBAL_RE.match(call.args))
        for call in calls
        if call.interface == "wl_registry" and call.method == "global"
    ]
    manager_globals = [
        (call, int(match["global_id"]), int(match["version"]))
        for call, match in globals_
        if match is not None
    ]
    if not manager_globals:
        raise TraceValidationError("wp_color_manager_v1 was not advertised by the registry")

    creations: dict[tuple[str, int], list[int]] = {}
    creations_by_id: dict[int, list[tuple[str, int]]] = {}
    for call in calls:
        match = NEW_ID_RE.search(call.args)
        if match is not None:
            interface = match["interface"]
        else:
            match = UNKNOWN_NEW_ID_RE.search(call.args)
            if call.interface == "wl_registry" and call.method == "bind":
                m_b = BIND_RE.match(call.args)
                interface = "wp_color_manager_v1" if m_b else "unknown"
            else:
                interface = {
                    ("wl_display", "get_registry"): "wl_registry",
                    ("wl_compositor", "create_surface"): "wl_surface",
                    ("wp_color_manager_v1", "get_surface"): "wp_color_management_surface_v1",
                    ("wp_color_manager_v1", "create_parametric_creator"): "wp_image_description_creator_params_v1",
                    ("wp_color_manager_v1", "create_icc_creator"): "wp_image_description_creator_icc_v1",
                    ("wp_color_manager_v1", "create_windows_scrgb"): "wp_image_description_v1",
                    ("wp_image_description_creator_params_v1", "create"): "wp_image_description_v1",
                    ("wp_image_description_creator_icc_v1", "create"): "wp_image_description_v1",
                    ("wp_color_management_surface_v1", "get_image_description"): "wp_image_description_v1",
                    ("wp_color_management_output_v1", "get_image_description"): "wp_image_description_v1",
                    ("wp_color_management_surface_feedback_v1", "get_preferred_parametric"): "wp_image_description_v1",
                }.get((call.interface, call.method), "unknown")
        if match is not None and interface is not None:
            obj_id = int(match["object_id"])
            creations.setdefault((interface, obj_id), []).append(call.index)
            creations_by_id.setdefault(obj_id, []).append((interface, call.index))

    bindings: list[tuple[TraceCall, TraceCall, int, int, int, int]] = []
    manager_bind_counts: dict[int, int] = {}
    for call in calls:
        if call.interface != "wl_registry" or call.method != "bind":
            continue
        match = BIND_RE.match(call.args)
        if match is None:
            continue
        global_id = int(match["global_id"])
        manager_id = int(match["manager_id"])
        manager_bind_counts[manager_id] = manager_bind_counts.get(manager_id, 0) + 1
        bound_version = int(match["version"])
        registry_creations = [
            index for index in creations.get(("wl_registry", call.object_id), [])
            if index < call.index
        ]
        if len(registry_creations) > 1:
            continue
        if registry_creations and any(
            earlier.interface == "wl_registry"
            and earlier.object_id == call.object_id
            and earlier.index < registry_creations[0]
            for earlier in calls
        ):
            continue
        advertisements = [
            (global_call, advertised_version)
            for global_call, advertised_id, advertised_version in manager_globals
            if global_call.object_id == call.object_id
            and advertised_id == global_id
            and global_call.index < call.index
            and (not registry_creations or global_call.index > registry_creations[0])
        ]
        if len(advertisements) != 1:
            continue
        global_call, advertised_version = advertisements[0]
        if not 1 <= bound_version <= advertised_version:
            continue
        bindings.append(
            (call, global_call, global_id, manager_id, advertised_version, bound_version)
        )
    if not bindings:
        raise TraceValidationError("wp_color_manager_v1 was advertised but not bound")

    toplevel_surfaces, subsurfaces, surface_attaches, viewport_surfaces = _surface_roles_and_attaches(calls)
    target_window_surfaces = [
        surf for surf, app in toplevel_surfaces.items()
        if app == expected_app_id
    ]

    has_perceptual = False
    has_done = False
    has_get_surface_window = False
    order_error = None
    candidates_hdr: list[dict[str, Any]] = []
    candidates_unknown: list[dict[str, Any]] = []
    candidates_sdr: list[dict[str, Any]] = []

    for bind, global_call, global_id, manager_id, advertised_version, bound_version in bindings:
        valid_gen, mgr_end_index = _is_valid_manager_generation(bindings, bind, manager_id, calls)
        if not valid_gen:
            first_delete_after_bind = next(
                (
                    cd.index
                    for cd in calls
                    if cd.interface == "wl_display"
                    and cd.method == "delete_id"
                    and cd.args == str(manager_id)
                    and cd.index > bind.index
                ),
                None,
            )
            if first_delete_after_bind is not None and any(
                c.interface == "wp_color_manager_v1"
                and c.object_id == manager_id
                and c.index > first_delete_after_bind
                for c in calls
            ):
                raise TraceValidationError(
                    f"wp_color_manager_v1#{manager_id} was used after delete_id without a new bind"
                )
            continue
        perceptual = next(
            (
                call
                for call in calls
                if call.interface == "wp_color_manager_v1"
                and call.object_id == manager_id
                and call.method == "supported_intent"
                and call.args == "0"
                and bind.index < call.index <= mgr_end_index
            ),
            None,
        )
        if perceptual is None:
            continue
        has_perceptual = True

        done = next(
            (
                call
                for call in calls
                if call.interface == "wp_color_manager_v1"
                and call.object_id == manager_id
                and call.method == "done"
                and call.args == ""
                and perceptual.index < call.index <= mgr_end_index
            ),
            None,
        )
        if done is None:
            continue
        has_done = True

        surfaces: list[tuple[TraceCall, int, int]] = []
        for call in calls:
            if (
                call.interface != "wp_color_manager_v1"
                or call.object_id != manager_id
                or call.method != "get_surface"
                or not (bind.index < call.index <= mgr_end_index)
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
            if wl_surface_id not in target_window_surfaces:
                continue
            has_get_surface_window = True

            # Connection isolation: verify that wl_surface and xdg_wm_base belong to this connection / registry
            surf_create = next(
                (
                    c for c in calls
                    if c.interface == "wl_compositor"
                    and c.method == "create_surface"
                    and (
                        (m := NEW_ID_RE.search(c.args)) is not None
                        and m.group("interface") == "wl_surface"
                        and int(m.group("object_id")) == wl_surface_id
                    )
                ),
                None,
            )
            if surf_create is not None:
                comp_id = surf_create.object_id
                comp_bind = next(
                    (
                        c for c in calls
                        if c.interface == "wl_registry"
                        and c.method == "bind"
                        and '"wl_compositor"' in c.args
                        and (
                            (m := (NEW_ID_RE.search(c.args) or UNKNOWN_NEW_ID_RE.search(c.args))) is not None
                            and int(m.group("object_id")) == comp_id
                        )
                    ),
                    None,
                )
                if comp_bind is not None:
                    link_ok, link_err, is_unproven = _verify_registry_connection_link(
                        calls,
                        comp_bind.object_id,
                        bind.object_id,
                        global_id,
                        connection_tags_verified=(selected_conn is not None),
                    )
                    if not link_ok:
                        if is_unproven:
                            candidates_unknown.append(
                                {
                                    "schema": TRACE_SCHEMA,
                                    "revision": PRIMARY_COMPOSITE_REVISION,
                                    "status": "UNKNOWN",
                                    "oracle": "primary_composite_window",
                                    "scope": UNKNOWN_SCOPE,
                                    "reason": link_err,
                                    "matched": {
                                        "registry_global": {
                                            "id": global_id,
                                            "advertised_version": advertised_version,
                                            "registry_id": bind.object_id,
                                            "event_index": global_call.index,
                                        },
                                        "manager": {
                                            "id": manager_id,
                                            "bound_version": bound_version,
                                            "bind_index": bind.index,
                                            "perceptual_intent_index": perceptual.index,
                                            "done_index": done.index,
                                        },
                                        "surface": {
                                            "wl_surface_id": wl_surface_id,
                                            "color_management_surface_id": color_surface_id,
                                            "get_surface_index": get_surface.index,
                                            "app_id": toplevel_surfaces.get(wl_surface_id, expected_app_id),
                                            "role": "composite_toplevel_window",
                                        },
                                    },
                                }
                            )
                            continue
                        order_error = (
                            f"wl_surface#{wl_surface_id} was created by wl_compositor#{comp_id} "
                            f"bound from foreign registry wl_registry#{comp_bind.object_id}: {link_err}"
                        )
                        continue

            has_col, col_err = _has_active_id_collision(calls, creations_by_id, wl_surface_id)
            if has_col:
                raise TraceValidationError(col_err)
            has_col, col_err = _has_active_id_collision(calls, creations_by_id, color_surface_id)
            if has_col:
                raise TraceValidationError(col_err)
            has_col, col_err = _has_active_id_collision(calls, creations_by_id, manager_id)
            if has_col:
                raise TraceValidationError(col_err)

            valid_generation, generation_end = _color_surface_generation_end(
                calls, creations, color_surface_id, get_surface.index,
                connection_verified=selected_conn is not None,
            )
            wl_surface_creations = creations.get(("wl_surface", wl_surface_id), [])
            if (
                not valid_generation
                or len(wl_surface_creations) > 1
                or (wl_surface_creations and wl_surface_creations[0] >= get_surface.index)
                or (
                    wl_surface_creations
                    and _used_before_creation(
                        calls, "wl_surface", wl_surface_id, wl_surface_creations[0]
                    )
                )
            ):
                continue

            xdg_surf = next(
                (
                    c for c in calls
                    if c.interface == "xdg_wm_base"
                    and c.method == "get_xdg_surface"
                    and (
                        (m := GET_XDG_SURFACE_RE.match(c.args)) is not None
                        and int(m["wl_surface_id"]) == wl_surface_id
                    )
                ),
                None,
            )
            if xdg_surf is not None:
                wm_id = xdg_surf.object_id
                wm_bind = next(
                    (
                        c for c in calls
                        if c.interface == "wl_registry"
                        and c.method == "bind"
                        and '"xdg_wm_base"' in c.args
                        and (
                            (m := (NEW_ID_RE.search(c.args) or UNKNOWN_NEW_ID_RE.search(c.args))) is not None
                            and int(m.group("object_id")) == wm_id
                        )
                    ),
                    None,
                )
                if wm_bind is not None:
                    link_ok, link_err, is_unproven = _verify_registry_connection_link(
                        calls,
                        wm_bind.object_id,
                        bind.object_id,
                        global_id,
                        connection_tags_verified=(selected_conn is not None),
                    )
                    if not link_ok:
                        if is_unproven:
                            candidates_unknown.append(
                                {
                                    "schema": TRACE_SCHEMA,
                                    "revision": PRIMARY_COMPOSITE_REVISION,
                                    "status": "UNKNOWN",
                                    "oracle": "primary_composite_window",
                                    "scope": UNKNOWN_SCOPE,
                                    "reason": link_err,
                                    "matched": {
                                        "registry_global": {
                                            "id": global_id,
                                            "advertised_version": advertised_version,
                                            "registry_id": bind.object_id,
                                            "event_index": global_call.index,
                                        },
                                        "manager": {
                                            "id": manager_id,
                                            "bound_version": bound_version,
                                            "bind_index": bind.index,
                                            "perceptual_intent_index": perceptual.index,
                                            "done_index": done.index,
                                        },
                                        "surface": {
                                            "wl_surface_id": wl_surface_id,
                                            "color_management_surface_id": color_surface_id,
                                            "get_surface_index": get_surface.index,
                                            "app_id": toplevel_surfaces.get(wl_surface_id, expected_app_id),
                                            "role": "composite_toplevel_window",
                                        },
                                    },
                                }
                            )
                            continue
                        order_error = (
                            f"xdg_wm_base#{wm_id} for wl_surface#{wl_surface_id} was bound "
                            f"from foreign registry wl_registry#{wm_bind.object_id}: {link_err}"
                        )
                        continue

            if trace_only:
                link_ok, link_err, is_unproven = _verify_registry_connection_link(
                    calls,
                    bind.object_id,
                    bind.object_id,
                    global_id,
                    connection_tags_verified=(selected_conn is not None),
                )
                if not link_ok:
                    if is_unproven:
                        candidates_unknown.append(
                            {
                                "schema": TRACE_SCHEMA,
                                "revision": PRIMARY_COMPOSITE_REVISION,
                                "status": "UNKNOWN",
                                "oracle": "primary_composite_window",
                                "scope": UNKNOWN_SCOPE,
                                "reason": link_err,
                                "matched": {
                                    "registry_global": {
                                        "id": global_id,
                                        "advertised_version": advertised_version,
                                        "registry_id": bind.object_id,
                                        "event_index": global_call.index,
                                    },
                                    "manager": {
                                        "id": manager_id,
                                        "bound_version": bound_version,
                                        "bind_index": bind.index,
                                        "perceptual_intent_index": perceptual.index,
                                        "done_index": done.index,
                                    },
                                    "surface": {
                                        "wl_surface_id": wl_surface_id,
                                        "color_management_surface_id": color_surface_id,
                                        "get_surface_index": get_surface.index,
                                        "app_id": toplevel_surfaces.get(wl_surface_id, expected_app_id),
                                        "role": "composite_toplevel_window",
                                    },
                                },
                            }
                        )
                        continue
                    order_error = f"registry #{bind.object_id} for wp_color_manager_v1 is invalid: {link_err}"
                    continue

            manager_creator_ids: set[int] = set()
            for c in calls:
                if (
                    c.interface == "wp_color_manager_v1"
                    and c.object_id == manager_id
                    and bind.index < c.index <= mgr_end_index
                ):
                    if c.method in ("create_parametric_creator", "create_icc_creator"):
                        m_cr = NEW_ID_RE.search(c.args) or UNKNOWN_NEW_ID_RE.search(c.args)
                        if m_cr:
                            manager_creator_ids.add(int(m_cr.group("object_id")))
                elif c.interface == "wp_color_management_surface_v1" and c.object_id == color_surface_id:
                    if c.method == "get_surface_feedback":
                        m_cr = NEW_ID_RE.search(c.args) or UNKNOWN_NEW_ID_RE.search(c.args)
                        if m_cr:
                            manager_creator_ids.add(int(m_cr.group("object_id")))

            for set_description in calls:
                if (
                    set_description.interface != "wp_color_management_surface_v1"
                    or set_description.object_id != color_surface_id
                    or set_description.method != "set_image_description"
                    or set_description.index <= get_surface.index
                    or (generation_end is not None and set_description.index >= generation_end)
                ):
                    continue
                description_match = SET_DESCRIPTION_RE.match(set_description.args)
                if description_match is None:
                    continue
                description_id = int(description_match["description_id"])
                # Proven creation requirement
                all_creations = creations.get(
                    ("wp_image_description_v1", description_id), []
                )
                if not all_creations:
                    order_error = f"wp_image_description_v1#{description_id} was passed to set_image_description without proven creation"
                    continue

                if len(all_creations) > 1:
                    order_error = (
                        f"wp_image_description_v1#{description_id} was recreated in the trace "
                        f"({len(all_creations)} creations observed); ID reuse is rejected as insufficient "
                        f"proof in a unified client trace"
                    )
                    continue

                creation_idx = all_creations[0]
                if creation_idx >= set_description.index:
                    order_error = f"wp_image_description_v1#{description_id} was used at index {set_description.index} before its first creation at {creation_idx}"
                    continue

                if not _is_created_by_manager(
                    calls_by_index, creation_idx, manager_id, manager_creator_ids, color_surface_id
                ):
                    order_error = f"wp_image_description_v1#{description_id} was not created by wp_color_manager_v1#{manager_id} or its creators"
                    continue

                # Check that descriptor was not destroyed prior to set_description
                if any(
                    c.interface == "wp_image_description_v1"
                    and c.object_id == description_id
                    and c.method == "destroy"
                    and creation_idx < c.index < set_description.index
                    for c in calls
                ):
                    order_error = f"wp_image_description_v1#{description_id} was destroyed before set_image_description"
                    continue

                # Check that color management surface interface was not destroyed before set_description
                if any(
                    c.interface == "wp_color_management_surface_v1"
                    and c.object_id == color_surface_id
                    and c.method == "destroy"
                    and get_surface.index < c.index < set_description.index
                    for c in calls
                ):
                    order_error = f"wp_color_management_surface_v1#{color_surface_id} was destroyed before set_image_description"
                    continue

                # Check usage before creation
                if any(
                    c.interface == "wp_image_description_v1"
                    and c.object_id == description_id
                    and c.index < creation_idx
                    for c in calls
                ):
                    order_error = f"wp_image_description_v1#{description_id} was used before its creation at index {creation_idx}"
                    continue

                # Check ready/ready2 event for this exact generation:
                # Must satisfy creation_idx < ready_call.index < set_description.index
                ready = _description_ready_between(calls, description_id, creation_idx, set_description.index)
                if ready is None:
                    if any(
                        c.interface == "wp_image_description_v1"
                        and c.object_id == description_id
                        and c.method in ("ready", "ready2")
                        and c.index > set_description.index
                        for c in calls
                    ):
                        order_error = "set_image_description was called before image description received ready event"
                    elif any(
                        c.interface == "wp_image_description_v1"
                        and c.object_id == description_id
                        and c.method in ("ready", "ready2")
                        and c.index <= creation_idx
                        for c in calls
                    ):
                        order_error = f"ready event for wp_image_description_v1#{description_id} was received before active creation"
                    else:
                        order_error = f"wp_image_description_v1#{description_id} never received a ready/ready2 event"
                    continue

                ready_call, readiness = ready

                commit = _same_surface_commit_after(
                    calls, wl_surface_id, set_description.index
                )
                if commit is None:
                    order_error = "no wl_surface.commit observed on window surface after set_image_description"
                    continue
                if generation_end is not None and commit.index >= generation_end:
                    order_error = "color management surface was destroyed before window commit"
                    continue

                intervening = next(
                    (
                        c
                        for c in calls
                        if c.interface == "wp_color_management_surface_v1"
                        and c.object_id == color_surface_id
                        and set_description.index < c.index < commit.index
                        and c.method in ("set_image_description", "unset_image_description", "destroy")
                    ),
                    None,
                )
                if intervening is not None:
                    if intervening.method == "unset_image_description":
                        order_error = (
                            f"wp_color_management_surface_v1#{color_surface_id} had unset_image_description "
                            f"called between set_image_description and wl_surface.commit"
                        )
                    elif intervening.method == "destroy":
                        order_error = (
                            f"wp_color_management_surface_v1#{color_surface_id} was destroyed "
                            f"between set_image_description and wl_surface.commit"
                        )
                    else:
                        order_error = (
                            f"wp_color_management_surface_v1#{color_surface_id} set_image_description "
                            f"was replaced before wl_surface.commit"
                        )
                    continue

                color_eval = _classify_description_for_window(
                    calls, calls_by_index, creations, manager_id, description_id, creation_idx
                )
                base_matched = {
                    "registry_global": {
                        "id": global_id,
                        "registry_id": global_call.object_id,
                        "event_index": global_call.index,
                        "advertised_version": advertised_version,
                    },
                    "manager": {
                        "id": manager_id,
                        "bind_index": bind.index,
                        "bound_version": bound_version,
                        "perceptual_intent_index": perceptual.index,
                        "done_index": done.index,
                    },
                    "surface": {
                        "color_management_surface_id": color_surface_id,
                        "wl_surface_id": wl_surface_id,
                        "role": "composite_toplevel_window",
                        "app_id": toplevel_surfaces[wl_surface_id],
                        "get_surface_index": get_surface.index,
                        "set_image_description_index": set_description.index,
                        "commit_index": commit.index,
                    },
                    "image_description": {
                        "id": description_id,
                        "creation_index": creation_idx,
                        "ready_index": ready_call.index,
                        "readiness": readiness,
                        "primaries_named": color_eval["primaries_named"],
                        "tf_named": color_eval["tf_named"],
                        "color_type": color_eval["color_type"],
                        "semantic_class": color_eval["semantic_class"],
                    },
                }

                if color_eval["semantic_class"] == "hdr":
                    effective_buf = _effective_buffer_at_commit(calls, wl_surface_id, commit.index)
                    base_matched["surface"]["effective_buffer_id"] = effective_buf
                    candidates_hdr.append(
                        {
                            "schema": TRACE_SCHEMA,
                            "revision": PRIMARY_COMPOSITE_REVISION,
                            "status": "PASS",
                            "oracle": "primary_composite_window",
                            "scope": PRIMARY_COMPOSITE_SCOPE,
                            "matched": base_matched,
                            "effective_buffer": effective_buf,
                            "commit_index": commit.index,
                            "wl_surface_id": wl_surface_id,
                        }
                    )
                elif color_eval["semantic_class"] == "unknown":
                    candidates_unknown.append(
                        {
                            "schema": TRACE_SCHEMA,
                            "revision": PRIMARY_COMPOSITE_REVISION,
                            "status": "UNKNOWN",
                            "oracle": "primary_composite_window",
                            "scope": UNKNOWN_SCOPE,
                            "reason": color_eval.get("reason")
                            or "image description color semantics are unverified for composite_window",
                            "matched": base_matched,
                        }
                    )
                elif color_eval["semantic_class"] == "sdr":
                    effective_buf = _effective_buffer_at_commit(calls, wl_surface_id, commit.index)
                    candidates_sdr.append(
                        {
                            "description_id": description_id,
                            "tf_named": color_eval["tf_named"],
                            "primaries_named": color_eval["primaries_named"],
                            "color_type": color_eval["color_type"],
                            "commit_index": commit.index,
                            "wl_surface_id": wl_surface_id,
                            "effective_buffer": effective_buf,
                            "matched": base_matched,
                        }
                    )

    if not target_window_surfaces:
        app_name = "CineHDR" if expected_app_id == DEFAULT_APP_ID else expected_app_id
        raise TraceValidationError(
            f"no toplevel surface was attributed to {app_name} (app_id {expected_app_id!r} not found)"
        )
    if not has_perceptual:
        raise TraceValidationError(
            "bound wp_color_manager_v1 did not advertise perceptual intent (supported_intent(0) missing)"
        )
    if not has_done:
        raise TraceValidationError(
            "bound wp_color_manager_v1 did not complete capability burst with done()"
        )
    if not has_get_surface_window:
        app_name = "CineHDR's" if expected_app_id == DEFAULT_APP_ID else f"{expected_app_id}'s"
        raise TraceValidationError(
            f"wp_color_manager_v1.get_surface was not called for {app_name} toplevel window surface"
        )

    # 1. Any confirmed HDR commit must prevent SDR-only classification.
    # When multiple HDR commits exist, evaluate full L3p criteria across candidates
    # so an early commit with active subsurface ambiguity does not mask a later
    # proven unambiguous commit on the same target window surface.
    if candidates_hdr:
        proven_hdr = []
        for hdr_cand in candidates_hdr:
            s_id = hdr_cand["wl_surface_id"]
            c_idx = hdr_cand["commit_index"]
            eff_buf = hdr_cand["effective_buffer"]
            if eff_buf is None:
                continue

            toplevels_at_c, subsurfaces_at_c = _surface_hierarchy_at_commit(calls, c_idx)
            app_subsurfaces = [
                child for child in subsurfaces_at_c
                if _find_toplevel_ancestor(child, subsurfaces_at_c, toplevels_at_c)[0] == s_id
            ]
            has_subsurface_ambiguity = False
            if app_subsurfaces:
                for child in sorted(app_subsurfaces):
                    child_curr, child_pend, child_attached = _buffer_state_at_commit(
                        calls, child, c_idx
                    )
                    if child_curr is not None or (child_attached and child_pend is not None):
                        has_subsurface_ambiguity = True
                        break
            if not has_subsurface_ambiguity:
                proven_hdr.append(hdr_cand)

        if proven_hdr:
            selected_hdr = proven_hdr[0]
        else:
            confirmed_hdr = next((c for c in candidates_hdr if c["effective_buffer"] is not None), None)
            selected_hdr = confirmed_hdr if confirmed_hdr is not None else candidates_hdr[0]

        for internal_key in ("effective_buffer", "commit_index", "wl_surface_id"):
            selected_hdr.pop(internal_key, None)
        return _finalize(selected_hdr)

    # 2. Unknown semantics must remain uncredited as proven SDR-only
    if candidates_unknown:
        return _finalize(candidates_unknown[0])

    # 3. SDR commits: fail-closed verification requires confirmed connection, target window,
    # ready SDR description, and effective non-zero buffer without subsurface ambiguity.
    if candidates_sdr:
        app_name = "CineHDR's" if expected_app_id == DEFAULT_APP_ID else f"{expected_app_id}'s"

        # When trace_only is active, connection attribution must be verified
        if trace_only and selected_conn is None:
            return _finalize(_unknown_composite_evidence(
                "trace-only L3p validation requires verified connection tags [conn#<id> fd#<fd>] for all Wayland calls"
            ))

        # Check for proven SDR commits: effective non-zero buffer and no active subsurfaces
        proven_sdr = []
        for sdr_cand in candidates_sdr:
            s_id = sdr_cand["wl_surface_id"]
            c_idx = sdr_cand["commit_index"]
            eff_buf = sdr_cand["effective_buffer"]
            if eff_buf is None:
                continue

            toplevels_at_c, subsurfaces_at_c = _surface_hierarchy_at_commit(calls, c_idx)
            app_subsurfaces = [
                child for child in subsurfaces_at_c
                if _find_toplevel_ancestor(child, subsurfaces_at_c, toplevels_at_c)[0] == s_id
            ]
            has_subsurface_ambiguity = False
            if app_subsurfaces:
                for child in sorted(app_subsurfaces):
                    if _effective_buffer_at_commit(calls, child, c_idx) is not None:
                        has_subsurface_ambiguity = True
                        break
            if not has_subsurface_ambiguity:
                proven_sdr.append(sdr_cand)

        if proven_sdr:
            raise TraceValidationError(
                f"only SDR color descriptions were committed on {app_name} toplevel window surface"
            )

        # No proven SDR commit: fail-closed diagnosis of buffer / subsurface defect
        first_sdr = candidates_sdr[0]
        s_id = first_sdr["wl_surface_id"]
        c_idx = first_sdr["commit_index"]
        toplevels_at_c, subsurfaces_at_c = _surface_hierarchy_at_commit(calls, c_idx)
        app_subsurfaces = [
            child for child in subsurfaces_at_c
            if _find_toplevel_ancestor(child, subsurfaces_at_c, toplevels_at_c)[0] == s_id
        ]
        subsurf_attaches = []
        if app_subsurfaces:
            for child in sorted(app_subsurfaces):
                child_buf = _effective_buffer_at_commit(calls, child, c_idx)
                if child_buf is not None:
                    subsurf_attaches.append((child, child_buf))
        if subsurf_attaches:
            details = ", ".join(f"wl_surface#{s} (wl_buffer#{b})" for s, b in subsurf_attaches)
            raise TraceValidationError(
                f"autonomous L3p video surface attribution is ambiguous: toplevel window (wl_surface#{s_id}) "
                f"has active subsurface(s) with buffers at SDR commit: {details}"
            )

        raise _diagnose_missing_buffer(calls, s_id, c_idx, "SDR")
    if order_error:
        raise TraceValidationError(order_error)

    app_name = "CineHDR's" if expected_app_id == DEFAULT_APP_ID else f"{expected_app_id}'s"
    raise TraceValidationError(
        "no bound wp_color_manager_v1 proved perceptual intent, done, ready "
        f"image description, and later commit on {app_name} toplevel window surface"
    )


def validate_multiphase_trace(
    trace: str,
    expected_app_id: str = DEFAULT_APP_ID,
) -> dict[str, Any]:
    """Validate 4-phase SDR->HDR->SDR->HDR color-management sequence and video surface attribution.

    Rejects count-based heuristics (simply counting 4 set_image_description calls).
    Requires each phase to have:
    - a distinct image description reaching ready/ready2 before submission;
    - a set_image_description request;
    - a subsequent commit on that surface.
    Requires strictly chronological order across all 4 phases.

    Surface attribution:
    - If set_image_description occurs on a subsurface with attached video buffers, status is PASS.
    - If set_image_description occurs only on a toplevel window surface (where app_id applies
      to the entire window and no subsurface/offload video buffer chain exists), status is UNKNOWN.
    """
    expected_app_id = _validate_app_id(expected_app_id)
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
        (call, int(match["global_id"]), int(match["version"]))
        for call, match in globals_
        if match is not None
    ]
    if not manager_globals:
        raise TraceValidationError("wp_color_manager_v1 was not advertised by the registry")

    creations: dict[tuple[str, int], list[int]] = {}
    for call in calls:
        match = NEW_ID_RE.search(call.args)
        if match is not None:
            interface = match["interface"]
        else:
            match = UNKNOWN_NEW_ID_RE.search(call.args)
            interface = {
                ("wl_display", "get_registry"): "wl_registry",
                ("wl_compositor", "create_surface"): "wl_surface",
                ("wp_color_manager_v1", "get_surface"): "wp_color_management_surface_v1",
                ("wp_color_manager_v1", "create_parametric_creator"): "wp_image_description_creator_params_v1",
                ("wp_image_description_creator_params_v1", "create"): "wp_image_description_v1",
            }.get((call.interface, call.method))
        if match is not None and interface is not None:
            creations.setdefault((interface, int(match["object_id"])), []).append(call.index)

    bindings: list[tuple[TraceCall, TraceCall, int, int, int, int]] = []
    manager_bind_counts: dict[int, int] = {}
    for call in calls:
        if call.interface != "wl_registry" or call.method != "bind":
            continue
        match = BIND_RE.match(call.args)
        if match is None:
            continue
        global_id = int(match["global_id"])
        manager_id = int(match["manager_id"])
        manager_bind_counts[manager_id] = manager_bind_counts.get(manager_id, 0) + 1
        bound_version = int(match["version"])
        registry_creations = [
            index for index in creations.get(("wl_registry", call.object_id), [])
            if index < call.index
        ]
        if len(registry_creations) > 1:
            continue
        if registry_creations and any(
            earlier.interface == "wl_registry"
            and earlier.object_id == call.object_id
            and earlier.index < registry_creations[0]
            for earlier in calls
        ):
            continue
        advertisements = [
            (global_call, advertised_version)
            for global_call, advertised_id, advertised_version in manager_globals
            if global_call.object_id == call.object_id
            and advertised_id == global_id
            and global_call.index < call.index
            and (not registry_creations or global_call.index > registry_creations[0])
        ]
        if len(advertisements) != 1:
            continue
        global_call, advertised_version = advertisements[0]
        if not 1 <= bound_version <= advertised_version:
            continue
        bindings.append(
            (call, global_call, global_id, manager_id, advertised_version, bound_version)
        )
    if not bindings:
        raise TraceValidationError("wp_color_manager_v1 was advertised but not bound")

    toplevel_surfaces, subsurfaces, surface_attaches, viewport_surfaces = _surface_roles_and_attaches(calls)
    image_descs_info = _extract_image_descriptions_info(calls)
    candidate_unknown: dict[str, Any] | None = None

    for bind, global_call, global_id, manager_id, advertised_version, bound_version in bindings:
        valid_gen, mgr_end_index = _is_valid_manager_generation(bindings, bind, manager_id, calls)
        if not valid_gen:
            first_delete_after_bind = next(
                (
                    cd.index
                    for cd in calls
                    if cd.interface == "wl_display"
                    and cd.method == "delete_id"
                    and cd.args == str(manager_id)
                    and cd.index > bind.index
                ),
                None,
            )
            if first_delete_after_bind is not None and any(
                c.interface == "wp_color_manager_v1"
                and c.object_id == manager_id
                and c.index > first_delete_after_bind
                for c in calls
            ):
                raise TraceValidationError(
                    f"wp_color_manager_v1#{manager_id} was used after delete_id without a new bind"
                )
            continue
        perceptual = next(
            (
                call
                for call in calls
                if call.interface == "wp_color_manager_v1"
                and call.object_id == manager_id
                and call.method == "supported_intent"
                and call.args == "0"
                and bind.index < call.index <= mgr_end_index
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
                and perceptual.index < call.index <= mgr_end_index
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
                or not (bind.index < call.index <= mgr_end_index)
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
            wl_surface_creations = creations.get(("wl_surface", wl_surface_id), [])
            if (
                len(creations.get(("wp_color_management_surface_v1", color_surface_id), [])) != 1
                or _used_before_creation(
                    calls, "wp_color_management_surface_v1", color_surface_id,
                    get_surface.index,
                )
                or len(wl_surface_creations) > 1
                or (wl_surface_creations and wl_surface_creations[0] >= get_surface.index)
            ):
                continue

            set_desc_calls = [
                call for call in calls
                if call.interface == "wp_color_management_surface_v1"
                and call.object_id == color_surface_id
                and call.method == "set_image_description"
                and call.index > get_surface.index
            ]

            phases: list[dict[str, Any]] = []
            last_commit_idx = get_surface.index

            for set_desc in set_desc_calls:
                if set_desc.index <= last_commit_idx:
                    continue
                match = SET_DESCRIPTION_RE.match(set_desc.args)
                if match is None:
                    continue
                desc_id = int(match["description_id"])
                ready = _description_ready_before(calls, desc_id, set_desc.index)
                if ready is None:
                    continue
                ready_call, readiness = ready
                commit = _same_surface_commit_after(calls, wl_surface_id, set_desc.index)
                if commit is None:
                    continue
                if any(
                    c.interface == "wp_color_management_surface_v1"
                    and c.object_id == color_surface_id
                    and c.method == "unset_image_description"
                    and set_desc.index < c.index < commit.index
                    for c in calls
                ):
                    continue

                desc_info = image_descs_info.get(desc_id, {})
                color_type = desc_info.get("color_type", "unrevealed")

                phases.append({
                    "phase_index": len(phases),
                    "description_id": desc_id,
                    "color_type": color_type,
                    "primaries_named": desc_info.get("primaries_named"),
                    "tf_named": desc_info.get("tf_named"),
                    "ready_index": ready_call.index,
                    "readiness": readiness,
                    "set_image_description_index": set_desc.index,
                    "commit_index": commit.index,
                })
                last_commit_idx = commit.index

            if len(phases) < 4:
                continue

            phase4 = phases[:4]
            desc_ids = [p["description_id"] for p in phase4]
            color_sequence = [p["color_type"] for p in phase4]
            expected_sequence = ["srgb_bt709", "rec2100_pq", "srgb_bt709", "rec2100_pq"]
            color_alternation_proven = (
                len(set(desc_ids)) >= 2
                and color_sequence == expected_sequence
            )

            toplevel_wl_id, toplevel_app_id = _find_toplevel_ancestor(
                wl_surface_id, subsurfaces, toplevel_surfaces
            )
            is_target_app = (toplevel_app_id == expected_app_id)

            is_subsurface = wl_surface_id in subsurfaces
            has_viewport = wl_surface_id in viewport_surfaces
            attached_buffers = surface_attaches.get(wl_surface_id, [])
            has_buffers = len(attached_buffers) > 0
            is_toplevel = wl_surface_id in toplevel_surfaces

            subsurface_video_chain_proven = (
                is_target_app
                and is_subsurface
                and has_viewport
                and has_buffers
            )

            if subsurface_video_chain_proven:
                surface_role = "video_subsurface"
            elif is_subsurface:
                surface_role = "subsurface"
            elif is_toplevel:
                surface_role = "toplevel_window"
            else:
                surface_role = "unattributed_surface"

            base_matched = {
                "registry_global": {
                    "id": global_id,
                    "registry_id": global_call.object_id,
                    "event_index": global_call.index,
                    "advertised_version": advertised_version,
                },
                "manager": {
                    "id": manager_id,
                    "bind_index": bind.index,
                    "bound_version": bound_version,
                    "perceptual_intent_index": perceptual.index,
                    "done_index": done.index,
                },
                "surface": {
                    "color_management_surface_id": color_surface_id,
                    "wl_surface_id": wl_surface_id,
                    "toplevel_wl_surface_id": toplevel_wl_id,
                    "role": surface_role,
                    "app_id": toplevel_app_id,
                    "has_viewport": has_viewport,
                    "subsurface_video_chain_proven": subsurface_video_chain_proven,
                    "attached_buffers_count": len(attached_buffers),
                },
                "phases": phase4,
                "color_alternation_proven": color_alternation_proven,
            }

            if subsurface_video_chain_proven and color_alternation_proven:
                return {
                    "schema": TRACE_SCHEMA,
                    "revision": MULTIPHASE_REVISION,
                    "status": "PASS",
                    "scope": MULTIPHASE_SCOPE,
                    "matched": base_matched,
                }
            else:
                if not is_target_app:
                    app_name = "CineHDR" if expected_app_id == DEFAULT_APP_ID else expected_app_id
                    reason = (
                        f"target surface cannot be attributed to {app_name}: ancestor toplevel app_id "
                        f"is foreign or unobserved ({toplevel_app_id!r})"
                    )
                elif len(set(desc_ids)) < 2:
                    reason = (
                        f"color alternation (SDR->HDR->SDR->HDR) cannot be proven: all 4 phases use "
                        f"identical image description #{desc_ids[0]}"
                    )
                elif not color_alternation_proven:
                    reason = (
                        f"color alternation (SDR->HDR->SDR->HDR) cannot be proven: phase sequence "
                        f"{color_sequence} does not match expected alternating SDR->HDR->SDR->HDR"
                    )
                elif not has_viewport:
                    reason = (
                        "target video surface cannot be proven: subsurface has buffer attaches but lacks "
                        "wp_viewport video scaling role"
                    )
                elif is_toplevel:
                    reason = (
                        "target video surface cannot be proven from client trace: xdg_toplevel app_id "
                        "identifies window, but subsurface/offload video buffer chain is unobserved"
                    )
                else:
                    reason = (
                        "target video surface cannot be proven from client trace: subsurface/offload "
                        "video buffer chain is unobserved"
                    )
                unknown_candidate = {
                    "schema": TRACE_SCHEMA,
                    "revision": MULTIPHASE_REVISION,
                    "status": "UNKNOWN",
                    "scope": MULTIPHASE_SCOPE,
                    "reason": reason,
                    "matched": base_matched,
                }
                is_candidate_target = (
                    candidate_unknown is not None
                    and candidate_unknown["matched"]["surface"]["app_id"] == expected_app_id
                )
                if candidate_unknown is None or (is_target_app and not is_candidate_target):
                    candidate_unknown = unknown_candidate

    if candidate_unknown is not None:
        return candidate_unknown

    raise TraceValidationError(
        "no bound wp_color_manager_v1 proved a complete 4-phase sequence "
        "(SDR->HDR->SDR->HDR) of ready image-description submission and commit"
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
    trace_path: Path,
    publication_report_path: Path,
    backend: str,
    *,
    oracle: str | None = None,
    multiphase: bool = False,
    expected_app_id: str | None = None,
) -> dict[str, Any]:
    """Validate trace and prerequisite publication report without copying either."""
    if backend not in {"legacy", "gpu-next"}:
        raise TraceValidationError(f"unsupported publication backend: {backend!r}")
    report_lines = publication_report_path.read_text(encoding="utf-8").splitlines()
    if "Overall result: PASS" not in report_lines:
        raise TraceValidationError("publication prerequisite report did not PASS")
    if f"Expected backend: {backend}" not in report_lines:
        raise TraceValidationError("publication prerequisite report backend did not match")

    target_app_id = _validate_app_id(expected_app_id)

    is_legacy = (not multiphase) and (oracle not in ("primary_composite", "composite_window", "strict_multiphase"))
    if is_legacy and expected_app_id is not None:
        raise TraceValidationError(
            f"legacy oracle does not support app_id attribution ({expected_app_id!r} specified)"
        )

    trace_text = trace_path.read_text(encoding="utf-8", errors="replace")
    if multiphase or oracle == "strict_multiphase":
        evidence = validate_multiphase_trace(trace_text, expected_app_id=target_app_id)
    elif oracle in ("primary_composite", "composite_window"):
        evidence = validate_composite_window_trace(trace_text, expected_app_id=target_app_id)
    else:
        evidence = validate_trace(trace_text)
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
    *,
    oracle: str | None = None,
    multiphase: bool = False,
) -> dict[str, Any]:
    """Build hash-linked failure evidence without exposing a successful chain.

    This intentionally reads both inputs again. If either is no longer safely
    readable, callers must not emit a misleading hash-linked failure report.
    """
    if multiphase or oracle == "strict_multiphase":
        rev = MULTIPHASE_REVISION
    elif oracle in ("primary_composite", "composite_window"):
        rev = PRIMARY_COMPOSITE_REVISION
    else:
        rev = TRACE_REVISION
    return {
        "schema": TRACE_SCHEMA,
        "revision": rev,
        "status": "FAIL",
        "oracle": oracle if oracle else ("strict_multiphase" if multiphase else "legacy"),
        "backend": backend,
        "scope": FAIL_SCOPE,
        "error": _safe_failure_reason(error),
        "publication_report": _input_reference(publication_report_path),
        "trace": _input_reference(trace_path),
    }


def create_trace_only_report(
    trace_path: Path,
    expected_app_id: str,
    *,
    oracle: str | None = None,
) -> dict[str, Any]:
    """Validate a single raw Wayland trace for autonomous L3p evidence without CineHDR publication report."""
    effective_oracle = "composite_window" if oracle is None else oracle
    if effective_oracle not in ("composite_window", "primary_composite"):
        raise TraceValidationError(
            f"trace-only mode requires oracle composite_window ({effective_oracle!r} specified)"
        )
    if expected_app_id is None or not isinstance(expected_app_id, str) or not expected_app_id.strip():
        raise TraceValidationError("trace-only mode requires a non-empty app_id")
    target_app_id = expected_app_id.strip()

    trace_text = trace_path.read_text(encoding="utf-8", errors="replace")
    evidence = validate_composite_window_trace(
        trace_text, expected_app_id=target_app_id, trace_only=True
    )

    if evidence.get("status") == "UNKNOWN":
        evidence.update(
            {
                "trace_only": True,
                "protocol_evidence_level": "L3p",
                "expected_app_id": target_app_id,
                "observed_app_id": target_app_id,
                "trace": _input_reference(trace_path),
                "scope": TRACE_ONLY_UNKNOWN_SCOPE,
            }
        )
        return evidence

    # Fail-closed autonomous video surface proof (L3p):
    # PASS requires a verified non-zero wl_buffer active at the chosen HDR commit_index
    # per Wayland double-buffered surface state, and absence of subsurface offload ambiguity.
    effective_trace, _, _, _ = _partition_by_connection(trace_text, target_app_id, trace_only=True)
    calls = _calls(effective_trace)
    matched_surface = evidence.get("matched", {}).get("surface", {})
    target_surf = matched_surface.get("wl_surface_id")
    target_commit_index = matched_surface.get("commit_index")
    if target_surf is None or target_commit_index is None:
        raise TraceValidationError("target window surface could not be identified from trace evidence")

    toplevel_surfaces, subsurfaces = _surface_hierarchy_at_commit(calls, target_commit_index)

    effective_buffer = _effective_buffer_at_commit(calls, target_surf, target_commit_index)
    if effective_buffer is None:
        raise _diagnose_missing_buffer(calls, target_surf, target_commit_index, "HDR")

    # Check for subsurfaces belonging to target window at target_commit_index
    app_subsurfaces = [
        child for child in subsurfaces
        if _find_toplevel_ancestor(child, subsurfaces, toplevel_surfaces)[0] == target_surf
    ]

    evidence.update(
        {
            "trace_only": True,
            "protocol_evidence_level": "L3p",
            "expected_app_id": target_app_id,
            "observed_app_id": target_app_id,
            "trace": _input_reference(trace_path),
        }
    )
    matched_surface["effective_buffer_id"] = effective_buffer

    active_subsurfaces = []
    for child in app_subsurfaces:
        child_current, child_pending, child_pending_attached = _buffer_state_at_commit(
            calls, child, target_commit_index
        )
        if child_current is not None or (child_pending_attached and child_pending is not None):
            active_subsurfaces.append(child)

    if active_subsurfaces:
        subsurface_names = ", ".join(f"wl_surface#{s}" for s in sorted(active_subsurfaces))
        evidence["status"] = "UNKNOWN"
        evidence["reason"] = (
            f"active buffer detected on subsurface ({subsurface_names}); "
            "video surface origin cannot be unambiguously proven on toplevel window"
        )
        evidence["scope"] = TRACE_ONLY_UNKNOWN_SCOPE
    elif evidence.get("status") == "PASS":
        evidence["scope"] = TRACE_ONLY_L3P_SCOPE
    else:
        evidence["scope"] = TRACE_ONLY_UNKNOWN_SCOPE

    return evidence


def create_trace_only_failure_evidence(
    trace_path: Path,
    expected_app_id: str,
    error: TraceValidationError,
    *,
    oracle: str | None = None,
) -> dict[str, Any]:
    """Build hash-linked failure evidence for autonomous L3p trace validation."""
    effective_oracle = oracle if oracle else "composite_window"
    try:
        trace_text = trace_path.read_text(encoding="utf-8", errors="replace")
        calls = _calls(trace_text)
        toplevel_surfaces, _, _, _ = _surface_roles_and_attaches(calls)
        observed_app_ids = sorted(set(toplevel_surfaces.values()))
    except (OSError, UnicodeError):
        observed_app_ids = []

    observed_app_id = observed_app_ids[0] if len(observed_app_ids) == 1 else None

    return {
        "schema": TRACE_SCHEMA,
        "revision": PRIMARY_COMPOSITE_REVISION,
        "status": "FAIL",
        "oracle": effective_oracle,
        "expected_app_id": expected_app_id,
        "observed_app_id": observed_app_id,
        "observed_app_ids": observed_app_ids,
        "app_id": expected_app_id,
        "trace_only": True,
        "protocol_evidence_level": "L3p",
        "scope": TRACE_ONLY_FAIL_SCOPE,
        "error": _safe_failure_reason(error),
        "trace": _input_reference(trace_path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate passive WAYLAND_DEBUG color-management evidence"
    )
    parser.add_argument(
        "--trace-only",
        action="store_true",
        default=False,
        help="Autonomous L3p validation of a single raw Wayland trace without publication report",
    )
    parser.add_argument("--trace", type=Path, required=True, help="Path to raw Wayland client log")
    parser.add_argument(
        "--publication-report",
        type=Path,
        default=None,
        help="CineHDR publication report (required in standard mode, forbidden with --trace-only)",
    )
    parser.add_argument(
        "--backend",
        choices=("legacy", "gpu-next"),
        default=None,
        help="CineHDR render backend (required in standard mode, forbidden with --trace-only)",
    )
    parser.add_argument("--output", type=Path, required=True, help="Output JSON evidence path")
    parser.add_argument(
        "--oracle",
        choices=("composite_window", "primary_composite", "strict_multiphase", "legacy"),
        default=None,
        help="Color management validation oracle (default: composite_window if requested or legacy)",
    )
    parser.add_argument(
        "--app-id",
        default=None,
        help="Expected Wayland app_id for surface attribution (default: io.github.rusmikev.CineHDR; required with --trace-only)",
    )
    parser.add_argument(
        "--multiphase",
        action="store_true",
        default=False,
        help="Validate 4-step publication phase sequence (SDR->HDR->SDR->HDR) and video surface attribution (alias for --oracle strict_multiphase)",
    )
    args = parser.parse_args(argv)

    if args.trace_only:
        if args.publication_report is not None:
            parser.error("--publication-report cannot be used with --trace-only")
        if args.backend is not None:
            parser.error("--backend cannot be used with --trace-only")
        if args.multiphase:
            parser.error("--multiphase cannot be used with --trace-only")
        if not args.app_id or not args.app_id.strip():
            parser.error("--trace-only mode requires --app-id")
        effective_oracle = "composite_window" if args.oracle is None else args.oracle
        if effective_oracle not in ("composite_window", "primary_composite"):
            parser.error(f"--trace-only mode requires oracle composite_window (got {args.oracle!r})")

        try:
            evidence = create_trace_only_report(
                args.trace,
                args.app_id,
                oracle=effective_oracle,
            )
            args.output.write_text(
                json.dumps(evidence, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            if evidence.get("status") != "PASS":
                status = evidence.get("status")
                reason = evidence.get("reason", "unverified")
                print(
                    f"Wayland color-management trace non-PASS status: {status} ({reason})",
                    file=sys.stderr,
                )
                return 1
        except TraceValidationError as error:
            print(f"Wayland color-management trace rejected: {error}", file=sys.stderr)
            try:
                failure_evidence = create_trace_only_failure_evidence(
                    args.trace,
                    args.app_id,
                    error,
                    oracle=effective_oracle,
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

    if args.publication_report is None:
        parser.error("the following arguments are required: --publication-report")
    if args.backend is None:
        parser.error("the following arguments are required: --backend")

    effective_oracle = "strict_multiphase" if args.multiphase else args.oracle
    try:
        evidence = create_evidence_report(
            args.trace,
            args.publication_report,
            args.backend,
            oracle=effective_oracle,
            expected_app_id=args.app_id,
        )
        args.output.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if evidence.get("status") != "PASS":
            print(
                f"Wayland color-management trace non-PASS status: {evidence.get('status')} ({evidence.get('reason', 'unverified')})",
                file=sys.stderr,
            )
            return 1
    except TraceValidationError as error:
        print(f"Wayland color-management trace rejected: {error}", file=sys.stderr)
        try:
            failure_evidence = create_failure_evidence(
                args.trace,
                args.publication_report,
                args.backend,
                error,
                oracle=effective_oracle,
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
