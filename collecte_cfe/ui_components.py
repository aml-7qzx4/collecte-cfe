"""Composants visuels natifs et ressources locales de Collecte CFE."""

import re
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFontDatabase, QIcon, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
)

from .ui_kit import button, hbox, make_frame, restyle, text_label, vbox

ASSET_ROOT = Path(__file__).parent / 'assets'
_FONT_FAMILIES = None


def asset_path(relative: str) -> str:
    return str(ASSET_ROOT / relative)


def load_local_fonts() -> dict[str, str]:
    global _FONT_FAMILIES
    if _FONT_FAMILIES is not None:
        return dict(_FONT_FAMILIES)
    families = {'title': 'Segoe UI', 'body': 'Segoe UI', 'mono': 'Segoe UI'}
    # The design's fonts first; the earlier ones stay as fallbacks while they are not bundled.
    files = {
        'title': ['BricolageGrotesque-Variable.ttf', 'Sora-Variable.ttf'],
        'body': ['IBMPlexSans-Regular.ttf', 'Inter-Variable.ttf'],
        'mono': ['IBMPlexMono-Medium.ttf'],
    }
    for role, names in files.items():
        for name in names:
            if not Path(asset_path(f'fonts/{name}')).is_file():
                continue
            font_id = QFontDatabase.addApplicationFont(asset_path(f'fonts/{name}'))
            registered = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
            if registered:
                families[role] = registered[0]
                for extra in ('Medium', 'SemiBold'):
                    if role == 'body' and name.endswith('-Regular.ttf'):
                        QFontDatabase.addApplicationFont(asset_path(f'fonts/{name.replace("Regular", extra)}'))
                break
    _FONT_FAMILIES = families
    return dict(families)


class NavButton(QPushButton):
    def __init__(self, title: str, subtitle: str, icon_name: str, slot: Callable, parent=None):
        super().__init__(parent)
        self.title = title
        self.subtitle = subtitle
        self.icon_name = icon_name
        self.setObjectName('nav')
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIcon(QIcon(asset_path(f'icons/{icon_name}')))
        self.setIconSize(QSize(20, 20))
        self.clicked.connect(lambda _checked=False: slot())
        self.setText(self.title if not self.subtitle else f'{self.title}\n{self.subtitle}')
        self.setIconSize(QSize(18, 18))
        self.setToolTip(self.title if not self.subtitle else f'{self.title} · {self.subtitle}')
        self.setMinimumHeight(42 if not self.subtitle else 50)


class ElidedLabel(QLabel):
    """A one-line label that ends with an ellipsis when its text does not fit."""

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, self.width())
        self.style().drawItemText(
            painter,
            self.rect(),
            int(self.alignment() | Qt.AlignmentFlag.AlignVCenter),
            self.palette(),
            self.isEnabled(),
            text,
            self.foregroundRole(),
        )


class CampaignNavItem(QPushButton):
    """A campaign in the sidebar: its name and how many dossiers it holds."""

    def __init__(self, batch_id: int, name: str, slot: Callable, parent=None):
        super().__init__(parent)
        self.batch_id = batch_id
        self.name = name
        self.setObjectName('campaignNavItem')
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(lambda _checked=False: slot(batch_id))
        box = QVBoxLayout(self)
        box.setContentsMargins(10, 9, 10, 9)
        box.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.name_label = ElidedLabel(name)
        self.name_label.setObjectName('campaignNavName')
        self.name_label.setMinimumWidth(0)
        self.name_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.badge = QLabel()
        self.badge.setObjectName('campaignNavBadge')
        self.badge.setFixedHeight(20)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.name_label, 1)
        row.addWidget(self.badge)
        box.addLayout(row)
        # How much of the campaign is collected, in a thin bar under its name.
        self.progress = QProgressBar()
        self.progress.setObjectName('campaignNavProgress')
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        box.addWidget(self.progress)
        for widget in (self.name_label, self.badge, self.progress):
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.toggled.connect(self._show_checked)
        self.setToolTip(name)

    def set_progress(self, done: int, total: int) -> None:
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(min(done, max(1, total)) if total else 0)

    def _show_checked(self, checked: bool) -> None:
        for label in (self.name_label, self.badge, self.progress):
            restyle(label, active=bool(checked))

    def set_name(self, name: str) -> None:
        self.name = name
        self.setToolTip(name)
        self.name_label.setText(name)

    def set_badge(self, text: str, tone: str = '', detail: str = '') -> None:
        self.badge.setText(text)
        self.badge.setProperty('tone', tone)
        self.badge.setVisible(bool(text))
        self.setToolTip(f'{self.name}\n{detail}' if detail else self.name)
        restyle(self.badge)


class SegmentBar(QFrame):
    """A rounded bar split in coloured parts: dossiers with notices, without, to retry; the rest stays grey."""

    def __init__(self, height: int = 10, parent=None):
        super().__init__(parent)
        self.segments = []
        self.total = 0
        self.setFixedHeight(height)

    def set_segments(self, segments, total: int) -> None:
        self.segments = [(value, color) for value, color in segments if value > 0]
        self.total = total
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        radius = rect.height() / 2
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#EEF0F5'))
        painter.drawRoundedRect(rect, radius, radius)
        if not self.total:
            return
        painter.setClipRect(rect)
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.setClipPath(path)
        x = rect.x()
        for value, color in self.segments:
            width = rect.width() * value / self.total
            painter.setBrush(QColor(color))
            painter.drawRect(QRectF(x, rect.y(), width, rect.height()))
            x += width


class FilterChip(QPushButton):
    """A rounded filter with how many items it keeps; checked, it is filled in ink."""

    def __init__(self, key: str, label: str, parent=None):
        super().__init__(parent)
        self.key = key
        self.label = label
        self.count = 0
        self.setObjectName('filterChip')
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 0, 12, 0)
        row.setSpacing(6)
        self.name_label = text_label(label, 'filterChipLabel')
        self.count_label = text_label('0', 'filterChipCount')
        for widget in (self.name_label, self.count_label):
            widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            row.addWidget(widget)
        self.setFixedHeight(32)
        self.toggled.connect(lambda _checked: self._restyle())
        self.set_count(0)

    def _restyle(self) -> None:
        for label in (self.name_label, self.count_label):
            restyle(label, active=self.isChecked())

    def text(self) -> str:
        return f'{self.label} {self.count}'

    def set_count(self, count: int) -> None:
        self.count = count
        self.count_label.setText(f'{count:,}'.replace(',', ' '))
        self.setAccessibleName(f'{self.label} {count}')
        self.setMinimumWidth(self.layout().sizeHint().width())


class CampaignTab(QPushButton):
    """A numbered step of the open campaign (Collecte, Dossiers, Courriels) with where it stands in a small badge."""

    def __init__(self, title: str, slot: Callable, number: int = 0, parent=None):
        super().__init__(parent)
        self.setObjectName('campaignTab')
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(lambda _checked=False: slot())
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 0, 14, 0)
        row.setSpacing(8)
        # The step number says in which order the campaign is worked through.
        self.step_label = QLabel(str(number) if number else '')
        self.step_label.setObjectName('campaignTabStep')
        self.step_label.setFixedSize(20, 20)
        self.step_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.step_label.setVisible(bool(number))
        self.title_label = QLabel(title)
        self.title_label.setObjectName('campaignTabTitle')
        self.badge = QLabel()
        self.badge.setObjectName('campaignTabBadge')
        self.badge.setFixedHeight(20)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for label in (self.step_label, self.title_label, self.badge):
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            row.addWidget(label)
        self.setFixedHeight(42)
        self.toggled.connect(lambda checked: self._restyle())
        self.set_badge('')

    def _restyle(self) -> None:
        for label in (self.step_label, self.title_label, self.badge):
            restyle(label, active=self.isChecked())
        restyle(self)

    def set_badge(self, text: str, tone: str = '') -> None:
        self.badge.setText(text)
        self.badge.setProperty('tone', tone)
        self.badge.setVisible(bool(text))
        self._restyle()
        self.setMinimumWidth(self.layout().sizeHint().width())


class ClickableRow(QFrame):
    """A row that opens what it shows when it is clicked anywhere."""

    def __init__(self, slot: Callable, parent=None):
        super().__init__(parent)
        self.slot = slot
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, event) -> None:
        super().mouseReleaseEvent(event)
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.rect().contains(event.position().toPoint())
            and self.isEnabled()
        ):
            self.slot()


class PasswordField(QLineEdit):
    """A password field with an eye at its right end to show or hide what was typed."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEchoMode(QLineEdit.EchoMode.Password)
        self.toggle = QToolButton(self)
        self.toggle.setObjectName('passwordVisibility')
        self.toggle.setCheckable(True)
        self.toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle.setIconSize(QSize(18, 18))
        self.toggle.toggled.connect(self._show)
        self._show(False)
        self.setTextMargins(0, 0, 30, 0)

    def _show(self, visible: bool) -> None:
        self.setEchoMode(QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)
        self.toggle.setIcon(QIcon(asset_path('icons/eye-off.svg' if visible else 'icons/eye.svg')))
        self.toggle.setToolTip('Masquer le mot de passe' if visible else 'Afficher le mot de passe')

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.toggle.setGeometry(self.width() - 34, (self.height() - 28) // 2, 28, 28)


class AccountDialog(QDialog):
    """Add or change an expert account: what it is called, its login, and its password kept in the Windows vault."""

    DELETE = 2

    def __init__(self, account=None, parent=None, *, refused=False):
        super().__init__(parent)
        self.editing = account is not None
        self.setObjectName('accountDialog')
        self.setWindowTitle('Compte impots.gouv')
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(16)
        title = QLabel(f'Modifier « {account["label"]} »' if account else 'Ajouter un compte impots.gouv')
        title.setObjectName('dialogTitle')
        title.setWordWrap(True)
        layout.addWidget(title)
        if refused:
            alert = QLabel(
                'impots.gouv.fr a refusé ces identifiants. Corrigez le mot de passe pour réactiver la connexion automatique.'
            )
            alert.setObjectName('dialogAlert')
            alert.setWordWrap(True)
            layout.addWidget(alert)
        self.label_input = QLineEdit((account or {}).get('label', ''))
        self.label_input.setPlaceholderText('Cabinet · principal')
        self.login_input = QLineEdit((account or {}).get('login', ''))
        self.login_input.setPlaceholderText('adresse@cabinet.fr')
        self.password = PasswordField()
        self.password_visibility = self.password.toggle
        self.errors = {}
        for name, label, field, helper in (
            ('label', 'Nom du compte', self.label_input, 'Pour le reconnaître dans l’application.'),
            (
                'login',
                'Adresse de connexion',
                self.login_input,
                'L’adresse e-mail du compte professionnel impots.gouv.fr.',
            ),
            (
                'password',
                'Mot de passe',
                self.password,
                'Laissez vide pour garder le mot de passe enregistré.'
                if account
                else 'Il reste sur ce poste, dans le coffre Windows.',
            ),
        ):
            block = vbox(spacing=5)
            caption = text_label(label, 'fieldLabel')
            block.addWidget(caption)
            block.addWidget(field)
            hint = text_label(helper, 'fieldHint', wrap=True)
            block.addWidget(hint)
            error = text_label('', 'formError', wrap=True)
            error.hide()
            block.addWidget(error)
            self.errors[name] = (error, hint)
            layout.addLayout(block)
            field.textEdited.connect(lambda _text, key=name: self._touch(key))
        rule = make_frame('wizardRule')
        layout.addWidget(rule)
        actions = hbox(spacing=8)
        self.delete_button = button('Supprimer ce compte', lambda: self.done(self.DELETE), name='dangerText')
        self.delete_button.setVisible(self.editing)
        actions.addWidget(self.delete_button)
        actions.addStretch()
        self.save_button = button('Enregistrer', self.accept, True)
        self.save_button.setDefault(True)
        actions.addWidget(button('Annuler', self.reject))
        actions.addWidget(self.save_button)
        layout.addLayout(actions)
        self.touched = set()
        self.validate()
        # A refused account is opened to change its password.
        if refused:
            self.password.setFocus()

    def _touch(self, key):
        self.touched.add(key)
        self.validate()

    def problems(self):
        """What keeps the account from being saved, field by field."""
        found = {}
        if not self.label_input.text().strip():
            found['label'] = 'Donnez un nom au compte.'
        if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', self.login_input.text().strip()):
            found['login'] = 'Indiquez une adresse e-mail valide.'
        if not self.editing and not self.password.text():
            found['password'] = 'Indiquez le mot de passe du compte.'
        return found

    def validate(self):
        found = self.problems()
        for key, (error, hint) in self.errors.items():
            shown = key in found and key in self.touched
            error.setText(found.get(key, ''))
            error.setVisible(shown)
            hint.setVisible(not shown)
        self.save_button.setEnabled(not found)
        return not found
