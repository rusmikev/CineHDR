#!/usr/bin/env python3
import os
import sys
import subprocess
import gi

# Insert the script directory at the beginning of the python path
root_dir = os.path.abspath(os.path.dirname(__file__))
sys.path.insert(0, root_dir)

# Ensure build directory and gresource are compiled
build_dir = os.path.join(root_dir, "build")
gresource_path = os.path.join(build_dir, "src", "cinehdr.gresource")
data_dir = os.path.join(root_dir, "data")

if not os.path.exists(gresource_path):
    print("Compiling resources with Meson...")
    if not os.path.exists(build_dir):
        subprocess.run(["meson", "setup", "build"], cwd=root_dir, check=True)
    subprocess.run(["meson", "compile", "-C", "build"], cwd=root_dir, check=True)

# Point GSettings and XDG_DATA_DIRS to local data directory
os.environ['GSETTINGS_SCHEMA_DIR'] = data_dir
xdg_data = os.environ.get('XDG_DATA_DIRS', '/usr/local/share:/usr/share')
os.environ['XDG_DATA_DIRS'] = f"{data_dir}:{xdg_data}"

gi.require_version('Gio', '2.0')
from gi.repository import Gio

try:
    resource = Gio.Resource.load(gresource_path)
    resource._register()
except Exception as e:
    print(f"Failed to load gresource: {e}")
    sys.exit(1)

from src import main
sys.exit(main.main('1.7.1-hdr-dev'))
