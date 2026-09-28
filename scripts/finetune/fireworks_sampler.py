"""Qwen3.8 27B on the Fireworks serverless training API as a five-loop `Sampler`, with metered spend.

`base` samples the bare base model; anything else is a saved training state
reference. Every call adds its prompt and sampled tokens to a spend file in the
format `spend_watchdog` reads, priced by `progress.QWEN3P8_27B_SERVERLESS_RATES`.
"""

from __future__ import annotations

import json
import os
import pathlib
import threading

from progress import Spend


class FireworksSampler:
    def __init__(self, model: str, spend_file: pathlib.Path, temperature: float, max_tokens: int):
        from fireworks.training.sdk import FiretitanSamplingParams, FiretitanServiceClient
        from serverless_train import BASE_MODEL, RENDERER, SERVERLESS_URL, TOKENIZER_MODEL
        from training.renderer import get_renderer, get_text_content
        from training.utils.tokenizers import load_tokenizer

        self.spend_file, self.text_content, self.lock = spend_file, get_text_content, threading.Lock()
        self.spend = Spend(**{key: value for key, value in _saved(spend_file).items() if key.endswith("_tokens")})
        tokenizer = load_tokenizer(TOKENIZER_MODEL)
        self.renderer = get_renderer(RENDERER, tokenizer)
        self.service = FiretitanServiceClient(api_key=os.environ["FIREWORKS_API_KEY"], base_url=SERVERLESS_URL)
        client = (self.service.create_lora_training_client(base_model=BASE_MODEL, rank=32, alpha=64)
                  if model == "base" else self.service.create_training_client_from_state(model))
        snapshot = client.save_weights_for_sampler("five-loop").result().path
        self.sampler = self.service.create_sampling_client(model_path=snapshot, tokenizer=tokenizer)
        self.params = FiretitanSamplingParams(max_tokens=max_tokens, temperature=temperature,
                                              stop=self.renderer.get_stop_sequences())
        print(f"sampling {model} from {snapshot}", flush=True)

    def __call__(self, messages: list[dict]) -> str:
        prompt = self.renderer.build_generation_prompt(messages)
        result = self.sampler.sample(prompt=prompt, num_samples=1, sampling_params=self.params).result(timeout=1800)
        sequences = list(getattr(result, "sequences", []) or [])
        tokens = list(sequences[0].tokens or []) if sequences else []
        with self.lock:
            self.spend.prefill_tokens += prompt.length
            self.spend.sample_tokens += len(tokens)
            temporary = self.spend_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.spend.as_dict()))
            os.replace(temporary, self.spend_file)
        return self.text_content(self.renderer.parse_response(tokens)[0]) if tokens else ""

    def close(self) -> None:
        self.sampler.close()
        self.service.close()


def _saved(path: pathlib.Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
