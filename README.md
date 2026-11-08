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

### Frame Generation на RTX 20/30 через Proton
1. OptiScaler кладётся рядом с exe игры как `dxgi.dll` (можно выбрать `winmm.dll`, `version.dll` и т.д.).
2. В `OptiScaler.ini` включаются `FGInput=dlssg`, `FGOutput=fsrfg` (или `nukems` / `xefg`), `StreamlineSpoofing`, `SpoofHAGS`, апскейлер остаётся DLSS.
3. В параметры запуска Steam добавляется:
   ```
   WINEDLLOVERRIDES=dxgi=n,b DXVK_NVAPI_GPU_ARCH=AD100 PROTON_ENABLE_NVAPI=1 %command%
   ```
   `WINEDLLOVERRIDES` заставляет Wine загрузить DLL OptiScaler вместо системной, а `DXVK_NVAPI_GPU_ARCH=AD100` говорит игре, что у вас видеокарта поколения Ada (RTX 40), и тогда в меню появляется DLSS FG.
4. В игре включите DLSS и DLSS Frame Generation. Меню OptiScaler открывается клавишей **Insert**.

Для игр, где есть DLSS, но нет DLSS FG, выберите режим **«FG из апскейлера»** (OptiFG + HUDFix).

> ⚠ Steam перезаписывает свой конфиг при выходе, поэтому **закройте Steam**, прежде чем записывать параметры запуска. Если Steam открыт, строка копируется в буфер обмена — вставьте её вручную: *Свойства игры → Параметры запуска*.

> ⚠ Не используйте OptiScaler и подмену DLL в онлайн-играх с античитом: это может закончиться баном. Приложение предупреждает, если находит EAC/BattlEye.

### Нативный DLSS FG (dlssg_for_sm86)
Карточка **DLSS Frame Generation** скачивает последний релиз [dlssg_for_sm86](https://github.com/sdli1995/dlssg_for_sm86) (MIT), проверяет SHA-256 каждого файла по его `manifest.json` и кладёт рядом с exe игры `version.dll`, `dlssg_sm86.ini` и папку `dlssg_sm86\`. В параметры запуска добавляется `WINEDLLOVERRIDES=version=n,b PROTON_ENABLE_NVAPI=1`.

- Нужна игра со встроенным DLSS FG (`sl.dlss_g.dll` / `nvngx_dlssg.dll`) и драйвер NVIDIA R580+.
- Мод сам подменяет архитектуру GPU только для Streamline. **Не добавляйте `DXVK_NVAPI_GPU_ARCH`** вместе с ним: по данным автора, глобальная подмена вешает видеокарту.
- Не включайте одновременно FG в OptiScaler.
- Лог мода: `<папка exe>/dlssg_sm86/logs/dlssg_sm86.log`. Если переключатель FG не появился, выберите следующий прокси: `winmm.dll`, `dbghelp.dll`, `dinput8.dll`…
- Работу под Proton автор не заявляет. Проверяйте по логу.

## CLI

```bash
dlss-swapper-linux list                       # игры с найденными DLL
dlss-swapper-linux show "Cyberpunk"           # подробности
dlss-swapper-linux versions --type dlss_g     # доступные версии
dlss-swapper-linux swap "Cyberpunk" latest    # DLSS/FG/RR до последних версий
dlss-swapper-linux swap 1091500 310.9.16.0 --type dlss
dlss-swapper-linux restore "Cyberpunk"        # вернуть оригиналы
dlss-swapper-linux opti-install "Cyberpunk" --fg optifg --preset 11
dlss-swapper-linux opti-install "Game" --fg upscaler --proxy winmm.dll
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
