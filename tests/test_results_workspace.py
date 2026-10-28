from pathlib import Path

from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtWidgets import QLabel, QPlainTextEdit

from collecte_cfe.app import MainWindow
from collecte_cfe.domain import Dossier, Notice
from collecte_cfe.store import Store
from collecte_cfe.ui_collect import COLLECT_PAGE


class MemorySettings:
    def __init__(self, values=None):
        self.values = values or {}

    def contains(self, key):
        return key in self.values

    def value(self, key, default=None, type=None):
        value = self.values.get(key, default)
        return type(value) if type else value

    def setValue(self, key, value):
        self.values[key] = value


def make_window(tmp_path, dossiers, years=(2025,)):
    store = Store(tmp_path / 'db.sqlite')
    batch = store.create_batch(list(years), tmp_path / 'out', dossiers, 1)
    return store, batch, MainWindow(store, settings=MemorySettings())


def test_situation_tiles_count_dossiers_per_year_and_combine_with_the_search(tmp_path, qapp):
    store, batch, window = make_window(
        tmp_path,
        [
            Dossier('Alpha', '000000001', 'Léa'),
            Dossier('Beta', '000000002', 'Paul'),
            Dossier('Gamma', '000000003', 'Léa'),
        ],
        (2024, 2025),
    )
    campaigns = store.batch_campaigns(batch)
    store.dossier_result(campaigns[0]['id'], '000000001', 'ERREUR', 'Erreur 2024')
    store.dossier_result(campaigns[1]['id'], '000000003', 'ERREUR', 'Erreur 2025')
    store.dossier_result(campaigns[1]['id'], '000000001', 'SANS_HABILITATION')
    store.dossier_result(campaigns[1]['id'], '000000002', 'ABSENT')
    window.open_campaign(batch)
    window.year_filter.setCurrentText('2025')

    counts = {key: tile.count for key, tile in window.result_filter_buttons.items()}
    assert (counts['ALL'], counts['TODO'], counts['ABSENT'], counts['PAY']) == (3, 2, 1, 0)
    assert window.result_filter_buttons['WAIT'].isHidden(), 'no dossier is waiting in 2025'
    window.result_filter_buttons['TODO'].click()
    assert window.table.rowCount() == 2
    assert sorted(window.table.item(row, 5).text() for row in range(2)) == ['Sans habilitation', 'À relancer']
    window.search.setText('Gamma')
    assert window.table.rowCount() == 1
    assert window.result_filter_buttons['TODO'].isChecked()
    assert window.result_detail_key is None
    window.close()


def test_same_siren_in_two_years_has_distinct_selection_and_detail(tmp_path, qapp):
    store, batch, window = make_window(tmp_path, [Dossier('Alpha', '000000001', 'Léa')], (2024, 2025))
    campaigns = store.batch_campaigns(batch)
    assert campaigns[0]['id'] != campaigns[1]['id']
    window.table.selectRow(0)
    assert window.result_detail_key == (campaigns[0]['id'], '000000001')
    assert window.result_detail_name.text() == 'Alpha'
    window.table.selectRow(1)
    assert window.result_detail_key == (campaigns[1]['id'], '000000001')
    assert window.result_detail_meta.text().endswith('2025')
    store.dossier_result(campaigns[0]['id'], '000000001', 'ERREUR', 'Mis à jour')
    window.refresh_table()
    assert window.result_detail_key == (campaigns[1]['id'], '000000001')
    assert window.result_detail_meta.text().endswith('2025')
    window.close()


def test_pagination_clears_detail_when_selected_identity_leaves_current_page(tmp_path, qapp):
    dossiers = [Dossier(f'Dossier {index:03}', f'{index:09}', 'Léa') for index in range(251)]
    store, batch, window = make_window(tmp_path, dossiers)
    window.table.selectRow(0)
    selected = window.result_detail_key
    assert selected is not None
    window.change_page(1)
    assert window.page_number == 1
    assert window.result_detail_key is None
    assert window.table.rowCount() == 1
    window.close()


def test_refresh_restores_all_selected_visible_errors_and_primary_detail(tmp_path, qapp):
    store, batch, window = make_window(
        tmp_path,
        [
            Dossier('Alpha', '000000001', 'Léa'),
            Dossier('Beta', '000000002', 'Léa'),
            Dossier('Gamma', '000000003', 'Léa'),
        ],
    )
    campaign = store.batch_campaigns(batch)[0]
    store.dossier_result(campaign['id'], '000000001', 'ERREUR', 'Alpha')
    store.dossier_result(campaign['id'], '000000002', 'ERREUR', 'Beta')
    window.refresh_table()
    window.table.selectRow(0)
    window.table.selectionModel().select(
        window.table.model().index(1, 0),
        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
    )
    expected = {(campaign['id'], '000000001'), (campaign['id'], '000000002')}
    assert window.selected_error_jobs() == expected
    primary_detail = window.result_detail_key

    store.dossier_result(campaign['id'], '000000003', 'EN_COURS', 'Traitement démarré')
    window.refresh_table()
    assert window.selected_error_jobs() == expected
    assert window.result_detail_key == primary_detail
    window.close()


def test_filter_moves_detail_to_surviving_selected_identity(tmp_path, qapp):
    store, batch, window = make_window(
        tmp_path,
        [
            Dossier('Alpha', '000000001', 'Léa'),
            Dossier('Beta', '000000002', 'Léa'),
        ],
    )
    campaign = store.batch_campaigns(batch)[0]
    store.dossier_result(campaign['id'], '000000001', 'ERREUR', 'Alpha')
    store.dossier_result(campaign['id'], '000000002', 'ERREUR', 'Beta')
    window.refresh_table()
    window.table.selectRow(0)
    window.table.selectionModel().select(
        window.table.model().index(1, 0),
        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
    )
    assert window.result_detail_key == (campaign['id'], '000000001')

    window.search.setText('Beta')
    assert window.table.rowCount() == 1
    assert window.selected_error_jobs() == {(campaign['id'], '000000002')}
    assert window.result_detail_key == (campaign['id'], '000000002')
    assert window.result_detail_name.text() == 'Beta'
    window.close()


def test_a_notice_to_check_puts_its_dossier_in_the_review_tile(tmp_path, qapp):
    from collecte_cfe.notice_extraction import NoticeExtraction

    store, batch, window = make_window(
        tmp_path, [Dossier('Alpha', '000000001', 'Léa'), Dossier('Beta', '000000002', 'Léa')]
    )
    campaign = store.batch_campaigns(batch)[0]
    for siren, status in [('000000001', 'A_VERIFIER'), ('000000002', 'EXTRAIT')]:
        notice = store.register_notice(
            campaign['id'], siren, Notice(2025, 'R' + siren, '00001', '75', 'Paris', 'Solde', '2026-12-15')
        )
        store.notice_result(notice['id'], 'TERMINE')
        store.notice_extraction_result(notice['id'], NoticeExtraction(50000, 50000, 'A_REGLER', None, False, status))
        store.dossier_result(campaign['id'], siren, 'TERMINE')
    window.open_campaign(batch)

    assert window.result_filter_buttons['TODO'].count == 1 and window.result_filter_buttons['PAY'].count == 1
    window.result_filter_buttons['TODO'].click()
    assert window.table.rowCount() == 1
    assert (window.table.item(0, 0).text(), window.table.item(0, 5).text()) == ('Alpha', 'À vérifier')
    window.result_filter_buttons['ABSENT'].click()
    assert window.table.rowCount() == 0
    window.close()


def test_the_detail_panel_lists_every_notice_and_opens_its_own_pdf(tmp_path, qapp, monkeypatch):
    from collecte_cfe import ui_results

    store, batch, window = make_window(tmp_path, [Dossier('Alpha', '000000001', 'Léa')])
    campaign = store.batch_campaigns(batch)[0]
    notices = [
        Notice(2025, 'R1', '00001', '75', 'Paris', 'Solde', '2026-12-15'),
        Notice(2025, 'R2', '00002', '75', 'Paris', 'Solde', '2026-12-15'),
    ]
    paths = []
    for notice_data in notices:
        notice = store.register_notice(campaign['id'], '000000001', notice_data)
        store.notice_result(notice['id'], 'TERMINE')
        path = Path(notice['path'])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'%PDF')
        paths.append(path)
    opened = []
    problems = []
    monkeypatch.setattr(ui_results.QDesktopServices, 'openUrl', lambda url: opened.append(url.toLocalFile()) or True)
    monkeypatch.setattr(window, 'problem', problems.append)
    window.table.selectRow(0)

    panels = window.detail_notice_panels
    assert [panel.notice['reference'] for panel in panels] == ['R1', 'R2']
    assert panels[0].findChild(QLabel, 'noticeTitle').text() == 'Solde 2025'
    assert panels[0].read_button is not None and panels[0].classify_button is None
    panels[1].open_button.click()
    assert window.open_first_notice()
    assert [Path(path) for path in opened] == [paths[1], paths[0]]
    assert problems == []
    window.close()


def test_notice_pdf_open_failures_are_actionable(tmp_path, qapp, monkeypatch):
    from collecte_cfe import ui_results

    store, batch, window = make_window(tmp_path, [Dossier('Alpha', '000000001', 'Léa')])
    campaign = store.batch_campaigns(batch)[0]
    notice = store.register_notice(
        campaign['id'], '000000001', Notice(2025, 'R1', '00001', '75', 'Paris', 'Solde', '2026-12-15')
    )
    store.notice_result(notice['id'], 'TERMINE')
    problems = []
    monkeypatch.setattr(window, 'problem', problems.append)
    assert window.open_notice_pdf(Path(notice['path'])) is False
    assert 'introuvable' in problems[-1]

    path = Path(notice['path'])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'%PDF')
    monkeypatch.setattr(ui_results.QDesktopServices, 'openUrl', lambda _url: False)
    assert window.open_notice_pdf(path) is False
    assert 'Impossible d’ouvrir' in problems[-1]
    window.close()


def test_filtering_out_selected_dossier_closes_the_dossier_sheet(tmp_path, qapp):
    store, batch, window = make_window(
        tmp_path, [Dossier('Alpha', '000000001', 'Léa'), Dossier('Beta', '000000002', 'Paul')]
    )
    window.open_campaign(batch)
    window.table.selectRow(0)
    assert window.result_detail_key is not None and not window.result_detail_card.isHidden()
    window.search.setText('Beta')
    assert window.result_detail_key is None and window.result_detail_card.isHidden()
    window.close()


def test_selected_error_detail_exposes_full_copyable_error(tmp_path, qapp):
    long_error = 'Erreur complète : session expirée au portail. ' * 20
    store, batch, window = make_window(tmp_path, [Dossier('Alpha', '000000001', 'Léa')])
    campaign = store.batch_campaigns(batch)[0]
    store.dossier_result(campaign['id'], '000000001', 'ERREUR', long_error)
    window.refresh_table()
    window.table.selectRow(0)
    assert isinstance(window.result_detail_error, QPlainTextEdit)
    assert window.result_detail_error.toPlainText() == long_error
    assert window.result_detail_error.isReadOnly()
    window.close()


def test_selecting_a_dossier_does_not_extract_notice_pdf_details(tmp_path, qapp, monkeypatch):
    from collecte_cfe import ui_results

    store, batch, window = make_window(tmp_path, [Dossier('Alpha', '000000001', 'Léa')])
    campaign = store.batch_campaigns(batch)[0]
    store.register_notice(
        campaign['id'], '000000001', Notice(2025, 'R1', '00001', '75', 'Paris', 'Solde', '2026-12-15')
    )
    monkeypatch.setattr(
        ui_results,
        'ensure_notice_details',
        lambda *_: (_ for _ in ()).throw(AssertionError('PDF extraction on selection')),
    )
    window.table.selectRow(0)
    assert window.result_detail_key == (campaign['id'], '000000001')
    window.close()


def test_missing_habilitation_detail_offers_connections(tmp_path, qapp):
    store, batch, window = make_window(tmp_path, [Dossier('Alpha', '000000001', 'Léa')])
    campaign = store.batch_campaigns(batch)[0]
    store.dossier_result(campaign['id'], '000000001', 'SANS_HABILITATION')
    window.refresh_table()
    window.table.selectRow(0)
    assert not window.result_connections_button.isHidden()
    assert window.result_retry_button.isHidden()
    window.close()


def test_selected_dossier_retry_uses_only_selected_campaign_identity_and_capture_path(tmp_path, qapp):
    store, batch, window = make_window(
        tmp_path, [Dossier('Alpha', '000000001', 'Léa'), Dossier('Beta', '000000002', 'Paul')]
    )
    campaign = store.batch_campaigns(batch)[0]
    store.dossier_result(campaign['id'], '000000001', 'ERREUR', 'Alpha error')
    store.dossier_result(campaign['id'], '000000002', 'ERREUR', 'Beta error')
    window.refresh_table()

    class Worker:
        count = 4

        def __init__(self):
            self.calls = []

        def isRunning(self):
            return True

        def submit_campaign(self, *args):
            self.calls.append(args)

    worker = Worker()
    window.worker = worker
    window.browser_ready = True
    window.connected_sessions = dict.fromkeys(range(4), True)
    window.table.selectRow(1)
    assert window.result_retry_button.isEnabled()
    window.result_retry_button.click()
    assert len(worker.calls) == 1
    call = worker.calls[0]
    assert call[0:3] == (batch, True, {(campaign['id'], '000000002')})
    assert Path(call[3]).parts[-2] == '_Captures'
    window.worker = None
    window.close()


def test_the_table_shows_the_pointing_box_first_and_hides_the_year_for_one_year(tmp_path, qapp):
    store, batch, window = make_window(tmp_path, [Dossier('Alpha', '000000001', 'Léa')])
    header = window.table.horizontalHeader()
    assert [window.table.horizontalHeaderItem(i).text() for i in range(9)] == [
        'Dossier',
        'Pointé',
        'Collaborateur',
        'Année',
        'À payer',
        'Situation',
        'Échéance',
        'Compte prélevé',
        'Mail',
    ]
    assert [header.logicalIndex(i) for i in range(3)] == [1, 0, 2]
    assert window.table.isColumnHidden(3), 'a single-year campaign does not need the year column'
    assert window.table.item(0, 0).data(Qt.ItemDataRole.UserRole + 1) == '000000001'
    window.close()


def test_a_notice_to_check_is_classified_by_hand_in_the_detail_panel(tmp_path, qapp, monkeypatch):
    from collecte_cfe.notice_extraction import NoticeExtraction

    store, batch, window = make_window(tmp_path, [Dossier('Alpha', '000000001', 'Léa')])
    campaign = store.batch_campaigns(batch)[0]
    for reference, status in [('R1', 'A_VERIFIER'), ('R2', 'EXTRAIT')]:
        notice = store.register_notice(
            campaign['id'], '000000001', Notice(2025, reference, '00001', '75', 'Paris', 'Solde', '2026-12-15')
        )
        store.notice_result(notice['id'], 'TERMINE')
        store.notice_extraction_result(notice['id'], NoticeExtraction(50000, None, 'NON_INDIQUE', None, False, status))
    store.dossier_result(campaign['id'], '000000001', 'TERMINE')
    window.open_campaign(batch)
    announced = []
    monkeypatch.setattr(window.toast, 'announce', lambda text, *_args, **_kwargs: announced.append(text))
    window.table.selectRow(0)

    first, second = window.detail_notice_panels
    assert (
        first.classify_button.text() == 'Classer à la main' and second.classify_button.text() == 'Corriger la lecture'
    )
    assert first.form.isHidden()
    first.classify_button.click()
    assert not first.form.isHidden()
    first.form.method_input.setCurrentIndex(first.form.method_input.findData('A_REGLER'))
    first.form.due_input.setText('400,00')
    first.form.save_button.click()

    first_row, second_row = store.notices(campaign['id'])
    assert (first_row['extraction_status'], first_row['payment_method'], first_row['amount_due_cents']) == (
        'MANUEL',
        'A_REGLER',
        40000,
    )
    assert second_row['extraction_status'] == 'EXTRAIT'
    assert announced == ['Avis 1 classé à la main : À régler · sans prélèvement automatique.']
    assert window.detail_notice_panels[0].classify_button.text() == 'Corriger la lecture'
    assert window.detail_notice_panels[0].reading_label.text() == 'Classée à la main'
    assert window.table.item(0, 5).text() == 'À régler'
    window.close()


def test_an_invalid_manual_classification_is_refused_with_a_reason(tmp_path, qapp):
    from collecte_cfe.notice_extraction import NoticeExtraction

    store, batch, window = make_window(tmp_path, [Dossier('Alpha', '000000001', 'Léa')])
    campaign = store.batch_campaigns(batch)[0]
    notice = store.register_notice(
        campaign['id'], '000000001', Notice(2025, 'R1', '00001', '75', 'Paris', 'Solde', '2026-12-15')
    )
    store.notice_result(notice['id'], 'TERMINE')
    store.notice_extraction_result(notice['id'], NoticeExtraction(None, None, 'NON_INDIQUE', None, False, 'A_VERIFIER'))
    window.table.selectRow(0)
    form = window.detail_notice_panels[0].form
    form.method_input.setCurrentIndex(form.method_input.findData('PRELEVEMENT_ECHEANCE'))
    form.tax_input.setText('500,00')
    form.save_button.click()
    assert form.error_label.text() == 'Indiquez le montant à payer imprimé sur l’avis.'
    assert not form.error_label.isHidden()
    assert store.notices(campaign['id'])[0]['extraction_status'] == 'A_VERIFIER'
    window.close()


def test_a_finished_dossier_is_pointed_from_its_detail_and_shown_in_the_table(tmp_path, qapp, monkeypatch):
    store, batch, window = make_window(
        tmp_path, [Dossier('Alpha', '000000001', 'Léa'), Dossier('Beta', '000000002', 'Léa')]
    )
    campaign = store.batch_campaigns(batch)[0]
    store.dossier_result(campaign['id'], '000000001', 'TERMINE')
    announced = []
    monkeypatch.setattr(window.toast, 'announce', lambda text, *_args, **_kwargs: announced.append(text))
    window.campaign_changed()
    row = next(i for i in range(window.table.rowCount()) if window.table.item(i, 0).text() == 'Alpha')
    window.table.selectRow(row)

    assert window.result_point_button.isVisible() or not window.result_point_button.isHidden()
    assert window.result_point_button.text() == 'Pointer ce dossier'
    window.result_point_button.click()

    assert store.dossiers(campaign['id'])[0]['pointed_at'] != ''
    assert announced == ['Alpha pointé.']
    row = next(i for i in range(window.table.rowCount()) if window.table.item(i, 0).text() == 'Alpha')
    assert window.table.item(row, 1).data(Qt.ItemDataRole.UserRole) == (True, True)
    assert window.table.item(row, 1).toolTip().startswith('Pointé le ')
    assert window.result_point_button.text() == 'Retirer le pointage'
    assert window.result_detail_pointed.text().startswith('Pointé le ')

    beta = next(i for i in range(window.table.rowCount()) if window.table.item(i, 0).text() == 'Beta')
    window.table.clearSelection()
    window.table.selectRow(beta)
    assert window.result_point_button.isHidden()
    window.close()


def test_several_finished_dossiers_are_pointed_together_and_filtered(tmp_path, qapp, monkeypatch):
    store, batch, window = make_window(
        tmp_path,
        [
            Dossier('Alpha', '000000001', 'Léa'),
            Dossier('Beta', '000000002', 'Léa'),
            Dossier('Gamma', '000000003', 'Léa'),
        ],
    )
    campaign = store.batch_campaigns(batch)[0]
    for siren in ('000000001', '000000002', '000000003'):
        store.dossier_result(campaign['id'], siren, 'TERMINE')
    monkeypatch.setattr(window.toast, 'announce', lambda text, *_args, **_kwargs: None)
    window.open_campaign(batch)
    selection = window.table.selectionModel()
    for row in range(2):
        selection.select(
            window.table.model().index(row, 0),
            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
        )

    assert window.result_point_button.text() == 'Pointer les 2 dossiers'
    window.result_point_button.click()

    assert sum(bool(d['pointed_at']) for d in store.dossiers(campaign['id'])) == 2
    window.unpointed_filter.click()
    assert window.table.rowCount() == 1
    window.close()


def test_a_click_on_the_pointing_box_points_only_that_dossier(tmp_path, qapp, monkeypatch):
    store, batch, window = make_window(
        tmp_path, [Dossier('Alpha', '000000001', 'Léa'), Dossier('Beta', '000000002', 'Léa')]
    )
    campaign = store.batch_campaigns(batch)[0]
    store.dossier_result(campaign['id'], '000000001', 'TERMINE')
    monkeypatch.setattr(window.toast, 'announce', lambda text, *_args, **_kwargs: None)
    window.open_campaign(batch)
    window.point_clicked_cell(0, 1)
    window.point_clicked_cell(1, 1)
    assert [bool(d['pointed_at']) for d in store.dossiers(campaign['id'])] == [True, False], (
        'a dossier still to collect cannot be pointed'
    )
    window.point_clicked_cell(0, 1)
    assert not store.dossiers(campaign['id'])[0]['pointed_at']
    window.close()


def test_the_manual_reading_form_returns_the_typed_values(qapp):
    from collecte_cfe.ui_results import ManualReadingForm

    form = ManualReadingForm({'tax_amount_cents': 50000, 'amount_due_cents': None, 'payment_method': 'NON_INDIQUE'})
    assert form.tax_input.text() == '500,00'
    assert form.due_input.text() == ''
    form.method_input.setCurrentIndex(form.method_input.findData('A_REGLER'))
    form.due_input.setText('1 284,00')
    assert form.values() == ('A_REGLER', 50000, 128400)


def test_a_dossier_reading_sums_what_is_due_and_keeps_the_reading_that_needs_attention():
    from collecte_cfe.ui_results import dossier_reading

    read = {'extraction_status': 'EXTRAIT', 'payment_method': 'A_REGLER', 'amount_due_cents': 1000}
    assert dossier_reading([]) == (None, '', '')
    assert dossier_reading([read, {**read, 'amount_due_cents': 2000}]) == (3000, 'À régler', 'Lue')
    assert dossier_reading([read, {**read, 'payment_method': 'PRELEVEMENT_ECHEANCE'}])[1] == 'Plusieurs modes'
    assert dossier_reading([read, {**read, 'extraction_status': 'MANUEL'}])[2] == 'Classée à la main'
    assert dossier_reading([read, {'extraction_status': '', 'amount_due_cents': None}]) == (None, 'À régler', 'Non lue')
    assert dossier_reading([read, {**read, 'extraction_status': 'A_VERIFIER'}])[2] == 'À vérifier'


def test_the_table_shows_what_each_dossier_has_to_pay_and_sorts_on_it(tmp_path, qapp):
    from collecte_cfe.notice_extraction import NoticeExtraction

    store, batch, window = make_window(
        tmp_path,
        [
            Dossier('Alpha', '000000001', 'Léa'),
            Dossier('Beta', '000000002', 'Léa'),
            Dossier('Gamma', '000000003', 'Léa'),
        ],
    )
    campaign = store.batch_campaigns(batch)[0]
    for siren, due, method in [
        ('000000001', 128400, 'A_REGLER'),
        ('000000002', 0, 'AUCUN_PAIEMENT'),
        ('000000003', 231000, 'PRELEVEMENT_ECHEANCE'),
    ]:
        notice = store.register_notice(
            campaign['id'], siren, Notice(2025, 'R' + siren, '00001', '75', 'Paris', 'Solde', '2026-12-15')
        )
        store.notice_result(notice['id'], 'TERMINE')
        store.notice_extraction_result(notice['id'], NoticeExtraction(due, due, method, None, False, 'EXTRAIT'))
        store.dossier_result(campaign['id'], siren, 'TERMINE')
    window.refresh_table()

    def rows():
        return [
            (window.table.item(row, 0).text(), window.table.item(row, 4).text(), window.table.item(row, 5).text())
            for row in range(window.table.rowCount())
        ]

    assert rows() == [
        ('Alpha', '1 284,00 €', 'À régler'),
        ('Beta', '0,00 €', 'Rien à payer'),
        ('Gamma', '2 310,00 €', 'Prélevé'),
    ]
    window.table.horizontalHeader().sectionClicked.emit(4)
    assert [name for name, *_ in rows()] == ['Beta', 'Alpha', 'Gamma']
    window.table.horizontalHeader().sectionClicked.emit(4)
    assert [name for name, *_ in rows()] == ['Gamma', 'Alpha', 'Beta']
    window.close()


def test_results_actions_appear_only_when_they_apply(tmp_path, qapp):
    store, batch, window = make_window(
        tmp_path,
        [
            Dossier('Alpha', '000000001', 'Léa'),
            Dossier('Beta', '000000002', 'Léa'),
            Dossier('Gamma', '000000003', 'Léa'),
        ],
    )
    campaign = store.batch_campaigns(batch)[0]
    window.open_campaign(batch)
    assert window.pause_button.isHidden() and window.retry_button.isHidden() and window.retry_selected_button.isHidden()
    assert window.export_button.isHidden() and window.run_button.isHidden(), 'nothing to export before the collection'
    assert window.next_step_button.text() == 'Lancer la collecte' and not window.next_step_button.isHidden()
    assert window.campaign_tab_buttons[COLLECT_PAGE].badge.text() == 'à lancer', 'the launch is in the Collecte tab'
    window.navigate(COLLECT_PAGE)
    assert not window.login_button.isHidden() and window.export_button.isHidden()
    window.navigate(2)

    store.dossier_result(campaign['id'], '000000001', 'ERREUR', 'Alpha')
    store.dossier_result(campaign['id'], '000000002', 'ERREUR', 'Beta')
    store.dossier_result(campaign['id'], '000000003', 'TERMINE')
    window.refresh_table()
    assert window.run_button.isHidden() and not window.retry_button.isHidden()
    assert not window.export_button.isHidden() and window.next_step_button.isHidden()
    assert window.retry_button.text() == 'Relancer les 2 erreurs'
    assert not window.retry_button.isEnabled() and 'Collecte' in window.retry_button.toolTip()
    window.table.selectRow(0)
    assert window.retry_selected_button.isHidden(), 'the dossier sheet already offers to retry one dossier'
    window.table.selectionModel().select(
        window.table.model().index(1, 0),
        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
    )
    assert not window.retry_selected_button.isHidden()
    assert window.retry_selected_button.text() == 'Relancer la sélection (2) + captures'
    window.close()


def test_pointing_from_the_keyboard_moves_to_the_next_dossier_to_point(tmp_path, qapp, monkeypatch):
    store, batch, window = make_window(
        tmp_path,
        [
            Dossier('Alpha', '000000001', 'Léa'),
            Dossier('Beta', '000000002', 'Léa'),
            Dossier('Gamma', '000000003', 'Léa'),
        ],
    )
    campaign = store.batch_campaigns(batch)[0]
    for siren in ('000000001', '000000002', '000000003'):
        store.dossier_result(campaign['id'], siren, 'TERMINE')
    monkeypatch.setattr(window.toast, 'announce', lambda text, *_args, **_kwargs: None)
    window.refresh_table()
    window.table.selectRow(0)
    window.point_from_keyboard()
    assert window.result_detail_name.text() == 'Beta'

    window.unpointed_filter.click()
    window.table.selectRow(0)
    assert window.result_detail_name.text() == 'Beta'
    window.point_from_keyboard()
    assert window.table.rowCount() == 1
    assert window.result_detail_name.text() == 'Gamma'
    assert [bool(d['pointed_at']) for d in store.dossiers(campaign['id'])] == [True, True, False]
    window.close()
