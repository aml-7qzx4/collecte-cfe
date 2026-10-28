from io import BytesIO

from reportlab.pdfgen import canvas

from collecte_cfe.cfe_details import CfeDetails, Levy, explain, explanation_lines, parse_details, read_details
from collecte_cfe.domain import Dossier, Notice
from collecte_cfe.store import Store

# Column positions of the calculation table: label, then commune, TSE and GEMAPI.
X_LABEL, X_RIGHT, X_COLUMNS = 13, 284, (200, 330, 450)


def detail_rows(
    year,
    *,
    rental=11451,
    base=11451,
    minimum=False,
    band='<= 10 000',
    minimum_amount=585,
    rate='26,92',
    previous_rate='26,43',
    extra=('1,14000', '0,23200'),
    fees=106,
    chambers=69,
):
    rates = [rate, *extra]
    contributions = [round(base * float(r.replace(',', '.')) / 100) for r in rates]
    total = sum(contributions)
    cfe = total + fees
    amount = lambda v: f'{v:,}'.replace(',', ' ')
    rows = [
        [
            (X_LABEL, '1 - Valeur locative des biens passibles de taxe foncière'),
            (240, amount(rental)),
            (X_RIGHT, '7 - Total des réductions (7)'),
        ],
        [
            (X_LABEL, '2 - Dont la valeur locative des biens concernés par la révision (2)'),
            (240, amount(rental)),
            (X_RIGHT, '8 - Base après réductions (8)'),
            (520, amount(base)),
        ],
        [
            (X_LABEL, '3 - Présence d’au moins un bien industriel parmi ces biens (3)'),
            (240, 'NON'),
            (X_RIGHT, '9 - Imposition sur la base minimum (9)'),
            (520, 'OUI' if minimum else 'NON'),
        ],
        [
            (X_LABEL, '4 - Le cas échéant, nombre de semaines d’activité saisonnière (4)'),
            (X_RIGHT, '10 - Chiffre d’affaires retenu pour la base minimum (10)'),
            (500, band),
        ],
        [
            (X_LABEL, '5 - Réduction pour création d’établissement (5)'),
            (X_RIGHT, '11 - Temps partiel ou moins de 9 mois/an (11)'),
            (520, 'NON'),
        ],
        [
            (X_LABEL, '6 - Réduction artisans et autres (6)'),
            (X_RIGHT, '12 - Base minimum applicable (9)'),
            (520, amount(minimum_amount)),
        ],
        [
            (30, 'Éléments de calcul (12)'),
            (200, 'Commune'),
            (260, 'Syndicats de communes'),
            (330, 'TSE (14)'),
            (450, 'TGEMAPI (15)'),
        ],
        [(X_LABEL, '13 - Bases exonérées')],
        [(X_LABEL, '15- Bases nettes'), *[(x, amount(base)) for x in X_COLUMNS]],
        [
            (X_LABEL, f'16- Rappel taux d’imposition {year - 1}'),
            *([(198, f'{previous_rate} %')] if previous_rate else []),
        ],
        [(X_LABEL, f'17- Taux d’imposition {year}'), *[(x, f'{r} %') for x, r in zip(X_COLUMNS, rates)]],
        [
            (X_LABEL, f'18- Cotisation {year} avant lissage'),
            *[(x, amount(c)) for x, c in zip(X_COLUMNS, contributions)],
        ],
        [(X_LABEL, f'19- Cotisation {year} lissée (17)'), *[(x, amount(c)) for x, c in zip(X_COLUMNS, contributions)]],
        [
            (X_LABEL, f'23- Total des cotisations {year} lissées de la ligne 19'),
            (240, amount(total)),
            (X_RIGHT, '24 - Frais de gestion (18)'),
            (520, amount(fees)),
        ],
        [(X_LABEL, '25 - Total de cotisation foncière des entreprises'), (500, amount(cfe))],
        [
            (X_LABEL, '35 - Cotisation totale de taxe pour frais de chambres de commerce et d’industrie'),
            (420, amount(chambers)),
        ],
        [(X_RIGHT, '196 - MONTANT DE VOTRE IMPÔT'), (500, amount(cfe + chambers))],
    ]
    return rows, cfe + chambers


def notice_pdf(rows, *, identification=True):
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer)
    if identification:
        pdf.drawString(48, 740, 'Identification de l’avis')
        pdf.showPage()
    pdf.setFont('Helvetica', 6)
    for index, row in enumerate(rows):
        for x, text in row:
            pdf.drawString(x, 760 - index * 11, text)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def write_notice(tmp_path, name, **options):
    rows, total = detail_rows(**options)
    path = tmp_path / name
    path.write_bytes(notice_pdf(rows))
    return path, total


def test_details_are_read_line_by_line_and_column_by_column(tmp_path):
    path, total = write_notice(tmp_path, 'avis.pdf', year=2025)
    details = read_details(path)
    assert details.rental_value == 11451 and details.base_after_reductions == 11451 and details.minimum_base is False
    assert details.turnover_band == '<= 10 000' and details.part_year is False and details.minimum_base_amount == 585
    assert [levy.rate for levy in details.levies] == ['26,92', '1,14000', '0,23200']
    assert [levy.previous_rate for levy in details.levies] == ['26,43', None, None]
    assert [levy.base for levy in details.levies] == [11451] * 3
    assert [levy.additional for levy in details.levies] == [False, True, True]
    assert sum(levy.contribution for levy in details.levies) == details.contributions
    assert details.cfe_total == details.contributions + details.management_fees and details.chambers == 69
    assert details.tax_total == total and details.base == 11451
    assert CfeDetails.from_dict(details.to_dict()) == details


def test_a_page_without_calculation_details_gives_nothing(tmp_path):
    path = tmp_path / 'autre.pdf'
    path.write_bytes(notice_pdf([[(13, 'Avis sans détail')]]))
    assert read_details(path) is None


def test_the_explanation_splits_the_change_between_base_rates_fees_and_chambers(tmp_path):
    previous, old_total = write_notice(
        tmp_path,
        'n-1.pdf',
        year=2024,
        rental=11000,
        base=11000,
        rate='26,43',
        previous_rate=None,
        extra=('1,14000', '0,20000'),
        fees=100,
        chambers=60,
    )
    current, new_total = write_notice(tmp_path, 'n.pdf', year=2025)
    explanation = explain(read_details(current), read_details(previous))
    assert explanation.delta == new_total - old_total > 0
    assert explanation.summary.startswith(f'Hausse de {new_total - old_total} €')
    texts = [reason.text for reason in explanation.reasons]
    assert any(text.startswith('Valeur locative : 11 000 € → 11 451 € (+4,1 %)') for text in texts)
    assert any(text.startswith('Effet de la base') for text in texts)
    assert any('Taux voté par la commune ou l’intercommunalité : 26,43 % → 26,92 %' in text for text in texts)
    assert any('Taxes additionnelles (TSE, GEMAPI) : 0,20000 % → 0,23200 %' in text for text in texts)
    assert 'Frais de gestion' in texts and 'Taxes pour frais de chambres consulaires' in texts
    assert sum(reason.amount or 0 for reason in explanation.reasons) == explanation.delta, (
        'every euro of the change is accounted for'
    )


def test_minimum_base_changes_are_named():
    levy = lambda base: (Levy('20,00', base=base, contribution=base // 5),)
    before = CfeDetails(
        minimum_base=True, turnover_band='<= 10 000', minimum_base_amount=416, levies=levy(416), tax_total=85
    )
    after = CfeDetails(
        minimum_base=True, turnover_band='<= 100 000', minimum_base_amount=589, levies=levy(589), tax_total=120
    )
    lines = explanation_lines(explain(after, before))
    assert lines[0] == 'Hausse de 35 € (+41,2 %) : 85 € → 120 €.'
    assert '• Tranche de chiffre d’affaires retenue pour la base minimum : <= 10 000 → <= 100 000.' in lines
    assert '• Base minimum : 416 € → 589 €.' in lines
    exempt = CfeDetails(
        minimum_base=True, exempt_base=589, levies=(Levy('20,00', base=0, contribution=0),), tax_total=0
    )
    assert any('Fin d’exonération' in line for line in explanation_lines(explain(after, exempt)))


def test_without_last_year_only_the_rate_change_is_explained():
    current = CfeDetails(levies=(Levy('26,92', '26,43', 11451, 3083),), tax_total=3346)
    lines = explanation_lines(explain(current, None))
    assert lines == [
        'Avis de l’année précédente non lu : seules les hausses de taux sont expliquées.',
        '• Taux voté par la commune ou l’intercommunalité : 26,43 % → 26,92 % : +56 €',
    ]


def test_breakdowns_are_kept_per_notice_and_found_for_last_year(tmp_path):
    from collecte_cfe.documents import change_explanation

    store = Store(tmp_path / 'db.sqlite')
    campaign = store.create_batch([2025], tmp_path / 'out', [Dossier('SAS Exemple', '000000001', 'Lea')], 1, 'CFE')
    campaign = store.batch_campaigns(campaign)[0]['id']
    notice = Notice(2025, 'REF-2025', '00018', '75', 'PARIS', 'Rôle général n°092', '15/12/2025')
    row = store.register_notice(campaign, '000000001', notice)
    path, _total = write_notice(tmp_path, 'n.pdf', year=2025)
    target = tmp_path / 'out' / 'classé.pdf'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(path.read_bytes())
    with store.connection() as c:
        c.execute('UPDATE notices SET path=? WHERE id=?', (str(target), row['id']))
    store.notice_result(row['id'], 'TERMINE', '', 'x')
    row = store.notices(campaign, '000000001')[0]
    lines = change_explanation(store, row)
    assert lines[0].startswith('Avis de l’année précédente non lu')
    assert store.breakdown('000000001', 2025, '00018', 'REF-2025') is not None, 'read once, then kept'
    previous_path, _ = write_notice(tmp_path, 'n-1.pdf', year=2024, rate='26,43', previous_rate=None, fees=100)
    store.save_breakdown(
        '000000001',
        Notice(2024, 'REF-2024', '00018', '75', 'PARIS', 'Rôle général n°092', ''),
        read_details(previous_path),
        'ANNEE_PRECEDENTE',
    )
    assert change_explanation(store, row)[0].startswith('Hausse de ')
    assert store.breakdown('000000001', 2024, '00018') is not None
    assert change_explanation(store, {**row, 'kind': 'ACOMPTE'}) == []


def test_the_collector_reads_last_years_notice_without_filing_it(tmp_path, monkeypatch):
    from threading import Event

    from collecte_cfe import collector as collector_module
    from collecte_cfe.collector import Collector

    store = Store(tmp_path / 'db.sqlite')
    batch = store.create_batch([2025], tmp_path / 'out', [Dossier('SAS Exemple', '000000001', 'Lea')], 1, 'CFE')
    campaign = store.batch_campaigns(batch)[0]['id']
    current = Notice(2025, 'REF-2025', '00018', '75', 'PARIS', 'Rôle général n°092', '15/12/2025')
    previous = Notice(2024, 'REF-2024', '00018', '75', 'PARIS', 'Rôle général n°092', '16/12/2024')
    pdfs = {
        current.key: write_notice(tmp_path, 'n.pdf', year=2025)[0],
        previous.key: write_notice(tmp_path, 'n-1.pdf', year=2024, rate='26,43', previous_rate=None)[0],
    }
    downloads = []

    class Portal:
        def open_dossier(self, siren, year):
            return [current]

        def download(self, n, path):
            downloads.append(n.year)
            path.write_bytes(pdfs[n.key].read_bytes())

        def previous_notice(self, n):
            return previous if n == current else None

        def close_dossier(self):
            pass

    monkeypatch.setattr(collector_module, 'verify_original_notice', lambda *args: None)
    monkeypatch.setattr(
        collector_module,
        'classify_pdf',
        lambda source, target, d, n: (
            target.parent.mkdir(parents=True, exist_ok=True),
            target.write_bytes(source.read_bytes()),
            'sha',
        )[2],
    )
    monkeypatch.setattr(collector_module, 'matches_sha256', lambda *args: True)
    Collector(store, Portal()).run(campaign, Event(), lambda message: None)
    assert downloads == [2025, 2024]
    assert (
        store.breakdown('000000001', 2024, '00018') is not None
        and store.breakdown('000000001', 2025, '00018') is not None
    )
    assert [p.name for p in (tmp_path / 'out').rglob('*.pdf')] == ['SAS Exemple_CFE_2025_SOLDE.pdf'], (
        'last year’s notice is not filed'
    )


def test_an_unnumbered_total_is_read_and_a_missing_one_is_said():
    text = '15- Bases nettes     416\n17- Taux d’imposition 2024     16,52 %\n                 MONTANT DE VOTRE IMPÔT          1 077\n'
    assert parse_details(text).tax_total == 1077
    current = CfeDetails(levies=(Levy('16,52', base=416, contribution=69),), tax_total=77)
    unread = CfeDetails(levies=(Levy('16,52', base=416, contribution=69),))
    assert explain(current, unread).summary == 'Montant total illisible sur l’un des deux avis : écart non chiffré.'
    assert explain(current, None).summary.startswith('Avis de l’année précédente non lu')


def test_the_2024_form_prints_the_tax_on_line_194_and_zero_is_a_total():
    text = (
        '15- Bases nettes        0        0        0\n'
        '17- Taux d’imposition 2024     16,52 %     1,20600 %     0,13000 %\n'
        '                      194 - MONTANT DE VOTRE IMPÔT          (lignes             0\n'
        '                      25+35+47+61+69+84+95+119+154+160+175+182+188 si servies)\n'
    )
    assert parse_details(text).tax_total == 0
    current = CfeDetails(
        minimum_base=True, minimum_base_amount=416, levies=(Levy('16,52', base=0, contribution=0),), tax_total=0
    )
    previous = CfeDetails(
        minimum_base=True, minimum_base_amount=409, levies=(Levy('16,52', base=0, contribution=0),), tax_total=0
    )
    lines = explanation_lines(explain(current, previous))
    assert lines[:2] == ['Montant stable : 0 €.', '• Base minimum : 409 € → 416 €.']
