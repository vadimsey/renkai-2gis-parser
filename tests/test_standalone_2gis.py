import json
from pathlib import Path

from standalone_2gis.main import Api, classify, export_rows
from company_import import parse


def row(**changes):
    value = {'name': 'Химчистка диванов', 'city': 'Краснодар', 'address': 'Краснодар', 'phones': ['+79180000000'], 'sourceUrl': 'https://2gis.ru/firm/70000001000000000', 'rubrics': ['Химчистка мягкой мебели'], 'websites': [], 'card_check': {'scope': '2gis_card', 'complete': True, 'websites': [], 'has_website': False}}
    value.update(changes)
    return value


def test_filter_rejects_site_and_incomplete_cards():
    assert classify(row()) == 'qualified'
    assert classify(row(websites=['https://example.com'])) == 'website'
    assert classify(row(card_check={'scope':'2gis_card', 'complete':False})) == 'unverified'
    assert classify(row(name='Уборка', rubrics=['Клининг'])) == 'unverified'
    assert classify(row(name='Многопрофильная компания', rubrics=['Чистка ковров', 'Мягкая мебель'])) == 'unverified'


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
