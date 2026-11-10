from urllib.parse import urlsplit


def messenger_kind(value):
    value = str(value or '').strip()
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
