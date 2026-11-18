# video_widget_render.py
#
# Copyright 2026 rusmikev / Diego Povliuk
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Per-frame render, fence, oracle, and first-frame helpers for MpvVideoWidget."""

from gettext import gettext as _
import logging
from typing import Any, Optional

from .gl_bindings import (
    GL_FRAMEBUFFER,
    GL_SYNC_FLUSH_COMMANDS_BIT,
    GL_SYNC_GPU_COMMANDS_COMPLETE,
    GL_TIMEOUT_EXPIRED,
    GL_WAIT_FAILED,
)


def is_publication_blocked(controller: Any) -> bool:
    """Return True while the HDR controller is transitioning or suppressed."""
    return bool(
        controller
        and (
            getattr(controller, "is_publication_pending", False)
            or getattr(controller, "is_publication_suppressed", False)
        )
    )


def sample_fbo_pixel_oracle(
    widget: Any, slot: Any, scaled_w: int, scaled_h: int, use_float: bool, vw: Any
) -> Optional[Any]:
    """Read FBO pixel samples while the slot FBO is bound, if an oracle is active."""
    if not (hasattr(widget, "_fbo_pixel_oracle_cb") and callable(widget._fbo_pixel_oracle_cb)):
        return None
    try:
        # libmpv may restore the default framebuffer on return.
        # Read from the caller-owned FBO that received this frame.
        vw.glBindFramebuffer(GL_FRAMEBUFFER, slot.resource.fbo_id.value)
        # The validation tool installs the callback and the sampler
        # together; without a sampler there is nothing to read.
        sampler = widget._fbo_pixel_oracle_sampler
        if callable(sampler):
            return sampler(scaled_w, scaled_h, use_float)
    except Exception as e:
        logging.warning(f"FBO pixel oracle readback failed: {e}")
        if hasattr(widget, "_fbo_pixel_oracle_error_cb") and callable(widget._fbo_pixel_oracle_error_cb):
            try:
                widget._fbo_pixel_oracle_error_cb(str(e))
            except Exception:
                pass
    return None


def log_first_frame(
    widget: Any, scaled_w: int, scaled_h: int, target_spec: Any
) -> None:
    """Log the first rendered frame target geometry and format once."""
    if widget._first_frame_logged:
        return
    logging.info(
        "Rendered first frame backend=%s target=%dx%d internal_format=0x%x depth=%d",
        widget.render_backend_active,
        scaled_w,
        scaled_h,
        target_spec.internal_format,
        target_spec.depth,
    )
    widget._first_frame_logged = True


def handle_render_error(
    widget: Any, render_error: Exception, slot: Any, vw: Any
) -> None:
    """Log frame render failure, release the FBO slot, and notify window."""
    logging.error(
        "Video renderer stopped after a frame-rendering failure; "
        "restart CineHDR in legacy mode: %s",
        render_error,
    )
    widget.fbo_pool.release_buffer(slot)
    window = getattr(widget, "_window", None)
    if window and hasattr(window, "show_toast"):
        vw.idle_add_once(
            window.show_toast,
            _("Video renderer stopped. Restart CineHDR."),
            True,
        )


def fence_and_check_superseded(
    widget: Any, slot: Any, controller: Any, render_epoch: Optional[int], vw: Any
) -> bool:
    """Create and flush GPU fence; release slot and return True if frame is superseded."""
    if vw.glFenceSync:
        slot.fence = vw.glFenceSync(GL_SYNC_GPU_COMMANDS_COMPLETE, 0)
    else:
        slot.fence = None

    # Submit the fence and pending GL commands to the GPU now: a fence
    # that was never flushed may never signal when another context
    # (GTK's renderer) waits on it via set_sync (F5).
    if vw.glFlush:
        try:
            vw.glFlush()
        except Exception:
            pass

    superseded = False
    if widget._shutting_down:
        superseded = True
    elif controller is not None:
        if getattr(controller, "_shutting_down", False) or getattr(
            controller, "is_publication_suppressed", False
        ):
            superseded = True
        elif getattr(controller, "is_publication_pending", False):
            superseded = True
        elif render_epoch is not None:
            current_committed = getattr(controller, "committed_output_state", None)
            if current_committed is None or current_committed.epoch != render_epoch:
                superseded = True

    if superseded:
        if slot.fence:
            if vw.glDeleteSync:
                try:
                    vw.glDeleteSync(slot.fence)
                except Exception:
                    pass
            slot.fence = None
        widget.fbo_pool.release_buffer(slot)
        return True
    return False


def attach_or_wait_fence(
    widget: Any, builder: Any, slot: Any, timeout_ns: int, vw: Any
) -> bool:
    """Attach GPU fence via set_sync or wait with bounded timeout on GTK < 4.16."""
    has_set_sync = False
    if slot.fence and hasattr(builder, "set_sync"):
        try:
            builder.set_sync(slot.fence)
            has_set_sync = True
        except AttributeError:
            pass

    if not has_set_sync and slot.fence and vw.glClientWaitSync:
        # GTK < 4.16: no GdkGLTextureBuilder.set_sync, so wait for the
        # GPU on the CPU side before publishing the texture. The timeout
        # is bounded so a stalled driver cannot freeze the UI thread.
        try:
            wait_result = vw.glClientWaitSync(
                slot.fence, GL_SYNC_FLUSH_COMMANDS_BIT, timeout_ns
            )
            if wait_result in (GL_TIMEOUT_EXPIRED, GL_WAIT_FAILED):
                logging.warning(
                    f"glClientWaitSync returned 0x{wait_result:04x}; "
                    "dropping frame to avoid tearing/artifacts"
                )
                widget.fbo_pool.release_buffer(slot)
                return False
        except Exception:
            pass
    return True


def notify_fbo_pixel_oracle(
    widget: Any,
    slot: Any,
    render_epoch: Optional[int],
    use_float: bool,
    scaled_w: int,
    scaled_h: int,
    pending_pixel_samples: Any,
) -> None:
    """Deliver captured FBO pixel samples to the oracle callback if installed."""
    if not (
        pending_pixel_samples is not None
        and hasattr(widget, "_fbo_pixel_oracle_cb")
        and callable(widget._fbo_pixel_oracle_cb)
    ):
        return
    try:
        widget._fbo_pixel_oracle_cb(
            slot=slot,
            epoch=render_epoch,
            frame_generation=widget.render_frame_generation,
            fbo_format=widget.render_target_format,
            is_float=use_float,
            width=scaled_w,
            height=scaled_h,
            patch_samples=pending_pixel_samples,
        )
    except Exception as e:
        logging.warning(f"FBO pixel oracle callback failed: {e}")
        if hasattr(widget, "_fbo_pixel_oracle_error_cb") and callable(
            widget._fbo_pixel_oracle_error_cb
        ):
            try:
                widget._fbo_pixel_oracle_error_cb(str(e))
            except Exception:
                pass


def dispatch_first_media_frame(widget: Any, rendered_media_frame: Any, vw: Any) -> None:
    """Schedule window notification once per (context, media_load) generation."""
    if rendered_media_frame is None or widget._first_media_frame_exported:
        return
    window = getattr(widget, "_window", None)
    exporter = getattr(window, "_diagnostics_exporter", None) if window else None
    export_completed = getattr(exporter, "export_completed", False) is True if exporter else False
    shutting_down = getattr(window, "_shutting_down", False) is True if window else False
    if export_completed or shutting_down:
        return

    raw_ctx_gen = getattr(widget, "render_context_generation", 0)
    ctx_gen = raw_ctx_gen if isinstance(raw_ctx_gen, int) else 0
    raw_load_gen = getattr(window, "_media_load_generation", 0) if window else 0
    load_gen = raw_load_gen if isinstance(raw_load_gen, int) else 0

    widget._first_media_frame_exported = True
    widget._exported_media_generation = (ctx_gen, load_gen)
    if window and hasattr(window, "on_first_media_frame_published"):
        vw.idle_add_once(
            window.on_first_media_frame_published,
            rendered_media_frame,
            ctx_gen,
            load_gen,
        )
    elif window and hasattr(window, "on_first_video_frame"):
        vw.idle_add_once(window.on_first_video_frame)
