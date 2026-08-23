"""Focused tests for the internal GPU Next render-backend selector."""

import ast
import ctypes
import os
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.diagnostics_report import build_video_output_report
from src.render_backend import (
    RENDER_BACKEND_ENV,
    ProcessRenderSelection,
    RenderBackend,
    RenderBackendConfigurationError,
    RenderBackendUnavailableError,
    RenderSelectionSource,
    create_render_context,
    dolby_vision_render_capability,
    install_python_mpv_depth_compat,
    mapped_library_path,
    render_target_spec,
    render_runtime_diagnostics,
    renderer_preference_requires_restart,
    requested_backend,
    select_process_backend,
    should_render_frame,
    target_configuration_requires_redraw,
)


class TestRenderBackendSelector(unittest.TestCase):
    def test_default_is_legacy(self):
        self.assertIs(requested_backend({}), RenderBackend.LEGACY)

    def test_invalid_environment_value_is_explicit(self):
        with self.assertRaises(RenderBackendConfigurationError):
            requested_backend({RENDER_BACKEND_ENV: "fastest"})

    def test_explicit_gpu_next_does_not_fallback(self):
        attempted = []

        def create(api_type):
            attempted.append(api_type)
            raise RuntimeError("not compiled in")

        with self.assertRaises(RenderBackendUnavailableError):
            create_render_context(RenderBackend.GPU_NEXT, create)
        self.assertEqual(attempted, ["opengl-next"])

    def test_auto_falls_back_only_when_initial_next_creation_fails(self):
        attempted = []

        def create(api_type):
            attempted.append(api_type)
            if api_type == "opengl-next":
                raise RuntimeError("unsupported API type")
            return "legacy-context"

        context, state = create_render_context(RenderBackend.AUTO, create)
        self.assertEqual(context, "legacy-context")
        self.assertEqual(attempted, ["opengl-next", "opengl"])
        self.assertEqual(state.active, "opengl")
        self.assertIn("unsupported API type", state.fallback_reason)

    def test_auto_uses_next_without_fallback(self):
        context, state = create_render_context(
            RenderBackend.AUTO, lambda api_type: f"{api_type}-context"
        )
        self.assertEqual(context, "opengl-next-context")
        self.assertEqual(state.active, "opengl-next")
        self.assertIsNone(state.fallback_reason)

    def test_process_selection_uses_settings_and_allows_saved_next_fallback(self):
        legacy = select_process_backend("legacy", {})
        gpu_next = select_process_backend("gpu-next", {})
        self.assertEqual(legacy.source, RenderSelectionSource.SETTINGS)
        self.assertEqual(legacy.requested, RenderBackend.LEGACY)
        self.assertFalse(legacy.allow_creation_fallback)
        self.assertEqual(gpu_next.requested, RenderBackend.GPU_NEXT)
        self.assertTrue(gpu_next.allow_creation_fallback)

    def test_environment_override_has_priority_and_gpu_next_is_strict(self):
        selection = select_process_backend(
            "legacy", {RENDER_BACKEND_ENV: "gpu-next"}
        )
        self.assertEqual(selection.configured, RenderBackend.LEGACY)
        self.assertEqual(selection.requested, RenderBackend.GPU_NEXT)
        self.assertEqual(selection.source, RenderSelectionSource.ENVIRONMENT)
        self.assertFalse(selection.allow_creation_fallback)

    def test_environment_auto_is_development_fallback(self):
        selection = select_process_backend(
            "gpu-next", {RENDER_BACKEND_ENV: "auto"}
        )
        self.assertEqual(selection.configured, RenderBackend.GPU_NEXT)
        self.assertEqual(selection.requested, RenderBackend.AUTO)
        self.assertTrue(selection.allow_creation_fallback)

    def test_saved_gpu_next_falls_back_without_changing_preference(self):
        selection = select_process_backend("gpu-next", {})
        attempted = []

        def create(api_type):
            attempted.append(api_type)
            if api_type == "opengl-next":
                raise RuntimeError("unpatched libmpv")
            return "legacy-context"

        context, state = create_render_context(
            selection.requested,
            create,
            allow_gpu_next_fallback=selection.allow_creation_fallback,
        )
        self.assertEqual(context, "legacy-context")
        self.assertEqual(attempted, ["opengl-next", "opengl"])
        self.assertEqual(selection.configured, RenderBackend.GPU_NEXT)
        self.assertEqual(state.requested, RenderBackend.GPU_NEXT)
        self.assertIn("unpatched libmpv", state.fallback_reason)

    def test_restart_indicator_tracks_requested_session_and_revert(self):
        selection = ProcessRenderSelection(
            configured=RenderBackend.LEGACY,
            requested=RenderBackend.LEGACY,
            source=RenderSelectionSource.SETTINGS,
            allow_creation_fallback=False,
        )
        self.assertTrue(
            renderer_preference_requires_restart(selection, "gpu-next")
        )
        self.assertFalse(renderer_preference_requires_restart(selection, "legacy"))

    def test_environment_override_is_not_reported_as_restart_pending(self):
        selection = ProcessRenderSelection(
            configured=RenderBackend.LEGACY,
            requested=RenderBackend.GPU_NEXT,
            source=RenderSelectionSource.ENVIRONMENT,
            allow_creation_fallback=False,
        )
        self.assertFalse(
            renderer_preference_requires_restart(selection, "legacy")
        )
        self.assertFalse(
            renderer_preference_requires_restart(selection, "gpu-next")
        )

    def test_dovi_rpu_path_is_version_qualified(self):
        runtime = {
            "mpv_version": "mpv v0.41.0-dev-g97179bce7",
            "libplacebo_version": "v7.360.1",
            "ffmpeg_version": "8.1.2",
        }
        capability = dolby_vision_render_capability(
            "opengl-next", "active", runtime
        )
        self.assertTrue(capability.rpu_path_available)
        self.assertEqual(capability.reason, "pinned-source-reviewed-path")

        unknown = dolby_vision_render_capability(
            "opengl-next",
            "active",
            {**runtime, "mpv_version": "mpv v0.42.0"},
        )
        self.assertFalse(unknown.rpu_path_available)
        self.assertEqual(unknown.reason, "unvalidated-mpv-build")

        legacy = dolby_vision_render_capability("opengl", "active", runtime)
        self.assertFalse(legacy.rpu_path_available)
        self.assertEqual(legacy.reason, "inactive-backend")


class TestRendererSettingIntegration(unittest.TestCase):
    def test_schema_has_persistent_enum_with_legacy_default(self):
        schema_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "data",
            "io.github.rusmikev.CineHDR.gschema.xml",
        )
        root = ET.parse(schema_path).getroot()
        renderer_enum = root.find(
            "./enum[@id='io.github.rusmikev.CineHDR.RenderBackend']"
        )
        self.assertIsNotNone(renderer_enum)
        self.assertEqual(
            [value.attrib["nick"] for value in renderer_enum.findall("value")],
            ["legacy", "gpu-next"],
        )
        key = root.find("./schema/key[@name='render-backend']")
        self.assertIsNotNone(key)
        self.assertEqual(
            key.attrib["enum"], "io.github.rusmikev.CineHDR.RenderBackend"
        )
        self.assertEqual(key.findtext("default"), "'legacy'")

    def test_menu_is_always_visible_and_restart_only(self):
        menu_path = os.path.join(
            os.path.dirname(__file__), "..", "src", "hdr_menu.blp"
        )
        with open(menu_path, encoding="utf-8") as menu_file:
            menu = menu_file.read()
        self.assertIn('tooltip-text: _("HDR & Video Output")', menu)
        self.assertNotIn("&amp;", menu)
        self.assertIn("visible: true;", menu)
        self.assertIn("GPU Next (Experimental)", menu)
        self.assertIn("Restart CineHDR to apply", menu)
        self.assertIn("Current session: Not initialized", menu)

        menu_code_path = os.path.join(
            os.path.dirname(__file__), "..", "src", "hdr_menu.py"
        )
        with open(menu_code_path, encoding="utf-8") as menu_file:
            tree = ast.parse(menu_file.read())
        reset_method = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_on_hdr_reset"
        )
        reset_literals = {
            node.value
            for node in ast.walk(reset_method)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        self.assertNotIn("render-backend", reset_literals)

    def test_renderer_setting_is_available_from_start_page_preferences(self):
        preferences_path = os.path.join(
            os.path.dirname(__file__), "..", "src", "preferences.blp"
        )
        with open(preferences_path, encoding="utf-8") as preferences_file:
            preferences = preferences_file.read()
        self.assertIn("Adw.SwitchRow renderer_row", preferences)
        self.assertIn("GPU Next (Experimental)", preferences)
        self.assertIn("Restart CineHDR to apply", preferences)

        preferences_code_path = os.path.join(
            os.path.dirname(__file__), "..", "src", "preferences.py"
        )
        with open(preferences_code_path, encoding="utf-8") as source_file:
            preferences_code = source_file.read()
        self.assertIn("Developer environment override is active", preferences_code)
        self.assertIn("not after a", preferences_code)

        window_path = os.path.join(
            os.path.dirname(__file__), "..", "src", "window.blp"
        )
        with open(window_path, encoding="utf-8") as window_file:
            window = window_file.read()
        self.assertIn('"app.preferences"', window)

    def test_process_selection_is_passed_to_video_widget(self):
        paths = {
            name: os.path.join(os.path.dirname(__file__), "..", "src", name)
            for name in ("main.py", "window.py", "mpv_gl_area.py")
        }
        sources = {}
        for name, path in paths.items():
            with open(path, encoding="utf-8") as source_file:
                sources[name] = source_file.read()
        self.assertIn("select_process_backend", sources["main.py"])
        self.assertIn("render_backend_selection=self.app.render_backend_selection", sources["window.py"])
        self.assertIn("super().__init__(mpv_instance, render_backend_selection)", sources["mpv_gl_area.py"])

    def test_gpu_next_launcher_respects_ui_and_external_override(self):
        launcher_path = os.path.join(
            os.path.dirname(__file__), "..", "run_gpu_next.sh"
        )
        with open(launcher_path, encoding="utf-8") as launcher_file:
            launcher = launcher_file.read()
        self.assertIn("LD_LIBRARY_PATH", launcher)
        self.assertNotIn("export CINEHDR_RENDER_BACKEND=", launcher)

    def test_development_build_tracks_each_generated_blueprint(self):
        meson_path = os.path.join(
            os.path.dirname(__file__), "..", "src", "meson.build"
        )
        with open(meson_path, encoding="utf-8") as meson_file:
            meson_source = meson_file.read()
        self.assertIn("'hdr_menu.ui'", meson_source)
        self.assertIn("'hdr_diagnostics.ui'", meson_source)
        self.assertIn("'@OUTDIR@'", meson_source)
        self.assertNotIn("output: '.'", meson_source)

        run_dev_path = os.path.join(
            os.path.dirname(__file__), "..", "run_dev.py"
        )
        with open(run_dev_path, encoding="utf-8") as run_dev_file:
            run_dev_source = run_dev_file.read()
        self.assertIn(
            'subprocess.run(["meson", "compile", "-C", "build"]',
            run_dev_source,
        )

    def test_diagnostics_ui_is_compact_and_has_copy_lifecycle(self):
        blueprint_path = os.path.join(
            os.path.dirname(__file__), "..", "src", "hdr_diagnostics.blp"
        )
        with open(blueprint_path, encoding="utf-8") as blueprint_file:
            blueprint = blueprint_file.read()
        self.assertIn('title: _("Video Output Diagnostics")', blueprint)
        self.assertIn('icon-name: "edit-copy-symbolic"', blueprint)
        self.assertNotIn("&amp;", blueprint)
        for row_name in (
            "renderer_requested_row",
            "renderer_dependency_row",
            "gpu_next_capability_row",
            "dovi_capability_row",
        ):
            row_block = blueprint.split(f"Adw.ActionRow {row_name}", 1)[1].split(
                "}", 1
            )[0]
            self.assertIn("visible: false;", row_block)
        for title in (
            "Current Renderer",
            "After Restart",
            "Status",
            "Render Target",
        ):
            self.assertIn(title, blueprint)

        source_path = os.path.join(
            os.path.dirname(__file__), "..", "src", "hdr_diagnostics.py"
        )
        with open(source_path, encoding="utf-8") as source_file:
            source = source_file.read()
            tree = ast.parse(source)
        self.assertNotIn("Unvalidated and disabled", source)
        self.assertNotIn("Direct 16-bit HDR Pass-through", source)
        self.assertNotIn("HDR10 base layer", source)
        copy_method = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_on_copy"
        )
        self.assertTrue(
            any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "set"
                for node in ast.walk(copy_method)
            )
        )
        for lifecycle_name in ("_on_unrealize", "_on_closed"):
            method = next(
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name == lifecycle_name
            )
            self.assertTrue(
                any(
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "_stop_copy_feedback"
                    for node in ast.walk(method)
                )
            )
        self.assertNotIn("NVIDIA workaround", source)

    def test_dovi_ab_probe_is_embedded_negative_control(self):
        probe_path = os.path.join(
            os.path.dirname(__file__), "dovi_rpu_ab_egl.c"
        )
        with open(probe_path, encoding="utf-8") as probe_file:
            probe = probe_file.read()
        self.assertIn("MPV_RENDER_API_TYPE_OPENGL_NEXT", probe)
        self.assertIn('format=dolbyvision=no', probe)
        self.assertIn('video-out-params/colormatrix', probe)
        self.assertIn('rpu_effect_observed', probe)
        self.assertIn('color validation still pending', probe)

        runner_path = os.path.join(
            os.path.dirname(__file__), "run_dovi_rpu_ab.py"
        )
        with open(runner_path, encoding="utf-8") as runner_file:
            runner = runner_file.read()
        self.assertIn('default="surfaceless"', runner)
        self.assertNotIn("/mnt/", runner)


class TestDiagnosticsReport(unittest.TestCase):
    def test_plain_text_report_is_structured_complete_and_path_safe(self):
        report = build_video_output_report(
            {
                "Requested this session": "gpu-next",
                "Active API": "opengl-next",
                "mpv configuration": "--enable-libplacebo\n--enable-libmpv",
                "libmpv path": "/opt/cinehdr/lib/libmpv.so.2",
                "Media Path": "/private/movie.mkv",
            },
            {"HDR Status": "Active", "Surface Format": "RGBA16F"},
            {"Video Codec / Format": "hevc", "Resolution": "3840x2160"},
        )
        self.assertIn("[Video Renderer]", report)
        self.assertIn("[Output & Color State]", report)
        self.assertIn("[Video Signal]", report)
        self.assertIn("Requested this session: gpu-next", report)
        self.assertIn("Active API: opengl-next", report)
        self.assertIn("mpv configuration: --enable-libplacebo --enable-libmpv", report)
        self.assertIn("libmpv path: /opt/cinehdr/lib/libmpv.so.2", report)
        self.assertIn("HDR Status: Active", report)
        self.assertIn("Video Codec / Format: hevc", report)
        self.assertNotIn("/private/movie.mkv", report)


class TestRenderTargetSpec(unittest.TestCase):
    def test_float_and_byte_targets_have_matching_format_and_depth(self):
        float_target = render_target_spec(True, rgba16f=0x881A, rgba8=0x8058)
        byte_target = render_target_spec(False, rgba16f=0x881A, rgba8=0x8058)
        self.assertEqual((float_target.internal_format, float_target.depth), (0x881A, 16))
        self.assertEqual((byte_target.internal_format, byte_target.depth), (0x8058, 8))

    def test_paused_target_resize_can_redraw_the_previous_frame(self):
        self.assertFalse(should_render_frame(0, force_redraw=False))
        self.assertTrue(should_render_frame(1, force_redraw=False))
        self.assertTrue(should_render_frame(0, force_redraw=True))

    def test_only_an_existing_texture_needs_target_reconfiguration(self):
        old = (1120, 630, 2)
        new = (1280, 720, 2)
        self.assertFalse(
            target_configuration_requires_redraw(
                old, new, has_texture=False
            )
        )
        self.assertFalse(
            target_configuration_requires_redraw(
                old, old, has_texture=True
            )
        )
        self.assertTrue(
            target_configuration_requires_redraw(
                old, new, has_texture=True
            )
        )
        self.assertTrue(
            target_configuration_requires_redraw(
                old, (1120, 630, 1), has_texture=True
            )
        )

    def test_video_widget_coalesces_size_redraws_through_the_render_path(self):
        source_path = os.path.join(
            os.path.dirname(__file__), "..", "src", "video_widget.py"
        )
        with open(source_path, encoding="utf-8") as source_file:
            source = source_file.read()

        self.assertIn("def do_size_allocate(self, width: int, height: int, baseline: int)", source)
        self.assertIn("Gtk.Widget.do_size_allocate(self, width, height, baseline)", source)
        self.assertIn("target_configuration_requires_redraw(", source)
        self.assertIn("self._schedule_render(force_redraw=True)", source)
        self.assertIn("update_flags = self.mpv_ctx.update()", source)
        self.assertIn("force_redraw=force_redraw", source)

    def test_size_allocate_executes_parent_chain_and_forced_redraw(self):
        from src.video_widget import MpvVideoWidget

        widget = SimpleNamespace(
            _last_target_configuration=(1120, 630, 2),
            props=SimpleNamespace(scale_factor=2),
            current_texture=object(),
            _schedule_render=Mock(),
        )
        with patch("src.video_widget.Gtk.Widget.do_size_allocate") as parent:
            MpvVideoWidget.do_size_allocate(widget, 1280, 720, -1)

        parent.assert_called_once_with(widget, 1280, 720, -1)
        self.assertEqual(widget._last_target_configuration, (1280, 720, 2))
        widget._schedule_render.assert_called_once_with(force_redraw=True)

    def test_main_render_passes_depth_outside_opengl_fbo(self):
        source_path = os.path.join(
            os.path.dirname(__file__), "..", "src", "video_widget.py"
        )
        with open(source_path, encoding="utf-8") as source_file:
            tree = ast.parse(source_file.read())

        render_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "render"
        ]
        self.assertEqual(len(render_calls), 1)
        keywords = {keyword.arg: keyword.value for keyword in render_calls[0].keywords}
        self.assertIn("depth", keywords)
        self.assertIn("opengl_fbo", keywords)
        self.assertIsInstance(keywords["opengl_fbo"], ast.Dict)
        fbo_keys = {
            key.value
            for key in keywords["opengl_fbo"].keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        self.assertIn("internal_format", fbo_keys)
        self.assertNotIn("depth", fbo_keys)

    def test_render_failure_restores_fbo_and_latches_renderer(self):
        source_path = os.path.join(
            os.path.dirname(__file__), "..", "src", "video_widget.py"
        )
        with open(source_path, encoding="utf-8") as source_file:
            tree = ast.parse(source_file.read())

        render_method = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef)
            and node.name == "_render_pending_frame"
        )
        render_try = next(
            node
            for node in ast.walk(render_method)
            if isinstance(node, ast.Try)
            and any(
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "render"
                for child in ast.walk(node)
            )
        )
        restores_default_fbo = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "glBindFramebuffer"
            and len(node.args) == 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == 0
            for statement in render_try.finalbody
            for node in ast.walk(statement)
        )
        self.assertTrue(restores_default_fbo)

        failure_latches = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_latch_render_restart"
            and any(
                keyword.arg == "status"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value == "runtime-failure"
                for keyword in node.keywords
            )
            for node in ast.walk(render_method)
        )
        self.assertTrue(failure_latches)

    def test_context_creation_failure_is_recorded_for_diagnostics(self):
        source_path = os.path.join(
            os.path.dirname(__file__), "..", "src", "video_widget.py"
        )
        with open(source_path, encoding="utf-8") as source_file:
            tree = ast.parse(source_file.read())
        realize = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "_on_realize"
        )
        creation_try = next(
            node
            for node in ast.walk(realize)
            if isinstance(node, ast.Try)
            and any(
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id == "create_render_context"
                for child in ast.walk(node)
            )
        )
        self.assertTrue(
            any(
                isinstance(node, ast.Constant)
                and node.value == "initialization-failure"
                for handler in creation_try.handlers
                for node in ast.walk(handler)
            )
        )

    def test_dolby_vision_warning_is_backend_neutral(self):
        controller_path = os.path.join(
            os.path.dirname(__file__), "..", "src", "hdr_controller.py"
        )
        with open(controller_path, encoding="utf-8") as controller_file:
            controller = controller_file.read()
        self.assertNotIn("legacy GPU renderer", controller)
        self.assertIn("disabled until the active embedded renderer", controller)


class TestRenderLifecycleSource(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source_path = os.path.join(
            os.path.dirname(__file__), "..", "src", "video_widget.py"
        )
        with open(source_path, encoding="utf-8") as source_file:
            tree = ast.parse(source_file.read())
        widget_class = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "MpvVideoWidget"
        )
        cls.methods = {
            node.name: node
            for node in widget_class.body
            if isinstance(node, ast.FunctionDef)
        }

    @staticmethod
    def _attribute_call_lines(method, attribute):
        return sorted(
            node.lineno
            for node in ast.walk(method)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == attribute
        )

    def test_shutdown_order_uses_owning_area_before_free_and_pool_release(self):
        method = self.methods["_shutdown_render_context_for_area"]
        callback_disable_lines = [
            node.lineno
            for node in ast.walk(method)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Attribute) and target.attr == "update_cb"
                for target in node.targets
            )
        ]
        make_current = self._attribute_call_lines(method, "make_current")
        get_error = self._attribute_call_lines(method, "get_error")
        free = self._attribute_call_lines(method, "free")
        release_all = self._attribute_call_lines(method, "release_all")
        self.assertEqual(len(callback_disable_lines), 1)
        self.assertEqual(len(make_current), 1)
        self.assertEqual(len(get_error), 1)
        self.assertEqual(len(free), 1)
        self.assertEqual(len(release_all), 1)
        self.assertLess(
            callback_disable_lines[0],
            make_current[0],
        )
        self.assertLess(make_current[0], get_error[0])
        self.assertLess(get_error[0], free[0])
        self.assertLess(free[0], release_all[0])

        texture_clear_lines = [
            node.lineno
            for node in ast.walk(method)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Attribute)
                and target.attr in ("current_texture", "_fallback_slot")
                for target in node.targets
            )
            and isinstance(node.value, ast.Constant)
            and node.value.value is None
        ]
        fallback_release_lines = [
            node.lineno
            for node in ast.walk(method)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "release_buffer"
        ]
        self.assertEqual(len(texture_clear_lines), 2)
        self.assertEqual(len(fallback_release_lines), 1)
        self.assertTrue(all(line < release_all[0] for line in texture_clear_lines))
        self.assertLess(fallback_release_lines[0], release_all[0])

        unrealize = self.methods["_on_unrealize"]
        shutdown_calls = [
            node
            for node in ast.walk(unrealize)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_shutdown_render_context_for_area"
        ]
        self.assertEqual(len(shutdown_calls), 1)
        self.assertIsInstance(shutdown_calls[0].args[0], ast.Name)
        self.assertEqual(shutdown_calls[0].args[0].id, "area")

    def test_shutdown_aborts_if_owning_gl_context_is_invalid(self):
        method = self.methods["_shutdown_render_context_for_area"]
        error_guard = next(
            node
            for node in ast.walk(method)
            if isinstance(node, ast.If)
            and any(
                isinstance(child, ast.Name) and child.id == "area_error"
                for child in ast.walk(node.test)
            )
        )
        self.assertTrue(
            any(isinstance(node, ast.Return) for node in ast.walk(error_guard))
        )

        make_current_try = next(
            node
            for node in ast.walk(method)
            if isinstance(node, ast.Try)
            and any(
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "make_current"
                for child in ast.walk(node)
            )
        )
        self.assertTrue(make_current_try.handlers)
        self.assertTrue(
            any(
                isinstance(node, ast.Return)
                for handler in make_current_try.handlers
                for node in ast.walk(handler)
            )
        )

    def test_realize_latches_stale_context_before_touching_new_gl_context(self):
        method = self.methods["_on_realize"]
        self.assertFalse(self._attribute_call_lines(method, "free"))
        latch_lines = self._attribute_call_lines(method, "_latch_render_restart")
        make_current = self._attribute_call_lines(method, "make_current")
        get_error = self._attribute_call_lines(method, "get_error")
        create_lines = [
            node.lineno
            for node in ast.walk(method)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "create_render_context"
        ]
        self.assertGreaterEqual(len(latch_lines), 2)
        self.assertEqual(len(make_current), 1)
        self.assertEqual(len(get_error), 1)
        self.assertEqual(len(create_lines), 1)
        self.assertLess(latch_lines[0], make_current[0])
        self.assertLess(make_current[0], get_error[0])
        self.assertLess(get_error[0], create_lines[0])

    def test_terminal_failure_cannot_be_cleared_by_glarea_recreation(self):
        latch = self.methods["_latch_render_restart"]
        terminal_true = [
            node
            for node in ast.walk(latch)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Attribute)
                and target.attr == "_render_terminal_failure"
                for target in node.targets
            )
            and isinstance(node.value, ast.Constant)
            and node.value.value is True
        ]
        self.assertEqual(len(terminal_true), 1)

        realize = self.methods["_on_realize"]
        terminal_guard = next(
            node
            for node in realize.body
            if isinstance(node, ast.If)
            and any(
                isinstance(child, ast.Attribute)
                and child.attr == "_render_terminal_failure"
                for child in ast.walk(node.test)
            )
        )
        self.assertTrue(
            any(isinstance(node, ast.Return) for node in ast.walk(terminal_guard))
        )
        make_current = self._attribute_call_lines(realize, "make_current")
        create_lines = [
            node.lineno
            for node in ast.walk(realize)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "create_render_context"
        ]
        self.assertLess(terminal_guard.lineno, make_current[0])
        self.assertLess(terminal_guard.lineno, create_lines[0])

        for method_name, method in self.methods.items():
            if method_name == "__init__":
                continue
            terminal_false = [
                node
                for node in ast.walk(method)
                if isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Attribute)
                    and target.attr == "_render_terminal_failure"
                    for target in node.targets
                )
                and isinstance(node.value, ast.Constant)
                and node.value.value is False
            ]
            self.assertEqual(terminal_false, [], method_name)

    def test_hdr_controller_disconnect_is_dispose_only(self):
        disconnecting_methods = []
        for method_name, method in self.methods.items():
            for node in ast.walk(method):
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "disconnect"
                    and isinstance(node.func.value, ast.Attribute)
                    and node.func.value.attr == "hdr_controller"
                ):
                    continue
                disconnecting_methods.append(method_name)
        self.assertEqual(disconnecting_methods, ["do_dispose"])

    def test_realize_handlers_are_disconnected_on_both_lifecycle_edges(self):
        for method_name in ("_on_realize", "_on_unrealize"):
            calls = self._attribute_call_lines(
                self.methods[method_name], "_disconnect_realize_handlers"
            )
            self.assertEqual(len(calls), 1)
        disconnect_method = self.methods["_disconnect_realize_handlers"]
        self.assertGreaterEqual(
            len(self._attribute_call_lines(disconnect_method, "disconnect")), 2
        )

    def test_video_selection_is_captured_before_context_free(self):
        shutdown = self.methods["_shutdown_render_context_for_area"]
        capture = self._attribute_call_lines(
            shutdown, "_capture_video_selection_for_context_restore"
        )
        free = self._attribute_call_lines(shutdown, "free")
        self.assertEqual(len(capture), 1)
        self.assertEqual(len(free), 1)
        self.assertLess(capture[0], free[0])

    def test_video_selection_restore_follows_update_callback_installation(self):
        realize = self.methods["_on_realize"]
        callback_assignment = [
            node.lineno
            for node in ast.walk(realize)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Attribute)
                and target.attr == "update_cb"
                for target in node.targets
            )
        ]
        restore = self._attribute_call_lines(
            realize, "_restore_video_selection_after_context_recreation"
        )
        self.assertEqual(len(callback_assignment), 1)
        self.assertEqual(len(restore), 1)
        self.assertLess(callback_assignment[0], restore[0])

    def test_video_restore_forces_track_transition_and_latches_failure(self):
        restore = self.methods[
            "_restore_video_selection_after_context_recreation"
        ]
        commands = [
            node
            for node in ast.walk(restore)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "command"
        ]
        self.assertEqual(len(commands), 2)
        self.assertEqual(
            [node.value for node in commands[0].args[:3]],
            ["set", "vid", "no"],
        )
        self.assertIsInstance(commands[1].args[2], ast.Name)
        self.assertEqual(commands[1].args[2].id, "selection")
        self.assertEqual(
            len(self._attribute_call_lines(restore, "_latch_render_restart")),
            1,
        )


class TestPythonMpvDepthCompatibility(unittest.TestCase):
    def test_repairs_python_mpv_108_integer_constructor(self):
        class BrokenRenderParam:
            TYPES = {"depth": (5, int)}

            def __init__(self, name, value=None):
                self.type_id, constructor = self.TYPES[name]
                self.value = constructor(**value)

        class FakeMpv:
            MpvRenderParam = BrokenRenderParam

        self.assertTrue(install_python_mpv_depth_compat(FakeMpv))
        param = BrokenRenderParam("depth", 16)
        self.assertEqual(param.type_id, 5)
        self.assertEqual(
            ctypes.cast(param.data, ctypes.POINTER(ctypes.c_int)).contents.value,
            16,
        )
        self.assertFalse(install_python_mpv_depth_compat(FakeMpv))


class TestRuntimeDiagnostics(unittest.TestCase):
    def test_finds_real_loaded_library_path(self):
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as maps_file:
            maps_file.write(
                "7f000-7f100 r-xp 00000000 00:01 1 "
                "/opt/cinehdr/lib/libmpv.so.2.5.0\n"
            )
            maps_file.flush()
            self.assertEqual(
                mapped_library_path("libmpv", maps_path=maps_file.name),
                "/opt/cinehdr/lib/libmpv.so.2.5.0",
            )

    def test_collects_mpv_identity_with_soname_fallback(self):
        class Backend:
            _name = "libmpv-test.so.2"

        class FakeMpvModule:
            backend = Backend()

        class Player:
            values = {
                "mpv-version": "mpv test-revision",
                "ffmpeg-version": "8.test",
                "mpv-configuration": "-Dlibmpv=true",
            }

            def _get_property(self, name):
                return self.values[name]

        runtime = render_runtime_diagnostics(FakeMpvModule, Player())
        self.assertIn("libmpv", runtime["libmpv_path"])
        self.assertEqual(runtime["mpv_version"], "mpv test-revision")
        self.assertEqual(runtime["ffmpeg_version"], "8.test")
        self.assertEqual(runtime["mpv_configuration"], "-Dlibmpv=true")


if __name__ == "__main__":
    unittest.main()
