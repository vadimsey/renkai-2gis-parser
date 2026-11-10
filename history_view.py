"""Read-only history browsing and portable, truthful exports."""
import csv
import html
import json
import os
import re
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime
from pathlib import Path

LABELS = {'qualified': 'Подходит · сайт не обнаружен', 'website': 'С сайтом · исключена'}


def query_history(history, query='', status='all', page=1, page_size=50, all_rows=False):
    from standalone_2gis.main import classify
    if status not in ('all', 'qualified', 'website'):
        raise ValueError('Неизвестный фильтр')
    page_size = max(1, min(100, int(page_size)))
    query = str(query).strip().casefold()[:250]
    digits = re.sub(r'\D', '', query)
    phone_query = len(digits) >= 4 and not re.search(r'[a-zа-яё]', query)
    rows, counts = [], {'all': 0, 'qualified': 0, 'website': 0}
    with history.connection() as db:
        saved = db.execute('SELECT card_id, first_seen, name, city, source_url, record_json FROM cards ORDER BY first_seen DESC, card_id').fetchall()
    for identity, first_seen, name, city, url, raw in saved:
        row = json.loads(raw)
        row.update(source_id=identity, sourceUrl=url, name=name, city=city)
        category = classify(row)
        if category not in LABELS:
            continue
        counts['all'] += 1
        counts[category] += 1
        if status != 'all' and status != category:
            continue
        haystack = ' '.join(str(row.get(k) or '') for k in ('name', 'city', 'address', 'phones', 'telegram', 'websites', 'sourceUrl')).casefold()
        if query and query not in haystack and not (phone_query and any(digits in re.sub(r'\D', '', p) for p in row.get('phones') or [])):
            continue
        row.update(_history_status=category, _first_seen=first_seen)
        rows.append(row)
    total = len(rows)
    pages = max(1, (total + page_size - 1) // page_size)
    page = max(1, min(int(page), pages))
    return {'rows': rows if all_rows else rows[(page-1)*page_size:page*page_size], 'total': total,
            'page': page, 'pages': pages, 'page_size': page_size, 'counts': counts}


def sites(row):
    value = row.get('websites') or (row.get('card_check') or {}).get('websites') or []
    return [value] if isinstance(value, str) else value


def cell(value):
    text = str(value or '')
    return "'" + text if text.lstrip().startswith(('=', '+', '-', '@')) else text


def export_history(rows, path, format):
    path = Path(path)
    if format not in ('html', 'csv', 'json'):
        raise ValueError('Неизвестный формат')
    if format == 'json':
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    elif format == 'csv':
        with path.open('w', encoding='utf-8-sig', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Название', 'Город', 'Адрес', 'Телефоны', 'Telegram', 'Сайты', 'Статус', 'Карточка 2ГИС', 'Сохранена'])
            for r in rows:
                writer.writerow([cell(v) for v in (r.get('name'), r.get('city'), r.get('address'), '; '.join(r.get('phones') or []), r.get('telegram'), '; '.join(sites(r)), LABELS[r['_history_status']], r.get('sourceUrl'), date(r))])
    else:
        esc = lambda x: html.escape(str(x or ''), quote=True)
        content = ''.join('<tr>' + ''.join('<td>' + esc(v) + '</td>' for v in (r.get('name'), r.get('city'), r.get('address'), '; '.join(r.get('phones') or []), r.get('telegram'), '; '.join(sites(r)), LABELS[r['_history_status']], r.get('sourceUrl'), date(r))) + '</tr>' for r in rows)
        payload = json.dumps(rows, ensure_ascii=False).replace('<', '\\u003c')
        path.write_text('<!doctype html><html lang="ru"><meta charset="utf-8"><title>RENKAI — база карточек</title><style>body{font:15px system-ui;margin:32px;color:#18181b}table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:12px;border-bottom:1px solid #ddd;vertical-align:top}p{color:#666}</style><h1>База карточек RENKAI</h1><p>Карточек: ' + str(len(rows)) + '. Статус отражает наличие сайта в сохранённой карточке 2ГИС.</p><table><thead><tr>' + ''.join('<th>'+v+'</th>' for v in ('Компания', 'Город', 'Адрес', 'Телефоны', 'Telegram', 'Сайты', 'Статус', '2ГИС', 'Сохранена')) + '</tr></thead><tbody>' + content + '</tbody></table><script type="application/json" id="lead-data">' + payload + '</script></html>', encoding='utf-8')
    return path


def date(row):
    return datetime.fromtimestamp(row.get('_first_seen', 0)).astimezone().isoformat(timespec='seconds')


def backup_history(history, destination):
    destination = Path(destination).resolve()
    source = history.path.resolve()
    if destination in (source, Path(str(source)+'-wal'), Path(str(source)+'-shm')):
        raise ValueError('Выберите другой файл: рабочую базу нельзя перезаписывать.')
    fd, temp = tempfile.mkstemp(prefix='.renkai-backup-', suffix='.sqlite3', dir=destination.parent)
    os.close(fd)
    try:
        with history.connection() as db, closing(sqlite3.connect(temp)) as target:
            db.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('Проверка копии базы не пройдена')
        os.replace(temp, destination)
    finally:
        Path(temp).unlink(missing_ok=True)
    return destination
