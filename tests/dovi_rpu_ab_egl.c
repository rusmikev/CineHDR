// Development-only Dolby Vision RPU A/B probe for CineHDR.
//
// Renders the same paused frame twice through the experimental embedded
// opengl-next API: once normally and once with vf=format=dolbyvision=no.
// A measurable difference proves that the Dolby Vision path affects pixels;
// it does not prove that either result is color-accurate.

#define _POSIX_C_SOURCE 200809L
#define GL_GLEXT_PROTOTYPES

#include <errno.h>
#include <math.h>
#include <stdbool.h>
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
#error "This validator requires the pinned experimental mpv headers"
#endif

enum {
    CAPTURE_WIDTH = 960,
    CAPTURE_HEIGHT = 540,
};

struct capture {
    float *pixels;
    char *color_matrix;
    char *hwdec;
    char *mpv_version;
    char *ffmpeg_version;
    int rendered_frames;
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
    putchar('"');
    for (const unsigned char *p = (const unsigned char *) value; *p; p++) {
        switch (*p) {
        case '\\': fputs("\\\\", stdout); break;
        case '"': fputs("\\\"", stdout); break;
        case '\n': fputs("\\n", stdout); break;
        case '\r': fputs("\\r", stdout); break;
        case '\t': fputs("\\t", stdout); break;
        default:
            if (*p < 0x20)
                printf("\\u%04x", *p);
            else
                putchar(*p);
        }
    }
    putchar('"');
}

static void destroy_capture(struct capture *capture)
{
    free(capture->pixels);
    mpv_free(capture->color_matrix);
    mpv_free(capture->hwdec);
    mpv_free(capture->mpv_version);
    mpv_free(capture->ffmpeg_version);
    *capture = (struct capture) {0};
}

static int set_option(mpv_handle *mpv, const char *name, const char *value)
{
    int error = mpv_set_option_string(mpv, name, value);
    if (error < 0) {
        fprintf(stderr, "Failed setting %s=%s: %s\n", name, value,
                mpv_error_string(error));
    }
    return error;
}

static int render_capture(const char *video_file, const char *timestamp,
                          const char *target_peak, const char *hwdec,
                          bool dolby_vision, struct capture *out)
{
    int result = -1;
    mpv_handle *mpv = NULL;
    mpv_render_context *render_context = NULL;
    GLuint framebuffer = 0;
    GLuint texture = 0;

    mpv = mpv_create();
    if (!mpv) {
        fputs("mpv_create failed\n", stderr);
        goto done;
    }

    if (set_option(mpv, "vo", "libmpv") < 0 ||
        set_option(mpv, "audio", "no") < 0 ||
        set_option(mpv, "pause", "yes") < 0 ||
        set_option(mpv, "hr-seek", "yes") < 0 ||
        set_option(mpv, "start", timestamp) < 0 ||
        set_option(mpv, "hwdec", hwdec) < 0 ||
        set_option(mpv, "target-trc", "pq") < 0 ||
        set_option(mpv, "target-prim", "bt.2020") < 0 ||
        set_option(mpv, "target-peak", target_peak) < 0)
    {
        goto done;
    }
    if (!dolby_vision &&
        set_option(mpv, "vf", "format=dolbyvision=no") < 0)
    {
        goto done;
    }

    int error = mpv_initialize(mpv);
    if (error < 0) {
        fprintf(stderr, "mpv_initialize failed: %s\n", mpv_error_string(error));
        goto done;
    }

    mpv_opengl_init_params init_params = {
        .get_proc_address = get_proc_address,
    };
    mpv_render_param create_params[] = {
        {MPV_RENDER_PARAM_API_TYPE, (void *) MPV_RENDER_API_TYPE_OPENGL_NEXT},
        {MPV_RENDER_PARAM_OPENGL_INIT_PARAMS, &init_params},
        {MPV_RENDER_PARAM_INVALID, NULL},
    };
    error = mpv_render_context_create(&render_context, mpv, create_params);
    if (error < 0) {
        fprintf(stderr, "opengl-next context creation failed: %s\n",
                mpv_error_string(error));
        goto done;
    }

    glGenFramebuffers(1, &framebuffer);
    glBindFramebuffer(GL_FRAMEBUFFER, framebuffer);
    glGenTextures(1, &texture);
    glBindTexture(GL_TEXTURE_2D, texture);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA16F, CAPTURE_WIDTH,
                 CAPTURE_HEIGHT, 0, GL_RGBA, GL_FLOAT, NULL);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                           GL_TEXTURE_2D, texture, 0);
    if (glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE) {
        fputs("RGBA16F framebuffer is incomplete\n", stderr);
        goto done;
    }

    const char *load[] = {"loadfile", video_file, NULL};
    error = mpv_command(mpv, load);
    if (error < 0) {
        fprintf(stderr, "loadfile failed: %s\n", mpv_error_string(error));
        goto done;
    }

    double deadline = monotonic_seconds() + 30.0;
    while (monotonic_seconds() < deadline) {
        mpv_event *event = mpv_wait_event(mpv, 0.02);
        if (event->event_id == MPV_EVENT_END_FILE) {
            fputs("Video ended before a frame was captured\n", stderr);
            goto done;
        }

        uint64_t update = mpv_render_context_update(render_context);
        if (!(update & MPV_RENDER_UPDATE_FRAME))
            continue;

        mpv_opengl_fbo target = {
            .fbo = (int) framebuffer,
            .w = CAPTURE_WIDTH,
            .h = CAPTURE_HEIGHT,
            .internal_format = GL_RGBA16F,
        };
        int depth = 16;
        mpv_render_param frame_params[] = {
            {MPV_RENDER_PARAM_OPENGL_FBO, &target},
            {MPV_RENDER_PARAM_DEPTH, &depth},
            {MPV_RENDER_PARAM_INVALID, NULL},
        };
        glBindFramebuffer(GL_FRAMEBUFFER, framebuffer);
        glViewport(0, 0, CAPTURE_WIDTH, CAPTURE_HEIGHT);
        error = mpv_render_context_render(render_context, frame_params);
        if (error < 0) {
            fprintf(stderr, "render failed: %s\n", mpv_error_string(error));
            goto done;
        }
        out->rendered_frames++;
        glFinish();
        if (out->rendered_frames >= 1)
            break;
    }

    if (!out->rendered_frames) {
        fputs("Timed out waiting for a rendered frame\n", stderr);
        goto done;
    }

    size_t components = (size_t) CAPTURE_WIDTH * CAPTURE_HEIGHT * 4;
    out->pixels = calloc(components, sizeof(*out->pixels));
    if (!out->pixels) {
        fprintf(stderr, "Pixel allocation failed: %s\n", strerror(errno));
        goto done;
    }
    glBindFramebuffer(GL_FRAMEBUFFER, framebuffer);
    glReadPixels(0, 0, CAPTURE_WIDTH, CAPTURE_HEIGHT, GL_RGBA, GL_FLOAT,
                 out->pixels);
    out->color_matrix = mpv_get_property_string(
        mpv, "video-out-params/colormatrix"
    );
    out->hwdec = mpv_get_property_string(mpv, "hwdec-current");
    out->mpv_version = mpv_get_property_string(mpv, "mpv-version");
    out->ffmpeg_version = mpv_get_property_string(mpv, "ffmpeg-version");
    result = 0;

done:
    if (texture)
        glDeleteTextures(1, &texture);
    if (framebuffer)
        glDeleteFramebuffers(1, &framebuffer);
    if (render_context)
        mpv_render_context_free(render_context);
    if (mpv)
        mpv_terminate_destroy(mpv);
    if (result < 0)
        destroy_capture(out);
    return result;
}

int main(int argc, char **argv)
{
    if (argc < 4 || argc > 5) {
        fprintf(stderr,
                "Usage: %s <video-file> <timestamp-seconds> "
                "<target-peak-nits> [hwdec]\n",
                argv[0]);
        return 2;
    }

    const char *hwdec = argc == 5 ? argv[4] : "no";
    EGLDisplay display = eglGetDisplay(EGL_DEFAULT_DISPLAY);
    if (display == EGL_NO_DISPLAY || !eglInitialize(display, NULL, NULL)) {
        fputs("Could not initialize EGL display\n", stderr);
        return 1;
    }
    if (!eglBindAPI(EGL_OPENGL_API)) {
        fputs("Could not select the OpenGL EGL API\n", stderr);
        eglTerminate(display);
        return 1;
    }

    EGLint config_attributes[] = {
        EGL_SURFACE_TYPE, EGL_PBUFFER_BIT,
        EGL_RENDERABLE_TYPE, EGL_OPENGL_BIT,
        EGL_RED_SIZE, 8,
        EGL_GREEN_SIZE, 8,
        EGL_BLUE_SIZE, 8,
        EGL_NONE,
    };
    EGLConfig config = NULL;
    EGLint config_count = 0;
    if (!eglChooseConfig(display, config_attributes, &config, 1,
                         &config_count) || !config_count)
    {
        fputs("No suitable EGL configuration\n", stderr);
        eglTerminate(display);
        return 1;
    }
    EGLint pbuffer_attributes[] = {
        EGL_WIDTH, 1,
        EGL_HEIGHT, 1,
        EGL_NONE,
    };
    EGLSurface surface = eglCreatePbufferSurface(
        display, config, pbuffer_attributes
    );
    EGLContext context = eglCreateContext(
        display, config, EGL_NO_CONTEXT, NULL
    );
    if (surface == EGL_NO_SURFACE || context == EGL_NO_CONTEXT ||
        !eglMakeCurrent(display, surface, surface, context))
    {
        fputs("Could not create the EGL OpenGL context\n", stderr);
        if (context != EGL_NO_CONTEXT)
            eglDestroyContext(display, context);
        if (surface != EGL_NO_SURFACE)
            eglDestroySurface(display, surface);
        eglTerminate(display);
        return 1;
    }

    struct capture enabled = {0};
    struct capture disabled = {0};
    int result = 1;
    if (render_capture(argv[1], argv[2], argv[3], hwdec, true, &enabled) < 0 ||
        render_capture(argv[1], argv[2], argv[3], hwdec, false, &disabled) < 0)
    {
        goto done;
    }

    size_t pixels = (size_t) CAPTURE_WIDTH * CAPTURE_HEIGHT;
    double absolute_sum = 0.0;
    double square_sum = 0.0;
    double maximum = 0.0;
    size_t changed = 0;
    for (size_t pixel = 0; pixel < pixels; pixel++) {
        bool pixel_changed = false;
        for (size_t channel = 0; channel < 3; channel++) {
            size_t index = pixel * 4 + channel;
            double delta = fabs(
                enabled.pixels[index] - disabled.pixels[index]
            );
            absolute_sum += delta;
            square_sum += delta * delta;
            if (delta > maximum)
                maximum = delta;
            if (delta > 1e-5)
                pixel_changed = true;
        }
        if (pixel_changed)
            changed++;
    }

    double samples = pixels * 3.0;
    double mean_absolute_error = absolute_sum / samples;
    double root_mean_square_error = sqrt(square_sum / samples);
    double changed_ratio = (double) changed / pixels;
    bool matrix_transition =
        enabled.color_matrix && disabled.color_matrix &&
        strstr(enabled.color_matrix, "dolbyvision") &&
        !strstr(disabled.color_matrix, "dolbyvision");
    bool effect_observed = matrix_transition && changed_ratio > 0.001;

    fputs("{\n  \"api\": \"opengl-next\",\n", stdout);
    fputs("  \"gl_vendor\": ", stdout);
    json_string((const char *) glGetString(GL_VENDOR));
    fputs(",\n  \"gl_renderer\": ", stdout);
    json_string((const char *) glGetString(GL_RENDERER));
    fputs(",\n  \"gl_version\": ", stdout);
    json_string((const char *) glGetString(GL_VERSION));
    fputs(",\n  \"mpv_version\": ", stdout);
    json_string(enabled.mpv_version ? enabled.mpv_version : "unknown");
    fputs(",\n  \"ffmpeg_version\": ", stdout);
    json_string(enabled.ffmpeg_version ? enabled.ffmpeg_version : "unknown");
    fputs(",\n  \"requested_hwdec\": ", stdout);
    json_string(hwdec);
    fputs(",\n  \"active_hwdec\": ", stdout);
    json_string(enabled.hwdec ? enabled.hwdec : "none");
    fputs(",\n  \"dolby_vision_enabled_matrix\": ", stdout);
    json_string(enabled.color_matrix ? enabled.color_matrix : "unknown");
    fputs(",\n  \"dolby_vision_disabled_matrix\": ", stdout);
    json_string(disabled.color_matrix ? disabled.color_matrix : "unknown");
    printf(",\n  \"width\": %d,\n  \"height\": %d,\n",
           CAPTURE_WIDTH, CAPTURE_HEIGHT);
    printf("  \"mean_absolute_error\": %.9g,\n", mean_absolute_error);
    printf("  \"root_mean_square_error\": %.9g,\n", root_mean_square_error);
    printf("  \"maximum_channel_delta\": %.9g,\n", maximum);
    printf("  \"changed_pixel_ratio\": %.9g,\n", changed_ratio);
    printf("  \"rpu_effect_observed\": %s,\n",
           effect_observed ? "true" : "false");
    fputs("  \"interpretation\": ", stdout);
    json_string(
        effect_observed
            ? "RPU path changed rendered pixels; color validation still pending"
            : "No controlled RPU effect proved at this timestamp"
    );
    fputs("\n}\n", stdout);
    result = effect_observed ? 0 : 3;

done:
    destroy_capture(&enabled);
    destroy_capture(&disabled);
    eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
    eglDestroyContext(display, context);
    eglDestroySurface(display, surface);
    eglTerminate(display);
    return result;
}
