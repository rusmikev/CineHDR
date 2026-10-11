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

REPO_DIR = Path(__file__).resolve().parents[1]
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
        self.assertIn("wp_color_manager_v1", reason)

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
        from tests.parse_wayland_color_trace import _calls, _extract_image_descriptions_info
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
        self.assertEqual(descs[32]["color_type"], "rec2100_hlg")
        self.assertEqual(descs[32]["tf_named"], 13)
        self.assertNotEqual(descs[31]["color_type"], descs[32]["color_type"])
        self.assertEqual(descs[33]["color_type"], "srgb_bt709")

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
[ 1042.011]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.012]  wp_image_description_v1#31.ready(101)
[ 1042.013]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.014]  -> wl_surface#61.commit()
        """.strip()
        evidence = validate_composite_window_trace(trace)
        self.assertEqual(evidence["status"], "PASS")
        self.assertEqual(evidence["revision"], PRIMARY_COMPOSITE_REVISION)
        self.assertEqual(evidence["oracle"], "primary_composite_window")
        self.assertEqual(evidence["matched"]["surface"]["wl_surface_id"], 61)
        self.assertEqual(evidence["matched"]["surface"]["app_id"], "io.github.rusmikev.CineHDR")
        self.assertEqual(evidence["matched"]["surface"]["role"], "composite_toplevel_window")
        self.assertEqual(evidence["matched"]["image_description"]["id"], 31)
        self.assertIsNotNone(evidence["matched"]["image_description"]["creation_index"])

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
        self.assertEqual(evidence["status"], "PASS")
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
[ 1042.012]  -> wp_image_description_creator_params_v1#50.create(new id wp_image_description_v1#31)
[ 1042.013]  wp_image_description_v1#31.ready(101)
[ 1042.014]  -> wp_color_management_surface_v1#23.set_image_description(wp_image_description_v1#31, 0)
[ 1042.015]  -> wl_surface#61.commit()
        """.strip()
        evidence = validate_composite_window_trace(trace)
        self.assertEqual(evidence["status"], "PASS")

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


if __name__ == "__main__":
    unittest.main()
