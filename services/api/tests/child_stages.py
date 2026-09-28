"""Stages that a job run in its own process builds in the worker tests.

The child finds each factory by its module and name, so they live at module
level here, and a factory only gets the settings to work from.
"""

from __future__ import annotations

import os
import pathlib
import sqlite3
import time

from standardphysics_agents.tracing import traced
from standardphysics_pipeline.discovery import DiscoveryResult

from conftest import no_blender_stages
from standardphysics_api.stages import preview_ledger

HUNG_STAGE_PID = "hung-stage.pid"
LOCKED_ONCE = "locked-once"


def _keep_graph(graph):
    return graph


def _find_nothing(inputs):
    return DiscoveryResult()


def without_blender(settings):
    return no_blender_stages(label=_keep_graph, discover=_find_nothing)


def hanging_at_labeling(settings):
    """Stages whose labeling never returns, after writing the pid of the process it hangs in."""
    pid_file = pathlib.Path(settings.data_dir) / HUNG_STAGE_PID

    def label(graph):
        pid_file.write_text(str(os.getpid()))
        time.sleep(3600)

    return no_blender_stages(label=label, discover=_find_nothing)


def locked_on_the_first_try(settings):
    """Stages whose first rule check across every process meets a database another writer holds."""
    marker = pathlib.Path(settings.data_dir) / LOCKED_ONCE

    def ledger():
        if not marker.exists():
            marker.write_text("locked")
            raise sqlite3.OperationalError("database is locked")
        return preview_ledger()

    return no_blender_stages(ledger_factory=ledger)


@traced("child_stages.label")
def _traced_label(graph):
    return graph


def traced_at_labeling(settings):
    """Stages whose labeling is a traced operation, so a job child's trace can be looked for."""
    return no_blender_stages(label=_traced_label, discover=_find_nothing)
