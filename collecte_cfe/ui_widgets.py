"""Composants UI autonomes partagés par le tableau de bord."""

from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QLabel,
    QLineEdit,
)

from .ui_components import asset_path
from .ui_kit import button, hbox, restyle, text_label, vbox


class ElidedPathLineEdit(QLineEdit):
    """Champ de chemin lisible : valeur complète pour le métier, affichage raccourci."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._full_path = ''
        self.setReadOnly(True)

    def setText(self, value):
        self._full_path = str(value or '')
        self.setToolTip(self._full_path)
        self._refresh_display()

    def text(self):
        return self._full_path

    def displayed_path(self):
        return super().text()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_display()

    def _refresh_display(self):
        shown = (
            self.fontMetrics().elidedText(self._full_path, Qt.TextElideMode.ElideMiddle, max(60, self.width() - 26))
            if self._full_path
            else ''
        )
        super().setText(shown)


class PortfolioDropZone(QFrame):
    file_dropped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setProperty('dragActive', False)

    @staticmethod
    def portfolio_path(urls):
        if len(urls) != 1 or not urls[0].isLocalFile():
            return None
        path = urls[0].toLocalFile()
        return path if Path(path).suffix.lower() in ('.csv', '.xlsx') else None

    def _set_drag_active(self, value):
        restyle(self, dragActive=bool(value))
        self.update()

    def dragEnterEvent(self, event):
        if self.portfolio_path(event.mimeData().urls()):
            self._set_drag_active(True)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._set_drag_active(False)
        event.accept()

    def dropEvent(self, event):
        path = self.portfolio_path(event.mimeData().urls())
        self._set_drag_active(False)
        if path:
            event.acceptProposedAction()
            self.file_dropped.emit(path)
        else:
            event.ignore()


def when(stamp, *, year=False, time=True):
    """« 12/11 à 14:05 » from a stored ISO timestamp; with the year or without the time on demand."""
    day = f'{stamp[8:10]}/{stamp[5:7]}' + (f'/{stamp[:4]}' if year else '')
    return f'{day} à {stamp[11:16]}' if time else day


class ActionToast(QFrame):
    """Message in the bottom-right corner: what happened, why it matters, and a way to open the result.

    Confirmations fade after a few seconds; warnings stay until closed, since nothing else keeps them."""

    TONES = ('success', 'info', 'warning')

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName('actionToast')
        self.setFixedWidth(392)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(36)
        shadow.setOffset(0, 10)
        shadow.setColor(QColor(19, 26, 44, 46))
        self.setGraphicsEffect(shadow)
        row = hbox(self, margins=(16, 14, 10, 14), spacing=12)
        self.icon = QLabel()
        self.icon.setFixedSize(28, 28)
        row.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        text = vbox(spacing=3)
        row.addLayout(text, 1)
        self.message = text_label('', 'toastMessage', wrap=True)
        text.addWidget(self.message)
        self.detail = text_label('', 'toastDetail', wrap=True)
        text.addWidget(self.detail)
        self.open_button = button('Ouvrir', self.open_target, name='toastOpen')
        self.target = None
        text.addWidget(self.open_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.dismiss = button('', self.hide, name='toastDismiss')
        self.dismiss.setFixedSize(26, 26)
        self.dismiss.setIcon(QIcon(asset_path('icons/close.svg')))
        self.dismiss.setIconSize(QSize(14, 14))
        self.dismiss.setToolTip('Fermer')
        row.addWidget(self.dismiss, 0, Qt.AlignmentFlag.AlignTop)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)
        self.hide()

    def announce(self, message, target=None, *, detail='', tone='success'):
        tone = tone if tone in self.TONES else 'info'
        restyle(self, tone=tone)
        self.icon.setPixmap(QIcon(asset_path(f'icons/toast-{tone}.svg')).pixmap(28, 28))
        self.message.setText(message)
        self.detail.setText(detail)
        self.detail.setVisible(bool(detail))
        self.target = Path(target) if target is not None else None
        self.open_button.setVisible(target is not None)
        if target is not None:
            self.open_button.setText(
                'Ouvrir le fichier  →' if Path(target).suffix.lower() == '.xlsx' else 'Ouvrir le dossier  →'
            )
        self.adjustSize()
        parent = self.parentWidget()
        self.move(max(8, parent.width() - self.width() - 24), max(8, parent.height() - self.height() - 24))
        self.raise_()
        self.show()
        if tone == 'warning':
            self.timer.stop()
        else:
            self.timer.start(8000)

    def open_target(self):
        if self.target is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.target)))
