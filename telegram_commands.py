"""Private /test handler. Only its own checkpoint file is ever written."""
import argparse
from datetime import datetime,timezone
import json
import logging
from pathlib import Path
import re
import time
from urllib.parse import urlencode
import monitor as m
import paired_monitor as p

PATH=Path('state/telegram_test_commands.json')

def sample_messages():
    # Fixed, clearly labelled fixture. No opportunity lookup or registration.
    reference=datetime(2026,11,21,tzinfo=timezone.utc)
    deadline=datetime(2026,12,6,2,tzinfo=timezone.utc).timestamp()*1000
    a=dict(OPPORTUNITYID='01-24-012604-4001',OPPORTUNITYTYPE='Investment',
      OPPORTUNITYDESCRIPTION='انشاء و تشغيل و صيانة - أنشطة المركبات - محطة وقود وملحقاتها فئة ج - محطة محروقات-فئة ج',
      BALADYA='بلدية السليمى',STATUS='Announced',OPPORTUNITYACTIVESTATUS='Announced',
      STARTDATE=None,ENDDATE=deadline,LASTRFPSELLDATE=deadline,
      _maps='https://www.google.com/maps/search/?api=1&query=26.2995972%2C41.3341047')
    return ['رسالة تجريبية /test — بيانات نموذج وليست فرصة جديدة أو تحديثاً\n'+text
            for text in p.messages(a,'تجريبي 000',reference)]

def is_test(update,recipient):
    message=update.get('message',{})
    return (message.get('chat',{}).get('type')=='private' and
       str(message.get('chat',{}).get('id'))==recipient and
       str(message.get('from',{}).get('id'))==recipient and
       re.fullmatch(r'/test(?:@Furas_Private_20261120_bot)?',message.get('text','').strip(),re.I) is not None)

def handle(updates,state,path,recipient,sender=p.send,delay=3.2):
    handled=0
    for update in sorted(updates,key=lambda u:u['update_id']):
        identity=update['update_id']
        if identity<state['offset']:continue
        if is_test(update,recipient):
            pending=state.get('pending')
            if pending is None:
                pending={'update_id':identity,'accepted':0};state['pending']=pending
                m.save_state(path,state)
            if pending['update_id']!=identity:raise m.MonitorError('Unexpected pending test command')
            for index in range(pending['accepted'],2):
                sender(sample_messages()[index])
                pending['accepted']=index+1
                m.save_state(path,state)
                time.sleep(delay)
            state['pending']=None;handled+=1
        state['offset']=identity+1
        m.save_state(path,state)
    return handled

def run(smoke=False):
    recipient=m.required('TELEGRAM_CHAT_ID')
    if smoke:
        # The same authorized command handler, in a temporary checkpoint only.
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            count=handle([{'update_id':1,'message':{'chat':{'id':recipient,'type':'private'},
                'from':{'id':recipient},'text':'/test'}}],{'offset':0,'pending':None},
                Path(d)/'smoke.json',recipient)
        logging.info('Live /test handler smoke test: %d pair',count)
        return
    if PATH.exists():
        try:state=json.loads(PATH.read_text())
        except (ValueError,OSError):raise m.MonitorError('Invalid command checkpoint') from None
        if type(state.get('offset')) is not int or 'pending' not in state:
            raise m.MonitorError('Invalid command checkpoint schema')
    else:state={'offset':0,'pending':None}
    with m.session() as s:
        # No response bodies or token-bearing URLs are logged.
        data=m.get_json(s,'https://api.telegram.org/bot'+m.required('TELEGRAM_BOT_TOKEN')+
           '/getUpdates?'+urlencode({'offset':state['offset'],'limit':100,'timeout':0,
                                     'allowed_updates':json.dumps(['message'])}))
    if data.get('ok') is not True or not isinstance(data.get('result'),list):
        raise m.MonitorError('Cannot read Telegram commands; check webhook configuration')
    count=handle(data['result'],state,PATH,recipient)
    logging.info('Accepted /test commands: %d',count)

if __name__=='__main__':
    logging.basicConfig(level=logging.INFO,format='%(levelname)s %(message)s')
    parser=argparse.ArgumentParser();parser.add_argument('--smoke',action='store_true')
    try:run(parser.parse_args().smoke)
    except Exception as error:
        logging.error('%s',str(error) if isinstance(error,m.MonitorError) else type(error).__name__)
        raise SystemExit(1)
