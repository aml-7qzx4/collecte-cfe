"""What each dossier and year means for the client: the same words in the application and in the Excel workbooks."""

from .notice_extraction import review_label
from .portfolio import valid_client_email

# The situations sort dossiers by what the collaborator has to tell the client.
SITUATIONS = (
    ('ALL', 'Tous'),
    ('PAY', 'À régler'),
    ('DEBIT', 'Prélevé'),
    ('NONE', 'Rien à payer'),
    ('REVIEW', 'À vérifier'),
    ('ABSENT', 'Aucun avis'),
    ('RETRY', 'À relancer'),
    ('WAIT', 'En attente'),
    ('MANUAL', 'À vérifier manuellement'),
    ('CHECKED', 'Vérifié manuellement'),
)
# After this many failed attempts, a dossier is no longer retried on its own: the collaborator checks it on the portal.
MANUAL_AFTER = 2
SITUATION_LABELS = dict(SITUATIONS)
# What is left to handle comes first, then what the client pays, then the rest.
URGENCY = ('MANUAL', 'RETRY', 'REVIEW', 'WAIT', 'PAY', 'DEBIT', 'NONE', 'ABSENT', 'CHECKED')
MODE_SHORT = {
    'PRELEVEMENT_ECHEANCE': 'Prélèvement',
    'PRELEVEMENT_MENSUEL': 'Mensualisé',
    'AUCUN_PAIEMENT': 'Rien à payer',
    'A_REGLER': 'À régler',
    'NON_INDIQUE': 'Non indiqué',
}
TO_CHECK = 'À vérifier'


def date_key(text):
    parts = (text or '').replace('-', '/').split('/')
    return tuple(reversed(parts)) if len(parts[0]) == 2 else tuple(parts)


def reading_short(notice):
    label = review_label(notice)
    if label == 'Non analysé':
        return 'Non lue'
    return 'Partielle' if label.startswith('Partielle') else label


def dossier_reading(notices):
    """Sum what the classified notices of one dossier and year ask to pay, and keep the reading that needs the most attention."""
    if not notices:
        return None, '', ''
    dues = [notice.get('amount_due_cents') for notice in notices]
    due = None if any(value is None for value in dues) else sum(dues)
    methods = {notice.get('payment_method') or 'NON_INDIQUE' for notice in notices if notice.get('extraction_status')}
    mode = '' if not methods else MODE_SHORT.get(next(iter(methods)), '') if len(methods) == 1 else 'Plusieurs modes'
    readings = {reading_short(notice) for notice in notices}
    for wanted in (TO_CHECK, 'Non lue', 'Partielle', 'Classée à la main'):
        if wanted in readings:
            return due, mode, wanted
    return due, mode, 'Lue'


def dossier_situation(dossier, notices):
    """What the collaborator has to tell the client about one dossier and year."""
    status = dossier['status']
    if manual_check(dossier):
        return 'CHECKED' if dossier.get('verified_at') else 'MANUAL'
    if status in ('ERREUR', 'SANS_HABILITATION'):
        return 'RETRY'
    if status == 'ABSENT':
        return 'ABSENT'
    if status != 'TERMINE':
        return 'WAIT'
    # A dossier marked done without any classified notice needs a look too.
    if not notices or {reading_short(notice) for notice in notices} & {TO_CHECK, 'Non lue'}:
        return 'REVIEW'
    methods = {notice.get('payment_method') for notice in notices}
    if 'A_REGLER' in methods:
        return 'PAY'
    if methods & {'PRELEVEMENT_ECHEANCE', 'PRELEVEMENT_MENSUEL'}:
        return 'DEBIT'
    if methods == {'AUCUN_PAIEMENT'}:
        return 'NONE'
    return 'REVIEW'


def manual_check(dossier):
    """A dossier that failed too many times to be retried on its own."""
    return dossier['status'] == 'ERREUR' and (dossier.get('failures') or 0) >= MANUAL_AFTER


def situation_label(dossier, situation):
    return 'Sans habilitation' if dossier['status'] == 'SANS_HABILITATION' else SITUATION_LABELS[situation]


def short_day(stamp):
    """« 24/10 » from a stored ISO timestamp."""
    return f'{stamp[8:10]}/{stamp[5:7]}'


def mail_text(dossier, situation):
    """Where the client's mail stands, in the words of the Courriels tab."""
    if dossier.get('mail_sent_at'):
        return 'Envoyé ' + short_day(dossier['mail_sent_at'])
    if dossier.get('mail_opened_at'):
        return 'À confirmer'
    if dossier['status'] != 'TERMINE' or situation == 'REVIEW':
        return ''
    return 'Prêt' if valid_client_email(dossier.get('email') or '') else 'Sans adresse'


def earliest_due_date(notices):
    """The first payment deadline the client has to meet, read from the notices still to pay when there are some."""
    to_pay = [notice for notice in notices if notice.get('payment_method') == 'A_REGLER'] or notices
    dates = [notice.get('due_date') for notice in to_pay if notice.get('due_date')]
    return min(dates, key=date_key) if dates else ''
