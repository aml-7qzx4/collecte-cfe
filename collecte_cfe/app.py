"""Entrée desktop compatible avec les lanceurs existants."""

from .dashboard import MainWindow, main
from .sessions import BrowserWorker

__all__ = ['MainWindow', 'BrowserWorker', 'main']
