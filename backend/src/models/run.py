from sqlalchemy import Column,Integer,String,JSON,DateTime,func,ForeignKey,Boolean,UniqueConstraint,Index
from src.config.database import Base
class Run(Base):
 __tablename__='runs'
 id=Column(Integer,primary_key=True)
 name=Column(String(120),unique=True,nullable=False)
 batch_id=Column(Integer,ForeignKey('batches.id'),nullable=False)
 mapping_id=Column(Integer,ForeignKey('mappings.id'),nullable=False)
 status=Column(String(20),nullable=False,default='queued')
 cancel_requested=Column(Boolean,nullable=False,default=False)
 snapshot=Column(JSON,nullable=False)
 progress=Column(JSON,nullable=True)
 result=Column(JSON,nullable=True)
 recovery=Column(JSON,nullable=True)
 error=Column(String(1000),nullable=True)
 created_at=Column(DateTime,server_default=func.now())

class TargetRow(Base):
 """Current target rows of a run: always the highest published version.

 Kept byte-compatible with the original schema so an existing database
 volume keeps working without an ALTER TABLE.
 """
 __tablename__='target_rows'
 __table_args__=(UniqueConstraint('run_id','key_digest'),)
 id=Column(Integer,primary_key=True)
 run_id=Column(Integer,ForeignKey('runs.id'),nullable=False)
 ordinal=Column(Integer,nullable=False)
 key_digest=Column(String(64),nullable=False)
 payload=Column(JSON,nullable=False)

class VersionRow(Base):
 """Target rows of one run version.

 Restoring a version's logical backup rewrites only this table for that
 version; TargetRow is rewritten only when the restored version is current.
 """
 __tablename__='version_rows'
 __table_args__=(UniqueConstraint('run_id','version','key_digest',name='uq_version_rows_run_version_key'),)
 id=Column(Integer,primary_key=True)
 run_id=Column(Integer,ForeignKey('runs.id'),nullable=False)
 version=Column(Integer,nullable=False)
 ordinal=Column(Integer,nullable=False)
 key_digest=Column(String(64),nullable=False)
 payload=Column(JSON,nullable=False)

class RunVersion(Base):
 """Immutable published snapshot of one run version.

 Version 1 is the worker's initial publication; every accepted correction
 appends exactly one new version.  Older versions remain readable and keep
 their own target rows and logical backup.
 """
 __tablename__='run_versions'
 __table_args__=(UniqueConstraint('run_id','version',name='uq_run_versions_run_version'),Index('ix_run_versions_request','run_id','request_id'),UniqueConstraint('run_id','request_id',name='uq_run_versions_request'))
 id=Column(Integer,primary_key=True)
 run_id=Column(Integer,ForeignKey('runs.id'),nullable=False)
 version=Column(Integer,nullable=False)
 kind=Column(String(20),nullable=False,default='initial')
 created_at=Column(DateTime,server_default=func.now())
 actor=Column(String(120),nullable=True)
 request_id=Column(String(80),nullable=True)
 request_digest=Column(String(64),nullable=True)
 correction=Column(JSON,nullable=True)
 sources=Column(JSON,nullable=False,default=dict)
 source_count=Column(Integer,nullable=False,default=0)
 chunks=Column(JSON,nullable=False,default=list)
 accepted_rows=Column(JSON,nullable=False)
 accepted_origins=Column(JSON,nullable=False)
 rejected_rows=Column(JSON,nullable=False)
 replacements=Column(JSON,nullable=False,default=list)
 checksum=Column(String(64),nullable=False)
 row_hashes=Column(JSON,nullable=False)
 backup=Column(JSON,nullable=False)
 recovery=Column(JSON,nullable=True)
