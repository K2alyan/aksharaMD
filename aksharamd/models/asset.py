from pydantic import BaseModel, Field


class Asset(BaseModel):
    id: str
    type: str                       # image, table, figure
    page: int | None = None
    path: str | None = None         # relative to output assets/ dir
    alt_text: str | None = None
    caption: str | None = None
    width: int | None = None
    height: int | None = None
    metadata: dict = Field(default_factory=dict)
