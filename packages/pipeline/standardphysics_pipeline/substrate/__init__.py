"""The measured world before anybody has named any of it.

Regions come out of the geometry because they have shape, not because a scanner
shipped with a category for them. What they are called is a later question, and
a different layer's.
"""

from .planes import Plane, planes_of

__all__ = ["Plane", "planes_of"]
