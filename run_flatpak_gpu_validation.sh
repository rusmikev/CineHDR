#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
app_id="io.github.rusmikev.CineHDR"
bundle_path="${script_dir}/io.github.rusmikev.CineHDR-gpu-next.flatpak"
expected_commit="1ca32dbf0f0ef57a3b11545b1ff9cf2ec5ab837a6ff1d2328e52a556d759d5c5"
report_dir="${script_dir}/validation-reports"

usage() {
    echo "Usage: $0 <video-file> [quick]" >&2
}

print_install_hint() {
    echo "Install or update the corrected local bundle with:" >&2
    printf '  flatpak install --user --or-update %q\n' "${bundle_path}" >&2
}

if [[ $# -ne 1 && !( $# -eq 2 && "$2" == "quick" ) ]]; then
    usage
    exit 2
fi

if ! flatpak info --user "${app_id}" >/dev/null 2>&1; then
    echo "CineHDR Flatpak candidate is not installed." >&2
    print_install_hint
    exit 1
fi

installed_commit="$(flatpak info --show-commit --user "${app_id}")"
if [[ "${installed_commit}" != "${expected_commit}" ]]; then
    echo "CineHDR Flatpak candidate is stale: ${installed_commit}" >&2
    echo "Expected corrected GPU Next commit: ${expected_commit}" >&2
    print_install_hint
    exit 1
fi

if [[ ! -f "$1" ]]; then
    echo "CineHDR Flatpak validation: regular video file not found: $1" >&2
    exit 1
fi

if [[ -z "${WAYLAND_DISPLAY:-}" ]]; then
    echo "CineHDR Flatpak validation: a Wayland session is required." >&2
    exit 1
fi

video_path="$(realpath -e -- "$1")"
mkdir -p -- "${report_dir}"
run_stamp="$(date +%Y%m%d-%H%M%S)"
validation_log="${report_dir}/gpu-next-quick-${run_stamp}-$$.log"
run_marker="$(mktemp "${report_dir}/.flatpak-gpu-validation-start.XXXXXX")"
trap 'rm -f -- "${run_marker}"' EXIT

echo "CineHDR Flatpak validation log: ${validation_log}"
set +e
flatpak run \
    "--command=python3" \
    "--filesystem=${video_path}:ro" \
    "--filesystem=${report_dir}:rw" \
    "--env=GDK_BACKEND=wayland" \
    "--env=LIBVA_MESSAGING_LEVEL=2" \
    "--env=CINEHDR_RENDER_BACKEND=gpu-next" \
    "--env=CINEHDR_GPU_VALIDATION=quick" \
    "--env=CINEHDR_GPU_VALIDATION_REPORT_DIR=${report_dir}" \
    "${app_id}" -c \
    'import logging
import mpv
import runpy

logging.basicConfig(level=logging.INFO)
libmpv_logger = logging.getLogger("cinehdr.validation.libmpv")

def validation_mpv_log_handler(level, prefix, text):
    libmpv_logger.info("libmpv[%s] %s: %s", level, prefix, text.rstrip())

original_mpv_init = mpv.MPV.__init__

def validation_mpv_init(self, *args, **kwargs):
    kwargs["log_handler"] = validation_mpv_log_handler
    kwargs["loglevel"] = "v"
    return original_mpv_init(self, *args, **kwargs)

mpv.MPV.__init__ = validation_mpv_init
runpy.run_path("/app/bin/cinehdr", run_name="__main__")' \
    "${video_path}" 2>&1 | tee "${validation_log}"
application_status="${PIPESTATUS[0]}"
set -e

if [[ "${application_status}" -ne 0 ]]; then
    echo "CineHDR Flatpak validation failed with status ${application_status}. Log: ${validation_log}" >&2
    exit "${application_status}"
fi

new_report="$(find "${report_dir}" -maxdepth 1 -type f \
    -name 'gpu-next-quick-*.txt' -newer "${run_marker}" -print -quit)"
if [[ -z "${new_report}" ]]; then
    echo "CineHDR Flatpak validation ended without a new gpu-next quick report. Log: ${validation_log}" >&2
    exit 1
fi

echo "CineHDR Flatpak validation report: ${new_report}"
