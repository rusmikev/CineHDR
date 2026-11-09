#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Shell entry point for GNOME full pipeline & Wayland trace validation.
#
# Usage:
#   tools/validation/run_gnome_full_trace_validation.sh [--duration SECONDS]

set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd -- "${script_dir}/../.." && pwd -P)"

exec python3 "${script_dir}/run_gnome_full_trace_validation.py" "$@"
