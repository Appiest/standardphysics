"""Does the checker's feedback help the rearrangement model? Four model calls per room in every arm.

    A        run 1 RL, best of 4 independent samples (the saved held-out eval, rescored; no spend)
    A_fresh  the same, sampled again
    B        run 1 RL, one chain of up to 4 rounds, each refusal answered with the checker's reasons
    C        B, with the open floor rectangles added to the first prompt
    E        the untrained base model, as B
    D_B, D_C a strong general model through OpenRouter, as B and C, on a stratified subset

Qwen is sampled through the serverless training API from a snapshot of the
saved training state, exactly as the held-out evaluations were. Nothing is
deployed and nothing is trained. Every call is charged to a ledger before it
is made, pessimistically, and the run stops at the budget.

    python scripts/finetune/feedback_eval.py run --arm B
    python scripts/finetune/feedback_eval.py results
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import threading
import time
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from feedback_loop import Chain, diagnose, run_chains, with_open_floor
from feedback_metrics import arm_metrics
from multiroom_train_data import load

HOME = pathlib.Path(os.environ.get("SP_FINETUNE_HOME", pathlib.Path.home() / "sp-finetune"))
DATA = HOME / "repo/runs/finetune/multiroom/v2"
CEILING = HOME / "synthetic/runs/finetune/synthetic/ceiling.json"
OUT = pathlib.Path("runs/finetune/feedback")
ROUNDS = 4
TEMPERATURE = 0.7
MAX_SAMPLE_TOKENS = 384
FEEDBACK_TOKEN_ALLOWANCE = 400
FIREWORKS_LIMIT = 6.0
OPENROUTER_LIMIT = 10.0
FIREWORKS_RATES = {"prefill": 1.86, "sample": 5.595}
SUBSET_SIZE = 30
OPENROUTER_MAX_TOKENS = 4000
OPENROUTER_REASONING = {"effort": "low"}
ESTIMATE_EXIT_CODE = 4


class BudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class ArmSpec:
    backend: str
    model: str = "rl"
    hint: bool = False
    independent: bool = False
    rooms: str = "all"


ARMS = {
    "A": ArmSpec("rescore", independent=True),
    "A_fresh": ArmSpec("fireworks", independent=True),
    "B": ArmSpec("fireworks"),
    "C": ArmSpec("fireworks", hint=True),
    "E": ArmSpec("fireworks", model="base"),
    "D_B": ArmSpec("openrouter", model="openrouter", rooms="subset"),
    "D_C": ArmSpec("openrouter", model="openrouter", hint=True, rooms="subset"),
    "B_repeat": ArmSpec("fireworks"),
    "C_repeat": ArmSpec("fireworks", hint=True),
    "D_B_rest": ArmSpec("openrouter", model="openrouter", rooms="rest"),
    "D_C_rest": ArmSpec("openrouter", model="openrouter", hint=True, rooms="rest"),
}


# --- ledger -------------------------------------------------------------------


class Ledger:
    """Running spend, one file per provider, reread before every change so two queues never overwrite each other.

    The lock covers the threads of one queue; the file per provider covers the two queues.
    """

    def __init__(self, directory: pathlib.Path):
        self.directory = directory
        self.lock = threading.Lock()

    def path(self, provider: str) -> pathlib.Path:
        return self.directory / f"spend_{provider}.json"

    def load(self, provider: str) -> dict:
        path = self.path(provider)
        return json.loads(path.read_text()) if path.exists() else {"arms": {}}

    def dollars(self, provider: str) -> float:
        return self.load(provider).get("dollars", 0.0)

    def arm(self, provider: str, arm: str) -> dict:
        return self.load(provider)["arms"].get(arm, {})

    def add(self, provider: str, arm: str, **amounts) -> None:
        with self.lock:
            state = self.load(provider)
            for bucket in (state, state["arms"].setdefault(arm, {})):
                for key, value in amounts.items():
                    bucket[key] = round(bucket.get(key, 0) + value, 6)
            self.save(provider, state)

    def note(self, provider: str, arm: str, **fields) -> None:
        with self.lock:
            state = self.load(provider)
            state["arms"].setdefault(arm, {}).update(fields)
            self.save(provider, state)

    def save(self, provider: str, state: dict) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.path(provider).with_suffix(".tmp")
        temporary.write_text(json.dumps(state, indent=2) + "\n")
        os.replace(temporary, self.path(provider))


def fireworks_dollars(prefill: int, sample: int) -> float:
    return (prefill * FIREWORKS_RATES["prefill"] + sample * FIREWORKS_RATES["sample"]) / 1e6


def chain_worst_case_tokens(prompt_tokens: list[int], rounds: int = ROUNDS,
                            max_sample: int = MAX_SAMPLE_TOKENS) -> tuple[int, int]:
    """Every room runs every round, every answer is as long as allowed, every prompt token is uncached."""
    growth = max_sample + FEEDBACK_TOKEN_ALLOWANCE
    prefill = sum(tokens * rounds + growth * rounds * (rounds - 1) // 2 for tokens in prompt_tokens)
    return prefill, len(prompt_tokens) * rounds * max_sample


# --- rooms --------------------------------------------------------------------


def stratified_subset(variants: list[str], fixable: dict[str, bool], size: int = SUBSET_SIZE) -> list[str]:
    """`size` rooms, fixable and unfixable in their held-out proportion, picked by a stable hash."""
    ordered = sorted(variants, key=lambda variant: zlib.crc32(variant.encode()))
    can = [variant for variant in ordered if fixable.get(variant)]
    cannot = [variant for variant in ordered if not fixable.get(variant)]
    take = round(size * len(can) / max(1, len(variants)))
    return sorted(can[:take] + cannot[:size - take])


def load_ceiling(path: pathlib.Path) -> dict[str, dict]:
    return {row["variant_id"]: row for row in json.loads(path.read_text())["rooms"]}


# --- proposers ----------------------------------------------------------------


class FireworksProposer:
    """Qwen3.8 27B through the serverless training API, from a saved state or the bare base model."""

    def __init__(self, model: str, progress_path: pathlib.Path, ledger: Ledger, arm: str):
        from fireworks.training.sdk import FiretitanSamplingParams, FiretitanServiceClient
        from serverless_train import BASE_MODEL, RENDERER, SERVERLESS_URL, TOKENIZER_MODEL
        from training.renderer import get_renderer, get_text_content
        from training.utils.tokenizers import load_tokenizer

        self.ledger, self.arm, self.text_content = ledger, arm, get_text_content
        self.tokenizer = load_tokenizer(TOKENIZER_MODEL)
        self.renderer = get_renderer(RENDERER, self.tokenizer)
        self.service = FiretitanServiceClient(api_key=os.environ["FIREWORKS_API_KEY"], base_url=SERVERLESS_URL)
        if model == "base":
            client = self.service.create_lora_training_client(base_model=BASE_MODEL, rank=32, alpha=64)
        else:
            state = json.loads(progress_path.read_text())["steps"]["rl"]["state_ref"]
            client = self.service.create_training_client_from_state(state)
        self.snapshot = client.save_weights_for_sampler(f"fb-{model}").result().path
        self.sampler = self.service.create_sampling_client(model_path=self.snapshot, tokenizer=self.tokenizer)
        self.params = FiretitanSamplingParams(max_tokens=MAX_SAMPLE_TOKENS, temperature=TEMPERATURE,
                                              stop=self.renderer.get_stop_sequences())
        print(f"sampling {model} from {self.snapshot}", flush=True)

    def prompt_tokens(self, messages: list[dict]) -> int:
        return self.renderer.build_generation_prompt(messages).length

    def __call__(self, conversations: list[list[dict]], samples: int = 1) -> list[list[str]]:
        prompts = [self.renderer.build_generation_prompt(messages) for messages in conversations]
        prefill = sum(prompt.length for prompt in prompts) * samples
        worst = fireworks_dollars(prefill, len(prompts) * samples * MAX_SAMPLE_TOKENS)
        if self.ledger.dollars("fireworks") + worst > FIREWORKS_LIMIT:
            raise BudgetExceeded(f"next call could reach ${self.ledger.dollars('fireworks') + worst:.2f}")
        futures = [self.sampler.sample(prompt=prompt, num_samples=samples, sampling_params=self.params)
                   for prompt in prompts]
        groups = [list(getattr(future.result(timeout=1800), "sequences", []) or []) for future in futures]
        sampled = sum(len(sequence.tokens or []) for group in groups for sequence in group)
        self.ledger.add("fireworks", self.arm, prefill_tokens=prefill, sample_tokens=sampled,
                        dollars=fireworks_dollars(prefill, sampled))
        return [[self.text(sequence) for sequence in group] for group in groups]

    def text(self, sequence) -> str:
        return self.text_content(self.renderer.parse_response(list(sequence.tokens or []))[0])

    def close(self) -> None:
        self.sampler.close()
        self.service.close()


class OpenRouterProposer:
    """The repo's OpenRouter client, provider pinned and retention denied, several turns at a time."""

    def __init__(self, ledger: Ledger, arm: str, workers: int = 8):
        from standardphysics_agents.models import OpenRouter, provider_routing

        self.router, self.routing = OpenRouter(timeout=120.0), provider_routing
        self.ledger, self.arm, self.workers = ledger, arm, workers
        self.price = openrouter_price(self.router.model)
        print(f"sampling {self.router.model} at {self.price} per token", flush=True)

    def __call__(self, conversations: list[list[dict]], samples: int = 1) -> list[list[str]]:
        worst = sum(self.worst_case(messages) for messages in conversations)
        if self.ledger.dollars("openrouter") + worst > OPENROUTER_LIMIT:
            raise BudgetExceeded(f"next round could reach ${self.ledger.dollars('openrouter') + worst:.2f}")
        with ThreadPoolExecutor(self.workers) as pool:
            return [[text] for text in pool.map(self.one, conversations)]

    def worst_case(self, messages: list[dict]) -> float:
        characters = sum(len(message["content"]) for message in messages)
        return characters / 2.5 * self.price["prompt"] + OPENROUTER_MAX_TOKENS * self.price["completion"]

    def one(self, messages: list[dict]) -> str:
        for attempt in range(3):
            try:
                response = self.router.client().chat.completions.create(
                    model=self.router.model, messages=messages, temperature=TEMPERATURE,
                    max_tokens=OPENROUTER_MAX_TOKENS, extra_body={"provider": self.routing(self.router.model),
                                                                  "usage": {"include": True},
                                                                  "reasoning": OPENROUTER_REASONING})
            except Exception as error:  # a flaky provider costs a retry, then an unparseable answer
                print(f"openrouter error {type(error).__name__}: {error}", flush=True)
                time.sleep(5 * (attempt + 1))
                continue
            self.charge(response.usage)
            return response.choices[0].message.content or ""
        return ""

    def charge(self, usage) -> None:
        prompt, completion = usage.prompt_tokens, usage.completion_tokens
        cost = getattr(usage, "cost", None)
        if cost is None:
            cost = prompt * self.price["prompt"] + completion * self.price["completion"]
        self.ledger.add("openrouter", self.arm, prompt_tokens=prompt, completion_tokens=completion, dollars=cost,
                        calls=1)

    def close(self) -> None:
        return None


def openrouter_price(model: str) -> dict[str, float]:
    import urllib.request

    with urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=30) as response:
        listing = json.load(response)["data"]
    pricing = next(row["pricing"] for row in listing if row["id"] == model)
    return {"prompt": float(pricing["prompt"]), "completion": float(pricing["completion"])}


# --- arms ---------------------------------------------------------------------


def room_record(arm: str, variant: str, ceiling: dict, rounds: list[dict], messages: list[dict]) -> dict:
    row = ceiling.get(variant, {})
    return {"arm": arm, "variant": variant, "ceiling_fixable": bool(row.get("fixable")),
            "ceiling_all_clear": bool(row.get("all_clear")), "rounds": rounds, "messages": messages}


class Experiment:
    def __init__(self, args):
        self.args, self.out = args, args.out
        self.data = load(args.data)
        self.ceiling = load_ceiling(args.ceiling)
        self.rows = {row["variant"]: row for row in self.data.heldout}
        self.ledger = Ledger(self.out)

    def rooms(self, spec: ArmSpec) -> list[str]:
        variants = sorted(self.rows)
        subset = stratified_subset(variants, {v: bool(self.ceiling.get(v, {}).get("fixable")) for v in variants})
        if spec.rooms == "rest":
            variants = [variant for variant in variants if variant not in subset]
        elif spec.rooms == "subset" or self.args.subset:
            variants = subset
        return variants[: self.args.limit] if self.args.limit else variants

    def judge(self, completion: str, variant: str):
        window = self.data.variants[variant]["window_id"]
        return diagnose(completion, self.data.graph(variant), self.data.checker(window))

    def first_prompt(self, variant: str, hint: bool) -> list[dict]:
        messages = self.rows[variant]["messages"]
        return with_open_floor(messages, self.data.graph(variant)) if hint else messages

    def write(self, arm: str, records: list[dict]) -> pathlib.Path:
        path = self.out / "transcripts" / f"{arm}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(record) + "\n" for record in records))
        return path

    def rescore(self, arm: str, variants: list[str]) -> list[dict]:
        saved: dict[str, list[dict]] = {}
        for line in (self.args.data / "qwen3p8-27b/eval/rl.jsonl").read_text().splitlines():
            row = json.loads(line)
            saved.setdefault(row["variant"], []).append(row)
        return [self.independent_record(arm, variant, [row["completion"] for row in saved[variant]])
                for variant in variants]

    def independent_record(self, arm: str, variant: str, completions: list[str]) -> dict:
        rounds = []
        for index, completion in enumerate(completions, start=1):
            attempt = self.judge(completion, variant)
            rounds.append({"round": index, "completion": completion, "category": attempt.category,
                           "notes": list(attempt.notes), "feedback": None, **attempt.verdict.as_dict()})
        return room_record(arm, variant, self.ceiling, rounds, self.rows[variant]["messages"])

    def proposer(self, spec: ArmSpec, arm: str):
        if spec.backend == "openrouter":
            return OpenRouterProposer(self.ledger, arm)
        return FireworksProposer(spec.model, self.args.data / "PROGRESS_MULTIROOM.json", self.ledger, arm)

    def estimate(self, proposer, spec: ArmSpec, variants: list[str]) -> float:
        if spec.backend != "fireworks":
            return sum(proposer.worst_case(self.first_prompt(v, spec.hint)) for v in variants) * ROUNDS * 1.3
        tokens = [proposer.prompt_tokens(self.first_prompt(v, spec.hint)) for v in variants]
        if spec.independent:
            return fireworks_dollars(sum(tokens) * ROUNDS, len(tokens) * ROUNDS * MAX_SAMPLE_TOKENS)
        return fireworks_dollars(*chain_worst_case_tokens(tokens))

    def run(self, arm: str) -> None:
        spec, variants, label = ARMS[arm], self.rooms(ARMS[arm]), arm + self.args.tag
        if spec.backend == "rescore":
            print(f"wrote {self.write(label, self.rescore(label, variants))}", flush=True)
            return
        proposer = self.proposer(spec, label)
        try:
            self.run_with(proposer, label, spec, variants)
        finally:
            proposer.close()

    def run_with(self, proposer, arm: str, spec: ArmSpec, variants: list[str]) -> None:
        provider = "openrouter" if spec.backend == "openrouter" else "fireworks"
        limit = OPENROUTER_LIMIT if provider == "openrouter" else FIREWORKS_LIMIT
        worst, spent = self.estimate(proposer, spec, variants), self.ledger.dollars(provider)
        print(f"{arm}: {len(variants)} rooms, worst case ${worst:.2f}, {provider} spent ${spent:.2f} of ${limit}",
              flush=True)
        self.ledger.note(provider, arm, worst_case_estimate=round(worst, 4))
        if spent + worst > limit and not self.args.allow_over_estimate:
            raise SystemExit(ESTIMATE_EXIT_CODE)
        if spec.independent:
            groups = proposer([self.first_prompt(v, spec.hint) for v in variants], samples=ROUNDS)
            records = [self.independent_record(arm, v, group) for v, group in zip(variants, groups)]
        else:
            records = self.run_chain_arm(proposer, arm, spec, variants)
        print(f"wrote {self.write(arm, records)}", flush=True)

    def run_chain_arm(self, proposer, arm: str, spec: ArmSpec, variants: list[str]) -> list[dict]:
        chains = [Chain(variant, self.first_prompt(variant, spec.hint)) for variant in variants]
        provider = "openrouter" if spec.backend == "openrouter" else "fireworks"

        def propose(conversations):
            return [group[0] for group in proposer(conversations)]

        def report(round_index):
            left = sum(1 for chain in chains if chain.open)
            print(f"{arm} round {round_index}: {len(chains) - left} accepted, {left} open, "
                  f"spend {json.dumps(self.ledger.arm(provider, arm))}", flush=True)

        run_chains(chains, propose, self.judge, ROUNDS, after_round=report)
        return [room_record(arm, chain.variant, self.ceiling, chain.rounds, chain.messages) for chain in chains]


# --- results ------------------------------------------------------------------


REST = "_rest"


def _transcripts(out: pathlib.Path) -> dict[str, list[dict]]:
    """Every arm's rooms, with a `_rest` arm folded into the arm it completes."""
    arms: dict[str, list[dict]] = {}
    for path in sorted((out / "transcripts").glob("*.jsonl")):
        if path.stem not in ARMS:
            continue
        records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        arms.setdefault(path.stem.removesuffix(REST), []).extend(records)
    return arms


def _arm_cost(ledger: Ledger, name: str) -> dict:
    parts = [ledger.arm(provider, arm) for provider in ("fireworks", "openrouter") for arm in (name, name + REST)]
    return {"dollars": round(sum(part.get("dollars", 0.0) for part in parts), 4), "parts": [p for p in parts if p]}


def build_results(out: pathlib.Path, ceiling: dict[str, dict]) -> dict:
    ledger = Ledger(out)
    subset = stratified_subset(sorted(ceiling), {variant: bool(row["fixable"]) for variant, row in ceiling.items()})
    results = {"rounds_per_room": ROUNDS, "temperature": TEMPERATURE,
               "ceiling": {"rooms": len(ceiling), "fixable": sum(1 for row in ceiling.values() if row["fixable"]),
                           "all_clear": sum(1 for row in ceiling.values() if row["all_clear"])},
               "spend": {provider: {k: v for k, v in ledger.load(provider).items() if k != "arms"}
                         for provider in ("fireworks", "openrouter")},
               "subset": subset, "arms": {}, "arms_on_subset": {}}
    for name, records in _transcripts(out).items():
        results["arms"][name] = {**arm_metrics(records), "cost": _arm_cost(ledger, name)}
        results["arms_on_subset"][name] = arm_metrics([record for record in records if record["variant"] in subset])
    return results


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["run", "results"])
    parser.add_argument("--arm", choices=sorted(ARMS))
    parser.add_argument("--data", type=pathlib.Path, default=DATA)
    parser.add_argument("--ceiling", type=pathlib.Path, default=CEILING)
    parser.add_argument("--out", type=pathlib.Path, default=OUT)
    parser.add_argument("--limit", type=int, default=0, help="first N rooms only, for a smoke test")
    parser.add_argument("--subset", action="store_true", help="the stratified subset instead of all rooms")
    parser.add_argument("--tag", default="", help="suffix for the transcript file")
    parser.add_argument("--allow-over-estimate", action="store_true",
                        help="run although the worst case passes the limit; the per-call guard still stops it")
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "run":
        Experiment(args).run(args.arm)
        return
    results = build_results(args.out, load_ceiling(args.ceiling))
    (args.out / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps({name: {k: v for k, v in arm.items() if k != "rejections_by_round"}
                      for name, arm in results["arms"].items()}, indent=1))


if __name__ == "__main__":
    main()
