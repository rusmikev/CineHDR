#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd -- "${script_dir}/.." && pwd)"
repo_parent="$(cd -- "${repo_root}/.." && pwd)"
gpu_next_lib_dir="${repo_parent}/mpv-gpu-next-prefix/lib64"
gpu_next_lib="${gpu_next_lib_dir}/libmpv.so.2"

if [[ ! -e "${gpu_next_lib}" ]]; then
    echo "CineHDR GPU Next: экспериментальный libmpv не найден: ${gpu_next_lib}" >&2
    exit 1
fi

if [[ -z "${WAYLAND_DISPLAY:-}" ]]; then
    echo "CineHDR GPU Next: сеанс Wayland не обнаружен; HDR-вывод недоступен." >&2
    exit 1
fi

export LD_LIBRARY_PATH="${gpu_next_lib_dir}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
export GDK_BACKEND="wayland"

cd -- "${repo_root}"
exec python3 -c \
    'import logging, runpy; logging.basicConfig(level=logging.INFO); runpy.run_path("run_dev.py", run_name="__main__")' \
    "$@"
