"""HTTP-level checks: routes, roles, version tabs and version-scoped restore."""
import os,sys,json
import pytest
from fastapi.testclient import TestClient

def make_app():
 import src.routes.runs as rr
 import src.services.runs as svc
 from src.main import app
 from src.config.database import Base,engine
 Base.metadata.drop_all(engine);Base.metadata.create_all(engine)
 return app

class Ctx:
 pass

@pytest.fixture()
def client(monkeypatch):
 import src.services.runs as svc
 class FakeRedis:
  def rpush(self,q,v):pass
  def blpop(self,q,timeout=0):return None
  def ping(self):return True
 svc.queue=FakeRedis()
 import src.main as main
 main.queue=svc.queue
 from src.config.database import Base,engine,Session
 from src.models.identity import User
 from src.models.batch import Batch
 from src.models.mapping import Mapping
 from src.utils.security import hash_password
 Base.metadata.drop_all(engine);Base.metadata.create_all(engine)
 with Session() as s:
  for role in ('admin','operator','viewer'):
   s.add(User(email=role+'@x',role=role,password=hash_password('pw')))
  s.add(Batch(name='batch',source_file='f',description='',rows=[{'a':1}]))
  s.commit()
 from fastapi.testclient import TestClient
 c=TestClient(main.app)
 yield c

def login(c,role):
 r=c.post('/auth/login',json={'email':role+'@x','password':'pw'})
 assert r.status_code==200,r.text
 return {'Authorization':'Bearer '+r.json()['token']}

RULES=[{'source':'委托号','target':'sample_code','type':'text','required':True},
 {'source':'检测项','target':'test_name','type':'text','required':True},
 {'source':'结果','target':'result_value','type':'decimal','required':True},
 {'source':'单位','target':'unit','type':'text','required':True},
 {'source':'时间','target':'measured_at','type':'datetime','required':True}]
def row(code,result):return {'委托号':code,'检测项':'拉伸强度','结果':result,'单位':'MPa','时间':'2026-11-17T09:00:00+08:00'}

def seed_run(c,h):
 b=c.get('/batches',headers=h).json()[0]
 payload={k:b[k] for k in ('name','source_file','description')}
 payload['rows']=[row('A','bad'),row('A','420.5')]
 assert c.put('/batches/%d'%b['id'],json=payload,headers=h).status_code==200
 m=c.post('/mappings',json={'name':'map','batch_id':b['id'],'rules':RULES,'chunk_size':2},headers=h).json()
 r=c.post('/runs',json={'name':'run1','mapping_id':m['id']},headers=h).json()
 from src.worker import process
 process(r['id'])
 return r['id']

def test_routes_versions_correct_restore(client):
 h=login(client,'admin')
 rid=seed_run(client,h)
 v=client.get(f'/runs/{rid}/versions',headers=h).json()
 assert v['current_version']==1 and v['versions'][0]['accepted_count']==1
 d=client.get(f'/runs/{rid}/versions/1',headers=h).json()
 assert d['accepted_origins']==[2] and d['rejected_rows'][0]['row_number']==1
 assert client.get(f'/runs/{rid}/backup',headers=h).json()['run_version']==1
 r=client.post(f'/runs/{rid}/corrections',headers=h,json={'base_version':1,'corrections':{'1':row('A','999')},'request_id':'C1'})
 assert r.status_code==200,r.text
 assert r.json()['version']['version']==2
 assert client.get(f'/runs/{rid}/versions/2',headers=h).json()['accepted_origins']==[1]
 # retry same order idempotent
 again=client.post(f'/runs/{rid}/corrections',headers=h,json={'base_version':1,'corrections':{'1':row('A','999')},'request_id':'C1'})
 assert again.json()['replayed'] is True
 assert client.get(f'/runs/{rid}/versions',headers=h).json()['current_version']==2
 # restore v1 backup only rewrites v1 targets
 backup1=client.get(f'/runs/{rid}/backup?version=1',headers=h).json()
 p=client.post(f'/runs/{rid}/restore',headers=h,json={'backup':backup1})
 assert p.status_code==200,p.text
 assert p.json()['version']==1
 assert client.get(f'/runs/{rid}/targets',headers=h).json()['version']==2
 assert client.get(f'/runs/{rid}/targets?version=2',headers=h).json()['rows'][0]['result_value']=='999.000000'

def test_viewer_cannot_correct(client):
 h=login(client,'viewer')
 rid=seed_run(client,login(client,'operator'))
 r=client.post(f'/runs/{rid}/corrections',headers=h,json={'base_version':1,'corrections':{'1':row('A','1')}})
 assert r.status_code==403

def test_missing_run_and_bad_version(client):
 h=login(client,'operator')
 assert client.get('/runs/999/versions',headers=h).status_code==404
 rid=seed_run(client,h)
 assert client.get(f'/runs/{rid}/versions/7',headers=h).status_code==404
 assert client.get(f'/runs/{rid}/backup?version=7',headers=h).status_code==404
