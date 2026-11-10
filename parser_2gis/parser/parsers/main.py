from __future__ import annotations

import base64
import json
import re
import urllib.parse
from typing import TYPE_CHECKING, Optional

from ...chrome import ChromeRemote
from ...common import wait_until_finished
from ...logger import logger
from ..utils import blocked_requests

if TYPE_CHECKING:
    from ...chrome import ChromeOptions
    from ...chrome.dom import DOMNode
    from ...writer import FileWriter
    from ..options import ParserOptions


class MainParser:
    """Main parser that extracts useful payload
    from search result pages using Chrome browser
    and saves it into a `csv`, `xlsx` or `json` files.

    Args:
        url: 2GIS URLs with items to be collected.
        chrome_options: Chrome options.
        parser_options: Parser options.
    """
    def __init__(self, url: str,
                 chrome_options: ChromeOptions,
                 parser_options: ParserOptions) -> None:
        self._options = parser_options
        self._url = url
        # Optional RENKAI callbacks for progress, cancellation and access checks.
        self.renkai_access_check = None
        self.renkai_cancelled = None
        self.renkai_progress = None
        self.renkai_stop_reason = ''

        # "Catalog Item Document" response pattern.
        self._item_response_pattern = r'https://catalog\.api\.2gis.[^/]+/.*/items/byid'

        # Open browser, start remote
        response_patterns = [self._item_response_pattern]
        self._chrome_remote = ChromeRemote(chrome_options=chrome_options,
                                           response_patterns=response_patterns)
        self._chrome_remote.start()

        # Add counter for 2GIS requsts
        self._add_xhr_counter()

        # Disable specific requests
        blocked_urls = blocked_requests(extended=chrome_options.disable_images)
        self._chrome_remote.add_blocked_requests(blocked_urls)

    @staticmethod
    def url_pattern():
        """URL pattern for the parser."""
        return r'https?://2gis\.[^/]+/[^/]+/search/.*'

    @wait_until_finished(timeout=5, throw_exception=False)
    def _get_links(self) -> list[DOMNode]:
        """Extracts specific DOM node links from current DOM snapshot."""
        def valid_link(node: DOMNode) -> bool:
            if node.local_name == 'a' and 'href' in node.attributes:
                if re.search(r'/firm/\d+', node.attributes['href']):
                    return True
                link_match = re.match(r'.*/(firm|station)/.*\?stat=(?P<data>[a-zA-Z0-9%]+)', node.attributes['href'])
                if link_match:
                    try:
                        base64.b64decode(urllib.parse.unquote(link_match.group('data')))
                        return True
                    except:
                        pass

            return False

        dom_tree = self._chrome_remote.get_document()
        return dom_tree.search(valid_link)

    def _add_xhr_counter(self) -> None:
        """Inject old-school wrapper around XMLHttpRequest,
        to keep track of all pending requests to 2GIS website."""
        xhr_script = r'''
            (function() {
                var oldOpen = XMLHttpRequest.prototype.open;
                XMLHttpRequest.prototype.open = function(method, url, async, user, pass) {
                    if (url.match(/^https?\:\/\/[^\/]*2gis\.[a-z]+/i)) {
                        if (window.openHTTPs == undefined) {
                            window.openHTTPs = 1;
                        } else {
                            window.openHTTPs++;
                        }
                        this.addEventListener("readystatechange", function() {
                            if (this.readyState == 4) {
                                window.openHTTPs--;
                            }
                        }, false);
                    }
                    oldOpen.call(this, method, url, async, user, pass);
                }
            })();
        '''
        self._chrome_remote.add_start_script(xhr_script)

    @wait_until_finished(timeout=10, throw_exception=False)
    def _wait_requests_finished(self) -> bool:
        """Give the page time to settle without waiting on background traffic."""
        if self.renkai_cancelled and self.renkai_cancelled():
            return True
        return self._chrome_remote.execute_script(
            'document.readyState !== "loading" && (window.openHTTPs === undefined || window.openHTTPs === 0)')

    def _get_available_pages(self) -> dict[int, DOMNode]:
        """Get available pages to navigate."""
        dom_tree = self._chrome_remote.get_document()
        dom_links = dom_tree.search(lambda x: x.local_name == 'a' and 'href' in x.attributes)

        available_pages = {}
        for link in dom_links:
            link_match = re.match(r'.*/search/.*/page/(?P<page_number>\d+)', link.attributes['href'])
            if link_match:
                available_pages[int(link_match.group('page_number'))] = link

        return available_pages

    def _go_page(self, n_page: int) -> Optional[int]:
        """Go page with number `n_page`.

        Note:
            `n_page` gotta exists in current DOM.
            Otherwise 2GIS anti-bot will redirect you to the first page.

        Args:
            n_page: Page number.

        Returns:
            Navigated page number.
        """
        available_pages = self._get_available_pages()
        if n_page in available_pages:
            self._chrome_remote.perform_click(available_pages[n_page])
            return n_page

        return None

    def parse(self, writer: FileWriter) -> None:
        """Parse URL with result items.

        Args:
            writer: Target file writer.
        """
        # Starting from page 6 and further
        # 2GIS redirects user to the beginning automatically (anti-bot protection).
        # If a page argument found in the URL, we should manually walk to it first.

        current_page_number = 1
        url = re.sub(r'/page/\d+', '', self._url, re.I)

        page_match = re.search(r'/page/(?P<page_number>\d+)', self._url, re.I)
        if page_match:
            walk_page_number = int(page_match.group('page_number'))
        else:
            walk_page_number = None

        # Go URL
        self._chrome_remote.navigate(url, referer='https://google.com', timeout=120)

        # Document loaded, get its response
        responses = self._chrome_remote.get_responses(timeout=5)
        if not responses:
            logger.error('Ошибка получения ответа сервера.')
            return
        document_response = responses[0]

        if self.renkai_access_check:
            self.renkai_access_check(self._chrome_remote, document_response)

        # Handle 404
        assert document_response['mimeType'] == 'text/html'
        if document_response['status'] == 404:
            logger.warn('Сервер вернул сообщение "Точных совпадений нет / Не найдено".')

            if self._options.skip_404_response:
                return

        # Parsed records
        collected_records = 0

        # Already visited links
        visited_links: set[str] = set()

        # This wrapper is not necessary, but I'd like to be sure
        # we haven't gathered links from old DOM somehow.
        def link_key(link: DOMNode) -> str:
            href = link.attributes.get('href', '')
            firm = re.search(r'/firm/(\d+)', href)
            return firm.group(1) if firm else href.split('?', 1)[0]

        def get_unique_links() -> list[DOMNode]:
            seen = set()
            links = []
            for link in self._get_links(timeout=10) or []:
                key = link_key(link)
                if key and key not in visited_links and key not in seen:
                    seen.add(key)
                    links.append(link)
            return links

        while True:
            # Wait all 2GIS requests get finished
            self._wait_requests_finished()
            if self.renkai_cancelled and self.renkai_cancelled():
                return
            if self.renkai_access_check:
                self.renkai_access_check(self._chrome_remote, document_response)

            # Gather links to be clicked
            links = get_unique_links()

            # We should parse the page if we are not walking
            if not walk_page_number:
                # Iterate through gathered links
                failures = 0
                for link in links:
                    if self.renkai_cancelled and self.renkai_cancelled():
                        return
                    key = link_key(link)
                    visited_links.add(key)
                    if self.renkai_progress:
                        self.renkai_progress('Проверяем карточку %s; получено %s.' % (len(visited_links), collected_records))
                    resp = None
                    # 2GIS re-renders the list after a card opens, invalidating
                    # backend node IDs. Resolve a fresh node for each click.
                    for attempt in range(2):
                        fresh = next((candidate for candidate in (self._get_links(timeout=3) or [])
                                      if link_key(candidate) == key), None)
                        if fresh is None:
                            break
                        # Click the link to provoke request
                        # with a auth key and secret arguments
                        try:
                            self._chrome_remote.perform_click(fresh)
                        except Exception as exc:
                            if attempt == 0 and 'No node with given id found' in str(exc):
                                continue
                            logger.warning('Не удалось открыть карточку: %s', exc)
                            break

                        # Delay between clicks, could be usefull if
                        # 2GIS's anti-bot service become more strict.
                        if self._options.delay_between_clicks:
                            self._chrome_remote.wait(self._options.delay_between_clicks / 1000)

                        # Gather response and collect useful payload.
                        resp = self._chrome_remote.wait_response(self._item_response_pattern, timeout=15)

                        if resp and resp.get('status') in (403, 429):
                            raise RuntimeError('2ГИС ограничил доступ (HTTP %s)' % resp['status'])

                        if resp and resp.get('status') == -1:
                            error = re.search(r'net::[A-Z0-9_]+', resp.get('statusText') or '')
                            code = ' (' + error.group(0) + ')' if error else ''
                            self.renkai_stop_reason = ('Не удалось соединиться с каталогом 2ГИС' + code +
                                '. Проверьте подключение к catalog.api.2gis.ru и повторите позже.')
                            return

                        # Do not repeatedly request a card when the source fails.
                        break

                    # Get response body data
                    if resp and resp['status'] >= 0:
                        data = self._chrome_remote.get_response_body(resp, timeout=10) if resp else None

                        try:
                            doc = json.loads(data)
                        except json.JSONDecodeError:
                            logger.error('Сервер вернул некорректный JSON документ: "%s", пропуск позиции.', data)
                            doc = None
                    else:
                        doc = None

                    if doc:
                        # Write API document into a file
                        writer.write(doc)
                        collected_records += 1
                        failures = 0
                    else:
                        logger.error('Данные не получены, пропуск позиции.')
                        failures += 1
                        if failures >= 3:
                            self.renkai_stop_reason = ('2ГИС не вернул данные трёх карточек подряд. '
                                                      'Проверьте доступ в видимом браузере и повторите позже.')
                            return

                    # We've reached our limit, bail
                    if collected_records >= self._options.max_records:
                        logger.info('Спарсено максимально разрешенное количество записей с данного URL.')
                        return

            # Evaluate Garbage Collection if it's been exposed and enabled
            if self._options.use_gc and current_page_number % self._options.gc_pages_interval == 0:
                logger.debug('Запуск сборщика мусора.')
                self._chrome_remote.execute_script('"gc" in window && window.gc()')

            # Free memory allocated for collected requests
            self._chrome_remote.clear_requests()

            # Calculate next page number and navigate it
            if walk_page_number:
                available_pages = self._get_available_pages()
                available_pages_ahead = {k: v for k, v in available_pages.items()
                                         if k > current_page_number}
                next_page_number = min(available_pages_ahead, key=lambda n: abs(n - walk_page_number),  # type: ignore
                                       default=current_page_number + 1)
            else:
                next_page_number = current_page_number + 1

            current_page_number = self._go_page(next_page_number)  # type: ignore
            if not current_page_number:
                break  # Reached the end of the search results

            # Unset walking page if we've done walking to the desired page
            if walk_page_number and walk_page_number <= current_page_number:
                walk_page_number = None

    def close(self) -> None:
        self._chrome_remote.stop()

    def __enter__(self) -> MainParser:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def __repr__(self) -> str:
        classname = self.__class__.__name__
        return (f'{classname}(parser_options={self._options!r}, '
                'chrome_remote={self._chrome_remote!r}, '
                'url={self._url!r}')
