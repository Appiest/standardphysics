"""Extra one-shots for batch A's meme reels, built on the synth helpers in sound.py and written to public/sound/a-*.wav."""

import numpy as np

from sound import OUT, envelope, filtered, noise, seconds, write


def brass(t, hz, bend=0.0):
    frequency = hz * (1 + bend * t)
    phase = 2 * np.pi * np.cumsum(frequency) / 48000
    tone = sum(np.sin(phase * k) / k for k in range(1, 7))
    return filtered(tone, "lowpass", 1800) * (1 + 0.15 * np.sin(2 * np.pi * 6 * t))


def womp():
    notes = [(293.7, 0.32, 0.0), (277.2, 0.32, 0.0), (261.6, 0.32, 0.0), (246.9, 1.1, -0.08)]
    parts = [brass(seconds(length), hz, bend) * envelope(seconds(length), 0.03, length * 0.8) for hz, length, bend in notes]
    return np.concatenate(parts) * 0.5


def ding():
    t = seconds(1.4)
    return sum(np.sin(2 * np.pi * hz * t) * np.exp(-t * decay) for hz, decay in [(1318.5, 3), (1975.5, 4.5), (2637, 6)]) * 0.3


def sparkle():
    t = seconds(1.2)
    track = np.zeros(len(t))
    for index, hz in enumerate([1568, 2093, 2637, 3136, 4186]):
        start = int(index * 0.07 * 48000)
        chime = np.sin(2 * np.pi * hz * t) * np.exp(-t * 7) * 0.22
        track[start:] += chime[: len(track) - start]
    return track + filtered(noise(1.2), "highpass", 6000) * envelope(t, 0.01, 0.25) * 0.08


def clack():
    t = seconds(0.07)
    return filtered(noise(0.07), "bandpass", [1500, 5000]) * envelope(t, 0.0004, 0.01) * 0.9


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, clip in {"a-womp": womp(), "a-ding": ding(), "a-sparkle": sparkle(), "a-clack": clack()}.items():
        write(name, clip)
    print("wrote a-womp a-ding a-sparkle a-clack")


if __name__ == "__main__":
    main()
