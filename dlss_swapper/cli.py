"""Command line interface."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import core, gpu, optiscaler, steam, swapper
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
        tag += " [OptiScaler]" if d.owned_by_optiscaler else ""
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


def cmd_opti_install(a) -> None:
    g = _find_game(a.game, a.dir)
    info = gpu.detect()
    print(f"GPU: {info.name}")
    if info.native_dlss_fg and a.fg != "none":
        print("  У вас RTX 40+: DLSS FG работает нативно, разблокировка не нужна.")
    ac = swapper.anti_cheat(g.path)
    if ac and not a.yes:
        sys.exit(f"⚠ Найден античит ({', '.join(ac)}). Моды могут привести к бану. "
                 "Добавьте --yes, если игра оффлайн/античит отключён.")
    target = Path(a.target) if a.target else (swapper.exe_candidates(g.path) or [g.path])[0]
    if not target.is_absolute():
        target = g.path / target
    b = _bundle(a)
    opts = optiscaler.InstallOptions(
        proxy=a.proxy, fg_mode=a.fg, upscaler=a.upscaler or ("dlss" if info.has_dlss else "fsr31"),
        nvidia=info.is_nvidia if a.nvidia is None else a.nvidia,
        spoof_ada=not a.no_spoof_arch, overwrite_game_files=a.overwrite,
        dlss_preset=a.preset,
        extra_ini=dict(kv.split("=", 1) for kv in a.ini or []))
    res = optiscaler.install(g.path, target, b, opts)
    print(f"OptiScaler {b.tag} установлен в {target}")
    print(f"  файлов: {len(res.placed)}, пропущено (уже есть в игре): {len(res.skipped)}")
    for s in res.skipped:
        print(f"    = {s}")
    for w in res.warnings + [f"нет ключа в ini: {m}" for m in res.ini_missing]:
        print(f"  ⚠ {w}")
    rel = str(target.relative_to(g.path)) if target != g.path else "."
    lu = core.apply_launch_options(g, rel, opts, write=not a.no_launch)
    print(f"\nПараметры запуска Steam:\n  {lu.new}")
    if lu.written:
        print("  ✓ записаны в конфиг Steam")
    elif lu.error:
        print(f"  ⚠ {lu.error}")
    print("\nВ игре: включите DLSS и DLSS Frame Generation. Меню OptiScaler — клавиша Insert.")


def cmd_opti_remove(a) -> None:
    g = _find_game(a.game, a.dir)
    lu = core.remove_optiscaler(g, None, write=not a.no_launch)
    print("OptiScaler удалён.")
    if lu:
        print(f"Параметры запуска: {lu.new or '(пусто)'}" + (" ✓" if lu.written else ""))
        if lu.error:
            print(f"  ⚠ {lu.error}")


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

    s = sub.add_parser("opti-install", help="установить OptiScaler (+ разблокировка FG)")
    s.add_argument("game")
    s.add_argument("--fg", default="optifg", choices=list(optiscaler.FG_MODES))
    s.add_argument("--proxy", default="dxgi.dll", choices=optiscaler.PROXY_NAMES)
    s.add_argument("--upscaler", choices=optiscaler.UPSCALERS)
    s.add_argument("--target", help="папка с exe (по умолчанию определяется автоматически)")
    s.add_argument("--bundle", help="тег сборки или путь к архиву/папке OptiScaler")
    s.add_argument("--preset", type=int, help="пресет DLSS: 0=по умолч., 11=K, 13=M …")
    s.add_argument("--ini", action="append", metavar="Section.Key=value")
    s.add_argument("--nvidia", action=argparse.BooleanOptionalAction, default=None)
    s.add_argument("--no-spoof-arch", action="store_true",
                   help="не добавлять DXVK_NVAPI_GPU_ARCH=AD100")
    s.add_argument("--overwrite", action="store_true",
                   help="перезаписывать DLL игры (FSR/XeSS) файлами OptiScaler")
    s.add_argument("--no-launch", action="store_true", help="не трогать параметры запуска Steam")
    s.add_argument("--yes", action="store_true", help="игнорировать предупреждение об античите")
    s.set_defaults(fn=cmd_opti_install)

    s = sub.add_parser("opti-remove", help="удалить OptiScaler")
    s.add_argument("game")
    s.add_argument("--no-launch", action="store_true")
    s.set_defaults(fn=cmd_opti_remove)

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
