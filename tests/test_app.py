import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import Qt

from collecte_cfe.app import MainWindow
from collecte_cfe.store import Store


def test_import_create_campaign_and_reopen(tmp_path, qapp):
    source = tmp_path / 'p.csv'
    source.write_text('Nom du dossier;SIREN;Collaborateur\nExemple;000000001;Léa\n', encoding='utf-8-sig')
    store = Store(tmp_path / 'db.sqlite')
    w = MainWindow(store)
    w.load_portfolio(source)
    w.year.setValue(2025)
    w.output.setText(str(tmp_path / 'sortie'))
    w.create_campaign()
    assert w.table.rowCount() == 1
    assert w.table.item(0, 0).data(Qt.ItemDataRole.UserRole + 1) == '000000001'
    w.close()
    w = MainWindow(Store(tmp_path / 'db.sqlite'))
    assert w.campaigns.count() == 1 and w.table.rowCount() == 1
    assert w.year.value() == 2025
    w.close()
