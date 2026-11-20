import time,logging,redis
from pathlib import Path
from src.config.settings import REDIS_URL
from src.config.database import Session
from src.models.run import Run
from src.services.migration import migrate,Cancelled
from src.services.runs import publish
from src.services.audit import log
q=redis.Redis.from_url(REDIS_URL)
def progress(run_id,info):
 Path('/tmp/worker-heartbeat').touch()
 with Session() as s:
  r=s.get(Run,run_id)
  if not r or r.cancel_requested or r.status=='cancelled':raise Cancelled()
  r.progress=info;s.commit()
def process(run_id):
 with Session() as s:
  r=s.query(Run).filter_by(id=run_id).with_for_update().first()
  if not r or r.status!='queued':return
  r.status='running';snapshot=r.snapshot;s.commit()
 try:
  result=migrate(snapshot['batch']['rows'],snapshot['mapping']['rules'],snapshot['mapping']['chunk_size'],lambda info:progress(run_id,info))
  with Session() as s:
   r=s.query(Run).filter_by(id=run_id).with_for_update().first()
   if not r or r.cancel_requested:raise Cancelled()
   if r.status!='running':return
   publish(s,r,result,'initial',actor='worker')
   r.status='completed';s.commit()
  log('worker','run.completed',str(run_id))
 except Cancelled:
  with Session() as s:
   r=s.get(Run,run_id)
   if r.status!='completed':r.status='cancelled';r.error='用户取消，未提交目标行'
   s.commit()
  log('worker','run.cancelled',str(run_id))
 except Exception as exc:
  logging.exception('migration failed')
  with Session() as s:
   r=s.get(Run,run_id)
   if r.status!='completed':r.status='failed';r.error=str(exc)[:1000]
   s.commit()
def main():
 while True:
  Path('/tmp/worker-heartbeat').touch()
  try:
   item=q.blpop('migration-runs',timeout=2)
   if not item:continue
   process(int(item[1]))
  except Exception:logging.exception('worker loop');time.sleep(2)
if __name__=='__main__':main()
