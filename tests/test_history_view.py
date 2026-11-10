import json
import sqlite3
from types import SimpleNamespace

import pytest

from card_history import CardHistory
from company_import import parse
from history_view import backup_history, export_history, query_history
from standalone_2gis.main import Api


def populate(history, count=3):
    for i in range(count):
        history.remember({'source': '2gis', 'source_id': str(i+1), 'name': 'МЯГКИЙ диван ' + str(i),
                          'city': 'Краснодар', 'sourceUrl': 'https://2gis.ru/firm/' + str(i+1),
                          'phones': ['+7 (918) 123-45-67'], 'websites': ['https://example.test'] if i % 2 else []})


def test_search_filters_counts_pagination_and_authoritative_identity(tmp_path):
    history = CardHistory(tmp_path / 'history.sqlite3')
    populate(history, 53)
    assert query_history(history, 'мягкий')['total'] == 53
    assert query_history(history, '91812345')['total'] == 53
    assert query_history(history, 'КРАСНОДАР', 'website')['total'] == 26
    assert query_history(history, status='qualified')['total'] == 27
    page = query_history(history, page=2)
    assert len(page['rows']) == 3 and page['pages'] == 2
    assert page['counts'] == {'all': 53, 'qualified': 27, 'website': 26}
    assert query_history(history, 'не существует')['total'] == 0
    with history.connection() as db:
        row = json.loads(db.execute("SELECT record_json FROM cards WHERE card_id='1'").fetchone()[0])
        row['source_id'] = '999'
        row['sourceUrl'] = 'https://2gis.ru/firm/999'
        db.execute("UPDATE cards SET record_json=? WHERE card_id='1'", (json.dumps(row),))
    saved = query_history(history, all_rows=True)['rows']
    assert next(r for r in saved if r['source_id'] == '1')['sourceUrl'] == 'https://2gis.ru/firm/1'


@pytest.mark.parametrize('format', ['html', 'csv', 'json'])
def test_exports_preserve_sites_and_filter_across_pages(tmp_path, format):
    history = CardHistory(tmp_path / 'history.sqlite3')
    populate(history, 53)
    rows = query_history(history, all_rows=True)['rows']
    rows[0]['name'] = '</script><img src=x onerror=alert(1)>'
    path = export_history(rows, tmp_path / ('cards.' + format), format)
    content = path.read_text(encoding='utf-8-sig')
    assert 'https://example.test' in content
    if format == 'json':
        assert len(json.loads(content)) == 53
    elif format == 'html':
        assert len(parse(content, 'cards.html')['records']) == 53
        assert '</script><img' not in content
    else:
        assert "'+7 (918)" in content
        assert 'С сайтом' in content


def test_backup_includes_wal_and_protects_live_database(tmp_path):
    history = CardHistory(tmp_path / 'history.sqlite3')
    populate(history)
    copy = backup_history(history, tmp_path / 'backup.sqlite3')
    with sqlite3.connect(copy) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert db.execute('SELECT COUNT(*) FROM cards').fetchone()[0] == 3
    for suffix in ('', '-wal', '-shm'):
        with pytest.raises(ValueError):
            backup_history(history, str(history.path) + suffix)
    assert history.count() == 3


def test_bridge_save_cancel_filter_and_backup(tmp_path):
    api = Api(tmp_path)
    populate(api.history, 53)
    api._window = SimpleNamespace(create_file_dialog=lambda *a, **kw: None)
    assert api.export_history()['cancelled']
    assert api.backup_history()['cancelled']
    path = tmp_path / 'filtered.json'
    api._window.create_file_dialog = lambda *a, **kw: [str(path)]
    result = api.export_history('', 'website', 'json')
    assert result['ok'] and result['count'] == 26
    assert len(json.loads(path.read_text(encoding='utf-8'))) == 26
    path = tmp_path / 'snapshot.sqlite3'
    assert api.backup_history()['ok']
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT COUNT(*) FROM cards').fetchone()[0] == 53
    api._window.create_file_dialog = lambda *a, **kw: [str(api.history.path)]
    assert not api.export_history()['ok']
    assert not api.backup_history()['ok']
    assert api.history.count() == 53
