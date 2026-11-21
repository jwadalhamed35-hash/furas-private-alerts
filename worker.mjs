import { DurableObject } from 'cloudflare:workers';
const keyboard={keyboard:[[{text:'/test'}]],resize_keyboard:true,is_persistent:true};
export function delaySeconds(random=crypto.getRandomValues(new Uint32Array(1))[0]) {
  return 3+Math.floor((random/4294967296)*48);
}
export function permitted(u,chat){
  const m=u?.message;
  return Number.isSafeInteger(u?.update_id)&&m?.chat?.type==='private'&&
    String(m.chat.id)===chat&&String(m.from?.id)===chat&&
    /^\/test(?:@Furas_Private_20261120_bot)?$/i.test((m.text||'').trim());
}
export function sample(){
  const h='رسالة تجريبية /test — بيانات نموذج وليست فرصة جديدة أو تحديثاً\nفرصة سارية | محطات الوقود (مشروع رقم تجريبي 000)\n';
  const body='انشاء و تشغيل و صيانة - أنشطة المركبات - محطة وقود وملحقاتها فئة ج - محطة محروقات-فئة ج\nالموقع: بلدية السليمى\nآخر موعد لبيع الكراسة: 2026-12-06 05:00 (السعودية)\n';
  const map='خرائط Google: https://www.google.com/maps/search/?api=1&query=26.2995972%2C41.3341047';
  return [h+'رقم الفرصة: 01-24-012604-4001\n'+body+'التفاصيل: https://furas.momah.gov.sa/opportunity/01-24-012604-4001?type=Investment\n'+map+'\nمصدر النقطة: خريطة فرص الرسمية؛ يرجى مطابقتها مع كراسة المشروع.',h+body+map];
}
export default {
 async fetch(request,env){
  if(new URL(request.url).pathname==='/health'&&request.method==='GET')return Response.json({service:'furas-test',ready:!!(env.TEST_JOBS&&env.TELEGRAM_BOT_TOKEN&&env.TELEGRAM_CHAT_ID&&env.WEBHOOK_SECRET)});
  if(new URL(request.url).pathname!=='/telegram'||request.method!=='POST')return new Response('Not found',{status:404});
  if(!env.WEBHOOK_SECRET||request.headers.get('X-Telegram-Bot-Api-Secret-Token')!==env.WEBHOOK_SECRET)return new Response('Forbidden',{status:403});
  if(Number(request.headers.get('Content-Length')||0)>16384)return new Response('Too large',{status:413});
  let update;try{update=await request.json();}catch{return new Response('Invalid JSON',{status:400});}
  if(!permitted(update,env.TELEGRAM_CHAT_ID))return new Response('OK');
  // One durable instance per Telegram update: repeat delivery reuses the same job.
  const job=env.TEST_JOBS.get(env.TEST_JOBS.idFromName(String(update.update_id)));
  await job.fetch(new Request('https://job/enqueue',{method:'POST'}));
  return new Response('OK');
 }
};
export class TestJob extends DurableObject {
 constructor(ctx,env){super(ctx,env);this.ctx=ctx;this.env=env;}
 async fetch(){
  await this.ctx.storage.transaction(async tx=>{
   if(await tx.get('job'))return;
   const delay=delaySeconds();
   await tx.put('job',{stage:0,status:'waiting',delay,accepted:[]});
   await tx.setAlarm(Date.now()+delay*1000);
  });
  return new Response('Queued');
 }
 async alarm(){
  const job=await this.ctx.storage.get('job');
  if(!job||job.status==='complete'||job.status==='uncertain'||job.status==='sending'||job.status==='rejected')return;
  const messages=sample();
  const index=job.stage;
  // Mark before POST: never blindly resend an uncertain Telegram request.
  job.status='sending';await this.ctx.storage.put('job',job);
  let response,data;
  try{
   response=await fetch('https://api.telegram.org/bot'+this.env.TELEGRAM_BOT_TOKEN+'/sendMessage',{
    method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chat_id:this.env.TELEGRAM_CHAT_ID,text:messages[index],reply_markup:keyboard,link_preview_options:{is_disabled:true}}),signal:AbortSignal.timeout(15000)});
   data=await response.json();
  }catch{job.status='uncertain';await this.ctx.storage.put('job',job);console.error('Telegram result uncertain; request not retried');return;}
  if(!response.ok||data.ok!==true||!data.result?.message_id){
   job.status='rejected';job.error_code=data.error_code||response.status;
   await this.ctx.storage.put('job',job);console.error('Telegram rejected test message',job.error_code);return;
  }
  job.accepted.push(data.result.message_id);job.stage++;
  if(job.stage===2){job.status='complete';await this.ctx.storage.put('job',job);console.info('Test pair accepted',job.delay);}
  else{job.status='waiting';await this.ctx.storage.put('job',job);await this.ctx.storage.setAlarm(Date.now()+3200);}
 }
}
