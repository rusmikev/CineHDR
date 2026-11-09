#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Shell entry point for Native GNOME legacy trace and SDR texture validation (not Flatpak or L4).
#
# Usage:
#   tools/validation/run_gnome_full_trace_validation.sh [--duration SECONDS]

set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

exec python3 "${script_dir}/run_gnome_full_trace_validation.py" "$@"
