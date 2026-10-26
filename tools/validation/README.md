# Инвентарь инструментов валидации CineHDR (`C0` / решение `D7`)

Этот документ фиксирует проверяемую связь **«инвариант → исторический дефект или синтетический отрицательный контроль → инструмент и функции → регрессионный тест → команда воспроизведения → границы доказательства → точки подключения в продукте»** перед удалением governance-документов в задаче `C1` и переносом кода валидации в задачах `C2`, `C3` и `C4` (`docs/PLAN.md`, решение `D7`, `docs/adr/0008-release-scope-and-evidence.md`).

> **Примечание о ссылках на документы, удаляемые в `C1`**: ссылки на `docs/GPU_NEXT_DEVELOPMENT.md`, `docs/MODEL_USAGE_LOG.md` и `docs/adr/0006-bounded-validation-governance.md` ниже привязаны к базовому коммиту `45fb026e891a21ceb3150808b140085b10138988` (а также к коммитам, в которых были зафиксированы соответствующие свидетельства) и точным заголовкам разделов, чтобы оставаться воспроизводимыми через `git show <commit>:<path>` после выполнения `C1`.

## Разграничение текущего расположения (`C0`) и целевого переноса (`C2`/`C3`/`C4`)

На этапе `C0` код продукта, тесты и скрипты запуска **не перемещаются**. Все пути и команды воспроизведения ниже разделены на **текущее расположение (на коммите `C0`)** и **целевое расположение после `C2`/`C3`/`C4`**:

| Компонент (объём решения `D7`) | Текущее расположение на этапе `C0` | Целевое расположение (`C2` / `C3` / `C4`) |
|---|---|---|
| Пиксельный оракул FBO и отрицательные контроли (`PausedTransitionPixelOracle`, `FboPixelCapture`, `read_fbo_patch_samples`, `compute_patch_centers`, `compute_samples_hash`, `serialize_fbo_pixel_measurements`, `verify_fbo_pixel_measurements_artifact`) | `src/gpu_validation.py` (тесты: `tests/test_hdr.py`, `tests/test_gpu_validation.py`) | `tools/validation/gpu_validation.py` (`C4a`; опциональная установка в `cinehdr/validation/` при `-Dvalidation=true` по `C4d`) |
| Парсер трассы протокола Wayland и его тесты (`validate_trace`, `validate_composite_window_trace`, `validate_multiphase_trace`, `create_evidence_report`) | `tools/validation/parse_wayland_color_trace.py`, `tools/validation/test_wayland_color_trace.py` (до `C2a`: `tests/...`) | `tools/validation/parse_wayland_color_trace.py`, `tools/validation/test_wayland_color_trace.py` (`C2a`, остаются в `meson test`) |
| Паузовый автосценарий `SDR → HDR → SDR`, проверка первого опубликованного кадра и чистое завершение (`GpuValidationSession` в режиме `publication`, `validation_steps("publication")`, `publication_texture_evidence`, `evaluate_publication_validation`, лаунчер) | `src/gpu_validation.py`, `tools/validation/run_wayland_publication_validation.sh` (до `C3`: в корне; тесты: `tests/test_gpu_validation.py`, `tests/test_wayland_publication_scenario.py`, `tests/test_hdr.py`) | `tools/validation/gpu_validation.py` (`C4a`) и `tools/validation/run_wayland_publication_validation.sh` (`C3`) |
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
  1. **Дефект 1A (основной исторический дефект — устаревшие HDR-пиксели и `GL_RGBA16F`/`rec2100-pq` после перехода `HDR → SDR` на паузе, `2026-10-10`)**:
     В авторизованном прогоне GNOME Gate 2W Run 6 на Candidate v7 (`2026-10-10`) переходы `initial_sdr` и `SDR → HDR` прошли, но переход `HDR → SDR` на паузе завершился `FAIL`: контроллер сообщил об SDR-источнике и `hdr_active = False` (`target-trc = auto`), однако опубликованная текстура и целевой FBO остались в состоянии `GL_RGBA16F` / `rec2100-pq` со старыми PQ-пикселями (в симуляции и отрицательном контроле `reference_white = 0.5807` вместо SDR `1.0`), поскольку `mpv` сгенерировал ранний update до применения `video-params`, а `_on_hdr_controller_change()` вызывал только `queue_draw()` старой текстуры без принудительного перерендера FBO.
  2. **Дефект 1B (перепутанная ориентация строк патчей в координатах `glReadPixels` в Run 7 Candidate v12, `2026-10-11`)**:
     В прогоне Run 7 Candidate v12 (`2026-10-11`) `compute_patch_centers()` вычислял центры нижнего ряда патчей (`red_primary`, `green_primary`, `blue_primary`, `neutral_gradient`) в координатах сверху вниз (`y = 3 * height // 4`), тогда как `glReadPixels` в OpenGL использует начало координат в левом нижнем углу (`y = 0` внизу). В результате в нижнем ряду считывался верхний ряд (`black`, `near_black`, `reference_white`, `peak_white`), и праймариз давали `(0.0, 0.0, 0.0)` / `(1.0, 1.0, 1.0)` вместо чистых цветов.
  3. **Дефект 1C (захват кадра до `step 0` или маскировка дефектного первого опубликованного кадра последующим кадром в Run 7 Candidate v8–v12, `2026-10-10` — `2026-10-11`)**:
     До введения привязки к `_pending_render_frame_generation` и сохранения первого вердикта фазы в `PausedTransitionPixelOracle` ранний кадр до `step 0` мог переиспользоваться как `initial_sdr`, либо ошибочный первый кадр перехода мог быть перезаписан следующим тиком рендера.
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
- Сохраняемые ADR: `docs/adr/0007-paused-hdr-output-republication.md`, `docs/adr/0004-gate2-fbo-pixel-validation.md`.
- Источники, удаляемые в `C1` (доступны в коммите `45fb026e891a21ceb3150808b140085b10138988`):
  - `45fb026e891a21ceb3150808b140085b10138988:docs/GPU_NEXT_DEVELOPMENT.md`, разделы `### Gate 2W Run 6 result — GNOME / Candidate v7 (2026-10-10)` и `### Gate 2W Run 7 final GNOME adjudication (2026-10-11)`.
  - `45fb026e891a21ceb3150808b140085b10138988:docs/MODEL_USAGE_LOG.md`, строка таблицы `2026-10-11 | Gate 2W Run 7 Candidate v14 final adjudication` и раздел `### 2026-10-17 First-frame republication simplification and multi-backend validation`.

#### 1.3. Фактические текущие файлы и функции
- `src/hdr_controller.py`: `CommittedOutputState`, `HdrController.apply_hdr_settings`, `HdrController.committed_output_state`, `HdrController.output_epoch`, `HdrController.is_publication_pending`, `HdrController.is_publication_suppressed`, `HdrController.state_gap_reason`.
- `src/video_widget.py`: `MpvVideoWidget._on_hdr_controller_change`, `MpvVideoWidget._schedule_render`, `MpvVideoWidget._render_pending_frame`, `should_render_frame`.
- `src/gpu_validation.py` *(целевой путь в `C4a`: `tools/validation/gpu_validation.py`)*: `CANONICAL_8_PATCHES`, `DEFAULT_SDR_REPEAT_TOLERANCE`, `DEFAULT_MIN_HDR_DISTINCTION`, `compute_patch_centers`, `read_fbo_patch_samples`, `compute_samples_hash`, `FboPixelCapture`, `PausedTransitionPixelOracle` (`record_capture`, `record_readback_error`, `get_capture`, `evaluate`), `serialize_fbo_pixel_measurements`, `verify_fbo_pixel_measurements_artifact`, `GpuValidationSession._on_fbo_pixel_capture`, `GpuValidationSession._on_fbo_pixel_readback_error`.
- `tests/test_hdr.py`: классы `TestPausedHdrOutputRepublication`, `TestPausedTransitionPixelOracle`.
- `tests/test_gpu_validation.py`: класс `TestPublicationFboPixelValidationPipeline`.

#### 1.4. Карта «дефект / контроль → конкретный регрессионный тест»
- **Дефект 1A (перерисовка FBO и публикация при `HDR ↔ SDR` на паузе и `update_flags == 0`)**:
  - `TestPausedHdrOutputRepublication.test_event_order_frame_rendered_then_state_change_sdr` (`tests/test_hdr.py`)
  - `TestPausedHdrOutputRepublication.test_controlled_sequence_pause_sdr_hdr_sdr_full_cycle` (`tests/test_hdr.py`)
  - `TestPausedHdrOutputRepublication.test_zero_mpv_update_flags_forces_render_when_scheduled` (`tests/test_hdr.py`)
  - `TestPausedHdrOutputRepublication.test_partial_setter_failure_suppresses_publication` (`tests/test_hdr.py`)
  - `TestPausedHdrOutputRepublication.test_superseded_epoch_discards_render` (`tests/test_hdr.py`)
  - `TestPausedHdrOutputRepublication.test_shutdown_cancels_pending_and_suppresses_publication` (`tests/test_hdr.py`)
  - `TestPausedTransitionPixelOracle.test_paused_transition_defect_reproduction_and_resolution` (`tests/test_hdr.py`)
- **Дефект 1B (ориентация строк патчей в координатах `glReadPixels`, `y=0` внизу)**:
  - `TestPublicationFboPixelValidationPipeline.test_fbo_patch_centers_match_live_fixture_row_orientation` (`tests/test_gpu_validation.py`)
- **Дефект 1C (запрет переиспользования кадра до `step 0` и запрет маскировки дефектного первого кадра последующим кадром)**:
  - `TestPublicationFboPixelValidationPipeline.test_initial_paused_publication_requests_and_requires_fresh_frame` (`tests/test_gpu_validation.py`)
  - `TestPublicationFboPixelValidationPipeline.test_first_new_publication_mismatch_survives_later_recovery` (`tests/test_gpu_validation.py`)
  - `TestPausedTransitionPixelOracle.test_first_published_transition_frame_cannot_be_hidden_by_recovery` (`tests/test_hdr.py`)
  - `TestPausedTransitionPixelOracle.test_fbo_dropped_after_readback_is_never_sent_to_oracle` (`tests/test_hdr.py`)
  - `TestPausedTransitionPixelOracle.test_oracle_rejects_overwrite_attempt` (`tests/test_hdr.py`)
- **Дефект 1D (сохранение `UNVERIFIED` при отсутствии getter'а `Gdk.GLTexture` на первом опубликованном кадре)**:
  - `TestPublicationFboPixelValidationPipeline.test_first_published_unavailable_texture_evidence_survives_recovery_as_unverified` (`tests/test_gpu_validation.py`)
- **Синтетические отрицательные контроли пиксельного оракула и артефакта `fbo_pixel_measurements.json`**:
  - `TestPausedTransitionPixelOracle.test_negative_control_substituting_hdr_pixels_fails` (`tests/test_hdr.py`)
  - `TestPausedTransitionPixelOracle.test_hdr_not_distinguishable_from_sdr_fails` (`tests/test_hdr.py`)
  - `TestPausedTransitionPixelOracle.test_oracle_rejects_stale_or_non_advancing_epoch` (`tests/test_hdr.py`)
  - `TestPublicationFboPixelValidationPipeline.test_strict_rejection_on_negative_control_mismatch` (`tests/test_gpu_validation.py`)
  - `TestPublicationFboPixelValidationPipeline.test_strict_rejection_on_readback_error` (`tests/test_gpu_validation.py`)
  - `TestPublicationFboPixelValidationPipeline.test_verify_fbo_pixel_measurements_negative_cases` (`tests/test_gpu_validation.py`)
  - `TestPublicationFboPixelValidationPipeline.test_negative_session_write_report_fails_when_artifact_corrupted` (`tests/test_gpu_validation.py`)

#### 1.5. Команда воспроизведения и предусловия
- **Предусловия для юнит-тестов**: Python 3, установленные PyGObject (`gi.repository.Gtk`, `Gdk`, `Adw`, `GLib`, `Gio`) и тестовое окружение репозитория; физический GPU или активная сессия Wayland для юнит-тестов **не требуются** (GL readback и текстуры мокируются детерминированно).
- **Команда воспроизведения (текущее расположение `C0`)**:
  ```bash
  python3 -m unittest \
    tests.test_hdr.TestPausedHdrOutputRepublication \
    tests.test_hdr.TestPausedTransitionPixelOracle \
    tests.test_gpu_validation.TestPublicationFboPixelValidationPipeline -v
  ```
- **После переноса в `C4`**: те же тест-классы запускаются через `meson test` / `python3 -m unittest` с импортом `PausedTransitionPixelOracle` и функций артефакта из `tools/validation/gpu_validation.py`.

#### 1.6. Что результат доказывает и чего НЕ доказывает
- **Что доказывают модульные тесты (на моках)**:
  - Детерминированную логику смены эпох `CommittedOutputState`, принудительного планирования `_schedule_render(force_redraw=True)` при `update_flags == 0`, вычисления координат патчей `compute_patch_centers()`, сохранения первого опубликованного образца фазы без перезаписи и отбраковки невалидных/подделанных данных в `PausedTransitionPixelOracle` и `verify_fbo_pixel_measurements_artifact()`.
- **Что доказывает живой запуск оракула в сценарии `publication` (на реальном GPU)**:
  - Что в реальном OpenGL/libmpv контексте (`opengl` или `opengl-next`) первый опубликованный кадр каждой фазы (`initial_sdr`, `hdr`, `return_sdr`) отрендерен в целевой FBO (`GL_RGBA8` или `GL_RGBA16F`), а считанные через `glReadPixels` пиксели 8 патчей возвращаются в `return_sdr` к `initial_sdr` в пределах `<= 0.020` и отличаются от `hdr` не менее чем на `0.150`.
- **Чего НЕ доказывает (ни модульный тест, ни живой FBO readback)**:
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
Следующий критерий описывает целевой L3p из правил. Реализация на этапе C0 уже проверяет цепочку протокола, но ещё не реализует весь этот критерий; ограничения перечислены в §2.6 и являются входом для C2a.

- **Защищаемый инвариант (`AGENTS.md` §8, `docs/TESTING.md` §L3p, `45fb026e891a21ceb3150808b140085b10138988:docs/adr/0006-bounded-validation-governance.md` §`Gate 2W multi-phase provenance and execution amendment (2026-10-04)`)**:
  Клиентская публикация `Gdk.ColorState` в `Gdk.GLTextureBuilder` не доказывает отправку HDR-метаданных композитору. Передача HDR-описания поверхности композитору Wayland считается подтверждённой (`PASS` на уровне L3p) только тогда, когда в сырой трассе `WAYLAND_DEBUG` данного процесса в рамках одной жизни `wl_registry` зафиксирована полная без сбоев цепочка:
  1. биндинг `wp_color_manager_v1` и получение `wp_color_management_surface_v1` через `get_surface(wl_surface)` для поверхности, несущей видео (toplevel `xdg_toplevel` при композитном пути или связанный `wl_subsurface` при offload, с реальным `wl_surface.attach(wl_buffer != 0)`);
  2. создание описания изображения с HDR-трансфером (параметрический PQ `tf_named(11)` + `primaries_named(6)` BT.2020 или поддерживаемое представление по `AGENTS.md` §8) и получение события `wp_image_description_v1.ready(identity)` / `ready2(hi, lo)` **до** вызова `set_image_description`;
  3. вызов `wp_color_management_surface_v1.set_image_description(desc, render_intent)` без промежуточного `unset_image_description` или повторного создания/переиспользования ID описания до коммита;
  4. последующий `wl_surface.commit()` на той же самой поверхности и в той же эпохе `wl_registry`, при отсутствии `wp_image_description_v1.failed`, `wl_display.error` и разрыва соединения.
- **Конкретные исторические дефекты и отрицательные контроли (с разграничением отказа KWin/GTK 4.22.4 и прогона GNOME Run 2)**:
  1. **Дефект 2A (отсутствие opt-in `GDK_DEBUG=color-mgmt` и отказ capability-гейта GTK 4.22.4 на KWin 6.7.4 без `TF srgb`, `2026-08-23`)**:
     - В первом живом трассировочном прогоне на KWin 6.7.4 / GTK 4.22.4 (`2026-08-23`) без `GDK_DEBUG=color-mgmt` GTK вообще не связывал `wp_color_manager_v1`.
     - Во втором прогоне с `GDK_DEBUG=color-mgmt` на KWin 6.7.4 (который в `wp_color_manager_v1.supported_tf_named` объявлял `gamma22` и `pq`, но не объявлял `TF srgb`) GTK связывал `wp_color_manager_v1` и получал `done()`, но затем уничтожал менеджер (`destroy`) **до** вызова `get_surface` или `set_image_description` (`gate2-wayland-color-management-legacy-20260823-153023.json`, статус `FAIL`), из-за чего композитор интерпретировал буфер Rec.2100 PQ как sRGB.
     - *Отличие от GNOME Run 2 (`wayland-trace-MgsoJA`, `2026-09-24`)*: на GNOME Shell / Mutter протокольная цепочка в Run 2, напротив, **прошла (`status: PASS`)** (`wp_color_manager_v1#32` → `wp_image_description_v1#51.ready` → `set_image_description` → `wl_surface#61.commit()`), а причина перевода Run 2 в `HOLD` заключалась исключительно в отсутствии синхронного снимка `Video Output Diagnostics` из той же сессии (`Active API` и `Session status` остались `Unknown`, см. Дефект 4A в разделе 4).
  2. **Дефект 2B (несколько `wl_registry` в одном процессе: приватные probe-соединения `wayland_cm_probe` / `wayland_output_hdr` и переиспользование object ID между реестрами, `2026-09-24` и `2026-10-04`)**:
     В реальном процессе CineHDR создаются отдельные `wl_registry` (основной реестр GDK `wl_registry#2`, probe-реестры `wayland_cm_probe` и `wayland_output_hdr`). В ранней версии парсера глобальный поиск `wp_color_manager_v1` либо выбирал незавершённый приватный probe-менеджер вместо полного менеджера GDK, либо при переиспользовании object ID (`wl_registry#2`, `wp_image_description_v1#31`, `wl_callback#31.done()` → `wl_display#1.delete_id(31)`) между разными эпохами реестра ошибочно склеивал события от чужого реестра/менеджера с основным окном (выявлено ревью Astra high `2026-10-04` и исправлено в коммитах `e41fbeb1dad3b1b463c40030efba587f2341bfb7` и `6a88937df082193c93e11e5423a268e87c501850`).
  3. **Дефект 2C (различение композитного окна `xdg_toplevel` и видео-subsurface `wl_subsurface` + `wp_viewport`, а также отбраковка HLG `tf=13` вместо PQ `tf=11`, `2026-10-04`)**:
     В `validate_multiphase_trace` требовалось строго отличать 4-фазное чередование `SDR → HDR → SDR → HDR` на доказанном видео-subsurface (с `wl_subsurface`, `wp_viewport` и `wl_surface.attach`) от смены описания на `xdg_toplevel` без subsurface-цепочки (возвращает `UNKNOWN`), а также отклонять подмену PQ (`tf_named=11`) на HLG (`tf_named=13`).
  4. **Синтетические отрицательные контроли протокола (`unset_image_description` перед `commit`, `ready` после `set_image_description`, `wp_image_description_v1.failed`, `wl_display.error`, разрыв сокета, 64-битный `ready2`)**:
     Для случаев `unset_image_description` между `set_image_description` и `wl_surface.commit()` и `wp_image_description_v1.failed` в живой трассе GNOME/KWin *исторический дефект не задокументирован*; эти ветки защищены синтетическими отрицательными контролями в `tools/validation/test_wayland_color_trace.py`.
  5. **Дефект 2D (выбор неэффективного описания цвета при commit или ложный PASS при замене/отмене в composite_window, `2026-10-19`)**:
     В `validate_composite_window_trace` кандидат `set_image_description` ошибочно подтверждался первым же последующим `commit`, даже если до этого коммита на той же цветовой поверхности был вызван другой `set_image_description` (например, валидное описание #32 вместо #31), `unset_image_description` или `destroy` интерфейса `wp_color_management_surface_v1`. В результате парсер возвращал устаревший дескриптор (#31 вместо #32) либо выдавал ложный `PASS` при замене на некорректный/неподдерживаемый запрос. Исправлено учётом double-buffered семантики Wayland (`color-management-v1.xml` строки 713–718, 730–778, 780–789): промежуточный `set_image_description` (с любыми аргументами), `unset_image_description` или `destroy` на интерфейсе `wp_color_management_surface_v1` отменяет кандидат, тогда как уничтожение самого `wp_image_description_v1` после `set` сохраняет кандидат благодаря copy semantics протокола.
  6. **Дефект 2E (ложный PASS для любого ready-описания или SDR/unrevealed в composite_window, `2026-10-20`)**:
     Ранее `validate_composite_window_trace` выдавал `status: PASS` при наличии любого подтверждённого коммитом ready-описания, даже если дескриптор задавал SDR-трансфер (sRGB, BT.1886, gamma 2.2, WCG-SDR BT.2020+BT.1886), нераскрытые параметры или `ext_linear`. Подключена строгая проверка семантики именованных трансферов параметрических описаний (`wp_image_description_creator_params_v1`): только подтверждённый коммитом HDR-трансфер (PQ `tf=11`, HLG `tf=13`) даёт `status: PASS`. Если коммитились только SDR-описания — отказ (`TraceValidationError` с указанием SDR); если присутствуют нераскрытые/неподдерживаемые описания (`ext_linear`, параметры после `create`, чужие объекты, ID reuse creator'а) — возвращается `status: UNKNOWN` с причиной. Ранний SDR/UNKNOWN коммит не маскирует последующий корректный HDR коммит. Ревизия повышена до `wayland-composite-window-hdr-submission-v1`. Источник семантики параметров creator'а: `color-management-v1.xml` строки 1026–1130 (`wayland-protocols-devel-1.49-1.fc44.noarch`).
  7. **Дефект 2F (ложные PASS при set_tf_power, отсутствующих, повреждённых или неизвестных primaries в параметрических описаниях composite_window, `2026-10-20`)**:
     В `validate_composite_window_trace` после подключения семантики HDR выявлены ложные `PASS`: 1) `set_tf_power` отдельно уже давал `UNKNOWN` на `798e3ac` (как нераскрытый `incomplete_set`), а ложный `PASS` возникал при сочетании с `set_tf_named` (в любом порядке) вместо протокольного отказа (`already_set`); 2) creator без установки primaries (нарушение `incomplete_set`) или с повреждённым/неизвестным значением enum (`broken` или `999`, нарушение `invalid_primaries_named`) давал ложный `PASS` при наличии HDR `set_tf_named`; 3) альтернативный `set_primaries` (CIE xy) давал `PASS` без валидации координат. Исправлено согласно `color-management-v1.xml` строки 1026–1050, 1058–1065, 1126–1160, 1171–1214 (`wayland-protocols-devel-1.49-1.fc44.noarch`): совместное использование `set_tf_named` и `set_tf_power` либо повторные вызовы TF возвращают `UNKNOWN` (`already_set / contradictory`); `set_tf_power` отдельно возвращает `UNKNOWN` (степенная кривая не верифицирована); отсутствие primaries возвращает `UNKNOWN` (`incomplete_set / unrevealed`); повреждённые аргументы или неизвестные primaries (вне 1..10) возвращают `UNKNOWN` (`malformed / invalid_primaries_named`); альтернативный `set_primaries` возвращает `UNKNOWN` (непроверенные координаты); ранний UNKNOWN-кандидат не маскирует последующий корректный HDR-кандидат.
  8. **Распознавание предопределённого HDR-описания Windows-scRGB (`create_windows_scrgb`, решение D7, AGENTS §8, `2026-10-21`)**:
     В `validate_composite_window_trace` добавлено распознавание предопределённого HDR-представления Windows-scRGB, создаваемого через `wp_color_manager_v1.create_windows_scrgb` (`color-management-v1.xml` строки 165–166, 474–524, 539–550, `wayland-protocols-devel-1.49-1.fc44.noarch`):
     - Описание использует координаты primaries sRGB/BT.709 (`primaries_named=1`) и характеристику передачи extended linear (`tf_named=5`), классифицируясь как `color_type="windows_scrgb"`, `semantic_class="hdr"`;
     - Требует обязательного предварительного объявления `wp_color_manager_v1.feature.windows_scrgb` (feature 7) именно тем менеджером, который связан с проверяемой поверхностью окна. При отсутствии объявления или объявлении другим менеджером возвращается `status: UNKNOWN` с диагностической причиной, а не `PASS`;
     - Проходит через полный общий путь проверок доказательства: проверка `app_id` окна, происхождение от менеджера целевой поверхности, отсутствие переиспользования ID, получение `ready`/`ready2` до `set_image_description`, отсутствие замены или отмены до коммита, последующий `commit` целевой поверхности и изоляция от объектов других реестров/менеджеров;
     - Не требует параметрического creator, `set_tf_named`, `set_luminances` или `get_information` (запрещён протоколом для этого типа);
     - Параметрическое `ext_linear` без проверенных luminances остаётся `UNKNOWN`.
  9. **Распознавание параметрического HDR-описания `ext_linear` с проверенными `set_luminances` (`AGENTS.md` §8, `color-management-v1.xml` строки 148–149, 308–313, 1216–1275, `wayland-protocols-devel-1.49-1.fc44.noarch`, `2026-10-21`)**:
     В `validate_composite_window_trace` расширен строгий параметрический путь `composite_window` для `tf_named=ext_linear` (`tf_named=5`) с проверенными именованными primaries (`primaries_named` 1..10):
     - Требует обязательного предварительного объявления `wp_color_manager_v1.feature.set_luminances` (feature 4) именно тем менеджером, который создал creator, до вызова `set_luminances`. При отсутствии объявления или объявлении другим менеджером возвращается `status: UNKNOWN` с диагностической причиной;
     - Учитывает ровно один вызов `set_luminances(min_lum, max_lum, reference_lum)` того же creator до вызова `create`. Повторные, повреждённые, отрицательные, выходящие за диапазон `uint32` (0..4294967296) аргументы, вызовы на чужих creator или после `create()` возвращают `UNKNOWN`;
     - Аргументы передаются строго как целые без знака: `min_lum` в cd/m² × 10000, `max_lum` и `reference_lum` в cd/m² без масштабирования; строго проверяются физические ограничения `max_lum * 10000 > min_lum` и `reference_lum * 10000 > min_lum` без ошибок округления;
     - Классифицируется как HDR (`semantic_class="hdr"`, `color_type="custom_p{prim}_tf5"`) строго при выполнении критерия различимости `max_lum > reference_lum` (per `AGENTS.md` §8); при `max_lum <= reference_lum` описание остаётся `UNKNOWN` (`semantic_class="unknown"`);
     - Без явного вызова `set_luminances` параметрическое `ext_linear` остаётся `UNKNOWN` (значения по умолчанию не подставляются);
     - После классификации применяется весь существующий строгий путь проверок доказательства: `ready`/`ready2` → `set_image_description` → `commit` на целевой поверхности без промежуточной замены на SDR или ошибок протокола.
  10. **Поколенческий учёт ID объектов wp_color_manager_v1, валидация связности реестров wl_registry и селективная проверка активности буферов на subsurfaces (`2026-10-24`)**:
      - В `validate_composite_window_trace` глобальный подсчёт `manager_bind_counts[id] != 1` заменён на строгий поколенческий учёт `_is_valid_manager_generation`: клиенты со сложной многопоточной архитектурой WSI (например, Mesa Vulkan WSI) могут создавать вспомогательные query-очереди, последовательно выполняя `bind` → `destroy` менеджера цвета и получая событие `delete_id`, после чего тот же ID перевыделяется основной display-очереди из нового `wl_registry`. Повторное использование того же ID разрешено строго при условии полного завершения предыдущего поколения (`destroy` или `delete_id`) до нового `bind` и отсутствия вызовов менеджера до его создания. Одновременное существование активных объектов с одинаковым ID строго бракуется проверкой `_has_active_id_collision` (`status: FAIL`);
      - Проверка принадлежности реестров к одному Wayland-соединению (`_verify_registry_connection_link`): если `wl_compositor` и `wp_color_manager_v1` связаны через разные `wl_registry` (например, `wl_registry#2` и `wl_registry#52`), парсер проверяет, что оба реестра созданы вызовом `wl_display#1.get_registry` в рамках одного соединения и объявляют одинаковый глобальный ID для `wp_color_manager_v1`. Инородные реестры без подтверждённой связи с `wl_display` отклоняются (`status: FAIL`);
      - В `create_trace_only_report` проверка `subsurfaces` сделана селективной: наличие объявленных, но неактивных дочерних подповерхностей (без эффективного или ожидающего ненулевого буфера в момент коммита, например пустых OSD-слоёв mpv) не вызывает ложного отказа; статус `UNKNOWN` возвращается только если на дочерней подповерхности обнаружен эффективный ненулевой буфер, создающий неоднозначность разделения видеоповерхности и интерфейса.
   11. **Устранение ложных PASS в --trace-only: связь реестров, завершение по delete_id, межинтерфейсные коллизии ID и снимок иерархии subsurfaces на момент коммита (`2026-10-25`)**:
       - **Связь реестров**: численный ID `wl_display#1` в объединённом выводе `WAYLAND_DEBUG` не доказывает общее соединение при наличии нескольких реестров (`wl_registry`). Если принадлежность разных реестров к одному соединению невозможно строго доказать из трассы, возвращается честный статус `UNKNOWN` вместо ложного `PASS`;
       - **Жизненный цикл объектов**: приход события `wl_display#1.delete_id` завершает поколение объекта даже при отсутствии последующего `bind`. Вызовы на удалённом объекте без нового `bind` отбраковываются с ошибкой протокола (`status: FAIL`);
       - **Межинтерфейсные коллизии ID**: создание объекта с тем же численным ID при активном объекте другого интерфейса (например, `wl_surface#3` во время активности `wp_color_manager_v1#3`) выявляется кросс-интерфейсной проверкой коллизий активных жизненных циклов (`_has_active_id_collision`) и бракуется (`status: FAIL`);
       - **Временной срез иерархии подповерхностей (subsurfaces)**: принадлежность подповерхностей окну (`subsurfaces`) и состояние их буферов вычисляются функцией `_surface_hierarchy_at_commit` строго на момент выбранного HDR `commit_index`. Переподчинение подповерхности другому родителю или её уничтожение после этого коммита не искажает историческое состояние в момент HDR-публикации (`status: UNKNOWN`).
   12. **Устранение ложных PASS в --trace-only при отсутствии/дублировании реестра: снятие безусловного доверия ID 2 и проверка поколений реестра (`2026-10-25`)**:
       - **Снятие безусловного доверия reg_id == 2**: в `_verify_registry_connection_link` устранено безусловное предположение `if reg_id == 2: return True, None`. Одинаковый числовой ID реестра в объединённом `WAYLAND_DEBUG` не доказывает принадлежность одному соединению, особенно при повторном создании;
       - **Наблюдаемое происхождение реестра**: для статуса `PASS` в автономном режиме `--trace-only` происхождение `wl_registry` должно быть явно зафиксировано в трассе вызовом `wl_display#1.get_registry`. При отсутствии наблюдаемого создания возвращается `status: UNKNOWN` (`reason: creation of wl_registry#X by wl_display#1 was not observed in trace: registry origin cannot be proven`);
       - **Контроль активных поколений реестра**: при наличии нескольких вызовов `wl_display#1.get_registry` для одного и того же ID без завершения предыдущего поколения (`delete_id`) возвращается `status: UNKNOWN` (`reason: multiple active creations of wl_registry#X observed in trace without termination: connection belonging cannot be established in unified WAYLAND_DEBUG`);
       - **Канонический положительный и отрицательные контроли**: синтетическая трасса `SYNTHETIC_MPV_HDR_TRACE` дополнена каноническим созданием `wl_display#1.get_registry(new id wl_registry#2)`, добавлены регрессионные тесты для мутации 1 (удаление создания реестра → UNKNOWN) и мутации 2 (дублирование создания реестра перед `get_surface` без завершения → UNKNOWN).
   13. **Воспроизводимый инструмент маркировки соединений Wayland (`wayland_conn_tagger.c`) и автономный тест (`2026-10-25`)**:
       - **Проблема коллизии соединений**: при запуске mpv с `vo=gpu-next` и `waylandvk` одновременно открываются независимые Wayland-соединения (mpv и Mesa Vulkan WSI), в которых создаются идентичные численные ID (`wl_display#1`, `wl_registry#2`, `wl_callback#3`). В стандартном `WAYLAND_DEBUG=1` все сообщения смешиваются в общий поток `stderr` без атрибуции соединения, делая невозможным надёжное доказательство принадлежности объектов;
       - **Архитектура шима (`tools/validation/wayland_conn_tagger.c`)**: библиотека `LD_PRELOAD`, перехватывающая:
         - Жизненный цикл соединений: `wl_display_connect_to_fd`, `wl_display_connect`, `wl_display_disconnect` (регистрация указателя `display`, назначение уникального `conn_id` и сохранение сокета `fd`);
         - Маршаллинг запросов клиента: `wl_proxy_marshal_array_flags` (охватывает все макросы и вызовы `wl_proxy_marshal*` в `libwayland-client`);
         - Диспетчеризацию входящих событий: все 10 публичных функций диспетчеризации (`wl_display_dispatch`, `wl_display_dispatch_queue`, `wl_display_dispatch_queue_pending`, `wl_display_dispatch_queue_pending_single`, `wl_display_dispatch_pending`, `wl_display_dispatch_pending_single`, `wl_display_dispatch_queue_timeout`, `wl_display_dispatch_timeout`, `wl_display_roundtrip`, `wl_display_roundtrip_queue`);
         - Чтение событий и цикл дисплея: `wl_display_read_events`, `wl_display_prepare_read`, `wl_display_prepare_read_queue`, `wl_display_cancel_read`, `wl_display_flush` (используемые poll source GTK `gdkeventsource` и читателями очередей);
         - Перехват динамической загрузки библиотеки (`dlopen`, `dlclose`): вызывает `real_dlopen(filename, flags)` в первую очередь (сохраняя возврат `NULL` и `dlerror()` для несуществующих путей вида `/nonexistent/libwayland-client.so.0` или невалидных флагов) и подменяет возвращаемый дескриптор на `tagger_handle` только тогда, когда открытый модуль совпадает с настоящей `libwayland-client` (`target_wayland_handle`, определяемой через `dl_iterate_phdr` независимо от промежуточных Wayland-интерпозеров в `LD_PRELOAD`) и эта библиотека экспортирует все обёрнутые функции (`target_has_all_wrapped_symbols`). Если в загруженной `libwayland` отсутствует необязательный обёрнутый символ (например, `wl_display_dispatch_queue_timeout` на старых версиях), шим не изменяет загруженную таблицу ELF-символов (`DT_SYMTAB`) и права страниц (`mprotect`), а оставляет исходный дескриптор `real_h`, так что `dlsym` честно возвращает `NULL` с `dlerror()`, прямые вызовы обёрток защищены от `NULL real_fn`, а неотслеживаемые вызовы дают `[conn#untracked fd#?]` и честный `UNKNOWN` в парсере без ложного `PASS` и без краша (включая сборку с `-Wl,--as-needed` и `-Wl,-z,noseparate-code`). Баланс `dlopen`/`dlclose` для подменённого handle учитывается счётчиком `substituted_open_count` под мьютексом `dl_lock` (лишний `dlclose` возвращает `-1`, не выгружая шим), а глобальный `dlsym` остаётся нетронутым;
         - Загрузчик probe в `src/wayland_cm_probe.py` открывает `libwayland-client` с `RTLD_GLOBAL`, затем разрешает функции через дескриптор процесса (`ctypes.CDLL(None)`). Так вызовы `ctypes` проходят через `LD_PRELOAD` даже в GNOME Platform 50, где нет `wl_display_dispatch_*_single` и шим корректно не подменяет исходный дескриптор. Проверка отсутствующего символа по исходному дескриптору по-прежнему возвращает ошибку;
         - Атомарное обнаружение и жизненный цикл соединений: потокобезопасное первое обнаружение `wl_display` в критической секции под мьютексом `conns_lock` с атомарным получением `fd` через `wl_display_get_fd` (исключающее дублирование `conn_id` при параллельном входе нескольких потоков), и полное удаление записей при `wl_display_disconnect`;
         - Форматированный вывод `stderr`: `__fprintf_chk` (используемый внутренней функцией `wl_closure_print`), `__vfprintf_chk`, `fprintf`, `vfprintf`, `fputs`;
         - Атомарность вывода и защита от гонок: вывод префикса `[conn#<id> fd#<fd>]` и тела сообщения объединяются в критическую секцию с `flockfile(stream)` / `funlockfile(stream)` (рекурсивный мьютекс потока), исключая разделение префикса и сообщения при параллельных вызовах из разных потоков;
         - Защита контекста потока: контекст соединения сохраняется в thread-local переменных `current_conn_id` и `current_conn_fd` с использованием RAII-подобного сохранения/восстановления стека контекстов (`enter_conn_scope` / `leave_conn_scope`) и guard-флага от рекурсии. Сообщения протокола вне активного контекста маркируются как `[conn#untracked fd#?]`, исключая присвоение чужого `conn_id`;
       - **Автономный тест (`tools/validation/test_conn_tagger_harness.c`, `test_wayland_conn_tagger.py`)**:
         - Через `socketpair` и `wl_display_connect_to_fd` запускаются два клиента в одном процессе;
         - Исходящие запросы (`get_registry`, `sync`) и входящие события (`callback.done`) перемежаются;
         - Проверка GTK-цикла (`--read-events`): `prepare_read` → `read_events` → `dispatch_pending` подтверждает корректную маркировку событий без untracked;
         - Проверка динамической загрузки `ctypes.CDLL`: тест подтверждает маршрутизацию вызовов Python ctypes через обёртки шима;
         - Проверка цепочки нескольких интерпозеров (`test_two_interposers_rtld_next_chaining`): тест проверяет все перестановки порядка предзагрузки двух библиотек с `RTLD_NEXT` без бесконечной рекурсии и крашей;
         - Отрицательные контроли чужих библиотек, несуществующих путей (`/nonexistent/libwayland-client.so.0`), альтернативной `libwayland-client.so.0`, баланса `dlopen`/`dlclose` (`--handle-semantics`), отсутствующих в загруженной `libwayland` обёрнутых символов + защиты от `NULL real_fn` (`--missing-symbol`), сборки с `-Wl,--as-needed` / `-Wl,-z,noseparate-code` и цепочек с другим Wayland-интерпозером до/после шима;
         - Многопоточный стресс-тест атомарного первого обнаружения соединений: 16 параллельных потоков и последующее чистое разрегистрирование при disconnect;
         - Многопоточный стресс-тест запускает 2 параллельных потока по 2000 вызовов `wl_display_get_registry` и подтверждает 4000 атомарных строк, 0 двойных меток, 0 строк без меток и ровно по 2000 строк на каждое соединение;
         - Положительный контроль подтверждает наличие меток `[conn#1 fd#...]` и `[conn#2 fd#...]` на всех сообщениях;
         - Отрицательный контроль подтверждает отсутствие меток при запуске без `LD_PRELOAD`;
       - **Команды сборки и тестирования**:
         ```bash
         # Сборка библиотеки-перехватчика
         gcc -Wall -Wextra -shared -fPIC -o /tmp/libwayland_conn_tag.so tools/validation/wayland_conn_tagger.c -ldl -lwayland-client

         # Сборка тестового стенда
         gcc -Wall -Wextra -o /tmp/test_conn_tagger_harness tools/validation/test_conn_tagger_harness.c -lwayland-client -ldl

         # Автономный запуск через Python unittest
         python3 tools/validation/test_wayland_conn_tagger.py
         ```
       - **Ограничения применения к нативному `mpv`**:
         1. **Динамическая линковка**: `LD_PRELOAD` работает только с динамически слинкованным `libwayland-client.so.0`; статические бинарники не могут быть перехвачены;
         2. **Изоляция Flatpak / Sandboxing**: при запуске внутри Flatpak-песочницы хостовый путь библиотеки недоступен без явного проброса через `--filesystem`;
         3. **Флаг `RTLD_DEEPBIND`**: если драйвер или библиотека открывает `libwayland-client` через `dlopen(..., RTLD_DEEPBIND)`, вызовы функций могут замыкаться внутри библиотеки минуя `LD_PRELOAD`;
         4. **Наследование окружения**: дочерние процессы, порождаемые `mpv` (скрипты, yt-dlp), наследуют `LD_PRELOAD` и `WAYLAND_DEBUG`, если переменные не очищены;
         5. **Перемешивание логов в `stderr`**: OSD-статусы и терминальные логи `mpv` попадают в `stderr` вместе с `WAYLAND_DEBUG`; для чистого захвата требуется перенаправление `2> wayland_tagged.log` или запуск с `--msg-level=all=warn`.
   14. **Интеграция парсера с маркированными трассами: автоматический выбор соединения по app-id и fail-closed валидация меток (`2026-10-25`)**:
       - **Автоматический выбор соединения**: при наличии меток `[conn#<id> fd#<fd>]` парсер автоматически определяет соединение, на котором вызван `xdg_toplevel.set_app_id(expected_app_id)`, и изолирует поток сообщений этого соединения без необходимости ручной предварительной фильтрации;
       - **Защита от отсутствующих/неотслеживаемых меток**: если в трассе присутствуют метки, но хотя бы одна строка протокола Wayland не имеет достоверной метки соединения или содержит метку `[conn#untracked]`, парсер возвращает `status: UNKNOWN` (`reason: unverifiable/untracked Wayland debug call without verified connection attribution`);
       - **Обязательность меток в режиме `--trace-only`**: в автономном режиме `--trace-only` (L3p) немаркированная трасса без меток `[conn#<id> fd#<fd>]` строго возвращает `status: UNKNOWN`, так как единый числовой поток без подтверждения дескрипторов сокетов не доказывает изоляцию соединения. При этом для автономных L1-тестов семантики протокола (`trace_only=False`) поддержка немаркированных синтетических трасс сохранена;
       - **Контроль инвариантности `fd` и полноты меток**: парсер проверяет постоянство сокетного дескриптора `fd` внутри каждого `conn_id` по всей трассе; изменение `fd` внутри одного соединения (например, подмена на `[conn#1 fd#12]`) либо отсутствие `fd#` на протокольном сообщении (например, `[conn#1]`) возвращает `status: UNKNOWN`;
       - **Защита от неоднозначности и подмены**: если несколько соединений претендуют на один `app-id` или ни одно не найдено — возвращается `status: UNKNOWN`; при подмене метки коммита или описания на чужое соединение — возвращается отказ `FAIL` (невыполненный протокольный контракт);
       - **Метаданные верификации**: в результирующий JSON-отчёт L3p добавляются поля `selected_connection_id`, `total_connections_observed` и `connection_tags_verified: true`.

#### 2.2. Проверяемые источники
- Коммит `074c38cf577183e57c36f64b9e40c78bcfd870a9` (`fix: bypass KWin/GTK color management deadlock via linear extended and graceful SDR fallback`).
- Коммит `d6f8a2b8414ebcaebb0baa5509767d71873d9b2c` (`fix: model GTK Wayland color management policy and restore Rec.2100 PQ consistency`).
- Коммит `ee2d333c2645e943b4a131e0774a5ed455b6b700` (`fix: close color-management gate bypass, register main option, and add realize regression test`).
- Коммит `e41fbeb1dad3b1b463c40030efba587f2341bfb7` (`fix(validation): track Wayland registry lifetimes in trace parser`).
- Коммит `9010e5a4a473aa90ae35ef5bb22b27dacf373d6e` (`docs(validation): record GNOME trace evidence and Gate 2W hold`).
- Коммит `6a88937df082193c93e11e5423a268e87c501850` (`fix(validation): enforce multi-backend publication matrix and fixture verification`).
- Источники, удаляемые в `C1` (доступны в коммите `45fb026e891a21ceb3150808b140085b10138988`):
  - `45fb026e891a21ceb3150808b140085b10138988:docs/GPU_NEXT_DEVELOPMENT.md`, разделы `## Gate 2 start: controlled embedded-FBO pixels` и `## GNOME Wayland color-management trace capture (2026-09-24)`.
  - `45fb026e891a21ceb3150808b140085b10138988:docs/MODEL_USAGE_LOG.md`, разделы `### 2026-09-24 Wayland trace parser correction (offline)`, `### 2026-09-24 Target GNOME Wayland trace capture and Gate 2W audit`, `### 2026-09-26 Target GNOME Wayland trace capture Attempt 3 and Gate 2W narrow row acceptance`, `### 2026-10-04 Gate 2W Run 4 offline remediation of Astra high review blockers`.
  - `45fb026e891a21ceb3150808b140085b10138988:docs/adr/0006-bounded-validation-governance.md`, раздел `## Gate 2W multi-phase provenance and execution amendment (2026-10-04)`.

#### 2.3. Фактические текущие файлы и функции
- `tools/validation/parse_wayland_color_trace.py` *(до `C2a`: `tests/parse_wayland_color_trace.py`)*:
  - `validate_trace(trace: str)`
  - `validate_composite_window_trace(trace: str, expected_app_id="io.github.rusmikev.CineHDR")`
  - `validate_multiphase_trace(trace: str, expected_app_id="io.github.rusmikev.CineHDR")`
  - `create_evidence_report(trace_path, publication_report_path, backend, *, oracle=None, multiphase=False, expected_app_id=None)`
  - `create_failure_evidence(trace_path, publication_report_path, backend, error, *, oracle=None, multiphase=False)`
  - `main(argv)` — CLI с обязательными аргументами `--trace`, `--publication-report`, `--backend {legacy,gpu-next}`, `--output`, опциональным выбором ожидаемого приложения `--app-id` (по умолчанию `io.github.rusmikev.CineHDR`) и опциональным выбором оракула `--oracle {composite_window,primary_composite,strict_multiphase,legacy}` (или `--multiphase` как синонимом `--oracle strict_multiphase`).
- `src/gtk_cm_policy.py`: `COLOR_MGMT_OPT_IN_ENV = "CINEHDR_EXPERIMENTAL_COLOR_MGMT"`, `COLOR_MGMT_OPT_IN_FLAG = "--experimental-color-mgmt"`, `CmCaps`, `gtk_cm_opted_in`, `apply_color_mgmt_opt_in`, `apply_gsk_renderer_policy`, `gtk_color_managed`, `gtk_can_tag_hdr`, `preferred_gsk_renderer`.
- `tools/validation/test_wayland_color_trace.py` *(до `C2a`: `tests/test_wayland_color_trace.py`)*: классы `TestGtkCmPolicy`, `WaylandColorTraceTests`.

#### 2.4. Карта «дефект / контроль → конкретный регрессионный тест»
- **Дефект 2A (политика opt-in `GDK_DEBUG=color-mgmt` и блокировка HDR на KWin 6.7.4 без `TF srgb`, в том числе после `invalidate_hdr_support_cache` на `realize`)**:
  - `TestGtkCmPolicy.test_gtk_cm_opted_in` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_gtk_color_managed_requires_opt_in` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_gtk_color_managed_kwin_missing_srgb` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_regression_kwin_caps_after_invalidate_blocks_hdr` (`tools/validation/test_wayland_color_trace.py`)
- **Дефект 2B (отсев приватного probe-трафика `wayland_cm_probe` и защита от переиспользования object ID / `wl_registry` / чужих `delete_id`)**:
  - `WaylandColorTraceTests.test_valid_trace_selects_a_complete_manager_not_private_probe_traffic` (`tools/validation/test_wayland_color_trace.py`)
  - `WaylandColorTraceTests.test_early_bind_survives_later_registry_id_reuse` (`tools/validation/test_wayland_color_trace.py`)
  - `WaylandColorTraceTests.test_reused_registry_or_chain_object_id_is_ambiguous` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_composite_window_rejects_id_reuse_with_foreign_callback_delete_id` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_composite_window_rejects_ambiguous_delete_id_with_alien_destruction` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_composite_window_rejects_foreign_registry_surface` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_composite_window_rejects_foreign_manager_description` (`tools/validation/test_wayland_color_trace.py`)
- **Дефект 2C (проверка subsurface-цепочки и чередования `SDR → HDR → SDR → HDR`, отбраковка HLG `tf=13` для PQ)**:
  - `TestGtkCmPolicy.test_multiphase_trace_four_phases_on_subsurface_with_buffers_passes` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_multiphase_trace_four_phases_on_toplevel_without_subsurface_chain_returns_unknown` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_multiphase_trace_hlg_transfer_rejected_for_pq` (`tools/validation/test_wayland_color_trace.py`)
- **Позитивный контроль композитного окна (включая реплей трассы Attempt 3 `wayland-trace-Z4GghM`) и синтетические отрицательные контроли протокола**:
  - `TestGtkCmPolicy.test_composite_window_synthetic_positive` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_composite_window_attempt3_positive` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_composite_window_rejects_wrong_surface` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_composite_window_rejects_wrong_order` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_composite_window_rejects_unset_image_description_before_commit` (`tools/validation/test_wayland_color_trace.py`)
  - `WaylandColorTraceTests.test_ready_after_set_and_wrong_surface_commit_are_rejected` (`tools/validation/test_wayland_color_trace.py`)
  - `WaylandColorTraceTests.test_failed_description_display_error_and_protocol_disconnect_are_rejected` (`tools/validation/test_wayland_color_trace.py`)
  - `WaylandColorTraceTests.test_ready2_is_accepted_and_records_the_64_bit_identity` (`tools/validation/test_wayland_color_trace.py`)
- **Контроли выбора ожидаемого `app_id` (параметризация поверхностей, изоляция приложений, валидация аргумента, порядок цепочек в strict_multiphase и несовместимость с legacy-оракулом)**:
  - `TestGtkCmPolicy.test_composite_window_with_custom_app_id_and_default_fallback` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_multiphase_with_custom_app_id_and_default_fallback` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_multiphase_with_foreign_and_target_chains_ordering_and_completeness` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_multi_app_trace_selects_only_specified_app` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_cli_and_report_propagate_app_id` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_empty_app_id_and_legacy_combination_rejected` (`tools/validation/test_wayland_color_trace.py`)
- **Классификация именованных трансферов в `_extract_image_descriptions_info` (`semantic_class: hdr | sdr | unknown`, изоляция описаний и извлечение через creator и info-объект)**:
  - `TestGtkCmPolicy.test_extract_image_descriptions_distinguishes_pq_and_hlg` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_extract_image_descriptions_pq_hlg_with_other_primaries_semantic_class_hdr` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_extract_image_descriptions_sdr_and_wcg_sdr_transfers` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_extract_image_descriptions_primaries_only_unknown_tf_and_ext_linear` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_extract_image_descriptions_via_creator_and_get_information` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_extract_image_descriptions_neighboring_description_metadata_isolation` (`tools/validation/test_wayland_color_trace.py`)
- **Выбор действительно применяемого описания цвета при commit и отмена кандидатов в composite_window (double-buffered семантика, замена set, отмена через unset/destroy интерфейса поверхности, copy semantics дескриптора и изоляция поверхностей)**:
  - `TestGtkCmPolicy.test_composite_window_selects_subsequent_effective_description` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_composite_window_rejects_replaced_description_when_subsequent_set_is_invalid` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_composite_window_rejects_color_surface_destroy_before_commit` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_composite_window_preserves_candidate_when_image_description_destroyed_after_set` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_composite_window_operations_on_other_surface_do_not_cancel_candidate` (`tools/validation/test_wayland_color_trace.py`)
- **Проверка семантики HDR параметрических описаний в composite_window (C2a, ревизия `wayland-composite-window-hdr-submission-v1`)**:
  - `TestGtkCmPolicy.test_composite_window_accepts_parametric_pq_and_hlg` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_composite_window_rejects_sdr_transfers` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_composite_window_rejects_wcg_sdr_transfer` (`tools/validation/test_wayland_color_trace.py`)
  - `TestTraceOnlyCliValidation.test_trace_only_hlg_positive_and_wcg_sdr_negative_effective_commit_controls` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_composite_window_unrevealed_or_unknown_transfer_returns_unknown` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_composite_window_ext_linear_returns_unknown` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_composite_window_parameters_after_create_returns_unknown` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_composite_window_parameters_on_foreign_creator_not_transferred` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_composite_window_hdr_replaced_by_sdr_before_commit_rejects` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_composite_window_early_sdr_commit_does_not_hide_subsequent_hdr_commit` (`tools/validation/test_wayland_color_trace.py`)
- **Защита от ложных PASS параметрических описаний в composite_window (C2a, Дефект 2F)**:
  - `TestGtkCmPolicy.test_composite_window_tf_power_with_named_tf_returns_unknown` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_composite_window_missing_primaries_returns_unknown` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_composite_window_broken_or_unknown_primaries_returns_unknown` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_composite_window_custom_set_primaries_returns_unknown` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_composite_window_early_unknown_commit_does_not_hide_subsequent_hdr_commit` (`tools/validation/test_wayland_color_trace.py`)
- **Распознавание предопределённого HDR-описания Windows-scRGB в composite_window (C2a, решение D7, AGENTS §8)**:
  - `TestGtkCmPolicy.test_composite_window_windows_scrgb_positive` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_composite_window_windows_scrgb_negative_and_fallback_controls` (`tools/validation/test_wayland_color_trace.py`)
- **Распознавание параметрического HDR-описания ext_linear с проверенными set_luminances в composite_window (C2a, AGENTS §8)**:
  - `TestGtkCmPolicy.test_composite_window_ext_linear_luminances_positive` (`tools/validation/test_wayland_color_trace.py`)
  - `TestGtkCmPolicy.test_composite_window_ext_linear_luminances_negative_and_fallback_controls` (`tools/validation/test_wayland_color_trace.py`)
- **Поколенческий учёт ID объектов wp_color_manager_v1, связность мульти-реестров и селективная активность subsurfaces (2026-10-24 — 2026-10-25)**:
  - `TestTraceOnlyCli.test_trace_only_synthetic_subsurface_detected_returns_unknown` (`tools/validation/test_wayland_color_trace.py`)
  - `TestTraceOnlyCli.test_trace_only_synthetic_declared_unused_subsurface_passes` (`tools/validation/test_wayland_color_trace.py`)
  - `TestTraceOnlyCli.test_trace_only_synthetic_subsurface_reparented_after_commit_rejected` (`tools/validation/test_wayland_color_trace.py`)
  - `TestTraceOnlyCli.test_trace_only_synthetic_reused_manager_id_same_registry_passes` (`tools/validation/test_wayland_color_trace.py`)
  - `TestTraceOnlyCli.test_trace_only_synthetic_distinct_registries_unproven_connection_returns_unknown` (`tools/validation/test_wayland_color_trace.py`)
  - `TestTraceOnlyCli.test_trace_only_synthetic_concurrent_manager_id_collision_rejected` (`tools/validation/test_wayland_color_trace.py`)
  - `TestTraceOnlyCli.test_trace_only_synthetic_concurrent_surface_id_collision_rejected` (`tools/validation/test_wayland_color_trace.py`)
  - `TestTraceOnlyCli.test_trace_only_synthetic_manager_delete_id_before_use_rejected` (`tools/validation/test_wayland_color_trace.py`)
  - `TestTraceOnlyCli.test_trace_only_synthetic_concurrent_cross_interface_id_collision_rejected` (`tools/validation/test_wayland_color_trace.py`)
  - `TestTraceOnlyCli.test_trace_only_synthetic_foreign_registry_unlinked_rejected` (`tools/validation/test_wayland_color_trace.py`)
  - `TestTraceOnlyCliValidation.test_trace_only_synthetic_missing_registry_creation_returns_unknown` (`tools/validation/test_wayland_color_trace.py`)
  - `TestTraceOnlyCliValidation.test_trace_only_synthetic_duplicate_registry_creation_returns_unknown` (`tools/validation/test_wayland_color_trace.py`)
- **Маркировка соединений Wayland в многопоточном/мульти-очередном клиенте (2026-10-25)**:
  - `TestWaylandConnTagger.test_tagged_multi_connection_execution` (`tools/validation/test_wayland_conn_tagger.py`)
  - `TestWaylandConnTagger.test_untagged_execution_negative_control` (`tools/validation/test_wayland_conn_tagger.py`)
  - `TestWaylandConnTagger.test_multithreaded_atomic_tagging_stress` (`tools/validation/test_wayland_conn_tagger.py`)
- **Автоматический выбор и строгая валидация маркированных соединений в парсере (2026-10-25)**:
  - `TestTraceOnlyCliValidation.test_trace_only_untagged_trace_returns_unknown` (`tools/validation/test_wayland_color_trace.py`)
  - `TestTraceOnlyCliValidation.test_composite_window_untagged_trace_passes_in_standard_or_l1_mode` (`tools/validation/test_wayland_color_trace.py`)
  - `TestTaggedConnectionTraceValidation.test_multi_connection_tagged_trace_auto_selects_and_passes` (`tools/validation/test_wayland_color_trace.py`)
  - `TestTaggedConnectionTraceValidation.test_tagged_trace_missing_tag_returns_unknown` (`tools/validation/test_wayland_color_trace.py`)
  - `TestTaggedConnectionTraceValidation.test_tagged_trace_untracked_tag_returns_unknown` (`tools/validation/test_wayland_color_trace.py`)
  - `TestTaggedConnectionTraceValidation.test_tagged_trace_tampered_commit_tag_fails` (`tools/validation/test_wayland_color_trace.py`)
  - `TestTaggedConnectionTraceValidation.test_tagged_trace_unmatched_app_id_returns_unknown` (`tools/validation/test_wayland_color_trace.py`)
  - `TestTaggedConnectionTraceValidation.test_tagged_trace_conflicting_app_ids_returns_unknown` (`tools/validation/test_wayland_color_trace.py`)
  - `TestTaggedConnectionTraceValidation.test_tagged_trace_inconsistent_fd_for_same_conn_id_returns_unknown` (`tools/validation/test_wayland_color_trace.py`)
  - `TestTaggedConnectionTraceValidation.test_tagged_trace_missing_fd_on_protocol_message_returns_unknown` (`tools/validation/test_wayland_color_trace.py`)

#### 2.5. Команда воспроизведения и предусловия
- **Предусловия для юнит-тестов парсера**: стандартный Python 3 (без GPU и без запущенного композитора Wayland).
- **Команда воспроизведения юнит-тестов (после переноса `C2a`)**:
  ```bash
  python3 -m unittest tools/validation/test_wayland_color_trace.py -v
  python3 tools/validation/test_wayland_conn_tagger.py -v
  ```
- **Команда запуска CLI парсера на сохранённом логе трассы и отчёте публикации**:
  ```bash
  python3 tools/validation/parse_wayland_color_trace.py \
    --trace /path/to/wayland-client.raw.log \
    --publication-report /path/to/gate2-wayland-publication-gpu-next.txt \
    --backend gpu-next \
    --oracle composite_window \
    --output /path/to/gate2-wayland-color-management-gpu-next.json
  ```
  *(Доступные значения `--oracle`: `composite_window`, `primary_composite`, `strict_multiphase`, `legacy`; флаг `--multiphase` эквивалентен `--oracle strict_multiphase`. Опция `--app-id <app_id>` позволяет переопределить ожидаемый идентификатор приложения, по умолчанию `io.github.rusmikev.CineHDR`.)*

- **Регламент и команды эталонного запуска mpv с маркировкой соединений (P1.3, шаг 0)**:
  Владелец выполняет запуск нативного mpv в активной сессии Wayland (GNOME-AMD с включённым HDR) с использованием interposer-шима `wayland_conn_tagger.c`. Парсер автоматически атрибутирует соединение по `app_id="mpv"` без ручной фильтрации лога по `conn#`.

  1. **Сборка библиотеки-перехватчика и тестового стенда**:
     ```bash
     gcc -Wall -Wextra -shared -fPIC -o /tmp/libwayland_conn_tag.so tools/validation/wayland_conn_tagger.c -ldl -lwayland-client
     gcc -Wall -Wextra -o /tmp/test_conn_tagger_harness tools/validation/test_conn_tagger_harness.c -lwayland-client
     ```

  2. **Автономная самопроверка шима (без дисплея)**:
     ```bash
     python3 tools/validation/test_wayland_conn_tagger.py
     ```
     *(Убедиться, что пройдены все 3 теста: базовая маркировка двух соединений, отрицательный контроль без LD_PRELOAD и многопоточный стресс-тест атомарности вывода).*

  3. **Preflight: определение и проверка синтетического HDR10-паттерна**:
     Перед запуском проверяется наличие и целостность подготовленного эталонного файла (без привязки к конкретному каталогу рабочей копии):
     ```bash
     EXPECTED_SHA="cdee467e93298eb5bad11c408edbe1138edf046bc845580fc97ae0f250eb4b50"
     COMMON_DIR="$(git rev-parse --git-common-dir 2>/dev/null)"
     REPO_ROOT="$(dirname "$COMMON_DIR")"
     CANDIDATES=(
       "${PATTERN_PATH:-}"
       "validation-reports/fixtures/cinehdr-gate2-hdr.mkv"
       "${REPO_ROOT}/validation-reports/fixtures/cinehdr-gate2-hdr.mkv"
       "../validation-reports/fixtures/cinehdr-gate2-hdr.mkv"
     )
     RESOLVED_PATTERN=""
     for cand in "${CANDIDATES[@]}"; do
       if [ -n "$cand" ] && [ -r "$cand" ]; then
         sha=$(sha256sum "$cand" | awk '{print $1}')
         if [ "$sha" = "$EXPECTED_SHA" ]; then
           RESOLVED_PATTERN="$cand"
           break
         fi
       fi
     done

     if [ -z "$RESOLVED_PATTERN" ]; then
       echo "ОШИБКА: синтетический HDR10-паттерн с SHA-256 $EXPECTED_SHA не найден." >&2
       echo "Укажите путь явно: export PATTERN_PATH=/path/to/cinehdr-gate2-hdr.mkv" >&2
       exit 1
     fi
     echo "Используется проверенный паттерн: $RESOLVED_PATTERN"
     ```

  4. **Запуск эталонного нативного mpv в цикле (--loop-file=inf)**:
     ```bash
     mkdir -p validation-reports
     TS=$(date +%Y%m%dT%H%M%SZ)
     WAYLAND_DEBUG=1 LD_PRELOAD=/tmp/libwayland_conn_tag.so \
       mpv --vo=gpu-next --gpu-api=vulkan --gpu-context=waylandvk \
           --target-colorspace-hint=yes --loop-file=inf --msg-level=vo=v \
           --log-file="validation-reports/${TS}-mpv-runtime.log" \
           "$RESOLVED_PATTERN" \
           2> "validation-reports/${TS}-mpv-tagged-wayland.log"
     ```
     *(Паттерн: проверенный синтетический HDR10-файл `cinehdr-gate2-hdr.mkv` [Rec.2100 PQ, BT.2020, 10-бит, sha256: `cdee467e93298eb5bad11c408edbe1138edf046bc845580fc97ae0f250eb4b50`]. Параметр `--loop-file=inf` зацикливает 1-секундный паттерн, позволяя оператору спокойно убедиться в открытии окна и появлении видео, после чего окно закрывается клавишей `q`).*

  5. **Быстрая проверка наличия меток и селекторов в логах**:
     ```bash
     # Проверить, что лог не пуст и содержит маркированные строки [conn#... fd#...]
     head -n 20 "validation-reports/${TS}-mpv-tagged-wayland.log"
     grep -c '\[conn#' "validation-reports/${TS}-mpv-tagged-wayland.log"

     # Проверить в runtime-логе mpv выбор бэкенда gpu-next, Vulkan и HDR10 swapchain
     grep -E 'gpu-next|waylandvk|VK_COLOR_SPACE|surface caps' "validation-reports/${TS}-mpv-runtime.log"
     ```

  6. **Автономная валидация маркированной трассы парсером (--trace-only)**:
     ```bash
     python3 tools/validation/parse_wayland_color_trace.py \
       --trace-only \
       --trace "validation-reports/${TS}-mpv-tagged-wayland.log" \
       --app-id mpv \
       --oracle composite_window \
       --output "validation-reports/${TS}-mpv-l3p.json"
     ```
     *(Парсер автоматически изолирует соединение `conn#`, на котором вызван `xdg_toplevel.set_app_id("mpv")`, проверит инвариантность дескриптора сокета `fd` и выполнит протокольный аудит double-buffered HDR-описания).*

  7. **Что возвращает владелец в зависимости от результата**:
     - **При PASS (код выхода 0)**:
       - SHA-256 файлов `validation-reports/${TS}-mpv-tagged-wayland.log` и `validation-reports/${TS}-mpv-runtime.log`;
       - JSON-отчёт `validation-reports/${TS}-mpv-l3p.json`;
       - Строки из `validation-reports/${TS}-mpv-runtime.log` с подтверждением `waylandvk`, устройства Vulkan и формата swapchain (`VK_COLOR_SPACE_HDR10_ST2084_EXT`);
       - *Внимание: PASS трассы доказывает только L3p-протокольную передачу HDR-описания композитору поверхностью mpv. Он НЕ подтверждает физический HDR-вывод монитором и НЕ является закрытием L4 (визуальное сравнение L4 на ступенях паттерна проводится отдельно).*
     - **При UNKNOWN (код выхода 1)**:
       - SHA-256 и размер обоих логов;
       - Значение поля `"reason"` из `validation-reports/${TS}-mpv-l3p.json` и вывод ошибки из `stderr` (например, отсутствие меток, расхождение `fd`, неоднозначность app-id или несоответствие структуры);
       - Первые 50 строк маркированного лога и строки `xdg_toplevel.set_app_id` для анализа причин неоднозначности.
     - **При FAIL (код выхода 1)**:
       - SHA-256 обоих логов;
       - Полный `validation-reports/${TS}-mpv-l3p.json` и вывод `stderr`;
       - Зафиксированную причину протокольного отказа (например, отказ в HDR-трансфере, прикрепление SDR-описания или ошибка протокола).

- **Регламент и команды протокольного захвата L3p для CineHDR в Flatpak (GNOME-AMD, P1.3, шаг 1)**:
  Владелец выполняет запуск CineHDR в установленной Flatpak-песочнице (`io.github.rusmikev.CineHDR`) на стенде GNOME-AMD с включённым HDR. Ожидаемый рендер-путь — композитный toplevel (`graphics-offload` выключен, режим `hdr-mode=auto`). Парсер автоматически атрибутирует соединение по `--app-id io.github.rusmikev.CineHDR`.

  1. **Преодоление границы Flatpak-песочницы для `wayland_conn_tagger`**:
     - **Совместимость библиотек**: перехватчик `wayland_conn_tagger.c` компилируется под хостовым `gcc` (при glibc ≥ 2.34, совместимом с Flatpak Platform 50) либо герметично внутри SDK-рантайма:
       ```bash
       # Вариант A (хостовый компилятор):
       gcc -Wall -Wextra -shared -fPIC -o /tmp/libwayland_conn_tag.so tools/validation/wayland_conn_tagger.c -ldl -lwayland-client

       # Вариант B (герметичная сборка внутри Flatpak Sdk 50):
       flatpak run --user --filesystem="$(pwd)":ro --filesystem=/tmp:rw --command=gcc org.gnome.Sdk//50 \
         -Wall -Wextra -shared -fPIC -o /tmp/libwayland_conn_tag.so "$(pwd)/tools/validation/wayland_conn_tagger.c" -ldl -lwayland-client
       ```
     - **Автономная самопроверка шима**:
       ```bash
       python3 tools/validation/test_wayland_conn_tagger.py
       ```
     - **Проброс файлов и окружения в sandbox**:
       1. Путь `/tmp` внутри песочницы изолирован (tmpfs). Файл перехватчика пробрасывается явно через флаг `--filesystem=/tmp/libwayland_conn_tag.so:ro`;
       2. Переменные `LD_PRELOAD` и `WAYLAND_DEBUG` не наследуются из хостовой среды и передаются через аргументы `--env=LD_PRELOAD=/tmp/libwayland_conn_tag.so` и `--env=WAYLAND_DEBUG=1`;
       3. Переменная `--env=CINEHDR_NON_UNIQUE=1` предотвращает передачу файла уже запущенному пользовательскому экземпляру CineHDR через D-Bus activation;
       4. Переменные `--env=CINEHDR_EXPERIMENTAL_COLOR_MGMT=1` и `--env=GDK_DEBUG=color-mgmt` включают Wayland Color Management в GTK.

  2. **Подготовка фикстур (HEVC Main10, закольцовывание в 60 с и верификация)**:
     При использовании профиля `--fixture-profile hevc-main10-yuv420p10-lossless` генератор создаёт видеофайлы с расширением `.mp4` (не `.mkv`):
     `validation-reports/fixtures/cinehdr-gate2-hevc-main10-yuv420p10-lossless-<mode>.mp4`.
     Базовая фикстура длится 1.0 с (24 кадра). Для комфортного интерактивного воспроизведения и визуального осмотра фикстура закольцовывается в 60 с без перекодирования через `-c copy`:
     ```bash
     mkdir -p validation-reports/fixtures
     # Генерация базовых 1-секундных фикстур:
     python3 tests/generate_patterns.py --fixture-profile hevc-main10-yuv420p10-lossless --mode hdr --cache-dir validation-reports/fixtures
     python3 tests/generate_patterns.py --fixture-profile hevc-main10-yuv420p10-lossless --mode hlg --cache-dir validation-reports/fixtures
     python3 tests/generate_patterns.py --fixture-profile hevc-main10-yuv420p10-lossless --mode wcg-sdr --cache-dir validation-reports/fixtures

     # Закольцовывание в 60 с (потоковая копия без пережатия):
     for MODE in hdr hlg wcg-sdr; do
       ffmpeg -y -stream_loop 59 \
         -i "validation-reports/fixtures/cinehdr-gate2-hevc-main10-yuv420p10-lossless-${MODE}.mp4" \
         -c copy \
         "validation-reports/fixtures/cinehdr-gate2-hevc-main10-yuv420p10-lossless-${MODE}-60s.mp4"
     done

     # Исполняемая автоматическая верификация закольцованных файлов через ffprobe (fail-fast):
     for MODE in hdr hlg wcg-sdr; do
       FILE="validation-reports/fixtures/cinehdr-gate2-hevc-main10-yuv420p10-lossless-${MODE}-60s.mp4"
       [ -f "$FILE" ] || { echo "FAIL-FAST: фикстура $FILE не найдена" >&2; exit 1; }

       STREAM_INFO=$(ffprobe -v error \
         -select_streams v:0 \
         -show_entries stream=codec_name,pix_fmt,color_transfer,color_primaries \
         -show_entries format=duration \
         -of default=noprint_wrappers=1:nokey=0 \
         "$FILE") || { echo "FAIL-FAST: ffprobe завершился с ошибкой на $FILE" >&2; exit 1; }

       CODEC=$(echo "$STREAM_INFO" | awk -F= '$1=="codec_name"{print $2}')
       PIX_FMT=$(echo "$STREAM_INFO" | awk -F= '$1=="pix_fmt"{print $2}')
       PRIMARIES=$(echo "$STREAM_INFO" | awk -F= '$1=="color_primaries"{print $2}')
       TRANSFER=$(echo "$STREAM_INFO" | awk -F= '$1=="color_transfer"{print $2}')
       DURATION=$(echo "$STREAM_INFO" | awk -F= '$1=="duration"{print $2}')

       [ "$CODEC" = "hevc" ] || { echo "FAIL: $FILE codec=$CODEC (ожидался hevc)" >&2; exit 1; }
       [ "$PIX_FMT" = "yuv420p10le" ] || { echo "FAIL: $FILE pix_fmt=$PIX_FMT (ожидался yuv420p10le)" >&2; exit 1; }
       [ "$PRIMARIES" = "bt2020" ] || { echo "FAIL: $FILE color_primaries=$PRIMARIES (ожидался bt2020)" >&2; exit 1; }

       case "$MODE" in
         hdr)
           [ "$TRANSFER" = "smpte2084" ] || { echo "FAIL: $FILE color_transfer=$TRANSFER (ожидался smpte2084)" >&2; exit 1; }
           ;;
         hlg)
           [ "$TRANSFER" = "arib-std-b67" ] || { echo "FAIL: $FILE color_transfer=$TRANSFER (ожидался arib-std-b67)" >&2; exit 1; }
           ;;
         wcg-sdr)
           case "$TRANSFER" in
             bt709|iec61966-2-1) ;;
             *) echo "FAIL: $FILE color_transfer=$TRANSFER (ожидался bt709 или iec61966-2-1)" >&2; exit 1 ;;
           esac
           ;;
       esac

       awk -v dur="$DURATION" 'BEGIN { if (dur < 59.9) { print "FAIL: duration=" dur " < 60s"; exit 1 } }' || exit 1
       echo "OK: $FILE проверен (codec=$CODEC, pix_fmt=$PIX_FMT, primaries=$PRIMARIES, transfer=$TRANSFER, duration=${DURATION}s)"
     done
     ```

  3. **Preflight: сохранение настроек GSettings, fail-fast и гарантированный trap восстановления**:
     > [!IMPORTANT]
     > **Безопасность конфигурации стенда**:
     > 1. Чтение исходных настроек выполняется в режиме fail-fast: при сбое чтения `graphics-offload` или `hdr-mode` выполнение немедленно прекращается без подстановки вымышленных значений (`false` / `auto`) и без записи в пользовательские параметры.
     > 2. Восстановление исходных параметров гарантируется через shell-обработчик `trap ... EXIT INT TERM`, срабатывающий при завершении, любой ошибке или прерывании по Ctrl-C.
     > 3. Автоматизированные тесты и агенты разработки не должны вызывать команды, модифицирующие реальные GSettings владельца.

     ```bash
     # Шаг 3.1: Fail-fast чтение исходных настроек пользователя (без fallback-подстановок):
     ORIG_OFFLOAD=$(flatpak run --command=gsettings io.github.rusmikev.CineHDR get io.github.rusmikev.CineHDR graphics-offload) || {
       echo "FAIL-FAST: не удалось прочитать текущее значение graphics-offload из GSettings. Выполнение остановлено без изменения настроек." >&2
       exit 1
     }
     ORIG_MODE=$(flatpak run --command=gsettings io.github.rusmikev.CineHDR get io.github.rusmikev.CineHDR hdr-mode) || {
       echo "FAIL-FAST: не удалось прочитать текущее значение hdr-mode из GSettings. Выполнение остановлено без изменения настроек." >&2
       exit 1
     }
     echo "Исходные настройки зафиксированы: graphics-offload=$ORIG_OFFLOAD, hdr-mode=$ORIG_MODE"

     # Шаг 3.2: Установка обработчика восстановления (гарантирует возврат при штатном выходе, ошибке или Ctrl-C):
     restore_gsettings() {
       local exit_code=$?
       echo "Восстановление исходных настроек GSettings (graphics-offload=$ORIG_OFFLOAD, hdr-mode=$ORIG_MODE)..."
       flatpak run --command=gsettings io.github.rusmikev.CineHDR set io.github.rusmikev.CineHDR graphics-offload "$ORIG_OFFLOAD"
       flatpak run --command=gsettings io.github.rusmikev.CineHDR set io.github.rusmikev.CineHDR hdr-mode "$ORIG_MODE"
       exit $exit_code
     }
     trap restore_gsettings EXIT INT TERM

     # Шаг 3.3: Фиксация настроек стенда для валидации: graphics-offload=false (композитный путь GTK), hdr-mode='auto'
     flatpak run --command=gsettings io.github.rusmikev.CineHDR set io.github.rusmikev.CineHDR graphics-offload false || exit 1
     flatpak run --command=gsettings io.github.rusmikev.CineHDR set io.github.rusmikev.CineHDR hdr-mode 'auto' || exit 1
     ```

  4. **Запуск CineHDR во Flatpak с захватом маркированной трассы**:
     ```bash
     mkdir -p validation-reports
     TS=$(date +%Y%m%dT%H%M%SZ)
     TEST_FILE="validation-reports/fixtures/cinehdr-gate2-hevc-main10-yuv420p10-lossless-hdr-60s.mp4"

     flatpak run \
       --filesystem=/tmp/libwayland_conn_tag.so:ro \
       --filesystem="$(realpath "$TEST_FILE")":ro \
       --env=LD_PRELOAD=/tmp/libwayland_conn_tag.so \
       --env=WAYLAND_DEBUG=1 \
       --env=CINEHDR_NON_UNIQUE=1 \
       --env=CINEHDR_EXPERIMENTAL_COLOR_MGMT=1 \
       --env=GDK_DEBUG=color-mgmt \
       io.github.rusmikev.CineHDR \
       "$(realpath "$TEST_FILE")" \
       2> "validation-reports/${TS}-cinehdr-tagged-wayland.log"
     ```
     *(Во время воспроизведения оператор открывает диалог диагностики через меню «Video Output Diagnostics», нажимает кнопку «Copy Report» и сохраняет текст отчёта в файл `validation-reports/${TS}-cinehdr-copy-report.txt`, после чего закрывает окно приложения).*

  5. **Автономная валидация трассы парсером (--trace-only)**:
     ```bash
     python3 tools/validation/parse_wayland_color_trace.py \
       --trace-only \
       --trace "validation-reports/${TS}-cinehdr-tagged-wayland.log" \
       --app-id io.github.rusmikev.CineHDR \
       --oracle composite_window \
       --output "validation-reports/${TS}-cinehdr-l3p.json"
     ```

  6. **Специфика сценариев и критерии приёмки**:
     - **HDR10 (Rec.2100 PQ)**:
       - Оракул трассы: `status: PASS` (подтверждён параметрический PQ `tf=11`);
       - Copy Report: `Auto gate: allowed`, `HDR Status: Active`, `Gdk.ColorState: Rec.2100 PQ`, `Transfer Characteristics: pq`, `Color Primaries: bt.2020`.
     - **HLG (L3p + визуальный L4)**:
       - Тестовый файл: `cinehdr-gate2-hevc-main10-yuv420p10-lossless-hlg-60s.mp4` либо визуальный паттерн `cinehdr-l4-hlg-bt2020-60s.mkv` (`python3 tests/generate_patterns.py --mode l4-hlg --cache-dir validation-reports/fixtures`);
       - Оракул трассы CineHDR: `status: PASS` с выходным PQ `tf=11`; входной HLG преобразуется в PQ-текстуру. Для эталонного mpv нативное HLG-описание может использовать `tf=13`, что оценивается отдельно;
       - Copy Report: `Transfer Characteristics: hlg` (вход), `Color Primaries: bt.2020`, `Gdk.ColorState: Rec.2100 PQ` (выход);
       - L4 визуальный оракул: относительные градации scene-light HLG `0.05, 0.18, 0.50, 0.75, 0.88, 1.0` без выдуманных нит. Верхние градации (75%, 88%, 100%) отчётливо различимы без клиппинга. Нижняя полоса — HLG reference white при **75% кодового сигнала** (10-битный код 767), между плашками 18% и 50% scene-light; сопоставимый относительный контраст с эталонным mpv на том же дисплее.
     - **Отрицательный контроль WCG-SDR (BT.2020 + BT.1886 / sRGB)**:
       - Тестовый файл: `cinehdr-gate2-hevc-main10-yuv420p10-lossless-wcg-sdr-60s.mp4`;
       - Цель: подтвердить, что контент с широким охватом BT.2020, но стандартным SDR-трансфером НЕ активирует HDR-режим при `hdr-mode=auto`;
       - Оракул трассы: **ТОЛЬКО** доказанный `status: FAIL` (код завершения 1, `error: only SDR color descriptions were committed ...`) в комбинации с SDR Copy Report (`HDR Status: Disabled`, `Gdk.ColorState: sRGB`, SDR-трансфер источника). Общий отказ `no valid HDR commits found` или отсутствие CM-событий не доказывает корректный отрицательный контроль;
       - **ВНИМАНИЕ**: статус `UNKNOWN` (неполная трасса, сбой шима или нераспознанный app-id) **НЕ является подтверждением отрицательного контроля** и свидетельствует о невалидности эксперимента.

  7. **Восстановление исходных параметров GSettings**:
     При использовании скрипта с шага 3 исходные параметры `graphics-offload` и `hdr-mode` восстанавливаются автоматически через установленный `trap restore_gsettings EXIT INT TERM` как при успешном завершении, так и при ошибке любой команды или нажатии Ctrl-C.
     Для ручного восстановления (или в случае аварийного завершения оболочки):
     ```bash
     # Ручной вызов восстановления исходных параметров:
     flatpak run --command=gsettings io.github.rusmikev.CineHDR set io.github.rusmikev.CineHDR graphics-offload "$ORIG_OFFLOAD"
     flatpak run --command=gsettings io.github.rusmikev.CineHDR set io.github.rusmikev.CineHDR hdr-mode "$ORIG_MODE"
     ```

  8. **Что возвращает владелец**:
     - **При PASS (код выхода 0)**:
       - SHA-256 файлов `validation-reports/${TS}-cinehdr-tagged-wayland.log` и `validation-reports/${TS}-cinehdr-copy-report.txt`;
       - JSON-отчёт `validation-reports/${TS}-cinehdr-l3p.json`;
       - Содержимое `validation-reports/${TS}-cinehdr-copy-report.txt`;
       - *Внимание: PASS трассы доказывает только L3p-протокольную передачу HDR-описания композитору поверхностью CineHDR. Он НЕ подтверждает физический HDR-вывод монитором и НЕ является закрытием L4 (визуальное сравнение L4 на ступенях паттерна проводится отдельно).*
     - **При UNKNOWN / FAIL (код выхода 1)**:
       - SHA-256 и размер обоих логов;
       - Значение поля `"reason"` из `validation-reports/${TS}-cinehdr-l3p.json` и вывод ошибки из `stderr`;
       - Первые 50 строк маркированного лога и строки `xdg_toplevel.set_app_id` для анализа причин.

#### 2.6. Что результат доказывает и чего НЕ доказывает
- **Что доказывают модульные тесты (`tools/validation/test_wayland_color_trace.py`)**:
  - Корректность грамматики и конечного автомата парсера на синтетических трассах (и сохранённом логе `wayland-trace-Z4GghM`), а также логику capability-гейта `gtk_color_managed()` на моках `CmCaps`.
  - Корректность извлечения и внутренней классификации семантики именованных трансферов (`semantic_class: hdr | sdr | unknown`) в `_extract_image_descriptions_info` через параметрический creator и info-объект `wp_image_description_info_v1`, а также взаимную изоляцию метаданных между дескрипторами.
  - Корректность выбора действительно применяемого описания цвета при commit в `composite_window` по double-buffered семантике Wayland (`color-management-v1.xml` строки 713–718, 730–778, 780–789): отмену кандидата при последующем `set_image_description` (в том числе некорректном/неподдерживаемом), `unset_image_description` или `destroy` интерфейса `wp_color_management_surface_v1`, сохранение кандидата при `destroy` самого `wp_image_description_v1` (copy semantics) и независимость от операций на других поверхностях.
  - Соблюдение строгого контракта HDR-семантики в `composite_window` (ревизия `wayland-composite-window-hdr-submission-v1`): допуск к статусу `PASS` только подтверждённого коммитом параметрического описания с распознанным HDR-трансфером (PQ `tf=11`, HLG `tf=13`), отказ при публикации только SDR (BT.1886, sRGB, gamma 2.2, WCG-SDR BT.2020+BT.1886), возврат `UNKNOWN` при наличии нераскрытых или неподдерживаемых описаний (`ext_linear`, вызов параметров после `create()`, чужие creator'ы), а также сохранение HDR PASS при наличии ранних SDR/UNKNOWN коммитов перед более поздним HDR коммитом.
- **Что доказывает прогон CLI парсера на живом логе `WAYLAND_DEBUG=client` (`create_evidence_report`)**:
  - При `PASS` выбранного оракула в переданной трассе найдена цепочка manager → ready → set → commit. Для `composite_window` поверхность определяется по app_id и проверяется семантика HDR параметрического описания; `strict_multiphase` дополнительно проверяет четыре фазы и цепочку subsurface/viewport/attach. Отчёт содержит SHA-256 обоих входных файлов и проверку строк `Overall result: PASS` / `Expected backend` в отчёте публикации.
  - Семантика HDR подключена к оракулу `composite_window` для параметрических описаний, созданных клиентом через `create_parametric_creator → параметры → create`: оракул требует подтверждённого коммитом HDR-трансфера (PQ, HLG) для статуса `PASS`. Для описаний с нераскрытыми параметрами, `ext_linear`, чужими creator'ами или сведениями только из `get_information` возвращается статус `UNKNOWN`. Наличие `PASS` по `composite_window` доказывает факт публикации хотя бы одного подтверждённого HDR-описания на окне приложения, но не доказывает непрерывный HDR, соответствие пикселей описанию, трансформацию композитора или физический вывод дисплея. Полный уровень L3p и этап C2 остаются в статусе `IN PROGRESS` (предстоит расширение на другие представления HDR из AGENTS §8).
  - `_fatal_error()` сейчас отклоняет весь лог при найденных `failed` / `wl_display.error`, без разделения соединений. Единый stderr не содержит надёжного идентификатора соединения: проверки происхождения объектов и запрет неоднозначного переиспользования ID не доказывают изоляцию всех соединений. Уточнение области ошибок по правилам также относится к C2a.
- **Автономный L3p-режим CLI для эталонного mpv (`--trace-only`) и границы доказательства**:
  - В автономном режиме `--trace-only` парсер проверяет одну сырую трассу Wayland для окна с явно заданным идентификатором `--app-id <app_id>` (для эталонного mpv: `--app-id mpv`) через существующий оракул `composite_window`. Отчёт публикации CineHDR (`--publication-report`) и тип бэкенда (`--backend`) в этом режиме не требуются и не подменяются фиктивными значениями.
  - Результирующий JSON содержит явные маркеры автономного протокольного доказательства: `"protocol_evidence_level": "L3p"`, `"trace_only": true`, а также SHA-256 хеш сырой трассы. Код завершения CLI `0` возвращается **только** при статусе `PASS`; статус `UNKNOWN` (неполная/нераскрытая трасса или неоднозначность видеоповерхности) и отказ (`FAIL` / ошибка протокола) возвращают ненулевой код с выводом понятной причины в `stderr`.
  - В failure JSON поля `expected_app_id` и `observed_app_id` / `observed_app_ids` строго разделены, что исключает выдачу ожидаемого идентификатора за факт при несовпадении или отсутствии окон. Поле `scope` для статуса `UNKNOWN` не содержит утверждений о CineHDR или «PASS proves» и нейтрально фиксирует неполноту или неоднозначность протокольных доказательств.
  - **Граница доказательства эталона**:
    - `PASS` доказывает исключительно то, что на установленной парсером поверхности Wayland целевого приложения (с подтверждённым `set_app_id`) зафиксировано распознанное HDR-описание изображения (параметрический PQ/HLG, предопределённый Windows scRGB или параметрический `ext_linear` с проверенными `set_luminances`), получение события `ready`/`ready2` до `set_image_description`, корректный вызов `set_image_description`, активный ненулевой буфер на этой же поверхности окна в момент выбранного HDR `commit_index` с учётом double-buffered семантики `wl_surface.attach` и последующий `wl_surface.commit` без ошибок протокола, а также отсутствие неоднозначности переноса видео на subsurface.
    - Атрибуция по `app_id` доказывает принадлежность окна целевому приложению, но сама по себе **не доказывает**, что окно несёт видеоповерхность: для доказательства `PASS` обязателен подтверждённый ненулевой буфер, эффективный на выбранном HDR-коммите (`effective_buffer_id`), и отсутствие активных дочерних подповерхностей (`subsurfaces`). По double-buffered контракту Wayland `attach` после выбранного коммита не может подтверждать его; `attach(nil, ...)` (в libwayland 1.26.0 форматируется как `nil`) до коммита отменяет ожидающий буфер; ранее закоммиченный буфер сохраняется эффективным на последующих коммитах, если не был снят или перезаписан. Если эффективный ненулевой буфер на момент коммита отсутствует или обнаружен только на других поверхностях, трасса бракуется (`FAIL`). Если у окна обнаружены `subsurfaces`, парсер проверяет, несёт ли хотя бы одна дочерняя подповерхность эффективный или ожидающий ненулевой буфер в момент выбранного коммита: если подповерхность активна, происхождение видеоповерхности неоднозначно и парсер возвращает статус `UNKNOWN` с точной причиной; если подповерхности объявлены, но не имеют прикреплённых буферов (например, неиспользуемые OSD-слои), видеоповерхность однозначно принадлежит toplevel-окну и трасса допускается к `PASS`.
    - В результирующем JSON поле `matched.surface.effective_buffer_id` фиксирует целочисленный идентификатор буфера, эффективного именно на выбранном HDR-коммите, а не общий список всех буферов трассы.
    - **Связь нескольких реестров на одном соединении**: в приложениях, использующих раздельные очереди событий (например, Mesa Vulkan WSI в mpv с бэкендом `waylandvk`), композитор и менеджер цветов могут создаваться из разных объектов `wl_registry` (например, `wl_registry#2` и `wl_registry#52`). Парсер связывает разные реестры **только** в том случае, когда принадлежность одному соединению строго доказана проверенными метками `[conn#<id> fd#<fd>]`, оба реестра созданы через `wl_display#1.get_registry` на этом же сокете и все протокольные вызовы изолированы в рамках одного соединения. В немаркированной или смешанной трассе совпадение числового ID `wl_display#1` не считается доказательством общего соединения и возвращает статус `UNKNOWN`.
    - `PASS` **не доказывает** физический вывод HDR на мониторе, применение метаданных композитором или отсутствие обратного тон-маппинга в SDR композитором (для подтверждения доставки фотонов на панель необходима независимая визуальная приёмка L4 по `docs/TESTING.md`).
    - `PASS` **не подтверждает** параметры запуска mpv как свершившийся факт: запрошенные флаги командной строки (`--vo=gpu-next`, `--gpu-api=vulkan`, `--gpu-context=waylandvk`) могли не примениться или откатиться в fallback; фактический контекст и API обязаны независимо подтверждаться по выводу runtime-лога mpv (`--msg-level=vo=v`), а не по факту передачи аргументов в CLI.
    - Парсер **не выдаёт предполагаемый `app_id` за наблюдённый факт**: если окно не вызвало `set_app_id` с точно совпадающим значением, парсер завершается ошибкой `TraceValidationError` (fail-closed), исключая ложный PASS от посторонних окон.
- **Чего НЕ доказывает**:
  - Что два входных файла получены из одного процесса или сессии: `create_evidence_report` самостоятельно не проверяет такую связь; для неё нужны отдельные артефакты запуска. Хеширование файлов фиксирует их содержимое, а не происхождение.
  - Какие именно пиксели были записаны в FBO до создания текстуры (это проверяет пиксельный оракул FBO, раздел 1).
  - Как композитор тон-маппит эту поверхность при композитинге и какова физическая яркость панели (уровень L4).

#### 2.7. Точки подключения в продукте
- В горячем рендер-пути продукта точек подключения **нет**: трасса собирается внешне через стандартную переменную окружения libwayland `WAYLAND_DEBUG=client` (или `WAYLAND_DEBUG=1`) из stderr клиентского процесса.
- При старте процесса в `src/cinehdr.in` и `run_dev.py` (до импорта `gi.repository.Gtk`, с проверкой и регистрацией флага в `src/main.py`) функция `apply_color_mgmt_opt_in(os.environ, sys.argv, gtk_initialized=...)` из `src/gtk_cm_policy.py` добавляет `color-mgmt` в `GDK_DEBUG`, если задана переменная окружения `CINEHDR_EXPERIMENTAL_COLOR_MGMT=1` (`COLOR_MGMT_OPT_IN_ENV`) или передан CLI-флаг `--experimental-color-mgmt` (`COLOR_MGMT_OPT_IN_FLAG`).

---

### 3. Паузовый сценарий `SDR → HDR → SDR`, первый опубликованный кадр и чистое завершение (`publication`)

#### 3.1. Защищаемый инвариант и конкретные дефекты
- **Защищаемый инвариант (`AGENTS.md` §4.1, §4.4, `docs/adr/0001-gpu-next-render-api.md`, `docs/adr/0007-paused-hdr-output-republication.md`)**:
  1. Изолированный процесс валидации (`Gio.ApplicationFlags.NON_UNIQUE`) запускается в режиме `publication` строго на паузе (`pause=True`), без восстановления пользовательской сессии (`restore_last_playlist` и `quit_watch_later` отключены), с отключённым автоподхватом соседей (`autocreate-playlist=no`), декодером `hwdec=vaapi-copy` и точным списком из двух проверенных по SHA-256 фикстур (`playlist-pos=0` — SDR BT.709, `playlist-pos=1` — HDR10 PQ).
  2. Сценарий проходит 4 шага (`(0, "sdr")` → `(1, "hdr")` → `(0, "sdr")` → `(1, "hdr")`) в одном контексте рендера (`render_context_generation` не меняется) без пересоздания `MpvRenderContext`. На каждом шаге доказывается публикация **нового** кадра (`render_frame_generation` строго возрастает), совпадение размеров `Gdk.GLTexture` с ожидаемым дробным масштабом Wayland (`round(widget_w * scale)` × `round(widget_h * scale)`), соответствие формата/цветового состояния текстуры и прохождение пиксельного оракула FBO.
  3. При закрытии окна (`CineWindow.do_close_request`) контекст рендера libmpv освобождается строго **до** остановки ядра mpv (`_video_area.shutdown_render_context()` → `mpv_render_context_free()` → `mpv.quit()` / `wait_for_shutdown()`), а внешний супервизор `run_wayland_publication_validation.sh` никогда не останавливает пользовательские экземпляры CineHDR (`user_instances`) и не затирает финальный `cleanup_status.json`.
- **Конкретные исторические дефекты и отрицательные контроли**:
  1. **Дефект 3A (снятие паузы в `CineApplication.do_open` при открытии фикстуры в режиме `publication`, `2026-10-04`)**:
     Стандартный `CineApplication.do_open()` после `loadfile` устанавливал `win.mpv.pause = False`, из-за чего паузовый контракт сценария `publication` нарушался сразу при старте, а смена файлов происходила во время воспроизведения вместо детерминированного паузового кадра. Исправлено функцией `_uses_publication_fixture_contract(mode)` в `src/main.py` и проверкой `pause=True` / `playlist-pos=0` в `GpuValidationSession._wait_until_ready()`.
  2. **Дефект 3B (загрязнение валидации пользовательским плейлистом `autocreate-playlist=filter`, `watch_later` и прямым `hwdec=vaapi`, `2026-08-22` — `2026-08-23`)**:
     В ранних прогонах Gate 1/Gate 2 пользовательские настройки `autocreate-playlist=filter` и `restore-last-playlist` подгружали посторонние файлы из каталога пользователя, а `hwdec=auto` выбирал прямой `vaapi`, тогда как для сопоставимой проверки обоих бэкендов в `prepare_validation_player(..., "publication")` требуется `hwdec="vaapi-copy"`, `pause="yes"`, `keep-open="yes"`.
  3. **Дефект 3C (ошибка формулы размеров текстуры при дробном масштабировании Wayland `1.25` и формата строки версии `libplacebo` в Run 4, `2026-10-04`)**:
     В GNOME Run 4 при дробном масштабе монитора `1.25` (`_cached_scale = 1.25`, виджет `1120×630`) `MpvVideoWidget` создавал текстуру `1400×788`, тогда как валидатор умножал размер виджета на целочисленный `scale_factor == 2` (`2240×1260`) и ложно валил проверку размеров. Одновременно строка версии `libplacebo` `"v7.360.1 (v7.360.1)"` внутри контейнера отклонялась проверкой, ожидавшей только `"v7.360.1"`.
  4. **Дефект 3D (раскрытие границы доказательств: публикация текстуры не доказывает передачу HDR композитору, `2026-10-04`, актуализация P1.1 `2026-10-24`)**:
     Исторически режим `auto` был закрыт временным заглушечным гейтом `is_surface_submission_proven() == False` и требовал тестового переопределения `CINEHDR_FORCE_SURFACE_SUBMISSION=1`. По задаче P1.1 переменная `CINEHDR_FORCE_SURFACE_SUBMISSION` удалена: гейт `auto` вычисляется динамически функцией `hdr_output_allowed()` по runtime-критериям (CM-global, GTK opt-in, монитор под окном HDR, caps). Отчёт публикации обязан фиксировать раскрытие границ доказательств (`Compositor surface submission: unverified (GDK texture publication only)`), подчеркивая, что подготовка и публикация PQ-текстуры в GTK не является подтверждением передачи HDR композитору.
  5. **Дефект 3E (отказ проверки `/app/lib/libmpv.so.2.5.0` внутри Flatpak-контейнера и защита от подмены библиотеки, `2026-10-03` — `2026-10-04`)**:
     Внутри Flatpak-контейнера `libmpv` загружается из `/app/lib/libmpv.so.2.5.0`, что первоначально отклонялось проверкой хостового префикса `mpv-gpu-next-prefix/lib64`. В `is_verified_flatpak_container()` и `publication_runtime_errors()` введена строгая проверка `/.flatpak-info`, read-only монтирования `/app` в `/proc/self/mountinfo` и канонического пути `/app/lib/libmpv.so.2.5.0` с отбраковкой любых внешних путей (`/usr/lib`, `/tmp`, симлинков наружу).
  6. **Дефект 3F (перезапись `cleanup_status.json` в `EXIT`-ловушке и ложный возврат `0` при пропаже `.flatpak/<id>/info` в `run_wayland_publication_validation.sh`, `2026-10-04`)**:
     При штатном завершении `evaluate_session_results` записывал `cleanup_status.json`, но срабатывавший при выходе из скрипта `trap cleanup_handler EXIT` перезаписывал его; кроме того, если файл `$XDG_RUNTIME_DIR/.flatpak/<id>/info` исчезал раньше, чем процесс пропадал из `flatpak ps`, `cleanup_instance` обязан проверять `flatpak ps --columns=instance` и завершаться ошибкой (fail-closed), если экземпляр всё ещё числится запущенным или вызов `flatpak ps` не удался.
  7. **Дефект 3G (потеря статуса `UNKNOWN`, подмена `FAIL` и неконтролируемое подавление ошибок в publication-раннере, `2026-10-24`)**:
     Если парсер трассы возвращал `UNKNOWN` (неопределённое или неполное протокольное доказательство), `evaluate_session_results` немедленно завершался с `sys.exit("FAIL: protocol evidence status is not PASS: UNKNOWN...")`. В результате `session_facts.json` не создавался, а `write_gate2w_summary` не вызывался вовсе (из-за `set -e`). Кроме того, агрегатор `write_gate2w_summary` безусловно превращал любой non-PASS факт в `FAIL`, а первоначальное исправление допускало совпадение exit code 1 при `UNKNOWN` и `FAIL`, проверку JSON через `grep` и маскирование ошибок старыми файлами.
     Исправлено:
     - при `status == "UNKNOWN"` в протоколе и соблюдении всех остальных обязательных инвариантов (FBO-пиксели, publication report, хеши, завершение контейнера) создаётся `session_facts.json` со статусами `protocol_status=UNKNOWN`, `backend_result=UNKNOWN`, сохранением точной причины `reason`, сброшенным флагом `composite_window_surface_proven=False` и сохранённым `container_session_correlated=True`;
     - `evaluate_session_results` возвращает отдельный код возврата `2` для контролируемого `UNKNOWN`, выдаваемый строго после завершения всех обязательных проверок и успешной записи фактов; любой `FAIL` возвращает код `1`;
     - перед новой оценкой устаревшие `session_facts.json` удаляются и не могут оставаться свидетельством результата;
     - `run_backend` проверяет точный код `eval_rc -eq 2` и валидирует JSON через Python-парсер (без `grep`), исключая подавление ошибок `FAIL` содержимым старых файлов;
     - в `write_gate2w_summary` сохраняется статус `UNKNOWN` для соответствующего бэкенда, `gate2w_status=OPEN`, `gate2w_closed=false`;
     - при `--backend all` статус `UNKNOWN` одного бэкенда не блокирует запуск второго, а `PASS` второго не скрывает `UNKNOWN`;
     - итоговый запуск скрипта завершается ненулевым кодом (код 1) с явным выводом `UNKNOWN` без заявления об успешном завершении;
     - `UNKNOWN` протокола не отменяет проверку FBO: повреждённый или отсутствующий FBO-артефакт при `UNKNOWN` остаётся строгим отказом `FAIL`;
     - тесты выполняют настоящие управляющие функции раннера (`main`, `run_backend`, `evaluate_session_results`, `write_gate2w_summary`) без копирования логики и проверены на чувствительность к намеренным мутациям;
     - `report_root` сохранён строго относительным к `repo_root` без введения нового публичного API `CINEHDR_REPORT_ROOT`.

#### 3.2. Проверяемые источники
- Коммит `51890eb60ec5646d090f65e067b81e47a2ec629c` (`gpu-next: bound validation and harden vendor smoke`).
- Коммит `24c878ebd32956243120c757b92ad3587b2e7189` (`test(gpu): enforce strict Flatpak validation reporting and record anonymized evidence`).
- Коммит `beed9089a977769c0d2b09e78e9841a397f46cf1` (`feat(render): republish render target on paused HDR output change (ADR-0007)`).
- Коммит `6a88937df082193c93e11e5423a268e87c501850` (`fix(validation): enforce multi-backend publication matrix and fixture verification`).
- Коммит `feb0d405297f031d4a49db6f7ddf5cd5e7c612d1` (`fix(hdr): remove async property readback and preserve first published sample`).
- Сохраняемые ADR: `docs/adr/0001-gpu-next-render-api.md`, `docs/adr/0004-gate2-fbo-pixel-validation.md`, `docs/adr/0007-paused-hdr-output-republication.md`.
- Источники, удаляемые в `C1` (доступны в коммите `45fb026e891a21ceb3150808b140085b10138988`):
  - `45fb026e891a21ceb3150808b140085b10138988:docs/GPU_NEXT_DEVELOPMENT.md`, разделы `## Gate 2 start: controlled embedded-FBO pixels`, `### Gate 2W Run 4 Rerun Brief (ADR-0006 Governance, 2026-10-04)`, `### Gate 2W Run 5 result — GNOME / Candidate v6 (2026-10-04)`, `### Gate 2W Run 6 result — GNOME / Candidate v7 (2026-10-10)`, `### Gate 2W Run 7 final GNOME adjudication (2026-10-11)`.
  - `45fb026e891a21ceb3150808b140085b10138988:docs/MODEL_USAGE_LOG.md`, разделы `### 2026-10-03 Gate 2W Run 4 offline qualification and provenance analysis`, `### 2026-10-04 Gate 2W Run 4 offline remediation of Astra high review blockers`, `### 2026-10-17 First-frame republication simplification and multi-backend validation`.
  - `45fb026e891a21ceb3150808b140085b10138988:docs/adr/0006-bounded-validation-governance.md`, раздел `## Gate 2W multi-phase provenance and execution amendment (2026-10-04)`.

#### 3.3. Фактические текущие файлы и функции
- `src/gpu_validation.py` *(целевой путь в `C4a`: `tools/validation/gpu_validation.py`)*:
  - `GpuValidationConfig`, `validation_config_from_env`
  - `prepare_validation_player`
  - `validation_steps` (ветка `mode == "publication"`)
  - `expected_scaled_texture_dimensions`, `publication_texture_evidence`, `publication_state_errors`
  - `publication_runtime_errors`, `is_verified_flatpak_container`
  - `GpuValidationSession` (`start`, `_wait_until_ready`, `_apply_step`, `_verify_step`, `_finish`, `_write_publication_report`)
  - `evaluate_publication_validation`.
- `tools/validation/run_wayland_publication_validation.sh` *(перенесён из корня по `C3`)*:
  - `check_flatpak_preflight`, `cleanup_instance`, `cleanup_handler`, `evaluate_session_results`, `run_backend`, `write_gate2w_summary`, `main`.
- `src/main.py`: `_uses_publication_fixture_contract`, `CineApplication.__init__` (`Gio.ApplicationFlags.NON_UNIQUE` при `CINEHDR_GPU_VALIDATION`), `CineApplication.do_open` (вызов `prepare_validation_player`, сохранение `pause=True`, запуск `GpuValidationSession`).
- `src/window.py`: `CineWindow.__init__` (`self._is_gpu_validation`), `CineWindow.do_close_request` (отключение `quit_watch_later` при валидации, вызов `self._video_area.shutdown_render_context()` до `self.mpv.quit()` и `self.mpv.wait_for_shutdown()`).
- `src/video_widget.py`: `MpvVideoWidget.shutdown_render_context`, `MpvVideoWidget._on_unrealize`.

#### 3.4. Карта «дефект / контроль → конкретный регрессионный тест»
- **Дефект 3A (сохранение `pause=True` в `do_open` и отбраковка незапауженного старта или `playlist-pos != 0` в `_wait_until_ready`)**:
  - `TestStartupFixtureContract.test_do_open_preserves_publication_pause_contract` (`tests/test_gpu_validation.py`)
  - `TestStartupFixtureContract.test_wait_until_ready_rejects_unpaused_initial_playback` (`tests/test_gpu_validation.py`)
  - `TestStartupFixtureContract.test_wait_until_ready_rejects_playlist_pos_not_zero` (`tests/test_gpu_validation.py`)
- **Дефект 3B (изоляция плейлиста, `watch_later` и `hwdec=vaapi-copy` перед `loadfile`)**:
  - `ValidationConfigTests.test_validation_player_does_not_restore_or_save_watch_later` (`tests/test_gpu_validation.py`)
  - `ValidationConfigTests.test_publication_player_is_isolated_before_loadfile` (`tests/test_gpu_validation.py`)
  - `ValidationPlanTests.test_publication_plan_is_exact_paused_sdr_hdr_sdr_hdr_matrix` (`tests/test_gpu_validation.py`)
- **Дефект 3C (формула размеров текстуры при дробном масштабе `1.25` и строка версии `libplacebo` `"v7.360.1 (v7.360.1)"`)**:
  - `PublicationEvidenceTests.test_expected_scaled_texture_dimensions_formula` (`tests/test_gpu_validation.py`)
  - `PublicationEvidenceTests.test_publication_texture_evidence_fractional_scale_matching_run4` (`tests/test_gpu_validation.py`)
  - `PublicationEvidenceTests.test_publication_libplacebo_version_strict_formats` (`tests/test_gpu_validation.py`)
  - `PublicationEvidenceTests.test_missing_actual_texture_getter_is_unavailable_not_intended_state` (`tests/test_gpu_validation.py`)
- **Дефект 3D (удаление `CINEHDR_FORCE_SURFACE_SUBMISSION` и раскрытие границы `Compositor surface submission: unverified`)**:
  - `ValidationConfigTests.test_publication_config_does_not_require_surface_override` (`tests/test_gpu_validation.py`)
  - `PublicationEvidenceTests.test_publication_report_discloses_surface_submission_boundary` (`tests/test_gpu_validation.py`)
  - `WaylandPublicationScenarioTests.test_report_without_surface_submission_disclosure_is_rejected` (`tests/test_wayland_publication_scenario.py`)
- **Дефект 3E (проверка `/app/lib/libmpv.so.2.5.0` внутри верифицированного Flatpak-контейнера и отбраковка подмены библиотеки)**:
  - `PublicationEvidenceTests.test_flatpak_candidate_libmpv_path_and_foreign_substitution` (`tests/test_gpu_validation.py`)
  - `PublicationEvidenceTests.test_is_verified_flatpak_container_facts` (`tests/test_gpu_validation.py`)
- **Проверка роста `render_frame_generation` и отбраковки устаревшей HDR-поверхности при переходе к SDR**:
  - `PublicationEvidenceTests.test_publication_switch_requires_a_new_render_frame_generation` (`tests/test_gpu_validation.py`)
  - `PublicationEvidenceTests.test_transition_rejects_stale_hdr_surface_on_sdr` (`tests/test_gpu_validation.py`)
- **Дефект 3F (preflight коммитов, защита пользовательских экземпляров `user_instances`, fail-closed очистка в `cleanup_instance`/`cleanup_handler` и проверка артефакта FBO в `run_wayland_publication_validation.sh`)**:
  - `WaylandPublicationScenarioTests.test_preflight_blocks_when_candidate_v5_unpinned` (`tests/test_wayland_publication_scenario.py`)
  - `WaylandPublicationScenarioTests.test_preflight_blocks_when_targeting_candidate_v4` (`tests/test_wayland_publication_scenario.py`)
  - `WaylandPublicationScenarioTests.test_preflight_fails_on_candidate_commit_mismatch` (`tests/test_wayland_publication_scenario.py`)
  - `WaylandPublicationScenarioTests.test_cleanup_instance_refuses_user_instance` (`tests/test_wayland_publication_scenario.py`)
  - `WaylandPublicationScenarioTests.test_cleanup_instance_refuses_metadata_mismatch` (`tests/test_wayland_publication_scenario.py`)
  - `WaylandPublicationScenarioTests.test_cleanup_instance_fails_closed_when_info_missing_and_instance_still_running` (`tests/test_wayland_publication_scenario.py`)
  - `WaylandPublicationScenarioTests.test_cleanup_instance_fails_closed_when_info_missing_and_ps_fails` (`tests/test_wayland_publication_scenario.py`)
  - `WaylandPublicationScenarioTests.test_cleanup_handler_does_not_overwrite_existing_cleanup_status` (`tests/test_wayland_publication_scenario.py`)
  - `WaylandPublicationScenarioTests.test_eval_fails_when_fbo_pixel_artifact_is_missing` (`tests/test_wayland_publication_scenario.py`)
  - `WaylandPublicationScenarioTests.test_eval_fails_when_fbo_pixel_artifact_hash_mismatched` (`tests/test_wayland_publication_scenario.py`)
- **Дефект 3G (сохранение статуса `UNKNOWN` протокола, `session_facts.json`, агрегация матриц, код возврата `2` и execution isolation)**:
  - `WaylandPublicationScenarioTests.test_unknown_protocol_evidence_generates_unknown_session_facts` (`tests/test_wayland_publication_scenario.py`)
  - `WaylandPublicationScenarioTests.test_unknown_protocol_with_damaged_fbo_artifact_fails` (`tests/test_wayland_publication_scenario.py`)
  - `WaylandPublicationScenarioTests.test_stale_unknown_facts_invalidated_on_subsequent_failure` (`tests/test_wayland_publication_scenario.py`)
  - `WaylandPublicationScenarioTests.test_gate2w_summary_aggregation_matrix` (`tests/test_wayland_publication_scenario.py`)
  - `WaylandPublicationScenarioTests.test_gate2w_summary_single_backend_unknown` (`tests/test_wayland_publication_scenario.py`)
  - `WaylandPublicationScenarioTests.test_real_shell_runner_single_pass_exits_zero` (`tests/test_wayland_publication_scenario.py`)
  - `WaylandPublicationScenarioTests.test_real_shell_runner_single_unknown_writes_summary_and_exits_nonzero` (`tests/test_wayland_publication_scenario.py`)
  - `WaylandPublicationScenarioTests.test_real_shell_runner_first_unknown_continues_to_second_backend` (`tests/test_wayland_publication_scenario.py`)
  - `WaylandPublicationScenarioTests.test_real_shell_runner_pass_and_unknown_matrix_aggregation` (`tests/test_wayland_publication_scenario.py`)
  - `WaylandPublicationScenarioTests.test_real_shell_runner_real_failure_does_not_become_controlled_unknown` (`tests/test_wayland_publication_scenario.py`)
  - `WaylandPublicationScenarioTests.test_real_shell_runner_mutation_sensitivity` (`tests/test_wayland_publication_scenario.py`)

#### 3.5. Команда воспроизведения и предусловия
- **Предусловия для модульных/сценарных тестов (без GPU и без живой сессии)**: Python 3, `bash`, стандартные утилиты (`sha256sum`, `python3`), установленные модули проекта.
- **Команда воспроизведения модульных/сценарных тестов (текущее расположение `C0`)**:
  ```bash
  python3 -m unittest \
    tests.test_gpu_validation \
    tests.test_wayland_publication_scenario -v
  ```
- **Фактический интерфейс и предусловия скрипта `tools/validation/run_wayland_publication_validation.sh`**:
  - Синтаксис CLI:
    ```bash
    bash tools/validation/run_wayland_publication_validation.sh \
      [--protocol-trace|--no-protocol-trace] \
      [--backend legacy|gpu-next|all] \
      [--dry-run] [-h|--help]
    ```
  - Обязательные переменные окружения и предусловия (`check_flatpak_preflight` и проверка фикстур в `main`):
    1. `EXPECTED_APP_COMMIT` — полный 64-символьный hex SHA-256 коммита установленного Flatpak `io.github.rusmikev.CineHDR` (не может быть пустым и не может равняться Candidate v4 `8ed86c0b54694b75d1aaa9be75956d1d3a9d075901d90695e4e0d3d144970813`); должен совпадать с `flatpak info --show-commit io.github.rusmikev.CineHDR`.
    2. `EXPECTED_RUNTIME_COMMIT` — опциональный 64-символьный коммит рантайма `org.gnome.Platform/x86_64/50` (по умолчанию `0bd5091046708e64bc882e8349877cfbae713f6a1e2d0487a5f10af820a2d792`).
    3. Активный `WAYLAND_DISPLAY` (если не указан `--dry-run`). Скрипт опрашивает `flatpak ps --columns=instance,application` перед стартом и заносит все уже запущенные экземпляры `io.github.rusmikev.CineHDR` в массив `user_instances`, чтобы `cleanup_instance` никогда их не завершал.
    4. Две детерминированные фикстуры HEVC Main 10 (`yuv420p10`, lossless) по фиксированным путям:
       - `validation-reports/fixtures/cinehdr-gate2-hevc-main10-yuv420p10-lossless-sdr.mp4` (`SHA-256: 41393a0ee9dd887a1f11d4b21946fef3d1f49e72127719eefd1ac40aa9d5e360`);
       - `validation-reports/fixtures/cinehdr-gate2-hevc-main10-yuv420p10-lossless-hdr.mp4` (`SHA-256: bc437162b00b6565ca486ecc0a67704b6f099ebccf03cf671b2a0f87e94e021d`; если файл отсутствует, скрипт автоматически копирует его из `validation-reports/gate2-w4-hardware-20260926-095640/fixture/cinehdr-gate2-hevc-main10-yuv420p10-lossless-hdr.mp4` при совпадении SHA-256).
    5. Выходной каталог создаётся автоматически по пути `validation-reports/wayland-publication-<YYYYMMDD-HHMMSS>-<pid>/` (с подкаталогами `legacy/`, `gpu-next/` и сводкой `gate2w_summary.json`).
  - Пример dry-run проверки префлайта без запуска контейнера и GPU:
    ```bash
    EXPECTED_APP_COMMIT='REPLACE_WITH_64_HEX_APP_COMMIT' \
      bash tools/validation/run_wayland_publication_validation.sh --dry-run --protocol-trace --backend all
    ```

#### 3.6. Что результат доказывает и чего НЕ доказывает
- **Что доказывают модульные и сценарные тесты (`tests/test_gpu_validation.py`, `tests/test_wayland_publication_scenario.py`)**:
  - На моках и изолированных bash-функциях: соблюдение паузового контракта `do_open`, изоляцию настроек плеера, формулу дробного масштабирования `expected_scaled_texture_dimensions()`, отбраковку неверного префикса `libmpv`, защиту пользовательских экземпляров Flatpak в `cleanup_instance`, сохранность `cleanup_status.json` при срабатывании `EXIT` trap и проверку целостности `fbo_pixel_measurements.json`.
- **Что доказывает живой запуск `tools/validation/run_wayland_publication_validation.sh` (на реальном GPU и Wayland-сессии)**:
  - При успешном запуске `--backend all --protocol-trace` в отдельных процессах `legacy` (`opengl`) и `gpu-next` (`opengl-next`) установленного Flatpak-пакета обе фикстуры (`SDR` и `HDR`) проходят паузовый цикл `SDR → HDR → SDR → HDR` с новым кадром на каждом шаге, валидными `Gdk.GLTexture`, прошедшим `PausedTransitionPixelOracle`, протокольной цепочкой `primary_composite` в границах §2.6 и чистым завершением (`CLEAN`). Выбор одного бэкенда или `--no-protocol-trace` соответственно сужает результат.
- **Чего НЕ доказывает**:
  - Отсутствие пропусков кадров при длительном воспроизведении (60 fps soak).
  - Реальный физический HDR-вывод монитора (тон-маппинг композитора и яркость на экране проверяются только визуальным/фотометрическим оракулом L4).

#### 3.7. Точки подключения в продукте
- `src/main.py`:
  - В `CineApplication.__init__`: чтение `os.environ.get("CINEHDR_GPU_VALIDATION")` для добавления `Gio.ApplicationFlags.NON_UNIQUE` (чтобы тестовый экземпляр не подключался к пользовательскому по D-Bus).
  - В `CineApplication.do_open`: при активном `CINEHDR_GPU_VALIDATION` вызов `prepare_validation_player(win.mpv, self.gpu_validation_config.mode)` до `loadfile`, сохранение `pause=True` при `_uses_publication_fixture_contract(mode)` и планирование `GpuValidationSession(...).start` через `GLib.idle_add`.
  - *(По `C4d`: при `CINEHDR_GPU_VALIDATION` `main.py` будет импортировать `cinehdr.validation`, а при отсутствии модуля — завершаться с явной ошибкой в лог, не молча).*
- `src/window.py`:
  - `self._is_gpu_validation = bool(os.environ.get("CINEHDR_GPU_VALIDATION", "").strip())` в `CineWindow.__init__` (отключает `restore_last_playlist` при старте и `quit_watch_later()` в `do_close_request`).
  - Порядок закрытия в `CineWindow.do_close_request`: `self._video_area.shutdown_render_context()` вызывается до `self.mpv.quit()` / `self.mpv.wait_for_shutdown()`.

---

### 4. Автоматическое сохранение диагностического снимка первого опубликованного кадра (`DiagnosticsExporter`)

#### 4.1. Защищаемый инвариант и конкретные дефекты
- **Защищаемый инвариант (`AGENTS.md` §9–10, решение `D7`)**:
  1. Автоматический экспорт диагностического отчёта (`video_output_diagnostics.txt` и `video_output_diagnostics.json`) использует **единый** продуктовый сборщик фактов `collect_video_output_facts()` и генератор текста `build_video_output_report()` из `src/diagnostics_report.py` (гарантия побайтового совпадения логики с кнопкой `Copy Report` в диалоге `Video Output Diagnostics` на одном и том же `DiagnosticSnapshot`).
  2. Снимок со статусом `evidence_status = "CONFIRMED"` (`first_frame_confirmed = True`, `trigger = "first-media-frame-confirmed"`) записывается **только** после того, как реально декодированный медиа-кадр (`media_frame_evidence(self.mpv)`: `source_width > 0`, `source_height > 0`, `time_pos is not None`, непустой `path_token`) опубликован в `self.current_texture` при `self.render_frame_generation > 0`.
  3. **Точное разделение синхронного захвата в `_render_pending_frame()` и отложенного сбора в `idle_add_once`**:
     - Внутри GL-колбэка `MpvVideoWidget._render_pending_frame()` (`src/video_widget.py`) **не выполняется никакого дискового ввода-вывода** и не вызывается `collect_video_output_facts(win)`. В момент публикации текстуры синхронно фиксируются только `rendered_media_frame = media_frame_evidence(self.mpv)`, `ctx_gen = self.render_context_generation` и `load_gen = getattr(window, "_media_load_generation", 0)`, после чего планируется `idle_add_once(window.on_first_media_frame_published, rendered_media_frame, ctx_gen, load_gen)`.
     - Вызов `collect_video_output_facts(window)` внутри `DiagnosticsExporter.export_snapshot()` выполняется **позже, асинхронно на главном цикле GTK** (в idle-колбэке `CineWindow.on_first_media_frame_published`). `on_first_media_frame_published` проверяет `path_token` и поколения перед передачей в экспортёр. Вокруг `collect_video_output_facts(window)` экспортёр сверяет поколения контекста/загрузки, идентичность виджета и состояние завершения; повторной проверки `path_token` после сборки снимка нет. Если приложение закрывается до подтверждённого экспорта, в `do_close_request` записывается снимок со статусом `"INCOMPLETE"` (`first_frame_confirmed = False`).
- **Конкретные исторические дефекты и отрицательные контроли**:
  1. **Дефект 4A (отсутствие автоматического клиентского диагностического снимка в той же сессии в GNOME Run 2 `wayland-trace-MgsoJA`, `2026-09-24`)**:
     В прогоне GNOME Run 2 (`2026-09-24`) парсер протокола Wayland зафиксировал `PASS` (`wp_color_manager_v1#32` → `wp_image_description_v1#51.ready` → `set_image_description` → `wl_surface#61.commit()`), но в приложении ещё не было механизма автоматического сохранения `Video Output Diagnostics` из того же самого процесса после публикации первого медиа-кадра. Из-за этого поля `Active API` и `Session status` для сессии `wayland-trace-MgsoJA` остались `Unknown`, а комбинированная строка была переведена в `HOLD` («нет свидетельства из той же сессии»). В Run 3 (`wayland-trace-Z4GghM`, `2026-09-26`) `DiagnosticsExporter` устранил этот пробел.
  2. **Дефект 4B (пустой начальный GL redraw до декодирования медиа-кадра и гонки поколений `load_generation` / `context_generation` при отложенном idle-экспорте, `2026-09-25`)**:
     При подготовке Attempt 3 (`2026-09-25`) были выявлены и закрыты регрессионными тестами дефекты:
     - первый вызов `_render_pending_frame()` может произойти до декодирования первого видеокадра (пустой GL redraw без `video-params`); он не должен помечать `_first_media_frame_exported = True` или экспортировать `CONFIRMED`;
     - отложенный через `idle_add_once` колбэк от старого файла или старого `render_context_generation` при быстром переключении/перезагрузке того же файла (`start-file` увеличивает `_media_load_generation`) мог перезаписать состояние нового поколения или отменить уведомление нового поколения;
     - если `context_generation` или `load_generation` изменились во время выполнения `collect_video_output_facts()`, снимок не имеет права получить статус `CONFIRMED`.
  3. **Дефект 4C (преждевременная остановка сессии сразу после появления `video_output_diagnostics.json` до `wl_surface.commit()` и маскировка ненулевого кода возврата процесса, `2026-09-25`)**:
     В логике верификации Attempt 3 обнаружение валидного `video_output_diagnostics.json` до появления `wl_surface.commit()` в `wayland-client.raw.log` не должно приводить к раннему завершению процесса до протокольного коммита, а поздний `wl_display.error`, SDR-манифест или ненулевой exit code дочернего процесса обязаны отзывать предварительный успех.

#### 4.2. Проверяемые источники
- Коммит `a25b2a38af8c61a7c36a990c5e92d4989bf83550` (`feat(diagnostics): add autonomous diagnostic report export and CLI option`).
- Коммит `9e2475aabf702b95d090b4c29b8802a9462eac87` (`fix(diagnostics): track Dolby Vision RPU warnings and enforce snapshot consistency`).
- Коммит `4831fefb4f7fe8df74462286b233b39eb579cae3` (`fix(diagnostics): unify decoder snapshot, chain validation logging, and clarify provenance`).
- Коммит `9010e5a4a473aa90ae35ef5bb22b27dacf373d6e` (`docs(validation): record GNOME trace evidence and Gate 2W hold`).
- Коммит `528242c7b647594fde792d2558101b3ad144f609` (`test: make diagnostics export write error test independent of root DAC permissions`).
- Источники, удаляемые в `C1` (доступны в коммите `45fb026e891a21ceb3150808b140085b10138988`):
  - `45fb026e891a21ceb3150808b140085b10138988:docs/GPU_NEXT_DEVELOPMENT.md`, разделы `## GNOME Wayland color-management trace capture (2026-09-24)` и `### Attempt 3 (2026-09-26, Candidate v4): Narrow row acceptance`.
  - `45fb026e891a21ceb3150808b140085b10138988:docs/MODEL_USAGE_LOG.md`, разделы `### 2026-09-24 Target GNOME Wayland trace capture and Gate 2W audit` и `### 2026-09-26 Target GNOME Wayland trace capture Attempt 3 and Gate 2W narrow row acceptance`.

#### 4.3. Фактические текущие файлы и функции
- `src/diagnostics_report.py`:
  - *Остаются в `src/diagnostics_report.py` по `D7` и `C4b`*: `DiagnosticSnapshot`, `collect_video_output_facts`, `build_video_output_report`, `generate_video_output_report`, `read_flatpak_info`, `format_dovi_rpu_warnings`, `get_dovi_rpu_warning_state`.
  - *Переносится в `tools/validation/diagnostics_export.py` в `C4b`*: константы окружения (`DIAGNOSTICS_EXPORT_PATH_ENV = "CINEHDR_DIAGNOSTICS_EXPORT_PATH"`, `DIAGNOSTICS_EXPORT_DIR_ENV = "CINEHDR_DIAGNOSTICS_EXPORT_DIR"`, `EXPECTED_FLATPAK_INSTANCE_ENV = "CINEHDR_EXPECTED_FLATPAK_INSTANCE"`, `EXPECTED_APP_COMMIT_ENV = "CINEHDR_EXPECTED_APP_COMMIT"`, `EXPECTED_RUNTIME_COMMIT_ENV = "CINEHDR_EXPECTED_RUNTIME_COMMIT"`, `DIAGNOSTICS_SESSION_ID_ENV = "CINEHDR_DIAGNOSTICS_SESSION_ID"`) и класс `DiagnosticsExporter` (`from_env`, `check_identity`, `export_snapshot`, `on_first_media_frame`, `on_first_frame`, `on_shutdown`).
- `src/render_backend.py`: `MediaFrameEvidence`, `media_frame_evidence`.
- `src/video_widget.py`: вычисление `rendered_media_frame = media_frame_evidence(self.mpv)` и планирование `idle_add_once(window.on_first_media_frame_published, rendered_media_frame, ctx_gen, load_gen)` в `MpvVideoWidget._render_pending_frame`.
- `src/window.py`: `self._diagnostics_exporter`, `self._media_load_generation`, `CineWindow.on_first_media_frame_published`, `CineWindow.do_close_request`.
- `tests/test_diagnostics_export.py`: класс `DiagnosticsExportTests`.
- `tests/test_attempt3_regressions.py`: класс `Attempt3RegressionsTests`.

#### 4.4. Карта «дефект / контроль → конкретный регрессионный тест»
- **Дефект 4A (инстанцирование из переменных окружения, паритет с `Copy Report`, статус `CONFIRMED` только при первом медиа-кадре и `INCOMPLETE` при закрытии без кадра, проверка идентичности Flatpak)**:
  - `DiagnosticsExportTests.test_exporter_from_env_instantiation` (`tests/test_diagnostics_export.py`)
  - `DiagnosticsExportTests.test_parity_between_headless_export_and_copy_report` (`tests/test_diagnostics_export.py`)
  - `DiagnosticsExportTests.test_exporter_first_frame_confirmed_produces_confirmed_status` (`tests/test_diagnostics_export.py`)
  - `DiagnosticsExportTests.test_exporter_missing_first_frame_produces_incomplete_status` (`tests/test_diagnostics_export.py`)
  - `DiagnosticsExportTests.test_exporter_flatpak_identity_mismatch` (`tests/test_diagnostics_export.py`)
  - `DiagnosticsExportTests.test_exporter_handles_write_error_gracefully` (`tests/test_diagnostics_export.py`)
- **Дефект 4B (игнорирование пустого начального GL redraw, отсутствие дискового I/O в `_render_pending_frame` и защита от гонок `context_generation` / `_media_load_generation` при отложенном вызове `idle_add_once`)**:
  - `DiagnosticsExportTests.test_frame_sequence_empty_redraw_then_media_frame_then_repeat` (`tests/test_diagnostics_export.py`)
  - `DiagnosticsExportTests.test_no_disk_io_inside_render_callback` (`tests/test_diagnostics_export.py`)
  - `DiagnosticsExportTests.test_regression_deferred_export_invalidation` (`tests/test_diagnostics_export.py`)
  - `DiagnosticsExportTests.test_regression_load_change_discards_old_and_exports_new` (`tests/test_diagnostics_export.py`)
  - `DiagnosticsExportTests.test_regression_late_old_callback_does_not_cancel_new_notification` (`tests/test_diagnostics_export.py`)
  - `DiagnosticsExportTests.test_stale_callback_clobbers_new_generation_state` (`tests/test_diagnostics_export.py`)
  - `DiagnosticsExportTests.test_same_file_reload_race_causes_premature_export` (`tests/test_diagnostics_export.py`)
  - `DiagnosticsExportTests.test_generation_change_during_snapshot_collection_rejects_confirmed` (`tests/test_diagnostics_export.py`)
  - `DiagnosticsExportTests.test_call_boundary_generation_advance_rejects_and_allows_next_generation_export` (`tests/test_diagnostics_export.py`)
- **Дефект 4C (запрет ранней остановки до `wl_surface.commit()`, отбраковка неполных/чужих бандлов, откат при позднем `wl_display.error` и сохранение exit code дочернего процесса)**:
  - `Attempt3RegressionsTests.test_regression1_valid_json_before_commit_no_early_stop` (`tests/test_attempt3_regressions.py`)
  - `Attempt3RegressionsTests.test_regression2_incomplete_corrupted_and_foreign_session_rejected` (`tests/test_attempt3_regressions.py`)
  - `Attempt3RegressionsTests.test_regression3_full_correct_bundle_accepted_within_narrow_claim` (`tests/test_attempt3_regressions.py`)
  - `Attempt3RegressionsTests.test_regression4_sdr_manifest_rejected_by_client_hdr_check` (`tests/test_attempt3_regressions.py`)
  - `Attempt3RegressionsTests.test_regression5_late_protocol_error_revokes_preliminary_success` (`tests/test_attempt3_regressions.py`)
  - `Attempt3RegressionsTests.test_regression9_child_process_exit_code_preserved_and_not_masked` (`tests/test_attempt3_regressions.py`)
  - `Attempt3RegressionsTests.test_regression11_sub_10ms_budget_rejects_zero_timeout_and_finalizes_timeout` (`tests/test_attempt3_regressions.py`)

#### 4.5. Команда воспроизведения и предусловия
- **Предусловия для юнит-тестов**: Python 3, `bash`, установленные модули проекта; GPU и активный композитор Wayland не требуются.
- **Команда воспроизведения юнит-тестов (текущее расположение `C0`)**:
  ```bash
  python3 -m unittest \
    tests.test_diagnostics_export \
    tests.test_attempt3_regressions -v
  ```
- **Активация автоэкспорта при запуске плеера (`DiagnosticsExporter.from_env`)**:
  В текущем приложении CLI-флаг `--export-diagnostics` **отсутствует** (`src/main.py` регистрирует только `--new-window` и `--experimental-color-mgmt`). Автоэкспорт включается исключительно через переменные окружения:
  - `CINEHDR_DIAGNOSTICS_EXPORT_DIR=/path/to/dir` или `CINEHDR_DIAGNOSTICS_EXPORT_PATH=/path/to/video_output_diagnostics.txt` (при указании `.txt` рядом автоматически создаётся одноимённый `.json`);
  - опциональные переменные верификации идентичности сессии:
    - `CINEHDR_EXPECTED_FLATPAK_INSTANCE`
    - `CINEHDR_EXPECTED_APP_COMMIT`
    - `CINEHDR_EXPECTED_RUNTIME_COMMIT`
    - `CINEHDR_DIAGNOSTICS_SESSION_ID`.

#### 4.6. Что результат доказывает и чего НЕ доказывает
- **Что доказывают модульные тесты (`tests/test_diagnostics_export.py`, `tests/test_attempt3_regressions.py`)**:
  - На моках: совпадение текстового отчёта с `Copy Report`, отсутствие файлового ввода-вывода внутри `_render_pending_frame()`, инвалидацию устаревших idle-колбэков при смене `render_context_generation` / `_media_load_generation` / `path_token` и корректную отбраковку неполных или повреждённых диагностических бандлов.
- **Что доказывает живой экспорт `video_output_diagnostics.json` со статусом `CONFIRMED`**:
  - Что в конкретном живом процессе (с записанным `process_id` из `os.getpid()` внутри контейнера, фактическим `flatpak_instance_id` и проверенными ожидаемыми `app_commit` / `runtime_commit`; ожидаемый instance ID сверяется, если задан) первый декодированный медиа-кадр (`MediaFrameEvidence`: `source_width`, `source_height`, `time_pos`, `path_token`) был опубликован в `Gdk.GLTexture`, а последующий снимок `collect_video_output_facts(win)` на главном цикле GTK выполнен в том же самом поколении контекста рендера и загрузки файла (`ctx_gen`, `load_gen`), зафиксировав `active_api`, `session_status`, `render_target_format`, `gdk_color_state` и `active_decoder`.
- **Чего НЕ доказывает**:
  - Что все поля `collect_video_output_facts(win)` были считаны синхронно внутри GL-колбэка того самого кадра (внутри `_render_pending_frame` синхронно захватываются только `MediaFrameEvidence`, `ctx_gen` и `load_gen`, тогда как остальной снимок собирается в `idle_add_once` под защитой проверки поколений).
  - Содержимое пикселей внутри FBO (проверяется пиксельным оракулом FBO, раздел 1).
  - Отправку `wp_image_description_v1` композитору по протоколу Wayland (проверяется `parse_wayland_color_trace.py`, раздел 2) и физический вывод на экран (L4).

#### 4.7. Точки подключения в продукте (сохраняются по `D7` и `C4c`)
- `CineWindow.__init__` (`src/window.py`):
  - `self._media_load_generation = 0` (инкрементируется в обработчике события mpv `start-file`) и `self._diagnostics_exporter = DiagnosticsExporter.from_env()`.
- `MpvVideoWidget._render_pending_frame` (`src/video_widget.py`):
  - После публикации `self.current_texture = texture` и `self.render_frame_generation += 1`, только если `not self._first_media_frame_exported` и `rendered_media_frame is not None`, помечает `self._first_media_frame_exported = True`, захватывает `(rendered_media_frame, ctx_gen, load_gen)` и вызывает `idle_add_once(window.on_first_media_frame_published, rendered_media_frame, ctx_gen, load_gen)`. В последующих кадрах того же файла проверка не выполняет работы.
- `CineWindow.on_first_media_frame_published` и `CineWindow.do_close_request` (`src/window.py`):
  - Вызывают `self._diagnostics_exporter.on_first_media_frame(...)` на главном цикле GTK и `self._diagnostics_exporter.on_shutdown(self)` перед `shutdown_render_context()`. При `self._diagnostics_exporter is None` (режим по умолчанию) обе точки являются немедленным no-op.

---

## Справочник по прочим режимам и скриптам вне ядра `D7` (входные данные для `C2` / `C3` / `C4` / `C16`)

По правилу `docs/PLAN.md` (`C0`, `C2`, `C3`, `C4`, `C16`) механизмы вне четырёх обязательных частей `D7` оцениваются индивидуально. Ниже зафиксировано наличие или отсутствие задокументированных исторических дефектов и синтетических отрицательных контролей для каждого из них:

| Механизм / файл | Защищаемый инвариант | Исторический дефект в документации / git | Синтетический отрицательный контроль / тест |
|---|---|---|---|
| Режим `lifecycle` в `src/gpu_validation.py` (`_capture_video_selection_for_context_restore`, `_restore_video_after_context_recreate` в `src/video_widget.py`) | Восстановление видеоцепочки (`vid=no` → `vid=auto`/track ID) после `mpv_render_context_free()`, который в `opengl-next` вызывает `kill_video_async()` и сбрасывает `vid` | **Задокументирован**: в `docs/adr/0001-gpu-next-render-api.md` (§`GL context recreation`) и `45fb026e891a21ceb3150808b140085b10138988:docs/GPU_NEXT_DEVELOPMENT.md` (§`Verified first-frame checks`) при пересоздании `Gtk.GLArea` контекста видео замирало без активного трека (`vid=no`) | `ValidationPlanTests.test_lifecycle_plan_recreates_the_context_twice`, `PublicationEvidenceTests.test_context_cycle_requires_a_new_context_and_preserves_hdr_state`, `PublicationEvidenceTests.test_context_cycle_fails_if_generation_does_not_advance` (`tests/test_gpu_validation.py`) |
| Режим `transition` в `src/gpu_validation.py` (непаузовый цикл `SDR → HDR → SDR → HDR`) | Смена `GL_RGBA8`/`srgb` ↔ `GL_RGBA16F`/`rec2100-pq` в одном процессе без пересоздания контекста | **Задокументирован**: перекрывается более строгим паузовым сценарием `publication` + `PausedTransitionPixelOracle` (разделы 1 и 3 выше) | `ValidationPlanTests.test_transition_plan_runs_sdr_hdr_sdr_hdr_in_one_context`, `PublicationEvidenceTests.test_transition_accepts_published_hdr_state_after_a_new_frame`, `PublicationEvidenceTests.test_transition_rejects_stale_hdr_surface_on_sdr` (`tests/test_gpu_validation.py`) |
| `tools/validation/run_gate2_resource_settling.sh` (перенесён из `run_gate2_resource_settling.sh` по `C3`), `tools/validation/run_gpu_validation.sh` (перенесён из `run_gpu_validation.sh` по `C3`; ранее также `run_flatpak_gpu_validation.sh`), `src/gpu_validation.py` (режимы `quick`, `soak`, `sdr-soak`, `settling`, `lifecycle`, `transition`) | Отсутствие монотонного роста VRAM/RSS на трёх сопоставимых точках после прогрева и ресайзов на паузе (`settling`); происхождение фикстур (`fixtures.json`), свежесть отчёта и разделение backend-процессов (`legacy` и `gpu-next`); выполнение режимов `quick`, `soak`, `sdr-soak`, `lifecycle`, `transition`; стабильность 30-минутного прогона (`ADR-0001` Gate 1/Gate 2). Не является доказательством отсутствия утечек или HDR-вывода (байтовых порогов нет: WARN выставляется только при строгом росте RSS или DRM VRAM на трёх сопоставимых точках, PASS — при полном наборе доказательств без такого роста; WARN не доказывает утечку) | В части ресайза на паузе (`2026-08-23`, `45fb026e891a21ceb3150808b140085b10138988:docs/MODEL_USAGE_LOG.md` строки `First resource-settling run` и `Second resource-settling integration failure`): выявил, что GTK 4.22.4 не эмитил `notify::width/height` при аллокации виджета (исправлено переходом на `do_size_allocate` в `MpvVideoWidget`); по самой утечке VRAM/RSS в живом прогоне — **исторический дефект не задокументирован**. Основание сохранения `run_gate2_resource_settling.sh`: проверка сопоставимых точек после прогрева и ресайзов, происхождения фикстур, свежести отчёта и разделения backend-процессов. Основание переноса `run_gpu_validation.sh`: единственный способ запуска режимов `quick`, `soak`, `sdr-soak`, `lifecycle`, `transition`; необходимость и судьба каждого режима решаются отдельно в задаче `C4` по правилу `D7` (инструмент перенесён без утверждения, что режимы доказанно необходимы для релиза) | Синтетические unit-тесты `ResourceSettlingEvidenceTests.test_settling_trend_only_warns_for_three_strictly_rising_endpoints`, `ResourceSettlingEvidenceTests.test_settling_evaluation_fails_closed_for_missing_or_unloaded_evidence` (`tests/test_gpu_validation.py`) |
| `tools/validation/test_pixel_pipeline.py` (перенесён из `tests/test_pixel_pipeline.py` по `C2c`) | Проверка инструментов генерации тестовых паттернов (`generate_patterns.py`), контракта и CLI EGL pixel probe (`pixel_validator_egl.c`, `run_pixel_pipeline.py`) и раннера расширенной матрицы Gate 2 (`tools/validation/run_gate2_extended_matrix.py`): математические проверки PQ/SDR рампы, структуры YUV/HEVC фикстур, контракта CLI и компиляции C-пробника, целостности снимков W4 и оверлея субтитров W5 (`ADR-0004` / исторически `ADR-0005`, см. `git show 0d3d57a:docs/adr/0005-gate2-thresholds.md`; числовые пороги относятся к историческому offscreen-инструменту EGL, не являются сертификацией точности цветопередачи и не входят в release gates 1.0). Действующий путь: `tools/validation/test_pixel_pipeline.py` (исторический путь: `tests/test_pixel_pipeline.py`). Команды запуска: `python3 -m unittest tools.validation.test_pixel_pipeline -v`, `python3 tools/validation/test_pixel_pipeline.py`, в составе Meson: `meson test -C build pixel_pipeline_tests` | **Исторический дефект не задокументирован** на реальном GPU (в живых прогонах Gate 2/3 синтетические паттерны проходили с запасом); защищает валидационный инструментарий и контракты пробника, а не измеренный вывод GPU | Синтетические отрицательные контроли в `tools/validation/test_pixel_pipeline.py` (проверка подделки метаданных/кадров HEVC без ffmpeg, невалидного JSON отчёта пробника, пороговых нарушений точности цвета, расхождения идентичности YUV-фикстур, невалидных PNG-снимков W4, порчи пикселей вне ROI субтитров W5) |
| `tools/validation/run_gate2_extended_matrix.py` (перенесён из `tests/run_gate2_extended_matrix.py` по `C2b`) | Гарнитуры расширенной матрицы Gate 2: W3 (монотонность градиента без дизеринга и проверка наличия данных рампы `has_dither_data = len(ramp_dither) == len(ramp_no)` при `dither=fruit`; не доказывает применение алгоритма дизеринга), W4 скриншоты, W5 субтитры для нативного `gpu-next`. Действующий путь: `tools/validation/run_gate2_extended_matrix.py` (исторический путь: `tests/run_gate2_extended_matrix.py`). Команды запуска: `python3 tools/validation/run_gate2_extended_matrix.py --help`, `python3 tools/validation/run_gate2_extended_matrix.py --only-w4 --software-only`, полный запуск: `python3 tools/validation/run_gate2_extended_matrix.py --mpv-source <path> --mpv-prefix <path>` | Продуктовый дефект рендера — **исторический дефект не задокументирован** (нативный `gpu-next` вне релизного scope 1.0 по `D5`) | Проверка порогов исторического `ADR-0005` (см. `git show 0d3d57a:docs/adr/0005-gate2-thresholds.md`; пороги offscreen-инструмента EGL не входят в release gates 1.0) и отрицательные контроли W4/W5 в `tools/validation/test_pixel_pipeline.py` (`TestGate2ScreenshotValidation`: проверка целостности PNG, повреждения CRC, не-PNG/усечённых данных, отсутствия Pillow, аномальных габаритов/цветов, искажения паттернов, контракта hwdec и оверлея субтитров W5) |
| `run_benchmark.py` (удалён в задаче `C3`) | Синтетический бенчмарк и профилировщик: расчёт объёма FBO по размерам и формату (теоретический VRAM: `RGBA16F` vs `RGBA8`); значения resize timing заданы нулями (`0.0 ms`), а не измерены; реальный headless-запуск libmpv с `vo=null` для SDR и HDR параметров, где FPS берётся из свойств mpv (`estimated-vf-fps`/`container-fps`), а frame time вычисляется из FPS (`1000.0 / avg_fps`); при недоступности libmpv или ошибке используется симуляция (`_simulate_mpv_pipeline`); время переключения `HdrController` измеряется в цикле с mock-плеером `MagicMock`. Не является измерением реального GTK/FBO/HDR-вывода. Утрачиваемые интерфейсы: CLI-запуск `python3 run_benchmark.py [--video <url>] [--duration <sec>] [--iterations <n>] [--output-json <path>] [--output-md <path>]` и генерация отчётов `benchmark_report.json` / `benchmark_report.md` | **Исторический дефект продукта не задокументирован**. Обоснование удаления по `docs/PLAN.md` (§6, `C3`): инструмент не обеспечивает проверку актуального релизного инварианта с конкретным дефектом или отрицательным контролем (синтетический расчёт VRAM, headless libmpv с `vo=null` без сэмплирования GTK scene graph и mock-переключения не тестируют реальный конвейер отображения кадра в scope 1.0). Замена для критериев релиза 1.0 не требуется; реальная производительность и диагностика вывода подтверждаются через Video Output Diagnostics и Copy Report. Для чтения удалённого файла: `git show e728de92eaed667f27615d276a429f7b0877fbba:run_benchmark.py` | Отрицательный контроль и проверяемые пороги отсутствуют (чисто информационный синтетический инструмент без ассертов, гейтов и регистрации в тестовых сьютах) |
| `run_flatpak_gpu_validation.sh` (удалён в задаче `C3`) | Устаревший quick-раннер для раннего Flatpak-бандла (`io.github.rusmikev.CineHDR-gpu-next.flatpak`) и фиксированного коммита (`4edda5ddca09dd99f8897e3c51738a4f508bc7318029c891e0c4e78229e5d736`): запуск с принудительным устройством `DRI_PRIME=pci-0000_03_00_0`, monkeypatching mpv (`hwdec=vaapi-copy` и цепочка log_handler), запуск в режиме `quick` с валидацией строгого текстового отчёта `gpu-next-quick-*.txt` через grep (`Overall result: PASS/WARN`, `Active renderer: opengl-next`, `Hardware decoding: vaapi-copy`, `OpenGL renderer:.*RX 9060 XT`). Утрачиваемые интерфейсы: запуск скрипта `run_flatpak_gpu_validation.sh <video-file> [quick]`, текстовые отчёты `gpu-next-quick-*.txt` и 19 unit-тестов раннера в `tests/test_gpu_validation.py`. | **Задокументирован**: коммиты `24c878e` (строгий текстовый отчёт, предотвращение ложноположительных прохождений) и `4831fef` (сохранение предупреждений DoVi RPU и цепочка обработчиков логов libmpv при bootstrap). Удалён в задаче `C3` по правилу `docs/PLAN.md` (§6, `C3`): устаревший launcher-specific сценарий, привязанный к единичному аппаратному сетапу, фиксированному коммиту и текстовому отчёту без протокольной трассировки Wayland. Что теряем (без прямой замены): запуск скрипта `run_flatpak_gpu_validation.sh`, проверку точного коммита бандла `expected_commit`, фиксацию `DRI_PRIME`, monkeypatching `vaapi-copy` и regex-валидацию полей `gpu-next-quick-*.txt`; 19 собственных тестов лаунчера. Сохраняемая защита (не является полной заменой удалённого ad-hoc сценария): обработка предупреждений DoVi RPU в продукте сохранена в `src/window.py` (`make_mpv_log_handler`); безопасное форматирование и извлечение состояния RPU — в `src/diagnostics_report.py` (`format_dovi_rpu_warnings`, `get_dovi_rpu_warning_state`); сценарий публикации `tools/validation/run_wayland_publication_validation.sh` самостоятельно выполняет проверку метаданных Flatpak-контейнера (`flatpak ps`, `/.flatpak/<id>/info`) и трассировку `wp_color_management_v1`; стабильность ресурсов проверяется в `ResourceSettlingEvidenceTests`. Для чтения удалённого файла: `git show d247f4da4fdf93431a208a37660ab6c282b37dbd:run_flatpak_gpu_validation.sh` | Собственные отрицательные контроли парсера отчёта и bootstrap удалены вместе со скриптом (19 тестов). Продуктовые контроли сохранены: обработка и форматирование DoVi RPU закреплены тестами в `tests/test_hdr.py` (`TestDoviRpuWarningDiagnostics`: `test_first_warning_saved_and_text_captured`, `test_repeated_warnings_counted_without_spam`, `test_other_warnings_and_errors_not_lost`, `test_unavailable_observation_does_not_become_confirmed_zero`, `test_ui_and_copy_report_reflect_identical_facts`); изоляция и проверки метаданных контейнера — в `tests/test_wayland_publication_scenario.py`; тренды ресурсов — в `ResourceSettlingEvidenceTests` |
| `tests/run_gate3_dovi_matrix.py` (удалён в задаче `C2b`) | Автономный аппаратный target-peak sweep (100, 203, 400, 600, 1000 нит) в `opengl-next` для native `gpu-next` и mock-проверка политики профилей DoVi в `HdrController` | **Исторический дефект продукта не задокументирован** (аппаратный native `gpu-next` вне scope 1.0 по `D5`). Потерянный аппаратный сценарий: свип целевой яркости `target-peak` (100, 203, 400, 600, 1000 нит) в `opengl-next` на синтетическом HDR-паттерне через C-пробник `pixel_validator_egl` (замена в scope 1.0 не нужна). Проверки продуктовой политики DoVi сохранены и усилены в unit-тестах: `test_dovi_unsupported_property_and_reshaping_capability`, `test_dovi_profile_5_blocked`, `test_dovi_profile_8_allowed`, `TestDoviRpuWarningDiagnostics`, `test_dovi_info_*` в `tests/test_hdr.py`. Утверждение о наличии отрицательного контроля `dolbyvision=no` в удаляемом runner являлось ошибочным (в коде runner отсутствовало). Для чтения удалённого файла: `git show 5c65a04ca03551dcc7b377251d1654f785b8831e:tests/run_gate3_dovi_matrix.py` | Синтетические отрицательные контроли и проверки политики DoVi в `tests/test_hdr.py` (`test_dovi_unsupported_property_and_reshaping_capability`, `test_dovi_profile_5_blocked`, `test_dovi_profile_8_allowed`) |
| `tests/run_performance_ab.py` (удалён в задаче `C2b`) | Автономный EGL-бенчмарк сравнения производительности `opengl` vs `opengl-next` (Gate 2 W6: p95 рендер-тайм `<= +10%`, отсутствие избыточных дропов кадров) | **Исторический дефект продукта не задокументирован**. В коммите `4846e0fa88aa93a276033d4e567ecb379faa37d0` исправлен дефект инструментария (некорректный порог p95 в `run_performance_ab.py` и чтение устаревших mpv drop-свойств в `tests/pixel_validator_egl.c`). Удалён в задаче `C2b` по решению `D5` и `docs/PLAN.md` (нативный `gpu-next` и сравнительные A/B-бенчмарки исключены из релизного scope 1.0; прямой замены в scope 1.0 нет). Для чтения удалённого файла: `git show 6b399719e5e6da4cbc6e6d9d9d090eee0c42addc:tests/run_performance_ab.py` | Отрицательный контроль отсутствует (чистый A/B-бенчмарк) |
| `tests/test_vendor_smoke.py` и `tests/test_nvidia.py` (задача `C16`) | Проверка «процесс жив 8 с, опубликован первый медиа-кадр, подтверждена идентичность runtime и отсутствие GL-ошибок» на Intel Mesa и NVIDIA PRIME (`371b510f0f7184e323b8e8fb727d87f99afe9287`, `c46b5247a7542a5902f361b7f0345bb7652ef65a`) | **Задокументирован**: в раннем smoke-прогоне (`1224119d39218b128bf199a84f3b1d5bdc133ada`, `c46b5247a7542a5902f361b7f0345bb7652ef65a`) без проверки телеметрии первого кадра отчёт завышал статус прохождения; по правилу `C16` эти файлы **не трогаются** в `C2` до подтверждения замены в `C4` | Проверки в `tests/test_vendor_smoke.py` и `tests/test_nvidia.py` |
