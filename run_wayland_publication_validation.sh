#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Explicit Gate 2 GTK/GDK object-publication validation. This launcher is
# intentionally local-only: it neither installs anything nor selects a GPU.

set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
fixture_cache="$(mktemp -d /tmp/cinehdr-wayland-publication-fixtures.XXXXXX)"
report_root="${script_dir}/validation-reports"
run_stamp="$(date +%Y%m%d-%H%M%S)"
run_dir="${report_root}/wayland-publication-${run_stamp}-$$"
gpu_next_lib_dir="${script_dir}/../mpv-gpu-next-prefix/lib64"
gpu_next_lib="${gpu_next_lib_dir}/libmpv.so.2"

fixture_profile="hevc-main10-yuv420p10-lossless"
fixture_revision="hevc-main10-yuv420p10-mp4-v2"
hdr_sha256="bc437162b00b6565ca486ecc0a67704b6f099ebccf03cf671b2a0f87e94e021d"
sdr_sha256="41393a0ee9dd887a1f11d4b21946fef3d1f49e72127719eefd1ac40aa9d5e360"

protocol_trace=false
if [[ $# -eq 1 && "$1" == "--protocol-trace" ]]; then
    protocol_trace=true
elif [[ $# -ne 0 ]]; then
    echo "Usage: $0 [--protocol-trace]" >&2
    exit 2
fi

if [[ -z "${WAYLAND_DISPLAY:-}" ]]; then
    echo "CineHDR publication validation requires a Wayland session." >&2
    exit 1
fi
if [[ ! -e "${gpu_next_lib}" ]]; then
    echo "CineHDR publication validation: pinned libmpv is missing: ${gpu_next_lib}" >&2
    exit 1
fi

mkdir -p -- "${run_dir}"
fixture_manifest="${run_dir}/fixtures.json"

export LD_LIBRARY_PATH="${gpu_next_lib_dir}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
export GDK_BACKEND="wayland"

python3 "${script_dir}/tests/generate_patterns.py" \
    --cache-dir "${fixture_cache}" \
    --fixture-profile "${fixture_profile}" \
    --mode all > "${fixture_manifest}"

# Read paths from generator JSON plus each metadata file. Do not infer paths
# from cache names or globs: the metadata itself is the fixture provenance.
mapfile -t fixture_paths < <(
    python3 - "${fixture_manifest}" \
        "${fixture_profile}" "${fixture_revision}" \
        "${hdr_sha256}" "${sdr_sha256}" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

manifest_path = Path(sys.argv[1])
profile, revision, expected_hdr, expected_sdr = sys.argv[2:]
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
if manifest.get("fixture_profile") != profile:
    raise SystemExit("generator did not report the required fixture profile")
fixtures = manifest.get("fixtures")
if not isinstance(fixtures, list):
    raise SystemExit("generator output has no fixture list")
by_mode = {}
for fixture in fixtures:
    if not isinstance(fixture, dict) or fixture.get("mode") not in {"hdr", "sdr"}:
        raise SystemExit("generator output has an invalid fixture entry")
    mode = fixture["mode"]
    if mode in by_mode:
        raise SystemExit("generator output repeated a fixture mode")
    media = Path(str(fixture.get("path", "")))
    metadata_path = Path(str(fixture.get("metadata_path", "")))
    if media.suffix != ".mp4" or not media.is_file() or not metadata_path.is_file():
        raise SystemExit("generator did not produce an HEVC MP4 and metadata")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    expected_sha = expected_hdr if mode == "hdr" else expected_sdr
    if (metadata.get("fixture_profile") != profile or
            metadata.get("generator_revision") != revision or
            metadata.get("sha256") != expected_sha):
        raise SystemExit(f"{mode} fixture provenance did not match the accepted revision")
    actual_sha = hashlib.sha256(media.read_bytes()).hexdigest()
    if actual_sha != expected_sha:
        raise SystemExit(f"{mode} fixture media SHA-256 did not match the accepted fixture")
    by_mode[mode] = media
if set(by_mode) != {"sdr", "hdr"}:
    raise SystemExit("generator did not produce exactly SDR and HDR fixtures")
print(by_mode["sdr"])
print(by_mode["hdr"])
PY
)

if [[ ${#fixture_paths[@]} -ne 2 ]]; then
    echo "CineHDR publication validation: fixture provenance returned invalid paths." >&2
    exit 1
fi
sdr_media="${fixture_paths[0]}"
hdr_media="${fixture_paths[1]}"

# Independently check the produced bytes after parsing the recorded metadata.
if [[ "$(sha256sum -- "${sdr_media}" | awk '{print $1}')" != "${sdr_sha256}" ||
      "$(sha256sum -- "${hdr_media}" | awk '{print $1}')" != "${hdr_sha256}" ]]; then
    echo "CineHDR publication validation: generated media SHA-256 mismatch." >&2
    exit 1
fi

run_backend() {
    local backend="$1"
    local log_path="${run_dir}/publication-${backend}.log"
    local marker
    local application_status
    local reports
    local protocol_json
    local protocol_reports
    local -a process_env

    marker="$(mktemp "${run_dir}/.${backend}-start.XXXXXX")"
    echo "CineHDR publication validation (${backend}): ${log_path}"
    process_env=(
        "CINEHDR_RENDER_BACKEND=${backend}"
        "CINEHDR_GPU_VALIDATION=publication"
        "CINEHDR_GPU_VALIDATION_REPORT_DIR=${run_dir}"
        "CINEHDR_PUBLICATION_EXPECTED_BACKEND=${backend}"
        "CINEHDR_PUBLICATION_FIXTURE_PROFILE=${fixture_profile}"
        "CINEHDR_PUBLICATION_FIXTURE_REVISION=${fixture_revision}"
        "CINEHDR_PUBLICATION_HDR_SHA256=${hdr_sha256}"
        "CINEHDR_PUBLICATION_SDR_SHA256=${sdr_sha256}"
    )
    if [[ "${protocol_trace}" == true ]]; then
        # Do not export this globally: only the traced CineHDR child receives
        # passive libwayland and GTK color-management trace settings.
        process_env+=("WAYLAND_DEBUG=client")
        case ":${GDK_DEBUG:-}:" in
            *:color-mgmt:*) process_env+=("GDK_DEBUG=${GDK_DEBUG}") ;;
            ::) process_env+=("GDK_DEBUG=color-mgmt") ;;
            *) process_env+=("GDK_DEBUG=${GDK_DEBUG}:color-mgmt") ;;
        esac
    fi
    set +e
    (
        cd -- "${script_dir}"
        env "${process_env[@]}" \
            python3 "${script_dir}/run_dev.py" "${sdr_media}" "${hdr_media}"
    ) 2>&1 | tee "${log_path}"
    application_status="${PIPESTATUS[0]}"
    set -e
    if [[ "${application_status}" -ne 0 ]]; then
        echo "CineHDR publication validation (${backend}) exited ${application_status}." >&2
        exit "${application_status}"
    fi

    mapfile -t reports < <(
        find "${run_dir}" -maxdepth 1 -type f \
            -name "gate2-wayland-publication-${backend}-*.txt" \
            -newer "${marker}" -print
    )
    if [[ ${#reports[@]} -ne 1 ]]; then
        echo "CineHDR publication validation (${backend}) expected one fresh report, found ${#reports[@]}." >&2
        exit 1
    fi
    if ! grep -Fxq "Overall result: PASS" "${reports[0]}"; then
        echo "CineHDR publication validation (${backend}) did not PASS: ${reports[0]}" >&2
        exit 1
    fi
    echo "CineHDR publication report (${backend}): ${reports[0]}"

    if [[ "${protocol_trace}" == true ]]; then
        protocol_json="${run_dir}/gate2-wayland-color-management-${backend}-${run_stamp}.json"
        python3 "${script_dir}/tests/parse_wayland_color_trace.py" \
            --trace "${log_path}" \
            --publication-report "${reports[0]}" \
            --backend "${backend}" \
            --output "${protocol_json}"
        mapfile -t protocol_reports < <(
            find "${run_dir}" -maxdepth 1 -type f \
                -name "gate2-wayland-color-management-${backend}-*.json" \
                -newer "${marker}" -print
        )
        if [[ ${#protocol_reports[@]} -ne 1 ]]; then
            echo "CineHDR protocol trace (${backend}) expected one fresh JSON report, found ${#protocol_reports[@]}." >&2
            exit 1
        fi
        python3 - "${protocol_reports[0]}" "${backend}" <<'PY'
import json
from pathlib import Path
import sys

evidence = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
backend = sys.argv[2]
if (evidence.get("schema") != "cinehdr.gate2.wayland-color-management-trace.v1" or
        evidence.get("revision") != "wayland-client-submission-v1" or
        evidence.get("status") != "PASS" or
        evidence.get("backend") != backend):
    raise SystemExit("protocol JSON did not contain a strict PASS evidence record")
PY
        echo "CineHDR Wayland protocol report (${backend}): ${protocol_reports[0]}"
    fi
    rm -f -- "${marker}"
}

# Separate processes are mandatory; do not hot-swap the renderer API.
run_backend legacy
run_backend gpu-next
