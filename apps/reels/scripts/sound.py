"""Synthesises the reels' sound: a kit of one-shot effects and a music bed per reel, written to public/sound."""

from pathlib import Path

import numpy as np
from scipy.io import wavfile
from scipy.signal import butter, sosfilt

RATE = 48000
OUT = Path(__file__).resolve().parents[1] / "public" / "sound"
rng = np.random.default_rng(7)


def seconds(duration):
    return np.arange(int(duration * RATE)) / RATE


def envelope(t, attack, decay):
    return np.minimum(1, t / max(attack, 1e-4)) * np.exp(-t / decay)


def filtered(signal, kind, cutoff, order=2):
    return sosfilt(butter(order, cutoff, btype=kind, fs=RATE, output="sos"), signal)


def noise(duration):
    return rng.standard_normal(int(duration * RATE))


def sweep_sine(t, start_hz, end_hz, curve=6.0):
    frequency = end_hz + (start_hz - end_hz) * np.exp(-t * curve)
    return np.sin(2 * np.pi * np.cumsum(frequency) / RATE)


def boom():
    t = seconds(1.6)
    body = sweep_sine(t, 120, 38, 9) * envelope(t, 0.002, 0.45)
    click = filtered(noise(1.6), "highpass", 2500) * envelope(t, 0.0005, 0.012) * 0.5
    return np.tanh((body + click) * 1.6) * 0.9


def hit():
    t = seconds(0.9)
    body = sweep_sine(t, 180, 55, 14) * envelope(t, 0.001, 0.16)
    crack = filtered(noise(0.9), "bandpass", [900, 6000]) * envelope(t, 0.0005, 0.035)
    return np.tanh((body * 0.9 + crack * 0.7) * 1.4) * 0.85


def whoosh(duration=0.7, rising=True):
    t = seconds(duration)
    shape = np.sin(np.pi * t / duration) ** 2
    raw = noise(duration)
    low, high = (filtered(raw, "bandpass", [300, 1400]), filtered(raw, "bandpass", [1400, 7000]))
    blend = t / duration if rising else 1 - t / duration
    return (low * (1 - blend) + high * blend) * shape * 0.5


def riser(duration=1.4):
    t = seconds(duration)
    tone = sweep_sine(duration - t, 900, 180, 2.2) * 0.25
    air = filtered(noise(duration), "highpass", 3000) * 0.3
    return (tone + air) * (t / duration) ** 2.4


def tick():
    t = seconds(0.06)
    return filtered(noise(0.06), "bandpass", [2500, 9000]) * envelope(t, 0.0003, 0.006) * 0.8


def tape():
    t = seconds(0.25)
    slap = filtered(noise(0.25), "bandpass", [180, 2400]) * envelope(t, 0.0008, 0.03)
    body = np.sin(2 * np.pi * 110 * t) * envelope(t, 0.001, 0.05) * 0.5
    return np.tanh((slap + body) * 2.2) * 0.7


def shutter():
    t = seconds(0.18)
    first = filtered(noise(0.18), "bandpass", [1200, 8000]) * envelope(t, 0.0003, 0.008)
    second = np.roll(first, int(0.07 * RATE)) * 0.8
    return (first + second) * 0.9


def scan(duration=1.0):
    t = seconds(duration)
    shape = np.sin(np.pi * t / duration)
    hum = sum(np.sin(2 * np.pi * f * t) / (i + 1) for i, f in enumerate([220, 440, 660, 1320]))
    shimmer = filtered(noise(duration), "bandpass", [4000, 9000]) * 0.4
    return (hum * 0.18 + shimmer * 0.25) * shape


def scratch(duration=4.0):
    t = seconds(duration)
    raw = filtered(noise(duration), "bandpass", [1800, 7500])
    strokes = 0.55 + 0.45 * np.sin(2 * np.pi * 3.1 * t + 2 * np.sin(2 * np.pi * 0.7 * t))
    grain = (rng.random(len(t)) > 0.9985) * rng.standard_normal(len(t)) * 3
    return (raw * strokes + filtered(grain, "highpass", 1500)) * 0.16


def pop():
    t = seconds(0.12)
    return np.sin(2 * np.pi * (700 + 900 * np.exp(-t * 60)) * t) * envelope(t, 0.001, 0.03) * 0.5


def note_hz(semitones_from_a4):
    return 440 * 2 ** (semitones_from_a4 / 12)


def pad(t, chord, brightness=1200):
    voices = sum(np.sign(np.sin(2 * np.pi * note_hz(n) * t * (1 + d))) for n in chord for d in (-0.003, 0.003))
    return filtered(voices, "lowpass", brightness) / (len(chord) * 2)


def place(track, clip, at, gain=1.0):
    start = int(at * RATE)
    end = min(len(track), start + len(clip))
    if start < len(track):
        track[start:end] += clip[: end - start] * gain


def drum_kick():
    t = seconds(0.4)
    return np.tanh(sweep_sine(t, 150, 45, 30) * envelope(t, 0.001, 0.12) * 2)


def hat():
    t = seconds(0.08)
    return filtered(noise(0.08), "highpass", 7000) * envelope(t, 0.0005, 0.02) * 0.35


def clap():
    t = seconds(0.25)
    burst = filtered(noise(0.25), "bandpass", [900, 3500]) * envelope(t, 0.001, 0.06)
    return burst * 0.6


def bed(duration, bpm, chords, pattern, bass_notes, pad_gain=0.22):
    track = np.zeros(int(duration * RATE))
    beat = 60 / bpm
    bar = beat * 4
    for bar_index in range(int(duration / bar) + 1):
        start = bar_index * bar
        chord = chords[bar_index % len(chords)]
        t = seconds(bar)
        place(track, pad(t, chord) * np.minimum(1, t / 0.3) * pad_gain, start)
        bass = np.sin(2 * np.pi * note_hz(bass_notes[bar_index % len(bass_notes)]) * t)
        for step in range(16):
            at = start + step * beat / 4
            play_step(track, pattern, step, at, bass, t)
    return track


def play_step(track, pattern, step, at, bass, t):
    if step in pattern.get("kick", ()):
        place(track, drum_kick(), at, 0.8)
    if step in pattern.get("hat", ()):
        place(track, hat(), at)
    if step in pattern.get("clap", ()):
        place(track, clap(), at)
    if step in pattern.get("bass", ()):
        length = int(0.22 * RATE)
        place(track, bass[:length] * envelope(t[:length], 0.004, 0.09) * 0.35, at)


def loopable(track, overlap=1.0):
    fade = int(overlap * RATE)
    head = track[:fade] * np.linspace(0, 1, fade) + track[-fade:] * np.linspace(1, 0, fade)
    return np.concatenate([head, track[fade:-fade]])


def write(name, mono):
    peak = np.max(np.abs(mono)) or 1
    stereo = np.stack([mono, mono], axis=1) / max(1.0, peak / 0.95)
    wavfile.write(OUT / f"{name}.wav", RATE, (stereo * 32767).astype(np.int16))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    kit = {"boom": boom(), "hit": hit(), "whoosh": whoosh(), "whoosh-down": whoosh(0.6, rising=False), "riser": riser(), "tick": tick(), "tape": tape(),
           "shutter": shutter(), "scan": scan(), "scratch": scratch(), "pop": pop()}
    for name, clip in kit.items():
        write(name, clip)
    minor = [[-12, -5, 0, 3, 7], [-16, -9, -4, 0, 3], [-19, -12, -7, -3, 0], [-14, -9, -5, 0, 2]]
    tense = {"kick": (0, 8), "hat": tuple(range(0, 16, 2)), "bass": (0, 3, 8, 11)}
    write("bed-inches", bed(23.5, 96, minor, tense, [-36, -40, -43, -38]))
    bouncy = {"kick": (0, 4, 8, 12), "hat": (2, 6, 10, 14), "clap": (4, 12), "bass": (0, 3, 6, 10, 14)}
    bright = [[-9, -5, -2, 3], [-14, -10, -7, -2], [-7, -3, 0, 5], [-12, -8, -5, 0]]
    write("bed-pov", bed(22, 124, bright, bouncy, [-33, -38, -31, -36], pad_gain=0.16))
    calm = {"hat": (4, 12), "bass": (0, 10)}
    airy = [[-9, -2, 3, 7], [-12, -5, 2, 7], [-7, 0, 5, 9]]
    write("bed-oneline", loopable(bed(17, 90, airy, calm, [-33, -36, -31], pad_gain=0.3)))
    print("wrote", sorted(p.name for p in OUT.glob("*.wav")))


if __name__ == "__main__":
    main()
