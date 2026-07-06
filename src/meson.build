pkgdatadir = get_option('prefix') / get_option('datadir') / meson.project_name()
moduledir = pkgdatadir / 'cinehdr'
gnome = import('gnome')

blueprints = custom_target('blueprints',
  input: files(
    'history.blp',
    'playlist.blp',
    'preferences.blp',
    'options.blp',
    'hdr_menu.blp',
    'hdr_diagnostics.blp',
    'shortcuts-dialog.blp',
    'window.blp',
  ),
  output: '.',
  command: [find_program('blueprint-compiler'), 'batch-compile', '@OUTPUT@', '@CURRENT_SOURCE_DIR@', '@INPUT@'],
)

gnome.compile_resources('cinehdr',
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
  '__init__.py',
  'gl_renderer.py',
  'hdr_controller.py',
  'hdr_detection.py',
  'hdr_diagnostics.py',
  'hdr_menu.py',
  'history.py',
  'main.py',
  'mpris.py',
  'options.py',
  'playlist.py',
  'preferences.py',
  'save_session.py',
  'shortcuts.py',
  'utils.py',
  'video_widget.py',
  'window.py',
]

install_data(cine_sources, install_dir: moduledir)
