from collections import Counter
from html import escape
from pathlib import Path

from PySide6.QtCore import QItemSelectionModel, QPointF, QRectF, QSize, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QWidget,
)

from .documents import change_explanation, ensure_notice_details
from .domain import safe_name, summarize_dossiers
from .notice_extraction import (
    MANUAL_METHODS,
    PAYMENT_LABELS,
    debited_account,
    format_amount,
    manual_reading,
    parse_amount,
)
from .portfolio import valid_client_email
from .situations import (
    SITUATIONS,
    TO_CHECK,
    date_key,
    dossier_reading,
    dossier_situation,
    earliest_due_date,
    mail_text,
    reading_short,
    situation_label,
)
from .ui_collect import COLLECT_PAGE
from .ui_components import FilterChip, asset_path, load_local_fonts
from .ui_kit import button, hbox, make_frame, make_widget, restyle, text_label, vbox
from .ui_style import SITUATION_COLORS
from .ui_widgets import when

LABELS = {
    'EN_ATTENTE': 'En attente',
    'EN_COURS': 'En cours',
    'TERMINE': 'Terminé',
    'ERREUR': 'À relancer',
    'SANS_HABILITATION': 'Aucune habilitation',
    'ABSENT': 'Aucun avis',
    'PAUSE': 'En pause',
    'CONNEXION_REQUISE': 'Connexion requise',
    'AVEC_ERREURS': 'À relancer',
}

# The filters above the table: what is left to handle (to retry or to check) is kept together.
FILTERS = (
    ('ALL', 'Tous'),
    ('PAY', 'À régler'),
    ('DEBIT', 'Prélevé'),
    ('NONE', 'Rien à payer'),
    ('TODO', 'À traiter'),
    ('ABSENT', 'Sans avis'),
    ('WAIT', 'En attente'),
)
FILTER_SITUATIONS = {'TODO': {'MANUAL', 'RETRY', 'REVIEW'}}
ALL_COLLABORATORS = 'Tous'

# Logical columns; the pointing box is shown first.
COLUMNS = ['Dossier', 'Pointé', 'Collaborateur', 'Année', 'À payer', 'Situation', 'Échéance', 'Compte prélevé', 'Mail']
COL_NAME, COL_POINT, COL_COLLAB, COL_YEAR, COL_DUE, COL_SITUATION, COL_DATE, COL_ACCOUNT, COL_MAIL = range(len(COLUMNS))
VISUAL_ORDER = [COL_POINT, COL_NAME, COL_COLLAB, COL_YEAR, COL_DUE, COL_SITUATION, COL_DATE, COL_ACCOUNT, COL_MAIL]
COLUMN_WIDTHS = {
    COL_POINT: 64,
    COL_COLLAB: 110,
    COL_YEAR: 64,
    COL_DUE: 104,
    COL_SITUATION: 200,
    COL_DATE: 96,
    COL_ACCOUNT: 250,
    COL_MAIL: 116,
}

KIND_LABELS = {'ACOMPTE': 'Acompte', 'SOLDE': 'Solde', 'SUPPLEMENTAIRE': 'Rôle supplémentaire'}
READING_TONES = {
    'Lue': 'success',
    'Classée à la main': 'info',
    TO_CHECK: 'warning',
    'Non lue': 'warning',
    'À relancer': 'warning',
}
SHEET_WIDTH = 380


def _amount_text(cents):
    return '' if cents is None else format_amount(cents).removesuffix(' €')


def _siren_text(siren):
    return f'{siren[:3]} {siren[3:6]} {siren[6:]}' if len(siren) == 9 else siren


class ManualReadingForm(QFrame):
    """Lets a collaborator copy the amounts and payment mode printed on a notice that could not be read with certainty."""

    def __init__(self, notice, parent=None):
        super().__init__(parent)
        self.setObjectName('manualReadingForm')
        layout = vbox(self, margins=(0, 6, 0, 0), spacing=6)
        note = QLabel('Recopiez les montants imprimés sur l’avis.')
        note.setWordWrap(True)
        note.setObjectName('muted')
        layout.addWidget(note)
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.method_input = QComboBox()
        for method in MANUAL_METHODS:
            self.method_input.addItem(PAYMENT_LABELS[method], method)
        current = self.method_input.findData(notice.get('payment_method'))
        self.method_input.setCurrentIndex(max(0, current))
        self.tax_input = QLineEdit(_amount_text(notice.get('tax_amount_cents')))
        self.tax_input.setPlaceholderText('ex. 1 284,00')
        self.due_input = QLineEdit(_amount_text(notice.get('amount_due_cents')))
        self.due_input.setPlaceholderText('ex. 1 284,00')
        form.addRow('Mode', self.method_input)
        form.addRow('Montant de l’avis (€)', self.tax_input)
        form.addRow('À payer (€)', self.due_input)
        layout.addLayout(form)
        self.error_label = text_label('', 'formError', wrap=True)
        self.error_label.hide()
        layout.addWidget(self.error_label)
        actions = QHBoxLayout()
        actions.addStretch()
        self.cancel_button = QPushButton('Annuler')
        actions.addWidget(self.cancel_button)
        self.save_button = QPushButton('Enregistrer')
        self.save_button.setObjectName('primary')
        actions.addWidget(self.save_button)
        layout.addLayout(actions)
        self.cancel_button.clicked.connect(self.hide)

    def values(self):
        return self.method_input.currentData(), parse_amount(self.tax_input.text()), parse_amount(self.due_input.text())

    def show_error(self, text):
        self.error_label.setText(text)
        self.error_label.show()


class NoticePanel(QFrame):
    """One notice of the selected dossier: what it asks to pay, how it was read and what can be done with it."""

    def __init__(self, ordinal, notice, workspace):
        super().__init__()
        self.setObjectName('noticeCard')
        self.ordinal = ordinal
        self.notice = notice
        box = vbox(self, margins=14, spacing=6)
        head = hbox(spacing=8)
        kind = notice.get('kind') or ''
        title = text_label(f'{KIND_LABELS.get(kind, kind.title())} {notice["year"]}', 'noticeTitle')
        head.addWidget(title, 1)
        finished = notice['status'] == 'TERMINE'
        reading = reading_short(notice) if finished else LABELS.get(notice['status'], notice['status'])
        reading_text = {'Lue': 'Lu sur l’avis', 'Partielle': 'Lu en partie', 'Non lue': 'Pas encore lu'}.get(
            reading, reading
        )
        self.reading_label = text_label(reading_text, 'readingText')
        self.reading_label.setProperty('tone', READING_TONES.get(reading, 'neutral'))
        head.addWidget(self.reading_label)
        box.addLayout(head)
        if not finished:
            self._add_line(box, notice.get('error') or 'Avis non classé.', 'muted')
        elif notice.get('extraction_status'):
            due, tax = notice.get('amount_due_cents'), notice.get('tax_amount_cents')
            self._add_line(box, format_amount(due) if due is not None else 'Montant non lu', 'noticeDue')
            mode = PAYMENT_LABELS.get(notice.get('payment_method'), PAYMENT_LABELS['NON_INDIQUE'])
            self._add_line(box, mode + (f' · échéance {notice["due_date"]}' if notice.get('due_date') else ''))
            if tax is not None and tax != due:
                self._add_line(box, f'Montant de l’avis : {format_amount(tax)}', 'muted')
            if notice.get('refund_cents'):
                self._add_line(box, f'Remboursement annoncé : {format_amount(notice["refund_cents"])}')
            if notice.get('bank_account_display'):
                self._add_line(box, f'Compte : {notice["bank_account_display"]}', 'muted')
        else:
            self._add_line(box, 'Montants pas encore lus sur le PDF.', 'muted')
        place = f'Réf. {notice["reference"]} · établissement {notice["nic"]}' + (
            f' · {notice["commune"]}' if notice.get('commune') else ''
        )
        self._add_line(box, place, 'mono')
        evolution = change_explanation(workspace.store, notice) if finished else []
        if evolution:
            rule = make_frame('noticeRule')
            box.addWidget(rule)
            self._add_line(box, evolution[0], 'evolutionSummary')
            for reason in evolution[1:]:
                self._add_line(box, '• ' + reason.lstrip('•- '), 'evolutionReason')
        actions = hbox(margins=(0, 4, 0, 0), spacing=12)
        self.open_button = QPushButton('Ouvrir le PDF')
        self.open_button.setObjectName('linkStrong')
        self.open_button.clicked.connect(lambda: workspace.open_notice_pdf(notice['path']))
        actions.addWidget(self.open_button)
        self.read_button = self.classify_button = None
        if finished and not notice.get('extraction_status'):
            self.read_button = QPushButton('Lire l’avis')
            self.read_button.setObjectName('textButton')
            self.read_button.clicked.connect(lambda: workspace.read_notice_now(self))
            actions.addWidget(self.read_button)
        if finished and notice.get('extraction_status'):
            text = 'Classer à la main' if notice['extraction_status'] == 'A_VERIFIER' else 'Corriger la lecture'
            self.classify_button = QPushButton(text)
            self.classify_button.setObjectName('linkMuted')
            actions.addWidget(self.classify_button)
        actions.addStretch()
        box.addLayout(actions)
        self.form = ManualReadingForm(notice)
        self.form.hide()
        box.addWidget(self.form)
        if self.classify_button is not None:
            self.classify_button.clicked.connect(lambda: self.form.setVisible(not self.form.isVisible()))
        self.form.save_button.clicked.connect(lambda: workspace.save_manual_reading(self))

    @staticmethod
    def _add_line(box, text, name=None):
        label = text_label(text, wrap=True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if name:
            label.setObjectName(name)
        box.addWidget(label)
        return label


class _CellDelegate(QStyledItemDelegate):
    def paint_background(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ''
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)


class DossierDelegate(_CellDelegate):
    """The dossier's name, with its SIREN underneath in the mono font."""

    def paint(self, painter, option, index):
        self.paint_background(painter, option, index)
        fonts = load_local_fonts()
        rect = option.rect.adjusted(10, 7, -8, -7)
        painter.save()
        name_font = QFont(fonts['body'])
        name_font.setPixelSize(13)
        name_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(name_font)
        painter.setPen(QColor('#131A2C'))
        name = painter.fontMetrics().elidedText(str(index.data() or ''), Qt.TextElideMode.ElideRight, rect.width())
        painter.drawText(
            QRectF(rect.x(), rect.y(), rect.width(), rect.height() / 2 + 2),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            name,
        )
        mono = QFont(fonts['mono'])
        mono.setPixelSize(11)
        painter.setFont(mono)
        painter.setPen(QColor('#767E96'))
        painter.drawText(
            QRectF(rect.x(), rect.y() + rect.height() / 2, rect.width(), rect.height() / 2),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            _siren_text(str(index.data(Qt.ItemDataRole.UserRole + 1) or '')),
        )
        painter.restore()


class PointDelegate(_CellDelegate):
    """The pointing box: filled once the collaborator has handled the dossier."""

    def paint(self, painter, option, index):
        self.paint_background(painter, option, index)
        pointed, eligible = index.data(Qt.ItemDataRole.UserRole) or (False, False)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(option.rect.x() + 18, option.rect.center().y() - 11, 22, 22)
        if pointed:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor('#2F45D8'))
            painter.drawRoundedRect(box, 6, 6)
            pen = QPen(QColor('#FFFFFF'), 2.6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            origin = box.topLeft()
            painter.drawPolyline([origin + QPointF(6, 11.5), origin + QPointF(9.5, 15), origin + QPointF(16, 7.5)])
        else:
            painter.setBrush(QColor('#FFFFFF'))
            painter.setPen(QPen(QColor('#CDD3E0' if eligible else '#E9ECF3'), 1.5))
            painter.drawRoundedRect(box, 6, 6)
        painter.restore()


class ChipDelegate(_CellDelegate):
    """The dossier's situation as a coloured chip."""

    def paint(self, painter, option, index):
        self.paint_background(painter, option, index)
        label = str(index.data() or '')
        if not label:
            return
        background, foreground = SITUATION_COLORS.get(
            index.data(Qt.ItemDataRole.UserRole) or 'WAIT', SITUATION_COLORS['WAIT']
        )
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = QFont(load_local_fonts()['body'])
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        width = min(option.rect.width() - 16, painter.fontMetrics().horizontalAdvance(label) + 32)
        chip = QRectF(option.rect.x() + 8, option.rect.center().y() - 12, width, 24)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(background))
        painter.drawRoundedRect(chip, 12, 12)
        painter.setBrush(QColor(foreground))
        painter.drawEllipse(QRectF(chip.x() + 10, chip.center().y() - 3, 6, 6))
        painter.setPen(QColor(foreground))
        painter.drawText(chip.adjusted(22, 0, -8, 0), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
        painter.restore()


class ResultsWorkspaceMixin:
    """The campaign screen: collection status, situation tiles, the dossier table and the dossier sheet."""

    def build_results_page(self):
        page = make_widget('page')
        self.results_page = page
        layout = hbox(page, margins=0, spacing=16)
        self.result_rows = []
        self.error_count = 0
        self.pending_count = 0
        self._sort = None

        # Campaign actions sit in the page header, before the launch button.
        self.export_button = button('Export Excel', self.export, name='headerAccent')
        self.export_button.setIcon(QIcon(asset_path('icons/action-excel.svg')))
        self.export_button.setIconSize(QSize(18, 18))
        self.export_button.setToolTip('Un classeur par collaborateur, toutes années')
        self.pause_button = button('Mettre en pause', self.pause_collection)
        self.retry_button = button('Relancer les erreurs', lambda: self.start_collection(True), name='primary')
        position = self.header_actions_layout.indexOf(self.login_button)
        # The way to the next step of the campaign: from the collection to the dossiers, or back to collect.
        self.next_step_button = button('Voir les dossiers', lambda: self.navigate(self.next_step_page))
        self.next_step_page = 2
        # The same « ⋯ » menu as on the campaign cards: rename, open the output folder, delete.
        self.campaign_menu_button = QToolButton()
        self.campaign_menu_button.setObjectName('cardMenu')
        self.campaign_menu_button.setIcon(QIcon(asset_path('icons/more.svg')))
        self.campaign_menu_button.setIconSize(QSize(16, 16))
        self.campaign_menu_button.setToolTip('Renommer, ouvrir le dossier de sortie, supprimer')
        self.campaign_menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.campaign_menu_button)
        self.campaign_archive_action = menu.addAction('Archiver dans un Excel', self.toggle_selected_archive)
        menu.addAction('Renommer', lambda: self.rename_campaign(self.selected_batch()))
        menu.addAction('Ouvrir le dossier de sortie', lambda: self.open_batch_output(self.selected_batch()))
        menu.addSeparator()
        menu.addAction('Supprimer la campagne', lambda: self.delete_campaign(self.selected_batch()))
        self.campaign_menu_button.setMenu(menu)
        for offset, widget in enumerate(
            (self.export_button, self.pause_button, self.retry_button, self.next_step_button)
        ):
            self.header_actions_layout.insertWidget(position + offset, widget)

        self.results_card = make_frame('resultsCard')
        table_box = vbox(self.results_card, margins=0, spacing=0)
        chips_bar = make_frame('cardBar')
        chips = hbox(chips_bar, margins=(16, 14, 16, 14), spacing=8)
        self.result_filter_buttons = {}
        for key, label in FILTERS:
            chip = FilterChip(key, label)
            chip.clicked.connect(lambda _checked=False, selected=key: self.set_result_filter(selected))
            self.result_filter_buttons[key] = chip
            chips.addWidget(chip)
        chips.addStretch()
        table_box.addWidget(chips_bar)
        filters = hbox(margins=(16, 12, 16, 12), spacing=10)
        self.search = QLineEdit()
        self.search.setObjectName('searchField')
        self.search.setPlaceholderText('Nom, SIREN ou collaborateur')
        self.search.addAction(QIcon(asset_path('icons/search.svg')), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setToolTip('Rechercher un dossier (Ctrl+F)')
        self.search.setMinimumWidth(0)
        filters.addWidget(self.search, 1)
        self.collaborator_box = QWidget()
        collaborator_row = hbox(self.collaborator_box, margins=0, spacing=8)
        collaborator_label = text_label('Collaborateur', 'muted')
        collaborator_row.addWidget(collaborator_label)
        self.collaborator_filter = QComboBox()
        self.collaborator_filter.addItem(ALL_COLLABORATORS)
        self.collaborator_filter.setMinimumWidth(130)
        collaborator_row.addWidget(self.collaborator_filter)
        filters.addWidget(self.collaborator_box)
        self.year_filter = QComboBox()
        self.year_filter.setMaxVisibleItems(12)
        self.year_filter.addItem('Toutes les années')
        self.year_filter.setFixedWidth(170)
        filters.addWidget(self.year_filter)
        self.unpointed_filter = QCheckBox('Non pointés')
        self.unpointed_filter.setObjectName('plainCheck')
        filters.addWidget(self.unpointed_filter)
        self.retry_selected_button = button('Relancer la sélection + captures', self.start_selected_retry)
        self.retry_selected_button.setToolTip('Relancer les dossiers sélectionnés en enregistrant des captures')
        filters.addWidget(self.retry_selected_button)
        table_box.addLayout(filters)
        self.search.textChanged.connect(self.filter_changed)
        self.year_filter.currentIndexChanged.connect(self.filter_changed)
        self.collaborator_filter.currentIndexChanged.connect(self.filter_changed)
        self.unpointed_filter.toggled.connect(self.filter_changed)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setFrameShape(QFrame.Shape.NoFrame)
        self.table.setMouseTracking(True)
        self.table.setItemDelegateForColumn(COL_NAME, DossierDelegate(self.table))
        self.table.setItemDelegateForColumn(COL_POINT, PointDelegate(self.table))
        self.table.setItemDelegateForColumn(COL_SITUATION, ChipDelegate(self.table))
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.table.verticalHeader().setDefaultSectionSize(52)
        header = self.table.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in COLUMN_WIDTHS.items():
            header.resizeSection(column, width)
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(COL_POINT, QHeaderView.ResizeMode.Fixed)
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(48)
        for target, logical in enumerate(VISUAL_ORDER):
            current = header.visualIndex(logical)
            if current != target:
                header.moveSection(current, target)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(False)
        header.sectionClicked.connect(self.sort_results)
        self.table.itemDoubleClicked.connect(lambda _item: self.open_first_notice())
        self.table.cellClicked.connect(self.point_clicked_cell)
        self.table.itemSelectionChanged.connect(self.result_selection_changed)
        table_box.addWidget(self.table, 1)
        pagination_frame = make_frame('cardFoot')
        pagination = hbox(pagination_frame, margins=(16, 10, 16, 10))
        self.summary = text_label('', 'muted')
        pagination.addWidget(self.summary, 1)
        self.prev_button = button('Précédent', lambda: self.change_page(-1))
        self.next_button = button('Suivant', lambda: self.change_page(1))
        pagination.addWidget(self.prev_button)
        pagination.addWidget(self.next_button)
        table_box.addWidget(pagination_frame)
        self.results_card.setMinimumHeight(320)
        layout.addWidget(self.results_card, 1)
        self.build_result_detail()
        layout.addWidget(self.result_detail_card)

        self.results_scroll = QScrollArea()
        self.results_scroll.setObjectName('pageScroll')
        self.results_scroll.setWidgetResizable(True)
        self.results_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.results_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.results_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results_scroll.setWidget(page)
        self.pages.addWidget(self.results_scroll)
        self.build_result_shortcuts()
        self._current_result_filter = 'ALL'
        self._detail_row = None
        self.set_result_filter('ALL', refresh=False)
        self.reset_result_detail()

    def build_result_detail(self):
        """The dossier sheet: a column beside the table while a dossier is selected."""
        self.result_detail_card = make_frame('dossierSheet')
        self.result_detail_card.setFixedWidth(SHEET_WIDTH)
        drawer = vbox(self.result_detail_card, margins=0, spacing=0)
        self.result_detail_key = None
        self._detail_notices_signature = None
        self.detail_notice_panels = []

        head = make_frame('drawerHead')
        head_box = vbox(head, margins=(20, 18, 20, 14), spacing=6)
        title_row = hbox(spacing=8)
        self.result_detail_name = text_label('', 'resultDetailName', wrap=True)
        title_row.addWidget(self.result_detail_name, 1, Qt.AlignmentFlag.AlignTop)
        # An icon, not the ✕ character: some Windows fonts draw it as a thin bar.
        self.drawer_close_button = QPushButton()
        self.drawer_close_button.setObjectName('drawerClose')
        self.drawer_close_button.setToolTip('Fermer la fiche (Échap)')
        self.drawer_close_button.setIcon(QIcon(asset_path('icons/close.svg')))
        self.drawer_close_button.setIconSize(QSize(14, 14))
        self.drawer_close_button.setAccessibleName('Fermer la fiche')
        self.drawer_close_button.clicked.connect(self.close_dossier_drawer)
        title_row.addWidget(self.drawer_close_button, 0, Qt.AlignmentFlag.AlignTop)
        head_box.addLayout(title_row)
        self.result_detail_meta = text_label('', 'muted', wrap=True)
        self.result_detail_meta.setTextFormat(Qt.TextFormat.RichText)
        self.result_detail_meta.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        head_box.addWidget(self.result_detail_meta)
        self.result_detail_status = text_label('', 'situationChip')
        head_box.addWidget(self.result_detail_status, 0, Qt.AlignmentFlag.AlignLeft)
        self.result_detail_pointed = text_label('', 'muted')
        head_box.addWidget(self.result_detail_pointed)
        drawer.addWidget(head)

        body = make_widget('drawerBody')
        body_box = vbox(body, margins=(20, 16, 20, 16), spacing=12)
        self.result_detail_error = QPlainTextEdit()
        self.result_detail_error.setObjectName('resultDetailError')
        self.result_detail_error.setReadOnly(True)
        body_box.addWidget(self.result_detail_error)
        self.result_notices_heading = text_label('', 'detailHeading')
        body_box.addWidget(self.result_notices_heading)
        self.result_notices_box = vbox(spacing=10)
        body_box.addLayout(self.result_notices_box)
        self.result_notices_empty = text_label('', 'muted', wrap=True)
        body_box.addWidget(self.result_notices_empty)
        self.result_email_label = text_label('', 'muted', wrap=True)
        self.result_email_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body_box.addWidget(self.result_email_label)
        body_box.addStretch()
        scroll = QScrollArea()
        scroll.setObjectName('drawerScroll')
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(body)
        drawer.addWidget(scroll, 1)

        foot = make_frame('drawerFoot')
        foot_box = vbox(foot, margins=(20, 14, 20, 18), spacing=8)
        # After two failed attempts, the collaborator checks the dossier on the portal and says so here.
        self.result_verified_check = QCheckBox('Vérifié manuellement sur impots.gouv.fr')
        self.result_verified_check.setObjectName('plainCheck')
        self.result_verified_check.toggled.connect(self.set_verified_from_sheet)
        foot_box.addWidget(self.result_verified_check)
        self.result_point_button = button('Pointer ce dossier', self.toggle_point_selected, name='primary')
        foot_box.addWidget(self.result_point_button)
        self.result_retry_button = button('Relancer ce dossier + capture', self.retry_selected_result, name='primary')
        foot_box.addWidget(self.result_retry_button)
        self.result_retry_hint = text_label('Connectez-vous d’abord, dans l’onglet Collecte.', 'runHint')
        self.result_retry_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        foot_box.addWidget(self.result_retry_hint)
        self.result_connections_button = button('Vérifier les comptes experts', lambda: self.navigate(1))
        foot_box.addWidget(self.result_connections_button)
        secondary = hbox(spacing=8)
        self.result_mail_button = button('Envoyer le mail', self.prepare_client_mail, name='accent')
        secondary.addWidget(self.result_mail_button, 1)
        self.result_folder_button = button('Ouvrir le dossier', self.open_dossier_folder)
        secondary.addWidget(self.result_folder_button, 1)
        foot_box.addLayout(secondary)
        self.result_shortcuts_hint = text_label(
            '↑ ↓ dossier suivant · P pointer · Entrée ouvrir l’avis', 'shortcutHint'
        )
        self.result_shortcuts_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        foot_box.addWidget(self.result_shortcuts_hint)
        drawer.addWidget(foot)
        self.result_detail_card.hide()

    def build_result_shortcuts(self):
        for keys, slot in (
            (QKeySequence('P'), self.point_from_keyboard),
            (QKeySequence(Qt.Key.Key_Return), self.open_first_notice),
            (QKeySequence(Qt.Key.Key_Enter), self.open_first_notice),
        ):
            shortcut = QShortcut(keys, self.table)
            shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
            shortcut.activated.connect(slot)
        find = QShortcut(QKeySequence.StandardKey.Find, self.results_scroll)
        find.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        find.activated.connect(lambda: (self.search.setFocus(), self.search.selectAll()))
        for parent in (self.results_scroll, self.result_detail_card):
            close = QShortcut(QKeySequence(Qt.Key.Key_Escape), parent)
            close.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            close.activated.connect(self.close_dossier_drawer)

    def place_dossier_drawer(self):
        self.result_detail_card.setVisible(self.result_detail_key is not None)

    def close_dossier_drawer(self):
        self.table.clearSelection()
        self.reset_result_detail()

    def sort_results(self, column):
        if column == COL_POINT:
            return
        if self._sort and self._sort[0] == column:
            self._sort = (column, not self._sort[1])
        else:
            self._sort = (column, False)
        header = self.table.horizontalHeader()
        header.setSortIndicatorShown(True)
        header.setSortIndicator(column, Qt.SortOrder.DescendingOrder if self._sort[1] else Qt.SortOrder.AscendingOrder)
        self.rendered = None
        self.refresh_table()

    def set_result_filter(self, key, refresh=True):
        self._current_result_filter = key
        for filter_key, tile in self.result_filter_buttons.items():
            tile.setChecked(filter_key == key)
        if refresh:
            self.filter_changed()

    def filter_changed(self, *_):
        self.page_number = 0
        self.rendered = None
        self.refresh_table()

    def change_page(self, delta):
        self.page_number = max(0, self.page_number + delta)
        self.rendered = None
        self.refresh_table()

    def refresh_collaborator_filter(self):
        batch = self.selected_batch()
        current = self.collaborator_filter.currentText()
        names = sorted(
            {
                d['collaborator']
                for c in (self.store.batch_campaigns(batch) if batch is not None else [])
                for d in self.store.dossiers(c['id'])
                if d['collaborator']
            },
            key=str.casefold,
        )
        self.collaborator_filter.blockSignals(True)
        self.collaborator_filter.clear()
        self.collaborator_filter.addItem(ALL_COLLABORATORS)
        self.collaborator_filter.addItems(names)
        self.collaborator_filter.setCurrentText(current if current in names else ALL_COLLABORATORS)
        self.collaborator_box.setVisible(len(names) > 1)
        self.collaborator_filter.blockSignals(False)

    def _identity_from_selection(self):
        indexes = self.table.selectionModel().selectedRows()
        if not indexes:
            return None
        row = indexes[0].row()
        if row >= len(self.result_rows):
            return None
        campaign, dossier = self.result_rows[row]
        return campaign['id'], dossier['siren']

    def _identities_from_selection(self):
        return {
            (self.result_rows[index.row()][0]['id'], self.result_rows[index.row()][1]['siren'])
            for index in self.table.selectionModel().selectedRows()
            if index.row() < len(self.result_rows)
        }

    def _row_values(self, campaign, dossier, notices):
        due, _mode, _reading = dossier_reading(notices)
        situation = dossier_situation(dossier, notices)
        values = [
            dossier['name'],
            '',
            dossier['collaborator'],
            str(campaign['year']),
            format_amount(due) if notices and due is not None else ('—' if notices else ''),
            situation_label(dossier, situation),
            earliest_due_date(notices),
            debited_account(notices),
            mail_text(dossier, situation),
        ]
        return values, due, situation

    def _sort_key(self, column, campaign, dossier, values, due, situation):
        if column == COL_DUE:
            return (due is None, due or 0)
        if column == COL_YEAR:
            return campaign['year']
        if column == COL_SITUATION:
            return [key for key, _label in SITUATIONS].index(situation)
        if column == COL_DATE:
            return (not values[COL_DATE], date_key(values[COL_DATE]))
        return values[column].casefold()

    def refresh_table(self):
        previous_selection = self._identities_from_selection()
        previous_key = self.result_detail_key or self._identity_from_selection()
        batch = self.selected_batch()
        all_rows = []
        notices_by = {}
        annual = self.store.batch_campaigns(batch) if batch is not None else []
        for campaign in annual:
            for notice in self.store.notices(campaign['id']):
                if notice['status'] == 'TERMINE':
                    notices_by.setdefault((campaign['id'], notice['siren']), []).append(notice)
            for dossier in self.store.dossiers(campaign['id']):
                all_rows.append((campaign, dossier))
        state_counts = summarize_dossiers(dossier for _, dossier in all_rows)
        complete = sum(d['status'] in ('TERMINE', 'ABSENT', 'ERREUR', 'SANS_HABILITATION') for _, d in all_rows)
        self.progress.setRange(0, max(1, len(all_rows)))
        self.progress.setValue(complete)
        restyle(self.progress, hasErrors=self.has_errors)
        self.year_filter.setVisible(self.year_filter.count() > 2)
        self.table.setColumnHidden(COL_YEAR, len(annual) < 2)

        situations = {
            (c['id'], d['siren']): dossier_situation(d, notices_by.get((c['id'], d['siren']), [])) for c, d in all_rows
        }
        self.pending_count = sum(situation == 'WAIT' for situation in situations.values())
        handle = sum(situation in ('MANUAL', 'RETRY', 'REVIEW') for situation in situations.values())
        # The errors to relaunch leave out the dossiers that already failed twice: those wait for a manual check.
        self.error_count = sum(
            situations[(c['id'], d['siren'])] == 'RETRY' and d['status'] == 'ERREUR' for c, d in all_rows
        )
        self.has_errors = self.error_count > 0
        if batch is not None:
            self.update_campaign_badge(
                batch, len({d['siren'] for _, d in all_rows}), handle, self.pending_count, len(all_rows)
            )
        self.collect_stats = {
            'dossiers': len({d['siren'] for _, d in all_rows}),
            'total': len(all_rows),
            'finished': state_counts['finished'],
            'done': complete,
            'notices': sum(len(rows) for rows in notices_by.values()),
            'absent': state_counts['absent'],
            'retry': sum(situation == 'RETRY' for situation in situations.values()),
            'manual': sum(situation == 'MANUAL' for situation in situations.values()),
            'pending': state_counts['pending'],
            'checked': max((d.get('checked') or '' for _c, d in all_rows), default=''),
        }

        year = self.year_filter.currentText()
        collaborator = self.collaborator_filter.currentText()
        scoped = [
            (c, d)
            for c, d in all_rows
            if (year == 'Toutes les années' or str(c['year']) == year)
            and (collaborator == ALL_COLLABORATORS or d['collaborator'] == collaborator)
        ]
        scoped_counts = Counter(situations[(c['id'], d['siren'])] for c, d in scoped)
        for key, tile in self.result_filter_buttons.items():
            count = (
                len(scoped)
                if key == 'ALL'
                else sum(scoped_counts[situation] for situation in FILTER_SITUATIONS.get(key, {key}))
            )
            tile.set_count(count)
            tile.setVisible(key != 'WAIT' or count > 0)
        if self._current_result_filter == 'WAIT' and not self.result_filter_buttons['WAIT'].count:
            self.set_result_filter('ALL', refresh=False)

        needle = self.search.text().strip().casefold()
        wanted = self._current_result_filter
        unpointed = self.unpointed_filter.isChecked()
        filtered = []
        for campaign, dossier in scoped:
            identity = (campaign['id'], dossier['siren'])
            if (
                needle
                and needle not in ' '.join([dossier['name'], dossier['siren'], dossier['collaborator']]).casefold()
            ):
                continue
            if wanted != 'ALL' and situations[identity] not in FILTER_SITUATIONS.get(wanted, {wanted}):
                continue
            if unpointed and dossier.get('pointed_at'):
                continue
            row_values, due, situation = self._row_values(campaign, dossier, notices_by.get(identity, []))
            filtered.append((campaign, dossier, row_values, due, situation))
        if self._sort:
            column, descending = self._sort
            filtered.sort(key=lambda entry: self._sort_key(column, *entry), reverse=descending)
        self.page_number = min(self.page_number, max(0, (len(filtered) - 1) // 250))
        start = self.page_number * 250
        page_rows = filtered[start : start + 250]
        rows = [(campaign, dossier) for campaign, dossier, *_rest in page_rows]
        signature = [
            (c['id'], d['siren'], d['error'], d.get('pointed_at'), d['status'], *row_values)
            for c, d, row_values, _due, _situation in page_rows
        ]
        self.result_rows = rows
        if signature != self.rendered:
            row_by_identity = {(c['id'], d['siren']): i for i, (c, d) in enumerate(rows)}
            restore_rows = [row_by_identity[key] for key in previous_selection if key in row_by_identity]
            mono = QFont(load_local_fonts()['mono'])
            mono.setPixelSize(12)
            self.table.blockSignals(True)
            self.table.setUpdatesEnabled(False)
            self.table.clearSelection()
            self.table.setRowCount(len(rows))
            for index, (campaign, dossier, row_values, _due, situation) in enumerate(page_rows):
                for column, text in enumerate(row_values):
                    item = QTableWidgetItem(text)
                    if column == COL_NAME:
                        item.setData(Qt.ItemDataRole.UserRole, campaign['id'])
                        item.setData(Qt.ItemDataRole.UserRole + 1, dossier['siren'])
                        item.setToolTip(
                            f'{dossier["name"]} · SIREN {dossier["siren"]}'
                            + (f'\n{dossier["error"]}' if dossier['error'] else '')
                        )
                    elif column == COL_POINT:
                        pointed = bool(dossier.get('pointed_at'))
                        stamp = dossier.get('pointed_at') or ''
                        item.setData(Qt.ItemDataRole.UserRole, (pointed, dossier['status'] == 'TERMINE'))
                        item.setToolTip(
                            f'Pointé le {when(stamp)}'
                            if pointed
                            else 'Pointer ce dossier'
                            if dossier['status'] == 'TERMINE'
                            else 'Pointage possible une fois les avis classés'
                        )
                    elif column == COL_SITUATION:
                        item.setData(Qt.ItemDataRole.UserRole, situation)
                    elif column == COL_MAIL:
                        item.setForeground(QColor('#59627A'))
                    elif column in (COL_DUE, COL_DATE, COL_ACCOUNT):
                        item.setFont(mono)
                        if column == COL_DUE:
                            item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    if not item.toolTip():
                        item.setToolTip(text)
                    self.table.setItem(index, column, item)
            self.table.setUpdatesEnabled(True)
            self.table.blockSignals(False)
            self.rendered = signature
            selection_model = self.table.selectionModel()
            selection_model.blockSignals(True)
            for row in restore_rows:
                selection_model.select(
                    self.table.model().index(row, 0),
                    QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
                )
            selection_model.blockSignals(False)
        visible_identities = {(c['id'], d['siren']) for c, d in rows}
        detail_key = (
            previous_key
            if previous_key in visible_identities
            else next(((c['id'], d['siren']) for c, d in rows if (c['id'], d['siren']) in previous_selection), None)
        )
        if detail_key is None:
            self.reset_result_detail()
        else:
            self.show_result_detail(detail_key)
        self.prev_button.setEnabled(self.page_number > 0)
        self.next_button.setEnabled(start + 250 < len(filtered))
        pointed = sum(bool(dossier.get('pointed_at')) for _c, dossier, *_rest in filtered)
        paging = f' · {start + 1}–{start + len(rows)}' if len(filtered) > 250 else ''
        self.summary.setText(
            f'{len(filtered)} dossier'
            + ('s' if len(filtered) > 1 else '')
            + (' / année' if len(annual) > 1 else '')
            + f' · {pointed} pointé'
            + ('s' if pointed > 1 else '')
            + paging
        )
        self.prev_button.setVisible(len(filtered) > 250)
        self.next_button.setVisible(len(filtered) > 250)
        self.update_point_button()
        self.update_buttons()
        self.update_campaign_tabs()

    def update_header_actions(self):
        """One main action per tab, shown only when it applies, with why it cannot run yet."""
        index = self.pages.currentIndex()
        has = self.selected_batch() is not None
        dossiers = index == 2 and has
        collect = index == COLLECT_PAGE and has
        mails = index == 3 and has
        can_run = self.run_button.isEnabled()
        blocked = 'Connectez-vous dans l’onglet Collecte pour relancer.'
        errors = self.error_count
        self.open_button.setVisible(False)
        self.new_campaign_button.setVisible(index == 0 and self.campaign_flow.currentWidget() is self.campaign_library)
        if hasattr(self, 'mail_template_header_button'):
            self.mail_template_header_button.setVisible(mails)
        stats = self.collect_stats or {}
        total, done, pending = stats.get('total', 0), stats.get('done', 0), stats.get('pending', 0)
        work_left = pending > 0 or errors > 0
        not_collected = dossiers and not self.busy and total > 0 and pending == total
        self.export_button.setVisible(dossiers and not not_collected)
        self.export_button.setEnabled(not self.busy)
        self.retry_button.setText('Relancer le dossier en erreur' if errors == 1 else f'Relancer les {errors} erreurs')
        self.retry_button.setVisible(dossiers and not self.busy and errors > 0)
        self.retry_button.setEnabled(can_run)
        self.retry_button.setToolTip('' if can_run else blocked)
        self.pause_button.setVisible(collect and self.busy)
        count = self.store.batch(self.selected_batch())['sessions'] if has else 0
        connected = self.browser_ready and all(self.connected_sessions.get(i, False) for i in range(count))
        self.login_button.setVisible(collect and not self.busy and not connected and (work_left or not total))
        self.run_button.setVisible(collect and not self.busy and connected and (work_left or not total))
        # Once collected, the Collecte tab leads to the dossiers; a campaign not collected yet leads back to its collection.
        to_dossiers = collect and not self.busy and done > 0
        self.next_step_page = COLLECT_PAGE if not_collected else 2
        self.next_step_button.setText('Lancer la collecte' if not_collected else 'Voir les dossiers')
        self.next_step_button.setVisible(to_dossiers or not_collected)
        self.next_step_button.setObjectName('primary' if not_collected or (to_dossiers and not work_left) else '')
        restyle(self.next_step_button)
        self.campaign_menu_button.setVisible((dossiers or collect or mails) and not self.busy)
        if has:
            archived = bool(self.store.batch(self.selected_batch()).get('archived_at'))
            self.campaign_archive_action.setText('Sortir des archives' if archived else 'Archiver dans un Excel')
        self.update_run_hint()

    def toggle_selected_archive(self):
        batch = self.selected_batch()
        if batch is None:
            return
        if self.store.batch(batch).get('archived_at'):
            self.unarchive_campaign(batch)
        else:
            self.archive_campaign(batch)

    def update_result_actions(self):
        self.update_header_actions()
        can_run = self.run_button.isEnabled()
        selected = len(self.selected_error_jobs())
        self.retry_selected_button.setText(f'Relancer la sélection ({selected}) + captures')
        # One selected error is already offered in the dossier sheet.
        self.retry_selected_button.setVisible(selected > 1 and not self.busy)
        self.retry_selected_button.setEnabled(can_run)
        if self.result_detail_key is not None and self._detail_row is not None:
            self.result_retry_button.setEnabled(can_run and self._detail_row[1]['status'] == 'ERREUR')
            self.result_retry_button.setToolTip(
                '' if can_run else 'Connectez-vous dans l’onglet Collecte pour relancer.'
            )
            self.result_retry_hint.setVisible(self._detail_row[1]['status'] == 'ERREUR' and not can_run)

    def result_selection_changed(self):
        self.show_result_detail(self._identity_from_selection())
        self.update_point_button()
        self.update_buttons()

    def reset_result_detail(self):
        self.result_detail_key = None
        self._detail_row = None
        self.result_detail_name.setText('')
        for label in (
            self.result_detail_meta,
            self.result_detail_status,
            self.result_detail_pointed,
            self.result_notices_heading,
            self.result_notices_empty,
            self.result_email_label,
        ):
            label.clear()
        self.result_detail_error.clear()
        self.clear_detail_notices()
        self.place_dossier_drawer()

    def clear_detail_notices(self):
        for panel in self.detail_notice_panels:
            # A panel can be rebuilt from its own Save button, so let Qt delete it once that click is done.
            panel.hide()
            self.result_notices_box.removeWidget(panel)
            panel.deleteLater()
        self.detail_notice_panels = []
        self._detail_notices_signature = None

    def show_result_detail(self, key):
        if key is None:
            self.reset_result_detail()
            return
        found = next(
            (
                (campaign, dossier)
                for campaign, dossier in self.result_rows
                if (campaign['id'], dossier['siren']) == key
            ),
            None,
        )
        if found is None:
            self.reset_result_detail()
            return
        campaign, dossier = found
        self.result_detail_key = key
        self._detail_row = (campaign, dossier)
        notices = self.store.notices(*key)
        finished = [notice for notice in notices if notice['status'] == 'TERMINE']
        situation = dossier_situation(dossier, finished)
        due, _mode, _reading = dossier_reading(finished)
        self.result_detail_name.setText(dossier['name'])
        email = dossier.get('email') or ''
        mono = load_local_fonts()['mono']
        self.result_detail_meta.setText(
            f'<span style="font-family:\'{mono}\'">{_siren_text(dossier["siren"])}</span> · '
            + escape(
                ' · '.join(
                    part
                    for part in (
                        dossier['collaborator'] or 'sans collaborateur',
                        email,
                        f'CFE {campaign["year"]}' if not self.table.isColumnHidden(COL_YEAR) else '',
                    )
                    if part
                )
            )
        )
        chip = {
            'PAY': f'À régler par le client · {format_amount(due)}' if due is not None else 'À régler par le client',
            'DEBIT': f'Prélevé · {format_amount(due)}' if due is not None else 'Prélevé',
            'ABSENT': 'Aucun avis publié',
        }.get(situation, situation_label(dossier, situation))
        self.result_detail_status.setText(chip)
        self.result_detail_status.setProperty('kind', situation)
        restyle(self.result_detail_status)
        pointed = dossier.get('pointed_at')
        self.result_detail_pointed.setText(f'Pointé le {when(pointed, year=True)}' if pointed else '')
        self.result_detail_pointed.setVisible(bool(self.result_detail_pointed.text()))
        self.result_detail_error.setPlainText(dossier['error'] or '')
        self.result_detail_error.setVisible(bool(dossier['error']))
        lines = min(6, 2 + len(dossier['error'] or '') // 36)
        self.result_detail_error.setFixedHeight(self.result_detail_error.fontMetrics().lineSpacing() * lines + 30)
        self.show_detail_notices(key, notices)
        sent = dossier.get('mail_sent_at') or ''
        opened = dossier.get('mail_opened_at') or ''
        line = (
            f'Mail envoyé le {when(sent, year=True)}'
            if sent
            else 'Mail ouvert dans Outlook, à confirmer une fois parti.'
            if opened
            else ''
            if email
            else 'Pas d’adresse e-mail · ajoutez-la dans Courriels.'
        )
        self.result_email_label.setText(line)
        self.result_email_label.setVisible(bool(line))
        self.result_retry_button.setVisible(dossier['status'] == 'ERREUR')
        self.result_retry_button.setEnabled(bool(self.run_button.isEnabled()) and dossier['status'] == 'ERREUR')
        self.result_retry_hint.setVisible(dossier['status'] == 'ERREUR' and not self.run_button.isEnabled())
        self.result_connections_button.setVisible(dossier['status'] == 'SANS_HABILITATION')
        verified = dossier.get('verified_at') or ''
        self.result_verified_check.blockSignals(True)
        self.result_verified_check.setChecked(bool(verified))
        self.result_verified_check.blockSignals(False)
        self.result_verified_check.setText(
            f'Vérifié manuellement le {when(verified, year=True, time=False)}'
            if verified
            else 'Vérifié manuellement sur impots.gouv.fr'
        )
        self.result_verified_check.setVisible(situation in ('MANUAL', 'CHECKED'))
        ready = dossier['status'] == 'TERMINE' and valid_client_email(email)
        self.result_mail_button.setText(
            'Marquer non envoyé' if sent else 'Marquer comme envoyé' if opened else 'Envoyer le mail'
        )
        self.result_mail_button.setEnabled(ready or bool(sent or opened))
        self.result_mail_button.setToolTip(
            '' if ready or sent or opened else 'Disponible quand les avis sont classés et l’adresse du client connue.'
        )
        self.place_dossier_drawer()

    def set_verified_from_sheet(self, verified):
        if self._detail_row is None:
            return
        campaign, dossier = self._detail_row
        try:
            self.store.verify_dossier(campaign['id'], dossier['siren'], verified)
        except ValueError as error:
            self.problem(str(error))
            return
        self.diagnostics.append('dossier_verified' if verified else 'dossier_unverified', batch=self.selected_batch())
        self.toast.announce(
            f'{dossier["name"]} vérifié manuellement.' if verified else f'Vérification retirée pour {dossier["name"]}.'
        )
        self.rendered = None
        self.refresh_table()

    def show_detail_notices(self, key, notices):
        signature = (
            key,
            tuple(
                tuple(
                    notice.get(field)
                    for field in (
                        'id',
                        'status',
                        'error',
                        'extraction_status',
                        'tax_amount_cents',
                        'amount_due_cents',
                        'payment_method',
                        'refund_cents',
                        'bank_account_display',
                    )
                )
                for notice in notices
            ),
        )
        if signature == self._detail_notices_signature:
            return
        self.clear_detail_notices()
        self._detail_notices_signature = signature
        for ordinal, notice in enumerate(notices, start=1):
            panel = NoticePanel(ordinal, notice, self)
            self.detail_notice_panels.append(panel)
            self.result_notices_box.addWidget(panel)
        classified = sum(notice['status'] == 'TERMINE' for notice in notices)
        self.result_notices_heading.setText(
            (f'{classified} avis classé' + ('s' if classified > 1 else '')) if notices else ''
        )
        self.result_notices_heading.setVisible(bool(notices))
        self.result_notices_empty.setText('Aucun avis enregistré pour ce dossier.')
        self.result_notices_empty.setVisible(not notices)

    def save_manual_reading(self, panel):
        form = panel.form
        try:
            method, tax_cents, due_cents = form.values()
            reading = manual_reading(panel.notice, method, tax_cents, due_cents)
        except ValueError as error:
            form.show_error(str(error))
            return False
        self.store.notice_extraction_result(panel.notice['id'], reading)
        self.diagnostics.append('notice_manual', batch=self.selected_batch())
        self.toast.announce(f'Avis {panel.ordinal} classé à la main : {PAYMENT_LABELS[method]}.')
        self.rendered = None
        self.refresh_table()
        return True

    def read_notice_now(self, panel):
        notice = ensure_notice_details(self.store, panel.notice)
        if not notice.get('extraction_status'):
            self.problem('Le fichier PDF de cet avis est introuvable dans le dossier de sortie.')
            return
        self.rendered = None
        self.refresh_table()

    def open_first_notice(self):
        if self.result_detail_key is None:
            return False
        notices = [notice for notice in self.store.notices(*self.result_detail_key) if notice['status'] == 'TERMINE']
        if not notices:
            self.toast.announce('Aucun avis classé pour ce dossier.')
            return False
        return self.open_notice_pdf(notices[0]['path'])

    def dossier_folder(self):
        campaign, dossier = self._detail_row
        notices = self.store.notices(campaign['id'], dossier['siren'])
        folders = [Path(notice['path']).parent for notice in notices if Path(notice['path']).parent.is_dir()]
        if folders:
            return folders[0]
        folder = Path(campaign['output']) / safe_name(dossier['collaborator']) / safe_name(dossier['name'])
        return folder if folder.is_dir() else Path(campaign['output'])

    def open_dossier_folder(self):
        if self._detail_row is None:
            return False
        return QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.dossier_folder())))

    def prepare_client_mail(self):
        """Open this client's mail in Outlook with the notices of every year attached, ready to send."""
        if self._detail_row is None:
            return False
        dossier = self._detail_row[1]
        siren = dossier['siren']
        if dossier.get('mail_sent_at') or dossier.get('mail_opened_at'):
            self.mark_mails_sent([siren], not dossier.get('mail_sent_at'))
            return True
        candidate = next((item for item in self.mail_candidates() if item['dossier']['siren'] == siren), None)
        if candidate is None:
            self.problem('Collecte à terminer pour ce dossier.')
            return False
        return self.open_drafts([candidate])

    def point_targets(self):
        identities = self._identities_from_selection()
        if self.result_detail_key is not None:
            identities.add(self.result_detail_key)
        return [
            (campaign, dossier)
            for campaign, dossier in self.result_rows
            if (campaign['id'], dossier['siren']) in identities and dossier['status'] == 'TERMINE'
        ]

    def update_point_button(self):
        targets = self.point_targets()
        if not targets:
            self.result_point_button.hide()
            return
        all_pointed = all(dossier.get('pointed_at') for _, dossier in targets)
        if len(targets) == 1:
            text = 'Retirer le pointage' if all_pointed else 'Pointer ce dossier'
        else:
            text = f'Retirer le pointage ({len(targets)})' if all_pointed else f'Pointer les {len(targets)} dossiers'
        self.result_point_button.setText(text)
        self.result_point_button.setObjectName('utility' if all_pointed else 'primary')
        restyle(self.result_point_button)
        self.result_point_button.show()

    def point_clicked_cell(self, row, column):
        """A click on the pointing box points or unpoints that dossier only."""
        if column != COL_POINT or row >= len(self.result_rows):
            return
        campaign, dossier = self.result_rows[row]
        if dossier['status'] != 'TERMINE':
            return
        self.set_pointed([(campaign, dossier)], not dossier.get('pointed_at'))

    def point_from_keyboard(self):
        """Point the selected dossier, then move to the next one so a whole list can be pointed from the keyboard."""
        targets = self.point_targets()
        if not targets:
            return
        row = self.table.currentRow()
        key = self.result_detail_key
        self.toggle_point_selected()
        if len(targets) != 1 or row < 0:
            return
        still_here = (
            row < len(self.result_rows) and (self.result_rows[row][0]['id'], self.result_rows[row][1]['siren']) == key
        )
        following = row + 1 if still_here else row
        if following < self.table.rowCount():
            self.table.selectRow(following)

    def toggle_point_selected(self):
        targets = self.point_targets()
        if targets:
            self.set_pointed(targets, not all(dossier.get('pointed_at') for _, dossier in targets))

    def set_pointed(self, targets, pointed):
        try:
            for campaign, dossier in targets:
                self.store.point_dossier(campaign['id'], dossier['siren'], pointed)
        except ValueError as error:
            self.problem(str(error))
            return
        self.diagnostics.append('dossier_pointed' if pointed else 'dossier_unpointed', batch=self.selected_batch())
        if len(targets) == 1:
            name = targets[0][1]['name']
            self.toast.announce(f'{name} pointé.' if pointed else f'Pointage retiré pour {name}.')
        else:
            self.toast.announce(
                f'{len(targets)} dossiers pointés.' if pointed else f'Pointage retiré pour {len(targets)} dossiers.'
            )
        self.rendered = None
        self.refresh_table()

    def retry_selected_result(self):
        key = self.result_detail_key
        if key is not None:
            self.start_selected_retry({key})

    def open_notice_pdf(self, path):
        path = Path(path)
        try:
            if not path.is_file():
                self.problem('Le fichier PDF de cet avis est introuvable dans le dossier de sortie.')
                return False
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                self.problem('Impossible d’ouvrir le PDF. Vérifiez l’application associée aux fichiers PDF.')
                return False
        except OSError:
            self.problem('Impossible d’accéder au PDF. Vérifiez le dossier de sortie et ses autorisations.')
            return False
        return True
