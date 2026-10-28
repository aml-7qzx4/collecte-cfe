import re
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QFrame, QLabel, QLineEdit, QPlainTextEdit, QScrollArea, QWidget

from . import APP_NAME
from .client_mail import MailTemplate
from .ui_kit import button, hbox, make_frame, make_widget, text_label, vbox

SETTINGS_PAGE = 4
SECTIONS = (
    ('accounts', 'Comptes impots.gouv'),
    ('mails', 'Texte des mails'),
    ('station', 'Ce poste'),
    ('journal', 'Journal'),
)


def _section(title, text=''):
    """A white section with its title and one line on what it holds."""
    frame = make_frame('settingsSection')
    box = vbox(frame, margins=0, spacing=0)
    head = make_frame('settingsHead')
    head_row = hbox(head, margins=(20, 16, 20, 16), spacing=12)
    titles = vbox(spacing=4)
    heading = text_label(title, 'settingsTitle')
    titles.addWidget(heading)
    if text:
        line = text_label(text, 'muted', wrap=True)
        titles.addWidget(line)
    head_row.addLayout(titles, 1)
    box.addWidget(head)
    return frame, box, head_row


def application_version():
    """When this copy of the application was built, read from the installed program."""
    if getattr(sys, 'frozen', False):
        try:
            return f'{datetime.fromtimestamp(Path(sys.executable).stat().st_mtime):%d/%m/%Y} · version installée'
        except OSError:
            return 'Version installée'
    return 'Version de développement'


class SettingsPageMixin:
    """What holds for every campaign of this workstation: expert accounts, sessions, mail text, name and journal."""

    def build_settings_page(self):
        page = make_widget('page')
        outer = hbox(page, margins=0, spacing=24)
        menu = vbox(spacing=2)
        self.settings_links = {}
        for key, label in SECTIONS:
            link = button(label, lambda _=False, k=key: self.show_settings_section(k))
            link.setObjectName('settingsLink')
            link.setCheckable(True)
            menu.addWidget(link)
            self.settings_links[key] = link
        menu.addStretch()
        menu_frame = QWidget()
        menu_frame.setLayout(menu)
        menu_frame.setFixedWidth(200)
        outer.addWidget(menu_frame)

        content = make_widget('page')
        sections = vbox(content, margins=(0, 0, 8, 0), spacing=16)
        self.settings_sections = {}

        accounts, box, head = _section(
            'Comptes impots.gouv',
            'La collecte se connecte avec ces comptes. Les mots de passe restent dans le coffre Windows de ce poste.',
        )
        self.add_account_button = button('Ajouter un compte', self.add_expert_account, True)
        head.addWidget(self.add_account_button, 0, Qt.AlignmentFlag.AlignTop)
        # Without any account, one sentence and the button that adds the first one.
        self.accounts_empty = make_frame('accountsEmpty')
        empty = vbox(self.accounts_empty, margins=(20, 22, 20, 24), spacing=10)
        empty_text = QLabel(
            'Aucun compte expert. Ajoutez le compte impots.gouv.fr du cabinet : la collecte s’y connectera seule.'
        )
        empty_text.setObjectName('muted')
        empty_text.setWordWrap(True)
        empty.addWidget(empty_text)
        first = button('Ajouter le premier compte', self.add_expert_account, name='accent')
        empty.addWidget(first, 0, Qt.AlignmentFlag.AlignLeft)
        box.addWidget(self.accounts_empty)
        self.account_rows_box = vbox(spacing=0)
        box.addLayout(self.account_rows_box)
        self.account_rows = []
        sections.addWidget(accounts)
        self.settings_sections['accounts'] = accounts

        mails = make_frame('settingsSection')
        mail_row = hbox(mails, margins=(20, 16, 20, 16), spacing=16)
        mail_text = vbox(spacing=4)
        mail_title = text_label('Texte des mails', 'settingsTitle')
        mail_text.addWidget(mail_title)
        self.settings_mail_state = text_label('', 'muted', wrap=True)
        mail_text.addWidget(self.settings_mail_state)
        mail_text.addSpacing(8)
        self.settings_mail_button = button('Modifier le texte', self.edit_mail_template, name='headerAccent')
        mail_text.addWidget(self.settings_mail_button, 0, Qt.AlignmentFlag.AlignLeft)
        mail_text.addStretch()
        mail_row.addLayout(mail_text, 1)
        self.settings_mail_preview = text_label('', 'settingsPreview', wrap=True)
        self.settings_mail_preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        mail_row.addWidget(self.settings_mail_preview, 1)
        sections.addWidget(mails)
        self.settings_sections['mails'] = mails

        station = make_frame('settingsSection')
        station_box = vbox(station, margins=(20, 16, 20, 18), spacing=14)
        station_title = text_label('Ce poste', 'settingsTitle')
        station_box.addWidget(station_title)
        columns = hbox(spacing=16)
        name_box = vbox(spacing=6)
        name_label = text_label('Votre nom', 'fieldLabel')
        name_box.addWidget(name_label)
        self.account_name = QLineEdit(str(self.settings.value('profile/display_name', '') or ''))
        self.account_name.setMaxLength(100)
        self.account_name.setPlaceholderText('Votre nom')
        self.account_name.editingFinished.connect(self.save_profile_name)
        name_box.addWidget(self.account_name)
        name_box.addStretch()
        columns.addLayout(name_box, 1)
        data_box = vbox(spacing=6)
        data_label = text_label('Données de l’application', 'fieldLabel')
        data_box.addWidget(data_label)
        data_path = text_label(str(self.store.path.parent), 'mono', wrap=True)
        data_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        data_box.addWidget(data_path)
        data_open = button(
            'Ouvrir le dossier',
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.path.parent))),
            name='textButton',
        )
        data_box.addWidget(data_open, 0, Qt.AlignmentFlag.AlignLeft)
        data_box.addStretch()
        columns.addLayout(data_box, 1)
        version_box = vbox(spacing=6)
        version_label = text_label('Version', 'fieldLabel')
        version_box.addWidget(version_label)
        self.settings_version = QLabel(application_version())
        version_box.addWidget(self.settings_version)
        version_box.addStretch()
        columns.addLayout(version_box, 1)
        station_box.addLayout(columns)
        sections.addWidget(station)
        self.settings_sections['station'] = station

        journal, box, head = _section('Journal', 'Sans identifiants ni mots de passe.')
        export = button('Exporter pour le support', self.export_diagnostics)
        head.addWidget(export, 0, Qt.AlignmentFlag.AlignTop)
        self.logs_view = QPlainTextEdit()
        self.logs_view.setObjectName('diagnosticLog')
        self.logs_view.setReadOnly(True)
        self.logs_view.setMinimumHeight(220)
        box.addWidget(self.logs_view, 1)
        sections.addWidget(journal)
        self.settings_sections['journal'] = journal
        sections.addStretch()

        self.settings_scroll = QScrollArea()
        self.settings_scroll.setObjectName('pageScroll')
        self.settings_scroll.setWidgetResizable(True)
        self.settings_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.settings_scroll.setWidget(content)
        outer.addWidget(self.settings_scroll, 1)
        self.settings_scroll.verticalScrollBar().valueChanged.connect(self.mark_settings_section)
        self.pages.addWidget(page)
        self.mark_settings_section()

    def show_settings_section(self, key):
        self.settings_scroll.verticalScrollBar().setValue(self.settings_sections[key].y())
        for name, link in self.settings_links.items():
            link.setChecked(name == key)

    def mark_settings_section(self, *_):
        """Highlight the section at the top of the scrolled settings."""
        top = self.settings_scroll.verticalScrollBar().value()
        current = SECTIONS[0][0]
        for key, _label in SECTIONS:
            if self.settings_sections[key].y() <= top + 24:
                current = key
        if self.settings_scroll.verticalScrollBar().value() >= self.settings_scroll.verticalScrollBar().maximum() > 0:
            current = SECTIONS[-1][0]
        for name, link in self.settings_links.items():
            link.setChecked(name == current)

    def refresh_settings_page(self):
        template = self.store.mail_template()
        self.settings_mail_state.setText('Texte par défaut' if template == MailTemplate() else 'Texte personnalisé')
        self.settings_mail_preview.setText(f'Objet : {template.subject}\n\n{template.body}')
        self.refresh_diagnostic_view()
        self.update_account_states()

    def save_profile_name(self):
        name = ' '.join(self.account_name.text().split())[:100]
        if name == (self.settings.value('profile/display_name', '') or ''):
            self.account_name.setText(name)
            return
        self.account_name.setText(name)
        self.settings.setValue('profile/display_name', name)
        self.update_user_block()
        self.say('Nom enregistré.' if name else 'Nom effacé.')

    def refresh_diagnostic_view(self):
        # « 27/10 10:46  Session 1 connectée » rather than the stored timestamp.
        lines = [
            re.sub(r'^\d{4}-(\d{2})-(\d{2})T(\d{2}:\d{2})\S*\s*\|\s*', r'\2/\1 \3  ', line)
            for line in self.diagnostics.recent().splitlines()
        ]
        self.logs_view.setPlainText('\n'.join(lines))
        self.logs_view.moveCursor(self.logs_view.textCursor().MoveOperation.End)

    def export_diagnostics(self):
        suggested = f'{APP_NAME.replace(" ", "-")}-diagnostic-{datetime.now():%Y%m%d-%H%M}.zip'
        destination, _ = QFileDialog.getSaveFileName(
            self, 'Exporter les logs', str(Path.home() / 'Documents' / suggested), 'Archive ZIP (*.zip)'
        )
        if not destination:
            return
        try:
            self.diagnostics.export(destination, self.diagnostic_error_path)
            self.say('Archive de diagnostic enregistrée.')
        except OSError:
            self.problem('Export impossible. Choisissez un autre emplacement.')

    def update_user_block(self):
        """The bottom of the sidebar: who works on this workstation and whether it is connected to impots.gouv.fr."""
        name = ' '.join(str(self.settings.value('profile/display_name', '') or '').split())
        self.user_name.setText(name or 'Votre nom')
        initials = ''.join(part[0] for part in name.split()[:2]).upper()
        self.user_initials.setText(initials or '·')
        connected = any(bool(value) for value in self.connected_sessions.values())
        self.user_sessions.setText('Connecté à impots.gouv.fr' if connected else 'Non connecté')
