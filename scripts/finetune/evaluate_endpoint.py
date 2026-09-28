"""Score a model behind any OpenAI-compatible endpoint on the held-out room 6 variants.

    python scripts/finetune/evaluate_endpoint.py --base-url http://localhost:8080/v1 --model room6-rl \
        --api-key-env LOCAL_KEY --samples 4

Reports parse rate, hard-constraint pass rate, gate acceptance, mean reward and
mean shortfall recovered, and writes every completion with its verdict.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib

from openai import OpenAI
from room6_data import load
from standardphysics_agents.training.reward import summarize

DEFAULT_DATA = pathlib.Path(__file__).resolve().parents[2] / "runs/finetune/room6/data"


def evaluate(client: OpenAI, model: str, data_dir: pathlib.Path, samples: int, temperature: float, out: pathlib.Path):
    data = load(data_dir)
    records, verdicts = [], []
    for row in data.heldout:
        for index in range(samples):
            reply = client.chat.completions.create(model=model, messages=row["messages"], temperature=temperature,
                                                   max_tokens=512)
            text = reply.choices[0].message.content or ""
            verdict = data.score(text, row["variant"])
            verdicts.append(verdict)
            records.append({"variant": row["variant"], "sample": index, "completion": text, **verdict.as_dict()})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(record) + "\n" for record in records))
    return summarize(verdicts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--data", type=pathlib.Path, default=DEFAULT_DATA)
    parser.add_argument("--samples", type=int, default=4)
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--out", type=pathlib.Path)
    args = parser.parse_args()
    client = OpenAI(base_url=args.base_url, api_key=os.environ.get(args.api_key_env, "none"))
    out = args.out or args.data.parent / "eval" / f"{args.model.replace('/', '_')}.jsonl"
    print(json.dumps(evaluate(client, args.model, args.data, args.samples, args.temperature, out)))


if __name__ == "__main__":
    main()
