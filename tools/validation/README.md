# Инвентарь инструментов валидации CineHDR (`C0` / решение `D7`)

Этот документ фиксирует проверяемую связь **«инвариант → исторический дефект или синтетический отрицательный контроль → инструмент и функции → регрессионный тест → команда воспроизведения → границы доказательства → точки подключения в продукте»** перед удалением governance-документов в задаче `C1` и переносом кода валидации в задачах `C2`, `C3` и `C4` (`docs/PLAN.md`, решение `D7`, `docs/adr/0008-release-scope-and-evidence.md`).

## Разграничение текущего расположения (`C0`) и целевого переноса (`C2`/`C3`/`C4`)

На этапе `C0` код продукта, тесты и скрипты запуска **не перемещаются**. Все пути и команды воспроизведения ниже разделены на **текущее расположение (на коммите `C0`)** и **целевое расположение после `C2`/`C3`/`C4`**:

| Компонент (объём решения `D7`) | Текущее расположение на этапе `C0` | Целевое расположение (`C2` / `C3` / `C4`) |
|---|---|---|
| Пиксельный оракул FBO и отрицательные контроли (`PausedTransitionPixelOracle`, `FboPixelCapture`, `read_fbo_patch_samples`, `compute_patch_centers`, `compute_samples_hash`, `serialize_fbo_pixel_measurements`, `verify_fbo_pixel_measurements_artifact`) | `src/gpu_validation.py` (тесты: `tests/test_hdr.py`, `tests/test_gpu_validation.py`) | `tools/validation/gpu_validation.py` (`C4a`; опциональная установка в `cinehdr/validation/` при `-Dvalidation=true` по `C4d`) |
| Парсер трассы протокола Wayland и его тесты (`validate_trace`, `validate_composite_window_trace`, `validate_multiphase_trace`, `create_evidence_report`) | `tests/parse_wayland_color_trace.py`, `tests/test_wayland_color_trace.py` | `tools/validation/parse_wayland_color_trace.py`, `tools/validation/test_wayland_color_trace.py` (`C2a`, остаются в `meson test`) |
| Паузовый автосценарий `SDR → HDR → SDR`, проверка первого опубликованного кадра и чистое завершение (`GpuValidationSession` в режиме `publication`, `validation_steps("publication")`, `publication_texture_evidence`, `evaluate_publication_validation`, лаунчер) | `src/gpu_validation.py`, `run_wayland_publication_validation.sh` (тесты: `tests/test_gpu_validation.py`, `tests/test_wayland_publication_scenario.py`, `tests/test_hdr.py`) | `tools/validation/gpu_validation.py` (`C4a`) и `tools/validation/run_wayland_publication_validation.sh` (`C3`) |
| Автоматический экспорт диагностического снимка первого опубликованного медиа-кадра (`DiagnosticsExporter`) | `src/diagnostics_report.py` (тесты: `tests/test_diagnostics_export.py`, `tests/test_attempt3_regressions.py`) | `tools/validation/diagnostics_export.py` (`C4b`); функции `collect_video_output_facts` и `build_video_output_report` остаются в `src/diagnostics_report.py` |

---

## Известные дефекты и их проверки

### 1. Пиксельный оракул FBO и отрицательные контроли (начиная с дефекта устаревших HDR-пикселей после перехода `HDR → SDR` на паузе)

#### 1.1. Защищаемый инвариант и конкретные дефекты
- **Защищаемый инвариант (`AGENTS.md` §4.1–4.2, `docs/adr/0001-gpu-next-render-api.md`, `docs/adr/0004-gate2-fbo-pixel-validation.md`, `docs/adr/0007-paused-hdr-output-republication.md`)**:
  При каждом переключении режима вывода или источника (`SDR ↔ HDR`), в том числе на паузе (`pause=True`, когда `mpv_render_context_update()` может возвращать `update_flags == 0`), `HdrController.apply_hdr_settings()` атомарно фиксирует неизменяемый снимок `CommittedOutputState(epoch, target_trc, target_prim, target_peak, fbo_format, gdk_color_state, is_float, is_hdr)`, а `MpvVideoWidget._on_hdr_controller_change()` принудительно планирует новый рендер (`_schedule_render(force_redraw=True)`). Первый же опубликованный после смены эпохи кадр обязан:
  1. быть отрендерен в FBO соответствующего формата (`GL_RGBA8` / `srgb` для SDR; `GL_RGBA16F` / `rec2100-pq` для HDR);
  2. содержать фактические пиксели в вызывающем FBO (`glReadPixels` из 8 канонических патчей `CANONICAL_8_PATCHES`), где `return_sdr` возвращается к эталону `initial_sdr` в пределах калиброванного допуска `DEFAULT_SDR_REPEAT_TOLERANCE = 0.020`, а `hdr` отличается от `initial_sdr` на `reference_white` / `peak_white` не менее чем на `DEFAULT_MIN_HDR_DISTINCTION = 0.150`;
  3. не затираться последующими («восстанавливающимися») кадрами той же фазы.
- **Конкретные исторические дефекты и отрицательные контроли**:
  1. **Дефект 1A (основной исторический дефект — устаревшие HDR-пиксели и `GL_RGBA16F`/`rec2100-pq` после перехода `HDR → SDR` на паузе)**:
     В авторизованном прогоне GNOME Gate 2W Run 6 на Candidate v7 (`2026-10-04`) переходы `initial_sdr` и `SDR → HDR` прошли, но переход `HDR → SDR` на паузе завершился `FAIL`: контроллер сообщил об SDR-источнике и `hdr_active = False` (`target-trc = auto`), однако опубликованная текстура и целевой FBO остались в состоянии `GL_RGBA16F` / `rec2100-pq` со старыми PQ-пикселями (в симуляции и отрицательном контроле `reference_white = 0.5807` вместо SDR `1.0`), поскольку `mpv` сгенерировал ранний update до применения `video-params`, а `_on_hdr_controller_change()` вызывал только `queue_draw()` старой текстуры без принудительного перерендера FBO.
  2. **Дефект 1B (перепутанная ориентация строк патчей в координатах `glReadPixels` в Run 7 Candidate v12)**:
     В прогоне Run 7 Candidate v12 (`2026-10-11`, `docs/GPU_NEXT_DEVELOPMENT.md` §15 / `docs/MODEL_USAGE_LOG.md`) `compute_patch_centers()` вычислял центры нижнего ряда патчей (`red_primary`, `green_primary`, `blue_primary`, `neutral_gradient`) в координатах сверху вниз (`y = 3 * height // 4`), тогда как `glReadPixels` в OpenGL использует начало координат в левом нижнем углу (`y = 0` внизу). В результате в нижнем ряду считывался верхний ряд (`black`, `near_black`, `reference_white`, `peak_white`), и `red_primary`/`green_primary`/`blue_primary` давали `(0.0, 0.0, 0.0)` / `(1.0, 1.0, 1.0)` вместо чистых праймариз.
  3. **Дефект 1C (захват кадра до `step 0` или маскировка дефектного первого опубликованного кадра последующим кадром в Run 7 Candidate v8–v12)**:
     В Candidate v8–v12 (`2026-10-10` — `2026-10-11`) до введения привязки к `_pending_render_frame_generation` и сохранения первого вердикта фазы в `_step_first_publication_captures` ранний кадр до `step 0` мог переиспользоваться как `initial_sdr`, либо ошибочный первый кадр перехода мог быть перезаписан следующим тиком рендера.
  4. **Дефект 1D (ложный `FAIL` или потеря `UNVERIFIED` при отсутствии getter'ов `Gdk.GLTexture` на первом опубликованном кадре, `2026-10-17`)**:
     Если в окружении `Gdk.GLTexture` не предоставлял getter формата/цветового состояния на первом опубликованном кадре, проверка либо подменяла фактический тег текстуры намерением виджета, либо теряла статус `UNVERIFIED` при последующем перерендере.
  5. **Синтетические отрицательные контроли целостности артефакта `fbo_pixel_measurements.json` (`verify_fbo_pixel_measurements_artifact`)** — для случаев подделки `raw_samples_hash`, инжекции `NaN`/`Infinity`, неполного набора из 8 патчей или ошибки `glGetError != GL_NO_ERROR`: *исторический дефект не задокументирован*; защита опирается на явно реализованные синтетические отрицательные контроли в `PausedTransitionPixelOracle.evaluate(substitute_negative_hdr_pixels=True)` и `verify_fbo_pixel_measurements_artifact()`.

#### 1.2. Проверяемые источники
- Коммит `beed9089a977769c0d2b09e78e9841a397f46cf1` (`feat(render): republish render target on paused HDR output change (ADR-0007)`).
- Коммит `a7c9a25663ed7047d5ee0f76f58c78b5b6f89102` (`test(pixel-pipeline): add development caller-owned FBO pixel capture and verification`).
- Коммит `6a88937df082193c93e11e5423a268e87c501850` (`fix(validation): enforce multi-backend publication matrix and fixture verification`).
- Коммит `91403edde152320fac42ce7a9dfe20c0b40e62db` (`docs(validation): publish bounded GNOME Run 7 adjudication`).
- Коммит `feb0d405297f031d4a49db6f7ddf5cd5e7c612d1` (`fix(hdr): remove async property readback and preserve first published sample`).
- Коммит `556da105a142ba8f014c961caeca011dd64fe5bf` (`docs: refine ADR-0007 synchronization details and packaged inspection findings`).
- Документы: `docs/adr/0007-paused-hdr-output-republication.md` (разделы `Context`, `Accepted decision (2026-10-17 amendment)`, `Alternatives, falsifier, and rollback`, `Subsequent bounded validation`), `docs/adr/0004-gate2-fbo-pixel-validation.md`, `docs/GPU_NEXT_DEVELOPMENT.md` (разделы по Gate 2W Run 6 и Run 7 v8–v14), `docs/MODEL_USAGE_LOG.md` (записи `2026-10-04`, `2026-10-10`, `2026-10-11`, `2026-10-17`).

#### 1.3. Фактические текущие файлы и функции
- `src/hdr_controller.py`: `CommittedOutputState`, `HdrController.apply_hdr_settings`, `HdrController.committed_output_state`, `HdrController.output_epoch`, `HdrController.is_publication_pending`, `HdrController.is_publication_suppressed`, `HdrController.state_gap_reason`.
- `src/video_widget.py`: `MpvVideoWidget._on_hdr_controller_change`, `MpvVideoWidget._schedule_render`, `MpvVideoWidget._render_pending_frame`, `should_render_frame`.
- `src/gpu_validation.py` *(целевой путь в `C4a`: `tools/validation/gpu_validation.py`)*: `CANONICAL_8_PATCHES`, `DEFAULT_SDR_REPEAT_TOLERANCE`, `DEFAULT_MIN_HDR_DISTINCTION`, `compute_patch_centers`, `read_fbo_patch_samples`, `compute_samples_hash`, `FboPixelCapture`, `PausedTransitionPixelOracle` (`record_capture`, `record_readback_error`, `get_capture`, `evaluate`), `serialize_fbo_pixel_measurements`, `verify_fbo_pixel_measurements_artifact`, `GpuValidationSession._on_fbo_pixel_capture`, `GpuValidationSession._on_fbo_pixel_readback_error`.
- `tests/test_hdr.py`: классы `TestPausedHdrOutputRepublication`, `TestPausedTransitionPixelOracle`.
- `tests/test_gpu_validation.py`: класс `TestPublicationFboPixelValidationPipeline`.

#### 1.4. Имена существующих регрессионных тестов и отрицательных контролей
- В `tests/test_hdr.py`:
  - `TestPausedHdrOutputRepublication.test_event_order_frame_rendered_then_state_change_sdr`
  - `TestPausedHdrOutputRepublication.test_event_order_reverse_direction_sdr_to_hdr`
  - `TestPausedHdrOutputRepublication.test_controlled_sequence_pause_sdr_hdr_sdr_full_cycle`
  - `TestPausedHdrOutputRepublication.test_zero_mpv_update_flags_forces_render_when_scheduled`
  - `TestPausedHdrOutputRepublication.test_partial_setter_failure_suppresses_publication`
  - `TestPausedHdrOutputRepublication.test_superseded_epoch_discards_render`
  - `TestPausedHdrOutputRepublication.test_repeated_notifications_do_not_cause_duplicate_render`
  - `TestPausedHdrOutputRepublication.test_shutdown_cancels_pending_and_suppresses_publication`
  - `TestPausedTransitionPixelOracle.test_paused_transition_defect_reproduction_and_resolution`
  - `TestPausedTransitionPixelOracle.test_negative_control_substituting_hdr_pixels_fails`
  - `TestPausedTransitionPixelOracle.test_first_published_transition_frame_cannot_be_hidden_by_recovery`
  - `TestPausedTransitionPixelOracle.test_video_widget_fbo_pixel_readback_and_oracle_callback`
  - `TestPausedTransitionPixelOracle.test_paused_single_frame_publication_and_oracle_capture`
  - `TestPausedTransitionPixelOracle.test_fbo_dropped_after_readback_is_never_sent_to_oracle`
  - `TestPausedTransitionPixelOracle.test_strict_canonical_8_patches_required`
  - `TestPausedTransitionPixelOracle.test_strict_channel_count_and_provenance`
  - `TestPausedTransitionPixelOracle.test_provenance_dimension_mismatch_fails`
  - `TestPausedTransitionPixelOracle.test_hdr_not_distinguishable_from_sdr_fails`
  - `TestPausedTransitionPixelOracle.test_missing_captures_return_unverified`
  - `TestPausedTransitionPixelOracle.test_calibrated_tolerance_boundary`
  - `TestPausedTransitionPixelOracle.test_oracle_rejects_invalid_phase`
  - `TestPausedTransitionPixelOracle.test_oracle_rejects_stale_or_non_advancing_epoch`
  - `TestPausedTransitionPixelOracle.test_oracle_rejects_fixture_hash_and_media_mismatch`
  - `TestPausedTransitionPixelOracle.test_oracle_rejects_empty_or_unconfirmed_target_properties`
  - `TestPausedTransitionPixelOracle.test_oracle_rejects_overwrite_attempt`
  - `TestPausedTransitionPixelOracle.test_oracle_unknown_provenance_returns_unverified`
- В `tests/test_gpu_validation.py` (`TestPublicationFboPixelValidationPipeline`):
  - `test_strict_rejection_on_negative_control_mismatch`
  - `test_transition_rejects_stale_hdr_surface_on_sdr`
  - `test_fbo_patch_centers_match_live_fixture_row_orientation`
  - `test_initial_paused_publication_requests_and_requires_fresh_frame`
  - `test_first_new_publication_mismatch_survives_later_recovery`
  - `test_first_published_unavailable_texture_evidence_survives_recovery_as_unverified`
  - `test_missing_actual_texture_getter_is_unavailable_not_intended_state`
  - `test_strict_rejection_when_samples_missing_or_incomplete_phases`
  - `test_strict_rejection_on_readback_error`
  - `test_fbo_pixel_measurements_serialization_complete`
  - `test_fbo_pixel_measurements_serialization_on_fail_preserves_partial_data`
  - `test_verify_fbo_pixel_measurements_artifact`
  - `test_verify_fbo_pixel_measurements_negative_cases`
  - `test_negative_session_write_report_fails_when_artifact_corrupted`

#### 1.5. Команда воспроизведения и предусловия
- **Предусловия**: Python 3, установленные PyGObject (`gi.repository.Gtk`, `Gdk`, `Adw`, `GLib`, `Gio`) и тестовое окружение репозитория; физический GPU или активная сессия Wayland для юнит-тестов **не требуются** (GL readback и текстуры мокируются детерминированно).
- **Команда воспроизведения (текущее расположение `C0`)**:
  ```bash
  python3 -m unittest \
    tests.test_hdr.TestPausedHdrOutputRepublication \
    tests.test_hdr.TestPausedTransitionPixelOracle \
    tests.test_gpu_validation.TestPublicationFboPixelValidationPipeline -v
  ```
- **После переноса в `C4`**: те же тест-классы запускаются через `meson test` / `python3 -m unittest` с импортом `PausedTransitionPixelOracle` и функций артефакта из `tools/validation/gpu_validation.py`.

#### 1.6. Что результат доказывает и чего НЕ доказывает
- **Доказывает**:
  - На уровне вызывающего FBO (`glReadPixels`) и `Gdk.GLTextureBuilder` смена эпохи `CommittedOutputState` на паузе принудительно перерисовывает FBO в целевом формате (`GL_RGBA8` или `GL_RGBA16F`) и публикует новую текстуру с согласованным `Gdk.ColorState`.
  - Пиксели фазы `return_sdr` возвращаются к SDR-эталону `initial_sdr` в пределах `<= 0.020`, а подстановка устаревших HDR-пикселей (`substitute_negative_hdr_pixels=True`) или сбой на первом опубликованном кадре детерминированно отклоняется (`FAIL`), даже если следующий кадр восстановился.
- **НЕ доказывает**:
  - Что GTK/GSK прикрепил `wp_image_description_v1` к поверхности Wayland (`wl_surface`) и выполнил `wl_surface.commit()` (это область парсера трассы L3p, раздел 2).
  - Что композитор Wayland или физический монитор реально отобразили HDR-сигнал без клиппинга или тон-маппинга (это область L4).

#### 1.7. Точки подключения в продукте (сохраняются по `D7` и `C4c`)
- `MpvVideoWidget._fbo_pixel_oracle_cb` (по умолчанию `None`) и `MpvVideoWidget._fbo_pixel_oracle_error_cb` (по умолчанию `None`) в `src/video_widget.py`:
  - Внутри `MpvVideoWidget._render_pending_frame()` чтение `read_fbo_patch_samples(width, height, is_float=use_float)` выполняется **только** когда `self._fbo_pixel_oracle_cb is not None`, сразу после `mpv_ctx.render()` и явного `glBindFramebuffer(GL_FRAMEBUFFER, slot.resource.fbo_id.value)`, пока целевой FBO слота привязан.
  - Сам callback `_fbo_pixel_oracle_cb(...)` вызывается **только после** успешного `builder.build(...)`, назначения `self.current_texture = texture`, `self.render_color_state = color_state`, инкремента `self.render_frame_generation += 1` и записи `self._committed_render_epoch = render_epoch`. Если кадр отброшен до публикации (смена эпохи во время рендера или таймаут `glClientWaitSync`), callback не вызывается.
  - Когда `_fbo_pixel_oracle_cb is None` (обычная работа плеера), на кадр выполняется **ноль** дополнительных вызовов `glReadPixels` и ноль аллокаций.

---

### 2. Парсер трассы протокола Wayland и его тесты (`L3p`)

#### 2.1. Защищаемый инвариант и конкретные дефекты
- **Защищаемый инвариант (`AGENTS.md` §8, `docs/TESTING.md` §L3p, `docs/adr/0006-bounded-validation-governance.md`)**:
  Клиентская публикация `Gdk.ColorState` в `Gdk.GLTextureBuilder` не доказывает отправку HDR-метаданных композитору. Передача HDR-описания поверхности композитору Wayland считается подтверждённой (`PASS` на уровне L3p) только тогда, когда в сырой трассе `WAYLAND_DEBUG` данного процесса в рамках одной жизни `wl_registry` зафиксирована полная без сбоев цепочка:
  1. биндинг `wp_color_manager_v1` и получение `wp_color_management_surface_v1` через `get_surface(wl_surface)` для поверхности, несущей видео (toplevel `xdg_toplevel` при композитном пути или связанный `wl_subsurface` при offload, с реальным `wl_surface.attach(wl_buffer != 0)`);
  2. создание описания изображения с HDR-трансфером (параметрический PQ `tf_named(11)` + `primaries_named(3)` BT.2020 или поддерживаемое представление по `AGENTS.md` §8) и получение события `wp_image_description_v1.ready(identity)` / `ready2(hi, lo)` **до** вызова `set_image_description`;
  3. вызов `wp_color_management_surface_v1.set_image_description(desc, render_intent)` без промежуточного `unset_image_description` или `wl_display.delete_id(desc)` до коммита;
  4. последующий `wl_surface.commit()` на той же самой поверхности и в той же эпохе `wl_registry`, при отсутствии `wp_image_description_v1.failed`, `wl_display.error` и разрыва соединения.
- **Конкретные исторические дефекты и отрицательные контроли**:
  1. **Дефект 2A (отсутствие `GDK_DEBUG=color-mgmt` и несовместимость capability-гейта GTK с KWin 6.7.4 без `TF srgb`, `2026-08-23`)**:
     Без `GDK_DEBUG=color-mgmt` в GTK 4.16–4.22 (и в KWin 6.7.4, который в `wp_color_manager_v1.supported_tf_named` объявлял `gamma22`, но не `srgb`), GTK уничтожал `wp_color_manager_v1` при инициализации (`gdk_wayland_color_New` → `destroy`), никогда не вызывал `get_surface` / `set_image_description`, и композитор интерпретировал 16-битный буфер Rec.2100 PQ как неразмеченный sRGB (выцветшая картинка). В Run 1 (`wayland-trace-cW5kFA`) и Run 2 (`wayland-trace-MgsoJA`, `2026-09-24`) на GTK 4.22.4 выяснилось также, что даже при успешном `wp_color_manager_v1.done()` GTK создавал sRGB-описание, но не вызывал `get_surface` для окна приложения.
  2. **Дефект 2B (несколько `wl_registry` в одном процессе: приватные probe-соединения `wayland_cm_probe` / `wayland_output_hdr` и переиспользование object ID между реестрами, `2026-09-24` и `2026-10-04`)**:
     В реальном процессе CineHDR создаются отдельные `wl_registry` (основной реестр GDK `wl_registry#2`, probe-реестры `wayland_cm_probe` и `wayland_output_hdr`). В ранней версии парсера глобальный поиск `wp_color_manager_v1` либо выбирал незавершённый приватный probe-менеджер вместо полного менеджера GDK, либо при переиспользовании object ID (`wl_registry#2`, `wp_image_description_v1#31`, `wl_callback#31.done()` → `wl_display#1.delete_id(31)`) между разными эпохами реестра ошибочно склеивал события от чужого реестра/менеджера с основным окном (выявлено ревью Astra high `2026-10-04` и исправлено в коммите `e41fbeb1dad3b1b463c40030efba587f2341bfb7`).
  3. **Дефект 2C (привязка многофазной смены `SDR → HDR → SDR → HDR` к toplevel без проверки subsurface-цепочки или смешение HLG `tf=13` с PQ `tf=11`, `2026-10-04`)**:
     При наличии offload видео могло выводиться на `wl_subsurface`, тогда как `xdg_toplevel` не получал `set_image_description`, либо наоборот — описание проверялось на чужой поверхности без привязанных буферов (`wl_surface.attach`).
  4. **Синтетические отрицательные контроли протокола (`unset_image_description` перед `commit`, `ready` после `set_image_description`, `wp_image_description_v1.failed`, `wl_display.error`, разрыв сокета, 64-битный `ready2`)**:
     Для случаев `unset_image_description` между `set_image_description` и `wl_surface.commit()` и `wp_image_description_v1.failed` в живой трассе GNOME/KWin *исторический дефект не задокументирован* (поскольку GTK 4.22.4 в живых прогонах останавливался раньше — до `get_surface`/`set_image_description`); эти ветки защищены синтетическими отрицательными контролями в `tests/test_wayland_color_trace.py`.

#### 2.2. Проверяемые источники
- Коммит `074c38cf577183e57c36f64b9e40c78bcfd870a9` (`fix: bypass KWin/GTK color management deadlock via linear extended and graceful SDR fallback`).
- Коммит `d6f8a2b8414ebcaebb0baa5509767d71873d9b2c` (`fix: model GTK Wayland color management policy and restore Rec.2100 PQ consistency`).
- Коммит `ee2d333c2645e943b4a131e0774a5ed455b6b700` (`fix: close color-management gate bypass, register main option, and add realize regression test`).
- Коммит `e41fbeb1dad3b1b463c40030efba587f2341bfb7` (`fix(validation): track Wayland registry lifetimes in trace parser`).
- Коммит `9010e5a4a473aa90ae35ef5bb22b27dacf373d6e` (`docs(validation): record GNOME trace evidence and Gate 2W hold`).
- Документы: `docs/GPU_NEXT_DEVELOPMENT.md` (разделы по KWin/GTK `2026-08-23`, Attempt 1–4 `2026-09-24`–`2026-10-03`, GNOME Run 4–6 `2026-10-04`), `docs/MODEL_USAGE_LOG.md` (записи `2026-09-24`, `2026-09-25`, `2026-10-04`), `docs/adr/0006-bounded-validation-governance.md`.

#### 2.3. Фактические текущие файлы и функции
- `tests/parse_wayland_color_trace.py` *(целевой путь в `C2a`: `tools/validation/parse_wayland_color_trace.py`)*:
  - `validate_trace(lines)`
  - `validate_composite_window_trace(lines, expected_app_id=...)`
  - `validate_multiphase_trace(lines, expected_app_id=...)`
  - `create_evidence_report(...)`
  - `main()` (CLI с режимами `--composite-window`, `--multiphase`, `--json-out`, `--require-commit-after-set`).
- `src/gtk_cm_policy.py`: `CmCaps`, `gtk_cm_opted_in`, `gtk_color_managed`, `gtk_can_tag_hdr`, `preferred_gsk_renderer`.
- `tests/test_wayland_color_trace.py` *(целевой путь в `C2a`: `tools/validation/test_wayland_color_trace.py`)*: классы `TestGtkCmPolicy`, `WaylandColorTraceTests`.

#### 2.4. Имена существующих регрессионных тестов и отрицательных контролей
- В `tests/test_wayland_color_trace.py`:
  - `TestGtkCmPolicy.test_gtk_color_managed_requires_opt_in`
  - `TestGtkCmPolicy.test_gtk_color_managed_kwin_missing_srgb`
  - `TestGtkCmPolicy.test_regression_kwin_caps_after_invalidate_blocks_hdr`
  - `WaylandColorTraceTests.test_valid_trace_passes_strict_chain`
  - `WaylandColorTraceTests.test_early_bind_survives_later_registry_id_reuse`
  - `WaylandColorTraceTests.test_valid_trace_selects_a_complete_manager_not_private_probe_traffic`
  - `WaylandColorTraceTests.test_reused_registry_or_chain_object_id_is_ambiguous`
  - `WaylandColorTraceTests.test_ready_after_set_and_wrong_surface_commit_are_rejected`
  - `WaylandColorTraceTests.test_failed_description_display_error_and_protocol_disconnect_are_rejected`
  - `WaylandColorTraceTests.test_ready2_is_accepted_and_records_the_64_bit_identity`
  - `WaylandColorTraceTests.test_composite_window_passes_strict_chain`
  - `WaylandColorTraceTests.test_composite_window_rejects_wrong_surface`
  - `WaylandColorTraceTests.test_composite_window_rejects_wrong_order`
  - `WaylandColorTraceTests.test_composite_window_rejects_unset_image_description_before_commit`
  - `WaylandColorTraceTests.test_composite_window_rejects_id_reuse_without_delete_id`
  - `WaylandColorTraceTests.test_composite_window_rejects_delete_id_after_recreation`
  - `WaylandColorTraceTests.test_composite_window_rejects_id_reuse_even_with_delete_id`
  - `WaylandColorTraceTests.test_composite_window_rejects_id_reuse_with_foreign_callback_delete_id`
  - `WaylandColorTraceTests.test_composite_window_rejects_ambiguous_delete_id_with_alien_destruction`
  - `WaylandColorTraceTests.test_composite_window_rejects_foreign_registry_surface`
  - `WaylandColorTraceTests.test_composite_window_rejects_foreign_manager_description`
  - `WaylandColorTraceTests.test_multiphase_trace_four_phases_on_subsurface_with_buffers_passes`
  - `WaylandColorTraceTests.test_multiphase_trace_four_phases_on_toplevel_without_subsurface_chain_returns_unknown`
  - `WaylandColorTraceTests.test_multiphase_trace_hlg_transfer_rejected_for_pq`

#### 2.5. Команда воспроизведения и предусловия
- **Предусловия**: только стандартный Python 3 (без GPU и без запущенного композитора Wayland).
- **Команда воспроизведения (текущее расположение `C0`)**:
  ```bash
  python3 -m unittest tests.test_wayland_color_trace -v
  ```
- **Команда запуска CLI парсера на сохранённом логе трассы**:
  ```bash
  python3 tests/parse_wayland_color_trace.py --composite-window /path/to/wayland-client.raw.log
  ```
  *(после `C2a`: `python3 tools/validation/parse_wayland_color_trace.py ...` и `python3 -m unittest tools/validation/test_wayland_color_trace.py -v`)*.

#### 2.6. Что результат доказывает и чего НЕ доказывает
- **Доказывает**:
  - Наличие или отсутствие в клиентском протокольном логе `WAYLAND_DEBUG=client` валидной последовательности `wp_color_manager_v1` → `get_surface` → `create_parametric_creator` (HDR описание) → `ready`/`ready2` → `set_image_description` → `wl_surface.commit` на поверхности окна/subsurface с видео без протокольных ошибок и коллизий object ID.
- **НЕ доказывает**:
  - Какие именно пиксели были записаны в FBO до создания текстуры (это проверяет пиксельный оракул FBO, раздел 1).
  - Как композитор тон-маппит эту поверхность при композитинге и какова физическая яркость панели (уровень L4).

#### 2.7. Точки подключения в продукте
- В горячем рендер-пути продукта точек подключения **нет**: трасса собирается внешне через стандартную переменную окружения libwayland `WAYLAND_DEBUG=client` (или `WAYLAND_DEBUG=1`) и `GDK_DEBUG=color-mgmt` из stderr клиентского процесса.
- При запуске `src/main.py` до `gi.require_version("Gtk", "4.0")` функция `apply_color_mgmt_opt_in_from_argv()` включает `GDK_DEBUG=color-mgmt`, если передан флаг `--experimental-color-mgmt` или переменная `CINEHDR_COLOR_MGMT=1`.

---

### 3. Паузовый сценарий `SDR → HDR → SDR`, первый опубликованный кадр и чистое завершение (`publication`)

#### 3.1. Защищаемый инвариант и конкретные дефекты
- **Защищаемый инвариант (`AGENTS.md` §4.1, §4.4, `docs/adr/0001-gpu-next-render-api.md`, `docs/adr/0007-paused-hdr-output-republication.md`)**:
  1. Изолированный процесс валидации (`Gio.ApplicationFlags.NON_UNIQUE`) запускается в режиме `publication` строго на паузе (`pause=True`), без восстановления пользовательской сессии (`restore_last_playlist` и `quit_watch_later` отключены), с отключённым автоподхватом соседей (`autocreate-playlist=no`), безопасным декодером (`hwdec=auto-copy`) и точным списком из двух проверенных по SHA-256 фикстур (`playlist-pos=0` — SDR BT.709, `playlist-pos=1` — HDR10 PQ).
  2. Сценарий проходит фазы `initial_sdr` → `hdr` → `return_sdr` → `repeat_hdr` в одном контексте рендера (`render_context_generation == 1`) без пересоздания `MpvRenderContext`. На каждой фазе доказывается публикация **нового** кадра (`render_frame_generation` строго возрастает), совпадение размеров `Gdk.GLTexture` с ожидаемым дробным масштабом Wayland (`round(widget_w * scale)` × `round(widget_h * scale)`), соответствие формата/цветового состояния текстуры и прохождение пиксельного оракула FBO.
  3. При закрытии окна (`CineWindow.do_close_request`) контекст рендера libmpv освобождается строго **до** остановки ядра mpv (`_video_area.shutdown_render_context()` → `mpv_render_context_free()` → `mpv.quit()` / `wait_for_shutdown()`), а внешний супервизор `run_wayland_publication_validation.sh` никогда не убивает пользовательские экземпляры CineHDR и не затирает финальный статус очистки.
- **Конкретные исторические дефекты и отрицательные контроли**:
  1. **Дефект 3A (снятие паузы в `CineApplication.do_open` при открытии фикстуры в режиме `publication`, `2026-10-04`)**:
     Стандартный `CineApplication.do_open()` после `loadfile` вызывал `player.pause = False`, из-за чего паузовый контракт сценария `publication` нарушался сразу при старте, а смена файлов происходила во время воспроизведения вместо детерминированного паузового кадра. Исправлено функцией `_uses_publication_fixture_contract(mode)` в `src/main.py` и `prepare_validation_player()` в `src/gpu_validation.py`.
  2. **Дефект 3B (загрязнение валидации пользовательским плейлистом `autocreate-playlist=filter`, `watch_later` и `hwdec=vaapi`, `2026-08-22` — `2026-08-23`)**:
     В ранних прогонах Gate 1/Gate 2 пользовательские настройки `autocreate-playlist=filter` и `restore-last-playlist` подгружали посторонние файлы из каталога пользователя, а `hwdec=auto` выбирал прямой `vaapi` DMABUF interop, несовместимый с экспериментальным `opengl-next` на гибридной графике (`vaapi-copy` устранил отказ).
  3. **Дефект 3C (ошибка формулы размеров текстуры при дробном масштабировании Wayland `1.25` и формата версии `libplacebo` в Run 4, `2026-10-04`)**:
     В GNOME Run 4 при дробном масштабе монитора `1.25` (`_cached_scale = 1.25`, виджет `1120×630`) `MpvVideoWidget` создавал текстуру `1400×788`, тогда как валидатор умножал размер виджета на целочисленный `get_scale_factor() == 2` (`2240×1260`) и ложно валил проверку размеров. Одновременно строка версии `libplacebo` `"v7.360.1 (v7.360.1)"` отклонялась парсером версий, ожидавшим только `"v7.360.1"`.
  4. **Дефект 3D (блокировка HDR в режиме `auto` в изолированном прогоне Run 5 без `CINEHDR_FORCE_SURFACE_SUBMISSION=1`, `2026-10-04`)**:
     Поскольку в продукте режим `auto` был закрыт гейтом `is_surface_submission_proven() == False`, прогон Run 5 без изолированного переопределения `CINEHDR_FORCE_SURFACE_SUBMISSION=1` оставался в SDR на шаге 1 (`hdr_active=False`, `GL_RGBA8`/`srgb`).
  5. **Дефект 3E (подмена `/app/lib/libmpv.so` хостовым путём и проверка Flatpak-контейнера, `2026-10-03`)**:
     При проверке изолированного Flatpak-рантайма требовалось гарантировать, что `libmpv.so.2.5.0` загружен именно из `/app/lib/` внутри контейнера с read-only `/app` в `/proc/self/mountinfo` (`is_verified_flatpak_container`), а не из хостового префикса.
  6. **Дефект 3F (перезапись `cleanup_status.json` в `EXIT`-ловушке и ложный возврат `0` при пропаже `.flatpak/<id>/info` в `run_wayland_publication_validation.sh`, `2026-10-04`)**:
     При штатном завершении `evaluate_session_results` записывал `cleanup_status.json`, но срабатывавший при выходе из скрипта `trap cleanup_handler EXIT` перезаписывал его; кроме того, если каталог `~/.var/app/.../.flatpak/<id>/info` исчезал раньше, чем процесс пропадал из `flatpak ps`, `cleanup_instance` мог ошибочно вернуть успех или при неверной фильтрации затронуть чужой экземпляр.

#### 3.2. Проверяемые источники
- Коммит `51890eb60ec5646d090f65e067b81e47a2ec629c` (`gpu-next: bound validation and harden vendor smoke`).
- Коммит `24c878ebd32956243120c757b92ad3587b2e7189` (`test(gpu): enforce strict Flatpak validation reporting and record anonymized evidence`).
- Коммит `9010e5a4a473aa90ae35ef5bb22b27dacf373d6e` (`docs(validation): record GNOME trace evidence and Gate 2W hold`).
- Коммит `beed9089a977769c0d2b09e78e9841a397f46cf1` (`feat(render): republish render target on paused HDR output change (ADR-0007)`).
- Коммит `6a88937df082193c93e11e5423a268e87c501850` (`fix(validation): enforce multi-backend publication matrix and fixture verification`).
- Коммит `feb0d405297f031d4a49db6f7ddf5cd5e7c612d1` (`fix(hdr): remove async property readback and preserve first published sample`).
- Документы: `docs/GPU_NEXT_DEVELOPMENT.md` (разделы по Gate 1/Gate 2 и Gate 2W Run 4, Run 5, Run 6, Run 7), `docs/MODEL_USAGE_LOG.md` (записи `2026-10-03`, `2026-10-04`, `2026-10-10`, `2026-10-11`), `docs/adr/0001-gpu-next-render-api.md`, `docs/adr/0006-bounded-validation-governance.md`, `docs/adr/0007-paused-hdr-output-republication.md`.

#### 3.3. Фактические текущие файлы и функции
- `src/gpu_validation.py` *(целевой путь в `C4a`: `tools/validation/gpu_validation.py`)*:
  - `ValidationConfig`, `validation_config_from_env`
  - `prepare_validation_player`
  - `validation_steps` (ветка `mode == "publication"`)
  - `expected_scaled_texture_dimensions`, `publication_texture_evidence`
  - `publication_runtime_errors`, `is_verified_flatpak_container`
  - `GpuValidationSession` (`start`, `_wait_until_ready`, `_begin_step`, `_tick`, `_sample_publication_state`, `_finish`, `_write_report`)
  - `evaluate_publication_validation`.
- `run_wayland_publication_validation.sh` *(целевой путь в `C3`: `tools/validation/run_wayland_publication_validation.sh`)*:
  - `check_flatpak_preflight`, `verify_fixture_contract`, `cleanup_instance`, `cleanup_handler`, `evaluate_session_results`, `run_backend`, `write_gate2w_summary`.
- `src/main.py`: `_uses_publication_fixture_contract`, `CineApplication.__init__` (`Gio.ApplicationFlags.NON_UNIQUE` при `CINEHDR_GPU_VALIDATION`), `CineApplication.do_open` (вызов `prepare_validation_player`, сохранение `pause=True`, запуск `GpuValidationSession`).
- `src/window.py`: `CineWindow.__init__` (`self._is_gpu_validation`), `CineWindow.do_close_request` (отключение `quit_watch_later` при валидации, вызов `self._video_area.shutdown_render_context()` до `self.mpv.quit()` и `self.mpv.wait_for_shutdown()`).
- `src/video_widget.py`: `MpvVideoWidget.shutdown_render_context`, `MpvVideoWidget._on_unrealize`.

#### 3.4. Имена существующих регрессионных тестов и отрицательных контролей
- В `tests/test_gpu_validation.py`:
  - `TestGpuValidation.test_do_open_preserves_publication_pause_contract`
  - `TestGpuValidation.test_wait_until_ready_rejects_unpaused_initial_playback`
  - `TestGpuValidation.test_wait_until_ready_rejects_playlist_pos_not_zero`
  - `TestGpuValidation.test_validation_player_does_not_restore_or_save_watch_later`
  - `TestGpuValidation.test_publication_player_is_isolated_before_loadfile`
  - `TestGpuValidation.test_expected_scaled_texture_dimensions_formula`
  - `TestGpuValidation.test_publication_texture_evidence_fractional_scale_matching_run4`
  - `TestGpuValidation.test_publication_libplacebo_version_strict_formats`
  - `TestGpuValidation.test_flatpak_candidate_libmpv_path_and_foreign_substitution`
  - `TestGpuValidation.test_is_verified_flatpak_container_facts`
  - `TestGpuValidation.test_publication_requires_exact_isolated_surface_override`
  - `TestGpuValidation.test_report_without_override_or_scope_is_rejected`
  - `TestGpuValidation.test_publication_validation_passes_with_complete_four_state_evidence`
  - `TestGpuValidation.test_publication_rejects_non_advancing_frame_generation`
  - `TestGpuValidation.test_publication_rejects_context_recreation`
  - `TestGpuValidation.test_publication_rejects_end_file_error_and_unexpected_eof`
- В `tests/test_wayland_publication_scenario.py` (`WaylandPublicationScenarioTests`):
  - `test_preflight_blocks_user_cinehdr_instance`
  - `test_preflight_Rejects_Missing_Flatpak_And_Monitors`
  - `test_preflight_rejects_same_sdr_and_hdr_fixture_hashes`
  - `test_preflight_rejects_swapped_sdr_and_hdr_fixtures`
  - `test_cleanup_instance_refuses_user_instance`
  - `test_cleanup_instance_refuses_metadata_mismatch`
  - `test_cleanup_instance_fails_closed_when_info_missing_and_instance_still_running`
  - `test_cleanup_instance_fails_closed_when_info_missing_and_ps_fails`
  - `test_cleanup_handler_does_not_overwrite_existing_cleanup_status`
  - `test_evaluate_session_results_passes_only_when_both_publication_and_trace_pass`
  - `test_evaluate_session_results_fails_when_trace_is_unknown_on_gtk_4_22_4`
  - `test_multi_backend_orchestration_passes_both_legacy_and_gpu_next`
  - `test_multi_backend_orchestration_stops_after_legacy_failure`

#### 3.5. Команда воспроизведения и предусловия
- **Предусловия для модульных/сценарных тестов (без GPU и без живой сессии)**: Python 3, `bash`, стандартные утилиты (`sha256sum`, `jq`/`python3`), установленные модули проекта.
- **Команда воспроизведения тестов (текущее расположение `C0`)**:
  ```bash
  python3 -m unittest \
    tests.test_gpu_validation \
    tests.test_wayland_publication_scenario -v
  ```
- **Команда живого запуска сценария на машине владельца (`O`, требует активной Wayland-сессии, установленного Flatpak `io.github.rusmikev.CineHDR`, отсутствия запущенных пользовательских экземпляров CineHDR и двух фикстур SDR/HDR)**:
  ```bash
  bash run_wayland_publication_validation.sh \
    --sdr-file /path/to/sdr_bt709.mp4 \
    --hdr-file /path/to/hdr10_pq.mp4 \
    --out-dir /path/to/output_dir
  ```
  *(после `C3`: `bash tools/validation/run_wayland_publication_validation.sh ...`)*.

#### 3.6. Что результат доказывает и чего НЕ доказывает
- **Доказывает**:
  - Что в изолированном процессе на паузе обе фикстуры (`SDR` и `HDR`) последовательно проходят цикл `SDR → HDR → SDR → HDR` с публикацией первого нового кадра на каждом шаге, корректными параметрами `CommittedOutputState`, `Gdk.GLTexture`, пикселями FBO и штатным освобождением `mpv_render_context_free()` до остановки ядра `mpv`.
  - В связке с `parse_wayland_color_trace.py` (`evaluate_session_results`) — также статус протокольной отправки `wp_color_management_v1` (`PASS` / `UNKNOWN` / `FAIL`).
- **НЕ доказывает**:
  - Отсутствие пропусков кадров при длительном воспроизведении (60 fps soak) или отсутствие деградации на других GPU/композиторах.
  - Реальный физический HDR-вывод монитора (тон-маппинг и яркость на экране проверяются только визуальным/фотометрическим оракулом L4).

#### 3.7. Точки подключения в продукте
- `src/main.py`:
  - В `CineApplication.__init__`: чтение `os.environ.get("CINEHDR_GPU_VALIDATION")` для добавления `Gio.ApplicationFlags.NON_UNIQUE` (чтобы тестовый экземпляр не подключался к пользовательскому по D-Bus).
  - В `CineApplication.do_open`: при активном `CINEHDR_GPU_VALIDATION` вызов `prepare_validation_player(win.player, config, files)` до `loadfile`, сохранение `pause=True` для `publication` и планирование `GpuValidationSession(app, win, config, files).start` через `GLib.idle_add`.
  - *(По `C4d`: при `CINEHDR_GPU_VALIDATION` `main.py` будет импортировать `cinehdr.validation`, а при отсутствии модуля — завершаться с явной ошибкой в лог, не молча).*
- `src/window.py`:
  - `self._is_gpu_validation = bool(os.environ.get("CINEHDR_GPU_VALIDATION", "").strip())` в `CineWindow.__init__` (отключает `restore_last_playlist` при старте и `quit_watch_later()` в `do_close_request`).
  - Порядок закрытия в `CineWindow.do_close_request`: `self._video_area.shutdown_render_context()` вызывается до `self.mpv.quit()` / `self.mpv.wait_for_shutdown()`.

---

### 4. Автоматическое сохранение диагностического снимка первого опубликованного кадра (`DiagnosticsExporter`)

#### 4.1. Защищаемый инвариант и конкретные дефекты
- **Защищаемый инвариант (`AGENTS.md` §10, `GEMINI.md` §2.2–2.3, решение `D7`)**:
  1. Автоматический экспорт диагностического отчёта (`video_output_diagnostics.txt` и `video_output_diagnostics.json`) использует **единый** продуктовый сборщик фактов `collect_video_output_facts()` и генератор текста `build_video_output_report()` из `src/diagnostics_report.py` (гарантия побайтового совпадения логики с кнопкой `Copy Report` в диалоге `Video Output Diagnostics` на фиксированном снимке).
  2. Снимок со статусом `evidence_status = "CONFIRMED"` (`first_frame_confirmed = True`) записывается **только** после того, как реально декодированный медиа-кадр (`media_frame_evidence`: `source_width > 0`, `source_height > 0`, `time_pos is not None`, непустой `path_token`) опубликован в `self.current_texture` и `self.render_frame_generation > 0`.
  3. Внутри GL-колбэка `_render_pending_frame()` не выполняется **никакого** дискового ввода-вывода: уведомление окна откладывается через `idle_add_once`, а перед записью на главном цикле GTK проверяется неизменность `(render_context_generation, _media_load_generation, path_token)`. Если приложение закрылось до первого медиа-кадра, в `do_close_request` записывается снимок со статусом `"INCOMPLETE"` (`first_frame_confirmed = False`).
- **Конкретные исторические дефекты и отрицательные контроли**:
  1. **Дефект 4A (отсутствие синхронного клиентского диагностического снимка в Run 2 `wayland-trace-MgsoJA`, `2026-09-24`)**:
     В прогоне Run 2 (`2026-09-24`) была собрана трасса `WAYLAND_DEBUG`, но не существовало механизма автоматического сохранения `Video Output Diagnostics` из того же самого процесса в момент публикации первого кадра, из-за чего поля `Active API` и `Session status` остались `Unknown`, а прогон не мог быть принят как доказательство.
  2. **Дефект 4B (пустой начальный GL redraw до декодирования медиа-кадра и гонки поколений `load_generation` / `context_generation` при перезагрузке файла, `2026-09-25`)**:
     При подготовке Attempt 3 (`2026-09-25`, ревью Sol high / Astra high в `docs/MODEL_USAGE_LOG.md`) были выявлены и закрыты регрессионными тестами дефекты:
     - первый вызов `_render_pending_frame()` может произойти до декодирования первого видеокадра (пустой GL redraw без `video-params`); он не должен помечать `_first_media_frame_exported = True` или экспортировать `CONFIRMED`;
     - отложенный через `idle_add_once` колбэк от старого файла или старого `render_context_generation` при быстром переключении/перезагрузке того же файла (`start-file` увеличивает `_media_load_generation`) мог перезаписать состояние нового поколения или отменить уведомление нового поколения;
     - если `context_generation` или `load_generation` изменились во время сбора `collect_video_output_facts()`, снимок не имеет права получить статус `CONFIRMED`.
  3. **Дефект 4C (преждевременная остановка сессии сразу после появления `video_output_diagnostics.json` до `wl_surface.commit()` и маскировка ненулевого кода возврата процесса, `2026-09-25`)**:
     В супервизоре Attempt 3 обнаружение валидного `video_output_diagnostics.json` до появления `wl_surface.commit()` в `wayland-client.raw.log` приводило к раннему завершению процесса до протокольного коммита, либо поздний `wl_display.error` / ненулевой exit code дочернего процесса после записи JSON не отзывал предварительный успех.

#### 4.2. Проверяемые источники
- Коммит `a25b2a38af8c61a7c36a990c5e92d4989bf83550` (`feat(diagnostics): add autonomous diagnostic report export and CLI option`).
- Коммит `9e2475aabf702b95d090b4c29b8802a9462eac87` (`fix(diagnostics): track Dolby Vision RPU warnings and enforce snapshot consistency`).
- Коммит `4831fefb4f7fe8df74462286b233b39eb579cae3` (`fix(diagnostics): unify decoder snapshot, chain validation logging, and clarify provenance`).
- Коммит `528242c7b647594fde792d2558101b3ad144f609` (`test: make diagnostics export write error test independent of root DAC permissions`).
- Документы: `docs/GPU_NEXT_DEVELOPMENT.md` (разделы по Run 2 `wayland-trace-MgsoJA` и подготовке Attempt 3 `2026-09-25`), `docs/MODEL_USAGE_LOG.md` (записи `2026-09-24`, `2026-09-25`), `docs/adr/0006-bounded-validation-governance.md`.

#### 4.3. Фактические текущие файлы и функции
- `src/diagnostics_report.py`:
  - *Остаются в `src/diagnostics_report.py` по `D7` и `C4b`*: `DiagnosticSnapshot`, `collect_video_output_facts`, `build_video_output_report`, `generate_video_output_report`, `read_flatpak_info`, `format_dovi_rpu_warnings`, `get_dovi_rpu_warning_state`.
  - *Переносится в `tools/validation/diagnostics_export.py` в `C4b`*: класс `DiagnosticsExporter` (`from_env`, `check_identity`, `export_snapshot`, `on_first_media_frame`, `on_first_frame`, `on_shutdown`).
- `src/render_backend.py`: `MediaFrameEvidence`, `media_frame_evidence`.
- `src/video_widget.py`: проверка `rendered_media_frame = media_frame_evidence(self.mpv)` и планирование `idle_add_once(window.on_first_media_frame_published, rendered_media_frame, ctx_gen, load_gen)` в `MpvVideoWidget._render_pending_frame`.
- `src/window.py`: `self._diagnostics_exporter`, `self._media_load_generation`, `CineWindow.on_first_media_frame_published`, `CineWindow.do_close_request`.
- `tests/test_diagnostics_export.py`: класс `DiagnosticsExportTests`.
- `tests/test_attempt3_regressions.py`: класс `Attempt3RegressionsTests`.

#### 4.4. Имена существующих регрессионных тестов и отрицательных контролей
- В `tests/test_diagnostics_export.py` (`DiagnosticsExportTests`):
  - `test_exporter_disabled_by_default`
  - `test_exporter_first_frame_confirmed_produces_confirmed_status`
  - `test_exporter_missing_first_frame_produces_incomplete_status`
  - `test_exporter_identity_mismatch_marks_failed`
  - `test_parity_between_headless_export_and_copy_report`
  - `test_frame_sequence_empty_redraw_then_media_frame_then_repeat`
  - `test_no_disk_io_inside_render_callback`
  - `test_regression_deferred_export_invalidation`
  - `test_regression_load_change_discards_old_and_exports_new`
  - `test_regression_late_old_callback_does_not_cancel_new_notification`
  - `test_stale_callback_clobbers_new_generation_state`
  - `test_same_file_reload_race_causes_premature_export`
  - `test_generation_change_during_snapshot_collection_rejects_confirmed`
  - `test_call_boundary_generation_advance_rejects_and_allows_next_generation_export`
  - `test_exporter_write_error_returns_false_and_allows_retry`
- В `tests/test_attempt3_regressions.py` (`Attempt3RegressionsTests`):
  - `test_regression1_valid_json_before_commit_no_early_stop`
  - `test_regression2_incomplete_json_stops_immediately_as_failed_before_timeout`
  - `test_regression3_invalid_corrupt_json_stops_immediately_as_failed`
  - `test_regression4_timeout_without_json_produces_timeout_and_exit_124`
  - `test_regression5_late_protocol_error_revokes_preliminary_success`
  - `test_regression9_child_process_exit_code_preserved_and_not_masked`
  - `test_regression11_zero_remaining_budget_never_calls_readiness_with_zero_timeout`

#### 4.5. Команда воспроизведения и предусловия
- **Предусловия**: Python 3, `bash`, установленные модули проекта; GPU и активный композитор Wayland не требуются.
- **Команда воспроизведения (текущее расположение `C0`)**:
  ```bash
  python3 -m unittest \
    tests.test_diagnostics_export \
    tests.test_attempt3_regressions -v
  ```
- **Активация автоэкспорта при запуске плеера**:
  Переменная окружения `CINEHDR_EXPORT_DIAGNOSTICS=/path/to/dir` или CLI-флаг `--export-diagnostics=/path/to/dir` (опционально с проверкой идентичности `CINEHDR_EXPECT_FLATPAK_INSTANCE`, `CINEHDR_EXPECT_APP_COMMIT`, `CINEHDR_EXPECT_RUNTIME_COMMIT`, `CINEHDR_VALIDATION_SESSION_ID`).

#### 4.6. Что результат доказывает и чего НЕ доказывает
- **Доказывает**:
  - Что в конкретной сессии (с подтверждённым PID, `flatpak_instance_id`, `app_commit`, `runtime_commit`) первый декодированный медиа-кадр действительно дошёл до публикации в `Gdk.GLTexture`, и фиксирует состояние `Active API`, `render_target_format`, `gdk_color_state`, декодера и предупреждений RPU в этот момент.
- **НЕ доказывает**:
  - Содержимое пикселей внутри FBO (проверяется пиксельным оракулом FBO, раздел 1).
  - Отправку `wp_image_description_v1` композитору по протоколу Wayland (проверяется `parse_wayland_color_trace.py`, раздел 2) и физический вывод на экран (L4).

#### 4.7. Точки подключения в продукте (сохраняются по `D7` и `C4c`)
- `CineWindow.__init__` (`src/window.py`):
  - `self._media_load_generation = 0` (инкрементируется в обработчике события mpv `start-file`) и `self._diagnostics_exporter`.
- `MpvVideoWidget._render_pending_frame` (`src/video_widget.py`):
  - После публикации `self.current_texture = texture` и `self.render_frame_generation += 1`, только если `not self._first_media_frame_exported` и `rendered_media_frame is not None`, вызывает `idle_add_once(window.on_first_media_frame_published, rendered_media_frame, ctx_gen, load_gen)`. После срабатывания на первом медиа-кадре флаг `_first_media_frame_exported` становится `True`, и в последующих кадрах проверка не выполняет работы.
- `CineWindow.on_first_media_frame_published` и `CineWindow.do_close_request` (`src/window.py`):
  - Вызывают `self._diagnostics_exporter.on_first_media_frame(...)` на главном цикле GTK и `self._diagnostics_exporter.on_shutdown(self)` перед `shutdown_render_context()`. При `self._diagnostics_exporter is None` (режим по умолчанию) обе точки являются немедленным no-op.

---

## Справочник по прочим режимам и скриптам вне ядра `D7` (входные данные для `C2` / `C3` / `C4` / `C16`)

По правилу `docs/PLAN.md` (`C0`, `C2`, `C3`, `C4`, `C16`) механизмы вне четырёх обязательных частей `D7` оцениваются индивидуально. Ниже зафиксировано наличие или отсутствие задокументированных исторических дефектов и синтетических отрицательных контролей для каждого из них:

| Механизм / файл | Защищаемый инвариант | Исторический дефект в документации / git | Синтетический отрицательный контроль / тест |
|---|---|---|---|
| Режим `lifecycle` в `src/gpu_validation.py` (`_capture_video_selection_for_context_restore`, `_restore_video_after_context_recreate` в `src/video_widget.py`) | Восстановление видеоцепочки (`vid=no` → `vid=auto`/track ID) после `mpv_render_context_free()`, который в `opengl-next` вызывает `kill_video_async()` и сбрасывает `vid` | **Задокументирован**: в `docs/adr/0001-gpu-next-render-api.md` (§`GL context recreation`) и `docs/GPU_NEXT_DEVELOPMENT.md` при пересоздании `Gtk.GLArea` контекста видео замирало без активного трека (`vid=no`) | `TestGpuValidation.test_lifecycle_validation_passes_two_realize_cycles` (`tests/test_gpu_validation.py`), тесты восстановления `vid` в `tests/test_hdr.py` |
| Режим `transition` в `src/gpu_validation.py` (непаузовый цикл `SDR → HDR → SDR → HDR`) | Смена `GL_RGBA8`/`srgb` ↔ `GL_RGBA16F`/`rec2100-pq` в одном процессе без пересоздания контекста | **Задокументирован**: перекрывается более строгим паузовым сценарием `publication` + `PausedTransitionPixelOracle` (разделы 1 и 3 выше) | `TestGpuValidation.test_transition_validation_passes_four_switches`, `test_transition_rejects_stale_hdr_surface_on_sdr` (`tests/test_gpu_validation.py`) |
| Режимы `quick`, `soak`, `sdr-soak`, `settling` и сэмплирование RSS/DRM в `src/gpu_validation.py`, `run_gate2_resource_settling.sh`, `run_gpu_validation.sh`, `run_flatpak_gpu_validation.sh` | Отсутствие роста VRAM/RSS после прогрева при 20 циклах load/unload и 100 ресайзах; стабильность 30-минутного прогона (`ADR-0001` Gate 1/Gate 2) | В части `1567e10a5b454e19a75f5965fcf37c353d48b70c`: парсер манифеста `settling` не поддерживал HLG-фикстуры; по самому механизму утечки VRAM/RSS в живом прогоне — **исторический дефект не задокументирован** | Синтетические unit-тесты `TestGpuValidation` в `tests/test_gpu_validation.py` (моки RSS/DRM и шагов `settling`/`soak`) |
| `tests/test_pixel_pipeline.py` (`src/pixel_pipeline.py`) | Монотонность PQ/SDR рампы, нейтральность серой оси, точность праймариз и отсутствие клиппинга (уровень L3, `ADR-0004` / `ADR-0005`) | **Исторический дефект не задокументирован** на реальном GPU (в живых прогонах Gate 2/3 синтетические паттерны проходили с запасом); защищает математический конвейер L3 | Синтетические отрицательные контроли в `tests/test_pixel_pipeline.py` (инжекция немонотонности, сдвига нейтральной оси, клиппинга) |
| `tests/run_gate2_extended_matrix.py`, `tests/run_gate3_dovi_matrix.py`, `tests/run_performance_ab.py`, `run_benchmark.py` | Гарнитуры расширенной матрицы Gate 2 (W3 дизеринг, W4 скриншоты, W5 субтитры, W6 p95 рендер-тайм `<= +10%`) и Gate 3 (DoVi профили 5/7/8, sweep `target-peak`) для нативного `gpu-next` | В коммите `4846e0fa88aa93a276033d4e567ecb379faa37d0`: исправление некорректного порога p95 и устаревших mpv drop-свойств в самом раннере; продуктовый дефект рендера — **исторический дефект не задокументирован** (нативный `gpu-next` вне релизного scope 1.0 по `D5`) | Отрицательный контроль `dolbyvision=no` в `tests/run_gate3_dovi_matrix.py` и проверка порогов `ADR-0005` |
| `tests/test_vendor_smoke.py` и `tests/test_nvidia.py` (задача `C16`) | Проверка «процесс жив 8 с, опубликован первый медиа-кадр, подтверждена идентичность runtime и отсутствие GL-ошибок» на Intel Mesa и NVIDIA PRIME (`371b510f0f7184e323b8e8fb727d87f99afe9287`, `c46b5247a7542a5902f361b7f0345bb7652ef65a`) | **Задокументирован**: в раннем smoke-прогоне (`1224119d39218b128bf199a84f3b1d5bdc133ada`, `c46b5247a7542a5902f361b7f0345bb7652ef65a`) без проверки телеметрии первого кадра отчёт завышал статус прохождения; по правилу `C16` эти файлы **не трогаются** в `C2` до подтверждения замены в `C4` | Проверки в `tests/test_vendor_smoke.py` и `tests/test_nvidia.py` |
