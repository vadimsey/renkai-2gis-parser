"""RENKAI adapter for Eroloft/parser-2gis-new (LGPL-3.0)."""
import json
import sys
import time
from pathlib import Path
from urllib.parse import quote

from hybrid_core import Merger, from_2gis
from pipeline_worker import write_json


class AccessBlocked(Exception):
    pass


class Worker:
    def __init__(self, config, folder):
        self.config, self.folder = config, Path(folder)
        self.merger = Merger(config.get('exclude', []))
        self.source = {'name': '2ГИС', 'status': 'running', 'count': 0, 'message': 'Ищем карточки 2ГИС'}

    def cancelled(self):
        return (self.folder / 'cancel').exists()

    def checkpoint(self, status='collecting'):
        self.source['count'] = len(self.merger.rows)
        write_json(self.folder / 'collected.json', self.merger.rows)
        write_json(self.folder / 'worker.json', {
            'status': status, 'seen': self.merger.seen, 'candidates': len(self.merger.rows),
            'query_errors': int(self.source['status'] in ('error', 'blocked')),
            'sources': {'2gis': self.source}, 'updated': time.time(),
            'note': 'Данные получены из карточек 2ГИС; наличие сайта проверяется отдельно.'})

    def write(self, doc):
        """Receive the original parser's by-id catalog document."""
        if self.cancelled():
            return
        if not isinstance(doc, dict) or (doc.get('meta') or {}).get('code') != 200:
            return
        for item in (doc.get('result') or {}).get('items', []):
            row = from_2gis(item, self.config['city']) if isinstance(item, dict) else None
            if row:
                self.merger.add(row)
        self.source['message'] = 'Получено карточек: ' + str(len(self.merger.rows))
        self.checkpoint()

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
            from parser_2gis.chrome import ChromeOptions
            from parser_2gis.parser import get_parser, ParserOptions
            from site_audit_browser import chrome_path
            executable = chrome_path()
            if not executable:
                raise RuntimeError('Не найден Chrome или Edge.')
            city_code = str(self.config.get('city_code') or '')
            if not city_code or not all(c.isascii() and (c.isalnum() or c == '-') for c in city_code):
                raise RuntimeError('Укажите корректный код города 2ГИС.')
            url = 'https://2gis.ru/' + city_code + '/search/' + quote('химчистка мебели')
            options = ChromeOptions(binary_path=Path(executable), headless=not self.config.get('browser_visible', False), disable_images=False)
            requested = self.config.get('pool', 50)
            max_records = sys.maxsize if requested is None else max(int(requested), 1)
            parser = get_parser(url, options, ParserOptions(max_records=max_records, delay_between_clicks=1000))
            parser.renkai_access_check = self.check_access
            parser.renkai_cancelled = self.cancelled
            parser.parse(self)
            if self.cancelled():
                self.source.update(status='cancelled', message='Остановлено')
                status = 'cancelled'
            elif not self.merger.rows:
                self.source.update(status='error', message='Карточки не получены. Проверьте доступ к 2ГИС или изменение разметки.')
                status = 'error'
            else:
                self.source.update(status='done', message='Карточки получены; проверяем сайт и услугу.')
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
            if parser:
                try:
                    parser.close()
                except Exception:
                    pass
        self.checkpoint(status)
        return 0


def run(config_path):
    path = Path(config_path)
    config = json.loads(path.read_text(encoding='utf-8-sig'))
    return Worker(config, path.parent).run()
