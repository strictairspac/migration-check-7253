"""End-to-end checks for versioned corrections, runnable without Docker.

Uses SQLite in a temp file and a stubbed Redis queue; exercises the service
layer exactly as the HTTP routes call it.
"""
import os, sys, json
import pytest

class FakeRedis:
 def __init__(self):self.items=[]
 def rpush(self,q,v):self.items.append(v)
 def blpop(self,q,timeout=0):return None
 def ping(self):return True

import src.services.runs as runs
runs.queue=FakeRedis()

from src.config.database import Base,engine,Session
from src.models.batch import Batch
from src.models.mapping import Mapping
from src.models.identity import User,Audit
from src.models.run import Run,TargetRow,VersionRow,RunVersion
from src.schemas.business import BatchInput,MappingInput,RunInput,CorrectionInput,RestoreInput
from src.services.migration import migrate
from src.services import runs as svc
from src.worker import process

RULES=[{'source':'委托号','target':'sample_code','type':'text','required':True},
 {'source':'检测项','target':'test_name','type':'text','required':True},
 {'source':'结果','target':'result_value','type':'decimal','required':True},
 {'source':'单位','target':'unit','type':'text','required':True},
 {'source':'时间','target':'measured_at','type':'datetime','required':True}]

def row(code,result,item='拉伸强度',unit='MPa',when='2026-11-17T09:00:00+08:00'):
 return {'委托号':code,'检测项':item,'结果':result,'单位':unit,'时间':when}

@pytest.fixture(autouse=True)
def schema():
 Base.metadata.drop_all(engine);Base.metadata.create_all(engine)
 with Session() as s:
  b=Batch(name='批次',source_file='f.json',description='',rows=[row('A','bad'),row('A','420.5'),row('B','1.0'),row('C','x')])
  s.add(b);s.flush()
  s.add(Mapping(name='映射',batch_id=b.id,rules=RULES,chunk_size=2));s.commit()

def make_run(name='run'):
 with Session() as s:
  m=s.query(Mapping).first()
  r=svc.create(RunInput(name=name,mapping_id=m.id))
 process(r['id'])
 return r['id']

def correct(run_id,corrections,base_version=1,request_id=None,reason=''):
 return svc.correct(run_id,CorrectionInput(base_version=base_version,corrections=corrections,reason=reason,request_id=request_id),actor='op@x')

def test_initial_version_and_targets():
 rid=make_run()
 with Session() as s:
  versions=s.query(RunVersion).filter_by(run_id=rid).all()
  targets=s.query(TargetRow).filter_by(run_id=rid).all()
 assert [v.version for v in versions]==[1]
 assert versions[0].accepted_origins==[2,3]
 assert sorted(t.ordinal for t in targets)==[2,3]
 assert sorted(t.ordinal for t in s.query(VersionRow).filter_by(run_id=rid).all())==[2,3]
 assert svc.targets(rid)['version']==1
 assert svc.targets(rid,1)['matches_version'] is True

def test_correction_displaces_earlier_accepted_row_and_publishes_everything():
 rid=make_run()
 out=correct(rid,{1:row('A','999')})
 assert out['version']['version']==2
 v=svc.version_detail(rid,2)
 assert v['accepted_origins']==[1,3]
 assert v['accepted_rows'][0]['result_value']=='999.000000'
 assert [r['type'] for r in v['replacements']].count('displaced')==1
 displaced=[r for r in v['replacements'] if r['type']=='displaced'][0]
 assert displaced['row_number']==2 and displaced['replaced_by']==1
 # target rows now only reflect version 2 for the current run
 assert svc.targets(rid)['rows']==v['accepted_rows']
 # backup contains the same rows and is bound to version 2
 assert svc.backup(rid,2)['rows']==v['accepted_rows']
 assert svc.backup(rid,2)['run_version']==2
 # version 1 backup untouched and still downloadable
 assert svc.backup(rid,1)['rows'][0]['result_value']=='420.500000'

def test_restore_old_version_only_affects_that_version():
 rid=make_run()
 old=svc.backup(rid,1)
 correct(rid,{1:row('A','999')})
 proof=svc.restore(rid,RestoreInput(backup=old))
 assert proof['version']==1 and proof['verified']
 with Session() as s:
  v1=[x.payload for x in s.query(VersionRow).filter_by(run_id=rid,version=1).order_by(VersionRow.ordinal)]
  v2=[x.payload for x in s.query(VersionRow).filter_by(run_id=rid,version=2).order_by(VersionRow.ordinal)]
  current=[x.payload for x in s.query(TargetRow).filter_by(run_id=rid).order_by(TargetRow.ordinal)]
 assert v1[0]['result_value']=='420.500000'
 assert v2[0]['result_value']=='999.000000'
 assert current==v2
 assert svc.targets(rid)['version']==2

def test_failed_correction_keeps_previous_version():
 rid=make_run()
 before=svc.targets(rid)
 with pytest.raises(Exception):
  correct(rid,{'9':row('Z','1')}) if False else svc.correct(rid,CorrectionInput(base_version=1,corrections={9:row('Z','1')}))
 assert svc.targets(rid)==before
 assert svc.versions(rid)['current_version']==1

def test_retry_same_request_does_not_republish():
 rid=make_run()
 first=correct(rid,{1:row('A','999')},request_id='CORR-1')
 replay=correct(rid,{1:row('A','999')},request_id='CORR-1')
 assert replay['replayed'] and replay['version']['version']==first['version']['version']
 assert svc.versions(rid)['current_version']==2
 with pytest.raises(Exception) as exc:
  correct(rid,{1:row('A','888')},request_id='CORR-1')
 assert '补正单号' in str(exc.value)

def test_concurrent_orders_on_different_keys_both_land():
 """Both started from v1: different natural keys merge into v2 then v3."""
 rid=make_run()
 correct(rid,{1:row('A','999')})              # touches A/拉伸强度
 second=correct(rid,{4:row('C','7.5')})      # touches C/拉伸强度 only
 assert second['version']['version']==3
 v=svc.version_detail(rid,3)
 assert v['accepted_origins']==[1,3,4]
 assert v['correction']['merged_from_version']==2
 assert v['correction']['base_version']==1

def test_concurrent_orders_on_same_key_force_recheck():
 rid=make_run()
 correct(rid,{1:row('A','999')})
 with pytest.raises(Exception) as exc:
  correct(rid,{2:row('A','111')},base_version=1)
 assert '重新核对' in str(exc.value)
 assert svc.versions(rid)['current_version']==2

def test_concurrent_orders_same_row_force_recheck():
 rid=make_run()
 correct(rid,{1:row('A','999')})
 with pytest.raises(Exception) as exc:
  correct(rid,{1:row('A','888')},base_version=1)
 assert '重新核对' in str(exc.value)

def test_stale_base_version_rejected():
 rid=make_run()
 with pytest.raises(Exception) as exc:
  correct(rid,{1:row('A','999')},base_version=5)
 assert '刷新' in str(exc.value)

def test_tampered_backup_rejected_and_version_confusion_blocked():
 rid=make_run()
 correct(rid,{1:row('A','999')})
 bad=json.loads(json.dumps(svc.backup(rid,2)))
 bad['rows'][0]['result_value']='0.000000'
 with pytest.raises(Exception):
  svc.restore(rid,RestoreInput(backup=bad))
 # v1 backup cannot be claimed as v2
 v1=json.loads(json.dumps(svc.backup(rid,1)))
 v1['run_version']=2
 with pytest.raises(Exception):
  svc.restore(rid,RestoreInput(backup=v1))

def test_history_versions_render_rejections_and_reasons():
 rid=make_run()
 correct(rid,{1:row('A','999')})
 v2=svc.version_detail(rid,2)
 reasons={r['row_number']:r['reason'] for r in v2['rejected_rows']}
 assert 'duplicate_natural_key' in reasons[2]
 assert 'invalid_decimal' in reasons[4]
 types={r['type'] for r in v2['replacements']}
 assert {'admitted','displaced'} <= types


def test_legacy_completed_run_is_promoted_to_version_one():
 rid=make_run()
 with Session() as s:
  s.query(RunVersion).filter_by(run_id=rid).delete()
  s.query(VersionRow).filter_by(run_id=rid).delete()
  s.commit()
 out=svc.correct(rid,CorrectionInput(base_version=1,corrections={1:row('A','999')}))
 assert out['version']['version']==2
 v1=svc.version_detail(rid,1)
 assert v1['accepted_origins']==[2,3]
 assert svc.backup(rid,1)['run_version']==1


def test_retry_after_flush_still_replays_not_republishes():
 rid=make_run()
 correct(rid,{1:row('A','999')},request_id='C1')
 correct(rid,{4:row('C','7.5')},base_version=2,request_id='C2')
 replay=correct(rid,{1:row('A','999')},base_version=1,request_id='C1')
 assert replay['replayed'] and replay['version']['version']==2
 assert svc.versions(rid)['current_version']==3

def test_restore_current_version_keeps_targets_consistent():
 rid=make_run()
 correct(rid,{1:row('A','999')})
 cur=json.loads(json.dumps(svc.backup(rid,2)))
 proof=svc.restore(rid,RestoreInput(backup=cur))
 assert proof['version']==2 and proof['verified']
 t=svc.targets(rid)
 assert t['version']==2 and t['rows'][0]['result_value']=='999.000000' and t['matches_version']
 with Session() as s:
  current=[x.payload for x in s.query(TargetRow).filter_by(run_id=rid).order_by(TargetRow.ordinal)]
 assert current==t['rows']

def test_correction_cannot_change_untargeted_rows():
 rid=make_run()
 out=correct(rid,{3:row('B','2.5')})
 v=svc.version_detail(rid,out['version']['version'])
 assert v['accepted_origins']==[2,3]
 by_origin=dict(zip(v['accepted_origins'],v['accepted_rows']))
 assert by_origin[2]['result_value']=='420.500000'
 assert by_origin[3]['result_value']=='2.500000'
 assert any(r['type']=='updated' and r['row_number']==3 for r in v['replacements'])
