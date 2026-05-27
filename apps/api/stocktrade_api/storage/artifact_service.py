from __future__ import annotations

from pathlib import Path

from .sqlite import ROOT
from .sqlite_models import Artifact

DEFAULT_ARTIFACT_ROOT = ROOT / "var" / "artifacts"


class ArtifactAccessError(RuntimeError):
    def __init__(self, message: str, *, status_code: int):
        super().__init__(message)
        self.status_code = status_code


def resolve_product_artifact_path(artifact: Artifact, artifact_root: str | Path) -> Path:
    raw_path = Path(artifact.path)
    if not artifact.path.strip():
        raise ArtifactAccessError("artifact path is empty", status_code=404)
    if raw_path.parts and raw_path.parts[0] == "data":
        raise ArtifactAccessError("legacy reference artifact is not product-owned", status_code=409)

    root = _resolve_root(artifact_root)
    if raw_path.is_absolute():
        candidate = raw_path.resolve(strict=False)
    elif len(raw_path.parts) >= 2 and raw_path.parts[:2] == ("var", "artifacts"):
        candidate = (ROOT / raw_path).resolve(strict=False)
        root = (ROOT / "var" / "artifacts").resolve(strict=False)
    else:
        candidate = (root / raw_path).resolve(strict=False)

    if not _is_relative_to(candidate, root):
        raise ArtifactAccessError("artifact path escapes artifact root", status_code=403)
    if not candidate.is_file():
        raise ArtifactAccessError("artifact file not found", status_code=404)
    return candidate


def _resolve_root(path: str | Path) -> Path:
    root = Path(path).expanduser()
    if not root.is_absolute():
        root = ROOT / root
    return root.resolve(strict=False)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
