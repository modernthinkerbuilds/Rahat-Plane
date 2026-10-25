"""agents.kipchoge.evidence — what the coach's answer may cite: evidence
records (PRD v0.3 §12.2), one set per ask, filled by the tools.

Every tool returns records (`agents.kipchoge.records.Record`) and adds
them here; the sanitizer, the checks and the renderer read the same
set. Ids say where a number came from: `run:<uuid>:<path>`,
`athlete:<field>`, `plan:<date>:<field>`, `lib:<type>:<field>`,
`rx:<date>:<field>` (a prescription from the model), `trend:…`,
`clock:…`, `wx:…`, `route:…`, `pop:…`.
"""
from __future__ import annotations

from typing import Any, Iterable

from agents.kipchoge.records import Record, RecordSet


class Evidence:
    def __init__(self, records: RecordSet | None = None):
        self.set = records if records is not None else RecordSet()

    # the harness's expectations
    def get(self, rid: str) -> Record | None:
        return self.set.get(rid)

    def refresh(self) -> None:
        pass                                  # the set is live: the tools add to it as they run

    def close(self) -> None:
        pass

    def for_judge(self, ids: list[str]) -> list[dict]:
        return [r.as_json() for r in self.set if r.id in set(ids)]

    # the coach's
    def add(self, records: Iterable[Record]) -> int:
        n = 0
        for r in records:
            self.set.add(r)
            n += 1
        return n

    def records(self) -> list[Record]:
        return list(self.set)

    def ids(self) -> list[str]:
        return self.set.ids()

    def values(self, prefix: str) -> dict[str, Any]:
        return {r.id: r.value for r in self.set.with_prefix(prefix)}

    def __len__(self) -> int:
        return len(self.set)
