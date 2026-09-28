from __future__ import annotations

import argparse
import json
from pathlib import Path

from .contracts import LoopConfig, SubmissionTask
from .engine import LoopEngine
from .models import OpenAICompatibleModel, ScriptedDemoModel
from .quality import SectionQualityGate
from .tracing import FanoutTraceSink, JsonlTraceSink, WeaveTraceSink


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a traceable self-improving submission loop.")
    parser.add_argument("brief", type=Path, help="JSON file with objective, required_sections, and optional max_iterations")
    parser.add_argument("--model", choices=("scripted", "openai"), default="scripted")
    parser.add_argument("--model-name", default="gpt-4.1-mini", help="Model name when --model=openai")
    parser.add_argument("--base-url", help="OpenAI-compatible API base URL")
    parser.add_argument("--trace", type=Path, default=Path("runs/latest.jsonl"))
    parser.add_argument("--weave-project", help="Enable W&B Weave tracing for this project")
    parser.add_argument("--print-draft", action="store_true")
    args = parser.parse_args()

    data = json.loads(args.brief.read_text(encoding="utf-8"))
    task = SubmissionTask(data["objective"], tuple(data["required_sections"]))
    config = LoopConfig(max_iterations=data.get("max_iterations", 4))
    model = ScriptedDemoModel() if args.model == "scripted" else OpenAICompatibleModel(args.model_name, args.base_url)
    local_trace = JsonlTraceSink(args.trace)
    traces = FanoutTraceSink(local_trace, WeaveTraceSink(args.weave_project)) if args.weave_project else local_trace
    result = LoopEngine(model, SectionQualityGate(), traces).run(task, config)

    print(json.dumps({"passed": result.passed, "attempts": result.attempts, "report": result.report.to_dict(), "trace": str(args.trace)}, indent=2))
    if args.print_draft:
        print("\n" + result.draft)


if __name__ == "__main__":
    main()
