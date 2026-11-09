"""Command line interface."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import core, detect, dlssg_sm86, dlssgopen, gpu, optiscaler, steam, swapper
from .library import DLL_TYPES, Library


_last_pct = [-1]


def _progress(done: int, total: int) -> None:
    if total:
        pct = done * 100 // total
        if pct == _last_pct[0]:
            return
        _last_pct[0] = pct
        sys.stderr.write(f"\r  {done * 100 // total:3d}%  {done >> 20}/{total >> 20} MiB")
        if done >= total:
            sys.stderr.write("\n")
        sys.stderr.flush()


def _find_game(query: str, extra: list[Path]) -> steam.Game:
    games = core.all_games(extra)
    if Path(query).is_dir():
        p = Path(query).resolve()
        for g in games:
            if g.path.resolve() == p:
                return g
        return steam.Game(p.name, p, "", "folder")
    exact = [g for g in games if g.appid == query or g.name.lower() == query.lower()]
    if exact:
        return exact[0]
    part = [g for g in games if query.lower() in g.name.lower()]
    if len(part) == 1:
        return part[0]
    if not part:
        sys.exit(f"Игра '{query}' не найдена. Смотрите: dlss-swapper-linux list")
    sys.exit("Неоднозначно: " + ", ".join(f"{g.name} ({g.appid})" for g in part))


def cmd_list(a) -> None:
    for g in core.all_games(a.dir):
        dlls = swapper.scan(g.path) if not a.fast else []
        inst = optiscaler.installed(g.path)
        info = ", ".join(f"{d.kind}={d.version}{'*' if d.swapped else ''}"
                         for d in dlls if not d.owned_by_optiscaler)
        opti = f"  [OptiScaler {', '.join(v['version'] for v in inst.values())}]" if inst else ""
        if a.all or a.fast or dlls or inst:
            print(f"{g.appid or '-':>10}  {g.source:<8} {g.name}{opti}")
            if info:
                print(f"{'':>20}{info}")
    print("\n* — версия заменена (оригинал в *.dlsss)", file=sys.stderr)


def cmd_show(a) -> None:
    g = _find_game(a.game, a.dir)
    print(f"{g.name}  ({g.source} {g.appid})\n  {g.path}")
    for d in swapper.scan(g.path):
        rel = d.path.relative_to(g.path)
        tag = " [заменён, оригинал " + str(d.original_version) + "]" if d.swapped else ""
        tag += " [мод]" if d.owned_by_optiscaler else ""
        print(f"  {d.label:<26} {d.version or '?':<14} {rel}{tag}")
    ac = swapper.anti_cheat(g.path)
    if ac:
        print(f"  ⚠ античит: {', '.join(ac)} — не используйте моды в онлайне!")
    cands = swapper.exe_candidates(g.path)
    if cands:
        print(f"  Папка exe (для OptiScaler): {cands[0].relative_to(g.path) if cands[0] != g.path else '.'}")
    for k, v in optiscaler.installed(g.path).items():
        print(f"  OptiScaler {v['version']} в '{k}' как {v['proxy']}, FG={v['fg_mode']}")
    if g.source in ("steam", "shortcut"):
        print(f"  Параметры запуска: {steam.get_launch_options(g) or '(пусто)'}")


def cmd_versions(a) -> None:
    lib = Library()
    lib.manifest(refresh=a.refresh)
    for r in lib.records(a.type)[: a.limit]:
        print(f"  {r.display:<34} {r.md5}")


def cmd_swap(a) -> None:
    g = _find_game(a.game, a.dir)
    lib = Library()
    kinds = [a.type] if a.type else ["dlss", "dlss_g", "dlss_d"]
    dlls = [d for d in swapper.scan(g.path) if d.kind in kinds and not d.owned_by_optiscaler]
    if not dlls:
        sys.exit("В игре нет подходящих DLL.")
    for kind in kinds:
        targets = [d for d in dlls if d.kind == kind]
        if not targets:
            continue
        rec = lib.find(kind, a.version)
        if not rec:
            sys.exit(f"Версия {a.version} для {kind} не найдена (см. versions --type {kind})")
        print(f"{DLL_TYPES[kind][1]} → {rec.version}")
        src = lib.ensure(rec, _progress)
        for d in targets:
            swapper.swap(g.path, d, rec, src)
            print(f"  {d.version} → {rec.version}  {d.path.relative_to(g.path)}")


def cmd_restore(a) -> None:
    g = _find_game(a.game, a.dir)
    n = swapper.restore_all(g.path)
    print(f"Восстановлено файлов: {n}")


def cmd_import(a) -> None:
    lib = Library()
    for p in a.files:
        r = lib.import_dll(Path(p), a.type)
        print(f"Импортирован {r.kind} {r.version} ({r.md5})")


def cmd_opti_fetch(a) -> None:
    if a.source:
        b = optiscaler.import_bundle(Path(a.source), a.tag)
    else:
        b = optiscaler.download_latest(_progress)
    print(f"OptiScaler {b.tag}: {b.path}  (Nukem: {'да' if b.has_nukem else 'нет'}, "
          f"fakenvapi: {'да' if b.has_fakenvapi else 'нет'})")


def _bundle(a) -> optiscaler.Bundle:
    if a.bundle:
        for b in optiscaler.local_bundles():
            if b.tag == a.bundle:
                return b
        p = Path(a.bundle)
        if p.exists():
            return optiscaler.import_bundle(p)
        sys.exit(f"Сборка {a.bundle} не найдена")
    bs = optiscaler.local_bundles()
    if bs:
        return bs[0]
    print("Скачиваю OptiScaler…")
    return optiscaler.download_latest(_progress)


def _target(a, g) -> Path:
    target = Path(a.target) if a.target else (swapper.exe_candidates(g.path) or [g.path])[0]
    return target if target.is_absolute() else g.path / target


def _print_setup(r: core.FgSetupResult) -> None:
    print(f"{core.method_label(r.method)}: версия {r.version}, {r.target} ({r.placed} файлов)")
    for w in r.warnings:
        print(f"  ⚠ {w}")
    for n in r.notes or []:
        print(f"  • {n}")
    lu = r.launch
    print(f"\nПараметры запуска Steam:\n  {lu.new or '(пусто)'}")
    if lu.written:
        print("  ✓ записаны в конфиг Steam")
    if lu.error:
        print(f"  ⚠ {lu.error}")


def _print_removed(lu) -> None:
    print("Удалено (логи сохранены).")
    if lu:
        print(f"Параметры запуска: {lu.new or '(пусто)'}" + (" ✓" if lu.written else ""))
        if lu.error:
            print(f"  ⚠ {lu.error}")


def cmd_detect(a) -> None:
    g = _find_game(a.game, a.dir)
    an = detect.analyze(g.path, gpu.detect())
    print(f"{g.name}\n  движок: {an.engine}, API: {an.api}" + (f", exe: {an.exe.name}" if an.exe else ""))
    print(f"  папка exe: {an.target.relative_to(g.path) if an.target != g.path else '.'}")
    print(f"  найдено: {', '.join(an.feature_labels) or 'нет DLSS/FSR/XeSS'}")
    if an.foreign_proxies:
        print(f"  заняты имена: {', '.join(an.foreign_proxies)}")
    print(f"\nРекомендация: {core.method_label(an.method)} (--fg {an.method}), прокси {an.proxy}")
    for r in an.reasons:
        print(f"  + {r}")
    for w in an.warnings:
        print(f"  ⚠ {w}")


def cmd_opti_install(a) -> None:
    g = _find_game(a.game, a.dir)
    info = gpu.detect()
    print(f"GPU: {info.name}")
    an = detect.analyze(g.path, info)
    if a.fg == "auto":
        a.fg = an.method
        print(f"Автовыбор: {core.method_label(a.fg)} — {an.reasons[0] if an.reasons else ''}")
    if a.proxy is None:
        a.proxy = an.proxy
    if a.target is None and an.target != g.path:
        a.target = str(an.target)
    if info.native_dlss_fg and a.fg != "none":
        print("  У вас RTX 40+: DLSS FG работает нативно, разблокировка не нужна.")
    ac = swapper.anti_cheat(g.path)
    if ac and not a.yes:
        sys.exit(f"⚠ Найден античит ({', '.join(ac)}). Моды могут привести к бану. "
                 "Добавьте --yes, если игра оффлайн/античит отключён.")
    opts = optiscaler.InstallOptions(
        proxy=a.proxy, upscaler=a.upscaler or ("dlss" if info.has_dlss else "fsr31"),
        nvidia=info.is_nvidia if a.nvidia is None else a.nvidia,
        spoof_ada=a.spoof_arch, hudfix=a.hudfix, overwrite_game_files=a.overwrite,
        dlss_preset=a.preset, use_game_fsr=a.game_fsr, simulate_waitable=a.waitable, log=a.log,
        extra_ini=dict(kv.split("=", 1) for kv in a.ini or []))
    r = core.setup_fg(g, _target(a, g), a.fg, opts, lambda: _bundle(a), dlssg_sm86.Options(),
                      lambda: None, write=not a.no_launch)
    _print_setup(r)
    print("\nВ игре включите DLSS. Меню OptiScaler — клавиша Insert.")


def cmd_opti_remove(a) -> None:
    _print_removed(core.remove_all_fg(_find_game(a.game, a.dir), write=not a.no_launch))


def cmd_dlssg_fetch(a) -> None:
    b = dlssg_sm86.import_bundle(Path(a.source)) if a.source else dlssg_sm86.download_latest(_progress)
    print(f"dlssg_for_sm86 {b.version}: {b.path}")


def _dlssg_bundle(a) -> dlssg_sm86.Bundle:
    bs = dlssg_sm86.local_bundles()
    if a.bundle:
        for b in bs:
            if b.version == a.bundle:
                return b
        return dlssg_sm86.import_bundle(Path(a.bundle))
    if bs:
        return bs[0]
    print("Скачиваю dlssg_for_sm86…")
    return dlssg_sm86.download_latest(_progress)


def cmd_dlssg_install(a) -> None:
    g = _find_game(a.game, a.dir)
    if not gpu.detect().is_nvidia:
        sys.exit("dlssg_for_sm86 работает только на видеокартах NVIDIA RTX.")
    frames = {"2x": 1, "3x": 2, "4x": 3, "6x": 5}[a.frames.lower()]
    opts = dlssg_sm86.Options(proxy=a.proxy, engine=a.engine, max_frames=frames, preset=a.preset,
                              super_resolution=a.sr, verbose_log=a.verbose)
    target = _target(a, g)
    r = core.setup_fg(g, target, "native", optiscaler.InstallOptions(), lambda: None, opts,
                      lambda: _dlssg_bundle(a), write=not a.no_launch)
    _print_setup(r)
    rel = str(target.relative_to(g.path)) if target != g.path else "."
    print(f"\nЛог мода: {dlssg_sm86.log_path(g.path, rel)}")
    print("В игре включите DLSS Frame Generation.")


def cmd_real_fg(a) -> None:
    g = _find_game(a.game, a.dir)
    if not gpu.detect().is_nvidia:
        sys.exit("Настоящий DLSS FG работает только на NVIDIA RTX.")
    r = core.setup_fg(g, g.path, "dlssg_open", optiscaler.InstallOptions(), lambda: None,
                      dlssg_sm86.Options(), lambda: None, write=not a.no_launch,
                      dlssgopen_bundle=lambda: (dlssgopen.local_bundles() or [dlssgopen.download_latest(_progress)])[0],
                      dlssg_indicator=a.indicator)
    _print_setup(r)
    print("\nВ игре включите DLSS и DLSS Frame Generation.")


def cmd_dlssg_remove(a) -> None:
    _print_removed(core.remove_all_fg(_find_game(a.game, a.dir), write=not a.no_launch))


def cmd_launch(a) -> None:
    g = _find_game(a.game, a.dir)
    if a.set is None:
        print(steam.get_launch_options(g))
    else:
        steam.set_launch_options(g, a.set)
        print("✓ записано")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="dlss-swapper-linux",
                                description="DLSS Swapper для Linux / Steam Proton")
    p.add_argument("--dir", action="append", type=Path, default=[],
                   help="дополнительная папка с игрой (можно несколько раз)")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("gui", help="графический интерфейс (по умолчанию)")
    s = sub.add_parser("list", help="игры с найденными DLL")
    s.add_argument("--all", action="store_true", help="показать все игры")
    s.add_argument("--fast", action="store_true", help="не сканировать файлы")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("detect", help="анализ файлов игры и рекомендация, что ставить")
    s.add_argument("game")
    s.set_defaults(fn=cmd_detect)

    s = sub.add_parser("show", help="подробности по игре")
    s.add_argument("game", help="название, appid или путь")
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("versions", help="доступные версии DLL")
    s.add_argument("--type", default="dlss", choices=list(DLL_TYPES))
    s.add_argument("--limit", type=int, default=25)
    s.add_argument("--refresh", action="store_true")
    s.set_defaults(fn=cmd_versions)

    s = sub.add_parser("swap", help="заменить версию DLL")
    s.add_argument("game")
    s.add_argument("version", nargs="?", default="latest", help="версия или 'latest'")
    s.add_argument("--type", choices=list(DLL_TYPES), help="по умолчанию dlss, dlss_g и dlss_d")
    s.set_defaults(fn=cmd_swap)

    s = sub.add_parser("restore", help="вернуть оригинальные DLL")
    s.add_argument("game")
    s.set_defaults(fn=cmd_restore)

    s = sub.add_parser("import", help="добавить свою DLL в библиотеку")
    s.add_argument("files", nargs="+")
    s.add_argument("--type", choices=list(DLL_TYPES))
    s.set_defaults(fn=cmd_import)

    s = sub.add_parser("opti-fetch", help="скачать/импортировать OptiScaler")
    s.add_argument("source", nargs="?", help="свой .7z/.zip или папка (иначе — последний релиз с GitHub)")
    s.add_argument("--tag")
    s.set_defaults(fn=cmd_opti_fetch)

    s = sub.add_parser("opti-install", help="установить OptiScaler (заменяет dlssg_for_sm86)")
    s.add_argument("game")
    s.add_argument("--fg", default="auto", choices=["auto"] + list(optiscaler.FG_MODES),
                   help="способ FG; auto — по анализу файлов игры")
    s.add_argument("--proxy", choices=optiscaler.PROXY_NAMES, help="по умолчанию — по анализу игры")
    s.add_argument("--upscaler", choices=optiscaler.UPSCALERS)
    s.add_argument("--target", help="папка с exe (по умолчанию определяется автоматически)")
    s.add_argument("--bundle", help="тег сборки или путь к архиву/папке OptiScaler")
    s.add_argument("--preset", type=int, help="пресет DLSS: 0=по умолч., 11=K, 13=M …")
    s.add_argument("--ini", action="append", metavar="Section.Key=value")
    s.add_argument("--nvidia", action=argparse.BooleanOptionalAction, default=None)
    s.add_argument("--spoof-arch", action="store_true",
                   help="добавить DXVK_NVAPI_GPU_ARCH=AD100 (может вешать GPU)")
    s.add_argument("--hudfix", action="store_true", help="включить HUDFix (может вызывать вылеты)")
    s.add_argument("--game-fsr", action=argparse.BooleanOptionalAction, default=True,
                   help="FSR FG из игры вместо FSR 4 из комплекта (по умолчанию да)")
    s.add_argument("--waitable", action=argparse.BooleanOptionalAction, default=True,
                   help="SimulateWaitableObject против зависания при старте FG (по умолчанию да)")
    s.add_argument("--log", action="store_true", help="включить OptiScaler.log")
    s.add_argument("--overwrite", action="store_true",
                   help="перезаписывать DLL игры (FSR/XeSS) файлами OptiScaler")
    s.add_argument("--no-launch", action="store_true", help="не трогать параметры запуска Steam")
    s.add_argument("--yes", action="store_true", help="игнорировать предупреждение об античите")
    s.set_defaults(fn=cmd_opti_install)

    s = sub.add_parser("opti-remove", help="удалить OptiScaler")
    s.add_argument("game")
    s.add_argument("--no-launch", action="store_true")
    s.set_defaults(fn=cmd_opti_remove)

    s = sub.add_parser("dlssg-fetch", help="скачать/импортировать dlssg_for_sm86")
    s.add_argument("source", nargs="?", help="свой .zip или папка (иначе — последний релиз)")
    s.set_defaults(fn=cmd_dlssg_fetch)

    s = sub.add_parser("dlssg-install", help="нативный DLSS FG на RTX 20/30 (заменяет OptiScaler)")
    s.add_argument("game")
    s.add_argument("--frames", default="2x", choices=["2x", "3x", "4x", "6x"])
    s.add_argument("--engine", default="DLSS", choices=list(dlssg_sm86.ENGINES))
    s.add_argument("--preset", default="A", choices=list(dlssg_sm86.PRESETS))
    s.add_argument("--sr", default="Off", choices=list(dlssg_sm86.SR_MODES),
                   help="INT8-ускорение DLSS SR на RTX 30/20")
    s.add_argument("--proxy", default="version.dll", choices=dlssg_sm86.PROXIES)
    s.add_argument("--target")
    s.add_argument("--bundle", help="версия или путь к архиву/папке")
    s.add_argument("--verbose", action="store_true", help="подробный лог (Level=2)")
    s.add_argument("--no-launch", action="store_true")
    s.set_defaults(fn=cmd_dlssg_install)

    s = sub.add_parser("real-fg", help="настоящий DLSS FG на RTX 20/30 (dlssg-open, Proton-обёртка)")
    s.add_argument("game")
    s.add_argument("--indicator", action="store_true", help="показывать индикатор DLSS-G NVIDIA")
    s.add_argument("--no-launch", action="store_true")
    s.set_defaults(fn=cmd_real_fg)

    s = sub.add_parser("dlssg-remove", help="удалить dlssg_for_sm86")
    s.add_argument("game")
    s.add_argument("--no-launch", action="store_true")
    s.set_defaults(fn=cmd_dlssg_remove)

    s = sub.add_parser("launch-options", help="показать/задать параметры запуска Steam")
    s.add_argument("game")
    s.add_argument("--set")
    s.set_defaults(fn=cmd_launch)

    a = p.parse_args(argv)
    if a.cmd in (None, "gui"):
        from .gui import run
        run(a.dir)
        return
    try:
        a.fn(a)
    except KeyboardInterrupt:
        sys.exit(130)
