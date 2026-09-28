"""Batch B's extra sound: a warm major-key bed for the wholesome reels and a rubber-stamp thud for ratings."""

import numpy as np

from sound import OUT, RATE, drum_kick, envelope, filtered, hat, note_hz, noise, place, seconds, write

PROGRESSION = [[-9, -5, -2, 3], [-14, -10, -7, -2], [-12, -9, -5, 0], [-16, -12, -9, -4]]
BASS = [-33, -38, -36, -40]


def pluck(hz, duration=0.9):
    t = seconds(duration)
    tone = np.sin(2 * np.pi * hz * t) + 0.35 * np.sin(2 * np.pi * hz * 2 * t) + 0.12 * np.sin(2 * np.pi * hz * 3 * t)
    return tone * envelope(t, 0.004, 0.32)


def warm_pad(chord, duration):
    t = seconds(duration)
    voices = sum(np.sin(2 * np.pi * note_hz(n) * t * (1 + d)) for n in chord for d in (-0.002, 0.002))
    swell = np.minimum(1, t / 0.6) * np.minimum(1, (duration - t) / 0.4)
    return filtered(voices, "lowpass", 1800) / (len(chord) * 2) * swell


def arpeggiate(track, chord, start, beat):
    pattern = [0, 2, 1, 3, 2, 1, 3, 2]
    for step, voice in enumerate(pattern):
        place(track, pluck(note_hz(chord[voice] + 12)), start + step * beat / 2, 0.16)


def warm_bed(duration=24.0, bpm=96):
    track = np.zeros(int(duration * RATE))
    beat = 60 / bpm
    bar = beat * 4
    for index in range(int(duration / bar) + 1):
        start = index * bar
        chord = PROGRESSION[index % len(PROGRESSION)]
        place(track, warm_pad(chord, bar + 0.3), start, 0.3)
        arpeggiate(track, chord, start, beat)
        t = seconds(bar)
        place(track, np.sin(2 * np.pi * note_hz(BASS[index % len(BASS)]) * t) * envelope(t, 0.01, 1.2) * 0.3, start)
        for step in range(4):
            place(track, drum_kick(), start + step * beat, 0.35 if step % 2 == 0 else 0.0)
            place(track, hat(), start + step * beat + beat / 2, 0.6)
    return track


def stamp():
    t = seconds(0.5)
    thud = np.sin(2 * np.pi * (60 + 90 * np.exp(-t * 40)) * t) * envelope(t, 0.001, 0.09)
    paper = filtered(noise(0.5), "bandpass", [400, 3000]) * envelope(t, 0.0005, 0.025)
    return np.tanh((thud * 1.2 + paper * 0.8) * 1.8) * 0.85


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    write("b-warm", warm_bed())
    write("b-stamp", stamp())
    print("wrote b-warm.wav b-stamp.wav")
