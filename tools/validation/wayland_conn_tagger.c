#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <dlfcn.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <pthread.h>
#include <wayland-client.h>

/*
 * tools/validation/wayland_conn_tagger.c
 *
 * Reproducible LD_PRELOAD interposer for Wayland client debugging.
 *
 * Problem:
 * In multi-connection Wayland processes (such as mpv with vo=gpu-next where
 * Mesa Vulkan WSI and mpv create separate wl_display connections), both
 * connections create identical numeric IDs (e.g. wl_display#1, wl_registry#2).
 * Standard WAYLAND_DEBUG=1 prints all messages to stderr without connection
 * attribution, making it impossible for automated validators to distinguish
 * separate connections without risking collision and false validation.
 *
 * Solution:
 * This library intercepts:
 * 1. Connection lifecycle (wl_display_connect, wl_display_connect_to_fd, wl_display_disconnect)
 *    to register each display and assign a unique conn_id and socket fd.
 * 2. All public request marshalling entry points (wl_proxy_marshal_array_flags)
 *    to establish connection context during request formatting.
 * 3. All public event dispatch entry points (wl_display_dispatch*, wl_display_roundtrip*)
 *    to establish connection context during incoming event dispatching across queues and threads.
 * 4. Event reading and loop synchronization entry points (wl_display_read_events,
 *    wl_display_prepare_read*, wl_display_cancel_read, wl_display_flush) used by GTK's
 *    poll source (gdkeventsource) and multi-threaded event readers.
 * 5. Dynamic symbol resolution (dlsym) to route dlopen/ctypes consumers (e.g. CineHDR's
 *    Python ctypes background probes) directly to wrapped functions.
 * 6. Stderr formatting routines (__fprintf_chk, vfprintf, fprintf, fputs) called by
 *    libwayland's internal wl_closure_print to prefix each message with:
 *    [conn#<id> fd#<fd>]
 *
 * Atomicity:
 * To avoid race conditions between parallel threads where prefix and message writes
 * could interleave (producing double tags and untagged lines), stream output is
 * locked using flockfile(stream)/funlockfile(stream) and emitted atomically.
 *
 * If a Wayland closure is emitted outside any tracked connection context, it is
 * explicitly prefixed with [conn#untracked fd#?], enabling fail-closed rejection.
 */

struct conn_info {
    struct wl_display *display;
    int conn_id;
    int fd;
    struct conn_info *next;
};

static struct conn_info *conns = NULL;
static pthread_mutex_t conns_lock = PTHREAD_MUTEX_INITIALIZER;
static int next_conn_id = 1;

static __thread int current_conn_id = 0;
static __thread int current_conn_fd = -1;
static __thread bool in_format_hook = false;

struct conn_scope {
    int prev_id;
    int prev_fd;
};

static void register_display(struct wl_display *display, int fd) {
    if (!display) return;
    pthread_mutex_lock(&conns_lock);
    struct conn_info *ci = (struct conn_info *)malloc(sizeof(*ci));
    if (ci) {
        ci->display = display;
        ci->conn_id = next_conn_id++;
        ci->fd = fd;
        ci->next = conns;
        conns = ci;
    }
    pthread_mutex_unlock(&conns_lock);
}

static void unregister_display(struct wl_display *display) {
    if (!display) return;
    pthread_mutex_lock(&conns_lock);
    struct conn_info **curr = &conns;
    while (*curr) {
        if ((*curr)->display == display) {
            struct conn_info *to_free = *curr;
            *curr = (*curr)->next;
            free(to_free);
            break;
        }
        curr = &(*curr)->next;
    }
    pthread_mutex_unlock(&conns_lock);
}

static struct conn_info *find_conn(struct wl_display *display) {
    if (!display) return NULL;
    pthread_mutex_lock(&conns_lock);
    for (struct conn_info *c = conns; c; c = c->next) {
        if (c->display == display) {
            pthread_mutex_unlock(&conns_lock);
            return c;
        }
    }
    pthread_mutex_unlock(&conns_lock);
    return NULL;
}

static inline struct conn_scope enter_conn_scope(struct wl_display *display) {
    struct conn_scope scope = { current_conn_id, current_conn_fd };
    if (!display) {
        current_conn_id = -1;
        current_conn_fd = -1;
        return scope;
    }
    struct conn_info *ci = find_conn(display);
    if (!ci) {
        /* Auto-register display if created before tagger or via non-intercepted path */
        static int (*real_get_fd)(struct wl_display *) = NULL;
        if (!real_get_fd) {
            real_get_fd = (int (*)(struct wl_display *))dlsym(RTLD_NEXT, "wl_display_get_fd");
        }
        if (real_get_fd) {
            int fd = real_get_fd(display);
            if (fd >= 0) {
                register_display(display, fd);
                ci = find_conn(display);
            }
        }
    }
    if (ci) {
        current_conn_id = ci->conn_id;
        current_conn_fd = ci->fd;
    } else {
        current_conn_id = -1;
        current_conn_fd = -1;
    }
    return scope;
}

static inline void leave_conn_scope(struct conn_scope scope) {
    current_conn_id = scope.prev_id;
    current_conn_fd = scope.prev_fd;
}

/* =========================================================================
 * Connection Lifecycle Interception
 * ========================================================================= */

struct wl_display *wl_display_connect_to_fd(int fd) {
    struct wl_display *(*real_fn)(int) = (struct wl_display *(*)(int))dlsym(RTLD_NEXT, "wl_display_connect_to_fd");
    struct wl_display *d = real_fn(fd);
    if (d) register_display(d, fd);
    return d;
}

struct wl_display *wl_display_connect(const char *name) {
    struct wl_display *(*real_fn)(const char *) = (struct wl_display *(*)(const char *))dlsym(RTLD_NEXT, "wl_display_connect");
    struct wl_display *d = real_fn(name);
    if (d) {
        int fd = wl_display_get_fd(d);
        register_display(d, fd);
    }
    return d;
}

void wl_display_disconnect(struct wl_display *display) {
    void (*real_fn)(struct wl_display *) = (void (*)(struct wl_display *))dlsym(RTLD_NEXT, "wl_display_disconnect");
    unregister_display(display);
    real_fn(display);
}

/* =========================================================================
 * Request Marshalling Interception
 * In libwayland-client, all requests pass through wl_proxy_marshal_array_flags.
 * ========================================================================= */

struct wl_proxy *wl_proxy_marshal_array_flags(
    struct wl_proxy *proxy, uint32_t opcode,
    const struct wl_interface *interface, uint32_t version,
    uint32_t flags, union wl_argument *args)
{
    static struct wl_proxy *(*real_fn)(
        struct wl_proxy *, uint32_t, const struct wl_interface *, uint32_t, uint32_t, union wl_argument *) = NULL;
    if (!real_fn) {
        real_fn = (struct wl_proxy *(*)(
            struct wl_proxy *, uint32_t, const struct wl_interface *, uint32_t, uint32_t, union wl_argument *))
            dlsym(RTLD_NEXT, "wl_proxy_marshal_array_flags");
    }
    struct wl_display *disp = proxy ? wl_proxy_get_display(proxy) : NULL;
    struct conn_scope scope = enter_conn_scope(disp);
    struct wl_proxy *ret = real_fn(proxy, opcode, interface, version, flags, args);
    leave_conn_scope(scope);
    return ret;
}

/* =========================================================================
 * Event Dispatching Interception
 * Covers all 10 public dispatch entry points in Wayland 1.26.0.
 * ========================================================================= */

int wl_display_dispatch_queue(struct wl_display *display, struct wl_event_queue *queue) {
    static int (*real_fn)(struct wl_display *, struct wl_event_queue *) = NULL;
    if (!real_fn) real_fn = (int (*)(struct wl_display *, struct wl_event_queue *))dlsym(RTLD_NEXT, "wl_display_dispatch_queue");
    struct conn_scope scope = enter_conn_scope(display);
    int ret = real_fn(display, queue);
    leave_conn_scope(scope);
    return ret;
}

int wl_display_dispatch(struct wl_display *display) {
    static int (*real_fn)(struct wl_display *) = NULL;
    if (!real_fn) real_fn = (int (*)(struct wl_display *))dlsym(RTLD_NEXT, "wl_display_dispatch");
    struct conn_scope scope = enter_conn_scope(display);
    int ret = real_fn(display);
    leave_conn_scope(scope);
    return ret;
}

int wl_display_dispatch_queue_pending(struct wl_display *display, struct wl_event_queue *queue) {
    static int (*real_fn)(struct wl_display *, struct wl_event_queue *) = NULL;
    if (!real_fn) real_fn = (int (*)(struct wl_display *, struct wl_event_queue *))dlsym(RTLD_NEXT, "wl_display_dispatch_queue_pending");
    struct conn_scope scope = enter_conn_scope(display);
    int ret = real_fn(display, queue);
    leave_conn_scope(scope);
    return ret;
}

int wl_display_dispatch_pending(struct wl_display *display) {
    static int (*real_fn)(struct wl_display *) = NULL;
    if (!real_fn) real_fn = (int (*)(struct wl_display *))dlsym(RTLD_NEXT, "wl_display_dispatch_pending");
    struct conn_scope scope = enter_conn_scope(display);
    int ret = real_fn(display);
    leave_conn_scope(scope);
    return ret;
}

int wl_display_dispatch_queue_pending_single(struct wl_display *display, struct wl_event_queue *queue) {
    static int (*real_fn)(struct wl_display *, struct wl_event_queue *) = NULL;
    if (!real_fn) real_fn = (int (*)(struct wl_display *, struct wl_event_queue *))dlsym(RTLD_NEXT, "wl_display_dispatch_queue_pending_single");
    struct conn_scope scope = enter_conn_scope(display);
    int ret = real_fn(display, queue);
    leave_conn_scope(scope);
    return ret;
}

int wl_display_dispatch_pending_single(struct wl_display *display) {
    static int (*real_fn)(struct wl_display *) = NULL;
    if (!real_fn) real_fn = (int (*)(struct wl_display *))dlsym(RTLD_NEXT, "wl_display_dispatch_pending_single");
    struct conn_scope scope = enter_conn_scope(display);
    int ret = real_fn(display);
    leave_conn_scope(scope);
    return ret;
}

int wl_display_dispatch_queue_timeout(struct wl_display *display, struct wl_event_queue *queue, const struct timespec *deadline) {
    static int (*real_fn)(struct wl_display *, struct wl_event_queue *, const struct timespec *) = NULL;
    if (!real_fn) real_fn = (int (*)(struct wl_display *, struct wl_event_queue *, const struct timespec *))dlsym(RTLD_NEXT, "wl_display_dispatch_queue_timeout");
    struct conn_scope scope = enter_conn_scope(display);
    int ret = real_fn(display, queue, deadline);
    leave_conn_scope(scope);
    return ret;
}

int wl_display_dispatch_timeout(struct wl_display *display, const struct timespec *deadline) {
    static int (*real_fn)(struct wl_display *, const struct timespec *) = NULL;
    if (!real_fn) real_fn = (int (*)(struct wl_display *, const struct timespec *))dlsym(RTLD_NEXT, "wl_display_dispatch_timeout");
    struct conn_scope scope = enter_conn_scope(display);
    int ret = real_fn(display, deadline);
    leave_conn_scope(scope);
    return ret;
}

int wl_display_roundtrip_queue(struct wl_display *display, struct wl_event_queue *queue) {
    static int (*real_fn)(struct wl_display *, struct wl_event_queue *) = NULL;
    if (!real_fn) real_fn = (int (*)(struct wl_display *, struct wl_event_queue *))dlsym(RTLD_NEXT, "wl_display_roundtrip_queue");
    struct conn_scope scope = enter_conn_scope(display);
    int ret = real_fn(display, queue);
    leave_conn_scope(scope);
    return ret;
}

int wl_display_roundtrip(struct wl_display *display) {
    static int (*real_fn)(struct wl_display *) = NULL;
    if (!real_fn) real_fn = (int (*)(struct wl_display *))dlsym(RTLD_NEXT, "wl_display_roundtrip");
    struct conn_scope scope = enter_conn_scope(display);
    int ret = real_fn(display);
    leave_conn_scope(scope);
    return ret;
}

/* =========================================================================
 * Event Reading & Display Loop Interception
 * Covers GTK event loops (gdkeventsource), explicit polling, and socket reading.
 * ========================================================================= */

int wl_display_flush(struct wl_display *display) {
    static int (*real_fn)(struct wl_display *) = NULL;
    if (!real_fn) real_fn = (int (*)(struct wl_display *))dlsym(RTLD_NEXT, "wl_display_flush");
    struct conn_scope scope = enter_conn_scope(display);
    int ret = real_fn ? real_fn(display) : -1;
    leave_conn_scope(scope);
    return ret;
}

int wl_display_prepare_read(struct wl_display *display) {
    static int (*real_fn)(struct wl_display *) = NULL;
    if (!real_fn) real_fn = (int (*)(struct wl_display *))dlsym(RTLD_NEXT, "wl_display_prepare_read");
    struct conn_scope scope = enter_conn_scope(display);
    int ret = real_fn ? real_fn(display) : -1;
    leave_conn_scope(scope);
    return ret;
}

int wl_display_prepare_read_queue(struct wl_display *display, struct wl_event_queue *queue) {
    static int (*real_fn)(struct wl_display *, struct wl_event_queue *) = NULL;
    if (!real_fn) real_fn = (int (*)(struct wl_display *, struct wl_event_queue *))dlsym(RTLD_NEXT, "wl_display_prepare_read_queue");
    struct conn_scope scope = enter_conn_scope(display);
    int ret = real_fn ? real_fn(display, queue) : -1;
    leave_conn_scope(scope);
    return ret;
}

void wl_display_cancel_read(struct wl_display *display) {
    static void (*real_fn)(struct wl_display *) = NULL;
    if (!real_fn) real_fn = (void (*)(struct wl_display *))dlsym(RTLD_NEXT, "wl_display_cancel_read");
    struct conn_scope scope = enter_conn_scope(display);
    if (real_fn) real_fn(display);
    leave_conn_scope(scope);
}

int wl_display_read_events(struct wl_display *display) {
    static int (*real_fn)(struct wl_display *) = NULL;
    if (!real_fn) real_fn = (int (*)(struct wl_display *))dlsym(RTLD_NEXT, "wl_display_read_events");
    struct conn_scope scope = enter_conn_scope(display);
    int ret = real_fn ? real_fn(display) : -1;
    leave_conn_scope(scope);
    return ret;
}

/* =========================================================================
 * Dynamic Symbol Resolution Interception (dlsym)
 * Ensures that callers loading libwayland-client via dlopen (e.g. Python ctypes)
 * obtain pointers to our wrapped functions instead of bypassing LD_PRELOAD.
 * ========================================================================= */

static void *(*real_dlsym)(void *, const char *) = NULL;

static void init_real_dlsym(void) {
    if (!real_dlsym) {
        real_dlsym = (void *(*)(void *, const char *))dlvsym(RTLD_NEXT, "dlsym", "GLIBC_2.2.5");
        if (!real_dlsym) {
            real_dlsym = (void *(*)(void *, const char *))dlvsym(RTLD_NEXT, "dlsym", "GLIBC_2.34");
        }
    }
}

__attribute__((constructor))
static void conn_tagger_init(void) {
    init_real_dlsym();
}

void *dlsym(void *handle, const char *symbol) {
    if (!real_dlsym) init_real_dlsym();

    /* Direct delegation when resolving real symbols inside tagger wrappers */
    if (handle == RTLD_NEXT) {
        return real_dlsym(handle, symbol);
    }

    if (strcmp(symbol, "wl_display_connect") == 0) return (void *)wl_display_connect;
    if (strcmp(symbol, "wl_display_connect_to_fd") == 0) return (void *)wl_display_connect_to_fd;
    if (strcmp(symbol, "wl_display_disconnect") == 0) return (void *)wl_display_disconnect;
    if (strcmp(symbol, "wl_proxy_marshal_array_flags") == 0) return (void *)wl_proxy_marshal_array_flags;
    if (strcmp(symbol, "wl_display_dispatch_queue") == 0) return (void *)wl_display_dispatch_queue;
    if (strcmp(symbol, "wl_display_dispatch") == 0) return (void *)wl_display_dispatch;
    if (strcmp(symbol, "wl_display_dispatch_queue_pending") == 0) return (void *)wl_display_dispatch_queue_pending;
    if (strcmp(symbol, "wl_display_dispatch_pending") == 0) return (void *)wl_display_dispatch_pending;
    if (strcmp(symbol, "wl_display_dispatch_queue_pending_single") == 0) return (void *)wl_display_dispatch_queue_pending_single;
    if (strcmp(symbol, "wl_display_dispatch_pending_single") == 0) return (void *)wl_display_dispatch_pending_single;
    if (strcmp(symbol, "wl_display_dispatch_queue_timeout") == 0) return (void *)wl_display_dispatch_queue_timeout;
    if (strcmp(symbol, "wl_display_dispatch_timeout") == 0) return (void *)wl_display_dispatch_timeout;
    if (strcmp(symbol, "wl_display_roundtrip_queue") == 0) return (void *)wl_display_roundtrip_queue;
    if (strcmp(symbol, "wl_display_roundtrip") == 0) return (void *)wl_display_roundtrip;
    if (strcmp(symbol, "wl_display_flush") == 0) return (void *)wl_display_flush;
    if (strcmp(symbol, "wl_display_prepare_read") == 0) return (void *)wl_display_prepare_read;
    if (strcmp(symbol, "wl_display_prepare_read_queue") == 0) return (void *)wl_display_prepare_read_queue;
    if (strcmp(symbol, "wl_display_cancel_read") == 0) return (void *)wl_display_cancel_read;
    if (strcmp(symbol, "wl_display_read_events") == 0) return (void *)wl_display_read_events;

    return real_dlsym(handle, symbol);
}

/* =========================================================================
 * Stderr Formatting Interception
 * wl_closure_print writes via open_memstream + fprintf(stderr, "%s", buffer).
 * On fortified glibc, fprintf is emitted as __fprintf_chk.
 * All stderr writes are wrapped in flockfile/funlockfile to ensure atomic
 * emission of the [conn#... fd#...] tag and the message without thread race.
 * ========================================================================= */

static bool is_wayland_debug_line(const char *buf) {
    if (!buf) return false;
    const char *p = buf;
    if (p[0] == '\033' && p[1] == '[') {
        const char *m = strchr(p, 'm');
        if (m) p = m + 1;
    }
    if (p[0] != '[') return false;
    const char *hash = strchr(p, '#');
    if (!hash) return false;
    const char *dot = strchr(hash, '.');
    if (!dot) return false;
    const char *paren = strchr(dot, '(');
    return paren != NULL;
}

static void format_and_print_atomic(FILE *stream, const char *msg) {
    static int (*real_fputs)(const char *, FILE *) = NULL;
    if (!real_fputs) real_fputs = (int (*)(const char *, FILE *))dlsym(RTLD_NEXT, "fputs");

    char tag_buf[64];
    if (current_conn_id > 0 && current_conn_fd >= 0) {
        snprintf(tag_buf, sizeof(tag_buf), "[conn#%d fd#%d] ", current_conn_id, current_conn_fd);
    } else {
        snprintf(tag_buf, sizeof(tag_buf), "[conn#untracked fd#?] ");
    }

    flockfile(stream);
    real_fputs(tag_buf, stream);
    real_fputs(msg, stream);
    funlockfile(stream);
}

int __fprintf_chk(FILE *stream, int flag, const char *format, ...) {
    static int (*real_vfprintf)(FILE *, const char *, va_list) = NULL;
    if (!real_vfprintf) real_vfprintf = (int (*)(FILE *, const char *, va_list))dlsym(RTLD_NEXT, "vfprintf");

    va_list ap;
    va_start(ap, format);
    int ret = 0;
    if (!in_format_hook && stream == stderr && strcmp(format, "%s") == 0) {
        in_format_hook = true;
        va_list ap_peek;
        va_copy(ap_peek, ap);
        const char *s = va_arg(ap_peek, const char *);
        if (is_wayland_debug_line(s)) {
            format_and_print_atomic(stream, s);
            va_end(ap_peek);
            in_format_hook = false;
            va_end(ap);
            return (int)strlen(s);
        }
        va_end(ap_peek);
        in_format_hook = false;
    }
    flockfile(stream);
    ret = real_vfprintf(stream, format, ap);
    funlockfile(stream);
    va_end(ap);
    (void)flag;
    return ret;
}

int __vfprintf_chk(FILE *stream, int flag, const char *format, va_list ap) {
    static int (*real_vfprintf)(FILE *, const char *, va_list) = NULL;
    if (!real_vfprintf) real_vfprintf = (int (*)(FILE *, const char *, va_list))dlsym(RTLD_NEXT, "vfprintf");

    if (!in_format_hook && stream == stderr && strcmp(format, "%s") == 0) {
        in_format_hook = true;
        va_list ap_peek;
        va_copy(ap_peek, ap);
        const char *s = va_arg(ap_peek, const char *);
        if (is_wayland_debug_line(s)) {
            format_and_print_atomic(stream, s);
            va_end(ap_peek);
            in_format_hook = false;
            return (int)strlen(s);
        }
        va_end(ap_peek);
        in_format_hook = false;
    }
    flockfile(stream);
    int ret = real_vfprintf(stream, format, ap);
    funlockfile(stream);
    (void)flag;
    return ret;
}

int fprintf(FILE *stream, const char *format, ...) {
    static int (*real_vfprintf)(FILE *, const char *, va_list) = NULL;
    if (!real_vfprintf) real_vfprintf = (int (*)(FILE *, const char *, va_list))dlsym(RTLD_NEXT, "vfprintf");

    va_list ap;
    va_start(ap, format);
    int ret = 0;
    if (!in_format_hook && stream == stderr && strcmp(format, "%s") == 0) {
        in_format_hook = true;
        va_list ap_peek;
        va_copy(ap_peek, ap);
        const char *s = va_arg(ap_peek, const char *);
        if (is_wayland_debug_line(s)) {
            format_and_print_atomic(stream, s);
            va_end(ap_peek);
            in_format_hook = false;
            va_end(ap);
            return (int)strlen(s);
        }
        va_end(ap_peek);
        in_format_hook = false;
    }
    flockfile(stream);
    ret = real_vfprintf(stream, format, ap);
    funlockfile(stream);
    va_end(ap);
    return ret;
}

int vfprintf(FILE *stream, const char *format, va_list ap) {
    static int (*real_vfprintf)(FILE *, const char *, va_list) = NULL;
    if (!real_vfprintf) real_vfprintf = (int (*)(FILE *, const char *, va_list))dlsym(RTLD_NEXT, "vfprintf");

    if (!in_format_hook && stream == stderr && strcmp(format, "%s") == 0) {
        in_format_hook = true;
        va_list ap_peek;
        va_copy(ap_peek, ap);
        const char *s = va_arg(ap_peek, const char *);
        if (is_wayland_debug_line(s)) {
            format_and_print_atomic(stream, s);
            va_end(ap_peek);
            in_format_hook = false;
            return (int)strlen(s);
        }
        va_end(ap_peek);
        in_format_hook = false;
    }
    flockfile(stream);
    int ret = real_vfprintf(stream, format, ap);
    funlockfile(stream);
    return ret;
}

int fputs(const char *s, FILE *stream) {
    static int (*real_fputs)(const char *, FILE *) = NULL;
    if (!real_fputs) real_fputs = (int (*)(const char *, FILE *))dlsym(RTLD_NEXT, "fputs");

    if (!in_format_hook && stream == stderr && is_wayland_debug_line(s)) {
        in_format_hook = true;
        format_and_print_atomic(stream, s);
        in_format_hook = false;
        return (int)strlen(s);
    }
    flockfile(stream);
    int ret = real_fputs(s, stream);
    funlockfile(stream);
    return ret;
}
