# DLSS Swapper для Linux

Аналог [DLSS Swapper](https://github.com/beeradmoore/dlss-swapper) для Linux и игр, запускаемых через **Steam + Proton**.

- Меняет версии **DLSS**, **DLSS Frame Generation**, **DLSS Ray Reconstruction**, **FSR 3.1** и **XeSS** в играх. Оригиналы сохраняются рядом как `*.dlsss`.
- Ставит **OptiScaler** и **включает генерацию кадров на картах без RTX 40** (RTX 20/30, AMD, Intel): FSR FG или XeSS FG из данных апскейлера игры, либо Nukem `dlssg-to-fsr3` вместо DLSS FG игры.
- **Нативный DLSS Frame Generation на RTX 20/30** через [dlssg_for_sm86](https://github.com/sdli1995/dlssg_for_sm86): настоящий DLSS-G от NVIDIA (2X–6X), а не подмена на FSR. Работает в играх, где DLSS FG уже есть.
- Сам прописывает **параметры запуска Steam** (`WINEDLLOVERRIDES`, переменные DXVK-NVAPI), так что игра запускается из Steam как обычно.
- Находит игры Steam (обычный, Flatpak, Snap), все библиотеки и **сторонние игры, добавленные в Steam** (например, установленные вне Steam), плюс любые папки, которые вы добавите вручную.
- Кнопка обновления: приложение проверяет релизы на GitHub и обновляется само.

![](packaging/io.github.capptainncwc.DlssSwapperLinux.svg)

## Установка

Скачайте `.deb` из [Releases](https://github.com/CapptainnCWC/dlss-swapper-linux/releases) и установите:

```bash
sudo apt install ./dlss-swapper-linux_*_all.deb
```

Зависимости: Python 3.10+, GTK 4, libadwaita ≥ 1.5 (Ubuntu 24.04+, Debian 13+). Пакет `7zip` не обязателен: встроенный распаковщик справляется с архивами OptiScaler.

Запуск без установки:

```bash
git clone https://github.com/CapptainnCWC/dlss-swapper-linux && cd dlss-swapper-linux && ./dlss-swapper-linux
```

## Как это работает

### Замена DLL
Версии берутся из того же манифеста, что и у DLSS Swapper для Windows (114+ версий DLSS, DLSS FG, RR, FSR, XeSS). Каждый файл проверяется по MD5. Свои DLL можно добавить через меню **Импортировать DLL** (или `dlss-swapper-linux import file.dll`).

Если в Steam нажать «Проверить целостность файлов», Steam вернёт оригинальные DLL.

### Автоопределение
Для каждой игры приложение смотрит её файлы и предлагает, что ставить. Блок «Рекомендуется» в карточке Frame Generation (или `dlss-swapper-linux detect "Игра"`) показывает:
- движок (Unreal, Unity, RE Engine…) и графический API — DX12 / DX11 / Vulkan берутся из таблицы импорта exe, а если рендерер грузится динамически, как в Baldur's Gate 3, то из строк внутри exe;
- что есть в игре: DLSS, DLSS FG, DLSS RR, Streamline, Reflex, FSR 3.1, FSR FG, XeSS, XeSS FG;
- папку с exe (для UE — `*/Binaries/Win64`, папка `Engine` пропускается);
- какие имена прокси-DLL уже заняты игрой или другим модом.

Из этого выбираются способ FG, имя прокси (`dxgi.dll`; если он занят — `winmm.dll`; для Vulkan — всегда `winmm.dll`), папка и использование FSR игры. Если что-то уже установлено, карточка показывает установленные настройки, а рекомендацию — отдельно.

### Frame Generation
Всё про генерацию кадров собрано в одной карточке **Frame Generation**. Выберите способ и нажмите «Установить» (или «Применить», если уже установлено). Приложение уберёт то, что стояло раньше, поставит выбранное и один раз запишет параметры запуска Steam. Выбранные настройки сохраняются в состоянии игры и показываются при следующем открытии.

| Способ | Что делает | Когда выбирать |
|---|---|---|
| **DLSSG (NVIDIA)** | Настоящий DLSS-G игры на RTX 20/30 через [dlssg-open](https://github.com/ProfessorTee/dlssg-open) | NVIDIA RTX 20/30, в игре DLSS FG 310.x. Проверено: Black Myth: Wukong, RTX 3060 |
| Только апскейлер | OptiScaler без FG | Нужен другой апскейлер или настройки DLSS |
| FSR FG | OptiScaler генерирует кадры FSR FG из данных апскейлера (DLSS/FSR/XeSS) | Любая DX12-игра с апскейлером, в том числе без DLSS FG (например, The Last of Us Part I) |
| DLSSG → FSR3 (Nukem) | DLSS FG игры → dlssg-to-fsr3 | То же, другая реализация. В Wukong зависает |
| XeSS FG | OptiScaler генерирует кадры XeSS FG из данных апскейлера | Только игры **без** DLSS FG. Если FSR FG зависает |

В списке показываются только способы, которые могут сработать в этой игре: пресеты на DLSSG (DLSSG (NVIDIA), DLSSG → FSR3 (Nukem)) скрыты, если в файлах игры нет `nvngx_dlssg.dll` / `sl.dlss_g.dll`, а XeSS FG скрыт, если DLSSG есть. «Дополнительно → Показать все способы» показывает всё, на случай если игра скачивает DLSSG позже.

Что выяснилось при отладке под Proton (RTX 3060, The Last of Us Part I) и выставлено по умолчанию:
- **FSR FG из игры.** Если в игре есть свой FSR 3.1 (`amd_fidelityfx_dx12.dll`), OptiScaler берёт его, а файлы FSR 4 SDK из комплекта не копируются: FG из FSR 4 SDK ронял видеокарту (Xid 32).
- **Сигнал готовности кадра** (`SimulateWaitableObject=true`) включён. Без него игра зависала сразу после старта FG.
- **HUDFix** выключен: он может вызывать вылеты.
- **Подмена архитектуры** `DXVK_NVAPI_GPU_ARCH=AD100` выключена. С ней DLSS запускал на RTX 30 ядра для RTX 40, и видеокарта падала (Xid 13/32). Включайте её только в режимах, где игре нужно показать переключатель DLSS FG.
- **Не открывайте меню OptiScaler (Insert) во время игры с FG.** Под Proton OptiScaler рисует меню через Vulkan поверх вывода vkd3d, и вместе с генерацией кадров это роняет видеокарту: с FSR FG почти сразу (Xid 109/32), с XeSS FG иногда (Xid 13 на третьем открытии; в другом запуске 13 открытий прошли без сбоев). Без FG меню работает. Всё нужное уже записано в `OptiScaler.ini`, настройки меняйте в приложении.

### Настоящий DLSSG (NVIDIA) на RTX 20/30
NVIDIA DLSS-G 310.x уже содержит ядра для Ampere (`sm_86`), а под Proton ему мешают три вещи. Пресет «DLSSG (NVIDIA)» закрывает все три по схеме [dlssg-open](https://github.com/ProfessorTee/dlssg-open) (MIT):

1. **CUDA.** В Proton `nvcuda.dll` — пустая заглушка, а NGX спрашивает у CUDA устройство, архитектуру и LUID. Ставится `nvcuda-lite` из dlssg-open.
2. **Ядра.** Runtime отдаёт драйверу ядра `sm_89`, которые vkd3d-proton не может загрузить на Ampere. Пропатченный DXVK-NVAPI (`nvapi64.dll` из dlssg-open) на лету перенацеливает PTX на `sm_86` и подставляет `sm_86`-ядра, которые NVIDIA уже вшила в тот же runtime.
3. **Проверки архитектуры и HAGS.** Через `DXVK_NVAPI_GPU_ARCH=AD100 NVCUDA_LITE_CC=8.9 DXVK_NVAPI_CUBIN_RETARGET_SM=<sm>`, а в реестр префикса пишется `HwSchMode=2`: некоторые игры (Wukong) проверяют HAGS через реестр.

Upstream ставит эти файлы прямо в Proton, что затрагивает все игры и сбрасывается при обновлении Proton. dlss-swapper-linux вместо этого создаёт **инструмент совместимости Steam** «<Proton> + DLSS FG (dlss-swapper)». Это обёртка над вашим Proton, которая назначается только этой игре и перед каждым запуском кладёт `nvcuda.dll` и `nvapi64.dll` в её префикс. Сам Proton не меняется. При удалении возвращаются прежний Proton, параметры запуска и оригинальные файлы префикса.

- Нужен DLSS-G 310.x. Если в игре старее, замените `nvngx_dlssg.dll` в разделе «Апскейлеры».
- Переключатель «Индикатор DLSS-G» включает оверлей NVIDIA, по которому видно, что генерация идёт.
- Steam должен быть закрыт, чтобы приложение могло назначить инструмент совместимости. После первого назначения Steam может докачать кэш шейдеров.
- В CLI: `dlss-swapper-linux real-fg "Игра" [--indicator]`.

Мод dlssg_for_sm86 под Proton не работает (DXVK-NVAPI не реализует `NvAPI_D3D12_CreateCuModule`) и в интерфейсе скрыт.

Для OptiScaler в параметры запуска добавляется `WINEDLLOVERRIDES=dxgi=n,b PROTON_ENABLE_NVAPI=1`, для dlssg_for_sm86 — `WINEDLLOVERRIDES=version=n,b PROTON_ENABLE_NVAPI=1`. При удалении приложение убирает только то, что добавило само, а логи модов оставляет.

> ⚠ Steam перезаписывает свой конфиг при выходе, поэтому **закройте Steam**, прежде чем записывать параметры запуска. Если Steam открыт, строка копируется в буфер обмена — вставьте её вручную: *Свойства игры → Параметры запуска*.

> ⚠ Не используйте моды в онлайн-играх с античитом: это может закончиться баном. Приложение предупреждает, если находит EAC/BattlEye.

## CLI

```bash
dlss-swapper-linux list                       # игры с найденными DLL
dlss-swapper-linux show "Cyberpunk"           # подробности
dlss-swapper-linux detect "Cyberpunk"         # анализ файлов и рекомендация
dlss-swapper-linux versions --type dlss_g     # доступные версии
dlss-swapper-linux swap "Cyberpunk" latest    # DLSS/FG/RR до последних версий
dlss-swapper-linux swap 1091500 310.9.16.0 --type dlss
dlss-swapper-linux restore "Cyberpunk"        # вернуть оригиналы
dlss-swapper-linux opti-install "Cyberpunk"                     # способ и прокси по анализу игры
dlss-swapper-linux opti-install "Cyberpunk" --fg optifg --preset 11
dlss-swapper-linux opti-install "Game" --fg optifg --log            # FSR FG из данных апскейлера
dlss-swapper-linux opti-install "Game" --fg optifg --no-game-fsr    # FSR из комплекта OptiScaler
dlss-swapper-linux opti-remove "Cyberpunk"
dlss-swapper-linux dlssg-install "Cyberpunk" --frames 2x --verbose   # нативный DLSS FG
dlss-swapper-linux dlssg-remove "Cyberpunk"
dlss-swapper-linux opti-fetch ~/Downloads/Optiscaler_0.9.4.7z   # свой архив OptiScaler
dlss-swapper-linux --dir "/games/MyGame" show "/games/MyGame"   # игра вне Steam
```

## Где что хранится

| Что | Где |
|---|---|
| Библиотека DLL, OptiScaler, манифест | `~/.local/share/dlss-swapper-linux/` |
| Что изменено в игре (для отката) | `<папка игры>/.dlss-swapper-linux.json` |
| Резервные копии конфига Steam | `userdata/<id>/config/localconfig.vdf.bak-*` |

## Благодарности

- [DLSS Swapper](https://github.com/beeradmoore/dlss-swapper): манифест и зеркало DLL
- [OptiScaler](https://github.com/optiscaler/OptiScaler)
- [dlssg-open](https://github.com/ProfessorTee/dlssg-open) (ProfessorTee)
- [dlssg_for_sm86](https://github.com/sdli1995/dlssg_for_sm86) (sdli1995)
- [dlssg-to-fsr3](https://github.com/Nukem9/dlssg-to-fsr3) (Nukem9)
- [fakenvapi](https://github.com/optiscaler/fakenvapi), [DXVK-NVAPI](https://github.com/jp7677/dxvk-nvapi)

DLSS — товарный знак NVIDIA. Проект не связан с NVIDIA, AMD, Intel или Valve.
