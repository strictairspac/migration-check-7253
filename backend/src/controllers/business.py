from src.services import batches,mappings,runs
from src.services.audit import log
def save_batch(data,user,item_id=None):
 row=batches.save(data,item_id);log(user.email,'batch.save',row['name']);return row
def save_mapping(data,user,item_id=None):
 row=mappings.save(data,item_id);log(user.email,'mapping.save',row['name']);return row
def start_run(data,user):
 row=runs.create(data);log(user.email,'run.queue',row['name']);return row
def correct_run(item_id,data,user):
 result=runs.correct(item_id,data,actor=user.email)
 if result.get('replayed'):log(user.email,'run.correction.replayed',f'{item_id}:v{result["version"]["version"]}')
 else:log(user.email,'run.correction.publish',f'{item_id}:v{result["version"]["version"]}')
 return result
def restore_run(item_id,data,user):
 proof=runs.restore(item_id,data);log(user.email,'run.restore',f'{item_id}:v{proof["version"]}');return proof
