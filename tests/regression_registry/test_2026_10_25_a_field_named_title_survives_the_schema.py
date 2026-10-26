"""2026-10-25 — the harness's schema converter dropped every key named
"title", a field called `title` included, while `required` still named
it; Gemini refused the running coach's whole answer schema with
"properties[sections].items.required[0]: property is not defined" and
every analysis fell to the floor. Only a schema's own title (metadata)
is dropped now; a field named title survives."""
from __future__ import annotations

from pydantic import BaseModel, Field

from core import llm_chat


class Section(BaseModel):
    title: str
    lines: list[str] = Field(default_factory=list)


class Answer(BaseModel):
    headline: str
    sections: list[Section] = Field(default_factory=list)


def test_a_field_named_title_is_kept_and_required_names_only_defined_properties():
    schema = llm_chat.schema_for(Answer)
    items = schema["properties"]["sections"]["items"]
    assert "title" in items["properties"], items
    assert set(items.get("required", [])) <= set(items["properties"])
    assert "title" not in schema and "title" not in items            # the metadata titles are still dropped
    assert "title" not in items["properties"]["title"]
    api = llm_chat.to_openapi(schema)
    sec = api["properties"]["sections"]["items"]
    assert set(sec.get("required", [])) <= set(sec["properties"]) and "title" in sec["properties"]
