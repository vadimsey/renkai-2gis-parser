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
    if row.get('websites') or card.get('websites') or card.get('has_website'):
        return 'website'
    # User-selected rule: every collected card with no detected website qualifies,
    # including incomplete cards and cards without explicit service evidence.
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
        item.update(phones=json.dumps(item['phones'], ensure_ascii=False), original_site='', audit_note='Сайт не обнаружен в полученных данных 2ГИС; полнота контактов и услуга не являются условиями отбора. Внешний поиск не выполнялся.', skip_reason='', sources=row.get('sources', []), websites=[])
        records.append(item)
    document = company_html(records, title='RENKAI / ХИМЧИСТКА МЕБЕЛИ', subtitle='Сайты в карточках 2ГИС не указаны', filename='companies.html')
    (directory / 'companies.html').write_bytes(document.body)


class Api:
    def __init__(self, base=None):
        self.base = Path(base or (Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'RENKAI-2GIS'))
        self.base.mkdir(parents=True, exist_ok=True)
        from card_history import CardHistory
        self.history = CardHistory(self.base / 'history.sqlite3')
        self.history.import_runs([self.base / 'runs'])
        self.run_dir = None
        self.process = None

    def cities(self):
        from parser_2gis.cities import russian_cities
        return russian_cities()

    def start(self, city, city_code, limit, visible):
        if self.process and self.process.poll() is None:
            return {'ok': False, 'error': 'Поиск уже запущен.'}
        city, city_code = str(city).strip()[:120], str(city_code).strip().lower()
        try:
            from parser_2gis.cities import resolve_city
            location = resolve_city(city, city_code)
            city, city_code = location['name'], location['code']
        except (ValueError, OSError) as exc:
            return {'ok': False, 'error': str(exc)}
        try:
            limit = None if str(limit).strip() == '' else int(limit)
            if limit is not None and limit < 1:
                raise ValueError('non-positive limit')
        except (ValueError, TypeError):
            return {'ok': False, 'error': 'Введите положительное количество или оставьте поле пустым для поиска всех доступных карточек.'}
        self.run_dir = self.base / 'runs' / (time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
        self.run_dir.mkdir(parents=True)
        config = {'city': city, 'city_code': city_code, 'pool': limit, 'browser_visible': bool(visible), 'exclude': [],
                  'history_path': str(self.history.path), 'history_import_roots': [str(self.base / 'runs')]}
        path = self.run_dir / 'config.json'
        path.write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
        command = [sys.executable] if getattr(sys, 'frozen', False) else [sys.executable, str(Path(__file__).resolve())]
        command += ['--parser-worker', str(path)]
        try:
            self.process = subprocess.Popen(command, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), cwd=str(self.run_dir))
        except OSError as exc:
            return {'ok': False, 'error': str(exc)}
        return {'ok': True, 'city': city, 'city_code': city_code}

    def state(self):
        if not self.run_dir:
            return {'status': 'idle', 'rows': [], 'counts': {}, 'history': {'total':self.history.count(), 'skipped':0}}
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
        return {'status': status, 'running': running, 'message': message, 'counts': counts, 'rows': accepted, 'directory': str(self.run_dir),
                'history': {'total':self.history.count(), 'skipped':source.get('history_skipped', 0)}}

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
        from parser_2gis.cities import russian_cities, resolve_city
        from parser_2gis.parser.page_data import snapshot_from_html
        from card_history import CardHistory
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as folder:
            history = CardHistory(Path(folder) / 'history.sqlite3')
            assert history.remember({'source':'2gis', 'source_id':'1', 'name':'Smoke test'})
            assert CardHistory(history.path).claim('1', 'another-run') == 'seen'
        assert snapshot_from_html('<html>missing data</html>', 'https://2gis.ru/') is None
        assert resolve_city('Нижний Новгород', 'krasnodar')['code'] == 'n_novgorod'
        Path(sys.argv[2]).write_text('imports ok; history ok; cities=' + str(len(russian_cities())), encoding='utf-8')
        return 0
    import webview
    api = Api()
    ui = (Path(sys._MEIPASS) / 'standalone_2gis' / 'ui' if getattr(sys, 'frozen', False) else Path(__file__).parent / 'ui') / 'index.html'
    window = webview.create_window('Renkai Parser 0.1.5', ui.as_uri(), js_api=api, width=1360, height=900, min_size=(980, 720), background_color='#f4f4f5')
    window.events.closing += api.stop
    if len(sys.argv) >= 3 and sys.argv[1] == '--ui-smoke-test':
        report = Path(sys.argv[2])
        def check():
            try:
                if not window.events.loaded.wait(20):
                    raise RuntimeError('UI did not load')
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if window.evaluate_js("document.querySelectorAll('#cityList option').length > 100"):
                        break
                    time.sleep(.2)
                else:
                    raise RuntimeError('City catalog did not load through the desktop bridge')
                result = window.evaluate_js("""(() => {
                    const input = document.querySelector('#city');
                    input.value = 'Нижний Новгород'; input.dispatchEvent(new Event('input'));
                    const cityCode = document.querySelector('#cityCode').value;
                    input.value = 'Краснодар'; input.dispatchEvent(new Event('input'));
                    return JSON.stringify({title:document.querySelector('h1').textContent,
                        cities:document.querySelectorAll('#cityList option').length,
                        testedCityCode:cityCode,start:!!document.querySelector('#start'),export:!!document.querySelector('#export'),
                        historyCounter:!!document.querySelector('#historySkipped')});
                })()""")
                if json.loads(result)['testedCityCode'] != 'n_novgorod':
                    raise RuntimeError('City selection failed in the packaged desktop UI')
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
