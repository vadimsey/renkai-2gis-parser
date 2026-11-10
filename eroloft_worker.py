"""RENKAI adapter for Eroloft/parser-2gis-new (LGPL-3.0)."""
import json
import sys
import time
import uuid
from pathlib import Path

from hybrid_core import Merger, from_2gis
from pipeline_worker import write_json


class AccessBlocked(Exception):
    pass


class Worker:
    def __init__(self, config, folder):
        self.config, self.folder = config, Path(folder)
        self.merger = Merger(config.get('exclude', []))
        self.source = {'name': '2ГИС', 'status': 'running', 'count': 0, 'message': 'Ищем карточки 2ГИС'}
        self.history = None
        self.history_owner = uuid.uuid4().hex

    def cancelled(self):
        return (self.folder / 'cancel').exists()

    def checkpoint(self, status='collecting'):
        self.source['count'] = len(self.merger.rows)
        if self.history:
            self.source['history_total'] = self.history.count()
        write_json(self.folder / 'collected.json', self.merger.rows)
        write_json(self.folder / 'worker.json', {
            'status': status, 'seen': self.merger.seen, 'candidates': len(self.merger.rows),
            'query_errors': int(self.source['status'] in ('error', 'blocked')) + int(self.source.get('skipped', 0)),
            'sources': {'2gis': self.source}, 'updated': time.time(),
            'note': 'Источник: карточки 2ГИС; наличие сайта проверяется по полученным контактам.'})

    def write(self, doc):
        """Receive the original parser's by-id catalog document."""
        if self.cancelled():
            return
        if not isinstance(doc, dict) or (doc.get('meta') or {}).get('code') != 200:
            return
        for item in (doc.get('result') or {}).get('items', []):
            row = from_2gis(item, self.config['city']) if isinstance(item, dict) else None
            if row:
                from parser_2gis.contact_urls import telegram_bot_urls
                if telegram_bot_urls(row):
                    if self.history:
                        self.history.remember(row)
                    self.source['telegram_bots_excluded'] = self.source.get('telegram_bots_excluded', 0) + 1
                    continue
                # Save every read card, including websites and excluded contacts.
                if self.history is None or self.history.remember(row):
                    self.merger.add(row)
        self.source['message'] = 'Получено карточек: ' + str(len(self.merger.rows))
        self.checkpoint()

    def progress(self, message):
        if self.source.get('history_skipped'):
            message += ' Пропущено из базы: %s.' % self.source['history_skipped']
        self.source['message'] = message
        self.checkpoint()

    def skip_known_card(self, identity):
        result = self.history.claim(identity, self.history_owner)
        if result == 'new':
            return False
        key = 'history_skipped' if result == 'seen' else 'history_busy'
        self.source[key] = self.source.get(key, 0) + 1
        return True

    def check_access(self, remote, response):
        def page_blocked():
            text = str(remote.execute_script("document.body ? document.body.innerText.slice(0,12000) : ''") or '').lower()
            url = str(remote.execute_script('location.href') or '').lower()
            return any(v in text for v in ('подтвердите, что вы не робот', 'подтвердите что вы не робот', 'доступ ограничен', 'доступ временно ограничен', 'проверка безопасности', 'too many requests', 'verify you are human')) or 'captcha' in url

        if response.get('status') in (403, 429) or page_blocked():
            if not self.config.get('browser_visible'):
                raise AccessBlocked('2ГИС запросил подтверждение доступа. Включите видимый браузер и пройдите проверку вручную.')
            self.source['message'] = 'Пройдите проверку 2ГИС в открытом браузере; ожидание до 3 минут.'
            self.checkpoint()
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline and not self.cancelled():
                time.sleep(2)
                if not page_blocked():
                    self.source['message'] = 'Проверка пройдена; собираем карточки.'
                    self.checkpoint()
                    return
            raise AccessBlocked('Подтверждение доступа 2ГИС не завершено. Запустите поиск позже.')

    def run(self):
        self.checkpoint()
        parser = None
        try:
            if self.cancelled():
                self.source.update(status='cancelled', message='Остановлено')
                self.checkpoint('cancelled')
                return 0
            from card_history import CardHistory, default_history_path
            self.history = CardHistory(self.config.get('history_path') or default_history_path())
            roots = self.config.get('history_import_roots', [str(self.history.path.parent / 'runs')])
            self.source['history_imported'] = self.history.import_runs(roots)
            self.source.update(history_skipped=0, history_busy=0)
            from parser_2gis.chrome import ChromeOptions
            from parser_2gis.parser import get_parser, ParserOptions
            from site_audit_browser import chrome_path
            executable = chrome_path()
            if not executable:
                raise RuntimeError('Не найден Chrome или Edge.')
            from parser_2gis.cities import resolve_city, furniture_search_url
            location = resolve_city(self.config.get('city', ''), self.config.get('city_code', ''))
            self.config.update(city=location['name'], city_code=location['code'])
            url = furniture_search_url(location)
            options = ChromeOptions(binary_path=Path(executable), headless=not self.config.get('browser_visible', False), disable_images=False)
            requested = self.config.get('pool')
            max_records = sys.maxsize if requested is None else max(int(requested), 1)
            parser = get_parser(url, options, ParserOptions(max_records=max_records, delay_between_clicks=1000))
            parser.renkai_access_check = self.check_access
            parser.renkai_cancelled = self.cancelled
            parser.renkai_progress = self.progress
            parser.renkai_page_data = True
            parser.renkai_skip_known = self.skip_known_card
            parser.parse(self)
            self.source['skipped'] = int(getattr(parser, 'renkai_skipped', 0))
            if self.cancelled():
                self.source.update(status='cancelled', message='Остановлено')
                status = 'cancelled'
            elif not self.merger.rows and not (self.source['history_skipped'] or self.source['history_busy'] or self.source.get('telegram_bots_excluded')):
                self.source.update(status='error', message=getattr(parser, 'renkai_stop_reason', '') or 'Карточки не получены. Проверьте доступ к 2ГИС или изменение разметки.')
                status = 'error'
            else:
                message = getattr(parser, 'renkai_stop_reason', '') or 'Получено уникальных карточек: %s.' % len(self.merger.rows)
                if self.source['skipped']:
                    message += ' Недоступно карточек: %s; они пропущены.' % self.source['skipped']
                message += ' Пропущено из базы: %s.' % self.source['history_skipped']
                if self.source['history_busy']:
                    message += ' Уже проверяются другим запуском: %s.' % self.source['history_busy']
                if self.source.get('telegram_bots_excluded'):
                    message += ' Telegram-боты исключены: %s.' % self.source['telegram_bots_excluded']
                self.source.update(status='done', message=message)
                status = 'done'
        except AccessBlocked as exc:
            self.source.update(status='blocked', message=str(exc))
            status = 'error'
        except Exception as exc:
            message = str(exc)
            if '403' in message or '429' in message:
                self.source.update(status='blocked', message='2ГИС ограничил доступ: ' + message[:160])
            else:
                self.source.update(status='error', message='Не удалось получить карточки 2ГИС: ' + message[:200])
            status = 'error'
        finally:
            if self.history:
                self.history.release(self.history_owner)
            if parser:
                try:
                    parser.close()
                except Exception:
                    pass
        self.checkpoint(status)
        return 1 if status == 'error' else 0


def run(config_path):
    path = Path(config_path)
    config = json.loads(path.read_text(encoding='utf-8-sig'))
    return Worker(config, path.parent).run()
