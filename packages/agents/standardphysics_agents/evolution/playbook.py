"""What the router has learned, kept as lessons the next run starts from.

A playbook is a short, versioned list of lessons added to TypeSafe's
instructions. Each lesson says which kinds of room it applies to and which
failed cases taught it. A lesson can change which action the router is likely
to pick and nothing else: every answer still goes through `parse_decision` and
`_authorize`, and every layout still goes through `accepts()`.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

MAX_LESSONS = 8
"""Past eight, the oldest lesson goes. A playbook nobody can read in one glance
has stopped being guidance and started being noise."""

MAX_LESSON_CHARS = 240

DEFAULT_PLAYBOOK_PATH = Path("runs/playbook.json")


@dataclass(frozen=True)
class Lesson:
    text: str
    room_kinds: tuple[str, ...] = ()
    """Where it applies. Empty means every kind of room."""

    learned_from: tuple[str, ...] = ()
    """The case ids whose failures taught it."""

    source: str = "local_reflection"

    def applies_to(self, room_kind: str) -> bool:
        return not self.room_kinds or room_kind in self.room_kinds


@dataclass(frozen=True)
class Playbook:
    version: int = 0
    lessons: tuple[Lesson, ...] = ()

    def for_room(self, room_kind: str) -> list[str]:
        return [lesson.text for lesson in self.lessons if lesson.applies_to(room_kind)]

    def knows(self, text: str) -> bool:
        return any(lesson.text.casefold() == text.casefold() for lesson in self.lessons)

    def with_lesson(self, lesson: Lesson) -> Playbook:
        return Playbook(version=self.version + 1, lessons=(*self.lessons, lesson)[-MAX_LESSONS:])

    def to_json(self) -> dict:
        return {"version": self.version, "lessons": [asdict(lesson) for lesson in self.lessons]}

    @classmethod
    def from_json(cls, data: dict) -> Playbook:
        return cls(
            version=int(data.get("version", 0)),
            lessons=tuple(_lesson_from_json(item) for item in data.get("lessons", [])),
        )


def _lesson_from_json(item: dict) -> Lesson:
    return Lesson(
        text=item["text"],
        room_kinds=tuple(item.get("room_kinds", ())),
        learned_from=tuple(item.get("learned_from", ())),
        source=item.get("source", "local_reflection"),
    )


def load_playbook(path: Path = DEFAULT_PLAYBOOK_PATH) -> Playbook:
    if not path.exists():
        return Playbook()
    return Playbook.from_json(json.loads(path.read_text(encoding="utf-8")))


def save_playbook(playbook: Playbook, path: Path = DEFAULT_PLAYBOOK_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(playbook.to_json(), indent=2) + "\n", encoding="utf-8")
    return path
