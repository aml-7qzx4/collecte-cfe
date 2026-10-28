"""Small widget factories shared by every screen, so each one is built the same way in one line."""

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLayout, QPushButton, QVBoxLayout, QWidget

Margins = int | tuple[int, int, int, int]


def restyle(widget: QWidget, **properties) -> None:
    """Set dynamic properties, then re-apply the style rules that depend on them."""
    for name, value in properties.items():
        widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def make_frame(name: str) -> QFrame:
    """A frame the style sheet draws by its name (card, panel, row…)."""
    frame = QFrame()
    frame.setObjectName(name)
    return frame


def make_widget(name: str) -> QWidget:
    widget = QWidget()
    widget.setObjectName(name)
    return widget


def text_label(text: str = '', name: str = '', *, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    if wrap:
        label.setWordWrap(True)
    return label


def button(text: str, slot: Callable, primary: bool = False, *, name: str = '') -> QPushButton:
    widget = QPushButton(text)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    widget.clicked.connect(slot)
    if name or primary:
        widget.setObjectName(name or 'primary')
    return widget


def _spaced(layout: QLayout, margins: Margins | None, spacing: int | None) -> QLayout:
    if margins is not None:
        layout.setContentsMargins(*((margins,) * 4 if isinstance(margins, int) else margins))
    if spacing is not None:
        layout.setSpacing(spacing)
    return layout


def vbox(parent: QWidget | None = None, margins: Margins | None = None, spacing: int | None = None) -> QVBoxLayout:
    return _spaced(QVBoxLayout(parent) if parent is not None else QVBoxLayout(), margins, spacing)


def hbox(parent: QWidget | None = None, margins: Margins | None = None, spacing: int | None = None) -> QHBoxLayout:
    return _spaced(QHBoxLayout(parent) if parent is not None else QHBoxLayout(), margins, spacing)
