"""Meme one-shots for the reels, written to public/sound/m-*.wav.

The vine boom is synthesised here. The rest are fetched from Mixkit (free licence, commercial use, no attribution
needed), downmixed, resampled to the kit's rate and trimmed to the part a punchline needs.
"""

import io
import urllib.request

import numpy as np
from scipy.io import wavfile
from scipy.signal import resample_poly

from sound import RATE, envelope, filtered, noise, seconds, sweep_sine, write

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"}

MIXKIT = {
    "m-trombone": (472, 3.2),
    "m-buzzer": (948, 1.2),
    "m-record-stop": (704, 1.0),
    "m-gasp": (967, 1.2),
    "m-laugh": (424, 3.0),
    "m-cheer": (610, 3.0),
    "m-splat": (2889, 1.0),
    "m-clown-horn": (715, 1.5),
    "m-boing": (2894, 1.0),
}
"""Our name for each clip: its Mixkit id, and the seconds of it we keep."""


def vine_boom():
    """A bass drop that falls an octave in its first fifty milliseconds, overdriven until it buzzes, with a dull thud on top."""
    t = seconds(1.4)
    body = sweep_sine(t, 110, 52, 22) * envelope(t, 0.001, 0.55)
    growl = sweep_sine(t, 220, 104, 22) * envelope(t, 0.001, 0.3) * 0.35
    thud = filtered(noise(1.4), "lowpass", 400) * envelope(t, 0.0005, 0.03) * 1.2
    driven = np.tanh((body + growl + thud) * 3.2)
    return filtered(driven, "lowpass", 2600) * 0.95


def as_float_mono(rate, samples):
    scale = np.iinfo(samples.dtype).max if np.issubdtype(samples.dtype, np.integer) else 1.0
    mono = samples.astype(np.float64) / scale
    if mono.ndim == 2:
        mono = mono.mean(axis=1)
    return resample_poly(mono, RATE, rate) if rate != RATE else mono


def trimmed(mono, keep_seconds, fade_seconds=0.08):
    start = int(np.argmax(np.abs(mono) > 0.01 * np.max(np.abs(mono))))
    clip = mono[start : start + int(keep_seconds * RATE)].copy()
    fade = min(int(fade_seconds * RATE), len(clip))
    clip[-fade:] *= np.linspace(1, 0, fade)
    return clip / np.max(np.abs(clip)) * 0.95


def mixkit(item_id, keep_seconds):
    url = f"https://assets.mixkit.co/active_storage/sfx/{item_id}/{item_id}.wav"
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=60) as response:
        rate, samples = wavfile.read(io.BytesIO(response.read()))
    return trimmed(as_float_mono(rate, samples), keep_seconds)


def main():
    write("m-vine-boom", vine_boom())
    for name, (item_id, keep_seconds) in MIXKIT.items():
        write(name, mixkit(item_id, keep_seconds))
    print("wrote m-vine-boom", *MIXKIT)


if __name__ == "__main__":
    main()
