python3 = find_program('python3')

test('hdr_tests', python3,
  args: [
    meson.current_source_dir() / 'test_hdr.py'
  ],
  env: [
    'GSETTINGS_SCHEMA_DIR=' + meson.project_source_root() / 'data',
    'CINEHDR_GRESOURCE=' + cinehdr_resources.full_path(),
    'PYTHONPATH=' + meson.project_source_root()
  ],
  depends: [cinehdr_resources]
)

test('render_backend_tests', python3,
  args: [
    meson.current_source_dir() / 'test_render_backend.py'
  ],
  env: [
    'PYTHONPATH=' + meson.project_source_root()
  ]
)

test('gpu_validation_tests', python3,
  args: [
    meson.current_source_dir() / 'test_gpu_validation.py'
  ],
  env: [
    'PYTHONPATH=' + meson.project_source_root()
  ]
)

test('wayland_color_trace_tests', python3,
  args: [
    meson.current_source_dir() / 'test_wayland_color_trace.py'
  ],
  env: [
    'PYTHONPATH=' + meson.current_source_dir()
  ]
)

test('flatpak_manifest_tests', python3,
  args: [
    meson.current_source_dir() / 'test_flatpak_manifest.py'
  ]
)

# ADR-0004's EGL probe is explicit; this static/unit suite must not require a
# driver, a sibling mpv build, generated media, or a display server.
test('pixel_pipeline_tests', python3,
  args: [
    meson.current_source_dir() / 'test_pixel_pipeline.py'
  ],
  env: [
    'PYTHONPATH=' + meson.project_source_root()
  ]
)
