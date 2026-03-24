"""Priority and urgency helpers shared by UI-driven runtime scheduling paths."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


DEFAULT_PRIORITY_BUCKET = "visible-page"
DEFAULT_INTENT = "primary-refresh"

PRIORITY_BUCKET_RANKS = {
    "click": 0,
    "focused-child": 1,
    DEFAULT_PRIORITY_BUCKET: 2,
    "background-fill": 3,
}

_INTENT_BASE_SCORES = {
    "symbol-click": 100,
    "run-primary": 96,
    "path-open": 92,
    "page-nav": 88,
    "primary-refresh": 74,
    "edit-refresh": 58,
    "live-watch": 40,
    "probe": 16,
    "background-fill": 8,
}

_PRIORITY_ALIASES = {
    "click": "click",
    "focused-child": "focused-child",
    "visible-page": DEFAULT_PRIORITY_BUCKET,
    "background-fill": "background-fill",
}


def _clamp(value: int, *, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))


def _path_depth(path: str | None) -> int:
    text = str(path or "").strip()
    if not text or text == "/":
        return 0
    return len([token for token in text.split("/") if token])


def normalize_priority_bucket(priority: str | Mapping[str, Any] | PriorityContext | None) -> str:
    if isinstance(priority, PriorityContext):
        return priority.bucket
    if isinstance(priority, Mapping):
        raw_bucket = priority.get("bucket") or priority.get("priority") or priority.get("requested_priority")
        text = str(raw_bucket or DEFAULT_PRIORITY_BUCKET).strip().lower()
        return _PRIORITY_ALIASES.get(text, DEFAULT_PRIORITY_BUCKET)
    text = str(priority or DEFAULT_PRIORITY_BUCKET).strip().lower()
    return _PRIORITY_ALIASES.get(text, DEFAULT_PRIORITY_BUCKET)


def priority_rank(priority: str | Mapping[str, Any] | PriorityContext | None) -> int:
    return PRIORITY_BUCKET_RANKS[normalize_priority_bucket(priority)]


def priority_urgency_score(priority: str | Mapping[str, Any] | PriorityContext | None) -> int:
    if isinstance(priority, PriorityContext):
        return priority.urgency_score
    if isinstance(priority, Mapping):
        raw = priority.get("urgency_score", priority.get("score", 0))
        try:
            return _clamp(int(raw), minimum=0, maximum=100)
        except (TypeError, ValueError):
            return 0
    return 0


def priority_sort_key(priority: str | Mapping[str, Any] | PriorityContext | None) -> tuple[int, int]:
    return (priority_rank(priority), -priority_urgency_score(priority))


def normalize_priority_payload(priority: str | Mapping[str, Any] | PriorityContext | None) -> dict[str, Any]:
    if isinstance(priority, PriorityContext):
        return priority.as_payload()
    return {
        "bucket": normalize_priority_bucket(priority),
        "urgency_score": priority_urgency_score(priority),
    }


def sanitize_interaction_context(raw: Mapping[str, Any] | None, *, path: str = "") -> dict[str, Any]:
    payload = dict(raw or {})
    intent = str(payload.get("intent") or DEFAULT_INTENT).strip().lower() or DEFAULT_INTENT
    source = str(payload.get("source") or "ui").strip().lower() or "ui"
    sequence_raw = payload.get("sequence", 0)
    age_raw = payload.get("age_ms", payload.get("stale_ms", 0))
    try:
        sequence = max(0, int(sequence_raw))
    except (TypeError, ValueError):
        sequence = 0
    try:
        age_ms = max(0, int(age_raw))
    except (TypeError, ValueError):
        age_ms = 0
    path_depth = payload.get("path_depth")
    try:
        normalized_path_depth = max(0, int(path_depth)) if path_depth is not None else _path_depth(path)
    except (TypeError, ValueError):
        normalized_path_depth = _path_depth(path)
    return {
        "intent": intent,
        "source": source,
        "sequence": sequence,
        "age_ms": age_ms,
        "selected": bool(payload.get("selected", False)),
        "visible": bool(payload.get("visible", False)),
        "direct": bool(payload.get("direct", False)),
        "path_depth": normalized_path_depth,
    }


@dataclass(frozen=True)
class PriorityContext:
    bucket: str
    urgency_score: int
    priority_class: str
    intent: str
    source: str
    sequence: int
    age_ms: int
    selected: bool
    visible: bool
    direct: bool
    path_depth: int
    job_kind: str
    enqueue: bool
    ui_awaited: bool

    def as_payload(self) -> dict[str, Any]:
        return {
            "bucket": self.bucket,
            "urgency_score": self.urgency_score,
            "priority_class": self.priority_class,
            "intent": self.intent,
            "source": self.source,
            "sequence": self.sequence,
            "age_ms": self.age_ms,
            "selected": self.selected,
            "visible": self.visible,
            "direct": self.direct,
            "path_depth": self.path_depth,
            "job_kind": self.job_kind,
            "enqueue": self.enqueue,
            "ui_awaited": self.ui_awaited,
        }


def compute_priority_context(
    *,
    job_kind: str,
    enqueue: bool,
    ui_awaited: bool,
    path: str = "",
    interaction: Mapping[str, Any] | None = None,
) -> PriorityContext:
    normalized_interaction = sanitize_interaction_context(interaction, path=path)
    normalized_job_kind = str(job_kind or "run").strip().lower() or "run"
    intent = normalized_interaction["intent"]
    source = normalized_interaction["source"]
    path_depth = int(normalized_interaction["path_depth"])
    base_score = _INTENT_BASE_SCORES.get(intent, _INTENT_BASE_SCORES[DEFAULT_INTENT])
    score = base_score
    if normalized_interaction["direct"]:
        score += 4
    if normalized_interaction["selected"]:
        score += 6
    if normalized_interaction["visible"]:
        score += 4
    if ui_awaited:
        score += 5
    if enqueue:
        score += 4
    score += min(path_depth * 2, 6)
    score -= min(int(normalized_interaction["age_ms"]) // 700, 20)
    if source in {"poll", "socket"}:
        score -= 10
    urgency_score = _clamp(score, minimum=0, maximum=100)

    if normalized_job_kind in {"background-fill", "background"}:
        bucket = "background-fill"
    elif intent in {"symbol-click", "run-primary"}:
        bucket = "click"
    elif intent in {"path-open", "page-nav"}:
        bucket = "focused-child"
    elif intent == "probe":
        bucket = "background-fill"
    elif intent == "live-watch":
        bucket = DEFAULT_PRIORITY_BUCKET if ui_awaited else "background-fill"
    elif intent in {"edit-refresh", "primary-refresh"}:
        bucket = DEFAULT_PRIORITY_BUCKET
    elif path_depth > 0 and ui_awaited:
        bucket = "focused-child"
    elif ui_awaited or enqueue:
        bucket = DEFAULT_PRIORITY_BUCKET
    else:
        bucket = "background-fill"

    if bucket == "background-fill" and urgency_score < 50:
        priority_class = "background"
    elif urgency_score >= 80 or (bucket in {"click", "focused-child"} and ui_awaited):
        priority_class = "interactive"
    else:
        priority_class = "normal"

    return PriorityContext(
        bucket=bucket,
        urgency_score=urgency_score,
        priority_class=priority_class,
        intent=intent,
        source=source,
        sequence=int(normalized_interaction["sequence"]),
        age_ms=int(normalized_interaction["age_ms"]),
        selected=bool(normalized_interaction["selected"]),
        visible=bool(normalized_interaction["visible"]),
        direct=bool(normalized_interaction["direct"]),
        path_depth=path_depth,
        job_kind=normalized_job_kind,
        enqueue=bool(enqueue),
        ui_awaited=bool(ui_awaited),
    )