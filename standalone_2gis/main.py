"""Separate desktop shell for the RENKAI 2GIS worker."""
import csv
import json
import os
import re
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def classify(row):
    card = row.get('card_check') or {}
    if card.get('scope') != '2gis_card' or card.get('complete') is not True:
        return 'unverified'
    if row.get('websites') or card.get('websites') or card.get('has_website'):
        return 'website'
    text = ' '.join([str(row.get('name') or ''), *(str(x) for x in row.get('rubrics', []))]).lower().replace('ё', 'е')
    if not (re.search(r'(?:химчист|чистк|чисти|очист)', text) and re.search(r'(?:мебел|диван|матрас|кресел|кресл|стул|обивк)', text)):
        return 'unverified'
    return 'qualified'


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return default


def export_rows(rows, directory):
    from company_import import normalize
    from company_html import company_html
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'companies.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    with (directory / 'companies.csv').open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['Название', 'Город', 'Адрес', 'Телефоны', 'Telegram', 'VK', 'Карточка 2ГИС'])
        writer.writeheader()
        for r in rows:
            writer.writerow({'Название': r.get('name', ''), 'Город': r.get('city', ''), 'Адрес': r.get('address', ''), 'Телефоны': '; '.join(r.get('phones') or []), 'Telegram': r.get('telegram', ''), 'VK': r.get('vk', ''), 'Карточка 2ГИС': r.get('sourceUrl', '')})
    records = []
    for row in rows:
        item = normalize(row)
        item.update(phones=json.dumps(item['phones'], ensure_ascii=False), original_site='', audit_note='Сайт не указан в полученной карточке 2ГИС; внешний поиск не выполнялся.', skip_reason='', sources=row.get('sources', []), websites=[])
        records.append(item)
    document = company_html(records, title='RENKAI / ХИМЧИСТКА МЕБЕЛИ', subtitle='Сайты в карточках 2ГИС не указаны', filename='companies.html')
    (directory / 'companies.html').write_bytes(document.body)


class Api:
    def __init__(self, base=None):
        self.base = Path(base or (Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'RENKAI-2GIS'))
        self.base.mkdir(parents=True, exist_ok=True)
        self.run_dir = None
        self.process = None

    def start(self, city, city_code, limit, visible):
        if self.process and self.process.poll() is None:
            return {'ok': False, 'error': 'Поиск уже запущен.'}
        city, city_code = str(city).strip()[:120], str(city_code).strip().lower()
        if not city or not re.fullmatch(r'[a-z0-9-]{2,60}', city_code):
            return {'ok': False, 'error': 'Укажите город и его код из адреса 2ГИС латиницей.'}
        try:
            limit = None if str(limit).strip() == '' else int(limit)
            if limit is not None and limit < 1:
                raise ValueError('non-positive limit')
        except (ValueError, TypeError):
            return {'ok': False, 'error': 'Введите положительное количество или оставьте поле пустым для поиска всех доступных карточек.'}
        self.run_dir = self.base / 'runs' / (time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
        self.run_dir.mkdir(parents=True)
        config = {'city': city, 'city_code': city_code, 'pool': limit, 'browser_visible': bool(visible), 'exclude': []}
        path = self.run_dir / 'config.json'
        path.write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
        command = [sys.executable] if getattr(sys, 'frozen', False) else [sys.executable, str(Path(__file__).resolve())]
        command += ['--parser-worker', str(path)]
        try:
            self.process = subprocess.Popen(command, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), cwd=str(self.run_dir))
        except OSError as exc:
            return {'ok': False, 'error': str(exc)}
        return {'ok': True}

    def state(self):
        if not self.run_dir:
            return {'status': 'idle', 'rows': [], 'counts': {}}
        worker = read_json(self.run_dir / 'worker.json', {})
        rows = read_json(self.run_dir / 'collected.json', [])
        if not isinstance(rows, list): rows = []
        accepted = [r for r in rows if classify(r) == 'qualified']
        counts = {'all': len(rows), 'qualified': len(accepted), 'website': sum(classify(r) == 'website' for r in rows), 'unverified': sum(classify(r) == 'unverified' for r in rows)}
        running = self.process is not None and self.process.poll() is None
        status = worker.get('status', 'collecting' if running else 'error')
        if not running and status == 'collecting': status = 'error'
        source = worker.get('sources', {}).get('2gis', {})
        message = source.get('message') or ('Открываем 2ГИС…' if running else 'Рабочий процесс завершился без отчёта.')
        if source.get('status') == 'blocked': status = 'blocked'
        return {'status': status, 'running': running, 'message': message, 'counts': counts, 'rows': accepted, 'directory': str(self.run_dir)}

    def stop(self):
        if self.run_dir and self.process and self.process.poll() is None:
            (self.run_dir / 'cancel').write_text('stop', encoding='utf-8')
            return {'ok': True}
        return {'ok': False}

    def export(self):
        if not self.run_dir: return {'ok': False, 'error': 'Сначала запустите поиск.'}
        rows = [r for r in read_json(self.run_dir / 'collected.json', []) if classify(r) == 'qualified']
        if not rows: return {'ok': False, 'error': 'Пока нет подходящих карточек для экспорта.'}
        folder = self.run_dir / 'export'
        try:
            export_rows(rows, folder)
            os.startfile(str(folder))
            return {'ok': True, 'path': str(folder)}
        except Exception as exc:
            return {'ok': False, 'error': str(exc)}


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == '--parser-worker':
        try:
            from eroloft_worker import run
            return run(sys.argv[2])
        except Exception:
            Path(sys.argv[2]).with_name('worker-error.log').write_text(traceback.format_exc(), encoding='utf-8')
            return 1
    if len(sys.argv) >= 3 and sys.argv[1] == '--smoke-test':
        from eroloft_worker import Worker
        from parser_2gis.parser import get_parser
        Path(sys.argv[2]).write_text('imports ok', encoding='utf-8')
        return 0
    import webview
    api = Api()
    ui = (Path(sys._MEIPASS) / 'standalone_2gis' / 'ui' if getattr(sys, 'frozen', False) else Path(__file__).parent / 'ui') / 'index.html'
    window = webview.create_window('Renkai Parser', ui.as_uri(), js_api=api, width=1360, height=900, min_size=(980, 720), background_color='#f4f4f5')
    window.events.closing += api.stop
    if len(sys.argv) >= 3 and sys.argv[1] == '--ui-smoke-test':
        report = Path(sys.argv[2])
        def check():
            try:
                if not window.events.loaded.wait(20):
                    raise RuntimeError('UI did not load')
                result = window.evaluate_js("JSON.stringify({title:document.querySelector('h1').textContent,city:document.querySelector('#city').value,start:!!document.querySelector('#start'),export:!!document.querySelector('#export')})")
                report.write_text(result, encoding='utf-8')
            except Exception:
                report.write_text(traceback.format_exc(), encoding='utf-8')
            finally:
                window.destroy()
        webview.start(check, debug=False)
    else:
        webview.start(debug=False)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
