from PySide6.QtWidgets import QHeaderView

from collecte_cfe.app import MainWindow
from collecte_cfe.domain import Dossier
from collecte_cfe.store import Store
from collecte_cfe.ui_collect import COLLECT_PAGE
from tests.test_dashboard import MemorySettings


def campaign_store(tmp_path):
    store = Store(tmp_path / 'shell.sqlite')
    first = store.create_batch(
        [2024, 2025],
        tmp_path / 'first',
        [Dossier('Atelier Alpha', '000000001', 'Camille')],
        2,
        'CFE Alpha',
    )
    second = store.create_batch(
        [2025],
        tmp_path / 'second',
        [Dossier('Maison Beta', '000000002', 'Alex')],
        1,
        'CFE Beta',
    )
    return store, first, second


def test_every_campaign_is_an_entry_of_the_sidebar(tmp_path, qapp):
    store, first, second = campaign_store(tmp_path)
    window = MainWindow(store, settings=MemorySettings({'ui/sidebar_collapsed': False}))
    assert sorted(window.campaign_nav_items) == sorted([first, second])
    assert window.campaign_nav_items[first].name_label.text() == 'CFE Alpha'
    window.campaign_nav_items[first].click()
    assert window.selected_batch() == first and window.pages.currentIndex() == COLLECT_PAGE
    assert window.title.text() == 'CFE Alpha'
    assert window.campaign_nav_items[first].isChecked() and not window.campaign_nav_items[second].isChecked()
    window.campaign_nav_items[second].click()
    assert window.selected_batch() == second and window.title.text() == 'CFE Beta'
    assert window.campaign_nav_items[second].isChecked() and not window.campaign_nav_items[first].isChecked()
    window.close()


def test_the_campaign_entry_says_what_is_left_to_do(tmp_path, qapp):
    store, first, second = campaign_store(tmp_path)
    campaigns = store.batch_campaigns(first)
    store.dossier_result(campaigns[0]['id'], '000000001', 'TERMINE')
    store.dossier_result(campaigns[1]['id'], '000000001', 'ERREUR', 'Délai dépassé')
    window = MainWindow(store, settings=MemorySettings({'ui/sidebar_collapsed': False}))
    item = window.campaign_nav_items[first]
    assert item.badge.text() == '1', 'the badge counts dossiers, whatever their state'
    assert '2 à traiter' in item.toolTip(), 'one error and one dossier without a classified notice, in the tooltip'
    assert window.campaign_nav_items[second].badge.text() == '1'
    assert 'à lancer' in window.campaign_nav_items[second].toolTip()
    window.open_campaign(first)
    assert window.campaign_tab_buttons[COLLECT_PAGE].badge.text() == '1 à relancer'
    assert window.campaign_tab_buttons[COLLECT_PAGE].badge.property('tone') == 'danger'
    assert window.campaign_tab_buttons[2].badge.text() == '1', 'the Dossiers tab counts the dossiers of the campaign'
    assert item.progress.value() == item.progress.maximum() == 2, (
        'the sidebar entry shows that both years were collected'
    )
    window.close()


def test_pages_of_a_campaign_are_tabs_under_its_header(tmp_path, qapp):
    store, first, _ = campaign_store(tmp_path)
    window = MainWindow(store, settings=MemorySettings())
    window.open_campaign(first)
    assert len(window.nav) == 2 and [button.title for button in window.nav] == ['Toutes les campagnes', 'Réglages']
    assert not window.campaign_back.isHidden() and window.campaign_back.text() == 'Campagnes'
    assert not window.campaign_tabs.isHidden() and window.campaign_tab_buttons[2].isChecked()
    window.campaign_tab_buttons[3].click()
    assert window.pages.currentIndex() == 3 and window.title.text() == 'CFE Alpha'
    window.campaign_tab_buttons[COLLECT_PAGE].click()
    assert window.pages.currentIndex() == COLLECT_PAGE and window.campaign_tab_buttons[COLLECT_PAGE].isChecked()
    assert window.campaign_nav_items[first].isChecked() and not window.nav[0].isChecked()
    window.browser_hosts[0].handle = 123
    window.set_browser_workspace(True)
    assert window.campaign_back.isHidden() and window.campaign_tabs.isHidden()
    window.browser_hosts[0].handle = None
    window.leave_browser_workspace()
    assert window.pages.currentIndex() == COLLECT_PAGE and not window.campaign_tabs.isHidden()
    window.campaign_back.click()
    assert window.pages.currentIndex() == 0 and window.campaign_flow.currentWidget() is window.campaign_library
    assert window.nav[0].isChecked() and window.campaign_back.isHidden() and window.campaign_tabs.isHidden()
    window.nav[1].click()
    assert window.pages.currentIndex() == 4 and window.nav[1].isChecked() and window.title.text() == 'Réglages'
    assert window.campaign_tabs.isHidden() and not window.campaign_nav_items[first].isChecked()
    window.close()


def test_the_dossier_sheet_is_a_column_beside_the_table_and_closes(tmp_path, qapp):
    store, first, _ = campaign_store(tmp_path)
    window = MainWindow(store, settings=MemorySettings({'ui/sidebar_collapsed': False}))
    window.resize(1440, 900)
    window.open_campaign(first)
    window.show()
    qapp.processEvents()
    assert window.rail.width() == 232
    assert window.table.horizontalHeader().sectionResizeMode(0) == QHeaderView.ResizeMode.Stretch
    assert window.result_detail_card.isHidden()
    window.table.selectRow(0)
    qapp.processEvents()
    drawer = window.result_detail_card
    assert not drawer.isHidden() and drawer.geometry().left() > window.results_card.geometry().right()
    assert drawer.geometry().right() <= window.results_page.rect().right()
    window.navigate(4)
    assert not drawer.isVisible(), 'the sheet belongs to the Dossiers tab'
    window.open_campaign(first)
    window.table.selectRow(0)
    window.drawer_close_button.click()
    assert drawer.isHidden() and window.result_detail_key is None and not window.table.selectionModel().selectedRows()
    window.resize(900, 620)
    window.apply_responsive_layout()
    qapp.processEvents()
    assert window.rail.width() == 232
    assert window.header_actions.geometry().right() <= window.header_panel.rect().right()
    assert window.table.geometry().right() <= window.results_card.rect().right()
    window.close()


def test_the_status_bar_only_shows_during_a_collection(tmp_path, qapp, monkeypatch):
    store, first, _ = campaign_store(tmp_path)
    window = MainWindow(store, settings=MemorySettings())
    window.open_campaign(first)
    announced = []
    monkeypatch.setattr(window.toast, 'announce', lambda text, *_args, **_kwargs: announced.append(text))
    assert window.activity.isHidden()
    window.say('Compte expert mis à jour.')
    assert announced == ['Compte expert mis à jour.']
    window.collection_state(True)
    assert not window.activity.isHidden()
    window.navigate(COLLECT_PAGE)
    assert window.campaign_tab_buttons[COLLECT_PAGE].badge.text() == 'en cours' and not window.pause_button.isHidden()
    window.navigate(2)
    assert window.campaign_nav_items[first].badge.text() == 'en cours'
    announced.clear()
    window.say('Pause demandée.')
    assert announced == [] and window.status.text() == 'Pause demandée.'
    monkeypatch.setattr(window, 'notify_collection_result', lambda batch: None)
    window.collection_state(False)
    assert window.activity.isHidden() and window.pause_button.isHidden()
    window.close()


def test_the_window_shows_a_neutral_name_and_icon(tmp_path, qapp):
    from collecte_cfe import APP_NAME

    store, first, _ = campaign_store(tmp_path)
    window = MainWindow(store, settings=MemorySettings({'ui/sidebar_collapsed': False}))
    assert APP_NAME == 'Collecte CFE' and window.windowTitle() == APP_NAME
    assert window.brand_name.text() == 'Collecte CFE'
    assert not window.brand.pixmap().isNull() and window.brand.text() == ''
    assert not window.windowIcon().isNull()
    window.close()


def test_a_campaign_is_worked_through_collecte_then_dossiers_then_courriels(tmp_path, qapp):
    store, first, second = campaign_store(tmp_path)
    window = MainWindow(store, settings=MemorySettings())
    assert [(tab.step_label.text(), tab.title_label.text()) for tab in window.campaign_tab_buttons.values()] == [
        ('1', 'Collecte'),
        ('2', 'Dossiers'),
        ('3', 'Courriels'),
    ]
    window.campaign_nav_items[second].click()
    assert window.pages.currentIndex() == COLLECT_PAGE, 'a campaign not collected yet opens on its collection'
    assert window.next_step_button.isHidden()
    window.navigate(2)
    assert not window.next_step_button.isHidden() and window.next_step_button.text() == 'Lancer la collecte'
    assert window.export_button.isHidden(), 'nothing to export before the collection'
    window.next_step_button.click()
    assert window.pages.currentIndex() == COLLECT_PAGE
    campaign = store.batch_campaigns(second)[0]['id']
    store.dossier_result(campaign, '000000002', 'TERMINE')
    window.navigate(0)
    window.campaign_nav_items[second].click()
    assert window.pages.currentIndex() == 2, 'once collected, the campaign opens on its dossiers'
    window.navigate(COLLECT_PAGE)
    assert not window.next_step_button.isHidden() and window.next_step_button.objectName() == 'primary'
    assert window.login_button.isHidden(), 'nothing is left to collect'
    window.next_step_button.click()
    assert window.pages.currentIndex() == 2 and not window.export_button.isHidden()
    window.close()


def test_an_open_campaign_can_be_renamed_or_deleted_from_its_header(tmp_path, qapp, monkeypatch):
    store, first, second = campaign_store(tmp_path)
    window = MainWindow(store, settings=MemorySettings())
    window.open_campaign(first)
    assert not window.campaign_menu_button.isHidden()
    assert [action.text() for action in window.campaign_menu_button.menu().actions() if action.text()] == [
        'Archiver dans un Excel',
        'Renommer',
        'Ouvrir le dossier de sortie',
        'Supprimer la campagne',
    ]
    monkeypatch.setattr(window, 'confirm_campaign_deletion', lambda name: name == 'CFE Alpha')
    window.campaign_menu_button.menu().actions()[-1].trigger()
    assert [batch['id'] for batch in store.batches()] == [second] and window.pages.currentIndex() == 0
    assert window.campaign_menu_button.isHidden(), 'no campaign menu on the list of campaigns'
    window.close()
