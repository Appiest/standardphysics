"""Stands in for the SPAR3D furniture script: each object's metrics name the runtime the API handed it."""

import json
import os
import pathlib
import sys

output = pathlib.Path(sys.argv[sys.argv.index("--output-dir") + 1])
for index, argument in enumerate(sys.argv):
    if argument == "--batch-node":
        node = output / sys.argv[index + 1]
        node.mkdir(parents=True, exist_ok=True)
        (node / "metrics.json").write_text(json.dumps({
            "node_id": sys.argv[index + 1], "status": "rejected",
            "python": os.environ["SP_FURNITURE_PYTHON"], "source": os.environ["SP_FURNITURE_SOURCE"],
        }))
