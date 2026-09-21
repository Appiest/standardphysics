"""Can a model write a predicate nobody gave it?

It gets the operator list and the grammar. It does not get the word 'rests on',
any example predicate, or any label from the scan. The regions are nameless.
"""
import json
import os
import sys
import urllib.request

from substrate import Refused, load, select

GRAMMAR = """
You are given a set of measured 3D regions from a real scan. They have no
names. You know nothing about what they are.

Write ONE predicate, as JSON, selecting the regions a question asks about.
It is evaluated for each region with $r bound to that region.

Predicate forms:
  {"and": [p, ...]}  {"or": [p, ...]}  {"not": p}
  {"lt": [e, e]}  {"gt": [e, e]}  {"lte": [e, e]}  {"gte": [e, e]}
  {"abs_lt": [e, e]}  {"eq": [e, e]}  {"neq": [e, e]}
  ("eq"/"neq" also compare two region bindings directly, e.g. {"neq": ["$r", "$other"]})
  {"exists": {"as": "$other", "where": p}}   true if any other region satisfies p

Value forms (e):
  a plain number
  {"op": "gap", "a": R, "b": R, "along": "gravity"|"x"|"y"|"z"}
      signed distance between the two regions' facing surfaces along that
      direction. Along gravity it is how far a sits above b. Negative means
      they overlap.
  {"op": "footprint_overlap", "a": R, "b": R}
      fraction of a's ground footprint lying inside b's, 0 to 1
  {"op": "volume", "a": R}
  {"op": "horizontal_distance", "a": R, "b": R}
  {"op": "centroid", "a": R, "axis": 0|1|2}
  {"op": "extent", "a": R, "axis": 0|1|2}
  {"op": "bottom", "a": R}   lowest z
  {"op": "top", "a": R}      highest z

R is "$r", "$other", or a region id.
Axis 2 is the direction the scan measured as up. Units are metres.

Answer with ONLY the JSON predicate. No prose, no code fence.
"""

def ask(model, question, regions, repair=None):
    turns = [
            {"role": "system", "content": GRAMMAR},
            {"role": "user", "content":
                f"The scan has {len(regions)} regions. Here are their measurements:\n"
                + json.dumps([{k: [round(x, 3) for x in v] if isinstance(v, list) else v
                               for k, v in r.items()} for r in regions.values()])
                + f"\n\nWrite the predicate selecting: {question}"},
    ]
    if repair:
        turns.append({"role": "assistant", "content": repair[0]})
        turns.append({"role": "user", "content":
            f"That failed: {repair[1]}. Answer again with only valid JSON using only the listed forms."})
    body = {"model": model, "messages": turns, "temperature": 0}
    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        payload = json.load(response)
    text = payload["choices"][0]["message"]["content"].strip()
    if text.startswith("```"):
        text = text.split("```")[1].removeprefix("json").strip()
    return text, payload.get("usage", {})


QUESTIONS = [
    ("things sitting directly on the lowest surface in the scan", "floor-standing"),
    ("things that are held up by another thing rather than by the lowest surface", "on-something"),
    ("things that are precariously balanced: held up by something whose contact "
     "with them is small compared to how far the held thing sticks out", "precarious"),
]

def main():
    model = sys.argv[1]
    regions, truth = load("test1")
    total_in = total_out = 0
    for question, tag in QUESTIONS:
        text = ""
        failure = None
        try:
            for attempt in range(2):
                try:
                    repair = None if attempt == 0 else (text, str(failure)[:200])
                    text, usage = ask(model, question, regions, repair)
                    total_in += usage.get("prompt_tokens", 0)
                    total_out += usage.get("completion_tokens", 0)
                    expression = json.loads(text)
                    chosen = select(expression, regions)
                    break
                except (json.JSONDecodeError, Refused, KeyError) as exc:
                    failure = exc
                    if attempt == 1:
                        raise
            print(f"\n=== {tag}")
            print("  predicate:", json.dumps(expression)[:300])
            print(f"  selected {len(chosen)}: {sorted({truth[c] for c in chosen})}")
            print("  ids:", " ".join(sorted(chosen, key=lambda r: int(r[1:])))[:150])
        except Refused as exc:
            print(f"\n=== {tag}\n  REFUSED: {exc}")
        except Exception as exc:
            print(f"\n=== {tag}\n  FAILED: {type(exc).__name__}: {str(exc)[:200]}")
    print(f"\ntokens: {total_in} in, {total_out} out")

main()
