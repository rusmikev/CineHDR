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
PRIMARY_COMPOSITE_REVISION = "wayland-composite-window-submission-v1"
SCOPE = (
    "PASS proves a ready image description was submitted and committed by the "
    "Wayland client without a traced protocol rejection. It does not prove "
    "compositor transformation, display mode, luminance, pixels, or color accuracy."
)
PRIMARY_COMPOSITE_SCOPE = (
    "PASS proves a ready image description was submitted and committed on CineHDR's "
    "dynamically identified composite window surface without a traced protocol rejection, "
    "satisfying ADR-0004. It does not prove compositor transformation, display mode, "
    "luminance, pixels, or color accuracy."
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
    r"^\s*wl_buffer(?:#|@)(?P<buffer_id>\d+)\s*,\s*(?P<x>-?\d+)\s*,\s*(?P<y>-?\d+)\s*$"
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


def _used_before_creation(
    calls: list[TraceCall], interface: str, object_id: int, creation_index: int
) -> bool:
    return any(
        call.interface == interface
        and call.object_id == object_id
        and call.index < creation_index
        for call in calls
    )


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
        if (
            manager_bind_counts[manager_id] != 1
            or _used_before_creation(calls, "wp_color_manager_v1", manager_id, bind.index)
        ):
            continue
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
                buf_id = int(match["buffer_id"])
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


def _extract_image_descriptions_info(calls: list[TraceCall]) -> dict[int, dict[str, Any]]:
    """Extract color metadata (primaries, tf, color_type) for image descriptions.

    Understands:
    1. wp_image_description_creator_params_v1 (set_primaries_named, set_tf_named, create).
    2. wp_image_description_info_v1 (primaries_named, tf_named).
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
                info_to_desc[int(m["info_id"])] = call.object_id
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
        prim = data.get("primaries_named")
        tf = data.get("tf_named")
        if prim == 1 and tf in (1, 2, 3, 9, 14):
            data["color_type"] = "srgb_bt709"
        elif prim == 6 and tf == 11:
            data["color_type"] = "rec2100_pq"
        elif prim == 6 and tf == 13:
            data["color_type"] = "rec2100_hlg"
        elif prim is not None or tf is not None:
            data["color_type"] = f"custom_p{prim}_tf{tf}"
        else:
            data["color_type"] = "unrevealed"

    return descs


def validate_composite_window_trace(
    trace: str,
    expected_app_id: str = "io.github.rusmikev.CineHDR",
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
    fatal = _fatal_error(trace)
    if fatal is not None:
        raise fatal
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
                ("wp_color_manager_v1", "create_icc_creator"): "wp_image_description_creator_icc_v1",
                ("wp_color_manager_v1", "create_windows_scrgb"): "wp_image_description_v1",
                ("wp_image_description_creator_params_v1", "create"): "wp_image_description_v1",
                ("wp_image_description_creator_icc_v1", "create"): "wp_image_description_v1",
                ("wp_color_management_surface_v1", "get_image_description"): "wp_image_description_v1",
                ("wp_color_management_output_v1", "get_image_description"): "wp_image_description_v1",
                ("wp_color_management_surface_feedback_v1", "get_preferred_parametric"): "wp_image_description_v1",
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
    cinehdr_window_surfaces = [
        surf for surf, app in toplevel_surfaces.items()
        if app == expected_app_id
    ]

    has_perceptual = False
    has_done = False
    has_get_surface_window = False
    order_error = None

    for bind, global_call, global_id, manager_id, advertised_version, bound_version in bindings:
        if (
            manager_bind_counts[manager_id] != 1
            or _used_before_creation(calls, "wp_color_manager_v1", manager_id, bind.index)
        ):
            continue
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
        has_perceptual = True

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
        has_done = True

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
            if wl_surface_id not in cinehdr_window_surfaces:
                continue
            has_get_surface_window = True

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
                if comp_bind is not None and comp_bind.object_id != bind.object_id:
                    order_error = (
                        f"wl_surface#{wl_surface_id} was created by wl_compositor#{comp_id} "
                        f"bound from foreign registry wl_registry#{comp_bind.object_id}"
                    )
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
                if wm_bind is not None and wm_bind.object_id != bind.object_id:
                    order_error = (
                        f"xdg_wm_base#{wm_id} for wl_surface#{wl_surface_id} was bound "
                        f"from foreign registry wl_registry#{wm_bind.object_id}"
                    )
                    continue

            manager_creator_ids: set[int] = set()
            for c in calls:
                if c.interface == "wp_color_manager_v1" and c.object_id == manager_id:
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

                if any(
                    c.interface == "wp_color_management_surface_v1"
                    and c.object_id == color_surface_id
                    and c.method == "unset_image_description"
                    and set_description.index < c.index < commit.index
                    for c in calls
                ):
                    order_error = (
                        f"wp_color_management_surface_v1#{color_surface_id} had unset_image_description "
                        f"called between set_image_description and wl_surface.commit"
                    )
                    continue

                return {
                    "schema": TRACE_SCHEMA,
                    "revision": PRIMARY_COMPOSITE_REVISION,
                    "status": "PASS",
                    "oracle": "primary_composite_window",
                    "scope": PRIMARY_COMPOSITE_SCOPE,
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
                        },
                    },
                }

    if not cinehdr_window_surfaces:
        raise TraceValidationError(
            f"no toplevel surface was attributed to CineHDR (app_id {expected_app_id!r} not found)"
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
        raise TraceValidationError(
            "wp_color_manager_v1.get_surface was not called for CineHDR's toplevel window surface"
        )
    if order_error:
        raise TraceValidationError(order_error)

    raise TraceValidationError(
        "no bound wp_color_manager_v1 proved perceptual intent, done, ready "
        "image description, and later commit on CineHDR's toplevel window surface"
    )


def validate_multiphase_trace(trace: str) -> dict[str, Any]:
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

    for bind, global_call, global_id, manager_id, advertised_version, bound_version in bindings:
        if (
            manager_bind_counts[manager_id] != 1
            or _used_before_creation(calls, "wp_color_manager_v1", manager_id, bind.index)
        ):
            continue
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
            is_cinehdr_app = (toplevel_app_id == "io.github.rusmikev.CineHDR")

            is_subsurface = wl_surface_id in subsurfaces
            has_viewport = wl_surface_id in viewport_surfaces
            attached_buffers = surface_attaches.get(wl_surface_id, [])
            has_buffers = len(attached_buffers) > 0
            is_toplevel = wl_surface_id in toplevel_surfaces

            subsurface_video_chain_proven = (
                is_cinehdr_app
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
                if not is_cinehdr_app:
                    reason = (
                        f"target surface cannot be attributed to CineHDR: ancestor toplevel app_id "
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
                return {
                    "schema": TRACE_SCHEMA,
                    "revision": MULTIPHASE_REVISION,
                    "status": "UNKNOWN",
                    "scope": MULTIPHASE_SCOPE,
                    "reason": reason,
                    "matched": base_matched,
                }

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
) -> dict[str, Any]:
    """Validate trace and prerequisite publication report without copying either."""
    if backend not in {"legacy", "gpu-next"}:
        raise TraceValidationError(f"unsupported publication backend: {backend!r}")
    report_lines = publication_report_path.read_text(encoding="utf-8").splitlines()
    if "Overall result: PASS" not in report_lines:
        raise TraceValidationError("publication prerequisite report did not PASS")
    if f"Expected backend: {backend}" not in report_lines:
        raise TraceValidationError("publication prerequisite report backend did not match")
    trace_text = trace_path.read_text(encoding="utf-8", errors="replace")
    if multiphase or oracle == "strict_multiphase":
        evidence = validate_multiphase_trace(trace_text)
    elif oracle in ("primary_composite", "composite_window"):
        evidence = validate_composite_window_trace(trace_text)
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate passive WAYLAND_DEBUG color-management evidence"
    )
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--publication-report", type=Path, required=True)
    parser.add_argument("--backend", choices=("legacy", "gpu-next"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--oracle",
        choices=("composite_window", "primary_composite", "strict_multiphase", "legacy"),
        default=None,
        help="Color management validation oracle (default: composite_window if requested or legacy)",
    )
    parser.add_argument(
        "--multiphase",
        action="store_true",
        default=False,
        help="Validate 4-step publication phase sequence (SDR->HDR->SDR->HDR) and video surface attribution (alias for --oracle strict_multiphase)",
    )
    args = parser.parse_args(argv)
    effective_oracle = "strict_multiphase" if args.multiphase else args.oracle
    try:
        evidence = create_evidence_report(
            args.trace,
            args.publication_report,
            args.backend,
            oracle=effective_oracle,
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
