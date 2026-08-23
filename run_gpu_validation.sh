#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mpv_prefix="${CINEHDR_MPV_PREFIX:-${script_dir}/../mpv-gpu-next-prefix}"
gpu_next_lib_dir="${mpv_prefix}/lib64"
gpu_next_lib="${gpu_next_lib_dir}/libmpv.so.2"

usage() {
    echo "Использование:" >&2
    echo "  ./run_gpu_validation.sh <видеофайл> [quick|soak|sdr-soak|lifecycle]" >&2
    echo "  ./run_gpu_validation.sh <SDR-файл> <HDR-файл> transition" >&2
}

video_files=()
if [[ $# -eq 1 ]]; then
    video_files=("$1")
    validation_mode="quick"
elif [[ $# -eq 2 && ( "$2" == "quick" || "$2" == "soak" || "$2" == "sdr-soak" || "$2" == "lifecycle" ) ]]; then
    video_files=("$1")
    validation_mode="$2"
elif [[ $# -eq 3 && "$3" == "transition" ]]; then
    video_files=("$1" "$2")
    validation_mode="transition"
else
    usage
    exit 2
fi

for video_file in "${video_files[@]}"; do
    if [[ ! -f "${video_file}" ]]; then
        echo "CineHDR: видеофайл не найден: ${video_file}" >&2
        exit 1
    fi
done

if [[ "${validation_mode}" == "transition" || "${validation_mode}" == "sdr-soak" ]]; then
    sdr_transfer="$(ffprobe -v error -select_streams v:0 \
        -show_entries stream=color_transfer -of default=nw=1:nk=1 \
        "${video_files[0]}" | head -n 1)"
    if [[ "${sdr_transfer}" != "bt709" ]]; then
        echo "CineHDR: первый файл должен быть подтверждённым SDR BT.709 (получено: ${sdr_transfer:-unknown})." >&2
        exit 2
    fi
fi

if [[ "${validation_mode}" == "transition" ]]; then
    hdr_transfer="$(ffprobe -v error -select_streams v:0 \
        -show_entries stream=color_transfer -of default=nw=1:nk=1 \
        "${video_files[1]}" | head -n 1)"
    if [[ "${hdr_transfer}" != "smpte2084" && "${hdr_transfer}" != "arib-std-b67" ]]; then
        echo "CineHDR: второй файл должен быть PQ или HLG HDR (получено: ${hdr_transfer:-unknown})." >&2
        exit 2
    fi
fi

if [[ ! -e "${gpu_next_lib}" ]]; then
    echo "CineHDR GPU Next: экспериментальный libmpv не найден: ${gpu_next_lib}" >&2
    exit 1
fi

if [[ -z "${WAYLAND_DISPLAY:-}" ]]; then
    echo "CineHDR GPU Next: сеанс Wayland не обнаружен; проверка HDR недоступна." >&2
    exit 1
fi

export LD_LIBRARY_PATH="${gpu_next_lib_dir}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
export GDK_BACKEND="wayland"
export CINEHDR_RENDER_BACKEND="gpu-next"
export CINEHDR_GPU_VALIDATION="${validation_mode}"
export CINEHDR_GPU_VALIDATION_REPORT_DIR="${script_dir}/validation-reports"

mkdir -p -- "${CINEHDR_GPU_VALIDATION_REPORT_DIR}"
run_stamp="$(date +%Y%m%d-%H%M%S)"
validation_log="${CINEHDR_GPU_VALIDATION_REPORT_DIR}/gpu-next-${validation_mode}-${run_stamp}-$$.log"
run_marker="$(mktemp "${CINEHDR_GPU_VALIDATION_REPORT_DIR}/.validation-start.XXXXXX")"
trap 'rm -f -- "${run_marker}"' EXIT

echo "CineHDR: полный журнал проверки: ${validation_log}"
cd -- "${script_dir}"
set +e
python3 -c \
    'import logging, runpy; logging.basicConfig(level=logging.INFO); runpy.run_path("run_dev.py", run_name="__main__")' \
    "${video_files[@]}" 2>&1 | tee "${validation_log}"
application_status="${PIPESTATUS[0]}"
set -e

if [[ "${application_status}" -ne 0 ]]; then
    echo "CineHDR: процесс проверки завершился с кодом ${application_status}. Журнал: ${validation_log}" >&2
    exit "${application_status}"
fi

new_report="$(find "${CINEHDR_GPU_VALIDATION_REPORT_DIR}" -maxdepth 1 -type f \
    -name "gpu-next-${validation_mode}-*.txt" -newer "${run_marker}" -print -quit)"
if [[ -z "${new_report}" ]]; then
    echo "CineHDR: процесс завершился без нового отчёта. Журнал: ${validation_log}" >&2
    exit 1
fi

echo "CineHDR: отчёт проверки: ${new_report}"
