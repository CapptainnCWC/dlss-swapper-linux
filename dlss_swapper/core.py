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


def apply_launch_options(game: Game, rel_target: str, opts: optiscaler.InstallOptions,
                         write: bool = True) -> LaunchUpdate:
    env, ov = optiscaler.launch_env(opts)
    return _apply_env(game, env, ov, lambda undo: optiscaler.set_launch_undo(game.path, rel_target, undo), write)


def apply_dlssg_launch_options(game: Game, rel_target: str, opts: dlssg_sm86.Options,
                               write: bool = True) -> LaunchUpdate:
    env, ov = dlssg_sm86.launch_env(opts)
    lu = _apply_env(game, env, ov, lambda undo: dlssg_sm86.set_launch_undo(game.path, rel_target, undo), write)
    if "DXVK_NVAPI_GPU_ARCH" in lu.new:
        lu.error = ((lu.error + "\n") if lu.error else "") + (
            "В параметрах запуска есть DXVK_NVAPI_GPU_ARCH — уберите его: dlssg_for_sm86 подменяет "
            "архитектуру сам, а глобальная подмена может вешать видеокарту.")
    return lu


def _apply_env(game: Game, env: dict, ov: dict, save_undo, write: bool) -> LaunchUpdate:
    old = ""
    if game.source in ("steam", "shortcut"):
        try:
            old = steam.get_launch_options(game)
        except Exception:
            old = ""
    new, undo = launchopts.apply(old, env, ov)
    save_undo(undo)
    if not write or game.source == "folder":
        return LaunchUpdate(old, new, False,
                            None if write is False else "Игра не из Steam: добавьте её как "
                            "стороннюю игру в Steam и вставьте строку в Параметры запуска.")
    try:
        steam.set_launch_options(game, new)
        return LaunchUpdate(old, new, True)
    except steam.SteamRunningError as e:
        return LaunchUpdate(old, new, False, str(e))
    except Exception as e:  # noqa: BLE001
        return LaunchUpdate(old, new, False, f"Не удалось записать параметры запуска: {e}")


def remove_optiscaler(game: Game, rel_target: str | None = None, write: bool = True) -> LaunchUpdate | None:
    removed = optiscaler.uninstall(game.path, rel_target)
    return _revert(game, removed, optiscaler.revert_launch_options, write)


def remove_dlssg(game: Game, rel_target: str | None = None, write: bool = True) -> LaunchUpdate | None:
    removed = dlssg_sm86.uninstall(game.path, rel_target)
    return _revert(game, removed, dlssg_sm86.revert_launch_options, write)


def _revert(game: Game, removed: list, revert_fn, write: bool) -> LaunchUpdate | None:
    if not removed or game.source == "folder":
        return None
    old = steam.get_launch_options(game)
    new = old
    for inst in removed:
        new = revert_fn(new, inst)
    if new == old:
        return LaunchUpdate(old, new, False)
    if not write:
        return LaunchUpdate(old, new, False)
    try:
        steam.set_launch_options(game, new)
        return LaunchUpdate(old, new, True)
    except steam.SteamRunningError as e:
        return LaunchUpdate(old, new, False, str(e))
