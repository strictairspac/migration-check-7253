import redis
from datetime import datetime,timezone
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from src.config.database import Session
from src.config.settings import REDIS_URL
from src.models.batch import Batch
from src.models.mapping import Mapping
from src.models.run import Run,TargetRow,VersionRow,RunVersion
from src.utils.serialization import record
from src.services.migration import (digest,key_digest,validate_backup,make_backup,apply_corrections,
 check_source,convert_row)

queue=redis.Redis.from_url(REDIS_URL)
CORRECTION_CHUNK=500

def list_items():
 with Session() as s:return [record(r) for r in s.query(Run).order_by(Run.id.desc()).limit(100).all()]

def detail(item_id):
 with Session() as s:
  r=s.get(Run,item_id)
  if not r:raise HTTPException(404,'未找到对账运行')
  return record(r)

def create(data):
 with Session() as s:
  m=s.get(Mapping,data.mapping_id)
  if not m:raise HTTPException(404,'未找到字段映射')
  b=s.get(Batch,m.batch_id)
  r=Run(name=data.name,batch_id=b.id,mapping_id=m.id,status='queued',snapshot={'batch':record(b),'mapping':record(m)})
  s.add(r);s.commit();s.refresh(r);result=record(r)
 try:queue.rpush('migration-runs',result['id'])
 except redis.RedisError:
  with Session() as s:r=s.get(Run,result['id']);r.status='failed';r.error='队列不可用，请创建新任务';s.commit()
  raise HTTPException(503,'队列不可用，请稍后重试')
 return result

def cancel(item_id):
 with Session() as s:
  r=s.query(Run).filter_by(id=item_id).with_for_update().first()
  if not r:raise HTTPException(404,'未找到对账运行')
  if r.status not in ('queued','running'):raise HTTPException(409,'运行已终止，无法取消')
  r.cancel_requested=True
  if r.status=='queued':r.status='cancelled'
  s.commit();return record(r)

# ---- versioned publication -------------------------------------------------

def ensure_initial_version(s,r):
 """Give a run completed before versioning existed its version 1 record.

 Legacy runs only have runs.result plus target_rows; this promotes them
 without changing their persisted target rows so they appear as a version and
 can be corrected going forward.  No-op when a version already exists.
 """
 if not r or r.status!='completed' or not isinstance(r.result,dict):return latest_version(s,r.id) if r else None
 existing=latest_version(s,r.id)
 if existing:return existing
 rows=[x.payload for x in s.query(TargetRow).filter_by(run_id=r.id).order_by(TargetRow.ordinal)]
 origins=[x.ordinal for x in s.query(TargetRow).filter_by(run_id=r.id).order_by(TargetRow.ordinal)]
 if not rows and (r.result.get('accepted_count') or r.result.get('accepted_rows')):return None
 checksum=digest(rows)
 accepted=r.result.get('accepted_rows')
 if isinstance(accepted,list) and digest(accepted)!=checksum:
  # persisted target rows disagree with the result payload; trust neither
  return None
 result={'accepted_rows':rows,'accepted_origins':origins or list(range(1,len(rows)+1)),
  'rejected_rows':r.result.get('rejected_rows') or [],'replacements':[],
  'checksum':checksum,'row_hashes':[digest(x) for x in rows],
  'source_count':r.result.get('source_count',len((r.snapshot.get('batch') or {}).get('rows') or [])),
  'chunks':r.result.get('chunks') or [],'accepted_count':len(rows),'rejected_count':len(r.result.get('rejected_rows') or [])}
 v=publish(s,r,result,'initial',actor='legacy-import')
 return v

def version_record(v):
 out=record(v)
 out['accepted_count']=len(v.accepted_rows or [])
 out['rejected_count']=len(v.rejected_rows or [])
 out['replacement_count']=len(v.replacements or [])
 out['corrected_count']=len((v.correction or {}).get('corrections') or {})
 for key in ('accepted_rows','accepted_origins','rejected_rows','replacements','checksum','row_hashes','backup','sources','chunks','correction','request_digest'):
  out.pop(key,None)
 return out

def versions(item_id):
 with Session() as s:
  r=s.query(Run).filter_by(id=item_id).first()
  if not r:raise HTTPException(404,'未找到对账运行')
  if ensure_initial_version(s,r):s.commit()
  rows=s.query(RunVersion).filter_by(run_id=item_id).order_by(RunVersion.version).all()
  return {'current_version':rows[-1].version if rows else None,'versions':[version_record(v) for v in rows]}

def version_detail(item_id,version):
 with Session() as s:
  r=s.query(Run).filter_by(id=item_id).first()
  if not r:raise HTTPException(404,'未找到对账运行')
  if ensure_initial_version(s,r):s.commit()
  v=s.query(RunVersion).filter_by(run_id=item_id,version=version).first()
  if not v:raise HTTPException(404,'未找到该运行版本')
  out=version_record(v)
  out.update({'accepted_rows':v.accepted_rows,'accepted_origins':v.accepted_origins,'rejected_rows':v.rejected_rows,
   'replacements':v.replacements,'checksum':v.checksum,'row_hashes':v.row_hashes,'backup':v.backup,
   'source_count':v.source_count,'chunks':v.chunks,'correction':v.correction,'request_id':v.request_id})
  return out

def latest_version(s,item_id):
 return s.query(RunVersion).filter_by(run_id=item_id).order_by(RunVersion.version.desc()).first()

def current_rows(s,item_id,version):
 rows=s.query(VersionRow).filter_by(run_id=item_id,version=version).order_by(VersionRow.ordinal).all()
 return [r.payload for r in rows],[r.ordinal for r in rows]

def publish(s,r,result,kind,actor=None,request=None,correction=None,sources=None):
 """Append one immutable run version, its target rows, reconciliation and backup atomically."""
 latest=latest_version(s,r.id)
 version=(latest.version if latest else 0)+1
 backup=make_backup(result,r.id,r.snapshot['batch']['name'],version)
 v=RunVersion(run_id=r.id,version=version,kind=kind,actor=actor,
  request_id=(request or {}).get('request_id'),request_digest=(request or {}).get('digest'),
  correction=correction,sources=sources or {},source_count=result['source_count'],chunks=result['chunks'],
  accepted_rows=result['accepted_rows'],accepted_origins=result['accepted_origins'],
  rejected_rows=result['rejected_rows'],replacements=result['replacements'],
  checksum=result['checksum'],row_hashes=result['row_hashes'],backup=backup)
 s.add(v)
 _write_version_rows(s,r.id,version,result['accepted_rows'],result['accepted_origins'])
 _sync_current_targets(s,r.id,version)
 s.flush()
 r.result=dict(result,version=version,backup=backup)
 return v

def _write_version_rows(s,run_id,version,rows,origins):
 s.query(VersionRow).filter_by(run_id=run_id,version=version).delete()
 for i,row in enumerate(rows):s.add(VersionRow(run_id=run_id,version=version,ordinal=origins[i],key_digest=key_digest(row),payload=row))
 s.flush()

def _sync_current_targets(s,run_id,version):
 """Mirror the highest published version into target_rows (current target)."""
 latest=latest_version(s,run_id)
 s.query(TargetRow).filter_by(run_id=run_id).delete()
 if latest and latest.version==version:
  for i,row in enumerate(latest.accepted_rows):
   s.add(TargetRow(run_id=run_id,ordinal=latest.accepted_origins[i],key_digest=key_digest(row),payload=row))
 s.flush()

# ---- corrections -----------------------------------------------------------

def _touched_keys(corrections,rules,base_rows,base_origins):
 """Natural keys an order can affect: the keys its rows held at the base
 version plus the keys the corrected source rows now resolve to."""
 by_row=dict(zip(base_origins,base_rows))
 keys=set()
 for row_number,source in corrections.items():
  if row_number in by_row:keys.add(key_digest(by_row[row_number]))
  try:keys.add(key_digest(convert_row(source,rules)))
  except (ValueError,TypeError):pass
 return keys

def _order_digest(corrections):
 """Content identity of a correction order, independent of the base version
 so a retry of the same order (even after the run moved on) replays."""
 return digest({'corrections':{str(k):v for k,v in sorted(corrections.items())}})

def correct(item_id,data,actor=None):
 """Correct rejected source rows by 1-based row number and publish a new version.

 Same transaction: recomputed target rows, reconciliation and backup all take
 effect together; any failure rolls back and the previous version stays the
 current one.  The run row is locked for check+compute+write, so two orders on
 the same run serialise.  An order based on an older version merges when it
 touches only untouched natural keys; if another order already changed the same
 sample_code+test_name, the later order is asked to re-check.  request_id makes
 a retry of the same order return the published version instead of publishing
 again.
 """
 with Session() as s:
  r=s.query(Run).filter_by(id=item_id).with_for_update().first()
  if not r:raise HTTPException(404,'未找到对账运行')
  if r.status!='completed':raise HTTPException(409,'仅已完成运行可以补正')
  ensure_initial_version(s,r)
  s.flush()
  rules=r.snapshot['mapping']['rules']
  source_rows=r.snapshot['batch']['rows']
  latest=latest_version(s,item_id)
  if not latest:raise HTTPException(409,'该运行没有可补正的入库版本')
  corrections={}
  for row_number,source in data.corrections.items():
   if not 1<=row_number<=len(source_rows):raise HTTPException(422,'补正行号第%d行超出源批次范围'%row_number)
   try:check_source(source)
   except ValueError as e:raise HTTPException(422,'第%d行：%s'%(row_number,e))
   corrections[row_number]=source
  order_digest=_order_digest(corrections)
  if data.request_id:
   prior=s.query(RunVersion).filter(RunVersion.run_id==item_id,RunVersion.request_id==data.request_id).first()
   if prior:
    if prior.request_digest!=order_digest:raise HTTPException(409,'同一补正单号的内容与已发布版本不一致，请更换单号后重试')
    stored=dict(prior.correction or {});stored.update({'result_version':prior.version,'replayed':True})
    return {'run':record(r),'version':version_record(prior),'correction':stored,'replayed':True}
  if data.base_version>latest.version:raise HTTPException(409,'补正所基于的第%d版尚未入库，请刷新后重新核对'%data.base_version)
  base=s.query(RunVersion).filter_by(run_id=item_id,version=data.base_version).first()
  if not base:raise HTTPException(404,'未找到补正所基于的运行版本')
  intermediate=s.query(RunVersion).filter(RunVersion.run_id==item_id,RunVersion.version>data.base_version).order_by(RunVersion.version).all()
  new_keys=_touched_keys(corrections,rules,base.accepted_rows,base.accepted_origins)
  if intermediate:
   touched_rows=set();touched_keys=set()
   for v in intermediate:
    meta=v.correction or {}
    touched_rows.update(int(k) for k in (meta.get('corrections') or {}))
    touched_keys.update(meta.get('touched_keys') or [])
   if touched_rows & set(corrections):raise HTTPException(409,'第%s行已被第%s版补正改变，请按最新版本重新核对后再提交'%(','.join(str(x) for x in sorted(touched_rows&set(corrections))),',第'.join(str(v.version) for v in intermediate)))
   if touched_keys & new_keys:raise HTTPException(409,'委托号+检测项已被第%s版补正改变，请按最新版本重新核对后再提交'%'、第'.join(str(v.version) for v in intermediate))
  cumulative=dict((latest.correction or {}).get('corrections') or {})
  cumulative.update({str(k):v for k,v in corrections.items()})
  cumulative={int(k):v for k,v in cumulative.items()}
  previous_rows,previous_origins=current_rows(s,item_id,latest.version)
  try:result=apply_corrections(r.snapshot,cumulative,previous={'rows':previous_rows,'origins':previous_origins},chunk_size=CORRECTION_CHUNK)
  except ValueError as e:raise HTTPException(422,'补正无法计算：'+str(e))
  touched=sorted(new_keys)
  correction={'request_id':data.request_id,'base_version':data.base_version,'merged_from_version':latest.version,
   'corrections':{str(k):v for k,v in sorted(cumulative.items())},'order_corrections':{str(k):v for k,v in sorted(corrections.items())},
   'touched_keys':touched,'reason':data.reason,'result_version':latest.version+1}
  try:
   v=publish(s,r,result,'correction',actor=actor,request={'request_id':data.request_id,'digest':order_digest},
    correction=correction,sources={str(k):val for k,val in sorted(corrections.items())})
   s.commit()
  except IntegrityError:
   s.rollback();raise HTTPException(409,'运行已有新的入库版本，请重新核对后再提交')
  s.refresh(v)
  return {'run':record(r),'version':version_record(v),'correction':correction,'replayed':False}

# ---- target, backup, restore ----------------------------------------------

def targets(item_id,version=None):
 with Session() as s:
  r=s.query(Run).filter_by(id=item_id).first()
  if not r:raise HTTPException(404,'未找到对账运行')
  if ensure_initial_version(s,r):s.commit()
  latest=latest_version(s,item_id)
  if not latest:return {'rows':[],'count':0,'checksum':digest([]),'version':None}
  chosen=latest.version if version is None else version
  v=s.query(RunVersion).filter_by(run_id=item_id,version=chosen).first()
  if not v:raise HTTPException(404,'未找到该运行版本')
  if chosen==latest.version and version is None:rows=[x.payload for x in s.query(TargetRow).filter_by(run_id=item_id).order_by(TargetRow.ordinal)]
  else:rows=[x.payload for x in s.query(VersionRow).filter_by(run_id=item_id,version=chosen).order_by(VersionRow.ordinal)]
  return {'rows':rows,'count':len(rows),'checksum':digest(rows),'version':chosen,'current_version':latest.version,
   'matches_version':digest(rows)==v.checksum,'source':'target_rows' if chosen==latest.version and version is None else 'version_rows'}

def backup(item_id,version=None):
 with Session() as s:
  r=s.get(Run,item_id)
  if not r:raise HTTPException(404,'未找到对账运行')
  if r.status!='completed':raise HTTPException(409,'仅完成运行可以备份')
  v=ensure_initial_version(s,r);s.commit()
  if version is None:v=v or latest_version(s,item_id)
  else:v=s.query(RunVersion).filter_by(run_id=item_id,version=version).first()
  if not v:raise HTTPException(404,'未找到该运行版本')
  return v.backup

def restore(item_id,data):
 """Restore one version's logical backup; other versions stay untouched."""
 with Session() as s:
  r=s.query(Run).filter_by(id=item_id).with_for_update().first()
  if not r:raise HTTPException(404,'未找到对账运行')
  if r.status!='completed':raise HTTPException(409,'仅完成运行可以恢复')
  latest=ensure_initial_version(s,r)
  if latest:s.commit()
  latest=latest_version(s,item_id)
  if not latest:raise HTTPException(409,'该运行没有可恢复的入库版本')
  backup=data.backup
  if not isinstance(backup,dict):raise HTTPException(422,'备份内容无效')
  declared=backup.get('run_version')
  if declared is None and backup.get('version')==1:declared=1
  if declared is None:
   declared=latest.version
  if not isinstance(declared,int):raise HTTPException(422,'备份版本无效')
  v=s.query(RunVersion).filter_by(run_id=item_id,version=declared).first()
  if not v:raise HTTPException(404,'未找到备份对应的第%d版，无法恢复'%declared)
  try:rows,origins=validate_backup(backup,v.checksum,item_id,v.version)
  except ValueError as e:raise HTTPException(422,str(e))
  if origins is None:
   if len(rows)!=len(v.accepted_origins):raise HTTPException(422,'旧版备份缺少源行号，无法写入分版目标表')
   origins=v.accepted_origins
  before=s.query(VersionRow).filter_by(run_id=item_id,version=v.version).count()
  _write_version_rows(s,item_id,v.version,rows,origins)
  if v.version==latest.version:_sync_current_targets(s,item_id,v.version)
  scope=s.query(TargetRow) if v.version==latest.version else s.query(VersionRow).filter_by(run_id=item_id,version=v.version)
  actual=[x.payload for x in scope.order_by((TargetRow if v.version==latest.version else VersionRow).ordinal)]
  proof={'restored_at':datetime.now(timezone.utc).isoformat(),'version':v.version,'previous_count':before,
   'restored_count':len(actual),'checksum':digest(actual),'verified':digest(actual)==v.checksum}
  if not proof['verified']:raise HTTPException(409,'恢复后校验失败，已回滚')
  v.recovery=(v.recovery or [])+[proof]
  if v.version==latest.version:
   r.recovery=(r.recovery or [])+[proof]
  s.commit()
  return proof
