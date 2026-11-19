"""Behavioral characterization tests for CineWindow prior to module decomposition (C24)."""

import atexit
import hashlib
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("GSETTINGS_BACKEND", "memory")

_temp_schema_dir = tempfile.mkdtemp(prefix="cinehdr-win-test-schema-")
_schema_src = os.path.abspath(os.path.join(os.path.dirname(__file__), "../data"))
for _schema_file in os.listdir(_schema_src):
    if _schema_file.endswith(".gschema.xml"):
        shutil.copy2(os.path.join(_schema_src, _schema_file), _temp_schema_dir)
subprocess.run(["glib-compile-schemas", _temp_schema_dir], check=True)
os.environ["GSETTINGS_SCHEMA_DIR"] = _temp_schema_dir
atexit.register(lambda: shutil.rmtree(_temp_schema_dir, ignore_errors=True))

import gi  # noqa: E402

gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, Gtk  # noqa: E402


def _ensure_gresource_registered() -> None:
    gresource_path = os.environ.get("CINEHDR_GRESOURCE")
    if not gresource_path:
        candidate = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "../build/src/cinehdr.gresource")
        )
        if os.path.exists(candidate):
            gresource_path = candidate
    if gresource_path and os.path.exists(gresource_path):
        resource = Gio.Resource.load(gresource_path)
        Gio.resources_register(resource)


_ensure_gresource_registered()

from src.diagnostics_report import (  # noqa: E402
    DIAGNOSTICS_EXPORT_PATH_ENV,
    generate_video_output_report,
)
from src.utils import PrimaryClick, SecondaryClick  # noqa: E402
from src.window import CineWindow, make_mpv_log_handler, safe_mpv_file_error_label  # noqa: E402


def _make_blank_window() -> CineWindow:
    win = CineWindow.__new__(CineWindow)
    win.app = MagicMock()
    win.app.get_is_registered.return_value = True
    win._mpris = MagicMock()
    win.mpv = MagicMock()
    win.mpv.playlist = []
    win.mpv.user_data = {}
    win._video_area = MagicMock()
    win._visible_dialog = None
    win.playlist_ls = Gio.ListStore()
    win._playlist_debounce_id = 0
    win._playlist_prev_pos = 0
    win.prev_shuffle = False
    win.playlist_changed = False
    win.has_some_doc_path = False
    win.can_go_prev = False
    win.can_go_next = False
    win._loop_mode = "off"
    win._chapters = []
    win._curr_chapter_time = None
    win._actions = {}
    win._prev_motion_xy = (0, 0)
    win._hover_time = 0.0
    win._show_remaining = False
    win._prev_prog_time = -1.0
    win._prev_prog_motion_xy = (0, 0)
    win._inhibit_cookie = 0
    win._video_path = None
    win._is_startup = True
    win._is_audio = False
    win._space_hold_id = 0
    win._space_holding = False
    win._space_pressed = False
    win._left_clk = PrimaryClick.PLAY_PAUSE
    win._right_clk = SecondaryClick.CONTEXT_MENU
    win._click_delay_id = 0
    win._click_time = 200
    win._click_holding = False
    win._prev_speed = 1.0
    win._wheel_accum_x = 0.0
    win._wheel_accum_y = 0.0
    win._hide_icon_indicator = True
    win.skip_pause_obs_count = 0
    win._playing_on_press = False
    win._duration = 0.0
    win.thumb_area = None
    win._thumb_w = 1280
    win.is_local_path = True
    win._prog_fine_tune = False
    win._error_count = 0
    win._pressed_combos = set()
    win._hide_timeout_id = 0
    win._is_fullscreen = False
    win._is_inactive = False
    win._is_gpu_validation = False
    win.dovi_rpu_warning_count = 0
    win.dovi_rpu_warning_text = None
    win._dovi_rpu_logged = False
    win._shutting_down = False
    win._media_load_generation = 0
    win._diagnostics_exporter = None
    win._mouse_binds = {}
    win._nonrepeat_keys = set()
    win._has_enter_binding = False
    win._has_kp_enter_binding = False
    win._ab_loop_a = None
    win._ab_loop_b = None
    win.spinner = MagicMock()
    win.start_page = MagicMock()
    win.revealer_ui = MagicMock()
    win.revealer_drop_indicator = MagicMock()
    win.revealer_icon_indicator = MagicMock()
    win.icon_indicator = MagicMock()
    win.drop_label = MagicMock()
    win.drop_icon = MagicMock()
    win.title_widget = MagicMock()
    win.headerbar = MagicMock()
    win.controls_box = MagicMock()
    win.audio_only_icon = MagicMock()
    win.context_popover_menu = MagicMock()
    win.primary_menu_btn = MagicMock()
    win.open_menu_btn = MagicMock()
    win.options_menu_btn = MagicMock()
    win.hdr_menu_btn = MagicMock()
    win.volume_menu_btn = MagicMock()
    win.mute_toggle_btn = MagicMock()
    win.volume_scale = MagicMock()
    win.volume_scale_adj = MagicMock()
    win.subtitles_menu_btn = MagicMock()
    win.subtitles_menu = Gio.Menu()
    win.audio_tracks_menu_btn = MagicMock()
    win.audio_tracks_menu = Gio.Menu()
    win.video_tracks_menu_btn = MagicMock()
    win.video_tracks_menu = Gio.Menu()
    win.chapters_menu_btn = MagicMock()
    win.chapters_menu = Gio.Menu()
    win.previous_btn = MagicMock()
    win.play_pause_btn = MagicMock()
    win.next_btn = MagicMock()
    win.shuffle_toggle_btn = MagicMock()
    win.ab_loop_btn = MagicMock()
    win.loop_btn = MagicMock()
    win.fullscreen_btn = MagicMock()
    win.time_elapsed_label = MagicMock()
    win.time_total_label = MagicMock()
    win.video_progress_scale = MagicMock()
    win.video_progress_adj = MagicMock()
    win.tooltip_thumb_revealer = MagicMock()
    win.tooltip_label_revealer = MagicMock()
    win.toast_overlay = MagicMock()
    win.motion_controls = MagicMock()
    win.motion_header = MagicMock()
    win.motion_controls_separator = MagicMock()
    win.mute_handler_id = 1
    win.volume_handler_id = 2
    win.props = SimpleNamespace(fullscreened=False, dialogs=[])
    return win


class TestWindowActionsAndInit(unittest.TestCase):
    def test_setup_actions_registers_expected_actions_and_accels(self):
        win = _make_blank_window()
        added_actions = {}
        win.add_action = lambda act: added_actions.__setitem__(act.get_name(), act)
        win._setup_actions()

        expected = {
            "clear-and-add",
            "select-subtitle",
            "select-audio",
            "select-video",
            "select-chapter",
            "add-sub-tracks",
            "add-audio-tracks",
            "add-playlist-files",
            "open-folder",
            "open-url",
            "add-url",
            "open-history",
            "add-playlist-folder",
            "open-playlist-dialog",
            "open-sub-menu",
            "open-audio-menu",
            "open-chapters-menu",
            "save-session",
            "save-session-close",
            "quit",
            "custom-shortcuts",
            "play-pause",
            "previous",
            "next",
        }
        self.assertEqual(set(added_actions.keys()), expected)
        self.assertEqual(set(win._actions.keys()), expected)
        win.app.set_accels_for_action.assert_any_call("win.quit", ["q", "<primary>w"])
        win.app.set_accels_for_action.assert_any_call("win.clear-and-add", ["<primary>o"])
        win.app.set_accels_for_action.assert_any_call("app.shortcuts", [])

    def test_create_action_stateful_rejects_non_int_type(self):
        win = _make_blank_window()
        with self.assertRaises(TypeError):
            win._create_action_stateful("bad", lambda *a: None, "s")

    def test_init_gpu_validation_skips_session_restore_and_wires_exporter(self):
        win = _make_blank_window()
        win.maximize = MagicMock()
        mock_app = MagicMock()
        mock_app.mpris = MagicMock()
        mock_app.gpu_validation_config = SimpleNamespace(mode="auto")
        mock_app.render_backend_selection = SimpleNamespace(selected="legacy")

        mock_mpv_instance = MagicMock()
        mock_mpv_instance.hwdec = ["no", "vaapi"]
        mock_mpv_instance.__getitem__.side_effect = lambda key: False if key == "window-maximized" else None
        mock_mpv_instance.input_bindings = {}
        mock_exporter = MagicMock()
        mock_diag_mod = SimpleNamespace(exporter_from_env=MagicMock(return_value=mock_exporter))
        mock_video_area = MagicMock()

        with patch("src.window.Adw.ApplicationWindow.__init__", return_value=None), \
             patch("src.window.Gtk.WindowGroup"), \
             patch("src.window.settings") as mock_settings, \
             patch.dict(os.environ, {DIAGNOSTICS_EXPORT_PATH_ENV: "/tmp/out.txt"}, clear=False), \
             patch("src.window.load_optional_validation", return_value=mock_diag_mod), \
             patch("src.window.mpv.MPV", return_value=mock_mpv_instance) as mock_mpv_cls, \
             patch("src.window.MpvVideoWidget", return_value=mock_video_area), \
             patch("src.window.Gtk.GraphicsOffload"), \
             patch.object(CineWindow, "video_overlay", MagicMock()), \
             patch("src.window.sync_mpv_with_settings"), \
             patch("src.window.restore_last_playlist") as mock_restore, \
             patch.object(CineWindow, "_setup_actions"), \
             patch.object(CineWindow, "_setup_widgets"), \
             patch.object(CineWindow, "_setup_observers"):
            mock_settings.get_boolean.return_value = True
            mock_settings.get_int.return_value = 0
            CineWindow.__init__(win, application=mock_app)

        self.assertIs(win._diagnostics_exporter, mock_exporter)
        self.assertTrue(win._is_gpu_validation)
        mock_video_area.setup_window_integration.assert_called_once_with(win)
        mock_mpv_cls.assert_called_once()
        mock_restore.assert_not_called()

    def test_public_log_helpers_reexported_and_functional(self):
        self.assertEqual(safe_mpv_file_error_label(b"file not found"), "file not found")
        self.assertEqual(safe_mpv_file_error_label("/secret/path.mkv"), "unavailable")
        owner = SimpleNamespace(
            dovi_rpu_warning_count=0,
            dovi_rpu_warning_text=None,
            _dovi_rpu_logged=False,
        )
        mpv_log = MagicMock()
        handler = make_mpv_log_handler(owner, mpv_log)
        handler("warn", "ffmpeg", "Multiple Dolby Vision RPUs found")
        handler("warn", "ffmpeg", "Multiple Dolby Vision RPUs found")
        self.assertEqual(owner.dovi_rpu_warning_count, 2)
        self.assertEqual(owner.dovi_rpu_warning_text, "ffmpeg: Multiple Dolby Vision RPUs found")
        mpv_log.warning.assert_called_once()


class TestWindowDiagnosticsLifecycle(unittest.TestCase):
    def test_first_media_frame_published_resets_state_on_path_mismatch(self):
        win = _make_blank_window()
        exporter = MagicMock(export_completed=False)
        win._diagnostics_exporter = exporter
        win._media_load_generation = 3
        win._video_area.render_context_generation = 2
        win._video_area._first_media_frame_exported = True
        win._video_area._exported_media_generation = (2, 3)

        win.mpv = {"path": "/tmp/other.mkv"}
        frame = SimpleNamespace(
            path_token=hashlib.sha256(b"/tmp/first.mkv").hexdigest()
        )
        win.on_first_media_frame_published(frame, context_generation=2, load_generation=3)

        exporter.on_first_media_frame.assert_not_called()
        self.assertFalse(win._video_area._first_media_frame_exported)
        self.assertIsNone(win._video_area._exported_media_generation)

    def test_first_media_frame_published_exports_when_tokens_and_gens_match(self):
        win = _make_blank_window()
        exporter = MagicMock(export_completed=False)
        win._diagnostics_exporter = exporter
        win._media_load_generation = 4
        win._video_area.render_context_generation = 1
        media_path = "/tmp/sample-hdr.mkv"
        win.mpv = {"path": media_path}
        expected_token = hashlib.sha256(os.fsencode(os.path.realpath(media_path))).hexdigest()
        frame = SimpleNamespace(path_token=expected_token)

        win.on_first_media_frame_published(frame, context_generation=1, load_generation=4)

        exporter.on_first_media_frame.assert_called_once_with(
            win, frame, context_generation=1, load_generation=4
        )

    def test_first_media_frame_published_ignores_stale_generation(self):
        win = _make_blank_window()
        exporter = MagicMock(export_completed=False)
        win._diagnostics_exporter = exporter
        win._media_load_generation = 5
        win._video_area.render_context_generation = 1
        frame = SimpleNamespace(path_token="abc")

        win.on_first_media_frame_published(frame, context_generation=1, load_generation=4)
        exporter.on_first_media_frame.assert_not_called()


class TestWindowMediaAndPlaylist(unittest.TestCase):
    def test_update_track_menus_ignores_albumart_for_audio_detection(self):
        win = _make_blank_window()
        win.subtitles_menu_btn.get_popover.return_value = None
        win.audio_tracks_menu_btn.get_popover.return_value = None
        tracks = [
            {"id": 1, "type": "audio", "title": "English", "lang": "eng"},
            {"id": 2, "type": "video", "albumart": True},
            {"id": 3, "type": "sub", "lang": "eng"},
        ]
        win._update_track_menus(tracks)

        self.assertTrue(win._is_audio)
        win.video_tracks_menu_btn.set_visible.assert_called_once_with(False)
        self.assertEqual(win.audio_tracks_menu.get_n_items(), 3)
        self.assertEqual(win.subtitles_menu.get_n_items(), 3)
        self.assertEqual(win.video_tracks_menu.get_n_items(), 1)

    def test_update_track_menus_shows_video_button_when_multiple_real_video_tracks(self):
        win = _make_blank_window()
        win.subtitles_menu_btn.get_popover.return_value = None
        win.audio_tracks_menu_btn.get_popover.return_value = None
        tracks = [
            {"id": 1, "type": "video"},
            {"id": 2, "type": "video"},
        ]
        win._update_track_menus(tracks)

        self.assertFalse(win._is_audio)
        win.video_tracks_menu_btn.set_visible.assert_called_once_with(True)
        self.assertEqual(win.video_tracks_menu.get_n_items(), 2)

    def test_splice_playlist_and_navigation_wraparound(self):
        win = _make_blank_window()
        uid = os.getuid()
        win.mpv.playlist = [
            {"filename": f"/run/user/{uid}/doc/abc/a.mkv"},
            {"filename": "/home/user/b.mkv"},
        ]
        win.shuffle_toggle_btn.props = SimpleNamespace(active=True)
        with patch("src.window.has_host_permission", False):
            win.splice_playlist()

        self.assertTrue(win.has_some_doc_path)
        self.assertTrue(win.prev_shuffle)
        self.assertFalse(win.playlist_changed)
        self.assertEqual(win.playlist_ls.get_n_items(), 2)

        win.mpv.playlist_count = 3
        win.mpv.playlist_pos = 0
        win.on_previous_clicked()
        self.assertEqual(win.mpv.playlist_pos, 2)
        win.on_next_clicked()
        self.assertEqual(win.mpv.playlist_pos, 0)

    def test_sync_can_prev_next_respects_loop_playlist(self):
        win = _make_blank_window()
        win._actions = {"previous": MagicMock(props=SimpleNamespace(enabled=False)), "next": MagicMock(props=SimpleNamespace(enabled=False))}
        win.previous_btn.props = SimpleNamespace(sensitive=False)
        win.next_btn.props = SimpleNamespace(sensitive=False)
        win.shuffle_toggle_btn.props = SimpleNamespace(visible=False)
        win.mpv.playlist_count = 2
        win.mpv.playlist_pos = 0
        win.mpv.loop_playlist = False

        win._sync_can_prev_next()
        self.assertFalse(win.can_go_prev)
        self.assertTrue(win.can_go_next)

        win.mpv.loop_playlist = "inf"
        win._sync_can_prev_next()
        self.assertTrue(win.can_go_prev)
        self.assertTrue(win.can_go_next)
        self.assertTrue(win.shuffle_toggle_btn.props.visible)

    def test_save_session_blocked_under_gpu_validation_and_closes_when_requested(self):
        win = _make_blank_window()
        win.close = MagicMock()
        win._is_gpu_validation = True
        with patch("src.window.save_last_playlist_file") as mock_save:
            win._on_save_session(close=True)
        mock_save.assert_not_called()
        win.close.assert_not_called()

        win._is_gpu_validation = False
        with patch("src.window.settings") as mock_settings, \
             patch("src.window.save_last_playlist_file") as mock_save:
            win._on_save_session(close=True)
        mock_settings.set_boolean.assert_called_once_with("save-session", True)
        mock_save.assert_called_once_with(win.mpv)
        win.close.assert_called_once()

    def test_drop_handles_urls_media_and_subtitles(self):
        win = _make_blank_window()
        win.mpv.idle_active = False
        win.mpv.playlist = []

        sub_file = MagicMock()
        sub_file.get_path.return_value = "/tmp/movie.srt"
        sub_file.get_basename.return_value = "movie.srt"
        sub_info = MagicMock()
        sub_file.query_info.return_value = sub_info

        vid_file = MagicMock()
        vid_file.get_path.return_value = "/tmp/movie.mkv"
        vid_file.get_basename.return_value = "movie.mkv"
        vid_info = MagicMock()
        vid_info.get_file_type.return_value = Gio.FileType.REGULAR
        vid_info.get_content_type.return_value = "video/x-matroska"
        vid_file.query_info.return_value = vid_info

        file_list = MagicMock(spec=Gdk.FileList)
        file_list.get_files.return_value = [sub_file, vid_file]

        with patch("src.window.is_same_playlist", return_value=False):
            win._on_drop(None, file_list, 0, 0)

        win.mpv.command_async.assert_any_call("sub-add", "/tmp/movie.srt", "select")
        win.mpv.loadfile.assert_called_once_with("/tmp/movie.mkv", "replace")
        win.mpv.command_async.assert_any_call("set", "pause", "no")


class TestWindowControlsAndInput(unittest.TestCase):
    def test_loop_mode_cycles_off_playlist_file_off(self):
        win = _make_blank_window()
        win._loop_mode = "off"
        win._on_loop_toggled(None)
        self.assertEqual((win.mpv.loop_file, win.mpv.loop_playlist), ("no", "inf"))

        win._loop_mode = "playlist"
        win._on_loop_toggled(None)
        self.assertEqual((win.mpv.loop_file, win.mpv.loop_playlist), ("inf", "no"))

        win._loop_mode = "file"
        win._on_loop_toggled(None)
        self.assertEqual((win.mpv.loop_file, win.mpv.loop_playlist), ("no", "no"))

    def test_update_duration_sets_width_chars_and_disables_zero_duration(self):
        win = _make_blank_window()
        win._hide_time_tooltip = MagicMock()
        win._update_duration(0)
        win.video_progress_scale.set_can_target.assert_called_with(False)
        win._hide_time_tooltip.assert_called_once()

        for dur, expected_chars in [(120, 5), (700, 6), (3700, 7), (90000, 10)]:
            win._update_duration(dur)
            win.time_elapsed_label.set_width_chars.assert_called_with(expected_chars)

    def test_update_volume_icon_thresholds(self):
        win = _make_blank_window()
        win.volume_menu_btn.props = SimpleNamespace(icon_name="")
        cases = [
            (True, 50, "cine-volume-mute-symbolic"),
            (False, 0, "cine-volume-mute-symbolic"),
            (False, 20, "cine-volume-low-symbolic"),
            (False, 50, "cine-volume-mid-symbolic"),
            (False, 100, "cine-volume-max-symbolic"),
            (False, 130, "cine-volume-overamp-symbolic"),
        ]
        for muted, vol, icon in cases:
            win.mpv.mute = muted
            win.mpv.volume = vol
            win._update_volume_icon()
            self.assertEqual(win.volume_menu_btn.props.icon_name, icon)

    def test_update_progress_elapsed_and_remaining(self):
        win = _make_blank_window()
        win.time_elapsed_label.props = SimpleNamespace(label="")
        win.video_progress_adj.props = SimpleNamespace(value=0.0)
        win.mpv.duration = 100.0
        win._show_remaining = False
        win._update_progress(25.0)
        self.assertEqual(win.time_elapsed_label.props.label, "0:25")

        win._show_remaining = True
        win._update_progress(25.0, update_bar=False)
        self.assertEqual(win.time_elapsed_label.props.label, "-1:15")

    def test_progress_drag_pauses_and_resumes_only_when_playing(self):
        win = _make_blank_window()
        win.mpv.pause = False
        win._on_progress_pressed()
        self.assertTrue(win._playing_on_press)
        self.assertEqual(win.skip_pause_obs_count, 1)
        win.mpv.command_async.assert_called_with("set", "pause", "yes")

        win._on_progress_released()
        self.assertFalse(win._playing_on_press)
        self.assertEqual(win.skip_pause_obs_count, 2)
        win.mpv.command_async.assert_called_with("set", "pause", "no")

    def test_key_event_space_short_press_and_nonrepeat_dedup(self):
        win = _make_blank_window()
        win.app.get_actions_for_accel.return_value = []
        win._nonrepeat_keys = {"a"}

        self.assertTrue(win._on_key_event(None, Gdk.KEY_a, 0, Gdk.ModifierType(0), "keypress"))
        self.assertTrue(win._on_key_event(None, Gdk.KEY_a, 0, Gdk.ModifierType(0), "keypress"))
        self.assertEqual(
            [c for c in win.mpv.command_async.call_args_list if c.args == ("keypress", "a")],
            [unittest.mock.call("keypress", "a")],
        )
        win._on_key_event(None, Gdk.KEY_a, 0, Gdk.ModifierType(0), "keyup")
        self.assertNotIn("a", win._pressed_combos)

        with patch("src.window.timeout_add_once", return_value=77), \
             patch("src.window.GLib.source_remove") as mock_remove:
            self.assertTrue(win._on_key_event(None, Gdk.KEY_space, 0, Gdk.ModifierType(0), "keypress"))
            self.assertEqual(win._space_hold_id, 77)
            self.assertTrue(win._on_key_event(None, Gdk.KEY_space, 0, Gdk.ModifierType(0), "keyup"))
            mock_remove.assert_called_once_with(77)
            win.mpv.command_async.assert_any_call("keypress", "SPACE")

    def test_click_released_focus_guard_and_double_click_command(self):
        win = _make_blank_window()
        win.motion_controls.props = SimpleNamespace(contains_pointer=False)
        win.motion_header.props = SimpleNamespace(contains_pointer=False)
        win.motion_controls_separator.props = SimpleNamespace(contains_pointer=False)
        win._is_inactive = True
        win._left_clk = PrimaryClick.FOCUS_PLAY_PAUSE
        win._mouse_binds = {"MBTN_LEFT_DBL": "cycle fullscreen; show-text hi"}

        with patch("src.window.timeout_add_once") as mock_timeout:
            win._on_click_released(None, 1, 10, 10, "MBTN_LEFT")
            mock_timeout.assert_not_called()

        win._is_inactive = False
        win._on_click_released(None, 2, 10, 10, "MBTN_LEFT")
        win.mpv.command_async.assert_any_call("keypress", "MBTN_LEFT_DBL")

    def test_mouse_scroll_touchpad_scales_and_emits_wheel_command(self):
        win = _make_blank_window()
        event = MagicMock()
        event.get_modifier_state.return_value = Gdk.ModifierType(0)
        event.get_unit.return_value = Gdk.ScrollUnit.SURFACE
        event.get_relative_direction.return_value = Gdk.ScrollRelativeDirection.IDENTICAL
        controller = MagicMock()
        controller.get_current_event.return_value = event

        win._on_mouse_scroll(controller, 0.0, -5.0)
        win.mpv.command_async.assert_not_called()
        win._on_mouse_scroll(controller, 0.0, -6.0)
        win.mpv.command_async.assert_called_once_with("keypress", "WHEEL_UP")

    def test_hide_ui_and_fullscreen_decoration_layout(self):
        win = _make_blank_window()
        win.mpv.idle_active = False
        win.mpv.__getitem__.return_value = False
        win._is_fullscreen = True
        for btn in (
            win.primary_menu_btn,
            win.open_menu_btn,
            win.options_menu_btn,
            win.hdr_menu_btn,
            win.volume_menu_btn,
            win.subtitles_menu_btn,
            win.audio_tracks_menu_btn,
            win.video_tracks_menu_btn,
            win.chapters_menu_btn,
        ):
            btn.props = SimpleNamespace(active=False)
        win.motion_controls.props = SimpleNamespace(contains_pointer=False)
        win.motion_header.props = SimpleNamespace(contains_pointer=False)
        win.set_cursor_from_name = MagicMock()

        win._hide_ui()
        win.revealer_ui.set_reveal_child.assert_called_once_with(False)
        win.set_cursor_from_name.assert_called_once_with("none")

        win.props = SimpleNamespace(fullscreened=True)
        with patch("src.window.gtk_setts", SimpleNamespace(get_property=lambda _k: "appmenu:minimize,maximize,close")):
            win._set_fs_state(win, None)
        win.headerbar.set_decoration_layout.assert_called_once_with(":close")


class TestWindowObserversAndCopyReportParity(unittest.TestCase):
    def test_setup_observers_preserves_exact_registration_order(self):
        win = _make_blank_window()
        recorded = []
        win.mpv.event_callback = lambda name: lambda fn: recorded.append(("event", name)) or fn
        win.mpv.property_observer = lambda name: lambda fn: recorded.append(("prop", name)) or fn

        win._setup_observers()

        expected = [
            ("event", "start-file"),
            ("event", "file-loaded"),
            ("event", "end-file"),
            ("prop", "path"),
            ("prop", "video-params/rotate"),
            ("prop", "audio-params"),
            ("prop", "playlist-count"),
            ("prop", "playlist-pos"),
            ("prop", "ab-loop-b"),
            ("prop", "ab-loop-a"),
            ("prop", "loop-file"),
            ("prop", "loop-playlist"),
            ("prop", "fullscreen"),
            ("prop", "time-pos"),
            ("prop", "seeking"),
            ("prop", "duration"),
            ("prop", "mute"),
            ("prop", "volume"),
            ("prop", "sid"),
            ("prop", "aid"),
            ("prop", "vid"),
            ("prop", "track-list"),
            ("prop", "playlist-pos"),
            ("prop", "chapter-list"),
            ("prop", "chapter"),
            ("prop", "pause"),
            ("prop", "idle-active"),
            ("prop", "media-title"),
            ("prop", "sub-scale"),
            ("prop", "sid"),
            ("prop", "sub-visibility"),
            ("prop", "aid"),
            ("prop", "vid"),
            ("prop", "video-zoom"),
            ("prop", "vo"),
            ("event", "shutdown"),
        ]
        self.assertEqual(recorded, expected)

    def test_observer_callbacks_runtime_contracts(self):
        win = _make_blank_window()
        events = {}
        props = {}
        win.mpv.event_callback = lambda name: lambda fn: events.__setitem__(name, fn) or fn
        win.mpv.property_observer = lambda name: lambda fn: props.setdefault(name, []).append(fn) or fn
        win._setup_observers()

        # start-file advances _media_load_generation
        with patch("src.window.idle_add_once"):
            events["start-file"](None)
        self.assertEqual(win._media_load_generation, 1)

        # end-file non-error idle close when keep_open is False and not startup
        win._is_startup = False
        win._presented = True
        win.mpv.keep_open = False
        win.mpv.idle_active = True
        idle_ev = MagicMock()
        idle_ev.as_dict.return_value = {"reason": b"eof"}
        with patch("src.window.idle_add_once") as mock_idle:
            events["end-file"](idle_ev)
        mock_idle.assert_any_call(win.close)

        # pause seeks to 0 at EOF (which resets skip_pause_obs_count so UI updates)
        win.mpv.eof_reached = True
        win.skip_pause_obs_count = 1
        with patch("src.window.idle_add_once") as mock_idle:
            props["pause"][0]("pause", True)
        win.mpv.seek.assert_called_once_with(0, reference="absolute")
        self.assertEqual(win.skip_pause_obs_count, 0)
        self.assertEqual(mock_idle.call_count, 2)

        # pause when not at EOF honors skip_pause_obs_count without UI update
        win.mpv.eof_reached = False
        win.skip_pause_obs_count = 1
        with patch("src.window.idle_add_once") as mock_idle:
            props["pause"][0]("pause", True)
        self.assertEqual(win.skip_pause_obs_count, 0)
        mock_idle.assert_not_called()

        # vid == 0 clears video_area frame
        with patch("src.window.idle_add_once") as mock_idle:
            props["vid"][1]("vid", False)
        mock_idle.assert_any_call(win._video_area.clear_frame)

        # vo guard resets non-libmpv backend
        props["vo"][0]("vo", [{"name": "gpu"}])
        win.mpv.__setitem__.assert_called_with("vo", "libmpv")

    def test_copy_report_parity_on_window_instance(self):
        win = _make_blank_window()
        win.mpv = {
            "vo-configured": True,
            "current-vo": "libmpv",
            "hwdec-current": "vaapi",
            "video-params": {
                "colormatrix": "bt.2020-ncl",
                "colorlevels": "limited",
                "primaries": "bt.2020",
                "gamma": "pq",
                "sig-peak": 10.0,
                "pixelformat": "p010",
            },
            "path": "/media/hdr_sample.mkv",
        }
        win.offload = SimpleNamespace(get_enabled=lambda: Gtk.GraphicsOffloadEnabled.ENABLED)
        win.get_surface = lambda: None
        win.get_display = lambda: None
        win.dovi_rpu_warning_count = 1
        win.dovi_rpu_warning_text = "ffmpeg: Multiple Dolby Vision RPUs found"

        report = generate_video_output_report(win, sample_time_utc="2026-11-12T00:00:00Z")

        self.assertIn("Multiple Dolby Vision RPUs", report)
        self.assertIn("Transfer Characteristics: pq", report)
        self.assertIn("Enabled (subsurface / direct scanout possible)", report)


class TestColdOpenRenderContextOrder(unittest.TestCase):
    def test_ensure_video_render_context_realizes_window_then_video_area(self):
        win = _make_blank_window()
        calls = []
        state = {"win": False, "area": False}

        def win_realize():
            calls.append("win.realize")
            state["win"] = True

        def area_realize():
            calls.append("video_area.realize")
            state["area"] = True

        win.get_realized = lambda: state["win"]
        win.realize = win_realize
        win._video_area = SimpleNamespace(
            get_realized=lambda: state["area"],
            realize=area_realize,
        )

        win.ensure_video_render_context()
        self.assertEqual(calls, ["win.realize", "video_area.realize"])

        # Idempotent once realized
        win.ensure_video_render_context()
        self.assertEqual(calls, ["win.realize", "video_area.realize"])

    def test_do_open_realizes_video_area_before_loadfile(self):
        from src.main import CineApplication

        app = CineApplication.__new__(CineApplication)
        app.gpu_validation_config = None
        app._gpu_validation_session = None
        app.can_restore_session = True
        app.props = SimpleNamespace(active_window=None)

        calls = []
        win = _make_blank_window()
        win.get_realized = lambda: False
        win.realize = lambda: calls.append("win.realize")
        win._video_area = SimpleNamespace(
            get_realized=lambda: False,
            realize=lambda: calls.append("video_area.realize"),
        )
        win.mpv = MagicMock()
        win.mpv.loadfile = lambda path, mode: calls.append(("loadfile", path, mode))
        app.get_windows = lambda: [win]

        gfile = MagicMock()
        gfile.get_path.return_value = "/tmp/sample.mkv"

        with patch("src.main.CineWindow", return_value=win), \
             patch("src.main.os.path.exists", return_value=True):
            app.do_open([gfile], 1, "")

        self.assertEqual(
            calls,
            [
                "win.realize",
                "video_area.realize",
                ("loadfile", "/tmp/sample.mkv", "append-play"),
            ],
        )

    def test_restore_last_playlist_ensures_render_context_before_loadfile(self):
        from src.save_session import restore_last_playlist

        calls = []
        win = _make_blank_window()
        win.app = SimpleNamespace(can_restore_session=True)
        win.ensure_video_render_context = lambda: calls.append("ensure_render_context")
        win.mpv = MagicMock()
        win.mpv.loadfile = lambda path, mode: calls.append(("loadfile", path, mode))

        scheduled = []

        def fake_idle(fn, *args, **kwargs):
            scheduled.append((fn, args, kwargs))
            return 1

        with patch("src.save_session.os.path.exists", return_value=True), \
             patch("src.save_session.os.path.getsize", return_value=42), \
             patch("src.save_session.idle_add_once", side_effect=fake_idle):
            restored = restore_last_playlist(win)

        self.assertTrue(restored)
        self.assertFalse(win.app.can_restore_session)
        for fn, args, kwargs in scheduled:
            fn(*args, **kwargs)
        self.assertEqual(calls[0], "ensure_render_context")
        self.assertEqual(calls[1][0], "loadfile")

    def test_rotate_observer_swaps_dimensions_and_dispatches_on_idle(self):
        win = _make_blank_window()
        win._presented = False
        win._shutting_down = False
        props = {}
        win.mpv = SimpleNamespace(
            event_callback=lambda name: lambda fn: fn,
            property_observer=lambda name: lambda fn: props.setdefault(name, []).append(fn) or fn,
            width=1920,
            height=1080,
        )
        win._setup_observers()

        with patch("src.window.idle_add_once") as mock_idle:
            props["video-params/rotate"][0]("video-params/rotate", 90)
        mock_idle.assert_called_once_with(win.set_win_size_and_present, 1080, 1920)


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    unittest.main()
