from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends

from shared.schemas import Job, JobStatus, JobType

from ..db import Database
from ..deps import get_db, not_found

router = APIRouter(tags=["jobs"])


@router.get("/jobs/{job_id}", response_model=Job)
def get_job(job_id: int, db: Database = Depends(get_db)):
    job = db.get_job(job_id)
    if not job:
        raise not_found("job", job_id)
    return job


@router.get("/jobs", response_model=list[Job])
def list_jobs(type: Optional[JobType] = None, status: Optional[JobStatus] = None, limit: int = 100,
              db: Database = Depends(get_db)):
    return db.list_jobs(type, status, min(max(limit, 1), 500))
