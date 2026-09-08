project('xepub', version : '1.0.1', meson_version : '>= 0.59.0')

prefix = get_option('prefix')
datadir = get_option('datadir')
bindir = get_option('bindir')
xepub_dir = join_paths(prefix, datadir, 'xepub')

install_data(
  'xepub/main.py',
  'xepub/epub.py',
  'xepub/paginator.py',
  'xepub/preferences.py',
  'xepub/state.py',
  'xepub/window.ui',
  install_dir: xepub_dir,
)

custom_target('xepub_window_versioned',
  input: 'xepub/window.py',
  output: 'window.py',
  command: ['sed', 's/__PROJECT_VERSION__/@0@/g'.format(meson.project_version()), '@INPUT@'],
  capture: true,
  install: true,
  install_dir: xepub_dir,
)

launcher_conf = configuration_data()
launcher_conf.set('xepub_dir', xepub_dir)
configure_file(
  input: 'data/xepub.in',
  output: 'xepub',
  configuration: launcher_conf,
  install: true,
  install_dir: bindir,
  install_mode: 'rwxr-xr-x',
)

subdir('data')
subdir('po')

python3 = find_program('/usr/bin/python3')
test('unit-tests', python3,
  args: ['-m', 'unittest', 'discover', '-s', 'tests', '-v'],
  env: ['PYTHONDONTWRITEBYTECODE=1'],
  workdir: meson.project_source_root(),
)
