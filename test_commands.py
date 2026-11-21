import unittest,tempfile,json
from pathlib import Path
import telegram_commands as c

def update(identity=1,chat=123,text='/test',sender=123,kind='private'):
 return {'update_id':identity,'message':{'chat':{'id':chat,'type':kind},'from':{'id':sender},'text':text}}
class Commands(unittest.TestCase):
 def test_private_only_and_format(self):
  for u in [update(chat=999),update(sender=999),update(kind='group'),update(text='/testx')]:self.assertFalse(c.is_test(u,'123'))
  self.assertTrue(c.is_test(update(text='/test@Furas_Private_20261120_bot'),'123'))
  full,short=c.sample_messages();self.assertIn('التفاصيل:',full);self.assertNotIn('التفاصيل:',short)
  for text in (full,short):self.assertIn('تجريبي 000',text);self.assertIn('خرائط Google:',text)
 def test_replay_partial_and_no_monitor_writes(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'commands.json';state={'offset':0,'pending':None};sent=[]
   def fail_second(text):
    if sent:raise c.m.MonitorError('failed')
    sent.append(text);return 1
   with self.assertRaises(c.m.MonitorError):c.handle([update()],state,path,'123',fail_second,0)
   state=json.loads(path.read_text());self.assertEqual(state['pending']['accepted'],1)
   c.handle([update()],state,path,'123',sent.append,0);self.assertEqual(len(sent),2)
   c.handle([update()],state,path,'123',sent.append,0);self.assertEqual(len(sent),2)
   self.assertEqual([p.name for p in Path(d).iterdir()],['commands.json'])
if __name__=='__main__':unittest.main()
