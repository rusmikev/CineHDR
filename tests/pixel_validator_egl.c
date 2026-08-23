// Development-only Gate 2 embedded-FBO pixel probe for CineHDR.
//
// One process owns exactly one mpv render context.  The Python runner invokes
// this binary separately for opengl and opengl-next; it must never hot-swap a
// renderer in a live context.  All successful output is a single JSON object.

#define _GNU_SOURCE
#define _POSIX_C_SOURCE 200809L
#define GL_GLEXT_PROTOTYPES

#include <dlfcn.h>
#include <errno.h>
#include <link.h>
#include <math.h>
#include <stdbool.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include <EGL/egl.h>
#include <GL/gl.h>
#include <mpv/client.h>
#include <mpv/render.h>
#include <mpv/render_gl.h>

#ifndef MPV_RENDER_API_TYPE_OPENGL_NEXT
#error "This validator requires the accepted mpv-gpu-next headers"
#endif

enum {
    FRAME_WIDTH = 320,
    FRAME_HEIGHT = 180,
    PATCH_COLUMNS = 4,
    PATCH_ROWS = 2,
    PATCH_COUNT = PATCH_COLUMNS * PATCH_ROWS,
};

#define GRADIENT_RAMP_SAMPLES 60

struct probe_result {
    char *mpv_version;
    char *ffmpeg_version;
    char *input_primaries;
    char *input_transfer;
    char *input_matrix;
    char *input_levels;
    char *input_pixel_format;
    char *active_hwdec;
    char *target_primaries;
    char *target_transfer;
    char *target_peak;
    char *capture_time_pos;
    char *capture_pause;
    char *capture_loop_file;
    bool capture_update_callback_observed;
    const char *libplacebo_version;
    char libmpv_path[4096];
    char libplacebo_path[4096];
    float samples[PATCH_COUNT][3];
    float gradient_ramp[GRADIENT_RAMP_SAMPLES];
    int gradient_ramp_count;
    float subtitle_sample[3];
    bool subtitle_sampled;
    int rendered_frames;
    GLenum gl_error;
    double frame_time_mean_ms;
    double frame_time_p50_ms;
    double frame_time_p95_ms;
    double frame_time_p99_ms;
    int dropped_frames;
    int benchmark_frame_count;
};

static const char *patch_names[PATCH_COUNT] = {
    "black", "near_black", "reference_white", "peak_white",
    "red_primary", "green_primary", "blue_primary", "neutral_gradient",
};

static void *get_proc_address(void *context, const char *name)
{
    (void) context;
    return (void *) eglGetProcAddress(name);
}

static double monotonic_seconds(void)
{
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return now.tv_sec + now.tv_nsec / 1000000000.0;
}

static void json_string(const char *value)
{
    const unsigned char *character;
    if (!value) {
        fputs("null", stdout);
        return;
    }
    putchar('"');
    for (character = (const unsigned char *) value; *character; character++) {
        switch (*character) {
        case '\\': fputs("\\\\", stdout); break;
        case '"': fputs("\\\"", stdout); break;
        case '\n': fputs("\\n", stdout); break;
        case '\r': fputs("\\r", stdout); break;
        case '\t': fputs("\\t", stdout); break;
        default:
            if (*character < 0x20)
                printf("\\u%04x", *character);
            else
                putchar(*character);
        }
    }
    putchar('"');
}

static void clear_gl_errors(void)
{
    while (glGetError() != GL_NO_ERROR) {
    }
}

static void render_update_callback(void *context)
{
    atomic_bool *pending = context;
    atomic_store_explicit(pending, true, memory_order_release);
}

static bool drain_diagnostic_events(mpv_handle *mpv)
{
    bool end_file_observed = false;
    for (;;) {
        mpv_event *event = mpv_wait_event(mpv, 0);
        if (event->event_id == MPV_EVENT_NONE)
            break;
        if (event->event_id == MPV_EVENT_LOG_MESSAGE) {
            mpv_event_log_message *message = event->data;
            fprintf(stderr, "libmpv[%s] %s: %s", message->level,
                    message->prefix, message->text);
        }
        if (event->event_id == MPV_EVENT_END_FILE)
            end_file_observed = true;
    }
    return end_file_observed;
}

static int set_option(mpv_handle *mpv, const char *name, const char *value)
{
    int error = mpv_set_option_string(mpv, name, value);
    if (error < 0) {
        fprintf(stderr, "failed setting %s=%s: %s\n", name, value,
                mpv_error_string(error));
    }
    return error;
}

struct library_lookup {
    const char *name;
    char *destination;
    size_t destination_size;
};

static int remember_library(struct dl_phdr_info *info, size_t size, void *data)
{
    struct library_lookup *lookup = data;
    (void) size;
    if (lookup->destination[0] || !info->dlpi_name ||
        !strstr(info->dlpi_name, lookup->name))
        return 0;
    snprintf(lookup->destination, lookup->destination_size, "%s", info->dlpi_name);
    return 1;
}

static void record_loaded_library_paths(struct probe_result *result)
{
    const char *(*placebo_version)(void);
    struct library_lookup mpv_lookup = {
        .name = "libmpv.so",
        .destination = result->libmpv_path,
        .destination_size = sizeof(result->libmpv_path),
    };
    struct library_lookup placebo_lookup = {
        .name = "libplacebo.so",
        .destination = result->libplacebo_path,
        .destination_size = sizeof(result->libplacebo_path),
    };
    dl_iterate_phdr(remember_library, &mpv_lookup);
    dl_iterate_phdr(remember_library, &placebo_lookup);
    placebo_version = dlsym(RTLD_DEFAULT, "pl_version");
    if (placebo_version)
        result->libplacebo_version = placebo_version();
}

static void free_result(struct probe_result *result)
{
    mpv_free(result->mpv_version);
    mpv_free(result->ffmpeg_version);
    mpv_free(result->input_primaries);
    mpv_free(result->input_transfer);
    mpv_free(result->input_matrix);
    mpv_free(result->input_levels);
    mpv_free(result->input_pixel_format);
    mpv_free(result->active_hwdec);
    mpv_free(result->target_primaries);
    mpv_free(result->target_transfer);
    mpv_free(result->target_peak);
    mpv_free(result->capture_time_pos);
    mpv_free(result->capture_pause);
    mpv_free(result->capture_loop_file);
    *result = (struct probe_result) {0};
}

static bool has_required_metadata(const struct probe_result *result)
{
    return result->mpv_version && result->ffmpeg_version &&
           result->input_primaries && result->input_transfer &&
           result->input_matrix && result->input_levels &&
           result->input_pixel_format && result->active_hwdec &&
           result->target_primaries && result->target_transfer &&
           result->target_peak && result->capture_time_pos &&
           result->capture_pause && result->capture_loop_file &&
           result->libmpv_path[0] &&
           result->libplacebo_path[0] && result->libplacebo_version;
}

static void report_missing_metadata(const struct probe_result *result)
{
    const struct {
        const char *name;
        const char *value;
    } fields[] = {
        {"mpv_version", result->mpv_version},
        {"ffmpeg_version", result->ffmpeg_version},
        {"input_primaries", result->input_primaries},
        {"input_transfer", result->input_transfer},
        {"input_matrix", result->input_matrix},
        {"input_levels", result->input_levels},
        {"input_pixel_format", result->input_pixel_format},
        {"active_hwdec", result->active_hwdec},
        {"target_primaries", result->target_primaries},
        {"target_transfer", result->target_transfer},
        {"target_peak", result->target_peak},
        {"capture_time_pos", result->capture_time_pos},
        {"capture_pause", result->capture_pause},
        {"capture_loop_file", result->capture_loop_file},
        {"libplacebo_version", result->libplacebo_version},
    };
    for (size_t index = 0; index < sizeof(fields) / sizeof(fields[0]); index++) {
        if (!fields[index].value)
            fprintf(stderr, "missing required evidence: %s\n", fields[index].name);
    }
    if (!result->libmpv_path[0])
        fputs("missing required evidence: libmpv_path\n", stderr);
    if (!result->libplacebo_path[0])
        fputs("missing required evidence: libplacebo_path\n", stderr);
}

static int capture(const char *video_file, const char *mode, const char *api,
                   const char *timestamp, const char *decode_mode,
                   const char *delivery_mode,
                   struct probe_result *result)
{
    const bool is_hdr = strcmp(mode, "hdr") == 0;
    // The Python runner selects continuous delivery only after fixture
    // provenance proves every decoded HEVC frame is byte-identical. Paused
    // RGB delivery still consumes only explicitly published render updates.
    const bool requires_published_update = strcmp(delivery_mode, "paused") == 0;
    const char *pause_mode = strcmp(delivery_mode, "paused") == 0 ? "yes" : "no";
    const char *loop_file_mode = strcmp(delivery_mode, "paused") == 0 ? "no" : "inf";
    const char *api_type = strcmp(api, "opengl-next") == 0
        ? MPV_RENDER_API_TYPE_OPENGL_NEXT : MPV_RENDER_API_TYPE_OPENGL;
    const GLenum internal_format = is_hdr ? GL_RGBA16F : GL_RGBA8;
    const GLenum readback_type = is_hdr ? GL_FLOAT : GL_UNSIGNED_BYTE;
    const int depth = is_hdr ? 16 : 8;
    const char *target_primaries = is_hdr ? "bt.2020" : "bt.709";
    const char *target_transfer = is_hdr ? "pq" : "srgb";
    const char *target_peak_env = getenv("CINEHDR_PROBE_TARGET_PEAK");
    const char *target_peak = target_peak_env ? target_peak_env : (is_hdr ? "1000" : "100");
    int status = -1;
    mpv_handle *mpv = NULL;
    mpv_render_context *render_context = NULL;
    GLuint framebuffer = 0;
    GLuint texture = 0;

    mpv = mpv_create();
    if (!mpv) {
        fputs("mpv_create failed\n", stderr);
        goto done;
    }
    const char *dither_opt = getenv("CINEHDR_PROBE_DITHER");
    const char *dither_depth_opt = getenv("CINEHDR_PROBE_DITHER_DEPTH");
    const char *sub_file_opt = getenv("CINEHDR_PROBE_SUB_FILE");

    // These are fixed for each invocation so only the requested render API varies.
    if (set_option(mpv, "config", "no") < 0 ||
        set_option(mpv, "terminal", "no") < 0 ||
        set_option(mpv, "vo", "libmpv") < 0 ||
        set_option(mpv, "audio", "no") < 0 ||
        set_option(mpv, "pause", pause_mode) < 0 ||
        set_option(mpv, "loop-file", loop_file_mode) < 0 ||
        set_option(mpv, "keep-open", "yes") < 0 ||
        set_option(mpv, "hr-seek", "yes") < 0 ||
        set_option(mpv, "start", timestamp) < 0 ||
        set_option(mpv, "hwdec", decode_mode) < 0 ||
        set_option(mpv, "dither", dither_opt ? dither_opt : "no") < 0 ||
        set_option(mpv, "target-prim", target_primaries) < 0 ||
        set_option(mpv, "target-trc", target_transfer) < 0 ||
        set_option(mpv, "target-peak", target_peak) < 0)
    {
        goto done;
    }
    if (dither_depth_opt && set_option(mpv, "dither-depth", dither_depth_opt) < 0)
        goto done;
    int error = mpv_initialize(mpv);
    if (error < 0) {
        fprintf(stderr, "mpv_initialize failed: %s\n", mpv_error_string(error));
        goto done;
    }
    mpv_request_log_messages(mpv, "info");

    mpv_opengl_init_params init_params = {
        .get_proc_address = get_proc_address,
        .get_proc_address_ctx = NULL,
    };
    mpv_render_param create_params[] = {
        {MPV_RENDER_PARAM_API_TYPE, (void *) api_type},
        {MPV_RENDER_PARAM_OPENGL_INIT_PARAMS, &init_params},
        {MPV_RENDER_PARAM_INVALID, NULL},
    };
    error = mpv_render_context_create(&render_context, mpv, create_params);
    if (error < 0) {
        fprintf(stderr, "%s context creation failed: %s\n", api,
                mpv_error_string(error));
        goto done;
    }

    glGenFramebuffers(1, &framebuffer);
    glBindFramebuffer(GL_FRAMEBUFFER, framebuffer);
    glGenTextures(1, &texture);
    glBindTexture(GL_TEXTURE_2D, texture);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexImage2D(GL_TEXTURE_2D, 0, internal_format, FRAME_WIDTH, FRAME_HEIGHT,
                 0, GL_RGBA, readback_type, NULL);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                           GL_TEXTURE_2D, texture, 0);
    if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) {
        fputs("declared FBO is incomplete\n", stderr);
        goto done;
    }

    const char *load[] = {"loadfile", video_file, NULL};
    atomic_bool update_pending;
    atomic_init(&update_pending, false);
    mpv_render_context_set_update_callback(
        render_context, render_update_callback, &update_pending
    );
    error = mpv_command(mpv, load);
    if (error < 0) {
        fprintf(stderr, "loadfile failed: %s\n", mpv_error_string(error));
        goto done;
    }
    if (sub_file_opt) {
        const char *sub_cmd[] = {"sub-add", sub_file_opt, "select", NULL};
        mpv_command(mpv, sub_cmd);
    }
    bool last_frame_nonempty = false;
    bool last_frame_nonuniform = false;
    bool last_primary_structure_ready = false;
    bool update_callback_observed = false;
    float last_readiness_pixels[3][4] = {{0}};
    uint64_t update_frame_count = 0;
    uint64_t first_update_frame_flags = 0;
    uint64_t last_update_frame_flags = 0;
    int64_t first_update_target_time = 0;
    int64_t last_update_target_time = 0;
    const struct timespec callback_poll_delay = {
        .tv_sec = 0,
        .tv_nsec = 2 * 1000 * 1000,
    };
    const double deadline = monotonic_seconds() + 30.0;
    while (monotonic_seconds() < deadline) {
        if (!atomic_exchange_explicit(
                &update_pending, false, memory_order_acquire)) {
            nanosleep(&callback_poll_delay, NULL);
            continue;
        }
        update_callback_observed = true;
        // The callback is the only cross-thread hand-off. During this render
        // phase the GL thread calls only the render-context APIs below. Paused
        // delivery consumes published frame updates; continuous-identical may
        // render the current proven-identical frame after a callback even if
        // its update flag was already cleared.
        const uint64_t update_flags = mpv_render_context_update(render_context);
        if (update_flags & MPV_RENDER_UPDATE_FRAME) {
            mpv_render_frame_info frame_info = {0};
            mpv_render_param frame_info_param = {
                MPV_RENDER_PARAM_NEXT_FRAME_INFO, &frame_info,
            };
            error = mpv_render_context_get_info(render_context, frame_info_param);
            if (error < 0) {
                fprintf(stderr, "next-frame info failed: %d\n", error);
                goto done;
            }
            update_frame_count++;
            if (update_frame_count == 1) {
                first_update_frame_flags = frame_info.flags;
                first_update_target_time = frame_info.target_time;
            }
            last_update_frame_flags = frame_info.flags;
            last_update_target_time = frame_info.target_time;
        }
        if (requires_published_update && !(update_flags & MPV_RENDER_UPDATE_FRAME))
            continue;
        mpv_opengl_fbo target = {
            .fbo = (int) framebuffer,
            .w = FRAME_WIDTH,
            .h = FRAME_HEIGHT,
            .internal_format = (int) internal_format,
        };
        // Match CineHDR's embedded FBO contract. glReadPixels is bottom-left,
        // so the sampler below maps logical top-row patches explicitly.
        int flip_y = 0;
        // The offscreen probe has no presentation/swap loop to wait for a
        // target time. Keep both delivery modes and both APIs non-blocking.
        int block_for_target_time = 0;
        mpv_render_param render_params[] = {
            {MPV_RENDER_PARAM_OPENGL_FBO, &target},
            {MPV_RENDER_PARAM_FLIP_Y, &flip_y},
            {MPV_RENDER_PARAM_DEPTH, (void *) &depth},
            {MPV_RENDER_PARAM_BLOCK_FOR_TARGET_TIME, &block_for_target_time},
            {MPV_RENDER_PARAM_INVALID, NULL},
        };
        clear_gl_errors();
        glBindFramebuffer(GL_FRAMEBUFFER, framebuffer);
        glViewport(0, 0, FRAME_WIDTH, FRAME_HEIGHT);
        error = mpv_render_context_render(render_context, render_params);
        if (error < 0) {
            fprintf(stderr, "render failed: %d\n", error);
            goto done;
        }
        glFinish();
        mpv_render_context_report_swap(render_context);
        result->gl_error = glGetError();
        if (result->gl_error != GL_NO_ERROR) {
            fprintf(stderr, "GL error after render: 0x%x\n", result->gl_error);
            goto done;
        }
        bool frame_nonempty = false;
        bool frame_nonuniform = false;
        bool frame_primary_structure_ready = false;
        // The renderer may leave an internal framebuffer bound. Rebind the
        // caller-owned target before every readback, as CineHDR does when it
        // hands the slot to the next consumer.
        glBindFramebuffer(GL_FRAMEBUFFER, framebuffer);
        float readiness_pixels[3][4] = {{0}};
        for (int column = 0; column < 3; column++) {
            const int x = column * (FRAME_WIDTH / PATCH_COLUMNS) +
                          (FRAME_WIDTH / PATCH_COLUMNS) / 2;
            glReadPixels(x, FRAME_HEIGHT * 3 / 4, 1, 1, GL_RGBA,
                         GL_FLOAT, readiness_pixels[column]);
            for (int component = 0; component < 3; component++) {
                frame_nonempty = frame_nonempty ||
                                 readiness_pixels[column][component] != 0.0f;
                frame_nonuniform = frame_nonuniform ||
                                   readiness_pixels[column][component] !=
                                   readiness_pixels[0][0];
            }
        }
        frame_primary_structure_ready =
            readiness_pixels[0][0] > readiness_pixels[0][1] &&
            readiness_pixels[0][0] > readiness_pixels[0][2] &&
            readiness_pixels[1][1] > readiness_pixels[1][0] &&
            readiness_pixels[1][1] > readiness_pixels[1][2] &&
            readiness_pixels[2][2] > readiness_pixels[2][0] &&
            readiness_pixels[2][2] > readiness_pixels[2][1];
        last_frame_nonempty = frame_nonempty;
        last_frame_nonuniform = frame_nonuniform;
        last_primary_structure_ready = frame_primary_structure_ready;
        memcpy(last_readiness_pixels, readiness_pixels,
               sizeof(last_readiness_pixels));
        result->gl_error = glGetError();
        if (result->gl_error != GL_NO_ERROR) {
            fprintf(stderr, "GL error checking frame readiness: 0x%x\n",
                    result->gl_error);
            goto done;
        }
        if (!frame_nonempty || !frame_nonuniform || !frame_primary_structure_ready)
            continue;
        result->rendered_frames = 1;
        break;
    }
    const bool end_file_observed = drain_diagnostic_events(mpv);
    if (!result->rendered_frames) {
        char *ready_pixel_format = mpv_get_property_string(
            mpv, "video-params/pixelformat"
        );
        char *ready_hwdec = mpv_get_property_string(mpv, "hwdec-current");
        char *ready_pause = mpv_get_property_string(mpv, "pause");
        char *ready_time_pos = mpv_get_property_string(mpv, "time-pos");
        fprintf(stderr,
                "timed out waiting for a rendered frame "
                "(nonempty=%s nonuniform=%s primary-structure=%s pixelformat=%s hwdec=%s "
                "pause=%s time-pos=%s update-frame-count=%llu "
                "first-flags=0x%llx first-target-time=%lld "
                "last-flags=0x%llx last-target-time=%lld end-file=%s samples="
                "[%g,%g,%g]/[%g,%g,%g]/[%g,%g,%g])\n",
                last_frame_nonempty ? "yes" : "no",
                last_frame_nonuniform ? "yes" : "no",
                last_primary_structure_ready ? "yes" : "no",
                ready_pixel_format ? ready_pixel_format : "unavailable",
                ready_hwdec ? ready_hwdec : "unavailable",
                ready_pause ? ready_pause : "unavailable",
                ready_time_pos ? ready_time_pos : "unavailable",
                (unsigned long long) update_frame_count,
                (unsigned long long) first_update_frame_flags,
                (long long) first_update_target_time,
                (unsigned long long) last_update_frame_flags,
                (long long) last_update_target_time,
                end_file_observed ? "yes" : "no",
                last_readiness_pixels[0][0], last_readiness_pixels[0][1],
                last_readiness_pixels[0][2], last_readiness_pixels[1][0],
                last_readiness_pixels[1][1], last_readiness_pixels[1][2],
                last_readiness_pixels[2][0], last_readiness_pixels[2][1],
                last_readiness_pixels[2][2]);
        mpv_free(ready_pixel_format);
        mpv_free(ready_hwdec);
        mpv_free(ready_pause);
        mpv_free(ready_time_pos);
        goto done;
    }

    const char *screenshot_path = getenv("CINEHDR_PROBE_SCREENSHOT_PATH");
    if (screenshot_path) {
        const char *shot_cmd[] = {"screenshot-to-file", screenshot_path, "video", NULL};
        mpv_command(mpv, shot_cmd);
    }

    const size_t component_count = (size_t) FRAME_WIDTH * FRAME_HEIGHT * 4;
    glBindFramebuffer(GL_FRAMEBUFFER, framebuffer);
    if (is_hdr) {
        float *pixels = calloc(component_count, sizeof(*pixels));
        if (!pixels) {
            fprintf(stderr, "readback allocation failed: %s\n", strerror(errno));
            goto done;
        }
        glReadPixels(0, 0, FRAME_WIDTH, FRAME_HEIGHT, GL_RGBA, GL_FLOAT, pixels);
        for (int index = 0; index < PATCH_COUNT; index++) {
            const int source_row = index / PATCH_COLUMNS;
            const int sample_row = source_row;
            const int x = (index % PATCH_COLUMNS) * (FRAME_WIDTH / PATCH_COLUMNS) +
                          (FRAME_WIDTH / PATCH_COLUMNS) / 2;
            const int y = sample_row * (FRAME_HEIGHT / PATCH_ROWS) +
                          (FRAME_HEIGHT / PATCH_ROWS) / 2;
            const size_t offset = ((size_t) y * FRAME_WIDTH + x) * 4;
            for (int component = 0; component < 3; component++)
                result->samples[index][component] = pixels[offset + component];
        }
        const int grad_patch = 7;
        const int grad_row = grad_patch / PATCH_COLUMNS;
        const int grad_y = grad_row * (FRAME_HEIGHT / PATCH_ROWS) + (FRAME_HEIGHT / PATCH_ROWS) / 2;
        const int grad_x0 = (grad_patch % PATCH_COLUMNS) * (FRAME_WIDTH / PATCH_COLUMNS) + 5;
        const int grad_x1 = ((grad_patch % PATCH_COLUMNS) + 1) * (FRAME_WIDTH / PATCH_COLUMNS) - 5;
        result->gradient_ramp_count = GRADIENT_RAMP_SAMPLES;
        for (int i = 0; i < GRADIENT_RAMP_SAMPLES; i++) {
            int x = grad_x0 + (int) ((grad_x1 - grad_x0) * (float) i / (float) (GRADIENT_RAMP_SAMPLES - 1));
            size_t off = ((size_t) grad_y * FRAME_WIDTH + x) * 4;
            result->gradient_ramp[i] = (pixels[off + 0] + pixels[off + 1] + pixels[off + 2]) / 3.0f;
        }
        if (sub_file_opt) {
            const int sub_x = FRAME_WIDTH / 2;
            const int sub_y = FRAME_HEIGHT / 6;
            const size_t sub_off = ((size_t) sub_y * FRAME_WIDTH + sub_x) * 4;
            result->subtitle_sample[0] = pixels[sub_off + 0];
            result->subtitle_sample[1] = pixels[sub_off + 1];
            result->subtitle_sample[2] = pixels[sub_off + 2];
            result->subtitle_sampled = true;
        }
        free(pixels);
    } else {
        unsigned char *pixels = calloc(component_count, sizeof(*pixels));
        if (!pixels) {
            fprintf(stderr, "readback allocation failed: %s\n", strerror(errno));
            goto done;
        }
        glReadPixels(0, 0, FRAME_WIDTH, FRAME_HEIGHT, GL_RGBA, GL_UNSIGNED_BYTE, pixels);
        for (int index = 0; index < PATCH_COUNT; index++) {
            const int source_row = index / PATCH_COLUMNS;
            const int sample_row = source_row;
            const int x = (index % PATCH_COLUMNS) * (FRAME_WIDTH / PATCH_COLUMNS) +
                          (FRAME_WIDTH / PATCH_COLUMNS) / 2;
            const int y = sample_row * (FRAME_HEIGHT / PATCH_ROWS) +
                          (FRAME_HEIGHT / PATCH_ROWS) / 2;
            const size_t offset = ((size_t) y * FRAME_WIDTH + x) * 4;
            for (int component = 0; component < 3; component++)
                result->samples[index][component] = pixels[offset + component] / 255.0f;
        }
        const int grad_patch = 7;
        const int grad_row = grad_patch / PATCH_COLUMNS;
        const int grad_y = grad_row * (FRAME_HEIGHT / PATCH_ROWS) + (FRAME_HEIGHT / PATCH_ROWS) / 2;
        const int grad_x0 = (grad_patch % PATCH_COLUMNS) * (FRAME_WIDTH / PATCH_COLUMNS) + 5;
        const int grad_x1 = ((grad_patch % PATCH_COLUMNS) + 1) * (FRAME_WIDTH / PATCH_COLUMNS) - 5;
        result->gradient_ramp_count = GRADIENT_RAMP_SAMPLES;
        for (int i = 0; i < GRADIENT_RAMP_SAMPLES; i++) {
            int x = grad_x0 + (int) ((grad_x1 - grad_x0) * (float) i / (float) (GRADIENT_RAMP_SAMPLES - 1));
            size_t off = ((size_t) grad_y * FRAME_WIDTH + x) * 4;
            result->gradient_ramp[i] = (pixels[off + 0] + pixels[off + 1] + pixels[off + 2]) / (3.0f * 255.0f);
        }
        if (sub_file_opt) {
            const int sub_x = FRAME_WIDTH / 2;
            const int sub_y = FRAME_HEIGHT / 6;
            const size_t sub_off = ((size_t) sub_y * FRAME_WIDTH + sub_x) * 4;
            result->subtitle_sample[0] = pixels[sub_off + 0] / 255.0f;
            result->subtitle_sample[1] = pixels[sub_off + 1] / 255.0f;
            result->subtitle_sample[2] = pixels[sub_off + 2] / 255.0f;
            result->subtitle_sampled = true;
        }
        free(pixels);
    }
    result->gl_error = glGetError();
    if (result->gl_error != GL_NO_ERROR) {
        fprintf(stderr, "GL error after readback: 0x%x\n", result->gl_error);
        goto done;
    }

    result->mpv_version = mpv_get_property_string(mpv, "mpv-version");
    result->ffmpeg_version = mpv_get_property_string(mpv, "ffmpeg-version");
    result->input_primaries = mpv_get_property_string(mpv, "video-params/primaries");
    result->input_transfer = mpv_get_property_string(mpv, "video-params/gamma");
    result->input_matrix = mpv_get_property_string(mpv, "video-params/colormatrix");
    result->input_levels = mpv_get_property_string(mpv, "video-params/colorlevels");
    result->input_pixel_format = mpv_get_property_string(mpv, "video-params/pixelformat");
    result->active_hwdec = mpv_get_property_string(mpv, "hwdec-current");
    result->target_primaries = mpv_get_property_string(mpv, "target-prim");
    result->target_transfer = mpv_get_property_string(mpv, "target-trc");
    result->target_peak = mpv_get_property_string(mpv, "target-peak");
    result->capture_time_pos = mpv_get_property_string(mpv, "time-pos");
    result->capture_pause = mpv_get_property_string(mpv, "pause");
    result->capture_loop_file = mpv_get_property_string(mpv, "loop-file");
    result->capture_update_callback_observed = update_callback_observed;
    record_loaded_library_paths(result);
    if (!has_required_metadata(result)) {
        report_missing_metadata(result);
        goto done;
    }
    for (int patch = 0; patch < PATCH_COUNT; patch++) {
        for (int component = 0; component < 3; component++) {
            if (!isfinite(result->samples[patch][component])) {
                fputs("readback contained a non-finite component\n", stderr);
                goto done;
            }
        }
    }

    const char *bench_str = getenv("CINEHDR_PROBE_BENCHMARK_FRAMES");
    int bench_count = bench_str ? atoi(bench_str) : 0;
    if (result->rendered_frames && bench_count > 0) {
        if (bench_count > 2000)
            bench_count = 2000;
        double *timings = calloc(bench_count, sizeof(double));
        double sum = 0.0;
        int valid_frames = 0;
        mpv_opengl_fbo b_target = {
            .fbo = (int) framebuffer,
            .w = FRAME_WIDTH,
            .h = FRAME_HEIGHT,
            .internal_format = (int) internal_format,
        };
        int b_flip_y = 0;
        int b_block = 0;
        mpv_render_param b_params[] = {
            {MPV_RENDER_PARAM_OPENGL_FBO, &b_target},
            {MPV_RENDER_PARAM_FLIP_Y, &b_flip_y},
            {MPV_RENDER_PARAM_DEPTH, (void *) &depth},
            {MPV_RENDER_PARAM_BLOCK_FOR_TARGET_TIME, &b_block},
            {MPV_RENDER_PARAM_INVALID, NULL},
        };
        for (int i = 0; i < bench_count; i++) {
            struct timespec t0, t1;
            clock_gettime(CLOCK_MONOTONIC, &t0);
            glBindFramebuffer(GL_FRAMEBUFFER, framebuffer);
            glViewport(0, 0, FRAME_WIDTH, FRAME_HEIGHT);
            int r_err = mpv_render_context_render(render_context, b_params);
            glFinish();
            clock_gettime(CLOCK_MONOTONIC, &t1);
            if (r_err >= 0) {
                mpv_render_context_report_swap(render_context);
                double frame_ms = (t1.tv_sec - t0.tv_sec) * 1000.0 + (t1.tv_nsec - t0.tv_nsec) / 1000000.0;
                timings[valid_frames++] = frame_ms;
                sum += frame_ms;
            }
        }
        if (valid_frames > 0) {
            for (int i = 0; i < valid_frames - 1; i++) {
                for (int j = i + 1; j < valid_frames; j++) {
                    if (timings[i] > timings[j]) {
                        double tmp = timings[i];
                        timings[i] = timings[j];
                        timings[j] = tmp;
                    }
                }
            }
            result->benchmark_frame_count = valid_frames;
            result->frame_time_mean_ms = sum / valid_frames;
            result->frame_time_p50_ms = timings[(int)(valid_frames * 0.50)];
            result->frame_time_p95_ms = timings[(int)(valid_frames * 0.95)];
            result->frame_time_p99_ms = timings[(int)(valid_frames * 0.99)];
        }
        free(timings);
        int64_t drops = 0;
        if (mpv_get_property(mpv, "vo-drop-frame-count", MPV_FORMAT_INT64, &drops) >= 0 ||
            mpv_get_property(mpv, "drop-frame-count", MPV_FORMAT_INT64, &drops) >= 0)
        {
            result->dropped_frames = (int) drops;
        }
    }

    status = 0;

done:
    if (texture)
        glDeleteTextures(1, &texture);
    if (framebuffer)
        glDeleteFramebuffers(1, &framebuffer);
    if (render_context)
        mpv_render_context_free(render_context);
    if (mpv)
        mpv_terminate_destroy(mpv);
    return status;
}

static void print_result(const char *mode, const char *api, const char *timestamp,
                         const char *decode_mode, const char *delivery_mode,
                         const struct probe_result *result)
{
    const bool is_hdr = strcmp(mode, "hdr") == 0;
    bool nonempty = false;
    for (int patch = 0; patch < PATCH_COUNT; patch++) {
        for (int component = 0; component < 3; component++)
            nonempty = nonempty || result->samples[patch][component] != 0.0f;
    }
    printf("{\n  \"schema\": \"cinehdr.gate2.probe.v1\",\n");
    printf("  \"requested_api\": \"%s\",\n", api);
    printf("  \"active_api\": \"%s\",\n", api);
    printf("  \"fixture_mode\": \"%s\",\n", mode);
    printf("  \"frame_timestamp\": %s,\n", timestamp);
    fputs("  \"capture\": {\"delivery_mode\": ", stdout);
    json_string(delivery_mode);
    fputs(", \"time_pos\": ", stdout);
    json_string(result->capture_time_pos);
    fputs(", \"pause\": ", stdout);
    json_string(result->capture_pause);
    fputs(", \"loop_file\": ", stdout);
    json_string(result->capture_loop_file);
    printf(", \"update_callback_observed\": %s",
           result->capture_update_callback_observed ? "true" : "false");
    fputs("},\n", stdout);
    fputs("  \"loaded\": {\"libmpv_path\": ", stdout);
    json_string(result->libmpv_path);
    fputs(", \"libplacebo_path\": ", stdout);
    json_string(result->libplacebo_path);
    fputs(", \"mpv_version\": ", stdout);
    json_string(result->mpv_version);
    fputs(", \"libplacebo_version\": ", stdout);
    json_string(result->libplacebo_version);
    fputs(", \"ffmpeg_version\": ", stdout);
    json_string(result->ffmpeg_version);
    fputs("},\n  \"input\": {\"primaries\": ", stdout);
    json_string(result->input_primaries);
    fputs(", \"transfer\": ", stdout);
    json_string(result->input_transfer);
    fputs(", \"matrix\": ", stdout);
    json_string(result->input_matrix);
    fputs(", \"levels\": ", stdout);
    json_string(result->input_levels);
    fputs(", \"pixel_format\": ", stdout);
    json_string(result->input_pixel_format);
    fputs("},\n  \"output\": {\"target_primaries\": ", stdout);
    json_string(result->target_primaries);
    fputs(", \"target_transfer\": ", stdout);
    json_string(result->target_transfer);
    fputs(", \"target_peak\": ", stdout);
    json_string(result->target_peak);
    fputs("},\n  \"fbo\": {", stdout);
    printf("\"width\": %d, \"height\": %d, \"internal_format\": \"%s\", ",
           FRAME_WIDTH, FRAME_HEIGHT, is_hdr ? "GL_RGBA16F" : "GL_RGBA8");
    printf("\"depth\": %d, \"complete\": true},\n", is_hdr ? 16 : 8);
    fputs("  \"gl\": {\"vendor\": ", stdout);
    json_string((const char *) glGetString(GL_VENDOR));
    fputs(", \"renderer\": ", stdout);
    json_string((const char *) glGetString(GL_RENDERER));
    fputs(", \"version\": ", stdout);
    json_string((const char *) glGetString(GL_VERSION));
    fputs("},\n  \"decode\": {\"requested\": ", stdout);
    json_string(decode_mode);
    fputs(", \"active\": ", stdout);
    json_string(result->active_hwdec);
    fputs("},\n  \"readback\": {", stdout);
    printf("\"nonempty\": %s, \"gl_error\": %u, \"flip_y\": false, "
           "\"gl_readback_origin\": \"bottom-left\", "
           "\"source_grid_mapping\": \"source-row-0-to-gl-row-0\", \"patches\": [\n",
           nonempty ? "true" : "false", result->gl_error);
    for (int patch = 0; patch < PATCH_COUNT; patch++) {
        printf("    {\"name\": \"%s\", \"row\": %d, \"column\": %d, "
               "\"r\": %.9g, \"g\": %.9g, \"b\": %.9g}%s\n",
               patch_names[patch], patch / PATCH_COLUMNS, patch % PATCH_COLUMNS,
               result->samples[patch][0], result->samples[patch][1],
               result->samples[patch][2], patch + 1 == PATCH_COUNT ? "" : ",");
    }
    fputs("  ],\n  \"gradient_ramp\": [", stdout);
    for (int i = 0; i < result->gradient_ramp_count; i++) {
        printf("%.9g%s", result->gradient_ramp[i], i + 1 == result->gradient_ramp_count ? "" : ", ");
    }
    fputs("]", stdout);
    if (result->subtitle_sampled) {
        printf(",\n  \"subtitle_sample\": [%.9g, %.9g, %.9g]",
               result->subtitle_sample[0], result->subtitle_sample[1], result->subtitle_sample[2]);
    }
    fputs("},\n  \"errors\": {\"mpv\": null, \"fbo\": null, \"gl\": null},\n", stdout);
    if (result->benchmark_frame_count > 0) {
        printf("  \"performance\": {\n");
        printf("    \"total_frames\": %d,\n", result->benchmark_frame_count);
        printf("    \"mean_ms\": %.4f,\n", result->frame_time_mean_ms);
        printf("    \"p50_ms\": %.4f,\n", result->frame_time_p50_ms);
        printf("    \"p95_ms\": %.4f,\n", result->frame_time_p95_ms);
        printf("    \"p99_ms\": %.4f,\n", result->frame_time_p99_ms);
        printf("    \"dropped_frames\": %d\n", result->dropped_frames);
        printf("  },\n");
    }
    printf("  \"rendered_frames\": %d\n}\n", result->rendered_frames);
}

int main(int argc, char **argv)
{
    if (argc != 7 || (strcmp(argv[2], "hdr") && strcmp(argv[2], "hlg") && strcmp(argv[2], "sdr")) ||
        (strcmp(argv[3], "opengl") && strcmp(argv[3], "opengl-next")) ||
        (strcmp(argv[5], "no") && strcmp(argv[5], "vaapi-copy") && strcmp(argv[5], "auto") && strcmp(argv[5], "vaapi")) ||
        (strcmp(argv[6], "paused") && strcmp(argv[6], "continuous-identical")))
    {
        fprintf(stderr, "Usage: %s <fixture> <hdr|hlg|sdr> <opengl|opengl-next> <timestamp-seconds> <no|vaapi-copy|auto|vaapi> <paused|continuous-identical>\n", argv[0]);
        return 2;
    }
    EGLDisplay display = eglGetDisplay(EGL_DEFAULT_DISPLAY);
    EGLContext context = EGL_NO_CONTEXT;
    EGLSurface surface = EGL_NO_SURFACE;
    int status = 1;
    if (display == EGL_NO_DISPLAY || !eglInitialize(display, NULL, NULL) ||
        !eglBindAPI(EGL_OPENGL_API))
    {
        fputs("could not initialize EGL OpenGL display\n", stderr);
        return 1;
    }
    EGLint config_attributes[] = {
        EGL_SURFACE_TYPE, EGL_PBUFFER_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_BIT,
        EGL_RED_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_BLUE_SIZE, 8, EGL_NONE,
    };
    EGLConfig config = NULL;
    EGLint config_count = 0;
    EGLint pbuffer_attributes[] = {EGL_WIDTH, 1, EGL_HEIGHT, 1, EGL_NONE};
    if (!eglChooseConfig(display, config_attributes, &config, 1, &config_count) ||
        !config_count ||
        (surface = eglCreatePbufferSurface(display, config, pbuffer_attributes)) == EGL_NO_SURFACE ||
        (context = eglCreateContext(display, config, EGL_NO_CONTEXT, NULL)) == EGL_NO_CONTEXT ||
        !eglMakeCurrent(display, surface, surface, context))
    {
        fputs("could not create surfaceless-compatible EGL pbuffer context\n", stderr);
        goto done;
    }
    struct probe_result result = {0};
    if (capture(argv[1], argv[2], argv[3], argv[4], argv[5], argv[6], &result) == 0) {
        print_result(argv[2], argv[3], argv[4], argv[5], argv[6], &result);
        status = 0;
    }
    free_result(&result);

done:
    eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
    if (context != EGL_NO_CONTEXT)
        eglDestroyContext(display, context);
    if (surface != EGL_NO_SURFACE)
        eglDestroySurface(display, surface);
    eglTerminate(display);
    return status;
}
