import os
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import Qt, QUrl
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from collecte_cfe.app import MainWindow
from collecte_cfe.credentials import AccountManager
from collecte_cfe.domain import Dossier
from collecte_cfe.store import Store
from collecte_cfe.ui_browser import (
    browser_canvas_extent,
    browser_canvas_width,
    display_portal_url,
    responsive_browser_zoom,
)
from collecte_cfe.ui_collect import COLLECT_PAGE
from collecte_cfe.ui_components import AccountDialog
from collecte_cfe.ui_style import STYLE
from collecte_cfe.ui_widgets import ElidedPathLineEdit, PortfolioDropZone
from collecte_cfe.window_embed import WindowEmbedder, native_child_geometry
from tests.test_credentials import MemoryVault


def test_account_dialog_masks_password_and_session_selection_persists(tmp_path, qapp):
    dialog = AccountDialog()
    assert dialog.password.echoMode() == dialog.password.EchoMode.Password
    dialog.close()
    store = Store(tmp_path / 'db.sqlite')
    manager = AccountManager(store, MemoryVault())
    first = manager.add('Cabinet A', 'a@example.fr', 'local secret')
    second = manager.add('Cabinet B', 'b@example.fr', 'other secret')
    w = MainWindow(store, settings=MemorySettings(), accounts=manager)
    assert [manager.account_for_session(i)['id'] for i in range(4)] == [first, second, first, second], (
        'the accounts take the sessions in turn'
    )
    w.close()
    reopened = MainWindow(store, settings=MemorySettings(), accounts=manager)
    assert [manager.account_for_session(i)['id'] for i in range(4)] == [first, second, first, second]
    reopened.close()


def test_account_dialog_can_show_and_hide_typed_password(qapp):
    dialog = AccountDialog()
    dialog.password.setText('Un secret provisoire')
    assert dialog.password.echoMode() == dialog.password.EchoMode.Password
    dialog.password_visibility.click()
    assert dialog.password.echoMode() == dialog.password.EchoMode.Normal
    assert dialog.password.text() == 'Un secret provisoire'
    dialog.password_visibility.click()
    assert dialog.password.echoMode() == dialog.password.EchoMode.Password
    assert dialog.password.text() == 'Un secret provisoire'
    dialog.close()


def test_connect_accounts_stays_on_the_current_page(tmp_path, qapp):
    w = MainWindow(Store(tmp_path / 'db.sqlite'), settings=MemorySettings())
    w.navigate(1)
    assert w.pages.currentIndex() == 4, 'the expert accounts are in the settings now'
    requested = []
    w.start_browser_sessions = lambda count, focus=0, initial_sessions=None, show_browser=True: requested.append(
        (count, show_browser)
    )
    w.open_browser()
    assert requested == [(w.session_count.value(), False)]
    assert w.pages.currentIndex() == 4
    w.close()


def test_bulk_session_completion_does_not_force_browser_view(tmp_path, qapp):
    w = MainWindow(Store(tmp_path / 'db.sqlite'), settings=MemorySettings())
    w.navigate(1)
    w.show_browser_after_connect = False
    w.show_session = lambda _index: (_ for _ in ()).throw(AssertionError('Browser must stay hidden'))
    w.browser_opened()
    assert not w.browser_workspace_active
    assert w.pages.currentIndex() == 4
    w.close()


def test_user_can_open_session_after_background_connection(tmp_path, qapp):
    w = MainWindow(Store(tmp_path / 'db.sqlite'), settings=MemorySettings())
    w.show_browser_after_connect = False
    w.browser_hosts[0].handle = 123
    w.show_session(0)
    assert w.browser_workspace_active
    w.browser_hosts[0].handle = None
    w.close()


def test_settings_page_saves_local_name_and_displays_timestamped_events(tmp_path, qapp):
    import re

    settings = MemorySettings()
    w = MainWindow(Store(tmp_path / 'db.sqlite'), settings=settings)
    w.navigate(4)
    assert w.nav[1].title == 'Réglages' and w.nav[1].isChecked() and w.title.text() == 'Réglages'
    assert [link.text() for link in w.settings_links.values()] == [
        'Comptes impots.gouv',
        'Texte des mails',
        'Ce poste',
        'Journal',
    ]
    w.account_name.setText('Camille Martin')
    w.save_profile_name()
    assert settings.value('profile/display_name') == 'Camille Martin'
    assert (w.user_name.text(), w.user_initials.text(), w.user_sessions.text()) == (
        'Camille Martin',
        'CM',
        'Non connecté',
    )
    w.diagnostics.append('session_connected', session=0)
    w.refresh_diagnostic_view()
    last = w.logs_view.toPlainText().splitlines()[-1]
    assert re.fullmatch(r'\d{2}/\d{2} \d{2}:\d{2}  Session connectée \| Session 1', last), last
    assert 'Texte par défaut' in w.settings_mail_state.text() and '{avis}' in w.settings_mail_preview.text()
    assert not hasattr(w, 'connection_count_buttons'), 'the number of sessions is not asked'
    w.new_campaign()
    assert w.session_count.value() == 4
    w.close()


def test_footer_live_console_can_expand_without_persisting_raw_messages(tmp_path, qapp):
    w = MainWindow(Store(tmp_path / 'db.sqlite'), settings=MemorySettings())
    assert w.console_view.isHidden()
    w.console_toggle.click()
    assert not w.console_view.isHidden()
    w.append_live_event('Session 1 · recherche en cours')
    assert '› Session 1 · recherche en cours' in w.console_view.toPlainText()
    w.session_updated(0, 'Dossier de test · 2025')
    assert 'Dossier de test' not in w.console_view.toPlainText(), 'the sessions are not followed one by one'
    w.session_updated(0, 'CAPTCHA à vérifier')
    assert '› CAPTCHA à vérifier' in w.console_view.toPlainText()
    assert 'recherche en cours' not in w.diagnostics.recent()
    w.console_toggle.click()
    assert w.console_view.isHidden()
    w.close()


def test_integrity_export_event_shows_a_warning_that_stays(tmp_path, qapp):
    store = Store(tmp_path / 'db.sqlite')
    batch = store.create_batch([2025], tmp_path / 'out', [Dossier('Alpha', '000000001', 'Léa')], 1)
    w = MainWindow(store, settings=MemorySettings())
    w.refresh_campaigns(select=batch)
    message = 'Les résultats sont enregistrés. Export Excel impossible : avis classé modifié pour le SIREN 000000001 ; relancez la collecte.'
    w.append_live_event(message)
    assert w.toast.message.text() == 'Export bloqué : relancez la collecte' and w.toast.detail.text() == message
    assert w.toast.property('tone') == 'warning' and not w.toast.timer.isActive(), 'a warning stays until it is closed'
    w.append_live_event('Les résultats sont enregistrés. Export Excel impossible : vérifiez la sortie.')
    assert w.toast.message.text() == 'Export Excel à relancer'
    assert w.toast.detail.text() == 'Les résultats sont enregistrés. Fermez le classeur ouvert, puis relancez l’export.'
    w.close()


class MemorySettings:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def value(self, key, default=None, type=None):
        value = self.values.get(key, default)
        return type(value) if type and value is not None else value

    def setValue(self, key, value):
        self.values[key] = value

    def contains(self, key):
        return key in self.values


def test_the_sidebar_always_shows_its_labels(tmp_path, qapp):
    w = MainWindow(Store(tmp_path / 'db.sqlite'), settings=MemorySettings())
    assert w.rail.width() == 232 and not hasattr(w, 'sidebar_toggle')
    assert [button.text() for button in w.nav] == ['Toutes les campagnes', 'Réglages']
    w.resize(900, 620)
    w.apply_responsive_layout()
    assert w.rail.width() == 232, 'even a narrow window keeps the full sidebar'
    w.close()


def test_campaign_page_matches_the_new_shell(tmp_path, qapp):
    w = MainWindow(Store(tmp_path / 'db.sqlite'), settings=MemorySettings({'ui/sidebar_collapsed': False}))
    assert w.rail.width() == 232
    assert w.import_zone.objectName() == 'importZone'
    assert w.portfolio_columns.text().count('SIREN') == 1
    assert w.run_button.parent() is w.header_actions
    assert w.run_button.text() == 'Lancer la collecte'
    assert w.run_button.objectName() == 'runCollection'
    assert w.create_button.objectName() == 'primary'
    assert w.campaigns.parent() is w.header_actions
    assert w.campaign_flow.currentWidget() is w.campaign_library
    assert w.new_campaign_button.text() == 'Nouvelle campagne'
    w.close()


def test_collection_button_explains_when_work_is_running(tmp_path, qapp):
    w = MainWindow(Store(tmp_path / 'db.sqlite'), settings=MemorySettings())
    w.collection_state(True)
    assert w.run_button.text() == 'Collecte en cours…'
    w.collection_state(False)
    assert w.run_button.text() == 'Lancer la collecte'
    assert 'QPushButton#runCollection' in STYLE
    w.close()


def test_portfolio_drop_zone_accepts_only_one_csv_or_xlsx_file(tmp_path):
    csv = tmp_path / 'clients.csv'
    csv.write_text('x', encoding='utf-8')
    pdf = tmp_path / 'document.pdf'
    pdf.write_text('x', encoding='utf-8')
    assert Path(PortfolioDropZone.portfolio_path([QUrl.fromLocalFile(str(csv))])) == csv
    assert PortfolioDropZone.portfolio_path([QUrl.fromLocalFile(str(pdf))]) is None
    assert PortfolioDropZone.portfolio_path([QUrl.fromLocalFile(str(csv)), QUrl.fromLocalFile(str(csv))]) is None


def test_dropped_portfolio_is_loaded_into_the_new_campaign(tmp_path):
    app = QApplication.instance() or QApplication([])
    csv = tmp_path / 'clients.csv'
    csv.write_text('Nom du dossier,SIREN,Collaborateur\nAlpha,000000001,Léa\n', encoding='utf-8')
    w = MainWindow(Store(tmp_path / 'db.sqlite'), settings=MemorySettings())
    w.new_campaign()
    w.import_zone.file_dropped.emit(str(csv))
    app.processEvents()
    assert len(w.portfolio) == 1 and w.portfolio[0].siren == '000000001'
    assert w.import_zone.property('loaded') is True and 'clients.csv' in w.portfolio_label.text()
    w.close()


def test_navigation_labels_are_short_and_unambiguous(tmp_path, qapp):
    w = MainWindow(Store(tmp_path / 'db.sqlite'), settings=MemorySettings())
    assert [button.text() for button in w.nav] == ['Toutes les campagnes', 'Réglages']
    assert w.campaigns_heading.text() == 'Campagnes' and w.new_campaign_nav.text() == '+ Nouvelle campagne'
    assert not hasattr(w, 'session_buttons') and w.campaign_tabs.isHidden(), (
        'the campaign tabs only show inside a campaign'
    )
    assert [tab.title_label.text() for tab in w.campaign_tab_buttons.values()] == ['Collecte', 'Dossiers', 'Courriels']
    w.close()


def test_year_chips_choose_one_or_several_years(tmp_path):
    from datetime import datetime

    QApplication.instance() or QApplication([])
    w = MainWindow(Store(tmp_path / 'db.sqlite'), settings=MemorySettings())
    w.new_campaign()
    now = datetime.now().year
    assert [chip.year for chip in w.year_chips] == [now - 3, now - 2, now - 1, now]
    assert w.year_mode == 'single' and [chip.year for chip in w.year_chips if chip.isChecked()] == [now]
    next(chip for chip in w.year_chips if chip.year == now - 1).click()
    assert w.year_mode == 'multiple' and w.selected_years() == [now - 1, now] and w.years.text() == f'{now - 1}, {now}'
    next(chip for chip in w.year_chips if chip.year == now).click()
    assert w.year_mode == 'single' and w.selected_years() == [now - 1]
    next(chip for chip in w.year_chips if chip.year == now - 1).click()
    assert w.selected_years() == [now - 1], 'one year at least stays chosen'
    w.close()


def test_output_path_keeps_its_full_value_while_displaying_a_short_version(qapp, tmp_path):
    field = ElidedPathLineEdit()
    path = tmp_path / 'un dossier avec un nom tres long' / 'sortie CFE'
    field.resize(180, 40)
    field.setText(str(path))
    field.show()
    qapp.processEvents()
    assert field.text() == str(path)
    assert field.toolTip() == str(path)
    assert field.displayed_path() != str(path) and '…' in field.displayed_path()


def test_export_shows_a_toast_with_a_direct_workbook_action(qapp, tmp_path):
    store = Store(tmp_path / 'db.sqlite')
    batch = store.create_batch([2025], tmp_path / 'out', [Dossier('Alpha', '000000001', 'Léa')], 1)
    w = MainWindow(store, settings=MemorySettings())
    w.refresh_campaigns(select=batch)
    w.show()
    qapp.processEvents()
    w.export()
    qapp.processEvents()
    assert w.toast.isVisible()
    assert '1 classeur' in w.toast.message.text()
    assert w.toast.open_button.text().startswith('Ouvrir le fichier') and w.toast.property('tone') == 'success'
    assert (tmp_path / 'out' / 'Léa' / 'Suivi Campagne 1 — Léa.xlsx').exists()
    w.close()


def test_export_shows_integrity_reason_but_keeps_generic_other_errors(qapp, tmp_path, monkeypatch):
    import collecte_cfe.dashboard as dashboard

    store = Store(tmp_path / 'db.sqlite')
    batch = store.create_batch([2025], tmp_path / 'out', [Dossier('Alpha', '000000001', 'Léa')], 1)
    w = MainWindow(store, settings=MemorySettings())
    w.refresh_campaigns(select=batch)
    monkeypatch.setattr(
        dashboard,
        'export_batch_actions',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError('SIREN 000000001 : relancez la collecte.')),
    )
    w.export()
    qapp.processEvents()
    assert w.toast.message.text() == 'Export impossible'
    assert '000000001' in w.toast.detail.text() and 'relancez la collecte' in w.toast.detail.text()
    monkeypatch.setattr(
        dashboard, 'export_batch_actions', lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError('détail interne'))
    )
    w.export()
    qapp.processEvents()
    assert w.toast.detail.text() == 'Vérifiez le dossier de sortie et fermez les classeurs ouverts.'
    w.close()


def test_campaign_source_view_distinguishes_file_and_account_modes(tmp_path, qapp):
    w = MainWindow(Store(tmp_path / 'db.sqlite'), settings=MemorySettings())
    assert 'tous les dossiers' in w.portfolio_label.text().lower()
    csv = tmp_path / 'cabinet.csv'
    csv.write_text('Nom du dossier,SIREN,Collaborateur\nAlpha,000000001,Léa\n', encoding='utf-8')
    w.load_portfolio(csv)
    assert '1 dossier' in w.portfolio_label.text().lower()
    assert 'cabinet.csv' in w.portfolio_label.text() and 'aucune adresse e-mail' in w.portfolio_label.text()
    assert 'e-mail du client (facultatif)' in w.portfolio_columns.text()
    facts = [[label.text() for label in row.findChildren(QLabel)] for row in w.portfolio_fact_rows]
    assert facts[:3] == [
        ['Dossiers lus', '<b>1</b>'],
        ['Collaborateurs', '<b>1</b>'],
        ['Adresses e-mail', '<b>0 sur 1</b>'],
    ]
    assert w.portfolio_mail_note.text().startswith('1 client sans adresse')
    csv.write_text(
        'Nom du dossier,SIREN,Collaborateur,E-mail\nAlpha,000000001,Léa,alpha@example.fr\n', encoding='utf-8'
    )
    w.load_portfolio(csv)
    assert '1 adresse e-mail' in w.portfolio_label.text() and w.portfolio[0].email == 'alpha@example.fr'
    w.clear_portfolio()
    assert 'tous les dossiers' in w.portfolio_label.text().lower()
    w.close()


def test_lines_to_ignore_are_listed_under_the_drop_zone_without_a_dialog(tmp_path, qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    w = MainWindow(Store(tmp_path / 'db.sqlite'), settings=MemorySettings())
    w.new_campaign()
    csv = tmp_path / 'cabinet.csv'
    csv.write_text(
        'Nom du dossier;SIREN;Collaborateur\nAlpha;123456782;Léa\nBeta;41209;Léa\nAlpha bis;123456782;Léa\n',
        encoding='utf-8-sig',
    )
    monkeypatch.setattr(QMessageBox, 'exec', lambda dialog: (_ for _ in ()).throw(AssertionError('no dialog expected')))

    w.load_portfolio(csv)

    assert [d.name for d in w.portfolio] == ['Alpha']
    assert '2 lignes ignorées' in w.portfolio_label.text()
    assert not w.portfolio_check_panel.isHidden() and w.portfolio_check_panel.property('tone') == 'warning'
    assert w.portfolio_check_title.text().startswith('2 lignes ignorées. Corrigez le fichier')
    assert w.portfolio_check_lines.text().splitlines() == [
        'Ligne 3 · ignorée : SIREN manquant ou invalide (9 chiffres requis)',
        'Ligne 4 · ignorée : SIREN en doublon (123456782), déjà présent ligne 2',
    ]
    assert w.create_button.text() == 'Créer la campagne · 1 dossier'
    w.clear_portfolio()
    assert w.portfolio_check_panel.isHidden() and w.create_button.text() == 'Créer la campagne'
    w.close()


def test_a_wrong_check_digit_is_reported_without_blocking_the_import(tmp_path, qapp):
    w = MainWindow(Store(tmp_path / 'db.sqlite'), settings=MemorySettings())
    w.new_campaign()
    csv = tmp_path / 'cabinet.csv'
    csv.write_text(
        'Nom du dossier;SIREN;Collaborateur\nAlpha;123456782;Léa\nDelta;123456789;Léa\n', encoding='utf-8-sig'
    )

    w.load_portfolio(csv)

    assert len(w.portfolio) == 2
    assert '1 SIREN à vérifier' in w.portfolio_label.text()
    assert not w.portfolio_check_panel.isHidden() and w.portfolio_check_panel.property('tone') == 'info'
    assert (
        w.portfolio_check_lines.text()
        == 'Ligne 3 · à vérifier : SIREN 123456789 à vérifier : la clé de contrôle ne correspond pas'
    )
    w.close()


def test_dashboard_style_uses_the_reference_palette_and_local_font_tokens():
    for token in ('#2F45D8', '#F2F4F8', '#131A2C', '#E9ECF3', '#FFF0DC', '#DFF1F6', '#FCE5EB'):
        assert token in STYLE
    assert '#BC4937' not in STYLE and '#17243B' not in STYLE
    assert '@BODYFONT@' in STYLE and '@TITLEFONT@' in STYLE and '@MONOFONT@' in STYLE
    assert 'http://' not in STYLE.lower() and 'https://' not in STYLE.lower()
    assert 'QMessageBox' in STYLE and 'QInputDialog' in STYLE
    assert 'QMessageBox QLabel' in STYLE and 'background:#FFFFFF' in STYLE


def test_sessions_follow_the_campaign_session_count(tmp_path, qapp):
    store = Store(tmp_path / 'db.sqlite')
    batch = store.create_batch([2025], tmp_path / 'out', [], 3)
    w = MainWindow(store, settings=MemorySettings())
    w.open_campaign(batch)
    assert store.batch(batch)['sessions'] == 4 and len(w.session_statuses) == 4
    w.session_updated(1, 'Connectée')
    assert w.session_statuses[1] == 'Connectée'
    assert 'Connectée' in w.browser_session_tabs[1].text()
    w.close()


def test_connections_offer_all_at_once_or_one_by_one(tmp_path, qapp):
    store = Store(tmp_path / 'db.sqlite')
    store.create_batch([2025], tmp_path / 'out', [], 3)
    w = MainWindow(store, settings=MemorySettings())
    calls = []
    w.start_browser_sessions = lambda count, focus=0, initial_sessions=None, show_browser=True: calls.append(
        (count, focus, initial_sessions, show_browser)
    )
    w.open_browser()
    assert calls == [(4, 0, [0, 1, 2, 3], False)]
    assert w.login_button.text() == 'Se connecter'
    w.close()


def test_campaign_name_is_optional_and_suggested_from_the_year_and_portfolio(tmp_path, qapp):
    store = Store(tmp_path / 'db.sqlite')
    w = MainWindow(store, settings=MemorySettings())
    w.new_campaign()
    w.output.setText(str(tmp_path / 'out'))
    w.year.setValue(2025)
    w.campaign_name.setText('CFE décembre')
    w.create_campaign()
    batch = w.selected_batch()
    assert store.batch(batch)['name'] == 'CFE décembre'
    assert w.campaigns.currentText() == 'CFE décembre · 2025'

    w.new_campaign()
    w.output.setText(str(tmp_path / 'out-2'))
    w.year.setValue(2025)
    assert w.campaign_name.placeholderText() == 'CFE 2025 · compte expert'
    csv = tmp_path / 'clients-cabinet.csv'
    csv.write_text('Nom du dossier,SIREN,Collaborateur\nAlpha,123456782,Léa\n', encoding='utf-8')
    w.load_portfolio(csv)
    assert w.campaign_name.placeholderText() == 'CFE 2025 · clients-cabinet'
    w.set_year_mode('multiple')
    w.years.setText('2023-2025')
    assert w.campaign_name.placeholderText() == 'CFE 2023-2025 · clients-cabinet'
    w.create_campaign()
    assert store.batch(w.selected_batch())['name'] == 'CFE 2023-2025 · clients-cabinet'
    w.close()


def test_the_launch_button_says_why_the_collection_cannot_start(tmp_path, qapp):
    store = Store(tmp_path / 'db.sqlite')
    batch = store.create_batch([2025], tmp_path / 'out', [Dossier('Alpha', '123456782', 'Léa')], 2)
    w = MainWindow(store, settings=MemorySettings())
    w.open_campaign(batch, COLLECT_PAGE)
    assert not w.login_button.isHidden() and w.run_button.isHidden(), 'the sessions are connected before the launch'
    assert w.run_hint.text() == 'Connectez-vous d’abord.'
    assert not w.run_hint.isHidden() and w.run_button.toolTip() == w.run_hint.text()
    w.browser_opened()
    w.session_updated(0, 'Connectée')
    assert w.run_hint.text() == 'Connexion en cours…' and not w.login_button.isHidden()
    assert w.user_sessions.text() == 'Connecté à impots.gouv.fr'
    for index in (1, 2, 3):
        w.session_updated(index, 'Connectée')
    assert (
        w.run_button.isEnabled() and not w.run_button.isHidden() and w.login_button.isHidden() and w.run_hint.isHidden()
    )
    w.navigate(3)
    assert w.run_button.isHidden() and w.run_hint.isHidden() and not w.mail_template_button.isHidden()
    w.close()


def test_expert_accounts_are_listed_with_what_they_are_doing(tmp_path, qapp):
    store = Store(tmp_path / 'db.sqlite')
    store.create_batch([2025], tmp_path / 'out', [], 2)
    manager = AccountManager(store, MemoryVault())
    w = MainWindow(store, settings=MemorySettings(), accounts=manager)
    assert not w.accounts_empty.isHidden() and w.account_rows == []
    first = manager.add('Cabinet A', 'a@example.fr', 'secret')
    second = manager.add('Cabinet B', 'b@example.fr', 'secret')
    w.refresh_account_choices()
    states = lambda: [(row.state.text(), row.edit_button.text()) for row in w.account_rows]
    assert w.accounts_empty.isHidden() and states() == [('Mot de passe enregistré', 'Modifier')] * 2
    assert w.account_rows[0].state.property('tone') == 'success'
    assert [manager.account_for_session(i)['id'] for i in range(4)] == [first, second, first, second]
    manager.block_login(second)
    w.update_account_states()
    assert (
        states()[1] == ('Identifiants refusés · à corriger', 'Corriger')
        and w.account_rows[1].edit_button.objectName() == 'primary'
    )
    w.close()


def test_campaign_library_selects_renames_and_creates_campaigns(tmp_path, qapp):
    store = Store(tmp_path / 'db.sqlite')
    first = store.create_batch([2024], tmp_path / 'first', [], 1, name='Ancienne campagne')
    second = store.create_batch([2025], tmp_path / 'second', [], 2, name='Campagne active')
    w = MainWindow(store, settings=MemorySettings())
    assert len(w.campaign_cards) == 2 and w.campaign_flow.currentWidget() is w.campaign_library
    w.rename_campaign(first, 'CFE historique')
    assert store.batch(first)['name'] == 'CFE historique'
    assert w.campaigns.itemText(w.campaigns.findData(first)) == 'CFE historique · 2024'
    w.open_campaign(first)
    assert w.selected_batch() == first and w.pages.currentIndex() == 2
    w.navigate(0)
    w.new_campaign_button.click()
    assert w.campaign_flow.currentWidget() is w.campaign_editor
    assert w.campaign_name.text() == ''
    assert [step.detail.text() for step in w.wizard_steps] == ['En cours', 'À venir']
    w.campaign_back.click()
    assert w.campaign_flow.currentWidget() is w.campaign_library and not w.new_campaign_button.isHidden()
    assert w.selected_batch() == first or w.selected_batch() == second
    w.close()


def test_campaign_delete_requires_confirmation_and_keeps_other_campaigns(tmp_path, monkeypatch, qapp):
    from PySide6.QtWidgets import QMessageBox

    store = Store(tmp_path / 'db.sqlite')
    first = store.create_batch([2024], tmp_path / 'first', [], 1)
    second = store.create_batch([2025], tmp_path / 'second', [], 1)
    w = MainWindow(store, settings=MemorySettings())
    shown = []

    def answer(choice):
        def exec_box(box):
            shown.append(
                (
                    box.text(),
                    box.informativeText(),
                    [button.text() for button in box.buttons()],
                    box.defaultButton().text(),
                )
            )
            box.setProperty('choice', choice)
            target = next(button for button in box.buttons() if button.text() == choice)
            target.click()

        return exec_box

    monkeypatch.setattr(QMessageBox, 'exec', answer('Annuler'))
    w.delete_campaign(first)
    assert len(store.batches()) == 2
    question, detail, buttons, default = shown[0]
    assert (
        question.startswith('Supprimer la campagne « ')
        and 'irréversible' in detail
        and 'restent dans le dossier de sortie' in detail
    )
    assert sorted(buttons) == ['Annuler', 'Supprimer définitivement'] and default == 'Annuler'
    monkeypatch.setattr(QMessageBox, 'exec', answer('Supprimer définitivement'))
    w.delete_campaign(first)
    assert [batch['id'] for batch in store.batches()] == [second]
    assert w.campaigns.findData(first) == -1 and len(w.campaign_cards) == 1
    w.close()


def test_sessions_can_be_closed_from_the_browser_toolbar(tmp_path, qapp):
    store = Store(tmp_path / 'db.sqlite')
    store.create_batch([2025], tmp_path / 'out', [], 2)
    w = MainWindow(store, settings=MemorySettings())
    calls = []

    class Worker:
        count = 2

        def isRunning(self):
            return True

        def close_session(self, index):
            calls.append(index)

    w.worker = Worker()
    w.session_updated(0, 'Connectée')
    w.browser_tabs.setCurrentIndex(0)
    w.close_current_session()
    w.browser_tabs.setCurrentIndex(1)
    w.close_current_session()
    assert calls == [0, 1]
    w.worker = None
    w.close()


def test_results_table_keeps_business_columns_and_progress_steps(tmp_path, qapp):
    w = MainWindow(Store(tmp_path / 'db.sqlite'), settings=MemorySettings())
    assert [w.table.horizontalHeaderItem(i).text() for i in range(9)] == [
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
    assert w.results_card.objectName() == 'resultsCard'
    assert w.results_scroll.widgetResizable()
    assert w.results_scroll.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    w.close()


def test_campaign_cards_and_results_identify_which_campaign_has_errors(tmp_path, qapp):
    store = Store(tmp_path / 'db.sqlite')
    first = store.create_batch([2025], tmp_path / 'out', [Dossier('A', '000000001', 'Léa')], 1, 'Ancienne campagne')
    second = store.create_batch([2025], tmp_path / 'out', [Dossier('B', '000000002', 'Léa')], 1, 'Campagne testée')
    campaign = store.batch_campaigns(second)[0]['id']
    store.dossier_result(campaign, '000000002', 'ERREUR', 'Délai dépassé')
    w = MainWindow(store, settings=MemorySettings())
    card_text = ' '.join(label.text() for label in w.campaign_cards[0].findChildren(type(w.title)))
    assert '<b>1</b> à traiter' in card_text and '1 dossier à traiter' in card_text
    assert w.campaign_cards[0].open_button.text() == 'Ouvrir'
    w.open_campaign(first)
    assert w.title.text() == 'Ancienne campagne'
    w.open_campaign(second)
    assert w.title.text() == 'Campagne testée' and w.campaign_tab_buttons[COLLECT_PAGE].badge.text() == '1 à relancer'
    w.close()


def test_results_page_scrolls_vertically_at_minimum_window_size(tmp_path):
    app = QApplication.instance() or QApplication([])
    w = MainWindow(Store(tmp_path / 'db.sqlite'), settings=MemorySettings({'ui/sidebar_collapsed': True}))
    w.resize(900, 620)
    w.navigate(2)
    w.show()
    app.processEvents()
    w.apply_responsive_layout()
    app.processEvents()
    assert w.results_card.height() >= 320, 'the dossier table keeps room for its rows; the page scrolls instead'
    w.close()


def test_a_long_error_is_shown_in_full_in_the_dossier_sheet(tmp_path):
    app = QApplication.instance() or QApplication([])
    store = Store(tmp_path / 'db.sqlite')
    batch = store.create_batch([2025], tmp_path / 'out', [Dossier('Alpha', '000000001', 'Léa')], 1)
    campaign = store.batch_campaigns(batch)[0]
    long_error = 'Le portail indique une erreur de lecture de cet avis. ' * 12
    store.dossier_result(campaign['id'], '000000001', 'ERREUR', long_error)
    w = MainWindow(store, settings=MemorySettings({'ui/sidebar_collapsed': True}))
    w.resize(900, 620)
    w.open_campaign(batch)
    w.show()
    app.processEvents()
    assert long_error in w.table.item(0, 0).toolTip()
    w.table.selectRow(0)
    assert w.result_detail_error.isReadOnly()
    assert w.result_detail_error.toPlainText() == long_error
    assert not w.result_retry_hint.isHidden(), 'without connected sessions the sheet says why it cannot retry'
    w.close()


def test_optional_portfolio_and_multiple_years_survive_reopen(tmp_path, qapp):
    s = Store(tmp_path / 'db.sqlite')
    w = MainWindow(s)
    w.output.setText(str(tmp_path / 'out'))
    w.years.setText('2023, 2024, 2025')
    w.create_campaign()
    b = w.selected_batch()
    assert s.batch(b)['source'] == 'ACCOUNT'
    assert [c['year'] for c in s.batch_campaigns(b)] == [2023, 2024, 2025]
    w.close()
    w = MainWindow(s)
    assert w.selected_batch() == b and w.years.text() == '2023, 2024, 2025'
    assert s.batch(b)['source'] == 'ACCOUNT' and w.campaign_flow.currentWidget() is w.campaign_library
    w.close()


def test_sessions_gate_launch_and_results_filter(tmp_path, qapp):
    s = Store(tmp_path / 'db.sqlite')
    b = s.create_batch(
        [2024, 2025], tmp_path / 'out', [Dossier('Alpha', '000000001', 'Léa'), Dossier('Beta', '000000002', 'Paul')], 2
    )
    w = MainWindow(s)
    assert w.table.rowCount() == 4 and not w.run_button.isEnabled()
    w.browser_opened()
    w.session_updated(0, 'Connectée')
    assert not w.run_button.isEnabled()
    for index in (1, 2, 3):
        w.session_updated(index, 'Connectée')
    assert w.run_button.isEnabled()
    w.search.setText('Alpha')
    assert w.table.rowCount() == 2
    w.search.clear()
    w.year_filter.setCurrentText('2025')
    assert w.table.rowCount() == 2
    c = s.batch_campaigns(b)[1]
    s.dossier_result(c['id'], '000000001', 'ERREUR', 'Avis à relancer')
    w.refresh_table()
    w.result_filter_buttons['TODO'].click()
    assert w.table.rowCount() == 1
    w.session_updated(1, 'À reconnecter')
    assert not w.run_button.isEnabled()
    w.close()


def test_single_year_selector_changes_after_reopen(tmp_path, qapp):
    s = Store(tmp_path / 'db.sqlite')
    s.create_batch([2025], tmp_path / 'out', [], 2)
    w = MainWindow(s)
    w.year.setValue(2024)
    w.create_campaign()
    assert [c['year'] for c in s.batch_campaigns(w.selected_batch())] == [2024]
    w.close()


def test_integrated_browser_tabs_and_table_corners(tmp_path, qapp):
    s = Store(tmp_path / 'db.sqlite')
    s.create_batch([2025], tmp_path / 'out', [Dossier('Alpha', '000000001', 'Léa')], 2)
    w = MainWindow(s)
    assert w.minimumWidth() == 900 and w.minimumHeight() == 620
    assert w.browser_tabs.count() == 4
    assert [w.browser_tabs.isTabVisible(i) for i in range(4)] == [True, True, True, True], (
        'four sessions, even without being shown'
    )
    assert w.browser_tabs.tabBar().isHidden()
    assert w.browser_shell.objectName() == 'browserShell'
    assert w.browser_toolbar.parent() is w.browser_shell
    assert w.browser_tabs.parent() is w.browser_shell
    assert len(w.browser_session_tabs) == 4
    assert w.browser_address.objectName() == 'browserAddress'
    assert w.browser_zoom_label.text().endswith('%')
    assert w.browser_shell.isHidden()
    assert len(w.browser_scrolls) == 4
    assert len(w.browser_canvases) == 4
    assert all(host.minimumWidth() == 0 for host in w.browser_hosts)
    assert all(
        scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded for scroll in w.browser_scrolls
    )
    assert w.results_card.objectName() == 'resultsCard'
    w.browser_window_ready(0, 123)
    assert w.browser_hosts[0].handle == 123 and not w.browser_tabs.isHidden()
    assert w.browser_workspace_active
    assert not w.browser_shell.isHidden()
    assert not w.rail.isHidden() and w.header_panel.isHidden()
    assert not w.browser_toolbar.isHidden()
    w.show_session(1)
    assert w.browser_tabs.currentIndex() == 1
    w.session_updated(1, 'Connectée')
    assert 'Connectée' in w.browser_session_tabs[1].text() and w.browser_session_tabs[1].isChecked()
    w.leave_browser_workspace()
    assert not w.browser_workspace_active
    assert not w.rail.isHidden() and not w.header_panel.isHidden()
    assert w.browser_tabs.isHidden()
    assert w.browser_shell.isHidden()
    w.close()


def test_session_status_resynchronizes_browser_after_portal_load(tmp_path, qapp):
    class Backend:
        def __init__(self):
            self.calls = []

        def get_parent(self, _h):
            return 0

        def get_style(self, _h):
            return 0

        def set_parent(self, *_args):
            pass

        def set_style(self, *_args):
            pass

        def move(self, *args):
            self.calls.append(args)

        def show(self, *_args):
            pass

        def release(self, *_args):
            pass

    s = Store(tmp_path / 'db.sqlite')
    s.create_batch([2025], tmp_path / 'out', [], 1)
    w = MainWindow(s)
    backend = Backend()
    host = w.browser_hosts[0]
    host.embedder = WindowEmbedder(backend)
    host.resize(1180, 700)
    host.attach(123)
    backend.calls.clear()
    w.session_updated(0, 'À connecter')
    assert backend.calls == [(123, -9, -88, 1198, 788)]
    host.handle = None
    w.close()


def test_foreground_poll_restores_browser_focus_once_per_return_to_the_app(tmp_path, qapp):
    store = Store(tmp_path / 'db.sqlite')
    store.create_batch([2025], tmp_path / 'out', [], 1)
    w = MainWindow(store, settings=MemorySettings())
    calls = []
    active = [True]
    host = w.browser_hosts[0]
    host.handle = 123
    host.application_is_foreground = lambda _root: active[0]
    host.focus_browser = lambda: calls.append(0)
    w.browser_tabs.setCurrentIndex(0)
    w.browser_workspace_active = True
    w.browser_was_foreground = False
    w.maintain_browser_focus()
    w.maintain_browser_focus()
    active[0] = False
    w.maintain_browser_focus()
    active[0] = True
    w.maintain_browser_focus()
    assert calls == [0, 0]
    host.handle = None
    w.close()


def test_browser_workspace_keeps_native_window_fitted_after_chromium_resets(tmp_path):
    class Backend:
        def __init__(self):
            self.calls = []

        def get_parent(self, _h):
            return 0

        def get_style(self, _h):
            return 0

        def set_parent(self, *_args):
            pass

        def set_style(self, *_args):
            pass

        def move(self, *args):
            self.calls.append(args)

        def show(self, *_args):
            pass

        def release(self, *_args):
            pass

    app = QApplication.instance() or QApplication([])
    s = Store(tmp_path / 'db.sqlite')
    s.create_batch([2025], tmp_path / 'out', [], 1)
    w = MainWindow(s)
    backend = Backend()
    host = w.browser_hosts[0]
    host.embedder = WindowEmbedder(backend)
    host.resize(1180, 700)
    host.attach(123)
    w.set_browser_workspace(True)
    app.processEvents()
    backend.calls.clear()
    QTest.qWait(650)
    viewport = w.browser_scrolls[0].viewport()
    expected = native_child_geometry(
        browser_canvas_width(viewport.width()),
        viewport.height(),
        host.top_crop,
        host.devicePixelRatioF(),
        host.frame_crop,
    )
    assert (123, *expected) in backend.calls
    host.handle = None
    w.close()


def test_browser_host_keeps_a_scrollable_canvas_after_window_shrinks(tmp_path):
    class Backend:
        def __init__(self):
            self.calls = []

        def get_parent(self, _h):
            return 0

        def get_style(self, _h):
            return 0

        def set_parent(self, *_args):
            pass

        def set_style(self, *_args):
            pass

        def move(self, *args):
            self.calls.append(args)

        def show(self, *_args):
            pass

        def release(self, *_args):
            pass

    app = QApplication.instance() or QApplication([])
    s = Store(tmp_path / 'db.sqlite')
    s.create_batch([2025], tmp_path / 'out', [], 1)
    w = MainWindow(s)
    backend = Backend()
    host = w.browser_hosts[0]
    host.embedder = WindowEmbedder(backend)
    host.attach(123)
    w.set_browser_workspace(True)
    w.resize(1060, 740)
    app.processEvents()
    viewport = w.browser_scrolls[0].viewport()
    host.resize(viewport.width(), viewport.height())
    w.sync_browser_hosts()
    assert host.width() == browser_canvas_width(viewport.width()) and host.height() == viewport.height()
    assert w.browser_canvases[0].width() == browser_canvas_extent(viewport.width())
    host.handle = None
    w.close()


def test_browser_zoom_fits_the_portal_to_the_available_width():
    assert responsive_browser_zoom(1280) == 1.0
    assert responsive_browser_zoom(1024) == 0.85
    assert responsive_browser_zoom(804) == 0.67
    assert responsive_browser_zoom(600) == 0.6


def test_browser_address_never_displays_session_tokens():
    shown = display_portal_url('https://cfspro.impots.gouv.fr/mire/accueil.do;jsessionid=SECRET?action=reset#part')
    assert shown == 'cfspro.impots.gouv.fr/mire/accueil.do'
    assert 'SECRET' not in shown


def test_browser_container_exposes_a_real_horizontal_scrollbar(tmp_path):
    app = QApplication.instance() or QApplication([])
    s = Store(tmp_path / 'db.sqlite')
    s.create_batch([2025], tmp_path / 'out', [], 1)
    w = MainWindow(s)
    host = w.browser_hosts[0]
    host.handle = 123
    host._sync = lambda: None
    w.resize(1060, 740)
    w.show()
    w.set_browser_workspace(True)
    app.processEvents()
    w.sync_browser_hosts()
    app.processEvents()
    bar = w.browser_scrolls[0].horizontalScrollBar()
    assert bar.maximum() > 0 and not bar.isHidden()
    assert w.browser_canvases[0].width() - host.width() == 32
    bar.setValue(bar.maximum())
    app.processEvents()
    assert bar.value() == bar.maximum() and w.browser_canvases[0].x() < 0
    host.handle = None
    w.close()


def test_a_new_campaign_is_made_in_two_steps_and_opens_on_its_collection(tmp_path, qapp):
    store = Store(tmp_path / 'db.sqlite')
    w = MainWindow(store, settings=MemorySettings())
    problems = []
    w.problem = problems.append
    w.navigate(0)
    w.new_campaign_button.click()
    csv = tmp_path / 'portefeuille.csv'
    csv.write_text(
        'Nom du dossier,SIREN,Collaborateur,E-mail\nAlpha,000000001,Léa,alpha@example.fr\nBeta,000000002,Léa,\n',
        encoding='utf-8',
    )
    w.load_portfolio(csv)
    w.show_wizard_step(2)
    assert [step.detail.text() for step in w.wizard_steps] == ['portefeuille.csv · 2 dossiers', 'En cours']
    assert not hasattr(w, 'wizard_count_buttons') and w.session_count.isHidden(), 'the number of sessions is not asked'
    w.output.setText(str(tmp_path / 'sortie'))
    assert 'Alpha_CFE_' in w.output_tree.text() and 'Léa' in w.output_tree.text()
    w.create_button.click()
    batch = w.selected_batch()
    assert (
        store.batch(batch)['sessions'] == 4
        and w.pages.currentIndex() == COLLECT_PAGE
        and w.campaign_tab_buttons[COLLECT_PAGE].isChecked()
    )
    w.close()


def test_the_account_dialog_says_what_is_missing_once_a_field_is_typed_in(qapp):
    dialog = AccountDialog()
    error = lambda key: dialog.errors[key][0]
    assert not dialog.save_button.isEnabled() and error('login').isHidden(), 'no error before the field is used'
    assert dialog.delete_button.isHidden()
    dialog.login_input.setText('cabinet')
    dialog.login_input.textEdited.emit('cabinet')
    assert not error('login').isHidden() and error('login').text() == 'Indiquez une adresse e-mail valide.'
    dialog.label_input.setText('Cabinet')
    dialog.login_input.setText('a@cabinet.fr')
    dialog.password.setText('secret')
    dialog.login_input.textEdited.emit('a@cabinet.fr')
    assert dialog.save_button.isEnabled() and error('login').isHidden()
    dialog.close()
    editing = AccountDialog({'label': 'Cabinet', 'login': 'a@cabinet.fr'}, refused=True)
    assert editing.save_button.isEnabled(), 'an empty password keeps the saved one'
    assert not editing.delete_button.isHidden()
    editing.delete_button.click()
    assert editing.result() == AccountDialog.DELETE
    editing.close()


def test_an_account_row_opens_the_account_and_deletes_it_from_there(tmp_path, qapp, monkeypatch):
    from PySide6.QtCore import QPoint

    store = Store(tmp_path / 'db.sqlite')
    manager = AccountManager(store, MemoryVault())
    account = manager.add('Cabinet A', 'a@example.fr', 'secret')
    w = MainWindow(store, settings=MemorySettings(), accounts=manager)
    w.navigate(4)
    w.show()
    qapp.processEvents()
    opened = []
    monkeypatch.setattr(AccountDialog, 'exec', lambda dialog: opened.append(dialog) or AccountDialog.DELETE)
    monkeypatch.setattr(w, 'confirm_account_deletion', lambda name: opened.append(name) or True)
    row = w.account_rows[0]
    QTest.mouseClick(row, Qt.MouseButton.LeftButton, pos=QPoint(10, row.height() // 2))
    assert isinstance(opened[0], AccountDialog) and opened[1] == 'Cabinet A'
    assert manager.list_accounts() == [] and not w.accounts_empty.isHidden()
    assert account not in [(manager.account_for_session(i) or {}).get('id') for i in range(4)]
    w.close()
