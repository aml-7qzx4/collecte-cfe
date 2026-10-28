import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from reportlab.pdfgen import canvas

from collecte_cfe.domain import Dossier, Notice
from collecte_cfe.store import Store


def test_worker_failure_record_keeps_stack_location_without_exception_secrets(tmp_path):
    from collecte_cfe.sessions import record_worker_failure

    path = tmp_path / 'moteur-erreurs.txt'
    try:
        raise RuntimeError('secret-token=ne-jamais-enregistrer')
    except RuntimeError as error:
        record_worker_failure(error, path)
    report = path.read_text(encoding='utf-8')
    assert 'RuntimeError' in report
    assert 'test_sessions.py' in report
    assert 'secret-token' not in report
    assert 'ne-jamais-enregistrer' not in report


def test_activity_tracking_reports_pointer_clicks_for_native_keyboard_focus():
    from collecte_cfe.sessions import install_activity_tracking

    class Page:
        async def expose_binding(self, _name, callback):
            self.callback = callback

        async def add_init_script(self, script):
            self.script = script

        def on(self, _name, callback):
            self.request_callback = callback

    page = Page()
    activity = []
    focus = []
    asyncio.run(install_activity_tracking(page, lambda: activity.append(True), lambda: focus.append(True)))
    page.callback(None, 'pointerdown')
    assert activity == [True] and focus == [True]
    assert '__collecteMarkActivity(event.type)' in page.script


class IndependentPortal:
    def __init__(self, active, expired=False):
        self.active = active
        self.expired = expired
        self.siren = None
        self.calls = []

    async def connected(self):
        return True

    async def discover_dossiers(self, *_args):
        return [Dossier('A', '000000001', 'À affecter'), Dossier('B', '000000002', 'À affecter')] + [
            Dossier(f'Dossier {i}', str(100000000 + i), 'À affecter') for i in range(8)
        ]

    async def open_dossier(self, siren, year):
        from collecte_cfe.portal import SessionExpired

        if self.expired:
            raise SessionExpired('Connexion requise.')
        self.siren = siren
        self.active['now'] += 1
        self.active['max'] = max(self.active['max'], self.active['now'])
        await asyncio.sleep(0.05)
        self.calls.append((siren, year))
        return [Notice(year, siren, '00021', '33', 'BORDEAUX', 'Rôle général', '')]

    async def download(self, n, path):
        assert n.reference == self.siren
        c = canvas.Canvas(str(path))
        c.drawString(50, 700, f'SIREN {self.siren} - Cotisation {n.year} - Reference {n.reference}')
        c.showPage()
        c.save()

    async def close_dossier(self):
        if self.siren:
            self.active['now'] -= 1
        self.siren = None


class DirectSearchPortal(IndependentPortal):
    def __init__(self, active, denied=()):
        super().__init__(active)
        self.denied = set(denied)
        self.discovery_calls = 0

    async def discover_dossiers(self, *_args):
        self.discovery_calls += 1
        raise AssertionError('Un portefeuille fourni ne doit pas déclencher Tous mes dossiers.')

    async def open_dossier(self, siren, year):
        from collecte_cfe.portal import AuthorizationError

        if siren in self.denied:
            self.calls.append((siren, year))
            raise AuthorizationError('Aucune habilitation.')
        return await super().open_dossier(siren, year)


def test_file_portfolio_searches_each_siren_directly_without_discovery(tmp_path):
    from collecte_cfe.sessions import run_batch

    store = Store(tmp_path / 'db.sqlite')
    batch = store.create_batch([2025], tmp_path / 'out', [Dossier('A', '000000001', 'Léa')], 1)
    portal = DirectSearchPortal({'now': 0, 'max': 0})
    asyncio.run(run_batch(store, batch, [portal], threading.Event(), lambda _message: None, lambda *_args: None))
    assert portal.discovery_calls == 0
    assert portal.calls == [('000000001', 2025)]
    assert store.batch_campaigns(batch)[0]['status'] == 'TERMINE'
    assert {row['phase'] for row in store.timings(batch)} == {'RECHERCHE', 'AVIS'}
    assert (tmp_path / 'out' / f'Temps_collecte_CFE_Campagne {batch}.csv').is_file()


def test_assigned_login_uses_account_selected_for_each_session(tmp_path):
    from collecte_cfe.credentials import AccountManager
    from collecte_cfe.sessions import login_assigned_account
    from tests.test_credentials import MemoryVault

    manager = AccountManager(Store(tmp_path / 'db.sqlite'), MemoryVault())
    first = manager.add('Cabinet A', 'a@example.fr', 'secret A')
    second = manager.add('Cabinet B', 'b@example.fr', 'secret B')
    manager.assign(0, first)
    manager.assign(1, second)
    manager.assign(2, first)
    received = []

    async def attempt(_portal, login, password):
        received.append((login, password))
        return 'connected'

    for index in range(4):
        asyncio.run(login_assigned_account(object(), manager, index, attempt))
    assert received == [('a@example.fr', 'secret A'), ('b@example.fr', 'secret B'), ('a@example.fr', 'secret A')]


def test_refused_password_is_not_retyped_on_other_sessions(tmp_path):
    from collecte_cfe.credentials import AccountManager
    from collecte_cfe.sessions import login_assigned_account
    from tests.test_credentials import MemoryVault

    manager = AccountManager(Store(tmp_path / 'db.sqlite'), MemoryVault())
    account = manager.add('Cabinet', 'expert@example.fr', 'wrong password')
    manager.assign(0, account)
    manager.assign(1, account)
    calls = []

    async def refuse(_portal, login, password):
        calls.append((login, password))
        return 'refused'

    assert asyncio.run(login_assigned_account(object(), manager, 0, refuse)) == 'refused'
    assert asyncio.run(login_assigned_account(object(), manager, 1, refuse)) == 'refused'
    assert calls == [('expert@example.fr', 'wrong password')]


def test_login_result_maps_to_clear_session_status():
    from collecte_cfe.sessions import login_result_status

    assert login_result_status('connected', True) == 'Connectée'
    assert login_result_status('refused', True) == 'Connexion refusée'
    assert login_result_status('captcha_unreadable', True) == 'CAPTCHA à vérifier'
    assert login_result_status('manual', True) == 'Connexion à vérifier'
    assert login_result_status('manual', False) == 'À connecter'


def test_pool_runs_concurrently_and_assigns_each_dossier_year_once(tmp_path):
    from collecte_cfe.sessions import run_batch

    s = Store(tmp_path / 'db.sqlite')
    b = s.create_batch(
        [2024, 2025], tmp_path / 'out', [Dossier('A', '000000001', 'Léa'), Dossier('B', '000000002', 'Paul')], 2
    )
    active = {'now': 0, 'max': 0}
    portals = [IndependentPortal(active) for _ in range(2)]
    events = []
    asyncio.run(run_batch(s, b, portals, threading.Event(), events.append, lambda *_: None))
    calls = [call for p in portals for call in p.calls]
    assert len(calls) == len(set(calls)) == 4 and active['max'] == 2
    assert all(c['status'] == 'TERMINE' for c in s.batch_campaigns(b))
    assert len(list((tmp_path / 'out').rglob('*.pdf'))) == 4
    assert len(list((tmp_path / 'out').rglob('*.xlsx'))) == 3, 'one workbook per collaborator and one for the cabinet'


class ScopedPortal(IndependentPortal):
    def __init__(self, active, scope, denied=()):
        super().__init__(active)
        self.scope = dict(scope)
        self.denied = set(denied)

    async def discover_dossiers(self, *_args):
        return [Dossier(name, siren, 'À affecter') for siren, name in self.scope.items()]

    async def open_dossier(self, siren, year):
        from collecte_cfe.portal import AuthorizationError

        if siren in self.denied:
            self.calls.append((siren, year))
            raise AuthorizationError('Droits de consultation CFE absents.')
        return await super().open_dossier(siren, year)


def test_distinct_account_scopes_receive_only_their_own_dossiers(tmp_path):
    from collecte_cfe.sessions import run_batch

    s = Store(tmp_path / 'db.sqlite')
    dossiers = [Dossier('A', '000000001', 'Léa'), Dossier('B', '000000002', 'Paul')]
    b = s.create_batch([2025], tmp_path / 'out', dossiers, 2)
    active = {'now': 0, 'max': 0}
    first = ScopedPortal(active, {'000000001': 'A'})
    second = ScopedPortal(active, {'000000002': 'B'})
    asyncio.run(run_batch(s, b, [first, second], threading.Event(), lambda _: None, lambda *_: None))
    assert first.calls == [('000000001', 2025)]
    assert second.calls == [('000000002', 2025)]


def test_authorization_failure_is_reassigned_to_another_account_scope(tmp_path):
    from collecte_cfe.sessions import run_batch

    s = Store(tmp_path / 'db.sqlite')
    b = s.create_batch([2025], tmp_path / 'out', [Dossier('A', '000000001', 'Léa')], 2)
    active = {'now': 0, 'max': 0}
    first = ScopedPortal(active, {'000000001': 'A', '111111111': 'Premier'}, denied={'000000001'})
    second = ScopedPortal(active, {'000000001': 'A', '222222222': 'Second'})
    asyncio.run(run_batch(s, b, [first, second], threading.Event(), lambda _: None, lambda *_: None))
    campaign = s.batch_campaigns(b)[0]
    assert first.calls == [('000000001', 2025)]
    assert second.calls == [('000000001', 2025)]
    assert s.dossiers(campaign['id'])[0]['status'] == 'TERMINE'


def test_file_portfolio_tries_each_connected_session_before_no_authorization(tmp_path):
    from collecte_cfe.sessions import run_batch

    s = Store(tmp_path / 'db.sqlite')
    b = s.create_batch([2025], tmp_path / 'out', [Dossier('A', '000000001', 'Léa')], 2)
    active = {'now': 0, 'max': 0}
    scope = {'000000001': 'A'}
    portals = [ScopedPortal(active, scope, denied={'000000001'}) for _ in range(2)]
    asyncio.run(run_batch(s, b, portals, threading.Event(), lambda _: None, lambda *_: None))
    campaign = s.batch_campaigns(b)[0]
    row = s.dossiers(campaign['id'])[0]
    assert sum(len(p.calls) for p in portals) == 2
    assert row['status'] == 'SANS_HABILITATION'
    assert row['proof_path'] == ''
    assert campaign['status'] == 'AVEC_ERREURS'


def test_expiration_stops_new_work_and_preserves_pending_jobs(tmp_path):
    from collecte_cfe.sessions import run_batch

    s = Store(tmp_path / 'db.sqlite')
    ds = [Dossier(f'Dossier {i}', str(100000000 + i), 'Léa') for i in range(8)]
    b = s.create_batch([2025], tmp_path / 'out', ds, 2)
    active = {'now': 0, 'max': 0}
    portals = [IndependentPortal(active, expired=True), IndependentPortal(active)]
    stop = threading.Event()
    asyncio.run(run_batch(s, b, portals, stop, lambda _: None, lambda *_: None))
    c = s.batch_campaigns(b)[0]
    assert stop.is_set() and c['status'] == 'CONNEXION_REQUISE'
    assert sum(d['status'] == 'EN_ATTENTE' for d in s.dossiers(c['id'])) >= 6


def test_discovery_failure_never_creates_partial_portfolio(tmp_path):
    from collecte_cfe.portal import PortalError
    from collecte_cfe.sessions import run_batch

    class DiscoveryPortal(IndependentPortal):
        async def discover_dossiers(self, *args):
            raise PortalError('Pagination incomplète.')

    s = Store(tmp_path / 'db.sqlite')
    b = s.create_batch([2024, 2025], tmp_path / 'out', [], 1)
    events = []
    asyncio.run(
        run_batch(s, b, [DiscoveryPortal({'now': 0, 'max': 0})], threading.Event(), events.append, lambda *_: None)
    )
    assert not s.batch(b)['discovered'] and all(not s.dossiers(c['id']) for c in s.batch_campaigns(b))
    assert all(c['status'] == 'PAUSE' for c in s.batch_campaigns(b))
    assert any('Pagination incomplète' in e for e in events)


def test_retry_without_errors_never_announces_completion_of_pending_dossiers(tmp_path):
    from collecte_cfe.sessions import run_batch

    s = Store(tmp_path / 'db.sqlite')
    b = s.create_batch([2025], tmp_path / 'out', [Dossier('A', '000000001', 'Léa')], 1)
    events = []
    asyncio.run(
        run_batch(
            s, b, [IndependentPortal({'now': 0, 'max': 0})], threading.Event(), events.append, lambda *_: None, True
        )
    )
    assert s.batch_campaigns(b)[0]['status'] == 'PAUSE'
    assert not any('Collecte terminée' in e for e in events)


def test_retry_errors_only_skips_dossiers_without_authorization(tmp_path):
    from collecte_cfe.sessions import run_batch

    s = Store(tmp_path / 'db.sqlite')
    dossiers = [
        Dossier('Erreur', '000000001', 'Léa'),
        Dossier('Sans droits', '000000002', 'Léa'),
        Dossier('Terminé', '111111111', 'Léa'),
    ]
    b = s.create_batch([2025], tmp_path / 'out', dossiers, 1)
    campaign = s.batch_campaigns(b)[0]['id']
    s.dossier_result(campaign, '000000001', 'ERREUR', 'Délai dépassé.')
    s.dossier_result(campaign, '000000002', 'SANS_HABILITATION', 'Aucune habilitation.')
    s.dossier_result(campaign, '111111111', 'TERMINE')
    portal = IndependentPortal({'now': 0, 'max': 0})
    asyncio.run(run_batch(s, b, [portal], threading.Event(), lambda _: None, lambda *_: None, only_errors=True))
    assert portal.calls == [('000000001', 2025)]
    assert {row['siren']: row['status'] for row in s.dossiers(campaign)} == {
        '000000001': 'TERMINE',
        '000000002': 'SANS_HABILITATION',
        '111111111': 'TERMINE',
    }


def test_retry_selected_errors_only_and_passes_capture_directory(tmp_path):
    from collecte_cfe.sessions import run_batch

    s = Store(tmp_path / 'db.sqlite')
    dossiers = [Dossier('A', '000000001', 'Léa'), Dossier('B', '000000002', 'Léa')]
    batch = s.create_batch([2025], tmp_path / 'out', dossiers, 1)
    campaign = s.batch_campaigns(batch)[0]['id']
    for dossier in dossiers:
        s.dossier_result(campaign, dossier.siren, 'ERREUR', 'Délai dépassé.')
    portal = DirectSearchPortal({'now': 0, 'max': 0})
    asyncio.run(
        run_batch(
            s,
            batch,
            [portal],
            threading.Event(),
            lambda _: None,
            lambda *_: None,
            only_errors=True,
            selected_dossiers={(campaign, '000000001')},
            capture_dir=tmp_path / 'captures',
        )
    )
    assert portal.calls == [('000000001', 2025)]
    assert s.dossiers(campaign)[1]['status'] == 'ERREUR'


def test_normal_relaunch_preserves_completed_and_absent_dossiers(tmp_path):
    from hashlib import sha256

    from collecte_cfe.sessions import run_batch

    s = Store(tmp_path / 'db.sqlite')
    dossiers = [
        Dossier('Terminé', '000000001', 'Léa'),
        Dossier('Absent', '000000002', 'Léa'),
        Dossier('Erreur', '111111111', 'Léa'),
        Dossier('Sans droits', '222222222', 'Léa'),
    ]
    batch = s.create_batch([2025], tmp_path / 'out', dossiers, 1)
    campaign = s.batch_campaigns(batch)[0]['id']
    for dossier, status in zip(dossiers, ['TERMINE', 'ABSENT', 'ERREUR', 'SANS_HABILITATION']):
        s.dossier_result(campaign, dossier.siren, status)
    completed = s.register_notice(
        campaign, '000000001', Notice(2025, 'REF', '00021', '33', 'BORDEAUX', 'Rôle général', '')
    )
    target = Path(completed['path'])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'%PDF-1.4\n')
    s.notice_result(completed['id'], 'TERMINE', sha256=sha256(target.read_bytes()).hexdigest())
    proof = s.register_proof(campaign, '000000002')
    proof.parent.mkdir(parents=True, exist_ok=True)
    proof.write_bytes(b'%PDF-1.4\n')
    s.proof_result(campaign, '000000002', sha256(proof.read_bytes()).hexdigest())
    portal = IndependentPortal({'now': 0, 'max': 0})
    asyncio.run(run_batch(s, batch, [portal], threading.Event(), lambda _: None, lambda *_: None))
    assert portal.calls == [('111111111', 2025), ('222222222', 2025)]
    assert {row['siren']: row['status'] for row in s.dossiers(campaign)} == {
        '000000001': 'TERMINE',
        '000000002': 'ABSENT',
        '111111111': 'TERMINE',
        '222222222': 'TERMINE',
    }


def test_normal_relaunch_repairs_missing_completed_pdf(tmp_path):
    from collecte_cfe.sessions import run_batch

    s = Store(tmp_path / 'db.sqlite')
    batch = s.create_batch([2025], tmp_path / 'out', [Dossier('A', '000000001', 'Léa')], 1)
    first = IndependentPortal({'now': 0, 'max': 0})
    asyncio.run(run_batch(s, batch, [first], threading.Event(), lambda _: None, lambda *_: None))
    campaign = s.batch_campaigns(batch)[0]['id']
    target = Path(s.notices(campaign)[0]['path'])
    target.unlink()
    second = IndependentPortal({'now': 0, 'max': 0})
    asyncio.run(run_batch(s, batch, [second], threading.Event(), lambda _: None, lambda *_: None))
    assert second.calls == [('000000001', 2025)]
    assert target.is_file()


def test_normal_relaunch_repairs_modified_completed_pdf(tmp_path):
    from hashlib import sha256

    from collecte_cfe.sessions import run_batch

    store = Store(tmp_path / 'db.sqlite')
    batch = store.create_batch([2025], tmp_path / 'out', [Dossier('A', '000000001', 'Léa')], 1)
    first = IndependentPortal({'now': 0, 'max': 0})
    asyncio.run(run_batch(store, batch, [first], threading.Event(), lambda _: None, lambda *_: None))
    campaign = store.batch_campaigns(batch)[0]['id']
    notice = store.notices(campaign)[0]
    target = Path(notice['path'])
    modified = target.read_bytes() + b'\nmodified'
    target.write_bytes(modified)

    second = IndependentPortal({'now': 0, 'max': 0})
    asyncio.run(run_batch(store, batch, [second], threading.Event(), lambda _: None, lambda *_: None))

    assert second.calls == [('000000001', 2025)]
    assert sha256(target.read_bytes()).hexdigest() == store.notices(campaign)[0]['sha256']
    assert any(path.read_bytes() == modified for path in target.parent.glob('_A_verifier/*.pdf'))


def test_normal_relaunch_repairs_modified_absence_proof(tmp_path):
    from hashlib import sha256

    from collecte_cfe.sessions import run_batch

    class EmptyPortal(IndependentPortal):
        async def open_dossier(self, siren, year):
            await super().open_dossier(siren, year)
            return []

        async def capture_cfe_screen(self, path):
            from PIL import Image

            Image.new('RGB', (400, 200), 'white').save(path)
            return True

    store = Store(tmp_path / 'db.sqlite')
    batch = store.create_batch([2025], tmp_path / 'out', [Dossier('A', '000000001', 'Léa')], 1)
    first = EmptyPortal({'now': 0, 'max': 0})
    asyncio.run(run_batch(store, batch, [first], threading.Event(), lambda _: None, lambda *_: None))
    campaign = store.batch_campaigns(batch)[0]['id']
    target = Path(store.dossiers(campaign)[0]['proof_path'])
    modified = target.read_bytes() + b'\nmodified'
    target.write_bytes(modified)

    second = EmptyPortal({'now': 0, 'max': 0})
    asyncio.run(run_batch(store, batch, [second], threading.Event(), lambda _: None, lambda *_: None))

    assert second.calls == [('000000001', 2025)]
    assert sha256(target.read_bytes()).hexdigest() == store.dossiers(campaign)[0]['proof_sha256']
    assert any(path.read_bytes() == modified for path in target.parent.glob('_A_verifier/*.pdf'))


def test_wrong_pdf_identity_is_never_stamped_or_classified(tmp_path):
    from collecte_cfe.sessions import run_batch

    class WrongPortal(IndependentPortal):
        async def download(self, n, path):
            c = canvas.Canvas(str(path))
            c.drawString(50, 700, '000000002 2025 REF AUTRE DOSSIER')
            c.showPage()
            c.save()

    s = Store(tmp_path / 'db.sqlite')
    b = s.create_batch([2025], tmp_path / 'out', [Dossier('A', '000000001', 'Léa')], 1)
    asyncio.run(
        run_batch(s, b, [WrongPortal({'now': 0, 'max': 0})], threading.Event(), lambda _: None, lambda *_: None)
    )
    c = s.batch_campaigns(b)[0]
    assert c['status'] == 'AVEC_ERREURS' and s.notices(c['id'])[0]['status'] == 'ERREUR'
    assert not list((tmp_path / 'out').rglob('*.pdf'))


def test_pdf_identity_rejects_reference_prefix_and_payment_year(tmp_path):
    import pytest

    from collecte_cfe.documents import verify_original_notice
    from collecte_cfe.domain import Dossier, Notice

    for year, reference in [(2024, 'REF123'), (2025, 'REF1234'), (2024, 'REF1234')]:
        p = tmp_path / 'wrong.pdf'
        c = canvas.Canvas(str(p))
        c.drawString(40, 700, f'SIREN 000000001 - Cotisation {year} - Reference {reference} - Paiement 2025')
        c.save()
        with pytest.raises(ValueError):
            verify_original_notice(
                p,
                Dossier('Alpha', '000000001', 'Lea'),
                Notice(2025, 'REF123', '00021', '33', 'Bordeaux', 'Rôle général', ''),
            )


@pytest.mark.parametrize('count,initial,index', [(1, None, 0), (4, [2], 2)])
def test_browser_window_is_hidden_and_attached_before_portal_navigation(tmp_path, monkeypatch, count, initial, index):
    import playwright.async_api

    import collecte_cfe.sessions as sessions

    timeline = []
    launches = []
    context_options = []

    class Locator:
        async def count(self):
            return 0

        def filter(self, **_kwargs):
            return self

    class Page:
        main_frame = object()
        url = 'about:blank'

        async def goto(self, *_args, **_kwargs):
            timeline.append('navigation')

        def is_closed(self):
            return False

        def get_by_role(self, *_args, **_kwargs):
            return Locator()

        def locator(self, *_args, **_kwargs):
            return Locator()

        def on(self, *_args):
            pass

    class Context:
        async def route(self, *_args):
            pass

        async def new_page(self):
            timeline.append('window-created')
            return Page()

    class Browser:
        _impl_obj = SimpleNamespace(
            _connection=SimpleNamespace(_transport=SimpleNamespace(_proc=SimpleNamespace(pid=321)))
        )

        async def new_context(self, **kwargs):
            context_options.append(kwargs)
            return Context()

        async def close(self):
            timeline.append('closed')

    class Chromium:
        async def launch(self, **kwargs):
            launches.append(kwargs)
            return Browser()

    class Playwright:
        chromium = Chromium()

    class Manager:
        async def __aenter__(self):
            return Playwright()

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(playwright.async_api, 'async_playwright', lambda: Manager())
    monkeypatch.setattr(sessions, 'process_tree', lambda _pid: {321})
    monkeypatch.setattr(sessions, 'chromium_windows', lambda _pids: {123})
    monkeypatch.setattr(
        sessions, 'hide_chromium_window', lambda handle: timeline.append(('hidden', handle)), raising=False
    )
    store = Store(tmp_path / 'db.sqlite')
    worker = sessions.BrowserWorker(store, count, initial_sessions=initial)

    def attached(index, handle):
        timeline.append(('attached', index, handle))
        worker.confirm_window_attached(index)
        worker.request_shutdown()

    worker.window_ready.connect(attached)
    asyncio.run(worker.operate())
    assert timeline[:4] == ['window-created', ('hidden', 123), ('attached', index, 123), 'navigation']
    assert '--overscroll-history-navigation=0' in launches[0]['args']
    assert context_options[0]['no_viewport'] is True


def test_browser_worker_resizes_sessions_without_closing_the_first(tmp_path, monkeypatch):
    import playwright.async_api

    import collecte_cfe.sessions as sessions

    timeline = []
    windows = set()
    next_handle = iter((101, 102))

    class Locator:
        async def count(self):
            return 0

        def filter(self, **_kwargs):
            return self

    class Page:
        main_frame = object()
        url = 'about:blank'

        async def goto(self, *_args, **_kwargs):
            pass

        def is_closed(self):
            return False

        def get_by_role(self, *_args, **_kwargs):
            return Locator()

        def locator(self, *_args, **_kwargs):
            return Locator()

        def on(self, *_args):
            pass

    class Context:
        async def route(self, *_args):
            pass

        async def new_page(self):
            handle = next(next_handle)
            windows.add(handle)
            timeline.append(('created', handle))
            return Page()

        async def close(self):
            timeline.append('context-closed')

    class Browser:
        _impl_obj = SimpleNamespace(
            _connection=SimpleNamespace(_transport=SimpleNamespace(_proc=SimpleNamespace(pid=321)))
        )

        async def new_context(self, **_kwargs):
            return Context()

        async def close(self):
            timeline.append('browser-closed')

    class Chromium:
        async def launch(self, **_kwargs):
            return Browser()

    class Manager:
        async def __aenter__(self):
            return SimpleNamespace(chromium=Chromium())

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(playwright.async_api, 'async_playwright', lambda: Manager())
    monkeypatch.setattr(sessions, 'process_tree', lambda _pid: {321})
    monkeypatch.setattr(sessions, 'chromium_windows', lambda _pids: set(windows))
    monkeypatch.setattr(sessions, 'hide_chromium_window', lambda handle: timeline.append(('hidden', handle)))
    worker = sessions.BrowserWorker(Store(tmp_path / 'db.sqlite'), 1)
    worker.window_ready.connect(lambda index, _handle: worker.confirm_window_attached(index))
    worker.ready.connect(lambda: worker.resize_sessions(2))
    resized = []
    closed = []

    def resized_to(count):
        resized.append(count)
        if count == 2:
            worker.resize_sessions(1)
        elif count == 1:
            worker.request_shutdown()

    worker.sessions_resized.connect(resized_to)
    worker.window_closed.connect(closed.append)
    asyncio.run(worker.operate())
    assert timeline[:4] == [('created', 101), ('hidden', 101), ('created', 102), ('hidden', 102)]
    assert resized == [2, 1] and closed == [1]
    assert timeline.count('context-closed') == 1


def test_page_zoom_is_applied_without_touching_browser_storage():
    from collecte_cfe.sessions import apply_page_zoom

    class Page:
        def __init__(self):
            self.calls = []

        def is_closed(self):
            return False

        async def evaluate(self, script, value):
            self.calls.append((script, value))

    page = Page()
    asyncio.run(apply_page_zoom(page, 0.67))
    assert page.calls and page.calls[0][1] == 0.67
    assert 'documentElement.style.zoom' in page.calls[0][0]
    assert 'overflow-x' in page.calls[0][0]
    assert 'overscroll-behavior-x' in page.calls[0][0]
    assert 'collecte-bottom-spacer' in page.calls[0][0]
    assert "height:'56px'" in page.calls[0][0]


def test_external_main_navigation_is_aborted_before_request():
    from collecte_cfe.sessions import guard_portal_request

    class Route:
        def __init__(self):
            self.action = None

        async def abort(self):
            self.action = 'abort'

        async def continue_(self):
            self.action = 'continue'

    class Request:
        def __init__(self, url, navigation=True):
            self.url = url
            self.navigation = navigation

        def is_navigation_request(self):
            return self.navigation

    blocked = []
    external = Route()
    asyncio.run(guard_portal_request(external, Request('https://example.org'), blocked.append))
    resource = Route()
    asyncio.run(guard_portal_request(resource, Request('https://cdn.example.org/a.js', False), blocked.append))
    portal = Route()
    asyncio.run(guard_portal_request(portal, Request('https://cfspro.impots.gouv.fr/mire/accueil.do'), blocked.append))
    assert external.action == 'abort' and blocked == ['https://example.org']
    assert resource.action == 'continue' and portal.action == 'continue'


def test_only_connected_idle_sessions_are_refreshed_after_two_minutes():
    from collecte_cfe.sessions import refresh_inactive_sessions

    class Page:
        def __init__(self):
            self.reloads = 0

        def is_closed(self):
            return False

        async def reload(self, **_kwargs):
            self.reloads += 1

    class Portal:
        def __init__(self, connected=True):
            self.main = Page()
            self._connected = connected

        async def connected(self):
            return self._connected

    portals = [Portal(), Portal(), Portal(False), None]
    activity = [0, 31, 0, 0]
    refreshed = asyncio.run(refresh_inactive_sessions(portals, activity, now=120, collecting=False))
    assert refreshed == [0]
    assert [p.main.reloads if p else 0 for p in portals] == [1, 0, 0, 0]
    assert activity[0] == 120
    assert asyncio.run(refresh_inactive_sessions(portals, activity, now=300, collecting=True)) == []


def test_failed_idle_refresh_keeps_other_sessions_alive():
    from playwright.async_api import Error as PlaywrightError

    from collecte_cfe.sessions import refresh_inactive_sessions

    class Page:
        def __init__(self, fail=False):
            self.fail = fail
            self.reloads = 0

        def is_closed(self):
            return False

        async def reload(self, **_kwargs):
            self.reloads += 1
            if self.fail:
                raise PlaywrightError('Navigation interrupted; session URL is private')

    class Portal:
        def __init__(self, fail=False):
            self.main = Page(fail)

        async def connected(self):
            return True

    portals = [Portal(True), Portal(False)]
    activity = [0, 0]
    refreshed = asyncio.run(refresh_inactive_sessions(portals, activity, now=121))
    assert refreshed == [1]
    assert activity == [121, 121]
    assert [p.main.reloads for p in portals] == [1, 1]


def test_expired_connected_session_reauthenticates_once_without_retyping_refused_password(tmp_path):
    from collecte_cfe.credentials import AccountManager
    from collecte_cfe.sessions import reconnect_due, reconnect_expired_session
    from tests.test_credentials import MemoryVault

    class Page:
        url = 'https://cfspro-idp.impots.gouv.fr/oauth2/authorize'

        def __init__(self):
            self.gotos = []

        def is_closed(self):
            return False

        async def goto(self, *args, **kwargs):
            self.gotos.append(args)

    class Portal:
        def __init__(self):
            self.main = Page()

        async def connected(self):
            return False

    store = Store(tmp_path / 'db.sqlite')
    accounts = AccountManager(store, MemoryVault())
    account = accounts.add('Cabinet', 'cabinet@example.fr', 'secret')
    accounts.assign(0, account)
    portal = Portal()
    calls = []

    async def refused(_portal, _login, _password, **_kwargs):
        calls.append(1)
        return 'refused'

    assert reconnect_due(15, 0, 0, True, False, False)
    assert not reconnect_due(15, 0, 14, True, False, False)
    assert asyncio.run(reconnect_expired_session(portal, accounts, 0, attempt=refused)) == 'refused'
    assert accounts.login_blocked(account)
    assert asyncio.run(reconnect_expired_session(portal, accounts, 0, attempt=refused)) == 'refused'
    assert calls == [1] and portal.main.gotos == []


def test_worker_exposes_close_and_reopen_commands(tmp_path):
    from collecte_cfe.sessions import BrowserWorker

    worker = BrowserWorker(Store(tmp_path / 'db.sqlite'), 2)
    worker.close_session(1)
    worker.open_session(1)
    assert worker.commands.get_nowait() == ('close', 1)
    assert worker.commands.get_nowait() == ('open', 1)


def test_worker_can_close_and_reopen_the_same_session_slot(tmp_path, monkeypatch):
    import playwright.async_api

    import collecte_cfe.sessions as sessions

    timeline = []
    windows = set()
    handles = iter((201, 202))

    class Locator:
        async def count(self):
            return 0

        def filter(self, **_kwargs):
            return self

    class Page:
        main_frame = object()
        url = 'about:blank'

        def __init__(self, handle):
            self.handle = handle

        async def goto(self, *_args, **_kwargs):
            pass

        async def bring_to_front(self):
            pass

        def is_closed(self):
            return False

        def get_by_role(self, *_args, **_kwargs):
            return Locator()

        def locator(self, *_args, **_kwargs):
            return Locator()

        def on(self, *_args):
            pass

    class Context:
        def __init__(self):
            self.handle = None

        async def route(self, *_args):
            pass

        async def new_page(self):
            self.handle = next(handles)
            windows.add(self.handle)
            timeline.append(('created', self.handle))
            return Page(self.handle)

        async def close(self):
            windows.discard(self.handle)
            timeline.append(('closed', self.handle))

    class Browser:
        _impl_obj = SimpleNamespace(
            _connection=SimpleNamespace(_transport=SimpleNamespace(_proc=SimpleNamespace(pid=321)))
        )

        async def new_context(self, **_kwargs):
            return Context()

        async def close(self):
            timeline.append('browser-closed')

    class Chromium:
        async def launch(self, **_kwargs):
            return Browser()

    class Manager:
        async def __aenter__(self):
            return SimpleNamespace(chromium=Chromium())

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr(playwright.async_api, 'async_playwright', lambda: Manager())
    monkeypatch.setattr(sessions, 'process_tree', lambda _pid: {321})
    monkeypatch.setattr(sessions, 'chromium_windows', lambda _pids: set(windows))
    monkeypatch.setattr(sessions, 'hide_chromium_window', lambda handle: timeline.append(('hidden', handle)))
    worker = sessions.BrowserWorker(Store(tmp_path / 'db.sqlite'), 1)
    attached = []

    def attach(index, handle):
        attached.append((index, handle))
        worker.confirm_window_attached(index)
        if len(attached) == 2:
            worker.request_shutdown()

    worker.window_ready.connect(attach)
    worker.ready.connect(lambda: worker.close_session(0))
    worker.window_closed.connect(lambda index: worker.open_session(index))
    asyncio.run(worker.operate())
    assert attached == [(0, 201), (0, 202)]
    assert ('closed', 201) in timeline


def test_batch_final_export_preserves_notice_integrity_reason(tmp_path):
    from collecte_cfe.documents import classify_pdf
    from collecte_cfe.sessions import run_batch

    store = Store(tmp_path / 'db.sqlite')
    dossier = Dossier('Exemple', '000000001', 'Léa')
    batch = store.create_batch([2025], tmp_path / 'sortie', [dossier], 1)
    campaign = store.batch_campaigns(batch)[0]['id']
    notice = Notice(2025, '123', '00021', '33', 'BORDEAUX', 'Rôle général', '15/12/2025')
    row = store.register_notice(campaign, dossier.siren, notice)
    source = tmp_path / 'avis.pdf'
    pdf = canvas.Canvas(str(source))
    pdf.drawString(50, 700, 'Avis')
    pdf.showPage()
    pdf.save()
    digest = classify_pdf(source, Path(row['path']), dossier, notice)
    store.notice_result(row['id'], 'TERMINE', '', digest)
    Path(row['path']).write_bytes(Path(row['path']).read_bytes() + b'alteration')
    messages = []

    class DisconnectedPortal:
        async def connected(self):
            return False

    asyncio.run(
        run_batch(store, batch, [DisconnectedPortal()], threading.Event(), messages.append, lambda *_args: None)
    )

    refusal = next(message for message in messages if 'Export Excel impossible' in message)
    assert '000000001' in refusal and 'relancez la collecte' in refusal.lower()
