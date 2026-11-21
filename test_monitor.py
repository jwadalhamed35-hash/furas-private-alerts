from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import monitor as m
NOW=datetime(2026,11,20,12,tzinfo=timezone.utc)
MS=NOW.timestamp()*1000

def row(identity='A'):
    return dict(OPPORTUNITYID=identity,OPPORTUNITYTYPE='Investment',OPPORTUNITYDESCRIPTION='إنشاء وتشغيل محطة وقود',STATUS='Announced',OPPORTUNITYACTIVESTATUS='Announced',STARTDATE=MS-1000,ENDDATE=MS+100000,LASTRFPSELLDATE=MS+100000)

class Tests(unittest.TestCase):
    def test_dates_and_status(self):
        self.assertTrue(m.active(row(),NOW))
        for key,value in [('LASTRFPSELLDATE',MS),('ENDDATE',MS-1),('STATUS','Cancelled'),('OPPORTUNITYACTIVESTATUS','Cancelled'),('STARTDATE',MS+1),('LASTRFPSELLDATE',None),('LASTRFPSELLDATE','unknown')]:
            a=row();a[key]=value
            self.assertFalse(m.active(a,NOW),(key,value))

    def test_classification(self):
        for title,expected in [('إنشاء فندق',True),('محطات الوقود',True),('محطة محروقات فئة أ',True),('شقق فندقية',True),('مبنى تجاري بجوار فندق النخلة - تجاري - مبنى سكني',False),('محطة كهرباء',False),('محل مقابل محطة وقود',False)]:
            self.assertEqual(bool(m.category({'OPPORTUNITYDESCRIPTION':title})),expected)

    def test_partial_failure_and_dedup(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'state.json';state={'version':1,'sent':{}};calls=[]
            def sender(message):
                if len(calls)==1:raise m.MonitorError('rejected')
                calls.append(message)
            with self.assertRaises(m.MonitorError):m.process([row('A'),row('B')],state,path,'telegram','one',sender,NOW,100,0)
            restored=json.loads(path.read_text());sent=[]
            self.assertEqual(m.process([row('A'),row('B')],restored,path,'telegram','one',sent.append,NOW,100,0),1)
            self.assertIn('رقم الفرصة: B',sent[0])
            self.assertEqual(m.process([row('A')],restored,path,'telegram','two',sent.append,NOW,100,0),1)

    def test_backlog_limit(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'state.json';state={'version':1,'sent':{}};sent=[]
            for _ in range(2):self.assertEqual(m.process([row('A'),row('B')],state,path,'telegram','one',sent.append,NOW,1,0),1)

    def test_whatsapp_group_template_payload(self):
        env={'WA_API_VERSION':'v27.0','WA_PHONE_NUMBER_ID':'123','WA_GROUP_ID':'group-id','WA_ACCESS_TOKEN':'test-only','WA_TEMPLATE_NAME':'furas_alert'}
        with patch.dict(m.os.environ,env), patch.object(m,'post_json',return_value={'messages':[{'id':'accepted'}]}) as post:
            m.send_whatsapp('فرصة\nوصف ورابط')
            payload=post.call_args.args[1]
            self.assertEqual(payload['recipient_type'],'group')
            self.assertEqual(payload['to'],'group-id')
            self.assertEqual(payload['type'],'template')
            self.assertNotIn('\n',payload['template']['components'][0]['parameters'][0]['text'])

    def test_batches_and_incomplete_fetch(self):
        cfg={'gisLayerUrl':{'investmentMapServerInvestmentP':'https://gisOppertunities.momra.gov.sa/arcgis/rest/services/GISProjects/InvestmentView/MapServer'},'gisLayerIndex':{'investmentMapServerInvestmentP':0},'proxy':{'proxyUrl':'https://gisapps.balady.gov.sa/opportunities/proxy/proxy.ashx'}}
        def feature(i):
            a={k:None for k in m.FIELDS.split(',')};a['OBJECTID']=i
            return {'attributes':a}
        pages=[cfg,{'objectIds':list(range(201))},{'features':[feature(i) for i in range(100)]},{'features':[feature(i) for i in range(100,200)]},{'features':[feature(200)]}]
        with patch.object(m,'get_json',side_effect=pages):self.assertEqual(len(m.fetch(None,NOW)),201)
        pages[-1]={'features':[]}
        with patch.object(m,'get_json',side_effect=pages):
            with self.assertRaises(m.MonitorError):m.fetch(None,NOW)

if __name__=='__main__':unittest.main()
