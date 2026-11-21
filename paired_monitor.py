"""Two-message Telegram alerts, stable project numbers and revision tracking."""
import hashlib
import json
import logging
import math
from pathlib import Path
import time
from datetime import datetime, timezone
from urllib.parse import urlencode
import monitor as m

PATH = Path('state/paired.json')
ENDPOINT = ('https://gisapps.balady.gov.sa/opportunities/proxy/proxy.ashx?'
 'https://gisOppertunities.momra.gov.sa/arcgis/rest/services/GISProjects/InvestmentView/MapServer/0/query?')


def key(a):
    return str(a['OPPORTUNITYTYPE'])+':'+str(a['OPPORTUNITYID'])


def decorate(feature, spatial_reference):
    a=feature['attributes'].copy()
    geometry=feature.get('geometry') or {}
    x,y=geometry.get('x'),geometry.get('y')
    good=(spatial_reference.get('wkid')==4326 and
          all(type(v) in (int,float) and math.isfinite(v) for v in (x,y)) and
          34<=x<=56 and 16<=y<=36)
    a['_maps']=('https://www.google.com/maps/search/?'+urlencode(
        {'api':1,'query':f'{y:.7f},{x:.7f}'})) if good else None
    return a


def details(s, object_ids):
    rows=[]
    for offset in range(0,len(object_ids),75):
        batch=object_ids[offset:offset+75]
        data=m.get_json(s,ENDPOINT+urlencode(dict(f='json',objectIds=','.join(map(str,batch)),
            outFields='*',returnGeometry='true',outSR=4326)))
        fs=data.get('features')
        if not isinstance(fs,list) or data.get('exceededTransferLimit'):
            raise m.MonitorError('Incomplete detailed source data')
        if {f['attributes']['OBJECTID'] for f in fs}!=set(batch):
            raise m.MonitorError('Tracked source record missing; refusing partial scan')
        rows.extend(decorate(f,data.get('spatialReference',{})) for f in fs)
    return rows


def fingerprint(a):
    # Include published business data and map pin; ignore system edit timestamps,
    # which can change without a change relevant to the investor.
    ignored={'OBJECTID','CREATED_USER','CREATED_DATE','LAST_EDITED_USER','LAST_EDITED_DATE'}
    data={k:v.strip() if isinstance(v,str) else v for k,v in a.items() if k not in ignored}
    return hashlib.sha256(json.dumps(data,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def date_text(value):
    t=m.timestamp(value)
    return t.astimezone(m.RIYADH).strftime('%Y-%m-%d %H:%M') if t else 'غير متاح في المصدر'


def messages(a, number, now, updated=False):
    status='فرصة سارية' if m.active(a,now) else 'تحديث حالة الفرصة — غير متاحة حالياً'
    heading=f'{status} | {m.category(a) or "تحديث النشاط"} (مشروع رقم {number})'
    if updated: heading+='\nتحديث بيانات المشروع'
    description=' '.join(str(a.get('OPPORTUNITYDESCRIPTION') or 'الوصف غير متاح').split())
    location=' — '.join(str(a[k]).strip() for k in ['REGIONNAME','CITYNAME','BALADYA'] if str(a.get(k) or '').strip()) or 'راجع تفاصيل الفرصة'
    common=f'{description}\nالموقع: {location}\nآخر موعد لبيع الكراسة: {date_text(a.get("LASTRFPSELLDATE"))} (السعودية)'
    maps=a.get('_maps') or 'الإحداثيات غير متاحة في المصدر؛ راجع كراسة المشروع'
    # Reuse the verified official details URL formatter, even for a closed record.
    kind='TempRental' if a['OPPORTUNITYTYPE']=='TemporaryRental' else a['OPPORTUNITYTYPE']
    from urllib.parse import quote
    url='https://furas.momah.gov.sa/opportunity/'+quote(str(a['OPPORTUNITYID']),safe='')+'?'+urlencode({'type':kind})
    full=f'{heading}\nرقم الفرصة: {a["OPPORTUNITYID"]}\n{common}\nالتفاصيل: {url}\nخرائط Google: {maps}\nمصدر النقطة: خريطة فرص الرسمية؛ يرجى مطابقتها مع كراسة المشروع.'
    short=f'{heading}\n{common}\nخرائط Google: {maps}'
    if not m.active(a,now):
        note=f'\nحالة المنصة: {a.get("STATUS")} / {a.get("OPPORTUNITYACTIVESTATUS")}'
        full+=note;short+=note
    if any(len(t)>4096 for t in (full,short)):
        raise m.MonitorError('Message exceeds Telegram limit; review source description')
    return [full,short]


def send(text):
    data=m.post_json(f'https://api.telegram.org/bot{m.required("TELEGRAM_BOT_TOKEN")}/sendMessage',
      {'chat_id':m.required('TELEGRAM_CHAT_ID'),'text':text,'link_preview_options':{'is_disabled':True}})
    if data.get('ok') is not True or not data.get('result',{}).get('message_id'):
        raise m.MonitorError('Telegram did not accept paired alert')
    return data['result']['message_id']


def process(rows,state,path,now,sender=send,delay=3.2):
    records=state['projects']; sent=0
    for a in sorted(rows,key=key):
        k=key(a); rec=records.get(k)
        if rec is None:
            if not m.active(a,now) or not m.category(a):continue
            rec={'number':state['next_number'],'object_id':a['OBJECTID'],'fingerprint':None,'pending':None,'message_ids':[],'was_active':True}
            state['next_number']+=1;records[k]=rec
        digest=fingerprint(a)
        is_active=m.active(a,now)
        # Date passage is observed too; send closure once, without repeating it.
        revision=digest+(':active' if is_active else ':closed')
        pending=rec.get('pending')
        if pending is None:
            if rec['fingerprint']==revision:continue
            if sent>=100:break
            pending={'revision':revision,'messages':messages(a,rec['number'],now,rec['fingerprint'] is not None),'accepted':0}
            rec['pending']=pending
            m.save_state(path,state)
        # Resume a partially accepted pair before handling newer revisions next hour.
        for index in range(pending['accepted'],2):
            message_id=sender(pending['messages'][index])
            rec['message_ids'].append(message_id)
            pending['accepted']=index+1
            m.save_state(path,state)
            time.sleep(delay)
        rec['fingerprint']=pending['revision'];rec['pending']=None
        rec['object_id']=a['OBJECTID'];rec['was_active']=is_active
        m.save_state(path,state);sent+=1
    return sent


def run():
    if PATH.exists():
        try:state=json.loads(PATH.read_text())
        except (OSError,ValueError):raise m.MonitorError('Cannot read paired alert history') from None
        if state.get('version')!=2 or not isinstance(state.get('projects'),dict) or not isinstance(state.get('next_number'),int):
            raise m.MonitorError('Invalid paired history schema')
    else:
        # Explicit migration is allowed only from the existing checked-in v1 history.
        old=json.loads(Path('state/sent.json').read_text())
        if old.get('version')!=1:raise m.MonitorError('Missing legacy history for first migration')
        state={'version':2,'next_number':1,'projects':{}}
    recipient_hash=hashlib.sha256(m.required('TELEGRAM_CHAT_ID').encode()).hexdigest()
    if state.get('recipient',recipient_hash)!=recipient_hash:raise m.MonitorError('Recipient changed; review history before sending')
    state['recipient']=recipient_hash
    now=datetime.now(timezone.utc)
    with m.session() as s:
        current=m.fetch(s,now)
        ids={a['OBJECTID'] for a in current if m.active(a,now) and m.category(a)}
        ids.update(rec['object_id'] for rec in state['projects'].values())
        rows=details(s,sorted(ids))
    logging.info('Validated detailed records: %d',len(rows))
    count=process(rows,state,PATH,now)
    logging.info('Accepted project pairs: %d',count)

if __name__=='__main__':
    logging.basicConfig(level=logging.INFO,format='%(levelname)s %(message)s')
    try:run()
    except Exception as error:
        logging.error('%s',str(error) if isinstance(error,m.MonitorError) else type(error).__name__)
        raise SystemExit(1)
