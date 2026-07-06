python3 = find_program('python3')

test('hdr_tests', python3,
  args: [
    meson.current_source_dir() / 'test_hdr.py'
  ],
  env: [
    'GSETTINGS_SCHEMA_DIR=' + meson.project_source_root() / 'data'
  ]
)
