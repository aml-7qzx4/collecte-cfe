import re
import sys
import tempfile
from datetime import datetime
from html import escape
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTabWidget,
    QToolButton,
    QWidget,
)

from . import APP_NAME
from .credentials import AccountManager
from .diagnostics import DiagnosticLog
from .documents import archive_batch_workbook, export_batch_actions
from .domain import summarize_dossiers
from .notice_extraction import SETTLED_LABELS, review_label
from .paths import app_settings, legacy_database_paths, prepare_data_dir
from .portfolio import check_portfolio, valid_client_email
from .situations import dossier_situation
from .store import Store
from .ui_browser import BROWSER_CANVAS_MIN_WIDTH, BROWSER_END_MARGIN, BrowserWindowMixin
from .ui_collect import COLLECT_PAGE, SESSIONS_AT_LEAST, CollectPageMixin
from .ui_components import (
    AccountDialog,
    CampaignNavItem,
    CampaignTab,
    ClickableRow,
    NavButton,
    SegmentBar,
    asset_path,
    load_local_fonts,
)
from .ui_kit import button, hbox, make_frame, make_widget, restyle, text_label, vbox
from .ui_mail import MailPageMixin
from .ui_results import ResultsWorkspaceMixin
from .ui_settings import SETTINGS_PAGE, SettingsPageMixin
from .ui_style import STYLE
from .ui_widgets import ActionToast, ElidedPathLineEdit, PortfolioDropZone, when
from .window_embed import EmbeddedWindowHost


def parse_years(text, fallback):
    if not text.strip():
        return [fallback]
    result = set()
    for token in re.split(r'[,;\s]+', text.strip()):
        if not re.fullmatch(r'\d{4}(?:-\d{4})?', token):
            raise ValueError('Indiquez les années comme 2023, 2024, 2025 ou 2023-2025.')
        bounds = [int(y) for y in token.split('-')]
        if any(not 2016 <= y <= 2100 for y in bounds) or bounds[-1] < bounds[0]:
            raise ValueError(
                'Les années doivent être comprises entre 2016 et 2100, dans l’ordre croissant pour une plage.'
            )
        result.update(range(bounds[0], bounds[-1] + 1))
    return sorted(result)


def _count(number, singular, plural):
    return f'{number} {singular if number <= 1 else plural}'


def _issue_lines(issues, limit=25):
    lines = [
        f'Ligne {issue.line} · {"ignorée" if issue.level == "error" else "à vérifier"} : {issue.message}'
        for issue in issues[:limit]
    ]
    if len(issues) > limit:
        lines.append(f'… et {len(issues) - limit} autre(s).')
    return '\n'.join(lines)


class MainWindow(
    ResultsWorkspaceMixin, MailPageMixin, CollectPageMixin, SettingsPageMixin, BrowserWindowMixin, QMainWindow
):
    def __init__(self, store=None, settings=None, accounts=None):
        super().__init__()
        local = prepare_data_dir()
        self.store = store or Store(local / 'collecte-cfe.sqlite')
        self.accounts = accounts or AccountManager(self.store, legacy_paths=legacy_database_paths())
        self.diagnostics = DiagnosticLog(self.store.path.parent / 'journal-activite.txt')
        self.diagnostic_error_path = self.store.path.parent / 'moteur-erreurs.txt'
        self.diagnostics.append('application_started')
        self.session_statuses = []
        self.logged_session_events = []
        self.last_live_session_text = []
        self.settings = settings or app_settings()
        self.font_families = load_local_fonts()
        self.portfolio = []
        self.portfolio_issues = []
        self.portfolio_name = ''
        self.worker = None
        self.busy = False
        self.browser_ready = False
        self.pending_browser_focus = 0
        self.show_browser_after_connect = True
        self.connected_sessions = {}
        self.closing = False
        self.restart_sessions = False
        self.browser_workspace_active = False
        self.browser_was_foreground = False
        self.browser_urls = []
        self.browser_zoom_offsets = []
        self.browser_zoom_sent = []
        self.has_errors = False
        self.page_number = 0
        self.result_rows = []
        self.rendered = None
        self.setWindowIcon(QIcon(asset_path('app-icon.svg')))
        self.setWindowTitle(APP_NAME)
        self.resize(1280, 870)
        self.setMinimumSize(900, 620)
        root = make_widget('workspace')
        self.setCentralWidget(root)
        self.workspace = root
        outer = hbox(root, margins=0, spacing=0)
        self.rail = make_frame('rail')
        self.rail.setFixedWidth(232)
        menu = vbox(self.rail, margins=(14, 20, 14, 16), spacing=3)
        self.brand_row = QWidget()
        brand_layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, self.brand_row)
        self.brand_layout = brand_layout
        brand_layout.setContentsMargins(2, 0, 0, 0)
        brand_layout.setSpacing(8)
        self.brand = QLabel()
        self.brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.brand.setObjectName('brand')
        self.brand.setFixedSize(30, 30)
        self.brand.setPixmap(QIcon(asset_path('app-icon.svg')).pixmap(30, 30))
        brand_layout.addWidget(self.brand)
        self.brand_name = text_label(APP_NAME, 'brandName')
        brand_layout.addWidget(self.brand_name, 1)
        menu.addWidget(self.brand_row)
        menu.addSpacing(16)
        self.campaigns_heading = text_label('Campagnes', 'railSection')
        menu.addWidget(self.campaigns_heading)
        # Each campaign is an entry of its own, with how far its collection is.
        self.campaign_nav_box = vbox(spacing=2)
        menu.addLayout(self.campaign_nav_box)
        self.campaign_nav_items = {}
        menu.addSpacing(2)
        self.new_campaign_nav = button('+ Nouvelle campagne', self.start_new_campaign, name='newCampaignNav')
        menu.addWidget(self.new_campaign_nav)
        menu.addStretch()
        self.nav = [
            NavButton('Toutes les campagnes', '', 'campaigns.svg', lambda: self.navigate(0)),
            NavButton('Réglages', '', 'settings.svg', lambda: self.navigate(SETTINGS_PAGE)),
        ]
        for nav_button in self.nav:
            menu.addWidget(nav_button)
        self.user_block = make_frame('railUser')
        user = hbox(self.user_block, margins=(8, 10, 4, 4), spacing=10)
        self.user_initials = text_label('', 'railAvatar')
        self.user_initials.setFixedSize(28, 28)
        self.user_initials.setAlignment(Qt.AlignmentFlag.AlignCenter)
        user.addWidget(self.user_initials)
        names = vbox(spacing=0)
        self.user_name = text_label('', 'railUserName')
        names.addWidget(self.user_name)
        self.user_sessions = text_label('', 'railUserDetail')
        names.addWidget(self.user_sessions)
        user.addLayout(names, 1)
        menu.addWidget(self.user_block)
        outer.addWidget(self.rail)
        self.body = vbox(margins=(24, 20, 24, 16), spacing=12)
        outer.addLayout(self.body, 1)
        self.header_panel = make_widget('headerPanel')
        header = QBoxLayout(QBoxLayout.Direction.LeftToRight, self.header_panel)
        self.header_layout = header
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(18)
        titles = vbox(spacing=5)
        self.campaign_back = button('Campagnes', lambda: self.navigate(0), name='backLink')
        self.campaign_back.hide()
        titles.addWidget(self.campaign_back, 0, Qt.AlignmentFlag.AlignLeft)
        self.title = text_label('', 'title')
        titles.addWidget(self.title)
        self.source_summary = QLabel('')
        self.source_summary.setObjectName('muted')
        self.source_summary.setWordWrap(True)
        self.source_summary.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.source_summary.linkActivated.connect(lambda _link: self.open_output())
        titles.addWidget(self.source_summary)
        header.addLayout(titles, 1)
        self.header_actions = QWidget(self.header_panel)
        self.header_actions.setObjectName('headerActions')
        actions_box = vbox(self.header_actions, margins=0, spacing=6)
        actions = hbox(spacing=8)
        self.header_actions_layout = actions
        actions_box.addLayout(actions)
        self.campaigns = QComboBox(self.header_actions)
        self.campaigns.setMaxVisibleItems(12)
        self.campaigns.setMinimumWidth(0)
        self.campaigns.currentIndexChanged.connect(self.campaign_changed)
        self.campaigns.hide()
        self.open_button = button('Ouvrir le dossier', self.open_output)
        self.open_button.setParent(self.header_actions)
        actions.addWidget(self.open_button)
        self.login_button = button('Se connecter', self.open_browser, True)
        self.login_button.setParent(self.header_actions)
        actions.addWidget(self.login_button)
        self.run_button = button('Lancer la collecte', self.start_collection, True)
        self.run_button.setParent(self.header_actions)
        self.run_button.setObjectName('runCollection')
        actions.addWidget(self.run_button)
        self.run_hint = text_label(self.header_actions, 'runHint')
        self.run_hint.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.run_hint.hide()
        actions_box.addWidget(self.run_hint, 0, Qt.AlignmentFlag.AlignRight)
        self.header_actions.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        header.addWidget(self.header_actions, 0, Qt.AlignmentFlag.AlignBottom)
        self.body.addWidget(self.header_panel)
        self.campaign_panel = self.header_actions
        # The three steps of an open campaign, each with where it stands.
        self.campaign_tabs = make_frame('campaignTabs')
        tabs = hbox(self.campaign_tabs, margins=0, spacing=4)
        # In the order of the work: collect the notices, go through the dossiers, then write to the clients.
        self.campaign_tab_buttons = {
            COLLECT_PAGE: CampaignTab('Collecte', lambda: self.navigate(COLLECT_PAGE), 1),
            2: CampaignTab('Dossiers', lambda: self.navigate(2), 2),
            3: CampaignTab('Courriels', lambda: self.navigate(3), 3),
        }
        for tab in self.campaign_tab_buttons.values():
            tabs.addWidget(tab)
        tabs.addStretch()
        self.campaign_tabs.hide()
        self.body.addWidget(self.campaign_tabs)

        self.pages = QStackedWidget()
        self.body.addWidget(self.pages, 1)
        self.build_campaign_page()
        self.build_connections_page()
        self.build_results_page()
        self.build_mail_page()
        self.build_settings_page()
        self.build_collect_page()
        self.refresh_account_choices()
        self.apply_session_count(self.session_count.value())
        self.header_actions_layout.insertStretch(0, 1)
        self.header_actions_layout.addWidget(self.campaign_menu_button)
        self.activity = make_frame('activity')
        activity_box = vbox(self.activity, margins=(16, 12, 16, 12))
        activity_head = hbox(spacing=10)
        self.status = text_label('', wrap=True)
        activity_head.addWidget(self.status, 1)
        self.console_toggle = button('Journal en direct', self.toggle_live_console, name='consoleToggle')
        activity_head.addWidget(self.console_toggle)
        activity_box.addLayout(activity_head)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        activity_box.addWidget(self.progress)
        self.console_view = QPlainTextEdit()
        self.console_view.setObjectName('liveConsole')
        self.console_view.setReadOnly(True)
        self.console_view.setMaximumBlockCount(400)
        self.console_view.setFixedHeight(175)
        self.console_view.setVisible(False)
        activity_box.addWidget(self.console_view)
        self.body.addWidget(self.activity)
        self.activity.hide()
        for utility in [self.clear_button, self.prev_button, self.next_button]:
            utility.setObjectName('utility')
        styled = (
            STYLE.replace('@ASSETS@', (Path(__file__).parent / 'assets').as_posix())
            .replace('@BODYFONT@', self.font_families['body'])
            .replace('@TITLEFONT@', self.font_families['title'])
            .replace('@MONOFONT@', self.font_families['mono'])
        )
        self.setStyleSheet(styled)
        self.toast = ActionToast(root)
        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: self.refresh_table() if self.busy else None)
        self.timer.start(1800)
        self.browser_geometry_timer = QTimer(self)
        self.browser_geometry_timer.setInterval(300)
        self.browser_geometry_timer.timeout.connect(self.sync_browser_hosts)
        self.browser_focus_timer = QTimer(self)
        self.browser_focus_timer.setInterval(75)
        self.browser_focus_timer.timeout.connect(self.maintain_browser_focus)
        self.refresh_campaigns()
        self.navigate(0)

    def warn(self, title, detail=''):
        """Something needs the user: a message that stays in the corner until closed."""
        self.toast.announce(title, detail=detail, tone='warning')

    def update_activity_bar(self):
        # The progress bar and live journal only mean something while a collection runs, away from the Collecte page.
        self.activity.setVisible(
            self.busy and not self.browser_workspace_active and self.pages.currentIndex() != COLLECT_PAGE
        )

    def say(self, text):
        """Confirm an action: in the status bar during a collection, in a toast otherwise."""
        self.status.setText(text)
        if self.activity.isHidden():
            self.toast.announce(text)

    def toggle_live_console(self):
        opened = self.console_view.isHidden()
        self.console_view.setVisible(opened)
        self.console_toggle.setText('Masquer le journal' if opened else 'Journal en direct')

    def append_live_event(self, message):
        line = ' '.join(str(message).split())[:350]
        if not line:
            return
        self.status.setText(line)
        self.console_view.appendPlainText(f'{datetime.now():%H:%M:%S}  › {line}')
        self.record_live_event(line)
        if 'Export Excel impossible' in line and self.selected_batch() is not None:
            if 'relancez la collecte' in line.lower():
                self.warn('Export bloqué : relancez la collecte', line)
            else:
                self.warn(
                    'Export Excel à relancer',
                    'Les résultats sont enregistrés. Fermez le classeur ouvert, puis relancez l’export.',
                )

    def apply_responsive_layout(self):
        if not hasattr(self, 'header_actions'):
            return
        content_width = max(1, self.width() - self.rail.width() - 48)
        narrow_header = content_width < 760
        self.header_layout.setDirection(
            QBoxLayout.Direction.TopToBottom if narrow_header else QBoxLayout.Direction.LeftToRight
        )
        if hasattr(self, 'result_detail_card'):
            self.place_dossier_drawer()

    def build_campaign_page(self):
        self.campaign_flow = QStackedWidget()
        self.campaign_flow.setObjectName('campaignFlow')
        self.campaign_library = make_widget('page')
        library = vbox(self.campaign_library, margins=0, spacing=16)
        # The library's one action sits in the page header.
        self.new_campaign_button = button('Nouvelle campagne', self.new_campaign, True)
        self.new_campaign_button.setParent(self.header_actions)
        self.header_actions_layout.addWidget(self.new_campaign_button)
        self.campaign_empty = make_frame('campaignEmpty')
        empty_box = vbox(self.campaign_empty, margins=(28, 36, 28, 36))
        empty_title = text_label('Aucune campagne enregistrée', 'sectionTitle')
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_box.addWidget(empty_title)
        empty_text = text_label('Créez votre première collecte CFE pour commencer.', 'muted')
        empty_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_box.addWidget(empty_text)
        self.campaign_list = make_widget('campaignList')
        self.campaign_list_layout = QGridLayout(self.campaign_list)
        self.campaign_list_layout.setContentsMargins(0, 0, 0, 0)
        self.campaign_list_layout.setSpacing(16)
        list_content = make_widget('page')
        list_box = vbox(list_content, margins=(0, 0, 8, 0), spacing=12)
        list_box.addWidget(self.campaign_empty)
        list_box.addWidget(self.campaign_list)
        list_box.addStretch()
        self.campaign_library_scroll = QScrollArea()
        self.campaign_library_scroll.setObjectName('pageScroll')
        self.campaign_library_scroll.setWidgetResizable(True)
        self.campaign_library_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.campaign_library_scroll.setWidget(list_content)
        library.addWidget(self.campaign_library_scroll, 1)
        self.campaign_flow.addWidget(self.campaign_library)

        # A new campaign in three steps: the portfolio, the years and the filing, then the accounts and sessions.
        page = make_widget('page')
        layout = vbox(page, margins=0, spacing=16)
        steps = hbox(spacing=10)
        self.wizard_steps = []
        for number, title in enumerate(('Portefeuille', 'Années et classement'), start=1):
            step = make_frame('wizardStep')
            row = hbox(step, margins=(14, 10, 16, 10), spacing=10)
            step.dot = text_label(str(number), 'wizardDot')
            step.dot.setFixedSize(26, 26)
            step.dot.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row.addWidget(step.dot)
            texts = vbox(spacing=0)
            step.title = text_label(title, 'wizardTitle')
            texts.addWidget(step.title)
            step.detail = text_label('', 'wizardDetail')
            texts.addWidget(step.detail)
            row.addLayout(texts)
            step.number = number
            steps.addWidget(step)
            self.wizard_steps.append(step)
        steps.addStretch()
        layout.addLayout(steps)
        content = hbox(spacing=16)
        self.wizard_form = QStackedWidget()
        self.wizard_form.setObjectName('wizardForm')

        first = make_frame('wizardCard')
        box = vbox(first, margins=(24, 22, 24, 22), spacing=14)
        heading = text_label('Le portefeuille', 'settingsTitle')
        box.addWidget(heading)
        self.import_zone = PortfolioDropZone()
        self.import_zone.setObjectName('importZone')
        self.import_zone.file_dropped.connect(self.load_dropped_portfolio)
        import_box = vbox(self.import_zone, margins=(24, 22, 24, 22), spacing=8)
        upload_icon = text_label('↓', 'uploadIcon')
        upload_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        upload_icon.setFixedSize(50, 50)
        import_box.addWidget(upload_icon, 0, Qt.AlignmentFlag.AlignCenter)
        self.upload_title = text_label('Déposez votre liste de clients', 'importTitle')
        self.upload_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        import_box.addWidget(self.upload_title)
        self.upload_hint = text_label('Glissez un fichier CSV ou XLSX dans cette zone', 'muted')
        self.upload_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        import_box.addWidget(self.upload_hint)
        self.import_button = button('Parcourir les fichiers', self.choose_portfolio, name='outline')
        import_box.addWidget(self.import_button, 0, Qt.AlignmentFlag.AlignCenter)
        box.addWidget(self.import_zone)
        portfolio_status = QHBoxLayout()
        self.portfolio_label = text_label('', 'portfolioState', wrap=True)
        portfolio_status.addWidget(self.portfolio_label, 1)
        self.clear_button = button('Retirer le fichier', self.clear_portfolio, name='textButton')
        portfolio_status.addWidget(self.clear_button)
        box.addLayout(portfolio_status)
        self.portfolio_columns = text_label(
            'Colonnes attendues : nom du dossier · SIREN · collaborateur · e-mail du client (facultatif)',
            'columnChips',
            wrap=True,
        )
        box.addWidget(self.portfolio_columns)
        box.addStretch()
        box.addLayout(
            self.wizard_buttons(
                ('Annuler', self.show_campaign_library),
                ('Continuer · années et classement', lambda: self.show_wizard_step(2)),
            )
        )
        self.wizard_form.addWidget(first)

        second = make_frame('wizardCard')
        box = vbox(second, margins=(24, 22, 24, 22), spacing=16)
        name_label = text_label('Nom de la campagne', 'fieldLabel')
        box.addWidget(name_label)
        self.campaign_name = QLineEdit()
        self.campaign_name.setMaxLength(100)
        box.addWidget(self.campaign_name)
        years_label = text_label('Années d’imposition', 'fieldLabel')
        box.addWidget(years_label)
        self.year_chips_box = hbox(spacing=8)
        self.year_chips = []
        box.addLayout(self.year_chips_box)
        # The years themselves, kept in the fields the collection reads.
        hidden = QWidget()
        hidden.hide()
        hidden_box = QHBoxLayout(hidden)
        self.year_mode_buttons = []
        for text, mode in [('Une année', 'single'), ('Plusieurs années', 'multiple')]:
            choice = button(text, lambda _=False, value=mode: self.set_year_mode(value))
            choice.setCheckable(True)
            hidden_box.addWidget(choice)
            self.year_mode_buttons.append(choice)
        self.year = QSpinBox()
        self.year.setRange(2016, 2100)
        self.year.setValue(datetime.now().year)
        hidden_box.addWidget(self.year)
        self.years = QLineEdit()
        hidden_box.addWidget(self.years)
        box.addWidget(hidden)
        self.year.valueChanged.connect(self.update_name_suggestion)
        self.years.textChanged.connect(self.update_name_suggestion)
        output_label = text_label('Dossier de sortie', 'fieldLabel')
        box.addWidget(output_label)
        row = QHBoxLayout()
        self.output = ElidedPathLineEdit()
        self.output.setObjectName('monoField')
        self.output.setPlaceholderText('Dossier pour les PDF et les actions Excel')
        row.addWidget(self.output, 1)
        self.output_button = button('Choisir…', self.choose_output)
        row.addWidget(self.output_button)
        box.addLayout(row)
        self.output_tree = text_label('', 'outputTree')
        self.output_tree.setTextFormat(Qt.TextFormat.RichText)
        box.addWidget(self.output_tree)
        self.output.textChanged.connect(self.update_output_tree)
        box.addStretch()
        accounts_hint = text_label(
            'La collecte se connecte avec les comptes experts enregistrés dans Réglages.', 'fieldHint'
        )
        box.addWidget(accounts_hint)
        # The number of sessions follows the expert accounts: four at least, one per account beyond.
        self.session_count = QSpinBox()
        self.session_count.setRange(1, 999)
        self.session_count.setValue(SESSIONS_AT_LEAST)
        self.session_count.hide()
        box.addWidget(self.session_count)
        self.create_button = button('Créer la campagne', self.create_campaign, True)
        box.addLayout(self.wizard_buttons(('Retour', lambda: self.show_wizard_step(1)), self.create_button))
        self.wizard_form.addWidget(second)
        content.addWidget(self.wizard_form, 1)

        self.portfolio_summary = make_frame('wizardCard')
        self.portfolio_summary.setFixedWidth(340)
        aside = vbox(self.portfolio_summary, margins=(20, 18, 20, 18), spacing=12)
        aside_title = text_label('Contrôle du portefeuille', 'sessionTitle')
        aside.addWidget(aside_title)
        self.portfolio_facts = vbox(spacing=10)
        aside.addLayout(self.portfolio_facts)
        self.portfolio_fact_rows = []
        self.portfolio_mail_note = text_label('', 'warningNote', wrap=True)
        self.portfolio_mail_note.hide()
        aside.addWidget(self.portfolio_mail_note)
        self.portfolio_check_panel = make_frame('portfolioCheck')
        check_box = vbox(self.portfolio_check_panel, margins=(12, 10, 12, 10), spacing=4)
        self.portfolio_check_title = text_label('', 'portfolioCheckTitle', wrap=True)
        check_box.addWidget(self.portfolio_check_title)
        self.portfolio_check_lines = text_label('', 'muted', wrap=True)
        self.portfolio_check_lines.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.portfolio_check_lines.hide()
        check_box.addWidget(self.portfolio_check_lines)
        self.portfolio_check_panel.hide()
        aside.addWidget(self.portfolio_check_panel)
        self.portfolio_details_button = button(
            'Voir le détail',
            lambda: self.portfolio_check_lines.setVisible(not self.portfolio_check_lines.isVisible()),
            name='linkStrong',
        )
        aside.addWidget(self.portfolio_details_button, 0, Qt.AlignmentFlag.AlignLeft)
        aside.addStretch()
        content.addWidget(self.portfolio_summary, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(content, 1)
        self.year_mode = 'single'
        self.set_year_mode('single')
        self.update_campaign_source_view()
        scroll = QScrollArea()
        scroll.setObjectName('pageScroll')
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(page)
        self.campaign_editor = scroll
        self.campaign_flow.addWidget(scroll)
        self.show_wizard_step(1)

        self.pages.addWidget(self.campaign_flow)

    def wizard_buttons(self, back, forward):
        row = hbox(spacing=8)
        frame = make_frame('wizardRule')
        outer = vbox(spacing=16)
        outer.addWidget(frame)
        text, slot = back
        row.addWidget(button(text, slot))
        row.addStretch()
        if isinstance(forward, tuple):
            forward = button(forward[0], forward[1], True)
        row.addWidget(forward)
        outer.addLayout(row)
        return outer

    def show_wizard_step(self, number):
        self.wizard_form.setCurrentIndex(number - 1)
        details = {1: self.portfolio_step_detail(), 2: self.years_step_detail()}
        for step in self.wizard_steps:
            state = 'done' if step.number < number else 'active' if step.number == number else 'todo'
            step.setProperty('stepState', state)
            step.dot.setProperty('stepState', state)
            if state == 'done':
                step.dot.setPixmap(QIcon(asset_path('icons/check-white.svg')).pixmap(14, 14))
            else:
                step.dot.clear()
                step.dot.setText(str(step.number))
            step.detail.setText(
                details[step.number] if state == 'done' else 'En cours' if state == 'active' else 'À venir'
            )
            step.setMinimumWidth(step.layout().sizeHint().width())
            for widget in (step, step.dot, step.title, step.detail):
                restyle(widget)
        if number == 2:
            self.update_output_tree()

    def portfolio_step_detail(self):
        return (
            f'{self.portfolio_file} · {_count(len(self.portfolio), "dossier", "dossiers")}'
            if self.portfolio
            else 'Tout le compte expert'
        )

    def years_step_detail(self):
        years = self.selected_years()
        return ', '.join(str(year) for year in years) if years else 'Années à choisir'

    def selected_years(self):
        if self.year_mode == 'multiple':
            try:
                return parse_years(self.years.text(), self.year.value()) if self.years.text().strip() else []
            except ValueError:
                return []
        return [self.year.value()]

    def sync_year_chips(self):
        """One chip per tax year around this one; the chosen years are filled in ink."""
        chosen = set(self.selected_years())
        now = datetime.now().year
        shown = sorted(set(range(now - 3, now + 1)) | chosen)
        if [chip.year for chip in self.year_chips] != shown:
            for chip in self.year_chips:
                chip.hide()
                self.year_chips_box.removeWidget(chip)
                chip.deleteLater()
            self.year_chips = []
            for year in shown:
                chip = button(str(year), lambda _=False, y=year: self.toggle_year(y))
                chip.setObjectName('yearChip')
                chip.setCheckable(True)
                chip.year = year
                self.year_chips_box.addWidget(chip)
                self.year_chips.append(chip)
            self.year_chips_box.addStretch()
        for chip in self.year_chips:
            chip.setChecked(chip.year in chosen)

    def toggle_year(self, year):
        chosen = set(self.selected_years())
        chosen ^= {year}
        if not chosen:
            chosen = {year}
        years = sorted(chosen)
        if len(years) == 1:
            self.years.clear()
            self.year.setValue(years[0])
            self.set_year_mode('single')
        else:
            self.years.setText(', '.join(str(y) for y in years))
            self.set_year_mode('multiple')

    def update_output_tree(self, *_):
        output = self.output.text().strip()
        folder = Path(output).name if output else 'Dossier de sortie'
        years = self.selected_years() or [self.year.value()]
        year = years[-1]
        sample = self.portfolio[0] if self.portfolio else None
        collaborator = (sample.collaborator if sample else '') or 'À affecter'
        dossier = sample.name if sample else 'Nom du dossier'
        mono = self.font_families['mono']
        lines = [
            escape(folder),
            f'└ {escape(collaborator)}',
            f'&nbsp;&nbsp;└ {escape(dossier)}',
            f'&nbsp;&nbsp;&nbsp;&nbsp;├ {escape(dossier)}_CFE_{year}_SOLDE.pdf',
            f'&nbsp;&nbsp;&nbsp;&nbsp;└ {escape(dossier)}_CFE_{year}_SANS_HABILITATION.pdf <span style="font-family:\'{self.font_families["body"]}\'; color:#59627A">(si besoin)</span>',
        ]
        self.output_tree.setText(
            f'<div style="font-family:\'{mono}\'; line-height:150%">' + '<br>'.join(lines) + '</div>'
        )

    def set_year_mode(self, mode):
        self.year_mode = 'multiple' if mode == 'multiple' else 'single'
        for button_index, choice in enumerate(self.year_mode_buttons):
            choice.setChecked((button_index == 0) == (self.year_mode == 'single'))
        self.update_name_suggestion()

    def suggested_campaign_name(self):
        period = str(self.year.value())
        if self.year_mode == 'multiple' and self.years.text().strip():
            try:
                years = parse_years(self.years.text(), self.year.value())
                period = str(years[0]) if len(years) == 1 else f'{years[0]}-{years[-1]}'
            except ValueError:
                period = self.years.text().strip()
        return f'CFE {period} · {self.portfolio_name if self.portfolio else "compte expert"}'

    def update_name_suggestion(self, *_):
        self.campaign_name.setPlaceholderText(self.suggested_campaign_name())
        if hasattr(self, 'year_chips_box'):
            self.sync_year_chips()

    def show_campaign_library(self):
        self.refresh_campaign_cards()
        self.campaign_flow.setCurrentWidget(self.campaign_library)
        self.title.setText('Campagnes')
        self.source_summary.setText('')
        self.campaign_back.hide()
        self.campaign_tabs.hide()
        self.header_actions.setVisible(True)
        self.update_header_actions()

    def new_campaign(self):
        self.portfolio = []
        self.portfolio_issues = []
        self.portfolio_file = ''
        self.campaign_name.clear()
        self.year.setValue(datetime.now().year)
        self.years.clear()
        self.set_year_mode('single')
        self.output.setText('')
        self.session_count.setValue(self.needed_sessions())
        self.update_campaign_source_view()
        self.campaign_flow.setCurrentWidget(self.campaign_editor)
        self.show_wizard_step(1)
        self.title.setText('Nouvelle campagne')
        self.source_summary.setText('')
        self.header_actions.setVisible(False)
        self.campaign_back.show()
        self.campaign_tabs.hide()
        self.update_campaign_nav_checks()

    def start_new_campaign(self):
        self.navigate(0)
        self.new_campaign()

    def refresh_campaign_nav(self):
        """Rebuild the sidebar's campaign entries, each with what is left to do on it."""
        # Archived campaigns stay on the Campagnes page, not in the sidebar.
        batches = [batch for batch in self.store.batches() if not batch.get('archived_at')]
        kept = {batch['id'] for batch in batches}
        for batch_id in [key for key in self.campaign_nav_items if key not in kept]:
            item = self.campaign_nav_items.pop(batch_id)
            item.hide()
            self.campaign_nav_box.removeWidget(item)
            item.deleteLater()
        for position, batch in enumerate(batches):
            item = self.campaign_nav_items.get(batch['id'])
            if item is None:
                item = CampaignNavItem(batch['id'], batch['name'], self.open_campaign_where_it_stands)
                self.campaign_nav_items[batch['id']] = item
            self.campaign_nav_box.insertWidget(position, item)
            item.set_name(batch['name'])
            self.update_campaign_badge(batch['id'], *self.campaign_progress(batch['id']))
        self.update_campaign_nav_checks()

    def campaign_progress(self, batch_id):
        """Number of dossiers, dossiers left to handle (errors, notices to check) and dossiers not yet collected."""
        handle = pending = rows = 0
        sirens = set()
        for campaign in self.store.batch_campaigns(batch_id):
            notices = {}
            for notice in self.store.notices(campaign['id']):
                if notice['status'] == 'TERMINE':
                    notices.setdefault(notice['siren'], []).append(notice)
            for dossier in self.store.dossiers(campaign['id']):
                sirens.add(dossier['siren'])
                situation = dossier_situation(dossier, notices.get(dossier['siren'], []))
                handle += situation in ('MANUAL', 'RETRY', 'REVIEW')
                pending += situation == 'WAIT'
                rows += 1
        return len(sirens), handle, pending, rows

    def update_campaign_badge(self, batch_id, dossiers, handle=0, pending=0, rows=None):
        """The sidebar shows how many dossiers a campaign holds and how far its collection is; what is left to do is in its tooltip."""
        item = self.campaign_nav_items.get(batch_id)
        if item is None:
            return
        rows = dossiers if rows is None else rows
        item.set_progress(rows - pending, rows)
        running = self.busy and batch_id == self.selected_batch()
        state = (
            'collecte en cours'
            if running
            else 'à lancer'
            if dossiers and pending == dossiers
            else ', '.join(
                part
                for part in (f'{handle} à traiter' if handle else '', f'{pending} en attente' if pending else '')
                if part
            )
            or 'terminée'
        )
        item.set_badge(
            str(dossiers) if dossiers else '',
            'running' if running else '',
            f'{dossiers} dossier' + ('s' if dossiers > 1 else '') + f' · {state}' if dossiers else '',
        )

    def update_campaign_nav_checks(self):
        index = self.pages.currentIndex()
        selected = self.selected_batch()
        in_campaign = index in (2, 3, COLLECT_PAGE) and not self.browser_workspace_active
        for batch_id, item in self.campaign_nav_items.items():
            item.setChecked(in_campaign and batch_id == selected)

    def campaign_subtitle(self, batch_id):
        annual = self.store.batch_campaigns(batch_id)
        dossiers = len({d['siren'] for campaign in annual for d in self.store.dossiers(campaign['id'])})
        output = annual[0]['output'] if annual else ''
        parts = Path(output).parts
        shown = ('…\\' + '\\'.join(parts[-2:])) if len(parts) > 3 else output
        return (
            f'{_count(dossiers, "dossier", "dossiers")} · {self.campaign_years(annual)} · classés dans '
            f'<a href="output" style="color:#2F45D8; text-decoration:none">{escape(shown)}</a>'
        )

    @staticmethod
    def campaign_years(annual):
        years = [str(c['year']) for c in annual]
        return ('année ' if len(years) == 1 else 'années ') + ', '.join(years)

    def update_campaign_header(self, index):
        batch_id = self.selected_batch()
        in_campaign = batch_id is not None and index in (2, 3, COLLECT_PAGE) and not self.browser_workspace_active
        self.update_campaign_nav_checks()
        editing = self.pages.currentIndex() == 0 and self.campaign_flow.currentWidget() is self.campaign_editor
        self.campaign_back.setVisible((in_campaign or editing) and not self.browser_workspace_active)
        self.campaign_tabs.setVisible(in_campaign)
        if not in_campaign:
            return
        for page, tab in self.campaign_tab_buttons.items():
            tab.setChecked(page == index)
        batch = self.store.batch(batch_id)
        annual = self.store.batch_campaigns(batch_id)
        self.title.setText(batch['name'])
        if index == 2:
            self.source_summary.setText(self.campaign_subtitle(batch_id))
        elif index == COLLECT_PAGE:
            dossiers = len({d['siren'] for campaign in annual for d in self.store.dossiers(campaign['id'])})
            self.source_summary.setText(f'{_count(dossiers, "dossier", "dossiers")} · {self.campaign_years(annual)}')
        else:
            dossiers = len({d['siren'] for campaign in annual for d in self.store.dossiers(campaign['id'])})
            self.source_summary.setText(f'{_count(dossiers, "dossier", "dossiers")} · {self.campaign_years(annual)}')
        self.update_campaign_tabs()
        self.header_actions.setVisible(True)
        self.update_header_actions()

    def update_campaign_tabs(self):
        """Each step of the campaign says where it stands: dossiers, collection, mails ready."""
        if self.selected_batch() is None:
            return
        stats = self.collect_stats or {}
        total = stats.get('total', 0)
        pending = stats.get('pending', 0)
        retry = stats.get('retry', 0)
        current = self.pages.currentIndex()
        dossiers = self.campaign_tab_buttons[2]
        dossiers.set_badge(str(stats.get('dossiers', 0)), 'active' if current == 2 else '')
        collect = (
            ('en cours', 'running')
            if self.busy
            else (f'{retry} à relancer', 'danger')
            if retry
            else ('à lancer', '')
            if total and pending == total
            else (f'{pending} en attente', '')
            if pending
            else ('terminée', '')
            if total
            else ('', '')
        )
        self.campaign_tab_buttons[COLLECT_PAGE].set_badge(*collect) if collect[1] else self.campaign_tab_buttons[
            COLLECT_PAGE
        ].set_badge(collect[0], 'active' if current == COLLECT_PAGE else '')
        ready = (getattr(self, 'mail_counts', None) or {}).get('READY', 0)
        self.campaign_tab_buttons[3].set_badge(
            _count(ready, 'prêt', 'prêts') if ready else '', 'active' if current == 3 else ''
        )

    def refresh_campaign_cards(self):
        while self.campaign_list_layout.count():
            item = self.campaign_list_layout.takeAt(0)
            # Hidden at once: until Qt deletes it, a card still paints at its old place behind the new ones.
            if item.widget():
                item.widget().hide()
                item.widget().setParent(None)
                item.widget().deleteLater()
        self.campaign_cards = []
        batches = self.store.batches()
        self.campaign_empty.setVisible(not batches)
        columns = 2 if self.width() - self.rail.width() - 64 >= 880 else 1
        active = [batch for batch in batches if not batch.get('archived_at')]
        archived = [batch for batch in batches if batch.get('archived_at')]
        row = 0
        for position, batch in enumerate(active):
            frame = self.campaign_card(batch)
            self.campaign_list_layout.addWidget(frame, row + position // columns, position % columns)
            self.campaign_cards.append(frame)
        row += (len(active) + columns - 1) // columns
        if archived:
            heading = text_label('Archivées', 'settingsTitle')
            heading.setContentsMargins(0, 12, 0, 0)
            self.campaign_list_layout.addWidget(heading, row, 0, 1, columns)
            row += 1
            for position, batch in enumerate(archived):
                frame = self.campaign_card(batch)
                self.campaign_list_layout.addWidget(frame, row + position // columns, position % columns)
                self.campaign_cards.append(frame)
        for column in range(2):
            self.campaign_list_layout.setColumnStretch(column, 1 if column < columns else 0)

    def campaign_card(self, batch):
        """One campaign: how far its collection is, what to do next, and the one button that does it."""
        annual = self.store.batch_campaigns(batch['id'])
        years = ', '.join(str(c['year']) for c in annual)
        rows = []
        finished = absent = handle = pending = 0
        ready = sent = with_email = 0
        checked = ''
        for campaign in annual:
            notices = {}
            for notice in self.store.notices(campaign['id']):
                if notice['status'] == 'TERMINE':
                    notices.setdefault(notice['siren'], []).append(notice)
            for dossier in self.store.dossiers(campaign['id']):
                rows.append(dossier)
                situation = dossier_situation(dossier, notices.get(dossier['siren'], []))
                finished += dossier['status'] == 'TERMINE'
                absent += situation == 'ABSENT'
                handle += situation in ('MANUAL', 'RETRY', 'REVIEW')
                pending += situation == 'WAIT'
                checked = max(checked, dossier.get('checked') or '')
        clients = {}
        for dossier in rows:
            clients.setdefault(dossier['siren'], []).append(dossier)
        for dossiers in clients.values():
            first = dossiers[0]
            if not valid_client_email(first.get('email') or ''):
                continue
            with_email += 1
            if first.get('mail_sent_at'):
                sent += 1
            elif all(d['status'] == 'TERMINE' for d in dossiers) and not first.get('mail_opened_at'):
                ready += 1
        total = len(rows)
        count = len(clients)
        meta = f'{years} · {_count(count, "dossier", "dossiers")}' + (
            f' · terminée le {when(checked, year=True, time=False)}'
            if total and not pending and not handle and checked
            else ''
        )
        if total and pending == total:
            step, action, target = 'Pas encore collectée', 'Lancer la collecte', COLLECT_PAGE
        elif handle or ready:
            parts = [
                part
                for part in (
                    _count(handle, 'dossier à traiter', 'dossiers à traiter') if handle else '',
                    _count(ready, 'mail prêt', 'mails prêts') if ready else '',
                )
                if part
            ]
            step, action, target = ', '.join(parts), 'Ouvrir', 2
        elif pending:
            step, action, target = _count(pending, 'dossier en attente', 'dossiers en attente'), 'Ouvrir', 2
        elif with_email and sent == with_email:
            step, action, target = 'Tous les mails envoyés', 'Ouvrir', 2
        else:
            step, action, target = ('Collecte terminée' if total else 'Aucun dossier pour l’instant'), 'Ouvrir', 2
        archived = bool(batch.get('archived_at'))
        if archived:
            meta = f'{years} · {_count(count, "dossier", "dossiers")} · archivée le {when(batch["archived_at"], year=True, time=False)}'
            step, action, target = 'Archivée dans un Excel, à la racine du dossier de sortie.', 'Ouvrir l’archive', None
        frame = make_frame('campaignCard')
        frame.setProperty('archived', archived)
        box = vbox(frame, margins=(20, 18, 20, 18), spacing=14)
        head = hbox(spacing=12)
        titles = vbox(spacing=4)
        title = text_label(batch['name'], 'campaignCardTitle', wrap=True)
        titles.addWidget(title)
        detail = text_label(meta, 'muted')
        titles.addWidget(detail)
        head.addLayout(titles, 1)
        more = QToolButton()
        more.setObjectName('cardMenu')
        more.setIcon(QIcon(asset_path('icons/more.svg')))
        more.setIconSize(QSize(16, 16))
        more.setToolTip('Plus d’actions : renommer, supprimer')
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(more)
        if archived:
            menu.addAction('Ouvrir la campagne', lambda b=batch['id']: self.open_campaign(b))
            menu.addAction('Sortir des archives', lambda b=batch['id']: self.unarchive_campaign(b))
        else:
            menu.addAction('Archiver dans un Excel', lambda b=batch['id']: self.archive_campaign(b))
        menu.addAction('Renommer', lambda b=batch['id']: self.rename_campaign(b))
        menu.addAction('Ouvrir le dossier de sortie', lambda b=batch['id']: self.open_batch_output(b))
        menu.addSeparator()
        menu.addAction('Supprimer', lambda b=batch['id']: self.delete_campaign(b))
        more.setMenu(menu)
        head.addWidget(more, 0, Qt.AlignmentFlag.AlignTop)
        box.addLayout(head)
        bar = SegmentBar(8)
        bar.set_segments([(finished, '#1A6A80'), (absent, '#9AA6C0'), (handle, '#B01F48')], total)
        box.addWidget(bar)
        counts = hbox(spacing=14)
        for number, label, kind in (
            (finished, 'avec avis', ''),
            (absent, 'sans avis', ''),
            (handle, 'à traiter', 'retry'),
            (pending, 'en attente', ''),
        ):
            value = text_label(f'<b>{number}</b> {label}', 'collectCount')
            value.setProperty('kind', kind)
            counts.addWidget(value)
        counts.addStretch()
        box.addLayout(counts)
        foot = make_frame('cardFoot')
        foot_row = hbox(foot, margins=(0, 12, 0, 0), spacing=12)
        next_step = text_label(step, 'muted', wrap=True)
        foot_row.addWidget(next_step, 1)
        open_button = button(
            action,
            (lambda _=False, b=batch['id']: self.open_archive(b))
            if archived
            else (lambda _=False, b=batch['id'], page=target: self.open_campaign(b, page)),
        )
        open_button.setObjectName('accent')
        foot_row.addWidget(open_button)
        frame.open_button = open_button
        frame.menu = menu
        box.addWidget(foot)
        return frame

    def archive_campaign(self, batch):
        """Keep a finished campaign in one Excel workbook and move it to the archived campaigns."""
        if self.busy:
            self.problem('Mettez la collecte en pause avant d’archiver la campagne.')
            return False
        _dossiers, _handle, pending, _rows = self.campaign_progress(batch)
        if pending:
            self.problem('Terminez la collecte avant d’archiver la campagne.')
            return False
        try:
            path = archive_batch_workbook(self.store, batch)
        except ValueError as error:
            self.warn('Archivage impossible', str(error))
            return False
        except Exception:
            self.warn('Archivage impossible', 'Vérifiez le dossier de sortie et fermez les classeurs ouverts.')
            return False
        self.store.archive_batch(batch, path)
        self.diagnostics.append('campaign_archived', batch=batch)
        self.toast.announce('Campagne archivée.', path)
        self.refresh_campaigns()
        self.navigate(0)
        return True

    def unarchive_campaign(self, batch):
        self.store.archive_batch(batch, None)
        self.diagnostics.append('campaign_unarchived', batch=batch)
        self.refresh_campaigns()
        self.navigate(0)

    def open_archive(self, batch):
        path = self.store.batch(batch).get('archive_path') or ''
        if path and Path(path).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        else:
            self.problem(
                'L’Excel d’archive est introuvable dans le dossier de sortie. Sortez la campagne des archives puis archivez-la à nouveau.'
            )

    def open_batch_output(self, batch):
        annual = self.store.batch_campaigns(batch)
        if annual:
            QDesktopServices.openUrl(QUrl.fromLocalFile(annual[0]['output']))

    def open_campaign_where_it_stands(self, batch):
        """A campaign not collected yet opens on its collection; once collected, on its dossiers."""
        _dossiers, _handle, pending, rows = self.campaign_progress(batch)
        collecting = self.busy and batch == self.selected_batch()
        self.open_campaign(batch, COLLECT_PAGE if collecting or not rows or pending == rows else 2)

    def open_campaign(self, batch, page=2):
        index = self.campaigns.findData(batch)
        if index < 0:
            return
        if index == self.campaigns.currentIndex():
            self.rendered = None
            self.refresh_table()
        else:
            self.campaigns.setCurrentIndex(index)
        self.navigate(page)

    def rename_campaign(self, batch, name=None):
        if self.busy:
            self.problem('Mettez la collecte en pause avant de renommer la campagne.')
            return
        if name is None:
            dialog = QInputDialog(self)
            dialog.setWindowTitle('Renommer la campagne')
            dialog.setLabelText('Nouveau nom')
            dialog.setTextValue(self.store.batch(batch)['name'])
            dialog.setOkButtonText('Enregistrer')
            dialog.setCancelButtonText('Annuler')
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            name = dialog.textValue()
        try:
            self.store.rename_batch(batch, name)
            self.refresh_campaigns(batch)
            self.say('Nom de la campagne enregistré.')
        except ValueError as error:
            self.problem(str(error))

    def delete_campaign(self, batch):
        if self.busy:
            self.problem('Mettez la collecte en pause avant de supprimer la campagne.')
            return
        if not self.confirm_campaign_deletion(self.store.batch(batch)['name']):
            return
        self.store.delete_batch(batch)
        self.refresh_campaigns()
        self.say('Campagne supprimée. Les fichiers de sortie sont conservés.')
        self.navigate(0)

    def confirm_campaign_deletion(self, name):
        """Say plainly that the deletion cannot be undone; « Annuler » is the default answer."""
        box = QMessageBox(
            QMessageBox.Icon.Warning, 'Supprimer la campagne', f'Supprimer la campagne « {name} » ?', parent=self
        )
        box.setInformativeText(
            'Cette action est irréversible : les résultats, le pointage et le suivi des mails de la campagne '
            'sont effacés de l’application.\n\nLes PDF et les classeurs Excel déjà créés restent dans le dossier de sortie.'
        )
        delete = box.addButton('Supprimer définitivement', QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton('Annuler', QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        return box.clickedButton() is delete

    def load_dropped_portfolio(self, path):
        try:
            self.load_portfolio(path)
        except Exception as error:
            self.problem(
                str(error) if isinstance(error, ValueError) else 'Le fichier n’a pas pu être lu. Vérifiez son format.'
            )

    def update_campaign_source_view(self):
        if self.portfolio:
            self.portfolio_label.setText(
                f'{len(self.portfolio):,} dossiers importés'.replace(',', ' ')
                + (f' · {self.portfolio_file}' if getattr(self, 'portfolio_file', '') else '')
            )
            self.upload_title.setText('Portefeuille prêt')
            self.upload_hint.setText('Vous pouvez déposer un autre fichier pour le remplacer.')
            self.import_zone.setProperty('loaded', True)
            self.clear_button.setVisible(True)
        else:
            self.portfolio_label.setText(
                'Sans fichier, tous les dossiers accessibles depuis le compte expert seront utilisés.'
            )
            self.upload_title.setText('Déposez votre liste de clients')
            self.upload_hint.setText('Glissez un fichier CSV ou XLSX dans cette zone')
            self.import_zone.setProperty('loaded', False)
            self.clear_button.setVisible(False)
        restyle(self.import_zone)
        self.create_button.setText(
            f'Créer la campagne · {_count(len(self.portfolio), "dossier", "dossiers")}'
            if self.portfolio
            else 'Créer la campagne'
        )
        self.show_portfolio_issues()
        self.update_name_suggestion()
        self.update_portfolio_facts()

    def update_portfolio_facts(self):
        """What was read in the portfolio: dossiers, collaborators, addresses and the SIREN to look at."""
        for row in self.portfolio_fact_rows:
            row.hide()
            self.portfolio_facts.removeWidget(row)
            row.deleteLater()
        self.portfolio_fact_rows = []
        if self.portfolio:
            emails = sum(bool(dossier.email) for dossier in self.portfolio)
            total = len(self.portfolio)
            doubtful = sum(issue.level != 'error' for issue in self.portfolio_issues)
            ignored = sum(issue.level == 'error' for issue in self.portfolio_issues)
            facts = [
                ('Dossiers lus', f'{total:,}'.replace(',', ' '), False),
                (
                    'Collaborateurs',
                    str(len({dossier.collaborator for dossier in self.portfolio if dossier.collaborator})),
                    False,
                ),
                ('Adresses e-mail', f'{emails} sur {total}', False),
            ]
            if doubtful:
                facts.append(('SIREN à vérifier', str(doubtful), True))
            if ignored:
                facts.append(('Lignes ignorées', str(ignored), True))
            missing = total - emails
            self.portfolio_mail_note.setText(
                f'{_count(missing, "client sans adresse e-mail", "clients sans adresse e-mail")}, à compléter dans Courriels.'
            )
            self.portfolio_mail_note.setVisible(missing > 0)
        else:
            facts = [('Source', 'compte expert', False)]
            self.portfolio_mail_note.setVisible(False)
        for label, value, warning in facts:
            row = QWidget()
            line = hbox(row, margins=0)
            name = QLabel(label)
            figure = QLabel(f'<b>{escape(value)}</b>')
            for widget in (name, figure):
                widget.setObjectName('factWarning' if warning else 'fact')
            line.addWidget(name, 1)
            line.addWidget(figure)
            self.portfolio_facts.addWidget(row)
            self.portfolio_fact_rows.append(row)
        self.portfolio_details_button.setVisible(bool(self.portfolio_issues and self.portfolio))

    def show_portfolio_issues(self):
        issues = self.portfolio_issues if self.portfolio else []
        self.portfolio_check_panel.setVisible(bool(issues))
        if not issues:
            return
        ignored = sum(issue.level == 'error' for issue in issues)
        doubtful = len(issues) - ignored
        parts = ([_count(ignored, 'ligne ignorée', 'lignes ignorées')] if ignored else []) + (
            [f'{doubtful} SIREN à vérifier'] if doubtful else []
        )
        advice = (
            'Corrigez le fichier puis déposez-le à nouveau, ou créez la campagne sans ces lignes.'
            if ignored
            else 'Ces dossiers seront collectés ; vérifiez leur SIREN si un avis manque.'
        )
        self.portfolio_check_title.setText(' · '.join(parts) + '. ' + advice)
        self.portfolio_check_lines.setText(_issue_lines(issues))
        self.portfolio_check_panel.setProperty('tone', 'warning' if ignored else 'info')
        restyle(self.portfolio_check_panel)

    def build_connections_page(self):
        page = make_widget('page')
        layout = vbox(page, margins=0, spacing=16)
        self.browser_shell = QFrame(page)
        self.browser_shell.setObjectName('browserShell')
        shell = vbox(self.browser_shell, margins=0, spacing=0)
        self.browser_toolbar = QFrame(self.browser_shell)
        self.browser_toolbar.setObjectName('browserToolbar')
        toolbar = vbox(self.browser_toolbar, margins=(8, 7, 8, 7), spacing=6)
        tabs_row = hbox(margins=0, spacing=6)
        self.back_to_connections = button('←', self.leave_browser_workspace, name='browserBack')
        self.back_to_connections.setToolTip('Revenir aux connexions')
        tabs_row.addWidget(self.back_to_connections)
        self.browser_session_tabs = []
        self.browser_tabs_row = tabs_row
        tabs_row.addStretch()
        toolbar.addLayout(tabs_row)
        address_row = hbox(margins=0, spacing=6)
        lock = text_label('●', 'browserLock')
        address_row.addWidget(lock)
        self.browser_address = text_label('impots.gouv.fr', 'browserAddress')
        self.browser_address.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        address_row.addWidget(self.browser_address, 1)
        self.browser_refresh = button('↻', self.reload_browser, name='browserTool')
        self.browser_refresh.setToolTip('Actualiser la page')
        address_row.addWidget(self.browser_refresh)
        self.browser_close = button('Fermer', self.close_current_session, name='browserTool')
        self.browser_close.setToolTip('Fermer cette session')
        address_row.addWidget(self.browser_close)
        self.browser_zoom_out = button('−', lambda: self.adjust_browser_zoom(-0.05), name='browserTool')
        address_row.addWidget(self.browser_zoom_out)
        self.browser_zoom_label = button('100 %', self.reset_browser_zoom, name='browserZoom')
        self.browser_zoom_label.setToolTip('Revenir au zoom automatique')
        address_row.addWidget(self.browser_zoom_label)
        self.browser_zoom_in = button('+', lambda: self.adjust_browser_zoom(0.05), name='browserTool')
        address_row.addWidget(self.browser_zoom_in)
        toolbar.addLayout(address_row)
        self.browser_toolbar.setVisible(False)
        shell.addWidget(self.browser_toolbar)
        self.browser_tabs = QTabWidget(self.browser_shell)
        self.browser_tabs.setObjectName('browserTabs')
        self.browser_tabs.setMinimumHeight(480)
        self.browser_tabs.tabBar().hide()
        self.browser_hosts = []
        self.browser_scrolls = []
        self.browser_canvases = []
        self.browser_tabs.setVisible(False)
        shell.addWidget(self.browser_tabs, 1)
        self.browser_shell.setVisible(False)
        layout.addWidget(self.browser_shell, 1)
        self.pages.addWidget(page)
        self.ensure_session_slots(SESSIONS_AT_LEAST)

    def ensure_session_slots(self, count):
        """A window, a tab and a state for each session; more appear when there are more expert accounts."""
        for index in range(len(self.browser_hosts), count):
            tab = button(f'Session {index + 1} · Non ouverte', lambda _=False, i=index: self.show_session(i))
            tab.setObjectName('browserSessionTab')
            tab.setCheckable(True)
            tab.setFixedWidth(166)
            self.browser_tabs_row.insertWidget(1 + index, tab)
            self.browser_session_tabs.append(tab)
            self.session_statuses.append('Non ouverte')
            scroll = QScrollArea(parent=self.browser_tabs)
            scroll.setObjectName('browserScroll')
            scroll.setWidgetResizable(False)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            canvas = QWidget(scroll)
            canvas.setObjectName('browserCanvas')
            canvas.resize(BROWSER_CANVAS_MIN_WIDTH + BROWSER_END_MARGIN, 480)
            host = EmbeddedWindowHost(parent=canvas)
            host.setGeometry(0, 0, BROWSER_CANVAS_MIN_WIDTH, 480)
            scroll.setWidget(canvas)
            self.browser_tabs.addTab(scroll, '')
            self.browser_scrolls.append(scroll)
            self.browser_canvases.append(canvas)
            self.browser_hosts.append(host)
            self.logged_session_events.append(None)
            self.last_live_session_text.append(None)
            self.browser_urls.append('impots.gouv.fr')
            self.browser_zoom_offsets.append(0.0)
            self.browser_zoom_sent.append(None)

    def planned_sessions(self):
        """How many sessions the open campaign uses; a new campaign takes the number the wizard holds."""
        batch = self.selected_batch()
        return self.store.batch(batch)['sessions'] if batch is not None else self.session_count.value()

    def needed_sessions(self):
        """Four sessions, or one per expert account when the cabinet has more."""
        return max(SESSIONS_AT_LEAST, len(self.usable_accounts()))

    def usable_accounts(self):
        return [account for account in self.accounts.list_accounts() if not self.accounts.login_blocked(account['id'])]

    def sync_sessions_to_accounts(self):
        """Give the sessions the expert accounts in turn, and open as many sessions as needed; nobody chooses either."""
        usable = self.usable_accounts()
        count = self.needed_sessions()
        self.ensure_session_slots(count)
        if not self.busy:
            for index in range(count):
                account = usable[index % len(usable)]['id'] if usable else None
                # A session already connected keeps its account until it is closed.
                if self.browser_hosts[index].handle is not None and self.connected_sessions.get(index, False):
                    continue
                if (self.accounts.account_for_session(index) or {}).get('id') != account:
                    self.accounts.assign(index, account)
            batch = self.selected_batch()
            if batch is not None and self.store.batch(batch)['sessions'] != count:
                self.store.update_batch_sessions(batch, count)
        self.apply_session_count(count)
        return count

    def refresh_account_choices(self):
        self.rebuild_account_rows(self.accounts.list_accounts())
        self.sync_sessions_to_accounts()
        if hasattr(self, 'connection_panel'):
            self.update_collect_page()

    def rebuild_account_rows(self, accounts):
        for row in self.account_rows:
            row.hide()
            self.account_rows_box.removeWidget(row)
            row.deleteLater()
        self.account_rows = []
        self.accounts_empty.setVisible(not accounts)
        for account in accounts:
            # The whole row opens the account; deleting it is done from there.
            row = ClickableRow(lambda a=account['id']: self.edit_expert_account(a))
            row.setObjectName('accountRow')
            row.account_id = account['id']
            line = hbox(row, margins=(20, 14, 20, 14), spacing=14)
            names = vbox(spacing=1)
            label = text_label(account['label'], 'accountName')
            names.addWidget(label)
            login = text_label(account['login'], 'muted')
            names.addWidget(login)
            line.addLayout(names, 1)
            row.state = text_label('', 'readingBadge')
            line.addWidget(row.state)
            try:
                row.has_password = bool(self.accounts.password(account['id']))
            except Exception:
                row.has_password = False
            row.edit_button = button('Modifier', lambda _=False, a=account['id']: self.edit_expert_account(a))
            line.addWidget(row.edit_button)
            for child in (label, login, row.state):
                child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self.account_rows_box.addWidget(row)
            self.account_rows.append(row)
        self.update_account_states()

    def account_state(self, account):
        """Say whether an expert account can connect on its own."""
        if self.accounts.login_blocked(account):
            return 'Identifiants refusés · à corriger', 'warning'
        return 'Mot de passe enregistré', 'success'

    def update_account_states(self):
        for row in self.account_rows:
            text, tone = self.account_state(row.account_id)
            if not row.has_password:
                text, tone = 'Mot de passe à saisir sur ce poste', 'attention'
            row.state.setText(text)
            restyle(row.state, tone=tone)
            # The account to correct stands out: its button is the main one.
            row.edit_button.setText('Corriger' if tone in ('warning', 'attention') else 'Modifier')
            row.edit_button.setObjectName('primary' if tone in ('warning', 'attention') else '')
            restyle(row.edit_button)
            row.edit_button.setEnabled(not self.busy)
            row.setEnabled(not self.busy)

    def add_expert_account(self):
        dialog = AccountDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.accounts.add(dialog.label_input.text(), dialog.login_input.text(), dialog.password.text())
            self.refresh_account_choices()
            self.diagnostics.append('account_added')
            self.say('Compte enregistré.')
        except Exception:
            self.problem('Compte non enregistré. Vérifiez les champs et l’accès au coffre Windows.')

    def edit_expert_account(self, account_id):
        account = next((item for item in self.accounts.list_accounts() if item['id'] == account_id), None)
        if account is None:
            return
        dialog = AccountDialog(account, parent=self, refused=self.accounts.login_blocked(account_id))
        answer = dialog.exec()
        if answer == AccountDialog.DELETE:
            self.delete_expert_account(account_id)
            return
        if answer != QDialog.DialogCode.Accepted:
            return
        try:
            self.accounts.update(
                account_id, dialog.label_input.text(), dialog.login_input.text(), dialog.password.text() or None
            )
            self.refresh_account_choices()
            self.diagnostics.append('account_updated')
            self.say('Compte mis à jour.')
        except Exception:
            self.problem('Modification impossible. Vérifiez les champs et l’accès au coffre Windows.')

    def delete_expert_account(self, account_id):
        account = next((item for item in self.accounts.list_accounts() if item['id'] == account_id), None)
        if account is None:
            return
        if not self.confirm_account_deletion(account['label']):
            return
        try:
            self.accounts.remove(account_id)
            self.refresh_account_choices()
            self.say('Compte supprimé.')
            self.diagnostics.append('account_removed')
        except Exception:
            self.problem('Suppression impossible. Vérifiez l’accès au coffre Windows.')

    def confirm_account_deletion(self, name):
        box = QMessageBox(
            QMessageBox.Icon.Warning, 'Supprimer le compte', f'Supprimer le compte « {name} » ?', parent=self
        )
        box.setInformativeText('Son mot de passe est effacé du coffre Windows. La collecte ne s’y connectera plus.')
        delete = box.addButton('Supprimer le compte', QMessageBox.ButtonRole.DestructiveRole)
        cancel = box.addButton('Annuler', QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cancel)
        box.setEscapeButton(cancel)
        box.exec()
        return box.clickedButton() is delete

    def apply_session_count(self, count):
        count = max(1, int(count))
        self.ensure_session_slots(count)
        self.session_count.blockSignals(True)
        self.session_count.setValue(count)
        self.session_count.blockSignals(False)
        for index in range(len(self.browser_hosts)):
            visible = index < count
            self.browser_tabs.setTabVisible(index, visible)
            self.browser_session_tabs[index].setVisible(visible)

    def navigate(self, index):
        # The expert accounts live in the settings now; the connections page only hosts the session windows.
        if index == 1:
            index = SETTINGS_PAGE
        if self.browser_workspace_active:
            self.set_browser_workspace(False)
        self.pages.setCurrentIndex(index)
        if index == 0:
            self.show_campaign_library()
        elif index == 2:
            self.title.setText('Campagne')
            self.source_summary.setText('')
            self.header_actions.setVisible(False)
        elif index == 3:
            self.title.setText('Courriels aux clients')
            self.source_summary.setText('')
            self.header_actions.setVisible(False)
            self.refresh_mail_candidates()
            self.prepare_mail_application()
        elif index == COLLECT_PAGE:
            self.title.setText('Collecte')
            self.source_summary.setText('')
            self.header_actions.setVisible(False)
            self.refresh_table()
        else:
            self.title.setText('Réglages')
            self.source_summary.setText('')
            self.header_actions.setVisible(False)
            self.refresh_settings_page()
        self.nav[0].setChecked(index == 0)
        self.nav[1].setChecked(index == SETTINGS_PAGE)
        self.update_campaign_header(index if index in (2, 3, COLLECT_PAGE) else -1)
        self.update_activity_bar()
        if hasattr(self, 'result_detail_card'):
            self.place_dossier_drawer()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'rail'):
            self.apply_responsive_layout()
        if hasattr(self, 'all_columns_button'):
            QTimer.singleShot(0, self.update_all_columns_label)
        if hasattr(self, 'browser_hosts'):
            QTimer.singleShot(0, self.sync_browser_hosts)
        if hasattr(self, 'toast') and self.toast.isVisible():
            self.toast.move(
                max(8, self.workspace.width() - self.toast.width() - 20),
                max(8, self.workspace.height() - self.toast.height() - 20),
            )

    def selected_batch(self):
        return self.campaigns.currentData()

    def selected_campaign(self):
        batch = self.selected_batch()
        return self.store.batch_campaigns(batch)[0]['id'] if batch is not None else None

    def refresh_campaigns(self, select=None):
        self.campaigns.blockSignals(True)
        self.campaigns.clear()
        for b in self.store.batches():
            years = ', '.join(str(c['year']) for c in self.store.batch_campaigns(b['id']))
            self.campaigns.addItem(f'{b["name"]} · {years}', b['id'])
            self.campaigns.setItemData(
                self.campaigns.count() - 1,
                f'{b["name"]} · CFE {years} · créée le {b["created"][:10]}',
                Qt.ItemDataRole.ToolTipRole,
            )
        if select is not None:
            self.campaigns.setCurrentIndex(self.campaigns.findData(select))
        self.campaigns.blockSignals(False)
        self.campaign_changed()
        if hasattr(self, 'campaign_list_layout'):
            self.refresh_campaign_cards()
        self.refresh_campaign_nav()

    def campaign_changed(self):
        batch = self.selected_batch()
        self.page_number = 0
        self.rendered = None
        self.year_filter.blockSignals(True)
        self.year_filter.clear()
        self.year_filter.addItem('Toutes les années')
        if batch is not None:
            annual = self.store.batch_campaigns(batch)
            b = self.store.batch(batch)
            self.campaign_name.setText(b['name'])
            self.year.setValue(annual[0]['year'])
            self.years.setText(', '.join(str(c['year']) for c in annual) if len(annual) > 1 else '')
            self.set_year_mode('multiple' if len(annual) > 1 else 'single')
            self.output.setText(annual[0]['output'])
            self.sync_sessions_to_accounts()
            self.year_filter.addItems([str(c['year']) for c in annual])
        self.year_filter.blockSignals(False)
        self.refresh_collaborator_filter()
        self.refresh_table()
        self.refresh_mail_candidates()
        self.update_buttons()
        current = self.pages.currentIndex()
        self.update_campaign_header(current if current in (1, 2, 3, COLLECT_PAGE) else -1)

    def load_portfolio(self, path):
        check = check_portfolio(path)
        if not check.dossiers:
            raise ValueError('Le portefeuille ne contient aucun dossier utilisable.\n\n' + _issue_lines(check.issues))
        self.portfolio = check.dossiers
        self.portfolio_issues = check.issues
        self.portfolio_name = Path(path).stem
        self.portfolio_file = Path(path).name
        self.update_campaign_source_view()
        label = f'{len(self.portfolio):,} dossiers · {Path(path).name}'.replace(',', ' ')
        emails = sum(bool(dossier.email) for dossier in self.portfolio)
        label += (
            f' · {_count(emails, "adresse e-mail", "adresses e-mail")}'
            if emails
            else ' · aucune adresse e-mail (à ajouter dans Courriels)'
        )
        if check.ignored:
            label += f' · {_count(len(check.ignored), "ligne ignorée", "lignes ignorées")}'
        if check.warnings:
            label += f' · {len(check.warnings)} SIREN à vérifier'
        self.portfolio_label.setText(label)

    def clear_portfolio(self):
        self.portfolio = []
        self.portfolio_issues = []
        self.update_campaign_source_view()

    def choose_portfolio(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Importer le portefeuille', '', 'Portefeuilles (*.csv *.xlsx)')
        if path:
            try:
                self.load_portfolio(path)
            except Exception as e:
                self.problem(
                    str(e) if isinstance(e, ValueError) else 'Le fichier n’a pas pu être lu. Vérifiez son format.'
                )

    def choose_output(self):
        path = QFileDialog.getExistingDirectory(self, 'Choisir le dossier de sortie', self.output.text())
        if path:
            self.output.setText(path)

    def create_campaign(self):
        if not self.output.text().strip():
            self.problem('Choisissez un dossier de sortie.')
            return
        try:
            years = (
                parse_years(self.years.text(), self.year.value())
                if self.year_mode == 'multiple' or self.years.text().strip()
                else [self.year.value()]
            )
            if self.year_mode == 'multiple' and not self.years.text().strip():
                raise ValueError('Indiquez les années à collecter.')
            directory = Path(self.output.text()).resolve()
            directory.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=directory):
                pass
            name = self.campaign_name.text().strip() or self.suggested_campaign_name()
            batch = self.store.create_batch(years, directory, self.portfolio, self.needed_sessions(), name)
            self.refresh_campaigns(batch)
            self.say('Campagne créée.')
            self.open_campaign(batch, COLLECT_PAGE)
        except ValueError as e:
            self.problem(str(e))
        except Exception:
            self.problem('Impossible de créer la campagne. Vérifiez les droits d’écriture dans la sortie.')

    def update_buttons(self):
        has = self.selected_batch() is not None
        count = self.planned_sessions()
        connected = self.browser_ready and all(self.connected_sessions.get(i, False) for i in range(count))
        matches = not self.worker or self.worker.count == count
        self.run_button.setEnabled(has and connected and matches and not self.busy)
        self.login_button.setEnabled(not self.busy)
        for b in [
            self.campaigns,
            self.create_button,
            self.import_button,
            self.clear_button,
            self.campaign_name,
            self.year,
            self.years,
            self.output,
            self.output_button,
            self.session_count,
            self.add_account_button,
        ]:
            b.setEnabled(not self.busy)
        self.update_account_states()
        self.update_run_hint()
        self.update_user_block()
        self.open_button.setEnabled(has)
        if hasattr(self, 'campaign_nav_items') and self.selected_batch() in self.campaign_nav_items and self.busy:
            self.campaign_nav_items[self.selected_batch()].set_badge('en cours', 'running')
        for index, tab in enumerate(self.browser_session_tabs):
            tab.setVisible(index < count)
        if hasattr(self, 'result_retry_button'):
            self.update_result_actions()
        self.update_collect_page()

    def run_block_reason(self):
        """Why the collection cannot start yet, in the words of the next thing to do; empty when it can."""
        batch = self.selected_batch()
        if batch is None:
            return 'Ouvrez une campagne.'
        if self.busy:
            return ''
        count = self.store.batch(batch)['sessions']
        if not self.browser_ready:
            return 'Connectez-vous d’abord.'
        connected = sum(bool(self.connected_sessions.get(index, False)) for index in range(count))
        if connected < count:
            return 'Connexion en cours…'
        if self.worker and self.worker.count != count:
            return 'Connexion en cours…'
        return ''

    def update_run_hint(self):
        reason = '' if self.run_button.isEnabled() else self.run_block_reason()
        self.run_button.setToolTip(reason or 'Lancer la collecte des avis CFE pour cette campagne')
        self.run_hint.setText(reason)
        self.run_hint.setVisible(bool(reason) and not (self.run_button.isHidden() and self.login_button.isHidden()))

    def collection_state(self, value):
        was_busy = self.busy
        self.busy = value
        if value != was_busy:
            batch = self.selected_batch()
            self.diagnostics.append('campaign_started' if value else 'campaign_finished', batch=batch)
            if value:
                self.collection_started_at = datetime.now()
                self.collect_done_at_start = (self.collect_stats or {}).get('done', 0)
            if batch is not None:
                if value:
                    self.status.setText(f'Collecte en cours · {self.store.batch(batch)["name"]}')
                    self.toast.announce('Collecte lancée.', tone='info')
                else:
                    self.notify_collection_result(batch)
        self.run_button.setText('Collecte en cours…' if value else 'Lancer la collecte')
        self.update_activity_bar()
        self.refresh_table()
        self.update_buttons()
        if not value:
            self.refresh_campaign_nav()
        current = self.pages.currentIndex()
        if current == COLLECT_PAGE:
            self.update_campaign_header(current)

    def notify_collection_result(self, batch):
        campaigns = self.store.batch_campaigns(batch)
        rows = [row for campaign in campaigns for row in self.store.dossiers(campaign['id'])]
        notices = [notice for campaign in campaigns for notice in self.store.notices(campaign['id'])]
        counts = summarize_dossiers(rows)
        classified = [notice for notice in notices if notice['status'] == 'TERMINE']
        reviews = sum(review_label(notice) not in SETTLED_LABELS for notice in classified)
        statuses = {campaign['status'] for campaign in campaigns}
        if 'PAUSE' in statuses:
            self.toast.announce('Collecte en pause', tone='info')
        elif 'CONNEXION_REQUISE' in statuses:
            self.warn(
                'Collecte interrompue · connexion requise',
                'Reconnectez les comptes experts, puis reprenez la campagne.',
            )
        elif not statuses <= {'TERMINE', 'AVEC_ERREURS'}:
            self.warn('Collecte à reprendre')
        else:
            follow = [
                part
                for part in (
                    _count(counts['retry'], 'à relancer', 'à relancer'),
                    _count(counts['no_access'], 'sans habilitation', 'sans habilitation'),
                    _count(reviews, 'lecture à vérifier', 'lectures à vérifier'),
                )
                if not part.startswith('0 ')
            ]
            title = f'Collecte terminée · {_count(len(classified), "avis classé", "avis classés")}'
            if follow:
                self.warn(title, ' · '.join(follow))
            else:
                self.toast.announce(
                    title,
                    detail=f'{_count(counts["absent"], "dossier sans avis", "dossiers sans avis")}, preuve dans chaque dossier.'
                    if counts['absent']
                    else '',
                )

    def start_collection(self, only_errors=False):
        if self.worker and self.worker.isRunning() and self.run_button.isEnabled():
            self.collection_state(True)
            self.navigate(COLLECT_PAGE)
            self.worker.submit_campaign(self.selected_batch(), bool(only_errors))

    def selected_error_jobs(self):
        return {
            (self.result_rows[index.row()][0]['id'], self.result_rows[index.row()][1]['siren'])
            for index in self.table.selectionModel().selectedRows()
            if self.result_rows[index.row()][1]['status'] == 'ERREUR'
        }

    def start_selected_retry(self, selected_override=None):
        if not self.worker or not self.worker.isRunning() or not self.run_button.isEnabled():
            return
        selected = set(selected_override) if selected_override is not None else self.selected_error_jobs()
        if not selected:
            self.problem('Sélectionnez au moins un dossier « À relancer » dans le tableau.')
            return
        campaign = self.store.batch_campaigns(self.selected_batch())[0]
        capture_dir = Path(campaign['output']) / '_Captures' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        self.status.setText(f'{len(selected)} dossier(s) sélectionné(s) · captures dans {capture_dir}')
        self.collection_state(True)
        self.navigate(COLLECT_PAGE)
        self.worker.submit_campaign(self.selected_batch(), True, selected, capture_dir)

    def pause_collection(self):
        if self.worker:
            self.worker.stop.set()
            self.status.setText('Pause demandée. Chaque avis en cours sera enregistré avant l’arrêt.')

    def export(self):
        try:
            paths = export_batch_actions(self.store, self.selected_batch())
            quantity = len(paths)
            self.status.setText(
                f'{quantity} classeur'
                + ('' if quantity == 1 else 's')
                + ' enregistré'
                + ('' if quantity == 1 else 's')
                + ', un par collaborateur avec toutes les années.'
            )
            if paths:
                target = paths[0] if len(paths) == 1 else Path(self.store.campaign(self.selected_campaign())['output'])
                self.toast.announce(
                    f'{quantity} classeur'
                    + ('' if quantity == 1 else 's')
                    + ' Excel créé'
                    + ('' if quantity == 1 else 's')
                    + '.',
                    target,
                )
            else:
                self.toast.announce('Aucun dossier à exporter dans cette campagne.', tone='info')
        except ValueError as error:
            self.warn('Export impossible', str(error))
        except Exception:
            self.warn('Export impossible', 'Vérifiez le dossier de sortie et fermez les classeurs ouverts.')

    def open_output(self):
        if self.selected_campaign() is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.store.campaign(self.selected_campaign())['output']))

    def problem(self, text):
        QMessageBox.information(self, APP_NAME, text)

    def closeEvent(self, event):
        self.closing = True
        if self.worker and self.worker.isRunning():
            for host in self.browser_hosts:
                host.release()
            self.worker.request_shutdown()
            self.status.setText('Fermeture après enregistrement des avis en cours…')
            event.ignore()
            self.setEnabled(False)
            self.worker.finished.connect(self.close)
            return
        self.close_mail_application()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    # The interface is drawn light: a Windows dark mode would otherwise darken the fields the style leaves alone.
    app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    window = MainWindow()
    window.showMaximized()
    if '--connect' in sys.argv:
        QTimer.singleShot(0, window.open_browser)
    return app.exec()
