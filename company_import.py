"""Parse data, never execute imported scripts or load remote resources."""
import csv
import hashlib
import io
import json
import re
import unicodedata
from html.parser import HTMLParser
from urllib.parse import urlsplit

LIMIT=10000

def compact(value,max_length=600):
    return str(value or '').strip()[:max_length]

def fold(value):
    return re.sub(r'\s+',' ',unicodedata.normalize('NFKC',str(value or '')).casefold().replace('ё','е')).strip()

def url(value,kind=''):
    value=compact(value,2000)
    if not value:return ''
    if kind=='telegram' and re.fullmatch(r'@[A-Za-z][\w]{3,31}',value):value='https://t.me/'+value[1:]
    try:
        parts=urlsplit(value)
        if parts.scheme not in ('https','http') or not parts.hostname or parts.username or parts.password:return ''
        host=parts.hostname.lower()
        if kind=='telegram' and (host not in ('t.me','telegram.me','www.t.me') or not re.fullmatch(r'/[A-Za-z][A-Za-z0-9_]{3,31}/?',parts.path)):return ''
        if kind=='vk' and host not in ('vk.com','www.vk.com','m.vk.com','vk.ru','www.vk.ru'):return ''
        return value
    except ValueError:return ''

def phone(value):
    digits=re.sub(r'\D','',str(value or ''))
    if len(digits)==11 and digits.startswith('8'):digits='7'+digits[1:]
    return '+'+digits if 7<=len(digits)<=15 else ''

def normalize(row,city=''):
    if not isinstance(row,dict):raise ValueError('Ожидался объект компании')
    def get(*keys):return next((row[k] for k in keys if row.get(k) not in (None,'',[])), '')
    name=compact(get('name','Название','Клиника','Компания','Название компании'),250)
    if not name:raise ValueError('Нет названия компании')
    phones=get('phones','phone','Телефон','Телефоны')
    if not isinstance(phones,list):phones=re.split(r'[;,\n]|\s/\s',str(phones))
    phones=list(dict.fromkeys(p for p in map(phone,phones) if p))[:15]
    city=compact(get('city','Город') or city,150)
    address=compact(get('address','Адрес'),1000)
    source=url(get('sourceUrl','source_url','twoGisUrl','two_gis_url','2ГИС','2GIS','url'))
    firm=re.search(r'(?:2gis\.(?:ru|com|kz|kg|uz|ae))/[^?#]*firm/(\d+)',source)
    source_id=firm.group(1) if firm else compact(get('id','source_id'),100)
    identity='2gis:'+source_id if source_id and source_id.isdigit() and len(source_id)>10 else 'local:'+hashlib.sha256(fold('|'.join([name,city,address or (phones[0] if phones else '')])).encode()).hexdigest()
    status=fold(get('status','Статус'))
    status={'contacted':'contacted','done':'contacted','написал':'contacted','отправлено':'contacted','связались':'contacted','interested':'interested','есть интерес':'interested','won':'won','клиент':'won','blocked':'blocked','rejected':'blocked','excluded':'blocked','отказ':'blocked','не писать':'blocked'}.get(status,'new')
    data=dict(identity_key=identity,name=name,city=city,address=address,phones=phones,
              telegram=url(get('telegram','tg','Telegram'),'telegram'),vk=url(get('vk','ВКонтакте','VK'),'vk'),source_url=source,
              demo_url=url(get('preparedSite','demo_url','prepared_site','siteUrl','demoSite','Сайт','site')),
              outreach=compact(get('outreach','outreachText','message','Аутрич'),12000),
              observation=compact(get('observation','Проверка записи','reason','evidence'),2500),notes=compact(get('notes','Заметки'),6000),status=status)
    data['search_text']=fold(' '.join([name,city,address,*phones,data['telegram'],data['vk']]))
    return data

class Document(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.scripts=[];self.script=None;self.cards=[];self.card=None;self.depth=0;self.capture=None
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=='script':self.script={'attrs':attrs,'text':''};return
        if self.script is not None:return
        classes=attrs.get('class','').split()
        if self.card is None and (tag=='article' or any(c in ('card','lead-card','company-card') for c in classes)):
            self.card={'phones':[]};self.depth=1
        elif self.card is not None and tag not in ('br','img','input','hr','meta','link','wbr'):self.depth+=1
        if self.card is None:return
        if tag in ('h2','h3') and not self.card.get('name'):self.capture='name'
        elif 'address' in classes:self.capture='address'
        elif tag=='textarea':self.capture='outreach'
        elif tag=='a':
            href=attrs.get('href','')
            if href.startswith('tel:'):self.card['phones'].append(href[4:])
            elif url(href,'telegram'):self.card['telegram']=href
            elif url(href,'vk'):self.card['vk']=href
            elif '2gis.' in href:self.card['sourceUrl']=href
            elif '/s/' in href:self.card['preparedSite']=href
    def handle_endtag(self,tag):
        if tag=='script' and self.script is not None:self.scripts.append(self.script);self.script=None;return
        if self.script is not None:return
        if tag in ('h2','h3','textarea') or tag in ('p','div') and self.capture=='address':self.capture=None
        if self.card is not None:
            self.depth-=1
            if self.depth<=0:
                if self.card.get('name'):self.cards.append(self.card)
                self.card=None;self.capture=None
    def handle_data(self,data):
        if self.script is not None:self.script['text']+=data
        elif self.card is not None and self.capture:self.card[self.capture]=self.card.get(self.capture,'')+data

def parse(content,filename,city=''):
    if len(content.encode('utf-8'))>20_000_000:raise ValueError('Файл больше 20 МБ. Разделите подборку на несколько файлов.')
    rows=None;method=''
    def unpack(value):
        if isinstance(value,list):return value
        if isinstance(value,dict):
            for key in ('leads','companies','items','records'):
                if isinstance(value.get(key),list):return value[key]
        return None
    if filename.lower().endswith('.csv'):
        sample=content.lstrip('\ufeff')
        try:dialect=csv.Sniffer().sniff(sample[:5000],delimiters=',;\t')
        except csv.Error:raise ValueError('Не удалось прочитать CSV. Используйте заголовки столбцов и разделитель ; или ,')
        rows=list(csv.DictReader(io.StringIO(sample),dialect=dialect));method='CSV'
    elif filename.lower().endswith('.json'):
        rows=unpack(json.loads(content.lstrip('\ufeff')));method='JSON'
    else:
        doc=Document();doc.feed(content)
        for script in doc.scripts:
            if script['attrs'].get('type')=='application/json':
                try:rows=unpack(json.loads(script['text']))
                except (ValueError,TypeError):continue
                if rows is not None:method='JSON внутри HTML';break
        if rows is None:
            decoder=json.JSONDecoder()
            for script in doc.scripts:
                match=re.search(r'(?:const|let|var)\s+(?:leads|companies|data)\s*=\s*',script['text'])
                if not match:continue
                try:value,_=decoder.raw_decode(script['text'][match.end():]);rows=unpack(value)
                except ValueError:continue
                if rows is not None:method='Данные подборки HTML';break
        if rows is None and doc.cards:rows=doc.cards;method='Карточки HTML'
    if rows is None:raise ValueError('Не удалось распознать компании. Поддерживаются наши HTML-подборки, JSON с leads/companies и CSV с заголовками name, phone, telegram, vk, address.')
    if not rows:raise ValueError('В файле нет компаний')
    if len(rows)>LIMIT:raise ValueError('За один импорт — до 10 000 компаний')
    records=[];issues=[];seen=set()
    for i,row in enumerate(rows):
        try:
            record=normalize(row,city)
            if record['identity_key'] in seen:issues.append(f'Строка {i+1}: повтор внутри файла');continue
            seen.add(record['identity_key']);records.append(record)
        except ValueError as error:issues.append(f'Строка {i+1}: {error}')
    if not records:raise ValueError('Ни одной компании с названием не найдено')
    return {'records':records,'issues':issues,'input_count':len(rows),'method':method}
