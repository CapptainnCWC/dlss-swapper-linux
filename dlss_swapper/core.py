"""High level operations shared by CLI and GUI."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import dlssg_sm86, launchopts, optiscaler, steam
from .steam import Game


def all_games(extra_dirs: list[Path] | None = None) -> list[Game]:
    roots = steam.find_steam_roots()
    games = steam.steam_games(roots) + steam.shortcut_games(roots)
    for d in extra_dirs or []:
        d = Path(d)
        if d.is_dir():
            games.append(Game(d.name, d, "", "folder"))
    return games


@dataclass
class LaunchUpdate:
    old: str
    new: str
    written: bool
    error: str | None = None


# ------------------------------------------------------------ unified FG setup
# One "Frame Generation" choice per game: OptiScaler modes or native DLSS-G (dlssg_for_sm86).
FG_METHODS = {
    "none": "Только апскейлер",
    "optifg": "FSR FG (OptiFG)",
    "nukems": "FSR3 FG (Nukem)",
    "xefg": "XeSS FG",
    "upscaler": "Из апскейлера",
    "native": "DLSS FG (sm86)",
}
FG_METHOD_HINTS = {
    "none": "OptiScaler без генерации кадров",
    "optifg": "DLSS FG игры → FSR FG через OptiScaler",
    "nukems": "DLSS FG игры → dlssg-to-fsr3 через OptiScaler",
    "xefg": "DLSS FG игры → XeSS FG через OptiScaler",
    "upscaler": "Для игр без DLSS FG: OptiScaler генерирует кадры из DLSS",
    "native": "Настоящий DLSS-G от NVIDIA на RTX 20/30 (dlssg_for_sm86), экспериментально",
}


@dataclass
class FgSetupResult:
    method: str
    version: str
    target: Path
    placed: int
    warnings: list
    launch: LaunchUpdate


def current_fg(game: Game) -> tuple[str | None, str | None, dict]:
    """(method, rel_target, saved options) of what is installed now."""
    dg = dlssg_sm86.installed(game.path)
    if dg:
        k, v = next(iter(dg.items()))
        return "native", k, v.get("options", {})
    op = optiscaler.installed(game.path)
    if op:
        k, v = next(iter(op.items()))
        return v.get("fg_mode", "none"), k, v.get("options", {})
    return None, None, {}


def _read_opts(game: Game) -> str:
    if game.source in ("steam", "shortcut"):
        try:
            return steam.get_launch_options(game)
        except Exception:  # noqa: BLE001
            return ""
    return ""


def _write_opts(game: Game, old: str, new: str, write: bool) -> LaunchUpdate:
    if game.source == "folder":
        return LaunchUpdate(old, new, False, "Игра не из Steam: добавьте её как стороннюю игру в Steam "
                                             "и вставьте строку в Параметры запуска.")
    if not write or new == old:
        return LaunchUpdate(old, new, new == old)
    try:
        steam.set_launch_options(game, new)
        return LaunchUpdate(old, new, True)
    except steam.SteamRunningError as e:
        return LaunchUpdate(old, new, False, str(e))
    except Exception as e:  # noqa: BLE001
        return LaunchUpdate(old, new, False, f"Не удалось записать параметры запуска: {e}")


def remove_all_fg(game: Game, write: bool = True) -> LaunchUpdate | None:
    old = _read_opts(game)
    cur = old
    removed_any = False
    for inst in optiscaler.uninstall(game.path):
        cur, removed_any = optiscaler.revert_launch_options(cur, inst), True
    for inst in dlssg_sm86.uninstall(game.path):
        cur, removed_any = dlssg_sm86.revert_launch_options(cur, inst), True
    if not removed_any:
        return None
    return _write_opts(game, old, cur, write)


def setup_fg(game: Game, target: Path, method: str, opti_opts: optiscaler.InstallOptions,
             opti_bundle, dlssg_opts: dlssg_sm86.Options, dlssg_bundle, write: bool = True) -> FgSetupResult:
    """Remove whatever is installed, install the chosen method, write launch options once.

    opti_bundle / dlssg_bundle are callables returning a bundle (may download)."""
    old = _read_opts(game)
    cur = old
    for inst in optiscaler.uninstall(game.path):
        cur = optiscaler.revert_launch_options(cur, inst)
    for inst in dlssg_sm86.uninstall(game.path):
        cur = dlssg_sm86.revert_launch_options(cur, inst)
    rel = str(target.relative_to(game.path)) if target != game.path else "."
    warnings: list[str] = []
    if method == "native":
        bundle = dlssg_bundle()
        res = dlssg_sm86.install(game.path, target, bundle, dlssg_opts)
        env, ov = dlssg_sm86.launch_env(dlssg_opts)
        cur, undo = launchopts.apply(cur, env, ov)
        dlssg_sm86.set_launch_undo(game.path, rel, undo)
        version, placed, warnings = bundle.version, len(res.placed), res.warnings
    else:
        opti_opts.fg_mode = method
        bundle = opti_bundle()
        res = optiscaler.install(game.path, target, bundle, opti_opts)
        env, ov = optiscaler.launch_env(opti_opts)
        cur, undo = launchopts.apply(cur, env, ov)
        optiscaler.set_launch_undo(game.path, rel, undo)
        version, placed = bundle.tag, len(res.placed)
        warnings = list(res.warnings) + [f"нет ключа в ini: {m}" for m in res.ini_missing]
        if res.skipped:
            warnings.append("Оставлены файлы игры: " + ", ".join(Path(s).name for s in res.skipped))
    lu = _write_opts(game, old, cur, write)
    if "DXVK_NVAPI_GPU_ARCH" in lu.new and not (method != "native" and opti_opts.spoof_ada):
        lu.error = ((lu.error + "\n") if lu.error else "") + (
            "В параметрах запуска есть DXVK_NVAPI_GPU_ARCH — уберите его: на RTX 30 он вешает видеокарту.")
    return FgSetupResult(method, version, target, placed, warnings, lu)
