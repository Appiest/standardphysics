"""Stands in for the SPAR3D furniture script in the worker tests: it writes its pid and never finishes."""

import os
import pathlib
import sys
import time

output = pathlib.Path(sys.argv[sys.argv.index("--output-dir") + 1])
(output / "script.pid").write_text(str(os.getpid()))
time.sleep(3600)
