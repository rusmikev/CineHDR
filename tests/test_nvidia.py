#!/usr/bin/env python3
"""Bounded Intel/NVIDIA renderer smoke runner.

This is an execution harness, not a gate adjudicator. Each selected process
must prove that the requested API and GPU were active, the pinned runtime was
loaded, the intended media path reached a timed video render, and matching HDR
telemetry appeared without application or file-open errors.

The runner deliberately does not claim pixel correctness, compositor or
display behavior, Dolby Vision certification, performance, or stability beyond
the short observation window. See ADR-0006.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time


PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_ID = "io.github.rusmikev.CineHDR"
REPORT_SCHEMA = "cinehdr-vendor-smoke-v2"
PLAY_DURATION = 8
SHUTDOWN_TIMEOUT = 3

PINNED_RUNTIME_TOKENS = (
    "mpv=mpv v0.41.0-dev-g97179bce7",
    "libplacebo=v7.360.1",
    "ffmpeg=8.1.2",
)

FIXTURE_SPECS = {
    "hdr10": {
        "label": "HDR10 PQ (HEVC BT.2020)",
        "relative_path": "01. Black Clipping_1_HDR10.mp4",
        "expected_sha256": "23b9dd904e0138e476946afaa7ecf644726e8a5a6f75d5aa75774ab9f5b6e82c",
        "expected_source_hdr": True,
        "expected_dovi_profile": None,
    },
    "hlg": {
        "label": "HLG (HEVC BT.2100)",
        "relative_path": "Grayscale BT.2100 HLG.mkv",
        "expected_sha256": "7b0f7fb469cf7980cc7107a3db5e8a0a01b4c008f3248c3c80eace9e040914f4",
        "expected_source_hdr": True,
        "expected_dovi_profile": None,
    },
    "dovi-p8": {
        "label": "Dolby Vision Profile 8 (HEVC)",
        "relative_path": "quietvoid/02themeg-dovi.mkv",
        "expected_sha256": "66f0a93ee9909ad0229102aafb63542791f98e7f991c6e3f783b4f14ee630675",
        "expected_source_hdr": True,
        "expected_dovi_profile": 8,
    },
    "colorbars-pq": {
        "label": "HDR10 PQ color bars",
        "relative_path": "colorbars.mp4",
        "expected_sha256": "6adcda398d75f100d23daeb53a9f71967c7f9281e21547b1acde01e533cf11cf",
        "expected_source_hdr": True,
        "expected_dovi_profile": None,
    },
}

TEST_CONFIGS = {
    "intel-legacy": {
        "label": "Intel (iGPU) - Legacy",
        "vendor_family": "intel",
        "mode": "flatpak",
        "env": {},
        "expected_vendor": "intel",
        "expected_renderer_token": "iris",
        "expected_api": "opengl",
        "requested_mode": "legacy",
    },
    "intel-gpu-next": {
        "label": "Intel (iGPU) - GPU Next",
        "vendor_family": "intel",
        "mode": "flatpak",
        "env": {},
        "expected_vendor": "intel",
        "expected_renderer_token": "iris",
        "expected_api": "opengl-next",
        "requested_mode": "gpu-next",
    },
    "nvidia-legacy": {
        "label": "NVIDIA (dGPU) - Legacy",
        "vendor_family": "nvidia",
        "mode": "flatpak",
        "env": {
            "__NV_PRIME_RENDER_OFFLOAD": "1",
            "__GLX_VENDOR_LIBRARY_NAME": "nvidia",
        },
        "expected_vendor": "nvidia",
        "expected_renderer_token": "rtx 3050",
        "expected_api": "opengl",
        "requested_mode": "legacy",
    },
    "nvidia-gpu-next": {
        "label": "NVIDIA (dGPU) - GPU Next",
        "vendor_family": "nvidia",
        "mode": "flatpak",
        "env": {
            "__NV_PRIME_RENDER_OFFLOAD": "1",
            "__GLX_VENDOR_LIBRARY_NAME": "nvidia",
        },
        "expected_vendor": "nvidia",
        "expected_renderer_token": "rtx 3050",
        "expected_api": "opengl-next",
        "requested_mode": "gpu-next",
    },
}

_IGNORE_PATTERNS = (
    "keyboardinterrupt",
    "gsk-warning",
    "deprecationwarning",
    "exception ignored in:",
    "ioflags",
    "pymapping_haskeystring",
)
_FILE_ERROR_PATTERNS = ("file error path:",)

_BACKEND_RE = re.compile(
    r"render backend requested=(?P<requested>\S+) "
    r"active=(?P<active>\S+) fallback_reason=(?P<fallback>.*)$",
    re.IGNORECASE,
)
_FIRST_FRAME_RE = re.compile(
    r"Rendered first frame backend=(?P<backend>\S+) "
    r"target=(?P<width>\d+)x(?P<height>\d+) .* depth=(?P<depth>\d+)",
    re.IGNORECASE,
)
_MEDIA_FRAME_RE = re.compile(
    r"Rendered first media frame backend=(?P<backend>\S+) "
    r"source=(?P<width>\d+)x(?P<height>\d+) "
    r"time_pos=(?P<time>[0-9]+(?:\.[0-9]+)?) "
    r"path_token=(?P<path_token>[0-9a-f]{64})",
    re.IGNORECASE,
)

_BOOTSTRAP = r'''import logging
import os
import runpy
import mpv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(os.environ["CINEHDR_LOG_FILE"], mode="w", encoding="utf-8"),
        logging.StreamHandler(),
    ],
    force=True,
)

original_mpv_init = mpv.MPV.__init__

def isolated_mpv_init(self, *args, **kwargs):
    kwargs["config"] = False
    kwargs.pop("config_dir", None)
    kwargs["autocreate_playlist"] = "no"
    kwargs["resume_playback"] = False
    kwargs["save_position_on_quit"] = False
    kwargs["save_watch_history"] = False
    kwargs["loop_file"] = "no"
    kwargs["loop_playlist"] = "no"
    return original_mpv_init(self, *args, **kwargs)

mpv.MPV.__init__ = isolated_mpv_init
runpy.run_path(os.environ["CINEHDR_ENTRYPOINT"], run_name="__main__")
'''


class PreflightError(RuntimeError):
    """A condition prevented CineHDR from starting; no attempt was consumed."""


def get_sha256(filepath: os.PathLike[str] | str) -> str:
    """Return the SHA-256 of a required regular file."""

    path = Path(filepath)
    if not path.is_file():
        raise PreflightError(f"required regular file is absent: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def path_token(filepath: os.PathLike[str] | str) -> str:
    canonical = os.path.realpath(os.path.abspath(os.fspath(filepath)))
    return hashlib.sha256(os.fsencode(canonical)).hexdigest()


def resolve_fixture_paths(
    fixture_root: os.PathLike[str] | str,
    fixture_ids: list[str],
) -> dict[str, Path]:
    """Resolve every selected fixture or fail before an application starts."""

    root = Path(fixture_root).expanduser().resolve()
    if not root.is_dir():
        raise PreflightError(f"fixture root is not a directory: {root}")
    resolved = {}
    for fixture_id in fixture_ids:
        path = (root / FIXTURE_SPECS[fixture_id]["relative_path"]).resolve()
        if not path.is_file():
            raise PreflightError(
                f"fixture {fixture_id!r} is absent at its prescribed relative path"
            )
        resolved[fixture_id] = path
    return resolved


def validate_fixture_hashes(fixture_paths: dict[str, Path]) -> dict[str, str]:
    """Reject changed media before CineHDR or a GPU process starts."""

    hashes = {}
    for fixture_id, path in fixture_paths.items():
        actual = get_sha256(path)
        expected = FIXTURE_SPECS[fixture_id]["expected_sha256"]
        if actual != expected:
            raise PreflightError(
                f"fixture {fixture_id!r} SHA-256 mismatch: "
                f"expected {expected}, got {actual}"
            )
        hashes[fixture_id] = actual
    return hashes


def _parse_json_object(line: str) -> dict[str, object] | None:
    try:
        start = line.index("{")
        end = line.rindex("}") + 1
        value = json.loads(line[start:end])
    except (ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def parse_log_lines(lines: list[str]) -> dict[str, object]:
    """Extract structured smoke evidence from combined application logs."""

    parsed: dict[str, object] = {
        "lines": [line.rstrip("\n") for line in lines],
        "backend_state": None,
        "first_frame": None,
        "media_frame": None,
        "telemetry": None,
        "runtime_line": None,
        "errors": [],
        "file_open_errors": [],
        "key_logs": [],
    }

    for line in parsed["lines"]:
        lower = line.lower()

        if any(pattern in lower for pattern in _FILE_ERROR_PATTERNS):
            parsed["file_open_errors"].append(line.strip())
            continue

        if any(word in lower for word in ("error", "critical", "fatal", "traceback")):
            if not any(ignored in lower for ignored in _IGNORE_PATTERNS):
                parsed["errors"].append(line.strip())

        backend_match = _BACKEND_RE.search(line)
        if backend_match and parsed["backend_state"] is None:
            parsed["backend_state"] = backend_match.groupdict()

        first_frame_match = _FIRST_FRAME_RE.search(line)
        if first_frame_match and parsed["first_frame"] is None:
            values = first_frame_match.groupdict()
            parsed["first_frame"] = {
                "backend": values["backend"],
                "target_width": int(values["width"]),
                "target_height": int(values["height"]),
                "depth": int(values["depth"]),
            }

        media_match = _MEDIA_FRAME_RE.search(line)
        if media_match and parsed["media_frame"] is None:
            values = media_match.groupdict()
            parsed["media_frame"] = {
                "backend": values["backend"],
                "source_width": int(values["width"]),
                "source_height": int(values["height"]),
                "time_pos": float(values["time"]),
                "path_token": values["path_token"].lower(),
            }

        if "render runtime " in lower and "opengl=" in lower:
            parsed["runtime_line"] = line.strip()

        if "hdr pipeline telemetry:" in lower:
            candidate = _parse_json_object(line)
            current = parsed["telemetry"]
            if candidate is not None and (
                current is None
                or (candidate.get("source_hdr") and not current.get("source_hdr"))
            ):
                parsed["telemetry"] = candidate

        if any(
            token in lower
            for token in (
                "render backend",
                "render runtime",
                "rendered first frame",
                "rendered first media frame",
                "hdr pipeline telemetry",
                "file error path:",
            )
        ):
            parsed["key_logs"].append(line.strip())

    return parsed


def parse_log_file(log_path: os.PathLike[str] | str) -> dict[str, object]:
    path = Path(log_path)
    if not path.is_file():
        return parse_log_lines([])
    return parse_log_lines(
        path.read_text(encoding="utf-8", errors="replace").splitlines()
    )


def _runtime_matches(runtime_line: str | None) -> bool:
    return bool(runtime_line) and all(
        token.lower() in runtime_line.lower() for token in PINNED_RUNTIME_TOKENS
    )


def evaluate_result(
    result: dict[str, object],
    config: dict[str, object],
    fixture_spec: dict[str, object],
    *,
    expected_path_token: str,
) -> dict[str, object]:
    """Apply the v2 oracle without interpreting an aggregate project gate."""

    errors = list(result.get("errors") or [])
    file_errors = list(result.get("file_open_errors") or [])
    stub_errors = [
        error
        for error in errors
        if "stub" in error.lower() or "notimplementederror" in error.lower()
    ]
    real_errors = [error for error in errors if error not in stub_errors]

    def fail(status: str, reason: str) -> dict[str, object]:
        result["status"] = status
        result["failure_reason"] = reason
        return result

    if result.get("forced_termination"):
        return fail("CRASH", "application ignored SIGINT and required SIGKILL")
    if stub_errors:
        return fail(
            "STUB_UNSUPPORTED",
            "the requested renderer API is a libmpv stub; this is not GPU Next coverage",
        )
    if result.get("exited_early"):
        return fail("EARLY_EXIT", "application exited before the observation window ended")
    if result.get("exit_code") not in (0, -2, -15, 130):
        return fail("CRASH", f"unexpected exit code: {result.get('exit_code')}")
    if not result.get("log_written"):
        return fail("NO_LOG", "no application log was captured")
    if file_errors:
        return fail("FILE_PLAYBACK_ERROR", "the application reported a file-open error")
    if real_errors:
        return fail("APPLICATION_ERROR", f"application errors: {real_errors[:2]}")

    backend = result.get("backend_state")
    if not isinstance(backend, dict):
        return fail("BACKEND_NOT_OBSERVED", "render backend state was not logged")
    expected_api = config["expected_api"]
    if (
        backend.get("requested") != config["requested_mode"]
        or backend.get("active") != expected_api
        or backend.get("fallback") != "none"
    ):
        return fail(
            "BACKEND_MISMATCH",
            f"expected strict {config['requested_mode']}/{expected_api}/none, got {backend}",
        )

    runtime_line = result.get("runtime_line")
    if not _runtime_matches(runtime_line if isinstance(runtime_line, str) else None):
        return fail(
            "RUNTIME_MISMATCH",
            "the exact pinned mpv/libplacebo/FFmpeg stack was not observed",
        )
    runtime_lower = runtime_line.lower()
    if config["expected_vendor"] not in runtime_lower:
        return fail("VENDOR_MISMATCH", "the expected GL vendor was not observed")
    if config["expected_renderer_token"] not in runtime_lower:
        return fail("RENDERER_MISMATCH", "the expected GPU renderer was not observed")

    first_frame = result.get("first_frame")
    if not isinstance(first_frame, dict) or first_frame.get("backend") != expected_api:
        return fail(
            "FRAME_NOT_OBSERVED",
            "a target render for the expected API was not observed",
        )

    media_frame = result.get("media_frame")
    if not isinstance(media_frame, dict):
        return fail(
            "MEDIA_FRAME_NOT_OBSERVED",
            "no loaded timed video frame reached the render call",
        )
    if media_frame.get("backend") != expected_api:
        return fail(
            "MEDIA_FRAME_BACKEND_MISMATCH",
            "the media-frame marker used another API",
        )
    if media_frame.get("path_token") != expected_path_token:
        return fail(
            "MEDIA_IDENTITY_MISMATCH",
            "the rendered media path was not the prescribed fixture",
        )
    if (
        int(media_frame.get("source_width", 0)) <= 0
        or int(media_frame.get("source_height", 0)) <= 0
        or float(media_frame.get("time_pos", -1)) < 0
    ):
        return fail("MEDIA_FRAME_INVALID", "the media-frame structure is invalid")

    telemetry = result.get("telemetry")
    if not isinstance(telemetry, dict):
        return fail("NO_TELEMETRY", "HDR pipeline telemetry was not observed")
    if telemetry.get("source_hdr") is not fixture_spec["expected_source_hdr"]:
        return fail(
            "TELEMETRY_MISMATCH",
            "source HDR state does not match the fixture contract",
        )
    if telemetry.get("dovi_profile") != fixture_spec["expected_dovi_profile"]:
        return fail(
            "TELEMETRY_MISMATCH",
            "Dolby Vision profile does not match the fixture contract",
        )
    if telemetry.get("target_trc") not in ("auto", "pq"):
        return fail(
            "TELEMETRY_MISMATCH",
            "target transfer is neither the SDR fallback nor PQ",
        )

    result["status"] = "PASS"
    result.pop("failure_reason", None)
    return result


def _sanitize_line(line: str, private_paths: list[str]) -> str:
    sanitized = line
    replacements = [(str(PROJECT_ROOT), "<checkout>"), (str(Path.home()), "<home>")]
    replacements.extend((path, "<fixture>") for path in private_paths)
    for source, replacement in sorted(
        replacements, key=lambda item: len(item[0]), reverse=True
    ):
        if source:
            sanitized = sanitized.replace(source, replacement)
    return sanitized


def _application_command(
    config: dict[str, object],
    video_path: Path,
    artifact_dir: Path,
    application_log: Path,
    state_dir: Path,
) -> list[str]:
    gpu_env = [
        f"--env={name}={value}"
        for name, value in sorted(config["env"].items())
    ]
    return [
        "flatpak",
        "run",
        "--command=python3",
        f"--filesystem={video_path}:ro",
        f"--filesystem={artifact_dir}:rw",
        "--env=PYTHONUNBUFFERED=1",
        "--env=GDK_BACKEND=wayland",
        "--env=GSETTINGS_BACKEND=memory",
        "--env=CINEHDR_NON_UNIQUE=1",
        f"--env=CINEHDR_RENDER_BACKEND={config['requested_mode']}",
        f"--env=CINEHDR_LOG_FILE={application_log}",
        "--env=CINEHDR_ENTRYPOINT=/app/bin/cinehdr",
        f"--env=XDG_CONFIG_HOME={state_dir / 'config'}",
        f"--env=XDG_CACHE_HOME={state_dir / 'cache'}",
        f"--env=XDG_STATE_HOME={state_dir / 'state'}",
        *gpu_env,
        APP_ID,
        "-c",
        _BOOTSTRAP,
        str(video_path),
    ]


def run_test(
    config_id: str,
    fixture_id: str,
    video_path: Path,
    fixture_sha256: str,
    artifact_dir: Path,
) -> dict[str, object]:
    """Run one bounded application process and retain raw log artifacts."""

    config = TEST_CONFIGS[config_id]
    fixture_spec = FIXTURE_SPECS[fixture_id]
    safe_name = f"{config_id}-{fixture_id}"
    application_log = artifact_dir / f"{safe_name}.application.log"
    process_log = artifact_dir / f"{safe_name}.process.log"
    state_dir = artifact_dir / f"{safe_name}.state"
    state_dir.mkdir()

    env = os.environ.copy()
    env.update(config["env"])
    env.update(
        {
            "PYTHONUNBUFFERED": "1",
            "GDK_BACKEND": "wayland",
            "GSETTINGS_BACKEND": "memory",
            "CINEHDR_NON_UNIQUE": "1",
            "CINEHDR_RENDER_BACKEND": config["requested_mode"],
            "CINEHDR_LOG_FILE": str(application_log),
            "CINEHDR_ENTRYPOINT": "/app/bin/cinehdr",
            "XDG_CONFIG_HOME": str(state_dir / "config"),
            "XDG_CACHE_HOME": str(state_dir / "cache"),
            "XDG_STATE_HOME": str(state_dir / "state"),
        }
    )
    result: dict[str, object] = {
        "config_id": config_id,
        "config": config["label"],
        "run_mode": config["mode"],
        "backend_requested": config["requested_mode"],
        "expected_api": config["expected_api"],
        "expected_vendor": config["expected_vendor"],
        "fixture_id": fixture_id,
        "fixture_label": fixture_spec["label"],
        "fixture_sha256": fixture_sha256,
        "expected_source_hdr": fixture_spec["expected_source_hdr"],
        "expected_dovi_profile": fixture_spec["expected_dovi_profile"],
        "status": "UNKNOWN",
        "exit_code": None,
        "elapsed_seconds": None,
        "exited_early": False,
        "forced_termination": False,
        "application_log_artifact": application_log.name,
        "process_log_artifact": process_log.name,
    }

    started = time.monotonic()
    output = ""
    command = _application_command(
        config,
        video_path,
        artifact_dir,
        application_log,
        state_dir,
    )
    result["application_command_sha256"] = hashlib.sha256(
        "\0".join(command).encode("utf-8")
    ).hexdigest()
    try:
        proc = subprocess.Popen(
            command,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        deadline = started + PLAY_DURATION
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                result["exited_early"] = True
                break
            time.sleep(0.1)

        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGINT)
            except ProcessLookupError:
                pass
        try:
            output, _ = proc.communicate(timeout=SHUTDOWN_TIMEOUT)
        except subprocess.TimeoutExpired:
            result["forced_termination"] = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            output, _ = proc.communicate()
        result["exit_code"] = proc.returncode
    except Exception as error:
        sanitized_error = _sanitize_line(
            str(error), [str(video_path), str(artifact_dir), str(state_dir)]
        )
        result["status"] = "EXECUTION_ERROR"
        result["failure_reason"] = sanitized_error
        result["errors"] = [sanitized_error]
        return result
    finally:
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        process_log.write_text(output, encoding="utf-8")

    log_lines = output.splitlines()
    if application_log.is_file():
        log_lines.extend(
            application_log.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
        )
    parsed = parse_log_lines(log_lines)
    private_paths = [str(video_path), str(artifact_dir), str(state_dir)]
    for key in (
        "backend_state",
        "first_frame",
        "media_frame",
        "telemetry",
        "runtime_line",
    ):
        result[key] = parsed[key]
    result["log_written"] = bool(parsed["lines"])
    result["errors"] = [
        _sanitize_line(line, private_paths) for line in parsed["errors"]
    ]
    result["file_open_errors"] = [
        _sanitize_line(line, private_paths) for line in parsed["file_open_errors"]
    ]
    result["key_logs"] = [
        _sanitize_line(line, private_paths) for line in parsed["key_logs"]
    ]

    evaluate_result(
        result,
        config,
        fixture_spec,
        expected_path_token=path_token(video_path),
    )
    if isinstance(result.get("runtime_line"), str):
        result["runtime_line"] = _sanitize_line(
            result["runtime_line"], private_paths
        )
    result["application_log_sha256"] = (
        get_sha256(application_log) if application_log.is_file() else None
    )
    result["process_log_sha256"] = get_sha256(process_log)
    return result


def _run_text(command: list[str]) -> str:
    return subprocess.check_output(
        command,
        cwd=PROJECT_ROOT,
        text=True,
        stderr=subprocess.STDOUT,
        timeout=10,
    ).strip()


def collect_preflight(
    expected_git_commit: str,
    selected_configs: list[str],
    expected_flatpak_commit: str | None,
) -> dict[str, object]:
    if not os.environ.get("WAYLAND_DISPLAY"):
        raise PreflightError("a Wayland display is required")
    if os.environ.get("XDG_SESSION_TYPE", "").lower() != "wayland":
        raise PreflightError("XDG_SESSION_TYPE must be wayland")
    actual_git_commit = _run_text(["git", "rev-parse", "HEAD"])
    if actual_git_commit != expected_git_commit:
        raise PreflightError(
            f"checkout commit mismatch: expected {expected_git_commit}, "
            f"got {actual_git_commit}"
        )
    git_status = _run_text(["git", "status", "--porcelain"])
    if git_status:
        raise PreflightError("execution checkout is not clean")

    if not expected_flatpak_commit:
        raise PreflightError("--expected-flatpak-commit is required")
    flatpak_commit = _run_text(
        ["flatpak", "info", "--show-commit", "--user", APP_ID]
    )
    if flatpak_commit != expected_flatpak_commit:
        raise PreflightError(
            f"Flatpak commit mismatch: expected {expected_flatpak_commit}, "
            f"got {flatpak_commit}"
        )

    return {
        "git_commit": actual_git_commit,
        "git_status_clean": True,
        "flatpak_commit": flatpak_commit,
        "wayland_display": os.environ.get("WAYLAND_DISPLAY"),
        "session_type": os.environ.get("XDG_SESSION_TYPE"),
    }


def probe_active_monitors() -> list[str]:
    """Return non-gating host monitor context without failing the smoke row."""

    try:
        from src.wayland_output_hdr import probe_outputs

        outputs = probe_outputs()
        descriptions = []
        for name, info in outputs.items():
            if info.hdr:
                descriptions.append(
                    f"{name}: HDR tf={info.tf} max_lum={info.max_lum:.1f} nit"
                )
            else:
                descriptions.append(f"{name}: SDR")
        return descriptions or ["no outputs detected"]
    except Exception as error:
        return [f"probe unavailable: {type(error).__name__}: {error}"]


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        action="append",
        required=True,
        choices=tuple(TEST_CONFIGS),
        help="one or two configs for the same GPU family",
    )
    parser.add_argument(
        "--fixture",
        action="append",
        required=True,
        choices=tuple(FIXTURE_SPECS),
        help="one or two independently declared fixture rows",
    )
    parser.add_argument("--fixture-root", required=True, type=Path)
    parser.add_argument("--expected-git-commit", required=True)
    parser.add_argument("--expected-flatpak-commit", required=True)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def _validate_selection(config_ids: list[str], fixture_ids: list[str]) -> None:
    if len(config_ids) > 2:
        raise PreflightError(
            "one smoke command may select at most two renderer configs"
        )
    if len(fixture_ids) > 2:
        raise PreflightError(
            "one smoke command may select at most two fixture rows"
        )
    if (
        len(set(config_ids)) != len(config_ids)
        or len(set(fixture_ids)) != len(fixture_ids)
    ):
        raise PreflightError("duplicate config or fixture selection is not allowed")
    vendors = {
        TEST_CONFIGS[config_id]["vendor_family"] for config_id in config_ids
    }
    if len(vendors) != 1:
        raise PreflightError("one smoke command must target exactly one GPU family")
    estimated = len(config_ids) * len(fixture_ids) * PLAY_DURATION
    if estimated > 120:
        raise PreflightError("the selected smoke matrix exceeds the two-minute budget")


def print_result(result: dict[str, object]) -> None:
    icon = "✅" if result["status"] == "PASS" else "❌"
    print(
        f"  {icon} [{result['config_id']} | {result['fixture_id']}] "
        f"{result['status']}"
    )
    if result.get("failure_reason"):
        print(f"      {result['failure_reason']}")


def main(argv: list[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        _validate_selection(args.config, args.fixture)
        fixture_paths = resolve_fixture_paths(args.fixture_root, args.fixture)
        fixture_hashes = validate_fixture_hashes(fixture_paths)
        output_path = args.output.expanduser().resolve()
        if output_path.exists():
            raise PreflightError(f"refusing to overwrite report: {output_path}")
        artifact_dir = output_path.with_suffix("")
        artifact_dir = artifact_dir.parent / f"{artifact_dir.name}-artifacts"
        if artifact_dir.exists():
            raise PreflightError(
                f"refusing to reuse artifact directory: {artifact_dir}"
            )
        preflight = collect_preflight(
            args.expected_git_commit,
            args.config,
            args.expected_flatpak_commit,
        )
    except (OSError, subprocess.SubprocessError, PreflightError) as error:
        print(f"PREFLIGHT_BLOCKED: {error}", file=sys.stderr)
        return 2

    output_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_dir.mkdir()
    results = []
    for config_id in args.config:
        for fixture_id in args.fixture:
            result = run_test(
                config_id,
                fixture_id,
                fixture_paths[fixture_id],
                fixture_hashes[fixture_id],
                artifact_dir,
            )
            results.append(result)
            print_result(result)

    report = {
        "schema": REPORT_SCHEMA,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "runner_argv_sha256": hashlib.sha256(
            "\0".join(sys.argv).encode("utf-8")
        ).hexdigest(),
        "execution_status": (
            "OBSERVATIONS_SATISFIED"
            if results and all(result["status"] == "PASS" for result in results)
            else "OBSERVATIONS_FAILED"
        ),
        "gate_adjudication": "NOT_PERFORMED_BY_OPERATOR",
        "observation_window_seconds": PLAY_DURATION,
        "preflight": preflight,
        "selected_configs": args.config,
        "selected_fixtures": args.fixture,
        "fixture_sha256": fixture_hashes,
        "environment_context": {
            "active_monitors": [
                _sanitize_line(description, [])
                for description in probe_active_monitors()
            ]
        },
        "non_claims": [
            "pixel or color correctness",
            "Gate 2W compositor submission or display output",
            "Dolby Vision certification",
            "performance",
            "stability beyond the short observation window",
        ],
        "results": results,
    }
    output_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Evidence report: {output_path}")
    return 0 if report["execution_status"] == "OBSERVATIONS_SATISFIED" else 1


if __name__ == "__main__":
    sys.exit(main())
