"""Préparation locale de brouillons CFE pour l'application mail Windows."""

import re
from dataclasses import dataclass
from urllib.parse import quote

from .notice_extraction import format_amount
from .portfolio import valid_client_email

KIND_LABELS = {'ACOMPTE': 'Acompte', 'SOLDE': 'Solde', 'SUPPLEMENTAIRE': 'Avis supplémentaire'}

# Fields the cabinet can place in its own wording; the amounts always come from the notices themselves.
FIELDS = {
    'client': 'nom du client',
    'annees': 'années des avis',
    'avis': 'détail de chaque avis : montant, paiement',
    'pieces_jointes': 'phrase sur les avis joints',
    'collaborateur': 'collaborateur du dossier',
}
_FIELD = re.compile(r'\{(\w+)\}')


@dataclass(frozen=True)
class MailTemplate:
    subject: str = 'Avis CFE {annees} — {client}'
    body: str = 'Bonjour,\n\nVoici les informations figurant sur vos avis de CFE :\n\n{avis}\n\n{pieces_jointes}\n\nBien cordialement,'

    def check(self) -> 'MailTemplate':
        """Refuse a wording that would lose the notices' details or keep an unknown field as is."""
        if not self.subject.strip():
            raise ValueError('Indiquez un objet pour le mail.')
        unknown = sorted({name for name in _FIELD.findall(self.subject + self.body) if name not in FIELDS})
        if unknown:
            raise ValueError(
                'Champ inconnu : ' + ', '.join('{' + name + '}' for name in unknown) + '. Utilisez les champs proposés.'
            )
        if '{avis}' not in self.body:
            raise ValueError('Gardez {avis} dans le texte : c’est là que figurent les montants de chaque avis.')
        if '{avis}' in self.subject:
            raise ValueError('{avis} va dans le texte, pas dans l’objet.')
        return self


def fill(text: str, values: dict) -> str:
    return _FIELD.sub(lambda match: values.get(match.group(1), match.group(0)), text)


def compose_client_draft(
    dossier: dict, notices: list[dict], attachments: int = 0, template: MailTemplate | None = None
) -> tuple[str, str]:
    if not valid_client_email(dossier.get('email')):
        raise ValueError('Adresse e-mail du client absente ou invalide.')
    if not notices:
        raise ValueError('Aucun avis classé : le message ne peut pas être préparé.')
    lines = []
    for row in sorted(notices, key=lambda n: (n['year'], n['kind'], n.get('reference', ''))):
        method = row.get('payment_method')
        if (
            row.get('status') != 'TERMINE'
            or row.get('extraction_status') not in ('EXTRAIT', 'PARTIEL', 'MANUEL')
            or row.get('tax_amount_cents') is None
            or method not in ('AUCUN_PAIEMENT', 'PRELEVEMENT_MENSUEL', 'PRELEVEMENT_ECHEANCE', 'A_REGLER')
            or (method not in ('PRELEVEMENT_MENSUEL', 'AUCUN_PAIEMENT') and row.get('amount_due_cents') is None)
        ):
            raise ValueError('Un avis reste à vérifier avant la préparation du courriel.')
        kind = KIND_LABELS.get(row['kind'], 'Avis')
        detail = f'{kind} {row["year"]} : montant de l’avis {format_amount(row["tax_amount_cents"])}.'
        if method == 'AUCUN_PAIEMENT':
            detail += ' Aucun montant à régler selon cet avis.'
            if row.get('refund_cents'):
                detail += f' L’avis annonce un remboursement de {format_amount(row["refund_cents"])}.'
        elif method == 'PRELEVEMENT_MENSUEL':
            detail += ' Le document indique un prélèvement mensuel ; consultez son échéancier.'
            if row.get('amount_due_cents'):
                detail += f' Il reste {format_amount(row["amount_due_cents"])} à prélever.'
        elif method == 'PRELEVEMENT_ECHEANCE':
            detail += f' Montant à payer {format_amount(row["amount_due_cents"])}. Le document indique un prélèvement automatique à l’échéance.'
        else:
            detail += f' Montant à payer {format_amount(row["amount_due_cents"])}. Aucun prélèvement automatique n’est indiqué ; un règlement est à effectuer si vous ne l’avez pas déjà réalisé.'
        lines.append(detail)
    years = sorted({row['year'] for row in notices})
    closing = (
        (
            'Vous trouverez votre avis en pièce jointe.'
            if attachments == 1
            else 'Vous trouverez vos avis en pièce jointe.'
        )
        if attachments
        else 'Vous pouvez consulter le document pour vérifier le détail de chaque échéance.'
    )
    values = {
        'client': dossier['name'],
        'annees': ', '.join(map(str, years)),
        'avis': '\n\n'.join(lines),
        'pieces_jointes': closing,
        'collaborateur': dossier.get('collaborator') or '',
    }
    template = template or MailTemplate()
    return fill(template.subject, values).strip(), fill(template.body, values)


def mailto_uri(recipient: str, subject: str, body: str) -> str:
    if not valid_client_email(recipient):
        raise ValueError('Adresse e-mail du client invalide.')
    if '\r' in recipient or '\n' in recipient:
        raise ValueError('Adresse e-mail du client invalide.')
    uri = f'mailto:{quote(recipient, safe="@")}?subject={quote(subject, safe="")}&body={quote(body.replace(chr(10), chr(13) + chr(10)), safe="")}'
    if len(uri) > 1900:
        raise ValueError('Message trop long pour ouvrir un brouillon dans Outlook. Préparez moins d’avis à la fois.')
    return uri
