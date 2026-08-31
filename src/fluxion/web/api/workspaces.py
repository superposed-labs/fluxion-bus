"""Web API for effective workspace permissions and App-owned CRUD."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from fluxion.config.settings import Settings
from fluxion.workspace import WorkspaceAccessService

router = APIRouter()


class WorkspaceAccessInput(BaseModel):
    path: str = Field(min_length=1)
    key: str = ""
    access: str = "read-write"
    default_executor: str = ""
    description: str = ""


class WorkspaceAccessUpdate(BaseModel):
    path: str | None = None
    key: str | None = None
    access: str | None = None
    default_executor: str | None = None
    description: str | None = None


class WorkspaceApprovalInput(BaseModel):
    path: str | None = None
    mode: str | None = None
    client_id: str | None = None


class WorkspaceProjectApprovalInput(WorkspaceApprovalInput):
    access: str | None = None
    key: str = ""
    default_executor: str = ""
    description: str = ""


def _service() -> WorkspaceAccessService:
    # This intentionally does not use web.deps.get_settings(), whose cache is
    # appropriate for the rest of the observation deck but would make legacy
    # permission edits wait for a process restart.
    return WorkspaceAccessService(Settings.reload())


def _bad_request(error: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(error))


@router.get("/workspaces")
def list_workspaces() -> dict[str, Any]:
    return _service().list_workspaces()


@router.get("/workspaces/requests")
def list_workspace_requests() -> dict[str, Any]:
    return {"requests": _service().list_requests()}


@router.get("/workspaces/requests/{request_id}")
def get_workspace_request(request_id: str) -> dict[str, Any]:
    """Read one authorization request by id.

    A caller that was rejected with `authorization_request_id` needs to know
    whether the user answered it. Listing every client's pending request and
    filtering locally would expose unrelated workspace paths to answer a
    question about a single row.
    """
    try:
        result = _service().get_request(request_id)
    except (OSError, RuntimeError, ValueError) as error:
        raise _bad_request(error) from error
    if not result.get("found"):
        raise HTTPException(status_code=404, detail=result)
    return result


@router.post("/workspaces", status_code=201)
def create_workspace(payload: WorkspaceAccessInput) -> dict[str, Any]:
    try:
        return _service().create_entry(
            path=payload.path,
            key=payload.key,
            access=payload.access,
            default_executor=payload.default_executor,
            description=payload.description,
        )
    except (OSError, RuntimeError, ValueError) as error:
        raise _bad_request(error) from error


@router.put("/workspaces/{entry_id}")
def update_workspace(entry_id: str, payload: WorkspaceAccessUpdate) -> dict[str, Any]:
    try:
        return _service().update_entry(
            entry_id,
            path=payload.path,
            key=payload.key,
            access=payload.access,
            default_executor=payload.default_executor,
            description=payload.description,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (OSError, RuntimeError, ValueError) as error:
        raise _bad_request(error) from error


@router.delete("/workspaces/{entry_id}")
def delete_workspace(entry_id: str) -> dict[str, Any]:
    try:
        return _service().delete_entry(entry_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (OSError, RuntimeError, ValueError) as error:
        raise _bad_request(error) from error


@router.post("/workspaces/requests/{request_id}/approve")
def approve_workspace_request(
    request_id: str,
    payload: WorkspaceApprovalInput | None = None,
) -> dict[str, Any]:
    payload = payload or WorkspaceApprovalInput()
    try:
        result = _service().approve_request(
            request_id,
            path=payload.path,
            mode=payload.mode,
            client_id=payload.client_id,
        )
    except (OSError, RuntimeError, ValueError) as error:
        raise _bad_request(error) from error
    if result.get("status") == "not-found":
        raise HTTPException(status_code=404, detail=result)
    return result


@router.post("/workspaces/requests/{request_id}/deny")
def deny_workspace_request(request_id: str) -> dict[str, Any]:
    try:
        result = _service().deny_request(request_id)
    except (OSError, RuntimeError, ValueError) as error:
        raise _bad_request(error) from error
    if result.get("status") == "not-found":
        raise HTTPException(status_code=404, detail=result)
    return result


@router.post("/workspaces/requests/{request_id}/allow-project")
def allow_workspace_request_as_project(
    request_id: str,
    payload: WorkspaceProjectApprovalInput | None = None,
) -> dict[str, Any]:
    payload = payload or WorkspaceProjectApprovalInput()
    try:
        result = _service().allow_request_as_project(
            request_id,
            path=payload.path,
            mode=payload.mode,
            client_id=payload.client_id,
            access=payload.access,
            key=payload.key,
            default_executor=payload.default_executor,
            description=payload.description,
        )
    except (OSError, RuntimeError, ValueError) as error:
        raise _bad_request(error) from error
    if result.get("status") == "not-found":
        raise HTTPException(status_code=404, detail=result)
    if result.get("status") in {"mismatch", "expired", "denied", "consumed", "active"}:
        raise HTTPException(status_code=409, detail=result)
    return result
