#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>
#include <stdint.h>
#include <pthread.h>
#include <dlfcn.h>
#include <wayland-client.h>

/*
 * tools/validation/test_conn_tagger_harness.c
 *
 * Autonomous multi-connection Wayland test harness.
 * Uses socketpair + wl_display_connect_to_fd to simulate distinct Wayland
 * connections in a single process without requiring a display server or GPU.
 *
 * Modes:
 * 1. Default (no args):
 *    Interleaves requests and events across both connections, intentionally
 *    producing identical numeric IDs (wl_display#1, wl_registry#2, wl_callback#3).
 *    Also verifies nested context restoration across different connections.
 * 2. --multithreaded [count]:
 *    Launches 2 parallel pthreads, each performing [count] calls on its own
 *    connection concurrently, stressing thread-local isolation and atomic
 *    stderr output.
 * 3. --read-events:
 *    Simulates the GTK poll-driven event loop (wl_display_prepare_read,
 *    wl_display_read_events, wl_display_dispatch_pending, wl_display_cancel_read)
 *    across multiple connections.
 * 4. --atomic-discovery [threads]:
 *    Tests concurrent first discovery of an untracked wl_display by multiple
 *    threads entering enter_conn_scope simultaneously, and verifies that
 *    disconnect cleanly unregisters the display.
 * 5. --handle-semantics <alt_lib_path>:
 *    Verifies dlopen/dlclose handle semantics:
 *    - nonexistent path fails with NULL and sets dlerror();
 *    - alternative libwayland-client.so.0 path returns its own handle, not
 *      the shim or system library;
 *    - system SONAME returns the substituted shim handle with balanced
 *      dlopen/dlclose reference counting.
 * 6. --missing-symbol:
 *    When preloaded against a partial libwayland lacking
 *    wl_display_dispatch_queue_timeout, verifies that dlsym on the substituted
 *    handle returns NULL (with dlerror) and direct PLT invocation safely
 *    returns -1 without calling a NULL real_fn.
 */

static void sync_callback(void *data, struct wl_callback *cb, uint32_t serial) {
    (void)data; (void)cb; (void)serial;
}

static const struct wl_callback_listener cb_listener = {
    .done = sync_callback
};

struct thread_worker_arg {
    int sv[2];
    int iterations;
};

static void *thread_worker(void *arg) {
    struct thread_worker_arg *targ = (struct thread_worker_arg *)arg;
    struct wl_display *d = wl_display_connect_to_fd(targ->sv[0]);
    if (!d) return NULL;

    for (int i = 0; i < targ->iterations; i++) {
        struct wl_registry *r = wl_display_get_registry(d);
        (void)r;
    }

    wl_display_disconnect(d);
    return NULL;
}

static int run_multithreaded_test(int iterations) {
    struct thread_worker_arg args[2];
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, args[0].sv) < 0) return 1;
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, args[1].sv) < 0) return 1;
    args[0].iterations = iterations;
    args[1].iterations = iterations;

    pthread_t t1, t2;
    pthread_create(&t1, NULL, thread_worker, &args[0]);
    pthread_create(&t2, NULL, thread_worker, &args[1]);

    pthread_join(t1, NULL);
    pthread_join(t2, NULL);

    close(args[0].sv[1]);
    close(args[1].sv[1]);
    return 0;
}

static int run_interleaved_test(void) {
    int sv1[2], sv2[2];
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, sv1) < 0) {
        perror("socketpair 1");
        return 1;
    }
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, sv2) < 0) {
        perror("socketpair 2");
        return 1;
    }

    struct wl_display *d1 = wl_display_connect_to_fd(sv1[0]);
    struct wl_display *d2 = wl_display_connect_to_fd(sv2[0]);
    if (!d1 || !d2) {
        fprintf(stderr, "Failed to connect displays\n");
        return 2;
    }

    /* 1. Interleaved requests: both allocate wl_registry#2 */
    struct wl_registry *r1 = wl_display_get_registry(d1);
    struct wl_registry *r2 = wl_display_get_registry(d2);
    (void)r1; (void)r2;

    /* 2. Interleaved requests: both allocate wl_callback#3 */
    struct wl_callback *cb1 = wl_display_sync(d1);
    struct wl_callback *cb2 = wl_display_sync(d2);
    wl_callback_add_listener(cb1, &cb_listener, NULL);
    wl_callback_add_listener(cb2, &cb_listener, NULL);

    wl_display_flush(d1);
    wl_display_flush(d2);

    /* Consume requests on mock server side */
    char buf[256];
    read(sv1[1], buf, sizeof(buf));
    read(sv2[1], buf, sizeof(buf));

    /* 3. Server sends events back on both connections */
    uint32_t ev1[3] = { 3, (12 << 16) | 0, 101 };
    uint32_t ev2[3] = { 3, (12 << 16) | 0, 202 };
    write(sv1[1], ev1, sizeof(ev1));
    write(sv2[1], ev2, sizeof(ev2));

    /* 4. Interleaved dispatch: each dispatches its own queue */
    wl_display_dispatch(d1);
    wl_display_dispatch(d2);

    /* 5. Nested context verification: request on d2 while handling d1 */
    struct wl_callback *nested_cb = wl_display_sync(d2);
    (void)nested_cb;
    struct wl_callback *post_nested_cb = wl_display_sync(d1);
    (void)post_nested_cb;

    /* Teardown */
    wl_callback_destroy(cb1);
    wl_callback_destroy(cb2);
    wl_display_disconnect(d1);
    wl_display_disconnect(d2);
    close(sv1[1]);
    close(sv2[1]);

    return 0;
}

static int run_read_events_test(void) {
    int sv1[2], sv2[2];
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, sv1) < 0) {
        perror("socketpair 1");
        return 1;
    }
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, sv2) < 0) {
        perror("socketpair 2");
        return 1;
    }

    struct wl_display *d1 = wl_display_connect_to_fd(sv1[0]);
    struct wl_display *d2 = wl_display_connect_to_fd(sv2[0]);
    if (!d1 || !d2) {
        fprintf(stderr, "Failed to connect displays\n");
        return 2;
    }

    /* Allocate wl_registry#2 on both connections */
    struct wl_registry *r1 = wl_display_get_registry(d1);
    struct wl_registry *r2 = wl_display_get_registry(d2);
    (void)r1; (void)r2;

    /* Allocate wl_callback#3 on both connections */
    struct wl_callback *cb1 = wl_display_sync(d1);
    struct wl_callback *cb2 = wl_display_sync(d2);
    wl_callback_add_listener(cb1, &cb_listener, NULL);
    wl_callback_add_listener(cb2, &cb_listener, NULL);

    wl_display_flush(d1);
    wl_display_flush(d2);

    /* Consume requests on mock server side */
    char buf[256];
    if (read(sv1[1], buf, sizeof(buf)) < 0) {}
    if (read(sv2[1], buf, sizeof(buf)) < 0) {}

    /* Server sends events back on both connections */
    uint32_t ev1[3] = { 3, (12 << 16) | 0, 101 };
    uint32_t ev2[3] = { 3, (12 << 16) | 0, 202 };
    if (write(sv1[1], ev1, sizeof(ev1)) < 0) {}
    if (write(sv2[1], ev2, sizeof(ev2)) < 0) {}

    /* Verify GTK-style read_events loop on connection 1 */
    if (wl_display_prepare_read(d1) != 0) return 3;
    if (wl_display_read_events(d1) != 0) return 4;
    wl_display_dispatch_pending(d1);

    /* Verify GTK-style read_events loop on connection 2 */
    if (wl_display_prepare_read(d2) != 0) return 5;
    if (wl_display_read_events(d2) != 0) return 6;
    wl_display_dispatch_pending(d2);

    /* Verify cancel_read and prepare_read_queue */
    struct wl_event_queue *q = wl_display_create_queue(d1);
    if (wl_display_prepare_read_queue(d1, q) == 0) {
        wl_display_cancel_read(d1);
    }
    wl_event_queue_destroy(q);

    wl_callback_destroy(cb1);
    wl_callback_destroy(cb2);
    wl_display_disconnect(d1);
    wl_display_disconnect(d2);
    close(sv1[1]);
    close(sv2[1]);

    return 0;
}

struct discovery_worker_arg {
    struct wl_display *display;
};

static void *discovery_worker(void *arg) {
    struct discovery_worker_arg *targ = (struct discovery_worker_arg *)arg;
    struct wl_callback *cb = wl_display_sync(targ->display);
    (void)cb;
    return NULL;
}

static int run_atomic_discovery_test(int num_threads) {
    if (num_threads <= 0 || num_threads > 100) num_threads = 16;
    int sv[2];
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, sv) < 0) return 1;

    /* Connect directly via underlying libwayland, bypassing our connect wrapper
     * so that the display is initially unregistered in the tagger. */
    struct wl_display *(*real_connect)(int) =
        (struct wl_display *(*)(int))dlsym(RTLD_NEXT, "wl_display_connect_to_fd");
    if (!real_connect) return 2;

    struct wl_display *d = real_connect(sv[0]);
    if (!d) return 3;

    pthread_t threads[100];
    struct discovery_worker_arg args[100];
    for (int i = 0; i < num_threads; i++) {
        args[i].display = d;
        pthread_create(&threads[i], NULL, discovery_worker, &args[i]);
    }
    for (int i = 0; i < num_threads; i++) {
        pthread_join(threads[i], NULL);
    }

    wl_display_disconnect(d);
    close(sv[1]);

    /* Secondary display: after disconnect of d, verify next connection gets conn#2 */
    int sv2[2];
    if (socketpair(AF_UNIX, SOCK_STREAM, 0, sv2) < 0) return 4;
    struct wl_display *d2 = real_connect(sv2[0]);
    if (!d2) return 5;

    struct wl_callback *cb2 = wl_display_sync(d2);
    (void)cb2;
    wl_display_disconnect(d2);
    close(sv2[1]);

    return 0;
}

static int run_handle_semantics_test(const char *alt_lib_path) {
    /* 1. Nonexistent path with libwayland-client.so.0 basename must fail */
    dlerror();
    void *bad_h = dlopen("/nonexistent/libwayland-client.so.0", RTLD_NOW);
    const char *bad_err = dlerror();
    if (bad_h != NULL || bad_err == NULL) {
        fprintf(stderr, "Expected NULL handle and non-NULL dlerror for nonexistent path\n");
        return 10;
    }

    /* 2. System SONAME must return the substituted tagger handle */
    void *sub_h1 = dlopen("libwayland-client.so.0", RTLD_NOW);
    void *sub_h2 = dlopen("libwayland-client.so.0", RTLD_LAZY);
    if (!sub_h1 || !sub_h2 || sub_h1 != sub_h2) {
        fprintf(stderr, "Expected identical non-NULL substituted handles\n");
        return 11;
    }
    void *sub_connect = dlsym(sub_h1, "wl_display_connect");
    Dl_info sub_info;
    if (!sub_connect || !dladdr(sub_connect, &sub_info) || !sub_info.dli_fname ||
        strstr(sub_info.dli_fname, "libwayland_conn_tag.so") == NULL) {
        fprintf(stderr, "Expected wl_display_connect on substituted handle to come from tagger\n");
        return 12;
    }

    /* 3. Alternative library path must NOT be replaced with tagger or system lib */
    if (alt_lib_path && alt_lib_path[0] != '\0') {
        void *alt_h = dlopen(alt_lib_path, RTLD_NOW | RTLD_LOCAL);
        if (!alt_h || alt_h == sub_h1) {
            fprintf(stderr, "Alternative library was missing or replaced by substituted handle\n");
            return 13;
        }
        int (*alt_marker)(void) = (int (*)(void))dlsym(alt_h, "alt_wayland_marker");
        void *alt_connect = dlsym(alt_h, "wl_display_connect");
        Dl_info alt_info;
        if (!alt_marker || alt_marker() != 777 || !alt_connect ||
            alt_connect == sub_connect || !dladdr(alt_connect, &alt_info) ||
            !alt_info.dli_fname || strcmp(alt_info.dli_fname, alt_lib_path) != 0) {
            fprintf(stderr, "Alternative library symbols did not resolve from %s\n", alt_lib_path);
            return 14;
        }
        if (dlclose(alt_h) != 0) {
            fprintf(stderr, "dlclose on alternative handle failed\n");
            return 15;
        }
    }

    /* 4. Balanced dlclose on substituted handle + extra unbalanced dlclose protection */
    if (dlclose(sub_h1) != 0) {
        fprintf(stderr, "First balanced dlclose on substituted handle failed\n");
        return 16;
    }
    if (dlclose(sub_h2) != 0) {
        fprintf(stderr, "Second balanced dlclose on substituted handle failed\n");
        return 17;
    }
    if (dlclose(sub_h1) == 0) {
        fprintf(stderr, "Extra unbalanced dlclose on substituted handle unexpectedly returned 0\n");
        return 18;
    }
    /* Tagger remains mapped and usable after balanced dlclose */
    if (dlsym(sub_h1, "wl_display_connect") != sub_connect) {
        fprintf(stderr, "Tagger handle unusable after dlclose\n");
        return 19;
    }
    return 0;
}

static int run_missing_symbol_test(void) {
    void *sub_h = dlopen("libwayland-client.so.0", RTLD_NOW);
    if (!sub_h) {
        fprintf(stderr, "dlopen(libwayland-client.so.0) failed\n");
        return 20;
    }
    void *connect_fn = dlsym(sub_h, "wl_display_connect");
    Dl_info info;
    if (!connect_fn || !dladdr(connect_fn, &info) || !info.dli_fname ||
        strstr(info.dli_fname, "libwayland_conn_tag.so") == NULL) {
        fprintf(stderr, "wl_display_connect did not resolve to tagger\n");
        return 21;
    }

    /* Optional wrapped symbol absent in loaded mock libwayland must return NULL via dlsym */
    dlerror();
    void *opt_fn = dlsym(sub_h, "wl_display_dispatch_queue_timeout");
    const char *err = dlerror();
    if (opt_fn != NULL || err == NULL) {
        fprintf(stderr, "Expected NULL and dlerror for absent wrapped symbol wl_display_dispatch_queue_timeout\n");
        return 22;
    }

    /* Direct PLT call to absent wrapped symbol must not call NULL real_fn */
    int rc = wl_display_dispatch_queue_timeout(NULL, NULL, NULL);
    if (rc != -1) {
        fprintf(stderr, "Expected -1 from guarded wrapper when real_fn is NULL, got %d\n", rc);
        return 23;
    }

    if (dlclose(sub_h) != 0) {
        return 24;
    }
    return 0;
}

int main(int argc, char **argv) {
    if (argc >= 2 && strcmp(argv[1], "--multithreaded") == 0) {
        int iters = (argc >= 3) ? atoi(argv[2]) : 2000;
        if (iters <= 0) iters = 2000;
        return run_multithreaded_test(iters);
    }
    if (argc >= 2 && strcmp(argv[1], "--read-events") == 0) {
        return run_read_events_test();
    }
    if (argc >= 2 && strcmp(argv[1], "--atomic-discovery") == 0) {
        int threads = (argc >= 3) ? atoi(argv[2]) : 16;
        return run_atomic_discovery_test(threads);
    }
    if (argc >= 2 && strcmp(argv[1], "--handle-semantics") == 0) {
        const char *alt_path = (argc >= 3) ? argv[2] : "";
        return run_handle_semantics_test(alt_path);
    }
    if (argc >= 2 && strcmp(argv[1], "--missing-symbol") == 0) {
        return run_missing_symbol_test();
    }
    return run_interleaved_test();
}
