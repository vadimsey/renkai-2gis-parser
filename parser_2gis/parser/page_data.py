"""Read the public page's own server-rendered records in the original browser.

Normal document navigation loads the same contacts displayed by 2GIS without
depending on the separate catalog XHR. No alternate hosts or credentials.
"""
import ast
import json
import re
import time
from urllib.parse import unquote, urlsplit

from ..logger import logger
from ..furniture import furniture_service
from ..contact_urls import messenger_kind

SNAPSHOT = r'''JSON.stringify((() => {
  const s = window.initialState || {}, d = s.data || {}, search = d.search || {};
  const profiles = (d.entity || {}).profile || {};
  const searches = Object.entries(search.profile || {}).map(([key, value]) => {
    const data = value.data || {}, pagination = (search.pagination || {})[key] || {};
    return {page:data.currentPage, pages:data.pages, total:data.total,
      query:data.query, meta:value.meta,
      ids:((pagination[String(data.currentPage)] || {}).data || [])};
  });
  return {url:location.href, ready:document.readyState,
    text:(document.body ? document.body.innerText : '').slice(0,20000),
    profiles:profiles, searches:searches};
})())'''


class PageReadError(RuntimeError):
    pass


def snapshot_from_html(html, url):
    """Decode the page's JSON string literal without evaluating its scripts."""
    if not isinstance(html, str) or len(html) > 20_000_000:
        return None
    match = re.search(r'''\binitialState\s*=\s*JSON\.parse\(\s*('(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")''', html)
    if not match:
        return None
    try:
        state = json.loads(ast.literal_eval(match.group(1)))
    except (ValueError, SyntaxError, json.JSONDecodeError):
        return None
    if not isinstance(state, dict):
        return None
    data = state.get('data') or {}
    searches = []
    search = data.get('search') or {}
    for key, wrapped in (search.get('profile') or {}).items():
        row = wrapped.get('data') or {}
        page = row.get('currentPage')
        pagination = (search.get('pagination') or {}).get(key) or {}
        searches.append({'page': page, 'pages': row.get('pages'), 'total': row.get('total'),
                         'meta': wrapped.get('meta'), 'query': row.get('query'),
                         'ids': (pagination.get(str(page)) or {}).get('data') or []})
    return {'url': url, 'ready': 'complete', 'text': '',
            'profiles': (data.get('entity') or {}).get('profile') or {}, 'searches': searches,
            'market': data.get('market') or {}}


def identity(value):
    return str(value or '').split('_', 1)[0]


def path(url):
    return unquote(urlsplit(url).path).rstrip('/')


def cancelled(parser):
    callback = getattr(parser, 'renkai_cancelled', None)
    return bool(callback and callback())


def progress(parser, text):
    callback = getattr(parser, 'renkai_progress', None)
    if callback:
        callback(text)


def load_page(parser, url):
    remote = parser._chrome_remote
    remote.clear_requests()
    try:
        remote.navigate(url, timeout=5)
    except Exception as exc:
        if cancelled(parser):
            return None
        # Chrome can keep Page.navigate pending during an optional SSO redirect
        # even though the requested 2GIS HTML response has already arrived.
        if 'timeout' not in str(exc).casefold():
            raise PageReadError('Не удалось открыть страницу: ' + str(exc)) from exc
    deadline = time.monotonic() + 25
    snapshot = {}
    while time.monotonic() < deadline:
        if cancelled(parser):
            return None
        documents = [r for r in remote.get_responses(timeout=0)
                     if r.get('mimeType') == 'text/html' and path(r.get('url', '')) == path(url)
                     and urlsplit(r.get('url', '')).hostname == urlsplit(url).hostname]
        if documents:
            document = documents[-1]
            status = document.get('status', 0)
            if status in (403, 429):
                check = getattr(parser, 'renkai_access_check', None)
                if check:
                    check(remote, document)
                else:
                    raise RuntimeError('2ГИС ограничил доступ; нужна ручная проверка.')
            elif status >= 400:
                raise PageReadError('Страница 2ГИС вернула HTTP %s' % status)
            elif status == 200 and hasattr(remote, 'get_response_body'):
                try:
                    body = document.get('body') or remote.get_response_body(document, timeout=.5, call_timeout=2)
                except Exception:
                    body = ''
                parsed = snapshot_from_html(body, document['url'])
                if parsed and (parsed['profiles'] or parsed['searches']):
                    return parsed
        try:
            raw = remote.execute_script(SNAPSHOT, timeout=2)
        except Exception:
            time.sleep(.15)
            continue
        snapshot = json.loads(raw) if isinstance(raw, str) and raw else {}
        if not snapshot:
            time.sleep(.15)
            continue
        response = next((r for r in remote.get_responses(timeout=0)
                         if r.get('mimeType') == 'text/html'
                         and path(r.get('url') or url) == path(snapshot.get('url', ''))), {})
        text = snapshot.get('text', '').casefold()
        blocked = response.get('status') in (403, 429) or any(term in text for term in (
            'подтвердите, что вы не робот', 'подтвердите что вы не робот',
            'проверка безопасности', 'доступ ограничен', 'too many requests', 'verify you are human'))
        if blocked or 'captcha' in snapshot.get('url', '').casefold():
            check = getattr(parser, 'renkai_access_check', None)
            if check:
                check(remote, response)
                continue
            raise RuntimeError('2ГИС ограничил доступ к странице; нужна ручная проверка.')
        if response.get('status', 200) >= 400:
            raise PageReadError('Страница 2ГИС вернула HTTP %s' % response['status'])
        expected_firm = re.search(r'/firm/(\d+)', path(url))
        actual_firm = re.search(r'/firm/(\d+)', path(snapshot.get('url', '')))
        same_page = path(snapshot.get('url', '')) == path(url)
        if expected_firm and actual_firm and expected_firm.group(1) == actual_firm.group(1):
            same_page = True
        if same_page and snapshot.get('ready') != 'loading':
            if snapshot.get('profiles') or snapshot.get('searches'):
                return snapshot
        time.sleep(.15)
    actual = urlsplit(snapshot.get('url', ''))
    raise PageReadError('Страница 2ГИС не передала данные: %s; открыто: %s' % (url, actual.netloc + actual.path))


def extract_firm(snapshot, firm_id):
    for key, wrapped in snapshot.get('profiles', {}).items():
        row = wrapped.get('data') or {}
        if identity(row.get('id') or key) != firm_id:
            continue
        meta = wrapped.get('meta') or {}
        if meta.get('code') != 200 or not row.get('name'):
            return None
        result = dict(row)
        result['renkai_page_url'] = snapshot['url']
        result['renkai_transport'] = 'public_page'
        return result
    return None


def price_services(snapshot, firm_id):
    market = snapshot.get('market') or {}
    evidence = []
    for wrapped in (market.get('offers') or {}).values():
        offer = wrapped.get('data') or {}
        if str(offer.get('branchId')) != firm_id or (wrapped.get('meta') or {}).get('code') != 200:
            continue
        product_wrapper = (market.get('products') or {}).get(str(offer.get('productId'))) or {}
        if (product_wrapper.get('meta') or {}).get('code') != 200:
            continue
        product = (product_wrapper.get('data') or {}).get('product') or {}
        labels = [product.get('name'), *(c.get('label') for c in product.get('categories') or [])]
        text = ' · '.join(str(v) for v in labels if v)
        if not furniture_service([text]):
            text = ' · '.join(str(v) for v in [product.get('name'), product.get('description'), *labels[1:]] if v)
        if furniture_service([text]):
            evidence.append({'text': text[:500], 'url': snapshot['url'], 'product_id': str(offer['productId'])})
    return evidence


def needs_price_check(row):
    if not row.get('has_goods'):
        return False
    labels = [row.get('name'), *(r.get('name') for r in row.get('rubrics') or [])]
    if furniture_service(labels):
        return False
    for group in row.get('contact_groups') or []:
        for contact in group.get('contacts') or []:
            link = str(contact.get('url') or contact.get('value') or '')
            if contact.get('type') == 'website' and not messenger_kind(link):
                return False
    return True


def parse_page_data(parser, writer):
    base = re.sub(r'/page/\d+(?=/|$)', '', parser._url).rstrip('/')
    city = urlsplit(base).path.strip('/').split('/')[0]
    origin = 'https://' + urlsplit(base).netloc
    seen = set()
    start_page = re.search(r'/page/(\d+)(?=/|$)', parser._url)
    page = int(start_page.group(1)) if start_page else 1
    count = 0
    failures = 0
    parser.renkai_skipped = 0
    while not cancelled(parser):
        progress(parser, 'Загружаем страницу поиска %s; получено %s.' % (page, count))
        snapshot = load_page(parser, base if page == 1 else base + '/page/' + str(page))
        if snapshot is None:
            return
        search = next((s for s in snapshot.get('searches', []) if s.get('page') == page), None)
        if not search:
            parser.renkai_stop_reason = '2ГИС не подтвердил запрошенную страницу поиска; сбор остановлен.'
            return
        if (search.get('meta') or {}).get('code') != 200:
            raise RuntimeError('2ГИС не подтвердил успешную загрузку выдачи.')
        ids = list(dict.fromkeys(identity(v) for v in search.get('ids', []) if identity(v).isdigit()))
        fresh = [firm_id for firm_id in ids if firm_id not in seen]
        if not fresh:
            return
        for firm_id in fresh:
            if cancelled(parser):
                return
            seen.add(firm_id)
            progress(parser, 'Открываем карточку %s на странице %s; получено %s.' % (len(seen), page, count))
            if parser._options.delay_between_clicks:
                time.sleep(parser._options.delay_between_clicks / 1000)
            try:
                firm = load_page(parser, origin + '/' + city + '/firm/' + firm_id)
            except PageReadError as exc:
                failures += 1
                parser.renkai_skipped += 1
                logger.warning('Карточка %s пропущена: %s', firm_id, exc)
                progress(parser, 'Карточка недоступна; продолжаем сбор. Получено %s.' % count)
                if failures >= 3:
                    parser.renkai_stop_reason = 'Три карточки подряд не отдали данные; полученные контакты сохранены. ' + str(exc)
                    return
                continue
            if firm is None:
                return
            row = extract_firm(firm, firm_id)
            if row is None:
                parser.renkai_skipped += 1
                logger.warning('На странице не подтверждена карточка %s; пропуск.', firm_id)
                continue
            if needs_price_check(row):
                progress(parser, 'Проверяем услуги в прайсе: ' + row['name'])
                try:
                    prices = load_page(parser, origin + '/' + city + '/firm/' + firm_id + '/tab/prices')
                    if prices is not None:
                        row['renkai_service_evidence'] = price_services(prices, firm_id)
                except PageReadError as exc:
                    logger.warning('Прайс %s недоступен: %s', firm_id, exc)
            writer.write({'meta': {'code': 200}, 'result': {'items': [row]}})
            count += 1
            failures = 0
            if count >= parser._options.max_records:
                return
        if page >= int(search.get('pages') or page):
            return
        page += 1
