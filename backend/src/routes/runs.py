import json
from fastapi import APIRouter,Depends,Query
from fastapi.responses import Response
from src.middlewares.auth import current,writer,admin
from src.schemas.business import RunInput,RestoreInput,CorrectionInput
from src.services import runs
from src.controllers.business import start_run,correct_run,restore_run
from src.services.audit import log
router=APIRouter()
@router.get('/runs')
def listing(u=Depends(current)):return runs.list_items()
@router.post('/runs')
def create(data:RunInput,u=Depends(writer)):return start_run(data,u)
@router.get('/runs/{item_id}')
def detail(item_id:int,u=Depends(current)):return runs.detail(item_id)
@router.post('/runs/{item_id}/cancel')
def cancel(item_id:int,u=Depends(writer)):
 r=runs.cancel(item_id);log(u.email,'run.cancel',str(item_id));return r
@router.get('/runs/{item_id}/versions')
def versions(item_id:int,u=Depends(current)):return runs.versions(item_id)
@router.get('/runs/{item_id}/versions/{version}')
def version(item_id:int,version:int,u=Depends(current)):return runs.version_detail(item_id,version)
@router.post('/runs/{item_id}/corrections')
def correct(item_id:int,data:CorrectionInput,u=Depends(writer)):return correct_run(item_id,data,u)
@router.get('/runs/{item_id}/targets')
def targets(item_id:int,version:int|None=Query(default=None),u=Depends(current)):return runs.targets(item_id,version)
@router.get('/runs/{item_id}/backup')
def backup(item_id:int,version:int|None=Query(default=None),u=Depends(current)):
 body=runs.backup(item_id,version)
 suffix='' if version is None else '-v%d'%version
 return Response(json.dumps(body,ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="backup-{item_id}{suffix}.json"'})
@router.get('/runs/{item_id}/export')
def export(item_id:int,u=Depends(current)):
 return Response(json.dumps(runs.detail(item_id),ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="reconciliation-{item_id}.json"'})
@router.get('/runs/{item_id}/versions/{version}/export')
def export_version(item_id:int,version:int,u=Depends(current)):
 body=runs.version_detail(item_id,version)
 return Response(json.dumps(body,ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="reconciliation-{item_id}-v{version}.json"'})
@router.post('/runs/{item_id}/restore')
def restore(item_id:int,data:RestoreInput,u=Depends(admin)):return restore_run(item_id,data,u)
