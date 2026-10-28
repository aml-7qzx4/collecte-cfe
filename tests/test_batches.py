import threading

import pytest

from collecte_cfe.collector import Collector
from collecte_cfe.domain import Dossier
from collecte_cfe.portfolio import import_portfolio
from collecte_cfe.store import Store
from tests.test_collector import FakePortal


def test_batch_session_count_can_be_updated_between_one_and_four(tmp_path):
    store = Store(tmp_path / 'db.sqlite')
    batch = store.create_batch([2025], tmp_path / 'out', [], 2)
    store.update_batch_sessions(batch, 4)
    assert store.batch(batch)['sessions'] == 4
    with pytest.raises(ValueError):
        store.update_batch_sessions(batch, 0)


def test_aliases_for_the_three_portfolio_columns(tmp_path):
    p = tmp_path / 'p.csv'
    p.write_text('Nom du dossier;siren;nom du collab\nExemple;000000001;Léa\n', encoding='utf-8-sig')
    assert import_portfolio(p) == [Dossier('Exemple', '000000001', 'Léa')]


def test_client_email_persists_for_every_year_and_can_be_corrected(tmp_path):
    store = Store(tmp_path / 'db.sqlite')
    batch = store.create_batch(
        [2024, 2025], tmp_path / 'out', [Dossier('Exemple', '000000001', 'Léa', 'client@example.fr')], 1
    )
    campaigns = store.batch_campaigns(batch)
    assert [store.dossiers(c['id'])[0]['email'] for c in campaigns] == ['client@example.fr'] * 2
    store.update_client_email(batch, '000000001', 'nouveau@example.fr')
    reopened = Store(tmp_path / 'db.sqlite')
    assert [reopened.dossiers(c['id'])[0]['email'] for c in campaigns] == ['nouveau@example.fr'] * 2


def test_batch_groups_years_and_preserves_annual_resume(tmp_path):
    s = Store(tmp_path / 'db.sqlite')
    b = s.create_batch([2025, 2024, 2025], tmp_path / 'out', [Dossier('Exemple', '000000001', 'Léa')], 2)
    annual = s.batch_campaigns(b)
    assert [c['year'] for c in annual] == [2024, 2025]
    assert s.batch(b)['sessions'] == 2
    for c in annual:
        assert len(s.dossiers(c['id'])) == 1
    s2 = Store(tmp_path / 'db.sqlite')
    assert s2.batch(b)['source'] == 'FILE'
    assert len(s2.batch_campaigns(b)) == 2


def test_discovery_is_atomic_and_applies_to_every_year(tmp_path):
    s = Store(tmp_path / 'db.sqlite')
    b = s.create_batch([2024, 2025], tmp_path / 'out', [], 2)
    assert s.batch(b)['source'] == 'ACCOUNT' and not s.batch(b)['discovered']
    dossiers = [Dossier('SAS Exemple', '000000001', 'À affecter'), Dossier('SARL Atelier', '000000002', 'À affecter')]
    s.populate_batch(b, dossiers)
    assert s.batch(b)['discovered']
    assert all(len(s.dossiers(c['id'])) == 2 for c in s.batch_campaigns(b))
    with pytest.raises(ValueError):
        s.populate_batch(b, dossiers)
    assert all(len(s.dossiers(c['id'])) == 2 for c in s.batch_campaigns(b))


@pytest.mark.parametrize('years,sessions', [([], 2), ([2015], 2), ([2025], 0)])
def test_invalid_batch_rejected_without_partial_rows(tmp_path, years, sessions):
    s = Store(tmp_path / 'db.sqlite')
    with pytest.raises(ValueError):
        s.create_batch(years, tmp_path / 'out', [], sessions)
    assert not s.batches() and not s.campaigns()


def test_batch_accepts_a_custom_name_and_generates_a_default_name(tmp_path):
    store = Store(tmp_path / 'db.sqlite')
    custom = store.create_batch([2025], tmp_path / 'custom', [], 2, name='  CFE clients prioritaires  ')
    automatic = store.create_batch([2024], tmp_path / 'automatic', [], 1)
    assert store.batch(custom)['name'] == 'CFE clients prioritaires'
    assert store.batch(automatic)['name'] == f'Campagne {automatic}'


def test_existing_database_batches_receive_default_names(tmp_path):
    import sqlite3

    database = tmp_path / 'legacy.sqlite'
    connection = sqlite3.connect(database)
    connection.executescript("""
        CREATE TABLE batches (id INTEGER PRIMARY KEY, created TEXT NOT NULL, source TEXT NOT NULL,
            sessions INTEGER NOT NULL, discovered INTEGER NOT NULL DEFAULT 0);
        INSERT INTO batches(id,created,source,sessions,discovered)
        VALUES (7,'2026-09-01T12:00:00','ACCOUNT',2,0);
    """)
    connection.commit()
    connection.close()
    store = Store(database)
    assert store.batch(7)['name'] == 'Campagne 7'


def test_batch_can_be_renamed_and_deleted_without_touching_output_files(tmp_path):
    store = Store(tmp_path / 'db.sqlite')
    output = tmp_path / 'out'
    output.mkdir()
    kept = output / 'avis.pdf'
    kept.write_bytes(b'%PDF kept')
    batch = store.create_batch([2024, 2025], output, [Dossier('Alpha', '000000001', 'Léa')], 2)
    store.rename_batch(batch, '  CFE fin d’année  ')
    assert store.batch(batch)['name'] == 'CFE fin d’année'
    store.delete_batch(batch)
    assert store.batches() == [] and store.campaigns() == []
    assert kept.exists()


def test_collection_timings_are_stored_with_session_and_notice_details(tmp_path):
    from collecte_cfe.domain import Notice

    store = Store(tmp_path / 'db.sqlite')
    batch = store.create_batch([2025], tmp_path / 'out', [Dossier('Alpha', '000000001', 'Léa')], 2)
    campaign = store.batch_campaigns(batch)[0]['id']
    notice = Notice(2025, 'REF-1', '00021', '33', 'Bordeaux', 'Rôle général', '')
    store.record_timing(batch, campaign, '000000001', 2, 'AVIS', 'TERMINE', 3.125, notice=notice)
    rows = store.timings(batch)
    assert len(rows) == 1
    assert rows[0]['session'] == 2 and rows[0]['dossier'] == 'Alpha'
    assert rows[0]['reference'] == 'REF-1' and rows[0]['kind'] == 'SOLDE'
    assert rows[0]['seconds'] == pytest.approx(3.125)
    store.delete_batch(batch)
    assert store.timings(batch) == []


def test_batch_rename_rejects_an_empty_name(tmp_path):
    store = Store(tmp_path / 'db.sqlite')
    batch = store.create_batch([2025], tmp_path / 'out', [], 1)
    with pytest.raises(ValueError, match='nom'):
        store.rename_batch(batch, '   ')


def test_subset_collection_does_not_finish_or_export_other_jobs(tmp_path):
    s = Store(tmp_path / 'db.sqlite')
    c = s.create_campaign(2025, tmp_path / 'out', [Dossier('A', '000000001', 'Léa'), Dossier('B', '000000002', 'Léa')])
    rows = s.dossiers(c)
    p = FakePortal()
    outcome = Collector(s, p).run(c, threading.Event(), lambda _: None, dossiers=[rows[0]], finalize=False)
    assert outcome == 'OK'
    assert s.dossiers(c)[0]['status'] == 'TERMINE' and s.dossiers(c)[1]['status'] == 'EN_ATTENTE'
    assert s.campaign(c)['status'] == 'EN_ATTENTE'
    assert not list((tmp_path / 'out').rglob('*.xlsx'))
