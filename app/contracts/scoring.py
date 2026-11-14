"""Scoring contracts (spec section 5.2).

Only stdlib and pydantic may be imported here (app.contracts is a leaf package).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class NumericParsePolicy(BaseModel):
    """Policy for parsing short numeric answers (Part III THPTQG 2025+).

    Guards against Vietnamese thousand-separator ambiguity (e.g. ``1.000``).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    decimal_sep: Literal["COMMA_OR_DOT", "COMMA_ONLY", "DOT_ONLY"] = "COMMA_OR_DOT"
    allow_thousand_sep: bool = False
    max_chars: int = Field(default=4, ge=1, le=12)
    tolerance: Decimal = Field(default=Decimal("0.01"), ge=Decimal("0"))
