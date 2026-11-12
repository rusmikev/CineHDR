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

if not get_option('validation')
  test('product_boundary_tests', python3,
    args: [
      meson.current_source_dir() / 'test_product_boundary.py',
      meson.project_build_root(),
      cinehdr_resources.full_path(),
    ],
    env: [
      'GSETTINGS_SCHEMA_DIR=' + meson.project_source_root() / 'data',
      'GSETTINGS_BACKEND=memory',
      'PYTHONPATH=' + meson.project_source_root(),
    ],
    depends: [cinehdr_resources],
  )
endif

test('render_backend_tests', python3,
  args: [
    meson.current_source_dir() / 'test_render_backend.py'
  ],
  env: [
    'PYTHONPATH=' + meson.project_source_root()
  ]
)

test('gpu_validation_tests', python3,
  # ARM64 under QEMU already approaches Meson's default 30-second limit.
  timeout: 120,
  args: [
    meson.current_source_dir() / 'test_gpu_validation.py'
  ],
  env: [
    'GSETTINGS_SCHEMA_DIR=' + meson.project_source_root() / 'data',
    'CINEHDR_GRESOURCE=' + cinehdr_resources.full_path(),
    'PYTHONPATH=' + meson.project_source_root()
  ],
  depends: [cinehdr_resources]
)

test('wayland_color_trace_tests', python3,
  args: [
    meson.project_source_root() / 'tools/validation/test_wayland_color_trace.py'
  ],
  env: [
    'PYTHONPATH=' + meson.project_source_root() / 'tools/validation' + ':' + meson.project_source_root()
  ]
)

test('color_mgmt_opt_in_tests', python3,
  args: [
    meson.current_source_dir() / 'test_color_mgmt_opt_in.py'
  ],
  env: [
    'PYTHONPATH=' + meson.project_source_root()
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
  # CPU-heavy pixel calculations exceed 30 seconds in ARM64/QEMU CI.
  timeout: 120,
  args: [
    meson.project_source_root() / 'tools/validation/test_pixel_pipeline.py'
  ],
  env: [
    'PYTHONPATH=' + meson.project_source_root()
  ]
)

test('vendor_smoke_tests', python3,
  args: [
    meson.current_source_dir() / 'test_vendor_smoke.py'
  ],
  env: [
    'PYTHONPATH=' + meson.project_source_root()
  ]
)

test('diagnostics_export_tests', python3,
  args: [
    meson.current_source_dir() / 'test_diagnostics_export.py'
  ],
  env: [
    'GSETTINGS_SCHEMA_DIR=' + meson.project_source_root() / 'data',
    'CINEHDR_GRESOURCE=' + cinehdr_resources.full_path(),
    'PYTHONPATH=' + meson.project_source_root()
  ],
  depends: [cinehdr_resources]
)

test('gnome_pipeline_trace_tests', python3,
  args: [
    meson.current_source_dir() / 'test_gnome_pipeline_trace.py'
  ],
  env: [
    'PYTHONPATH=' + meson.project_source_root() / 'tools/validation' + ':' + meson.project_source_root()
  ]
)

test('window_lifecycle_tests', python3,
  args: [
    meson.current_source_dir() / 'test_window_lifecycle.py'
  ],
  env: [
    'GSETTINGS_SCHEMA_DIR=' + meson.project_source_root() / 'data',
    'GSETTINGS_BACKEND=memory',
    'CINEHDR_GRESOURCE=' + cinehdr_resources.full_path(),
    'PYTHONPATH=' + meson.project_source_root()
  ],
  depends: [cinehdr_resources]
)
