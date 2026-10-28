"""Où l'application range ses données : sur le poste, ou à côté du programme en version portable."""

import os
import shutil
import sys
from pathlib import Path

PORTABLE_MARKER = 'portable.txt'
PORTABLE_DATA = 'Donnees'
DATA_NAME = 'Collecte CFE'
DATABASE = 'collecte-cfe.sqlite'
LEGACY_DATA_NAME = 'Bubot'
LEGACY_DATABASE = 'bubot.sqlite'


def program_dir() -> Path:
    """The folder of the built program; the source tree when run from Python."""
    return Path(sys.executable).parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent


def portable_data_dir(directory: Path | None = None) -> Path | None:
    """A copy carried on a USB key keeps its data beside it, when its folder holds the portable marker."""
    directory = directory or program_dir()
    if not getattr(sys, 'frozen', False) and directory == program_dir():
        return None
    return directory / PORTABLE_DATA if (directory / PORTABLE_MARKER).is_file() else None


def local_root() -> Path:
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home())))


def data_dir() -> Path:
    return portable_data_dir() or local_root() / DATA_NAME


def legacy_database_paths() -> list[Path]:
    """Where the database lived before the rename, to find the passwords filed under its old location."""
    return [local_root() / LEGACY_DATA_NAME / LEGACY_DATABASE, data_dir() / LEGACY_DATABASE]


def prepare_data_dir() -> Path:
    """The data folder, after moving an older « Bubot » folder and database to their new names."""
    directory = data_dir()
    legacy = local_root() / LEGACY_DATA_NAME
    if portable_data_dir() is None and legacy.is_dir() and not directory.exists():
        try:
            legacy.rename(directory)
        except OSError:
            shutil.copytree(legacy, directory)
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / LEGACY_DATABASE).is_file() and not (directory / DATABASE).exists():
        for suffix in ('', '-wal', '-shm'):
            old = directory / f'{LEGACY_DATABASE}{suffix}'
            if old.exists():
                old.rename(directory / f'{DATABASE}{suffix}')
    return directory


def app_settings():
    """Preferences in an INI file beside the data for the portable copy, in the Windows registry otherwise."""
    from PySide6.QtCore import QSettings

    portable = portable_data_dir()
    if portable is not None:
        portable.mkdir(parents=True, exist_ok=True)
        return QSettings(str(portable / 'reglages.ini'), QSettings.Format.IniFormat)
    settings = QSettings(DATA_NAME, DATA_NAME)
    if not settings.allKeys():
        legacy = QSettings('Bubot', 'Bubot CFE')
        for key in legacy.allKeys():
            settings.setValue(key, legacy.value(key))
    return settings
