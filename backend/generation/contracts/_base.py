"""Shared base class and constrained types for every v3 contract.

Every contract is a pydantic v2 model with ``extra="forbid"``: unknown keys
are rejected, so a typo in seed data or a stale client payload fails loudly
instead of being dropped silently. Fields whose wire name collides with a
``BaseModel`` attribute (``json``, ``schema``, ``register``) use an alias; the
config below validates by either name and always serializes by alias, so the
stored JSON keeps the spec's key names.
"""

from __future__ import annotations

from typing import Annotated
from typing import Final
from typing import Literal

from pydantic import AfterValidator
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import StringConstraints

__all__ = [
    "CONTRACT_CONFIG",
    "ContractModel",
    "CweId",
    "DigestStr",
    "Fraction",
    "NonEmptyStr",
    "NonNegFloat",
    "NonNegInt",
    "PinnedRefStr",
    "PosFloat",
    "PosInt",
    "RefStr",
    "RelPath",
    "Severity",
    "Sha256",
    "SlugStr",
    "StudyKind",
]

CONTRACT_CONFIG: Final = ConfigDict(
    extra="forbid",
    validate_by_name=True,
    validate_by_alias=True,
    serialize_by_alias=True,
)


class ContractModel(BaseModel):
    """Base for every v3 contract: unknown keys are rejected."""

    model_config = CONTRACT_CONFIG


Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
SlugStr = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$", max_length=100)]
RefStr = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*@[1-9][0-9]*$", max_length=140)]
PinnedRefStr = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*@[1-9][0-9]*#[0-9a-f]{12}$", max_length=140),
]
# "sha256:<hex>" (an image id) or "<name>@sha256:<hex>" (a repo digest).
DigestStr = Annotated[str, StringConstraints(pattern=r"^(?:[^@\s]+@)?sha256:[0-9a-f]{64}$")]
CweId = Annotated[str, StringConstraints(pattern=r"^cwe-[1-9][0-9]*$")]
NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]


def _check_rel_path(value: str) -> str:
    if any(part in {"", ".", ".."} for part in value.split("/")):
        msg = f"relative path has an empty, '.' or '..' segment: {value!r}"
        raise ValueError(msg)
    return value


# A relative POSIX path: no leading slash, no backslash, no NUL, no empty / "." / ".." segments.
RelPath = Annotated[
    str,
    StringConstraints(pattern=r"^[^/\\\x00][^\\\x00]*$", max_length=1024),
    AfterValidator(_check_rel_path),
]

PosInt = Annotated[int, Field(gt=0)]
NonNegInt = Annotated[int, Field(ge=0)]
PosFloat = Annotated[float, Field(gt=0)]
NonNegFloat = Annotated[float, Field(ge=0)]
Fraction = Annotated[float, Field(ge=0, le=1)]

Severity = Literal["error", "warn", "info"]
StudyKind = Literal["exploratory", "confirmatory"]
