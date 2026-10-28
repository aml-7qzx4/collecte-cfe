from collections import deque
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QScrollArea, QSizePolicy, QVBoxLayout

from .situations import dossier_situation
from .ui_components import SegmentBar
from .ui_kit import button, hbox, make_frame, make_widget, restyle, text_label, vbox
from .ui_widgets import when

COLLECT_PAGE = 5
# Sessions opened at least; with more expert accounts, one session per account.
SESSIONS_AT_LEAST = 4
OPENING = ('Ouverture…', 'Connexion en cours…', 'Reconnexion en cours…')
# Colours of the progress bar: notices found, no notice, to retry.
SEGMENT_COLORS = ('#1A6A80', '#9AA6C0', '#B01F48')
TODO_LIMIT = 8


def connection_summary(statuses, busy):
    """Where the connection to impots.gouv.fr stands, all sessions together: (tone, title, line, action, session).

    The action names what the button does; the session is the one whose window needs the collaborator."""

    def first(*wanted):
        return next((index for index, status in enumerate(statuses) if status in wanted), None)

    refused = first('Connexion refusée')
    if refused is not None:
        return (
            'error',
            'Identifiants refusés par impots.gouv.fr',
            'Corrigez le compte expert dans Réglages, puis reconnectez-vous.',
            'settings',
            refused,
        )
    waiting = first('CAPTCHA à vérifier', 'Connexion à vérifier')
    if waiting is not None:
        captcha = statuses[waiting] == 'CAPTCHA à vérifier'
        return (
            'warning',
            'CAPTCHA à saisir' if captcha else 'Connexion à terminer',
            'Saisissez le code dans la fenêtre : la connexion reprend ensuite.'
            if captcha
            else 'Terminez la connexion dans la fenêtre.',
            'window',
            waiting,
        )
    expired = first('À reconnecter', 'Erreur de connexion')
    if expired is not None:
        return ('warning', 'Connexion expirée', 'Reconnectez-vous pour reprendre la collecte.', 'reconnect', expired)
    working = [status.split(' · ')[0] for status in statuses if ' · ' in status and not status.startswith('Connectée')]
    if statuses and all(status.startswith('Connectée') or ' · ' in status for status in statuses):
        if busy:
            return (
                'working',
                'Collecte en cours',
                'En cours : ' + ', '.join(working) if working else 'En attente des dossiers suivants.',
                '',
                None,
            )
        return ('connected', 'Connecté à impots.gouv.fr', 'Prêt pour la collecte.', '', None)
    if any(status in OPENING or status.startswith('Connectée') for status in statuses):
        return (
            'working',
            'Connexion en cours…',
            'La connexion se fait avec les comptes experts enregistrés.',
            '',
            None,
        )
    return (
        'idle',
        'Pas encore connecté',
        '« Se connecter » ouvre impots.gouv.fr avec les comptes experts enregistrés.',
        '',
        None,
    )


class CollectPageMixin:
    """The collection of the open campaign: how far it is, the connection, what is left and the last events."""

    def build_collect_page(self):
        self.live_events = deque(maxlen=40)
        self.collection_started_at = None
        self.collect_stats = {}
        self.collect_done_at_start = 0
        page = make_widget('page')
        layout = vbox(page, margins=0, spacing=14)
        # The page is never smaller than its content: a short window scrolls it instead of drawing cards over each other.
        layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetMinimumSize)

        progress = make_frame('card')
        progress_box = vbox(progress, margins=(20, 18, 20, 18), spacing=12)
        top = hbox(spacing=10)
        self.collect_done = text_label('0', 'collectBig')
        top.addWidget(self.collect_done, 0, Qt.AlignmentFlag.AlignBottom)
        self.collect_total = text_label('', 'collectTotal')
        top.addWidget(self.collect_total, 0, Qt.AlignmentFlag.AlignBottom)
        top.addStretch()
        self.collect_when = text_label('', 'muted')
        top.addWidget(self.collect_when, 0, Qt.AlignmentFlag.AlignBottom)
        progress_box.addLayout(top)
        self.collect_progress = SegmentBar(10)
        progress_box.addWidget(self.collect_progress)
        counts = hbox(spacing=18)
        self.collect_counts = {}
        for key, label in (
            ('notices', 'avec avis'),
            ('absent', 'sans avis'),
            ('retry', 'à relancer'),
            ('pending', 'restants'),
        ):
            value = text_label('', 'collectCount')
            value.setProperty('kind', key)
            counts.addWidget(value)
            self.collect_counts[key] = (value, label)
        counts.addStretch()
        progress_box.addLayout(counts)
        layout.addWidget(progress)

        # One block for the connection to impots.gouv.fr, whatever the number of sessions behind it.
        self.connection_panel = make_frame('connectionPanel')
        panel = hbox(self.connection_panel, margins=(20, 16, 20, 16), spacing=16)
        self.connection_dot = text_label('', 'connectionDot')
        self.connection_dot.setFixedSize(10, 10)
        dot = vbox(margins=(0, 6, 0, 0))
        dot.addWidget(self.connection_dot)
        dot.addStretch()
        panel.addLayout(dot)
        self.connection_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        texts = vbox(spacing=3)
        self.connection_title = text_label('', 'sessionTitle')
        texts.addWidget(self.connection_title)
        self.connection_text = text_label('', 'muted', wrap=True)
        texts.addWidget(self.connection_text)
        self.connection_accounts = text_label('', 'fieldHint', wrap=True)
        texts.addWidget(self.connection_accounts)
        panel.addLayout(texts, 1)
        self.connection_action = button('', self.run_connection_action, name='primary')
        panel.addWidget(self.connection_action, 0, Qt.AlignmentFlag.AlignVCenter)
        self.connection_window = button('Afficher le navigateur', self.show_first_window, name='linkStrong')
        panel.addWidget(self.connection_window, 0, Qt.AlignmentFlag.AlignVCenter)
        self.connection_target = None
        layout.addWidget(self.connection_panel)

        lower = hbox(spacing=14)
        todo = make_frame('card')
        todo_box = vbox(todo, margins=0, spacing=0)
        todo_head = text_label('À traiter après la collecte', 'eventsHeading')
        todo_box.addWidget(todo_head)
        self.collect_todo_box = vbox(spacing=0)
        todo_box.addLayout(self.collect_todo_box)
        self.collect_todo_empty = text_label('Rien à traiter.', 'muted')
        self.collect_todo_empty.setContentsMargins(18, 12, 18, 14)
        todo_box.addWidget(self.collect_todo_empty)
        todo_box.addStretch()
        self.collect_todo_rows = []
        lower.addWidget(todo, 1)
        events = make_frame('liveFeed')
        events_box = vbox(events, margins=(18, 14, 18, 14), spacing=6)
        heading = text_label('En direct', 'liveFeedTitle')
        events_box.addWidget(heading)
        self.collect_events_box = vbox(spacing=4)
        events_box.addLayout(self.collect_events_box)
        self.collect_events_empty = text_label('Aucun événement.', 'liveFeedLine')
        events_box.addWidget(self.collect_events_empty)
        events_box.addStretch()
        self.collect_event_rows = []
        lower.addWidget(events, 1)
        layout.addLayout(lower)
        layout.addStretch()

        self.collect_scroll = QScrollArea()
        self.collect_scroll.setObjectName('pageScroll')
        self.collect_scroll.setWidgetResizable(True)
        self.collect_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.collect_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.collect_scroll.setWidget(page)
        self.pages.addWidget(self.collect_scroll)

    def run_connection_action(self):
        action, session = self.connection_target or ('', None)
        if action == 'settings':
            self.navigate(1)
        elif action == 'window' and session is not None:
            self.show_session(session)
        elif action == 'reconnect':
            self.open_browser()

    def show_first_window(self):
        opened = next((index for index, host in enumerate(self.browser_hosts) if host.handle is not None), None)
        if opened is not None:
            self.show_session(opened)

    def update_connection_panel(self, count):
        statuses = self.session_statuses[:count]
        tone, title, text, action, session = connection_summary(statuses, self.busy)
        self.connection_title.setText(title)
        self.connection_text.setText(text)
        for widget in (self.connection_panel, self.connection_dot):
            restyle(widget, tone=tone)
        self.connection_target = (action, session)
        self.connection_action.setText(
            {'settings': 'Corriger le compte', 'window': 'Ouvrir la fenêtre', 'reconnect': 'Se reconnecter'}.get(
                action, ''
            )
        )
        self.connection_action.setVisible(bool(action))
        self.connection_window.setVisible(
            not action and any(host.handle is not None for host in self.browser_hosts[:count])
        )
        names = [account['label'] for account in self.usable_accounts()]
        self.connection_accounts.setText(
            ('Compte expert : ' if len(names) == 1 else 'Comptes experts : ') + ', '.join(names)
            if names
            else 'Aucun compte expert enregistré : la connexion se fait à la main, dans la fenêtre.'
        )

    def record_live_event(self, line):
        session, text = '', line
        if line.startswith('Session ') and ' · ' in line:
            session, text = line.split(' · ', 1)
        self.live_events.appendleft((datetime.now().strftime('%H:%M:%S'), session, text))
        if hasattr(self, 'collect_events_box'):
            self.render_collect_events()

    def render_collect_events(self):
        for row in self.collect_event_rows:
            row.hide()
            self.collect_events_box.removeWidget(row)
            row.deleteLater()
        self.collect_event_rows = []
        for time, _session, text in list(self.live_events)[:12]:
            row = make_frame('liveFeedRow')
            line = hbox(row, margins=0, spacing=10)
            # Each label belongs to its row from the start: shown without a parent, it would flash as a window of its own.
            stamp = QLabel(time, row)
            stamp.setObjectName('liveFeedTime')
            line.addWidget(stamp)
            what = QLabel(text, row)
            what.setObjectName('liveFeedLine')
            what.setWordWrap(True)
            line.addWidget(what, 1)
            self.collect_events_box.addWidget(row)
            self.collect_event_rows.append(row)
        self.collect_events_empty.setVisible(not self.live_events)

    def collect_todo(self):
        """The dossiers left to handle once the collection is over: to retry, without access, or a reading to check."""
        batch = self.selected_batch()
        rows = []
        for campaign in self.store.batch_campaigns(batch) if batch is not None else []:
            notices = {}
            for notice in self.store.notices(campaign['id']):
                if notice['status'] == 'TERMINE':
                    notices.setdefault(notice['siren'], []).append(notice)
            for dossier in self.store.dossiers(campaign['id']):
                situation = dossier_situation(dossier, notices.get(dossier['siren'], []))
                if situation not in ('MANUAL', 'RETRY', 'REVIEW'):
                    continue
                if situation == 'MANUAL':
                    reason, action = (
                        f'{dossier.get("failures") or 0} échecs : à vérifier sur impots.gouv.fr',
                        'Ouvrir la fiche',
                    )
                elif dossier['status'] == 'ERREUR':
                    reason, action = dossier['error'] or 'Erreur pendant la collecte', 'Relancer'
                elif dossier['status'] == 'SANS_HABILITATION':
                    reason, action = 'Sans habilitation sur les comptes · capture rangée', 'Voir la capture'
                else:
                    reason, action = 'Lecture du montant à vérifier', 'Ouvrir l’avis'
                rows.append((campaign, dossier, reason, action))
        return rows

    def render_collect_todo(self):
        for row in self.collect_todo_rows:
            row.hide()
            self.collect_todo_box.removeWidget(row)
            row.deleteLater()
        self.collect_todo_rows = []
        todo = self.collect_todo()
        for campaign, dossier, reason, action in todo[:TODO_LIMIT]:
            row = make_frame('todoRow')
            line = hbox(row, margins=(18, 12, 18, 12), spacing=12)
            texts = vbox(spacing=1)
            name = text_label(dossier['name'], 'accountName')
            texts.addWidget(name)
            why = text_label(reason, 'muted', wrap=True)
            texts.addWidget(why)
            line.addLayout(texts, 1)
            key = (campaign['id'], dossier['siren'])
            link = button(
                action,
                (lambda _=False, k=key: self.start_selected_retry({k}))
                if action == 'Relancer'
                else (lambda _=False, siren=dossier['siren']: self.show_mail_dossier(siren)),
            )
            link.setObjectName('linkStrong')
            link.setEnabled(action != 'Relancer' or self.run_button.isEnabled() or not self.busy)
            if action == 'Relancer' and not self.run_button.isEnabled():
                link.setToolTip('Connectez-vous pour relancer ce dossier.')
            line.addWidget(link)
            self.collect_todo_box.addWidget(row)
            self.collect_todo_rows.append(row)
        if len(todo) > TODO_LIMIT:
            more = make_frame('todoRow')
            more_line = hbox(more, margins=(18, 10, 18, 12))
            link = button(
                f'Voir les {len(todo)} dossiers à traiter', lambda: (self.navigate(2), self.set_result_filter('TODO'))
            )
            link.setObjectName('linkStrong')
            more_line.addWidget(link)
            more_line.addStretch()
            self.collect_todo_box.addWidget(more)
            self.collect_todo_rows.append(more)
        self.collect_todo_empty.setVisible(not todo)

    def update_collect_page(self):
        if not hasattr(self, 'connection_panel'):
            return
        stats = self.collect_stats or {}
        total = stats.get('total', 0)
        done = stats.get('done', 0)
        self.collect_done.setText(f'{done:,}'.replace(',', ' '))
        self.collect_total.setText(f'dossiers traités sur {total}' if total else 'dossier à collecter pour l’instant')
        if self.busy and self.collection_started_at is not None:
            text = f'Commencée à {self.collection_started_at:%H:%M}'
            elapsed = (datetime.now() - self.collection_started_at).total_seconds()
            handled = done - self.collect_done_at_start
            if handled > 0 and total > done:
                minutes = max(1, round(elapsed / handled * (total - done) / 60))
                text += f' · environ {minutes} min restante' + ('s' if minutes > 1 else '')
            self.collect_when.setText(text)
        else:
            checked = stats.get('checked', '')
            self.collect_when.setText(f'Dernier passage le {when(checked)}' if checked else 'Pas encore lancée')
        self.collect_progress.set_segments(
            [
                (stats.get('finished', 0), SEGMENT_COLORS[0]),
                (stats.get('absent', 0), SEGMENT_COLORS[1]),
                (stats.get('retry', 0), SEGMENT_COLORS[2]),
            ],
            total,
        )
        for key, (value, label) in self.collect_counts.items():
            number = stats.get('finished', 0) if key == 'notices' else stats.get(key, 0)
            value.setText(f'<b>{number}</b> {label}')
        self.update_connection_panel(self.planned_sessions())
        if self.pages.currentIndex() == COLLECT_PAGE:
            self.render_collect_todo()
