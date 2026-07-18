"""BaseSchema — the one Pydantic base every DTO in this project inherits from.

Single extension point for shared model config (e.g. per-field aliasing conventions) without
each DTO re-declaring it. NOT `strict=True`: tried it project-wide, but Pydantic v2 strict mode
validates FastAPI request bodies in python-mode (Starlette hands over an already-parsed dict, not
raw JSON bytes), and python-mode strict rejects `str -> UUID` — every UUID path/body param would
422 despite the wire value being the correct JSON string representation. Revisit as a per-field
`Field(strict=True)` opt-in on specific DTOs if a concrete need shows up, not a blanket default.
"""

from __future__ import annotations

from pydantic import BaseModel


class BaseSchema(BaseModel):
    pass
