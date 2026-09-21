"""Whether the app can answer a question nobody prepared it for.

`docs/MISSION.md` says what this has to prove and why a checklist cannot: real
scans only, questions written fresh each run against scenes the implementation
was not developed on, judged against the room rather than against an expected
string, with a share of them unanswerable so that saying so is a pass. The same
suite runs again over the same geometry with every name scrambled, and any drop
is something keyed to English names for earthly objects.
"""

from .judge import CouldNotJudge, Verdict, judge, numbers_traced
from .questions import CouldNotWriteQuestions, Question, write
from .scenes import NoRealScenes, Scene, real_scenes, scramble, split
from .suite import Result, Run, report, run, transcript

__all__ = [
    "CouldNotJudge", "CouldNotWriteQuestions", "NoRealScenes", "Question", "Result",
    "Run", "Scene", "Verdict", "judge", "numbers_traced", "real_scenes", "report",
    "run", "scramble", "split", "transcript", "write",
]
