# diagnostics_export.py
#
# Copyright 2026 rusmikev / Diego Povliuk
# SPDX-License-Identifier: GPL-3.0-or-later

"""Optional one-shot diagnostics exporter for validation runs."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from .. import diagnostics_report
from ..diagnostics_report import (
    DIAGNOSTICS_EXPORT_DIR_ENV,
    DIAGNOSTICS_EXPORT_PATH_ENV,
    DIAGNOSTICS_SESSION_ID_ENV,
    EXPECTED_APP_COMMIT_ENV,
    EXPECTED_FLATPAK_INSTANCE_ENV,
    EXPECTED_RUNTIME_COMMIT_ENV,
    DiagnosticSnapshot,
)

logger = logging.getLogger(__name__)


class DiagnosticsExporter:
    """Autonomous one-shot diagnostic snapshot exporter for Gate 2W validation."""

    def __init__(
        self,
        export_path: Path,
        export_dir: Path | None = None,
        expected_instance: str | None = None,
        expected_app_commit: str | None = None,
        expected_runtime_commit: str | None = None,
        session_id: str | None = None,
    ):
        self.export_path = export_path
        self.export_dir = export_dir or export_path.parent
        self.expected_instance = expected_instance
        self.expected_app_commit = expected_app_commit
        self.expected_runtime_commit = expected_runtime_commit
        self.session_id = session_id

        self.first_frame_confirmed = False
        self.confirmed_media_frame: Any = None
        self.export_completed = False
        self.export_status = "PENDING"
        self.last_write_error: str | None = None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> DiagnosticsExporter | None:
        env = os.environ if environ is None else environ
        raw_path = env.get(DIAGNOSTICS_EXPORT_PATH_ENV, "").strip()
        raw_dir = env.get(DIAGNOSTICS_EXPORT_DIR_ENV, "").strip()
        if not raw_path and not raw_dir:
            return None

        export_path = Path(raw_path).expanduser() if raw_path else None
        export_dir = Path(raw_dir).expanduser() if raw_dir else None

        if export_dir and not export_path:
            export_path = export_dir / "video_output_diagnostics.txt"
        elif export_path and not export_dir:
            export_dir = export_path.parent

        if not export_path or not export_dir:
            return None

        expected_instance = env.get(EXPECTED_FLATPAK_INSTANCE_ENV, "").strip() or None
        expected_app = env.get(EXPECTED_APP_COMMIT_ENV, "").strip() or None
        expected_rt = env.get(EXPECTED_RUNTIME_COMMIT_ENV, "").strip() or None
        session_id = env.get(DIAGNOSTICS_SESSION_ID_ENV, "").strip() or None

        return cls(
            export_path=export_path,
            export_dir=export_dir,
            expected_instance=expected_instance,
            expected_app_commit=expected_app,
            expected_runtime_commit=expected_rt,
            session_id=session_id,
        )

    def check_identity(
        self, flatpak_info: dict[str, str] | None = None
    ) -> tuple[bool, str, list[str], dict[str, Any]]:
        info = flatpak_info if flatpak_info is not None else diagnostics_report.read_flatpak_info()
        actual_instance = info.get("instance_id") or None
        actual_app_commit = info.get("app_commit") or None
        actual_runtime_commit = info.get("runtime_commit") or None

        reasons: list[str] = []

        # Real container evidence requires instance-id and commits present in /.flatpak-info
        is_container = bool(actual_instance and actual_app_commit and actual_runtime_commit)
        if not is_container:
            missing_parts = []
            if not actual_instance:
                missing_parts.append("instance-id")
            if not actual_app_commit:
                missing_parts.append("app-commit")
            if not actual_runtime_commit:
                missing_parts.append("runtime-commit")
            reasons.append(
                f"container_metadata_missing: running outside Flatpak container or /.flatpak-info incomplete (missing: {', '.join(missing_parts)})"
            )

        mismatches: list[str] = []
        if self.expected_instance and actual_instance != self.expected_instance:
            mismatches.append(
                f"instance mismatch: expected '{self.expected_instance}', actual '{actual_instance}'"
            )
        if self.expected_app_commit and actual_app_commit != self.expected_app_commit:
            mismatches.append(
                f"app-commit mismatch: expected '{self.expected_app_commit}', actual '{actual_app_commit}'"
            )
        if self.expected_runtime_commit and actual_runtime_commit != self.expected_runtime_commit:
            mismatches.append(
                f"runtime-commit mismatch: expected '{self.expected_runtime_commit}', actual '{actual_runtime_commit}'"
            )

        if mismatches:
            reasons.extend(mismatches)

        has_expected_app = bool(self.expected_app_commit)
        has_expected_runtime = bool(self.expected_runtime_commit)
        has_expected_instance = bool(self.expected_instance)

        if not is_container:
            identity_status = "CONTAINER_METADATA_MISSING"
            verified = False
        elif mismatches:
            identity_status = "IDENTITY_MISMATCH"
            verified = False
        elif not has_expected_app and not has_expected_runtime and not has_expected_instance:
            identity_status = "UNVERIFIED"
            verified = False
            reasons.append(
                "no_expected_identity_configured: container metadata present but no expected identity commits configured for verification"
            )
        elif not (has_expected_app and has_expected_runtime):
            # Partial verification: both expected app and runtime commits must be configured for complete build verification
            identity_status = "UNVERIFIED"
            verified = False
            missing_expected = []
            if not has_expected_app:
                missing_expected.append("expected_app_commit")
            if not has_expected_runtime:
                missing_expected.append("expected_runtime_commit")
            reasons.append(
                f"partial_identity_check: complete build verification requires both expected app and runtime commits (missing: {', '.join(missing_expected)})"
            )
        else:
            identity_status = "VERIFIED"
            verified = True

        identity_record = {
            "process_id": os.getpid(),
            "flatpak_instance_id": actual_instance,
            "app_commit": actual_app_commit,
            "runtime_commit": actual_runtime_commit,
            "expected_instance_id": self.expected_instance,
            "expected_app_commit": self.expected_app_commit,
            "expected_runtime_commit": self.expected_runtime_commit,
            "session_id": self.session_id,
            "identity_status": identity_status,
            "identity_verified": verified,
            "identity_reasons": reasons,
        }
        return verified, identity_status, reasons, identity_record

    def export_snapshot(
        self,
        win: Any,
        trigger: str = "first-media-frame-confirmed",
        incomplete_reasons: list[str] | None = None,
        media_frame: Any = None,
        flatpak_info: dict[str, str] | None = None,
        snapshot: DiagnosticSnapshot | None = None,
        context_generation: int | None = None,
        load_generation: int | None = None,
    ) -> tuple[bool, str | None]:
        """Perform atomic write of diagnostics text report and companion JSON manifest."""
        if self.export_completed:
            return True, None

        initial_shutting_down = (getattr(win, "_shutting_down", False) is True) if win is not None else False
        initial_video_area = (getattr(win, "_video_area", None) or getattr(win, "gl_area", None)) if win is not None else None
        raw_ctx = getattr(initial_video_area, "render_context_generation", None) if initial_video_area else None
        expected_ctx = context_generation if isinstance(context_generation, int) else (
            raw_ctx if isinstance(raw_ctx, int) else None
        )
        raw_load = getattr(win, "_media_load_generation", None) if win is not None else None
        expected_load = load_generation if isinstance(load_generation, int) else (
            raw_load if isinstance(raw_load, int) else None
        )

        if snapshot is None and win is not None:
            snapshot = diagnostics_report.collect_video_output_facts(win)

        id_verified, id_status, id_reasons, id_record = self.check_identity(flatpak_info=flatpak_info)

        failure_reasons: list[str] = list(incomplete_reasons or [])
        if not id_verified:
            failure_reasons.extend(id_reasons)

        generation_changed = False
        if win is not None:
            latest_shutting_down = getattr(win, "_shutting_down", False) is True
            latest_video_area = getattr(win, "_video_area", None) or getattr(win, "gl_area", None)
            raw_latest_ctx = getattr(latest_video_area, "render_context_generation", None) if latest_video_area else None
            latest_ctx = raw_latest_ctx if isinstance(raw_latest_ctx, int) else None
            raw_latest_load = getattr(win, "_media_load_generation", None)
            latest_load = raw_latest_load if isinstance(raw_latest_load, int) else None

            if not initial_shutting_down and latest_shutting_down:
                failure_reasons.append("shutdown_during_collection: window started shutting down during diagnostics collection")
                generation_changed = True
            elif initial_shutting_down and trigger != "shutdown-before-first-frame":
                failure_reasons.append("window_shutting_down: window was shutting down when export was triggered")
                generation_changed = True

            if expected_load is not None and latest_load is not None and latest_load != expected_load:
                failure_reasons.append(
                    f"generation_mismatch_during_collection: media load generation changed from {expected_load} to {latest_load}"
                )
                generation_changed = True

            if expected_ctx is not None and latest_ctx is not None and latest_ctx != expected_ctx:
                failure_reasons.append(
                    f"context_mismatch_during_collection: render context generation changed from {expected_ctx} to {latest_ctx}"
                )
                generation_changed = True

            if initial_video_area is not None and latest_video_area is not None and latest_video_area is not initial_video_area:
                failure_reasons.append("video_area_changed_during_collection: video area widget instance changed")
                generation_changed = True

        if generation_changed:
            self.first_frame_confirmed = False
            self.confirmed_media_frame = None

        if not self.first_frame_confirmed:
            if not any("first_frame_unconfirmed" in r for r in failure_reasons):
                failure_reasons.append("first_frame_unconfirmed: no video frame was published before shutdown")

        if media_frame is None and self.first_frame_confirmed:
            failure_reasons.append("media_frame_unconfirmed: published texture lacked valid media frame evidence")
        elif media_frame is not None:
            if getattr(media_frame, "source_width", 0) <= 0 or getattr(media_frame, "source_height", 0) <= 0:
                failure_reasons.append("media_frame_invalid: non-positive video frame dimensions")

        if snapshot is None:
            failure_reasons.append("snapshot_missing: video output diagnostics snapshot could not be collected")
        else:
            active_api = snapshot.renderer.get("Active API")
            if active_api != "opengl-next":
                failure_reasons.append(f"backend_fallback: active API is '{active_api}', expected 'opengl-next'")

            session_status = snapshot.renderer.get("Session status")
            if session_status != "active":
                failure_reasons.append(f"session_not_active: renderer session status is '{session_status}', expected 'active'")

            startup_fallback = snapshot.renderer.get("Startup fallback reason")
            if startup_fallback and str(startup_fallback).lower() not in ("none", "unknown"):
                failure_reasons.append(f"startup_fallback: {startup_fallback}")

            renderer_failure = snapshot.renderer.get("Renderer failure reason")
            if renderer_failure and str(renderer_failure).lower() not in ("none", "unknown"):
                failure_reasons.append(f"renderer_failure: {renderer_failure}")

            restart_required = snapshot.renderer.get("Render restart required")
            if restart_required and str(restart_required).lower() not in ("none", "unknown", "no"):
                failure_reasons.append(f"restart_required: {restart_required}")

            target_format = snapshot.renderer.get("Render target format")
            target_depth = snapshot.renderer.get("Render target depth")
            color_state = str(snapshot.output.get("Gdk.ColorState", ""))

            if target_format == "GL_RGBA8" or target_depth == 8:
                failure_reasons.append("target_format_insufficient: render target format is 8-bit GL_RGBA8, GL_RGBA16F required for HDR/PQ")
            elif target_format != "GL_RGBA16F" or target_depth != 16:
                failure_reasons.append(f"target_format_unverified: target format is '{target_format}' ({target_depth}-bit), expected GL_RGBA16F (16-bit)")

            color_state_lower = color_state.lower()
            if "pq" not in color_state_lower or "2100" not in color_state_lower:
                failure_reasons.append(f"color_state_unverified: published color state is '{color_state}', expected Rec.2100 PQ")

            if snapshot.video.get("Video Codec / Format") in ("No video loaded", "Unknown", None):
                failure_reasons.append("video_stream_unconfirmed: no video stream loaded in snapshot")

        if len(failure_reasons) == 0:
            evidence_status = "CONFIRMED"
        else:
            evidence_status = "INCOMPLETE"

        sample_time_utc = snapshot.sample_time_utc if snapshot else datetime.now(timezone.utc).isoformat()
        id_record["sample_time_utc"] = sample_time_utc
        id_record["evidence_status"] = evidence_status
        id_record["evidence_gaps"] = failure_reasons
        id_record["trigger"] = trigger
        id_record["first_frame_confirmed"] = self.first_frame_confirmed

        session_identity_fields: dict[str, object] = {
            "Evidence status": evidence_status,
            "Evidence gaps": "; ".join(failure_reasons) if failure_reasons else "none",
            "Trigger": trigger,
            "First frame confirmed": self.first_frame_confirmed,
            "Media frame": (
                f"{media_frame.source_width}x{media_frame.source_height} at {media_frame.time_pos:.6f}s "
                f"(path token: {media_frame.path_token})"
                if media_frame
                else "none"
            ),
            "Sample time (UTC)": sample_time_utc,
            "Process ID": id_record["process_id"],
            "Flatpak instance ID": id_record["flatpak_instance_id"] or "Unknown (not in Flatpak container)",
            "Flatpak app commit": id_record["app_commit"] or "Unknown",
            "Flatpak runtime commit": id_record["runtime_commit"] or "Unknown",
            "Identity verified": id_verified,
            "Identity status": id_status,
            "Claim boundary": (
                "CONFIRMED certifies only verified client-side texture publication and GdkColorState tagging; "
                "Gate 2W (Wayland protocol surface submission) and physical display photon output remain separate and uncertified."
            ),
        }

        report_text = diagnostics_report.build_video_output_report(
            snapshot.renderer,
            snapshot.output,
            snapshot.video,
            performance=snapshot.performance,
            session_identity=session_identity_fields,
        )

        report_sha256 = hashlib.sha256(report_text.encode("utf-8")).hexdigest()

        json_manifest = {
            "schema": "cinehdr.diagnostics.export.v1",
            "sample_time_utc": sample_time_utc,
            "trigger": trigger,
            "evidence_status": evidence_status,
            "evidence_gaps": failure_reasons,
            "failure_reason": "; ".join(failure_reasons) if failure_reasons else None,
            "first_frame_confirmed": self.first_frame_confirmed,
            "client_evidence_boundary": (
                "CONFIRMED certifies only verified client-side texture publication and GdkColorState tagging; "
                "Gate 2W (Wayland protocol surface submission) and physical display photon output remain separate and uncertified."
            ),
            "media_frame": {
                "source_width": media_frame.source_width,
                "source_height": media_frame.source_height,
                "time_pos": media_frame.time_pos,
                "path_token": media_frame.path_token,
            } if media_frame else None,
            "identity": id_record,
            "summary": {
                "requested_api": snapshot.renderer.get("Requested this session"),
                "active_api": snapshot.renderer.get("Active API"),
                "session_status": snapshot.renderer.get("Session status"),
                "startup_fallback_reason": snapshot.renderer.get("Startup fallback reason"),
                "renderer_failure_reason": snapshot.renderer.get("Renderer failure reason"),
                "configured_decoder": snapshot.video.get("Configured decoder"),
                "active_decoder": snapshot.video.get("Active decoder"),
                "render_target_format": snapshot.renderer.get("Render target format"),
                "render_target_depth": snapshot.renderer.get("Render target depth"),
                "gdk_color_state": snapshot.output.get("Gdk.ColorState"),
                "graphics_offload": snapshot.output.get("Graphics Offload"),
                "gl_renderer": snapshot.renderer.get("OpenGL renderer"),
                "gl_vendor": snapshot.renderer.get("OpenGL vendor"),
                "gl_version": snapshot.renderer.get("OpenGL version"),
            },
            "report_sha256": report_sha256,
            "target_file": str(self.export_path),
        }

        try:
            self.export_dir.mkdir(parents=True, exist_ok=True)
            txt_tmp = self.export_path.with_suffix(".tmp.txt")
            json_path = self.export_path.with_suffix(".json")
            json_tmp = self.export_path.with_suffix(".tmp.json")

            txt_tmp.write_text(report_text, encoding="utf-8")
            txt_tmp.replace(self.export_path)

            json_tmp.write_text(json.dumps(json_manifest, indent=2, default=str), encoding="utf-8")
            json_tmp.replace(json_path)

            if not generation_changed:
                self.export_completed = True
            self.export_status = evidence_status
            logger.info("Saved video output diagnostics to %s (evidence: %s)", self.export_path, evidence_status)
            return True, None
        except OSError as exc:
            err = f"write_error: {exc}"
            self.last_write_error = err
            logger.error("Failed to write diagnostics export: %s", exc)
            return False, err

    def on_first_media_frame(
        self,
        win: Any,
        media_frame: Any,
        context_generation: int | None = None,
        load_generation: int | None = None,
    ) -> tuple[bool, str | None]:
        """Triggered upon confirmation of the very first rendered and published media frame."""
        if self.export_completed:
            return True, None
        self.first_frame_confirmed = True
        self.confirmed_media_frame = media_frame
        ok, err = self.export_snapshot(
            win,
            trigger="first-media-frame-confirmed",
            media_frame=media_frame,
            context_generation=context_generation,
            load_generation=load_generation,
        )
        if not self.export_completed:
            self.first_frame_confirmed = False
            self.confirmed_media_frame = None
        return ok, err

    def on_first_frame(
        self,
        win: Any,
        media_frame: Any = None,
        context_generation: int | None = None,
        load_generation: int | None = None,
    ) -> tuple[bool, str | None]:
        """Backward-compatible entry point for first frame confirmation."""
        if self.export_completed:
            return True, None
        if media_frame is None and win is not None:
            mpv = getattr(win, "mpv", None)
            if mpv is None and hasattr(win, "player"):
                mpv = getattr(win.player, "mpv", None)
            if mpv is not None:
                try:
                    from ..render_backend import media_frame_evidence
                    media_frame = media_frame_evidence(mpv)
                except Exception:
                    media_frame = None
        return self.on_first_media_frame(
            win,
            media_frame=media_frame,
            context_generation=context_generation,
            load_generation=load_generation,
        )

    def on_shutdown(self, win: Any) -> tuple[bool, str | None]:
        """Triggered on application or window close; records incomplete evidence if no media frame occurred."""
        if not self.export_completed:
            return self.export_snapshot(
                win,
                trigger="shutdown-before-first-frame",
                incomplete_reasons=["first_frame_unconfirmed: no video frame was published before shutdown"],
            )
        return True, None


def exporter_from_env(environ: Mapping[str, str] | None = None) -> DiagnosticsExporter | None:
    """Create the optional exporter without exposing its class to the product."""
    return DiagnosticsExporter.from_env(environ)
