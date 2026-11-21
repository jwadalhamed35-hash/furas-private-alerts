"""Manual-only trial: exactly five distinct active opportunities with GIS pins.
Does not change normal monitoring or its sent history. No automatic POST retry.
"""
from datetime import datetime, timezone
import logging
import math
import time
from urllib.parse import urlencode
import monitor as m

ENDPOINT = ('https://gisapps.balady.gov.sa/opportunities/proxy/proxy.ashx?'
            'https://gisOppertunities.momra.gov.sa/arcgis/rest/services/'
            'GISProjects/InvestmentView/MapServer/0/query?')

def main():
    now = datetime.now(timezone.utc)
    with m.session() as s:
        rows = m.fetch(s, now)
        candidates = sorted((a for a in rows if m.active(a,now) and m.category(a)),
                            key=lambda a: str(a['OPPORTUNITYID']))
        selected=[]; seen=set()
        for a in candidates:
            key=(a['OPPORTUNITYTYPE'],a['OPPORTUNITYID'])
            if key in seen: continue
            seen.add(key)
            result=m.get_json(s,ENDPOINT+urlencode(dict(f='json',objectIds=a['OBJECTID'],
                outFields=m.FIELDS,returnGeometry='true',outSR=4326)))
            features=result.get('features',[])
            if len(features)!=1: raise m.MonitorError('Geometry record missing or ambiguous')
            feature=features[0]; current=feature['attributes']; geo=feature.get('geometry') or {}
            if current.get('OPPORTUNITYID')!=a['OPPORTUNITYID']: raise m.MonitorError('Geometry identity mismatch')
            if not m.active(current,now) or not m.category(current): continue
            x,y=geo.get('x'),geo.get('y')
            if not all(isinstance(v,(int,float)) and math.isfinite(v) for v in (x,y)): continue
            if not (34<=x<=56 and 16<=y<=36): continue
            if result.get('spatialReference',{}).get('wkid')!=4326:
                raise m.MonitorError('Unexpected coordinate system')
            url='https://www.google.com/maps/search/?'+urlencode({'api':1,'query':f'{y:.7f},{x:.7f}'})
            selected.append((current,url))
            if len(selected)==5: break
    if len(selected)!=5: raise m.MonitorError('Fewer than five validated project coordinates; nothing sent')
    for a,url in selected:
        m.send_telegram('تجربة موقع المشروع على الخرائط (5 مشاريع فقط)\n'+m.format_message(a)+
            '\nخرائط Google: '+url+'\nمصدر النقطة: خريطة فرص الرسمية؛ يرجى مطابقتها مع كراسة المشروع.')
        logging.info('Accepted map trial for %s',a['OPPORTUNITYID'])
        time.sleep(3.2)
    logging.info('Map trial completed: 5 messages; normal sent history unchanged')

if __name__=='__main__':
    logging.basicConfig(level=logging.INFO,format='%(levelname)s %(message)s')
    try: main()
    except Exception as e:
        logging.error('%s',str(e) if isinstance(e,m.MonitorError) else type(e).__name__)
        raise SystemExit(1)
