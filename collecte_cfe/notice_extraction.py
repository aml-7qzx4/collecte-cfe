"""Lecture ciblée des mentions de paiement imprimées sur un avis CFE."""

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader


@dataclass(frozen=True)
class NoticeExtraction:
    tax_amount_cents: int | None = None
    amount_due_cents: int | None = None
    payment_method: str = 'NON_INDIQUE'
    bank_account_display: str | None = None
    bank_account_masked: bool = False
    review_status: str = 'A_VERIFIER'
    refund_cents: int | None = None


PAYMENT_LABELS = {
    'PRELEVEMENT_ECHEANCE': 'Prélèvement à l’échéance',
    'PRELEVEMENT_MENSUEL': 'Prélèvement mensuel',
    'AUCUN_PAIEMENT': 'Aucun paiement dû',
    'A_REGLER': 'À régler · sans prélèvement automatique',
    'NON_INDIQUE': 'Non indiqué sur l’avis',
}


def format_amount(cents: int | None) -> str:
    if cents is None:
        return 'Non lu'
    return f'{cents // 100:,}'.replace(',', ' ') + f',{cents % 100:02} €'


MANUAL_METHODS = ('A_REGLER', 'PRELEVEMENT_ECHEANCE', 'PRELEVEMENT_MENSUEL', 'AUCUN_PAIEMENT')
DEBIT_METHODS = ('PRELEVEMENT_ECHEANCE', 'PRELEVEMENT_MENSUEL')


def debited_account(notices) -> str:
    """Account the CFE is taken from, as printed (partly masked by the DGFiP) on the classified notices paid by direct debit."""
    accounts = []
    for notice in notices:
        shown = notice.get('bank_account_display')
        if (
            notice.get('status') == 'TERMINE'
            and notice.get('payment_method') in DEBIT_METHODS
            and shown
            and shown not in accounts
        ):
            accounts.append(shown)
    return ' · '.join(accounts)


SETTLED_LABELS = ('Lue', 'Classée à la main')


def review_label(row) -> str:
    if not row.get('extraction_status'):
        return 'Non analysé'
    if row['extraction_status'] == 'A_VERIFIER':
        return 'À vérifier'
    if row['extraction_status'] == 'MANUEL':
        return 'Classée à la main'
    if row.get('bank_account_masked'):
        return 'Partielle · compte masqué'
    if row['extraction_status'] == 'PARTIEL':
        return 'Partielle'
    return 'Lue'


def _normalise(text: str) -> str:
    text = unicodedata.normalize('NFKD', text)
    return ''.join(c for c in text if not unicodedata.combining(c)).upper().replace('’', "'")


_AMOUNT = re.compile(r'(?<![\dA-Z])((?:\d{1,3}(?:[ .\u00a0\u202f]\d{3})+|\d+),\d{2})(?!\d)')


def _amount_after_label(lines: list[str], label: str) -> int | None:
    values = set()
    for line in lines:
        normal = _normalise(line)
        if label not in normal:
            continue
        tail = normal.split(label, 1)[1]
        match = _AMOUNT.search(tail)
        if match:
            euros, cents = re.sub(r'[ .\u00a0\u202f]', '', match.group(1)).split(',')
            values.add(int(euros) * 100 + int(cents))
    return next(iter(values)) if len(values) == 1 else None


def _valid_iban(value: str) -> bool:
    if len(value) != 27 or not re.fullmatch(r'FR\d{2}[A-Z0-9]{23}', value):
        return False
    rotated = value[4:] + value[:4]
    digits = ''.join(c if c.isdigit() else str(ord(c) - 55) for c in rotated)
    return int(digits) % 97 == 1


def _displayed_account(lines: list[str]) -> tuple[str | None, bool]:
    for index, line in enumerate(lines):
        label = _normalise(line)
        if 'COMPTE BANCAIRE' in label:
            candidate = line
        elif 'COMPTE A DEBITER' in label:
            candidate = line + ' ' + (lines[index + 1] if index + 1 < len(lines) else '')
        else:
            continue
        start = re.search(r'\bFR\d{2}', candidate, re.I)
        if not start:
            continue
        remainder = candidate[start.start() :]
        chars = []
        end = 0
        for end, char in enumerate(remainder):  # noqa: B007 - where the reading stopped is used below
            if char.isascii() and char.isalnum():
                chars.append(char.upper())
            elif not char.isspace():
                break
            if len(chars) == 27:
                break
        compact = ''.join(chars)
        if len(compact) != 27:
            continue
        masked = bool(re.search(r'X{4,}', compact))
        if not masked and not _valid_iban(compact):
            continue
        shown = ' '.join(remainder[: end + 1].split())
        return shown, masked
    return None, False


def parse_notice_text(text: str) -> NoticeExtraction:
    lines = text.splitlines()
    normal = _normalise(text)
    tax = _amount_after_label(lines, 'MONTANT DE VOTRE IMPOT')
    due = _amount_after_label(lines, 'MONTANT A PAYER')
    refund = None
    if "VOUS N'AVEZ RIEN A PAYER" in normal:
        method = 'AUCUN_PAIEMENT'
        if due is None:
            due = 0
    elif "VOUS N'AVEZ PAS ADHERE A UN PRELEVEMENT AUTOMATIQUE" in normal:
        method = 'A_REGLER'
    elif 'PRELEVEMENT MENSUEL' in normal or 'CONTRAT DE MENSUALISATION' in normal:
        # Monthly notices print no « montant à payer » but the balance left after the instalments.
        refund = _amount_after_label(lines, 'SOMME REMBOURSEE')
        remaining = _amount_after_label(lines, 'SOMME RESTANT A PRELEVER')
        if refund is not None and remaining is None:
            method = 'AUCUN_PAIEMENT'
            due = 0
        else:
            refund = None
            method = 'PRELEVEMENT_MENSUEL'
            if due is None:
                due = remaining
    elif "VOUS AVEZ CHOISI LE PRELEVEMENT A L'ECHEANCE" in normal or "CONTRAT DE PRELEVEMENT A L'ECHEANCE" in normal:
        method = 'PRELEVEMENT_ECHEANCE'
    else:
        method = 'NON_INDIQUE'
    bank, masked = _displayed_account(lines)
    incomplete = (
        tax is None
        or method == 'NON_INDIQUE'
        or (due is None and method not in ('PRELEVEMENT_MENSUEL', 'AUCUN_PAIEMENT'))
        or (method.startswith('PRELEVEMENT') and bank is None)
    )
    status = 'A_VERIFIER' if incomplete else 'PARTIEL' if masked or method == 'A_REGLER' else 'EXTRAIT'
    return NoticeExtraction(tax, due, method, bank, masked, status, refund)


def parse_amount(text: str) -> int | None:
    """Amount typed by a person, in cents; an empty field means « not given »."""
    compact = re.sub(r'[\s  €]', '', text or '')
    if not compact:
        return None
    match = re.fullmatch(r'(\d+)(?:[,.](\d{1,2}))?', compact)
    if not match:
        raise ValueError(f'Montant invalide : « {text.strip()} ».')
    return int(match.group(1)) * 100 + int((match.group(2) or '0').ljust(2, '0'))


def manual_reading(row: dict, method: str, tax_cents: int | None, due_cents: int | None) -> NoticeExtraction:
    """Settle a notice by hand; the account read on the PDF is kept, the status says it was classified manually."""
    if method not in MANUAL_METHODS:
        raise ValueError('Mode de paiement non reconnu.')
    if tax_cents is None:
        raise ValueError('Indiquez le montant de l’avis.')
    if method == 'AUCUN_PAIEMENT':
        due_cents = 0
    elif due_cents is None and method != 'PRELEVEMENT_MENSUEL':
        raise ValueError('Indiquez le montant à payer imprimé sur l’avis.')
    return NoticeExtraction(
        tax_cents,
        due_cents,
        method,
        row.get('bank_account_display') or None,
        bool(row.get('bank_account_masked')),
        'MANUEL',
        row.get('refund_cents') if method == 'AUCUN_PAIEMENT' else None,
    )


def extract_notice_pdf(path: Path | str) -> NoticeExtraction:
    reader = PdfReader(path, strict=True)
    try:
        metadata = reader.metadata or {}
        # Notices filed before the cover page was dropped start with it.
        covered = (metadata.get('/CollecteNoticeKey') or metadata.get('/BubotNoticeKey')) and metadata.get(
            '/CollecteCoverPage'
        ) != 'NON'
        page_index = 1 if covered else 0
        if len(reader.pages) <= page_index:
            return NoticeExtraction()
        return parse_notice_text(reader.pages[page_index].extract_text() or '')
    finally:
        reader.close()
