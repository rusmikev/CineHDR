#include <stdio.h>
#include <sys/socket.h>
#include <unistd.h>
#include <stdint.h>
#include <wayland-client.h>

/*
 * tools/validation/test_conn_tagger_harness.c
 *
 * Autonomous multi-connection Wayland test harness.
 * Uses socketpair + wl_display_connect_to_fd to simulate two distinct Wayland
 * connections in a single process without requiring a display server or GPU.
 *
 * Interleaves requests and events across both connections, intentionally
 * producing identical numeric IDs (wl_display#1, wl_registry#2, wl_callback#3)
 * to verify that the tagging interposer attributes every message correctly.
 */

static void sync_callback(void *data, struct wl_callback *cb, uint32_t serial) {
    (void)data; (void)cb; (void)serial;
}

static const struct wl_callback_listener cb_listener = {
    .done = sync_callback
};

int main(void) {
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
    /* Connection 1: callback#3.done(serial=101) */
    uint32_t ev1[3] = { 3, (12 << 16) | 0, 101 };
    /* Connection 2: callback#3.done(serial=202) */
    uint32_t ev2[3] = { 3, (12 << 16) | 0, 202 };
    write(sv1[1], ev1, sizeof(ev1));
    write(sv2[1], ev2, sizeof(ev2));

    /* 4. Interleaved dispatch: each dispatches its own queue */
    wl_display_dispatch(d1);
    wl_display_dispatch(d2);

    /* Teardown */
    wl_callback_destroy(cb1);
    wl_callback_destroy(cb2);
    wl_display_disconnect(d1);
    wl_display_disconnect(d2);
    close(sv1[1]);
    close(sv2[1]);

    return 0;
}
