"""Un moteur, plusieurs contextes éphémères, une tâche dossier/année par session."""

import asyncio
import hashlib
import tempfile
import threading
import traceback
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue
from urllib.parse import urlparse

from PySide6.QtCore import QThread, Signal

from .async_portal import AsyncPortal
from .collector import Collector
from .credentials import AccountManager
from .documents import export_batch_actions, export_batch_timings, proof_from_captures, recover_proof
from .domain import Dossier, safe_name
from .login import attempt_login
from .portal import HOME, AuthorizationError, PortalError, SessionExpired, is_tax_url
from .situations import manual_check
from .window_embed import chromium_windows, hide_chromium_window, process_tree

IDLE_REFRESH_SECONDS = 120
RECONNECT_GRACE_SECONDS = 10


def login_result_status(result, assigned):
    if result == 'connected':
        return 'Connectée'
    if result == 'refused':
        return 'Connexion refusée'
    if result == 'captcha_unreadable':
        return 'CAPTCHA à vérifier'
    return 'Connexion à vérifier' if assigned else 'À connecter'


async def login_assigned_account(portal, accounts, index, attempt=attempt_login, on_refresh=None):
    account = accounts.account_for_session(index)
    if account is None:
        return 'manual'
    if accounts.login_blocked(account['id']):
        return 'refused'
    try:
        password = accounts.password(account['id'])
    except Exception:
        return 'manual'
    if not password:
        return 'manual'
    if on_refresh is None:
        result = await attempt(portal, account['login'], password)
    else:
        result = await attempt(portal, account['login'], password, on_refresh=on_refresh)
    if result == 'refused':
        accounts.block_login(account['id'])
    return result


def reconnect_due(now, disconnected_since, last_activity, was_connected, attempted, collecting):
    return bool(
        was_connected
        and not attempted
        and not collecting
        and disconnected_since is not None
        and now - disconnected_since >= RECONNECT_GRACE_SECONDS
        and now - last_activity >= RECONNECT_GRACE_SECONDS
    )


async def reconnect_expired_session(portal, accounts, index, attempt=attempt_login, on_refresh=None):
    account = accounts.account_for_session(index)
    if account is None:
        return 'manual'
    if accounts.login_blocked(account['id']):
        return 'refused'
    page = portal.main
    if page.is_closed():
        return 'manual'
    if urlparse(page.url).hostname != 'cfspro-idp.impots.gouv.fr':
        try:
            await page.goto(HOME, wait_until='domcontentloaded')
        except Exception:
            return 'manual'
    try:
        if await portal.connected():
            return 'connected'
    except Exception:
        return 'manual'
    try:
        return await login_assigned_account(portal, accounts, index, attempt=attempt, on_refresh=on_refresh)
    except Exception:
        return 'manual'


def record_worker_failure(error, path):
    """Keep stack locations locally, never exception messages or browser state."""
    path = Path(path)
    frames = traceback.extract_tb(error.__traceback__)
    lines = [datetime.now().isoformat(timespec='seconds'), type(error).__name__]
    lines.extend(f'{Path(frame.filename).name}:{frame.lineno} {frame.name}' for frame in frames[-12:])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as report:
        report.write(' | '.join(lines) + '\n')


async def refresh_inactive_sessions(portals, last_activity, now, collecting=False, timeout=IDLE_REFRESH_SECONDS):
    """Actualise les sessions connectées restées sans activité, hors collecte."""
    from playwright.async_api import Error as PlaywrightError

    if collecting:
        return []
    refreshed = []
    for index, portal in enumerate(portals):
        if portal is None or now - last_activity[index] < timeout or portal.main.is_closed():
            continue
        try:
            if not await portal.connected():
                continue
            await portal.main.reload(wait_until='domcontentloaded')
            refreshed.append(index)
        except PlaywrightError:
            pass
        last_activity[index] = now
    return refreshed


async def install_activity_tracking(page, mark_activity, request_focus=lambda: None):
    """Observe seulement l'activité, sans lire ni conserver les données saisies."""
    if hasattr(page, 'expose_binding'):

        def observed(_source, event_type=None):
            mark_activity()
            if event_type == 'pointerdown':
                request_focus()

        await page.expose_binding('__collecteMarkActivity', observed)
    if hasattr(page, 'add_init_script'):
        await page.add_init_script("""() => {
            let last=0;
            const mark=() => {
                const now=Date.now();
                if (now-last<5000) return;
                last=now;
                if (window.__collecteMarkActivity) window.__collecteMarkActivity(event.type);
            };
            for (const event of ['pointerdown','keydown','input','wheel','touchstart'])
                addEventListener(event,mark,{capture:true,passive:true});
        }""")
    page.on('request', lambda _request: mark_activity())


async def apply_page_zoom(page, factor):
    if page.is_closed():
        return
    await page.evaluate(
        """(value) => {
        document.documentElement.style.zoom=String(value);
        document.documentElement.style.setProperty('overflow-x','auto','important');
        document.documentElement.style.setProperty('overscroll-behavior-x','none','important');
        if (document.body) {
            document.body.style.setProperty('overflow-x','visible','important');
            document.body.style.setProperty('overscroll-behavior-x','none','important');
            let spacer=document.getElementById('collecte-bottom-spacer');
            if (!spacer) {
                spacer=document.createElement('div'); spacer.id='collecte-bottom-spacer';
                spacer.setAttribute('aria-hidden','true'); document.body.appendChild(spacer);
            }
            Object.assign(spacer.style,{display:'block',clear:'both',width:'1px',height:'56px',
                pointerEvents:'none',visibility:'hidden',flex:'0 0 56px'});
        }
    }""",
        float(factor),
    )


async def guard_portal_request(route, request, on_blocked):
    if request.is_navigation_request() and not is_tax_url(request.url):
        await route.abort()
        on_blocked(request.url)
        return
    await route.continue_()


class PortalBridge:
    """Le collecteur ne reçoit ni objets Playwright ni informations de connexion."""

    def __init__(self, portal, loop):
        self.portal = portal
        self.loop = loop

    def call(self, operation, *args):
        return asyncio.run_coroutine_threadsafe(operation(*args), self.loop).result()

    def open_dossier(self, *args):
        return self.call(self.portal.open_dossier, *args)

    def download(self, *args):
        return self.call(self.portal.download, *args)

    def close_dossier(self):
        return self.call(self.portal.close_dossier)

    def capture_cfe_screen(self, *args):
        return self.call(self.portal.capture_cfe_screen, *args)

    def previous_notice(self, n):
        return self.portal.previous_notice(n)


def merge_discovered_dossiers(discovered):
    """Fusionne les périmètres sans conserver leur empreinte ni les données de session."""
    by_siren = {}
    for dossiers in discovered:
        for dossier in dossiers:
            by_siren.setdefault(dossier.siren, dossier.name)
    names = {}
    for siren, name in by_siren.items():
        names.setdefault(safe_name(name).casefold(), []).append(siren)
    return [
        Dossier(
            name if len(names[safe_name(name).casefold()]) == 1 else f'{safe_name(name)[:50]}_{siren}',
            siren,
            'À affecter',
        )
        for siren, name in by_siren.items()
    ]


def access_fingerprint(dossiers):
    sirens = '\n'.join(sorted({d.siren for d in dossiers}))
    return hashlib.sha256(sirens.encode('ascii')).hexdigest()


async def file_no_access_proof(store, campaign, row, portals, progress):
    """Keep in the client's folder each connected account's search showing it cannot reach this SIREN."""
    d = Dossier(row['name'], row['siren'], row['collaborator'])
    year = store.campaign(campaign)['year']
    with tempfile.TemporaryDirectory(prefix='Collecte-CFE-habilitation-') as folder:
        captures = []
        for index, portal in enumerate(portals):
            capture = Path(folder) / f'session-{index + 1}.png'
            if not hasattr(portal, 'capture_search') or not await portal.capture_search(d.siren, capture):
                continue
            account = store.assigned_expert_account(index)
            who = f'compte {account["label"]} ({account["login"]})' if account else f'session {index + 1}'
            captures.append((f'Recherche du SIREN dans la liste « Avis CFE » du {who} :', capture))
        if not captures:
            progress(f'{d.name} · Sans habilitation : aucune capture de la recherche n’a pu être faite.')
            return
        try:
            target = store.register_proof(campaign, d.siren, 'SANS_HABILITATION')
            digest = recover_proof(target, d, year, 'SANS_HABILITATION')
            if digest is None:
                digest = proof_from_captures(target, d, year, captures, 'SANS_HABILITATION')
            store.proof_result(campaign, d.siren, digest)
        except Exception:
            progress(f'{d.name} · Sans habilitation : la capture de la recherche n’a pas pu être enregistrée.')


async def run_batch(
    store, batch, portals, stop, progress, session_state, only_errors=False, selected_dossiers=None, capture_dir=None
):
    """Share the dossiers between the sessions, each taking the next one it can open."""
    campaigns = store.batch_campaigns(batch)
    if not campaigns:
        raise ValueError('Campagne introuvable.')
    batch_info = store.batch(batch)
    direct_search = batch_info['source'] == 'FILE'
    expired = False
    interrupted = False
    try:
        for index, portal in enumerate(portals):
            if not await portal.connected():
                session_state(index, 'À connecter')
                raise SessionExpired(f'Connectez la session {index + 1} avant de lancer la collecte.')

        if direct_search:
            scopes = [None] * len(portals)
            attempt_keys = [f'session-{index}' for index in range(len(portals))]
            group_labels = {}
            for index in range(len(portals)):
                session_state(index, 'Connectée · recherche directe')
            progress('Recherche directe de chaque SIREN du portefeuille.')
        else:

            async def discover(index, portal):
                session_state(index, 'Analyse des habilitations')
                return await portal.discover_dossiers(
                    lambda message: progress(f'Session {index + 1} · {message}'), stop
                )

            discovered = await asyncio.gather(*(discover(i, p) for i, p in enumerate(portals)))
            scopes = [{d.siren for d in dossiers} for dossiers in discovered]
            fingerprints = [access_fingerprint(dossiers) for dossiers in discovered]
            attempt_keys = fingerprints
            group_labels = {
                fingerprint: chr(64 + number) for number, fingerprint in enumerate(dict.fromkeys(fingerprints), 1)
            }
            for index, fingerprint in enumerate(fingerprints):
                session_state(index, f'Connectée · périmètre {group_labels[fingerprint]}')
            progress(f'{len(group_labels)} périmètre(s) de compte détecté(s) dans {len(portals)} session(s).')
            if not batch_info['discovered']:
                dossiers = merge_discovered_dossiers(discovered)
                store.populate_batch(batch, dossiers)

        jobs = []
        for campaign in campaigns:
            store.campaign_status(campaign['id'], 'EN_COURS')
            for row in store.dossiers(campaign['id']):
                if selected_dossiers is not None and (campaign['id'], row['siren']) not in selected_dossiers:
                    continue
                # A dossier failed twice waits for a manual check; only a relaunch chosen in its sheet tries it again.
                if selected_dossiers is None and manual_check(row):
                    continue
                if only_errors:
                    eligible = row['status'] == 'ERREUR'
                elif row['status'] == 'TERMINE':
                    notices = store.notices(campaign['id'], row['siren'])
                    eligible = not notices or any(not Collector.completed(n) for n in notices)
                elif row['status'] == 'ABSENT':
                    eligible = not Collector.proof_completed(row)
                else:
                    eligible = True
                if eligible:
                    jobs.append({'campaign': campaign['id'], 'row': row, 'state': 'pending', 'attempted': set()})
        total = len(jobs)
        done = 0
        running = 0
        loop = asyncio.get_running_loop()
        condition = asyncio.Condition()

        async def claim(index):
            nonlocal running
            async with condition:
                while not stop.is_set():
                    attempt_key = attempt_keys[index]
                    for job in jobs:
                        accessible = scopes[index] is None or job['row']['siren'] in scopes[index]
                        if job['state'] == 'pending' and accessible and attempt_key not in job['attempted']:
                            job['state'] = 'running'
                            job['owner'] = index
                            running += 1
                            return job
                    if running == 0:
                        return None
                    await condition.wait()
                return None

        async def finish(job, *, retry=False):
            nonlocal running, done
            async with condition:
                running -= 1
                if retry:
                    job['attempted'].add(attempt_keys[job['owner']])
                    job['state'] = 'pending'
                else:
                    job['state'] = 'done'
                    done += 1
                condition.notify_all()

        async def lane(index, portal):
            nonlocal done, expired, interrupted
            bridge = PortalBridge(portal, loop)
            while not stop.is_set():
                job = await claim(index)
                if job is None:
                    break
                campaign, row = job['campaign'], job['row']
                year = store.campaign(campaign)['year']
                session_state(index, f'{row["name"]} · {year}')
                retry = False
                try:
                    result = await asyncio.to_thread(
                        Collector(store, bridge).run,
                        campaign,
                        stop,
                        lambda message, year=year: progress(f'Session {index + 1} · {year} · {message}'),
                        dossiers=[row],
                        finalize=False,
                        verify_source=True,
                        session_number=index + 1,
                        capture_dir=capture_dir,
                    )
                    if result == 'CONNEXION_REQUISE':
                        expired = True
                        stop.set()
                        session_state(index, 'À reconnecter')
                    elif result == 'PAUSE':
                        interrupted = True
                except AuthorizationError:
                    retry = True
                    location = (
                        f'le périmètre {group_labels[attempt_keys[index]]}' if not direct_search else 'ce compte expert'
                    )
                    progress(
                        f'Session {index + 1} · {row["siren"]} sans habilitation sur {location} ; recherche dans une autre session.'
                    )
                except Exception:
                    interrupted = True
                    stop.set()
                    store.dossier_result(
                        campaign, row['siren'], 'EN_ATTENTE', 'Traitement interrompu. Reprenez la campagne.'
                    )
                    progress(f'Session {index + 1} · Collecte interrompue ; les avis enregistrés sont conservés.')
                finally:
                    await finish(job, retry=retry)
                    progress(f'Avancement · {done} / {total} tâches dossier / année traitées')
            if not expired:
                if await portal.connected():
                    state = (
                        'Connectée · recherche directe'
                        if direct_search
                        else f'Connectée · périmètre {group_labels[attempt_keys[index]]}'
                    )
                else:
                    state = 'À reconnecter'
                session_state(index, state)

        await asyncio.gather(*(lane(i, p) for i, p in enumerate(portals)))
        if not stop.is_set():
            for job in jobs:
                if job['state'] == 'pending':
                    store.dossier_result(
                        job['campaign'],
                        job['row']['siren'],
                        'SANS_HABILITATION',
                        'Aucune habilitation sur les comptes connectés.',
                    )
                    job['state'] = 'done'
                    done += 1
                    await file_no_access_proof(store, job['campaign'], job['row'], portals, progress)
            if done:
                progress(f'Avancement · {done} / {total} tâches dossier / année traitées')
    except SessionExpired as e:
        expired = True
        stop.set()
        progress(str(e))
    except (PortalError, ValueError) as e:
        interrupted = True
        progress(str(e))
    except Exception:
        interrupted = True
        stop.set()
        progress('Collecte interrompue. Les résultats sont conservés.')
    finally:
        finalized = []
        for campaign in campaigns:
            rows = store.dossiers(campaign['id'])
            pending = any(d['status'] in ('EN_ATTENTE', 'EN_COURS') for d in rows)
            status = (
                'CONNEXION_REQUISE'
                if expired
                else 'PAUSE'
                if interrupted or stop.is_set() or pending or not rows
                else 'AVEC_ERREURS'
                if any(d['status'] in ('ERREUR', 'SANS_HABILITATION') for d in rows)
                else 'TERMINE'
            )
            store.campaign_status(campaign['id'], status)
            finalized.append(status)
        if store.batch(batch)['discovered']:
            try:
                await asyncio.to_thread(export_batch_actions, store, batch)
            except ValueError as error:
                progress(f'Les résultats sont enregistrés. Export Excel impossible : {error}')
            except Exception:
                progress(
                    'Les résultats sont enregistrés. Export Excel impossible : fermez les classeurs ouverts et relancez l’export.'
                )
        try:
            timing_path = await asyncio.to_thread(export_batch_timings, store, batch)
            progress(f'Mesures enregistrées · {timing_path.name}')
        except Exception:
            progress('Les mesures restent enregistrées. Le fichier des temps n’a pas pu être actualisé.')
        progress(
            'Reconnectez les sessions pour reprendre.'
            if expired
            else 'Collecte en pause. Des dossiers restent à traiter.'
            if 'PAUSE' in finalized
            else 'Collecte terminée.'
        )


class BrowserWorker(QThread):
    ready = Signal()
    message = Signal(str)
    collecting = Signal(bool)
    failed = Signal(str)
    session_changed = Signal(int, str)
    window_ready = Signal(int, object)
    window_closed = Signal(int)
    sessions_resized = Signal(int)
    url_changed = Signal(int, str)
    navigation_blocked = Signal(int)
    focus_requested = Signal(int)
    captcha_refreshed = Signal(int, int)

    def __init__(self, store, count=2, accounts=None, initial_sessions=None):
        super().__init__()
        self.store = store
        self.count = count
        self.commands = Queue()
        self.accounts = accounts or AccountManager(store)
        self.initial_sessions = list(range(count)) if initial_sessions is None else list(initial_sessions)
        self.stop = threading.Event()
        self.shutdown = threading.Event()
        self.window_attached = [threading.Event() for _ in range(max(4, count))]

    def run(self):
        try:
            asyncio.run(self.operate())
        except Exception as error:
            try:
                record_worker_failure(error, self.store.path.parent / 'moteur-erreurs.txt')
            except OSError:
                pass
            self.failed.emit('Le moteur de navigation a été fermé ou n’a pas pu démarrer. Rouvrez les sessions.')

    async def operate(self):
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            launch_args = [
                '--window-position=-32000,-32000',
                '--overscroll-history-navigation=0',
                '--disable-background-timer-throttling',
                '--disable-backgrounding-occluded-windows',
                '--disable-renderer-backgrounding',
            ]
            try:
                browser = await p.chromium.launch(channel='msedge', headless=False, args=launch_args)
            except Exception:
                browser = await p.chromium.launch(headless=False, args=launch_args)
            driver_pid = browser._impl_obj._connection._transport._proc.pid
            known_windows = set()
            portals = []
            zoom_levels = []
            scope_labels = {}
            login_states = []
            loop = asyncio.get_running_loop()
            last_activity = []
            collecting_active = False
            ever_connected = []
            disconnected_since = []
            reconnect_attempted = []

            def grow(size):
                """One slot per session, as many as there are expert accounts (four at least)."""
                while len(portals) < size:
                    portals.append(None)
                    zoom_levels.append(1.0)
                    login_states.append('Non ouverte')
                    last_activity.append(loop.time())
                    ever_connected.append(False)
                    disconnected_since.append(None)
                    reconnect_attempted.append(False)
                while len(self.window_attached) < size:
                    self.window_attached.append(threading.Event())

            grow(max(4, self.count))
            try:

                async def connect_account(index, portal):
                    assigned = self.accounts.account_for_session(index) is not None
                    if assigned:
                        login_states[index] = 'Connexion en cours…'
                        self.session_changed.emit(index, login_states[index])
                    result = await login_assigned_account(
                        portal, self.accounts, index, on_refresh=lambda count: self.captcha_refreshed.emit(index, count)
                    )
                    login_states[index] = login_result_status(result, assigned)
                    if result == 'connected':
                        ever_connected[index] = True
                        disconnected_since[index] = None
                        reconnect_attempted[index] = False
                    self.session_changed.emit(index, login_states[index])
                    if result == 'refused':
                        self.message.emit(
                            f'Session {index + 1} · identifiants refusés. Modifiez le compte enregistré avant un nouvel essai automatique.'
                        )
                    elif result == 'captcha_unreadable':
                        self.message.emit(
                            f'Session {index + 1} · CAPTCHA non résolu après les régénérations. Terminez la connexion dans la session.'
                        )
                    elif result == 'manual' and assigned:
                        self.message.emit(
                            f'Session {index + 1} · connexion automatique non aboutie. Vérifiez la page pour terminer manuellement.'
                        )
                    return result

                async def create_session(index):
                    nonlocal known_windows
                    grow(index + 1)
                    if portals[index] is not None:
                        return portals[index]
                    context = await browser.new_context(accept_downloads=True, locale='fr-FR', no_viewport=True)

                    async def route_handler(route, request, session_index=index):
                        await guard_portal_request(
                            route, request, lambda _url: self.navigation_blocked.emit(session_index)
                        )

                    await context.route('**/*', route_handler)
                    page = await context.new_page()

                    def mark_activity(session_index=index):
                        last_activity[session_index] = loop.time()

                    await install_activity_tracking(
                        page, mark_activity, lambda session_index=index: self.focus_requested.emit(session_index)
                    )

                    async def navigated(frame, session_index=index, session_page=page):
                        if frame == session_page.main_frame:
                            self.url_changed.emit(session_index, frame.url)
                            await apply_page_zoom(session_page, zoom_levels[session_index])

                    page.on('framenavigated', navigated)
                    handle = None
                    for _ in range(100):
                        current = chromium_windows(process_tree(driver_pid))
                        known_windows.intersection_update(current)
                        candidates = current - known_windows
                        if len(candidates) == 1:
                            handle = candidates.pop()
                            known_windows.update(current)
                            break
                        if len(candidates) > 1:
                            raise RuntimeError('Association ambiguë des fenêtres Chromium.')
                        await asyncio.sleep(0.05)
                    if handle is None:
                        raise RuntimeError('Fenêtre Chromium introuvable pour la session intégrée.')
                    self.window_attached[index].clear()
                    hide_chromium_window(handle)
                    self.window_ready.emit(index, handle)
                    attached = await asyncio.to_thread(self.window_attached[index].wait, 5)
                    if not attached:
                        raise RuntimeError('La fenêtre Chromium n’a pas pu être intégrée à l’application.')
                    await page.goto(HOME, wait_until='domcontentloaded')
                    portals[index] = AsyncPortal(context, page)
                    last_activity[index] = loop.time()
                    await connect_account(index, portals[index])
                    return portals[index]

                async def close_session(index):
                    if not 0 <= index < len(portals) or portals[index] is None:
                        return
                    portal = portals[index]
                    portals[index] = None
                    scope_labels.pop(index, None)
                    login_states[index] = 'Non ouverte'
                    ever_connected[index] = False
                    disconnected_since[index] = None
                    reconnect_attempted[index] = False
                    await portal.context.close()
                    self.window_closed.emit(index)
                    self.session_changed.emit(index, 'Non ouverte')

                async def resize_sessions(target):
                    target = max(1, int(target))
                    grow(target)
                    if target > self.count:
                        for index in range(self.count, target):
                            await create_session(index)
                    elif target < self.count:
                        for index in range(self.count - 1, target - 1, -1):
                            await close_session(index)
                    self.count = target
                    self.sessions_resized.emit(target)

                for index in self.initial_sessions:
                    await create_session(index)
                self.ready.emit()
                self.message.emit('Sessions ouvertes.')
                last_check = 0
                while not self.shutdown.is_set():
                    try:
                        command, argument = self.commands.get_nowait()
                    except Empty:
                        command = None
                    if command == 'login':
                        index = argument or 0
                        grow(index + 1)
                        disconnected_since[index] = None
                        reconnect_attempted[index] = False
                        if portals[index] is None or portals[index].main.is_closed():
                            if portals[index] is not None:
                                await close_session(index)
                            portal = await create_session(index)
                            await portal.main.bring_to_front()
                        else:
                            portal = portals[index]
                            await portal.main.bring_to_front()
                            if not await portal.connected():
                                account = self.accounts.account_for_session(index)
                                if account is not None and self.accounts.login_blocked(account['id']):
                                    login_states[index] = 'Connexion refusée'
                                    self.session_changed.emit(index, login_states[index])
                                else:
                                    await portal.main.goto(HOME, wait_until='domcontentloaded')
                                    await connect_account(index, portal)
                    elif command == 'zoom':
                        index, factor = argument
                        zoom_levels[index] = factor
                        await apply_page_zoom(portals[index].main, factor)
                    elif command == 'reload':
                        index = argument
                        if portals[index] is not None:
                            await portals[index].main.reload(wait_until='domcontentloaded')
                            last_activity[index] = loop.time()
                    elif command == 'close':
                        await close_session(argument)
                    elif command == 'open':
                        await create_session(argument)
                    elif command == 'sessions':
                        await resize_sessions(argument)
                        self.message.emit(f'{self.count} session(s) prête(s).')
                    elif command == 'collect':
                        collecting_active = True
                        self.collecting.emit(True)

                        def state(index, text):
                            if text.startswith('Connectée · périmètre'):
                                scope_labels[index] = text
                            elif text in ('À reconnecter', 'À connecter'):
                                scope_labels.pop(index, None)
                            self.session_changed.emit(index, text)

                        try:
                            active = portals[: self.count]
                            if any(portal is None for portal in active):
                                raise SessionExpired('Rouvrez toutes les sessions avant de lancer la collecte.')
                            await run_batch(
                                self.store,
                                argument[0],
                                active,
                                self.stop,
                                self.message.emit,
                                state,
                                argument[1],
                                argument[2],
                                argument[3],
                            )
                        finally:
                            collecting_active = False
                            self.collecting.emit(False)
                    if asyncio.get_running_loop().time() - last_check > 2:
                        now = asyncio.get_running_loop().time()
                        for index, portal in enumerate(portals[: self.count]):
                            if portal is not None:
                                if await portal.connected():
                                    login_states[index] = 'Connectée'
                                    ever_connected[index] = True
                                    disconnected_since[index] = None
                                    reconnect_attempted[index] = False
                                    self.session_changed.emit(index, scope_labels.get(index, 'Connectée'))
                                else:
                                    if disconnected_since[index] is None:
                                        disconnected_since[index] = now
                                    if reconnect_due(
                                        now,
                                        disconnected_since[index],
                                        last_activity[index],
                                        ever_connected[index],
                                        reconnect_attempted[index],
                                        collecting_active,
                                    ):
                                        reconnect_attempted[index] = True
                                        login_states[index] = 'Reconnexion en cours…'
                                        self.session_changed.emit(index, login_states[index])
                                        result = await reconnect_expired_session(
                                            portal,
                                            self.accounts,
                                            index,
                                            on_refresh=lambda count, session=index: self.captcha_refreshed.emit(
                                                session, count
                                            ),
                                        )
                                        login_states[index] = login_result_status(result, True)
                                        self.session_changed.emit(index, login_states[index])
                                        if result == 'refused':
                                            self.message.emit(
                                                f'Session {index + 1} · reconnexion refusée. Modifiez le compte enregistré avant un nouvel essai.'
                                            )
                                        elif result != 'connected':
                                            self.message.emit(
                                                f'Session {index + 1} · reconnexion à terminer dans le navigateur.'
                                            )
                                    else:
                                        self.session_changed.emit(
                                            index,
                                            'À reconnecter'
                                            if ever_connected[index] and not reconnect_attempted[index]
                                            else login_states[index],
                                        )
                        await refresh_inactive_sessions(portals[: self.count], last_activity, now, collecting_active)
                        last_check = now
                    await asyncio.sleep(0.1)
            finally:
                # No profile, storage_state, trace, credential or session export is written.
                await browser.close()

    def submit_campaign(self, batch, only_errors=False, selected_dossiers=None, capture_dir=None):
        self.stop.clear()
        self.commands.put(('collect', (batch, only_errors, selected_dossiers, capture_dir)))

    def set_zoom(self, index, factor):
        self.commands.put(('zoom', (index, float(factor))))

    def reload(self, index):
        self.commands.put(('reload', index))

    def close_session(self, index):
        self.commands.put(('close', int(index)))

    def open_session(self, index):
        self.commands.put(('open', int(index)))

    def resize_sessions(self, count):
        self.commands.put(('sessions', int(count)))

    def confirm_window_attached(self, index):
        if 0 <= index < len(self.window_attached):
            self.window_attached[index].set()

    def request_shutdown(self):
        self.stop.set()
        self.shutdown.set()
