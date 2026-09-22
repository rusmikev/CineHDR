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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from parse_wayland_color_trace import (
    TRACE_REVISION,
    TRACE_SCHEMA,
    TraceValidationError,
    create_evidence_report,
    main,
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

    def test_realize_without_opt_in_blocks_hdr_even_with_good_caps(self):
        from src.gtk_cm_policy import TF_SRGB, TF_PQ
        supported, _probe = self._check_after_realize_invalidate(
            self._caps([TF_SRGB, TF_PQ]), {}
        )
        self.assertFalse(supported)

if __name__ == "__main__":
    unittest.main()
