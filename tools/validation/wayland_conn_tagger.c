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
 * 4. Stderr formatting routines (__fprintf_chk, vfprintf, fprintf, fputs) called by
 *    libwayland's internal wl_closure_print to prefix each message with:
 *    [conn#<id> fd#<fd>]
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
    struct conn_info *ci = find_conn(display);
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
 * Stderr Formatting Interception
 * wl_closure_print writes via open_memstream + fprintf(stderr, "%s", buffer).
 * On fortified glibc, fprintf is emitted as __fprintf_chk.
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

static void emit_tag_if_needed(FILE *stream, const char *msg) {
    if (stream != stderr) return;
    if (!is_wayland_debug_line(msg)) return;

    static int (*real_fprintf)(FILE *, const char *, ...) = NULL;
    if (!real_fprintf) real_fprintf = (int (*)(FILE *, const char *, ...))dlsym(RTLD_NEXT, "fprintf");

    if (current_conn_id > 0) {
        real_fprintf(stderr, "[conn#%d fd#%d] ", current_conn_id, current_conn_fd);
    } else {
        real_fprintf(stderr, "[conn#untracked fd#?] ");
    }
}

int __fprintf_chk(FILE *stream, int flag, const char *format, ...) {
    static int (*real_vfprintf)(FILE *, const char *, va_list) = NULL;
    if (!real_vfprintf) real_vfprintf = (int (*)(FILE *, const char *, va_list))dlsym(RTLD_NEXT, "vfprintf");

    va_list ap;
    va_start(ap, format);
    if (!in_format_hook) {
        in_format_hook = true;
        if (strcmp(format, "%s") == 0) {
            va_list ap_peek;
            va_copy(ap_peek, ap);
            const char *s = va_arg(ap_peek, const char *);
            emit_tag_if_needed(stream, s);
            va_end(ap_peek);
        }
        in_format_hook = false;
    }
    int ret = real_vfprintf(stream, format, ap);
    va_end(ap);
    (void)flag;
    return ret;
}

int __vfprintf_chk(FILE *stream, int flag, const char *format, va_list ap) {
    static int (*real_vfprintf)(FILE *, const char *, va_list) = NULL;
    if (!real_vfprintf) real_vfprintf = (int (*)(FILE *, const char *, va_list))dlsym(RTLD_NEXT, "vfprintf");

    if (!in_format_hook) {
        in_format_hook = true;
        if (strcmp(format, "%s") == 0) {
            va_list ap_peek;
            va_copy(ap_peek, ap);
            const char *s = va_arg(ap_peek, const char *);
            emit_tag_if_needed(stream, s);
            va_end(ap_peek);
        }
        in_format_hook = false;
    }
    (void)flag;
    return real_vfprintf(stream, format, ap);
}

int fprintf(FILE *stream, const char *format, ...) {
    static int (*real_vfprintf)(FILE *, const char *, va_list) = NULL;
    if (!real_vfprintf) real_vfprintf = (int (*)(FILE *, const char *, va_list))dlsym(RTLD_NEXT, "vfprintf");

    va_list ap;
    va_start(ap, format);
    if (!in_format_hook) {
        in_format_hook = true;
        if (strcmp(format, "%s") == 0) {
            va_list ap_peek;
            va_copy(ap_peek, ap);
            const char *s = va_arg(ap_peek, const char *);
            emit_tag_if_needed(stream, s);
            va_end(ap_peek);
        }
        in_format_hook = false;
    }
    int ret = real_vfprintf(stream, format, ap);
    va_end(ap);
    return ret;
}

int vfprintf(FILE *stream, const char *format, va_list ap) {
    static int (*real_vfprintf)(FILE *, const char *, va_list) = NULL;
    if (!real_vfprintf) real_vfprintf = (int (*)(FILE *, const char *, va_list))dlsym(RTLD_NEXT, "vfprintf");

    if (!in_format_hook) {
        in_format_hook = true;
        if (strcmp(format, "%s") == 0) {
            va_list ap_peek;
            va_copy(ap_peek, ap);
            const char *s = va_arg(ap_peek, const char *);
            emit_tag_if_needed(stream, s);
            va_end(ap_peek);
        }
        in_format_hook = false;
    }
    return real_vfprintf(stream, format, ap);
}

int fputs(const char *s, FILE *stream) {
    static int (*real_fputs)(const char *, FILE *) = NULL;
    if (!real_fputs) real_fputs = (int (*)(const char *, FILE *))dlsym(RTLD_NEXT, "fputs");

    if (!in_format_hook) {
        in_format_hook = true;
        emit_tag_if_needed(stream, s);
        in_format_hook = false;
    }
    return real_fputs(s, stream);
}
