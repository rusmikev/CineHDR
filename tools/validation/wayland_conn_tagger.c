#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <dlfcn.h>
#include <elf.h>
#include <link.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <pthread.h>
#include <sys/mman.h>
#include <unistd.h>
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
 *    and atomic discovery to register each display and assign a unique conn_id and socket fd.
 * 2. All public request marshalling entry points (wl_proxy_marshal_array_flags)
 *    to establish connection context during request formatting.
 * 3. All public event dispatch entry points (wl_display_dispatch*, wl_display_roundtrip*)
 *    to establish connection context during incoming event dispatching across queues and threads.
 * 4. Event reading and loop synchronization entry points (wl_display_read_events,
 *    wl_display_prepare_read*, wl_display_cancel_read, wl_display_flush) used by GTK's
 *    poll source (gdkeventsource) and multi-threaded event readers.
 * 5. Dynamic library loading (dlopen, dlclose) to route dlopen/ctypes consumers
 *    (e.g. CineHDR's Python ctypes background probes) to this interposer library
 *    only when real_dlopen succeeds and resolves to the active libwayland-client
 *    instance, preserving load failures, alternative library handles, and
 *    dlopen/dlclose reference balance while leaving global dlsym untouched.
 *    Any wrapped entry point absent in the actually loaded libwayland is marked
 *    SHN_UNDEF in this interposer's in-memory DT_SYMTAB so dlsym on a substituted
 *    handle returns NULL (with dlerror set) and NULL real_fn is never called.
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

/* =========================================================================
 * Underlying libwayland and dlfcn function pointers
 * ========================================================================= */

static void *(*real_dlopen)(const char *, int) = NULL;
static int (*real_dlclose)(void *) = NULL;
static void *tagger_handle = NULL;
static void *target_wayland_handle = NULL;
static pthread_mutex_t dl_lock = PTHREAD_MUTEX_INITIALIZER;
static size_t substituted_open_count = 0;
static volatile bool tagger_ready = false;

static int (*real_wl_display_get_fd)(struct wl_display *) = NULL;
static struct wl_display *(*real_wl_proxy_get_display)(struct wl_proxy *) = NULL;

static struct wl_display *(*real_wl_display_connect_to_fd)(int) = NULL;
static struct wl_display *(*real_wl_display_connect)(const char *) = NULL;
static void (*real_wl_display_disconnect)(struct wl_display *) = NULL;
static struct wl_proxy *(*real_wl_proxy_marshal_array_flags)(
    struct wl_proxy *, uint32_t, const struct wl_interface *, uint32_t, uint32_t, union wl_argument *) = NULL;
static int (*real_wl_display_dispatch_queue)(struct wl_display *, struct wl_event_queue *) = NULL;
static int (*real_wl_display_dispatch)(struct wl_display *) = NULL;
static int (*real_wl_display_dispatch_queue_pending)(struct wl_display *, struct wl_event_queue *) = NULL;
static int (*real_wl_display_dispatch_pending)(struct wl_display *) = NULL;
static int (*real_wl_display_dispatch_queue_pending_single)(struct wl_display *, struct wl_event_queue *) = NULL;
static int (*real_wl_display_dispatch_pending_single)(struct wl_display *) = NULL;
static int (*real_wl_display_dispatch_queue_timeout)(
    struct wl_display *, struct wl_event_queue *, const struct timespec *) = NULL;
static int (*real_wl_display_dispatch_timeout)(struct wl_display *, const struct timespec *) = NULL;
static int (*real_wl_display_roundtrip_queue)(struct wl_display *, struct wl_event_queue *) = NULL;
static int (*real_wl_display_roundtrip)(struct wl_display *) = NULL;
static int (*real_wl_display_flush)(struct wl_display *) = NULL;
static int (*real_wl_display_prepare_read)(struct wl_display *) = NULL;
static int (*real_wl_display_prepare_read_queue)(struct wl_display *, struct wl_event_queue *) = NULL;
static void (*real_wl_display_cancel_read)(struct wl_display *) = NULL;
static int (*real_wl_display_read_events)(struct wl_display *) = NULL;

static void conn_tagger_init(void);

static void init_real_dlfcn(void) {
    if (!real_dlopen) {
        real_dlopen = (void *(*)(const char *, int))dlvsym(RTLD_NEXT, "dlopen", "GLIBC_2.2.5");
        if (!real_dlopen) {
            real_dlopen = (void *(*)(const char *, int))dlvsym(RTLD_NEXT, "dlopen", "GLIBC_2.34");
        }
    }
    if (!real_dlclose) {
        real_dlclose = (int (*)(void *))dlvsym(RTLD_NEXT, "dlclose", "GLIBC_2.2.5");
        if (!real_dlclose) {
            real_dlclose = (int (*)(void *))dlvsym(RTLD_NEXT, "dlclose", "GLIBC_2.34");
        }
    }
}

static void *resolve_target_wl_symbol(const char *name) {
    void *in_target = NULL;
    if (target_wayland_handle) {
        in_target = dlsym(target_wayland_handle, name);
        if (!in_target) {
            /* Symbol is absent in the actually loaded libwayland implementation. */
            return NULL;
        }
    }
    void *next_sym = dlsym(RTLD_NEXT, name);
    return next_sym ? next_sym : in_target;
}

struct wrapped_sym_status {
    const char *name;
    bool present;
};

struct dynsym_sync_ctx {
    uintptr_t self_addr;
    const struct wrapped_sym_status *entries;
    size_t count;
};

static int sync_dynsym_phdr_cb(struct dl_phdr_info *info, size_t size, void *data) {
    (void)size;
    struct dynsym_sync_ctx *ctx = (struct dynsym_sync_ctx *)data;
    bool match = false;
    const ElfW(Dyn) *dyn = NULL;

    for (int i = 0; i < info->dlpi_phnum; i++) {
        const ElfW(Phdr) *p = &info->dlpi_phdr[i];
        if (p->p_type == PT_LOAD) {
            uintptr_t start = info->dlpi_addr + p->p_vaddr;
            uintptr_t end = start + p->p_memsz;
            if (ctx->self_addr >= start && ctx->self_addr < end) {
                match = true;
            }
        } else if (p->p_type == PT_DYNAMIC) {
            dyn = (const ElfW(Dyn) *)(info->dlpi_addr + p->p_vaddr);
        }
    }
    if (!match || !dyn) return 0;

    ElfW(Sym) *symtab = NULL;
    const char *strtab = NULL;
    size_t strsz = 0;
    for (const ElfW(Dyn) *d = dyn; d->d_tag != DT_NULL; d++) {
        if (d->d_tag == DT_SYMTAB) symtab = (ElfW(Sym) *)d->d_un.d_ptr;
        else if (d->d_tag == DT_STRTAB) strtab = (const char *)d->d_un.d_ptr;
        else if (d->d_tag == DT_STRSZ) strsz = d->d_un.d_val;
    }
    if (!symtab || !strtab || !strsz) return 1;

    size_t max_syms = ((uintptr_t)strtab > (uintptr_t)symtab)
        ? ((uintptr_t)strtab - (uintptr_t)symtab) / sizeof(ElfW(Sym))
        : 4096;
    long page_size = sysconf(_SC_PAGESIZE);
    if (page_size <= 0) page_size = 4096;

    for (size_t i = 0; i < max_syms; i++) {
        uint32_t st_name = symtab[i].st_name;
        if (st_name == 0 || st_name >= strsz) continue;
        const char *sname = strtab + st_name;
        for (size_t j = 0; j < ctx->count; j++) {
            if (!ctx->entries[j].present && strcmp(sname, ctx->entries[j].name) == 0) {
                uintptr_t page = (uintptr_t)&symtab[i] & ~(uintptr_t)(page_size - 1);
                if (mprotect((void *)page, (size_t)page_size * 2, PROT_READ | PROT_WRITE) == 0) {
                    symtab[i].st_value = 0;
                    symtab[i].st_shndx = SHN_UNDEF;
                    mprotect((void *)page, (size_t)page_size * 2, PROT_READ);
                }
                break;
            }
        }
    }
    return 1;
}

static void refresh_real_wl_symbols(void) {
    real_wl_display_get_fd =
        (int (*)(struct wl_display *))resolve_target_wl_symbol("wl_display_get_fd");
    real_wl_proxy_get_display =
        (struct wl_display *(*)(struct wl_proxy *))resolve_target_wl_symbol("wl_proxy_get_display");

    real_wl_display_connect_to_fd =
        (struct wl_display *(*)(int))resolve_target_wl_symbol("wl_display_connect_to_fd");
    real_wl_display_connect =
        (struct wl_display *(*)(const char *))resolve_target_wl_symbol("wl_display_connect");
    real_wl_display_disconnect =
        (void (*)(struct wl_display *))resolve_target_wl_symbol("wl_display_disconnect");
    real_wl_proxy_marshal_array_flags =
        (struct wl_proxy *(*)(struct wl_proxy *, uint32_t, const struct wl_interface *, uint32_t, uint32_t, union wl_argument *))
        resolve_target_wl_symbol("wl_proxy_marshal_array_flags");
    real_wl_display_dispatch_queue =
        (int (*)(struct wl_display *, struct wl_event_queue *))resolve_target_wl_symbol("wl_display_dispatch_queue");
    real_wl_display_dispatch =
        (int (*)(struct wl_display *))resolve_target_wl_symbol("wl_display_dispatch");
    real_wl_display_dispatch_queue_pending =
        (int (*)(struct wl_display *, struct wl_event_queue *))resolve_target_wl_symbol("wl_display_dispatch_queue_pending");
    real_wl_display_dispatch_pending =
        (int (*)(struct wl_display *))resolve_target_wl_symbol("wl_display_dispatch_pending");
    real_wl_display_dispatch_queue_pending_single =
        (int (*)(struct wl_display *, struct wl_event_queue *))resolve_target_wl_symbol("wl_display_dispatch_queue_pending_single");
    real_wl_display_dispatch_pending_single =
        (int (*)(struct wl_display *))resolve_target_wl_symbol("wl_display_dispatch_pending_single");
    real_wl_display_dispatch_queue_timeout =
        (int (*)(struct wl_display *, struct wl_event_queue *, const struct timespec *))
        resolve_target_wl_symbol("wl_display_dispatch_queue_timeout");
    real_wl_display_dispatch_timeout =
        (int (*)(struct wl_display *, const struct timespec *))resolve_target_wl_symbol("wl_display_dispatch_timeout");
    real_wl_display_roundtrip_queue =
        (int (*)(struct wl_display *, struct wl_event_queue *))resolve_target_wl_symbol("wl_display_roundtrip_queue");
    real_wl_display_roundtrip =
        (int (*)(struct wl_display *))resolve_target_wl_symbol("wl_display_roundtrip");
    real_wl_display_flush =
        (int (*)(struct wl_display *))resolve_target_wl_symbol("wl_display_flush");
    real_wl_display_prepare_read =
        (int (*)(struct wl_display *))resolve_target_wl_symbol("wl_display_prepare_read");
    real_wl_display_prepare_read_queue =
        (int (*)(struct wl_display *, struct wl_event_queue *))resolve_target_wl_symbol("wl_display_prepare_read_queue");
    real_wl_display_cancel_read =
        (void (*)(struct wl_display *))resolve_target_wl_symbol("wl_display_cancel_read");
    real_wl_display_read_events =
        (int (*)(struct wl_display *))resolve_target_wl_symbol("wl_display_read_events");

    const struct wrapped_sym_status statuses[] = {
        { "wl_display_connect_to_fd", real_wl_display_connect_to_fd != NULL },
        { "wl_display_connect", real_wl_display_connect != NULL },
        { "wl_display_disconnect", real_wl_display_disconnect != NULL },
        { "wl_proxy_marshal_array_flags", real_wl_proxy_marshal_array_flags != NULL },
        { "wl_display_dispatch_queue", real_wl_display_dispatch_queue != NULL },
        { "wl_display_dispatch", real_wl_display_dispatch != NULL },
        { "wl_display_dispatch_queue_pending", real_wl_display_dispatch_queue_pending != NULL },
        { "wl_display_dispatch_pending", real_wl_display_dispatch_pending != NULL },
        { "wl_display_dispatch_queue_pending_single", real_wl_display_dispatch_queue_pending_single != NULL },
        { "wl_display_dispatch_pending_single", real_wl_display_dispatch_pending_single != NULL },
        { "wl_display_dispatch_queue_timeout", real_wl_display_dispatch_queue_timeout != NULL },
        { "wl_display_dispatch_timeout", real_wl_display_dispatch_timeout != NULL },
        { "wl_display_roundtrip_queue", real_wl_display_roundtrip_queue != NULL },
        { "wl_display_roundtrip", real_wl_display_roundtrip != NULL },
        { "wl_display_flush", real_wl_display_flush != NULL },
        { "wl_display_prepare_read", real_wl_display_prepare_read != NULL },
        { "wl_display_prepare_read_queue", real_wl_display_prepare_read_queue != NULL },
        { "wl_display_cancel_read", real_wl_display_cancel_read != NULL },
        { "wl_display_read_events", real_wl_display_read_events != NULL },
    };

    bool any_missing = false;
    for (size_t i = 0; i < sizeof(statuses) / sizeof(statuses[0]); i++) {
        if (!statuses[i].present) {
            any_missing = true;
            break;
        }
    }
    if (any_missing) {
        struct dynsym_sync_ctx ctx = {
            .self_addr = (uintptr_t)&refresh_real_wl_symbols,
            .entries = statuses,
            .count = sizeof(statuses) / sizeof(statuses[0]),
        };
        dl_iterate_phdr(sync_dynsym_phdr_cb, &ctx);
    }

    /* Clear any dlerror left by probing optional symbols. */
    (void)dlerror();
}

__attribute__((constructor))
static void conn_tagger_init(void) {
    if (tagger_ready) return;
    init_real_dlfcn();

    Dl_info self_info;
    if (dladdr((void *)conn_tagger_init, &self_info) && self_info.dli_fname && real_dlopen) {
        tagger_handle = real_dlopen(self_info.dli_fname, RTLD_NOW | RTLD_NOLOAD);
        if (tagger_handle && real_dlclose) {
            /* Balance RTLD_NOLOAD refcount; LD_PRELOAD keeps tagger mapped. */
            real_dlclose(tagger_handle);
        }
    }

    /* Identify the active underlying libwayland-client handle in RTLD_NEXT. */
    void *probe_sym = dlsym(RTLD_NEXT, "wl_display_get_fd");
    if (!probe_sym) {
        probe_sym = dlsym(RTLD_NEXT, "wl_display_connect");
    }
    if (probe_sym && real_dlopen) {
        Dl_info wl_info;
        if (dladdr(probe_sym, &wl_info) && wl_info.dli_fname) {
            target_wayland_handle = real_dlopen(wl_info.dli_fname, RTLD_LAZY | RTLD_NOLOAD);
            if (target_wayland_handle && real_dlclose) {
                /* Balance RTLD_NOLOAD refcount; DT_NEEDED/LD_PRELOAD keeps it mapped. */
                real_dlclose(target_wayland_handle);
            }
        }
    }

    refresh_real_wl_symbols();
    tagger_ready = true;
}

static void register_display_locked(struct wl_display *display, int fd) {
    if (!display) return;
    for (struct conn_info *c = conns; c; c = c->next) {
        if (c->display == display) {
            if (fd >= 0) c->fd = fd;
            return;
        }
    }
    struct conn_info *ci = (struct conn_info *)malloc(sizeof(*ci));
    if (ci) {
        ci->display = display;
        ci->conn_id = next_conn_id++;
        ci->fd = fd;
        ci->next = conns;
        conns = ci;
    }
}

static void register_display(struct wl_display *display, int fd) {
    if (!display) return;
    pthread_mutex_lock(&conns_lock);
    register_display_locked(display, fd);
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
        } else {
            curr = &(*curr)->next;
        }
    }
    pthread_mutex_unlock(&conns_lock);
}

static bool get_or_register_conn(struct wl_display *display, int *out_conn_id, int *out_fd) {
    if (!display) return false;
    pthread_mutex_lock(&conns_lock);
    for (struct conn_info *c = conns; c; c = c->next) {
        if (c->display == display) {
            if (out_conn_id) *out_conn_id = c->conn_id;
            if (out_fd) *out_fd = c->fd;
            pthread_mutex_unlock(&conns_lock);
            return true;
        }
    }

    /* First discovery of an untracked wl_display: resolve socket fd atomically */
    if (!real_wl_display_get_fd) {
        real_wl_display_get_fd = (int (*)(struct wl_display *))resolve_target_wl_symbol("wl_display_get_fd");
    }
    int fd = real_wl_display_get_fd ? real_wl_display_get_fd(display) : -1;
    if (fd >= 0) {
        register_display_locked(display, fd);
        for (struct conn_info *c = conns; c; c = c->next) {
            if (c->display == display) {
                if (out_conn_id) *out_conn_id = c->conn_id;
                if (out_fd) *out_fd = c->fd;
                pthread_mutex_unlock(&conns_lock);
                return true;
            }
        }
    }

    pthread_mutex_unlock(&conns_lock);
    return false;
}

static inline struct conn_scope enter_conn_scope(struct wl_display *display) {
    struct conn_scope scope = { current_conn_id, current_conn_fd };
    int id = -1, fd = -1;
    if (display && get_or_register_conn(display, &id, &fd)) {
        current_conn_id = id;
        current_conn_fd = fd;
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
    if (!real_wl_display_connect_to_fd) {
        real_wl_display_connect_to_fd =
            (struct wl_display *(*)(int))resolve_target_wl_symbol("wl_display_connect_to_fd");
        if (!real_wl_display_connect_to_fd) return NULL;
    }
    struct wl_display *d = real_wl_display_connect_to_fd(fd);
    if (d) register_display(d, fd);
    return d;
}

struct wl_display *wl_display_connect(const char *name) {
    if (!real_wl_display_connect) {
        real_wl_display_connect =
            (struct wl_display *(*)(const char *))resolve_target_wl_symbol("wl_display_connect");
        if (!real_wl_display_connect) return NULL;
    }
    struct wl_display *d = real_wl_display_connect(name);
    if (d) {
        if (!real_wl_display_get_fd) {
            real_wl_display_get_fd =
                (int (*)(struct wl_display *))resolve_target_wl_symbol("wl_display_get_fd");
        }
        int fd = real_wl_display_get_fd ? real_wl_display_get_fd(d) : -1;
        register_display(d, fd);
    }
    return d;
}

void wl_display_disconnect(struct wl_display *display) {
    if (!real_wl_display_disconnect) {
        real_wl_display_disconnect =
            (void (*)(struct wl_display *))resolve_target_wl_symbol("wl_display_disconnect");
        if (!real_wl_display_disconnect) return;
    }
    unregister_display(display);
    real_wl_display_disconnect(display);
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
    if (!real_wl_proxy_marshal_array_flags) {
        real_wl_proxy_marshal_array_flags =
            (struct wl_proxy *(*)(struct wl_proxy *, uint32_t, const struct wl_interface *, uint32_t, uint32_t, union wl_argument *))
            resolve_target_wl_symbol("wl_proxy_marshal_array_flags");
        if (!real_wl_proxy_marshal_array_flags) return NULL;
    }
    if (!real_wl_proxy_get_display) {
        real_wl_proxy_get_display =
            (struct wl_display *(*)(struct wl_proxy *))resolve_target_wl_symbol("wl_proxy_get_display");
    }
    struct wl_display *disp = (proxy && real_wl_proxy_get_display) ? real_wl_proxy_get_display(proxy) : NULL;
    struct conn_scope scope = enter_conn_scope(disp);
    struct wl_proxy *ret = real_wl_proxy_marshal_array_flags(proxy, opcode, interface, version, flags, args);
    leave_conn_scope(scope);
    return ret;
}

/* =========================================================================
 * Event Dispatching Interception
 * Covers all 10 public dispatch entry points in Wayland 1.26.0.
 * ========================================================================= */

int wl_display_dispatch_queue(struct wl_display *display, struct wl_event_queue *queue) {
    if (!real_wl_display_dispatch_queue) {
        real_wl_display_dispatch_queue =
            (int (*)(struct wl_display *, struct wl_event_queue *))resolve_target_wl_symbol("wl_display_dispatch_queue");
        if (!real_wl_display_dispatch_queue) return -1;
    }
    struct conn_scope scope = enter_conn_scope(display);
    int ret = real_wl_display_dispatch_queue(display, queue);
    leave_conn_scope(scope);
    return ret;
}

int wl_display_dispatch(struct wl_display *display) {
    if (!real_wl_display_dispatch) {
        real_wl_display_dispatch =
            (int (*)(struct wl_display *))resolve_target_wl_symbol("wl_display_dispatch");
        if (!real_wl_display_dispatch) return -1;
    }
    struct conn_scope scope = enter_conn_scope(display);
    int ret = real_wl_display_dispatch(display);
    leave_conn_scope(scope);
    return ret;
}

int wl_display_dispatch_queue_pending(struct wl_display *display, struct wl_event_queue *queue) {
    if (!real_wl_display_dispatch_queue_pending) {
        real_wl_display_dispatch_queue_pending =
            (int (*)(struct wl_display *, struct wl_event_queue *))resolve_target_wl_symbol("wl_display_dispatch_queue_pending");
        if (!real_wl_display_dispatch_queue_pending) return -1;
    }
    struct conn_scope scope = enter_conn_scope(display);
    int ret = real_wl_display_dispatch_queue_pending(display, queue);
    leave_conn_scope(scope);
    return ret;
}

int wl_display_dispatch_pending(struct wl_display *display) {
    if (!real_wl_display_dispatch_pending) {
        real_wl_display_dispatch_pending =
            (int (*)(struct wl_display *))resolve_target_wl_symbol("wl_display_dispatch_pending");
        if (!real_wl_display_dispatch_pending) return -1;
    }
    struct conn_scope scope = enter_conn_scope(display);
    int ret = real_wl_display_dispatch_pending(display);
    leave_conn_scope(scope);
    return ret;
}

int wl_display_dispatch_queue_pending_single(struct wl_display *display, struct wl_event_queue *queue) {
    if (!real_wl_display_dispatch_queue_pending_single) {
        real_wl_display_dispatch_queue_pending_single =
            (int (*)(struct wl_display *, struct wl_event_queue *))resolve_target_wl_symbol("wl_display_dispatch_queue_pending_single");
        if (!real_wl_display_dispatch_queue_pending_single) return -1;
    }
    struct conn_scope scope = enter_conn_scope(display);
    int ret = real_wl_display_dispatch_queue_pending_single(display, queue);
    leave_conn_scope(scope);
    return ret;
}

int wl_display_dispatch_pending_single(struct wl_display *display) {
    if (!real_wl_display_dispatch_pending_single) {
        real_wl_display_dispatch_pending_single =
            (int (*)(struct wl_display *))resolve_target_wl_symbol("wl_display_dispatch_pending_single");
        if (!real_wl_display_dispatch_pending_single) return -1;
    }
    struct conn_scope scope = enter_conn_scope(display);
    int ret = real_wl_display_dispatch_pending_single(display);
    leave_conn_scope(scope);
    return ret;
}

int wl_display_dispatch_queue_timeout(struct wl_display *display, struct wl_event_queue *queue, const struct timespec *deadline) {
    if (!real_wl_display_dispatch_queue_timeout) {
        real_wl_display_dispatch_queue_timeout =
            (int (*)(struct wl_display *, struct wl_event_queue *, const struct timespec *))
            resolve_target_wl_symbol("wl_display_dispatch_queue_timeout");
        if (!real_wl_display_dispatch_queue_timeout) return -1;
    }
    struct conn_scope scope = enter_conn_scope(display);
    int ret = real_wl_display_dispatch_queue_timeout(display, queue, deadline);
    leave_conn_scope(scope);
    return ret;
}

int wl_display_dispatch_timeout(struct wl_display *display, const struct timespec *deadline) {
    if (!real_wl_display_dispatch_timeout) {
        real_wl_display_dispatch_timeout =
            (int (*)(struct wl_display *, const struct timespec *))resolve_target_wl_symbol("wl_display_dispatch_timeout");
        if (!real_wl_display_dispatch_timeout) return -1;
    }
    struct conn_scope scope = enter_conn_scope(display);
    int ret = real_wl_display_dispatch_timeout(display, deadline);
    leave_conn_scope(scope);
    return ret;
}

int wl_display_roundtrip_queue(struct wl_display *display, struct wl_event_queue *queue) {
    if (!real_wl_display_roundtrip_queue) {
        real_wl_display_roundtrip_queue =
            (int (*)(struct wl_display *, struct wl_event_queue *))resolve_target_wl_symbol("wl_display_roundtrip_queue");
        if (!real_wl_display_roundtrip_queue) return -1;
    }
    struct conn_scope scope = enter_conn_scope(display);
    int ret = real_wl_display_roundtrip_queue(display, queue);
    leave_conn_scope(scope);
    return ret;
}

int wl_display_roundtrip(struct wl_display *display) {
    if (!real_wl_display_roundtrip) {
        real_wl_display_roundtrip =
            (int (*)(struct wl_display *))resolve_target_wl_symbol("wl_display_roundtrip");
        if (!real_wl_display_roundtrip) return -1;
    }
    struct conn_scope scope = enter_conn_scope(display);
    int ret = real_wl_display_roundtrip(display);
    leave_conn_scope(scope);
    return ret;
}

/* =========================================================================
 * Event Reading & Display Loop Interception
 * Covers GTK event loops (gdkeventsource), explicit polling, and socket reading.
 * ========================================================================= */

int wl_display_flush(struct wl_display *display) {
    if (!real_wl_display_flush) {
        real_wl_display_flush =
            (int (*)(struct wl_display *))resolve_target_wl_symbol("wl_display_flush");
        if (!real_wl_display_flush) return -1;
    }
    struct conn_scope scope = enter_conn_scope(display);
    int ret = real_wl_display_flush(display);
    leave_conn_scope(scope);
    return ret;
}

int wl_display_prepare_read(struct wl_display *display) {
    if (!real_wl_display_prepare_read) {
        real_wl_display_prepare_read =
            (int (*)(struct wl_display *))resolve_target_wl_symbol("wl_display_prepare_read");
        if (!real_wl_display_prepare_read) return -1;
    }
    struct conn_scope scope = enter_conn_scope(display);
    int ret = real_wl_display_prepare_read(display);
    leave_conn_scope(scope);
    return ret;
}

int wl_display_prepare_read_queue(struct wl_display *display, struct wl_event_queue *queue) {
    if (!real_wl_display_prepare_read_queue) {
        real_wl_display_prepare_read_queue =
            (int (*)(struct wl_display *, struct wl_event_queue *))resolve_target_wl_symbol("wl_display_prepare_read_queue");
        if (!real_wl_display_prepare_read_queue) return -1;
    }
    struct conn_scope scope = enter_conn_scope(display);
    int ret = real_wl_display_prepare_read_queue(display, queue);
    leave_conn_scope(scope);
    return ret;
}

void wl_display_cancel_read(struct wl_display *display) {
    if (!real_wl_display_cancel_read) {
        real_wl_display_cancel_read =
            (void (*)(struct wl_display *))resolve_target_wl_symbol("wl_display_cancel_read");
        if (!real_wl_display_cancel_read) return;
    }
    struct conn_scope scope = enter_conn_scope(display);
    real_wl_display_cancel_read(display);
    leave_conn_scope(scope);
}

int wl_display_read_events(struct wl_display *display) {
    if (!real_wl_display_read_events) {
        real_wl_display_read_events =
            (int (*)(struct wl_display *))resolve_target_wl_symbol("wl_display_read_events");
        if (!real_wl_display_read_events) return -1;
    }
    struct conn_scope scope = enter_conn_scope(display);
    int ret = real_wl_display_read_events(display);
    leave_conn_scope(scope);
    return ret;
}

/* =========================================================================
 * Dynamic Library Loading Interception (dlopen, dlclose)
 *
 * 1. Calls real_dlopen(filename, flags) first so nonexistent paths or invalid
 *    flags fail with NULL and original dlerror().
 * 2. Substitutes tagger_handle only when filename names libwayland-client AND
 *    the loaded handle matches target_wayland_handle (the active RTLD_NEXT
 *    libwayland-client), so explicit alternative libraries are never silently
 *    replaced with the system one.
 * 3. Tracks substituted_open_count under dl_lock so each dlclose(tagger_handle)
 *    decrements the corresponding reference on target_wayland_handle without
 *    unloading the interposer or underflowing on extra dlclose calls.
 * ========================================================================= */

static bool is_wayland_client_lib(const char *filename) {
    if (!filename) return false;
    const char *slash = strrchr(filename, '/');
    const char *name = slash ? slash + 1 : filename;
    if (strcmp(name, "libwayland-client.so") == 0) return true;
    if (strcmp(name, "libwayland-client.so.0") == 0) return true;
    if (strncmp(name, "libwayland-client.so.0.", 23) == 0) return true;
    return false;
}

void *dlopen(const char *filename, int flags) {
    if (!tagger_ready) {
        conn_tagger_init();
    }
    if (!real_dlopen) {
        init_real_dlfcn();
    }
    void *real_h = real_dlopen ? real_dlopen(filename, flags) : NULL;
    if (!real_h) {
        return NULL;
    }

    if (filename && is_wayland_client_lib(filename) && tagger_handle) {
        pthread_mutex_lock(&dl_lock);
        if (!target_wayland_handle && strchr(filename, '/') == NULL) {
            target_wayland_handle = real_h;
            refresh_real_wl_symbols();
        }
        if (real_h == target_wayland_handle) {
            substituted_open_count++;
            pthread_mutex_unlock(&dl_lock);
            return tagger_handle;
        }
        pthread_mutex_unlock(&dl_lock);
    }

    return real_h;
}

int dlclose(void *handle) {
    if (!real_dlclose) {
        init_real_dlfcn();
    }
    if (tagger_handle && handle == tagger_handle) {
        pthread_mutex_lock(&dl_lock);
        if (substituted_open_count == 0) {
            pthread_mutex_unlock(&dl_lock);
            return -1;
        }
        substituted_open_count--;
        void *target = target_wayland_handle;
        pthread_mutex_unlock(&dl_lock);
        return (real_dlclose && target) ? real_dlclose(target) : 0;
    }
    return real_dlclose ? real_dlclose(handle) : 0;
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
