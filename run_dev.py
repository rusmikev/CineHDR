#!/usr/bin/env python3
import os
import sys
import subprocess
import gi

# Insert the script directory at the beginning of the python path
root_dir = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, root_dir)

# Keep the development build current before loading its resources. Meson is
# incremental, so this is cheap when nothing changed and prevents a running
# test build from silently using stale Blueprint-generated UI files.
build_dir = os.path.join(root_dir, "build")
gresource_path = os.path.join(build_dir, "src", "cinehdr.gresource")
data_dir = os.path.join(root_dir, "data")
schema_build_dir = os.path.join(build_dir, "data")

try:
    if not os.path.exists(os.path.join(build_dir, "build.ninja")):
        subprocess.run(["meson", "setup", "build"], cwd=root_dir, check=True)
    subprocess.run(["meson", "compile", "-C", "build"], cwd=root_dir, check=True)
except (FileNotFoundError, subprocess.CalledProcessError):
    # Fallback when meson is not available in Flatpak runtime
    os.makedirs(os.path.join(build_dir, "src"), exist_ok=True)
    for blp in ["src/hdr_diagnostics.blp", "src/hdr_menu.blp", "src/history.blp", "src/options.blp", "src/playlist.blp", "src/preferences.blp", "src/shortcuts-dialog.blp", "src/window.blp"]:
        blp_path = os.path.join(root_dir, blp)
        if os.path.exists(blp_path):
            ui_out = os.path.join(build_dir, "src", os.path.basename(blp).replace(".blp", ".ui"))
            subprocess.run(["blueprint-compiler", "compile", blp_path, "--output", ui_out], check=False)
    if os.path.exists(os.path.join(root_dir, "src", "cinehdr.gresource.xml")):
        subprocess.run(["glib-compile-resources", "src/cinehdr.gresource.xml", "--sourcedir=build/src", "--sourcedir=src", f"--target={gresource_path}"], cwd=root_dir, check=False)

# Compile the development GSettings schema into the build tree.  Pointing
# GSETTINGS_SCHEMA_DIR at the source XML is not enough: Gio only reads the
# compiled gschemas.compiled file.
os.makedirs(schema_build_dir, exist_ok=True)
try:
    subprocess.run(
        ["glib-compile-schemas", "--targetdir", schema_build_dir, data_dir],
        cwd=root_dir,
        check=True,
    )
except Exception:
    pass

# Point GSettings and XDG_DATA_DIRS to local project data.
os.environ['GSETTINGS_SCHEMA_DIR'] = schema_build_dir
xdg_data = os.environ.get('XDG_DATA_DIRS', '/usr/local/share:/usr/share')
os.environ['XDG_DATA_DIRS'] = f"{data_dir}:{xdg_data}"

# Set up local translations
locale_dir = os.path.join(build_dir, "po")
try:
    import locale
    import gettext
    locale.setlocale(locale.LC_ALL, '')
    locale.bindtextdomain('cine', locale_dir)
    locale.textdomain('cine')
    gettext.bindtextdomain('cine', locale_dir)
    gettext.textdomain('cine')
except Exception as e:
    print(f"Cannot set locale/translations: {e}")

gi.require_version('Gio', '2.0')
gi.require_version('GLib', '2.0')
from gi.repository import Gio, GLib

try:
    resource = Gio.Resource.load(gresource_path)
    resource._register()
except Exception as e:
    print(f"Failed to load gresource: {e}")
    sys.exit(1)

from gettext import gettext as _
GLib.set_prgname('cinehdr')
GLib.set_application_name(_('CineHDR'))

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Gdk
from src import main

app = main.CineApplication()
def _on_startup(application):
    display = Gdk.Display.get_default()
    if display:
        icon_theme = Gtk.IconTheme.get_for_display(display)
        icons_path = os.path.join(root_dir, "data", "icons")
        if os.path.exists(icons_path):
            icon_theme.add_search_path(icons_path)
app.connect('startup', _on_startup)
sys.exit(app.run(sys.argv))
