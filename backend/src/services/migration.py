import json,hashlib,math
from decimal import Decimal,InvalidOperation
from datetime import datetime,timezone,timedelta
from src.constants.enums import TYPES
class Cancelled(Exception):pass
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
def key_digest(row):return digest([row['sample_code'],row['test_name']])
def validate_rules(rules):
 if not isinstance(rules,list) or len(rules)!=5:raise ValueError('必须为五个目标字段各配置一条映射')
 targets=[]
 for rule in rules:
  if not isinstance(rule,dict) or set(rule)!={'source','target','type','required'}:raise ValueError('映射须包含source/target/type/required')
  target=rule['target']
  if not isinstance(target,str) or target not in TYPES or rule['type']!=TYPES[target]:raise ValueError('目标字段或类型不匹配')
  if not isinstance(rule['source'],str) or not 1<=len(rule['source'])<=60:raise ValueError('源字段名无效')
  if not isinstance(rule['required'],bool) or (target!='unit' and not rule['required']):raise ValueError('除unit外均为必填目标字段')
  targets.append(target)
 if len(set(targets))!=5:raise ValueError('目标字段不可重复')
 return rules

def check_source(source):
 """Validate one raw source row (used by batch input and by corrections)."""
 if not isinstance(source,dict):raise ValueError('原行必须是对象')
 if len(source)>20:raise ValueError('每行最多20列')
 for k,v in source.items():
  if not isinstance(k,str) or not k or len(k)>60:raise ValueError('源字段名无效')
  if not (v is None or isinstance(v,(str,int,float)) and not isinstance(v,bool)):raise ValueError('字段值类型无效')
  if isinstance(v,float) and not math.isfinite(v):raise ValueError('字段值必须为有限数值')
  if len(str(v))>500:raise ValueError('单元格最多500字符')
 return source

def convert(value,kind,required):
 if value is None or (isinstance(value,str) and not value.strip()):
  if required:raise ValueError('null_required')
  return None
 if kind=='text':
  v=str(value).strip()
  if len(v)>160:raise ValueError('text_too_long')
  return v
 if kind=='decimal':
  try:
   d=Decimal(str(value))
   if not d.is_finite() or abs(d)>Decimal('1000000000'):raise ValueError('invalid_decimal')
   return str(d.quantize(Decimal('0.000001')))
  except (InvalidOperation,TypeError):raise ValueError('invalid_decimal')
 if kind=='datetime':
  try:
   dt=datetime.fromisoformat(str(value))
   if dt.tzinfo is None:dt=dt.replace(tzinfo=timezone(timedelta(hours=8)))
   return dt.astimezone(timezone.utc).isoformat(timespec='seconds')
  except (ValueError,TypeError):raise ValueError('invalid_datetime')
 raise ValueError('invalid_type')

def convert_row(source,rules):
 target={}
 for rule in rules:
  if rule['source'] not in source:raise ValueError('missing_column:'+rule['source'])
  try:target[rule['target']]=convert(source[rule['source']],rule['type'],rule['required'])
  except ValueError as e:raise ValueError(rule['target']+':'+str(e))
 return target

def natural_key(target):
 return {'sample_code':target.get('sample_code'),'test_name':target.get('test_name')}

def evaluate(rows,rules,corrections=None,previous_rows=None,previous_origins=None,chunk_size=None,progress=None):
 """Convert the frozen source snapshot and decide which rows enter the target.

 Rules that must hold across a correction:
 - rows are visited in source order, so the earliest legal row for a natural
   key always wins; a correction that makes an earlier row legal therefore
   displaces a row that an earlier version had admitted.
 - corrections only replace the source row identified by its 1-based row
   number; all other source rows are untouched.
 """
 validate_rules(rules)
 if not isinstance(rows,list) or len(rows)>2000:raise ValueError('迁移配额超限')
 if chunk_size is not None and not 1<=chunk_size<=500:raise ValueError('迁移配额超限')
 progress=progress or (lambda p:None)
 corrections={int(k):v for k,v in (corrections or {}).items()}
 accepted=[];origins=[];rejected=[];best={};chunks=[]
 step=chunk_size or max(1,len(rows) or 1)
 for start in range(0,len(rows),step):
  chunk=rows[start:start+step]
  for offset,original in enumerate(chunk):
   row_number=start+offset+1
   corrected=row_number in corrections
   source=corrections[row_number] if corrected else original
   try:
    target=convert_row(source,rules)
    key=key_digest(target)
   except (ValueError,TypeError) as e:
    rejected.append({'row_number':row_number,'reason':str(e),'source':source,'corrected':corrected})
    continue
   holder=best.get(key)
   if holder is not None:
    rejected.append({'row_number':row_number,'reason':'duplicate_natural_key:sample_code+test_name','source':source,'corrected':corrected,'duplicate_of':holder['row_number']})
    continue
   best[key]={'row_number':row_number,'target':target}
   accepted.append(target);origins.append(row_number)
  if chunk_size is not None:
   item={'chunk':len(chunks)+1,'processed':start+len(chunk),'accepted':len(accepted),'rejected':len(rejected)}
   chunks.append(item);progress(item)
 if not rows and chunk_size is not None:progress({'chunk':0,'processed':0,'accepted':0,'rejected':0})
 new_by_row={n:t for n,t in zip(origins,accepted)}
 prev_by_row={int(n):t for n,t in zip(previous_origins or [],previous_rows or [])}
 replacements=[]
 rejection_by_number={r['row_number']:r for r in rejected}
 for row_number in sorted(prev_by_row):
  if row_number in new_by_row:continue
  rejection=rejection_by_number.get(row_number,{})
  if rejection.get('duplicate_of') is not None:
   reason='同键更早的补正行已入库，原入库行退出目标'
  else:
   reason='补正后转为隔离：'+str(rejection.get('reason','源行已不可入库'))
  replacements.append({'type':'displaced','row_number':row_number,'key':natural_key(prev_by_row[row_number]),'replaced_by':rejection.get('duplicate_of'),'reason':reason})
 for row_number in sorted(new_by_row):
  if row_number not in prev_by_row:
   replacements.append({'type':'admitted','row_number':row_number,'key':natural_key(new_by_row[row_number]),'reason':'补正后成为该自然键最早合法行' if row_number in corrections else '首次入库'})
  elif prev_by_row[row_number]!=new_by_row[row_number]:
   replacements.append({'type':'updated','row_number':row_number,'key':natural_key(new_by_row[row_number]),'reason':'同一行补正后目标值变化'})
 for row_number in sorted(corrections):
  if row_number in new_by_row or row_number in prev_by_row:continue
  rejection=rejection_by_number.get(row_number,{})
  reason=rejection.get('reason','补正后仍被隔离')
  if rejection.get('duplicate_of') is not None:reason='同键已有更早合法行（第%d行），补正行未入库'%rejection['duplicate_of']
  replacements.append({'type':'rejected','row_number':row_number,'reason':reason})
 return {'accepted_rows':accepted,'accepted_origins':origins,'rejected_rows':rejected,'replacements':replacements,'row_hashes':[digest(r) for r in accepted],'checksum':digest(accepted),'chunks':chunks,'source_count':len(rows),'accepted_count':len(accepted),'rejected_count':len(rejected)}

def migrate(rows,rules,chunk_size,progress=lambda p:None):
 return evaluate(rows,rules,chunk_size=chunk_size,progress=progress)

def apply_corrections(snapshot,corrections,previous=None,chunk_size=500):
 previous=previous or {}
 return evaluate(snapshot['batch']['rows'],snapshot['mapping']['rules'],corrections=corrections,previous_rows=previous.get('rows'),previous_origins=previous.get('origins'),chunk_size=chunk_size)

def make_backup(result,run_id,batch_name,run_version=1):
 return {'version':2,'kind':'logical-json-snapshot','run_id':run_id,'run_version':run_version,'batch_name':batch_name,'rows':result['accepted_rows'],'origins':result.get('accepted_origins',[]),'row_hashes':result['row_hashes'],'checksum':result['checksum']}
def validate_backup(backup,expected,run_id,run_version=None):
 if not isinstance(backup,dict) or backup.get('kind')!='logical-json-snapshot':raise ValueError('备份类型不匹配')
 schema=backup.get('version')
 if schema not in (1,2):raise ValueError('备份版本或运行编号不一致')
 if backup.get('run_id')!=run_id:raise ValueError('备份版本或运行编号不一致')
 if schema==1:
  if run_version not in (None,1):raise ValueError('该备份属于第1版，不能恢复到其它版本')
 else:
  if run_version is not None and backup.get('run_version')!=run_version:raise ValueError('备份属于第%s版，不能恢复到第%s版'%(backup.get('run_version'),run_version))
 rows=backup.get('rows')
 if not isinstance(rows,list) or len(rows)>2000:raise ValueError('备份行数无效')
 if backup.get('checksum')!=digest(rows) or backup.get('checksum')!=expected:raise ValueError('备份checksum不匹配，拒绝恢复')
 if backup.get('row_hashes')!=[digest(r) for r in rows]:raise ValueError('逐行摘要不匹配')
 if any(not isinstance(r,dict) or set(r)!=set(TYPES) for r in rows):raise ValueError('目标schema不一致')
 if len({key_digest(r) for r in rows})!=len(rows):raise ValueError('备份自然键重复')
 origins=backup.get('origins')
 if origins is not None:
  if not isinstance(origins,list) or len(origins)!=len(rows) or len(set(origins))!=len(origins) or any(not isinstance(o,int) or isinstance(o,bool) or o<1 for o in origins):raise ValueError('备份源行号无效')
 return rows,origins
