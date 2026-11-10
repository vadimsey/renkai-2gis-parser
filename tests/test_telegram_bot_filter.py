import json

import pytest

from card_history import CardHistory
from hybrid_core import from_2gis
from parser_2gis.contact_urls import is_telegram_bot_url
from standalone_2gis.main import Api, classify


@pytest.mark.parametrize('url,expected', [
    ('https://t.me/Cleaning_Bot', True),
    ('https://telegram.me/cleanbot?start=order', True),
    ('tg://resolve?domain=CleanBot', True),
    ('https://t.me/order_service?startapp=booking', True),
    ('https://t.me/cleaning_company', False),
    ('https://t.me/+invitationbot', False),
    ('https://t.me/share/url?url=https://t.me/CleanBot', False),
    ('https://example.test/t.me/CleanBot', False),
    ('https://t.me.attacker.test/CleanBot', False),
    ('https://t.me/c/123/456', False),
    ('https://vk.com/CleanBot', False),
])
def test_bot_links_require_telegram_host_and_bot_target(url, expected):
    assert is_telegram_bot_url(url) is expected


def bot_row():
    return from_2gis({'id':'70000001000000001','name':'ТЕСТ Химчистка', 'contact_groups':[{'contacts':[
        {'type':'website','url':'https://t.me/CleanBot'},
        {'type':'website','url':'https://t.me/cleaning_owner'},
    ]}]}, 'Краснодар')


def test_bot_is_not_lost_when_a_normal_telegram_contact_follows_it():
    row = bot_row()
    assert row['telegram'] == 'https://t.me/cleaning_owner'
    assert row['telegram_bots'] == ['https://t.me/CleanBot']
    assert classify(row) == 'telegram_bot'


def test_bot_card_is_deleted_but_cannot_be_reimported_or_reopened(tmp_path):
    history = CardHistory(tmp_path / 'history.sqlite3')
    row = bot_row(); old = {**row, 'telegram_links':[], 'telegram_bots':[]}
    assert history.remember(old)
    assert not history.update_reviewed(row)
    assert history.count() == 0
    assert history.claim(row['source_id'], 'next') == 'seen'
    assert not history.remember(old)
    with history.connection() as db:
        assert db.execute('SELECT reason FROM exclusions').fetchone()[0] == 'telegram_bot'
        assert db.execute('SELECT COUNT(*) FROM cards').fetchone()[0] == 0


def test_new_bots_never_enter_cards_table(tmp_path):
    history = CardHistory(tmp_path / 'history.sqlite3')
    assert not history.remember(bot_row())
    assert history.count() == 0


def test_reopen_latest_run_excludes_bots_and_sites(tmp_path):
    folder = tmp_path / 'runs/old'; folder.mkdir(parents=True)
    bot = bot_row()
    plain = {**bot, 'source_id':'2', 'sourceUrl':'https://2gis.ru/firm/2','telegram_links':[],'telegram_bots':[]}
    site = {**plain, 'source_id':'3', 'sourceUrl':'https://2gis.ru/firm/3','websites':['https://example.test']}
    (folder/'collected.json').write_text(json.dumps([bot,plain,site]),encoding='utf-8')
    (folder/'worker.json').write_text('{"status":"done"}',encoding='utf-8')
    api = Api(tmp_path); state = api.state()
    assert api.run_dir == folder
    assert state['counts']['qualified'] == 1
    assert state['counts']['website'] == 1
    assert state['telegram_bots_excluded'] == 1
    assert api.history.count() == 2
