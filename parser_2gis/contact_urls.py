from urllib.parse import urlsplit, parse_qs, unquote


def is_telegram_bot_url(value):
    value = str(value or '').strip()
    try:
        parsed = urlsplit(value if '://' in value else 'https://' + value)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.scheme.casefold() == 'tg' and parsed.netloc.casefold() == 'resolve':
            username = (query.get('domain') or [''])[0]
        elif (parsed.hostname or '').casefold() in ('t.me', 'telegram.me', 'www.t.me', 'www.telegram.me'):
            parts = unquote(parsed.path).strip('/').split('/')
            username = parts[1] if parts[0] == 's' and len(parts) > 1 else parts[0]
        else:
            return False
        if not username or username.startswith('+') or username.casefold() in ('share','joinchat','addstickers','addemoji','iv','c','s'):
            return False
        if not all(c.isascii() and (c.isalnum() or c == '_') for c in username):
            return False
        return username.casefold().endswith('bot') or any(k in query for k in ('start','startgroup','startapp','startattach'))
    except (ValueError, IndexError):
        return False


def telegram_bot_urls(row):
    check = row.get('card_check') or {}
    values = [row.get('telegram'), *(row.get('telegram_links') or []),
              *(row.get('telegram_bots') or []), *(row.get('websites') or []),
              *(check.get('telegram_bots') or []), *(check.get('websites') or [])]
    return list(dict.fromkeys(str(v) for v in values if is_telegram_bot_url(v)))


def messenger_kind(value):
    value = str(value or '').strip()
    if value.casefold().startswith('tg://resolve?'):
        return 'telegram'
    try:
        host = (urlsplit(value if '://' in value else 'https://' + value).hostname or '').casefold()
    except ValueError:
        return ''
    for kind, domains in (
        ('telegram', ('t.me', 'telegram.me')),
        ('vk', ('vk.com', 'vk.ru')),
        ('messenger', ('max.ru', 'wa.me', 'whatsapp.com')),
    ):
        if any(host == domain or host.endswith('.' + domain) for domain in domains):
            return kind
    return ''
