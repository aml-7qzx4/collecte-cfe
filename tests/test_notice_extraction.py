from collecte_cfe.notice_extraction import parse_notice_text


def test_extracts_distinct_amounts_payment_and_masked_account():
    text = """MONTANT DE VOTRE IMPÔT 1 234,00 €
MONTANT A PAYER 934,00 €
Vous avez choisi le PRÉLÈVEMENT À L'ÉCHÉANCE. Le montant à payer sera prélevé
sur le compte bancaire FR00 0000 0000 0000 XXXXXXXX 000.
Référence Unique de Mandat (RUM) : FR76ZZZ123456P123456789012
"""
    result = parse_notice_text(text)
    assert result.tax_amount_cents == 123400
    assert result.amount_due_cents == 93400
    assert result.payment_method == 'PRELEVEMENT_ECHEANCE'
    assert result.bank_account_display == 'FR00 0000 0000 0000 XXXXXXXX 000'
    assert result.bank_account_masked is True
    assert result.review_status == 'PARTIEL'


def test_monthly_notice_does_not_invent_amount_to_pay():
    text = """Compte à débiter : Prochaines échéances 2025 :
FR00 0000 0000 0000 XXXXXXXX 000 Les échéances ci-dessous se substituent à la date limite.
MONTANT DE VOTRE IMPÔT 88,00 €
Numéro de contrat de prélèvement mensuel : 123456
"""
    result = parse_notice_text(text)
    assert result.tax_amount_cents == 8800
    assert result.amount_due_cents is None
    assert result.payment_method == 'PRELEVEMENT_MENSUEL'
    assert result.bank_account_masked is True


def test_no_payment_due_and_unknown_method_are_kept_distinct():
    result = parse_notice_text('MONTANT DE VOTRE IMPÔT 5,00 €\nMONTANT A PAYER 0,00 €\nVOUS N’AVEZ RIEN À PAYER.')
    assert result.tax_amount_cents == 500
    assert result.amount_due_cents == 0
    assert result.payment_method == 'AUCUN_PAIEMENT'
    unknown = parse_notice_text('MONTANT DE VOTRE IMPÔT 42,00 €')
    assert unknown.amount_due_cents is None
    assert unknown.payment_method == 'NON_INDIQUE'
    assert unknown.review_status == 'A_VERIFIER'


def test_rum_is_not_a_bank_account_and_unreadable_page_requires_review():
    result = parse_notice_text(
        'Référence Unique de Mandat (RUM) : FR76ZZZ123456P123456789012\nMONTANT DE VOTRE IMPÔT 42,00 €'
    )
    assert result.bank_account_display is None
    assert parse_notice_text('').review_status == 'A_VERIFIER'


def test_suggested_payment_method_is_not_reported_as_chosen():
    text = """MONTANT DE VOTRE IMPÔT 500,00 €
MONTANT A PAYER 500,00 €
À ce jour, vous n'avez pas adhéré à un prélèvement automatique. Vous pouvez payer
votre impôt en optant pour le prélèvement à l'échéance ou par paiement en ligne.
"""
    result = parse_notice_text(text)
    assert result.payment_method == 'A_REGLER'
    assert result.bank_account_display is None
    assert result.review_status == 'PARTIEL'


def test_full_account_needs_a_valid_iban_checksum():
    template = """MONTANT DE VOTRE IMPÔT 100,00 €
MONTANT A PAYER 100,00 €
Vous avez choisi le PRÉLÈVEMENT À L'ÉCHÉANCE.
sur le compte bancaire {}.
"""
    valid = parse_notice_text(template.format('FR14 2004 1010 0505 0001 3M02 606'))
    assert valid.bank_account_display == 'FR14 2004 1010 0505 0001 3M02 606'
    assert valid.bank_account_masked is False
    invalid = parse_notice_text(template.format('FR15 2004 1010 0505 0001 3M02 606'))
    assert invalid.bank_account_display is None
    assert invalid.review_status == 'A_VERIFIER'


def test_single_x_in_a_valid_iban_is_not_treated_as_redaction():
    text = """MONTANT DE VOTRE IMPÔT 100,00 €
MONTANT A PAYER 100,00 €
Vous avez choisi le PRÉLÈVEMENT À L'ÉCHÉANCE.
sur le compte bancaire FR56 2004 1010 0505 0001 3X02 606.
"""
    result = parse_notice_text(text)
    assert result.bank_account_display == 'FR56 2004 1010 0505 0001 3X02 606'
    assert result.bank_account_masked is False
    assert result.review_status == 'EXTRAIT'


# Layouts observed on real monthly notices; amounts and accounts are invented.
MONTHLY_REMAINING = """Compte à débiter : Prochaines échéances 2025 :
FR00 0000 0000 0000 XXXXXXXX 000 Les échéances ci-dessous se substituent à la date limite.
17 NOVEMBRE   2025          0,00  €
Contrat de mensualisation au nom de 15 DECEMBRE   2025          7,00  €
15 JANVIER   2026:         7,00 €   15 JUIN      2026:         7,00 €
MONTANT DE VOTRE IMPÔT          77,00 €
Total de vos versements          70,00 €
Somme restant à prélever           7,00 €
Numéro de contrat de prélèvement mensuel :
"""
MONTHLY_REFUND = """Compte à débiter : Vous serez remboursé prochainement
Contrat de mensualisation au nom de
15 JANVIER   2026:        30,00 €   15 JUIN      2026:        30,00 €
MONTANT DE VOTRE IMPÔT         307,00 €
Total de vos versements         328,00 €
Somme remboursée          21,00 €
Numéro de contrat de prélèvement mensuel :
"""


def test_monthly_notice_reads_the_printed_balance_still_to_be_debited():
    result = parse_notice_text(MONTHLY_REMAINING)
    assert result.tax_amount_cents == 7700
    assert result.amount_due_cents == 700
    assert result.payment_method == 'PRELEVEMENT_MENSUEL'
    assert result.refund_cents is None
    assert result.review_status == 'PARTIEL'


def test_monthly_notice_ending_in_a_refund_requires_no_payment():
    result = parse_notice_text(MONTHLY_REFUND)
    assert result.tax_amount_cents == 30700
    assert result.amount_due_cents == 0
    assert result.payment_method == 'AUCUN_PAIEMENT'
    assert result.refund_cents == 2100
    assert result.review_status == 'EXTRAIT'


def test_monthly_balance_is_not_read_outside_a_monthly_contract():
    result = parse_notice_text('MONTANT DE VOTRE IMPÔT 500,00 €\nMONTANT A PAYER 500,00 €\nSomme remboursée 21,00 €\n')
    assert result.refund_cents is None
    assert result.amount_due_cents == 50000


def test_a_manual_reading_keeps_the_account_and_is_labelled_as_such():
    import pytest

    from collecte_cfe.notice_extraction import manual_reading, review_label

    row = {'bank_account_display': 'FR14 2004 XXXXXXXX 606', 'bank_account_masked': 1, 'refund_cents': None}

    reading = manual_reading(row, 'A_REGLER', 50000, 40000)

    assert (reading.tax_amount_cents, reading.amount_due_cents, reading.payment_method) == (50000, 40000, 'A_REGLER')
    assert (reading.bank_account_display, reading.bank_account_masked, reading.review_status) == (
        'FR14 2004 XXXXXXXX 606',
        True,
        'MANUEL',
    )
    assert review_label({'extraction_status': 'MANUEL'}) == 'Classée à la main'
    assert manual_reading(row, 'AUCUN_PAIEMENT', 30700, None).amount_due_cents == 0
    assert manual_reading(row, 'PRELEVEMENT_MENSUEL', 7700, None).amount_due_cents is None
    with pytest.raises(ValueError, match='montant à payer'):
        manual_reading(row, 'PRELEVEMENT_ECHEANCE', 50000, None)
    with pytest.raises(ValueError, match='montant de l’avis'):
        manual_reading(row, 'A_REGLER', None, 100)
    with pytest.raises(ValueError, match='Mode de paiement'):
        manual_reading(row, 'NON_INDIQUE', 100, 100)


def test_typed_amounts_are_read_in_cents():
    import pytest

    from collecte_cfe.notice_extraction import parse_amount

    assert parse_amount('1 284,00 €') == 128400
    assert parse_amount('1 284,5') == 128450
    assert parse_amount('530') == 53000
    assert parse_amount('  ') is None
    with pytest.raises(ValueError, match='Montant'):
        parse_amount('douze euros')
    with pytest.raises(ValueError, match='Montant'):
        parse_amount('-5,00')
