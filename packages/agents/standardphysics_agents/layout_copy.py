"""Every sentence a shop owner reads about moving things: proposed layouts, their own requests, replies to an ask.

Kept apart from the finding copy in `copy.py` under the same rules: short
sentences, ordinary words, inches, and a next step whenever something could not
be done.
"""

from __future__ import annotations

from .numbers import inches, things

NO_ARRANGEMENT = "We couldn't find an arrangement that works."
"""The sentence the plan prescribes for an exhausted search, section 2.

It is always followed by one specific thing the owner could allow, because a
dead end with no next move is not an answer.
"""

RATIONALES = {
    "split_the_gap": "Move {what} {distance} apart.",
    "move_one_aside": "Move {what} {distance} over.",
    "stagger": "Step {what} {distance} apart along the aisle, so they stop lining up.",
    "turn_one": "Turn {what} a quarter turn.",
    "arrange_room": "Set the tables square to the walls with their chairs around them, and keep the aisles open.",
}

RELAXATIONS = {
    "unlock": "Can {what} be moved? Unlock it and we'll try again.",
    "set_aside": "Try it without {what}?",
}


def proposal_rationale(strategy: str, labels: list[str], inches_moved: float) -> str:
    """What the owner sees on the before and after, in one sentence."""
    template = RATIONALES.get(strategy, "Move {what} {distance}.")
    return template.format(
        what=things(labels) or "it", distance=inches(inches_moved)
    )


REQUEST_SENTENCES = {
    "back": "Move {what} {distance} back.",
    "front": "Move {what} {distance} toward the door.",
    "left": "Move {what} {distance} to the left.",
    "right": "Move {what} {distance} to the right.",
    "apart": "Move {what} {distance} apart.",
    "together": "Move {what} {distance} closer together.",
}

ASK_REPLIES = {
    "asked_to_move_something_fixed": (
        "That one is built in. Tell us which of the loose pieces to move."
    ),
    "asked_about_something_that_is_not_here": (
        "Point at the piece you mean in the plan and we will look at it."
    ),
    "asked_to_move_something_it_also_locked": (
        "That piece is on both lists. Say whether it moves or stays."
    ),
    "question_names_nothing": (
        "Name a piece of furniture and ask again. The counter, the tables, "
        "the chairs."
    ),
    "question_does_not_say_which_dimension": (
        "Say whether you mean how tall, how wide or how deep."
    ),
    "question_names_only_one_end": (
        "Name both ends and we will measure between them."
    ),
    "question_does_not_say_how_big": (
        "Tell us how long it is in inches and we will see where it goes."
    ),
    "question_does_not_say_which_way": (
        "Say which way: back, front, left, right, apart or together."
    ),
    "measurement_out_of_range": "Give us the size in inches and we will try it.",
    "restatement_too_long": "Ask it in one sentence and we will have a go.",
}

ASK_DEFAULT_REPLY = (
    "We did not follow that one. You can ask how many of something you have, "
    "how big it is, where it is, what shape it sits in, or whether something "
    "you are thinking of buying would fit."
)


def request_rationale(direction: str, labels: list[str], inches_moved: float) -> str:
    """What will happen, with the distance, for the owner to approve."""
    template = REQUEST_SENTENCES.get(direction, "Move {what} {distance}.")
    return template.format(
        what=things(labels) or "it", distance=inches(inches_moved)
    )


def no_room_for_request(labels: list[str], blocker: str | None) -> str:
    """Why the pieces stopped where they did, and what to try instead."""
    what = things(labels) or "they"
    subject = what[:1].upper() + what[1:]
    one = len(labels) == 1
    if blocker:
        runs = "runs" if one else "run"
        return (
            f"{subject} {runs} into the {blocker.casefold()} first. "
            "Try a shorter move, or fewer pieces."
        )
    is_are = "is" if one else "are"
    return (
        f"{subject} {is_are} blocked on every side. "
        "Try a shorter move, or fewer pieces."
    )


def ask_reply(reason: str) -> str:
    """What to say when a request could not be read, as something to do."""
    return ASK_REPLIES.get(reason, ASK_DEFAULT_REPLY)


def relaxation_question(kind: str, labels: list[str]) -> str:
    """One specific thing to allow, phrased as a choice the owner makes."""
    return RELAXATIONS[kind].format(what=things(labels) or "one piece")


def no_arrangement(question: str | None) -> str:
    return f"{NO_ARRANGEMENT} {question}" if question else NO_ARRANGEMENT


REPORT_READY = "Your report is ready."


def escalation_note(count: int) -> str:
    """What happens next to something furniture cannot fix."""
    if count == 1:
        return "An accessibility professional will look at this one."
    return f"An accessibility professional will look at these {count}."
