"""The same self-contained HTML cards and lead-data payload used by POTOK imports."""
import html
import json
from fastapi.responses import HTMLResponse
from company_import import url

LABELS={'new':'Не обработан','contacted':'Написал','interested':'Есть интерес','won':'Клиент','blocked':'Не писать'}

def record(row):
    row=dict(row)
    row['phones']=json.loads(row['phones'])
    row.pop('outreach', None)
    row.pop('skipped_message', None)
    return row

def furniture_review(a):
    if not a:
        return ''
    e = html.escape
    fields = [('Услуга', 'service'), ('Свежие работы', 'fresh_works'), ('Цена', 'pricing'),
              ('Запись и выезд', 'booking'), ('Фото, адрес и состав', 'transfer'),
              ('Что уже удобно', 'convenient'), ('Контакт', 'contact'), ('Возможная проблема', 'problem')]
    details = ''.join('<p><strong>'+e(label)+':</strong> '+e(a.get(key, ''))+'</p>' for label,key in fields)
    questions = ''.join('<li>'+e(q)+'</li>' for q in a.get('questions', []))
    evidence = ''.join('<li><a target="_blank" rel="noopener noreferrer" href="'+e(h,quote=True)+'">Источник ↗</a> '
                       +e(s.get('observation',''))+' <small>('+e(s.get('kind',''))+')</small></li>'
                       for s in a.get('evidence',[]) if (h:=url(s.get('url'))))
    return ('<section class="furniture-audit"><p><strong>Оценка: '+e(a.get('status',''))+'</strong> — '+e(a.get('reason',''))+'</p>'
            +'<p><strong>Черновик первого сообщения:</strong><br>'+e(a.get('message',''))+'</p>'
            +'<details><summary>Проверка, источники и вопросы</summary>'+details
            +'<p><strong>Вопросы перед адаптацией:</strong></p><ul>'+questions+'</ul>'
            +'<p><strong>Источники, проверено '+e(a.get('checked_at',''))+':</strong></p><ul>'+evidence+'</ul></details></section>')


def company_html(rows,*,title='RENKAI / КОМПАНИИ',subtitle='',filename='potok-companies.html'):
    e=html.escape;cards=[]
    for r in rows:
        links=' '.join('<a target="_blank" rel="noopener noreferrer" href="'+e(h,quote=True)+'">'+label+' ↗</a>' for label,h in [('Telegram',url(r['telegram'],'telegram')),('VK',url(r['vk'],'vk')),('Источник',url(r['source_url'])),('Демо-сайт',url(r['demo_url'])),('Исходный сайт',url(r.get('original_site','')))] if h)
        extra=' '.join('<a target="_blank" rel="noopener noreferrer" href="'+e(h,quote=True)+'">'+e(s.get('source','Источник'))+' ↗</a>' for s in r.get('sources',[]) if (h:=url(s.get('url'))) and h!=r['source_url'])
        links+=' '+extra
        phones=' · '.join(e(p) for p in json.loads(r['phones']))
        audit_status = r.get('furniture_audit', {}).get('status', 'не проверено')
        cards.append('<article data-furniture-status="'+e(audit_status,quote=True)+'"><small>'+e(LABELS.get(r['status'],r['status']))+'</small><h2>'+e(r['name'])+'</h2>'+('<p class="reason">'+e(r['skip_reason'])+'</p>' if r.get('skip_reason') else '')+'<p>'+e(r['city']+' · '+r['address'])+'</p><p>'+phones+'</p><nav>'+links+'</nav>'+('<p>'+e(r['audit_note'])+'</p>' if r.get('audit_note') else '')+furniture_review(r.get('furniture_audit'))+'</article>')
    document='''<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>RENKAI · Компании</title><style>body{background:#0a100d;color:#edf4ee;font:16px system-ui;max-width:1000px;margin:40px auto;padding:20px}h1{letter-spacing:.05em}article{background:#111c15;border:1px solid #294032;border-radius:16px;padding:24px;margin:16px 0}p,small{color:#a8baae}nav{display:flex;gap:16px;flex-wrap:wrap}a{color:#9fe5b7}</style><h1>'''+e(title)+'''</h1><p>'''+e(subtitle)+'''</p>'''+''.join(cards)+'''</html>'''
    payload=json.dumps({'companies':[record(r) for r in rows]},ensure_ascii=False).replace('&','\\u0026').replace('<','\\u003c').replace('>','\\u003e')
    document=document.replace('</html>','<script type="application/json" id="lead-data">'+payload+'</script></html>')
    return HTMLResponse(document,headers={'Content-Disposition':'attachment; filename="'+filename+'"','Cache-Control':'no-store'})
