#!/usr/bin/env python3
"""Autonomous unit test for Wayland connection tagging interposer."""

import os
import re
import shutil
import subprocess
import tempfile
import unittest

TAGGED_LINE_RE = re.compile(
    r"^\[conn#(?P<conn_id>\d+)\s+fd#(?P<fd>\d+)\]\s+(?:\{[^\}]+\}\s+)?(?P<call>.*)$"
)


class TestWaylandConnTagger(unittest.TestCase):
    """Verifies that the LD_PRELOAD interposer tags all requests and events

    across multiple connections with distinct connection identifiers.
    """

    @classmethod
    def setUpClass(cls):
        cls.work_dir = tempfile.mkdtemp(prefix="wl_tagger_test_")
        cls.src_dir = os.path.dirname(os.path.abspath(__file__))
        cls.tagger_src = os.path.join(cls.src_dir, "wayland_conn_tagger.c")
        cls.harness_src = os.path.join(cls.src_dir, "test_conn_tagger_harness.c")
        cls.so_path = os.path.join(cls.work_dir, "libwayland_conn_tag.so")
        cls.bin_path = os.path.join(cls.work_dir, "test_conn_tagger_harness")

        # Compile interposer shared library
        cmd_so = [
            "gcc",
            "-Wall",
            "-Wextra",
            "-shared",
            "-fPIC",
            "-o",
            cls.so_path,
            cls.tagger_src,
            "-ldl",
            "-lwayland-client",
        ]
        res_so = subprocess.run(cmd_so, capture_output=True, text=True)
        if res_so.returncode != 0:
            shutil.rmtree(cls.work_dir)
            raise RuntimeError(f"Failed to build interposer: {res_so.stderr}")

        # Compile test harness binary
        cmd_bin = [
            "gcc",
            "-Wall",
            "-Wextra",
            "-o",
            cls.bin_path,
            cls.harness_src,
            "-lwayland-client",
        ]
        res_bin = subprocess.run(cmd_bin, capture_output=True, text=True)
        if res_bin.returncode != 0:
            shutil.rmtree(cls.work_dir)
            raise RuntimeError(f"Failed to build harness: {res_bin.stderr}")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.work_dir, ignore_errors=True)

    def test_tagged_multi_connection_execution(self):
        """Positive test: both connections produce tagged requests and events."""
        env = dict(os.environ)
        env["LD_PRELOAD"] = self.so_path
        env["WAYLAND_DEBUG"] = "1"

        proc = subprocess.run(
            [self.bin_path],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(
            proc.returncode, 0, f"Harness exited with error: {proc.stderr}"
        )

        lines = [line.strip() for line in proc.stderr.splitlines() if line.strip()]
        self.assertGreaterEqual(len(lines), 6, f"Expected at least 6 lines in trace: {lines}")

        distinct_conns = set()

        for line in lines:
            m = TAGGED_LINE_RE.match(line)
            self.assertIsNotNone(
                m, f"Line missing verifiable connection tag: {line}"
            )
            conn_id = int(m.group("conn_id"))
            distinct_conns.add(conn_id)

        # Must have exactly 2 distinct connection IDs
        self.assertEqual(distinct_conns, {1, 2})

        # Verify that both connections issued get_registry and callback.done
        # with identical numeric IDs, but different connection IDs:
        parsed = [
            (int(m.group("conn_id")), m.group("call"))
            for m in [TAGGED_LINE_RE.match(l) for l in lines]
            if m
        ]
        reg_conns = {cid for cid, call in parsed if "wl_display#1.get_registry" in call}
        self.assertEqual(reg_conns, {1, 2}, f"get_registry not found on both conns: {reg_conns}")

        done_conns = {cid for cid, call in parsed if "wl_callback#3.done" in call}
        self.assertEqual(done_conns, {1, 2}, f"callback.done not found on both conns: {done_conns}")

    def test_multithreaded_atomic_tagging_stress(self):
        """Regression test for defect 3: 2 concurrent pthreads x 2000 calls.

        Verifies:
        - Exactly 4000 lines emitted.
        - Exactly 0 double tags (no interleaved writes).
        - Exactly 0 untagged lines.
        - Exactly 2000 lines for conn#1 and exactly 2000 lines for conn#2.
        """
        env = dict(os.environ)
        env["LD_PRELOAD"] = self.so_path
        env["WAYLAND_DEBUG"] = "1"

        proc = subprocess.run(
            [self.bin_path, "--multithreaded", "2000"],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(proc.returncode, 0, f"Multithreaded run failed: {proc.stderr}")

        lines = [line.strip() for line in proc.stderr.splitlines() if line.strip()]
        self.assertEqual(len(lines), 4000, f"Expected 4000 lines, got {len(lines)}")

        double_tag_lines = [l for l in lines if l.count("[conn#") > 1]
        self.assertEqual(
            len(double_tag_lines), 0, f"Found {len(double_tag_lines)} double tag lines: {double_tag_lines[:5]}"
        )

        unmatched_lines = [l for l in lines if not TAGGED_LINE_RE.match(l)]
        self.assertEqual(
            len(unmatched_lines), 0, f"Found {len(unmatched_lines)} untagged/corrupted lines: {unmatched_lines[:5]}"
        )

        conn1_lines = [l for l in lines if l.startswith("[conn#1 ")]
        conn2_lines = [l for l in lines if l.startswith("[conn#2 ")]
        self.assertEqual(len(conn1_lines), 2000, f"Expected 2000 lines for conn#1, got {len(conn1_lines)}")
        self.assertEqual(len(conn2_lines), 2000, f"Expected 2000 lines for conn#2, got {len(conn2_lines)}")

    def test_untagged_execution_negative_control(self):
        """Negative control: running without LD_PRELOAD produces un-tagged lines."""
        env = dict(os.environ)
        env.pop("LD_PRELOAD", None)
        env["WAYLAND_DEBUG"] = "1"

        proc = subprocess.run(
            [self.bin_path],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(proc.returncode, 0)
        lines = [line.strip() for line in proc.stderr.splitlines() if line.strip()]
        self.assertTrue(lines)
        for line in lines:
            self.assertIsNone(
                TAGGED_LINE_RE.match(line),
                f"Unexpected tag without LD_PRELOAD: {line}",
            )


if __name__ == "__main__":
    unittest.main()
