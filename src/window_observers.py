# window_observers.py
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""mpv event and property observer registration for CineWindow."""

import os
from gettext import gettext as _
from typing import Any, cast

import gi
import mpv

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from .playlist import Playlist, PlaylistItemObj
from .utils import mpv_prop


def _register_file_observers(win: Any, win_mod: Any) -> None:
    @win.mpv.event_callback("start-file")
    def on_start_file(_event):
        if win._shutting_down:
            return
        win._media_load_generation = getattr(win, "_media_load_generation", 0) + 1
        win_mod.idle_add_once(win.spinner.set_visible, True)

    def on_f_loaded():
        try:
            win.spinner.set_visible(False)
            win.is_local_path = win_mod.is_local_path(win.mpv.path)
            win.start_page.set_sensitive(True)
            win.hide_ui_timeout()
            win._on_ab_loop_btn_toggled(None)

            if win_mod.settings.get_boolean("thumbnail-preview") and win.is_local_path:
                win.setup_thumb_preview()
            elif win.thumb_area:
                win.thumb_area.unrealize()
                win.thumb_area.unmap()
                win.thumb_area = None

            win._set_time_tooltip()

            win._mpris.update_metadata()
        except mpv.ShutdownError:
            pass

    @win.mpv.event_callback("file-loaded")
    def on_file_loaded(_event):
        if win._shutting_down:
            return
        win_mod.idle_add_once(on_f_loaded)
        win_mod.timeout_add_seconds_once(5, setattr, win, "_error_count", 0)

    @win.mpv.event_callback("end-file")
    def on_end_file(event):
        if win._shutting_down:
            return

        win_mod.idle_add_once(win.spinner.set_visible, False)
        win_mod.idle_add_once(win.start_page.set_sensitive, True)

        try:
            curr_pos = win.mpv.playlist_pos
            info = event.as_dict()
            reason = info["reason"]

            if not win._presented and not info.get("playlist_insert_num_entries"):
                win_mod.idle_add_once(win.set_win_size_and_present)

            if reason == b"error":
                # Avoid stopping playback on last file/folder error
                playlist_count = cast(int, win.mpv.playlist_count)
                if curr_pos == playlist_count - 1:
                    win.mpv.playlist_pos = 0

                win._error_count += 1
                raw_error = info.get("file_error", b"unknown")
                win_mod.logger.warning(
                    "mpv end-file error label: %s",
                    win_mod.safe_mpv_file_error_label(raw_error),
                )
                error = (
                    raw_error.decode("utf-8", "replace")
                    if isinstance(raw_error, bytes)
                    else str(raw_error)
                )
                win_mod.idle_add_once(win.show_toast, _("File Error") + f": {error}")

                if win._error_count == 20:
                    win.mpv.stop()
                    win.shuffle_toggle_btn.set_active(False)
                    win._error_count = 0
            elif (
                not win.mpv.keep_open
                and win.mpv.idle_active
                and not win._is_startup
            ):
                win_mod.idle_add_once(win.close)
        except mpv.ShutdownError:
            pass


def _register_playlist_and_ab_observers(win: Any, win_mod: Any) -> None:
    @win.mpv.property_observer("path")
    def on_path_change(_name, path):
        win._video_path = path

    @win.mpv.property_observer("video-params/rotate")
    def on_rotate_change(_name, rotate_val):
        if win._shutting_down or rotate_val is None or win._presented:
            return

        raw_w = mpv_prop(win.mpv, "width")
        raw_h = mpv_prop(win.mpv, "height")
        if win._shutting_down:
            return

        vid_w = cast(int, raw_w) or 1920
        vid_h = cast(int, raw_h) or 1080

        if abs(rotate_val) in (90, 270):
            vid_w, vid_h = vid_h, vid_w

        win_mod.idle_add_once(win.set_win_size_and_present, vid_w, vid_h)

    @win.mpv.property_observer("audio-params")
    def on_a_params_change(_name, audio_params):
        if win._shutting_down or audio_params is None or win._presented:
            return

        win_mod.idle_add_once(win.set_win_size_and_present)

    @win.mpv.property_observer("playlist-count")
    def on_playlist_count_change(_name, _count):
        if win._shutting_down:
            return
        win.playlist_changed = True
        if isinstance(win._visible_dialog, Playlist):
            if win._playlist_debounce_id > 0:
                win_mod.GLib.source_remove(win._playlist_debounce_id)
                win._playlist_debounce_id = 0
            win._playlist_debounce_id = win_mod.timeout_add_once(75, win.splice_playlist)
        win_mod.idle_add_once(win._sync_can_prev_next)

    def update_playing_item(pos):
        try:
            prev_p = win._playlist_prev_pos
            prev_obj = cast(PlaylistItemObj, win.playlist_ls.get_item(prev_p))
            curr_obj = cast(PlaylistItemObj, win.playlist_ls.get_item(pos))
            prev_obj.playing = False
            curr_obj.playing = True
        except (AttributeError, OverflowError):
            pass
        finally:
            win._playlist_prev_pos = pos

    @win.mpv.property_observer("playlist-pos")
    def on_playlist_pos_changed(_name, pos):
        if win._shutting_down:
            return
        win_mod.idle_add_once(update_playing_item, pos)

    win._ab_loop_a = None
    win._ab_loop_b = None

    def sync_ab_loop(name):
        scale = win.video_progress_scale
        btn = win.ab_loop_btn

        a_time = win._ab_loop_a
        b_time = win._ab_loop_b
        a_on = a_time is not None
        b_on = b_time is not None
        is_looping = a_on and b_on
        ab_off = not a_on and not b_on

        if name == "ab-loop-a" and a_on:
            scale.add_mark(a_time, Gtk.PositionType.BOTTOM, None)
            btn.add_css_class("a-loop")

        if name == "ab-loop-b" and b_on:
            scale.add_mark(b_time, Gtk.PositionType.BOTTOM, None)
            btn.remove_css_class("a-loop")

        if ab_off:
            btn.remove_css_class("a-loop")
            scale.clear_marks()
            for chapter in win._chapters:
                time_pos = chapter.get("time")
                if time_pos is not None:
                    scale.add_mark(time_pos, Gtk.PositionType.TOP, None)

        if btn.get_active() != is_looping:
            btn.handler_block_by_func(win._on_ab_loop_btn_toggled)
            btn.set_active(is_looping)
            btn.handler_unblock_by_func(win._on_ab_loop_btn_toggled)

    @win.mpv.property_observer("ab-loop-a")
    @win.mpv.property_observer("ab-loop-b")
    def on_ab_loop_change(name, value):
        val = value if isinstance(value, float) else None
        if name == "ab-loop-a":
            win._ab_loop_a = val
        elif name == "ab-loop-b":
            win._ab_loop_b = val
        win_mod.idle_add_once(sync_ab_loop, name)


def _register_loop_and_playback_observers(win: Any, win_mod: Any) -> None:
    def sync_loop(name, value):
        new_mode = "no"
        if name == "loop-playlist" and value == "inf":
            win.mpv.loop_file = False
            new_mode = "playlist"
            win._sync_can_prev_next()
        elif name == "loop-file" and value == "inf":
            win.mpv.loop_playlist = False
            new_mode = "file"
        elif name == "loop-playlist":
            win._sync_can_prev_next()

        if win._loop_mode != new_mode:
            win._loop_mode = new_mode
            win._update_loop_state()
            win._mpris.update_loop()

    @win.mpv.property_observer("loop-playlist")
    @win.mpv.property_observer("loop-file")
    def on_loop_change(name, value):
        win_mod.idle_add_once(sync_loop, name, value)

    def sync_fs(value):
        icon = (
            "cine-view-restore-symbolic"
            if value
            else "cine-view-fullscreen-symbolic"
        )
        text = _("Exit Fullscreen") if value else _("Fullscreen")
        win.fullscreen_btn.set_tooltip_text(text)
        win.fullscreen_btn.set_icon_name(icon)
        win._sync_fullscreen(value)

    @win.mpv.property_observer("fullscreen")
    def on_fs_change(_name, value):
        win_mod.idle_add_once(sync_fs, value)
        win.hide_ui_timeout()

    @win.mpv.property_observer("time-pos")
    def on_time_change(_name, value):
        win_mod.idle_add_once(win._update_progress, float(value or 0))

    @win.mpv.property_observer("seeking")
    def on_seeking_change(_name, seeking):
        if not seeking:
            win_mod.idle_add_once(win._mpris.emit_seeked)

    @win.mpv.property_observer("duration")
    def on_duration_change(_name, value):
        val = float(value or 0)
        win._duration = val
        win_mod.idle_add_once(win._update_duration, val)


def _register_audio_volume_observers(win: Any, win_mod: Any) -> None:
    def sync_mute(muted):
        win.mute_toggle_btn.handler_block(win.mute_handler_id)
        win.mute_toggle_btn.set_active(muted)
        win.mute_toggle_btn.handler_unblock(win.mute_handler_id)
        win._update_volume_icon()
        user_data = cast(dict, win.mpv.user_data)
        show_icon = user_data.get("show-icon")

        if show_icon == "yes":
            win.icon_indicator.props.icon_name = (
                win.volume_menu_btn.props.icon_name
            )
            win._show_icon_indicator()
            user_data["show-icon"] = None

    @win.mpv.property_observer("mute")
    def on_mute_change(_name, muted):
        win_mod.idle_add_once(sync_mute, muted)

    def update_icon_and_vol_adj(value):
        vol = int(value)
        win.volume_scale.handler_block(win.volume_handler_id)
        win.volume_scale_adj.set_value(vol)
        win.volume_scale.handler_unblock(win.volume_handler_id)

        if vol > 0 and win.mpv.mute:
            win.mpv.mute = False

        if win.volume_menu_btn.props.active:
            win.mpv.show_text(_("Volume") + f": {vol}%")

        win._update_volume_icon()
        win_mod.settings.set_int("volume", vol)
        win._mpris.update_volume(vol)

    @win.mpv.property_observer("volume")
    def on_volume_change(_name, value):
        win_mod.idle_add_once(update_icon_and_vol_adj, value)


def _register_track_and_chapter_observers(win: Any, win_mod: Any) -> None:
    track_map = {
        "sid": "select-subtitle",
        "aid": "select-audio",
        "vid": "select-video",
    }

    def set_track(name, value):
        action_name = track_map.get(name) or ""
        val = value if isinstance(value, int) else 0
        if action := win.lookup_action(action_name):
            action.set_state(  # pyright: ignore[reportAttributeAccessIssue]
                win_mod.GLib.Variant("i", val)
            )

    def on_track_change(name, value):
        win_mod.idle_add_once(set_track, name, value)

    for prop in track_map:
        win.mpv.property_observer(prop)(on_track_change)

    @win.mpv.property_observer("track-list")
    def on_track_list_change(_name, track_list):
        win_mod.idle_add_once(win._update_track_menus, track_list)

    @win.mpv.property_observer("playlist-pos")
    def on_pl_pos_change(_name, _value):
        win_mod.idle_add_once(win._sync_can_prev_next)

    @win.mpv.property_observer("chapter-list")
    def on_chapter_list_change(_name, chapters):
        win._chapters = []
        win_mod.idle_add_once(win._update_chapter_marks_and_menu, chapters)

    @win.mpv.property_observer("chapter")
    def on_chapter_change(_name, chapter_idx):
        if chapter_idx is not None and win.chapters_menu_btn.get_active():
            win_mod.idle_add_once(win._sync_chapter_menu_selected)


def _register_pause_idle_title_observers(win: Any, win_mod: Any) -> None:
    @win.mpv.property_observer("pause")
    def on_pause_change(_name, paused):
        if win._shutting_down:
            return
        if win.mpv.eof_reached:  # allow to replay at eof, requires keep-open
            win.mpv.seek(0, reference="absolute")
            win.skip_pause_obs_count = 0

        if win.skip_pause_obs_count > 0:
            win.skip_pause_obs_count -= 1
            return

        win_mod.idle_add_once(win._sync_inhibit)
        win_mod.idle_add_once(win._update_play_pause_icon, paused)

    def sync_idle_active(is_idle):
        if win._shutting_down:
            return
        win._actions["open-sub-menu"].set_enabled(not is_idle)
        win._actions["open-audio-menu"].set_enabled(not is_idle)

        win.title_widget.set_visible(not is_idle)
        win.start_page.set_visible(is_idle)
        win.controls_box.set_visible(not is_idle)
        win._video_area.set_visible(not is_idle)

        win.drop_label.props.label = (
            _("Play") if is_idle else _("Play or Add Subtitles")
        )
        win.drop_icon.props.icon_name = (
            "cine-playback-start-symbolic"
            if is_idle
            else "cine-play-or-sub-symbolic"
        )

        if is_idle:
            win._error_count = 0
            win.revealer_ui.set_reveal_child(True)
            win.set_title(_("Cine"))
            win._hide_icon_indicator = True
            if isinstance(win._visible_dialog, Playlist):
                win._visible_dialog.close()

        win._sync_inhibit()

    @win.mpv.property_observer("idle-active")
    def on_idle_change(_name, is_idle):
        if win._shutting_down:
            return
        win._is_startup = False
        win_mod.idle_add_once(sync_idle_active, is_idle)

    def sync_title(title):
        try:
            if title == win.mpv.filename:
                title_no_ext = os.path.splitext(title)[0]
                win.set_title(title_no_ext)
                win.title_widget.set_title(title_no_ext)
            else:
                win.set_title(title)
                win.title_widget.set_title(title)
                pos = abs(cast(int, win.mpv.playlist_pos))
                if obj := cast(PlaylistItemObj, win.playlist_ls.get_item(pos)):
                    obj.notify("playing")

            win._hide_icon_indicator = False
            win._mpris.update_metadata()
        except mpv.ShutdownError:
            pass

    @win.mpv.property_observer("media-title")
    def on_title_change(_name, title):
        if win._shutting_down:
            return
        if title:
            win_mod.idle_add_once(sync_title, title)


def _register_media_track_icon_observers(win: Any, win_mod: Any) -> None:
    @win.mpv.property_observer("sub-scale")
    def on_sub_scale_change(_name, value):
        if win._visible_dialog is None:
            win_mod.idle_add_once(win_mod.settings.set_double, "subtitle-scale", value)

    def set_sub_icon(name, value):
        try:
            sub_on_icon = "cine-subtitles-symbolic"
            sub_off_icon = "cine-subtitles-off-symbolic"

            sub_on = (value == "auto" or value) and win.mpv.sid
            win.subtitles_menu_btn.props.icon_name = (
                sub_on_icon if sub_on else sub_off_icon
            )

            if name != "sub-visibility":
                return

            user_data = cast(dict, win.mpv.user_data)
            show_icon = user_data.get("show-icon")

            if show_icon == "yes":
                icon = sub_on_icon if sub_on else sub_off_icon
                win.icon_indicator.props.icon_name = icon
                win._show_icon_indicator()
                user_data["show-icon"] = None
        except mpv.ShutdownError:
            pass

    @win.mpv.property_observer("sub-visibility")
    @win.mpv.property_observer("sid")
    def on_sub_vis_change(name, value):
        if win._shutting_down:
            return
        win_mod.idle_add_once(set_sub_icon, name, value)

    def set_aid_icon(value):
        audio_on = value == "auto" or value
        win.audio_tracks_menu_btn.props.icon_name = (
            "cine-audio-symbolic" if audio_on else "cine-audio-off-symbolic"
        )

    @win.mpv.property_observer("aid")
    def on_aid_change(_name, value):
        if win._shutting_down:
            return
        win_mod.idle_add_once(set_aid_icon, value)

    @win.mpv.property_observer("vid")
    def on_vid_change(_name, value):
        if win._shutting_down:
            return
        win_mod.idle_add_once(win.audio_only_icon.set_visible, not bool(value))
        if not value:
            # clear the last frame, which sometimes can still be present
            if hasattr(win._video_area, "clear_frame"):
                win_mod.idle_add_once(win._video_area.clear_frame)
            else:
                win_mod.idle_add_once(win._video_area.queue_render)


def _register_video_vo_and_shutdown_observers(win: Any, win_mod: Any) -> None:
    @win.mpv.property_observer("video-zoom")
    def on_zoom_change(_name, value):
        if round(value, 2) == 0.00:
            win.mpv["video-align-x"] = 0
            win.mpv["video-align-y"] = 0

    @win.mpv.property_observer("vo")
    def on_vo_change(_name, vo_list):
        try:
            if vo_list[0].get("name") != "libmpv":
                win.mpv["vo"] = "libmpv"
        except mpv.ShutdownError:
            pass

    @win.mpv.event_callback("shutdown")
    def on_quit(_event):
        if win._shutting_down:
            return
        win_mod.idle_add_once(win.close)


def setup_window_observers(win: Any) -> None:
    """Register mpv event callbacks and property observers in the exact runtime order."""
    from . import window as win_mod

    _register_file_observers(win, win_mod)
    _register_playlist_and_ab_observers(win, win_mod)
    _register_loop_and_playback_observers(win, win_mod)
    _register_audio_volume_observers(win, win_mod)
    _register_track_and_chapter_observers(win, win_mod)
    _register_pause_idle_title_observers(win, win_mod)
    _register_media_track_icon_observers(win, win_mod)
    _register_video_vo_and_shutdown_observers(win, win_mod)
