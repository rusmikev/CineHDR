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
            "-ldl",
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

    def test_read_events_loop_tagged_execution(self):
        """Verifies that GTK-style read_events loop is tagged with distinct conns."""
        env = dict(os.environ)
        env["LD_PRELOAD"] = self.so_path
        env["WAYLAND_DEBUG"] = "1"

        proc = subprocess.run(
            [self.bin_path, "--read-events"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(
            proc.returncode, 0, f"Harness exited with error: {proc.stderr}"
        )

        lines = [line.strip() for line in proc.stderr.splitlines() if line.strip()]
        self.assertGreaterEqual(
            len(lines), 6, f"Expected at least 6 lines in trace: {lines}"
        )

        distinct_conns = set()
        for line in lines:
            m = TAGGED_LINE_RE.match(line)
            self.assertIsNotNone(
                m, f"Line missing verifiable connection tag: {line}"
            )
            distinct_conns.add(int(m.group("conn_id")))
            self.assertNotIn("[conn#untracked", line)

        self.assertEqual(distinct_conns, {1, 2})

        parsed = [
            (int(m.group("conn_id")), m.group("call"))
            for m in [TAGGED_LINE_RE.match(l) for l in lines]
            if m
        ]
        done_conns = {cid for cid, call in parsed if "wl_callback#3.done" in call}
        self.assertEqual(
            done_conns, {1, 2}, f"callback.done not found on both conns: {done_conns}"
        )

    def test_ctypes_cdll_dispatch_tagging(self):
        """Verifies that Python ctypes.CDLL dynamically resolving libwayland entry points

        (e.g. background CM probe and output HDR monitor) routes through tagger wrappers.
        """
        env = dict(os.environ)
        env["LD_PRELOAD"] = self.so_path
        env["WAYLAND_DEBUG"] = "1"

        script = """
import socket
import struct
import ctypes

lib = ctypes.CDLL("libwayland-client.so.0")
lib.wl_display_connect_to_fd.restype = ctypes.c_void_p
lib.wl_display_connect_to_fd.argtypes = [ctypes.c_int]
lib.wl_display_disconnect.restype = None
lib.wl_display_disconnect.argtypes = [ctypes.c_void_p]
lib.wl_display_flush.restype = ctypes.c_int
lib.wl_display_flush.argtypes = [ctypes.c_void_p]
lib.wl_display_prepare_read.restype = ctypes.c_int
lib.wl_display_prepare_read.argtypes = [ctypes.c_void_p]
lib.wl_display_read_events.restype = ctypes.c_int
lib.wl_display_read_events.argtypes = [ctypes.c_void_p]
lib.wl_display_dispatch_queue_pending.restype = ctypes.c_int
lib.wl_display_dispatch_queue_pending.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
lib.wl_display_create_queue.restype = ctypes.c_void_p
lib.wl_display_create_queue.argtypes = [ctypes.c_void_p]
lib.wl_event_queue_destroy.restype = None
lib.wl_event_queue_destroy.argtypes = [ctypes.c_void_p]
lib.wl_proxy_set_queue.restype = None
lib.wl_proxy_set_queue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
lib.wl_proxy_marshal_constructor.restype = ctypes.c_void_p
lib.wl_proxy_destroy.restype = None
lib.wl_proxy_destroy.argtypes = [ctypes.c_void_p]

reg_intf = ctypes.c_char.in_dll(lib, "wl_registry_interface")

s1, s2 = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
d = lib.wl_display_connect_to_fd(s1.fileno())
assert d

queue = lib.wl_display_create_queue(ctypes.c_void_p(d))
reg = lib.wl_proxy_marshal_constructor(
    ctypes.c_void_p(d),
    ctypes.c_uint32(1),
    ctypes.byref(reg_intf),
    ctypes.c_uint32(0)
)
lib.wl_proxy_set_queue(ctypes.c_void_p(reg), ctypes.c_void_p(queue))
lib.wl_display_flush(ctypes.c_void_p(d))

# Mock compositor response
req = s2.recv(256)
intf_bytes = b"wl_shm\\x00\\x00"
ev_global = struct.pack("IIII", 2, (28 << 16) | 0, 1, 7) + intf_bytes + struct.pack("I", 1)
s2.sendall(ev_global)

# Read into queue & dispatch on custom queue via ctypes
lib.wl_display_prepare_read(ctypes.c_void_p(d))
lib.wl_display_read_events(ctypes.c_void_p(d))
lib.wl_display_dispatch_queue_pending(ctypes.c_void_p(d), ctypes.c_void_p(queue))

lib.wl_proxy_destroy(ctypes.c_void_p(reg))
lib.wl_event_queue_destroy(ctypes.c_void_p(queue))
lib.wl_display_disconnect(ctypes.c_void_p(d))
s2.close()
"""
        proc = subprocess.run(
            ["python3", "-c", script],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(
            proc.returncode, 0, f"Python ctypes script failed: {proc.stderr}"
        )

        lines = [line.strip() for line in proc.stderr.splitlines() if line.strip()]
        self.assertGreaterEqual(
            len(lines), 2, f"Expected at least 2 lines in trace: {lines}"
        )

        for line in lines:
            m = TAGGED_LINE_RE.match(line)
            self.assertIsNotNone(
                m, f"ctypes line missing verifiable connection tag: {line}"
            )
            self.assertEqual(int(m.group("conn_id")), 1)
            self.assertNotIn("[conn#untracked", line)

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

    def test_two_interposers_rtld_next_chaining(self):
        """Regression test for defect 1: chaining 2 interposers with RTLD_NEXT.

        Verifies that wayland_conn_tagger does not hijack global dlsym, so
        caller-relative RTLD_NEXT semantics are preserved in all preload permutations
        without infinite recursion or crashes.
        """
        so_a = os.path.join(self.work_dir, "libinterposer_a.so")
        so_b = os.path.join(self.work_dir, "libinterposer_b.so")
        bin_app = os.path.join(self.work_dir, "test_interposer_app")

        src_a = """
#define _GNU_SOURCE
#include <stdio.h>
#include <dlfcn.h>
int puts(const char *s) {
    int (*real_puts)(const char *) = (int (*)(const char *))dlsym(RTLD_NEXT, "puts");
    printf("[a] ");
    return real_puts(s);
}
"""
        src_b = """
#define _GNU_SOURCE
#include <stdio.h>
#include <dlfcn.h>
int puts(const char *s) {
    int (*real_puts)(const char *) = (int (*)(const char *))dlsym(RTLD_NEXT, "puts");
    printf("[b] ");
    return real_puts(s);
}
"""
        src_app = """
#include <stdio.h>
int main() {
    puts("chain_test");
    return 0;
}
"""
        subprocess.run(
            ["gcc", "-shared", "-fPIC", "-o", so_a, "-x", "c", "-", "-ldl"],
            input=src_a,
            text=True,
            check=True,
        )
        subprocess.run(
            ["gcc", "-shared", "-fPIC", "-o", so_b, "-x", "c", "-", "-ldl"],
            input=src_b,
            text=True,
            check=True,
        )
        subprocess.run(
            ["gcc", "-o", bin_app, "-x", "c", "-"],
            input=src_app,
            text=True,
            check=True,
        )

        permutations = [
            f"{self.so_path} {so_a} {so_b}",
            f"{so_a} {self.so_path} {so_b}",
            f"{so_a} {so_b} {self.so_path}",
        ]
        for preload in permutations:
            env = dict(os.environ)
            env["LD_PRELOAD"] = preload
            proc = subprocess.run(
                [bin_app], env=env, capture_output=True, text=True, timeout=5
            )
            self.assertEqual(
                proc.returncode,
                0,
                f"Chained interposer failed with LD_PRELOAD={preload}: {proc.stderr}",
            )
            self.assertIn("[a]", proc.stdout)
            self.assertIn("[b]", proc.stdout)
            self.assertIn("chain_test", proc.stdout)

    def test_foreign_library_and_missing_symbol_negative_control(self):
        """Regression test for defect 2: dlsym on foreign library or missing symbol.

        Verifies that:
        1. Accessing Wayland symbols on foreign libraries (e.g. libm) returns NULL / raises AttributeError.
        2. Accessing non-existent symbols on libwayland-client returns NULL / raises AttributeError.
        3. Accessing existing Wayland symbols on libwayland-client succeeds.
        """
        env = dict(os.environ)
        env["LD_PRELOAD"] = self.so_path

        script = """
import ctypes

# 1. Foreign library must NOT return Wayland wrapper
m = ctypes.CDLL("libm.so.6")
try:
    _ = m.wl_display_connect
    raise AssertionError("Unexpectedly found wl_display_connect in libm.so.6")
except AttributeError:
    pass

# 2. Missing symbol on Wayland handle must NOT return a pointer
wl = ctypes.CDLL("libwayland-client.so.0")
try:
    _ = wl.nonexistent_symbol_12345_test
    raise AssertionError("Unexpectedly found nonexistent symbol in libwayland-client")
except AttributeError:
    pass

# 3. Existing wrapped and unwrapped symbols must resolve properly
assert wl.wl_display_connect is not None
assert wl.wl_display_roundtrip_queue is not None
assert wl.wl_display_create_queue is not None
"""
        proc = subprocess.run(
            ["python3", "-c", script],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(
            proc.returncode,
            0,
            f"Foreign library / missing symbol test failed: {proc.stderr}",
        )

    def test_atomic_first_discovery_concurrent_threads(self):
        """Regression test for defect 3: atomic first discovery and complete disconnect.

        Verifies:
        - 16 concurrent threads entering enter_conn_scope on an untracked display
          observe the identical conn_id (no duplicate entries in conns list).
        - Subsequent connection after disconnect gets the next conn_id (conn#2).
        """
        env = dict(os.environ)
        env["LD_PRELOAD"] = self.so_path
        env["WAYLAND_DEBUG"] = "1"

        proc = subprocess.run(
            [self.bin_path, "--atomic-discovery", "16"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(
            proc.returncode, 0, f"Atomic discovery test failed: {proc.stderr}"
        )

        lines = [line.strip() for line in proc.stderr.splitlines() if line.strip()]
        self.assertEqual(
            len(lines), 17, f"Expected 17 lines in trace, got {len(lines)}"
        )

        conn1_lines = [l for l in lines if l.startswith("[conn#1 ")]
        conn2_lines = [l for l in lines if l.startswith("[conn#2 ")]
        self.assertEqual(
            len(conn1_lines),
            16,
            f"Expected 16 lines for conn#1, got {len(conn1_lines)}",
        )
        self.assertEqual(
            len(conn2_lines),
            1,
            f"Expected 1 line for conn#2, got {len(conn2_lines)}",
        )

        unmatched = [l for l in lines if not TAGGED_LINE_RE.match(l)]
        self.assertEqual(len(unmatched), 0, f"Found untagged lines: {unmatched}")

    def test_nonexistent_path_and_alternative_library_and_dlclose_balance(self):
        """Negative controls for nonexistent path, alternative library, and dlopen/dlclose balance.

        Verifies:
        1. dlopen("/nonexistent/libwayland-client.so.0", ...) returns NULL and sets dlerror()
           (and ctypes.CDLL raises OSError).
        2. dlopen("/path/to/alt/libwayland-client.so.0", ...) returns the alternative library's
           own handle without silently replacing it with the shim or system libwayland.
        3. dlopen/dlclose reference counting on the substituted shim handle is balanced,
           and an extra unbalanced dlclose fails with -1 without unloading the shim.
        """
        alt_dir = os.path.join(self.work_dir, "alt_wl")
        os.makedirs(alt_dir, exist_ok=True)
        alt_so = os.path.join(alt_dir, "libwayland-client.so.0")
        alt_src = """
#include <stddef.h>
int alt_wayland_marker(void) { return 777; }
void *wl_display_connect(const char *name) { (void)name; return (void *)0x7770; }
"""
        subprocess.run(
            ["gcc", "-Wall", "-Wextra", "-shared", "-fPIC", "-o", alt_so, "-x", "c", "-"],
            input=alt_src,
            text=True,
            check=True,
        )

        env = dict(os.environ)
        env["LD_PRELOAD"] = self.so_path

        proc_c = subprocess.run(
            [self.bin_path, "--handle-semantics", alt_so],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(
            proc_c.returncode,
            0,
            f"C handle semantics test failed (rc={proc_c.returncode}): {proc_c.stderr}",
        )

        py_script = f"""
import ctypes

try:
    ctypes.CDLL("/nonexistent/libwayland-client.so.0")
    raise AssertionError("Expected OSError for /nonexistent/libwayland-client.so.0")
except OSError:
    pass

alt = ctypes.CDLL({alt_so!r})
assert alt.alt_wayland_marker() == 777
alt.wl_display_connect.restype = ctypes.c_void_p
alt.wl_display_connect.argtypes = [ctypes.c_char_p]
assert alt.wl_display_connect(None) == 0x7770
"""
        proc_py = subprocess.run(
            ["python3", "-c", py_script],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(
            proc_py.returncode,
            0,
            f"Python nonexistent/alternative library test failed: {proc_py.stderr}",
        )

    def test_missing_wrapped_symbol_and_null_real_fn_guard(self):
        """Negative control for absent optional wrapped symbols in loaded libwayland.

        Verifies:
        1. When the loaded libwayland lacks optional wrapped functions (such as
           wl_display_dispatch_queue_timeout / wl_display_dispatch_timeout), dlsym
           on the substituted handle returns NULL with dlerror set (and ctypes raises
           AttributeError).
        2. Direct PLT calls to the missing wrapped symbol safely return -1 without
           calling a NULL real_fn.
        """
        partial_dir = os.path.join(self.work_dir, "partial_wl")
        os.makedirs(partial_dir, exist_ok=True)
        partial_so = os.path.join(partial_dir, "libwayland-client.so.0")
        partial_src = """
#include <stddef.h>
#include <stdint.h>

struct mock_wl_interface {
    const char *name;
    int version;
    int method_count;
    const void *methods;
    int event_count;
    const void *events;
};
const struct mock_wl_interface wl_callback_interface = { "wl_callback", 1, 0, NULL, 0, NULL };
const struct mock_wl_interface wl_registry_interface = { "wl_registry", 1, 0, NULL, 0, NULL };

void *wl_display_connect(const char *name) { (void)name; return (void *)0x1111; }
void *wl_display_connect_to_fd(int fd) { (void)fd; return (void *)0x1112; }
void wl_display_disconnect(void *d) { (void)d; }
int wl_display_get_fd(void *d) { (void)d; return 9; }
int wl_display_dispatch(void *d) { (void)d; return 0; }
int wl_display_dispatch_pending(void *d) { (void)d; return 0; }
int wl_display_roundtrip(void *d) { (void)d; return 0; }
int wl_display_flush(void *d) { (void)d; return 0; }
int wl_display_prepare_read(void *d) { (void)d; return 0; }
int wl_display_prepare_read_queue(void *d, void *q) { (void)d; (void)q; return 0; }
void wl_display_cancel_read(void *d) { (void)d; }
int wl_display_read_events(void *d) { (void)d; return 0; }
void *wl_display_create_queue(void *d) { (void)d; return NULL; }
void wl_event_queue_destroy(void *q) { (void)q; }
int wl_proxy_add_listener(void *p, void (**l)(void), void *data) { (void)p; (void)l; (void)data; return 0; }
void wl_proxy_destroy(void *p) { (void)p; }
uint32_t wl_proxy_get_version(void *p) { (void)p; return 1; }
void *wl_proxy_marshal_flags(void *p, uint32_t op, const void *i, uint32_t v, uint32_t f, ...) {
    (void)p; (void)op; (void)i; (void)v; (void)f; return NULL;
}
"""
        subprocess.run(
            [
                "gcc",
                "-Wall",
                "-Wextra",
                "-shared",
                "-fPIC",
                "-Wl,-soname,libwayland-client.so.0",
                "-o",
                partial_so,
                "-x",
                "c",
                "-",
            ],
            input=partial_src,
            text=True,
            check=True,
        )

        env = dict(os.environ)
        env["LD_PRELOAD"] = f"{self.so_path}:{partial_so}"
        env["LD_BIND_NOW"] = "1"

        proc_c = subprocess.run(
            [self.bin_path, "--missing-symbol"],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(
            proc_c.returncode,
            0,
            f"C missing symbol / NULL real_fn guard test failed (rc={proc_c.returncode}): {proc_c.stderr}",
        )

        py_script = """
import ctypes

wl = ctypes.CDLL("libwayland-client.so.0")
assert wl.wl_display_connect is not None
assert wl.wl_display_dispatch is not None

for missing_sym in ("wl_display_dispatch_queue_timeout", "wl_display_dispatch_timeout"):
    try:
        getattr(wl, missing_sym)
        raise AssertionError(f"Unexpectedly resolved missing wrapped symbol {missing_sym}")
    except AttributeError:
        pass
"""
        proc_py = subprocess.run(
            ["python3", "-c", py_script],
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(
            proc_py.returncode,
            0,
            f"Python missing wrapped symbol test failed: {proc_py.stderr}",
        )

    def test_flatpak_platform_50_preload_loading(self):
        """Verifies loading wayland_conn_tagger inside Flatpak Platform 50 without playback."""
        flatpak_bin = shutil.which("flatpak")
        if not flatpak_bin:
            raise unittest.SkipTest("flatpak binary not available")

        # Check if org.gnome.Platform//50 is installed
        check = subprocess.run(
            [flatpak_bin, "info", "org.gnome.Platform//50"],
            capture_output=True,
            text=True,
        )
        if check.returncode != 0:
            raise unittest.SkipTest("org.gnome.Platform//50 not installed")

        cmd = [
            flatpak_bin,
            "run",
            "--user",
            f"--filesystem={self.work_dir}:ro",
            f"--env=LD_PRELOAD={self.so_path}",
            "--command=python3",
            "org.gnome.Platform//50",
            "-c",
            "import ctypes; lib = ctypes.CDLL('libwayland-client.so.0'); assert lib.wl_display_roundtrip_queue is not None",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        self.assertEqual(
            proc.returncode,
            0,
            f"Flatpak Platform 50 preload test failed: {proc.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
