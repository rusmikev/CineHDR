# Copyright 2026 rusmikev
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import contextlib
import hashlib
import io
import sys
from pathlib import Path
import tempfile
import unittest

REPO_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from parse_wayland_color_trace import (
    MULTIPHASE_REVISION,
    MULTIPHASE_SCOPE,
    PRIMARY_COMPOSITE_REVISION,
    PRIMARY_COMPOSITE_SCOPE,
    TRACE_REVISION,
    TRACE_SCHEMA,
    TraceValidationError,
    create_evidence_report,
    main,
    validate_composite_window_trace,
    validate_multiphase_trace,
    validate_trace,
)


VALID_TRACE = """
noise from CineHDR logging is ignored
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  wl_registry#2.global(19, "wp_color_manager_v1", 1)
[TID 27631] [ 1042.006]  -> wl_registry#2.bind(19, "wp_color_manager_v1", 1, new id [unknown]#11)
[ 1042.007]  wp_color_manager_v1#11.supported_intent(0)
[ 1042.008]  wp_color_manager_v1#11.done()
[ 1042.009]  -> wp_color_manager_v1#11.get_surface(new id wp_color_management_surface_v1#23, wl_surface#8)
[ 1042.010]  wp_image_description_v1#31.ready(123)
[ 1042.011]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.012]  -> wl_surface#8.commit()
""".strip()


class WaylandColorTraceTests(unittest.TestCase):
    def test_early_bind_survives_later_registry_id_reuse(self):
        trace = (
            "-> wl_display#1.get_registry(new id wl_registry#2)\n"
            + VALID_TRACE.replace(
                'wp_color_manager_v1#11.get_surface(',
                'wl_display#1.get_registry(new id wl_registry#2)\n'
                'wl_registry#2.global(19, "wp_color_manager_v1", 2)\n'
                'wp_color_manager_v1#11.get_surface(',
            )
        )
        evidence = validate_trace(trace)
        self.assertEqual(evidence["matched"]["registry_global"]["registry_id"], 2)
        self.assertEqual(evidence["matched"]["registry_global"]["id"], 19)
        self.assertEqual(evidence["matched"]["registry_global"]["event_index"], 6)
        self.assertEqual(evidence["matched"]["registry_global"]["advertised_version"], 1)
        self.assertEqual(evidence["matched"]["manager"]["bound_version"], 1)

    def test_early_bind_survives_later_advertisement_on_other_registry(self):
        trace = VALID_TRACE.replace(
            'wp_color_manager_v1#11.get_surface(',
            'wl_registry#7.global(19, "wp_color_manager_v1", 2)\n'
            'wp_color_manager_v1#11.get_surface(',
        )
        evidence = validate_trace(trace)
        self.assertEqual(evidence["matched"]["registry_global"]["registry_id"], 2)
        self.assertEqual(evidence["matched"]["registry_global"]["id"], 19)

    def test_future_or_other_registry_advertisement_cannot_authorize_bind(self):
        for trace in (
            VALID_TRACE.replace(
                'wl_registry#2.global(19, "wp_color_manager_v1", 1)\n', ''
            ) + '\nwl_registry#2.global(19, "wp_color_manager_v1", 1)',
            VALID_TRACE.replace(
                'wl_registry#2.global(19, "wp_color_manager_v1", 1)',
                'wl_registry#9.global(19, "wp_color_manager_v1", 1)',
            ),
        ):
            with self.subTest(trace=trace[:35]):
                with self.assertRaises(TraceValidationError):
                    validate_trace(trace)

    def test_bind_version_must_not_exceed_advertised_version(self):
        trace = VALID_TRACE.replace(
            'wl_registry#2.bind(19, "wp_color_manager_v1", 1,',
            'wl_registry#2.bind(19, "wp_color_manager_v1", 2,',
        )
        with self.assertRaises(TraceValidationError):
            validate_trace(trace)

    def test_unknown_new_id_surface_creation_is_accepted_once(self):
        trace = VALID_TRACE.replace(
            'new id wp_color_management_surface_v1#23', 'new id [unknown]#23'
        )
        self.assertEqual(validate_trace(trace)["status"], "PASS")
        with self.assertRaises(TraceValidationError):
            validate_trace(trace.replace(
                'wp_image_description_v1#31.ready(123)',
                'wp_color_manager_v1#3.get_surface('
                'new id wp_color_management_surface_v1#23, wl_surface#8)\n'
                'wp_image_description_v1#31.ready(123)',
            ))

    def test_implicit_prior_description_or_surface_lifetime_is_rejected(self):
        traces = (
            VALID_TRACE.replace(
                'wp_image_description_v1#31.ready(123)',
                'wp_image_description_v1#31.ready(123)\n'
                'wp_image_description_creator_params_v1#50.create('
                'new id wp_image_description_v1#31)',
            ),
            VALID_TRACE.replace(
                'wp_image_description_v1#31.ready(123)',
                'wl_compositor#4.create_surface(new id wl_surface#8)\n'
                'wp_image_description_v1#31.ready(123)',
            ),
        )
        for trace in traces:
            with self.subTest(trace=trace[-130:]):
                with self.assertRaises(TraceValidationError):
                    validate_trace(trace)

    def test_calls_before_captured_chain_object_creation_are_ambiguous(self):
        traces = (
            VALID_TRACE.replace(
                'wl_registry#2.bind(19, "wp_color_manager_v1", 1,',
                'wp_color_manager_v1#11.done()\n'
                'wl_registry#2.bind(19, "wp_color_manager_v1", 1,',
            ),
            VALID_TRACE.replace(
                'wp_color_manager_v1#11.get_surface(',
                'wp_color_management_surface_v1#23.destroy()\n'
                'wp_color_manager_v1#11.get_surface(',
            ),
            VALID_TRACE.replace(
                'wp_color_manager_v1#11.get_surface(',
                'wl_surface#8.commit()\n'
                'wl_compositor#4.create_surface(new id wl_surface#8)\n'
                'wp_color_manager_v1#11.get_surface(',
            ),
            VALID_TRACE.replace(
                'wp_image_description_v1#31.ready(123)',
                'wp_image_description_v1#31.destroy()\n'
                'wp_image_description_creator_params_v1#50.create('
                'new id wp_image_description_v1#31)\n'
                'wp_image_description_v1#31.ready(123)',
            ),
        )
        for trace in traces:
            with self.subTest(trace=trace[-130:]):
                with self.assertRaises(TraceValidationError):
                    validate_trace(trace)

    def test_reused_registry_or_chain_object_id_is_ambiguous(self):
        traces = (
            VALID_TRACE.replace(
                'wl_registry#2.global(19, "wp_color_manager_v1", 1)',
                'wl_display#1.get_registry(new id wl_registry#2)\n'
                'wl_registry#2.global(19, "wp_color_manager_v1", 1)',
            ).replace(
                'wl_registry#2.bind(19, "wp_color_manager_v1", 1,',
                'wl_display#1.get_registry(new id wl_registry#2)\n'
                'wl_registry#2.bind(19, "wp_color_manager_v1", 1,',
            ),
            VALID_TRACE.replace(
                'wl_registry#2.bind(19, "wp_color_manager_v1", 1,',
                'wl_display#1.get_registry(new id wl_registry#2)\n'
                'wl_registry#2.global(19, "wp_color_manager_v1", 1)\n'
                'wl_registry#2.bind(19, "wp_color_manager_v1", 1,',
            ),
            VALID_TRACE.replace(
                'wp_color_manager_v1#11.supported_intent(0)',
                'wl_registry#9.bind(19, "wp_color_manager_v1", 1, new id [unknown]#11)\n'
                'wp_color_manager_v1#11.supported_intent(0)',
            ),
            VALID_TRACE.replace(
                'wp_image_description_v1#31.ready(123)',
                'wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)\n'
                'wp_image_description_creator_params_v1#51.create(new id wp_image_description_v1#31)\n'
                'wp_image_description_v1#31.ready(123)',
            ),
            VALID_TRACE.replace(
                'wp_color_manager_v1#11.get_surface(',
                'wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#8)\n'
                'wp_color_manager_v1#11.get_surface(',
            ),
            VALID_TRACE.replace(
                'wp_image_description_v1#31.ready(123)',
                'wl_compositor#4.create_surface(new id wl_surface#8)\n'
                'wl_compositor#5.create_surface(new id wl_surface#8)\n'
                'wp_image_description_v1#31.ready(123)',
            ),
        )
        for trace in traces:
            with self.subTest(trace=trace[-130:]):
                with self.assertRaises(TraceValidationError):
                    validate_trace(trace)

    def test_valid_trace_selects_a_complete_manager_not_private_probe_traffic(self):
        evidence = validate_trace(VALID_TRACE)
        self.assertEqual(evidence["schema"], TRACE_SCHEMA)
        self.assertEqual(evidence["revision"], TRACE_REVISION)
        self.assertEqual(evidence["status"], "PASS")
        self.assertEqual(evidence["matched"]["manager"]["id"], 11)
        self.assertEqual(evidence["matched"]["surface"]["wl_surface_id"], 8)
        self.assertEqual(evidence["matched"]["image_description"]["id"], 31)
        self.assertLess(
            evidence["matched"]["image_description"]["ready_index"],
            evidence["matched"]["surface"]["set_image_description_index"],
        )
        self.assertLess(
            evidence["matched"]["surface"]["set_image_description_index"],
            evidence["matched"]["surface"]["commit_index"],
        )
        self.assertEqual(
            evidence["matched"]["image_description"]["readiness"]["event"],
            "ready",
        )
        self.assertEqual(
            evidence["matched"]["image_description"]["readiness"]["identity"],
            123,
        )

    def test_ready2_is_accepted_and_records_the_64_bit_identity(self):
        evidence = validate_trace(
            VALID_TRACE.replace(
                "wp_image_description_v1#31.ready(123)",
                "wp_image_description_v1#31.ready2(0x00000001, 42)",
            )
        )
        readiness = evidence["matched"]["image_description"]["readiness"]
        self.assertEqual(readiness["event"], "ready2")
        self.assertEqual(readiness["identity_hi"], 1)
        self.assertEqual(readiness["identity_lo"], 42)
        self.assertEqual(readiness["identity_64"], (1 << 32) | 42)

    def test_malformed_or_out_of_range_readiness_is_rejected(self):
        for replacement in (
            "wp_image_description_v1#31.ready()",
            "wp_image_description_v1#31.ready(4294967296)",
            "wp_image_description_v1#31.ready2(0, 0)",
            "wp_image_description_v1#31.ready2(4294967296, 1)",
        ):
            with self.subTest(replacement=replacement):
                with self.assertRaises(TraceValidationError):
                    validate_trace(
                        VALID_TRACE.replace(
                            "wp_image_description_v1#31.ready(123)", replacement
                        )
                    )

    def test_missing_required_evidence_is_rejected(self):
        mutations = {
            "global": ('wl_registry#2.global(19, "wp_color_manager_v1", 1)\n', ""),
            "bind": ('-> wl_registry#2.bind(19, "wp_color_manager_v1", 1, new id [unknown]#11)\n', ""),
            "intent": ("wp_color_manager_v1#11.supported_intent(0)", "wp_color_manager_v1#11.supported_intent(1)"),
            "done": ("wp_color_manager_v1#11.done()", ""),
            "get_surface": ("-> wp_color_manager_v1#11.get_surface(new id wp_color_management_surface_v1#23, wl_surface#8)", ""),
            "ready": ("wp_image_description_v1#31.ready(123)", ""),
            "set": ("-> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)", ""),
            "commit": ("-> wl_surface#8.commit()", ""),
        }
        for name, (before, after) in mutations.items():
            with self.subTest(name=name):
                with self.assertRaises(TraceValidationError):
                    validate_trace(VALID_TRACE.replace(before, after))

    def test_ready_after_set_and_wrong_surface_commit_are_rejected(self):
        ready_after_set = VALID_TRACE.replace(
            "wp_image_description_v1#31.ready(123)\n[ 1042.011]  -> wp_color_management_surface_v1#23.set_image_description",
            "-> wp_color_management_surface_v1#23.set_image_description",
        ).replace(
            "wp_image_description_v1#31, 0)\n[ 1042.012]",
            "wp_image_description_v1#31, 0)\n[ 1042.012]  wp_image_description_v1#31.ready(123)\n[ 1042.013]",
        )
        with self.assertRaises(TraceValidationError):
            validate_trace(ready_after_set)
        with self.assertRaises(TraceValidationError):
            validate_trace(VALID_TRACE.replace("wl_surface#8.commit()", "wl_surface#99.commit()"))

    def test_failed_description_display_error_and_protocol_disconnect_are_rejected(self):
        for rejected_line in (
            "wp_image_description_v1#31.failed(2, \"no description\")",
            "wl_display#1.error(wl_surface#8, 1, \"invalid object\")",
            "fatal protocol error: disconnected from display",
        ):
            with self.subTest(rejected_line=rejected_line):
                with self.assertRaisesRegex(TraceValidationError, "fatal/protocol"):
                    validate_trace(VALID_TRACE + "\n" + rejected_line)

    def test_malformed_color_management_call_is_strictly_insufficient(self):
        malformed = VALID_TRACE.replace(
            "new id wp_color_management_surface_v1#23, wl_surface#8",
            "wl_surface#8",
        )
        with self.assertRaises(TraceValidationError):
            validate_trace(malformed)

    def test_json_evidence_hashes_inputs_without_copying_raw_trace_or_absolute_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            trace_path = root / "legacy.trace.log"
            report_path = root / "gate2-wayland-publication-legacy.txt"
            output_path = root / "legacy.protocol.json"
            trace_path.write_text(VALID_TRACE, encoding="utf-8")
            report_path.write_text(
                "Overall result: PASS\nExpected backend: legacy\n",
                encoding="utf-8",
            )
            self.assertEqual(
                main([
                    "--trace", str(trace_path),
                    "--publication-report", str(report_path),
                    "--backend", "legacy",
                    "--output", str(output_path),
                ]),
                0,
            )
            evidence = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(evidence["publication_report"]["path"], report_path.name)
        self.assertEqual(evidence["trace"]["path"], trace_path.name)
        self.assertEqual(evidence["backend"], "legacy")
        self.assertNotIn(str(report_path.parent), json.dumps(evidence))
        self.assertNotIn("wp_color_manager_v1#11.done()", json.dumps(evidence))
        self.assertEqual(evidence["status"], "PASS")

    def test_cli_writes_hash_linked_fail_json_for_readable_rejected_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            trace_path = root / "legacy.trace.log"
            report_path = root / "gate2-wayland-publication-legacy.txt"
            output_path = root / "legacy.protocol.json"
            rejected_trace = VALID_TRACE + "\nwl_display#1.error(wl_surface#8, 1, \"invalid\")"
            trace_path.write_text(rejected_trace, encoding="utf-8")
            report_path.write_text(
                "Overall result: PASS\nExpected backend: legacy\n",
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                status = main([
                    "--trace", str(trace_path),
                    "--publication-report", str(report_path),
                    "--backend", "legacy",
                    "--output", str(output_path),
                ])
            failure = json.loads(output_path.read_text(encoding="utf-8"))
            encoded = json.dumps(failure)
            self.assertEqual(status, 1)
            self.assertIn("Wayland color-management trace rejected", stderr.getvalue())
            self.assertEqual(failure["schema"], TRACE_SCHEMA)
            self.assertEqual(failure["revision"], TRACE_REVISION)
            self.assertEqual(failure["status"], "FAIL")
            self.assertEqual(failure["backend"], "legacy")
            self.assertIn("not acceptance evidence", failure["scope"])
            self.assertEqual(
                failure["error"],
                "Wayland color-management trace evidence was rejected",
            )
            self.assertEqual(failure["trace"]["path"], trace_path.name)
            self.assertEqual(failure["publication_report"]["path"], report_path.name)
            self.assertEqual(
                failure["trace"]["sha256"],
                hashlib.sha256(rejected_trace.encode()).hexdigest(),
            )
            self.assertEqual(
                failure["publication_report"]["sha256"],
                hashlib.sha256(report_path.read_bytes()).hexdigest(),
            )
            self.assertNotIn("matched", failure)
            self.assertNotIn(str(root), encoded)
            self.assertNotIn("wp_color_manager_v1#11.done()", encoded)

    def test_nonpassing_publication_prerequisite_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            trace_path = root / "trace.log"
            report_path = root / "publication.txt"
            trace_path.write_text(VALID_TRACE, encoding="utf-8")
            report_path.write_text(
                "Overall result: FAIL\nExpected backend: legacy\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(TraceValidationError, "prerequisite"):
                create_evidence_report(trace_path, report_path, "legacy")

    def test_publication_prerequisite_backend_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            trace_path = root / "trace.log"
            report_path = root / "publication.txt"
            trace_path.write_text(VALID_TRACE, encoding="utf-8")
            report_path.write_text(
                "Overall result: PASS\nExpected backend: legacy\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(TraceValidationError, "backend"):
                create_evidence_report(trace_path, report_path, "gpu-next")


class TestGtkCmPolicy(unittest.TestCase):
    def test_gtk_cm_opted_in(self):
        from src.gtk_cm_policy import gtk_cm_opted_in
        self.assertFalse(gtk_cm_opted_in({}))
        self.assertFalse(gtk_cm_opted_in({"GDK_DEBUG": "opengl"}))
        self.assertTrue(gtk_cm_opted_in({"GDK_DEBUG": "color-mgmt"}))
        self.assertTrue(gtk_cm_opted_in({"GDK_DEBUG": "opengl:color-mgmt:portals"}))
        self.assertTrue(gtk_cm_opted_in({"GDK_DEBUG": "all"}))

    def test_gtk_color_managed_requires_opt_in(self):
        from src.gtk_cm_policy import (
            CmCaps, gtk_color_managed, INTENT_PERCEPTUAL, FEAT_PARAMETRIC,
            PRIM_SRGB, PRIM_BT2020, TF_SRGB, TF_PQ
        )
        caps = CmCaps(
            intents=frozenset([INTENT_PERCEPTUAL]),
            features=frozenset([FEAT_PARAMETRIC]),
            tfs=frozenset([TF_SRGB, TF_PQ]),
            primaries=frozenset([PRIM_SRGB, PRIM_BT2020]),
        )
        ok, reason = gtk_color_managed(caps, env={})
        self.assertFalse(ok)
        self.assertIn("GDK_DEBUG=color-mgmt", reason)

    def test_gtk_color_managed_requires_caps(self):
        from src.gtk_cm_policy import gtk_color_managed
        ok, reason = gtk_color_managed(None, env={"GDK_DEBUG": "color-mgmt"})
        self.assertFalse(ok)
        self.assertEqual(reason, "color management capabilities unavailable or unknown")

    def test_gtk_color_managed_kwin_missing_srgb(self):
        from src.gtk_cm_policy import (
            CmCaps, gtk_color_managed, INTENT_PERCEPTUAL, FEAT_PARAMETRIC,
            FEAT_SET_PRIMARIES, PRIM_SRGB, PRIM_BT2020, TF_PQ, TF_COMPOUND_POWER_2_4
        )
        # KWin advertises PQ and compound_power_2_4 but omits TF_SRGB (9)
        kwin_caps = CmCaps(
            intents=frozenset([INTENT_PERCEPTUAL]),
            features=frozenset([FEAT_PARAMETRIC, FEAT_SET_PRIMARIES]),
            tfs=frozenset([TF_PQ, TF_COMPOUND_POWER_2_4]),
            primaries=frozenset([PRIM_SRGB, PRIM_BT2020]),
        )
        ok, reason = gtk_color_managed(kwin_caps, env={"GDK_DEBUG": "color-mgmt"})
        self.assertFalse(ok)
        self.assertIn("TF srgb", reason)

    def test_gtk_color_managed_mutter_success(self):
        from src.gtk_cm_policy import (
            CmCaps, gtk_color_managed, gtk_can_tag_hdr, INTENT_PERCEPTUAL,
            FEAT_PARAMETRIC, FEAT_SET_PRIMARIES, PRIM_SRGB, PRIM_BT2020,
            TF_SRGB, TF_PQ, TF_EXT_LINEAR
        )
        mutter_caps = CmCaps(
            intents=frozenset([INTENT_PERCEPTUAL]),
            features=frozenset([FEAT_PARAMETRIC, FEAT_SET_PRIMARIES]),
            tfs=frozenset([TF_SRGB, TF_PQ, TF_EXT_LINEAR]),
            primaries=frozenset([PRIM_SRGB, PRIM_BT2020]),
        )
        ok, reason = gtk_color_managed(mutter_caps, env={"GDK_DEBUG": "color-mgmt"})
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")
        self.assertTrue(gtk_can_tag_hdr(mutter_caps, TF_PQ))
        self.assertTrue(gtk_can_tag_hdr(mutter_caps, TF_EXT_LINEAR))

    def test_gtk_can_tag_hdr_without_bt2020(self):
        from src.gtk_cm_policy import (
            CmCaps, gtk_can_tag_hdr, INTENT_PERCEPTUAL, FEAT_PARAMETRIC,
            PRIM_SRGB, TF_SRGB, TF_PQ
        )
        sdr_caps = CmCaps(
            intents=frozenset([INTENT_PERCEPTUAL]),
            features=frozenset([FEAT_PARAMETRIC]),
            tfs=frozenset([TF_SRGB]),
            primaries=frozenset([PRIM_SRGB]),
        )
        self.assertFalse(gtk_can_tag_hdr(sdr_caps, TF_PQ))

    def _check_after_realize_invalidate(self, caps, env):
        """Run check_hdr_support() the way MpvVideoWidget does on realize.

        get_cm_caps() is NOT mocked: after invalidate_hdr_support_cache() the
        caps cache is empty and caps can only arrive through probe_outputs(),
        which is exactly the path ee2d333's predecessor skipped.  Gdk is
        mocked wholesale so the GTK version of the test host cannot short-cut
        detection before the color-management gate.
        """
        import os
        from unittest.mock import MagicMock, patch
        from src import hdr_detection, wayland_cm_probe, wayland_output_hdr

        def fake_probe_outputs():
            wayland_output_hdr._cache_cm_caps = caps  # what the real probe stores
            return {}

        display = MagicMock()
        display.__class__.__name__ = "GdkWaylandDisplay"
        display.get_dmabuf_formats.return_value.get_n_formats.return_value = 10

        self.addCleanup(hdr_detection.invalidate_hdr_support_cache)
        with patch.dict(os.environ, env, clear=True), \
             patch.object(hdr_detection, "Gdk") as gdk, \
             patch.object(wayland_cm_probe, "probe_color_management", return_value=True), \
             patch.object(wayland_output_hdr, "probe_outputs", side_effect=fake_probe_outputs) as probe:
            gdk.Display.get_default.return_value = display
            hdr_detection.invalidate_hdr_support_cache()  # realize
            self.assertIsNone(wayland_output_hdr._cache_cm_caps)
            return hdr_detection.check_hdr_support(), probe

    def _caps(self, tfs):
        from src.gtk_cm_policy import (
            CmCaps, INTENT_PERCEPTUAL, FEAT_PARAMETRIC, FEAT_SET_PRIMARIES,
            PRIM_SRGB, PRIM_BT2020,
        )
        return CmCaps(
            intents=frozenset([INTENT_PERCEPTUAL]),
            features=frozenset([FEAT_PARAMETRIC, FEAT_SET_PRIMARIES]),
            tfs=frozenset(tfs),
            primaries=frozenset([PRIM_SRGB, PRIM_BT2020]),
        )

    def test_realize_control_mutter_caps_allow_hdr(self):
        """Positive control: proves the harness reaches the CM gate, so the
        KWin assertions below cannot pass through an early return/exception."""
        from src.gtk_cm_policy import TF_SRGB, TF_PQ
        supported, probe = self._check_after_realize_invalidate(
            self._caps([TF_SRGB, TF_PQ]), {"GDK_DEBUG": "color-mgmt"}
        )
        self.assertTrue(supported)
        probe.assert_called_once()

    def test_regression_kwin_caps_after_invalidate_blocks_hdr(self):
        """KWin 6.7.4 omits TF srgb, so GTK refuses color management.
        Before ee2d333 the gate was skipped when caps were dropped on realize
        and HDR was reported as supported (washed-out PQ)."""
        from src.gtk_cm_policy import TF_PQ, TF_COMPOUND_POWER_2_4
        supported, probe = self._check_after_realize_invalidate(
            self._caps([TF_PQ, TF_COMPOUND_POWER_2_4]), {"GDK_DEBUG": "color-mgmt"}
        )
        self.assertFalse(supported)
        probe.assert_called_once()

    def test_multiphase_trace_four_phases_on_subsurface_with_buffers_passes(self):
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wl_compositor#1.create_surface(new id wl_surface#71)
[ 1042.010]  -> wl_subcompositor#6.get_subsurface(new id wl_subsurface#72, wl_surface#71, wl_surface#61)
[ 1042.011]  -> wp_viewporter#12.get_viewport(new id wp_viewport#75, wl_surface#71)
[ 1042.012]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#71)
[ 1042.013]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.014]  -> wp_image_description_creator_params_v1#50.set_primaries_named(1)
[ 1042.015]  -> wp_image_description_creator_params_v1#50.set_tf_named(9)
[ 1042.016]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.017]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#52)
[ 1042.018]  -> wp_image_description_creator_params_v1#52.set_primaries_named(6)
[ 1042.019]  -> wp_image_description_creator_params_v1#52.set_tf_named(11)
[ 1042.020]  -> wp_image_description_creator_params_v1#52.create(new id wp_image_description_v1#32)
[ 1042.021]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#54)
[ 1042.022]  -> wp_image_description_creator_params_v1#54.set_primaries_named(1)
[ 1042.023]  -> wp_image_description_creator_params_v1#54.set_tf_named(9)
[ 1042.024]  -> wp_image_description_creator_params_v1#54.create(new id wp_image_description_v1#33)
[ 1042.025]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#56)
[ 1042.026]  -> wp_image_description_creator_params_v1#56.set_primaries_named(6)
[ 1042.027]  -> wp_image_description_creator_params_v1#56.set_tf_named(11)
[ 1042.028]  -> wp_image_description_creator_params_v1#56.create(new id wp_image_description_v1#34)
[ 1042.029]  wp_image_description_v1#31.ready(101)
[ 1042.030]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.031]  -> wl_surface#71.attach(wl_buffer#81, 0, 0)
[ 1042.032]  -> wl_surface#71.commit()
[ 1042.033]  wp_image_description_v1#32.ready(102)
[ 1042.034]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#32, 0)
[ 1042.035]  -> wl_surface#71.attach(wl_buffer#82, 0, 0)
[ 1042.036]  -> wl_surface#71.commit()
[ 1042.037]  wp_image_description_v1#33.ready(103)
[ 1042.038]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#33, 0)
[ 1042.039]  -> wl_surface#71.attach(wl_buffer#83, 0, 0)
[ 1042.040]  -> wl_surface#71.commit()
[ 1042.041]  wp_image_description_v1#34.ready(104)
[ 1042.042]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#34, 0)
[ 1042.043]  -> wl_surface#71.attach(wl_buffer#84, 0, 0)
[ 1042.044]  -> wl_surface#71.commit()
        """.strip()
        evidence = validate_multiphase_trace(trace)
        self.assertEqual(evidence["status"], "PASS")
        self.assertEqual(evidence["revision"], MULTIPHASE_REVISION)
        self.assertEqual(evidence["scope"], MULTIPHASE_SCOPE)
        self.assertTrue(evidence["matched"]["surface"]["subsurface_video_chain_proven"])
        self.assertEqual(evidence["matched"]["surface"]["role"], "video_subsurface")
        self.assertEqual(evidence["matched"]["surface"]["app_id"], "io.github.rusmikev.CineHDR")
        self.assertTrue(evidence["matched"]["color_alternation_proven"])
        self.assertEqual(len(evidence["matched"]["phases"]), 4)

    def test_multiphase_trace_four_phases_on_toplevel_without_subsurface_chain_returns_unknown(self):
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.set_primaries_named(1)
[ 1042.012]  -> wp_image_description_creator_params_v1#50.set_tf_named(9)
[ 1042.013]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.014]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#52)
[ 1042.015]  -> wp_image_description_creator_params_v1#52.set_primaries_named(6)
[ 1042.016]  -> wp_image_description_creator_params_v1#52.set_tf_named(11)
[ 1042.017]  -> wp_image_description_creator_params_v1#52.create(new id wp_image_description_v1#32)
[ 1042.018]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#54)
[ 1042.019]  -> wp_image_description_creator_params_v1#54.set_primaries_named(1)
[ 1042.020]  -> wp_image_description_creator_params_v1#54.set_tf_named(9)
[ 1042.021]  -> wp_image_description_creator_params_v1#54.create(new id wp_image_description_v1#33)
[ 1042.022]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#56)
[ 1042.023]  -> wp_image_description_creator_params_v1#56.set_primaries_named(6)
[ 1042.024]  -> wp_image_description_creator_params_v1#56.set_tf_named(11)
[ 1042.025]  -> wp_image_description_creator_params_v1#56.create(new id wp_image_description_v1#34)
[ 1042.026]  wp_image_description_v1#31.ready(101)
[ 1042.027]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.028]  -> wl_surface#61.commit()
[ 1042.029]  wp_image_description_v1#32.ready(102)
[ 1042.030]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#32, 0)
[ 1042.031]  -> wl_surface#61.commit()
[ 1042.032]  wp_image_description_v1#33.ready(103)
[ 1042.033]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#33, 0)
[ 1042.034]  -> wl_surface#61.commit()
[ 1042.035]  wp_image_description_v1#34.ready(104)
[ 1042.036]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#34, 0)
[ 1042.037]  -> wl_surface#61.commit()
        """.strip()
        evidence = validate_multiphase_trace(trace)
        self.assertEqual(evidence["status"], "UNKNOWN")
        self.assertFalse(evidence["matched"]["surface"]["subsurface_video_chain_proven"])
        self.assertEqual(evidence["matched"]["surface"]["role"], "toplevel_window")
        self.assertEqual(evidence["matched"]["surface"]["app_id"], "io.github.rusmikev.CineHDR")
        self.assertIn("target video surface cannot be proven", evidence["reason"])
        self.assertEqual(len(evidence["matched"]["phases"]), 4)

    def test_multiphase_trace_foreign_app_id_returns_unknown(self):
        """Negative regression: foreign app_id cannot be attributed to CineHDR."""
        base_trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("org.example.Other")
[ 1042.009]  -> wl_compositor#1.create_surface(new id wl_surface#71)
[ 1042.010]  -> wl_subcompositor#6.get_subsurface(new id wl_subsurface#72, wl_surface#71, wl_surface#61)
[ 1042.011]  -> wp_viewporter#12.get_viewport(new id wp_viewport#75, wl_surface#71)
[ 1042.012]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#71)
[ 1042.013]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.014]  -> wp_image_description_creator_params_v1#50.set_primaries_named(1)
[ 1042.015]  -> wp_image_description_creator_params_v1#50.set_tf_named(9)
[ 1042.016]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.017]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#52)
[ 1042.018]  -> wp_image_description_creator_params_v1#52.set_primaries_named(6)
[ 1042.019]  -> wp_image_description_creator_params_v1#52.set_tf_named(11)
[ 1042.020]  -> wp_image_description_creator_params_v1#52.create(new id wp_image_description_v1#32)
[ 1042.021]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#54)
[ 1042.022]  -> wp_image_description_creator_params_v1#54.set_primaries_named(1)
[ 1042.023]  -> wp_image_description_creator_params_v1#54.set_tf_named(9)
[ 1042.024]  -> wp_image_description_creator_params_v1#54.create(new id wp_image_description_v1#33)
[ 1042.025]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#56)
[ 1042.026]  -> wp_image_description_creator_params_v1#56.set_primaries_named(6)
[ 1042.027]  -> wp_image_description_creator_params_v1#56.set_tf_named(11)
[ 1042.028]  -> wp_image_description_creator_params_v1#56.create(new id wp_image_description_v1#34)
[ 1042.029]  wp_image_description_v1#31.ready(101)
[ 1042.030]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.031]  -> wl_surface#71.attach(wl_buffer#81, 0, 0)
[ 1042.032]  -> wl_surface#71.commit()
[ 1042.033]  wp_image_description_v1#32.ready(102)
[ 1042.034]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#32, 0)
[ 1042.035]  -> wl_surface#71.attach(wl_buffer#82, 0, 0)
[ 1042.036]  -> wl_surface#71.commit()
[ 1042.037]  wp_image_description_v1#33.ready(103)
[ 1042.038]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#33, 0)
[ 1042.039]  -> wl_surface#71.attach(wl_buffer#83, 0, 0)
[ 1042.040]  -> wl_surface#71.commit()
[ 1042.041]  wp_image_description_v1#34.ready(104)
[ 1042.042]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#34, 0)
[ 1042.043]  -> wl_surface#71.attach(wl_buffer#84, 0, 0)
[ 1042.044]  -> wl_surface#71.commit()
        """.strip()
        evidence = validate_multiphase_trace(base_trace)
        self.assertEqual(evidence["status"], "UNKNOWN")
        self.assertFalse(evidence["matched"]["surface"]["subsurface_video_chain_proven"])
        self.assertEqual(evidence["matched"]["surface"]["app_id"], "org.example.Other")
        self.assertIn("target surface cannot be attributed to CineHDR", evidence["reason"])

    def test_multiphase_trace_identical_descriptions_returns_unknown(self):
        """Negative regression: identical descriptions do not prove color alternation."""
        base_trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wl_compositor#1.create_surface(new id wl_surface#71)
[ 1042.010]  -> wl_subcompositor#6.get_subsurface(new id wl_subsurface#72, wl_surface#71, wl_surface#61)
[ 1042.011]  -> wp_viewporter#12.get_viewport(new id wp_viewport#75, wl_surface#71)
[ 1042.012]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#71)
[ 1042.013]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.014]  -> wp_image_description_creator_params_v1#50.set_primaries_named(1)
[ 1042.015]  -> wp_image_description_creator_params_v1#50.set_tf_named(9)
[ 1042.016]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.029]  wp_image_description_v1#31.ready(101)
[ 1042.030]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.031]  -> wl_surface#71.attach(wl_buffer#81, 0, 0)
[ 1042.032]  -> wl_surface#71.commit()
[ 1042.033]  wp_image_description_v1#31.ready(102)
[ 1042.034]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.035]  -> wl_surface#71.attach(wl_buffer#82, 0, 0)
[ 1042.036]  -> wl_surface#71.commit()
[ 1042.037]  wp_image_description_v1#31.ready(103)
[ 1042.038]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.039]  -> wl_surface#71.attach(wl_buffer#83, 0, 0)
[ 1042.040]  -> wl_surface#71.commit()
[ 1042.041]  wp_image_description_v1#31.ready(104)
[ 1042.042]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.043]  -> wl_surface#71.attach(wl_buffer#84, 0, 0)
[ 1042.044]  -> wl_surface#71.commit()
        """.strip()
        evidence = validate_multiphase_trace(base_trace)
        self.assertEqual(evidence["status"], "UNKNOWN")
        self.assertFalse(evidence["matched"]["color_alternation_proven"])
        self.assertIn("identical image description", evidence["reason"])

    def test_multiphase_trace_unrevealed_color_params_returns_unknown(self):
        """Negative regression: descriptors without parameter revelation cannot prove color alternation."""
        base_trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wl_compositor#1.create_surface(new id wl_surface#71)
[ 1042.010]  -> wl_subcompositor#6.get_subsurface(new id wl_subsurface#72, wl_surface#71, wl_surface#61)
[ 1042.011]  -> wp_viewporter#12.get_viewport(new id wp_viewport#75, wl_surface#71)
[ 1042.012]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#71)
[ 1042.029]  wp_image_description_v1#31.ready(101)
[ 1042.030]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.031]  -> wl_surface#71.attach(wl_buffer#81, 0, 0)
[ 1042.032]  -> wl_surface#71.commit()
[ 1042.033]  wp_image_description_v1#32.ready(102)
[ 1042.034]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#32, 0)
[ 1042.035]  -> wl_surface#71.attach(wl_buffer#82, 0, 0)
[ 1042.036]  -> wl_surface#71.commit()
[ 1042.037]  wp_image_description_v1#33.ready(103)
[ 1042.038]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#33, 0)
[ 1042.039]  -> wl_surface#71.attach(wl_buffer#83, 0, 0)
[ 1042.040]  -> wl_surface#71.commit()
[ 1042.041]  wp_image_description_v1#34.ready(104)
[ 1042.042]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#34, 0)
[ 1042.043]  -> wl_surface#71.attach(wl_buffer#84, 0, 0)
[ 1042.044]  -> wl_surface#71.commit()
        """.strip()
        evidence = validate_multiphase_trace(base_trace)
        self.assertEqual(evidence["status"], "UNKNOWN")
        self.assertFalse(evidence["matched"]["color_alternation_proven"])
        self.assertIn("color alternation", evidence["reason"])

    def test_multiphase_trace_subsurface_without_video_viewport_returns_unknown(self):
        """Negative regression: subsurface with buffers but without wp_viewport cannot prove video surface role."""
        base_trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wl_compositor#1.create_surface(new id wl_surface#71)
[ 1042.010]  -> wl_subcompositor#6.get_subsurface(new id wl_subsurface#72, wl_surface#71, wl_surface#61)
[ 1042.012]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#71)
[ 1042.013]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.014]  -> wp_image_description_creator_params_v1#50.set_primaries_named(1)
[ 1042.015]  -> wp_image_description_creator_params_v1#50.set_tf_named(9)
[ 1042.016]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.017]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#52)
[ 1042.018]  -> wp_image_description_creator_params_v1#52.set_primaries_named(6)
[ 1042.019]  -> wp_image_description_creator_params_v1#52.set_tf_named(11)
[ 1042.020]  -> wp_image_description_creator_params_v1#52.create(new id wp_image_description_v1#32)
[ 1042.021]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#54)
[ 1042.022]  -> wp_image_description_creator_params_v1#54.set_primaries_named(1)
[ 1042.023]  -> wp_image_description_creator_params_v1#54.set_tf_named(9)
[ 1042.024]  -> wp_image_description_creator_params_v1#54.create(new id wp_image_description_v1#33)
[ 1042.025]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#56)
[ 1042.026]  -> wp_image_description_creator_params_v1#56.set_primaries_named(6)
[ 1042.027]  -> wp_image_description_creator_params_v1#56.set_tf_named(11)
[ 1042.028]  -> wp_image_description_creator_params_v1#56.create(new id wp_image_description_v1#34)
[ 1042.029]  wp_image_description_v1#31.ready(101)
[ 1042.030]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.031]  -> wl_surface#71.attach(wl_buffer#81, 0, 0)
[ 1042.032]  -> wl_surface#71.commit()
[ 1042.033]  wp_image_description_v1#32.ready(102)
[ 1042.034]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#32, 0)
[ 1042.035]  -> wl_surface#71.attach(wl_buffer#82, 0, 0)
[ 1042.036]  -> wl_surface#71.commit()
[ 1042.037]  wp_image_description_v1#33.ready(103)
[ 1042.038]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#33, 0)
[ 1042.039]  -> wl_surface#71.attach(wl_buffer#83, 0, 0)
[ 1042.040]  -> wl_surface#71.commit()
[ 1042.041]  wp_image_description_v1#34.ready(104)
[ 1042.042]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#34, 0)
[ 1042.043]  -> wl_surface#71.attach(wl_buffer#84, 0, 0)
[ 1042.044]  -> wl_surface#71.commit()
        """.strip()
        evidence = validate_multiphase_trace(base_trace)
        self.assertEqual(evidence["status"], "UNKNOWN")
        self.assertFalse(evidence["matched"]["surface"]["subsurface_video_chain_proven"])
        self.assertEqual(evidence["matched"]["surface"]["role"], "subsurface")
        self.assertIn("wp_viewport", evidence["reason"])

    def test_multiphase_trace_rejects_fewer_than_four_phases(self):
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#8)
[ 1042.006]  wp_image_description_v1#31.ready(101)
[ 1042.007]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.008]  -> wl_surface#8.commit()
[ 1042.009]  wp_image_description_v1#32.ready(102)
[ 1042.010]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#32, 0)
[ 1042.011]  -> wl_surface#8.commit()
        """.strip()
        with self.assertRaises(TraceValidationError) as cm:
            validate_multiphase_trace(trace)
        self.assertIn("complete 4-phase sequence", str(cm.exception))

    def test_multiphase_trace_rejects_count_based_heuristic_without_commits(self):
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#8)
[ 1042.006]  wp_image_description_v1#31.ready(101)
[ 1042.007]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.008]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.009]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.010]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.011]  -> wl_surface#8.commit()
        """.strip()
        with self.assertRaises(TraceValidationError):
            validate_multiphase_trace(trace)

    def test_extract_image_descriptions_distinguishes_pq_and_hlg(self):
        from tools.validation.parse_wayland_color_trace import _calls, _extract_image_descriptions_info
        trace = """
[ 1042.001]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.002]  -> wp_image_description_creator_params_v1#50.set_primaries_named(6)
[ 1042.003]  -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.004]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.005]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#52)
[ 1042.006]  -> wp_image_description_creator_params_v1#52.set_primaries_named(6)
[ 1042.007]  -> wp_image_description_creator_params_v1#52.set_tf_named(13)
[ 1042.008]  -> wp_image_description_creator_params_v1#52.create(new id wp_image_description_v1#32)
[ 1042.009]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#54)
[ 1042.010]  -> wp_image_description_creator_params_v1#54.set_primaries_named(1)
[ 1042.011]  -> wp_image_description_creator_params_v1#54.set_tf_named(9)
[ 1042.012]  -> wp_image_description_creator_params_v1#54.create(new id wp_image_description_v1#33)
        """.strip()
        calls = _calls(trace)
        descs = _extract_image_descriptions_info(calls)
        self.assertEqual(descs[31]["color_type"], "rec2100_pq")
        self.assertEqual(descs[31]["tf_named"], 11)
        self.assertEqual(descs[31]["semantic_class"], "hdr")
        self.assertEqual(descs[32]["color_type"], "rec2100_hlg")
        self.assertEqual(descs[32]["tf_named"], 13)
        self.assertEqual(descs[32]["semantic_class"], "hdr")
        self.assertNotEqual(descs[31]["color_type"], descs[32]["color_type"])
        self.assertEqual(descs[33]["color_type"], "srgb_bt709")
        self.assertEqual(descs[33]["semantic_class"], "sdr")

    def test_extract_image_descriptions_pq_hlg_with_other_primaries_semantic_class_hdr(self):
        """PQ and HLG with other primaries classify as semantic_class='hdr' without false Rec.2100 name."""
        from tools.validation.parse_wayland_color_trace import _calls, _extract_image_descriptions_info
        trace = """
[ 1042.001]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.002]  -> wp_image_description_creator_params_v1#50.set_primaries_named(1)
[ 1042.003]  -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.004]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.005]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#52)
[ 1042.006]  -> wp_image_description_creator_params_v1#52.set_primaries_named(8)
[ 1042.007]  -> wp_image_description_creator_params_v1#52.set_tf_named(13)
[ 1042.008]  -> wp_image_description_creator_params_v1#52.create(new id wp_image_description_v1#32)
[ 1042.009]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#54)
[ 1042.010]  -> wp_image_description_creator_params_v1#54.set_primaries_named(9)
[ 1042.011]  -> wp_image_description_creator_params_v1#54.set_tf_named(11)
[ 1042.012]  -> wp_image_description_creator_params_v1#54.create(new id wp_image_description_v1#33)
        """.strip()
        calls = _calls(trace)
        descs = _extract_image_descriptions_info(calls)
        # sRGB primaries + PQ: semantic_class is hdr, color_type is accurate custom_p1_tf11 (not rec2100_pq)
        self.assertEqual(descs[31]["semantic_class"], "hdr")
        self.assertEqual(descs[31]["color_type"], "custom_p1_tf11")
        self.assertNotEqual(descs[31]["color_type"], "rec2100_pq")
        self.assertEqual(descs[31]["primaries_named"], 1)
        self.assertEqual(descs[31]["tf_named"], 11)

        # DCI-P3 primaries + HLG: semantic_class is hdr, color_type is custom_p8_tf13 (not rec2100_hlg)
        self.assertEqual(descs[32]["semantic_class"], "hdr")
        self.assertEqual(descs[32]["color_type"], "custom_p8_tf13")
        self.assertNotEqual(descs[32]["color_type"], "rec2100_hlg")
        self.assertEqual(descs[32]["primaries_named"], 8)
        self.assertEqual(descs[32]["tf_named"], 13)

        # Display P3 primaries + PQ: semantic_class is hdr, color_type is custom_p9_tf11 (not rec2100_pq)
        self.assertEqual(descs[33]["semantic_class"], "hdr")
        self.assertEqual(descs[33]["color_type"], "custom_p9_tf11")
        self.assertNotEqual(descs[33]["color_type"], "rec2100_pq")
        self.assertEqual(descs[33]["primaries_named"], 9)
        self.assertEqual(descs[33]["tf_named"], 11)

    def test_extract_image_descriptions_sdr_and_wcg_sdr_transfers(self):
        """Recognized regular SDR transfers and WCG-SDR (BT.2020 + BT.1886) classify as semantic_class='sdr'."""
        from tools.validation.parse_wayland_color_trace import _calls, _extract_image_descriptions_info
        trace = """
[ 1042.001]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.002]  -> wp_image_description_creator_params_v1#50.set_primaries_named(1)
[ 1042.003]  -> wp_image_description_creator_params_v1#50.set_tf_named(1)
[ 1042.004]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.005]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#52)
[ 1042.006]  -> wp_image_description_creator_params_v1#52.set_primaries_named(1)
[ 1042.007]  -> wp_image_description_creator_params_v1#52.set_tf_named(2)
[ 1042.008]  -> wp_image_description_creator_params_v1#52.create(new id wp_image_description_v1#32)
[ 1042.009]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#54)
[ 1042.010]  -> wp_image_description_creator_params_v1#54.set_primaries_named(1)
[ 1042.011]  -> wp_image_description_creator_params_v1#54.set_tf_named(14)
[ 1042.012]  -> wp_image_description_creator_params_v1#54.create(new id wp_image_description_v1#33)
[ 1042.013]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#56)
[ 1042.014]  -> wp_image_description_creator_params_v1#56.set_primaries_named(6)
[ 1042.015]  -> wp_image_description_creator_params_v1#56.set_tf_named(1)
[ 1042.016]  -> wp_image_description_creator_params_v1#56.create(new id wp_image_description_v1#34)
[ 1042.017]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#58)
[ 1042.018]  -> wp_image_description_creator_params_v1#58.set_primaries_named(1)
[ 1042.019]  -> wp_image_description_creator_params_v1#58.set_tf_named(4)
[ 1042.020]  -> wp_image_description_creator_params_v1#58.create(new id wp_image_description_v1#35)
        """.strip()
        calls = _calls(trace)
        descs = _extract_image_descriptions_info(calls)
        # sRGB + BT.1886
        self.assertEqual(descs[31]["semantic_class"], "sdr")
        self.assertEqual(descs[31]["color_type"], "srgb_bt709")

        # sRGB + gamma22
        self.assertEqual(descs[32]["semantic_class"], "sdr")
        self.assertEqual(descs[32]["color_type"], "srgb_bt709")

        # sRGB + compound_power_2_4
        self.assertEqual(descs[33]["semantic_class"], "sdr")
        self.assertEqual(descs[33]["color_type"], "srgb_bt709")

        # WCG-SDR: BT.2020 primaries + BT.1886 transfer -> must be classified as 'sdr', not HDR
        self.assertEqual(descs[34]["semantic_class"], "sdr")
        self.assertEqual(descs[34]["color_type"], "custom_p6_tf1")
        self.assertNotEqual(descs[34]["color_type"], "rec2100_pq")
        self.assertNotEqual(descs[34]["color_type"], "rec2100_hlg")
        self.assertNotEqual(descs[34]["color_type"], "srgb_bt709")

        # sRGB + ST 240
        self.assertEqual(descs[35]["semantic_class"], "sdr")
        self.assertEqual(descs[35]["color_type"], "custom_p1_tf4")

    def test_extract_image_descriptions_primaries_only_unknown_tf_and_ext_linear(self):
        """Primaries alone, unknown transfer function, and ext_linear without luminance parsing classify as 'unknown'."""
        from tools.validation.parse_wayland_color_trace import _calls, _extract_image_descriptions_info
        trace = """
[ 1042.001]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.002]  -> wp_image_description_creator_params_v1#50.set_primaries_named(6)
[ 1042.003]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.004]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#52)
[ 1042.005]  -> wp_image_description_creator_params_v1#52.set_primaries_named(6)
[ 1042.006]  -> wp_image_description_creator_params_v1#52.set_tf_named(5)
[ 1042.007]  -> wp_image_description_creator_params_v1#52.create(new id wp_image_description_v1#32)
[ 1042.008]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#54)
[ 1042.009]  -> wp_image_description_creator_params_v1#54.set_primaries_named(6)
[ 1042.010]  -> wp_image_description_creator_params_v1#54.set_tf_named(99)
[ 1042.011]  -> wp_image_description_creator_params_v1#54.create(new id wp_image_description_v1#33)
[ 1042.012]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#56)
[ 1042.013]  -> wp_image_description_creator_params_v1#56.set_primaries_named(1)
[ 1042.014]  -> wp_image_description_creator_params_v1#56.set_tf_named(6)
[ 1042.015]  -> wp_image_description_creator_params_v1#56.create(new id wp_image_description_v1#34)
[ 1042.016]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#58)
[ 1042.017]  -> wp_image_description_creator_params_v1#58.create(new id wp_image_description_v1#35)
        """.strip()
        calls = _calls(trace)
        descs = _extract_image_descriptions_info(calls)
        # Only primaries (BT.2020) without TF: primaries alone do NOT prove HDR -> unknown
        self.assertEqual(descs[31]["semantic_class"], "unknown")
        self.assertEqual(descs[31]["primaries_named"], 6)
        self.assertIsNone(descs[31]["tf_named"])
        self.assertEqual(descs[31]["color_type"], "custom_p6_tfNone")

        # ext_linear (tf=5) without luminance parsing -> unknown
        self.assertEqual(descs[32]["semantic_class"], "unknown")
        self.assertEqual(descs[32]["primaries_named"], 6)
        self.assertEqual(descs[32]["tf_named"], 5)
        self.assertEqual(descs[32]["color_type"], "custom_p6_tf5")

        # Unknown TF (99) -> unknown
        self.assertEqual(descs[33]["semantic_class"], "unknown")
        self.assertEqual(descs[33]["primaries_named"], 6)
        self.assertEqual(descs[33]["tf_named"], 99)
        self.assertEqual(descs[33]["color_type"], "custom_p6_tf99")

        # log_100 (tf=6, camera curve without display SDR/HDR definition) -> unknown
        self.assertEqual(descs[34]["semantic_class"], "unknown")
        self.assertEqual(descs[34]["primaries_named"], 1)
        self.assertEqual(descs[34]["tf_named"], 6)
        self.assertEqual(descs[34]["color_type"], "custom_p1_tf6")

        # Neither primaries nor TF -> unknown, unrevealed
        self.assertEqual(descs[35]["semantic_class"], "unknown")
        self.assertIsNone(descs[35]["primaries_named"])
        self.assertIsNone(descs[35]["tf_named"])
        self.assertEqual(descs[35]["color_type"], "unrevealed")

    def test_extract_image_descriptions_via_creator_and_get_information(self):
        """Image descriptions extracted via parametric creator and via get_information info object."""
        from tools.validation.parse_wayland_color_trace import _calls, _extract_image_descriptions_info
        trace = """
[ 1042.001]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.002]  -> wp_image_description_creator_params_v1#50.set_primaries_named(6)
[ 1042.003]  -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.004]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.005]  -> wp_image_description_v1#41.get_information(new id wp_image_description_info_v1#100)
[ 1042.006]  wp_image_description_info_v1#100.primaries_named(6)
[ 1042.007]  wp_image_description_info_v1#100.tf_named(13)
[ 1042.008]  wp_image_description_info_v1#100.done()
[ 1042.009]  -> wp_image_description_v1#42.get_information(new id wp_image_description_info_v1#101)
[ 1042.010]  wp_image_description_info_v1#101.primaries_named(1)
[ 1042.011]  wp_image_description_info_v1#101.tf_named(9)
[ 1042.012]  wp_image_description_info_v1#101.done()
        """.strip()
        calls = _calls(trace)
        descs = _extract_image_descriptions_info(calls)
        # Description 31 from creator: BT.2020 PQ
        self.assertEqual(descs[31]["primaries_named"], 6)
        self.assertEqual(descs[31]["tf_named"], 11)
        self.assertEqual(descs[31]["color_type"], "rec2100_pq")
        self.assertEqual(descs[31]["semantic_class"], "hdr")

        # Description 41 from get_information info object: BT.2020 HLG
        self.assertEqual(descs[41]["primaries_named"], 6)
        self.assertEqual(descs[41]["tf_named"], 13)
        self.assertEqual(descs[41]["color_type"], "rec2100_hlg")
        self.assertEqual(descs[41]["semantic_class"], "hdr")

        # Description 42 from get_information info object: sRGB
        self.assertEqual(descs[42]["primaries_named"], 1)
        self.assertEqual(descs[42]["tf_named"], 9)
        self.assertEqual(descs[42]["color_type"], "srgb_bt709")
        self.assertEqual(descs[42]["semantic_class"], "sdr")

    def test_extract_image_descriptions_neighboring_description_metadata_isolation(self):
        """Metadata of neighboring description_id is strictly isolated and does not transfer to target."""
        from tools.validation.parse_wayland_color_trace import _calls, _extract_image_descriptions_info
        trace = """
[ 1042.001]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.002]  -> wp_image_description_creator_params_v1#50.set_primaries_named(6)
[ 1042.003]  -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.004]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.005]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#52)
[ 1042.006]  -> wp_image_description_creator_params_v1#52.set_primaries_named(6)
[ 1042.007]  -> wp_image_description_creator_params_v1#52.create(new id wp_image_description_v1#32)
[ 1042.008]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#54)
[ 1042.009]  -> wp_image_description_creator_params_v1#54.set_primaries_named(1)
[ 1042.010]  -> wp_image_description_creator_params_v1#54.set_tf_named(9)
[ 1042.011]  -> wp_image_description_creator_params_v1#54.create(new id wp_image_description_v1#33)
[ 1042.012]  -> wp_image_description_v1#41.get_information(new id wp_image_description_info_v1#100)
[ 1042.013]  wp_image_description_info_v1#100.primaries_named(6)
[ 1042.014]  wp_image_description_info_v1#100.tf_named(11)
[ 1042.015]  wp_image_description_info_v1#100.done()
[ 1042.016]  -> wp_image_description_v1#42.get_information(new id wp_image_description_info_v1#101)
[ 1042.017]  wp_image_description_info_v1#101.primaries_named(6)
[ 1042.018]  wp_image_description_info_v1#101.done()
[ 1042.019]  -> wp_image_description_v1#43.get_information(new id wp_image_description_info_v1#102)
[ 1042.020]  wp_image_description_info_v1#102.tf_named(1)
[ 1042.021]  wp_image_description_info_v1#102.done()
        """.strip()
        calls = _calls(trace)
        descs = _extract_image_descriptions_info(calls)
        # Creator isolation: description 32 must not inherit tf_named=11 from description 31
        self.assertEqual(descs[31]["semantic_class"], "hdr")
        self.assertEqual(descs[31]["tf_named"], 11)
        self.assertEqual(descs[31]["color_type"], "rec2100_pq")

        self.assertIsNone(descs[32]["tf_named"])
        self.assertEqual(descs[32]["semantic_class"], "unknown")
        self.assertEqual(descs[32]["color_type"], "custom_p6_tfNone")

        self.assertEqual(descs[33]["semantic_class"], "sdr")
        self.assertEqual(descs[33]["tf_named"], 9)
        self.assertEqual(descs[33]["color_type"], "srgb_bt709")

        # Info object isolation: description 42 must not inherit tf_named=11 from 41 or tf_named=1 from 43
        self.assertEqual(descs[41]["semantic_class"], "hdr")
        self.assertEqual(descs[41]["tf_named"], 11)

        self.assertIsNone(descs[42]["tf_named"])
        self.assertEqual(descs[42]["semantic_class"], "unknown")
        self.assertEqual(descs[42]["primaries_named"], 6)
        self.assertEqual(descs[42]["color_type"], "custom_p6_tfNone")

        # Description 43 has only tf_named=1; must not inherit primaries_named=6 from 41 or 42
        self.assertIsNone(descs[43]["primaries_named"])
        self.assertEqual(descs[43]["tf_named"], 1)
        self.assertEqual(descs[43]["semantic_class"], "sdr")
        self.assertEqual(descs[43]["color_type"], "custom_pNone_tf1")

    def test_classify_named_color_metadata(self):
        """Pure classification of named primaries and transfer functions into color_type and semantic_class."""
        from tools.validation.parse_wayland_color_trace import (
            PRIM_NAMED_BT2020,
            PRIM_NAMED_SRGB,
            TF_NAMED_BT1886,
            TF_NAMED_EXT_LINEAR,
            TF_NAMED_HLG,
            TF_NAMED_SRGB,
            TF_NAMED_ST2084_PQ,
            _classify_named_color_metadata,
        )

        # Standard sRGB + sRGB transfer -> srgb_bt709, sdr
        self.assertEqual(
            _classify_named_color_metadata(PRIM_NAMED_SRGB, TF_NAMED_SRGB),
            ("srgb_bt709", "sdr"),
        )
        # Rec.2100 PQ -> rec2100_pq, hdr
        self.assertEqual(
            _classify_named_color_metadata(PRIM_NAMED_BT2020, TF_NAMED_ST2084_PQ),
            ("rec2100_pq", "hdr"),
        )
        # Rec.2100 HLG -> rec2100_hlg, hdr
        self.assertEqual(
            _classify_named_color_metadata(PRIM_NAMED_BT2020, TF_NAMED_HLG),
            ("rec2100_hlg", "hdr"),
        )
        # Custom / WCG-SDR: BT.2020 + BT.1886 -> custom_p6_tf1, sdr
        self.assertEqual(
            _classify_named_color_metadata(PRIM_NAMED_BT2020, TF_NAMED_BT1886),
            ("custom_p6_tf1", "sdr"),
        )
        # sRGB + PQ -> custom_p1_tf11, hdr
        self.assertEqual(
            _classify_named_color_metadata(PRIM_NAMED_SRGB, TF_NAMED_ST2084_PQ),
            ("custom_p1_tf11", "hdr"),
        )
        # ext_linear without luminance parsing -> unknown
        self.assertEqual(
            _classify_named_color_metadata(PRIM_NAMED_BT2020, TF_NAMED_EXT_LINEAR),
            ("custom_p6_tf5", "unknown"),
        )
        # Incomplete / unrevealed
        self.assertEqual(
            _classify_named_color_metadata(None, None),
            ("unrevealed", "unknown"),
        )
        self.assertEqual(
            _classify_named_color_metadata(PRIM_NAMED_BT2020, None),
            ("custom_p6_tfNone", "unknown"),
        )
        self.assertEqual(
            _classify_named_color_metadata(None, TF_NAMED_ST2084_PQ),
            ("custom_pNone_tf11", "hdr"),
        )

    def test_multiphase_trace_hlg_transfer_rejected_for_pq(self):
        """Negative regression: HLG transfer function (tf=13) is rejected when PQ (tf=11) is required."""
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wl_compositor#1.create_surface(new id wl_surface#71)
[ 1042.010]  -> wl_subcompositor#6.get_subsurface(new id wl_subsurface#72, wl_surface#71, wl_surface#61)
[ 1042.011]  -> wp_viewporter#12.get_viewport(new id wp_viewport#75, wl_surface#71)
[ 1042.012]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#71)
[ 1042.013]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.014]  -> wp_image_description_creator_params_v1#50.set_primaries_named(1)
[ 1042.015]  -> wp_image_description_creator_params_v1#50.set_tf_named(9)
[ 1042.016]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.017]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#52)
[ 1042.018]  -> wp_image_description_creator_params_v1#52.set_primaries_named(6)
[ 1042.019]  -> wp_image_description_creator_params_v1#52.set_tf_named(13)
[ 1042.020]  -> wp_image_description_creator_params_v1#52.create(new id wp_image_description_v1#32)
[ 1042.021]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#54)
[ 1042.022]  -> wp_image_description_creator_params_v1#54.set_primaries_named(1)
[ 1042.023]  -> wp_image_description_creator_params_v1#54.set_tf_named(9)
[ 1042.024]  -> wp_image_description_creator_params_v1#54.create(new id wp_image_description_v1#33)
[ 1042.025]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#56)
[ 1042.026]  -> wp_image_description_creator_params_v1#56.set_primaries_named(6)
[ 1042.027]  -> wp_image_description_creator_params_v1#56.set_tf_named(11)
[ 1042.028]  -> wp_image_description_creator_params_v1#56.create(new id wp_image_description_v1#34)
[ 1042.029]  wp_image_description_v1#31.ready(101)
[ 1042.030]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.031]  -> wl_surface#71.attach(wl_buffer#81, 0, 0)
[ 1042.032]  -> wl_surface#71.commit()
[ 1042.033]  wp_image_description_v1#32.ready(102)
[ 1042.034]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#32, 0)
[ 1042.035]  -> wl_surface#71.attach(wl_buffer#82, 0, 0)
[ 1042.036]  -> wl_surface#71.commit()
[ 1042.037]  wp_image_description_v1#33.ready(103)
[ 1042.038]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#33, 0)
[ 1042.039]  -> wl_surface#71.attach(wl_buffer#83, 0, 0)
[ 1042.040]  -> wl_surface#71.commit()
[ 1042.041]  wp_image_description_v1#34.ready(104)
[ 1042.042]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#34, 0)
[ 1042.043]  -> wl_surface#71.attach(wl_buffer#84, 0, 0)
[ 1042.044]  -> wl_surface#71.commit()
        """.strip()
        evidence = validate_multiphase_trace(trace)
        self.assertNotEqual(evidence["status"], "PASS")
        self.assertEqual(evidence["status"], "UNKNOWN")
        self.assertFalse(evidence["matched"]["color_alternation_proven"])
        self.assertIn("rec2100_hlg", evidence["reason"])
        self.assertIn("does not match expected alternating SDR->HDR->SDR->HDR", evidence["reason"])

    def test_composite_window_synthetic_positive(self):
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.0101] -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.0102] -> wp_image_description_creator_params_v1#50.set_primaries_named(6)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.012]  wp_image_description_v1#31.ready(101)
[ 1042.013]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.014]  -> wl_surface#61.commit()
        """.strip()
        evidence = validate_composite_window_trace(trace)
        self.assertEqual(evidence["status"], "PASS")
        self.assertEqual(evidence["revision"], PRIMARY_COMPOSITE_REVISION)
        self.assertEqual(evidence["oracle"], "primary_composite_window")
        self.assertEqual(evidence["scope"], PRIMARY_COMPOSITE_SCOPE)
        self.assertEqual(evidence["matched"]["surface"]["wl_surface_id"], 61)
        self.assertEqual(evidence["matched"]["surface"]["app_id"], "io.github.rusmikev.CineHDR")
        self.assertEqual(evidence["matched"]["surface"]["role"], "composite_toplevel_window")
        self.assertEqual(evidence["matched"]["image_description"]["id"], 31)
        self.assertIsNotNone(evidence["matched"]["image_description"]["creation_index"])
        self.assertEqual(evidence["matched"]["image_description"]["semantic_class"], "hdr")
        self.assertEqual(evidence["matched"]["image_description"]["color_type"], "rec2100_pq")

    def test_composite_window_attempt3_positive(self):
        attempt3_trace = REPO_DIR / "validation-reports" / "wayland-trace-Z4GghM" / "wayland-client.raw.log"
        if not attempt3_trace.exists():
            self.skipTest(f"Attempt 3 trace not found at {attempt3_trace}")
        raw = attempt3_trace.read_bytes()
        self.assertEqual(
            hashlib.sha256(raw).hexdigest(),
            "2e9d5de1a514986d1cbcb88d0b6d6312725e44e065e7fae7b77f40c3b77b107c",
        )
        evidence = validate_composite_window_trace(raw.decode("utf-8"))
        self.assertIn(evidence["status"], ("PASS", "UNKNOWN"))
        self.assertEqual(evidence["revision"], PRIMARY_COMPOSITE_REVISION)
        self.assertEqual(evidence["oracle"], "primary_composite_window")
        matched = evidence["matched"]
        # Indices are zero-based trace line numbers.
        self.assertEqual(matched["registry_global"]["event_index"], 62)
        self.assertEqual(matched["manager"]["id"], 32)
        self.assertEqual(matched["manager"]["bind_index"], 63)
        self.assertEqual(matched["manager"]["perceptual_intent_index"], 131)
        self.assertEqual(matched["manager"]["done_index"], 148)
        self.assertEqual(matched["surface"]["wl_surface_id"], 61)
        self.assertEqual(matched["surface"]["app_id"], "io.github.rusmikev.CineHDR")
        self.assertEqual(matched["surface"]["role"], "composite_toplevel_window")
        self.assertEqual(matched["surface"]["color_management_surface_id"], 64)
        self.assertEqual(matched["surface"]["get_surface_index"], 559)
        self.assertEqual(matched["surface"]["set_image_description_index"], 953)
        self.assertEqual(matched["surface"]["commit_index"], 968)
        self.assertEqual(matched["image_description"]["id"], 51)
        self.assertEqual(matched["image_description"]["creation_index"], 181)
        self.assertEqual(matched["image_description"]["ready_index"], 643)
        self.assertEqual(matched["image_description"]["readiness"]["identity"], 7)

    def test_composite_window_rejects_wrong_surface(self):
        # Case A: app_id is foreign
        foreign_app_trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("org.gnome.Totem")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.012]  wp_image_description_v1#31.ready(101)
[ 1042.013]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.014]  -> wl_surface#61.commit()
        """.strip()
        with self.assertRaises(TraceValidationError) as cm:
            validate_composite_window_trace(foreign_app_trace)
        self.assertIn("no toplevel surface was attributed to CineHDR", str(cm.exception))

        # Case B: get_surface called on an unrelated surface
        unrelated_surface_trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wl_compositor#1.create_surface(new id wl_surface#88)
[ 1042.010]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#88)
[ 1042.011]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.012]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.013]  wp_image_description_v1#31.ready(101)
[ 1042.014]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.015]  -> wl_surface#88.commit()
        """.strip()
        with self.assertRaises(TraceValidationError) as cm:
            validate_composite_window_trace(unrelated_surface_trace)
        self.assertIn("wp_color_manager_v1.get_surface was not called for CineHDR's toplevel window surface", str(cm.exception))

    def test_composite_window_rejects_wrong_order(self):
        # Case A: set_image_description BEFORE ready
        ready_after_trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.0101] -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.0102] -> wp_image_description_creator_params_v1#50.set_primaries_named(6)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.012]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.013]  wp_image_description_v1#31.ready(101)
[ 1042.014]  -> wl_surface#61.commit()
        """.strip()
        with self.assertRaises(TraceValidationError) as cm:
            validate_composite_window_trace(ready_after_trace)
        self.assertIn("set_image_description was called before image description received ready event", str(cm.exception))

        # Case B: commit BEFORE set_image_description without subsequent commit
        commit_before_trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.0101] -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.0102] -> wp_image_description_creator_params_v1#50.set_primaries_named(6)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.012]  -> wl_surface#61.commit()
[ 1042.013]  wp_image_description_v1#31.ready(101)
[ 1042.014]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
        """.strip()
        with self.assertRaises(TraceValidationError) as cm:
            validate_composite_window_trace(commit_before_trace)
        self.assertIn("no wl_surface.commit observed on window surface after set_image_description", str(cm.exception))

    def test_composite_window_rejects_uncreated_description(self):
        """Reject ready/set/commit where wp_image_description_v1 was never created."""
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  wp_image_description_v1#31.ready(101)
[ 1042.011]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.012]  -> wl_surface#61.commit()
        """.strip()
        with self.assertRaises(TraceValidationError) as cm:
            validate_composite_window_trace(trace)
        self.assertIn("without proven creation", str(cm.exception))

    def test_composite_window_rejects_id_reuse_without_delete_id(self):
        """destroy -> create on the same ID without wl_display.delete_id must fail."""
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.0101] -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.0102] -> wp_image_description_creator_params_v1#50.set_primaries_named(6)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.012]  wp_image_description_v1#31.ready(101)
[ 1042.013]  -> wp_image_description_v1#31.destroy()
[ 1042.014]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#52)
[ 1042.015]  -> wp_image_description_creator_params_v1#52.create(new id wp_image_description_v1#31)
[ 1042.016]  wp_image_description_v1#31.ready(102)
[ 1042.017]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.018]  -> wl_surface#61.commit()
        """.strip()
        with self.assertRaises(TraceValidationError) as cm:
            validate_composite_window_trace(trace)
        self.assertIn("was recreated in the trace", str(cm.exception))
        self.assertIn("ID reuse is rejected as insufficient", str(cm.exception))

    def test_composite_window_rejects_delete_id_after_recreation(self):
        """delete_id that arrives only after the recreation does not release the ID."""
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.0101] -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.0102] -> wp_image_description_creator_params_v1#50.set_primaries_named(6)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.012]  wp_image_description_v1#31.ready(101)
[ 1042.013]  -> wp_image_description_v1#31.destroy()
[ 1042.014]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#52)
[ 1042.015]  -> wp_image_description_creator_params_v1#52.create(new id wp_image_description_v1#31)
[ 1042.016]  wl_display#1.delete_id(31)
[ 1042.017]  wp_image_description_v1#31.ready(102)
[ 1042.018]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.019]  -> wl_surface#61.commit()
        """.strip()
        with self.assertRaises(TraceValidationError) as cm:
            validate_composite_window_trace(trace)
        self.assertIn("was recreated in the trace", str(cm.exception))
        self.assertIn("ID reuse is rejected as insufficient", str(cm.exception))

    def test_composite_window_rejects_id_reuse_even_with_delete_id(self):
        """destroy -> delete_id -> create must be rejected as insufficient proof on the primary row."""
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.0101] -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.0102] -> wp_image_description_creator_params_v1#50.set_primaries_named(6)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.012]  wp_image_description_v1#31.ready(101)
[ 1042.013]  -> wp_image_description_v1#31.destroy()
[ 1042.014]  wl_display#1.delete_id(31)
[ 1042.015]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#52)
[ 1042.016]  -> wp_image_description_creator_params_v1#52.create(new id wp_image_description_v1#31)
[ 1042.017]  wp_image_description_v1#31.ready(102)
[ 1042.018]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.019]  -> wl_surface#61.commit()
        """.strip()
        with self.assertRaises(TraceValidationError) as cm:
            validate_composite_window_trace(trace)
        self.assertIn("was recreated in the trace", str(cm.exception))
        self.assertIn("ID reuse is rejected as insufficient", str(cm.exception))

    def test_composite_window_rejects_id_reuse_with_foreign_callback_delete_id(self):
        """Regression test: foreign wl_callback#31.done() -> delete_id(31) across two registries must not allow false PASS."""
        trace = """
[ 1042.001]  -> wl_display#1.get_registry(new id wl_registry#2)
[ 1042.002]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.003]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.004]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.005]  wp_color_manager_v1#3.done()
[ 1042.006]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.007]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.008]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.009]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.010]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.011]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.012]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.013]  wp_image_description_v1#31.ready(101)
[ 1042.014]  -> wp_image_description_v1#31.destroy()
[ 1042.015]  -> wl_display#1.get_registry(new id wl_registry#99)
[ 1042.016]  wl_callback#31.done(1)
[ 1042.017]  wl_display#1.delete_id(31)
[ 1042.018]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#52)
[ 1042.019]  -> wp_image_description_creator_params_v1#52.create(new id wp_image_description_v1#31)
[ 1042.020]  wp_image_description_v1#31.ready(102)
[ 1042.021]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.022]  -> wl_surface#61.commit()
        """.strip()
        with self.assertRaises(TraceValidationError) as cm:
            validate_composite_window_trace(trace)
        self.assertIn("was recreated in the trace", str(cm.exception))
        self.assertIn("ID reuse is rejected as insufficient", str(cm.exception))

    def test_composite_window_rejects_recreation_without_prior_destroy(self):
        """Recreating an image description ID before destroying previous instance fails."""
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.0101] -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.0102] -> wp_image_description_creator_params_v1#50.set_primaries_named(6)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.012]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#52)
[ 1042.013]  -> wp_image_description_creator_params_v1#52.create(new id wp_image_description_v1#31)
[ 1042.014]  wp_image_description_v1#31.ready(102)
[ 1042.015]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.016]  -> wl_surface#61.commit()
        """.strip()
        with self.assertRaises(TraceValidationError) as cm:
            validate_composite_window_trace(trace)
        self.assertIn("was recreated in the trace", str(cm.exception))

    def test_composite_window_rejects_missing_manager_capabilities(self):
        # Case A: missing wp_color_manager_v1 global
        no_global_trace = """
[ 1042.001]  wl_registry#2.global(4, "wl_shm", 1)
        """.strip()
        with self.assertRaises(TraceValidationError) as cm:
            validate_composite_window_trace(no_global_trace)
        self.assertIn("wp_color_manager_v1 was not advertised by the registry", str(cm.exception))

        # Case B: missing supported_intent(0)
        no_intent_trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.0101] -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.0102] -> wp_image_description_creator_params_v1#50.set_primaries_named(6)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.012]  wp_image_description_v1#31.ready(101)
[ 1042.013]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.014]  -> wl_surface#61.commit()
        """.strip()
        with self.assertRaises(TraceValidationError) as cm:
            validate_composite_window_trace(no_intent_trace)
        self.assertIn("supported_intent(0) missing", str(cm.exception))

        # Case C: missing done()
        no_done_trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.0101] -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.0102] -> wp_image_description_creator_params_v1#50.set_primaries_named(6)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.012]  wp_image_description_v1#31.ready(101)
[ 1042.013]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.014]  -> wl_surface#61.commit()
        """.strip()
        with self.assertRaises(TraceValidationError) as cm:
            validate_composite_window_trace(no_done_trace)
        self.assertIn("done()", str(cm.exception))

    def test_composite_window_rejects_protocol_error(self):
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.0101] -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.0102] -> wp_image_description_creator_params_v1#50.set_primaries_named(6)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.012]  wp_image_description_v1#31.ready(101)
[ 1042.013]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.014]  -> wl_surface#61.commit()
[ 1042.015]  wl_display#1.error(wl_surface#61, 1, "protocol error")
        """.strip()
        with self.assertRaises(TraceValidationError) as cm:
            validate_composite_window_trace(trace)
        self.assertIn("trace contains fatal/protocol rejection evidence", str(cm.exception))

    def test_composite_window_accepts_unchanged_surface_descriptor(self):
        """Unchanging surface color descriptor must NOT fail the primary row oracle."""
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.0101] -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.0102] -> wp_image_description_creator_params_v1#50.set_primaries_named(6)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.012]  wp_image_description_v1#31.ready(101)
[ 1042.013]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.014]  -> wl_surface#61.commit()
[ 1042.015]  -> wl_surface#61.commit()
        """.strip()
        evidence = validate_composite_window_trace(trace)
        self.assertEqual(evidence["status"], "PASS")

    def test_composite_window_rejects_unset_image_description_before_commit(self):
        """unset_image_description between set_image_description and commit must invalidate the submission."""
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.0101] -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.0102] -> wp_image_description_creator_params_v1#50.set_primaries_named(6)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.012]  wp_image_description_v1#31.ready(101)
[ 1042.013]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.014]  -> wp_color_management_surface_v1#23.unset_image_description()
[ 1042.015]  -> wl_surface#61.commit()
        """.strip()
        with self.assertRaises(TraceValidationError) as cm:
            validate_composite_window_trace(trace)
        self.assertIn("had unset_image_description called between set_image_description and wl_surface.commit", str(cm.exception))

    def test_composite_window_accepts_unset_image_description_before_set(self):
        """unset_image_description occurring prior to set_image_description is valid."""
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_management_surface_v1#23.unset_image_description()
[ 1042.011]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.0111] -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.0112] -> wp_image_description_creator_params_v1#50.set_primaries_named(6)
[ 1042.012]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.013]  wp_image_description_v1#31.ready(101)
[ 1042.014]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.015]  -> wl_surface#61.commit()
        """.strip()
        evidence = validate_composite_window_trace(trace)
        self.assertEqual(evidence["status"], "PASS")

    def test_composite_window_selects_subsequent_effective_description(self):
        """set #31 -> set #32 -> commit on the same color surface must select #32 as effective description."""
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.0101] -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.0102] -> wp_image_description_creator_params_v1#50.set_primaries_named(6)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.012]  wp_image_description_v1#31.ready(101)
[ 1042.013]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#52)
[ 1042.0131] -> wp_image_description_creator_params_v1#52.set_tf_named(11)
[ 1042.0132] -> wp_image_description_creator_params_v1#52.set_primaries_named(6)
[ 1042.014]  -> wp_image_description_creator_params_v1#52.create(new id wp_image_description_v1#32)
[ 1042.015]  wp_image_description_v1#32.ready(102)
[ 1042.016]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.017]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#32, 0)
[ 1042.018]  -> wl_surface#61.commit()
        """.strip()
        evidence = validate_composite_window_trace(trace)
        self.assertEqual(evidence["status"], "PASS")
        self.assertEqual(evidence["matched"]["image_description"]["id"], 32)
        self.assertEqual(evidence["matched"]["surface"]["set_image_description_index"], 20)
        self.assertEqual(evidence["matched"]["surface"]["commit_index"], 21)

    def test_composite_window_rejects_replaced_description_when_subsequent_set_is_invalid(self):
        """set #31 replaced by an invalid or unsupported set before commit must not fall back to #31."""
        # Case A: subsequent set has unsupported render intent 1 (only intent 0 was advertised)
        trace_unsupported_intent = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.0101] -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.0102] -> wp_image_description_creator_params_v1#50.set_primaries_named(6)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.012]  wp_image_description_v1#31.ready(101)
[ 1042.013]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.014]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 1)
[ 1042.015]  -> wl_surface#61.commit()
        """.strip()
        with self.assertRaises(TraceValidationError):
            validate_composite_window_trace(trace_unsupported_intent)

        # Case B: subsequent set references an uncreated image description
        trace_uncreated = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.0101] -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.0102] -> wp_image_description_creator_params_v1#50.set_primaries_named(6)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.012]  wp_image_description_v1#31.ready(101)
[ 1042.013]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.014]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#99, 0)
[ 1042.015]  -> wl_surface#61.commit()
        """.strip()
        with self.assertRaises(TraceValidationError):
            validate_composite_window_trace(trace_uncreated)

    def test_composite_window_rejects_color_surface_destroy_before_commit(self):
        """Destroying wp_color_management_surface_v1 unsets description and must invalidate candidate before commit."""
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.0101] -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.0102] -> wp_image_description_creator_params_v1#50.set_primaries_named(6)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.012]  wp_image_description_v1#31.ready(101)
[ 1042.013]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.014]  -> wp_color_management_surface_v1#23.destroy()
[ 1042.015]  -> wl_surface#61.commit()
        """.strip()
        with self.assertRaises(TraceValidationError):
            validate_composite_window_trace(trace)

    def test_composite_window_preserves_candidate_when_image_description_destroyed_after_set(self):
        """wp_image_description_v1 has copy semantics; destroying it after set_image_description preserves candidate."""
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.0101] -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.0102] -> wp_image_description_creator_params_v1#50.set_primaries_named(6)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.012]  wp_image_description_v1#31.ready(101)
[ 1042.013]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.014]  -> wp_image_description_v1#31.destroy()
[ 1042.015]  -> wl_surface#61.commit()
        """.strip()
        evidence = validate_composite_window_trace(trace)
        self.assertEqual(evidence["status"], "PASS")
        self.assertEqual(evidence["matched"]["image_description"]["id"], 31)

    def test_composite_window_operations_on_other_surface_do_not_cancel_candidate(self):
        """set/unset/destroy operations on another surface interface must not affect target surface candidate."""
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wl_compositor#1.create_surface(new id wl_surface#88)
[ 1042.011]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#24, wl_surface#88)
[ 1042.012]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.0121] -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.0122] -> wp_image_description_creator_params_v1#50.set_primaries_named(6)
[ 1042.013]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.014]  wp_image_description_v1#31.ready(101)
[ 1042.015]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#52)
[ 1042.0151] -> wp_image_description_creator_params_v1#52.set_tf_named(11)
[ 1042.0152] -> wp_image_description_creator_params_v1#52.set_primaries_named(6)
[ 1042.016]  -> wp_image_description_creator_params_v1#52.create(new id wp_image_description_v1#32)
[ 1042.017]  wp_image_description_v1#32.ready(102)
[ 1042.018]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.019]  -> wp_color_management_surface_v1#24.set_image_description(wp_image_description_v1#32, 0)
[ 1042.020]  -> wp_color_management_surface_v1#24.unset_image_description()
[ 1042.021]  -> wp_color_management_surface_v1#24.destroy()
[ 1042.022]  -> wl_surface#61.commit()
        """.strip()
        evidence = validate_composite_window_trace(trace)
        self.assertEqual(evidence["status"], "PASS")
        self.assertEqual(evidence["matched"]["image_description"]["id"], 31)
        self.assertEqual(evidence["matched"]["surface"]["wl_surface_id"], 61)

    def test_composite_window_rejects_foreign_manager_description(self):
        """wp_image_description_v1 created by a foreign manager/creator must be rejected."""
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#99.create_parametric_creator(new id wp_image_description_creator_params_v1#950)
[ 1042.011]  -> wp_image_description_creator_params_v1#950.create(new id wp_image_description_v1#31)
[ 1042.012]  wp_image_description_v1#31.ready(101)
[ 1042.013]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.014]  -> wl_surface#61.commit()
        """.strip()
        with self.assertRaises(TraceValidationError) as cm:
            validate_composite_window_trace(trace)
        self.assertIn("was not created by wp_color_manager_v1#3 or its creators", str(cm.exception))

    def test_composite_window_rejects_foreign_registry_surface(self):
        """wl_surface created by a compositor bound from a foreign registry must be rejected."""
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_registry#99.bind(1, "wl_compositor", 6, new id [unknown]#1)
[ 1042.006]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.007]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.008]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.009]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.010]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.011]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.012]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.013]  wp_image_description_v1#31.ready(101)
[ 1042.014]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.015]  -> wl_surface#61.commit()
        """.strip()
        with self.assertRaises(TraceValidationError) as cm:
            validate_composite_window_trace(trace)
        self.assertIn("bound from foreign registry wl_registry#99", str(cm.exception))

    def test_composite_window_rejects_ambiguous_delete_id_with_alien_destruction(self):
        """ID reuse delete_id is rejected if another object with the same ID was destroyed in that window."""
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.0101] -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.0102] -> wp_image_description_creator_params_v1#50.set_primaries_named(6)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.012]  wp_image_description_v1#31.ready(101)
[ 1042.013]  -> wp_image_description_v1#31.destroy()
[ 1042.014]  -> wl_buffer#31.destroy()
[ 1042.015]  wl_display#1.delete_id(31)
[ 1042.016]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#52)
[ 1042.017]  -> wp_image_description_creator_params_v1#52.create(new id wp_image_description_v1#31)
[ 1042.018]  wp_image_description_v1#31.ready(102)
[ 1042.019]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.020]  -> wl_surface#61.commit()
        """.strip()
        with self.assertRaises(TraceValidationError) as cm:
            validate_composite_window_trace(trace)
        self.assertIn("was recreated in the trace", str(cm.exception))
        self.assertIn("ID reuse is rejected as insufficient", str(cm.exception))

    def test_composite_window_with_custom_app_id_and_default_fallback(self):
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("mpv")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.0101] -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.0102] -> wp_image_description_creator_params_v1#50.set_primaries_named(6)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.012]  wp_image_description_v1#31.ready(101)
[ 1042.013]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.014]  -> wl_surface#61.commit()
        """.strip()
        # Matching app_id passes
        evidence = validate_composite_window_trace(trace, expected_app_id="mpv")
        self.assertEqual(evidence["status"], "PASS")
        self.assertEqual(evidence["matched"]["surface"]["app_id"], "mpv")

        # Default CineHDR fails on mpv trace
        with self.assertRaises(TraceValidationError) as cm:
            validate_composite_window_trace(trace)
        self.assertIn("no toplevel surface was attributed to CineHDR", str(cm.exception))
        self.assertIn("app_id 'io.github.rusmikev.CineHDR' not found", str(cm.exception))

        # Different expected app_id fails with matching message
        with self.assertRaises(TraceValidationError) as cm:
            validate_composite_window_trace(trace, expected_app_id="org.other.Player")
        self.assertIn("no toplevel surface was attributed to org.other.Player", str(cm.exception))
        self.assertIn("app_id 'org.other.Player' not found", str(cm.exception))

    def test_composite_window_accepts_parametric_pq_and_hlg(self):
        """Parametric descriptions with PQ (tf=11) and HLG (tf=13) grant PASS."""
        base_template = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.set_primaries_named({prim})
[ 1042.012]  -> wp_image_description_creator_params_v1#50.set_tf_named({tf})
[ 1042.013]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.014]  wp_image_description_v1#31.ready(101)
[ 1042.015]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.016]  -> wl_surface#61.commit()
        """.strip()

        # Rec.2100 PQ (BT.2020 + PQ)
        trace_pq = base_template.format(prim=6, tf=11)
        ev_pq = validate_composite_window_trace(trace_pq)
        self.assertEqual(ev_pq["status"], "PASS")
        self.assertEqual(ev_pq["matched"]["image_description"]["semantic_class"], "hdr")
        self.assertEqual(ev_pq["matched"]["image_description"]["color_type"], "rec2100_pq")
        self.assertEqual(ev_pq["matched"]["image_description"]["tf_named"], 11)
        self.assertEqual(ev_pq["matched"]["image_description"]["primaries_named"], 6)

        # Rec.2100 HLG (BT.2020 + HLG)
        trace_hlg = base_template.format(prim=6, tf=13)
        ev_hlg = validate_composite_window_trace(trace_hlg)
        self.assertEqual(ev_hlg["status"], "PASS")
        self.assertEqual(ev_hlg["matched"]["image_description"]["semantic_class"], "hdr")
        self.assertEqual(ev_hlg["matched"]["image_description"]["color_type"], "rec2100_hlg")
        self.assertEqual(ev_hlg["matched"]["image_description"]["tf_named"], 13)

        # PQ with non-BT.2020 primaries (e.g. sRGB prim=1) -> semantic_class=hdr, but custom color_type
        trace_custom_hdr = base_template.format(prim=1, tf=11)
        ev_custom = validate_composite_window_trace(trace_custom_hdr)
        self.assertEqual(ev_custom["status"], "PASS")
        self.assertEqual(ev_custom["matched"]["image_description"]["semantic_class"], "hdr")
        self.assertEqual(ev_custom["matched"]["image_description"]["color_type"], "custom_p1_tf11")

    def test_composite_window_rejects_sdr_transfers(self):
        """Parametric descriptions with standard SDR transfer functions fail with SDR error."""
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.set_primaries_named(1)
[ 1042.012]  -> wp_image_description_creator_params_v1#50.set_tf_named(9)
[ 1042.013]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.014]  wp_image_description_v1#31.ready(101)
[ 1042.015]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.016]  -> wl_surface#61.commit()
        """.strip()
        with self.assertRaises(TraceValidationError) as cm:
            validate_composite_window_trace(trace)
        self.assertIn("only SDR color descriptions were committed", str(cm.exception))

    def test_composite_window_rejects_wcg_sdr_transfer(self):
        """WCG-SDR (BT.2020 primaries + BT.1886 transfer) fails with SDR error."""
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.set_primaries_named(6)
[ 1042.012]  -> wp_image_description_creator_params_v1#50.set_tf_named(1)
[ 1042.013]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.014]  wp_image_description_v1#31.ready(101)
[ 1042.015]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.016]  -> wl_surface#61.commit()
        """.strip()
        with self.assertRaises(TraceValidationError) as cm:
            validate_composite_window_trace(trace)
        self.assertIn("only SDR color descriptions were committed", str(cm.exception))

    def test_composite_window_unrevealed_or_unknown_transfer_returns_unknown(self):
        """Unrevealed transfer function or unparsed creator returns UNKNOWN with reason."""
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.012]  wp_image_description_v1#31.ready(101)
[ 1042.013]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.014]  -> wl_surface#61.commit()
        """.strip()
        evidence = validate_composite_window_trace(trace)
        self.assertEqual(evidence["status"], "UNKNOWN")
        self.assertEqual(evidence["matched"]["image_description"]["semantic_class"], "unknown")
        self.assertIn("unrevealed", evidence["reason"])

    def test_composite_window_ext_linear_returns_unknown(self):
        """ext_linear (tf=5) without luminance parsing returns UNKNOWN."""
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.set_primaries_named(6)
[ 1042.012]  -> wp_image_description_creator_params_v1#50.set_tf_named(5)
[ 1042.013]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.014]  wp_image_description_v1#31.ready(101)
[ 1042.015]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.016]  -> wl_surface#61.commit()
        """.strip()
        evidence = validate_composite_window_trace(trace)
        self.assertEqual(evidence["status"], "UNKNOWN")
        self.assertEqual(evidence["matched"]["image_description"]["semantic_class"], "unknown")
        self.assertIn("ext_linear", evidence["reason"])

    def test_composite_window_parameters_after_create_returns_unknown(self):
        """Setting parameters on creator after create() does not grant false HDR PASS."""
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.012]  -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.013]  wp_image_description_v1#31.ready(101)
[ 1042.014]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.015]  -> wl_surface#61.commit()
        """.strip()
        evidence = validate_composite_window_trace(trace)
        self.assertEqual(evidence["status"], "UNKNOWN")
        self.assertIn("after create() destructor", evidence["reason"])

    def test_composite_window_parameters_on_foreign_creator_not_transferred(self):
        """Parameters set on another creator do not leak to the candidate description."""
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.012]  -> wp_image_description_creator_params_v1#50.set_primaries_named(6)
[ 1042.013]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#52)
[ 1042.014]  -> wp_image_description_creator_params_v1#52.create(new id wp_image_description_v1#32)
[ 1042.015]  wp_image_description_v1#32.ready(102)
[ 1042.016]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#32, 0)
[ 1042.017]  -> wl_surface#61.commit()
        """.strip()
        evidence = validate_composite_window_trace(trace)
        self.assertEqual(evidence["status"], "UNKNOWN")
        self.assertEqual(evidence["matched"]["image_description"]["id"], 32)
        self.assertIn("unrevealed", evidence["reason"])

    def test_composite_window_hdr_replaced_by_sdr_before_commit_rejects(self):
        """HDR description replaced by SDR description before commit rejects with SDR error."""
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.012]  -> wp_image_description_creator_params_v1#50.set_primaries_named(6)
[ 1042.013]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.014]  wp_image_description_v1#31.ready(101)
[ 1042.015]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#52)
[ 1042.016]  -> wp_image_description_creator_params_v1#52.set_tf_named(1)
[ 1042.017]  -> wp_image_description_creator_params_v1#52.set_primaries_named(1)
[ 1042.018]  -> wp_image_description_creator_params_v1#52.create(new id wp_image_description_v1#32)
[ 1042.019]  wp_image_description_v1#32.ready(102)
[ 1042.020]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.021]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#32, 0)
[ 1042.022]  -> wl_surface#61.commit()
        """.strip()
        with self.assertRaises(TraceValidationError) as cm:
            validate_composite_window_trace(trace)
        self.assertIn("only SDR color descriptions were committed", str(cm.exception))

    def test_composite_window_early_sdr_commit_does_not_hide_subsequent_hdr_commit(self):
        """Early SDR commit does not hide a later valid HDR commit on the window surface."""
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.set_tf_named(1)
[ 1042.012]  -> wp_image_description_creator_params_v1#50.set_primaries_named(1)
[ 1042.013]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.014]  wp_image_description_v1#31.ready(101)
[ 1042.015]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.016]  -> wl_surface#61.commit()
[ 1042.017]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#52)
[ 1042.018]  -> wp_image_description_creator_params_v1#52.set_tf_named(11)
[ 1042.019]  -> wp_image_description_creator_params_v1#52.set_primaries_named(6)
[ 1042.020]  -> wp_image_description_creator_params_v1#52.create(new id wp_image_description_v1#32)
[ 1042.021]  wp_image_description_v1#32.ready(102)
[ 1042.022]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#32, 0)
[ 1042.023]  -> wl_surface#61.commit()
        """.strip()
        evidence = validate_composite_window_trace(trace)
        self.assertEqual(evidence["status"], "PASS")
        self.assertEqual(evidence["matched"]["image_description"]["id"], 32)
        self.assertEqual(evidence["matched"]["image_description"]["semantic_class"], "hdr")
        self.assertEqual(evidence["matched"]["image_description"]["color_type"], "rec2100_pq")

    def test_composite_window_tf_power_with_named_tf_returns_unknown(self):
        """set_tf_power on creator (alone or mixed with set_tf_named) must return UNKNOWN."""
        # 1. set_tf_power before set_tf_named(11)
        trace_power_before = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.0100] -> wp_image_description_creator_params_v1#50.set_tf_power(22000)
[ 1042.0101] -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.0102] -> wp_image_description_creator_params_v1#50.set_primaries_named(6)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.012]  wp_image_description_v1#31.ready(101)
[ 1042.013]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.014]  -> wl_surface#61.commit()
        """.strip()
        evidence_before = validate_composite_window_trace(trace_power_before)
        self.assertEqual(evidence_before["status"], "UNKNOWN")

        # 2. set_tf_power after set_tf_named(11)
        trace_power_after = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.0101] -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.0102] -> wp_image_description_creator_params_v1#50.set_primaries_named(6)
[ 1042.0103] -> wp_image_description_creator_params_v1#50.set_tf_power(22000)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.012]  wp_image_description_v1#31.ready(101)
[ 1042.013]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.014]  -> wl_surface#61.commit()
        """.strip()
        evidence_after = validate_composite_window_trace(trace_power_after)
        self.assertEqual(evidence_after["status"], "UNKNOWN")

        # 3. set_tf_power alone
        trace_power_alone = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.0101] -> wp_image_description_creator_params_v1#50.set_tf_power(22000)
[ 1042.0102] -> wp_image_description_creator_params_v1#50.set_primaries_named(6)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.012]  wp_image_description_v1#31.ready(101)
[ 1042.013]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.014]  -> wl_surface#61.commit()
        """.strip()
        evidence_alone = validate_composite_window_trace(trace_power_alone)
        self.assertEqual(evidence_alone["status"], "UNKNOWN")

    def test_composite_window_missing_primaries_returns_unknown(self):
        """Creator with set_tf_named but no primaries set must return UNKNOWN."""
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.0101] -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.012]  wp_image_description_v1#31.ready(101)
[ 1042.013]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.014]  -> wl_surface#61.commit()
        """.strip()
        evidence = validate_composite_window_trace(trace)
        self.assertEqual(evidence["status"], "UNKNOWN")
        self.assertIn("primaries", evidence["reason"].lower())

    def test_composite_window_broken_or_unknown_primaries_returns_unknown(self):
        """Malformed or unknown primaries arguments must return UNKNOWN."""
        # 1. Broken argument
        trace_broken = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.0101] -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.0102] -> wp_image_description_creator_params_v1#50.set_primaries_named(broken)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.012]  wp_image_description_v1#31.ready(101)
[ 1042.013]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.014]  -> wl_surface#61.commit()
        """.strip()
        evidence_broken = validate_composite_window_trace(trace_broken)
        self.assertEqual(evidence_broken["status"], "UNKNOWN")
        self.assertIn("primaries", evidence_broken["reason"].lower())

        # 2. Unknown enum value (999)
        trace_999 = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.0101] -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.0102] -> wp_image_description_creator_params_v1#50.set_primaries_named(999)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.012]  wp_image_description_v1#31.ready(101)
[ 1042.013]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.014]  -> wl_surface#61.commit()
        """.strip()
        evidence_999 = validate_composite_window_trace(trace_999)
        self.assertEqual(evidence_999["status"], "UNKNOWN")
        self.assertIn("primaries", evidence_999["reason"].lower())

    def test_composite_window_custom_set_primaries_returns_unknown(self):
        """Alternative set_primaries (CIE coordinates) is unverified and returns UNKNOWN."""
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.0101] -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.0102] -> wp_image_description_creator_params_v1#50.set_primaries(640000, 330000, 300000, 600000, 150000, 60000, 312700, 329000)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.012]  wp_image_description_v1#31.ready(101)
[ 1042.013]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.014]  -> wl_surface#61.commit()
        """.strip()
        evidence = validate_composite_window_trace(trace)
        self.assertEqual(evidence["status"], "UNKNOWN")
        self.assertIn("custom set_primaries", evidence["reason"].lower())

    def test_composite_window_early_unknown_commit_does_not_hide_subsequent_hdr_commit(self):
        """Early UNKNOWN commit does not hide a later valid HDR commit on the window surface."""
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.0101] -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.0102] -> wp_image_description_creator_params_v1#50.set_primaries_named(999)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.012]  wp_image_description_v1#31.ready(101)
[ 1042.013]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.014]  -> wl_surface#61.commit()
[ 1042.015]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#52)
[ 1042.016]  -> wp_image_description_creator_params_v1#52.set_tf_named(11)
[ 1042.017]  -> wp_image_description_creator_params_v1#52.set_primaries_named(6)
[ 1042.018]  -> wp_image_description_creator_params_v1#52.create(new id wp_image_description_v1#32)
[ 1042.019]  wp_image_description_v1#32.ready(102)
[ 1042.020]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#32, 0)
[ 1042.021]  -> wl_surface#61.commit()
        """.strip()
        evidence = validate_composite_window_trace(trace)
        self.assertEqual(evidence["status"], "PASS")
        self.assertEqual(evidence["matched"]["image_description"]["id"], 32)
        self.assertEqual(evidence["matched"]["image_description"]["semantic_class"], "hdr")
        self.assertEqual(evidence["matched"]["image_description"]["color_type"], "rec2100_pq")

    def test_composite_window_double_tf_with_problematic_primaries_prioritizes_tf_error(self):
        """Regression: multiple TF calls take priority over missing, unknown, broken, custom, or repeated primaries.

        Verifies that multiple TF requests return UNKNOWN, the contradictory TF reason,
        and unrevealed/None metadata, strictly preserving 570fa5c diagnostics behavior.
        """
        cases = [
            ("missing_primaries", ""),
            (
                "unknown_primaries",
                "[ 1042.0101] -> wp_image_description_creator_params_v1#50.set_primaries_named(999)",
            ),
            (
                "broken_primaries",
                "[ 1042.0101] -> wp_image_description_creator_params_v1#50.set_primaries_named(broken)",
            ),
            (
                "custom_primaries",
                "[ 1042.0101] -> wp_image_description_creator_params_v1#50.set_primaries(30000, 60000, 15000, 6000, 70000, 29000, 31270, 32900)",
            ),
            (
                "repeated_primaries",
                "[ 1042.0101] -> wp_image_description_creator_params_v1#50.set_primaries_named(1)\n"
                "[ 1042.0102] -> wp_image_description_creator_params_v1#50.set_primaries_named(6)",
            ),
        ]

        expected_reason = (
            "creator wp_image_description_creator_params_v1#50 had multiple transfer "
            "characteristic requests (already_set / contradictory)"
        )

        for case_name, prim_lines in cases:
            with self.subTest(case=case_name):
                trace = f"""
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
{prim_lines}
[ 1042.0103] -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.0104] -> wp_image_description_creator_params_v1#50.set_tf_named(1)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.012]  wp_image_description_v1#31.ready(101)
[ 1042.013]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.014]  -> wl_surface#61.commit()
""".strip()
                evidence = validate_composite_window_trace(trace)
                self.assertEqual(evidence["status"], "UNKNOWN")
                self.assertEqual(evidence["reason"], expected_reason)
                matched_img = evidence["matched"]["image_description"]
                self.assertIsNone(matched_img["primaries_named"])
                self.assertIsNone(matched_img["tf_named"])
                self.assertEqual(matched_img["color_type"], "unrevealed")
                self.assertEqual(matched_img["semantic_class"], "unknown")

    def test_composite_window_windows_scrgb_positive(self):
        """Positive control: valid windows_scrgb image description grants PASS in composite_window."""
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.0035] wp_color_manager_v1#3.supported_feature(7)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_windows_scrgb(new id wp_image_description_v1#31)
[ 1042.011]  wp_image_description_v1#31.ready(101)
[ 1042.012]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.013]  -> wl_surface#61.commit()
""".strip()
        evidence = validate_composite_window_trace(trace)
        self.assertEqual(evidence["status"], "PASS")
        self.assertEqual(evidence["oracle"], "primary_composite_window")
        matched_img = evidence["matched"]["image_description"]
        self.assertEqual(matched_img["id"], 31)
        self.assertEqual(matched_img["color_type"], "windows_scrgb")
        self.assertEqual(matched_img["semantic_class"], "hdr")
        self.assertEqual(matched_img["primaries_named"], 1)
        self.assertEqual(matched_img["tf_named"], 5)
        self.assertEqual(matched_img["readiness"]["identity"], 101)

    def test_composite_window_windows_scrgb_negative_and_fallback_controls(self):
        """Negative and fallback controls for windows_scrgb predefined HDR representation."""
        # 1. Feature not announced by manager -> UNKNOWN
        trace_no_feature = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_windows_scrgb(new id wp_image_description_v1#31)
[ 1042.011]  wp_image_description_v1#31.ready(101)
[ 1042.012]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.013]  -> wl_surface#61.commit()
""".strip()
        ev_no_feat = validate_composite_window_trace(trace_no_feature)
        self.assertEqual(ev_no_feat["status"], "UNKNOWN")
        self.assertIn("did not advertise feature.windows_scrgb", ev_no_feat["reason"])
        self.assertEqual(ev_no_feat["matched"]["image_description"]["color_type"], "windows_scrgb")
        self.assertEqual(ev_no_feat["matched"]["image_description"]["semantic_class"], "unknown")

        # 2. Feature announced by foreign manager -> UNKNOWN
        trace_foreign_feat = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.0041] wp_color_manager_v1#99.supported_feature(7)
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_windows_scrgb(new id wp_image_description_v1#31)
[ 1042.011]  wp_image_description_v1#31.ready(101)
[ 1042.012]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.013]  -> wl_surface#61.commit()
""".strip()
        ev_foreign_feat = validate_composite_window_trace(trace_foreign_feat)
        self.assertEqual(ev_foreign_feat["status"], "UNKNOWN")
        self.assertIn("did not advertise feature.windows_scrgb", ev_foreign_feat["reason"])

        # 3. Description created by foreign manager -> rejected (not PASS)
        trace_foreign_mgr = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.0035] wp_color_manager_v1#3.supported_feature(7)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#99.create_windows_scrgb(new id wp_image_description_v1#31)
[ 1042.011]  wp_image_description_v1#31.ready(101)
[ 1042.012]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.013]  -> wl_surface#61.commit()
""".strip()
        with self.assertRaises(TraceValidationError) as cm_fm:
            validate_composite_window_trace(trace_foreign_mgr)
        self.assertIn("was not created by wp_color_manager_v1#3 or its creators", str(cm_fm.exception))

        # 4. Missing ready -> rejected (not PASS)
        trace_no_ready = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.0035] wp_color_manager_v1#3.supported_feature(7)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_windows_scrgb(new id wp_image_description_v1#31)
[ 1042.012]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.013]  -> wl_surface#61.commit()
""".strip()
        with self.assertRaises(TraceValidationError) as cm_nr:
            validate_composite_window_trace(trace_no_ready)
        self.assertIn("never received a ready/ready2 event", str(cm_nr.exception))

        # 5. Missing commit -> rejected (not PASS)
        trace_no_commit = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.0035] wp_color_manager_v1#3.supported_feature(7)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_windows_scrgb(new id wp_image_description_v1#31)
[ 1042.011]  wp_image_description_v1#31.ready(101)
[ 1042.012]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
""".strip()
        with self.assertRaises(TraceValidationError) as cm_nc:
            validate_composite_window_trace(trace_no_commit)
        self.assertIn("no wl_surface.commit observed on window surface", str(cm_nc.exception))

        # 6. scRGB replaced by SDR before commit -> rejected (not PASS)
        trace_replaced_sdr = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.0035] wp_color_manager_v1#3.supported_feature(7)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_windows_scrgb(new id wp_image_description_v1#31)
[ 1042.011]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.012]  -> wp_image_description_creator_params_v1#50.set_primaries_named(1)
[ 1042.013]  -> wp_image_description_creator_params_v1#50.set_tf_named(1)
[ 1042.014]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#32)
[ 1042.015]  wp_image_description_v1#31.ready(101)
[ 1042.016]  wp_image_description_v1#32.ready(102)
[ 1042.017]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.018]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#32, 0)
[ 1042.019]  -> wl_surface#61.commit()
""".strip()
        with self.assertRaises(TraceValidationError) as cm_rs:
            validate_composite_window_trace(trace_replaced_sdr)
        self.assertIn("only SDR color descriptions were committed", str(cm_rs.exception))

        # 7. Early UNKNOWN commit, then valid scRGB commit -> PASS for second candidate
        trace_early_unknown = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.0035] wp_color_manager_v1#3.supported_feature(7)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.012]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#30)
[ 1042.013]  wp_image_description_v1#30.ready(100)
[ 1042.014]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#30, 0)
[ 1042.015]  -> wl_surface#61.commit()
[ 1042.016]  -> wp_color_manager_v1#3.create_windows_scrgb(new id wp_image_description_v1#31)
[ 1042.017]  wp_image_description_v1#31.ready(101)
[ 1042.018]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.019]  -> wl_surface#61.commit()
""".strip()
        ev_early = validate_composite_window_trace(trace_early_unknown)
        self.assertEqual(ev_early["status"], "PASS")
        self.assertEqual(ev_early["matched"]["image_description"]["id"], 31)
        self.assertEqual(ev_early["matched"]["image_description"]["color_type"], "windows_scrgb")
        self.assertEqual(ev_early["matched"]["image_description"]["semantic_class"], "hdr")

        # 8. Ordinary parametric ext_linear remains UNKNOWN
        trace_param_ext_linear = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.set_primaries_named(1)
[ 1042.012]  -> wp_image_description_creator_params_v1#50.set_tf_named(5)
[ 1042.013]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.014]  wp_image_description_v1#31.ready(101)
[ 1042.015]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.016]  -> wl_surface#61.commit()
""".strip()
        ev_pel = validate_composite_window_trace(trace_param_ext_linear)
        self.assertEqual(ev_pel["status"], "UNKNOWN")
        self.assertIn("ext_linear transfer function without luminance parsing is unverified", ev_pel["reason"])

    @staticmethod
    def _make_ext_linear_trace(
        *,
        feature_line: str = "wp_color_manager_v1#3.supported_feature(4)",
        lum_line: str = "-> wp_image_description_creator_params_v1#50.set_luminances(2000, 1000, 203)",
        post_create_line: str = "",
        intervening_lines: str = "",
        commit_line: str = "-> wl_surface#61.commit()",
        early_prefix: str = "",
        primaries_val: int = 6,
    ) -> str:
        body = [
            '[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)',
            '[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)',
            "[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)",
        ]
        if feature_line:
            body.append(f"[ 1042.0035] {feature_line}")
        body.extend([
            "[ 1042.004]  wp_color_manager_v1#3.done()",
            "[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)",
            '[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)',
            "[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)",
            '[ 1042.008]  -> xdg_toplevel#68.set_app_id("io.github.rusmikev.CineHDR")',
            "[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)",
        ])
        if early_prefix:
            body.append(early_prefix)
        body.extend([
            "[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)",
            f"[ 1042.011]  -> wp_image_description_creator_params_v1#50.set_primaries_named({primaries_val})",
            "[ 1042.012]  -> wp_image_description_creator_params_v1#50.set_tf_named(5)",
        ])
        if lum_line:
            body.append(f"[ 1042.0125] {lum_line}")
        body.append("[ 1042.013]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)")
        if post_create_line:
            body.append(f"[ 1042.0135] {post_create_line}")
        body.extend([
            "[ 1042.014]  wp_image_description_v1#31.ready(101)",
            "[ 1042.015]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)",
        ])
        if intervening_lines:
            body.append(intervening_lines)
        if commit_line:
            body.append(f"[ 1042.016]  {commit_line}")
        return "\n".join(body)

    def test_composite_window_ext_linear_luminances_positive(self):
        """Positive control: valid ext_linear with set_luminances grants PASS in composite_window."""
        trace = self._make_ext_linear_trace()
        evidence = validate_composite_window_trace(trace)
        self.assertEqual(evidence["status"], "PASS")
        self.assertEqual(evidence["oracle"], "primary_composite_window")
        matched_img = evidence["matched"]["image_description"]
        self.assertEqual(matched_img["id"], 31)
        self.assertEqual(matched_img["color_type"], "custom_p6_tf5")
        self.assertEqual(matched_img["semantic_class"], "hdr")
        self.assertEqual(matched_img["primaries_named"], 6)
        self.assertEqual(matched_img["tf_named"], 5)
        self.assertEqual(matched_img["readiness"]["identity"], 101)

    def test_composite_window_ext_linear_luminances_negative_and_fallback_controls(self):
        """Negative and fallback controls for ext_linear with set_luminances."""
        # 1. max_lum == ref_lum and max_lum < ref_lum -> UNKNOWN (HDR distinction criterion not met)
        for lum_args in ("2000, 203, 203", "2000, 203, 1000"):
            with self.subTest(lum_args=lum_args):
                tr = self._make_ext_linear_trace(
                    lum_line=f"-> wp_image_description_creator_params_v1#50.set_luminances({lum_args})"
                )
                ev = validate_composite_window_trace(tr)
                self.assertEqual(ev["status"], "UNKNOWN")
                self.assertEqual(ev["matched"]["image_description"]["semantic_class"], "unknown")
                self.assertIn("max_lum", ev["reason"])

        # 2. max_lum*10000 <= min_lum or ref_lum*10000 <= min_lum -> UNKNOWN
        for lum_args in ("10000000, 1000, 203", "2030000, 1000, 203"):
            with self.subTest(lum_args=lum_args):
                tr = self._make_ext_linear_trace(
                    lum_line=f"-> wp_image_description_creator_params_v1#50.set_luminances({lum_args})"
                )
                ev = validate_composite_window_trace(tr)
                self.assertEqual(ev["status"], "UNKNOWN")
                self.assertEqual(ev["matched"]["image_description"]["semantic_class"], "unknown")
                self.assertIn("must be strictly greater than min_lum", ev["reason"])

        # 3. Scale boundary conditions:
        # (799999, 1000, 80) -> PASS
        tr_scale_pass = self._make_ext_linear_trace(
            lum_line="-> wp_image_description_creator_params_v1#50.set_luminances(799999, 1000, 80)"
        )
        ev_scale_pass = validate_composite_window_trace(tr_scale_pass)
        self.assertEqual(ev_scale_pass["status"], "PASS")
        self.assertEqual(ev_scale_pass["matched"]["image_description"]["semantic_class"], "hdr")
        self.assertNotIn("reason", ev_scale_pass)

        # (800000, 1000, 80) -> UNKNOWN due to reference <= min (reference_lum*10000 <= min_lum)
        tr_scale_ref = self._make_ext_linear_trace(
            lum_line="-> wp_image_description_creator_params_v1#50.set_luminances(800000, 1000, 80)"
        )
        ev_scale_ref = validate_composite_window_trace(tr_scale_ref)
        self.assertEqual(ev_scale_ref["status"], "UNKNOWN")
        self.assertEqual(ev_scale_ref["matched"]["image_description"]["semantic_class"], "unknown")
        self.assertIn("must be strictly greater than min_lum", ev_scale_ref["reason"])
        self.assertIn("reference_lum*10000 (800000)", ev_scale_ref["reason"])

        # (799999, 80, 81) -> UNKNOWN due to missing HDR criterion, but NOT invalid range
        tr_scale_hdr = self._make_ext_linear_trace(
            lum_line="-> wp_image_description_creator_params_v1#50.set_luminances(799999, 80, 81)"
        )
        ev_scale_hdr = validate_composite_window_trace(tr_scale_hdr)
        self.assertEqual(ev_scale_hdr["status"], "UNKNOWN")
        self.assertEqual(ev_scale_hdr["matched"]["image_description"]["semantic_class"], "unknown")
        self.assertIn("HDR distinction criterion", ev_scale_hdr["reason"])
        self.assertNotIn("must be strictly greater than min_lum", ev_scale_hdr["reason"])

        # (800000, 80, 81) -> UNKNOWN due to max <= min (max_lum*10000 <= min_lum)
        tr_scale_max = self._make_ext_linear_trace(
            lum_line="-> wp_image_description_creator_params_v1#50.set_luminances(800000, 80, 81)"
        )
        ev_scale_max = validate_composite_window_trace(tr_scale_max)
        self.assertEqual(ev_scale_max["status"], "UNKNOWN")
        self.assertEqual(ev_scale_max["matched"]["image_description"]["semantic_class"], "unknown")
        self.assertIn("must be strictly greater than min_lum", ev_scale_max["reason"])
        self.assertIn("max_lum*10000 (800000)", ev_scale_max["reason"])
        self.assertNotIn("HDR distinction criterion", ev_scale_max["reason"])

        # 4. Negative, malformed, or uint32 overflow arguments -> UNKNOWN
        for lum_args in ("-1, 1000, 203", "2000, -1, 203", "2000, broken, 203", "4294967296, 1000, 203"):
            with self.subTest(bad_arg=lum_args):
                tr = self._make_ext_linear_trace(
                    lum_line=f"-> wp_image_description_creator_params_v1#50.set_luminances({lum_args})"
                )
                ev = validate_composite_window_trace(tr)
                self.assertEqual(ev["status"], "UNKNOWN")
                self.assertEqual(ev["matched"]["image_description"]["semantic_class"], "unknown")
                self.assertIn("malformed or out-of-range set_luminances arguments", ev["reason"])

        # 5. Missing set_luminances, repeated calls, foreign creator, or after create -> UNKNOWN
        ev_missing = validate_composite_window_trace(self._make_ext_linear_trace(lum_line=""))
        self.assertEqual(ev_missing["status"], "UNKNOWN")
        self.assertIn("without luminance parsing is unverified", ev_missing["reason"])

        tr_repeat = self._make_ext_linear_trace(
            lum_line="-> wp_image_description_creator_params_v1#50.set_luminances(2000, 1000, 203)\n"
            "[ 1042.0126] -> wp_image_description_creator_params_v1#50.set_luminances(2000, 1000, 203)"
        )
        ev_repeat = validate_composite_window_trace(tr_repeat)
        self.assertEqual(ev_repeat["status"], "UNKNOWN")
        self.assertIn("multiple set_luminances requests", ev_repeat["reason"])

        tr_foreign = self._make_ext_linear_trace(
            lum_line="-> wp_image_description_creator_params_v1#99.set_luminances(2000, 1000, 203)"
        )
        ev_foreign = validate_composite_window_trace(tr_foreign)
        self.assertEqual(ev_foreign["status"], "UNKNOWN")
        self.assertIn("without luminance parsing is unverified", ev_foreign["reason"])

        tr_post = self._make_ext_linear_trace(
            lum_line="",
            post_create_line="-> wp_image_description_creator_params_v1#50.set_luminances(2000, 1000, 203)",
        )
        ev_post = validate_composite_window_trace(tr_post)
        self.assertEqual(ev_post["status"], "UNKNOWN")
        self.assertIn("method set_luminances invoked", ev_post["reason"])
        self.assertIn("after create() destructor", ev_post["reason"])

        # 6. Missing feature.set_luminances, foreign manager, or late advertisement -> UNKNOWN
        ev_no_feat = validate_composite_window_trace(self._make_ext_linear_trace(feature_line=""))
        self.assertEqual(ev_no_feat["status"], "UNKNOWN")
        self.assertIn("did not advertise feature.set_luminances", ev_no_feat["reason"])

        tr_foreign_mgr = self._make_ext_linear_trace(
            feature_line="wp_color_manager_v1#99.supported_feature(4)"
        )
        ev_foreign_mgr = validate_composite_window_trace(tr_foreign_mgr)
        self.assertEqual(ev_foreign_mgr["status"], "UNKNOWN")
        self.assertIn("did not advertise feature.set_luminances", ev_foreign_mgr["reason"])

        tr_late_feat = self._make_ext_linear_trace(
            feature_line="",
            post_create_line="wp_color_manager_v1#3.supported_feature(4)",
        )
        ev_late_feat = validate_composite_window_trace(tr_late_feat)
        self.assertEqual(ev_late_feat["status"], "UNKNOWN")
        self.assertIn("did not advertise feature.set_luminances", ev_late_feat["reason"])

        # 7. HDR description replaced by SDR before commit -> TraceValidationError
        sdr_lines = (
            "[ 1042.0151] -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#51)\n"
            "[ 1042.0152] -> wp_image_description_creator_params_v1#51.set_primaries_named(1)\n"
            "[ 1042.0153] -> wp_image_description_creator_params_v1#51.set_tf_named(1)\n"
            "[ 1042.0154] -> wp_image_description_creator_params_v1#51.create(new id wp_image_description_v1#32)\n"
            "[ 1042.0155] wp_image_description_v1#32.ready(102)\n"
            "[ 1042.0156] -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#32, 0)"
        )
        tr_sdr_replaced = self._make_ext_linear_trace(intervening_lines=sdr_lines)
        with self.assertRaises(TraceValidationError) as cm_sdr:
            validate_composite_window_trace(tr_sdr_replaced)
        self.assertIn("only SDR color descriptions were committed", str(cm_sdr.exception))

        # 8. Early UNKNOWN commit, then valid ext_linear HDR commit -> PASS for second candidate
        early_unknown = (
            "[ 1042.0091] -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#49)\n"
            "[ 1042.0092] -> wp_image_description_creator_params_v1#49.set_tf_named(11)\n"
            "[ 1042.0093] -> wp_image_description_creator_params_v1#49.create(new id wp_image_description_v1#30)\n"
            "[ 1042.0094] wp_image_description_v1#30.ready(100)\n"
            "[ 1042.0095] -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#30, 0)\n"
            "[ 1042.0096] -> wl_surface#61.commit()"
        )
        tr_early = self._make_ext_linear_trace(early_prefix=early_unknown)
        ev_early = validate_composite_window_trace(tr_early)
        self.assertEqual(ev_early["status"], "PASS")
        self.assertEqual(ev_early["matched"]["image_description"]["id"], 31)
        self.assertEqual(ev_early["matched"]["image_description"]["semantic_class"], "hdr")
        self.assertEqual(ev_early["matched"]["image_description"]["color_type"], "custom_p6_tf5")

    def test_composite_window_ext_linear_uint32_parsing_regressions(self):
        """Compact parameterized regressions for set_luminances uint32 parsing via validate_composite_window_trace."""
        # 1. Underscores in each of the three arguments -> UNKNOWN
        underscore_cases = (
            "2_000, 1000, 203",
            "2000, 1_000, 203",
            "2000, 1000, 2_03",
        )
        for lum_args in underscore_cases:
            with self.subTest(underscores=lum_args):
                tr = self._make_ext_linear_trace(
                    lum_line=f"-> wp_image_description_creator_params_v1#50.set_luminances({lum_args})"
                )
                ev = validate_composite_window_trace(tr)
                self.assertEqual(ev["status"], "UNKNOWN")
                self.assertEqual(ev["matched"]["image_description"]["semantic_class"], "unknown")
                self.assertIn("malformed or out-of-range set_luminances arguments", ev["reason"])

        # 2. Binary, octal, hex, fractions, signs, and Unicode digits -> UNKNOWN
        non_ascii_or_radix_cases = (
            "0b11111010000, 1000, 203",
            "2000, 0b1111101000, 203",
            "2000, 1000, 0b11001011",
            "0o3720, 1000, 203",
            "2000, 0o1750, 203",
            "2000, 1000, 0o313",
            "0x7d0, 1000, 203",
            "2000, 0x3e8, 203",
            "2000, 1000, 0xcb",
            "+2000, 1000, 203",
            "2000, +1000, 203",
            "2000, 1000, +203",
            "2000.0, 1000, 203",
            "2000, 1000.5, 203",
            "2000, 1000, 203.0",
            "2000, 1000, \u0662\u0660\u0663",
        )
        for lum_args in non_ascii_or_radix_cases:
            with self.subTest(non_decimal_or_radix=lum_args):
                tr = self._make_ext_linear_trace(
                    lum_line=f"-> wp_image_description_creator_params_v1#50.set_luminances({lum_args})"
                )
                ev = validate_composite_window_trace(tr)
                self.assertEqual(ev["status"], "UNKNOWN")
                self.assertEqual(ev["matched"]["image_description"]["semantic_class"], "unknown")
                self.assertIn("malformed or out-of-range set_luminances arguments", ev["reason"])

        # 3. Standard decimal values retain behavior -> PASS
        decimal_cases = (
            "2000, 1000, 203",
            "0, 1000, 203",
            "  2000  ,  1000  ,  203  ",
        )
        for lum_args in decimal_cases:
            with self.subTest(decimal=lum_args):
                tr = self._make_ext_linear_trace(
                    lum_line=f"-> wp_image_description_creator_params_v1#50.set_luminances({lum_args})"
                )
                ev = validate_composite_window_trace(tr)
                self.assertEqual(ev["status"], "PASS")
                self.assertEqual(ev["matched"]["image_description"]["semantic_class"], "hdr")
                self.assertNotIn("reason", ev)

        # 4. Overflow of any of the three arguments -> UNKNOWN
        overflow_cases = (
            "4294967296, 1000, 203",
            "2000, 4294967296, 203",
            "2000, 1000, 4294967296",
        )
        for lum_args in overflow_cases:
            with self.subTest(overflow=lum_args):
                tr = self._make_ext_linear_trace(
                    lum_line=f"-> wp_image_description_creator_params_v1#50.set_luminances({lum_args})"
                )
                ev = validate_composite_window_trace(tr)
                self.assertEqual(ev["status"], "UNKNOWN")
                self.assertEqual(ev["matched"]["image_description"]["semantic_class"], "unknown")
                self.assertIn("malformed or out-of-range set_luminances arguments", ev["reason"])


    def test_multiphase_with_custom_app_id_and_default_fallback(self):
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("mpv")
[ 1042.009]  -> wl_compositor#1.create_surface(new id wl_surface#71)
[ 1042.010]  -> wl_subcompositor#6.get_subsurface(new id wl_subsurface#72, wl_surface#71, wl_surface#61)
[ 1042.011]  -> wp_viewporter#12.get_viewport(new id wp_viewport#75, wl_surface#71)
[ 1042.012]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#71)
[ 1042.013]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.014]  -> wp_image_description_creator_params_v1#50.set_primaries_named(1)
[ 1042.015]  -> wp_image_description_creator_params_v1#50.set_tf_named(9)
[ 1042.016]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.017]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#52)
[ 1042.018]  -> wp_image_description_creator_params_v1#52.set_primaries_named(6)
[ 1042.019]  -> wp_image_description_creator_params_v1#52.set_tf_named(11)
[ 1042.020]  -> wp_image_description_creator_params_v1#52.create(new id wp_image_description_v1#32)
[ 1042.021]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#54)
[ 1042.022]  -> wp_image_description_creator_params_v1#54.set_primaries_named(1)
[ 1042.023]  -> wp_image_description_creator_params_v1#54.set_tf_named(9)
[ 1042.024]  -> wp_image_description_creator_params_v1#54.create(new id wp_image_description_v1#33)
[ 1042.025]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#56)
[ 1042.026]  -> wp_image_description_creator_params_v1#56.set_primaries_named(6)
[ 1042.027]  -> wp_image_description_creator_params_v1#56.set_tf_named(11)
[ 1042.028]  -> wp_image_description_creator_params_v1#56.create(new id wp_image_description_v1#34)
[ 1042.029]  wp_image_description_v1#31.ready(101)
[ 1042.030]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.031]  -> wl_surface#71.attach(wl_buffer#81, 0, 0)
[ 1042.032]  -> wl_surface#71.commit()
[ 1042.033]  wp_image_description_v1#32.ready(102)
[ 1042.034]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#32, 0)
[ 1042.035]  -> wl_surface#71.attach(wl_buffer#82, 0, 0)
[ 1042.036]  -> wl_surface#71.commit()
[ 1042.037]  wp_image_description_v1#33.ready(103)
[ 1042.038]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#33, 0)
[ 1042.039]  -> wl_surface#71.attach(wl_buffer#83, 0, 0)
[ 1042.040]  -> wl_surface#71.commit()
[ 1042.041]  wp_image_description_v1#34.ready(104)
[ 1042.042]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#34, 0)
[ 1042.043]  -> wl_surface#71.attach(wl_buffer#84, 0, 0)
[ 1042.044]  -> wl_surface#71.commit()
        """.strip()
        # Matching app_id passes
        evidence = validate_multiphase_trace(trace, expected_app_id="mpv")
        self.assertEqual(evidence["status"], "PASS")
        self.assertTrue(evidence["matched"]["surface"]["subsurface_video_chain_proven"])
        self.assertEqual(evidence["matched"]["surface"]["app_id"], "mpv")

        # Default CineHDR returns UNKNOWN
        evidence_default = validate_multiphase_trace(trace)
        self.assertEqual(evidence_default["status"], "UNKNOWN")
        self.assertFalse(evidence_default["matched"]["surface"]["subsurface_video_chain_proven"])
        self.assertIn("target surface cannot be attributed to CineHDR", evidence_default["reason"])

        # Different app_id returns UNKNOWN with corresponding message
        evidence_other = validate_multiphase_trace(trace, expected_app_id="org.other.App")
        self.assertEqual(evidence_other["status"], "UNKNOWN")
        self.assertIn("target surface cannot be attributed to org.other.App", evidence_other["reason"])

    def test_multi_app_trace_selects_only_specified_app(self):
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("mpv")
[ 1042.009]  -> wl_compositor#1.create_surface(new id wl_surface#81)
[ 1042.010]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#87, wl_surface#81)
[ 1042.011]  -> xdg_surface#87.get_toplevel(new id xdg_toplevel#88)
[ 1042.012]  -> xdg_toplevel#88.set_app_id("io.github.rusmikev.CineHDR")
[ 1042.013]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.014]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.0141] -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.0142] -> wp_image_description_creator_params_v1#50.set_primaries_named(6)
[ 1042.015]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.016]  wp_image_description_v1#31.ready(101)
[ 1042.017]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.018]  -> wl_surface#61.commit()
[ 1042.019]  -> wl_surface#81.commit()
        """.strip()
        # When expecting CineHDR, mpv's color management does not satisfy it
        with self.assertRaises(TraceValidationError) as cm:
            validate_composite_window_trace(trace, expected_app_id="io.github.rusmikev.CineHDR")
        self.assertIn("wp_color_manager_v1.get_surface was not called for CineHDR's toplevel window surface", str(cm.exception))

        # When expecting mpv, mpv's surface passes
        evidence = validate_composite_window_trace(trace, expected_app_id="mpv")
        self.assertEqual(evidence["status"], "PASS")
        self.assertEqual(evidence["matched"]["surface"]["wl_surface_id"], 61)
        self.assertEqual(evidence["matched"]["surface"]["app_id"], "mpv")

    def test_cli_and_report_propagate_app_id(self):
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("mpv")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.0101] -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.0102] -> wp_image_description_creator_params_v1#50.set_primaries_named(6)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.012]  wp_image_description_v1#31.ready(101)
[ 1042.013]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.014]  -> wl_surface#61.commit()
        """.strip()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            trace_path = root / "mpv.trace.log"
            report_path = root / "gate2-wayland-publication-gpu-next.txt"
            out_pass = root / "pass.json"
            out_fail = root / "fail.json"

            trace_path.write_text(trace, encoding="utf-8")
            report_path.write_text(
                "Overall result: PASS\nExpected backend: gpu-next\n",
                encoding="utf-8",
            )

            # create_evidence_report with expected_app_id="mpv"
            rep_evidence = create_evidence_report(
                trace_path, report_path, "gpu-next",
                oracle="composite_window",
                expected_app_id="mpv",
            )
            self.assertEqual(rep_evidence["status"], "PASS")
            self.assertEqual(rep_evidence["matched"]["surface"]["app_id"], "mpv")

            # create_evidence_report without app_id fails (default CineHDR)
            with self.assertRaises(TraceValidationError):
                create_evidence_report(
                    trace_path, report_path, "gpu-next",
                    oracle="composite_window",
                )

            # CLI with --app-id mpv succeeds
            status = main([
                "--trace", str(trace_path),
                "--publication-report", str(report_path),
                "--backend", "gpu-next",
                "--oracle", "composite_window",
                "--app-id", "mpv",
                "--output", str(out_pass),
            ])
            self.assertEqual(status, 0)
            data_pass = json.loads(out_pass.read_text(encoding="utf-8"))
            self.assertEqual(data_pass["status"], "PASS")
            self.assertEqual(data_pass["matched"]["surface"]["app_id"], "mpv")

            # CLI without --app-id fails (default CineHDR)
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                status_fail = main([
                    "--trace", str(trace_path),
                    "--publication-report", str(report_path),
                    "--backend", "gpu-next",
                    "--oracle", "composite_window",
                    "--output", str(out_fail),
                ])
            self.assertEqual(status_fail, 1)
            data_fail = json.loads(out_fail.read_text(encoding="utf-8"))
            self.assertEqual(data_fail["status"], "FAIL")

    def test_empty_app_id_and_legacy_combination_rejected(self):
        trace = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.007]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.008]  -> xdg_toplevel#68.set_app_id("mpv")
[ 1042.009]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#61)
[ 1042.010]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.0101] -> wp_image_description_creator_params_v1#50.set_tf_named(11)
[ 1042.0102] -> wp_image_description_creator_params_v1#50.set_primaries_named(6)
[ 1042.011]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.012]  wp_image_description_v1#31.ready(101)
[ 1042.013]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.014]  -> wl_surface#61.commit()
        """.strip()
        # Empty and whitespace-only app_id rejected
        for empty_val in ("", "   ", "\t"):
            with self.assertRaises(TraceValidationError) as cm:
                validate_composite_window_trace(trace, expected_app_id=empty_val)
            self.assertIn("cannot be empty or whitespace-only", str(cm.exception))

            with self.assertRaises(TraceValidationError) as cm:
                validate_multiphase_trace(trace, expected_app_id=empty_val)
            self.assertIn("cannot be empty or whitespace-only", str(cm.exception))

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            trace_path = root / "trace.log"
            report_path = root / "gate2-wayland-publication-legacy.txt"
            out = root / "out.json"

            trace_path.write_text(VALID_TRACE, encoding="utf-8")
            report_path.write_text(
                "Overall result: PASS\nExpected backend: legacy\n",
                encoding="utf-8",
            )

            # create_evidence_report with empty app_id rejected
            with self.assertRaises(TraceValidationError) as cm:
                create_evidence_report(
                    trace_path, report_path, "legacy",
                    oracle="composite_window",
                    expected_app_id="",
                )
            self.assertIn("cannot be empty or whitespace-only", str(cm.exception))

            # Legacy oracle does not support app_id (neither custom nor CineHDR)
            with self.assertRaises(TraceValidationError) as cm:
                create_evidence_report(
                    trace_path, report_path, "legacy",
                    oracle="legacy",
                    expected_app_id="mpv",
                )
            self.assertIn("legacy oracle does not support app_id attribution", str(cm.exception))

            with self.assertRaises(TraceValidationError) as cm:
                create_evidence_report(
                    trace_path, report_path, "legacy",
                    oracle="legacy",
                    expected_app_id="io.github.rusmikev.CineHDR",
                )
            self.assertIn("legacy oracle does not support app_id attribution", str(cm.exception))

            with self.assertRaises(TraceValidationError) as cm:
                create_evidence_report(
                    trace_path, report_path, "legacy",
                    expected_app_id="mpv",
                )
            self.assertIn("legacy oracle does not support app_id attribution", str(cm.exception))

            # CLI with legacy and app-id returns 1
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                status_legacy_app = main([
                    "--trace", str(trace_path),
                    "--publication-report", str(report_path),
                    "--backend", "legacy",
                    "--oracle", "legacy",
                    "--app-id", "mpv",
                    "--output", str(out),
                ])
            self.assertEqual(status_legacy_app, 1)
            self.assertIn("legacy oracle does not support app_id attribution", stderr.getvalue())

            # CLI with empty app-id returns 1
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                status_empty = main([
                    "--trace", str(trace_path),
                    "--publication-report", str(report_path),
                    "--backend", "legacy",
                    "--oracle", "composite_window",
                    "--app-id", "  ",
                    "--output", str(out),
                ])
            self.assertEqual(status_empty, 1)
            self.assertIn("cannot be empty or whitespace-only", stderr.getvalue())

    def test_multiphase_with_foreign_and_target_chains_ordering_and_completeness(self):
        header = """
[ 1042.001]  wl_registry#2.global(4, "wp_color_manager_v1", 1)
[ 1042.002]  -> wl_registry#2.bind(4, "wp_color_manager_v1", 1, new id [unknown]#3)
[ 1042.003]  wp_color_manager_v1#3.supported_intent(0)
[ 1042.004]  wp_color_manager_v1#3.done()
        """.strip()

        foreign_full_chain = """
[ 1042.005]  -> wl_compositor#1.create_surface(new id wl_surface#161)
[ 1042.006]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#167, wl_surface#161)
[ 1042.007]  -> xdg_surface#167.get_toplevel(new id xdg_toplevel#168)
[ 1042.008]  -> xdg_toplevel#168.set_app_id("org.example.Foreign")
[ 1042.009]  -> wl_compositor#1.create_surface(new id wl_surface#171)
[ 1042.010]  -> wl_subcompositor#6.get_subsurface(new id wl_subsurface#172, wl_surface#171, wl_surface#161)
[ 1042.011]  -> wp_viewporter#12.get_viewport(new id wp_viewport#175, wl_surface#171)
[ 1042.012]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#123, wl_surface#171)
[ 1042.013]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#150)
[ 1042.014]  -> wp_image_description_creator_params_v1#150.set_primaries_named(1)
[ 1042.015]  -> wp_image_description_creator_params_v1#150.set_tf_named(9)
[ 1042.016]  -> wp_image_description_creator_params_v1#150.create(new id wp_image_description_v1#131)
[ 1042.017]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#152)
[ 1042.018]  -> wp_image_description_creator_params_v1#152.set_primaries_named(6)
[ 1042.019]  -> wp_image_description_creator_params_v1#152.set_tf_named(11)
[ 1042.020]  -> wp_image_description_creator_params_v1#152.create(new id wp_image_description_v1#132)
[ 1042.021]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#154)
[ 1042.022]  -> wp_image_description_creator_params_v1#154.set_primaries_named(1)
[ 1042.023]  -> wp_image_description_creator_params_v1#154.set_tf_named(9)
[ 1042.024]  -> wp_image_description_creator_params_v1#154.create(new id wp_image_description_v1#133)
[ 1042.025]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#156)
[ 1042.026]  -> wp_image_description_creator_params_v1#156.set_primaries_named(6)
[ 1042.027]  -> wp_image_description_creator_params_v1#156.set_tf_named(11)
[ 1042.028]  -> wp_image_description_creator_params_v1#156.create(new id wp_image_description_v1#134)
[ 1042.029]  wp_image_description_v1#131.ready(101)
[ 1042.030]  -> wp_color_management_surface_v1#123.set_image_description(wp_image_description_v1#131, 0)
[ 1042.031]  -> wl_surface#171.attach(wl_buffer#181, 0, 0)
[ 1042.032]  -> wl_surface#171.commit()
[ 1042.033]  wp_image_description_v1#132.ready(102)
[ 1042.034]  -> wp_color_management_surface_v1#123.set_image_description(wp_image_description_v1#132, 0)
[ 1042.035]  -> wl_surface#171.attach(wl_buffer#182, 0, 0)
[ 1042.036]  -> wl_surface#171.commit()
[ 1042.037]  wp_image_description_v1#133.ready(103)
[ 1042.038]  -> wp_color_management_surface_v1#123.set_image_description(wp_image_description_v1#133, 0)
[ 1042.039]  -> wl_surface#171.attach(wl_buffer#183, 0, 0)
[ 1042.040]  -> wl_surface#171.commit()
[ 1042.041]  wp_image_description_v1#134.ready(104)
[ 1042.042]  -> wp_color_management_surface_v1#123.set_image_description(wp_image_description_v1#134, 0)
[ 1042.043]  -> wl_surface#171.attach(wl_buffer#184, 0, 0)
[ 1042.044]  -> wl_surface#171.commit()
        """.strip()

        target_full_chain = """
[ 1042.045]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.046]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.047]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.048]  -> xdg_toplevel#68.set_app_id("mpv")
[ 1042.049]  -> wl_compositor#1.create_surface(new id wl_surface#71)
[ 1042.050]  -> wl_subcompositor#6.get_subsurface(new id wl_subsurface#72, wl_surface#71, wl_surface#61)
[ 1042.051]  -> wp_viewporter#12.get_viewport(new id wp_viewport#75, wl_surface#71)
[ 1042.052]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#71)
[ 1042.053]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.054]  -> wp_image_description_creator_params_v1#50.set_primaries_named(1)
[ 1042.055]  -> wp_image_description_creator_params_v1#50.set_tf_named(9)
[ 1042.056]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.057]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#52)
[ 1042.058]  -> wp_image_description_creator_params_v1#52.set_primaries_named(6)
[ 1042.059]  -> wp_image_description_creator_params_v1#52.set_tf_named(11)
[ 1042.060]  -> wp_image_description_creator_params_v1#52.create(new id wp_image_description_v1#32)
[ 1042.061]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#54)
[ 1042.062]  -> wp_image_description_creator_params_v1#54.set_primaries_named(1)
[ 1042.063]  -> wp_image_description_creator_params_v1#54.set_tf_named(9)
[ 1042.064]  -> wp_image_description_creator_params_v1#54.create(new id wp_image_description_v1#33)
[ 1042.065]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#56)
[ 1042.066]  -> wp_image_description_creator_params_v1#56.set_primaries_named(6)
[ 1042.067]  -> wp_image_description_creator_params_v1#56.set_tf_named(11)
[ 1042.068]  -> wp_image_description_creator_params_v1#56.create(new id wp_image_description_v1#34)
[ 1042.069]  wp_image_description_v1#31.ready(101)
[ 1042.070]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.071]  -> wl_surface#71.attach(wl_buffer#81, 0, 0)
[ 1042.072]  -> wl_surface#71.commit()
[ 1042.073]  wp_image_description_v1#32.ready(102)
[ 1042.074]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#32, 0)
[ 1042.075]  -> wl_surface#71.attach(wl_buffer#82, 0, 0)
[ 1042.076]  -> wl_surface#71.commit()
[ 1042.077]  wp_image_description_v1#33.ready(103)
[ 1042.078]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#33, 0)
[ 1042.079]  -> wl_surface#71.attach(wl_buffer#83, 0, 0)
[ 1042.080]  -> wl_surface#71.commit()
[ 1042.081]  wp_image_description_v1#34.ready(104)
[ 1042.082]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#34, 0)
[ 1042.083]  -> wl_surface#71.attach(wl_buffer#84, 0, 0)
[ 1042.084]  -> wl_surface#71.commit()
        """.strip()

        target_incomplete_chain = """
[ 1042.045]  -> wl_compositor#1.create_surface(new id wl_surface#61)
[ 1042.046]  -> xdg_wm_base#5.get_xdg_surface(new id xdg_surface#67, wl_surface#61)
[ 1042.047]  -> xdg_surface#67.get_toplevel(new id xdg_toplevel#68)
[ 1042.048]  -> xdg_toplevel#68.set_app_id("mpv")
[ 1042.049]  -> wl_compositor#1.create_surface(new id wl_surface#71)
[ 1042.050]  -> wl_subcompositor#6.get_subsurface(new id wl_subsurface#72, wl_surface#71, wl_surface#61)
[ 1042.051]  -> wp_viewporter#12.get_viewport(new id wp_viewport#75, wl_surface#71)
[ 1042.052]  -> wp_color_manager_v1#3.get_surface(new id wp_color_management_surface_v1#23, wl_surface#71)
[ 1042.053]  -> wp_color_manager_v1#3.create_parametric_creator(new id wp_image_description_creator_params_v1#50)
[ 1042.054]  -> wp_image_description_creator_params_v1#50.set_primaries_named(1)
[ 1042.055]  -> wp_image_description_creator_params_v1#50.set_tf_named(9)
[ 1042.056]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.057]  wp_image_description_v1#31.ready(101)
[ 1042.058]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.059]  -> wl_surface#71.attach(wl_buffer#81, 0, 0)
[ 1042.060]  -> wl_surface#71.commit()
        """.strip()

        # 1. Foreign full chain before valid target chain -> PASS target
        trace_foreign_first = f"{header}\n{foreign_full_chain}\n{target_full_chain}"
        evidence1 = validate_multiphase_trace(trace_foreign_first, expected_app_id="mpv")
        self.assertEqual(evidence1["status"], "PASS")
        self.assertEqual(evidence1["matched"]["surface"]["app_id"], "mpv")
        self.assertEqual(evidence1["matched"]["surface"]["wl_surface_id"], 71)

        # 2. Reverse order (target full chain before foreign) -> same result and target app_id
        trace_target_first = f"{header}\n{target_full_chain}\n{foreign_full_chain}"
        evidence2 = validate_multiphase_trace(trace_target_first, expected_app_id="mpv")
        self.assertEqual(evidence2["status"], "PASS")
        self.assertEqual(evidence2["matched"]["surface"]["app_id"], "mpv")
        self.assertEqual(evidence2["matched"]["surface"]["wl_surface_id"], 71)

        # 3. Foreign full chain, target incomplete -> non-PASS
        trace_incomplete = f"{header}\n{foreign_full_chain}\n{target_incomplete_chain}"
        evidence3 = validate_multiphase_trace(trace_incomplete, expected_app_id="mpv")
        self.assertNotEqual(evidence3.get("status"), "PASS")

        # 4. Only foreign chain -> non-PASS (UNKNOWN)
        trace_only_foreign = f"{header}\n{foreign_full_chain}"
        evidence4 = validate_multiphase_trace(trace_only_foreign, expected_app_id="mpv")
        self.assertNotEqual(evidence4.get("status"), "PASS")
        self.assertEqual(evidence4.get("status"), "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
