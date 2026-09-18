"""Astra proposes tasks on known scanned surfaces; code owns all coordinates."""
from __future__ import annotations

import time
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from standardphysics_contracts import SceneGraph, bounds_the_room

from ..models import OpenRouter
from ..router import ChoiceQuestion, Rejected, SystemOneClient, SystemOneError
from ..router.typesafe import TypeSafeRouter

# Explicit hypothetical prop footprints in meters, never scan measurements.
PROP_SIZES = {"medicine": (0.08, 0.08), "computer": (0.32, 0.24),
              "drink": (0.09, 0.09), "book": (0.16, 0.22), "phone": (0.08, 0.15),
              "food": (0.24, 0.24), "bag": (0.28, 0.18), "paper": (0.21, 0.29),
              "personal_item": (0.12, 0.10), "table_edge": (0.05, 0.05)}


class ScanTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,40}$")
    title: str = Field(min_length=1, max_length=90)
    target_node_id: UUID
    action: Literal["pick_up", "operate", "place"]
    prop: Literal["medicine", "computer", "drink", "book", "phone", "food", "bag", "paper", "personal_item", "table_edge"]
    assumption: str = Field(min_length=1, max_length=250)


class TaskSuite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tasks: list[ScanTask] = Field(min_length=10, max_length=10)


INSTRUCTION = (
    "Propose exactly ten everyday accessibility tasks in this scanned room. "
    "Use only supplied table IDs as target_node_id. Include retrieving a medicine bottle "
    "and using a computer. Also vary tasks such as picking up a drink, book or phone, "
    "placing food or a bag, and handling papers. Distribute tasks over the supplied tables. "
    "Give every task a unique short id. Props are hypothetical: the scan identifies furniture, "
    "not medicine, computers or other small items. Explicitly state that assumption in each task. "
    "Do not invent dimensions, object IDs, legal rules, diagnoses or compliance claims. "
    "The simulator will place each hypothetical prop on the measured table and evaluate "
    "route and reach envelopes; it does not prove grasp, keyboard use or task completion."
)


def validate_tasks(suite: TaskSuite, graph: SceneGraph) -> TaskSuite:
    tables = {node.id for node in graph.contents() if node.raw_category == "table"}
    if len({task.id for task in suite.tasks}) != len(suite.tasks):
        raise ValueError("task IDs must be unique")
    if any(task.target_node_id not in tables for task in suite.tasks):
        raise ValueError("every task must target a scanned table")
    if not {"medicine", "computer"} <= {task.prop for task in suite.tasks}:
        raise ValueError("tasks must include medicine and computer use")
    return suite


def propose_tasks(graph: SceneGraph, client: OpenRouter | None = None) -> tuple[TaskSuite, dict]:
    client = client or OpenRouter()
    tables = [node.model_dump(mode="json") for node in graph.nodes
              if not bounds_the_room(node) and node.raw_category == "table"]
    answer = client.structured(INSTRUCTION, {"scan_id": str(graph.scan_id), "tables": tables},
                               TaskSuite.model_json_schema(), "scan_task_suite")
    if isinstance(answer, Rejected):
        raise RuntimeError(  # noqa: TRY004 -- provider failure, not a caller type error
            f"Astra task generation failed: {answer.reason}"
        )
    suite = validate_tasks(TaskSuite.model_validate(answer.payload), graph)
    return suite, {"model": answer.model, "provider": answer.provider,
                   "calls": 1, "source": "Astra via OpenRouter", "props": "hypothetical"}


def choose_task(tasks: list[ScanTask], history: list[dict],
                router: TypeSafeRouter | None = None) -> tuple[ScanTask, dict]:
    """A live typed choice determines which task's variations run next."""
    if len(tasks) == 1:
        return tasks[0], {"source": "last remaining task", "calls": 0}
    router = router or TypeSafeRouter()
    if not router.configured:
        raise RuntimeError("TypeSafe is required for task prioritization")
    state = {
        "goal": "Investigate wheelchair access to everyday tasks, especially medicine and computer use.",
        "remaining_tasks": [task.model_dump(mode="json") for task in tasks],
        "completed_screenings": history,
    }
    question = ChoiceQuestion(
        instructions=(
            "Which remaining everyday task is most useful to investigate next, "
            "considering the user's priorities and completed route/reach evidence? "
            "Select a supplied task ID. This orders geometric tests and does not "
            "determine legal compliance."
        ),
        criteria={task.id: task.title for task in tasks},
    )
    client = SystemOneClient(
        api_key=router.api_key,
        base_url=router.base_url,
        path=router.path,
        model=router.model,
        transport=router.transport,
        budget=router.budget,
    )
    started = time.perf_counter()
    try:
        result = client.evaluate(state, {"task": question})
    except SystemOneError as error:
        raise RuntimeError(
            f"TypeSafe task prioritization failed: {error}"
        ) from error
    answer = result.answers["task"]
    selected = next(
        (task for task in tasks if task.id == getattr(answer, "choice", None)), None
    )
    if selected is None:
        raise ValueError("TypeSafe returned an unknown or missing task; no batch authorized")
    return selected, {
        "source": "typesafe",
        "calls": 1,
        "request": {
            "state": state,
            "questions": {"task": question.model_dump(mode="json")},
        },
        "response": {
            "model": result.model,
            "answers": {"task": answer.model_dump(mode="json")},
            "usage": result.usage.model_dump(mode="json"),
        },
        "elapsed_seconds": time.perf_counter() - started,
    }
