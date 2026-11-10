import json
from types import SimpleNamespace
from parser_2gis.parser.parsers.main import MainParser


class Remote:
    def __init__(self, fail_stale=False, respond=True):
        self.fail_stale = fail_stale
        self.respond = respond
        self.clicked = []
    def navigate(self, *args, **kwargs): pass
    def get_responses(self, **kwargs): return [{'mimeType': 'text/html', 'status': 200}]
    def perform_click(self, node):
        self.clicked.append(node.attributes['href'])
        if self.fail_stale:
            self.fail_stale = False
            raise RuntimeError('DOM.resolveNode error: No node with given id found')
    def wait_response(self, *args, **kwargs): return {'status':200} if self.respond else None
    def get_response_body(self, *args, **kwargs): return json.dumps({'meta':{'code':200},'result':{'items':[]}})
    def clear_requests(self): pass


def make_parser(remote, maximum):
    parser = MainParser.__new__(MainParser)
    parser._options = SimpleNamespace(max_records=maximum, delay_between_clicks=0, use_gc=False)
    parser._url = 'https://2gis.ru/krasnodar/search/test'
    parser._chrome_remote = remote
    parser._item_response_pattern = 'catalog'
    parser.renkai_access_check = None
    parser.renkai_cancelled = None
    parser.renkai_progress = None
    parser.renkai_stop_reason = ''
    parser._wait_requests_finished = lambda: True
    parser._get_links = lambda **kwargs: [SimpleNamespace(attributes={'href': f'/krasnodar/firm/{n}?stat=x'}) for n in range(1, 5)]
    return parser


def test_stale_node_is_refreshed_and_following_cards_are_processed():
    remote = Remote(fail_stale=True)
    parser = make_parser(remote, maximum=2)
    docs = []
    progress = []
    parser.renkai_progress = progress.append
    parser.parse(SimpleNamespace(write=docs.append))
    assert len(docs) == 2
    assert len(remote.clicked) == 3
    assert 'карточку 2' in progress[-1]


def test_missing_responses_stop_after_three_cards_without_retries():
    remote = Remote(respond=False)
    parser = make_parser(remote, maximum=10)
    parser.parse(SimpleNamespace(write=lambda doc: None))
    assert len(remote.clicked) == 3
    assert 'трёх карточек' in parser.renkai_stop_reason


def test_closed_catalog_connection_stops_immediately_and_explains_error():
    remote = Remote()
    remote.wait_response = lambda *args, **kwargs: {'status': -1, 'statusText': 'error: net::ERR_CONNECTION_CLOSED'}
    parser = make_parser(remote, maximum=10)
    parser.parse(SimpleNamespace(write=lambda doc: None))
    assert len(remote.clicked) == 1
    assert 'net::ERR_CONNECTION_CLOSED' in parser.renkai_stop_reason
