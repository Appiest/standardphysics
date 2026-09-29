"""Reading the fine-tuning ledger: the Pacific-time clock, the number formats, the
per-run lookups and the headline totals every part of the story quotes."""

import datetime

PACIFIC = datetime.timezone(datetime.timedelta(hours=-7), "Pacific time")

def pacific(timestamp):
    return datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00")).astimezone(PACIFIC)

def started(run):
    return pacific(run["sessions"][0]["opened_at"])

def clock(moment):
    return moment.strftime("%-I:%M %p").lower()

def day(moment):
    return moment.strftime("%a %b %-d")


def percent(share):
    return "none" if share is None else f"{share * 100:.1f}%"

def millions(count):
    return f"{count / 1_000_000:.1f}M"

def whole(count):
    return f"{count:,}"

def rl_rollouts(run):
    training = run["training"]
    return (training.get("rl_steps_done") or 0) * (training.get("rl_rollouts_per_step") or 0)

def evaluation_named(run, label):
    return next((entry for entry in run["evaluations"] if entry["label"] == label), None)

def last_evaluation(run):
    return run["evaluations"][-1] if run["evaluations"] else None


def totals(ledger, runs):
    spend = [run["spend"] for run in runs]
    first = pacific(ledger["adapters"][0]["created"])
    last = pacific(ledger["adapters"][-1]["created"])
    return {
        "adapters": len(ledger["adapters"]),
        "runs": len(runs),
        "sessions": sum(len(run["sessions"]) for run in runs),
        "sft_rows": sum(run["training"].get("sft_rows") or 0 for run in runs),
        "sft_examples": sum(
            (run["training"].get("sft_rows") or 0) * (run["training"].get("sft_epochs") or 0) for run in runs
        ),
        "rl_steps": sum(run["training"].get("rl_steps_done") or 0 for run in runs),
        "rl_rollouts": sum(rl_rollouts(run) for run in runs),
        "train_tokens": sum(entry["train_tokens"] for entry in spend),
        "read_tokens": sum(entry["prefill_tokens"] + entry["sample_tokens"] for entry in spend),
        "graded": sum(entry["samples"] for run in runs for entry in run["evaluations"]),
        "dollars": sum(entry["estimated_dollars"] for entry in spend),
        "first": first,
        "last": last,
    }
