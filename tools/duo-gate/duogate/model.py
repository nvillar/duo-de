"""Results vocabulary: PASS / FAIL / INCOMPLETE, verdicts with reasons, checks with repetition counts."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

PASS, FAIL, INCOMPLETE = "PASS", "FAIL", "INCOMPLETE"
_RANK = {PASS: 0, INCOMPLETE: 1, FAIL: 2}


def worst(*statuses: str) -> str:
    """FAIL beats INCOMPLETE beats PASS: a definite failure is reported even when other evidence is missing."""
    out = PASS
    for s in statuses:
        if _RANK[s] > _RANK[out]:
            out = s
    return out


class EvidenceError(Exception):
    """Evidence is missing, stale, ambiguous or unparsable. Always maps to INCOMPLETE, never to PASS or FAIL."""


_SHORT = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")
_LONG = re.compile(r"Rect\((-?\d+), (-?\d+) - (-?\d+), (-?\d+)\)")


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    right: int
    bottom: int

    @classmethod
    def parse(cls, text: str) -> "Rect":
        """Rect.toShortString() form "[l,t][r,b]" or Rect.toString() form "Rect(l, t - r, b)"."""
        m = _SHORT.fullmatch(text.strip()) or _LONG.fullmatch(text.strip())
        if not m:
            raise EvidenceError("not a rectangle: %r" % text)
        return cls(*(int(g) for g in m.groups()))

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def empty(self) -> bool:
        return self.width <= 0 or self.height <= 0

    def center(self) -> tuple[int, int]:
        return (self.left + self.right) // 2, (self.top + self.bottom) // 2

    def contains_point(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom

    def contains(self, other: "Rect") -> bool:
        return (self.left <= other.left and self.top <= other.top
                and other.right <= self.right and other.bottom <= self.bottom)

    def intersect(self, other: "Rect") -> "Rect":
        return Rect(max(self.left, other.left), max(self.top, other.top),
                    min(self.right, other.right), min(self.bottom, other.bottom))

    def intersects(self, other: "Rect") -> bool:
        return not self.intersect(other).empty

    def __str__(self) -> str:
        return "[%d,%d][%d,%d]" % (self.left, self.top, self.right, self.bottom)


@dataclass
class Verdict:
    """The outcome of one assertion or one attempt: a status and the reasons behind it."""
    status: str = PASS
    reasons: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def fail(self, reason: str) -> "Verdict":
        self.status = worst(self.status, FAIL)
        self.reasons.append("FAIL: " + reason)
        return self

    def incomplete(self, reason: str) -> "Verdict":
        self.status = worst(self.status, INCOMPLETE)
        self.reasons.append("INCOMPLETE: " + reason)
        return self

    def note(self, text: str) -> "Verdict":
        self.notes.append(text)
        return self

    def merge(self, other: "Verdict") -> "Verdict":
        self.status = worst(self.status, other.status)
        self.reasons.extend(other.reasons)
        self.notes.extend(other.notes)
        return self

    @property
    def ok(self) -> bool:
        return self.status == PASS


@dataclass
class Attempt:
    index: int
    status: str
    reasons: list[str]
    notes: list[str]
    marker: str | None = None
    evidence: list[str] = field(default_factory=list)

    def fail(self, reason: str) -> None:
        """Evidence found after the attempt was recorded (e.g. the state it left behind) that contradicts it."""
        self.status = FAIL
        self.reasons.append("FAIL: " + reason)

    def incomplete(self, reason: str) -> None:
        """Evidence found after the attempt was recorded that could not be judged: never better than INCOMPLETE,
        and an existing FAIL stays FAIL."""
        self.status = worst(self.status, INCOMPLETE)
        self.reasons.append("INCOMPLETE: " + reason)

    def to_json(self) -> dict:
        return {"index": self.index, "status": self.status, "reasons": self.reasons, "notes": self.notes,
                "marker": self.marker, "evidence": self.evidence}


@dataclass
class Check:
    """One gate check, evaluated over a required number of repetitions.

    Automated checks PASS only with every required repetition evidenced and passing. Manual checks (operator-observed
    outcomes: lit/dark panels, physical folds, real edge gestures, visual animation) can never PASS here: their best
    result is INCOMPLETE "manual review required", but contradicting machine evidence still makes them FAIL.
    """
    id: str
    gate: str
    title: str
    required: int = 1
    manual: bool = False
    attempts: list[Attempt] = field(default_factory=list)
    skipped: str | None = None

    def add(self, verdict: Verdict, marker: str | None = None, evidence: list[str] | None = None) -> Attempt:
        a = Attempt(len(self.attempts) + 1, verdict.status, list(verdict.reasons), list(verdict.notes), marker,
                    list(evidence or []))
        self.attempts.append(a)
        return a

    def skip(self, reason: str) -> "Check":
        self.skipped = reason
        return self

    def counts(self) -> dict:
        c = {PASS: 0, FAIL: 0, INCOMPLETE: 0}
        for a in self.attempts:
            c[a.status] += 1
        return c

    @property
    def status(self) -> str:
        c = self.counts()
        if c[FAIL]:
            return FAIL
        if self.skipped is not None or c[INCOMPLETE] or not self.attempts or len(self.attempts) < self.required:
            return INCOMPLETE
        if self.manual:
            return INCOMPLETE
        return PASS

    def summary_reason(self) -> str:
        if self.skipped is not None:
            return "not run: " + self.skipped
        c = self.counts()
        parts = []
        if not self.attempts:
            parts.append("no repetition attempted")
        elif len(self.attempts) < self.required:
            parts.append("%d of %d required repetitions evidenced" % (len(self.attempts), self.required))
        if c[INCOMPLETE]:
            parts.append("%d repetition(s) incomplete" % c[INCOMPLETE])
        if c[FAIL]:
            parts.append("%d repetition(s) failed" % c[FAIL])
        if self.manual and not c[FAIL]:
            parts.append("manual review required")
        return "; ".join(parts)

    def to_json(self) -> dict:
        c = self.counts()
        return {"id": self.id, "gate": self.gate, "title": self.title, "status": self.status,
                "manual": self.manual, "required": self.required, "attempted": len(self.attempts),
                "pass": c[PASS], "fail": c[FAIL], "incomplete": c[INCOMPLETE], "skipped": self.skipped,
                "summary": self.summary_reason(), "attempts": [a.to_json() for a in self.attempts]}
