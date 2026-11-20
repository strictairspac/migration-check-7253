from pydantic import BaseModel,Field,model_validator
from src.services.migration import validate_rules,check_source
class Login(BaseModel):email:str;password:str
class BatchInput(BaseModel):
 name:str=Field(min_length=2,max_length=120)
 source_file:str=Field(min_length=1,max_length=160)
 description:str=Field(default='',max_length=500)
 rows:list[dict]=Field(max_length=2000)
 @model_validator(mode='after')
 def bounded(self):
  for index,row in enumerate(self.rows):
   try:check_source(row)
   except ValueError as e:raise ValueError('第%d行：%s'%(index+1,e))
  return self
class MappingInput(BaseModel):
 name:str=Field(min_length=2,max_length=120)
 batch_id:int
 rules:list[dict]
 chunk_size:int=Field(default=50,ge=1,le=500)
 note:str=Field(default='',max_length=500)
 @model_validator(mode='after')
 def rules_valid(self):validate_rules(self.rules);return self
class RunInput(BaseModel):
 name:str=Field(min_length=2,max_length=120)
 mapping_id:int
class CorrectionInput(BaseModel):
 """Correct rejected source rows by 1-based row number, publishing a new version.

 base_version is the version the reviewer looked at.  With concurrent orders
 the service merges when the affected natural keys are untouched; if the same
 item changed meanwhile the order is rejected and must be re-checked.
 """
 base_version:int=Field(ge=1)
 corrections:dict[int,dict]
 reason:str=Field(default='',max_length=500)
 request_id:str|None=Field(default=None,max_length=80)
 @model_validator(mode='after')
 def bounded(self):
  if not self.corrections:raise ValueError('至少补正一行')
  if len(self.corrections)>2000:raise ValueError('单次补正最多2000行')
  for row_number,source in self.corrections.items():
   if row_number<1:raise ValueError('补正行号必须为正整数')
   try:check_source(source)
   except ValueError as e:raise ValueError('第%d行：%s'%(row_number,e))
  return self
class RestoreInput(BaseModel):backup:dict
