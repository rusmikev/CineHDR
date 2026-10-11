#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Gate 2W Wayland GDK publication matrix and protocol-submission validation.
#
# Invariants:
# - Pinned Flatpak Candidate v4 container execution only (no local dev run fallback).
# - Separate processes for legacy and gpu-next backends (no runtime hot-swap).
# - Full SDR -> HDR -> SDR -> HDR publication matrix per backend.
# - Wayland color-management protocol trace captured in the same session.
# - Bound container instance tracking (--instance-id-fd=3) and session identity.
# - Instance metadata verified from $XDG_RUNTIME_DIR/.flatpak/<instance-id>/info.
# - Strict isolation: never touches or terminates existing user CineHDR instances.
# - Governed by ADR-0004 and ADR-0006.

set -euo pipefail
umask 077

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
report_root="${script_dir}/validation-reports"
run_stamp="$(date +%Y%m%d-%H%M%S)"
run_dir="${report_root}/wayland-publication-${run_stamp}-$$"

# Target Flatpak Candidate v5 identity
app_id="io.github.rusmikev.CineHDR"
candidate_v4_commit="8ed86c0b54694b75d1aaa9be75956d1d3a9d075901d90695e4e0d3d144970813"
expected_app_commit="${EXPECTED_APP_COMMIT:-}"
expected_runtime_commit="${EXPECTED_RUNTIME_COMMIT:-0bd5091046708e64bc882e8349877cfbae713f6a1e2d0487a5f10af820a2d792}"
runtime_base="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

# Fixture definitions
fixture_profile="hevc-main10-yuv420p10-lossless"
fixture_revision="hevc-main10-yuv420p10-mp4-v2"
hdr_sha256="bc437162b00b6565ca486ecc0a67704b6f099ebccf03cf671b2a0f87e94e021d"
sdr_sha256="41393a0ee9dd887a1f11d4b21946fef3d1f49e72127719eefd1ac40aa9d5e360"

# User instances storage for process isolation
declare -A user_instances=()

# Active process and instance tracking for signal traps and early errors
current_test_instance_id=""
current_runner_pid=""
current_backend_dir=""
in_cleanup=false

cleanup_instance() {
    local inst="$1"
    if [[ -z "${inst}" ]]; then
        return 0
    fi

    # 1. Strictly verify inst is numeric
    if [[ ! "${inst}" =~ ^[0-9]+$ ]]; then
        echo "SECURITY ERROR: cleanup_instance received invalid instance ID: '${inst}'" >&2
        return 1
    fi

    # 2. Strictly check against user_instances
    if [[ -n "${user_instances[${inst}]:-}" ]]; then
        echo "CRITICAL: refusal to kill user-owned CineHDR instance: ${inst}" >&2
        return 1
    fi

    # 3. Re-verify metadata from $XDG_RUNTIME_DIR/.flatpak/<inst>/info
    local inst_info="${runtime_base}/.flatpak/${inst}/info"
    if [[ ! -f "${inst_info}" ]]; then
        # Info unlinked or missing; verify via flatpak ps that instance is not running
        local ps_inst
        if ps_inst="$(flatpak ps --columns=instance 2>/dev/null)"; then
            if ! grep -qw "${inst}" <<< "${ps_inst}"; then
                # Instance is verified not in flatpak ps and info is absent
                return 0
            fi
            echo "ERROR: Flatpak instance ${inst} info file is missing but instance is STILL RUNNING in flatpak ps!" >&2
        else
            echo "ERROR: Flatpak instance ${inst} info file is missing and flatpak ps query failed!" >&2
        fi
        return 1
    fi

    local info_app info_id info_app_commit info_rt_commit
    info_app="$(grep -E '^name=' "${inst_info}" | head -n1 | cut -d'=' -f2- | tr -d '[:space:]' || true)"
    info_id="$(grep -E '^instance-id=' "${inst_info}" | head -n1 | cut -d'=' -f2- | tr -d '[:space:]' || true)"
    info_app_commit="$(grep -E '^app-commit=' "${inst_info}" | head -n1 | cut -d'=' -f2- | tr -d '[:space:]' || true)"
    info_rt_commit="$(grep -E '^runtime-commit=' "${inst_info}" | head -n1 | cut -d'=' -f2- | tr -d '[:space:]' || true)"

    if [[ "${info_app}" != "${app_id}" || "${info_id}" != "${inst}" || \
          ( -n "${expected_app_commit}" && "${info_app_commit}" != "${expected_app_commit}" ) || \
          "${info_rt_commit}" != "${expected_runtime_commit}" ]]; then
        echo "SECURITY ERROR: metadata mismatch on instance ${inst}, refusing to kill!" >&2
        echo "  Expected: app=${app_id}, id=${inst}, app_commit=${expected_app_commit:-<unpinned>}, rt_commit=${expected_runtime_commit}" >&2
        echo "  Found:    app=${info_app}, id=${info_id}, app_commit=${info_app_commit}, rt_commit=${info_rt_commit}" >&2
        return 1
    fi

    flatpak kill "${inst}" 2>/dev/null || true

    # Polling to strictly verify instance is terminated and info unlinked
    local poll=0
    local is_term=false
    while [[ ${poll} -lt 30 ]]; do
        local ps_inst
        if ps_inst="$(flatpak ps --columns=instance 2>/dev/null)"; then
            if ! grep -qw "${inst}" <<< "${ps_inst}" && [[ ! -f "${inst_info}" ]]; then
                is_term=true
                break
            fi
        fi
        sleep 0.1
        poll=$((poll + 1))
    done

    if [[ "${is_term}" != true ]]; then
        echo "WARNING: Flatpak instance ${inst} was not confirmed terminated within timeout" >&2
        return 1
    fi

    return 0
}

cleanup_handler() {
    local sig="${1:-EXIT}"
    if [[ "${in_cleanup}" == true ]]; then
        return 0
    fi
    in_cleanup=true

    local killed_instance=false
    local killed_pid=false
    local cleanup_error=""

    # 1. Stop runner process if active
    if [[ -n "${current_runner_pid:-}" ]] && kill -0 "${current_runner_pid}" 2>/dev/null; then
        kill -INT "${current_runner_pid}" 2>/dev/null || true
        sleep 1
        if kill -0 "${current_runner_pid}" 2>/dev/null; then
            kill -TERM "${current_runner_pid}" 2>/dev/null || true
            sleep 1
        fi
        if kill -0 "${current_runner_pid}" 2>/dev/null; then
            kill -KILL "${current_runner_pid}" 2>/dev/null || true
        fi
        killed_pid=true
    fi

    # 2. Cleanup test instance if known
    if [[ -n "${current_test_instance_id:-}" ]]; then
        if cleanup_instance "${current_test_instance_id}"; then
            killed_instance=true
        else
            cleanup_error="cleanup_instance refused or failed for instance ${current_test_instance_id}"
        fi
    fi

    # 3. Telemetry in cleanup_status.json
    local target_dir="${current_backend_dir:-${run_dir:-}}"
    if [[ -n "${target_dir}" && -d "${target_dir}" ]]; then
        # Do not overwrite already recorded cleanup_status.json (e.g. from NORMAL completion)
        if [[ ! -f "${target_dir}/cleanup_status.json" ]]; then
            local status="CLEAN"
            if [[ -n "${cleanup_error}" ]]; then
                status="ERROR"
            fi
            local cleaned_id_json="null"
            if [[ -n "${current_test_instance_id:-}" ]]; then
                cleaned_id_json="\"${current_test_instance_id}\""
            fi
            cat > "${target_dir}/cleanup_status.json" << EOF
{
  "schema": "cinehdr.gate2w.cleanup-status.v1",
  "timestamp_utc": "$(date -u +"%Y-%m-%dT%H:%M:%S.%6NZ")",
  "trigger_signal": "${sig}",
  "cleaned_instance_id": ${cleaned_id_json},
  "instance_terminated": ${killed_instance},
  "runner_pid_terminated": ${killed_pid},
  "user_instances_preserved": ${#user_instances[@]},
  "status": "${status}",
  "error": "${cleanup_error}"
}
EOF
        fi
    fi

    current_test_instance_id=""
    current_runner_pid=""

    if [[ "${sig}" != "EXIT" && "${sig}" != "NORMAL" ]]; then
        trap - EXIT INT TERM HUP
        kill -s "${sig}" $$ 2>/dev/null || exit 1
    fi
}

trap 'cleanup_handler EXIT' EXIT
trap 'cleanup_handler INT' INT
trap 'cleanup_handler TERM' TERM
trap 'cleanup_handler HUP' HUP

check_flatpak_preflight() {
    if ! command -v flatpak >/dev/null 2>&1; then
        echo "PREFLIGHT_BLOCKED: flatpak command not found" >&2
        return 1
    fi

    local ps_output
    if ! ps_output="$(flatpak ps --columns=instance,application 2>&1)"; then
        echo "PREFLIGHT_BLOCKED: flatpak ps failed to query running instances: ${ps_output}" >&2
        return 1
    fi

    user_instances=()
    while read -r inst app; do
        if [[ -n "${inst}" && "${app}" == "${app_id}" ]]; then
            user_instances["${inst}"]=1
        fi
    done <<< "${ps_output}"

    # Pinned commit check for Candidate v5 (MANDATORY before flatpak run)
    if [[ -z "${expected_app_commit}" ]]; then
        echo "PREFLIGHT_BLOCKED: Candidate v5 app commit is unpinned. Set expected_app_commit (or EXPECTED_APP_COMMIT) before running." >&2
        return 1
    fi

    if [[ "${expected_app_commit}" == "${candidate_v4_commit}" ]]; then
        echo "PREFLIGHT_BLOCKED: expected_app_commit points to Candidate v4 (${candidate_v4_commit}). Candidate v4 contains legacy path validation and cannot be used for Run 4; Candidate v5 required." >&2
        return 1
    fi

    if ! flatpak info "${app_id}" >/dev/null 2>&1; then
        echo "PREFLIGHT_BLOCKED: Flatpak application ${app_id} is not installed" >&2
        return 1
    fi

    local installed_app
    installed_app="$(flatpak info --show-commit "${app_id}" 2>/dev/null || true)"
    local runtime_ref
    runtime_ref="$(flatpak info --show-runtime "${app_id}" 2>/dev/null || true)"
    local installed_rt=""
    if [[ -n "${runtime_ref}" ]]; then
        installed_rt="$(flatpak info --show-commit "${runtime_ref}" 2>/dev/null || true)"
    fi

    if [[ "${installed_app}" != "${expected_app_commit}" || "${installed_rt}" != "${expected_runtime_commit}" ]]; then
        echo "PREFLIGHT_BLOCKED: CineHDR Flatpak Candidate v5 commit mismatch:" >&2
        echo "  App commit:     expected ${expected_app_commit}, got ${installed_app}" >&2
        echo "  Runtime commit: expected ${expected_runtime_commit}, got ${installed_rt}" >&2
        return 1
    fi

    return 0
}

evaluate_session_results() {
    local b_dir="$1"
    local backend="$2"
    local session_id="$3"
    local instance_id="${4:-}"
    local check_trace="${5:-true}"

    python3 - "${b_dir}" "${backend}" "${session_id}" "${instance_id}" "${check_trace}" \
        "${expected_app_commit}" "${expected_runtime_commit}" "${app_id}" "${script_dir}" << 'PY_EVAL_EOF'
import configparser
import hashlib
import json
from pathlib import Path
import sys

b_dir = Path(sys.argv[1]).resolve()
backend = sys.argv[2]
session_id = sys.argv[3]
instance_id = sys.argv[4] if len(sys.argv) > 4 else ""
check_trace = sys.argv[5].lower() == "true" if len(sys.argv) > 5 else True
expected_app = sys.argv[6] if len(sys.argv) > 6 else ""
expected_rt = sys.argv[7] if len(sys.argv) > 7 else ""
expected_app_id = sys.argv[8] if len(sys.argv) > 8 else "io.github.rusmikev.CineHDR"
script_dir = Path(sys.argv[9]).resolve() if len(sys.argv) > 9 else Path.cwd()

# 1. Check process exit code
exit_code_path = b_dir / "process_exit_code.txt"
if not exit_code_path.exists():
    sys.exit(f"FAIL: process_exit_code.txt missing in {b_dir}")

try:
    exit_code_val = int(exit_code_path.read_text(encoding="utf-8").strip())
except Exception as e:
    sys.exit(f"FAIL: invalid process_exit_code format: {e}")

if exit_code_val != 0:
    sys.exit(f"FAIL: runner process terminated with non-zero exit code {exit_code_val}")

# 1b. Check cleanup status (MANDATORY for PASS)
cleanup_path = b_dir / "cleanup_status.json"
if not cleanup_path.exists():
    sys.exit(f"FAIL: cleanup_status.json missing in {b_dir}")

try:
    cleanup_data = json.loads(cleanup_path.read_text(encoding="utf-8"))
    if not cleanup_data.get("instance_terminated", False):
        sys.exit(f"FAIL: Flatpak test instance was not verified terminated in cleanup_status.json: {cleanup_data.get('error', '')}")
    if cleanup_data.get("status") != "CLEAN":
        sys.exit(f"FAIL: cleanup_status is not CLEAN: {cleanup_data.get('status')}")
except Exception as e:
    sys.exit(f"FAIL: parsing cleanup_status.json failed: {e}")

# 2. Check publication report
reports = list(b_dir.glob(f"gate2-wayland-publication-{backend}-*.txt"))
if len(reports) != 1:
    sys.exit(f"FAIL: expected exactly 1 publication report for {backend}, found {len(reports)}")

report_path = reports[0]
report_text = report_path.read_text(encoding="utf-8")
report_sha = hashlib.sha256(report_text.encode("utf-8")).hexdigest()

report_lines = [line.strip() for line in report_text.splitlines()]
if "Overall result: PASS" not in report_lines:
    sys.exit(f"FAIL: publication report for {backend} did not PASS: {report_path}")
if "Surface submission test override: enabled" not in report_lines:
    sys.exit(f"FAIL: publication report lacks active test override: {report_path}")
if "Override scope: test setup; not proof of Wayland submission or normal auto-mode behavior" not in report_lines:
    sys.exit(f"FAIL: publication report lacks override scope disclosure: {report_path}")
if f"Expected backend: {backend}" not in report_lines:
    sys.exit(f"FAIL: publication report expected backend mismatch: expected {backend}")

exp_api = "opengl" if backend == "legacy" else "opengl-next"
if f"Active renderer API: {exp_api}" not in report_lines:
    sys.exit(f"FAIL: publication report active renderer API mismatch: expected {exp_api}")

# Check publication report provenance: Session ID, Flatpak instance ID, Process ID
report_session_id = None
report_instance_id = None
report_pid = None
for line in report_lines:
    if line.startswith("Session ID:"):
        report_session_id = line[len("Session ID:"):].strip()
    elif line.startswith("Flatpak instance ID:"):
        report_instance_id = line[len("Flatpak instance ID:"):].strip()
    elif line.startswith("Process ID:"):
        try:
            report_pid = int(line[len("Process ID:"):].strip())
        except Exception:
            pass

if not report_session_id:
    sys.exit(f"FAIL: publication report missing Session ID in {report_path}")
if not session_id:
    sys.exit("FAIL: missing session ID")
if report_session_id != session_id:
    sys.exit(f"FAIL: publication report session ID mismatch: expected '{session_id}', got '{report_session_id}'")

if not report_instance_id:
    sys.exit(f"FAIL: publication report missing Flatpak instance ID in {report_path}")
if not instance_id:
    sys.exit("FAIL: missing instance ID")
if report_instance_id != instance_id:
    sys.exit(f"FAIL: publication report Flatpak instance ID mismatch: expected '{instance_id}', got '{report_instance_id}'")

if report_pid is None or report_pid <= 0:
    sys.exit(f"FAIL: publication report missing or invalid Process ID in {report_path}")

# Check exact 4 publication states and their chronological order
expected_step_names = [
    "actual initial SDR publication",
    "switch SDR to HDR publication",
    "switch HDR to SDR publication",
    "switch SDR to HDR publication again",
]
pass_steps = []
for line in report_lines:
    if line.startswith("PASS:"):
        header = line[len("PASS:"):].strip()
        step_name = header.split(" — ")[0].strip() if " — " in header else header
        pass_steps.append(step_name)

if len(pass_steps) != len(expected_step_names):
    sys.exit(f"FAIL: publication matrix incomplete, expected {len(expected_step_names)} PASS states, got {len(pass_steps)}")

for i, (exp_step, act_step) in enumerate(zip(expected_step_names, pass_steps)):
    if act_step != exp_step:
        sys.exit(f"FAIL: publication step {i} sequence mismatch: expected '{exp_step}', got '{act_step}'")

# 3. Check instance ID and instance_info metadata
if not instance_id:
    sys.exit("FAIL: missing instance ID")
if not (b_dir / "instance_info").exists():
    sys.exit(f"FAIL: instance_info file missing in {b_dir}")

try:
    cp = configparser.ConfigParser()
    cp.read(b_dir / "instance_info", encoding="utf-8")
    if not cp.has_section("Application") or not cp.has_section("Instance"):
        sys.exit("FAIL: instance_info missing required [Application] or [Instance] section")

    app_name = cp.get("Application", "name", fallback="")
    obs_id = cp.get("Instance", "instance-id", fallback="")
    app_commit = cp.get("Instance", "app-commit", fallback="")
    rt_commit = cp.get("Instance", "runtime-commit", fallback="")

    if app_name != expected_app_id:
        sys.exit(f"FAIL: instance_info application mismatch: expected {expected_app_id}, got {app_name}")
    if obs_id != instance_id:
        sys.exit(f"FAIL: instance_info instance-id mismatch: expected {instance_id}, got {obs_id}")
    if app_commit != expected_app:
        sys.exit(f"FAIL: instance_info app-commit mismatch: expected {expected_app}, got {app_commit}")
    if len(app_commit) != 64:
        sys.exit(f"FAIL: instance_info app-commit is not full 64-char SHA-256: {app_commit}")
    if rt_commit != expected_rt:
        sys.exit(f"FAIL: instance_info runtime-commit mismatch: expected {expected_rt}, got {rt_commit}")
    if len(rt_commit) != 64:
        sys.exit(f"FAIL: instance_info runtime-commit is not full 64-char SHA-256: {rt_commit}")

    inst_verified = True
except Exception as e:
    sys.exit(f"FAIL: parsing or validating instance_info failed: {e}")

# 4. Check protocol trace if requested (MANDATORY for Gate 2W PASS)
if not check_trace:
    sys.exit("FAIL: Gate 2W requires protocol trace checking; --no-protocol-trace cannot produce PASS or container_session_correlated")

trace_path = b_dir / "wayland-client.raw.log"
protocol_json_path = b_dir / f"gate2-wayland-color-management-{backend}.json"
if not trace_path.exists():
    sys.exit(f"FAIL: wayland trace log missing: {trace_path}")
trace_sha = hashlib.sha256(trace_path.read_bytes()).hexdigest()

sys.path.insert(0, str(script_dir))
from tests.parse_wayland_color_trace import create_evidence_report, TraceValidationError

try:
    evidence = create_evidence_report(trace_path, report_path, backend, oracle="primary_composite")
    protocol_json_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    protocol_sha = hashlib.sha256(protocol_json_path.read_bytes()).hexdigest()
except TraceValidationError as e:
    sys.exit(f"FAIL: Wayland color trace rejected for {backend}: {e}")
except Exception as e:
    sys.exit(f"FAIL: unexpected error parsing trace: {e}")

if evidence.get("status") != "PASS":
    sys.exit(f"FAIL: protocol evidence status is not PASS: {evidence.get('status')} ({evidence.get('reason', '')})")

# 5. Check mandatory FBO pixel measurements artifact (MANDATORY for Gate 2W PASS)
pixel_artifacts = list(b_dir.glob(f"gate2-wayland-fbo-pixels-{backend}-*.json"))
if len(pixel_artifacts) != 1:
    sys.exit(f"FAIL: expected exactly 1 FBO pixel measurements artifact for {backend}, found {len(pixel_artifacts)}")

pixel_artifact_path = pixel_artifacts[0]
try:
    pixel_data = json.loads(pixel_artifact_path.read_text(encoding="utf-8"))
except Exception as e:
    sys.exit(f"FAIL: parsing FBO pixel measurements artifact failed: {e}")

from src.gpu_validation import verify_fbo_pixel_measurements_artifact
pix_ok, pix_err = verify_fbo_pixel_measurements_artifact(pixel_data)
if not pix_ok:
    sys.exit(f"FAIL: mandatory FBO pixel measurements artifact is incomplete or invalid: {pix_err}")

pixel_artifact_sha = hashlib.sha256(pixel_artifact_path.read_bytes()).hexdigest()
if f"FBO pixel artifact: {pixel_artifact_path.name}" not in report_lines:
    sys.exit(f"FAIL: publication report missing reference to FBO pixel artifact: {pixel_artifact_path.name}")
if f"FBO pixel artifact SHA-256: {pixel_artifact_sha}" not in report_lines:
    sys.exit(f"FAIL: publication report FBO pixel artifact SHA-256 mismatch")
if "FBO pixel artifact verified: True" not in report_lines:
    sys.exit(f"FAIL: publication report does not certify FBO pixel artifact verification")

# 6. Generate machine-readable session_facts.json
facts = {
    "schema": "cinehdr.gate2w.session-facts.v1",
    "backend": backend,
    "session_id": session_id,
    "observed_instance_id": instance_id,
    "instance_metadata_verified": inst_verified,
    "observed_process_exit_code": exit_code_val,
    "expected_app_commit": expected_app,
    "expected_runtime_commit": expected_rt,
    "fbo_pixel_measurements": {
        "path": pixel_artifact_path.name,
        "sha256": pixel_artifact_sha,
        "status": "PASS",
        "phases_captured": ["initial_sdr", "hdr", "return_sdr"],
        "tolerance": pixel_data.get("tolerance"),
        "tolerance_status": pixel_data.get("tolerance_status"),
    },
    "publication_report": {
        "path": report_path.name,
        "sha256": report_sha,
        "result": "PASS",
        "session_id": report_session_id,
        "instance_id": report_instance_id,
        "process_id": report_pid,
        "states_passed": len(pass_steps),
        "steps": pass_steps,
    },
    "wayland_trace": {
        "path": trace_path.name,
        "sha256": trace_sha,
        "protocol_report_path": protocol_json_path.name,
        "protocol_report_sha256": protocol_sha,
        "protocol_status": "PASS",
        "oracle": "primary_composite_window",
        "composite_window_surface_proven": True,
        "toplevel_wl_surface_id": evidence.get("matched", {}).get("surface", {}).get("wl_surface_id"),
        "color_management_surface_id": evidence.get("matched", {}).get("surface", {}).get("color_management_surface_id"),
    },
    "container_session_correlated": True,
    "same_process_wire_correlated": False,
    "process_correlation_scope": {
        "status": "CONTAINER_INSTANCE_AND_SESSION_PROVEN",
        "same_process_wire_proven": False,
        "provenance_facts": {
            "publication_report_session_id_matched": True,
            "publication_report_instance_id_matched": True,
            "publication_report_pid_recorded": report_pid,
            "container_metadata_app_matched": True,
            "container_instance_id_matched": True,
            "wayland_debug_stream_redirected_from_runner": True,
            "wire_protocol_pid_embedded": False,
        },
        "per_frame_presentation_certified": False,
        "compositor_scanout_certified": False,
        "display_photometry_certified": False,
        "clarification": "Correlation proves identical session, container instance, and runner stream redirection. Wayland wire protocol itself does not contain an in-protocol process PID or nonce; wire-level same-process correlation is not claimed and remains unproven.",
    },
    "backend_result": "PASS",
    "gate2w_overall_closed": False,
}
(b_dir / "session_facts.json").write_text(json.dumps(facts, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(f"PASS ({backend}): publication matrix and protocol submission verified in session {session_id}")
PY_EVAL_EOF
}

run_backend() {
    local backend="$1"
    local prot_trace="$2"

    if [[ -z "${expected_app_commit}" || "${expected_app_commit}" == "${candidate_v4_commit}" ]]; then
        echo "SECURITY ERROR: refusal to launch flatpak run with unpinned or Candidate v4 commit." >&2
        exit 1
    fi

    local session_id="gate2w-pub-${backend}-${run_stamp}"
    local backend_dir="${run_dir}/${backend}"
    mkdir -p -- "${backend_dir}"
    chmod 0700 "${backend_dir}"
    current_backend_dir="${backend_dir}"

    local log_path="${backend_dir}/wayland-client.raw.log"
    local marker="$(mktemp "${backend_dir}/.${backend}-start.XXXXXX")"
    local start_time
    start_time="$(date -u +"%Y-%m-%dT%H:%M:%S.%6NZ")"
    echo "${start_time}" > "${backend_dir}/start_time.txt"

    echo "=== Gate 2W Publication Validation: ${backend} ==="
    echo "Session ID:  ${session_id}"
    echo "Backend Dir: ${backend_dir}"

    local test_instance_id=""
    local runner_pid=""
    local exit_code=0

    local flatpak_args=(
        run --system --die-with-parent
        --commit="${expected_app_commit}"
        --runtime-commit="${expected_runtime_commit}"
        --instance-id-fd=3
        --env="CINEHDR_RENDER_BACKEND=${backend}"
        --env="CINEHDR_GPU_VALIDATION=publication"
        --env="CINEHDR_FORCE_SURFACE_SUBMISSION=1"
        --env="CINEHDR_GPU_VALIDATION_REPORT_DIR=${backend_dir}"
        --env="CINEHDR_PUBLICATION_EXPECTED_BACKEND=${backend}"
        --env="CINEHDR_PUBLICATION_FIXTURE_PROFILE=${fixture_profile}"
        --env="CINEHDR_PUBLICATION_FIXTURE_REVISION=${fixture_revision}"
        --env="CINEHDR_PUBLICATION_HDR_SHA256=${hdr_sha256}"
        --env="CINEHDR_PUBLICATION_SDR_SHA256=${sdr_sha256}"
        --env="CINEHDR_NON_UNIQUE=1"
        --env="CINEHDR_EXPERIMENTAL_COLOR_MGMT=1"
        --env="CINEHDR_DIAGNOSTICS_SESSION_ID=${session_id}"
        --env="CINEHDR_EXPECTED_APP_COMMIT=${expected_app_commit}"
        --env="CINEHDR_EXPECTED_RUNTIME_COMMIT=${expected_runtime_commit}"
    )
    if [[ "${prot_trace}" == true ]]; then
        flatpak_args+=(
            --env="WAYLAND_DEBUG=client"
            --env="GDK_DEBUG=color-mgmt"
        )
    fi
    flatpak_args+=(
        "--filesystem=${sdr_media}:ro"
        "--filesystem=${hdr_media}:ro"
        "--filesystem=${backend_dir}:rw"
        "${app_id}"
        "${sdr_media}" "${hdr_media}"
    )

    flatpak "${flatpak_args[@]}" 3>"${backend_dir}/instance.txt" >"${log_path}" 2>&1 &
    runner_pid=$!
    current_runner_pid="${runner_pid}"

    # Wait up to 5s for instance ID to appear
    local waited=0
    while [[ ${waited} -lt 50 ]]; do
        if [[ -s "${backend_dir}/instance.txt" ]]; then
            test_instance_id="$(tr -d '[:space:]' < "${backend_dir}/instance.txt")"
            if [[ "${test_instance_id}" =~ ^[0-9]+$ ]]; then
                break
            fi
        fi
        sleep 0.1
        waited=$((waited + 1))
    done

    if [[ -z "${test_instance_id}" || ! "${test_instance_id}" =~ ^[0-9]+$ ]]; then
        echo "FAIL: Failed to acquire valid numeric Flatpak instance ID" >&2
        kill -TERM "${runner_pid}" 2>/dev/null || true
        wait "${runner_pid}" 2>/dev/null || true
        exit 1
    fi
    current_test_instance_id="${test_instance_id}"

    # Verify that instance is NOT one of the user's pre-existing instances
    if [[ -n "${user_instances[${test_instance_id}]:-}" ]]; then
        echo "CRITICAL: Flatpak instance ID collision with user instance: ${test_instance_id}" >&2
        exit 1
    fi

    # Verify that the test instance is actively reported by flatpak ps
    local ps_run_output
    if ! ps_run_output="$(flatpak ps --columns=instance 2>&1)"; then
        echo "FAIL: flatpak ps failed to query running instances after launch: ${ps_run_output}" >&2
        kill -TERM "${runner_pid}" 2>/dev/null || true
        wait "${runner_pid}" 2>/dev/null || true
        exit 1
    fi
    if ! grep -qw "${test_instance_id}" <<< "${ps_run_output}"; then
        echo "FAIL: Flatpak test instance ${test_instance_id} is missing from flatpak ps" >&2
        kill -TERM "${runner_pid}" 2>/dev/null || true
        wait "${runner_pid}" 2>/dev/null || true
        exit 1
    fi

    # Acquire instance_info metadata from $XDG_RUNTIME_DIR/.flatpak/<instance-id>/info
    local inst_info_src="${runtime_base}/.flatpak/${test_instance_id}/info"
    local waited_info=0
    while [[ ! -s "${inst_info_src}" && ${waited_info} -lt 50 ]]; do
        sleep 0.1
        waited_info=$((waited_info + 1))
    done

    if [[ -s "${inst_info_src}" ]]; then
        cp -p "${inst_info_src}" "${backend_dir}/instance_info"
    else
        echo "FAIL: Flatpak instance info file missing or empty: ${inst_info_src}" >&2
        kill -TERM "${runner_pid}" 2>/dev/null || true
        wait "${runner_pid}" 2>/dev/null || true
        exit 1
    fi

    # Active monitoring with hard timeout: 60s
    local max_wait=60
    local elapsed=0
    local report_found=false
    local reports=()

    while [[ ${elapsed} -lt ${max_wait} ]]; do
        mapfile -t reports < <(
            find "${backend_dir}" -maxdepth 1 -type f \
                -name "gate2-wayland-publication-${backend}-*.txt" \
                -newer "${marker}" 2>/dev/null || true
        )
        if [[ ${#reports[@]} -eq 1 ]]; then
            report_found=true
            break
        fi
        if ! kill -0 "${runner_pid}" 2>/dev/null; then
            break
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done

    # Wait for process exit or terminate
    if kill -0 "${runner_pid}" 2>/dev/null; then
        if [[ "${report_found}" == true ]]; then
            local post_wait=0
            while kill -0 "${runner_pid}" 2>/dev/null && [[ ${post_wait} -lt 6 ]]; do
                sleep 1
                post_wait=$((post_wait + 1))
            done
        fi
        if kill -0 "${runner_pid}" 2>/dev/null; then
            echo "Gracefully stopping test instance (SIGINT)..."
            kill -INT "${runner_pid}" 2>/dev/null || true
            sleep 2
        fi
        if kill -0 "${runner_pid}" 2>/dev/null; then
            kill -TERM "${runner_pid}" 2>/dev/null || true
            sleep 1
        fi
        if [[ -n "${test_instance_id}" ]]; then
            cleanup_instance "${test_instance_id}"
        fi
    fi

    wait "${runner_pid}" 2>/dev/null || exit_code=$?
    echo "${exit_code}" > "${backend_dir}/process_exit_code.txt"
    date -u +"%Y-%m-%dT%H:%M:%S.%6NZ" > "${backend_dir}/end_time.txt"
    rm -f -- "${marker}"

    local inst_terminated=true
    local cleanup_err=""
    if [[ -n "${test_instance_id:-}" ]]; then
        local ps_inst
        if ps_inst="$(flatpak ps --columns=instance 2>/dev/null)"; then
            if grep -qw "${test_instance_id}" <<< "${ps_inst}" || [[ -f "${runtime_base}/.flatpak/${test_instance_id}/info" ]]; then
                if ! cleanup_instance "${test_instance_id}"; then
                    inst_terminated=false
                    cleanup_err="test instance ${test_instance_id} was not verified terminated"
                fi
            fi
        else
            inst_terminated=false
            cleanup_err="flatpak ps failed during post-execution cleanup verification"
        fi
    fi

    current_test_instance_id=""
    current_runner_pid=""

    # Record clean status in cleanup_status.json if not present
    if [[ ! -f "${backend_dir}/cleanup_status.json" ]]; then
        local cleaned_id_json="null"
        if [[ -n "${test_instance_id:-}" ]]; then
            cleaned_id_json="\"${test_instance_id}\""
        fi
        local clean_status="CLEAN"
        if [[ "${inst_terminated}" != true ]]; then
            clean_status="ERROR"
        fi
        cat > "${backend_dir}/cleanup_status.json" << EOF
{
  "schema": "cinehdr.gate2w.cleanup-status.v1",
  "timestamp_utc": "$(date -u +"%Y-%m-%dT%H:%M:%S.%6NZ")",
  "trigger_signal": "NORMAL",
  "cleaned_instance_id": ${cleaned_id_json},
  "instance_terminated": ${inst_terminated},
  "runner_pid_terminated": true,
  "user_instances_preserved": ${#user_instances[@]},
  "status": "${clean_status}",
  "error": "${cleanup_err}"
}
EOF
    fi

    evaluate_session_results "${backend_dir}" "${backend}" "${session_id}" "${test_instance_id}" "${prot_trace}"
}

write_gate2w_summary() {
    local target_backend="$1"

    python3 - "${run_dir}" "${target_backend}" << 'PY_SUMMARY_EOF'
import json
from pathlib import Path
import sys

run_dir = Path(sys.argv[1]).resolve()
target_backend = sys.argv[2]

legacy_dir = run_dir / "legacy"
gpu_next_dir = run_dir / "gpu-next"

legacy_facts = None
gpu_next_facts = None

if (legacy_dir / "session_facts.json").exists():
    try:
        legacy_facts = json.loads((legacy_dir / "session_facts.json").read_text(encoding="utf-8"))
    except Exception:
        pass

if (gpu_next_dir / "session_facts.json").exists():
    try:
        gpu_next_facts = json.loads((gpu_next_dir / "session_facts.json").read_text(encoding="utf-8"))
    except Exception:
        pass

legacy_passed = legacy_facts is not None and legacy_facts.get("backend_result") == "PASS"
gpu_next_passed = gpu_next_facts is not None and gpu_next_facts.get("backend_result") == "PASS"

backends_summary = {
    "legacy": {
        "status": "PASS" if legacy_passed else ("FAIL" if legacy_facts else "NOT_RUN"),
        "session_facts_present": legacy_facts is not None,
    },
    "gpu-next": {
        "status": "PASS" if gpu_next_passed else ("FAIL" if gpu_next_facts else "NOT_RUN"),
        "session_facts_present": gpu_next_facts is not None,
    },
}

# Gate 2W aggregate evaluation
if target_backend in ("legacy", "gpu-next"):
    gate2w_status = "OPEN"
    gate2w_closed = False
    reason = f"PARTIAL_NOT_CLOSED: only single backend '{target_backend}' was evaluated. Gate 2W requires full matrix across both legacy and gpu-next."
elif target_backend == "all":
    if legacy_passed and gpu_next_passed:
        gate2w_status = "GATE2W_EVIDENCE_GATHERED"
        gate2w_closed = False
        reason = "Both legacy and gpu-next passed the client-side publication and protocol matrix. Gate 2W adjudication remains separate; physical output, scanout and per-frame presentation are non-claims, not additional criteria for this row."
    else:
        gate2w_status = "OPEN"
        gate2w_closed = False
        failed = [b for b, s in backends_summary.items() if s["status"] != "PASS"]
        reason = f"One or more backends failed or were not run: {', '.join(failed)}"
else:
    gate2w_status = "OPEN"
    gate2w_closed = False
    reason = f"Unknown backend target '{target_backend}'"

summary = {
    "schema": "cinehdr.gate2w.aggregate-summary.v1",
    "backend_target": target_backend,
    "gate2w_status": gate2w_status,
    "gate2w_closed": gate2w_closed,
    "reason": reason,
    "backends": backends_summary,
    "claim_boundaries": {
        "container_session_correlated": True if (legacy_passed or gpu_next_passed) else False,
        "same_process_wire_correlated": False,
        "per_frame_presentation_correlated": False,
        "compositor_scanout_certified": False,
        "display_photometry_certified": False,
    },
}

summary_path = run_dir / "gate2w_summary.json"
summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(f"Gate 2W aggregate summary written: {summary_path} (status: {gate2w_status})")
PY_SUMMARY_EOF
}

main() {
    local protocol_trace=true
    local dry_run=false
    local backend_choice="all"
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --protocol-trace) protocol_trace=true; shift ;;
            --no-protocol-trace) protocol_trace=false; shift ;;
            --backend) backend_choice="$2"; shift 2 ;;
            --dry-run) dry_run=true; shift ;;
            -h|--help)
                echo "Usage: $0 [--protocol-trace|--no-protocol-trace] [--backend legacy|gpu-next|all] [--dry-run]" >&2
                exit 0
                ;;
            *)
                echo "Unknown argument: $1" >&2
                echo "Usage: $0 [--protocol-trace|--no-protocol-trace] [--backend legacy|gpu-next|all] [--dry-run]" >&2
                exit 2
                ;;
        esac
    done

    if [[ "${backend_choice}" != "all" && "${backend_choice}" != "legacy" && "${backend_choice}" != "gpu-next" ]]; then
        echo "Invalid --backend choice: ${backend_choice}. Must be legacy, gpu-next, or all." >&2
        exit 2
    fi

    if [[ "${dry_run}" != true && -z "${WAYLAND_DISPLAY:-}" ]]; then
        echo "CineHDR publication validation requires a Wayland session." >&2
        exit 1
    fi

    # Preflight check: strictly verify Flatpak, Candidate v5 commit pinning, and capture user instances
    check_flatpak_preflight

    # Resolve fixtures paths and status
    local known_hdr="${script_dir}/validation-reports/gate2-w4-hardware-20260926-095640/fixture/cinehdr-gate2-hevc-main10-yuv420p10-lossless-hdr.mp4"
    local fixture_dir="${report_root}/fixtures"
    mkdir -p -- "${fixture_dir}"
    local sdr_file="${fixture_dir}/cinehdr-gate2-hevc-main10-yuv420p10-lossless-sdr.mp4"
    local hdr_file="${fixture_dir}/cinehdr-gate2-hevc-main10-yuv420p10-lossless-hdr.mp4"

    # Reuse existing verified HDR fixture if available
    if [[ ! -f "${hdr_file}" && -f "${known_hdr}" ]]; then
        if [[ "$(sha256sum "${known_hdr}" 2>/dev/null | awk '{print $1}')" == "${hdr_sha256}" ]]; then
            cp -p "${known_hdr}" "${hdr_file}"
        fi
    fi

    local hdr_status="MISSING"
    if [[ -f "${hdr_file}" ]]; then
        local actual_hdr_sha
        actual_hdr_sha="$(sha256sum "${hdr_file}" 2>/dev/null | awk '{print $1}')"
        if [[ "${actual_hdr_sha}" == "${hdr_sha256}" ]]; then
            hdr_status="VERIFIED (${hdr_sha256:0:16}...)"
        else
            hdr_status="SHA256_MISMATCH (${actual_hdr_sha:0:16}... != ${hdr_sha256:0:16}...)"
        fi
    fi

    local sdr_status="MISSING"
    if [[ -f "${sdr_file}" ]]; then
        local actual_sdr_sha
        actual_sdr_sha="$(sha256sum "${sdr_file}" 2>/dev/null | awk '{print $1}')"
        if [[ "${actual_sdr_sha}" == "${sdr_sha256}" ]]; then
            sdr_status="VERIFIED (${sdr_sha256:0:16}...)"
        else
            sdr_status="SHA256_MISMATCH (${actual_sdr_sha:0:16}... != ${sdr_sha256:0:16}...)"
        fi
    fi

    if [[ "${dry_run}" == true ]]; then
        echo "=== CineHDR Gate 2W Dry Run ==="
        echo "Mode: dry-run (no processes launched, no GPU calls, no gate closure)"
        echo "Backend target: ${backend_choice}"
        echo "Protocol trace enabled: ${protocol_trace}"
        echo "Wayland session display: ${WAYLAND_DISPLAY:-none}"
        echo "Flatpak Candidate v5: VERIFIED (app: ${expected_app_commit:0:16}..., runtime: ${expected_runtime_commit:0:16}...)"
        echo "Active user instances protected: ${#user_instances[@]} found"
        echo "HDR fixture: ${hdr_status}"
        echo "SDR fixture: ${sdr_status}"
        echo "Dry run successful: CLI arguments, Flatpak Candidate v5 identity, flatpak ps preflight, and user instance isolation verified."
        exit 0
    fi

    # In live execution mode, both fixtures MUST be verified
    if [[ "${hdr_status}" != VERIFIED* ]]; then
        echo "PREFLIGHT_BLOCKED: HDR fixture not verified: ${hdr_status}" >&2
        exit 1
    fi
    if [[ "${sdr_status}" != VERIFIED* ]]; then
        echo "PREFLIGHT_BLOCKED: SDR fixture not verified: ${sdr_status}" >&2
        exit 1
    fi

    sdr_media="${sdr_file}"
    hdr_media="${hdr_file}"
    mkdir -p -- "${run_dir}"
    chmod 0700 "${run_dir}"

    if [[ "${backend_choice}" == "all" || "${backend_choice}" == "legacy" ]]; then
        run_backend legacy "${protocol_trace}"
    fi

    if [[ "${backend_choice}" == "all" || "${backend_choice}" == "gpu-next" ]]; then
        run_backend gpu-next "${protocol_trace}"
    fi

    write_gate2w_summary "${backend_choice}"

    echo "Gate 2W publication validation sequence completed successfully."
    echo "Reports preserved in: ${run_dir}"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
