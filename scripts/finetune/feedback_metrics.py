"""Per-arm numbers for the feedback experiment, from the saved transcripts.

A room's record holds up to four scored proposals. In a chain the first
accepted one ends it; in best-of-4 the four are independent, and the room
keeps the accepted one with the highest reward, as a best-of-n pick would.
"""

from __future__ import annotations

from collections import Counter

from standardphysics_agents.training.snapped_reward import Verdict, summarize


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _share(count: int, total: int) -> float | None:
    return round(count / total, 4) if total else None


def chosen(record: dict) -> dict | None:
    """The proposal the room keeps: the best accepted one."""
    accepted = [attempt for attempt in record["rounds"] if attempt["gate_accepts"]]
    return max(accepted, key=lambda attempt: attempt["reward"], default=None)


def first_accepted_round(record: dict) -> int | None:
    return next((attempt["round"] for attempt in record["rounds"] if attempt["gate_accepts"]), None)


def cleared(record: dict) -> bool:
    return any(attempt["gate_accepts"] and attempt["fixable_left"] == 0 for attempt in record["rounds"])


def rejections_by_round(records: list[dict]) -> dict[str, dict[str, int]]:
    table: dict[str, Counter] = {}
    for record in records:
        for attempt in record["rounds"]:
            table.setdefault(str(attempt["round"]), Counter())[attempt["category"]] += 1
    return {round_index: dict(counts.most_common()) for round_index, counts in sorted(table.items())}


def until_accepted(record: dict) -> list[dict]:
    """The calls a room makes up to and including its first accepted one, as a chain would stop."""
    first = first_accepted_round(record)
    return [attempt for attempt in record["rounds"] if first is None or attempt["round"] <= first]


def open_room_rounds(records: list[dict]) -> dict[str, dict]:
    """Per round, among rooms with nothing accepted yet: how many called, and what share of each outcome."""
    table: dict[str, Counter] = {}
    for record in records:
        for attempt in until_accepted(record):
            table.setdefault(str(attempt["round"]), Counter())[attempt["category"]] += 1
    return {round_index: {"open_rooms": sum(counts.values()),
                          **{category: _share(count, sum(counts.values())) for category, count in counts.most_common()}}
            for round_index, counts in sorted(table.items())}


def next_outcome_after(records: list[dict]) -> dict[str, dict]:
    """For each kind of refusal, what the room's next call did: the effect of hearing that reason."""
    table: dict[str, Counter] = {}
    for record in records:
        calls = until_accepted(record)
        for previous, following in zip(calls, calls[1:]):
            table.setdefault(previous["category"], Counter())[following["category"]] += 1
    return {category: {"calls": sum(counts.values()), **{k: _share(v, sum(counts.values()))
                                                          for k, v in counts.most_common()}}
            for category, counts in sorted(table.items(), key=lambda item: -sum(item[1].values()))}


def _verdict(attempt: dict) -> Verdict:
    fields = Verdict.__dataclass_fields__
    return Verdict(**{key: value for key, value in attempt.items() if key in fields})


def arm_metrics(records: list[dict]) -> dict:
    rooms = len(records)
    picks = [chosen(record) for record in records]
    accepted = [pick for pick in picks if pick is not None]
    rounds = [first_accepted_round(record) for record in records]
    fixable = sum(1 for record in records if record["ceiling_fixable"])
    clearable = sum(1 for record in records if record["ceiling_all_clear"])
    accepted_count, cleared_count = len(accepted), sum(1 for record in records if cleared(record))
    return {
        "rooms": rooms,
        "calls": sum(len(record["rounds"]) for record in records),
        "accepted_rooms": accepted_count,
        "accepted_share": _share(accepted_count, rooms),
        "accepted_of_ceiling": _share(accepted_count, fixable),
        "accepted_where_search_failed": sum(1 for record, pick in zip(records, picks)
                                            if pick is not None and not record["ceiling_fixable"]),
        "accepted_where_search_succeeded": sum(1 for record, pick in zip(records, picks)
                                               if pick is not None and record["ceiling_fixable"]),
        "cleared_where_search_cleared": sum(1 for record in records if record["ceiling_all_clear"] and cleared(record)),
        "cleared_rooms": cleared_count,
        "cleared_share": _share(cleared_count, rooms),
        "cleared_of_ceiling": _share(cleared_count, clearable),
        "ceiling_fixable_rooms": fixable,
        "ceiling_all_clear_rooms": clearable,
        "mean_recovered_among_accepted": _mean([pick["shortfall_recovered"] for pick in accepted]),
        "mean_rounds_to_accept": _mean([value for value in rounds if value is not None]),
        "mean_usability_among_accepted": _mean([pick["usability"] for pick in accepted
                                                if pick["usability"] is not None]),
        "mean_room_reward": _mean([pick["reward"] if pick else 0.0 for pick in picks]),
        "per_call": summarize([_verdict(attempt) for record in records for attempt in record["rounds"]]),
        "rejections_by_round": rejections_by_round(records),
        "open_rooms_by_round": open_room_rounds(records),
        "next_outcome_after": next_outcome_after(records),
    }
