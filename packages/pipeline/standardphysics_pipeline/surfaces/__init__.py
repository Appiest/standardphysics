"""Counting what is on the surfaces of a scanned room.

A region has measured extent, so its sides have measured area. Cut a side into
patches of a known size, photograph each patch, and a model counting one patch
plus arithmetic the engine does is an answer nobody had to write a rule for.

The word for the thing being counted comes from the question and goes only to the
model. No code here matches it against anything.
"""

from .depth import seen
from .faces import Face, Patch, faces_of, patches_on
from .reader import NoCounterConfigured, counter
from .scan import Scan, ScanNotReadable, open_scan
from .tally import Counter, Reading, Surface, Tally, report, tally
from .views import View, best_view, candidates, cut_out

__all__ = [
    "Counter", "Face", "NoCounterConfigured", "Patch", "Reading", "Scan",
    "ScanNotReadable", "Surface", "Tally", "View", "best_view", "counter",
    "candidates", "cut_out", "faces_of", "open_scan", "patches_on", "report",
    "seen", "tally",
]
