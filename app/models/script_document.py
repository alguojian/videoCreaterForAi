from typing import Literal

from pydantic import BaseModel, Field


class MarkdownScriptRow(BaseModel):
    number: int = Field(ge=1)
    text: str = Field(min_length=1)
    emphasis_terms: list[str] = Field(default_factory=list)
    emphasis_positions: dict[str, Literal["left", "center", "right"]] = Field(
        default_factory=dict
    )
    material_search_terms: list[str] = Field(default_factory=list)


class MarkdownScriptDocument(BaseModel):
    title: str = Field(min_length=1)
    rows: list[MarkdownScriptRow] = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list)

    def script_text(self) -> str:
        return "\n".join(row.text for row in self.rows)
