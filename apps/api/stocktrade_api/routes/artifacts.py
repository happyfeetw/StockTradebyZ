from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from ..dependencies import get_artifact_root, get_run_repository
from ..storage.artifact_service import ArtifactAccessError, resolve_product_artifact_path
from ..storage.run_repository import ArtifactNotFoundError, RunRepository

router = APIRouter(tags=["artifacts"])


@router.get("/artifacts/{artifact_id}")
def get_artifact_file(
    artifact_id: str,
    repository: RunRepository = Depends(get_run_repository),
    artifact_root: Path = Depends(get_artifact_root),
) -> FileResponse:
    try:
        artifact = repository.get_artifact(artifact_id)
        file_path = resolve_product_artifact_path(artifact, artifact_root)
    except ArtifactNotFoundError as exc:
        raise HTTPException(status_code=404, detail="artifact not found") from exc
    except ArtifactAccessError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    return FileResponse(
        file_path,
        media_type=artifact.content_type or "application/octet-stream",
        filename=file_path.name,
    )
