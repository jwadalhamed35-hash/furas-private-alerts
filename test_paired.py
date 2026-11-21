import sys,tempfile,unittest
from pathlib import Path
from datetime import datetime,timezone
sys.path.insert(0,str(Path(__file__).resolve().parent))
import paired_monitor as p
NOW=datetime(2026,11,21,tzinfo=timezone.utc);MS=NOW.timestamp()*1000

def row():return dict(OBJECTID=1,OPPORTUNITYID='ABC',OPPORTUNITYTYPE='Investment',OPPORTUNITYDESCRIPTION='محطة وقود',STATUS='Announced',OPPORTUNITYACTIVESTATUS='Announced',STARTDATE=MS-1,ENDDATE=MS+100000,LASTRFPSELLDATE=MS+100000,_maps='https://example.com/map')
class Tests(unittest.TestCase):
 def test_pair_revision_and_partial_resume(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'state.json';state={'projects':{},'next_number':1};out=[]
   def sender(t):out.append(t);return len(out)
   a=row();self.assertEqual(p.process([a],state,path,NOW,sender,0),1)
   self.assertEqual(len(out),2);self.assertIn('مشروع رقم 1',out[0]);self.assertIn('مشروع رقم 1',out[1]);self.assertNotIn('رقم الفرصة:',out[1])
   self.assertEqual(p.process([a],state,path,NOW,sender,0),0)
   a['LAST_EDITED_DATE']=123;self.assertEqual(p.process([a],state,path,NOW,sender,0),0)
   a['OPPORTUNITYDESCRIPTION']='محطة وقود معدلة';self.assertEqual(p.process([a],state,path,NOW,sender,0),1)
   a['STATUS']='Cancelled';self.assertEqual(p.process([a],state,path,NOW,sender,0),1);self.assertIn('غير متاحة',out[-1])
   a['STATUS']='Announced'
   def fail_second(t):
    if 'رقم الفرصة:' not in t:raise p.m.MonitorError('failed')
    return sender(t)
   with self.assertRaises(p.m.MonitorError):p.process([a],state,path,NOW,fail_second,0)
   before=len(out);p.process([a],state,path,NOW,sender,0);self.assertEqual(len(out),before+1)
 def test_coordinates(self):
  a={'attributes':{},'geometry':{'x':41.3341047,'y':26.2995972}}
  self.assertIn('26.2995972%2C41.3341047',p.decorate(a,{'wkid':4326})['_maps'])
  self.assertIsNone(p.decorate(a,{'wkid':3857})['_maps'])
if __name__=='__main__': unittest.main()
