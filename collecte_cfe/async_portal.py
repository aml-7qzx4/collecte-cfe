"""Portail asynchrone : chaque instance appartient à un contexte indépendant.

Les avis sont lus en rejouant, depuis la fenêtre de la session, les requêtes de consultation
du portail (liste des dossiers, sélection, tableau des avis, PDF) : aucun clic dans les pages.
"""

import asyncio
import base64
import html
import re
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from playwright.async_api import Error as PlaywrightError

from .domain import Dossier, safe_name
from .portal import (
    HOME,
    AuthorizationError,
    PortalError,
    SessionExpired,
    is_tax_url,
    parse_notice_cells,
    parse_notice_page,
)

TABLE_ID = 'form:tableauAvisImposition'
SERVICE = 'consulter.avis.cfe'
NOTICE_PATH = '/adelie2mapi/xhtml/impots/cfe/avis_cfe.xhtml'
# After login the portal stops on a page whose single button leads to the professional space.
LANDING_LINK = '#retourProPriv'
LOGOUT = re.compile(r'^\s*Me déconnecter\s*$')
SIREN_SHOWN = re.compile(r'SIREN\s+ou\s+IDSP\s*:\s*(\d{9})')
ROWS_PER_PAGE = 50
MAX_PDF_BYTES = 100 * 1024 * 1024
EXPIRED = 'Session expirée. Reconnectez-vous manuellement puis reprenez la campagne.'

# Runs in the session window so that requests carry its cookies, origin and network stack.
FETCH_JS = """async ({url, form, ajax, binary}) => {
  const headers = {};
  let body;
  if (form) { body = new URLSearchParams(form).toString(); headers['Content-Type'] = 'application/x-www-form-urlencoded; charset=UTF-8'; }
  if (ajax) { headers['Faces-Request'] = 'partial/ajax'; headers['X-Requested-With'] = 'XMLHttpRequest'; }
  let response;
  try { response = await fetch(url, {method: form ? 'POST' : 'GET', body, headers, credentials: 'include'}); }
  catch (error) {
    // A redirect to the login domain is refused by the browser: tell it apart from a network failure.
    try { const probe = await fetch(url, {credentials: 'include', redirect: 'manual'}); return {failed: true, redirected: probe.type === 'opaqueredirect'}; }
    catch (_) { return {failed: true, redirected: false}; }
  }
  const result = {status: response.status, url: response.url, type: response.headers.get('content-type') || ''};
  if (!binary || !result.type.includes('pdf')) { result.text = await response.text(); return result; }
  const bytes = new Uint8Array(await response.arrayBuffer());
  let chunks = '';
  for (let start = 0; start < bytes.length; start += 32768) chunks += String.fromCharCode(...bytes.subarray(start, start + 32768));
  result.data = btoa(chunks);
  return result;
}"""


def dossier_id(href):
    return parse_qs(urlparse(href or '').query).get('idDossier', [''])[0]


def page_text(markup):
    return ' '.join(html.unescape(re.sub(r'<[^>]+>', ' ', markup)).split())


def session_token(markup):
    """Anti-CSRF token the portal injects into its service links; it changes during a session."""
    found = re.search(r'injectTokens\(\s*"token"\s*,\s*"([\w-]+)"', markup) or re.search(
        r'[?&](?:amp;)?token=([\w-]{8,})', markup
    )
    return found.group(1) if found else None


def view_state(markup):
    for tag in re.findall(r'<input\b[^>]*>', markup):
        if 'javax.faces.ViewState' in tag:
            found = re.search(r'\bvalue="([^"]*)"', tag)
            if found:
                return html.unescape(found.group(1))
    return None


def notice_rows(fragment):
    """Cell texts and download link id of each row of the notice table."""
    rows = []
    for row in re.findall(r'<tr\b[^>]*>(.*?)</tr>', fragment, re.S):
        cells = re.findall(r'<td\b[^>]*>(.*?)</td>', row, re.S)
        texts = [page_text(cell) for cell in cells]
        link = None
        if len(cells) > 7:
            found = re.search(r'<a\b[^>]*\bid="([^"]+)"', cells[7])
            link = html.unescape(found.group(1)) if found else None
        rows.append((texts, link))
    return rows


def table_body(markup):
    found = re.search(r'<tbody\b[^>]*\bid="' + re.escape(TABLE_ID) + r'_data"[^>]*>(.*?)</tbody>', markup, re.S)
    return found.group(1) if found else None


def partial_updates(xml):
    return dict(re.findall(r'<update id="([^"]+)"><!\[CDATA\[(.*?)\]\]></update>', xml, re.S))


def listed_dossiers(markup):
    """(siren, name) of each tile of a « Mes dossiers » page."""
    tiles = []
    for href, label in re.findall(r'<a\b[^>]*\bhref="([^"]*dossierSelect\.do[^"]*)"[^>]*>(.*?)</a>', markup, re.S):
        tiles.append((dossier_id(html.unescape(href)), page_text(label)))
    return tiles


def discovered_dossiers(labels):
    by_siren = {}
    for label in labels:
        match = re.fullmatch(r'\s*(.+?)\s+SIREN\s*([0-9]{9})\s*', re.sub(r'\s+', ' ', label))
        if not match:
            raise ValueError('Un dossier ne présente pas de SIREN reconnu : découverte interrompue.')
        name, siren = match.groups()
        if siren in by_siren and by_siren[siren] != name:
            raise ValueError('Un SIREN présente plusieurs noms : contrôle manuel nécessaire.')
        by_siren[siren] = name
    grouped = {}
    for siren, name in by_siren.items():
        grouped.setdefault(safe_name(name).casefold(), []).append(siren)
    result = []
    for siren, name in by_siren.items():
        if len(grouped[safe_name(name).casefold()]) > 1:
            name = f'{safe_name(name)[:50]}_{siren}'
        result.append(Dossier(name, siren, 'À affecter'))
    return result


class AsyncPortal:
    def __init__(self, context, main_page):
        self.context = context
        self.main = main_page
        self.siren = None
        self.view_state = None
        self.links = {}
        self.shown_first = 0
        self.listed = []
        self.alive_checked = None
        self.alive = False

    # --- Session -------------------------------------------------------------------------

    @property
    def base(self):
        parsed = urlparse(HOME)
        return f'{parsed.scheme}://{parsed.netloc}'

    async def connected(self):
        if self.main.is_closed():
            return False
        await self.leave_landing_page()
        # « Me déconnecter » is a button opening a confirmation since the portal redesign; it was a link before.
        if await self.main.locator('a,button').filter(has_text=LOGOUT).count():
            return True
        # The fiscal account pages show no logout control: ask the portal home, at most every 30 s.
        if urlparse(self.main.url).netloc != urlparse(HOME).netloc or '/adelie2mapi/' not in self.main.url:
            return False
        now = asyncio.get_running_loop().time()
        if self.alive_checked is None or now - self.alive_checked >= 30:
            self.alive_checked = now
            try:
                self.alive = bool(session_token(await self.get(HOME)))
            except PortalError:
                self.alive = False
        return self.alive

    async def leave_landing_page(self):
        if urlparse(self.main.url).hostname != 'cfspro.impots.gouv.fr':
            return
        try:
            if await self.main.locator(LANDING_LINK).count():
                await self.main.goto(HOME, wait_until='domcontentloaded')
        except PlaywrightError:
            pass

    async def check_session(self, page=None):
        page = page or self.main
        if page.is_closed():
            raise SessionExpired('Navigateur fermé. Ouvrez une nouvelle connexion.')
        if 'cfspro-idp.' in page.url or await page.get_by_role('button', name='Se connecter', exact=True).count():
            raise SessionExpired(EXPIRED)
        text = await page.locator('body').inner_text()
        if re.search(r'session.*(?:expir|termin)|reconnecter|authentification des professionnels', text, re.I):
            raise SessionExpired(EXPIRED)

    async def ready(self):
        """The session window must sit on the portal for its requests to carry the session."""
        if self.main.is_closed():
            raise SessionExpired('Navigateur fermé. Ouvrez une nouvelle connexion.')
        if urlparse(self.main.url).netloc != urlparse(HOME).netloc:
            await self.main.goto(HOME, wait_until='domcontentloaded')
        await self.check_session()

    async def request(self, url, form=None, *, ajax=False, binary=False):
        arguments = {'url': url, 'form': form, 'ajax': ajax, 'binary': binary}
        try:
            response = await self.main.evaluate(FETCH_JS, arguments)
        except PlaywrightError:
            # Someone clicked in the session window: wait for its new page, then read again (consultation only).
            if self.main.is_closed():
                raise SessionExpired('Navigateur fermé. Ouvrez une nouvelle connexion.') from None
            try:
                await self.main.wait_for_load_state('domcontentloaded', timeout=15000)
                await self.ready()
                response = await self.main.evaluate(FETCH_JS, arguments)
            except SessionExpired:
                raise
            except PlaywrightError:
                raise PortalError(
                    'La fenêtre de la session a changé de page pendant la lecture. Relancez ce dossier.'
                ) from None
        if response.get('failed'):
            if response.get('redirected'):
                raise SessionExpired(EXPIRED)
            raise PortalError('Le portail ne répond pas. Vérifiez la connexion internet puis relancez ce dossier.')
        if 'cfspro-idp.' in response['url'] or response['status'] in (401, 403):
            raise SessionExpired(EXPIRED)
        text = response.get('text') or ''
        if re.search(r'Authentification des professionnels', text) and 'Mot de passe' in text:
            raise SessionExpired(EXPIRED)
        if 'erreurCsrf' in response['url']:
            raise PortalError('Le portail a refusé le jeton de session : relancez ce dossier.')
        if response['status'] >= 400:
            raise PortalError(f'Le portail a répondu par une erreur ({response["status"]}). Relancez ce dossier.')
        return response

    async def get(self, url):
        return (await self.request(url))['text']

    async def token(self):
        markup = await self.get(HOME)
        token = session_token(markup)
        if token:
            return token
        if 'Me déconnecter' not in page_text(markup):
            raise SessionExpired(EXPIRED)
        raise PortalError('Jeton de session introuvable sur la page « Mes services ». Relancez ce dossier.')

    def dossier_list_url(self, token, **query):
        return f'{self.base}/mire/afficherSelectionDossier.do?' + urlencode({'idth': SERVICE, **query, 'token': token})

    # --- Portfolio -----------------------------------------------------------------------

    async def discover_dossiers(self, progress, stop):
        await self.ready()
        if not await self.connected():
            raise SessionExpired('Connexion manuelle requise.')
        token = await self.token()
        # The « Avis CFE » list only holds the dossiers this account may consult for the CFE.
        labels = []
        seen = set()
        expected = None
        pages = None
        for page_number in range(1, 10001):
            if stop.is_set():
                raise PortalError('Découverte mise en pause. Aucun portefeuille partiel enregistré.')
            markup = await self.get(self.dossier_list_url(token, p=page_number))
            text = page_text(markup)
            count = re.search(r'(\d[\d   ]*)\s*résultat', text, re.I)
            if count:
                total = int(re.sub(r'\D', '', count.group(1)))
                if expected is not None and total != expected:
                    raise PortalError('Le portefeuille a changé pendant la découverte. Relancez la campagne.')
                expected = total
            shown = re.search(r'Page\s+\d+\s+sur\s+(\d+)', text)
            if shown:
                pages = int(shown.group(1))
            page_labels = []
            for siren, name in listed_dossiers(markup):
                if not re.fullmatch(r'\d{9}', siren) or not name:
                    raise PortalError('Libellé de dossier non reconnu : découverte interrompue.')
                page_labels.append(f'{name} SIREN {siren}')
            if not page_labels:
                raise PortalError('Aucun dossier lisible : vérifiez les droits du compte expert.')
            fingerprint = tuple(page_labels)
            if fingerprint in seen:
                raise PortalError('La pagination des dossiers se répète : découverte interrompue.')
            seen.add(fingerprint)
            labels.extend(page_labels)
            progress(f'Découverte · page {page_number} · {len(labels)} dossiers')
            if pages is None or page_number >= pages:
                result = discovered_dossiers(labels)
                if expected is None or len(result) != expected:
                    raise PortalError('Le nombre de dossiers découvert est incomplet : aucune collecte lancée.')
                return result
        raise PortalError('Trop de pages de dossiers : découverte interrompue.')

    # --- One dossier ---------------------------------------------------------------------

    async def open_dossier(self, siren, year):
        await self.close_dossier()
        self.siren = siren
        stage = 'vérification de la connexion'
        try:
            await self.ready()
            if not await self.connected():
                raise SessionExpired('Connexion manuelle requise dans le navigateur ouvert.')
            stage = 'recherche du SIREN'
            token = await self.token()
            markup = await self.get(self.dossier_list_url(token, siren=siren))
            # The search matches SIREN prefixes: keep only the tile of this exact SIREN.
            tiles = [tile for tile in listed_dossiers(markup) if tile[0] == siren]
            if not tiles:
                if re.search(r'aucune habilitation|aucun dossier|aucun résultat', page_text(markup), re.I):
                    raise AuthorizationError('Dossier introuvable ou droits de consultation CFE absents.')
                raise PortalError('La recherche SIREN n’a pas affiché le dossier attendu.')
            if len(tiles) != 1:
                raise PortalError('La recherche SIREN ne donne pas un dossier unique.')
            stage = 'sélection du dossier CFE'
            await self.request(
                f'{self.base}/mire/dossierSelect.do?' + urlencode({'idth': SERVICE, 'idDossier': siren, 'token': token})
            )
            stage = 'lecture du tableau CFE'
            markup = await self.get(self.base + NOTICE_PATH)
            self.confirm_siren(markup)
            if re.search(r'indisponible|maintenance|erreur technique', page_text(markup), re.I):
                raise PortalError('Consultation indisponible : réessayez après la maintenance du portail.')
            body = table_body(markup)
            self.view_state = view_state(markup)
            if body is None or not self.view_state:
                raise PortalError(
                    'Structure de la page des avis CFE non reconnue : collecte suspendue pour ce dossier.'
                )
            rows = notice_rows(body)
            self.shown_first = 0
            total = re.search(r'rowCount:(\d+)', markup)
            total = int(total.group(1)) if total else None
            if total is not None and total > len(rows):
                rows = []
                for first in range(0, total, ROWS_PER_PAGE):
                    page = await self.table_page(first)
                    rows.extend(page)
                    if len(page) < ROWS_PER_PAGE:
                        break
                if len(rows) != total:
                    raise PortalError(
                        'Le tableau CFE n’a pas été lu en entier : aucune preuve d’absence ne sera produite.'
                    )
            if not rows:
                parse_notice_page([])
            notices = {}
            paginated = total is not None and total > len(notice_rows(body))
            for index, (cells, link) in enumerate(rows):
                for notice in parse_notice_page([cells]):
                    first = index - index % ROWS_PER_PAGE if paginated else 0
                    if notice.key in self.links and self.links[notice.key][0] != link:
                        raise PortalError('Plusieurs avis ont la même identité : contrôle manuel nécessaire.')
                    self.links[notice.key] = (link, first)
                    self.listed.append(notice)
                    if notice.year == year:
                        notices[notice.key] = notice
            return list(notices.values())
        except (PortalError, ValueError):
            raise
        except Exception as error:
            if self.main.is_closed():
                raise SessionExpired('Navigateur fermé. Ouvrez une nouvelle connexion.') from None
            raise PortalError(
                f'Échec pendant {stage} ({type(error).__name__}). Vérifiez le portail puis relancez ce dossier.'
            ) from None

    def confirm_siren(self, markup):
        found = SIREN_SHOWN.search(page_text(markup))
        if not found or found.group(1) != self.siren:
            raise PortalError('Le SIREN affiché ne correspond pas au dossier : téléchargement bloqué.')

    async def table_page(self, first):
        """PrimeFaces pagination of the notice table; the server keeps the page shown for downloads."""
        form = {
            'javax.faces.partial.ajax': 'true',
            'javax.faces.source': TABLE_ID,
            'javax.faces.partial.execute': TABLE_ID,
            'javax.faces.partial.render': TABLE_ID,
            TABLE_ID: TABLE_ID,
            f'{TABLE_ID}_pagination': 'true',
            f'{TABLE_ID}_first': str(first),
            f'{TABLE_ID}_rows': str(ROWS_PER_PAGE),
            f'{TABLE_ID}_skipChildren': 'true',
            f'{TABLE_ID}_encodeFeature': 'true',
            'form': 'form',
            'javax.faces.ViewState': self.view_state,
        }
        updates = partial_updates((await self.request(self.base + NOTICE_PATH, form, ajax=True))['text'])
        if TABLE_ID not in updates:
            raise PortalError('Pagination du tableau CFE non reconnue.')
        self.view_state = next((value for key, value in updates.items() if 'ViewState' in key), self.view_state)
        self.shown_first = first
        return notice_rows(updates[TABLE_ID])

    async def download(self, n, path: Path):
        try:
            if n.key not in self.links:
                raise PortalError('Cet avis n’est plus visible dans le tableau : relancez uniquement cet avis.')
            link, first = self.links[n.key]
            if not link:
                raise PortalError('Action de téléchargement CFE non reconnue.')
            # A download link only works while its row is on the page the server shows.
            if first != self.shown_first:
                page = await self.table_page(first)
                if not any(parse_notice_cells(cells).key == n.key for cells, _ in page if len(cells) > 1):
                    raise PortalError('Cet avis n’est plus visible dans le tableau : relancez uniquement cet avis.')
            response = await self.request(
                self.base + NOTICE_PATH,
                {'form': 'form', 'javax.faces.ViewState': self.view_state, link: link},
                binary=True,
            )
            if 'data' not in response:
                raise PortalError('Le portail n’a pas renvoyé le PDF de cet avis : relancez-le.')
            body = base64.b64decode(response['data'])
            if len(body) > MAX_PDF_BYTES:
                raise PortalError('PDF trop volumineux pour le traitement automatique.')
            if not body.startswith(b'%PDF-'):
                raise PortalError('Le portail n’a pas renvoyé un PDF valide pour cet avis.')
            path.write_bytes(body)
        except PortalError:
            raise
        except Exception:
            raise PortalError('Téléchargement de cet avis interrompu : vous pouvez le relancer.') from None

    async def capture_cfe_screen(self, path):
        """Show the confirmed CFE page of this dossier in the session window, then capture it."""
        if not self.siren:
            return False
        try:
            if urlparse(self.main.url).path != NOTICE_PATH or not is_tax_url(self.main.url):
                await self.main.goto(self.base + NOTICE_PATH, wait_until='domcontentloaded')
            heading = await self.main.locator('#sideMenu').inner_text(timeout=5000)
            found = SIREN_SHOWN.search(heading)
            if not found or found.group(1) != self.siren:
                return False
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            await self.main.screenshot(path=str(target), full_page=True)
            return True
        except PlaywrightError:
            return False

    async def capture_search(self, siren, path):
        """Show this account's « Avis CFE » search for the SIREN in the session window, then capture it if it finds nothing."""
        try:
            await self.ready()
            await self.main.goto(self.dossier_list_url(await self.token(), siren=siren), wait_until='domcontentloaded')
            if parse_qs(urlparse(self.main.url).query).get('siren') != [siren]:
                return False
            if not re.search(r'aucun dossier ne correspond', await self.main.locator('body').inner_text(), re.I):
                return False
            field = self.main.locator('#rechercheSiren')
            # Show the searched SIREN in the field, as a person would see it after typing it.
            if await field.count() and not await field.input_value():
                await field.fill(siren)
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            await self.main.screenshot(path=str(target), full_page=True)
            return True
        except (PlaywrightError, PortalError):
            return False

    def previous_notice(self, n):
        """Last year's main notice of the same establishment, read only to explain this year's change."""

        def main_notice(item):
            try:
                return item.year == n.year - 1 and item.nic == n.nic and item.kind == 'SOLDE'
            except ValueError:
                return False

        candidates = [item for item in self.listed if main_notice(item)]
        return candidates[0] if len(candidates) == 1 else None

    async def close_dossier(self):
        self.siren = None
        self.view_state = None
        self.links = {}
        self.shown_first = 0
        self.listed = []
