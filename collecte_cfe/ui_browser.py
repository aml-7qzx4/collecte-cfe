from urllib.parse import urlparse

from PySide6.QtCore import QTimer

from .sessions import BrowserWorker
from .ui_collect import COLLECT_PAGE

BROWSER_CANVAS_MIN_WIDTH = 1200
BROWSER_END_MARGIN = 32


def browser_canvas_width(viewport_width):
    return max(BROWSER_CANVAS_MIN_WIDTH, max(1, int(viewport_width)))


def browser_canvas_extent(viewport_width):
    width = browser_canvas_width(viewport_width)
    return width + (BROWSER_END_MARGIN if width > int(viewport_width) else 0)


def responsive_browser_zoom(width, adjustment=0.0):
    automatic = min(1.0, max(0.6, float(width) / 1200.0))
    return round(min(1.0, max(0.5, automatic + float(adjustment))), 2)


def display_portal_url(url):
    parsed = urlparse(url or '')
    hostname = (parsed.hostname or '').lower()
    if parsed.scheme != 'https' or not (hostname == 'impots.gouv.fr' or hostname.endswith('.impots.gouv.fr')):
        return 'Navigation externe bloquée'
    path = (parsed.path or '/').split(';', 1)[0]
    return f'{hostname}{path}'


class BrowserWindowMixin:
    def set_browser_workspace(self, active):
        active = bool(active and any(host.handle is not None for host in self.browser_hosts))
        # Coming back from a session window returns to the page it was opened from.
        if active and not self.browser_workspace_active:
            self.browser_return_page = self.pages.currentIndex()
        self.browser_workspace_active = active
        self.rail.setVisible(True)
        self.header_panel.setVisible(not active)
        self.campaign_panel.setVisible(not active)
        self.campaign_back.setVisible(False)
        self.update_activity_bar()
        self.campaign_tabs.setVisible(False)
        self.browser_shell.setVisible(active)
        self.browser_toolbar.setVisible(active)
        self.browser_tabs.setVisible(active)
        self.body.setContentsMargins(*(12, 10, 12, 10) if active else (24, 20, 24, 16))
        self.body.setSpacing(8 if active else 12)
        if hasattr(self, 'browser_geometry_timer'):
            self.browser_geometry_timer.start() if active else self.browser_geometry_timer.stop()
            self.browser_focus_timer.start() if active else self.browser_focus_timer.stop()
        self.browser_was_foreground = False
        if active:
            self.pages.setCurrentIndex(1)
            for b in self.nav:
                b.setChecked(False)
            QTimer.singleShot(0, self.sync_browser_hosts)

    def leave_browser_workspace(self):
        self.set_browser_workspace(False)
        self.navigate(getattr(self, 'browser_return_page', COLLECT_PAGE))
        for b in self.browser_session_tabs:
            b.setChecked(False)

    def sync_browser_hosts(self):
        for index, host in enumerate(self.browser_hosts):
            if host.handle is not None:
                viewport = self.browser_scrolls[index].viewport()
                target_width = browser_canvas_width(viewport.width())
                canvas = self.browser_canvases[index]
                canvas_width = browser_canvas_extent(viewport.width())
                if (
                    viewport.width() > 0
                    and viewport.height() > 0
                    and (canvas.width() != canvas_width or canvas.height() != viewport.height())
                ):
                    canvas.resize(canvas_width, viewport.height())
                if (
                    viewport.width() > 0
                    and viewport.height() > 0
                    and (host.width() != target_width or host.height() != viewport.height())
                ):
                    host.resize(target_width, viewport.height())
                host.sync_geometry()
                self.queue_browser_zoom(index)

    def open_browser(self):
        count = self.sync_sessions_to_accounts()
        self.start_browser_sessions(count, 0, list(range(count)), show_browser=False)

    def start_browser_sessions(self, count, focus=0, initial_sessions=None, show_browser=True):
        count = max(1, int(count))
        self.ensure_session_slots(count)
        self.pending_browser_focus = max(0, min(count - 1, int(focus)))
        self.show_browser_after_connect = show_browser
        initial_sessions = (
            list(range(count)) if initial_sessions is None else [i for i in initial_sessions if 0 <= i < count]
        )
        if self.worker and self.worker.isRunning():
            if self.worker.count >= count:
                for index in initial_sessions:
                    if self.browser_hosts[index].handle is None:
                        self.session_updated(index, 'Ouverture…')
                        self.worker.open_session(index)
                    elif not self.connected_sessions.get(index, False):
                        self.worker.commands.put(('login', index))
                if show_browser and self.browser_hosts[self.pending_browser_focus].handle is not None:
                    self.show_session(self.pending_browser_focus)
                return
            self.worker.resize_sessions(count)
            self.status.setText(f'Adaptation à {count} session(s)…')
            return
        self.browser_ready = False
        self.connected_sessions = {}
        self.session_statuses = [
            'Ouverture…' if index < count else 'Non ouverte' for index in range(len(self.session_statuses))
        ]
        self.status.setText('Ouverture des sessions indépendantes…')
        self.update_buttons()
        self.worker = BrowserWorker(self.store, count, accounts=self.accounts, initial_sessions=initial_sessions)
        self.worker.ready.connect(self.browser_opened)
        self.worker.message.connect(self.append_live_event)
        self.worker.collecting.connect(self.collection_state)
        self.worker.session_changed.connect(self.session_updated)
        self.worker.window_ready.connect(self.browser_window_ready)
        self.worker.window_closed.connect(self.browser_window_closed)
        self.worker.sessions_resized.connect(self.browser_sessions_resized)
        self.worker.failed.connect(self.browser_failed)
        self.worker.url_changed.connect(self.browser_url_changed)
        self.worker.navigation_blocked.connect(self.external_navigation_blocked)
        self.worker.captcha_refreshed.connect(
            lambda index, count: self.diagnostics.append('captcha_refreshed', session=index, refresh=count)
        )
        self.worker.focus_requested.connect(self.focus_browser_session)
        self.worker.finished.connect(self.browser_finished)
        self.worker.start()

    def show_session(self, index):
        if 0 <= index < self.browser_tabs.count():
            self.browser_tabs.setCurrentIndex(index)
        self.set_browser_workspace(True)
        self.update_browser_chrome(index)

    def close_session(self, index):
        if self.busy or not self.worker or not self.worker.isRunning():
            return
        self.worker.close_session(index)
        self.status.setText(f'Fermeture de la session {index + 1}…')

    def close_current_session(self):
        self.close_session(self.browser_tabs.currentIndex())

    def browser_window_ready(self, index, handle):
        self.browser_hosts[index].attach(handle)
        if self.worker:
            self.worker.confirm_window_attached(index)
        if self.browser_hosts[self.pending_browser_focus].handle is not None:
            self.browser_tabs.setCurrentIndex(self.pending_browser_focus)
        else:
            self.browser_tabs.setCurrentIndex(index)
        if self.show_browser_after_connect:
            self.set_browser_workspace(True)
        self.browser_zoom_sent[index] = None
        self.update_browser_chrome(index)
        self.queue_browser_zoom(index)
        self.status.setText(f'Session {index + 1} ouverte.')

    def browser_window_closed(self, index):
        self.browser_hosts[index].release()
        self.connected_sessions.pop(index, None)
        self.browser_urls[index] = 'impots.gouv.fr'
        self.browser_zoom_sent[index] = None
        self.session_updated(index, 'Non ouverte')
        if self.browser_tabs.currentIndex() == index:
            opened = [i for i, host in enumerate(self.browser_hosts) if host.handle is not None]
            if opened:
                self.browser_tabs.setCurrentIndex(opened[0])
                self.update_browser_chrome(opened[0])
            else:
                self.set_browser_workspace(False)

    def browser_sessions_resized(self, count):
        self.browser_ready = True
        if self.show_browser_after_connect:
            self.show_session(getattr(self, 'pending_browser_focus', 0))
        self.update_buttons()

    def session_updated(self, index, text):
        self.session_statuses[index] = text
        event = (
            'session_connected'
            if text.startswith('Connectée')
            else {
                'Ouverture…': 'session_opening',
                'Connexion en cours…': 'session_connecting',
                'Reconnexion en cours…': 'session_reconnecting',
                'Connexion refusée': 'session_refused',
                'CAPTCHA à vérifier': 'session_captcha',
                'Connexion à vérifier': 'session_manual',
                'À reconnecter': 'session_reconnect_needed',
                'Non ouverte': 'session_closed',
            }.get(text)
        )
        if event and event != self.logged_session_events[index]:
            self.diagnostics.append(event, session=index)
            self.logged_session_events[index] = event
            # The sessions are not shown: the collaborator hears about the connection as a whole.
            count = self.planned_sessions()
            if event == 'session_connected' and all(
                self.connected_sessions.get(i, False) for i in range(count) if i != index
            ):
                self.toast.announce('Connecté à impots.gouv.fr.')
            elif event == 'session_refused':
                self.warn('Identifiants refusés', 'Corrigez le compte expert dans Réglages.')
            elif event in ('session_captcha', 'session_manual'):
                self.warn(
                    'CAPTCHA à saisir' if event == 'session_captcha' else 'Connexion à terminer',
                    'Ouvrez la fenêtre depuis l’onglet Collecte.',
                )
            elif event == 'session_reconnect_needed':
                self.warn('Connexion expirée', 'Reconnectez-vous depuis l’onglet Collecte.')
        if text != self.last_live_session_text[index]:
            self.last_live_session_text[index] = text
            if text in ('Connexion refusée', 'CAPTCHA à vérifier', 'Connexion à vérifier', 'À reconnecter'):
                self.append_live_event(text)
        self.browser_session_tabs[index].setText(f'Session {index + 1} · {text}')
        self.browser_session_tabs[index].setToolTip(text)
        if self.browser_tabs.currentIndex() == index:
            self.update_browser_chrome(index)
        if self.browser_hosts[index].handle is not None:
            self.browser_hosts[index].sync_geometry()
        if text.startswith('Connectée'):
            self.connected_sessions[index] = True
        else:
            self.connected_sessions[index] = False
        self.update_buttons()

    def update_browser_chrome(self, index=None):
        index = self.browser_tabs.currentIndex() if index is None else index
        for i, b in enumerate(self.browser_session_tabs):
            b.setChecked(i == index)
        if 0 <= index < len(self.browser_urls):
            self.browser_address.setText(self.browser_urls[index])
            zoom = responsive_browser_zoom(self.browser_hosts[index].width(), self.browser_zoom_offsets[index])
            self.browser_zoom_label.setText(f'{round(zoom * 100)} %')

    def browser_url_changed(self, index, url):
        self.browser_urls[index] = display_portal_url(url)
        if self.browser_tabs.currentIndex() == index:
            self.browser_address.setText(self.browser_urls[index])

    def focus_browser_session(self, index):
        if self.browser_workspace_active and self.browser_tabs.currentIndex() == index:
            self.browser_hosts[index].focus_browser()

    def maintain_browser_focus(self):
        index = self.browser_tabs.currentIndex()
        host = self.browser_hosts[index]
        active = bool(
            self.browser_workspace_active
            and host.handle is not None
            and host.application_is_foreground(int(self.winId()))
        )
        if active and not self.browser_was_foreground:
            host.focus_browser()
        self.browser_was_foreground = active

    def external_navigation_blocked(self, index):
        if self.browser_tabs.currentIndex() == index:
            self.browser_address.setText('Navigation externe bloquée')
        self.status.setText(f'Session {index + 1} · navigation extérieure à impots.gouv.fr bloquée.')
        QTimer.singleShot(2200, lambda: self.update_browser_chrome() if self.browser_workspace_active else None)

    def queue_browser_zoom(self, index):
        if not self.worker or not self.worker.isRunning() or self.browser_hosts[index].handle is None:
            return
        zoom = responsive_browser_zoom(self.browser_hosts[index].width(), self.browser_zoom_offsets[index])
        if self.browser_zoom_sent[index] != zoom:
            self.browser_zoom_sent[index] = zoom
            self.worker.set_zoom(index, zoom)
        if self.browser_tabs.currentIndex() == index:
            self.browser_zoom_label.setText(f'{round(zoom * 100)} %')

    def adjust_browser_zoom(self, delta):
        index = self.browser_tabs.currentIndex()
        self.browser_zoom_offsets[index] = round(self.browser_zoom_offsets[index] + delta, 2)
        self.browser_zoom_sent[index] = None
        self.queue_browser_zoom(index)

    def reset_browser_zoom(self):
        index = self.browser_tabs.currentIndex()
        self.browser_zoom_offsets[index] = 0.0
        self.browser_zoom_sent[index] = None
        self.queue_browser_zoom(index)

    def reload_browser(self):
        if self.worker and self.worker.isRunning():
            self.worker.reload(self.browser_tabs.currentIndex())

    def browser_opened(self):
        self.browser_ready = True
        if self.show_browser_after_connect:
            self.show_session(getattr(self, 'pending_browser_focus', 0))
        self.update_buttons()

    def browser_finished(self):
        self.browser_ready = False
        self.busy = False
        self.connected_sessions = {}
        for host in self.browser_hosts:
            host.release()
        self.set_browser_workspace(False)
        for index in range(len(self.session_statuses)):
            if self.logged_session_events[index] not in (None, 'session_closed'):
                self.diagnostics.append('session_closed', session=index)
            self.logged_session_events[index] = 'session_closed'
            self.last_live_session_text[index] = None
            self.session_statuses[index] = 'Non ouverte'
            self.browser_session_tabs[index].setText(f'Session {index + 1} · Non ouverte')
            self.browser_session_tabs[index].setChecked(False)
            self.browser_urls[index] = 'impots.gouv.fr'
            self.browser_zoom_sent[index] = None
        self.update_buttons()
        if self.restart_sessions and not self.closing:
            self.restart_sessions = False
            QTimer.singleShot(0, self.open_browser)

    def browser_failed(self, text):
        self.diagnostics.append('worker_error')
        self.append_live_event(text)
        self.status.setText(text)
        self.browser_ready = False
        self.connected_sessions = {}
        self.warn('Moteur de navigation à relancer', 'Reconnectez-vous depuis l’onglet Collecte de la campagne.')
        for host in self.browser_hosts:
            host.release()
        self.set_browser_workspace(False)
        self.update_buttons()
