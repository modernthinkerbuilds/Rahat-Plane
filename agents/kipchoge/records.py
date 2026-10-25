"""agents.kipchoge.records — the evidence record (PRD v0.3 §12.2).

Every number the coach may say is a record: a value, its unit, an 80%
range where it is an estimate, where it came from and which engine
version made it. The model writes `{{r:<id>}}` placeholders and the
renderer fills them (S1); the verifier blocks a number with no record
(E1). The id says where the number came from, in plain words:

    run:<workout uuid>:main_block.hr_avg      a metric of one run
    run:<uuid>:norm.pace_at_ref               a normalized value
    prov:<start utc>:hr_avg                   a provisional run (no file yet)
    athlete:zone2.cap                         a field of the athlete file
    plan:<plan id>:<session id>:minutes       a planned session's field
    lib:z2_strides.strides                    a session-library number
    wx:<uuid>:dew_point                       weather for that run
    trend:<engine version>:<date>:slope_per_week
    clock:days_since_last_run
    delta:<uuid>:<other uuid>:main_block.hr_avg
    msg:<message id>:<n>                      a number he said

Units live in the record, never in the id. Two runs on one day never
collide because the workout UUID is the key.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterator

ENGINE_VERSION = "1.0.0"


@dataclass(frozen=True)
class Record:
    id: str
    value: Any
    unit: str = ""
    lo: float | None = None
    hi: float | None = None
    source: str = ""
    version: str = ENGINE_VERSION
    at: str = ""

    def as_json(self) -> dict:
        d = {"id": self.id, "value": self.value, "unit": self.unit, "source": self.source, "version": self.version}
        if self.lo is not None or self.hi is not None:
            d["lo"], d["hi"] = self.lo, self.hi
        if self.at:
            d["at"] = self.at
        return d

    @property
    def has_range(self) -> bool:
        return self.lo is not None and self.hi is not None


def rid(*parts: Any) -> str:
    """Join id parts with ':'; a part containing ':' is kept as is (a
    timestamp), so `rid("prov", "2026-10-18 18:03:35", "hr_avg")` reads
    `prov:2026-10-18 18:03:35:hr_avg`."""
    return ":".join(str(p) for p in parts if p is not None and str(p) != "")


@dataclass
class RecordSet:
    """The records of one analysis, one card or one answer, by id."""
    records: dict[str, Record] = field(default_factory=dict)

    def add(self, rec: Record) -> Record:
        self.records[rec.id] = rec
        return rec

    def put(self, id: str, value: Any, unit: str = "", *, lo: float | None = None, hi: float | None = None,
            source: str = "", version: str = ENGINE_VERSION, at: str = "") -> Record:
        return self.add(Record(id, value, unit, lo, hi, source, version, at))

    def get(self, id: str) -> Record | None:
        return self.records.get(id)

    def value(self, id: str, default: Any = None) -> Any:
        r = self.records.get(id)
        return r.value if r is not None else default

    def require(self, id: str) -> Record:
        r = self.records.get(id)
        if r is None:
            raise KeyError(f"no evidence record {id!r}")
        return r

    def ids(self) -> list[str]:
        return sorted(self.records)

    def __contains__(self, id: str) -> bool:
        return id in self.records

    def __iter__(self) -> Iterator[Record]:
        return iter(self.records.values())

    def __len__(self) -> int:
        return len(self.records)

    def with_prefix(self, prefix: str) -> list[Record]:
        return [r for k, r in sorted(self.records.items()) if k.startswith(prefix)]

    def merge(self, other: "RecordSet") -> "RecordSet":
        self.records.update(other.records)
        return self

    def to_json(self) -> list[dict]:
        return [r.as_json() for _, r in sorted(self.records.items())]

    @classmethod
    def from_json(cls, rows: list[dict]) -> "RecordSet":
        rs = cls()
        for d in rows or []:
            rs.add(Record(d["id"], d.get("value"), d.get("unit", ""), d.get("lo"), d.get("hi"), d.get("source", ""),
                          d.get("version", ENGINE_VERSION), d.get("at", "")))
        return rs

    def dumps(self) -> str:
        return json.dumps(self.to_json(), ensure_ascii=False)
