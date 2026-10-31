# CineHDR — план до релиза 1.0

Редакция 4, 2026-10-31. Статусы задач обновляет агент в отчёте; текст задач и
критерии меняет только владелец.

Нумерация: `P<фаза>.<n>` — продуктовые задачи, `B<n>` — сборка/CI/зависимости,
`S<n>` — исследовательские спайки, `C<n>` — чистка и рефакторинг.
Исполнитель: **A** — агент (Gemini/ChatGPT), **O** — владелец (железо, решения).
Размер каждой задачи — один коммит/PR по `AGENTS.md` §12, если не сказано
«серия коммитов». Статусы: `TODO | IN PROGRESS | BLOCKED(<причина>) | DONE(<sha>)`.

## 0. Решения владельца

### Приняты 2026-10-18 (`docs/adr/0008`, Accepted)

- [x] **D1.** Upstreaming снят с целей релиза; цель — мержабельность с upstream `main` (процедура `upstream-diverge:`). `brand:`-коммиты отменены.
- [x] **D2.** Гейт `auto`: env-заглушка заменяется runtime-критерием (CM-global ∧ GTK opt-in ∧ монитор под окном в HDR ∧ caps достаточны). Приёмка — L3p и L4 (`AGENTS.md` §8).
- [x] **D3.** `GDK_DEBUG=color-mgmt` включён по умолчанию в Flatpak-лаунчере, opt-out `CINEHDR_NO_COLOR_MGMT=1`; нативная сборка — opt-in.
- [x] **D4.** Governance удалён по C1 (после C0): ADR-0006, MODEL_POLICY, MODEL_USAGE_LOG, шаблоны hand-off, GPU_VENDOR_SMOKE_VALIDATION, GPU_NEXT_DEVELOPMENT, TEST_MATRIX. Текстовый тест `tests/test_validation_governance.py` удалён в P0.2.
- [x] **D5.** Scope 1.0: Flatpak x86_64, Wayland; HDR для GNOME ≥ 48 (и Plasma при поддержке со стороны GTK — см. D8); X11 и прочие композиторы — SDR; gpu-next — experimental, выключен; нативная сборка — best effort. Из D5 следует удаление aarch64 из CI (C11).
- [x] **D6.** Fork-строки переводятся: `po/` через `update_translations.sh` в коммитах `i18n:`.
- [x] **D7.** Валидация: в `tools/validation/` (вне `cine_sources`) сохраняются парсер Wayland-трассы с тестами, пиксельный оракул FBO с отрицательными контролями, минимальный автосценарий «SDR → HDR → SDR на паузе + первый опубликованный кадр + корректное завершение», минимальный автоэкспорт снимка для него. Сборщик фактов остаётся в продукте; точки подключения в продукте сохраняются и по умолчанию выключены. Прочие механизмы удаляются только адресно по правилу §6.

### Приняты 2026-10-31 (редакция 4 плана, дополнение к `docs/adr/0008`)

- [x] **D8 (KDE в релизе 1.0).** В `color-management-v1` v2 трансферы `srgb` (9) и `ext_srgb` (10) помечены deprecated в пользу `gamma22` (2) / `compound_power_2_4` (14), и KWin с Plasma 6.7 перестал объявлять `TRANSFER_FUNCTION_SRGB` (подтверждено трассой Gate 2 на KWin 6.7.4 + GTK 4.22.4 в `docs/HDR.md` и внешними репортами). При этом GTK (как 4.22.5 в runtime GNOME 50 — проверено в P2.1/P2.2, так и 4.24.1/`main` в `gdk/wayland/gdkwaylandcolor.c`) биндит `wp_color_manager_v1` версией `MIN(version, 1)` и жёстко требует `TF_SRGB`, уничтожая color manager при его отсутствии (`"Not using color management: Can't create srgb image description"`). Реализовался риск из §9 ред. 3. Поэтому в релизе 1.0 статус KDE = **«SDR + корректный документированный fallback»**: диагностика и Copy Report показывают точную причину (`GTK cannot create sRGB image description: missing TF srgb`), гейт `auto` закрыт, CineHDR штатно работает через тон-маппинг mpv. Параллельно выполняется задача **P2.0**: issue + MR в апстрим GTK (fallback на `gamma22` / параметрический sRGB при отсутствии `TF_SRGB` в `gdk_wayland_color_prepare`). Нативный HDR на KDE переносится в post-1.0 с условием «GTK в Flatpak runtime содержит фикс». Сборка собственного патченного GTK внутри Flatpak отвергнута.
- [x] **D9 (libmpv в продуктовом манифесте).** Продуктовый манифест `build-aux/flatpak/io.github.rusmikev.CineHDR.json` собирает `libmpv` из апстрима (`v0.41.0` + патч `fix-screenshots.diff` из `mpv@c66204b69`, как в upstream Cine — без этого патча в `v0.41.0` ломаются скриншоты при hwdec). Экспериментальный форк `lhc70000/mpv` (`libmpv-gpu-next`) выносится в отдельный манифест `build-aux/flatpak/io.github.rusmikev.CineHDR.gpu-next.json` (не входит в CI по умолчанию). Дублирующий вендорный патч `build-aux/patches/mpv-gpu-next/` удаляется (`теряем: дубликат диффа форка; замена: git-источник в gpu-next манифесте`). Приёмка: L2 + L3p HDR10 на GNOME-AMD на апстримном `libmpv`.
- [x] **D10 (автоматизация доказательств и спайк S1).** Спайк **S1**: проверка запуска установленного Flatpak под headless-композитором в CI (`mutter --headless --virtual-monitor` или `weston --backend=headless` с `color-management=true`) на `llvmpipe` с `hdr-mode='force-hdr'`, `WAYLAND_DEBUG=1`, `LD_PRELOAD=libwayland_conn_tagger.so` и разбором через `parse_wayland_color_trace.py --trace-only --oracle composite_window`. Если композитор объявляет `parametric + srgb + bt2020 + st2084_pq` (проверяется через `wayland_output_hdr.get_cm_caps`) и трасса даёт PASS — сценарий становится релизным гейтом CI; иначе спайк закрывается как N/A с точной причиной. Формат «пакета доказательств» (`env.json`, `copy-report-*.txt`, `stderr.log`, `wayland.log`, `parser.json`, `pixel-measurements.json`, `SHA256SUMS`) стандартизируется для локальных раннеров и CI.
- [x] **D11 (порядок чистки — разблокировка C4 и C9).** Задачи **C4** и **C9** выполняются сейчас, не дожидаясь закрытия аппаратно-отложенных ячеек P1.5: (1) развязка границы импорта в `video_widget.py` через `self._fbo_pixel_oracle_sampler` (поведение при `None` идентично), (2) перенос `gpu_validation.py` и `DiagnosticsExporter` в `tools/validation/`, (3) meson-опция `-Dvalidation=true|false` (по умолчанию `false`) + L1-тест границы продукта (**C22**).
- [x] **D12 (бамп Flatpak runtime).** Переход `org.gnome.Platform` `50 → 51` (GTK 4.24.x) выделяется в отдельную задачу (**B3**) после закрытия GNOME-AMD Phase 1 и до релиза 1.0; приёмка — повтор L2 + L3p HDR10 + сценарии ресайз/выход + проверка frameclock (4K60) и HLG.
- [x] **D13 (git-гигиена и CI).** Ветка `main` защищена (вливание только из `task/*` с зелёным CI, без squash однотемных коммитов, merge-коммит `merge: <задача>`); слитые стейл-ветки и неактуальные worktrees удаляются; сборки-кандидаты под аппаратную приёмку тегируются `cand-YYYYMMDD-<n>` (ячейка `TESTING.md` ссылается на `тег/sha + flatpak-commit`); статус-коммиты `docs(status):` батчатся не чаще одного на сессию проверок владельца.

## 1. Правило параллельности и порядок ближайших работ

Чистка не меняет поведение рендер-пути и контроллера параллельно с открытой
задачей отладки поведения в том же файле. Однако развязка измерительных хуков без
смены поведения (**C4**) и исправление чистого критерия `is_hdr_content` в
`hdr_detection.py` (**C9**) разблокированы сейчас по **D11**.

**Приоритетный порядок на ближайшие две недели:**
`B2 → C4(шаг 1: граница импорта) → P1.9 → C9 → C10 → C18 → C21 → B1 → C4(шаги 2–3) + C22 → S1 → C17 → C19 (с C14) → C20 → P2.0 / P2.3′ → B3 → B4`.

## 2. Phase 0 — подготовка, CI и базовые зависимости

| # | Задача | Исп. | Готово, когда | Статус |
|---|---|---|---|---|
| P0.1 | Зафиксировать `docs/adr/0008` (Accepted) и пересмотр `docs/adr/0007` (гейт `auto`) | O | ADR в репо | DONE (45fb026) |
| P0.2 | Одним коммитом: положить `AGENTS.md`, `GEMINI.md`, `docs/PLAN.md`, `docs/TESTING.md` редакции 3 **и** удалить `tests/test_validation_governance.py` с его регистрацией в `tests/meson.build` | O / A | файлы в `main`; `meson test` и CI зелёные | DONE (45fb026; CI run #91 зелёный) |
| C0 | До C1: создать `tools/validation/README.md` с разделом «Известные дефекты и их проверки» | A | README создан; для каждого инструмента из D7 есть запись | DONE |
| C1 | Условие: C0 DONE. Удалить `docs/MODEL_POLICY.md`, `docs/MODEL_USAGE_LOG.md`, `docs/adr/0006-*`, `docs/GPU_TEST_HANDOFF_TEMPLATE.md`, `docs/GPU_VENDOR_SMOKE_VALIDATION.md`, `docs/GPU_NEXT_DEVELOPMENT.md`, `TEST_MATRIX.md`; из `.gitignore` убрать упоминания удалённых артефактов | A | `meson test` зелёный; нет действующих ссылок на удалённые документы | DONE (файлы C1 удалены, .gitignore очищен, meson test зелёный) |
| C2 | Тесты и гарнитуры — серия коммитов (`parse_wayland_color_trace.py` → `tools/validation/`, раннеры, `test_pixel_pipeline.py` → `tools/validation/`) | A | тесты зелёные; парсер трассы запускается из `tools/`, контроли проходят | DONE (c989619) |
| C3 | Корень: убрать валидационные и бенчмарк-скрипты из корня (`run_dev.py` и `update_translations.sh` остаются) | A | в корне из скриптов только `run_dev.py` и `update_translations.sh` | DONE |
| C15 | `hdr_diagnostics.py`: удалить дубликаты форматтеров/хелперов (импорт из `diagnostics_report`) | A → O | снимок идентичен до/после; O: диалог открывается, обновляется, Copy работает | DONE (код 2a28752; UI-smoke PASS 2026-10-24 a24fe81) |
| P0.3 | Влить upstream `main` (1.8.7); конфликты в пользу upstream вне HDR-пути; версия → `1.8.7.0` | A → O (L2) | SDR-файл играет, управление работает | DONE (4ae7ed5 / a24fe81) |
| C11 | CI: убрать aarch64 (по D5); добавить `ruff check`; оставить `meson test` | A | workflow зелёный | DONE (runs #89–#91 зелёные) |
| P0.4 | `ruff` конфиг в `pyproject.toml` (`F`, `E9`); ноль F-ошибок без изменения поведения | A | `ruff check` чистый | DONE |
| P0.5 | Базовые Copy Report с каждой машины/DE (SDR-файл, HDR-файл) — исходное состояние | O | отчёты в `validation-reports/` | TODO |
| B2 | CI: заменить `cache-key: flatpak-builder-${{ github.sha }}` на `flatpak-builder-${{ hashFiles('build-aux/flatpak/**', '.gitmodules') }}` (и аналогично в `release.yml`); добавить `concurrency` (`cancel-in-progress: true`); добавить инварианты `AGENTS.md` §2 в `lint`-job (шаги для C6 и C4 — `continue-on-error: true` до закрытия C6/C4, затем строгие) | A | прогон CI ≤ 20 мин при тёплом кэше; `lint` проверяет инварианты §2 | TODO |
| B1 | Манифест (`P-06′`, D9): в `build-aux/flatpak/io.github.rusmikev.CineHDR.json` переключить `libmpv` на апстрим `v0.41.0` (`tag: "v0.41.0"`, `commit: "41f6a645068483470267271e1d09966ca3b9f413"`) **с обязательным вторым source `{"type": "patch", "path": "fix-screenshots.diff"}`** (`mpv@c66204b`, отсутствует в `v0.41.0`); вынести сборку из `lhc70000/mpv` (`libmpv-gpu-next`) в `build-aux/flatpak/io.github.rusmikev.CineHDR.gpu-next.json`; удалить неиспользуемый дубликат `build-aux/patches/mpv-gpu-next/`; обновить `tests/test_flatpak_manifest.py` (пин источника + наличие патча). Тело коммита: «теряем: opengl-next в продуктовой сборке; замена: io.github.rusmikev.CineHDR.gpu-next.json; патч скриншотов — mpv@c66204b, отсутствует в v0.41.0» | A → O (L2, L3p) | CI зелёный; L2 SDR **со скриншотом при активном `hwdec`** (проверяет `fix-screenshots.diff`) + L3p HDR10 PASS на GNOME-AMD с апстримным `libmpv` | TODO |
| S1 | Спайк headless L3p/publication в CI (`P-07′`, D10): скрипт `tools/validation/ci_headless_l3p.sh` (сборка `libwltag.so` внутри `org.gnome.Sdk//50` с `-ldl -lwayland-client -lpthread`, запуск установленного Flatpak под headless `mutter`/`weston` на `llvmpipe` с абсолютным путём `$OUT`, `--filesystem="$OUT"`, `--env=LD_PRELOAD="$OUT/libwltag.so"`, `--env=WAYLAND_DEBUG=1`, `hdr-mode='force-hdr'`, проверка `get_cm_caps` и `parse_wayland_color_trace.py --trace-only --trace "$OUT/wayland.log" --output "$OUT/parser.json" --oracle composite_window --app-id io.github.rusmikev.CineHDR`) | A (скрипт/CI) → O (решение) | вердикт спайка (релизный гейт CI либо N/A с причиной) зафиксирован в `docs/PLAN.md` / `docs/adr/0008` | TODO |
| B3 | Бамп Flatpak runtime `org.gnome.Platform` `50 → 51` (GTK 4.24.x, D12); перед бампом проверить влияние нового frameclock GTK 4.24 на 4K60 и изменения HLG в GTK 4.23+ (CineHDR отдаёт композитору PQ, ожидаемое влияние нулевое) | A → O | L2 + L3p HDR10 + ресайз/выход PASS на runtime 51 | TODO |
| B4 | Влить upstream Cine `v1.8.8` (по процедуре P0.3 / `AGENTS.md` §13); версия → `1.8.8.0` | A → O (L2) | `meson test` и `ruff` зелёные; L2 SDR PASS | TODO |

## 3. Phase 1 — GNOME (AMD-десктоп + HDR-монитор)

| # | Задача | Исп. | Готово, когда | Статус |
|---|---|---|---|---|
| P1.1 | Гейт `auto`: заменить `is_surface_submission_proven()` на `hdr_output_allowed()` в `hdr_detection`; убрать `CINEHDR_FORCE_SURFACE_SUBMISSION`; тесты по всем комбинациям входов; диагностика «Auto gate: allowed / blocked: <reason>» | A → O | тесты; далее P1.3 | DONE (4b322ab; L1 и L2 PASS 2026-10-24 на GNOME-AMD/Flatpak) |
| P1.2 | `GDK_DEBUG=color-mgmt` по умолчанию в `cinehdr.in` (Flatpak) с opt-out `CINEHDR_NO_COLOR_MGMT=1`; `run_dev.py` без изменений; README | A → O (L2) | SDR UI не сломан; диагностика показывает opt-in active | DONE |
| P1.3 | GNOME-AMD, HDR включён в настройках: (0) эталон mpv подтверждён (лог + L3p его поверхности); (1) L3p HDR10 `auto`; (2) L4 HDR10 `auto`; (3) HLG (L3p + L4), WCG-SDR (остаётся SDR), DoVi P8 (L3p + L4), DoVi P5 → SDR с причиной; (4) наблюдение `force-hdr` при выключенном HDR в DE | O | ячейки `TESTING.md` | IN PROGRESS (эталон mpv L3p PASS ac297a0; HDR10 Auto L3p PASS c9741d3 и L4 PASS db3d2ad; HLG Auto L3p PASS 774e61d и L4 PASS 25dc776; WCG-SDR L3 PASS 08fdc46; DoVi P8 L3p PASS d3df0abc/bfe9695; **осталось закрыть**: DoVi P8 L4, DoVi P5 → SDR L3, `force-hdr` при выключенном HDR в DE L3, а также P1.10) |
| P1.4 | Если P1.3 не PASS: локализация слоя по Copy Report и трассе | A ↔ O | PASS в P1.3 | N/A (базовый путь HDR10 и HLG на GNOME-AMD прошёл L3p и L4) |
| P1.5 | Сценарии жизненного цикла на GNOME-AMD: ресайз и fullscreen в проигрывании; переключение HDR в настройках GNOME во время проигрывания; `auto/force-hdr/force-sdr` на лету; плейлист HDR→SDR→HDR (раннер `tools/validation/run_playlist_smoke.py` готов в 7d3f3b1); выход во время проигрывания; отключение монитора | O, баги → A | все доступные на одномониторном стенде ячейки GNOME-AMD PASS | IN PROGRESS («Выход во время проигрывания» L2 PASS 0418a3d9; «Ресайз + fullscreen» L3 PASS 0418a3d9; «`force-sdr`/`force-hdr` на лету» L3 PASS e7c4d221; «Перенос окна HDR↔SDR» — N/A до Phase 3 по решению владельца 2026-10-31; **осталось**: плейлист HDR→SDR→HDR, переключение HDR в DE во время проигрывания, отключение монитора) |
| P1.6 | Peak: упростить `target-peak` auto до «auto = pass-through, пресеты = явные» по ADR-0007 | O → A | решение в ADR-0007 + smoke | DONE (fb4dc18 / d1556fb; L1 12/12; аппаратный smoke PASS 2026-10-31 на Flatpak e7c4d221) |
| P1.7 | README: убрать `experimental-features`; переключатель HDR в настройках GNOME ≥ 48; таблица поддержки | A | README актуален | DONE |
| P1.8 | gpu-next: за настройкой с пометкой «Experimental», fallback на legacy при неудаче создания контекста; DoVi P5 → SDR с причиной в диагностике; из релизных критериев исключён | A | тесты `render_backend` | DONE |
| P1.9 | Запустить автосценарий `publication` на GNOME-AMD (установленный Flatpak, `CINEHDR_GPU_VALIDATION=publication` / `tools/validation/run_wayland_publication_validation.sh`) | O | ячейка `TESTING.md` PASS/FAIL с пакетом артефактов | TODO |
| P1.10 | L3p на GNOME-AMD при включённом `Graphics Offload` (ожидаемый путь — offload-подповерхность либо подтверждённый toplevel) — закрывает оговорку из ячейки HLG L4 | O | ячейка `TESTING.md` | TODO |

## 4. Phase 2 — KDE Plasma (по решению D8: документированный fallback в 1.0 + апстрим GTK)

| # | Задача | Исп. | Готово, когда | Статус |
|---|---|---|---|---|
| P2.1 | В исходниках GTK версии из runtime выяснить, какую TF GTK требует для базовой (sRGB) image description и принимает ли `gamma22` | A | отчёт со ссылками `файл:строка` | DONE (GTK 4.22.5 в `gdkwaylandcolor.c:702` и GTK 4.24.1/`main` строго требуют `TF_SRGB` [9], fallback на `gamma22` отсутствует; при отсутствии `TF_SRGB` GTK уничтожает `wp_color_manager_v1`) |
| P2.2 | По P2.1 обновить `gtk_color_managed()` (требование `TF_SRGB`) и тесты; строки причин — английские | A | тесты | DONE (410a45b; `gtk_color_managed()` возвращает `"GTK cannot create sRGB image description: missing TF srgb"`) |
| P2.0 | Апстрим GTK (D8): подготовить текст issue + MR в GTK (`gdk/wayland/gdkwaylandcolor.c`: принимать `gamma22` / параметрическое описание sRGB, когда композитор реализует `color-management-v1` ≥ v2 и не объявляет deprecated `TF_SRGB`); в диагностике CineHDR уточнить пояснение причины для пользователя | A (текст/патч) → O (отправка) | ссылка на GTK issue/MR в `docs/adr/0008` и `README.md` | TODO |
| P2.3′ | KDE-AMD (D8): подтвердить одним прогоном на Plasma ≥ 6.7 корректный SDR-fallback (Copy Report показывает `Auto gate: blocked: GTK cannot create sRGB image description: missing TF srgb`, caps композитора содержат `gamma22` без `srgb`, трасса L3p подтверждает отсутствие сломанной HDR-разметки, видео играет с тон-маппингом mpv, UI с `GDK_DEBUG=color-mgmt` не выцветший); обновить `README.md` (статус KDE в 1.0 — SDR fallback со ссылкой на ограничение GTK) | O → A | ячейки KDE-AMD (SDR play + fallback) PASS; HDR-ячейки KDE помечены `N/A (GTK missing TF_SRGB fallback)` | TODO |
| P2.4 | Если в P2.3′ обнаружится искажение SDR UI на KWin при `GDK_DEBUG=color-mgmt` — добавить авто-opt-out по идентификатору композитора/отсутствию `TF_SRGB` в лаунчере | O → A | SDR UI на KDE-AMD нормальный | TODO (условная по результату P2.3′) |
| P2.5 | Базовые сценарии жизненного цикла (SDR play, ресайз/fullscreen, выход во время проигрывания) на KDE-AMD | O → A | ячейки KDE-AMD | TODO |

## 5. Phase 3 — Intel / NVIDIA (ноутбук)

| # | Задача | Исп. | Готово, когда | Статус |
|---|---|---|---|---|
| P3.1 | Убрать проверку `get_dmabuf_formats().get_n_formats() == 0` из `_check_hdr_support_uncached` (не относится к GL-текстурному пути; блокирует NVIDIA); оставить только там, где решается offload | A | тесты | IN PROGRESS (код и L1-тесты готовы в 4ebb263/5913d3f; аппаратная проверка на NVIDIA в P3.4/P3.5) |
| P3.2 | Intel Mesa, GNOME: SDR L2/L3; hwdec `vaapi` vs `vaapi-copy` (в продукте не форсировать `vaapi-copy`); 4K60 без пропусков; путь `set_sync`; GL errors = 0 | O → A | ячейки GNOME-Intel | TODO |
| P3.3 | Intel + внешний HDR-монитор (если HDMI на Intel): L3p + L4 + перенос окна HDR↔SDR панель (отложенный из P1.5) | O | ячейка или N/A | TODO |
| P3.4 | NVIDIA PRIME во Flatpak: рабочий набор env (`__NV_PRIME_RENDER_OFFLOAD=1`, `__GLX_VENDOR_LIBRARY_NAME=nvidia`, EGL vendor) так, чтобы GTK и mpv были на одном GPU; README «advanced» | O → A | SDR PASS на NVIDIA | TODO |
| P3.5 | NVIDIA: fence/`set_sync` без stall, нет tearing, fp16-surface при HDR (если HDMI на dGPU) либо честный `Unknown/blocked: <reason>`; GSK renderer pin `gl` подтверждён или заменён | O → A | ячейки GNOME-NVIDIA, KDE-NVIDIA (SDR обязательно) | TODO |
| P3.6 | Известные ограничения по вендорам → README | A | README | TODO |

## 6. Чистка и рефакторинг

| # | Задача | Когда | Готово, когда | Статус |
|---|---|---|---|---|
| C4 | Серия коммитов (по D11 — **сейчас**). Вынести валидацию из устанавливаемого продукта в объёме D7: **(1)** развязать импорт в `src/video_widget.py` — убрать `from .gpu_validation import read_fbo_patch_samples`, добавить слот `self._fbo_pixel_oracle_sampler = None`, который валидационный инструмент выставляет вместе с `_fbo_pixel_oracle_cb`; **(2)** перенести `src/gpu_validation.py` → `tools/validation/gpu_validation.py` и `DiagnosticsExporter` → `tools/validation/diagnostics_export.py` (`collect_video_output_facts`/`build_video_output_report` остаются в `src/diagnostics_report.py`); удалить лишние режимы (`quick/soak/sdr-soak/lifecycle/transition/settling`) по одному коммиту со строками «теряем / замена»; **(3)** добавить meson-опцию `validation` (`default: false`): при `true` `tools/validation/*.py` ставятся в `cinehdr/validation/`, `main.py` при `CINEHDR_GPU_VALIDATION` импортирует `cinehdr.validation` и при отсутствии модуля явно завершается с ошибкой в лог | **СЕЙЧАС (D11)** | снимок отчёта идентичен до/после; `grep -rn "gpu_validation\|DiagnosticsExporter" src/meson.build src/*.py` пусто (кроме ленивого импорта `cinehdr.validation` в `main.py`); L2 владельца PASS; C22 зелёный | TODO |
| C5 | `wayland_output_hdr.py` (R7): удалить синхронные `probe_outputs`/`_probe_outputs_unsafe`/`_query_one_output` (замена: асинхронный `WaylandOutputHdrSource` + кэш); `get_cm_caps` и `get_hdr_unsupported_reason` читают кэш | после C21 | характеристический тест снимка до/после; O: диагностика показывает caps и состояние монитора | TODO |
| C6 | Единый helper `utils.mpv_prop(player, name, default=None)`; заменить `_get_property` в `hdr_detection._query_mpv_prop`, `render_backend.render_runtime_diagnostics`, `render_backend.media_frame_evidence`, `video_widget._capture_video_selection_for_context_restore` и `get_mpv_prop` в `diagnostics_report`; `self.mpv._dovi_rpu_*` → состояние окна | сейчас / параллельно с C21 | grep из `AGENTS.md` §2 пустой; фиксированный снимок → тот же отчёт | TODO |
| C7 | `hdr_controller.py`: оставить `load_hdr_config`/`save_hdr_config`; удалить устаревшие обёртки `load/save_hdr_setting`, `load/save_hdr_mode`, property `hdr_enabled` (и делегаты в `video_widget`); `hdr-target-prim` удалить из схемы и кода; убрать `getattr(self, "_initialized", True)` | после P1.5 | тесты `test_hdr`; O: L2 + переключение режимов | TODO |
| C8 | `video_widget.py` (расширенная по R2): убрать `getattr/hasattr` на собственных атрибутах, `try/except` вокруг `connect`; один кэш решения HDR; удалить мёртвые для D5 ветки GTK < 4.16 (`glClientWaitSync`) и старого PyGObject (`builder.build()` без `destroy-notify`); заменить per-frame замыкание `on_texture_release` на предсвязанный `slot.release_cb` в `GLFramebufferPool` (`data=None`); вынести первокадровый экспорт в `FirstFrameObserver`; разбить `_render_pending_frame` на `_acquire_target()`, `_render_into(slot)`, `_publish(slot)`. **ВАЖНО:** `install_python_mpv_depth_compat` сохраняется + добавляется канареечный тест (`MpvRenderParam('depth', 8)` без шима обязан бросать `TypeError`) | после P1.9 (или по шагам R2) | фиксированный снимок → тот же отчёт; автосценарий `publication` PASS; FBO drops 0; L2 владельца | TODO |
| C9 | `hdr_detection.is_hdr_content` → критерий строго по трансферу `HDR_TRANSFERS = frozenset({"pq", "hlg"})` (`AGENTS.md` §5.4) + инвертировать старые assertions `sig-peak > 1.0 ⇒ HDR` в `tests/test_hdr.py`; `_cached_support` не трогается напрямую из `wayland_output_hdr` (только через `invalidate_support_decision()`) | **СЕЙЧАС (D11)** | `meson test` зелёный; повтор L3 WCG-SDR и HDR10 Auto | TODO |
| C10 | `gtk_cm_policy.py`: `get_gtk_version()` возвращает `None` при ошибке `(OSError, AttributeError)` (с `logging.warning`), `apply_gsk_renderer_policy()` при `None` не выставляет `GSK_RENDERER`; уточнить docstring со ссылкой на GTK 4.18 NEWS (`gl` = новый NGL renderer) | **СЕЙЧАС** | тесты `test_color_mgmt_opt_in` зелёные | TODO |
| C12 | ADR 0001 и 0004 сократить до «контекст / решение / последствия» (≤ 1 страница); 0007 переписать под новый гейт | Phase 0–1 | `docs/adr/` ≤ 4 файла | DONE |
| C13 | `mpv_gl_area.VideoGLArea`: заменить использование в `window.py` прямым `MpvVideoWidget`. Примечание: сам модуль `src/mpv_gl_area.py` остаётся в продукте, так как содержит upstream-класс `ThumbPreviewGLArea` (`window.py:45`); неиспользуемый 8-строчный подкласс `VideoGLArea` внутри него можно удалить отдельным коммитом | любое | — | DONE (79f2188) |
| C14 | `docs/ARCHITECTURE.md`: одна страница с диаграммой конвейера, картой инвариантов `AGENTS.md` §4/§5/§9, точками подключения измерений и ограничениями GTK/KWin/python-mpv | после P1.3 | файл создан и соответствует коду `7d3f3b1` | DONE (2026-10-31) |
| C16 | `tests/test_vendor_smoke.py` + `tests/test_nvidia.py`: удалить только после того, как автосценарий первого опубликованного кадра и корректного завершения из C4 PASS хотя бы на одной ячейке (Intel или NVIDIA), либо с отдельным обоснованием | после C4 + прогона O | тело коммита содержит «теряем / замена» со ссылкой на отчёт | TODO |
| C17 | Ревизия незарегистрированных тестов и C-пробников в `tests/` и `tools/validation/` (серия однотемных коммитов, по одному коммиту на пункт, со строками «теряем / замена»): **(a)** удалить сломанный `tests/test_attempt3_regressions.py` (в теле: «теряем: регрессии attempt3 (ссылаются на удалённый скрипт `capture_wayland_trace_attempt3_corrected.sh`); замена: `test_wayland_publication_scenario.py` (72/72) и `test_wayland_color_trace.py` в `tools/validation/`»); **(b)** зарегистрировать `tools/validation/test_wayland_conn_tagger.py` (14/14 OK) в `tests/meson.build`; **(c)** перенести `tests/test_wayland_publication_scenario.py` (72/72 OK) в `tools/validation/` и зарегистрировать в `tests/meson.build`; **(d)** `dovi_rpu_ab_egl.c` / `run_dovi_rpu_ab.py` и `pixel_validator_egl.c` / `run_pixel_pipeline.py` / `run_reference_tests.py` — решение строго по правилу D7 (актуальный инвариант + конкретный дефект в `KNOWN_DEFECTS.md`, а не наличие текстовой проверки в `test_render_backend.py:575`, нарушающей дух `AGENTS.md` §11): DoVi A/B требует нативного `gpu-next` (вне scope 1.0 по D5/D7) — кандидат на удаление вместе с текстовым тестом `test_dovi_ab_probe_is_embedded_negative_control`; для `pixel_validator_egl.c` / `run_pixel_pipeline.py` при сохранении — перенос в `tools/validation/` с синхронным обновлением путей в `tools/validation/test_pixel_pipeline.py` | после C4 | в `tests/` только зарегистрированные в `meson.build` unit-тесты и `generate_patterns.py` | TODO |
| C18 | Гигиена дерева и `.gitignore`: выполнить `git rm --cached validation-reports/gate2w-run7-*.json`; очистить `.gitignore` от мусорных строк (`.git`, `/src/__pycache__` → `__pycache__/`, старых flatpak/report имён) | **СЕЙЧАС** | `git ls-files validation-reports/` пусто; `.gitignore` чистый | TODO |
| C19 | Канонизация `docs/` (`AGENTS.md` §14): убедиться, что все актуальные факты из `docs/HDR.md` и `docs/ROADMAP.md` (включая запись о пассивной трассе Gate 2 на `GTK 4.22.4 + KWin 6.7.4` — перенесена в `docs/TESTING.md` и `docs/ARCHITECTURE.md`) сохранены в каноничных документах; обновить ссылку в `src/wayland_output_hdr.py:68`; удалить `docs/HDR.md` и `docs/ROADMAP.md` со строками «теряем / замена» | после C14 | список файлов в `docs/` строго совпадает с `AGENTS.md` §14 | TODO |
| C20 | Разделить `tools/validation/README.md` (192 КБ) на два документа ≤ 40 КБ: `tools/validation/README.md` (инструменты, как запускать, что доказывают, точки подключения в продукте) и `tools/validation/KNOWN_DEFECTS.md` (реестр «исторический дефект / инвариант → воспроизведение → тест») | после C4 | оба файла ≤ 40 КБ | TODO |
| C21 | Унификация гейта в `src/hdr_detection.py` (R1): единая функция `evaluate_hdr_gate(*, connector=None, allow_probe=False, env=None) -> GateDecision(allowed: bool, reason: str, facts: dict)`; `check_hdr_support`, `hdr_output_allowed`, `get_hdr_unsupported_reason` становятся тонкими обёртками над ней с единственным кэшем (§5.5); убрать `except Exception: pass` | после C9 | таблица входов `test_hdr.py` зелёная; L2 владельца | TODO |
| C22 | L1-тест «граница продукта»: импорт `src.window` / `src.video_widget` не загружает `gpu_validation` в `sys.modules`; при `-Dvalidation=false` список `cine_sources` в `src/meson.build` не содержит модулей валидации | вместе с C4 | тест зарегистрирован в `tests/meson.build` и зелёный | TODO |

## 7. Правила рефакторинга и декомпозиции (R0–R7)

Общие правила (**R0**), дополняющие `AGENTS.md` §10 и §12:
1. Рефакторинг выполняется в отдельной ветке `task/refactor-<модуль>` и содержит **только** `refactor:`-коммиты (никаких `feat:` или `fix:` внутри).
2. Перед первым коммитом создаётся или фиксируется **характеризационный тест**:
   - для диагностики — фиксированный `DiagnosticSnapshot` → байт-в-байт тот же `build_video_output_report`;
   - для гейта — таблица входов → `(allowed, reason)`;
   - для рендер-пути — автосценарий `publication` PASS до и после.
3. Шаг ≤ 400 строк диффа, каждый коммит проходит тесты самостоятельно. Перемещение кода и изменение логики — **разные коммиты** (`git diff --color-moved` должен показывать чистый перенос).
4. В рефакторинге запрещено менять публичные имена, порядок наблюдателей mpv, порядок завершения (`AGENTS.md` §4.5) и английские строки причин в диагностике (они входят в оракулы).
5. Удаление любой ветки кода — только со строками «теряем / замена» и предварительным `grep -rn` по динамическому использованию.
6. Целевые размеры при декомпозиции: модуль продукта ≤ 800 строк, функция ≤ 80 строк; тестовый файл ≤ 1500 строк; README ≤ 40 КБ.

Адресные планы рефакторинга:
- **R1 (`hdr_detection.py`, задача C21):** единая функция `evaluate_hdr_gate(*, connector, allow_probe, env) -> GateDecision(allowed, reason, facts)`, над которой работают `check_hdr_support`, `hdr_output_allowed` и `get_hdr_unsupported_reason`. Кэш решения — в одном месте (`§5.5`). Чтение свойств mpv переводится на `utils.mpv_prop` (C6).
- **R2 (`video_widget.py`, задача C8):**
  - *(a)* удалить мёртвые для D5 ветки совместимости: `glClientWaitSync`-fallback (GTK < 4.16 без `set_sync`) и fallback `builder.build()` без `destroy-notify` (`_fallback_slot`). Шим `install_python_mpv_depth_compat` **сохраняется** (пиннованный `python-mpv 1.0.8` падает с `TypeError: int() argument after ** must be a mapping, not int` на `MpvRenderParam("depth", 8)`); к нему добавляется **канареечный unit-тест**: вызов непатченного конструктора `MpvRenderParam('depth', 8)` обязан бросать `TypeError` — когда апстрим `python-mpv` исправит баг, падение канарейки станет сигналом удалить шим (параллельно можно отправить фикс в апстрим `python-mpv`);
  - *(b)* разбить `_render_pending_frame` на `_acquire_target()`, `_render_into(slot)` и `_publish(slot)`;
  - *(c)* заменить `getattr/hasattr` на собственных атрибутах прямым доступом (`§10`); убрать per-frame аллокацию замыкания `on_texture_release`, предсвязав callback освобождения `slot.release_cb` один раз при создании слота в `GLFramebufferPool` (5 слотов на пул) и вызывая `builder.build(destroy=slot.release_cb, data=None)`. *(Проверено в runtime GTK 4.22.5: передача Python-объекта `data=slot` в `builder.build()` бросает `ValueError: Pointer arguments are restricted to integers, capsules, and None`, так как в GIR `gdk_gl_texture_builder_build` аргумент `data` описан как сырой `gpointer` с `closure_index = -1` без аннотации `(closure destroy)` — именно поэтому в коде стоял `except (TypeError, ValueError)` и `data=id(slot)`)*. При переиспользовании `Gdk.GLTextureBuilder` гарантировать явную перезапись всех полей включая `set_sync` на каждом кадре;
  - *(d)* вынести координацию первокадрового автоэкспорта (`_exported_media_generation`, обращение к `window._diagnostics_exporter`) из `video_widget.py` в отдельный `FirstFrameObserver`, чтобы `video_widget` не зависел от внутренностей `window` (`§3`).
- **R3 (`window.py`, 2334 строки / 90 КБ):** составить карту методов и поэтапно выделить HDR-UI проводку и координацию диагностики, не трогая upstream-участки без крайней необходимости, чтобы `git merge upstream/main` проходил без конфликтов. Последовательность завершения (`§4.5`) остаётся явно в `window.py`.
- **R4 (`diagnostics_report.py`, 1319 строк / 60 КБ):** разделить на `diagnostics_facts.py` (сборщик `collect_video_output_facts`) и `diagnostics_format.py` (форматтер `build_video_output_report`) с реэкспортом из `diagnostics_report.py` для сохранения контракта `§9`.
- **R5 (крупные тесты):**
  - `tests/test_hdr.py` (4616 строк / 224 КБ) разделить по тестируемым модулям: `test_hdr_content.py`, `test_hdr_gate.py`, `test_hdr_controller.py`, `test_video_widget.py`, `test_window_lifecycle.py` с отдельной регистрацией в `tests/meson.build` (ускоряет параллельный `meson test`);
  - `tools/validation/test_wayland_color_trace.py` (5342 строки / 320 КБ) разделить по оракулам/представлениям, вынеся громоздкие многострочные лог-фикстуры в `tools/validation/fixtures/*.log`.
- **R6 (`tools/validation`):** разделить `README.md` по C20; `wayland_conn_tagger.c` и его тест `test_wayland_conn_tagger.py` зарегистрировать в `tests/meson.build` (C17b).
- **R7 (`wayland_output_hdr.py`, 1378 строк / 52 КБ, задача C5):** удалить синхронные `probe_outputs`/`_probe_outputs_unsafe`/`_query_one_output` после полного перевода чтения на кэш асинхронного `WaylandOutputHdrSource`.

## 8. Phase 4 — релиз

| # | Задача | Исп. | Статус |
|---|---|---|---|
| P4.1 | Влить upstream `main` повторно (если выйдет после B4); версия `1.8.x.1`; `metainfo` `<release>` с датой и changelog | A | TODO |
| P4.2 | README финальный: установка из GitHub Release; требования (GNOME ≥ 48, Wayland, HDR-монитор; статус KDE по D8); таблица поддержки DE × GPU из `TESTING.md`; ограничения (X11 — SDR, DoVi P5 — SDR, KDE Plasma ≥ 6.7 — SDR fallback до фикса в GTK, NVIDIA — см. раздел); как снять Copy Report для баг-репорта | A | TODO |
| P4.3 | `CHANGELOG.md`; скриншоты HDR-меню и диагностики | A/O | TODO |
| P4.4 | Тег `v1.8.x.1` (с подписью), GitHub Release с `CineHDR-x86_64.flatpak` из CI, release notes | O | TODO |
| P4.5 | (после релиза, опционально) Flathub: отдельная задача — домен для app-id, ревью манифеста | — | TODO |

## 9. Критерии релиза 1.0

1. `docs/TESTING.md`:
   - **GNOME-AMD** — все сценарии PASS, включая L3p и L4 (перенос окна меж мониторами — в Phase 3 при наличии второго экрана);
   - **GNOME-Intel** — SDR PASS, HDR PASS или N/A с причиной;
   - **GNOME-NVIDIA** — SDR PASS, HDR задокументирован;
   - **KDE-AMD** (по D8) — SDR PASS + fallback PASS с точной причиной в диагностике (`GTK cannot create sRGB image description: missing TF srgb`), HDR-ячейки KDE = N/A со ссылкой на апстримный GTK-issue (P2.0);
   - **KDE-NVIDIA** — SDR PASS.
2. Нет крашей и зависаний на: выход во время проигрывания, отключение монитора, переключение HDR в DE во время проигрывания, серия ресайз/fullscreen, смена файла HDR↔SDR.
3. `auto` включает HDR на GNOME-AMD без ручных env-переменных (внутри Flatpak).
4. Диагностика и Copy Report — английские, содержат все факты из `AGENTS.md` §9.
5. CI зелёный (с рабочим кэшем `flatpak-builder` и проверками инвариантов `AGENTS.md` §2), `ruff` чистый, тесты герметичны; корень репозитория — только `run_dev.py`, `update_translations.sh`, конфиги; устанавливаемый продукт не содержит `gpu_validation`/`DiagnosticsExporter` (C22 зелёный); `libmpv` в продуктовом манифесте — апстрим `v0.41.0` + `fix-screenshots.diff` (B1); документы — каноничный набор из `AGENTS.md` §14.
6. README не содержит утверждений, не подтверждённых ячейкой `TESTING.md`.
7. Автосценарий `publication` (`tools/validation`) PASS на GNOME-AMD (P1.9) и (если спайк S1 PASS) в CI на релизном коммите; перечень `tools/validation/README.md` / `KNOWN_DEFECTS.md` воспроизводим.

## 10. Известные риски и реализовавшиеся ограничения

- **KDE Plasma ≥ 6.7 + GTK 4.22/4.24 (реализовался, решение D8):** KWin не объявляет deprecated `TF_SRGB` (9), а GTK 4.22.5 и 4.24.1 жёстко требуют его в `gdk_wayland_color_prepare` и отключают color management. В 1.0 это закрывается честным SDR-fallback с точной причиной в диагностике и отправкой фикса в апстрим GTK (P2.0).
- **Композиторы без реального HDR-тонмаппинга:** на композиторах, где GTK молча конвертирует HDR-описание в SDR, пользователь увидит картинку хуже, чем с тон-маппингом mpv. Страховка — гейт `auto`, `force-sdr` и точная причина в диагностике.
- **Эталон mpv:** требует проверки фактического выходного пути (лог + L3p трасса поверхности `mpv`), так как параметры командной строки сами по себе не гарантируют применения Vulkan/Wayland HDR.
- **gpu-next:** зависит от несмерженного PR `mpv#18607` / форка `lhc70000/mpv`. Вынесен из продуктового манифеста в отдельный `io.github.rusmikev.CineHDR.gpu-next.json` (D9) и не блокирует релиз 1.0.
