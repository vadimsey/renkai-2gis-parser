"""Shared, source-independent records for the RENKAI hybrid collector."""
import hashlib
import json
import re
import time
import unicodedata
from urllib.parse import urlsplit, urlunsplit

SOURCES = {'2gis': '2ГИС', 'yandex': 'Яндекс Карты', 'google': 'Google Maps', 'avito': 'Авито', 'vk': 'VK'}
DEFAULT_SOURCES = list(SOURCES)
PHONE = re.compile(r'(?<!\d)(?:\+7|8)[\s(.-]*\d{3}[\s).-]*\d{3}[\s.-]*\d{2}[\s.-]*\d{2}(?!\d)')
LINK = re.compile(r'https?://[^\s<>"\]]+', re.I)

def fold(value):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', str(value or '')).casefold().replace('ё', 'е')).strip()

def clean_url(value):
    try:
        parts=urlsplit(str(value or '').strip())
        if parts.scheme not in ('https','http') or not parts.hostname or parts.username or parts.password:return ''
        path=parts.path.rstrip('/')
        if parts.hostname.lower() in ('yandex.ru','yandex.com','www.yandex.ru','www.yandex.com'):
            match=re.match(r'(/maps/org/[^/]+/\d+)(?:/|$)',path)
            if match:path=match.group(1)
        return urlunsplit((parts.scheme,parts.netloc,path,'', ''))
    except ValueError:return ''

def number(value):
    digits=re.sub(r'\D','',str(value or ''))
    if len(digits)==11 and digits.startswith('8'):digits='7'+digits[1:]
    if len(digits)==10:digits='7'+digits
    return '+'+digits if 7<=len(digits)<=15 else ''

def normalize(raw, source, city=''):
    name=str(raw.get('name') or raw.get('title') or '').strip()[:500]
    if not name:return None
    link=clean_url(raw.get('sourceUrl') or raw.get('source_url') or raw.get('url'))
    if not link:return None
    phones=raw.get('phones') or []
    if isinstance(phones,str):phones=[phones]
    websites=raw.get('websites') or []
    if isinstance(websites,str):websites=[websites]
    phones=list(dict.fromkeys(p for p in map(number,phones) if p))
    websites=list(dict.fromkeys(p for p in map(clean_url,websites) if p))
    rubrics=[r.get('name','') if isinstance(r,dict) else str(r) for r in raw.get('rubrics',[])]
    return {**raw,'id':source+':'+str(raw.get('source_id') or hashlib.sha256(link.encode()).hexdigest()[:24]),
            'name':name,'source':source,'sourceUrl':link,'city':str(raw.get('city') or city)[:150],
            'address':re.sub(r'[\ue000-\uf8ff]', '',str(raw.get('address') or '')).strip()[:1000],'phones':phones,'websites':websites,
            'telegram':business_social(raw.get('telegram')),'vk':business_social(raw.get('vk')),
            'rubrics':rubrics,'sources':[{'source':source,'url':link}],
            '_observed_at':time.time(),'card_check':raw.get('card_check') or {'scope':'public_card','complete':False}}


def business_social(value):
    link=clean_url(value)
    # Footer links promote the map platform; they are not business contacts.
    account=urlsplit(link).path.strip('/').split('/')[0].lower() if link else ''
    if account in {'mapsyandex','yandexmaps','googlemaps','2gis','avito'}:return ''
    return link

class Merger:
    """Merge across providers, including a bridge that connects two earlier rows."""
    def __init__(self, excluded=()):
        self.rows=[];self.excluded=set();self.seen=0
        for row in excluded:self.excluded.update(self.keys(row))
    @staticmethod
    def keys(row):
        keys={'url:'+u for u in [clean_url(row.get('sourceUrl') or row.get('source_url')), *[clean_url(s.get('url')) for s in row.get('sources',[])]] if u}
        phones=row.get('phones') or []
        if isinstance(phones,str):
            try:phones=json.loads(phones)
            except ValueError:phones=[]
        keys.update('phone:'+p for p in map(number,phones) if p)
        if row.get('name') and row.get('address'):
            keys.add('place:'+fold('|'.join([row['name'],row.get('city',''),row['address']])))
        return keys
    def add(self,row):
        if not row:return False
        self.seen+=1;keys=self.keys(row)
        if keys&self.excluded:return False
        matches=[r for r in self.rows if self.keys(r)&keys]
        if not matches:self.rows.append(row);return True
        target=matches[0]
        for extra in [*matches[1:],row]:
            for field in ['phones','websites','rubrics','sources']:
                target[field]=list({json.dumps(x,sort_keys=True,ensure_ascii=False):x for x in [*target.get(field,[]),*extra.get(field,[])]}.values())
            for field in ['telegram','vk','address']:
                if not target.get(field) and extra.get(field):target[field]=extra[field]
            if extra.get('card_check',{}).get('complete'):target['card_check']=extra['card_check']
            if extra in self.rows and extra is not target:self.rows.remove(extra)
        return False

def from_2gis(item,city=''):
    identity=str(item.get('id') or '').split('_')[0]
    if not identity or not item.get('name'):return None
    contacts=[c for g in item.get('contact_groups',[]) or [] for c in g.get('contacts',[]) or [] if isinstance(c,dict)]
    phones=[c.get('value','') for c in contacts if c.get('type') in ('phone','tel')]
    websites=[];vk='';telegram=''
    for c in contacts:
        u=c.get('url') or c.get('value') or ''
        if 'vk.com/' in u or 'vk.ru/' in u:vk=u
        elif 't.me/' in u or 'telegram.me/' in u:telegram=u
        elif c.get('type')=='website' and u:
            # 2GIS can return a displayed domain without an explicit scheme.
            candidate=str(u).strip()
            if not candidate.startswith(('http://', 'https://')):candidate='https://'+candidate
            websites.append(candidate)
    complete=bool(contacts)
    return normalize({'source_id':identity,'name':item['name'],'city':city,'address':item.get('address_name',''),
        'sourceUrl':'https://2gis.ru/firm/'+identity,'phones':phones,'websites':websites,'vk':vk,'telegram':telegram,
        'rubrics':item.get('rubrics',[]),'card_check':{'scope':'2gis_card','complete':complete,'websites':websites,
        'has_website':any(c.get('type')=='website' and not any(x in str(c.get('url') or c.get('value') or '').lower() for x in ('t.me/', 'telegram.me/', 'vk.com/', 'vk.ru/')) for c in contacts),
        'online':any('booking' in str(c).lower() or 'yclients' in str(c).lower() for c in contacts)}},'2gis',city)

def archive_records(content, filename, query='', city=''):
    """Offline Telegram JSON and WhatsApp text, the chat-miner data workflow.

    Only contacts actually written in matching messages become candidates;
    a message author is never treated as proof of ownership of a business.
    """
    if len(content.encode('utf-8'))>20_000_000:raise ValueError('Файл больше 20 МБ')
    messages=[]
    if filename.lower().endswith('.json'):
        data=json.loads(content.lstrip('\ufeff'))
        if not isinstance(data,dict) or not isinstance(data.get('messages'),list):raise ValueError('Нужен экспорт одного чата Telegram в JSON')
        for m in data['messages']:
            if not isinstance(m,dict) or m.get('type','message')!='message':continue
            text=m.get('text','')
            if isinstance(text,list):text=''.join(x if isinstance(x,str) else str(x.get('text','')) for x in text if isinstance(x,(str,dict)))
            if isinstance(text,str):messages.append((str(m.get('from') or data.get('name') or 'Контакт из переписки'),text))
    elif filename.lower().endswith('.txt'):
        # Supports both Android and iOS exports; subsequent lines belong to the
        # preceding message. System lines without an author are ignored.
        pattern=re.compile(r'^\[?\d{1,4}[./-]\d{1,2}[./-]\d{1,4},?\s+\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AP]M)?\]?\s*(?:-\s*)?([^:]{1,100}):\s*(.*)$',re.I)
        for line in content.replace('\u200e','').splitlines():
            match=pattern.match(line)
            if match:messages.append((match[1],match[2]))
            elif messages:messages[-1]=(messages[-1][0],messages[-1][1]+'\n'+line)
    else:raise ValueError('Поддерживаются Telegram JSON и WhatsApp TXT')
    merger=Merger();words=[fold(w) for w in query.split() if len(w)>2]
    for author,text in messages[:100000]:
        if words and not any(w in fold(text) for w in words):continue
        links=[clean_url(u.rstrip('.,;)')) for u in LINK.findall(text)];links=[u for u in links if u]
        phones=[number(x) for x in PHONE.findall(text)]
        if not phones and not links:continue
        telegram=next((u for u in links if (urlsplit(u).hostname or '') in ('t.me','telegram.me')),'')
        vk=next((u for u in links if (urlsplit(u).hostname or '') in ('vk.com','vk.ru')),'')
        source=telegram or vk or (links[0] if links else '')
        # The local export is evidence, not a fabricated public source URL.
        record={'id':'archive:'+hashlib.sha256(text.encode()).hexdigest()[:24],'name':author,'city':city,'address':'',
                'phones':phones,'telegram':telegram,'vk':vk,'sourceUrl':source,'source':'archive','sources':[],
                'websites':[u for u in links if u not in (telegram,vk)],'rubrics':[],
                'observation':'Из вашей выгрузки переписки. Принадлежность контакта компании требует проверки. '+text[:600]}
        merger.add(record)
    return {'records':merger.rows,'messages':len(messages),'matched':len(merger.rows)}
