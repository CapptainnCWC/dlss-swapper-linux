# DLSS Swapper для Linux

Аналог [DLSS Swapper](https://github.com/beeradmoore/dlss-swapper) для Linux и игр, запускаемых через **Steam + Proton**.

- Меняет версии **DLSS**, **DLSS Frame Generation**, **DLSS Ray Reconstruction**, **FSR 3.1** и **XeSS** в играх. Оригиналы сохраняются рядом как `*.dlsss`.
- Ставит **OptiScaler** и **разблокирует DLSS Frame Generation на картах без RTX 40** (RTX 20/30, AMD, Intel): игра показывает переключатель DLSS FG, а кадры генерирует FSR FG (OptiFG), Nukem `dlssg-to-fsr3` или XeSS FG.
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
| Только апскейлер | OptiScaler без FG | Нужен другой апскейлер или настройки DLSS |
| FSR FG (OptiFG) | DLSS FG игры → FSR FG через OptiScaler | В игре есть DLSS FG, видеокарта без RTX 40 |
| FSR3 FG (Nukem) | DLSS FG игры → dlssg-to-fsr3 | То же, другая реализация |
| XeSS FG | DLSS FG игры → XeSS FG | То же |
| Из апскейлера | OptiScaler генерирует кадры из данных DLSS | В игре нет DLSS FG (например, The Last of Us Part I) |
| DLSS FG (sm86) | Настоящий DLSS-G через [dlssg_for_sm86](https://github.com/sdli1995/dlssg_for_sm86) | В игре есть DLSS FG, экспериментально |

Что выяснилось при отладке под Proton (RTX 3060, The Last of Us Part I) и выставлено по умолчанию:
- **FSR FG из игры.** Если в игре есть свой FSR 3.1 (`amd_fidelityfx_dx12.dll`), OptiScaler берёт его, а файлы FSR 4 SDK из комплекта не копируются: FG из FSR 4 SDK ронял видеокарту (Xid 32).
- **Сигнал готовности кадра** (`SimulateWaitableObject=true`) включён. Без него игра зависала сразу после старта FG.
- **HUDFix** выключен: он может вызывать вылеты.
- **Подмена архитектуры** `DXVK_NVAPI_GPU_ARCH=AD100` выключена. С ней DLSS запускал на RTX 30 ядра для RTX 40, и видеокарта падала (Xid 13/32). Включайте её только в режимах, где игре нужно показать переключатель DLSS FG.
- Если игра зависает при открытии меню OptiScaler (Insert), а FG работает, играйте без меню: FG уже включён в `OptiScaler.ini`.

Для OptiScaler в параметры запуска добавляется `WINEDLLOVERRIDES=dxgi=n,b PROTON_ENABLE_NVAPI=1`, для dlssg_for_sm86 — `WINEDLLOVERRIDES=version=n,b PROTON_ENABLE_NVAPI=1`. При удалении приложение убирает только то, что добавило само, а логи модов оставляет.

**dlssg_for_sm86.** Скачивается релиз автора (MIT), SHA-256 каждого файла проверяется по его `manifest.json`. Нужна игра со встроенным DLSS FG (`sl.dlss_g.dll`) и драйвер R580+. Под Proton мод может не найти CUDA: в логе `<папка exe>/dlssg_sm86/logs/dlssg_sm86.log` будет `Missing cuInit`.

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
dlss-swapper-linux opti-install "Game" --fg upscaler --log          # FG для игр без DLSS FG
dlss-swapper-linux opti-install "Game" --fg upscaler --no-game-fsr  # FSR из комплекта OptiScaler
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
- [dlssg_for_sm86](https://github.com/sdli1995/dlssg_for_sm86) (sdli1995)
- [dlssg-to-fsr3](https://github.com/Nukem9/dlssg-to-fsr3) (Nukem9)
- [fakenvapi](https://github.com/optiscaler/fakenvapi), [DXVK-NVAPI](https://github.com/jp7677/dxvk-nvapi)

DLSS — товарный знак NVIDIA. Проект не связан с NVIDIA, AMD, Intel или Valve.
