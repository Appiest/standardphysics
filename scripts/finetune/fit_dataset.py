"""Keep only dataset rows whose prompt fits `MAX_PROMPT_TOKENS`, measured with the training renderer.

A room with a hundred fixed fixtures renders to 9,000+ tokens: its SFT row is cut at the 8,192 token
training limit, and the budget preflight prices every row as the longest one. Dropping those rows keeps
the data whole and the estimate honest. The rows removed are moved to `dataset/over_length/`, and
`dataset/fit_report.json` records how many went from each file.

    python scripts/finetune/fit_dataset.py --data runs/finetune/harness
"""

from __future__ import annotations

import argparse
import json
import pathlib

from training.renderer import get_renderer
from training.utils.tokenizers import load_tokenizer

TOKENIZER_MODEL = "Qwen/Qwen3.8-27B"
RENDERER = "qwen3_8_disable_thinking_interleaved"
MAX_PROMPT_TOKENS = 4000
FILES = ("sft", "rl", "heldout")


def _prompt_messages(row: dict) -> list[dict]:
    return [message for message in row["messages"] if message["role"] != "assistant"]


def fit(dataset: pathlib.Path) -> dict:
    renderer = get_renderer(RENDERER, load_tokenizer(TOKENIZER_MODEL))
    report, spill = {}, dataset / "over_length"
    spill.mkdir(exist_ok=True)
    for name in FILES:
        path = dataset / f"{name}.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        lengths = [renderer.build_generation_prompt(_prompt_messages(row)).length for row in rows]
        kept = [row for row, length in zip(rows, lengths, strict=True) if length <= MAX_PROMPT_TOKENS]
        dropped = [row for row, length in zip(rows, lengths, strict=True) if length > MAX_PROMPT_TOKENS]
        (spill / f"{name}.jsonl").write_text("".join(json.dumps(row) + "\n" for row in dropped))
        path.write_text("".join(json.dumps(row) + "\n" for row in kept))
        report[name] = {"kept": len(kept), "dropped": len(dropped), "longest_kept": max(
            (length for length in lengths if length <= MAX_PROMPT_TOKENS), default=0),
            "dropped_webapp": sum(1 for row in dropped if row["variant"].startswith("webapp"))}
    (dataset / "fit_report.json").write_text(json.dumps({"max_prompt_tokens": MAX_PROMPT_TOKENS, **report},
                                                        indent=2) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=pathlib.Path, required=True)
    args = parser.parse_args()
    print(json.dumps(fit(args.data / "dataset"), indent=2))


if __name__ == "__main__":
    main()
