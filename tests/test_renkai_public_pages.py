import json
from types import SimpleNamespace

import pytest

from eroloft_worker import AccessBlocked, Worker
from parser_2gis.parser import ParserOptions
from parser_2gis.parser import page_data
from standalone_2gis.main import classify


def html_state(state):
    literal = json.dumps(json.dumps(state, ensure_ascii=False), ensure_ascii=False)
    return '<html><script>var initialState = JSON.parse(' + literal + ');</script></html>'


def firm_snapshot(identity, name='ТЕСТ Химчистка диванов'):
    return {'url': 'https://2gis.ru/krasnodar/firm/' + identity,
            'profiles': {identity: {'data': {'id': identity, 'name': name,
                'contact_groups': [{'contacts': [{'type': 'phone', 'value': '+70000000001'}]}]},
                'meta': {'code': 200}}}}


def test_public_html_survives_later_third_party_login_redirect():
    url = 'https://2gis.ru/krasnodar/firm/70000000000001'
    snapshot = firm_snapshot('70000000000001', "ТЕСТ Чистка диванов 'Мягко'")
    html = html_state({'data': {'entity': {'profile': snapshot['profiles']}}})
    class Remote:
        def clear_requests(self): pass
        def navigate(self, target, **kwargs): assert target == url
        def get_responses(self, **kwargs):
            return [{'url': url, 'mimeType': 'text/html', 'status': 200},
                    {'url': 'https://id.sber.ru/authorize', 'mimeType': 'text/html', 'status': 200}]
        def get_response_body(self, response, **kwargs):
            assert response['url'] == url
            return html
        def execute_script(self, *args): raise AssertionError('The browser has already left the 2GIS document')
    parser = SimpleNamespace(_chrome_remote=Remote())
    loaded = page_data.load_page(parser, url)
    row = page_data.extract_firm(loaded, '70000000000001')
    assert row['name'] == "ТЕСТ Чистка диванов 'Мягко'"
    assert row['renkai_page_url'] == url
    assert page_data.extract_firm(loaded, '70000000000002') is None


@pytest.mark.parametrize('status', [403, 429])
def test_access_limit_stops_without_repeating_source(tmp_path, status):
    url = 'https://2gis.ru/krasnodar/search/test'
    class Remote:
        navigations = 0
        def clear_requests(self): pass
        def navigate(self, *args, **kwargs): self.navigations += 1
        def get_responses(self, **kwargs): return [{'url': url, 'mimeType': 'text/html', 'status': status}]
        def execute_script(self, *args): return ''
    remote = Remote()
    worker = Worker({'city': 'Краснодар', 'browser_visible': False}, tmp_path)
    parser = SimpleNamespace(_chrome_remote=remote, renkai_access_check=worker.check_access)
    with pytest.raises(AccessBlocked): page_data.load_page(parser, url)
    assert remote.navigations == 1


def test_failed_card_is_skipped_and_successful_next_card_is_written(monkeypatch):
    def load(parser, url):
        if '/search/' in url:
            return {'searches': [{'page': 1, 'pages': 1, 'ids': ['1', '2'], 'meta': {'code': 200}}]}
        if url.endswith('/1'): raise page_data.PageReadError('TEST missing card')
        return firm_snapshot('2')
    monkeypatch.setattr(page_data, 'load_page', load)
    parser = SimpleNamespace(_url='https://2gis.ru/krasnodar/search/test', _options=ParserOptions(max_records=100))
    written = []
    page_data.parse_page_data(parser, SimpleNamespace(write=written.append))
    assert [d['result']['items'][0]['id'] for d in written] == ['2']


def test_new_transport_has_no_fifty_card_limit(monkeypatch):
    ids = [str(i) for i in range(1, 61)]
    def load(parser, url):
        if '/search/' in url:
            return {'searches': [{'page': 1, 'pages': 1, 'ids': ids, 'meta': {'code': 200}}]}
        return firm_snapshot(url.rsplit('/', 1)[1])
    monkeypatch.setattr(page_data, 'load_page', load)
    parser = SimpleNamespace(_url='https://2gis.ru/krasnodar/search/test', _options=ParserOptions(max_records=100))
    written = []
    page_data.parse_page_data(parser, SimpleNamespace(write=written.append))
    assert len(written) == 60


def test_price_evidence_belongs_to_the_same_firm_only():
    snapshot = {'url': 'https://2gis.ru/krasnodar/firm/1/tab/prices', 'market': {
        'products': {'p': {'meta': {'code': 200}, 'data': {'product': {'name': 'Химчистка дивана'}}}},
        'offers': {'p:2': {'meta': {'code': 200}, 'data': {'productId': 'p', 'branchId': '2'}}}}}
    assert page_data.price_services(snapshot, '1') == []
    snapshot['market']['offers']['p:2']['data']['branchId'] = '1'
    evidence = page_data.price_services(snapshot, '1')
    assert evidence == [{'text': 'Химчистка дивана', 'url': snapshot['url'], 'product_id': 'p'}]
    row = {'name': 'ТЕСТ Клининг', 'rubrics': ['Клининг'], 'service_evidence': evidence,
           'card_check': {'scope': '2gis_card', 'complete': True}}
    assert classify(row) == 'qualified'


@pytest.mark.parametrize('html', ['<html>Loading</html>', '<script>initialState=JSON.parse("[]")</script>',
                                '<script>initialState=JSON.parse("bad")</script>'])
def test_missing_or_malformed_data_is_not_a_complete_card(html):
    assert page_data.snapshot_from_html(html, 'https://2gis.ru/krasnodar/search/test') is None


def test_messenger_link_is_not_a_company_website():
    from hybrid_core import from_2gis
    base = {'id': '70000000000001', 'name': 'ТЕСТ Химчистка диванов'}
    social = from_2gis({**base, 'contact_groups': [{'contacts': [
        {'type': 'website', 'value': 'https://max.ru/testcompany'}]}]}, 'Краснодар')
    assert classify(social) == 'qualified'
    real_site = from_2gis({**base, 'contact_groups': [{'contacts': [
        {'type': 'website', 'value': 'https://example.test/t.me/company'}]}]}, 'Краснодар')
    assert classify(real_site) == 'website'
