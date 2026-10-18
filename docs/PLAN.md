# CineHDR — план до релиза 1.0

Редакция 3, 2026-10-18. Статусы задач обновляет агент в отчёте; текст задач и
критерии меняет только владелец.

Нумерация: `P<фаза>.<n>` — продуктовые задачи, `C<n>` — чистка.
Исполнитель: **A** — агент (Gemini/ChatGPT), **O** — владелец (железо, решения).
Размер каждой задачи — один коммит/PR по `AGENTS.md` §12, если не сказано
«серия коммитов». Статусы: `TODO | IN PROGRESS | BLOCKED(<причина>) | DONE(<sha>)`.

## 0. Решения (приняты владельцем 2026-10-18, ADR-0008 Accepted)

- [x] D1. Upstreaming снят с целей релиза; цель — мержабельность с upstream `main` (процедура `upstream-diverge:`). `brand:`-коммиты отменены.
- [x] D2. Гейт `auto`: env-заглушка заменяется runtime-критерием (CM-global ∧ GTK opt-in ∧ монитор под окном в HDR ∧ caps достаточны). Приёмка — L3p и L4 (`AGENTS.md` §8).
- [x] D3. `GDK_DEBUG=color-mgmt` включён по умолчанию в Flatpak-лаунчере, opt-out `CINEHDR_NO_COLOR_MGMT=1`; нативная сборка — opt-in.
- [x] D4. Governance подлежит удалению по C1 (после C0): ADR-0006, MODEL_POLICY, MODEL_USAGE_LOG, шаблоны hand-off, GPU_VENDOR_SMOKE_VALIDATION, GPU_NEXT_DEVELOPMENT, TEST_MATRIX. Текстовый тест `tests/test_validation_governance.py` удаляется раньше — в P0.2, одним коммитом с новыми правилами, потому что проверяет и содержимое AGENTS.md/GEMINI.md.
- [x] D5. Scope 1.0: Flatpak x86_64, Wayland; HDR для GNOME ≥ 48 и Plasma ≥ 6.3; X11 и прочие композиторы — SDR; gpu-next — experimental, выключен; нативная сборка — best effort. Из D5 следует удаление aarch64 из CI (C11).
- [x] D6. Fork-строки переводятся: `po/` через `update_translations.sh` в коммитах `i18n:`.
- [x] D7. Валидация: в `tools/validation/` (вне `cine_sources`) сохраняются парсер Wayland-трассы с тестами, пиксельный оракул FBO с отрицательными контролями, минимальный автосценарий «SDR → HDR → SDR на паузе + первый опубликованный кадр + корректное завершение», минимальный автоэкспорт снимка для него. Сборщик фактов остаётся в продукте; точки подключения в продукте сохраняются и по умолчанию выключены. Прочие механизмы удаляются только адресно по правилу §6.

Phase 0 разблокирована целиком. Порядок первых задач: P0.2 (правила + удаление текстового теста) → C0 → C15 → C1 → C2 → C3 → P0.3; P1.1 только после C15.

## 1. Правило параллельности

Чистка не трогает файл, по которому открыта продуктовая задача. Для
`video_widget.py`, `hdr_controller.py`, `hdr_detection.py` чистка (C7, C8, C9)
идёт **после** приёмки соответствующей продуктовой фазы. Исключение — C15
(`hdr_diagnostics.py`): выполняется в Phase 0 **до** P1.1, потому что P1.1
меняет `_hdr_status_text`, который сейчас существует в двух файлах.

## 2. Phase 0 — подготовка (без железа)

| # | Задача | Исп. | Готово, когда | Статус |
|---|---|---|---|---|
| P0.1 | Зафиксировать `docs/adr/0008` (Accepted) и пересмотр `docs/adr/0007` (гейт `auto`) | O | ADR в репо | TODO |
| P0.2 | Одним коммитом: положить `AGENTS.md`, `GEMINI.md`, `docs/PLAN.md`, `docs/TESTING.md` редакции 3 **и** удалить `tests/test_validation_governance.py` с его регистрацией в `tests/meson.build` (тест проверяет содержимое AGENTS.md/GEMINI.md и падает сразу после установки новой редакции). Тело коммита: «теряем: проверку текста документов; замена: не нужна (AGENTS §11)» | O (или A по поручению) | файлы в `main`; `meson test` зелёный в этом же коммите | IN PROGRESS |
| C0 | До C1: создать `tools/validation/README.md` с разделом «Известные дефекты и их проверки»: из `docs/MODEL_USAGE_LOG.md`, `docs/GPU_NEXT_DEVELOPMENT.md`, ADR-0004/0006 и истории git извлечь для каждого инструмента (sha или запись лога → инструмент → как воспроизвести → какой тест закрепляет). Первая запись известна: «старые HDR-пиксели после перехода HDR → SDR на паузе» — пиксельный оракул FBO, регрессионный тест в `tests/test_hdr.py` (искать по имени теста, не по номеру строки). Отсутствие записей по инструменту фиксируется словами «исторический дефект не задокументирован» — это вход для C2/C4, не приговор | A | README создан; для каждого инструмента из D7 есть запись | TODO |
| C1 | Условие: C0 DONE. Удалить `docs/MODEL_POLICY.md`, `docs/MODEL_USAGE_LOG.md`, `docs/adr/0006-*`, `docs/GPU_TEST_HANDOFF_TEMPLATE.md`, `docs/GPU_VENDOR_SMOKE_VALIDATION.md`, `docs/GPU_NEXT_DEVELOPMENT.md`, `TEST_MATRIX.md`; из `.gitignore` убрать упоминания удалённых артефактов. Тело коммита — по правилу §6 для каждого файла | A | `meson test` зелёный; `grep -rn "MODEL_POLICY\|ADR-0006\|TEST_MATRIX" --include=*.py --include=*.md .` пусто | TODO |
| C2 | Тесты и гарнитуры — серия коммитов, по одному на пункт, каждый со строками «теряем / замена» (§6): (a) `tests/parse_wayland_color_trace.py` + `tests/test_wayland_color_trace.py` → `tools/validation/` (сохраняется, L3p); добавить в парсер распознавание представлений из AGENTS §8 с положительным и отрицательным контролем на каждое и параметр app_id (для трассы эталона mpv); (b) `tests/run_gate2_extended_matrix.py`, `run_gate3_dovi_matrix.py`, `run_performance_ab.py` — агент читает каждый; сохраняется, если защищает актуальный инвариант и выявляет конкретный дефект (исторический или отрицательный контроль) — тогда переносится в `tools/validation/`; иначе удаляется с обоснованием (DoVi и perf A/B требуют нативного gpu-next — вне scope 1.0); (c) `tests/test_pixel_pipeline.py` — остаётся в `tests/`, если тестирует код продукта, иначе в `tools/validation/`; (d) `tests/meson.build` обновить; тесты `tools/validation` остаются в `meson test`. `tests/test_vendor_smoke.py` и `tests/test_nvidia.py` в C2 **не трогаются** — см. C16 | A | тесты зелёные; парсер трассы запускается из `tools/`, контроли проходят | TODO |
| C3 | Корень: удалить `run_benchmark.py`, `run_flatpak_gpu_validation.sh`, `run_gate2_resource_settling.sh`, `run_gpu_validation.sh`, `run_wayland_publication_validation.sh` (каждый — со строками «теряем / замена»; если скрипт — единственный способ запуска сохраняемого сценария из D7, он переносится в `tools/validation/`); `run_gpu_next.sh` → `tools/`; `update_translations.sh` остаётся | A | в корне из скриптов только `run_dev.py` | TODO |
| C15 | `hdr_diagnostics.py`: удалить дубликаты `_positive_float`, `_source_peak_nits`, `_hdr_status_text`, `get_mpv_prop`, `format_dovi_rpu_warnings`, `get_dovi_rpu_warning_state` (импорт из `diagnostics_report`). Fallback-ветки при `_current_snapshot is None` в `_copy_report`, `_update_stream_info`, `_update_performance_info` удалять только после проверки недостижимости по вызывающим местам и жизненному циклу диалога (`realize` → `update_diagnostics` → `_on_copy`); результат проверки — в теле коммита | A | `build_video_output_report(фиксированный снимок)` идентичен до/после; O: диалог открывается, обновляется, Copy работает | TODO |
| P0.3 | Влить upstream `main` (1.8.7); конфликты в пользу upstream вне HDR-пути; версия → `1.8.7.0` (схема `<upstream>.<hdr-rev>`) | A → O (L2) | SDR-файл играет, управление работает | TODO |
| C11 | CI: убрать aarch64 (по D5); добавить `ruff check`; оставить `meson test` | A | workflow зелёный | TODO |
| P0.4 | `ruff` конфиг в `pyproject.toml` (`F`, `E9`); ноль F-ошибок без изменения поведения | A | `ruff check` чистый | TODO |
| P0.5 | Базовые Copy Report с каждой машины/DE (SDR-файл, HDR-файл) — исходное состояние | O | отчёты в `validation-reports/` | TODO |

## 3. Phase 1 — GNOME (AMD-десктоп + HDR-монитор)

| # | Задача | Исп. | Готово, когда | Статус |
|---|---|---|---|---|
| P1.1 | Гейт `auto`: заменить `is_surface_submission_proven()` на `hdr_output_allowed()` в `hdr_detection` (CM-global ∧ GTK opt-in ∧ монитор под окном HDR ∧ caps по `gtk_color_managed`/`gtk_can_tag_hdr`); убрать `CINEHDR_FORCE_SURFACE_SUBMISSION` из продукта и из `_hdr_status_text`; тесты по всем комбинациям входов; диагностика «Auto gate: allowed / blocked: <reason>». Условие: C15 DONE | A → O | тесты; далее P1.3 | TODO |
| P1.2 | `GDK_DEBUG=color-mgmt` по умолчанию в `cinehdr.in` (Flatpak) с opt-out `CINEHDR_NO_COLOR_MGMT=1`; `run_dev.py` без изменений; README | A → O (L2) | SDR UI не сломан; диагностика показывает opt-in active | TODO |
| P1.3 | GNOME-AMD, HDR включён в настройках. (0) Проверить эталон по выходному пути: зафиксированная в TESTING.md команда; лог mpv с выбранными API/контекстом; L3p-трасса собственной поверхности mpv показывает HDR-описание и commit. Индикатор HDR на мониторе/в DE — условие эксперимента, не подтверждение. Без этого всё сравнение L4 — UNCLEAR. (1) L3p — запуск с `WAYLAND_DEBUG=1`, путь зафиксирован заранее (композитный toplevel при выключенном offload), парсер показывает HDR-описание и commit на поверхности с видео. (2) L4 — критерии ступеней/клиппинга заданы до запуска, сравнение с эталоном на синтетическом паттерне. (3) HLG (L3p + L4), WCG-SDR (остаётся SDR), DoVi P8. (4) Наблюдение `force-hdr` при выключенном HDR — записать как есть, без ожидания; после подтверждения использовать как негативный контроль для этой конфигурации | O | ячейки TESTING.md | TODO |
| P1.4 | Если P1.3 не PASS: по Copy Report и трассе локализовать слой (гейт → committed state → FBO/color state → описание поверхности → эталон), одна гипотеза на итерацию. Подозреваемые: GSK renderer (`gl` vs `vulkan` на GTK 4.22 — проверить в исходниках GTK, какой renderer выбирается и поддерживает ли fp16 + color state), `GtkGraphicsOffload` (отключить и сравнить — меняет путь L3p), `target-prim/trc`, `hdr-compute-peak`, сам эталон | A ↔ O | PASS в P1.3 | TODO |
| P1.5 | Сценарии жизненного цикла на GNOME-AMD: ресайз и fullscreen в проигрывании; перенос окна HDR↔SDR панель; переключение HDR в настройках GNOME во время проигрывания; `auto/force-hdr/force-sdr` на лету; плейлист HDR→SDR→HDR; выход во время проигрывания; отключение монитора | O, баги → A | все ячейки GNOME-AMD PASS | TODO |
| P1.6 | Peak: проверить `target-peak` auto (подстановка пика монитора при < 90% пика потока) на мониторе; при сомнениях упростить до «auto = pass-through, пресеты = явные» | O → A | решение в ADR-0007 | TODO |
| P1.7 | README: убрать `experimental-features`; переключатель HDR в настройках GNOME ≥ 48; таблица поддержки | A | README актуален | TODO |
| P1.8 | gpu-next: за настройкой с пометкой «Experimental», fallback на legacy при неудаче создания контекста; DoVi P5 → SDR с причиной в диагностике; из релизных критериев исключён | A | тесты render_backend | TODO |

## 4. Phase 2 — KDE Plasma (AMD, затем NVIDIA)

| # | Задача | Исп. | Готово, когда | Статус |
|---|---|---|---|---|
| P2.1 | В исходниках GTK версии из runtime выяснить, какую TF GTK требует для базовой (sRGB) image description и принимает ли `gamma22` (протокол v2 пометил `srgb` deprecated; KWin объявляет `gamma22`), и какое представление HDR GTK использует для поверхности (PQ / ext_linear). Результат — факт с `файл:строка` | A | отчёт со ссылками | TODO |
| P2.2 | По P2.1 обновить `gtk_color_managed()` (требование `TF_SRGB` захардкожено) и тесты; строки причин — английские | A | тесты | TODO |
| P2.3 | KDE-AMD: эталон проверен; L3p с путём, зафиксированным заранее; L4 по критериям; тот же набор файлов. Если GTK на KDE принципиально не может — задокументировать: на KDE `auto` = тон-маппинг mpv, диагностика показывает причину; README — «fallback», не «поддерживается» | O | ячейки KDE-AMD | TODO |
| P2.4 | KDE-специфика: SDR UI при включённом HDR (KWin использует gamma 2.2 для sRGB) — интерфейс не «выцветший» с `GDK_DEBUG=color-mgmt`; при проблеме — opt-out по compositor id | O → A | ячейки | TODO |
| P2.5 | Сценарии P1.5 на KDE-AMD | O → A | ячейки | TODO |

## 5. Phase 3 — Intel / NVIDIA (ноутбук)

| # | Задача | Исп. | Готово, когда | Статус |
|---|---|---|---|---|
| P3.1 | Убрать проверку `get_dmabuf_formats().get_n_formats() == 0` из `_check_hdr_support_uncached` (не относится к GL-текстурному пути; блокирует NVIDIA); оставить только там, где решается offload | A | тесты | TODO |
| P3.2 | Intel Mesa, GNOME: SDR L2/L3; hwdec `vaapi` vs `vaapi-copy` (в продукте не форсировать `vaapi-copy`); 4K60 без пропусков; путь `set_sync`; GL errors = 0 | O → A | ячейки GNOME-Intel | TODO |
| P3.3 | Intel + внешний HDR-монитор (если HDMI на Intel): L3p + L4 | O | ячейка или N/A | TODO |
| P3.4 | NVIDIA PRIME во Flatpak: рабочий набор env (`__NV_PRIME_RENDER_OFFLOAD=1`, `__GLX_VENDOR_LIBRARY_NAME=nvidia`, EGL vendor) так, чтобы GTK и mpv были на одном GPU; README «advanced» | O → A | SDR PASS на NVIDIA | TODO |
| P3.5 | NVIDIA: fence/`set_sync` без stall, нет tearing, fp16-surface при HDR (если HDMI на dGPU) либо честный `Unknown/blocked: <reason>`; GSK renderer pin `gl` подтверждён или заменён | O → A | ячейки GNOME-NVIDIA, KDE-NVIDIA (SDR обязательно) | TODO |
| P3.6 | Известные ограничения по вендорам → README | A | README | TODO |

## 6. Чистка (параллельно, по правилу параллельности)

| # | Задача | Когда | Готово, когда | Статус |
|---|---|---|---|---|
| C4 | Серия коммитов. Вынести валидацию из устанавливаемого продукта в объёме D7. (a) `src/gpu_validation.py` → `tools/validation/gpu_validation.py`: сохранить `compute_patch_centers`, `read_fbo_patch_samples`, `PausedTransitionPixelOracle` с отрицательными контролями, `serialize_/verify_fbo_pixel_measurements_artifact`, режим `publication` (SDR→HDR→SDR на паузе) с проверкой первого опубликованного кадра и корректного завершения, `is_verified_flatpak_container`, `publication_runtime_errors`; режимы `quick/soak/sdr-soak/lifecycle/transition/settling` и сэмплирование RSS/DRM — отдельные коммиты по правилу ниже, по одному на режим, сохраняются при выполнении критерия сохранения. (b) `DiagnosticsExporter` → `tools/validation/diagnostics_export.py` (автоэкспорт снимка на первом опубликованном кадре через `collect_video_output_facts`); `collect_video_output_facts`/`build_video_output_report` остаются в `src/diagnostics_report.py`. (c) Точки подключения в продукте **не удаляются**: `video_widget._fbo_pixel_oracle_cb`/`_error_cb` (по умолчанию `None`), callback первого опубликованного медиа-кадра, `window._media_load_generation`; они должны позволять измерить тот же FBO в момент публикации; при `None` — ноль дополнительной работы на кадр; перечень — в `tools/validation/README.md`. (d) Доступ из Flatpak: meson-опция `validation` (default `false`); при `true` `tools/validation/*.py` устанавливаются как `cinehdr/validation/`; `main.py` при `CINEHDR_GPU_VALIDATION` импортирует `cinehdr.validation`, при отсутствии модуля завершается с ошибкой в лог, не молча. (e) `tools/validation/README.md`: как запускать каждый инструмент и что он доказывает | после P1.5 | `build_video_output_report(фиксированный снимок)` идентичен до/после; живой диалог обновляется (O); `grep -rn "gpu_validation\|DiagnosticsExporter" src/` пусто; проверки из `tools/validation/README.md` воспроизводимы | TODO |
| C5 | `wayland_output_hdr.py`: удалить синхронные `probe_outputs`/`_probe_outputs_unsafe`/`_query_one_output` (теряем: синхронный probe; замена: `WaylandOutputHdrSource` + кэш); `get_cm_caps(allow_probe=True)` и `get_hdr_unsupported_reason` читают только кэш; `refresh_output_hdr_states` → async | после P2.2 | тесты; O: диагностика показывает caps и состояние монитора | TODO |
| C6 | Единый helper `utils.mpv_prop(player, name, default=None)`; заменить `_get_property` в `hdr_detection._query_mpv_prop`, `render_backend.render_runtime_diagnostics`, `video_widget._capture_video_selection_for_context_restore` и `get_mpv_prop` в `diagnostics_report`; `self.mpv._dovi_rpu_*` → состояние окна | после P1.5 | grep из AGENTS §2 пустой; фиксированный снимок → тот же отчёт | TODO |
| C7 | `hdr_controller.py`: оставить `load_hdr_config`/`save_hdr_config`; удалить `load/save_hdr_setting`, `load/save_hdr_mode`, property `hdr_enabled` (и делегаты в `video_widget`); `hdr-target-prim` удалить из схемы и кода; `getattr(self, "_initialized", True)` и прочий шум; `on_content_change_cb` → явный параметр | после P1.6 | тесты test_hdr; O: L2 + переключение режимов | TODO |
| C8 | `video_widget.py`: убрать `getattr/hasattr` на собственных атрибутах, `try/except` вокруг `connect`; один кэш решения HDR (убрать `_cached_hdr_support*`, читать `hdr_detection`); упростить `_update_cached_scale_and_limits`; `raise` после `_latch_render_restart` в `_on_realize` → только latch + лог. Точки подключения измерений сохраняются | после P3.5 | фиксированный снимок → тот же отчёт; O: L2 на трёх GPU; автосценарий `publication` PASS | TODO |
| C9 | `hdr_detection.is_hdr_content` → критерий по трансферу (AGENTS §5.4) + тест WCG-SDR; `_cached_support` не трогается из `wayland_output_hdr` (использовать `invalidate_support_decision()`) | после P1.3 | тесты | TODO |
| C10 | `gtk_cm_policy.py`: английские строки; `get_gtk_version()` → `None` при ошибке, политика renderer при `None` ничего не ставит; комментарий о том, какой renderer выбирает `gl` на GTK ≥ 4.18, со ссылкой на GTK (или правило §4 меняется под факт) | после P1.4 | тесты test_color_mgmt_opt_in | TODO |
| C12 | ADR 0001 и 0004 сократить до «контекст / решение / последствия» (≤ 1 страница); 0007 переписать под новый гейт | Phase 0–1 | `docs/adr/` ≤ 4 файла | TODO |
| C13 | `mpv_gl_area.VideoGLArea` — тонкий алиас: оставить или заменить прямым использованием `MpvVideoWidget`; одним коммитом | любое | — | TODO |
| C14 | `docs/ARCHITECTURE.md`: одна страница с диаграммой конвейера, точками подключения измерений и ссылками на инварианты AGENTS §4 | после P1.3 | файл соответствует коду | TODO |

| C16 | `tests/test_vendor_smoke.py` + `tests/test_nvidia.py`: удалить только после того, как замена существует и проверена — автосценарий первого опубликованного кадра и корректного завершения из C4 PASS хотя бы на одной ячейке (Intel или NVIDIA), — либо после отдельного обоснования в коммите, почему защита «процесс жив 8 с, первый медиа-кадр, идентичность runtime» больше не нужна. Будущая C4 заменой не считается | после C4 + прогона O | тело коммита содержит «теряем / замена» с ссылкой на отчёт прогона | TODO |

Правило удаления (D7):
- Механизм валидации сохраняется, если защищает актуальный инвариант и
  выявляет конкретный дефект — исторический либо намеренно внесённый
  отрицательный контроль. Отсутствие исторического дефекта само по себе —
  не основание для удаления; основание — отсутствие и инварианта, и дефекта.
- Удаление только адресное, по одному механизму на коммит; тело коммита
  содержит «теряем: <защита>» и «замена: <что/где>» либо «не нужна: <почему>».
  Пакетное удаление запрещено.
- Точки подключения измерений в продукте удаляются только тем же правилом и
  только если ни один сохранённый инструмент их не использует.
- Приёмка чистки диагностики: одинаковый фиксированный снимок
  (`DiagnosticSnapshot`) даёт одинаковый `build_video_output_report` до и
  после; обновление живого диалога проверяется отдельно владельцем. Сравнение
  двух живых Copy Report построчно не используется — у них закономерно
  различаются время, PID и счётчики.
- После каждого переноса проверки из `tools/validation/README.md` остаются
  воспроизводимыми — это проверяется перед закрытием задачи.

## 7. Phase 4 — релиз

| # | Задача | Исп. | Статус |
|---|---|---|---|
| P4.1 | Влить upstream `main` повторно; версия `1.8.x.1`; `metainfo` `<release>` с датой и changelog | A | TODO |
| P4.2 | README финальный: установка из GitHub Release; требования (GNOME ≥ 48 / Plasma ≥ 6.3, Wayland, HDR-монитор); таблица поддержки DE × GPU из TESTING.md; ограничения (X11 — SDR, DoVi P5 — SDR, NVIDIA — см. раздел); как снять Copy Report для баг-репорта | A | TODO |
| P4.3 | `CHANGELOG.md`; скриншоты HDR-меню и диагностики | A/O | TODO |
| P4.4 | Тег `v1.8.x.1`, GitHub Release с `CineHDR-x86_64.flatpak` из CI, release notes | O | TODO |
| P4.5 | (после релиза, опционально) Flathub: отдельная задача — домен для app-id, ревью манифеста | — | TODO |

## 8. Критерии релиза 1.0

1. `docs/TESTING.md`: GNOME-AMD — все сценарии PASS, включая L3p и L4; GNOME-Intel — SDR PASS, HDR PASS или N/A с причиной; GNOME-NVIDIA — SDR PASS, HDR задокументирован; KDE-AMD — SDR PASS, HDR PASS (L3p + L4) или документированный fallback с точной причиной в диагностике; KDE-NVIDIA — SDR PASS.
2. Нет крашей и зависаний на: выход во время проигрывания, отключение монитора, переключение HDR в DE во время проигрывания, серия ресайз/fullscreen, смена файла HDR↔SDR.
3. `auto` включает HDR на GNOME-AMD без ручных env-переменных (внутри Flatpak).
4. Диагностика и Copy Report — английские, содержат все факты из AGENTS §9.
5. CI зелёный, `ruff` чистый, тесты герметичны; корень репозитория — только `run_dev.py`, `update_translations.sh`, конфиги; `tools/validation/` вне `cine_sources`; документы — набор из AGENTS §14.
6. README не содержит утверждений, не подтверждённых ячейкой TESTING.md.
7. Автосценарий `publication` (tools/validation) PASS на GNOME-AMD на релизном коммите; перечень `tools/validation/README.md` воспроизводим.

## 9. Известные риски

- D2 означает: на композиторах, где GTK молча конвертирует HDR-описание в SDR, пользователь увидит картинку хуже, чем с тон-маппингом mpv. Страховка — `force-sdr` и точная причина в диагностике.
- P2.1 может показать, что HDR через GTK на KDE невозможен без изменений в GTK. Тогда KDE в релизе получает статус «SDR + корректный fallback»; это не провал фазы.
- Эталон mpv уже однажды оказался неверным при недоопределённой команде запуска; поэтому проверка эталона — обязательный шаг 0 в P1.3/P2.3.
- gpu-next зависит от чужой ветки mpv (`lhc70000/mpv`, апстримный RFC #18607 не смержен). В релиз не входит.
