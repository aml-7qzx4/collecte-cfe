"""Adaptateur DGFiP : aucune donnée de session ne sort du contexte Playwright."""

import re
from urllib.parse import urlparse

from .domain import Notice

HOME = 'https://cfspro.impots.gouv.fr/mire/accueil.do'


class PortalError(Exception):
    pass


class SessionExpired(PortalError):
    pass


class AuthorizationError(PortalError):
    pass


def parse_notice_cells(cells: list[str]) -> Notice:
    if len(cells) < 10 or not re.fullmatch(r'\d{4}', cells[1].strip()) or not cells[6].strip():
        raise PortalError('Structure du tableau CFE non reconnue : collecte suspendue pour ce dossier.')
    return Notice(
        int(cells[1]),
        cells[6].strip(),
        cells[4].strip(),
        cells[2].strip(),
        cells[3].strip(),
        cells[7].strip(),
        cells[9].strip(),
    )


def parse_notice_page(rows: list[list[str]]) -> list[Notice]:
    if not rows:
        raise PortalError('Le tableau CFE n’a pas été alimenté : aucune absence ne peut être confirmée.')
    if len(rows) == 1 and len(rows[0]) == 1:
        if re.search(r'aucun|pas d[’\x27]avis|no records', rows[0][0], re.I):
            return []
        raise PortalError('Résultat CFE vide non reconnu : contrôle manuel nécessaire.')
    return [parse_notice_cells(cells) for cells in rows]


def is_tax_url(url: str) -> bool:
    p = urlparse(url)
    return (
        p.scheme == 'https'
        and bool(p.hostname)
        and (p.hostname == 'impots.gouv.fr' or p.hostname.endswith('.impots.gouv.fr'))
        and not p.username
    )
