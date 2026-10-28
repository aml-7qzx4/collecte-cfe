from collecte_cfe.app import MainWindow
from collecte_cfe.credentials import AccountManager
from collecte_cfe.domain import Dossier, Notice
from collecte_cfe.store import Store
from collecte_cfe.ui_collect import COLLECT_PAGE, connection_summary
from tests.test_credentials import MemoryVault
from tests.test_dashboard import MemorySettings


def collect_window(tmp_path, accounts_count=1):
    store = Store(tmp_path / 'db.sqlite')
    batch = store.create_batch(
        [2025],
        tmp_path / 'out',
        [
            Dossier('Alpha', '000000001', 'Léa'),
            Dossier('Beta', '000000002', 'Léa'),
            Dossier('Gamma', '000000003', 'Paul'),
            Dossier('Delta', '000000004', 'Paul'),
        ],
        2,
        'CFE test',
    )
    accounts = AccountManager(store, MemoryVault())
    for number in range(accounts_count):
        accounts.add(f'Cabinet {number + 1}', f'expert{number + 1}@cabinet.example', 'secret')
    window = MainWindow(store, settings=MemorySettings(), accounts=accounts)
    window.open_campaign(batch)
    return store, batch, window


def test_the_collect_page_shows_progress_and_counts(tmp_path, qapp):
    store, batch, window = collect_window(tmp_path)
    campaign = store.batch_campaigns(batch)[0]['id']
    notice = store.register_notice(
        campaign, '000000001', Notice(2025, 'R1', '00001', '75', 'Paris', 'Solde', '15/12/2025')
    )
    store.notice_result(notice['id'], 'TERMINE')
    store.dossier_result(campaign, '000000001', 'TERMINE')
    store.dossier_result(campaign, '000000002', 'ABSENT')
    store.dossier_result(campaign, '000000003', 'ERREUR', 'Délai dépassé')
    window.campaign_tab_buttons[COLLECT_PAGE].click()
    assert window.pages.currentIndex() == COLLECT_PAGE
    assert window.title.text() == 'CFE test' and window.source_summary.text() == '4 dossiers · année 2025'
    assert (window.collect_done.text(), window.collect_total.text()) == ('3', 'dossiers traités sur 4')
    assert [value.text() for value, _label in window.collect_counts.values()] == [
        '<b>1</b> avec avis',
        '<b>1</b> sans avis',
        '<b>1</b> à relancer',
        '<b>1</b> restants',
    ]
    assert window.collect_progress.total == 4 and [value for value, _color in window.collect_progress.segments] == [
        1,
        1,
        1,
    ]
    assert (
        not window.login_button.isHidden()
        and window.login_button.text() == 'Se connecter'
        and window.run_button.isHidden()
    )
    assert [row.findChildren(type(window.title))[0].text() for row in window.collect_todo_rows] == ['Alpha', 'Gamma'], (
        'a notice not read yet and an error'
    )
    window.close()


def test_four_sessions_take_the_expert_accounts_in_turn_without_being_shown(tmp_path, qapp):
    store, batch, window = collect_window(tmp_path, accounts_count=2)
    assert store.batch(batch)['sessions'] == 4, 'four sessions at least, whatever the campaign was created with'
    assert [window.accounts.account_for_session(index)['label'] for index in range(4)] == [
        'Cabinet 1',
        'Cabinet 2',
        'Cabinet 1',
        'Cabinet 2',
    ]
    window.navigate(COLLECT_PAGE)
    assert not hasattr(window, 'session_status_cards') and not hasattr(window, 'add_session_button')
    assert window.connection_title.text() == 'Pas encore connecté'
    assert window.connection_accounts.text() == 'Comptes experts : Cabinet 1, Cabinet 2'
    window.close()


def test_more_expert_accounts_open_as_many_sessions(tmp_path, qapp):
    store, batch, window = collect_window(tmp_path, accounts_count=6)
    assert store.batch(batch)['sessions'] == 6 and len(window.browser_hosts) == 6 and len(window.session_statuses) == 6
    assert [window.accounts.account_for_session(index)['label'] for index in range(6)] == [
        f'Cabinet {number}' for number in range(1, 7)
    ]
    started = []
    window.start_browser_sessions = lambda count, focus=0, initial_sessions=None, show_browser=True: started.append(
        (count, initial_sessions)
    )
    window.open_browser()
    assert started == [(6, [0, 1, 2, 3, 4, 5])]
    window.close()


def test_the_connection_block_says_what_needs_the_collaborator(tmp_path, qapp):
    store, batch, window = collect_window(tmp_path)
    window.navigate(COLLECT_PAGE)
    for index in range(4):
        window.browser_hosts[index].handle = 100 + index
    window.browser_ready = True
    for index in range(4):
        window.session_updated(index, 'Connectée')
    assert window.connection_title.text() == 'Connecté à impots.gouv.fr' and window.connection_action.isHidden()
    assert not window.connection_window.isHidden(), 'the browser can still be shown'
    window.session_updated(2, 'CAPTCHA à vérifier')
    assert (
        window.connection_title.text() == 'CAPTCHA à saisir' and window.connection_panel.property('tone') == 'warning'
    )
    assert window.connection_action.text() == 'Ouvrir la fenêtre'
    window.connection_action.click()
    assert window.browser_workspace_active and window.browser_tabs.currentIndex() == 2
    window.leave_browser_workspace()
    window.session_updated(1, 'Connexion refusée')
    assert (
        window.connection_title.text() == 'Identifiants refusés par impots.gouv.fr'
        and window.connection_action.text() == 'Corriger le compte'
    )
    window.connection_action.click()
    assert window.pages.currentIndex() == 4
    for index in range(4):
        window.browser_hosts[index].handle = None
    window.close()


def test_the_connection_summary_puts_what_waits_first():
    assert (
        connection_summary(['Connectée', 'Connexion refusée', 'CAPTCHA à vérifier'], False)[1]
        == 'Identifiants refusés par impots.gouv.fr'
    )
    assert connection_summary(['Connectée', 'CAPTCHA à vérifier'], False)[3:] == ('window', 1)
    assert connection_summary(['Connectée', 'À reconnecter'], True)[3] == 'reconnect'
    assert connection_summary(['Alpha · 2025', 'Connectée', 'Beta · 2025'], True)[1:3] == (
        'Collecte en cours',
        'En cours : Alpha, Beta',
    )
    assert connection_summary(['Connectée', 'Ouverture…'], False)[1] == 'Connexion en cours…'
    assert connection_summary(['Non ouverte'] * 4, False)[1] == 'Pas encore connecté'


def test_the_last_events_are_listed_newest_first(tmp_path, qapp):
    store, batch, window = collect_window(tmp_path)
    window.navigate(COLLECT_PAGE)
    assert not window.collect_events_empty.isHidden()
    window.append_live_event('Session 1 · Alpha · solde 2025 classé')
    window.append_live_event('Export Excel terminé')
    assert window.collect_events_empty.isHidden()
    texts = [[label.text() for label in row.findChildren(type(window.title))] for row in window.collect_event_rows]
    assert [row[1:] for row in texts] == [['Export Excel terminé'], ['Alpha · solde 2025 classé']], (
        'the sessions are not named'
    )
    window.close()


def test_a_collection_opens_the_collect_page_without_the_bottom_bar(tmp_path, qapp, monkeypatch):
    store, batch, window = collect_window(tmp_path)
    monkeypatch.setattr(window.toast, 'announce', lambda text, *_args, **_kwargs: None)

    class Worker:
        count = 4

        def __init__(self):
            self.calls = []

        def isRunning(self):
            return True

        def submit_campaign(self, *args):
            self.calls.append(args)

    window.worker = Worker()
    window.browser_ready = True
    window.connected_sessions = dict.fromkeys(range(4), True)
    window.update_buttons()
    window.start_collection()
    assert (
        window.pages.currentIndex() == COLLECT_PAGE
        and window.campaign_tab_buttons[COLLECT_PAGE].badge.text() == 'en cours'
    )
    assert window.collect_when.text().startswith('Commencée à ') and not window.pause_button.isHidden()
    assert window.activity.isHidden(), 'the Collecte page already shows what the bottom bar would'
    window.navigate(2)
    assert not window.activity.isHidden()
    monkeypatch.setattr(window, 'notify_collection_result', lambda batch: None)
    window.collection_state(False)
    window.worker = None
    window.close()


def test_leaving_a_session_window_returns_to_the_collect_page(tmp_path, qapp):
    store, batch, window = collect_window(tmp_path)
    window.navigate(COLLECT_PAGE)
    window.browser_hosts[0].handle = 101
    window.show_session(0)
    assert window.browser_workspace_active
    window.browser_hosts[0].handle = None
    window.leave_browser_workspace()
    assert window.pages.currentIndex() == COLLECT_PAGE
    window.close()


def test_the_live_feed_never_shows_a_label_as_a_window_of_its_own(tmp_path, qapp):
    from PySide6.QtCore import QEvent, QObject

    store, batch, window = collect_window(tmp_path)
    window.navigate(COLLECT_PAGE)
    window.show()
    qapp.processEvents()
    windows = []

    class Spy(QObject):
        def eventFilter(self, obj, event):
            if event.type() == QEvent.Type.Show and obj.isWidgetType() and obj.isWindow() and obj is not window:
                windows.append(obj)
            return False

    spy = Spy()
    qapp.installEventFilter(spy)
    for number in range(3):
        window.append_live_event(f'Session 1 · Alpha · avis {number}')
        qapp.processEvents()
    qapp.removeEventFilter(spy)
    assert windows == []
    window.close()
