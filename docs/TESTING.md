# CineHDR — матрица проверок

Уровни доказательств, определения PASS/FAIL/UNCLEAR и формат запроса —
`AGENTS.md` §8. Ячейка: `NOT_RUN | PASS | FAIL | UNCLEAR | N/A` + дата +
короткий sha, например `PASS 2026-11-02 a1b2c3d`. Полные отчёты — локально в
`validation-reports/<дата>-<DE>-<GPU>-<сценарий>.txt` (в `.gitignore`).
Статусы ячеек обновляет агент по отчёту владельца; строки, столбцы и критерии
меняет только владелец.

## Окружения

| Столбец | DE / композитор | GPU | HDR-дисплей | Примечание |
|---|---|---|---|---|
| GNOME-AMD | GNOME ≥ 48 / Mutter | AMD (Mesa) | да | основной стенд |
| GNOME-Intel | GNOME ≥ 48 / Mutter | Intel (Mesa) | по возможности | ноутбук |
| GNOME-NVIDIA | GNOME ≥ 48 / Mutter | NVIDIA proprietary, PRIME | по возможности | ноутбук |
| KDE-AMD | Plasma ≥ 6.3 / KWin | AMD (Mesa) | да | |
| KDE-NVIDIA | Plasma ≥ 6.3 / KWin | NVIDIA proprietary, PRIME | по возможности | |

## Сценарии

| Сценарий | Уровень | GNOME-AMD | GNOME-Intel | GNOME-NVIDIA | KDE-AMD | KDE-NVIDIA |
|---|---|---|---|---|---|---|
| SDR play (управление, seek, выход) | L2/L3 | PASS 2026-10-24 a24fe81 (L2) | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN |
| Эталон mpv подтверждён по выходному пути (API/контекст + L3p его поверхности) | L3/L3p | PASS 2026-10-25 4fe5462 (RADV/waylandvk HDR10; parser 04c1252; L3p trace ac297a0, conn#4 fd#11, surface#5, effective_buffer#65) | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN |
| HDR10 `auto` — протокольная трасса | L3p | PASS 2026-10-25 c9741d3 (installed Flatpak db3d2ad, composite toplevel; trace d20eb02, verified conn#1, surface#58, Rec.2100 PQ, effective buffer#79; 0 untracked lines) | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN |
| HDR10 `auto` — визуальная приёмка на паттерне | L4 | PASS 2026-10-30 db3d2ad (владелец сравнил один HDR10-паттерн fullscreen на HDMI-1 при неизменных настройках GNOME: ступени 600/800/1000 нит различимы в CineHDR и mpv, 1000 не сливается с 800; эталонный mpv ранее подтверждён L3p и runtime-логом в этой конфигурации; лог текущего запуска сохранил только stderr, фотометрия не проводилась) | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN |
| HLG `auto` — протокольная трасса | L3p | PASS 2026-10-25 774e61d (GNOME Platform 50; исходники worktree поверх установленного Flatpak; trace 5ee3b22, verified conn#1 fd#8, composite surface#57, Rec.2100 PQ, effective buffer#74; 0 untracked; composite path при выключенном offload) | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN |
| HLG `auto` — визуальная приёмка на паттерне | L4 | PASS 2026-10-31 25dc776 (GNOME-AMD, HDR включён; тот же HLG-паттерн fullscreen на HDMI-1 без смены настроек: владелец различил все шесть вертикальных столбцов в CineHDR Auto и mpv target-peak=1000, переходы сопоставимы; нижняя полоса CineHDR между 18% и 50%; mpv Vulkan/Wayland HDR10 ST2084 в новом runtime-логе a8b3a34, L3p эталона с 1000 ранее PASS 431b844. Это визуальный PASS для диагностического эталона с явным 1000, не фотометрия; CineHDR Graphics Offload был enabled и текущая L3p-поверхность не проверялась) | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN |
| WCG-SDR остаётся SDR | L3 | PASS 2026-10-31 08fdc46 (GNOME-AMD, HDR включён, Auto/legacy; установленный Flatpak d3df0abc; FFV1 gbrp10, bt.2020 + sRGB; Copy Report: HDR Disabled (SDR Content), GL_RGBA8, Gdk.ColorState sRGB, target-trc/prim auto; владелец видел нормальное воспроизведение без искажений; физическая цветопередача не измерялась) | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN |
| DoVi Profile 8 | L3p/L4 | L3p PASS 2026-10-31 d3df0abc (GNOME-AMD, Auto/legacy, HDR10-совместимый базовый слой; владелец видел нормальное воспроизведение; Copy Report: Profile 8 base-layer fallback, Rec.2100 PQ, GL_RGBA16F; traces 22fa985/352178f и новый прогон bfe9695 [SHA-256 bfe969555ef61d8693922acf3eb7f4e79ea05e24ccb8179ed6895d2eb51731b9], verified conn#1 fd#8, composite surface#57, effective buffer#74, L3p parser PASS; в первом прогоне было 64 предупреждения о нескольких RPU в AU, в новом прогоне RPU-предупреждений нет, причина их появления не установлена; в новом прогоне exit code приложения 0 и парсера 0, но при закрытии остаётся video output initialization failed; сборка 95ecaa9 здесь не проверялась, обработка RPU не доказана). L4 NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN |
| DoVi Profile 5 → SDR с причиной | L3 | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN |
| `force-sdr` / `force-hdr` на лету | L3 | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN |
| `force-hdr` при выключенном HDR в DE — наблюдение задокументировано | L3 | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN |
| Ресайз + fullscreen во время проигрывания | L3 | UNCLEAR 2026-10-31 6673515 (GNOME-AMD, HLG Auto/legacy; владелец видел различимые полосы и не заметил зависаний при входе/выходе из fullscreen; Copy Report: GL_RGBA16F, Rec.2100 PQ, 1432 опубликованных текстуры, 0 сбоев FBO; после снимка stderr записал `video output initialization failed` при end-file, причина и код выхода не установлены) | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN |
| Перенос окна HDR ↔ SDR панель | L3 | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN |
| Переключение HDR в DE во время проигрывания | L3 | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN |
| Плейлист HDR → SDR → HDR | L3 | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN |
| Выход во время проигрывания (нет краша) | L2 | PASS 2026-10-31 0418a3d9 (GNOME-AMD, установленный Flatpak 0418a3d9 [исходники 8e4d820, исправление 95ecaa9], HLG Auto/legacy; закрытие во время воспроизведения, видео двигалось нормально, окно закрылось без повторного открытия, exit code 0; Copy Report: GL_RGBA16F, Rec.2100 PQ, Auto gate allowed; в stderr отсутствуют mpv end-file error и таймаут shutdown; лог p15-shutdown-20261031T074014Z; ранее SDR PASS 2026-10-24 a24fe81) | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN |
| Отключение монитора во время проигрывания | L3 | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN |
| 4K60 без пропусков кадров | L3 | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN |
| hwdec активен (vaapi / nvdec) | L3 | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN |
| Автосценарий `publication` (tools/validation) | L3 | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN | NOT_RUN |

HDR в столбце подтверждён, когда для HDR10 одновременно PASS в строках L3p и L4.

Строка «`force-hdr` при выключенном HDR» фиксирует наблюдение без заранее
заданного ожидания. После первого наблюдения на конфигурации владелец может
записать его как негативный контроль для этой конфигурации — и только для неё.

## Эталон

Эталон — нативный mpv на том же мониторе и при тех же настройках DE, в
конфигурации, уже проверенной владельцем на этой конфигурации (GNOME:
Vulkan/Wayland). Точная команда фиксируется здесь владельцем и не меняется
между ячейками без записи в отчёте:

```bash
# <команда, подтверждённая владельцем; обязательные элементы:>
mpv --vo=gpu-next --gpu-api=vulkan --gpu-context=waylandvk \
    --target-colorspace-hint=yes --msg-level=vo=v /path/pattern-hdr10.mkv
```

Эталон считается подтверждённым по данным его фактического выходного пути,
а не по параметрам запуска:
1. лог mpv (`--msg-level=vo=v`) показывает выбранные API и контекст
   (Vulkan / Wayland) — то, что запрошено, могло не примениться;
2. L3p-трасса собственной поверхности mpv (`WAYLAND_DEBUG=1`, парсер с
   app_id `mpv`) показывает распознанное HDR-описание и commit.

HDR-индикатор монитора/DE фиксирует условия эксперимента (выход в HDR-режиме),
но не подтверждает HDR-передачу конкретного окна mpv. При недостаточных
данных эталон — UNCLEAR, и все сравнения L4 этой сессии — UNCLEAR.

## Условия L3p

Ожидаемый путь записывается до запуска (композитный toplevel или
offload-подповерхность); фактический путь определяется по трассе.
`GtkGraphicsOffload = enabled` — разрешение, не доказательство перехода на
подповерхность. Трасса:

```bash
flatpak run --env=WAYLAND_DEBUG=1 io.github.rusmikev.CineHDR /path/pattern-hdr10.mkv \
  2> validation-reports/<дата>-<ячейка>-wayland.log
python3 tools/validation/parse_wayland_color_trace.py \
  validation-reports/<дата>-<ячейка>-wayland.log    # аргументы и путь — см. tools/validation/README.md
```

PASS = на поверхности, несущей видео (резолвится парсером по
`get_xdg_surface → get_toplevel → set_app_id` либо `get_subsurface`), после
`ready`/`ready2` есть `set_image_description` с представлением, которое парсер
распознаёт как HDR по семантике используемой версии протокола (PQ, HLG,
`ext_linear` с `max_lum > reference_lum`, предопределённые описания вроде
`create_windows_scrgb`), и последующий `commit`. FAIL = представление
распознано как SDR или описание отсутствует при HDR-контенте в `auto` с
открытым гейтом. UNCLEAR = поверхность не резолвится, трасса неполна,
представление не распознано, наблюдённый путь не совпал с ожидаемым. `wp_image_description_v1.failed` оценивается относительно проверяемой цепочки
описания цвета. `wl_display.error` на соединении, несущем проверяемую
поверхность, исключает PASS прогона независимо от указанного объекта. Ошибка
заведомо другого соединения фиксируется отдельно. Если принадлежность
соединению установить нельзя — UNCLEAR.

## Условия L4

- Fullscreen без UI; синтетический паттерн с подписанными патчами
  (100/203/400/600/800/1000 нит, PQ, bt.2020); тот же монитор, размер и
  настройки DE, что у эталона; эталон и CineHDR — последовательно, без смены
  настроек между ними.
- Критерии записываются в запросе до запуска. Базовые для HDR10-паттерна:
  (1) ступени 600/800/1000 нит различимы в CineHDR так же, как в эталоне;
  (2) ступень 1000 не слита с 800 (нет клиппинга), если в эталоне не слита.
- PASS — критерии выполнены. FAIL — воспроизводимо нарушены при корректных
  условиях (эталон подтверждён, условия соблюдены, повтор даёт то же).
  UNCLEAR — иначе; в отчёте указывается, что именно не позволило сделать вывод.
- Результат — визуальная приёмка, без заявления о колориметрической точности.

## Тестовые файлы

Набор фиксируется владельцем локально (пути и названия в репозиторий не попадают):
синтетический HDR10-паттерн с подписанными патчами 100/203/400/600/800/1000 нит
(PQ, bt.2020), HDR10 (кино, 1000 нит), HLG, SDR bt.709, WCG-SDR (bt.2020 +
bt.1886), Dolby Vision Profile 8, Dolby Vision Profile 5, 4K60 HEVC.
В Copy Report файл идентифицируется типом, не именем.

## Автоматические сценарии (tools/validation, D7)

Включаются только `CINEHDR_GPU_VALIDATION=<режим>` (сборка с meson-опцией
`validation=true`); без переменной измерения не выполняются. Артефакты —
`validation-reports/<дата>-<ячейка>-auto/`.

| Сценарий | Что доказывает | Артефакты |
|---|---|---|
| `publication`: SDR → HDR → SDR на паузе | 8-бит/16-бит FBO и color state текстуры меняются вслед за контентом; пиксели FBO в HDR-фазе отличаются от SDR; возврат в SDR воспроизводит исходные значения (регрессия «старые HDR-пиксели после HDR → SDR на паузе») | JSON пиксельных измерений, текстовый снимок диагностики |
| первый опубликованный кадр | состояние конвейера в момент первого медиа-кадра, а не после него | автоэкспорт снимка `collect_video_output_facts` |
| корректное завершение | `mpv_render_context_free` → `quit` без краша и без утечки слота FBO | код выхода, лог |

Перечень «известный дефект → проверка» и точки подключения в продукте —
`tools/validation/README.md`; они остаются воспроизводимыми после каждого
переноса (PLAN §6).

## Шаблон записи отчёта

```
validation-reports/2026-11-02-gnome-amd-hdr10-l4.txt
--- условия ---
эталон: <команда; подтверждён: да/нет, чем>
путь (L3p): <toplevel | subsurface>
критерии (L4): <ступени / клиппинг>
--- наблюдение ---
<1–3 строки; для L4 — по каждому критерию>
--- результат ---
PASS | FAIL | UNCLEAR: <причина, следующий шаг>
--- Copy Report ---
<вставка из HDR Diagnostics>
```
