import json
from pathlib import Path

from standalone_2gis.main import Api, classify, export_rows
from company_import import parse


def row(**changes):
    value = {'name': 'Химчистка диванов', 'city': 'Краснодар', 'address': 'Краснодар', 'phones': ['+79180000000'], 'sourceUrl': 'https://2gis.ru/firm/70000001000000000', 'rubrics': ['Химчистка мягкой мебели'], 'websites': [], 'card_check': {'scope': '2gis_card', 'complete': True, 'websites': [], 'has_website': False}}
    value.update(changes)
    return value


def test_filter_accepts_all_collected_cards_without_detected_site():
    assert classify(row()) == 'qualified'
    assert classify(row(websites=['https://example.com'])) == 'website'
    assert classify(row(card_check={'scope':'2gis_card', 'complete':False})) == 'qualified'
    assert classify(row(card_check={})) == 'qualified'
    assert classify(row(name='Уборка', rubrics=['Клининг'])) == 'qualified'
    assert classify(row(name='Многопрофильная компания', rubrics=['Чистка ковров', 'Мягкая мебель'])) == 'qualified'
    assert classify(row(card_check={'complete': False, 'has_website': True})) == 'website'
    assert classify(row(card_check={'websites': ['https://example.test']})) == 'website'


def test_state_and_export_include_previously_unverified_cards(tmp_path, monkeypatch):
    rows = [row(name='Уборка', rubrics=['Клининг']),
            row(name='Неполная карточка', phones=['+79180000001'], card_check={'complete':False}, sourceUrl='https://2gis.ru/firm/70000001000000001'),
            row(name='С сайтом', websites=['https://example.test'])]
    (tmp_path / 'collected.json').write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
    (tmp_path / 'worker.json').write_text('{"status":"done"}', encoding='utf-8')
    api = Api(tmp_path); api.run_dir = tmp_path
    state = api.state()
    assert state['counts'] == {'all':3, 'qualified':2, 'website':1, 'unverified':0}
    monkeypatch.setattr('standalone_2gis.main.os.startfile', lambda path: None)
    assert api.export()['ok']
    exported = json.loads((tmp_path / 'export/companies.json').read_text(encoding='utf-8'))
    assert [r['name'] for r in exported] == ['Уборка', 'Неполная карточка']
    html = (tmp_path / 'export/companies.html').read_text(encoding='utf-8')
    assert len(parse(html, 'companies.html')['records']) == 2


def test_exports_include_importable_lead_data(tmp_path):
    export_rows([row()], tmp_path)
    assert json.loads((tmp_path / 'companies.json').read_text(encoding='utf-8'))[0]['name'] == 'Химчистка диванов'
    html = (tmp_path / 'companies.html').read_text(encoding='utf-8')
    assert 'id="lead-data"' in html
    assert 'Химчистка диванов' in html
    assert parse(html, 'companies.html')['records'][0]['name'] == 'Химчистка диванов'
    assert '+79180000000' in (tmp_path / 'companies.csv').read_text(encoding='utf-8-sig')


def test_start_rejects_invalid_city_without_launching_process(tmp_path):
    api = Api(tmp_path)
    assert not api.start('Краснодар', '../../x', 50, True)['ok']
    assert api.process is None


def test_limit_can_be_unlimited_or_more_than_150(tmp_path, monkeypatch):
    monkeypatch.setattr('standalone_2gis.main.subprocess.Popen', lambda *a, **k: object())
    for value, expected in [('', None), ('250', 250)]:
        api = Api(tmp_path)
        assert api.start('Краснодар', 'krasnodar', value, True)['ok']
        config = json.loads((api.run_dir / 'config.json').read_text(encoding='utf-8'))
        assert config['pool'] == expected
