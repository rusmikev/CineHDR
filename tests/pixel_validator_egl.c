#define GL_GLEXT_PROTOTYPES
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <EGL/egl.h>
#include <GL/gl.h>
#include <mpv/client.h>
#include <mpv/render_gl.h>

static void *get_proc_address_mpv(void *fn_ctx, const char *name) {
    return (void *)eglGetProcAddress(name);
}

int main(int argc, char *argv[]) {
    if (argc < 2) {
        fprintf(stderr, "Usage: %s <video_file> [target_peak_nits]\n", argv[0]);
        return 1;
    }
    const char *video_file = argv[1];
    double target_peak = 1000.0;
    if (argc >= 3) target_peak = atof(argv[2]);

    EGLDisplay display = eglGetDisplay(EGL_DEFAULT_DISPLAY);
    eglInitialize(display, NULL, NULL);
    eglBindAPI(EGL_OPENGL_API);

    EGLint config_attribs[] = {
        EGL_SURFACE_TYPE, EGL_PBUFFER_BIT,
        EGL_BLUE_SIZE, 8, EGL_GREEN_SIZE, 8, EGL_RED_SIZE, 8, EGL_DEPTH_SIZE, 8,
        EGL_RENDERABLE_TYPE, EGL_OPENGL_BIT,
        EGL_NONE
    };
    EGLConfig config;
    EGLint num_config;
    eglChooseConfig(display, config_attribs, &config, 1, &num_config);

    EGLint context_attribs[] = {
        EGL_CONTEXT_MAJOR_VERSION, 3, EGL_CONTEXT_MINOR_VERSION, 3,
        EGL_CONTEXT_OPENGL_PROFILE_MASK, EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT,
        EGL_NONE
    };
    EGLContext context = eglCreateContext(display, config, EGL_NO_CONTEXT, context_attribs);
    eglMakeCurrent(display, EGL_NO_SURFACE, EGL_NO_SURFACE, context);

    mpv_handle *mpv = mpv_create();
    mpv_set_option_string(mpv, "vo", "libmpv");
    mpv_set_option_string(mpv, "audio", "no");
    mpv_set_option_string(mpv, "hwdec", "auto-copy");
    
    mpv_set_option_string(mpv, "target-trc", "pq");
    mpv_set_option_string(mpv, "target-prim", "bt.2020");
    char peak_str[32];
    snprintf(peak_str, sizeof(peak_str), "%f", target_peak);
    mpv_set_option_string(mpv, "target-peak", peak_str);

    mpv_initialize(mpv);

    mpv_opengl_init_params gl_init_params = {
        .get_proc_address = get_proc_address_mpv,
        .get_proc_address_ctx = NULL,
    };
    mpv_render_param params[] = {
        {MPV_RENDER_PARAM_API_TYPE, MPV_RENDER_API_TYPE_OPENGL},
        {MPV_RENDER_PARAM_OPENGL_INIT_PARAMS, &gl_init_params},
        {MPV_RENDER_PARAM_INVALID, NULL}
    };
    mpv_render_context *mpv_ctx;
    mpv_render_context_create(&mpv_ctx, mpv, params);

    GLuint fbo, texture;
    glGenFramebuffers(1, &fbo);
    glBindFramebuffer(GL_FRAMEBUFFER, fbo);

    glGenTextures(1, &texture);
    glBindTexture(GL_TEXTURE_2D, texture);
    
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA16F, 1280, 720, 0, GL_RGBA, GL_FLOAT, NULL);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, texture, 0);

    const char *cmd[] = {"loadfile", video_file, NULL};
    mpv_command(mpv, cmd);

    int frames_rendered = 0;
    while (frames_rendered < 5) {
        mpv_event *event = mpv_wait_event(mpv, 0.1);
        if (event->event_id == MPV_EVENT_END_FILE) break;
        
        mpv_opengl_fbo mpv_fbo = {
            .fbo = fbo,
            .w = 1280, .h = 720,
            .internal_format = GL_RGBA16F
        };
        mpv_render_param render_params[] = {
            {MPV_RENDER_PARAM_OPENGL_FBO, &mpv_fbo},
            {MPV_RENDER_PARAM_INVALID, NULL}
        };
        
        int update = mpv_render_context_update(mpv_ctx);
        if (update & MPV_RENDER_UPDATE_FRAME) {
            mpv_render_context_render(mpv_ctx, render_params);
            frames_rendered++;
            glFinish();
        }
    }

    float *pixels = malloc(1280 * 720 * 4 * sizeof(float));
    glReadPixels(0, 0, 1280, 720, GL_RGBA, GL_FLOAT, pixels);

    // Read the grid of patches (5x2 grid)
    int cols = 5;
    int rows = 2;
    int patch_w = 1280 / cols;
    int patch_h = 720 / rows;
    
    printf("[\n");
    for (int r = 0; r < rows; r++) {
        for (int c = 0; c < cols; c++) {
            // Get center pixel of the patch
            // Note: glReadPixels reads from bottom-left, but mpv might flip it, let's just sample
            int px = c * patch_w + patch_w / 2;
            int py = r * patch_h + patch_h / 2; // Might need to invert Y if FBO is flipped
            
            float pr = pixels[(py * 1280 + px) * 4 + 0];
            float pg = pixels[(py * 1280 + px) * 4 + 1];
            float pb = pixels[(py * 1280 + px) * 4 + 2];
            printf("  {\"row\": %d, \"col\": %d, \"r\": %f, \"g\": %f, \"b\": %f}%s\n", r, c, pr, pg, pb, (r==rows-1 && c==cols-1) ? "" : ",");
        }
    }
    printf("]\n");

    free(pixels);
    mpv_render_context_free(mpv_ctx);
    mpv_terminate_destroy(mpv);
    
    eglDestroyContext(display, context);
    eglTerminate(display);
    
    return 0;
}
