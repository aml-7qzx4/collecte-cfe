import pytest

from collecte_cfe.portal import PortalError, is_tax_url, parse_notice_page


def test_empty_dom_is_not_confirmed_absence():
    with pytest.raises(PortalError):
        parse_notice_page([])


def test_explicit_empty_result_and_valid_rows():
    assert parse_notice_page([['Aucun avis disponible']]) == []
    rows = [
        [
            '2026',
            '2025',
            '33',
            'BORDEAUX',
            '00021',
            "Avis d'imposition",
            '123',
            'Rôle général',
            '31/10/2026',
            '15/12/2026',
            '100,00',
        ]
    ]
    assert parse_notice_page(rows)[0].year == 2025
    with pytest.raises(PortalError):
        parse_notice_page([['Session expirée']])


@pytest.mark.parametrize(
    'url,accepted',
    [
        ('https://cfspro.impots.gouv.fr/document.pdf', True),
        ('https://impots.gouv.fr/document.pdf', True),
        ('http://impots.gouv.fr/document.pdf', False),
        ('https://impots.gouv.fr.example.org/document.pdf', False),
        ('https://example.org/document.pdf', False),
    ],
)
def test_direct_download_destination_is_dgfip_only(url, accepted):
    assert is_tax_url(url) == accepted
