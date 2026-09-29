"""Crawler interchange types.

This is the shape every crawler still returns, kept identical to
upstream so its parser fixes merge cleanly. It is *not* the public
contract: app/sources/adapter.py converts these into `Quote` objects
using each source's registry entry, dropping the channels a source does
not genuinely publish.

The one change from upstream is `Decimal` in place of `float`. Pydantic
would otherwise coerce an exact Decimal straight back into a binary
float and undo the whole point of the new parser.
"""

from decimal import Decimal
from typing import Dict, Optional

from pydantic import BaseModel, Field


class Rate(BaseModel):
    buy: Optional[Decimal] = Field(
        default=None,
        description="Авах ханш",
        examples=["3430.50"],
    )
    sell: Optional[Decimal] = Field(
        default=None,
        description="Зарах ханш",
        examples=["3450.00"],
    )


class CurrencyDetail(BaseModel):
    cash: Rate = Field(
        default_factory=Rate,
        description="Бэлэн ханш",
    )
    noncash: Rate = Field(
        default_factory=Rate,
        description="Бэлэн бус ханш",
    )


CurrencyRates = Dict[str, CurrencyDetail]
