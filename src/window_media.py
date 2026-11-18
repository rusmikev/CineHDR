# window_media.py
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""File/folder/URL dialogs, track/chapter menus, playlist & drop handling for CineWindow."""

import os
from gettext import gettext as _
from typing import Any, cast
from urllib.parse import urlparse

import gi
import mpv

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .history import HistoryDialog
from .playlist import Playlist, PlaylistItemObj
from .shortcuts import populate_shortcuts_dialog_mpv
from .utils import SUB_EXTS


def present_shortcuts(win: Any) -> None:
    """Present the shortcuts dialog populated from active mpv bindings."""
    builder = Gtk.Builder.new_from_resource(
        "/io/github/rusmikev/CineHDR/shortcuts-dialog.ui"
    )
    win.shortcuts_dialog = cast(
        Adw.ShortcutsDialog,
        builder.get_object("shortcuts_dialog"),
    )
    populate_shortcuts_dialog_mpv(win.shortcuts_dialog, win._input_bindings)
    win.shortcuts_dialog.present(win)


def present_history(win: Any) -> None:
    """Present the watch history dialog."""
    history_dialog = HistoryDialog(win)
    history_dialog.present(win)


def _add_track_to_menu(win: Any, track: dict[str, Any]) -> None:
    track_id = int(track.get("id", 0))
    track_type = track.get("type")
    lang = track.get("lang")
    title = track.get("title")

    label_parts = [p for p in (title, lang) if p]
    label = (
        " – ".join(label_parts) if label_parts else (_("Track") + f" {track_id}")
    )

    if track_type == "sub":
        menu = win.subtitles_menu
        action = "win.select-subtitle"
    elif track_type == "audio":
        menu = win.audio_tracks_menu
        action = "win.select-audio"
    else:
        menu = win.video_tracks_menu
        action = "win.select-video"

    item = Gio.MenuItem.new(label, None)
    item.set_action_and_target_value(action, GLib.Variant("i", track_id))
    menu.append_item(item)


def update_track_menus(win: Any, track_list: list[dict[str, Any]]) -> None:
    """Rebuild subtitle, audio, and video track menus from mpv track-list."""
    win.subtitles_menu.remove_all()
    win.subtitles_menu.append(_("Add Subtitle Track"), "win.add-sub-tracks")

    item_none_sub = Gio.MenuItem.new(_("None"), None)
    item_none_sub.set_action_and_target_value(
        "win.select-subtitle", GLib.Variant("i", 0)
    )
    win.subtitles_menu.append_item(item_none_sub)

    win.audio_tracks_menu.remove_all()
    win.audio_tracks_menu.append(_("Add Audio Track"), "win.add-audio-tracks")

    item_none_audio = Gio.MenuItem.new(_("None"), None)
    item_none_audio.set_action_and_target_value(
        "win.select-audio", GLib.Variant("i", 0)
    )
    win.audio_tracks_menu.append_item(item_none_audio)

    win.video_tracks_menu.remove_all()

    video_count = 0
    for track in track_list:
        _add_track_to_menu(win, track)
        track_type = track.get("type")
        if track_type == "video" and not track.get("albumart"):
            video_count += 1

    win._is_audio = video_count == 0
    win.video_tracks_menu_btn.set_visible(video_count > 1)

    def hide_box_first_model_btn(menu_btn):
        """Hide the space before add track label"""
        target = menu_btn.get_popover()
        for _i in range(8):
            if target:
                target = target.get_first_child()
        if target:
            target.set_visible(False)

    hide_box_first_model_btn(win.subtitles_menu_btn)
    hide_box_first_model_btn(win.audio_tracks_menu_btn)


def open_playlist_dialog(win: Any) -> None:
    """Present the playlist dialog when media is loaded."""
    if win.mpv.idle_active:
        return
    playlist = Playlist(win)
    playlist.present(win)


def open_folder_dialog(win: Any, action: Any) -> int:
    """Show folder picker for replacing or appending playlist items."""
    from . import window as win_mod

    add_mode = action.props.name != "open-folder"
    title = _("Add Folder") if add_mode else _("Open Folder")
    dialog = Gtk.FileDialog(title=title)
    curr_path = win.mpv.path

    if isinstance(curr_path, str) and os.path.exists(curr_path):
        folder_path = os.path.dirname(curr_path)
        dialog.set_initial_folder(Gio.File.new_for_path(folder_path))

    def on_open_response(dialog, result):
        try:
            folder = dialog.select_folder_finish(result)

            if not add_mode:
                win.mpv.stop()
                win.mpv.pause = False
                win.shuffle_toggle_btn.set_active(False)

            path = folder.get_path()
            win.mpv.loadfile(path, "append-play")

        except GLib.Error as e:
            win_mod.logger.warning(f"Dialog error: {e}")

    dialog.select_folder(win, None, on_open_response)
    return Gdk.EVENT_STOP  # so "<shift><primary>i" doesn't trigger inspector


def _on_open_response(win: Any, dialog: Any, result: Any, mode: str) -> None:
    from . import window as win_mod

    try:
        files = dialog.open_multiple_finish(result)

        if mode == "clear-and-add":
            win.mpv.stop()
            win.shuffle_toggle_btn.set_active(False)

        for file in files:
            path = file.get_path() or file.get_uri()

            if mode == "sub-add":
                win.mpv.sub_add(path)
            elif mode == "audio-add":
                win.mpv.audio_add(path)
            else:
                win.mpv.loadfile(path, "append-play")

        if mode == "clear-and-add":
            win.mpv.pause = False
    except GLib.Error as e:
        win_mod.logger.warning(f"Dialog error: {e}")
    except Exception:
        win_mod.logger.exception("Failed to add files")
    finally:
        if isinstance(win._visible_dialog, Playlist):
            win._visible_dialog.spinner.set_visible(False)


def open_add_dialog(win: Any, title: str, mode: str) -> None:
    """Open multi-file picker configured for media, audio, or subtitles."""
    file_filter = Gtk.FileFilter()
    dialog = Gtk.FileDialog(title=title)
    filters_list = Gio.ListStore.new(Gtk.FileFilter)
    filters_list.append(file_filter)
    dialog.set_filters(filters_list)
    dialog.set_default_filter(file_filter)

    curr_path = win.mpv.path
    if isinstance(curr_path, str) and os.path.exists(curr_path):
        folder_path = os.path.dirname(curr_path)
        dialog.set_initial_folder(Gio.File.new_for_path(folder_path))

    if mode == "sub-add":
        file_filter.set_name(_("Subtitles"))
        for sub in SUB_EXTS:
            file_filter.add_suffix(sub.lstrip("."))
    elif mode == "audio-add":
        file_filter.set_name(_("Audio"))
        for m in ["video/*", "audio/*"]:
            file_filter.add_mime_type(m)
    else:
        file_filter.set_name(_("Media"))
        for m in ["video/*", "audio/*", "image/*"]:
            file_filter.add_mime_type(m)

    dialog.open_multiple(
        win,
        None,
        lambda d, res: _on_open_response(win, d, res, mode),
    )

    if isinstance(win._visible_dialog, Playlist):
        win._visible_dialog.spinner.set_visible(True)


def save_session(win: Any, close: bool = False) -> None:
    """Persist session playlist and watch-later state."""
    from . import window as win_mod

    try:
        win_mod.settings.set_boolean("save-session", True)
        win_mod.save_last_playlist_file(win.mpv)
        if close:
            win.close()
            return
        win.mpv.write_watch_later_config()
        win_mod.idle_add_once(win.show_toast, _("Session Saved"))
    except Exception:
        win_mod.logger.exception("_on_save_session failed")


def open_url_dialog(win: Any, add: bool = False) -> None:
    """Present the Open/Add URL dialog and prefill clipboard URL if supported."""
    mode = "append-play" if add else "replace"
    view = Adw.ToolbarView()
    header_bar = Adw.HeaderBar()
    h_title = _("Add URL") if add else _("Open URL")
    header_bar.set_title_widget(Adw.WindowTitle(title=h_title))
    view.add_top_bar(header_bar)

    content_box = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=18,
        margin_bottom=18,
        margin_start=18,
        margin_end=18,
    )
    view.set_content(content_box)
    entry_row = Adw.EntryRow(title=_("URL"), activates_default=True)
    list_box = Gtk.ListBox(
        selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"]
    )
    list_box.append(entry_row)
    content_box.append(list_box)

    btn_open = Gtk.Button(
        label=_("Add") if add else _("Open"),
        css_classes=["pill", "suggested-action"],
        halign=Gtk.Align.CENTER,
        sensitive=False,
    )
    content_box.append(btn_open)
    dialog = Adw.Dialog(content_width=450, child=view, default_widget=btn_open)
    win.url = ""

    def is_valid_input(text):
        url = text.strip()
        parsed = urlparse(url)
        protocol_list = cast(list, win.mpv.protocol_list)
        path_exists = os.path.exists(url)
        if parsed.scheme in protocol_list or path_exists:
            win.url = url
            return True
        if url:
            win.url = f"https://{url}"
            return True
        return False

    def on_text_changed(*args):
        btn_open.set_sensitive(is_valid_input(entry_row.get_text()))

    entry_row.connect("notify::text", on_text_changed)

    def open_url(*args):
        dialog.close()
        try:
            win.mpv.loadfile(win.url, mode)
            if mode == "replace":
                win.mpv.pause = False
                win.shuffle_toggle_btn.set_active(False)
        except mpv.ShutdownError:
            pass

    def on_clipboard_read(clipboard: Gdk.Clipboard, result):
        if not (text := clipboard.read_text_finish(result)):
            return
        if urlparse(text).scheme in cast(list, win.mpv.protocol_list):
            entry_row.insert_text(text, 0)

    display_obj = Gdk.Display.get_default()
    if display_obj and (clipboard := display_obj.get_clipboard()):
        clipboard.read_text_async(None, on_clipboard_read)

    btn_open.connect("clicked", open_url)
    dialog.present(win)


def update_chapter_marks_and_menu(win: Any, chapters: list[dict[str, Any]]) -> None:
    """Update chapter marks on the seek scale and chapter menu items."""
    if not chapters:
        win.video_progress_scale.clear_marks()
        win.chapters_menu_btn.set_visible(False)
        return

    win._chapters = sorted(chapters, key=lambda c: c.get("time", 0))
    win.chapter_times, win.chapter_titles = [], []
    win.chapters_menu_btn.set_visible(True)
    win.chapters_menu.remove_all()

    for i, chapter in enumerate(win._chapters):
        title = chapter.get("title") or _("Chapter") + f" {i + 1}"
        item = Gio.MenuItem.new(title, None)
        item.set_action_and_target_value("win.select-chapter", GLib.Variant("i", i))
        win.chapters_menu.append_item(item)

        time_pos = chapter.get("time")
        if time_pos is not None:
            win.video_progress_scale.add_mark(time_pos, Gtk.PositionType.TOP, None)

        win.chapter_times.append(chapter.get("time"))
        win.chapter_titles.append(GLib.markup_escape_text(title))


def navigate_playlist(win: Any, direction: int) -> None:
    """Move playlist position forward or backward with wraparound."""
    pos = int(win.mpv.playlist_pos or 0)
    count = int(win.mpv.playlist_count or 0)

    if count > 0:
        win.mpv.playlist_pos = (pos + direction) % count


def on_shuffle_toggled(win: Any, button: Any) -> None:
    """Toggle playlist shuffle in mpv and sync MPRIS/playlist dialog."""
    from . import window as win_mod

    active = button.props.active
    cmd = "playlist-shuffle" if active else "playlist-unshuffle"
    win.mpv.command(cmd)

    win._mpris.update_shuffle(active)
    win.prev_shuffle = not active

    if isinstance(win._visible_dialog, Playlist):
        win_mod.idle_add_once(win.splice_playlist)


def sync_can_prev_next(win: Any) -> None:
    """Update prev/next sensitivity, actions, and shuffle button visibility."""
    try:
        count: int = cast(int, win.mpv.playlist_count) or 0
        pos: int = cast(int, win.mpv.playlist_pos) or 0
        has_multiple: bool = count > 1
        loop_list_on: bool = win.mpv.loop_playlist is not False and has_multiple

        win.can_go_prev = loop_list_on or (has_multiple and pos > 0)
        win.can_go_next = loop_list_on or (has_multiple and pos < count - 1)

        win._mpris.update_can_prev_next(win.can_go_prev, win.can_go_next)

        win.previous_btn.props.sensitive = win.can_go_prev
        win.next_btn.props.sensitive = win.can_go_next

        win._actions["previous"].props.enabled = win.can_go_prev
        win._actions["next"].props.enabled = win.can_go_next

        win.shuffle_toggle_btn.props.visible = has_multiple
    except mpv.ShutdownError:
        pass


def handle_drop(win: Any, value: Any) -> None:
    """Process dropped files, directories, subtitles, or URLs onto the window."""
    from . import window as win_mod

    win.revealer_drop_indicator.set_reveal_child(False)
    items: list[Gio.File] | list[str] = []
    playable_items: list[str] = []

    if win_mod.is_same_playlist(win.mpv.playlist):
        win.mpv.write_watch_later_config()

    if isinstance(value, Gdk.FileList):
        items = value.get_files()
    elif isinstance(value, str):
        items = [value]

    for item in items:
        if isinstance(item, str):
            playable_items.append(item)
            continue

        path = item.get_path() or item.get_uri()
        if not win_mod.is_local_path(path):
            playable_items.append(path)
            continue

        try:
            info = item.query_info(
                "standard::content-type,standard::type",
                Gio.FileQueryInfoFlags.NONE,
                None,
            )
        except Exception as e:
            win_mod.logger.exception("Drop failed")
            win_mod.idle_add_once(win.show_toast, str(e))
            return

        name = (item.get_basename() or "").lower()
        if name.endswith(SUB_EXTS):
            if not win.mpv.idle_active:
                win.mpv.command_async("sub-add", path, "select")
            continue

        mime = info.get_content_type() or ""
        if info.get_file_type() == Gio.FileType.DIRECTORY or mime.startswith(
            ("video/", "audio/", "image/")
        ):
            playable_items.append(path)

    for idx, source in enumerate(playable_items):
        mode = "replace" if idx == 0 else "append-play"
        win.mpv.loadfile(source, mode)

    if playable_items:
        win.mpv.command_async("set", "pause", "no")


def splice_playlist(win: Any) -> None:
    """Synchronize Gio.ListStore playlist model from current mpv playlist."""
    from . import window as win_mod

    win._playlist_debounce_id = 0
    win.has_some_doc_path = False
    new_items = []
    for idx, item in enumerate(cast(list, win.mpv.playlist)):
        new_items.append(PlaylistItemObj(item, idx))

        if (
            win.has_some_doc_path
            or f"/run/user/{os.getuid()}/doc/" not in item.get("filename")
            or win_mod.has_host_permission
        ):
            continue
        win.has_some_doc_path = True

    if isinstance(win._visible_dialog, Playlist):
        win._visible_dialog.set_save_btn_playlist()
        win._visible_dialog.set_item_count()

    win.playlist_ls.splice(0, win.playlist_ls.get_n_items(), new_items)
    win.prev_shuffle = win.shuffle_toggle_btn.props.active
    win.playlist_changed = False
