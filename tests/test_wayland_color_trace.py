# Copyright 2026 rusmikev
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import contextlib
import hashlib
import io
from pathlib import Path
import tempfile
import unittest

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


if __name__ == "__main__":
    unittest.main()
