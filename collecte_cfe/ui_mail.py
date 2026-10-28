import html
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from .client_mail import FIELDS, MailTemplate, compose_client_draft, mailto_uri
from .documents import ensure_notice_details
from .notice_extraction import format_amount
from .outlook_draft import BRIDGE, DraftUnavailable, open_draft, prepare_outlook
from .portfolio import valid_client_email
from .ui_components import FilterChip, asset_path, load_local_fonts
from .ui_kit import button, hbox, make_frame, make_widget, text_label, vbox
from .ui_results import _CellDelegate
from .ui_widgets import when

MAIL_FILTERS = (
    ('ALL', 'Tous'),
    ('READY', 'Prêts'),
    ('OPENED', 'À confirmer'),
    ('SENT', 'Envoyés'),
    ('NO_EMAIL', 'Sans adresse'),
    ('WAITING', 'En attente'),
)
MAIL_COLUMNS = ['', 'Client', 'SIREN', 'E-mail', 'Ce que dit le courriel', 'État', '']
MAIL_CHECK, MAIL_CLIENT, MAIL_SIREN, MAIL_EMAIL, MAIL_SUMMARY, MAIL_STATE, MAIL_ACTION = range(len(MAIL_COLUMNS))
MAIL_ACTIONS = {'READY': ('Envoyer', 'accent'), 'OPENED': ('Marquer envoyé', 'primary'), 'SENT': ('Renvoyer', '')}
# The other rows still offer the next step: add the address, or look at the dossier that holds the mail back.
OTHER_ACTIONS = {'NO_EMAIL': 'Ajouter l’adresse', 'WAITING': 'Voir le dossier'}
MAIL_CHIPS = {
    'READY': ('#E8EBFD', '#2F45D8'),
    'OPENED': ('#FFF0DC', '#A85600'),
    'SENT': ('#DFF1F6', '#1A6A80'),
    'NO_EMAIL': ('#EBEEF4', '#59627A'),
    'WAITING': ('#F0F2F6', '#59627A'),
}
# Tests open drafts on the calling thread, so that they can check the result right away.
DRAFTS_IN_BACKGROUND = True


class DraftJob(QObject):
    """Opens prepared mails in Outlook one after the other, away from the window's thread.

    Starting PowerShell and Outlook takes seconds: done on the window's thread, it froze the application."""

    finished = Signal(object)

    def __init__(self, opener, drafts):
        super().__init__()
        self.opener = opener
        self.drafts = drafts

    def start(self):
        if DRAFTS_IN_BACKGROUND:
            threading.Thread(target=self.run, daemon=True).start()
        else:
            self.run()

    def run(self):
        results = []
        for draft in self.drafts:
            try:
                self.opener(draft['to'], draft['subject'], draft['body'], draft['attachments'])
                results.append((draft, None))
            except Exception as error:
                results.append((draft, error))
        self.finished.emit(results)


def mail_summary(notices):
    """What the client's message says, in a few words: what to pay, or that nothing is to be paid."""
    finished = [notice for notice in notices if notice.get('status') == 'TERMINE']
    methods = {notice.get('payment_method') for notice in finished}
    to_pay = [notice.get('amount_due_cents') or 0 for notice in finished if notice.get('payment_method') == 'A_REGLER']
    if to_pay:
        return f'{format_amount(sum(to_pay))} à régler'
    if 'PRELEVEMENT_MENSUEL' in methods:
        return 'Prélèvement mensuel'
    if 'PRELEVEMENT_ECHEANCE' in methods:
        return 'Prélevé à l’échéance'
    if methods == {'AUCUN_PAIEMENT'}:
        return (
            'Rien à payer, remboursement' if any(notice.get('refund_cents') for notice in finished) else 'Rien à payer'
        )
    return ''


def mail_state(candidate):
    """Where a client's mail stands: ready, opened and to confirm, sent, missing its address, or waiting for the collection."""
    if not all(status == 'TERMINE' for status in candidate['statuses']):
        return 'WAITING', 'Collecte à terminer'
    sent = candidate['dossier'].get('mail_sent_at') or ''
    if sent:
        return 'SENT', f'Envoyé le {when(sent, time=False)}'
    try:
        compose_client_draft(candidate['dossier'], candidate['notices'])
    except ValueError as error:
        if 'Adresse' in str(error):
            return 'NO_EMAIL', 'Adresse à ajouter'
        return 'WAITING', 'Avis à vérifier' if 'vérifier' in str(error) else str(error)
    if candidate['dossier'].get('mail_opened_at'):
        return 'OPENED', 'À confirmer'
    return 'READY', 'Prêt'


def chip_label(state, label):
    return {'READY': 'Prêt', 'OPENED': 'À confirmer', 'NO_EMAIL': 'Sans adresse', 'WAITING': 'En attente'}.get(
        state, label
    )


class ClientDelegate(_CellDelegate):
    """The client's name, with the address its mail goes to underneath."""

    def paint(self, painter, option, index):
        self.paint_background(painter, option, index)
        fonts = load_local_fonts()
        rect = option.rect.adjusted(6, 10, -8, -10)
        painter.save()
        name_font = QFont(fonts['body'])
        name_font.setPixelSize(13)
        name_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(name_font)
        painter.setPen(QColor('#131A2C'))
        name = painter.fontMetrics().elidedText(str(index.data() or ''), Qt.TextElideMode.ElideRight, rect.width())
        painter.drawText(
            QRectF(rect.x(), rect.y(), rect.width(), rect.height() / 2),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            name,
        )
        email = str(index.data(Qt.ItemDataRole.UserRole + 2) or '')
        detail = QFont(fonts['body'])
        detail.setPixelSize(13)
        painter.setFont(detail)
        painter.setPen(QColor('#59627A'))
        shown = painter.fontMetrics().elidedText(
            email or 'Adresse à ajouter', Qt.TextElideMode.ElideRight, rect.width()
        )
        painter.drawText(
            QRectF(rect.x(), rect.y() + rect.height() / 2, rect.width(), rect.height() / 2),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            shown,
        )
        painter.restore()


class MailStateDelegate(_CellDelegate):
    """Where the mail stands, as a coloured chip."""

    def paint(self, painter, option, index):
        self.paint_background(painter, option, index)
        label = str(index.data() or '')
        if not label:
            return
        background, foreground = MAIL_CHIPS.get(
            index.data(Qt.ItemDataRole.UserRole) or 'WAITING', MAIL_CHIPS['WAITING']
        )
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont(load_local_fonts()['body'])
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        width = min(option.rect.width() - 12, painter.fontMetrics().horizontalAdvance(label) + 20)
        chip = QRectF(option.rect.x() + 4, option.rect.center().y() - 12, width, 24)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(background))
        painter.drawRoundedRect(chip, 12, 12)
        painter.setPen(QColor(foreground))
        painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, label)
        painter.restore()


class MailTemplateDialog(QDialog):
    """The cabinet's own wording for client mails, previewed on a real client while it is written."""

    def __init__(self, owner, template, sample):
        super().__init__(owner)
        self.setWindowTitle('Personnaliser le mail')
        self.setMinimumSize(940, 580)
        self.sample = sample
        self.saved = None
        layout = hbox(self, margins=(22, 20, 22, 18), spacing=20)
        form = vbox(spacing=8)
        layout.addLayout(form, 1)
        heading = text_label('Texte des mails aux clients', 'detailHeading')
        form.addWidget(heading)
        hint = text_label(
            '{avis} est remplacé par les montants et le mode de paiement de chaque avis.', 'muted', wrap=True
        )
        form.addWidget(hint)
        form.addWidget(QLabel('Objet'))
        self.subject = QLineEdit(template.subject)
        form.addWidget(self.subject)
        form.addWidget(QLabel('Texte'))
        self.body = QPlainTextEdit(template.body)
        self.body.setMinimumHeight(240)
        form.addWidget(self.body, 1)
        form.addWidget(QLabel('Insérer un champ à l’endroit du curseur :'))
        fields = hbox(spacing=6)
        for name, meaning in FIELDS.items():
            chip = QPushButton('{' + name + '}')
            chip.setObjectName('fieldChip')
            chip.setToolTip(meaning)
            chip.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            chip.clicked.connect(lambda _checked=False, field=name: self.insert(field))
            fields.addWidget(chip)
        fields.addStretch()
        form.addLayout(fields)
        self.error = text_label('', 'runHint', wrap=True)
        form.addWidget(self.error)
        actions = QHBoxLayout()
        reset = button('Rétablir le texte par défaut', self.reset, name='textButton')
        actions.addWidget(reset)
        actions.addStretch()
        actions.addWidget(button('Annuler', self.reject))
        self.save_button = button('Enregistrer', self.save, True)
        actions.addWidget(self.save_button)
        form.addLayout(actions)
        side = make_frame('card')
        side.setFixedWidth(330)
        side_box = vbox(side, margins=(16, 14, 16, 14))
        caption = text_label('Aperçu' + (f' · {sample["dossier"]["name"]}' if sample else ''), 'detailHeading')
        side_box.addWidget(caption)
        self.preview_subject = text_label('', wrap=True)
        side_box.addWidget(self.preview_subject)
        self.preview = text_label('', 'mailBody', wrap=True)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        side_box.addWidget(self.preview, 1)
        layout.addWidget(side)
        self.subject.textChanged.connect(self.refresh)
        self.body.textChanged.connect(self.refresh)
        self.refresh()

    def insert(self, field):
        # The chips take no focus: the field goes where the cursor was, in the subject or the text.
        if self.subject.hasFocus():
            self.subject.insert('{' + field + '}')
        else:
            self.body.insertPlainText('{' + field + '}')
            self.body.setFocus()

    def template(self):
        return MailTemplate(self.subject.text(), self.body.toPlainText())

    def reset(self):
        default = MailTemplate()
        self.subject.setText(default.subject)
        self.body.setPlainText(default.body)

    def refresh(self):
        try:
            template = self.template().check()
        except ValueError as error:
            self.error.setText(str(error))
            self.save_button.setEnabled(False)
            return
        self.error.setText('')
        self.save_button.setEnabled(True)
        if self.sample is None:
            self.preview_subject.setText('')
            self.preview.setText('L’aperçu apparaît dès qu’un client a ses avis classés et son adresse.')
            return
        try:
            subject, body = compose_client_draft(
                self.sample['dossier'], self.sample['notices'], self.sample['attachments'], template
            )
        except ValueError as error:
            self.preview_subject.setText('')
            self.preview.setText(str(error))
            return
        self.preview_subject.setText(f'Objet : <b>{html.escape(subject)}</b>')
        self.preview.setText(body)

    def save(self):
        try:
            self.saved = self.template().check()
        except ValueError as error:
            self.error.setText(str(error))
            return
        self.accept()


class MailPageMixin:
    """One draft per client, checked here and opened in Outlook, never sent without the collaborator."""

    def build_mail_page(self):
        self._mail_filter = 'ALL'
        self.mail_candidates_visible = []
        self.mail_counts = {}
        page = make_widget('page')
        layout = hbox(page, margins=0, spacing=16)

        clients = make_frame('resultsCard')
        clients_box = vbox(clients, margins=0, spacing=0)
        chips_bar = make_frame('cardBar')
        bar = hbox(chips_bar, margins=(16, 14, 16, 14), spacing=8)
        self.mail_filter_buttons = {}
        for key, label in MAIL_FILTERS:
            chip = FilterChip(key, label)
            chip.clicked.connect(lambda _checked=False, selected=key: self.set_mail_filter(selected))
            self.mail_filter_buttons[key] = chip
            bar.addWidget(chip)
        bar.addStretch()
        self.mail_select_toggle = button('Tout sélectionner', self.toggle_mail_selection, name='textButton')
        bar.addWidget(self.mail_select_toggle)
        clients_box.addWidget(chips_bar)

        self.mail_table = QTableWidget(0, len(MAIL_COLUMNS))
        self.mail_table.setHorizontalHeaderLabels(MAIL_COLUMNS)
        self.mail_table.setObjectName('mailList')
        self.mail_table.verticalHeader().setVisible(False)
        self.mail_table.horizontalHeader().setVisible(False)
        self.mail_table.setShowGrid(False)
        self.mail_table.setFrameShape(QFrame.Shape.NoFrame)
        self.mail_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.mail_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.mail_table.setEditTriggers(QAbstractItemView.EditTrigger.EditKeyPressed)
        self.mail_table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.mail_table.verticalHeader().setDefaultSectionSize(64)
        self.mail_table.setItemDelegateForColumn(MAIL_CLIENT, ClientDelegate(self.mail_table))
        self.mail_table.setItemDelegateForColumn(MAIL_STATE, MailStateDelegate(self.mail_table))
        header = self.mail_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in ((MAIL_CHECK, 46), (MAIL_SUMMARY, 170), (MAIL_STATE, 120), (MAIL_ACTION, 256)):
            header.resizeSection(column, width)
        header.setSectionResizeMode(MAIL_CLIENT, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(MAIL_CHECK, QHeaderView.ResizeMode.Fixed)
        self.mail_table.setColumnHidden(MAIL_SIREN, True)
        self.mail_table.setColumnHidden(MAIL_EMAIL, True)
        self.mail_table.itemChanged.connect(self.mail_item_changed)
        self.mail_table.itemSelectionChanged.connect(self.preview_mail_selection)
        clients_box.addWidget(self.mail_table, 1)
        self.mail_empty = text_label('', 'muted')
        self.mail_empty.setContentsMargins(16, 12, 16, 16)
        self.mail_empty.setWordWrap(True)
        clients_box.addWidget(self.mail_empty)
        # What is chosen and the one button that opens it in Outlook stay at the bottom of the list.
        send_bar = make_frame('sendBar')
        send = hbox(send_bar, margins=(16, 12, 16, 12), spacing=12)
        self.mail_chosen = text_label('', 'sendBarTitle')
        send.addWidget(self.mail_chosen)
        self.mail_chosen_detail = text_label('Avis joints · Outlook classique', 'sendBarDetail')
        send.addWidget(self.mail_chosen_detail, 1)
        self.mail_open_button = button(
            'Envoyer les mails par Outlook', self.run_mail_header_action, name='sendBarButton'
        )
        send.addWidget(self.mail_open_button)
        clients_box.addWidget(send_bar)
        layout.addWidget(clients, 1)

        preview = make_frame('dossierSheet')
        preview.setFixedWidth(390)
        preview_box = vbox(preview, margins=0, spacing=0)
        head = make_frame('drawerHead')
        head_box = vbox(head, margins=(20, 16, 20, 16), spacing=4)
        self.mail_preview_caption = text_label('Aperçu', 'detailHeading')
        head_box.addWidget(self.mail_preview_caption)
        self.mail_preview_to = QLabel()
        self.mail_preview_to.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        head_box.addWidget(self.mail_preview_to)
        self.mail_preview_subject = text_label('', wrap=True)
        self.mail_preview_subject.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        head_box.addWidget(self.mail_preview_subject)
        self.mail_preview_files = text_label('', 'attachmentLine', wrap=True)
        self.mail_preview_files.setTextFormat(Qt.TextFormat.RichText)
        head_box.addWidget(self.mail_preview_files)
        preview_box.addWidget(head)
        self.mail_preview = text_label('', 'mailBody', wrap=True)
        self.mail_preview.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.mail_preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body = QScrollArea()
        body.setObjectName('drawerScroll')
        body.setWidgetResizable(True)
        body.setFrameShape(QFrame.Shape.NoFrame)
        body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body.setWidget(self.mail_preview)
        preview_box.addWidget(body, 1)
        note = text_label('Votre signature Outlook s’ajoute à la fin.', 'mailNote', wrap=True)
        preview_box.addWidget(note)
        layout.addWidget(preview)

        # Personalising the wording is the tab's action, in the page header.
        self.mail_template_button = button('Personnaliser le mail', self.edit_mail_template)
        self.mail_template_button.setToolTip('Changer l’objet et le texte des mails aux clients')
        self.mail_template_header_button = self.mail_template_button
        self.header_actions_layout.insertWidget(0, self.mail_template_button)
        self.mail_opener = QDesktopServices.openUrl
        self.draft_opener = open_draft
        self.mail_job = None
        self.pages.addWidget(page)
        self.set_mail_filter('ALL', refresh=False)
        self.show_mail_preview(None)

    def mail_candidates(self):
        batch = self.selected_batch()
        if batch is None:
            return []
        candidates = {}
        for campaign in self.store.batch_campaigns(batch):
            notices = {}
            for row in self.store.notices(campaign['id']):
                notices.setdefault(row['siren'], []).append(row)
            for dossier in self.store.dossiers(campaign['id']):
                item = candidates.setdefault(dossier['siren'], {'dossier': dossier, 'notices': [], 'statuses': []})
                item['statuses'].append(dossier['status'])
                item['notices'].extend(notices.get(dossier['siren'], []))
        return list(candidates.values())

    def refresh_mail_candidates(self):
        if not hasattr(self, 'mail_table'):
            return
        candidates = self.mail_candidates()
        self.mail_candidates_visible = candidates
        self.mail_states = [mail_state(candidate) for candidate in candidates]
        self.mail_table.blockSignals(True)
        self.mail_table.setRowCount(len(candidates))
        mono = QFont(load_local_fonts()['mono'])
        mono.setPixelSize(12)
        for index, (candidate, (state, label)) in enumerate(zip(candidates, self.mail_states)):
            dossier = candidate['dossier']
            choice = QTableWidgetItem()
            choice.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
                if state in MAIL_ACTIONS
                else Qt.ItemFlag.NoItemFlags
            )
            choice.setCheckState(Qt.CheckState.Unchecked)
            self.mail_table.setItem(index, MAIL_CHECK, choice)
            summary = mail_summary(candidate['notices']) if state not in ('WAITING',) else label
            values = {
                MAIL_CLIENT: dossier['name'],
                MAIL_SIREN: dossier['siren'],
                MAIL_EMAIL: dossier.get('email', ''),
                MAIL_SUMMARY: summary,
                MAIL_STATE: chip_label(state, label),
                MAIL_ACTION: '',
            }
            for column, value in values.items():
                item = QTableWidgetItem(value)
                item.setToolTip(value or ('Ajoutez l’adresse du client' if column == MAIL_EMAIL else ''))
                item.setFlags(
                    Qt.ItemFlag.ItemIsEnabled
                    | Qt.ItemFlag.ItemIsSelectable
                    | (Qt.ItemFlag.ItemIsEditable if column == MAIL_EMAIL else Qt.ItemFlag.NoItemFlags)
                )
                if column == MAIL_CLIENT:
                    item.setData(Qt.ItemDataRole.UserRole + 1, dossier['siren'])
                    item.setData(Qt.ItemDataRole.UserRole + 2, dossier.get('email', ''))
                if column == MAIL_STATE:
                    item.setData(Qt.ItemDataRole.UserRole, state)
                if column == MAIL_SUMMARY:
                    item.setForeground(QColor('#3A4258'))
                self.mail_table.setItem(index, column, item)
            previous = self.mail_table.cellWidget(index, MAIL_ACTION)
            # Hidden at once: until Qt deletes it, the old row's buttons would still paint over the new ones.
            if previous is not None:
                previous.hide()
            self.mail_table.removeCellWidget(index, MAIL_ACTION)
            text, name = MAIL_ACTIONS.get(state, (OTHER_ACTIONS.get(state, ''), ''))
            if text:
                action = button(text, lambda _checked=False, siren=dossier['siren']: self.mail_row_action(siren))
                action.setObjectName(name)
                action.setProperty('compact', True)
                action.setMinimumHeight(30)
                # Wide enough for its bold label, whatever the style resolves first.
                bold = QFont(load_local_fonts()['body'])
                bold.setPixelSize(13)
                bold.setWeight(QFont.Weight.Bold)
                action.setMinimumWidth(QFontMetrics(bold).horizontalAdvance(text) + 30)
                holder = QWidget()
                holder_box = hbox(holder, margins=(2, 0, 10, 0), spacing=6)
                holder_box.addWidget(action)
                holder.action = action
                holder.resend = None
                if state == 'OPENED':
                    # The mail was lost, closed or needs sending again: open it once more in Outlook.
                    holder.resend = button(
                        'Renvoyer', lambda _checked=False, siren=dossier['siren']: self.resend_mail(siren)
                    )
                    holder.resend.setProperty('compact', True)
                    holder.resend.setMinimumHeight(30)
                    holder.resend.setToolTip('Rouvrir ce mail dans Outlook, avec ses avis en pièce jointe')
                    holder_box.addWidget(holder.resend)
                holder_box.addStretch()
                self.mail_table.setCellWidget(index, MAIL_ACTION, holder)
        self.mail_table.blockSignals(False)
        self.mail_counts = {
            key: sum(state == key for state, _label in self.mail_states) for key, _label in MAIL_FILTERS
        }
        self.mail_counts['ALL'] = len(self.mail_states)
        for key, _label in MAIL_FILTERS:
            self.mail_filter_buttons[key].set_count(self.mail_counts[key])
        self.apply_mail_filter()
        self.preview_mail_selection()
        self.update_mail_open_button()

    def set_mail_filter(self, key, refresh=True):
        self._mail_filter = key
        for filter_key, chip in self.mail_filter_buttons.items():
            chip.setChecked(filter_key == key)
        if refresh:
            self.apply_mail_filter()

    def apply_mail_filter(self):
        states = getattr(self, 'mail_states', [])
        for row, (state, _label) in enumerate(states):
            self.mail_table.setRowHidden(row, self._mail_filter not in ('ALL', state))
        shown = sum(self._mail_filter in ('ALL', state) for state, _label in states)
        self.mail_empty.setText(
            {
                'ALL': 'Aucun client dans cette campagne pour l’instant.',
                'READY': 'Aucun mail prêt.',
                'NO_EMAIL': 'Tous les clients ont une adresse.',
                'WAITING': 'Aucun client en attente.',
                'OPENED': 'Aucun mail ouvert en attente de confirmation.',
                'SENT': 'Aucun mail marqué envoyé pour l’instant.',
            }[self._mail_filter]
        )
        self.mail_empty.setVisible(shown == 0)
        self.update_mail_open_button()
        current = self.mail_table.currentRow()
        visible = 0 <= current < len(self.mail_candidates_visible) and not self.mail_table.isRowHidden(current)
        self.show_mail_preview(self.mail_candidates_visible[current] if visible else self.default_mail_preview())

    def default_mail_preview(self):
        """Before a client is chosen, the first mail of the list that can be written is shown."""
        states = getattr(self, 'mail_states', [])
        return next(
            (
                self.mail_candidates_visible[row]
                for row in range(len(states))
                if not self.mail_table.isRowHidden(row) and states[row][0] in MAIL_ACTIONS
            ),
            None,
        )

    def _visible_checkable_rows(self):
        return [
            row
            for row in range(self.mail_table.rowCount())
            if not self.mail_table.isRowHidden(row)
            and self.mail_table.item(row, MAIL_CHECK).flags() & Qt.ItemFlag.ItemIsEnabled
        ]

    def toggle_mail_selection(self):
        rows = self._visible_checkable_rows()
        check = not rows or not all(
            self.mail_table.item(row, MAIL_CHECK).checkState() == Qt.CheckState.Checked for row in rows
        )
        self.mail_table.blockSignals(True)
        for row in rows:
            self.mail_table.item(row, MAIL_CHECK).setCheckState(
                Qt.CheckState.Checked if check else Qt.CheckState.Unchecked
            )
        self.mail_table.blockSignals(False)
        self.update_mail_open_button()

    def checked_mail_rows(self):
        return [
            row
            for row in range(self.mail_table.rowCount())
            if self.mail_table.item(row, MAIL_CHECK).checkState() == Qt.CheckState.Checked
        ]

    def checked_mail_sirens(self, state):
        return [
            self.mail_candidates_visible[row]['dossier']['siren']
            for row in self.checked_mail_rows()
            if not self.mail_table.isRowHidden(row) and self.mail_states[row][0] == state
        ]

    def update_mail_open_button(self):
        if not hasattr(self, 'mail_open_button'):
            return
        # The header button does what the shown list calls for: send the ready mails, or confirm the ones sent from Outlook.
        state = self._mail_filter if self._mail_filter in MAIL_ACTIONS else 'READY'
        checked = len(self.checked_mail_sirens(state)) if hasattr(self, 'mail_states') else 0
        plural = 's' if checked > 1 else ''
        self.mail_open_button.setText(
            {
                'READY': f'Envoyer {checked} mail{plural} par Outlook' if checked else 'Envoyer les mails par Outlook',
                'OPENED': f'Marquer {checked} mail{plural} comme envoyé{plural}'
                if checked
                else 'Marquer comme envoyés',
                'SENT': f'Marquer {checked} mail{plural} non envoyé{plural}' if checked else 'Marquer non envoyés',
            }[state]
        )
        self.mail_open_button.setEnabled(checked > 0 and self.mail_job is None)
        self.mail_chosen.setText(
            f'{checked} client' + ('s' if checked > 1 else '') + ' choisi' + ('s' if checked > 1 else '')
            if checked
            else 'Aucun client choisi'
        )
        self.mail_open_button.setToolTip('' if checked else 'Cochez les clients concernés.')
        rows = self._visible_checkable_rows()
        all_checked = bool(rows) and all(
            self.mail_table.item(row, MAIL_CHECK).checkState() == Qt.CheckState.Checked for row in rows
        )
        self.mail_select_toggle.setText('Tout désélectionner' if all_checked else 'Tout sélectionner')
        self.mail_select_toggle.setVisible(bool(rows))

    def mail_item_changed(self, item):
        if item.column() == MAIL_CHECK:
            self.update_mail_open_button()
            return
        if item.column() != MAIL_EMAIL:
            return
        address = item.text().strip()
        previous = self.mail_candidates_visible[item.row()]['dossier'].get('email', '')
        if address == previous:
            return
        if address and not valid_client_email(address):
            self.mail_table.blockSignals(True)
            item.setText(previous)
            self.mail_table.blockSignals(False)
            self.problem('Adresse e-mail invalide. Corrigez-la ou laissez le champ vide.')
            return
        batch = self.selected_batch()
        if batch is None:
            return
        siren = self.mail_table.item(item.row(), MAIL_SIREN).text()
        try:
            self.store.update_client_email(batch, siren, address)
        except ValueError as exc:
            self.mail_table.blockSignals(True)
            item.setText(previous)
            self.mail_table.blockSignals(False)
            self.problem(str(exc))
            return
        self.refresh_mail_candidates()
        self.say('Adresse du client enregistrée dans la campagne.')

    def preview_mail_selection(self):
        row = self.mail_table.currentRow()
        self.show_mail_preview(
            self.mail_candidates_visible[row]
            if 0 <= row < len(self.mail_candidates_visible)
            else self.default_mail_preview()
        )

    @staticmethod
    def notice_attachments(notices):
        return [
            Path(notice['path'])
            for notice in notices
            if notice.get('status') == 'TERMINE' and Path(notice['path']).is_file()
        ]

    def show_mail_preview(self, candidate):
        if candidate is None:
            self.mail_preview_caption.setText('Aperçu')
            self.mail_preview_to.setText('')
            self.mail_preview_subject.setText('')
            self.mail_preview_files.setText('')
            self.mail_preview.setText('Choisissez un client pour voir son courriel.')
            return
        self.mail_preview_caption.setText(f'Aperçu · {candidate["dossier"]["name"]}')
        attachments = self.notice_attachments(candidate['notices'])
        try:
            subject, body = compose_client_draft(
                candidate['dossier'], candidate['notices'], len(attachments), self.store.mail_template()
            )
        except ValueError as exc:
            self.mail_preview_to.setText(f'À : {candidate["dossier"].get("email") or "adresse à ajouter"}')
            self.mail_preview_subject.setText('')
            self.mail_preview_files.setText('')
            self.mail_preview.setText(str(exc))
            return
        self.mail_preview_to.setText(f'À : {candidate["dossier"]["email"]}')
        self.mail_preview_subject.setText(f'Objet : <b>{html.escape(subject)}</b>')
        self.mail_preview.setText(body)
        icon = f'<img src="{html.escape(asset_path("icons/attachment.svg"))}" width="14" height="14"> '
        self.mail_preview_files.setText(
            '<br>'.join(icon + html.escape(path.name) for path in attachments) if attachments else 'Aucun PDF à joindre'
        )

    def mail_template_sample(self):
        """A client whose mail can be written, to preview the wording on real figures."""
        visible = [row for row in range(len(self.mail_candidates_visible)) if not self.mail_table.isRowHidden(row)]
        current = self.mail_table.currentRow()
        for row in ([current] if current in visible else []) + visible + list(range(len(self.mail_candidates_visible))):
            candidate = self.mail_candidates_visible[row]
            if self.mail_states[row][0] in MAIL_ACTIONS:
                return {**candidate, 'attachments': len(self.notice_attachments(candidate['notices']))}
        return None

    def edit_mail_template(self):
        dialog = MailTemplateDialog(self, self.store.mail_template(), self.mail_template_sample())
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.saved is None:
            return False
        self.store.save_mail_template(None if dialog.saved == MailTemplate() else dialog.saved)
        self.toast.announce('Texte des mails enregistré.')
        self.preview_mail_selection()
        if hasattr(self, 'settings_mail_preview'):
            self.refresh_settings_page()
        return True

    def prepare_mail_application(self):
        """Wake Outlook as soon as the mails are on screen, so that the first one opens without waiting."""
        if self.draft_opener is open_draft:
            prepare_outlook()

    def close_mail_application(self):
        BRIDGE.close()

    def prepare_draft(self, candidate):
        """The client's mail, ready to send with its notices attached; nothing is sent from here."""
        if not all(status == 'TERMINE' for status in candidate['statuses']):
            raise ValueError('Collecte à terminer pour ce client.')
        dossier = candidate['dossier']
        notices = [
            row if row.get('extraction_status') else ensure_notice_details(self.store, row)
            for row in candidate['notices']
        ]
        attachments = self.notice_attachments(notices)
        template = self.store.mail_template()
        subject, body = compose_client_draft(dossier, notices, len(attachments), template)
        _subject, plain = compose_client_draft(dossier, notices, 0, template)
        return {
            'batch': self.selected_batch(),
            'siren': dossier['siren'],
            'name': dossier['name'],
            'to': dossier['email'],
            'subject': subject,
            'body': body,
            'plain': plain,
            'attachments': attachments,
        }

    def open_drafts(self, candidates, resend=False):
        """Prepare the mails here, then let Outlook show them in the background while the window stays usable."""
        if self.mail_job is not None:
            self.toast.announce('Un mail est déjà en cours d’ouverture dans Outlook.', tone='info')
            return False
        drafts = []
        for candidate in candidates:
            try:
                drafts.append({**self.prepare_draft(candidate), 'resend': resend})
            except ValueError as exc:
                self.problem(f'{candidate["dossier"]["name"]} : {exc}')
                break
        if not drafts:
            return False
        count = len(drafts)
        self.mail_fallback = None
        self.mail_job = DraftJob(self.draft_opener, drafts)
        self.mail_job.finished.connect(self.drafts_opened)
        self.toast.announce(f'Ouverture de {count} mail{"s" if count > 1 else ""} dans Outlook…', tone='info')
        self.show_mail_busy(True)
        self.mail_job.start()
        return True

    def drafts_opened(self, results):
        self.mail_job = None
        self.show_mail_busy(False)
        opened = 0
        for draft, error in results:
            if error is not None:
                if not isinstance(error, DraftUnavailable):
                    self.warn('Mail non ouvert', f'{draft["name"]} : Outlook n’a pas répondu. Réessayez.')
                    continue
                # Without a compatible mail application, the draft opens without attachments and the notices' folder beside it.
                if not self.mail_opener(QUrl(mailto_uri(draft['to'], draft['subject'], draft['plain']))):
                    self.warn(
                        'Mail à ouvrir',
                        'Windows n’a pas ouvert la messagerie associée. Vérifiez l’application par défaut.',
                    )
                    continue
                if draft['attachments']:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(str(draft['attachments'][0].parent)))
                self.mail_fallback = str(error)
            self.store.mark_mail_opened(draft['batch'], draft['siren'])
            opened += 1
            if draft.get('resend'):
                self.store.mark_mail_sent(draft['batch'], draft['siren'], False)
        self.report_opened_mails(opened)

    def show_mail_busy(self, busy):
        """While Outlook opens the mails, their buttons wait instead of opening them twice."""
        for row in range(self.mail_table.rowCount()):
            holder = self.mail_table.cellWidget(row, MAIL_ACTION)
            if holder is not None:
                holder.setEnabled(not busy)
        if hasattr(self, 'result_mail_button'):
            self.result_mail_button.setEnabled(not busy)
        self.update_mail_open_button()

    def run_mail_header_action(self):
        state = self._mail_filter if self._mail_filter in MAIL_ACTIONS else 'READY'
        if state == 'READY':
            self.open_selected_drafts()
        else:
            self.mark_mails_sent(self.checked_mail_sirens(state), state == 'OPENED')

    def mail_candidate(self, siren):
        return next((item for item in self.mail_candidates_visible if item['dossier']['siren'] == siren), None)

    def mail_row_action(self, siren):
        """The row's button: send a ready mail, confirm one opened in Outlook, or put a sent one back."""
        candidate = self.mail_candidate(siren)
        if candidate is None:
            return
        state = mail_state(candidate)[0]
        if state == 'READY':
            self.open_drafts([candidate])
        elif state == 'OPENED':
            self.mark_mails_sent([siren], True)
        elif state == 'SENT':
            self.resend_mail(siren)
        elif state == 'NO_EMAIL':
            self.add_client_email(siren)
        elif state == 'WAITING':
            self.show_mail_dossier(siren)

    def add_client_email(self, siren):
        row = next(
            (index for index, item in enumerate(self.mail_candidates_visible) if item['dossier']['siren'] == siren),
            None,
        )
        if row is None:
            return False
        name = self.mail_candidates_visible[row]['dossier']['name']
        address, accepted = QInputDialog.getText(
            self, 'Adresse du client', f'E-mail de {name}', text=self.mail_table.item(row, MAIL_EMAIL).text()
        )
        if not accepted:
            return False
        self.mail_table.item(row, MAIL_EMAIL).setText(address.strip())
        return True

    def show_mail_dossier(self, siren):
        """Open the dossier that holds the mail back, in the Dossiers tab."""
        self.navigate(2)
        self.set_result_filter('ALL', refresh=False)
        self.search.clear()
        self.filter_changed()
        row = next(
            (index for index, (_campaign, dossier) in enumerate(self.result_rows) if dossier['siren'] == siren), None
        )
        if row is not None:
            self.table.selectRow(row)

    def resend_mail(self, siren):
        """Open an already opened or sent mail again; a sent one goes back to « À confirmer » until it leaves again."""
        candidate = self.mail_candidate(siren)
        if candidate is None:
            return False
        return self.open_drafts([candidate], resend=True)

    def mark_mails_sent(self, sirens, sent):
        batch = self.selected_batch()
        if batch is None or not sirens:
            return
        for siren in sirens:
            self.store.mark_mail_sent(batch, siren, sent)
        count = len(sirens)
        self.toast.announce(
            (
                f'{count} mail{"s" if count > 1 else ""} marqué{"s" if count > 1 else ""} envoyé{"s" if count > 1 else ""}.'
            )
            if sent
            else (f'{count} mail{"s" if count > 1 else ""} remis à envoyer.')
        )
        self.refresh_after_mail_change()

    def refresh_after_mail_change(self):
        self.refresh_mail_candidates()
        self.rendered = None
        self.refresh_table()
        if self.pages.currentIndex() in (2, 3):
            self.update_campaign_header(self.pages.currentIndex())

    def open_selected_drafts(self):
        rows = [row for row in self.checked_mail_rows() if self.mail_states[row][0] == 'READY']
        selected = [self.mail_candidates_visible[row] for row in rows]
        if not selected:
            self.problem('Sélectionnez au moins un client prêt.')
            return
        if len(selected) > 20:
            self.problem('20 mails au plus à la fois.')
            return
        self.open_drafts(selected)

    def report_opened_mails(self, opened):
        if not opened:
            return
        self.status.setText(f'{opened} mail(s) ouvert(s) dans Outlook. Vérifiez chaque message avant l’envoi.')
        self.toast.announce(
            f'{opened} mail'
            + ('' if opened == 1 else 's')
            + ' ouvert'
            + ('' if opened == 1 else 's')
            + ' dans Outlook.'
            if not self.mail_fallback
            else 'Mails ouverts sans pièce jointe : joignez les avis depuis le dossier ouvert.'
        )
        if self.mail_fallback:
            self.problem(
                f'{self.mail_fallback} Les mails se sont ouverts sans pièce jointe ; les avis sont dans le dossier qui vient de s’ouvrir.'
            )
        self.refresh_after_mail_change()
        # The mails just opened wait in « À confirmer », where their button marks them sent.
        if self.pages.currentIndex() == 3:
            self.set_mail_filter('OPENED')
