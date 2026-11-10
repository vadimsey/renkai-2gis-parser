"""Persistent, local 2GIS card history shared by desktop runs."""
import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit


def default_history_path():
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'RENKAI-2GIS' / 'history.sqlite3'


def card_id(row):
    try:
        url = urlsplit(str(row.get('sourceUrl') or ''))
    except ValueError:
        return None
    match = re.search(r'/firm/(\d+)', url.path) if url.hostname == '2gis.ru' else None
    if match:
        return match.group(1)
    check = row.get('card_check')
    if row.get('source') != '2gis' and (not isinstance(check, dict) or check.get('scope') != '2gis_card'):
        return None
    value = str(row.get('source_id') or '').split('_')[0]
    return value if value.isdigit() else None


class CardHistory:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS cards (
                    card_id TEXT PRIMARY KEY,
                    first_seen REAL NOT NULL,
                    name TEXT NOT NULL,
                    city TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    record_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS claims (
                    card_id TEXT PRIMARY KEY,
                    owner TEXT NOT NULL,
                    expires REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS imports (
                    path TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS exclusions (
                    card_id TEXT PRIMARY KEY,
                    reason TEXT NOT NULL,
                    excluded_at REAL NOT NULL,
                    evidence_url TEXT NOT NULL
                );
            ''')
        self.purge_bots()

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def count(self):
        with self.connection() as db:
            return db.execute('SELECT COUNT(*) FROM cards').fetchone()[0]

    def claim(self, identity, owner, *, now=None, lease=300):
        """Reserve before navigation. Failed/crashed reads never become seen cards."""
        now = time.time() if now is None else now
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM cards WHERE card_id=?', (identity,)).fetchone():
                return 'seen'
            if db.execute('SELECT 1 FROM exclusions WHERE card_id=?', (identity,)).fetchone():
                return 'seen'
            claim = db.execute('SELECT owner, expires FROM claims WHERE card_id=?', (identity,)).fetchone()
            if claim and claim[1] > now:
                return 'busy'
            db.execute('INSERT OR REPLACE INTO claims VALUES (?,?,?)', (identity, owner, now + lease))
        return 'new'

    @staticmethod
    def _insert(db, row):
        identity = card_id(row)
        if not identity or not row.get('name'):
            return False
        from parser_2gis.contact_urls import telegram_bot_urls
        bots = telegram_bot_urls(row)
        if bots:
            db.execute('INSERT OR REPLACE INTO exclusions VALUES (?,?,?,?)', (identity, 'telegram_bot', time.time(), bots[0]))
            db.execute('DELETE FROM cards WHERE card_id=?', (identity,))
            db.execute('DELETE FROM claims WHERE card_id=?', (identity,))
            return False
        if db.execute('SELECT 1 FROM exclusions WHERE card_id=?', (identity,)).fetchone():
            return False
        observed = row.get('_observed_at')
        observed = observed if isinstance(observed, (int, float)) else time.time()
        return db.execute('INSERT OR IGNORE INTO cards VALUES (?,?,?,?,?,?)', (
            identity, observed, str(row['name']), str(row.get('city') or ''),
            str(row.get('sourceUrl') or 'https://2gis.ru/firm/' + identity),
            json.dumps(row, ensure_ascii=False))).rowcount == 1

    def remember(self, row):
        with self.connection() as db:
            added = self._insert(db, row)
            identity = card_id(row)
            if identity:
                db.execute('DELETE FROM claims WHERE card_id=?', (identity,))
        return added

    def purge_bots(self):
        removed = 0
        from parser_2gis.contact_urls import telegram_bot_urls
        with self.connection() as db:
            for identity, raw in db.execute('SELECT card_id,record_json FROM cards').fetchall():
                try:
                    row = json.loads(raw)
                    if telegram_bot_urls(row):
                        self._insert(db, row)
                        removed += 1
                except (ValueError, TypeError):
                    continue
        return removed

    def update_reviewed(self, row):
        """Replace stale contact data after checking the original card again."""
        identity = card_id(row)
        if not identity:
            return False
        with self.connection() as db:
            from parser_2gis.contact_urls import telegram_bot_urls
            if telegram_bot_urls(row):
                self._insert(db, row)
                return False
            if db.execute('SELECT 1 FROM exclusions WHERE card_id=?', (identity,)).fetchone():
                return False
            db.execute('UPDATE cards SET name=?,city=?,source_url=?,record_json=? WHERE card_id=?',
                       (row['name'], row.get('city',''), row.get('sourceUrl',''), json.dumps(row,ensure_ascii=False), identity))
            self._insert(db, row)
        return True

    def release(self, owner):
        with self.connection() as db:
            db.execute('DELETE FROM claims WHERE owner=?', (owner,))

    def import_runs(self, roots):
        """Incrementally seed history from existing collected.json, never exports."""
        imported = 0
        for root in roots:
            root = Path(root)
            if not root.is_dir():
                continue
            for path in root.rglob('collected.json'):
                try:
                    stat = path.stat()
                    fingerprint = '%s:%s' % (stat.st_mtime_ns, stat.st_size)
                    key = str(path.resolve())
                    with self.connection() as db:
                        previous = db.execute('SELECT fingerprint FROM imports WHERE path=?', (key,)).fetchone()
                    if previous and previous[0] == fingerprint:
                        continue
                    if stat.st_size > 64_000_000:
                        continue
                    rows = json.loads(path.read_text(encoding='utf-8-sig'))
                    if not isinstance(rows, list):
                        continue
                    with self.connection() as db:
                        for row in rows:
                            if isinstance(row, dict):
                                imported += int(self._insert(db, row))
                                # Earlier runs merged branches by phone. Keep all
                                # original 2GIS IDs, not only the merged row's ID.
                                for source in row.get('sources') or []:
                                    if not isinstance(source, dict):
                                        continue
                                    alternate = {**row, 'sourceUrl':source.get('url', '')}
                                    if card_id(alternate) and card_id(alternate) != card_id(row):
                                        alternate.update(history_imported_from_merged=True,
                                                         history_origin_url=row.get('sourceUrl'))
                                        imported += int(self._insert(db, alternate))
                        db.execute('INSERT OR REPLACE INTO imports VALUES (?,?)', (key, fingerprint))
                except (OSError, ValueError, TypeError):
                    # A running old worker can be between writes. Retry next start.
                    continue
        return imported
