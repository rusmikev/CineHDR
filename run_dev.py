#!/usr/bin/env python3
import os
import sys
import gi

# Insert the script directory at the beginning of the python path
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

# Point GSettings to the installed Flatpak schemas directory
schema_dir = '/var/lib/flatpak/app/io.github.diegopvlk.Cine/x86_64/stable/cd135e5025e8076cfadcc1c65a8361e64bdbbd8858660eb5b0b30cf2cd5260d4/files/share/glib-2.0/schemas'
os.environ['GSETTINGS_SCHEMA_DIR'] = schema_dir

# Load the gresource from the installed Flatpak
gresource_path = '/var/lib/flatpak/app/io.github.diegopvlk.Cine/x86_64/stable/cd135e5025e8076cfadcc1c65a8361e64bdbbd8858660eb5b0b30cf2cd5260d4/files/share/cine/cine.gresource'

gi.require_version('Gio', '2.0')
from gi.repository import Gio

try:
    resource = Gio.Resource.load(gresource_path)
    resource._register()
except Exception as e:
    print(f"Failed to load gresource: {e}")
    sys.exit(1)

from cine import main
sys.exit(main.main('1.7.1-hdr'))
