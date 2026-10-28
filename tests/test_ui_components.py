from pathlib import Path

from collecte_cfe.ui_components import (
    NavButton,
    asset_path,
    load_local_fonts,
)


def test_assets_are_local_and_packaged():
    for name in (
        'campaigns.svg',
        'settings.svg',
        'action-excel.svg',
        'close.svg',
        'toast-warning.svg',
    ):
        path = Path(asset_path(f'icons/{name}'))
        assert path.is_file()
        source = path.read_text(encoding='utf-8').lower()
        assert '<image' not in source
        assert '<script' not in source


def test_sidebar_icons_are_distinct_grey_line_icons():
    names = ('campaigns.svg', 'settings.svg')
    sources = [Path(asset_path(f'icons/{name}')).read_text(encoding='utf-8') for name in names]
    assert all('stroke="#59627A"' in source and 'fill="none"' in source for source in sources)
    assert len(set(sources)) == len(names)


def test_local_fonts_are_available_without_remote_resources(qapp):
    families = load_local_fonts()
    assert set(families) == {'title', 'body', 'mono'}
    assert all(families.values())
    assert 'Sora' in families['title']
    assert 'Inter' in families['body']
    assert 'IBM Plex Mono' in families['mono']
    font_root = Path(asset_path('fonts'))
    assert len(list(font_root.glob('*.ttf'))) >= 5
    assert len(list(font_root.glob('*.txt'))) >= 3


def test_navigation_button_shows_its_title_and_subtitle(qapp):
    nav = NavButton('Comptes', 'Sessions impots.gouv', 'campaigns.svg', lambda: None)
    assert nav.text() == 'Comptes\nSessions impots.gouv'
    assert nav.toolTip() == 'Comptes · Sessions impots.gouv'
    assert not nav.icon().isNull()
