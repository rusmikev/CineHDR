#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>
#include <stdint.h>
#include <pthread.h>
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

int main(int argc, char **argv) {
    if (argc >= 2 && strcmp(argv[1], "--multithreaded") == 0) {
        int iters = (argc >= 3) ? atoi(argv[2]) : 2000;
        if (iters <= 0) iters = 2000;
        return run_multithreaded_test(iters);
    }
    if (argc >= 2 && strcmp(argv[1], "--read-events") == 0) {
        return run_read_events_test();
    }
    return run_interleaved_test();
}
