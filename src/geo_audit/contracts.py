"""Shared strict primitives. No I/O and no network at import time."""

from datetime import timedelta
from typing import Annotated, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)


def _utc(value: AwareDatetime) -> AwareDatetime:
    if value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must have UTC offset zero")
    return value


UTCDateTime = Annotated[AwareDatetime, AfterValidator(_utc)]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")]
SHA256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
NonNegativeInt = Annotated[int, Field(ge=0)]
PositiveSeconds = Annotated[float, Field(gt=0)]
SchemaVersion = Literal["1.0.0", "1.1.0", "1.2.0"]
AlgorithmVersion = Literal["step0-1.0", "step0-1.1", "step0-1.2"]
ConfigVersion = Literal["step0-config-1.0.0", "step0-config-1.1.0", "step0-config-1.2.0"]
CURRENT_SCHEMA_VERSION: Literal["1.2.0"] = "1.2.0"
CURRENT_ALGORITHM_VERSION: Literal["step0-1.2"] = "step0-1.2"
