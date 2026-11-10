import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from card_history import CardHistory, card_id
from parser_2gis.parser import ParserOptions
from parser_2gis.parser import page_data


def record(identity='1', **changes):
    row = {'source':'2gis', 'source_id':identity, 'name':'ТЕСТ Химчистка',
           'city':'Краснодар', 'sourceUrl':'https://2gis.ru/krasnodar/firm/' + identity,
           'phones':['+70000000001'], 'websites':[]}
    row.update(changes)
    return row


def test_all_read_cards_are_durable_and_identified_by_2gis_id(tmp_path):
    path = tmp_path / 'history.sqlite3'
    first = CardHistory(path)
    assert first.remember(record(websites=['https://example.test']))
    second = CardHistory(path)
    assert second.claim('1', 'next-run') == 'seen'
    assert not second.remember(record(sourceUrl='https://2gis.ru/anothercity/firm/1'))
    assert second.count() == 1
    with second.connection() as db:
        saved = json.loads(db.execute('SELECT record_json FROM cards').fetchone()[0])
    assert saved['websites'] == ['https://example.test'] and saved['phones']
    assert card_id({'source':'vk', 'source_id':'2'}) is None


def test_simultaneous_runs_claim_card_only_once(tmp_path):
    first = CardHistory(tmp_path / 'history.sqlite3')
    second = CardHistory(first.path)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda pair: pair[0].claim('1', pair[1]), [(first,'one'), (second,'two')]))
    assert sorted(results) == ['busy', 'new']
    first.remember(record())
    assert second.claim('1', 'another') == 'seen'


def test_failed_or_crashed_read_can_be_retried(tmp_path):
    history = CardHistory(tmp_path / 'history.sqlite3')
    assert history.claim('1', 'crashed', now=10, lease=5) == 'new'
    assert history.claim('1', 'other', now=12) == 'busy'
    assert history.claim('1', 'other', now=16) == 'new'
    history.release('other')
    assert history.claim('1', 'retry', now=17) == 'new'
    assert history.count() == 0


def test_import_existing_runs_is_incremental_and_handles_partial_write(tmp_path):
    folder = tmp_path / 'runs/old'; folder.mkdir(parents=True)
    path = folder / 'collected.json'
    history = CardHistory(tmp_path / 'history.sqlite3')
    path.write_text('[incomplete', encoding='utf-8')
    assert history.import_runs([folder.parent]) == 0
    path.write_text(json.dumps([record(), record('2', websites=['https://example.test']), {'name':'Not a 2GIS record'}]), encoding='utf-8')
    assert history.import_runs([folder.parent]) == 2
    assert history.import_runs([folder.parent]) == 0
    path.write_text(json.dumps([record(), record('2'), record('3')]), encoding='utf-8')
    assert history.import_runs([folder.parent]) == 1
    assert history.count() == 3


def test_skips_known_page_before_opening_and_limit_counts_new_cards(monkeypatch):
    visited, written = [], []
    def load(parser, url):
        visited.append(url)
        if '/search/' in url:
            page = 2 if url.endswith('/page/2') else 1
            return {'searches':[{'page':page, 'pages':2, 'meta':{'code':200}, 'ids':['1','2'] if page == 1 else ['3','4']}]}
        identity = url.rsplit('/', 1)[1]
        return {'url':url, 'profiles':{identity:{'meta':{'code':200}, 'data':{'id':identity, 'name':'ТЕСТ'}}}}
    monkeypatch.setattr(page_data, 'load_page', load)
    parser = SimpleNamespace(_url='https://2gis.ru/krasnodar/search/test',
                             _options=ParserOptions(max_records=1), renkai_skip_known=lambda identity: identity in ('1','2'))
    page_data.parse_page_data(parser, SimpleNamespace(write=written.append))
    assert len(written) == 1
    assert [url for url in visited if '/firm/' in url] == ['https://2gis.ru/krasnodar/firm/3']


def test_import_preserves_ids_of_branches_previously_merged_by_phone(tmp_path):
    path = tmp_path / 'collected.json'
    path.write_text(json.dumps([record(sources=[{'source':'2gis', 'url':'https://2gis.ru/firm/1'},
                                               {'source':'2gis', 'url':'https://2gis.ru/firm/2'}])]), encoding='utf-8')
    history = CardHistory(tmp_path / 'history.sqlite3')
    assert history.import_runs([tmp_path]) == 2
    assert history.claim('2', 'next') == 'seen'
