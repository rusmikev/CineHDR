pkgdatadir = get_option('prefix') / get_option('datadir') / meson.project_name()
moduledir = pkgdatadir / 'cinehdr'
gnome = import('gnome')

blueprint_sources = files(
  'history.blp',
  'playlist.blp',
  'preferences.blp',
  'options.blp',
  'hdr_menu.blp',
  'hdr_diagnostics.blp',
  'shortcuts-dialog.blp',
  'window.blp',
)

blueprints = custom_target('blueprints',
  input: blueprint_sources,
  output: [
    'history.ui',
    'playlist.ui',
    'preferences.ui',
    'options.ui',
    'hdr_menu.ui',
    'hdr_diagnostics.ui',
    'shortcuts-dialog.ui',
    'window.ui',
  ],
  command: [find_program('blueprint-compiler'), 'batch-compile', '@OUTDIR@', '@CURRENT_SOURCE_DIR@', '@INPUT@'],
)

cinehdr_resources = gnome.compile_resources('cinehdr',
  'cinehdr.gresource.xml',
  gresource_bundle: true,
  install: true,
  install_dir: pkgdatadir,
  dependencies: blueprints,
)

python = import('python')

conf = configuration_data()
conf.set('PYTHON', python.find_installation('python3').full_path())
conf.set('VERSION', meson.project_version())
conf.set('localedir', get_option('prefix') / get_option('localedir'))
conf.set('pkgdatadir', pkgdatadir)

configure_file(
  input: 'cinehdr.in',
  output: 'cinehdr',
  configuration: conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x'
)

cine_sources = [
  'shaders/flip-h.glsl',
  'shaders/flip-v.glsl',
  '__init__.py',
  'diagnostics_facts.py',
  'diagnostics_format.py',
  'diagnostics_report.py',
  'gl_bindings.py',
  'gl_renderer.py',
  'gtk_cm_policy.py',
  'hdr_controller.py',
  'hdr_detection.py',
  'hdr_diagnostics.py',
  'hdr_menu.py',
  'history.py',
  'main.py',
  'mpv_gl_area.py',
  'mpris.py',
  'options.py',
  'playlist.py',
  'preferences.py',
  'render_backend.py',
  'save_session.py',
  'shortcuts.py',
  'utils.py',
  'video_widget.py',
  'video_widget_lifecycle.py',
  'wayland_cm_probe.py',
  'wayland_cm_protocol.py',
  'wayland_output_hdr.py',
  'wayland_output_hdr_async.py',
  'wayland_output_hdr_sync.py',
  'window.py',
  'window_controls.py',
  'window_diagnostics.py',
  'window_input.py',
  'window_media.py',
  'window_observers.py',
]

if get_option('validation')
  install_data('validation/__init__.py', install_dir: moduledir / 'validation')
  install_data('../tools/validation/gpu_validation.py', install_dir: moduledir / 'validation')
  install_data('../tools/validation/diagnostics_export.py', install_dir: moduledir / 'validation')
endif

install_data(cine_sources, install_dir: moduledir)
