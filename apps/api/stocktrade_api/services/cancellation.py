from __future__ import annotations

from collections.abc import Callable

CancellationCheck = Callable[[], bool]


class WorkflowCancellationRequested(RuntimeError):
    pass


def raise_if_cancelled(
    should_cancel: CancellationCheck | None,
    message: str = "workflow cancellation requested",
) -> None:
    if should_cancel is not None and should_cancel():
        raise WorkflowCancellationRequested(message)
