from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.deps import get_current_admin
from app.schemas import JobListOut, JobOut
from app.services import jobs as jobs_service

router = APIRouter(
    prefix="/api/admin/jobs",
    tags=["admin-jobs"],
    dependencies=[Depends(get_current_admin)],
)


class JobParamsUpdate(BaseModel):
    interval_seconds: int | None = Field(default=None)
    ttl_seconds: int | None = Field(default=None)
    min_refresh_seconds: int | None = Field(default=None)
    soon_seconds: int | None = Field(default=None)


@router.get("", response_model=JobListOut)
def list_jobs() -> JobListOut:
    return JobListOut(items=[JobOut.model_validate(item) for item in jobs_service.list_jobs()])


@router.post("/{job_id}/run", response_model=JobListOut)
async def run_job(job_id: str) -> JobListOut:
    try:
        await jobs_service.run_job(job_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except jobs_service.JobBusyError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    finally:
        jobs_service.reset_loop_wait(job_id)
    return JobListOut(items=[JobOut.model_validate(item) for item in jobs_service.list_jobs()])


@router.patch("/{job_id}", response_model=JobListOut)
def update_job(job_id: str, payload: JobParamsUpdate) -> JobListOut:
    data: dict[str, Any] = payload.model_dump(exclude_unset=True)
    try:
        jobs_service.apply_job_params(job_id, data)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return JobListOut(items=[JobOut.model_validate(item) for item in jobs_service.list_jobs()])
