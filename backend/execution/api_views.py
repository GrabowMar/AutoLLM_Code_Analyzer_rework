"""/api/execution/ — capacity, queue, settings (spec §F.4).

Foundation stub: every route answers 501 until Track A implements the router.
"""

from __future__ import annotations

from ninja import Router

router = Router(tags=["execution"])

_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]
_NOT_IMPLEMENTED = {"detail": "Not implemented yet.", "code": "not_implemented"}


@router.api_operation(_METHODS, "/", response={501: dict}, operation_id="execution_root_stub", include_in_schema=False)
def not_implemented_root(request):
    return 501, _NOT_IMPLEMENTED


@router.api_operation(
    _METHODS,
    "/{path:rest}",
    response={501: dict},
    operation_id="execution_stub",
    include_in_schema=False,
)
def not_implemented(request, rest: str):
    return 501, _NOT_IMPLEMENTED
