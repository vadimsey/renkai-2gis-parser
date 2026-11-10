"""Exercise the actual RENKAI parser loop with offline 2GIS transport fixtures."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from eroloft_worker import Worker
from parser_2gis.cities import furniture_search_url, resolve_city, russian_cities
from parser_2gis.parser.parsers.main import MainParser
from standalone_2gis.main import Api, export_rows


@pytest.mark.parametrize('name,code', [(c['name'], c['code']) for c in russian_cities()])
def test_entire_upstream_city_catalog_is_accepted(name, code):
    assert resolve_city(name, code)['code'] == code


def test_backend_corrects_stale_city_code_and_supports_underscores(tmp_path, monkeypatch):
    monkeypatch.setattr('standalone_2gis.main.subprocess.Popen', lambda *a, **kw: object())
    api = Api(tmp_path)
    result = api.start('Нижний Новгород', 'krasnodar', '', True)
    assert result['ok']
    assert result['city_code'] == 'n_novgorod'
    config = json.loads((api.run_dir / 'config.json').read_text(encoding='utf-8'))
    assert config['city'] == 'Нижний Новгород'
    assert config['city_code'] == 'n_novgorod'
    assert '/n_novgorod/search/' in furniture_search_url(resolve_city(config['city'], config['city_code']))


def test_unknown_city_does_not_search_old_city(tmp_path):
    api = Api(tmp_path)
    assert not api.start('Несуществующий тестовый город', 'krasnodar', '', True)['ok']
    assert api.process is None and api.run_dir is None


def make_parser(options, docs):
    class FixtureChrome:
        page = 1
        serial = 0
        current = 0
        closed = False
        def __init__(self): self.clicked = []
        def navigate(self, *args, **kw): pass
        def get_responses(self, **kw): return [{'mimeType': 'text/html', 'status': 200}]
        def snapshot(self, **kw):
            self.serial += 1
            indices = [0, 0, 1] if self.page == 1 else [1, 2, 3]
            return [SimpleNamespace(attributes={'href': '/n_novgorod/firm/' + docs[i]['result']['items'][0]['id']},
                                    serial=self.serial, index=i) for i in indices]
        def perform_click(self, node):
            assert node.serial == self.serial, 'Parser used a stale DOM node'
            self.current = node.index
            self.clicked.append(node.index)
        def wait_response(self, *args, **kw): return {'status': 200}
        def get_response_body(self, *args, **kw): return json.dumps(docs[self.current])
        def clear_requests(self): pass
        def wait(self, *args): pass
        def stop(self): self.closed = True
        def execute_script(self, expression): return ''
    parser = MainParser.__new__(MainParser)
    remote = FixtureChrome()
    parser._chrome_remote = remote
    parser._options = options
    parser._url = furniture_search_url(resolve_city('Нижний Новгород'))
    parser._item_response_pattern = r'https://catalog\.api\.2gis.[^/]+/.*/items/byid'
    parser.renkai_access_check = parser.renkai_cancelled = parser.renkai_progress = None
    parser._get_links = remote.snapshot
    parser._wait_requests_finished = lambda **kw: True
    def go_page(page):
        remote.page = page
        return page if page <= 2 else None
    parser._go_page = go_page
    return parser, remote


def test_real_engine_worker_filter_and_export(tmp_path, monkeypatch):
    docs = []
    for i, name, contacts in [
        (0, 'ТЕСТ Химчистка диванов', [{'type': 'phone', 'value': '+70000000001'}]),
        (1, 'ТЕСТ Химчистка мебели с сайтом', [{'type': 'phone', 'value': '+70000000002'}, {'type': 'website', 'value': 'https://example.test'}]),
        (2, 'ТЕСТ Уборка офисов', [{'type': 'phone', 'value': '+70000000003'}]),
        (3, 'ТЕСТ Химчистка матрасов', []),
    ]:
        docs.append({'meta': {'code': 200}, 'result': {'items': [{
            'id': str(70000000000000 + i), 'name': name,
            'contact_groups': [{'contacts': contacts}],
            'rubrics': [{'name': 'Химчистка мебели' if i != 2 else 'Уборка офисов'}],
        }]}})
    captured = {}
    def factory(url, chrome, options):
        assert '/n_novgorod/search/' in url
        parser, remote = make_page_parser(options, docs, url)
        captured.update(parser=parser, remote=remote)
        return parser
    monkeypatch.setattr('parser_2gis.parser.get_parser', factory)
    monkeypatch.setattr('site_audit_browser.chrome_path', lambda: 'C:/test-chrome.exe')
    worker = Worker({'city': 'Нижний Новгород', 'city_code': 'krasnodar', 'pool': None, 'history_path':str(tmp_path / 'history.sqlite3')}, tmp_path)
    assert worker.run() == 0
    assert captured['remote'].clicked == [0, 1, 2, 3]
    assert captured['remote'].closed
    api = Api(tmp_path)
    api.run_dir = tmp_path
    state = api.state()
    assert state['status'] == 'done'
    assert state['counts'] == {'all': 4, 'qualified': 3, 'website': 1, 'unverified': 0}
    assert state['rows'][0]['city'] == 'Нижний Новгород'
    export_rows(state['rows'], tmp_path / 'export')
    rows = json.loads((tmp_path / 'export/companies.json').read_text(encoding='utf-8'))
    assert len(rows) == 3 and rows[0]['name'] == 'ТЕСТ Химчистка диванов'
    assert all(not r['websites'] for r in rows)
    # A fresh worker shares the DB and must skip even the excluded website card.
    again = tmp_path / 'again'; again.mkdir()
    next_worker = Worker({**worker.config, 'pool':None}, again)
    assert next_worker.run() == 0
    assert captured['remote'].clicked == []
    repeated = json.loads((again / 'worker.json').read_text(encoding='utf-8'))
    assert repeated['status'] == 'done'
    assert repeated['candidates'] == 0
    assert repeated['sources']['2gis']['history_skipped'] == 4
    assert next_worker.history.count() == 4


def test_real_engine_document_error_is_reported(tmp_path, monkeypatch):
    docs = [{'result': {'items': [{'id': str(70000000000000 + i)}]}} for i in range(4)]
    def factory(url, chrome, options):
        parser, remote = make_page_parser(options, docs, url)
        def fail(*args, **kwargs): raise RuntimeError('net::ERR_CONNECTION_CLOSED')
        remote.navigate = fail
        return parser
    monkeypatch.setattr('parser_2gis.parser.get_parser', factory)
    monkeypatch.setattr('site_audit_browser.chrome_path', lambda: 'C:/test-chrome.exe')
    worker = Worker({'city': 'Краснодар', 'city_code': 'krasnodar', 'pool': 1, 'history_path':str(tmp_path / 'history.sqlite3')}, tmp_path)
    assert worker.run() == 1
    state = json.loads((tmp_path / 'worker.json').read_text(encoding='utf-8'))
    assert state['status'] == 'error'
    assert 'ERR_CONNECTION_CLOSED' in state['sources']['2gis']['message']


def make_page_parser(options, docs, url):
    from parser_2gis.parser.page_data import SNAPSHOT
    class PageChrome:
        current_url = ''
        closed = False
        def __init__(self): self.clicked = []
        def navigate(self, target, **kwargs): self.current_url = target
        def get_responses(self, **kwargs):
            return [{'mimeType': 'text/html', 'status': 200, 'url': self.current_url}]
        def execute_script(self, expression, **kwargs):
            if expression != SNAPSHOT: return ''
            snapshot = {'url': self.current_url, 'ready': 'complete', 'text': '', 'profiles': {}, 'searches': []}
            if '/firm/' in self.current_url:
                firm_id = self.current_url.split('/firm/')[1]
                for i, doc in enumerate(docs):
                    row = doc['result']['items'][0]
                    if row['id'] == firm_id:
                        self.clicked.append(i)
                        snapshot['profiles'][firm_id] = {'data': row, 'meta': {'code': 200}}
            else:
                page = 2 if self.current_url.endswith('/page/2') else 1
                indices = [0, 0, 1] if page == 1 else [1, 2, 3]
                snapshot['searches'] = [{'page': page, 'pages': 2, 'meta': {'code': 200},
                    'ids': [docs[i]['result']['items'][0]['id'] for i in indices]}]
            return json.dumps(snapshot)
        def clear_requests(self): pass
        def stop(self): self.closed = True
        def wait_response(self, *args, **kwargs):
            raise AssertionError('Public-page parser must not call the broken catalog XHR')
    parser = MainParser.__new__(MainParser)
    remote = PageChrome()
    parser._chrome_remote = remote
    parser._options = options.model_copy(update={'delay_between_clicks': 0})
    parser._url = url
    return parser, remote
