import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from threading import Event, Thread
from urllib.parse import parse_qs, urlparse

import pytest

TABLE = 'form:tableauAvisImposition'
HOME_PATH = '/mire/accueil.do'
NOTICE_PATH = '/adelie2mapi/xhtml/impots/cfe/avis_cfe.xhtml'


def pdf_bytes(text):
    from reportlab.pdfgen import canvas

    buffer = BytesIO()
    document = canvas.Canvas(buffer)
    document.drawString(48, 740, text)
    document.showPage()
    document.save()
    return buffer.getvalue()


def notice_row(index, year, reference):
    """A row shaped like the portal's: 13 cells, the download link in the 8th."""
    link = f'{TABLE}:{index}:j_idt154'
    return (
        f'<tr data-ri="{index}"><td>{year}</td><td>{year}</td><td>75</td><td>VILLE DE PARIS</td><td>00018</td>'
        f'<td>Avis d&#39;imposition</td><td>{reference}</td><td><a id="{link}" href="#" '
        f"onclick=\"PrimeFaces.addSubmitParam('form',{{'{link}':'{link}'}}).submit('form');return false;\">Rôle général n°092</a></td>"
        f'<td>31/10/{year}</td><td>15/12/{year}</td><td></td><td></td><td>false</td></tr>'
    )


class Portal:
    """Local stand-in for the professional space: same paths, token, tiles, PrimeFaces table and PDF post."""

    def __init__(self, dossiers=None, notices=None, discovery_total=None, expire_on=None):
        self.dossiers = dossiers or {'000000001': 'SAS Exemple'}
        self.notices = notices if notices is not None else {}
        self.discovery_total = discovery_total
        self.expire_on = expire_on
        self.shown_siren = None
        self.token = 'TOK-1'
        self.selected = None
        self.first = 0
        self.rows = 15
        self.events = []
        portal = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, body, content_type='text/html; charset=utf-8', status=200, headers=()):
                self.send_response(status)
                self.send_header('Content-Type', content_type)
                for name, value in headers:
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body if isinstance(body, bytes) else body.encode())

            def redirect(self, location):
                self.respond('', status=302, headers=(('Location', location),))

            def do_GET(self):
                path = urlparse(self.path)
                query = {k: v[0] for k, v in parse_qs(path.query).items()}
                if portal.expire_on and portal.expire_on in path.path:
                    # The login page lives on another domain: the browser refuses the redirected fetch.
                    return self.redirect(f'http://localhost:{portal.port}/login')
                if path.path == HOME_PATH:
                    return self.respond(
                        f'<button>Me déconnecter</button><a href="#">Avis CFE</a><script>function injectTokens(){{}} injectTokens("token", "{portal.token}", [])</script>'
                    )
                if path.path == '/mire/afficherSelectionDossier.do':
                    if query.get('token') != portal.token:
                        return self.redirect('/mire/erreurCsrf.do')
                    if 'siren' in query:
                        portal.events.append(f'search:{query["siren"]}')
                        page = [(s, n) for s, n in portal.dossiers.items() if s.startswith(query['siren'])]
                        pages = 1
                        total = len(page)
                    else:
                        items = list(portal.dossiers.items())
                        number = int(query.get('p', '1'))
                        pages = max(1, (len(items) + 9) // 10)
                        page = items[(number - 1) * 10 : number * 10]
                        total = portal.discovery_total if portal.discovery_total is not None else len(items)
                    if not page:
                        return self.respond(
                            '<h2>Liste des dossiers</h2><p>Aucun dossier ne correspond à votre recherche.</p>'
                        )
                    tiles = ''.join(
                        f'<h3 class="fr-tile__title"><a href="dossierSelect.do?idth=consulter.avis.cfe&amp;idDossier={s}">{n}</a></h3><p>SIREN&nbsp;{s}</p>'
                        for s, n in page
                    )
                    return self.respond(
                        f'<h2>Liste des dossiers</h2><p><span>{total}</span>&nbsp;résultat(s) | Page 1 sur {pages}</p>{tiles}'
                    )
                if path.path == '/mire/dossierSelect.do':
                    if query.get('token') != portal.token:
                        return self.redirect('/mire/erreurCsrf.do')
                    portal.selected = query['idDossier']
                    portal.first = 0
                    portal.rows = 15
                    portal.events.append(f'select:{portal.selected}')
                    return self.redirect('/adelie2mapi/xhtml/accueil/accueil.xhtml')
                if path.path == '/adelie2mapi/xhtml/accueil/accueil.xhtml':
                    return self.respond(f'<div id="sideMenu">SIREN ou IDSP :<br>{portal.selected}</div>')
                if path.path == '/mire/erreurCsrf.do':
                    return self.respond('<p>Erreur</p>')
                if path.path == NOTICE_PATH:
                    rows = (
                        portal.table_rows()
                        or '<tr><td colspan="12">Aucune information n’a été trouvée concernant votre choix.</td></tr>'
                    )
                    count = len(portal.notices.get(portal.selected, []))
                    return self.respond(f'''<div id="sideMenu">SIREN ou IDSP : {portal.shown_siren or portal.selected}</div>
                        <form id="form" method="post"><table><tbody id="{TABLE}_data" class="ui-datatable-data">{rows}</tbody></table>
                        <input type="hidden" name="javax.faces.ViewState" id="j_id1:javax.faces.ViewState:0" value="VS-0" autocomplete="off" /></form>
                        <script>PrimeFaces.cw("DataTable","widget",{{id:"{TABLE}",paginator:{{rows:15,rowCount:{count},page:0}}}});</script>''')
                self.send_error(404)

            def do_POST(self):
                form = {
                    k: v[0]
                    for k, v in parse_qs(self.rfile.read(int(self.headers.get('Content-Length', '0'))).decode()).items()
                }
                if urlparse(self.path).path != NOTICE_PATH or not form.get('javax.faces.ViewState', '').startswith(
                    'VS-'
                ):
                    return self.send_error(400)
                if form.get('javax.faces.partial.ajax') == 'true':
                    portal.first = int(form[f'{TABLE}_first'])
                    portal.rows = int(form[f'{TABLE}_rows'])
                    portal.events.append(f'page:{portal.first}')
                    xml = (
                        f'<?xml version="1.0" encoding="UTF-8"?><partial-response><changes><update id="{TABLE}"><![CDATA[{portal.table_rows()}]]></update>'
                        f'<update id="j_id1:javax.faces.ViewState:0"><![CDATA[VS-{portal.first}]]></update></changes></partial-response>'
                    )
                    return self.respond(xml, 'text/xml;charset=UTF-8')
                links = [key for key in form if key.startswith(f'{TABLE}:')]
                # Like the portal, a link only works while its row is on the page the server shows.
                if len(links) != 1 or not portal.first <= int(links[0].split(':')[2]) < portal.first + portal.rows:
                    return self.respond('<p>Page expirée</p>')
                year, reference = portal.notices[portal.selected][int(links[0].split(':')[2])]
                portal.events.append(f'pdf:{reference}')
                return self.respond(
                    pdf_bytes(f'SIREN {portal.selected} {year} {reference}'),
                    'application/pdf',
                    headers=(('Content-Disposition', 'attachment;filename="AvisCfe.pdf"'),),
                )

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.port = self.server.server_port
        self.thread = Thread(target=self.server.serve_forever, daemon=True)

    def table_rows(self):
        items = self.notices.get(self.selected, [])
        return ''.join(
            notice_row(i, year, ref) for i, (year, ref) in enumerate(items) if self.first <= i < self.first + self.rows
        )

    @property
    def home(self):
        return f'http://127.0.0.1:{self.port}{HOME_PATH}'

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


def run_with_portal(monkeypatch, portal, scenario):
    from playwright.async_api import async_playwright

    from collecte_cfe import async_portal

    monkeypatch.setattr(async_portal, 'HOME', portal.home)

    async def main():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                context = await browser.new_context()
                page = await context.new_page()
                await scenario(async_portal.AsyncPortal(context, page), page, context)
            finally:
                await browser.close()

    with portal:
        asyncio.run(main())


def test_dossier_id_reads_the_exact_siren_of_a_tile_link():
    from collecte_cfe.async_portal import dossier_id

    assert dossier_id('dossierSelect.do?idth=consulter.avis.cfe&idDossier=000000001&token=T') == '000000001'
    assert dossier_id('dossierSelect.do?idth=consulter.avis.cfe&idDossier=0000000012&token=T') != '000000001'
    assert dossier_id(None) == ''


def test_discovered_dossiers_deduplicate_and_avoid_folder_collisions():
    from collecte_cfe.async_portal import discovered_dossiers

    ds = discovered_dossiers(
        ['SAS Exemple SIREN 000000001', 'SAS Exemple SIREN 000000002', 'SAS Exemple SIREN 000000001']
    )
    assert len(ds) == 2 and {d.siren for d in ds} == {'000000001', '000000002'}
    assert len({d.name for d in ds}) == 2 and all(d.collaborator == 'À affecter' for d in ds)
    with pytest.raises(ValueError):
        discovered_dossiers(['SAS Exemple SIREN inconnu'])


def test_connection_passes_the_landing_page_and_knows_the_logout_button(monkeypatch):
    """After login the portal stops on « Accéder à mon espace professionnel »; « Me déconnecter » is now a button."""
    from playwright.async_api import async_playwright

    from collecte_cfe import async_portal

    home = 'https://cfspro.impots.gouv.fr/mire/accueil.do'
    monkeypatch.setattr(async_portal, 'HOME', home)

    async def scenario():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                context = await browser.new_context()
                main = await context.new_page()

                async def serve(route):
                    if route.request.url.startswith(home):
                        body = '<button aria-controls="modal-logout">Me déconnecter</button><script>injectTokens("token", "T", [])</script>'
                    elif 'adelie2mapi' in route.request.url:
                        body = '<div id="sideMenu">SIREN ou IDSP : 000000001</div>'
                    else:
                        body = f'<p>Pour accéder à votre espace professionnel, veuillez cliquer sur le bouton ci-dessous.</p><a id="retourProPriv" href="{home}">Accéder à mon espace professionnel</a>'
                    await route.fulfill(status=200, content_type='text/html; charset=utf-8', body=body)

                await context.route('https://cfspro.impots.gouv.fr/**', serve)
                await main.goto('https://cfspro.impots.gouv.fr/?openidconnectcallback=1')
                portal = async_portal.AsyncPortal(context, main)
                assert await portal.connected()
                assert main.url == home
                # The fiscal account pages have no logout control: the portal home is asked instead.
                await main.goto('https://cfspro.impots.gouv.fr/adelie2mapi/xhtml/accueil/accueil.xhtml')
                assert await portal.connected()
                await main.goto('about:blank')
                await main.set_content('<button>Se connecter</button>')
                assert not await portal.connected()
            finally:
                await browser.close()

    asyncio.run(scenario())


def test_open_dossier_reads_every_page_of_the_table_and_downloads_by_request(monkeypatch, tmp_path):
    """60 notices span two server pages: the reader pages the table and shows the right page before each PDF."""
    from pypdf import PdfReader

    notices = [(2025 if i % 2 else 2024, f'REF-{i:03}') for i in range(60)]
    portal = Portal(notices={'000000001': notices})

    async def scenario(reader, main, context):
        found = await reader.open_dossier('000000001', 2025)
        assert len(found) == 30 and {n.year for n in found} == {2025}
        assert {n.reference for n in found} == {ref for year, ref in notices if year == 2025}
        assert len(context.pages) == 1 and main.url == portal.home, 'no page is opened or navigated to read the notices'
        late = next(n for n in found if n.reference == 'REF-055')
        early = next(n for n in found if n.reference == 'REF-001')
        for n, name in ((late, 'late.pdf'), (early, 'early.pdf'), (late, 'again.pdf')):
            await reader.download(n, tmp_path / name)
            assert n.reference in PdfReader(tmp_path / name).pages[0].extract_text()
        assert late.nic == '00018' and late.department == '75' and late.commune == 'VILLE DE PARIS'

    run_with_portal(monkeypatch, portal, scenario)
    assert portal.events[:2] == ['search:000000001', 'select:000000001']
    assert portal.events.count('page:0') == 2 and portal.events.count('page:50') == 2


def test_open_dossier_reports_absence_authorisation_and_session_loss(monkeypatch):
    from collecte_cfe.portal import AuthorizationError, PortalError, SessionExpired

    portal = Portal(
        dossiers={'000000001': 'SAS Exemple', '000000002': 'SAS Autre'}, notices={'000000002': [(2024, 'REF-1')]}
    )

    async def scenario(reader, main, context):
        assert await reader.open_dossier('000000001', 2025) == [], 'the portal says no information was found'
        assert await reader.open_dossier('000000002', 2025) == [], 'notices of other years only'
        with pytest.raises(AuthorizationError):
            await reader.open_dossier('000000009', 2025)
        portal.token = 'TOK-2'
        assert await reader.open_dossier('000000002', 2024), 'the token is read again for each dossier'
        portal.shown_siren = '000000001'
        with pytest.raises(PortalError, match='SIREN affiché'):
            await reader.open_dossier('000000002', 2024)
        portal.shown_siren = None
        portal.expire_on = '/mire/dossierSelect.do'
        with pytest.raises(SessionExpired):
            await reader.open_dossier('000000002', 2024)

    run_with_portal(monkeypatch, portal, scenario)


@pytest.mark.parametrize('mode', ['complete', 'incomplete', 'expired', 'pause'])
def test_discovery_reads_every_page_of_the_cfe_list(monkeypatch, mode):
    from collecte_cfe.portal import PortalError, SessionExpired

    dossiers = {f'{i:09}': 'SAS Exemple' for i in range(1, 13)}
    portal = Portal(
        dossiers=dossiers,
        discovery_total=13 if mode == 'incomplete' else None,
        expire_on='afficherSelectionDossier' if mode == 'expired' else None,
    )

    async def scenario(reader, main, context):
        stop = Event()
        events = []

        def progress(message):
            events.append(message)
            if mode == 'pause':
                stop.set()

        if mode == 'complete':
            found = await reader.discover_dossiers(progress, stop)
            assert {d.siren for d in found} == set(dossiers) and len({d.name for d in found}) == 12
            assert len(events) == 2
        else:
            with pytest.raises(SessionExpired if mode == 'expired' else PortalError):
                await reader.discover_dossiers(progress, stop)

    run_with_portal(monkeypatch, portal, scenario)


def test_capture_shows_the_cfe_page_of_the_dossier_only(monkeypatch, tmp_path):
    portal = Portal(notices={'000000001': [(2025, 'REF-1')]})

    async def scenario(reader, main, context):
        assert not await reader.capture_cfe_screen(tmp_path / 'none.png'), 'no dossier is open'
        await reader.open_dossier('000000001', 2025)
        assert await reader.capture_cfe_screen(tmp_path / 'page.png')
        assert (tmp_path / 'page.png').read_bytes().startswith(b'\x89PNG') and NOTICE_PATH in main.url
        portal.shown_siren = '000000002'
        await main.goto(portal.home)
        assert not await reader.capture_cfe_screen(tmp_path / 'other.png')

    run_with_portal(monkeypatch, portal, scenario)


def test_search_capture_only_when_the_portal_finds_no_dossier(monkeypatch, tmp_path):
    portal = Portal(dossiers={'000000001': 'SAS Exemple'})

    async def scenario(reader, main, context):
        assert await reader.capture_search('000000009', tmp_path / 'none.png')
        assert (tmp_path / 'none.png').read_bytes().startswith(b'\x89PNG')
        assert not await reader.capture_search('000000001', tmp_path / 'found.png'), (
            'an accessible dossier is not proof of no access'
        )
        assert not (tmp_path / 'found.png').exists()

    run_with_portal(monkeypatch, portal, scenario)


def test_a_read_survives_the_session_window_changing_page(monkeypatch):
    """Someone clicks in the session window during a read: the read waits for the new page and runs again."""
    from playwright.async_api import Error as PlaywrightError

    from collecte_cfe import async_portal

    monkeypatch.setattr(async_portal, 'HOME', 'https://cfspro.impots.gouv.fr/mire/accueil.do')

    class Count:
        async def count(self):
            return 0

        async def inner_text(self, **_):
            return 'Mes services'

    class Window:
        url = 'https://cfspro.impots.gouv.fr/mire/accueil.do'
        calls = 0
        closed = False

        def is_closed(self):
            return self.closed

        def get_by_role(self, *_args, **_kwargs):
            return Count()

        def locator(self, *_args, **_kwargs):
            return Count()

        async def wait_for_load_state(self, *_args, **_kwargs):
            pass

        async def evaluate(self, _script, arguments):
            self.calls += 1
            if self.calls == 1:
                raise PlaywrightError('Execution context was destroyed, most likely because of a navigation')
            return {'status': 200, 'url': arguments['url'], 'type': 'text/html', 'text': 'ok'}

    window = Window()
    portal = async_portal.AsyncPortal(None, window)
    assert asyncio.run(portal.get(async_portal.HOME)) == 'ok' and window.calls == 2
    window.calls = 0
    window.closed = True
    with pytest.raises(async_portal.SessionExpired):
        asyncio.run(portal.get(async_portal.HOME))
