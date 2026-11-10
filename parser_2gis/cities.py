"""Canonical search locations from the upstream 2GIS city catalog."""
import json
import re
from functools import lru_cache
from urllib.parse import quote

from .paths import data_path


def _key(value):
    return re.sub(r'\s+', ' ', str(value).strip().casefold().replace('ё', 'е').replace('–', '-').replace('—', '-'))


@lru_cache(maxsize=1)
def russian_cities():
    with (data_path() / 'cities.json').open(encoding='utf-8-sig') as stream:
        rows = json.load(stream)
    return sorted([dict(row) for row in rows if row.get('country_code') == 'ru'], key=lambda row: row['name'])


def resolve_city(name, code=''):
    """Resolve by city name; never reuse another city's stale UI code."""
    code = str(code or '').strip().lower()
    if code and not re.fullmatch(r'[a-z0-9_-]{2,60}', code):
        raise ValueError('Некорректный код города 2ГИС.')
    for row in russian_cities():
        if _key(name) in (_key(row['name']), row['code']):
            return dict(row)
    raise ValueError('Выберите город из справочника 2ГИС: код другого города использоваться не будет.')


def furniture_search_url(city):
    return 'https://2gis.' + city['domain'] + '/' + city['code'] + '/search/' + quote('химчистка мебели')
