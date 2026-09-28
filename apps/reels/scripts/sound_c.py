"""Batch C's sound: beds and one-shots for the quiz, facts, scan, fast one-line and wheelchair reels, written as public/sound/c-*.wav."""

import numpy as np

from sound import RATE, bed, envelope, filtered, loopable, noise, note_hz, pad, place, seconds, write


def chime(notes=(3, 7, 10, 15), gap=0.07):
    track = np.zeros(int(1.4 * RATE))
    for index, semitone in enumerate(notes):
        t = seconds(1.0)
        tone = np.sin(2 * np.pi * note_hz(semitone) * t) + 0.3 * np.sin(2 * np.pi * note_hz(semitone + 12) * t)
        place(track, tone * envelope(t, 0.003, 0.35) * 0.35, index * gap)
    return track


def heartbeat(duration, bpm=72):
    track = np.zeros(int(duration * RATE))
    t = seconds(0.25)
    thump = np.sin(2 * np.pi * (60 + 40 * np.exp(-t * 40)) * t) * envelope(t, 0.002, 0.07)
    for beat in np.arange(0, duration, 60 / bpm):
        place(track, thump, beat, 0.8)
        place(track, thump, beat + 0.22, 0.5)
    return track


def drone(duration):
    t = seconds(duration)
    tones = sum(np.sin(2 * np.pi * note_hz(n) * t * (1 + 0.002 * i)) for i, n in enumerate([-33, -21, -26]))
    swell = np.minimum(1, t / 2.0) * (0.6 + 0.4 * np.sin(2 * np.pi * 0.2 * t))
    air = filtered(noise(duration), "bandpass", [2000, 6000]) * 0.08
    return (filtered(tones, "lowpass", 900) * 0.25 + air) * swell


def suspense(duration):
    t = seconds(duration)
    rising = np.clip(t / duration, 0, 1)
    return drone(duration) * (0.6 + 0.6 * rising) + heartbeat(duration) * (0.3 + 0.7 * rising)


def main():
    airy = [[-9, -2, 3, 7], [-12, -5, 2, 7], [-7, 0, 5, 9]]
    write("c-bed-scan", loopable(bed(10, 80, airy, {"hat": (8,), "bass": (0,)}, [-33, -36, -31], pad_gain=0.34)))
    quiz_chords = [[-5, -1, 2, 7], [-7, -3, 0, 5], [-2, 2, 5, 10], [-7, -3, 0, 5]]
    quiz = {"kick": (0, 6, 8), "hat": (2, 6, 10, 14), "clap": (4, 12), "bass": (0, 3, 8, 11)}
    write("c-bed-quiz", bed(26, 118, quiz_chords, quiz, [-38, -40, -35, -40], pad_gain=0.14))
    facts = {"kick": (0, 4, 8, 12), "hat": tuple(range(1, 16, 2)), "clap": (4, 12), "bass": (0, 2, 8, 10)}
    write("c-bed-facts", bed(24, 112, [[-12, -8, -5, 0], [-15, -8, -3, 0], [-10, -7, -3, 2], [-17, -10, -5, -1]], facts, [-36, -39, -34, -41], pad_gain=0.15))
    write("c-bed-fastline", loopable(bed(13, 100, airy, {"hat": (4, 12), "kick": (0,), "bass": (0, 10)}, [-33, -36, -31], pad_gain=0.3)))
    write("c-bed-suspense", suspense(18))
    write("c-chime", chime())
    write("c-wrong", chime((0, -1), 0.12) * 0.7)
    print("batch C sounds written")


if __name__ == "__main__":
    main()
