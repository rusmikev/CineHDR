# Copyright 2026 rusmikev
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import types
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from src.gpu_validation import (
    GpuValidationSession,
    PUBLICATION_FFMPEG_VERSION,
    PUBLICATION_FIXTURE_PROFILE,
    PUBLICATION_FIXTURE_REVISION,
    PUBLICATION_HDR_SHA256,
    PUBLICATION_LIBMPV_PREFIX_SUFFIX,
    PUBLICATION_MPV_VERSION,
    PUBLICATION_SDR_SHA256,
    SETTLING_COOLDOWN_SECONDS,
    SETTLING_REVISION,
    SETTLING_SCHEMA,
    ValidationStep,
    aggregate_drm_memory_kib,
    evaluate_publication_validation,
    evaluate_resource_settling_validation,
    evaluate_validation,
    position_sample_is_stable,
    prepare_validation_player,
    publication_expected_api,
    publication_runtime_errors,
    publication_state_errors,
    publication_texture_evidence,
    resource_settling_trend,
    settling_endpoint_errors,
    validation_config_from_env,
    validation_expected_playlist_count,
    validation_steps,
)
from src.hdr_controller import is_tone_mapping_active


ROOT = Path(__file__).resolve().parents[1]


def publication_environment(**overrides):
    environment = {
        "CINEHDR_GPU_VALIDATION": "publication",
        "CINEHDR_PUBLICATION_EXPECTED_BACKEND": "gpu-next",
        "CINEHDR_PUBLICATION_FIXTURE_PROFILE": PUBLICATION_FIXTURE_PROFILE,
        "CINEHDR_PUBLICATION_FIXTURE_REVISION": PUBLICATION_FIXTURE_REVISION,
        "CINEHDR_PUBLICATION_HDR_SHA256": PUBLICATION_HDR_SHA256,
        "CINEHDR_PUBLICATION_SDR_SHA256": PUBLICATION_SDR_SHA256,
    }
    environment.update(overrides)
    return environment


def settling_environment(**overrides):
    environment = publication_environment(CINEHDR_GPU_VALIDATION="settling")
    environment.update(overrides)
    return environment


def pinned_publication_runtime():
    return {
        "libmpv_path": f"/work{PUBLICATION_LIBMPV_PREFIX_SUFFIX}libmpv.so.2.5.0",
        "mpv_version": PUBLICATION_MPV_VERSION,
        "libplacebo_version": "v7.360.1",
        "ffmpeg_version": PUBLICATION_FFMPEG_VERSION,
    }


def publication_metrics(signal: str, *, api: str = "opengl-next"):
    is_hdr = signal == "hdr"
    return {
        "active_api": api,
        "render_status": "active",
        "playlist_pos": 1 if is_hdr else 0,
        "source_hdr": is_hdr,
        "hdr_output_active": is_hdr,
        "render_target_format": "GL_RGBA16F" if is_hdr else "GL_RGBA8",
        "render_target_depth": 16 if is_hdr else 8,
        "render_color_state": "rec2100-pq" if is_hdr else "srgb",
        "hwdec": "vaapi-copy",
        "gl_errors": 0,
        "fbo_failures": 0,
        "render_context_generation": 4,
        "render_runtime": pinned_publication_runtime(),
        "publication_texture": {
            "available": True,
            "texture_is_gl_texture": True,
            "format": "R16G16B16A16_FLOAT" if is_hdr else "R8G8B8A8",
            "color_state_srgb": not is_hdr,
            "color_state_rec2100_pq": is_hdr,
            "dimensions_match": True,
            "display_is_wayland": True,
            "surface_is_wayland": True,
        },
    }


def settling_endpoint(
    cycle: int,
    rss_kib: int | None,
    drm_vram_kib: int | None,
    *,
    size: tuple[int, int] = (960, 540),
):
    metrics = publication_metrics("hdr")
    metrics.update(
        {
            "width": size[0],
            "height": size[1],
            "rss_kib": rss_kib,
            "drm_vram_kib": drm_vram_kib,
            "drm_gtt_kib": 1024,
            "fbo_drops": 0,
            "decoder_drops": 0,
            "vo_drops": 0,
        }
    )
    return {
        "name": f"cycle {cycle} small-window HDR endpoint",
        "action": "settling-endpoint",
        "expected_state": (cycle, 1, "hdr", 960, 540),
        "status": "PASS",
        "metrics": metrics,
    }


def complete_settling_steps(endpoints):
    endpoint_iter = iter(endpoints)
    results = []
    for step in validation_steps("settling"):
        if step.action == "settling-endpoint":
            results.append(next(endpoint_iter))
        else:
            results.append(
                {
                    "name": step.name,
                    "action": step.action,
                    "expected_state": step.value,
                    "status": "PASS",
                    "metrics": publication_metrics("hdr"),
                }
            )
    return tuple(results)


def unloaded_cooldown_metrics():
    return {
        "idle_active": True,
        "time_pos_available": False,
        "gl_errors": 0,
        "fbo_failures": 0,
        "render_context_generation": 4,
    }


class ValidationConfigTests(unittest.TestCase):
    def test_validator_is_off_by_default(self):
        self.assertIsNone(validation_config_from_env({}))

    def test_quick_config_uses_requested_report_directory(self):
        config = validation_config_from_env(
            {
                "CINEHDR_GPU_VALIDATION": "QUICK",
                "CINEHDR_GPU_VALIDATION_REPORT_DIR": "/tmp/cinehdr-reports",
            }
        )
        self.assertEqual(config.mode, "quick")
        self.assertEqual(config.report_dir, Path("/tmp/cinehdr-reports"))

    def test_invalid_mode_fails_closed(self):
        with self.assertRaises(ValueError):
            validation_config_from_env({"CINEHDR_GPU_VALIDATION": "maybe"})

    def test_lifecycle_mode_is_explicitly_supported(self):
        config = validation_config_from_env(
            {"CINEHDR_GPU_VALIDATION": "lifecycle"}
        )
        self.assertEqual(config.mode, "lifecycle")

    def test_transition_mode_is_explicitly_supported(self):
        config = validation_config_from_env(
            {"CINEHDR_GPU_VALIDATION": "transition"}
        )
        self.assertEqual(config.mode, "transition")

    def test_sdr_soak_mode_is_explicitly_supported(self):
        config = validation_config_from_env(
            {"CINEHDR_GPU_VALIDATION": "sdr-soak"}
        )
        self.assertEqual(config.mode, "sdr-soak")

    def test_publication_requires_exact_backend_and_fixture_provenance(self):
        config = validation_config_from_env(
            publication_environment(
                CINEHDR_PUBLICATION_EXPECTED_BACKEND="legacy",
            )
        )
        self.assertEqual(config.mode, "publication")
        self.assertEqual(config.expected_backend, "legacy")
        self.assertEqual(config.fixture_profile, PUBLICATION_FIXTURE_PROFILE)
        self.assertEqual(config.fixture_revision, PUBLICATION_FIXTURE_REVISION)
        self.assertEqual(config.fixture_hdr_sha256, PUBLICATION_HDR_SHA256)
        self.assertEqual(config.fixture_sdr_sha256, PUBLICATION_SDR_SHA256)

    def test_publication_rejects_missing_backend_or_fixture_hash(self):
        missing_backend = publication_environment()
        missing_backend.pop("CINEHDR_PUBLICATION_EXPECTED_BACKEND")
        with self.assertRaisesRegex(ValueError, "EXPECTED_BACKEND"):
            validation_config_from_env(missing_backend)
        with self.assertRaisesRegex(ValueError, "HDR_SHA256"):
            validation_config_from_env(
                publication_environment(CINEHDR_PUBLICATION_HDR_SHA256="wrong")
            )

    def test_settling_reuses_the_exact_publication_fixture_contract(self):
        config = validation_config_from_env(
            settling_environment(CINEHDR_PUBLICATION_EXPECTED_BACKEND="legacy")
        )
        self.assertEqual(config.mode, "settling")
        self.assertEqual(config.expected_backend, "legacy")
        self.assertEqual(config.fixture_profile, PUBLICATION_FIXTURE_PROFILE)
        self.assertEqual(config.fixture_revision, PUBLICATION_FIXTURE_REVISION)
        with self.assertRaisesRegex(ValueError, "SDR_SHA256"):
            validation_config_from_env(
                settling_environment(CINEHDR_PUBLICATION_SDR_SHA256="unaccepted")
            )

    def test_playlist_count_expectation_is_mode_specific(self):
        self.assertEqual(validation_expected_playlist_count("sdr-soak"), 1)
        self.assertEqual(validation_expected_playlist_count("transition"), 2)
        self.assertEqual(validation_expected_playlist_count("publication"), 2)
        self.assertEqual(validation_expected_playlist_count("settling"), 2)
        self.assertIsNone(validation_expected_playlist_count("quick"))

    def test_validation_player_does_not_restore_or_save_watch_later(self):
        player = {}
        self.assertEqual(prepare_validation_player(player), ())
        self.assertFalse(player["resume-playback"])
        self.assertFalse(player["save-position-on-quit"])
        self.assertEqual(player["autocreate-playlist"], "no")
        self.assertEqual(player["speed"], 1.0)

    def test_publication_player_is_isolated_before_loadfile(self):
        player = {}
        self.assertEqual(prepare_validation_player(player, "publication"), ())
        self.assertEqual(player["pause"], "yes")
        self.assertEqual(player["keep-open"], "yes")
        self.assertEqual(player["hwdec"], "vaapi-copy")
        normal_player = {}
        prepare_validation_player(normal_player, "quick")
        self.assertNotIn("pause", normal_player)
        self.assertNotIn("keep-open", normal_player)
        self.assertNotIn("hwdec", normal_player)

    def test_settling_player_is_paused_and_copy_decode_before_loadfile(self):
        player = {}
        self.assertEqual(prepare_validation_player(player, "settling"), ())
        self.assertEqual(player["pause"], "yes")
        self.assertEqual(player["keep-open"], "yes")
        self.assertEqual(player["hwdec"], "vaapi-copy")

    def test_drm_fdinfo_is_aggregated_once_per_client(self):
        duplicated_client = """
drm-driver: amdgpu
drm-client-id: 7
drm-pdev: 0000:03:00.0
drm-memory-vram: 1024 KiB
drm-memory-gtt: 2 MiB
"""
        second_client = """
drm-driver: amdgpu
drm-client-id: 8
drm-pdev: 0000:03:00.0
drm-resident-vram: 512 KiB
drm-memory-vram: 512 KiB
"""
        totals = aggregate_drm_memory_kib(
            [duplicated_client, duplicated_client, second_client]
        )
        self.assertEqual(totals["vram"], 1536)
        self.assertEqual(totals["gtt"], 2048)


class ValidationPlanTests(unittest.TestCase):
    def test_quick_plan_exercises_playback_and_window_transitions(self):
        steps = validation_steps("quick")
        actions = {step.action for step in steps}
        self.assertEqual(actions, {"play", "pause", "seek", "resize", "fullscreen"})
        self.assertEqual(steps[0].name, "normalize start position")
        self.assertLess(sum(step.wait_seconds for step in steps), 120)

    def test_soak_adds_thirty_minute_playback(self):
        steps = validation_steps("soak")
        self.assertTrue(
            any(
                step.name == "30-minute soak playback"
                and step.wait_seconds == 1800
                for step in steps
            )
        )

    def test_lifecycle_plan_recreates_the_context_twice(self):
        steps = validation_steps("lifecycle")
        cycles = [step for step in steps if step.action == "context-cycle"]
        self.assertEqual(len(cycles), 2)
        self.assertEqual([step.value for step in cycles], [1, 2])
        self.assertLess(sum(step.wait_seconds for step in steps), 120)

    def test_transition_plan_runs_sdr_hdr_sdr_hdr_in_one_context(self):
        steps = validation_steps("transition")
        states = [
            step.value[1]
            for step in steps
            if step.action in ("signal-state", "switch-media")
        ]
        self.assertEqual(states, ["sdr", "hdr", "sdr", "hdr"])
        self.assertNotIn("context-cycle", {step.action for step in steps})
        self.assertLess(sum(step.wait_seconds for step in steps), 120)

    def test_publication_plan_is_exact_paused_sdr_hdr_sdr_hdr_matrix(self):
        steps = validation_steps("publication")
        self.assertEqual(len(steps), 4)
        self.assertEqual(
            [step.value for step in steps],
            [(0, "sdr"), (1, "hdr"), (0, "sdr"), (1, "hdr")],
        )
        self.assertEqual(steps[0].action, "publication-state")
        self.assertEqual(
            [step.action for step in steps[1:]],
            ["switch-media", "switch-media", "switch-media"],
        )
        self.assertNotIn("play", {step.action for step in steps})
        self.assertNotIn("seek", {step.action for step in steps})
        self.assertEqual({step.wait_seconds for step in steps}, {6})

    def test_settling_plan_has_one_warmup_and_three_equal_hdr_endpoints(self):
        steps = validation_steps("settling")
        self.assertEqual(steps[0].action, "settling-warmup")
        self.assertEqual(steps[0].value, (1, "hdr"))
        endpoints = [step for step in steps if step.action == "settling-endpoint"]
        self.assertEqual(
            [step.value for step in endpoints],
            [
                (1, 1, "hdr", 960, 540),
                (2, 1, "hdr", 960, 540),
                (3, 1, "hdr", 960, 540),
            ],
        )
        resizes = [step.value for step in steps if step.action == "settling-resize"]
        self.assertEqual(
            resizes,
            [
                (1, "hdr", 1280, 720), (1, "hdr", 960, 540),
                (1, "hdr", 1280, 720), (1, "hdr", 960, 540),
                (1, "hdr", 1280, 720), (1, "hdr", 960, 540),
            ],
        )
        self.assertEqual(steps[-2].action, "settling-unload")
        self.assertEqual(steps[-1].action, "settling-cooldown")
        self.assertEqual(steps[-1].wait_seconds, SETTLING_COOLDOWN_SECONDS)
        self.assertNotIn("play", {step.action for step in steps})
        self.assertNotIn("context-cycle", {step.action for step in steps})

    def test_sdr_soak_requires_sdr_state_before_thirty_minutes(self):
        steps = validation_steps("sdr-soak")
        self.assertEqual(steps[0].action, "signal-state")
        self.assertEqual(steps[0].value, (0, "sdr"))
        soak = next(step for step in steps if step.wait_seconds == 1800)
        self.assertEqual(soak.name, "30-minute soak playback")
        self.assertLess(steps.index(steps[0]), steps.index(soak))

    def test_readiness_rejects_saved_position_jump(self):
        self.assertTrue(position_sample_is_stable(100.0, 100.5, 0.5))
        self.assertFalse(position_sample_is_stable(100.0, 2000.0, 0.5))
        self.assertFalse(position_sample_is_stable(None, 100.0, 0.5))


class ValidationResultTests(unittest.TestCase):
    def test_pass_requires_active_gpu_next_and_clean_measurements(self):
        result, warnings = evaluate_validation(
            active_api="opengl-next",
            session_status="active",
            hwdec="vaapi-copy",
            step_statuses=("PASS",) * 10,
            fbo_drops=0,
            decoder_drops=0,
            vo_drops=0,
            rss_growth_kib=1024,
            process_vram_growth_kib=2048,
            process_vram_measured=True,
            gl_errors=0,
            fbo_failures=0,
        )
        self.assertEqual(result, "PASS")
        self.assertEqual(warnings, ())

    def test_missing_hardware_decode_is_a_warning_not_a_false_pass(self):
        result, warnings = evaluate_validation(
            active_api="opengl-next",
            session_status="active",
            hwdec="no",
            step_statuses=("PASS",),
            fbo_drops=0,
            decoder_drops=0,
            vo_drops=0,
            rss_growth_kib=None,
            process_vram_growth_kib=None,
            process_vram_measured=True,
            gl_errors=0,
            fbo_failures=0,
        )
        self.assertEqual(result, "WARN")
        self.assertIn("hardware decoding was not active", warnings)

    def test_decoder_and_video_output_drops_are_not_combined(self):
        result, warnings = evaluate_validation(
            active_api="opengl-next",
            session_status="active",
            hwdec="vaapi-copy",
            step_statuses=("PASS",),
            fbo_drops=0,
            decoder_drops=12,
            vo_drops=0,
            rss_growth_kib=0,
            process_vram_growth_kib=0,
            process_vram_measured=True,
            gl_errors=0,
            fbo_failures=0,
        )
        self.assertEqual(result, "WARN")
        self.assertEqual(
            warnings, ("mpv decoder reported 12 dropped frames",)
        )

    def test_renderer_fallback_is_a_failure(self):
        result, warnings = evaluate_validation(
            active_api="opengl",
            session_status="startup-fallback",
            hwdec="vaapi-copy",
            step_statuses=(),
            fbo_drops=0,
            decoder_drops=0,
            vo_drops=0,
            rss_growth_kib=None,
            process_vram_growth_kib=None,
            process_vram_measured=True,
            gl_errors=0,
            fbo_failures=0,
        )
        self.assertEqual(result, "FAIL")
        self.assertEqual(warnings, ("GPU Next was not the active renderer",))

    def test_unavailable_vram_measurement_prevents_a_clean_pass(self):
        result, warnings = evaluate_validation(
            active_api="opengl-next",
            session_status="active",
            hwdec="vaapi-copy",
            step_statuses=("PASS",),
            fbo_drops=0,
            decoder_drops=0,
            vo_drops=0,
            rss_growth_kib=0,
            process_vram_growth_kib=None,
            process_vram_measured=False,
            gl_errors=0,
            fbo_failures=0,
        )
        self.assertEqual(result, "WARN")
        self.assertIn("did not expose CineHDR DRM-client VRAM", warnings[0])

    def test_observed_gl_error_is_a_failure(self):
        result, warnings = evaluate_validation(
            active_api="opengl-next",
            session_status="active",
            hwdec="vaapi-copy",
            step_statuses=("PASS",),
            fbo_drops=0,
            decoder_drops=0,
            vo_drops=0,
            rss_growth_kib=0,
            process_vram_growth_kib=0,
            process_vram_measured=True,
            gl_errors=1,
            fbo_failures=0,
        )
        self.assertEqual(result, "FAIL")
        self.assertIn("OpenGL errors", warnings[0])


class ResourceSettlingEvidenceTests(unittest.TestCase):
    def test_endpoint_requires_actual_state_rss_and_process_drm_vram(self):
        endpoint = settling_endpoint(1, 12_000, 34_000)["metrics"]
        self.assertEqual(
            settling_endpoint_errors(
                endpoint,
                expected_api="opengl-next",
                expected_context_generation=4,
            ),
            (),
        )
        endpoint["rss_kib"] = None
        endpoint["drm_vram_kib"] = None
        endpoint["render_target_format"] = "GL_RGBA8"
        errors = settling_endpoint_errors(
            endpoint,
            expected_api="opengl-next",
            expected_context_generation=4,
        )
        self.assertTrue(any("RSS" in error for error in errors))
        self.assertTrue(any("DRM VRAM" in error for error in errors))
        self.assertTrue(any("render target" in error for error in errors))

    def test_settling_trend_only_warns_for_three_strictly_rising_endpoints(self):
        endpoints = (
            settling_endpoint(1, 100, 1000),
            settling_endpoint(2, 110, 1000),
            settling_endpoint(3, 120, 1000),
        )
        result, findings, trends = evaluate_resource_settling_validation(
            active_api="opengl-next",
            session_status="active",
            expected_api="opengl-next",
            endpoint_results=endpoints,
            step_results=complete_settling_steps(endpoints),
            final=unloaded_cooldown_metrics(),
            expected_context_generation=4,
            failure_reason=None,
            isolation_failures=(),
        )
        self.assertEqual(result, "WARN")
        self.assertEqual(trends["rss_kib"], "strictly-increasing")
        self.assertEqual(trends["drm_vram_kib"], "not-strictly-increasing")
        self.assertTrue(any("rss_kib" in finding for finding in findings))

        plateau_endpoints = (
            settling_endpoint(1, 100, 1000),
            settling_endpoint(2, 100, 1000),
            settling_endpoint(3, 101, 1002),
        )
        result, findings, trends = evaluate_resource_settling_validation(
            active_api="opengl-next",
            session_status="active",
            expected_api="opengl-next",
            endpoint_results=plateau_endpoints,
            step_results=complete_settling_steps(plateau_endpoints),
            final=unloaded_cooldown_metrics(),
            expected_context_generation=4,
            failure_reason=None,
            isolation_failures=(),
        )
        self.assertEqual((result, findings), ("PASS", ()))
        self.assertEqual(trends["rss_kib"], "not-strictly-increasing")
        self.assertEqual(trends["drm_vram_kib"], "not-strictly-increasing")

    def test_settling_evaluation_fails_closed_for_missing_or_unloaded_evidence(self):
        endpoints = (
            settling_endpoint(1, 100, 1000),
            settling_endpoint(2, None, 1001),
            settling_endpoint(3, 102, 1002),
        )
        final = unloaded_cooldown_metrics()
        final["idle_active"] = False
        result, errors, _trends = evaluate_resource_settling_validation(
            active_api="opengl",
            session_status="startup-fallback",
            expected_api="opengl-next",
            endpoint_results=endpoints,
            step_results=complete_settling_steps(endpoints)[:-1],
            final=final,
            expected_context_generation=4,
            failure_reason="test failure",
            isolation_failures=("pause",),
        )
        self.assertEqual(result, "FAIL")
        self.assertTrue(any("active API" in error for error in errors))
        self.assertTrue(any("RSS" in error for error in errors))
        self.assertTrue(any("incomplete or out of order" in error for error in errors))
        self.assertTrue(any("media was unloaded" in error for error in errors))

    def test_settling_accepts_adjusted_but_equal_endpoint_sizes(self):
        adjusted = (
            settling_endpoint(1, 100, 1000, size=(944, 531)),
            settling_endpoint(2, 100, 1000, size=(944, 531)),
            settling_endpoint(3, 101, 1002, size=(944, 531)),
        )
        result, errors, _trends = evaluate_resource_settling_validation(
            active_api="opengl-next",
            session_status="active",
            expected_api="opengl-next",
            endpoint_results=adjusted,
            step_results=complete_settling_steps(adjusted),
            final=unloaded_cooldown_metrics(),
            expected_context_generation=4,
            failure_reason=None,
            isolation_failures=(),
        )
        self.assertEqual((result, errors), ("PASS", ()))
        adjusted[-1]["metrics"]["width"] = 960
        result, errors, _trends = evaluate_resource_settling_validation(
            active_api="opengl-next",
            session_status="active",
            expected_api="opengl-next",
            endpoint_results=adjusted,
            step_results=complete_settling_steps(adjusted),
            final=unloaded_cooldown_metrics(),
            expected_context_generation=4,
            failure_reason=None,
            isolation_failures=(),
        )
        self.assertEqual(result, "FAIL")
        self.assertTrue(any("not comparable" in error for error in errors))

    def test_settling_endpoint_and_cooldown_use_pre_unload_decode_evidence(self):
        session = GpuValidationSession.__new__(GpuValidationSession)
        session.config = SimpleNamespace(mode="settling", expected_backend="gpu-next")
        session.video_area = SimpleNamespace(
            render_backend_active="opengl-next",
            render_session_status="active",
        )
        session._settling_context_generation = 4
        endpoint_metrics = settling_endpoint(1, 100, 1000, size=(944, 531))["metrics"]
        endpoint_metrics["render_frame_generation"] = 12
        status, _detail = session._verify_step(
            ValidationStep(
                "cycle 1 small-window HDR endpoint",
                "settling-endpoint",
                6,
                (1, 1, "hdr", 960, 540),
            ),
            endpoint_metrics,
        )
        self.assertEqual(status, "PASS")
        cooldown = unloaded_cooldown_metrics()
        self.assertNotIn("hwdec", cooldown)
        status, _detail = session._verify_step(
            ValidationStep("ten-second unload cooldown", "settling-cooldown", 10),
            cooldown,
        )
        self.assertEqual(status, "PASS")

    def test_settling_helpers_do_not_fit_a_byte_threshold(self):
        self.assertEqual(resource_settling_trend((1, 2, 3)), "strictly-increasing")
        self.assertEqual(resource_settling_trend((1, 1, 2)), "not-strictly-increasing")
        self.assertEqual(resource_settling_trend((1, 2)), "unavailable")

    def test_settling_writer_records_typed_endpoints_scope_and_cooldown(self):
        endpoints = (
            settling_endpoint(1, 100, 1000),
            settling_endpoint(2, 100, 1000),
            settling_endpoint(3, 101, 1002),
        )
        step_results = list(complete_settling_steps(endpoints))
        step_results[-1]["metrics"] = unloaded_cooldown_metrics()
        with tempfile.TemporaryDirectory() as temporary:
            session = GpuValidationSession.__new__(GpuValidationSession)
            session.config = validation_config_from_env(settling_environment())
            session.config = SimpleNamespace(
                **{
                    **session.config.__dict__,
                    "report_dir": Path(temporary),
                }
            )
            session.step_results = step_results
            session.window = SimpleNamespace(
                app=SimpleNamespace(_gpu_validation_setup_failures=())
            )
            session._settling_context_generation = 4
            session.started_at = time.monotonic()
            session._renderer_facts = lambda: ("opengl-next", "active")
            session._expected_active_api = lambda: "opengl-next"
            report_path, status = session._write_resource_settling_report(None)
            report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(status, "PASS")
        self.assertEqual(report["schema"], SETTLING_SCHEMA)
        self.assertEqual(report["revision"], SETTLING_REVISION)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(len(report["hdr_endpoints"]), 3)
        self.assertEqual(
            report["endpoint_requested_logical_size"], {"width": 960, "height": 540}
        )
        self.assertEqual(report["cooldown_metrics"]["idle_active"], True)
        self.assertIn("not a leak-free claim", report["scope"])
        self.assertNotIn("path", report["fixture"])


class PublicationEvidenceTests(unittest.TestCase):
    def test_backend_mapping_is_startup_only_and_closed(self):
        self.assertEqual(publication_expected_api("legacy"), "opengl")
        self.assertEqual(publication_expected_api("gpu-next"), "opengl-next")
        with self.assertRaises(ValueError):
            publication_expected_api("auto")

    def test_actual_gltexture_evidence_reads_getters_and_wayland_types(self):
        srgb = object()
        rec2100_pq = object()

        class FakeColor:
            def __init__(self, value):
                self.value = value

            def equal(self, other):
                return self.value is other

        class FakeTexture:
            __gtype__ = SimpleNamespace(name="GdkGLTexture")

            def get_width(self):
                return 1280

            def get_height(self):
                return 720

            def get_format(self):
                return "R16G16B16A16_FLOAT"

            def get_color_state(self):
                return FakeColor(rec2100_pq)

        fake_gdk = SimpleNamespace(
            GLTexture=FakeTexture,
            MemoryFormat=SimpleNamespace(
                R8G8B8A8="R8G8B8A8",
                R16G16B16A16_FLOAT="R16G16B16A16_FLOAT",
            ),
            ColorState=SimpleNamespace(
                get_srgb=lambda: srgb,
                get_rec2100_pq=lambda: rec2100_pq,
            ),
        )
        display = SimpleNamespace(__gtype__=SimpleNamespace(name="GdkWaylandDisplay"))
        surface = SimpleNamespace(
            __gtype__=SimpleNamespace(name="GdkWaylandToplevel"),
            get_display=lambda: display,
        )
        area = SimpleNamespace(
            current_texture=FakeTexture(),
            get_width=lambda: 640,
            get_height=lambda: 360,
            props=SimpleNamespace(scale_factor=2),
            get_native=lambda: SimpleNamespace(get_surface=lambda: surface),
        )
        with patch("src.gpu_validation.Gdk", fake_gdk):
            evidence = publication_texture_evidence(area)
        self.assertTrue(evidence["available"])
        self.assertTrue(evidence["texture_is_gl_texture"])
        self.assertEqual(evidence["format"], "R16G16B16A16_FLOAT")
        self.assertFalse(evidence["color_state_srgb"])
        self.assertTrue(evidence["color_state_rec2100_pq"])
        self.assertTrue(evidence["dimensions_match"])
        self.assertTrue(evidence["display_is_wayland"])
        self.assertTrue(evidence["surface_is_wayland"])

        area.get_width = lambda: 641
        with patch("src.gpu_validation.Gdk", fake_gdk):
            mismatched = publication_texture_evidence(area)
        self.assertFalse(mismatched["dimensions_match"])

    def test_missing_actual_texture_getter_is_unavailable_not_intended_state(self):
        class FakeTexture:
            __gtype__ = SimpleNamespace(name="GdkGLTexture")

        fake_gdk = SimpleNamespace(GLTexture=FakeTexture)
        area = SimpleNamespace(current_texture=FakeTexture())
        with patch("src.gpu_validation.Gdk", fake_gdk):
            evidence = publication_texture_evidence(area)
        self.assertFalse(evidence["available"])
        self.assertIn("getter", str(evidence["error"]))

    def test_publication_state_requires_actual_sdr_and_hdr_evidence(self):
        self.assertEqual(
            publication_state_errors(
                publication_metrics("sdr"),
                expected_index=0,
                expected_signal="sdr",
                expected_api="opengl-next",
                expected_context_generation=4,
            ),
            (),
        )
        self.assertEqual(
            publication_state_errors(
                publication_metrics("hdr"),
                expected_index=1,
                expected_signal="hdr",
                expected_api="opengl-next",
                expected_context_generation=4,
            ),
            (),
        )
        self.assertEqual(
            publication_state_errors(
                publication_metrics("sdr", api="opengl"),
                expected_index=0,
                expected_signal="sdr",
                expected_api="opengl",
                expected_context_generation=4,
            ),
            (),
        )
        stale = publication_metrics("sdr")
        stale["publication_texture"]["format"] = "R16G16B16A16_FLOAT"
        stale["publication_texture"]["color_state_srgb"] = False
        stale["publication_texture"]["color_state_rec2100_pq"] = True
        stale["hwdec"] = "no"
        stale["active_api"] = "opengl"
        errors = publication_state_errors(
            stale,
            expected_index=0,
            expected_signal="sdr",
            expected_api="opengl-next",
            expected_context_generation=4,
        )
        self.assertTrue(any("active API" in error for error in errors))
        self.assertTrue(any("texture format" in error for error in errors))
        self.assertTrue(any("vaapi-copy" in error for error in errors))

    def test_runtime_identity_is_strict_only_for_publication(self):
        self.assertEqual(publication_runtime_errors(pinned_publication_runtime()), ())
        soname_link = pinned_publication_runtime()
        soname_link["libmpv_path"] = (
            f"/work{PUBLICATION_LIBMPV_PREFIX_SUFFIX}libmpv.so.2"
        )
        self.assertEqual(publication_runtime_errors(soname_link), ())
        bad = pinned_publication_runtime()
        bad["libmpv_path"] = "/usr/lib/libmpv.so.2"
        bad["mpv_version"] = "mpv v0.41.0"
        bad["ffmpeg_version"] = "8.1.1"
        errors = publication_runtime_errors(bad)
        self.assertEqual(len(errors), 3)
        self.assertTrue(any("pinned GPU Next prefix" in error for error in errors))

    def test_publication_evaluation_rejects_backend_fallback_and_runtime_drift(self):
        final = publication_metrics("hdr")
        result, errors = evaluate_publication_validation(
            active_api="opengl-next",
            session_status="active",
            expected_api="opengl-next",
            final=final,
            step_statuses=("PASS",) * 4,
            state_sequence=(
                (0, "sdr", "PASS"),
                (1, "hdr", "PASS"),
                (0, "sdr", "PASS"),
                (1, "hdr", "PASS"),
            ),
            failure_reason=None,
            isolation_failures=(),
        )
        self.assertEqual((result, errors), ("PASS", ()))
        final["render_runtime"]["libplacebo_version"] = "v7.359.0"
        result, errors = evaluate_publication_validation(
            active_api="opengl",
            session_status="startup-fallback",
            expected_api="opengl-next",
            final=final,
            step_statuses=("PASS", "FAIL"),
            state_sequence=((0, "sdr", "PASS"),),
            failure_reason=None,
            isolation_failures=(),
        )
        self.assertEqual(result, "FAIL")
        self.assertTrue(any("active API" in error for error in errors))
        self.assertTrue(any("libplacebo" in error for error in errors))
        self.assertTrue(any("sequence" in error for error in errors))

    def test_publication_switch_requires_a_new_render_frame_generation(self):
        session = GpuValidationSession.__new__(GpuValidationSession)
        session.config = SimpleNamespace(
            mode="publication", expected_backend="gpu-next"
        )
        session.video_area = SimpleNamespace(
            render_backend_active="opengl-next",
            render_session_status="active",
        )
        session._publication_context_generation = 4
        session._pending_render_frame_generation = 12
        after = publication_metrics("hdr")
        after["render_frame_generation"] = 12
        status, detail = session._verify_step(
            ValidationStep("switch SDR to HDR publication", "switch-media", 2, (1, "hdr")),
            after,
        )
        self.assertEqual(status, "FAIL")
        self.assertIn("no newer frame", detail)
        after["render_frame_generation"] = 13
        status, detail = session._verify_step(
            ValidationStep("switch SDR to HDR publication", "switch-media", 2, (1, "hdr")),
            after,
        )
        self.assertEqual(status, "PASS")

    def test_publication_start_command_failure_is_reportable(self):
        failures = []

        class FailingPlayer:
            def command(self, *args):
                raise RuntimeError("mpv command rejected")

        session = GpuValidationSession.__new__(GpuValidationSession)
        session.config = SimpleNamespace(mode="publication", expected_backend="legacy")
        session.player = FailingPlayer()
        session.video_area = SimpleNamespace(
            hdr_controller=SimpleNamespace(hdr_mode="auto"),
        )
        session.window = SimpleNamespace(
            show_toast=lambda *args: None,
            connect=lambda *args: 1,
        )
        session._finish = failures.append
        with self.assertLogs("src.gpu_validation", level="ERROR"):
            session.start()
        self.assertEqual(len(failures), 1)
        self.assertIn("could not prepare publication validation", failures[0])

    def test_pause_uses_the_settled_position_baseline(self):
        session = GpuValidationSession.__new__(GpuValidationSession)
        session.video_area = SimpleNamespace(
            render_backend_active="opengl-next",
            render_session_status="active",
        )
        session._pending_before_position = 2625.0
        session._pending_position_available = True
        status, detail = session._verify_step(
            ValidationStep("pause", "pause", 3),
            {
                "time_pos": 2625.1,
                "time_pos_available": True,
                "pause": True,
            },
        )
        self.assertEqual(status, "PASS")
        self.assertIn("0.10s", detail)

    def test_playback_jump_cannot_produce_a_false_pass(self):
        session = GpuValidationSession.__new__(GpuValidationSession)
        session.video_area = SimpleNamespace(
            render_backend_active="opengl-next",
            render_session_status="active",
        )
        session._pending_before_position = 100.0
        session._pending_position_available = True
        status, detail = session._verify_step(
            ValidationStep("warm playback", "play", 8),
            {
                "time_pos": 3000.0,
                "time_pos_available": True,
                "pause": False,
                "speed": 1.0,
            },
        )
        self.assertEqual(status, "FAIL")
        self.assertIn("jumped forward", detail)

    def test_ignored_resize_cannot_produce_a_clean_pass(self):
        session = GpuValidationSession.__new__(GpuValidationSession)
        session.video_area = SimpleNamespace(
            render_backend_active="opengl-next",
            render_session_status="active",
        )
        session._pending_before_size = (1120, 630)
        status, detail = session._verify_step(
            ValidationStep("resize", "resize", 5, (960, 540)),
            {"time_pos": 0, "width": 1120, "height": 630},
        )
        self.assertEqual(status, "WARN")
        self.assertIn("kept the previous", detail)

    def test_context_cycle_requires_a_new_context_and_preserves_hdr_state(self):
        old_context = object()
        new_context = object()
        session = GpuValidationSession.__new__(GpuValidationSession)
        session.video_area = SimpleNamespace(
            render_backend_active="opengl-next",
            render_session_status="active",
            render_context_generation=2,
            mpv_ctx=new_context,
            gl_area=SimpleNamespace(get_realized=lambda: True),
            _first_frame_logged=True,
            hdr_controller=SimpleNamespace(
                _disconnected=False,
                is_hdr_content=True,
            ),
            render_target_format="GL_RGBA16F",
            render_target_depth=16,
        )
        session._pending_context_generation = 1
        session._pending_context = old_context
        session._pending_hdr_state = True
        session._pending_render_target = ("GL_RGBA16F", 16)

        status, detail = session._verify_step(
            ValidationStep("GL context cycle 1", "context-cycle", 10, 1),
            {"time_pos": 100.0},
        )

        self.assertEqual(status, "PASS")
        self.assertIn("1→2", detail)

    def test_context_cycle_pauses_before_unrealize_and_schedules_realize(self):
        events = []

        class FakePlayer:
            def command(self, *args):
                events.append(("command", args))

        class FakeGlArea:
            def get_realized(self):
                return True

            def unrealize(self):
                events.append(("unrealize", ()))

        old_context = object()
        gl_area = FakeGlArea()
        session = GpuValidationSession.__new__(GpuValidationSession)
        session.player = FakePlayer()
        session.window = SimpleNamespace(
            get_width=lambda: 1280,
            get_height=lambda: 720,
        )
        session.video_area = SimpleNamespace(
            gl_area=gl_area,
            mpv_ctx=old_context,
            render_context_generation=1,
            hdr_controller=SimpleNamespace(is_hdr_content=True),
            render_target_format="GL_RGBA16F",
            render_target_depth=16,
        )

        with patch(
            "src.gpu_validation.GLib.timeout_add", return_value=99
        ) as timeout_add:
            session._apply_step(
                ValidationStep("GL context cycle 1", "context-cycle", 10, 1)
            )

        self.assertEqual(events[0], ("command", ("set", "pause", "yes")))
        self.assertEqual(events[1], ("unrealize", ()))
        timeout_add.assert_called_once_with(
            500, session._realize_validation_gl_area, gl_area
        )
        self.assertEqual(session.lifecycle_timer_id, 99)
        self.assertIs(session._pending_context, old_context)

    def test_context_cycle_fails_if_generation_does_not_advance(self):
        context = object()
        session = GpuValidationSession.__new__(GpuValidationSession)
        session.video_area = SimpleNamespace(
            render_backend_active="opengl-next",
            render_session_status="active",
            render_context_generation=1,
            mpv_ctx=context,
        )
        session._pending_context_generation = 1
        session._pending_context = context

        status, detail = session._verify_step(
            ValidationStep("GL context cycle 1", "context-cycle", 10, 1),
            {"time_pos": 100.0},
        )

        self.assertEqual(status, "FAIL")
        self.assertIn("not recreated", detail)

    def test_transition_accepts_published_hdr_state_after_a_new_frame(self):
        session = GpuValidationSession.__new__(GpuValidationSession)
        session.video_area = SimpleNamespace(
            render_backend_active="opengl-next",
            render_session_status="active",
        )
        session._pending_render_frame_generation = 10
        session._transition_context_generation = 1
        status, detail = session._verify_step(
            ValidationStep(
                "switch SDR to HDR", "switch-media", 12, (1, "hdr")
            ),
            {
                "time_pos": 12.0,
                "playlist_pos": 1,
                "render_frame_generation": 11,
                "render_context_generation": 1,
                "source_hdr": True,
                "hdr_output_active": True,
                "render_target_format": "GL_RGBA16F",
                "render_target_depth": 16,
                "render_color_state": "rec2100-pq",
                "dovi_profile": 8,
                "hwdec": "vaapi-copy",
                "target_trc": "pq",
                "target_prim": "bt.2020",
                "target_peak": 743,
            },
        )
        self.assertEqual(status, "PASS")
        self.assertIn("HDR source", detail)
        self.assertIn("GL_RGBA16F/16-bit", detail)

    def test_transition_rejects_stale_hdr_surface_on_sdr(self):
        session = GpuValidationSession.__new__(GpuValidationSession)
        session.video_area = SimpleNamespace(
            render_backend_active="opengl-next",
            render_session_status="active",
        )
        session._pending_render_frame_generation = 20
        session._transition_context_generation = 1
        status, detail = session._verify_step(
            ValidationStep(
                "switch HDR to SDR", "switch-media", 12, (0, "sdr")
            ),
            {
                "time_pos": 8.0,
                "playlist_pos": 0,
                "render_frame_generation": 21,
                "render_context_generation": 1,
                "source_hdr": False,
                "hdr_output_active": False,
                "render_target_format": "GL_RGBA16F",
                "render_target_depth": 16,
                "render_color_state": "rec2100-pq",
                "dovi_profile": None,
            },
        )
        self.assertEqual(status, "FAIL")
        self.assertIn("published target", detail)

    def test_counter_growth_survives_mpv_counter_reset(self):
        session = GpuValidationSession.__new__(GpuValidationSession)
        session.baseline = {"decoder_drops": 10}
        session.step_results = [
            {"metrics": {"decoder_drops": 12}},
            {"metrics": {"decoder_drops": 1}},
        ]
        self.assertEqual(
            session._counter_growth({"decoder_drops": 3}, "decoder_drops"),
            5,
        )

    def test_transition_resource_growth_compares_repeated_hdr_states(self):
        session = GpuValidationSession.__new__(GpuValidationSession)
        session.step_results = [
            {
                "action": "signal-state",
                "metrics": {"source_hdr": False, "drm_vram_kib": 200},
            },
            {
                "action": "switch-media",
                "metrics": {"source_hdr": True, "drm_vram_kib": 700},
            },
            {
                "action": "switch-media",
                "metrics": {"source_hdr": False, "drm_vram_kib": 600},
            },
            {
                "action": "switch-media",
                "metrics": {"source_hdr": True, "drm_vram_kib": 710},
            },
        ]
        self.assertEqual(
            session._transition_repeated_hdr_growth("drm_vram_kib"), 10
        )

    def test_numeric_hdr_target_is_reported_as_hdr_to_hdr_tone_mapping(self):
        self.assertTrue(is_tone_mapping_active(True, True, 743))
        self.assertFalse(is_tone_mapping_active(True, True, "auto"))
        self.assertTrue(is_tone_mapping_active(True, False, "auto"))
        self.assertFalse(is_tone_mapping_active(False, True, 743))


class ValidationIntegrationTests(unittest.TestCase):
    def test_resource_settling_launcher_is_isolated_and_accepts_pass_or_warn(self):
        launcher_path = ROOT / "run_gate2_resource_settling.sh"
        launcher = launcher_path.read_text(encoding="utf-8")
        self.assertTrue(launcher_path.stat().st_mode & 0o111)
        self.assertIn("set -euo pipefail", launcher)
        self.assertIn('echo "Usage: $0"', launcher)
        self.assertIn('mktemp -d /tmp/cinehdr-resource-settling-fixtures.XXXXXX', launcher)
        self.assertIn('fixture_profile="hevc-main10-yuv420p10-lossless"', launcher)
        self.assertIn('fixture_revision="hevc-main10-yuv420p10-mp4-v2"', launcher)
        self.assertIn(PUBLICATION_HDR_SHA256, launcher)
        self.assertIn(PUBLICATION_SDR_SHA256, launcher)
        self.assertIn('tests/generate_patterns.py', launcher)
        self.assertIn('metadata.get("generator_revision") != revision', launcher)
        self.assertIn('hashlib.sha256(media.read_bytes()).hexdigest()', launcher)
        self.assertIn('sha256sum -- "${sdr_media}"', launcher)
        self.assertIn('"CINEHDR_GPU_VALIDATION=settling"', launcher)
        self.assertIn('"CINEHDR_PUBLICATION_EXPECTED_BACKEND=${backend}"', launcher)
        self.assertIn('"CINEHDR_PUBLICATION_FIXTURE_PROFILE=${fixture_profile}"', launcher)
        self.assertIn('"CINEHDR_PUBLICATION_FIXTURE_REVISION=${fixture_revision}"', launcher)
        self.assertIn('"CINEHDR_PUBLICATION_HDR_SHA256=${hdr_sha256}"', launcher)
        self.assertIn('"CINEHDR_PUBLICATION_SDR_SHA256=${sdr_sha256}"', launcher)
        self.assertIn('python3 "${script_dir}/run_dev.py" "${sdr_media}" "${hdr_media}"', launcher)
        self.assertLess(launcher.index("run_backend legacy"), launcher.index("run_backend gpu-next"))
        self.assertIn('tee "${log_path}"', launcher)
        self.assertIn('application_status="${PIPESTATUS[0]}"', launcher)
        self.assertIn('gate2-resource-settling-${backend}-*.json', launcher)
        self.assertIn('-newer "${marker}"', launcher)
        self.assertIn('[[ ${#reports[@]} -ne 1 ]]', launcher)
        self.assertIn('report.get("status") not in {"PASS", "WARN"}', launcher)
        self.assertIn(SETTLING_SCHEMA, launcher)
        self.assertIn(SETTLING_REVISION, launcher)
        self.assertNotIn("flatpak", launcher.lower())
        self.assertNotIn("/dev/dri", launcher)
        self.assertNotIn("--device", launcher)
        self.assertNotIn("DRI_PRIME=", launcher)

    def test_resource_settling_report_is_machine_readable_and_bounded(self):
        source = (ROOT / "src/gpu_validation.py").read_text(encoding="utf-8")
        self.assertIn('"schema": SETTLING_SCHEMA', source)
        self.assertIn('"status": overall', source)
        self.assertIn('"hdr_endpoints": list(endpoint_results)', source)
        self.assertIn('"process_drm_gtt_kib"', source)
        self.assertIn('"global_vram_kib"', source)
        self.assertIn("not a leak-free claim", source)
        self.assertIn("not fit a byte threshold", source)
        self.assertIn("f\"gate2-resource-settling-{self.config.expected_backend}-{stamp}.json\"", source)

    def test_wayland_publication_launcher_is_strict_and_runs_two_processes(self):
        launcher_path = ROOT / "run_wayland_publication_validation.sh"
        launcher = launcher_path.read_text(encoding="utf-8")
        self.assertTrue(launcher_path.stat().st_mode & 0o111)
        self.assertIn("set -euo pipefail", launcher)
        self.assertIn('if [[ $# -eq 1 && "$1" == "--protocol-trace" ]]', launcher)
        self.assertIn('echo "Usage: $0 [--protocol-trace]"', launcher)
        self.assertIn('mktemp -d /tmp/cinehdr-wayland-publication-fixtures.XXXXXX', launcher)
        self.assertIn('fixture_profile="hevc-main10-yuv420p10-lossless"', launcher)
        self.assertIn('fixture_revision="hevc-main10-yuv420p10-mp4-v2"', launcher)
        self.assertIn(PUBLICATION_HDR_SHA256, launcher)
        self.assertIn(PUBLICATION_SDR_SHA256, launcher)
        self.assertIn('tests/generate_patterns.py', launcher)
        self.assertIn('--fixture-profile "${fixture_profile}"', launcher)
        self.assertIn('fixture.get("metadata_path", "")', launcher)
        self.assertIn('media.suffix != ".mp4"', launcher)
        self.assertIn('hashlib.sha256(media.read_bytes()).hexdigest()', launcher)
        self.assertIn('sha256sum -- "${sdr_media}"', launcher)
        self.assertIn('sha256sum -- "${hdr_media}"', launcher)
        self.assertIn('mapfile -t fixture_paths', launcher)
        self.assertIn('"CINEHDR_RENDER_BACKEND=${backend}"', launcher)
        self.assertIn('"CINEHDR_GPU_VALIDATION=publication"', launcher)
        self.assertIn('"CINEHDR_PUBLICATION_EXPECTED_BACKEND=${backend}"', launcher)
        self.assertIn('"CINEHDR_PUBLICATION_FIXTURE_PROFILE=${fixture_profile}"', launcher)
        self.assertIn('"CINEHDR_PUBLICATION_FIXTURE_REVISION=${fixture_revision}"', launcher)
        self.assertIn('"CINEHDR_PUBLICATION_HDR_SHA256=${hdr_sha256}"', launcher)
        self.assertIn('"CINEHDR_PUBLICATION_SDR_SHA256=${sdr_sha256}"', launcher)
        self.assertIn('python3 "${script_dir}/run_dev.py" "${sdr_media}" "${hdr_media}"', launcher)
        self.assertLess(launcher.index("run_backend legacy"), launcher.index("run_backend gpu-next"))
        self.assertIn('tee "${log_path}"', launcher)
        self.assertIn('application_status="${PIPESTATUS[0]}"', launcher)
        self.assertIn('gate2-wayland-publication-${backend}-*.txt', launcher)
        self.assertIn('-newer "${marker}"', launcher)
        self.assertIn('[[ ${#reports[@]} -ne 1 ]]', launcher)
        self.assertIn('grep -Fxq "Overall result: PASS"', launcher)
        self.assertIn('process_env+=("WAYLAND_DEBUG=client")', launcher)
        self.assertIn('process_env+=("GDK_DEBUG=color-mgmt")', launcher)
        self.assertIn('process_env+=("GDK_DEBUG=${GDK_DEBUG}:color-mgmt")', launcher)
        self.assertIn('case ":${GDK_DEBUG:-}:" in', launcher)
        self.assertNotIn('export GDK_DEBUG', launcher)
        self.assertIn('if [[ "${protocol_trace}" == true ]]; then', launcher)
        self.assertIn('tests/parse_wayland_color_trace.py', launcher)
        self.assertIn('--backend "${backend}"', launcher)
        self.assertIn('gate2-wayland-color-management-${backend}-${run_stamp}.json', launcher)
        self.assertIn('gate2-wayland-color-management-${backend}-*.json', launcher)
        self.assertIn('protocol_reports[@]} -ne 1', launcher)
        self.assertIn('"status") != "PASS"', launcher)
        self.assertIn('evidence.get("backend") != backend', launcher)
        trace_condition = launcher.index('if [[ "${protocol_trace}" == true ]]; then')
        parser = launcher.index('tests/parse_wayland_color_trace.py')
        self.assertLess(trace_condition, parser)
        self.assertLess(trace_condition, launcher.index('GDK_DEBUG=color-mgmt'))
        self.assertEqual(launcher.count('GDK_DEBUG=color-mgmt'), 1)
        self.assertNotIn("flatpak", launcher.lower())
        self.assertNotIn("/dev/dri", launcher)
        self.assertNotIn("--device", launcher)
        self.assertNotIn("DRI_PRIME=", launcher)

    def test_flatpak_launcher_is_quick_only_and_path_scoped(self):
        launcher = (ROOT / "run_flatpak_gpu_validation.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('app_id="io.github.rusmikev.CineHDR"', launcher)
        self.assertIn(
            'bundle_path="${script_dir}/io.github.rusmikev.CineHDR-gpu-next.flatpak"',
            launcher,
        )
        self.assertIn(
            'expected_commit="4edda5ddca09dd99f8897e3c51738a4f508bc7318029c891e0c4e78229e5d736"',
            launcher,
        )
        self.assertIn('Usage: $0 <video-file> [quick]', launcher)
        self.assertIn('"$2" == "quick"', launcher)
        self.assertNotIn('"$2" == "soak"', launcher)
        self.assertNotIn('"$2" == "lifecycle"', launcher)
        self.assertNotIn('"$2" == "transition"', launcher)
        self.assertIn('[[ ! -f "$1" ]]', launcher)
        self.assertIn('realpath -e -- "$1"', launcher)
        self.assertIn('WAYLAND_DISPLAY', launcher)
        self.assertIn('flatpak info --user "${app_id}"', launcher)
        self.assertIn(
            'flatpak info --show-commit --user "${app_id}"', launcher
        )
        self.assertIn('[[ "${installed_commit}" != "${expected_commit}" ]]', launcher)
        self.assertIn("flatpak install --user --or-update %q", launcher)
        self.assertLess(
            launcher.index('flatpak info --user "${app_id}"'),
            launcher.index('[[ ! -f "$1" ]]'),
        )
        self.assertIn('"--filesystem=${video_path}:ro"', launcher)
        self.assertIn('"--filesystem=${report_dir}:rw"', launcher)
        self.assertNotIn('--device=', launcher)
        self.assertIn('"--command=python3"', launcher)
        self.assertIn('logging.basicConfig(level=logging.INFO)', launcher)
        self.assertIn("import mpv", launcher)
        self.assertIn("original_mpv_init = mpv.MPV.__init__", launcher)
        self.assertIn(
            'kwargs["log_handler"] = validation_mpv_log_handler', launcher
        )
        self.assertIn('kwargs["loglevel"] = "v"', launcher)
        self.assertIn("mpv.MPV.__init__ = validation_mpv_init", launcher)
        self.assertIn("cinehdr.validation.libmpv", launcher)
        self.assertIn(
            'runpy.run_path("/app/bin/cinehdr", run_name="__main__")',
            launcher,
        )
        bootstrap_start = launcher.index("import logging\nimport mpv\nimport runpy")
        bootstrap_end = launcher.index(
            '\n    "${video_path}"', bootstrap_start
        )
        bootstrap = launcher[bootstrap_start:bootstrap_end].rstrip("' \\")
        compile(bootstrap, "<validation bootstrap>", "exec")
        self.assertIn('"--env=GDK_BACKEND=wayland"', launcher)
        self.assertIn('"--env=DRI_PRIME=pci-0000_03_00_0"', launcher)
        self.assertNotIn("LIBVA_DRIVERS_PATH", launcher)
        self.assertIn('"--env=LIBVA_MESSAGING_LEVEL=2"', launcher)
        self.assertIn('"--env=CINEHDR_RENDER_BACKEND=gpu-next"', launcher)
        self.assertIn('"--env=CINEHDR_GPU_VALIDATION=quick"', launcher)
        self.assertIn(
            '"--env=CINEHDR_GPU_VALIDATION_REPORT_DIR=${report_dir}"', launcher
        )
        self.assertIn("original_loadfile = mpv.MPV.loadfile", launcher)
        self.assertIn('self["hwdec"] = "vaapi-copy"', launcher)
        self.assertIn('tee "${validation_log}"', launcher)
        self.assertIn('application_status="${PIPESTATUS[0]}"', launcher)
        self.assertIn("-name 'gpu-next-quick-*.txt'", launcher)
        self.assertIn('-newer "${run_marker}"', launcher)
        self.assertIn("grep -Eq '^Overall result: (PASS|WARN)$'", launcher)
        self.assertIn("grep -q '^Hardware decoding: vaapi-copy$'", launcher)
        self.assertIn("grep -q '^Active renderer: opengl-next$'", launcher)
        self.assertIn("grep -q '^Renderer status: active$'", launcher)
        self.assertIn("grep -q '^OpenGL renderer:.*RX 9060 XT'", launcher)

    def test_launcher_is_strict_and_validator_remains_opt_in(self):
        launcher = (ROOT / "run_gpu_validation.sh").read_text(encoding="utf-8")
        main = (ROOT / "src/main.py").read_text(encoding="utf-8")
        self.assertIn('CINEHDR_RENDER_BACKEND="gpu-next"', launcher)
        self.assertIn('CINEHDR_GPU_VALIDATION="${validation_mode}"', launcher)
        self.assertIn('validation_mode="transition"', launcher)
        self.assertIn('"$2" == "sdr-soak"', launcher)
        self.assertIn('"${video_files[@]}"', launcher)
        self.assertIn('sdr_transfer="$(ffprobe', launcher)
        self.assertIn('hdr_transfer="$(ffprobe', launcher)
        self.assertIn('tee "${validation_log}"', launcher)
        self.assertIn('application_status="${PIPESTATUS[0]}"', launcher)
        self.assertIn('-newer "${run_marker}"', launcher)
        self.assertIn("процесс завершился без нового отчёта", launcher)
        self.assertIn('os.environ.get("CINEHDR_GPU_VALIDATION"', main)
        self.assertIn("Gio.ApplicationFlags.NON_UNIQUE", main)
        self.assertIn("rotation_value = parts[2].strip()", main)
        self.assertLess(
            main.index("prepare_validation_player"),
            main.index('win.mpv.loadfile(path, "append-play")'),
        )
        self.assertIn(
            "win.mpv, self.gpu_validation_config.mode", main
        )
        window = (ROOT / "src/window.py").read_text(encoding="utf-8")
        self.assertIn("Validation seeks must never overwrite", window)
        validation_flag = window.index("self._is_gpu_validation: bool")
        restore_guard = window.index("and not self._is_gpu_validation")
        restore_call = window.index("restore_last_playlist(self, self.app, self.mpv)")
        self.assertLess(validation_flag, restore_guard)
        self.assertLess(restore_guard, restore_call)
        self.assertIn("Ignoring session save during GPU validation", window)

    def test_workspace_player_setup_has_no_validation_log_handler(self):
        window = (ROOT / "src/window.py").read_text(encoding="utf-8")
        self.assertNotIn("validation_mpv_log_options", window)
        self.assertNotIn("mpv_logging", window)

    def test_report_source_omits_media_path_and_records_gl_identity(self):
        source = (ROOT / "src/gpu_validation.py").read_text(encoding="utf-8")
        self.assertIn('"Media path: omitted"', source)
        self.assertNotIn("path-expanded", source)
        self.assertIn("OpenGL renderer:", source)
        self.assertIn("GLSL version:", source)
        self.assertIn("Render context generation:", source)
        self.assertIn("Published color state:", source)
        self.assertIn("saved preference unchanged", source)
        self.assertIn("across file reloads (informational)", source)
        self.assertIn("Repeated HDR state", source)

    def test_renderer_counts_only_successfully_created_contexts(self):
        source = (ROOT / "src/video_widget.py").read_text(encoding="utf-8")
        creation = source.index("self.mpv_ctx, backend_state = create_render_context")
        generation = source.index("self.render_context_generation += 1")
        active = source.index("self.render_backend_active = backend_state.active")
        self.assertLess(creation, generation)
        self.assertLess(generation, active)

    def test_renderer_records_the_color_state_of_each_published_frame(self):
        source = (ROOT / "src/video_widget.py").read_text(encoding="utf-8")
        self.assertIn('color_state = "rec2100-pq"', source)
        self.assertIn('color_state = "srgb"', source)
        texture = source.index("self.current_texture = texture")
        color_state = source.index("self.render_color_state = color_state")
        generation = source.index("self.render_frame_generation += 1")
        self.assertLess(texture, color_state)
        self.assertLess(color_state, generation)


class FlatpakLauncherReportVerificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        launcher = (ROOT / "run_flatpak_gpu_validation.sh").read_text(
            encoding="utf-8"
        )
        check_start = launcher.index(
            'if [[ -z "${new_report}" || ! -f "${new_report}" ]]; then'
        )
        cls.verify_bash_snippet = launcher[check_start:]

    def _run_verification(
        self, report_content: str | None, filename: str = "gpu-next-quick-test.txt"
    ) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            if report_content is not None:
                report_file = tmp_path / filename
                report_file.write_text(report_content, encoding="utf-8")
                new_report_val = str(report_file)
            else:
                new_report_val = str(tmp_path / "non_existent.txt")

            bash_cmd = f"""
new_report="{new_report_val}"
validation_log="{tmpdir}/mock_validation.log"
{self.verify_bash_snippet}
"""
            return subprocess.run(
                ["bash", "-euo", "pipefail", "-c", bash_cmd],
                capture_output=True,
                text=True,
            )

    def _valid_report(self, **overrides) -> str:
        fields = {
            "result": "PASS",
            "active_renderer": "opengl-next",
            "renderer_status": "active",
            "hwdec": "vaapi-copy",
            "gl_renderer": "AMD Radeon RX 9060 XT (radeonsi, gfx1200, ACO, DRM 3.64, 7.2.5-200.fc44.x86_64)",
            "extra": "",
        }
        fields.update(overrides)
        return (
            f"CineHDR GPU Next Validation Report\n\n"
            f"Overall result: {fields['result']}\n"
            f"Mode: quick\n"
            f"Active renderer: {fields['active_renderer']}\n"
            f"Renderer status: {fields['renderer_status']}\n"
            f"Hardware decoding: {fields['hwdec']}\n"
            f"OpenGL renderer: {fields['gl_renderer']}\n"
            f"{fields['extra']}"
        )

    def test_valid_pass_report(self):
        report = self._valid_report()
        res = self._run_verification(report)
        self.assertEqual(res.returncode, 0, f"Stderr: {res.stderr}")
        self.assertIn("CineHDR Flatpak validation report:", res.stdout)
        self.assertNotIn("(WARN)", res.stdout)

    def test_valid_warn_report(self):
        report = self._valid_report(
            result="WARN",
            extra="[Warnings]\n- temporary dropped frame during seek\n",
        )
        res = self._run_verification(report)
        self.assertEqual(res.returncode, 0, f"Stderr: {res.stderr}")
        self.assertIn("CineHDR Flatpak validation report (WARN):", res.stdout)

    def test_rejects_fail_status(self):
        report = self._valid_report(result="FAIL")
        res = self._run_verification(report)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn(
            "status line is not exactly 'Overall result: PASS' or 'Overall result: WARN'",
            res.stderr,
        )

    def test_rejects_unknown_status(self):
        report = self._valid_report(result="UNKNOWN")
        res = self._run_verification(report)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn(
            "status line is not exactly 'Overall result: PASS' or 'Overall result: WARN'",
            res.stderr,
        )

    def test_rejects_extra_words_in_pass_status(self):
        report = self._valid_report(result="PASS with caveats")
        res = self._run_verification(report)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn(
            "status line is not exactly 'Overall result: PASS' or 'Overall result: WARN'",
            res.stderr,
        )

    def test_rejects_extra_words_in_warn_status(self):
        report = self._valid_report(result="WARN (non-critical)")
        res = self._run_verification(report)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn(
            "status line is not exactly 'Overall result: PASS' or 'Overall result: WARN'",
            res.stderr,
        )

    def test_rejects_duplicate_status_lines(self):
        report = (
            "Overall result: PASS\n"
            "Overall result: WARN\n"
            "Active renderer: opengl-next\n"
            "Renderer status: active\n"
            "Hardware decoding: vaapi-copy\n"
            "OpenGL renderer: AMD Radeon RX 9060 XT (radeonsi, gfx1200)\n"
        )
        res = self._run_verification(report)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("ambiguous or missing report status", res.stderr)

    def test_rejects_duplicate_active_renderer(self):
        report = self._valid_report(extra="Active renderer: opengl\n")
        res = self._run_verification(report)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn(
            "ambiguous, duplicate or missing 'Active renderer'", res.stderr
        )

    def test_rejects_duplicate_renderer_status(self):
        report = self._valid_report(extra="Renderer status: startup-fallback\n")
        res = self._run_verification(report)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn(
            "ambiguous, duplicate or missing 'Renderer status'", res.stderr
        )

    def test_rejects_duplicate_hardware_decoding(self):
        report = self._valid_report(extra="Hardware decoding: no\n")
        res = self._run_verification(report)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn(
            "ambiguous, duplicate or missing 'Hardware decoding'", res.stderr
        )

    def test_rejects_duplicate_opengl_renderer(self):
        report = self._valid_report(
            extra="OpenGL renderer: AMD Radeon 610M (Raphael)\n"
        )
        res = self._run_verification(report)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn(
            "ambiguous, duplicate or missing 'OpenGL renderer'", res.stderr
        )

    def test_rejects_wrong_gpu(self):
        report = self._valid_report(gl_renderer="AMD Radeon 610M (Raphael)")
        res = self._run_verification(report)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("expected RX 9060 XT in 'OpenGL renderer'", res.stderr)

    def test_rejects_software_decoder(self):
        report = self._valid_report(hwdec="no")
        res = self._run_verification(report)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("expected 'Hardware decoding: vaapi-copy'", res.stderr)

    def test_rejects_legacy_renderer(self):
        report = self._valid_report(active_renderer="opengl")
        res = self._run_verification(report)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("expected 'Active renderer: opengl-next'", res.stderr)

    def test_rejects_missing_report_file(self):
        res = self._run_verification(None)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("without a valid report file", res.stderr)

    def test_rejects_empty_report(self):
        res = self._run_verification("")
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("ambiguous or missing report status", res.stderr)


class FlatpakLauncherBootstrapExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        launcher = (ROOT / "run_flatpak_gpu_validation.sh").read_text(
            encoding="utf-8"
        )
        bootstrap_start = launcher.index(
            "import logging\nimport mpv\nimport runpy"
        )
        bootstrap_end = launcher.index('\n    "${video_path}"', bootstrap_start)
        cls.bootstrap_code = launcher[bootstrap_start:bootstrap_end].rstrip(
            "' \\\n"
        )

    def test_actual_launcher_bootstrap_executes_loadfile_interception_without_gtk_or_gl(
        self,
    ):
        events = []

        class MockMPV:
            def __init__(self, *args, **kwargs):
                events.append(("init_called", args, kwargs))
                self.options = {"hwdec": "no"}

            def __setitem__(self, key, value):
                events.append(("set_item", key, value))
                self.options[key] = value

            def __getitem__(self, key):
                return self.options.get(key)

            def loadfile(self, *args, **kwargs):
                events.append(
                    (
                        "loadfile_called",
                        args,
                        kwargs,
                        self.options.get("hwdec"),
                    )
                )
                return "LOADFILE_RESULT_PRESERVED"

        mock_mpv_mod = types.ModuleType("mpv")
        mock_mpv_mod.MPV = MockMPV

        mock_runpy_mod = types.ModuleType("runpy")
        run_path_mock = MagicMock(return_value={"status": "app_ran"})
        mock_runpy_mod.run_path = run_path_mock

        with patch.dict(
            "sys.modules", {"mpv": mock_mpv_mod, "runpy": mock_runpy_mod}
        ):
            compiled = compile(
                self.bootstrap_code, "<validation bootstrap>", "exec"
            )
            namespace = {}
            exec(compiled, namespace)

            run_path_mock.assert_called_once_with(
                "/app/bin/cinehdr", run_name="__main__"
            )

            player = mock_mpv_mod.MPV("custom_arg", flag=True)
            self.assertEqual(player["hwdec"], "no")
            init_events = [e for e in events if e[0] == "init_called"]
            self.assertEqual(len(init_events), 1)
            self.assertEqual(init_events[0][2].get("loglevel"), "v")
            self.assertTrue(callable(init_events[0][2].get("log_handler")))

            result = player.loadfile(
                "/video/media.mkv", "append-play", start_pos=15.0
            )
            self.assertEqual(result, "LOADFILE_RESULT_PRESERVED")

            sub_events = [
                e for e in events if e[0] in ("set_item", "loadfile_called")
            ]
            self.assertEqual(len(sub_events), 2)
            self.assertEqual(sub_events[0], ("set_item", "hwdec", "vaapi-copy"))
            self.assertEqual(
                sub_events[1],
                (
                    "loadfile_called",
                    ("/video/media.mkv", "append-play"),
                    {"start_pos": 15.0},
                    "vaapi-copy",
                ),
            )
            self.assertEqual(player["hwdec"], "vaapi-copy")


if __name__ == "__main__":
    unittest.main()
