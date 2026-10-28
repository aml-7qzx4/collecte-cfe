import pytest

from collecte_cfe.client_mail import compose_client_draft, mailto_uri


def notice(kind, method, tax, due, year=2025, status='PARTIEL'):
    return {
        'kind': kind,
        'year': year,
        'status': 'TERMINE',
        'extraction_status': status,
        'tax_amount_cents': tax,
        'amount_due_cents': due,
        'payment_method': method,
    }


def test_one_draft_for_selected_client_lists_each_notice_without_global_total():
    dossier = {'name': 'SAS Exemple', 'email': 'client@example.fr'}
    subject, body = compose_client_draft(
        dossier,
        [
            notice('ACOMPTE', 'PRELEVEMENT_ECHEANCE', 50000, 50000),
            notice('SOLDE', 'A_REGLER', 90000, 40000),
        ],
    )
    assert 'SAS Exemple' in subject
    assert 'Acompte' in body and 'Solde' in body
    assert '500,00 €' in body and '400,00 €' in body
    assert 'prélèvement automatique' in body
    assert 'règlement' in body
    assert 'total' not in body.casefold()
    uri = mailto_uri(dossier['email'], subject, body)
    assert uri.startswith('mailto:client@example.fr?')
    assert 'subject=' in uri and 'body=' in uri


def test_zero_payment_and_monthly_notice_use_distinct_wording():
    dossier = {'name': 'Exemple', 'email': 'client@example.fr'}
    _, zero = compose_client_draft(dossier, [notice('SOLDE', 'AUCUN_PAIEMENT', 500, 0)])
    _, monthly = compose_client_draft(dossier, [notice('SOLDE', 'PRELEVEMENT_MENSUEL', 8800, None)])
    assert 'aucun montant à régler' in zero.casefold()
    assert 'prélèvement mensuel' in monthly
    assert 'montant à payer' not in monthly.casefold()


def test_uncertain_reading_or_missing_email_blocks_draft():
    with pytest.raises(ValueError, match='e-mail'):
        compose_client_draft({'name': 'X', 'email': ''}, [notice('SOLDE', 'AUCUN_PAIEMENT', 0, 0)])
    with pytest.raises(ValueError, match='vérifier'):
        compose_client_draft(
            {'name': 'X', 'email': 'x@example.fr'},
            [notice('SOLDE', 'PRELEVEMENT_ECHEANCE', 10000, 10000, status='A_VERIFIER')],
        )


def test_monthly_balance_and_refund_are_mentioned_without_a_global_total():
    dossier = {'name': 'Exemple', 'email': 'client@example.fr'}
    _, remaining = compose_client_draft(dossier, [notice('SOLDE', 'PRELEVEMENT_MENSUEL', 7700, 700)])
    refund_notice = {**notice('SOLDE', 'AUCUN_PAIEMENT', 30700, 0, status='EXTRAIT'), 'refund_cents': 2100}
    _, refund = compose_client_draft(dossier, [refund_notice])
    assert 'prélèvement mensuel' in remaining
    assert 'reste 7,00 € à prélever' in remaining
    assert 'aucun montant à régler' in refund.casefold()
    assert 'remboursement de 21,00 €' in refund


def test_a_notice_classified_by_hand_can_be_sent_to_the_client():
    dossier = {'name': 'Exemple', 'email': 'client@example.fr'}
    _, body = compose_client_draft(dossier, [notice('SOLDE', 'A_REGLER', 50000, 40000, status='MANUEL')])
    assert '400,00 €' in body


def test_the_cabinet_wording_keeps_the_amounts_from_the_notices():
    from collecte_cfe.client_mail import MailTemplate

    dossier = {'name': 'SAS Exemple', 'email': 'client@example.fr', 'collaborator': 'Léa'}
    template = MailTemplate(
        'CFE {annees} : {client}',
        'Madame, Monsieur,\n\n{avis}\n\n{pieces_jointes}\n\nVotre contact : {collaborateur}.\n\nCordialement,',
    )
    subject, body = compose_client_draft(dossier, [notice('SOLDE', 'A_REGLER', 90000, 40000)], 1, template.check())
    assert subject == 'CFE 2025 : SAS Exemple'
    assert (
        body.startswith('Madame, Monsieur,')
        and '400,00 €' in body
        and 'Vous trouverez votre avis en pièce jointe.' in body
    )
    assert 'Votre contact : Léa.' in body
    assert (
        compose_client_draft(dossier, [notice('SOLDE', 'A_REGLER', 90000, 40000)])[0] == 'Avis CFE 2025 — SAS Exemple'
    ), 'default unchanged'


def test_a_wording_without_the_notices_or_with_an_unknown_field_is_refused():
    from collecte_cfe.client_mail import MailTemplate

    with pytest.raises(ValueError, match='{avis}'):
        MailTemplate('Objet', 'Bonjour, voici vos avis.').check()
    with pytest.raises(ValueError, match='{montant}'):
        MailTemplate('Objet', '{avis} {montant}').check()
    with pytest.raises(ValueError, match='objet'):
        MailTemplate('  ', '{avis}').check()
