"""Reading a walk's photos while the person is still walking.

Discovery used to start reading photos only after the walk was uploaded, and
reading a few hundred of them is bounded by the account's shared token budget,
not by anything the server can speed up. Photos that arrive during the walk
can be read in the meantime. Each answer goes into the same `DetectionCache`
discovery consults, so when the walk ends discovery finds most photos already
answered and asks only about the rest.

Three rules keep this honest:

- Only photos the `WalkSampler` keeps are read, so the photos read during the
  walk are the photos discovery wants at the end.
- Every request is non-urgent (`DetectorSlots`): a walk that has ended and has
  someone waiting on it always gets a slot first.
- Scans take turns. Two people walking at once each get every other photo read,
  rather than the first walk starving the second.

A photo that cannot be read here is simply not cached, and discovery asks for
it again at the end.
"""

from __future__ import annotations

import logging
import pathlib
import threading
from collections import deque
from collections.abc import Callable, Hashable
from dataclasses import dataclass, field

from standardphysics_contracts import PoseRecord

from .cache import DetectionCache
from .detect import Detection, ModelRequestInfo, detect_objects
from .detection_errors import DetectionAuthError, DetectionError
from .detector_transport import DETECTOR_SLOTS_IN_FLIGHT, answer_identity
from .walk_sampling import WalkSampler, viewpoint_of_pose

log = logging.getLogger(__name__)

LIVE_READERS = DETECTOR_SLOTS_IN_FLIGHT
"""Photos read at once across every walk in progress. A walk keeps about 1.5
photos a second and one takes about 5.5 s to read, so one walk needs about nine
in flight to keep up. Several walks at once can use every slot, because a walk
that has ended takes each slot first as it frees. With twelve, three walks at
once were held to 2 photos a second between them, well under the account's
budget, and each left 197 of its 355 photos for the end."""
FINISH_WAIT_SECONDS = 20.0
"""How long the end of a walk waits for photos already being read, which is
about one request. Anything still out after this is asked again by discovery."""

ReadPhoto = Callable[..., list[Detection]]


@dataclass(frozen=True)
class WaitingPhoto:
    frame_id: str
    path: pathlib.Path
    orientation: str


@dataclass(frozen=True)
class LiveReport:
    """What reading during one walk did, for the job record and the evidence trail."""

    offered: int = 0
    kept: int = 0
    read: int = 0
    unread: int = 0
    left_for_discovery: int = 0
    requests: tuple[ModelRequestInfo, ...] = ()


@dataclass
class _LiveScan:
    cache: DetectionCache
    sampler: WalkSampler = field(default_factory=WalkSampler)
    waiting: deque[WaitingPhoto] = field(default_factory=deque)
    in_flight: int = 0
    offered: int = 0
    kept: int = 0
    read: int = 0
    unread: int = 0
    requests: list[ModelRequestInfo] = field(default_factory=list)

    def report(self, left_for_discovery: int) -> LiveReport:
        return LiveReport(
            offered=self.offered, kept=self.kept, read=self.read, unread=self.unread,
            left_for_discovery=left_for_discovery, requests=tuple(self.requests),
        )


class LiveReader:
    """One pool of readers shared by every walk in progress, taking the walks in turn."""

    def __init__(self, workers: int = LIVE_READERS, read_photo: ReadPhoto = detect_objects) -> None:
        self._workers = workers
        self._read_photo = read_photo
        self._scans: dict[Hashable, _LiveScan] = {}
        self._turns: deque[Hashable] = deque()
        self._changed = threading.Condition()
        self._threads: list[threading.Thread] = []
        self._stopped = False
        self._refused: str | None = None

    def offer(self, scan_id: Hashable, pose: PoseRecord, path: pathlib.Path, cache_dir: pathlib.Path) -> bool:
        """Queue one arriving photo if the walk sampler keeps it. True when it was queued."""
        with self._changed:
            if self._stopped or self._refused is not None:
                return False
            scan = self._scans.setdefault(scan_id, _LiveScan(DetectionCache(cache_dir, answer_identity())))
            scan.offered += 1
            if not scan.sampler.keep(viewpoint_of_pose(pose)):
                return False
            scan.kept += 1
            scan.waiting.append(WaitingPhoto(pose.frame_id or path.name, path, pose.orientation))
            if scan_id not in self._turns:
                self._turns.append(scan_id)
            self._start_readers()
            self._changed.notify()
        return True

    def finish(self, scan_id: Hashable, wait_seconds: float = FINISH_WAIT_SECONDS) -> LiveReport:
        """End reading for a walk: drop what is still queued, wait briefly for what is in flight.

        Queued photos are left to discovery, which reads with every slot rather
        than the share a walk in progress gets.
        """
        with self._changed:
            scan = self._scans.get(scan_id)
            if scan is None:
                return LiveReport()
            left = len(scan.waiting)
            scan.waiting.clear()
            self._changed.wait_for(lambda: scan.in_flight == 0, wait_seconds)
            self._scans.pop(scan_id, None)
            return scan.report(left + scan.in_flight)

    def pending(self, scan_id: Hashable) -> int:
        """Photos of this walk queued or being read right now."""
        with self._changed:
            scan = self._scans.get(scan_id)
            return 0 if scan is None else len(scan.waiting) + scan.in_flight

    def stop(self) -> None:
        with self._changed:
            self._stopped = True
            self._changed.notify_all()

    def _start_readers(self) -> None:
        while len(self._threads) < self._workers:
            thread = threading.Thread(target=self._read_until_stopped, name="live-photo-reader", daemon=True)
            self._threads.append(thread)
            thread.start()

    def _read_until_stopped(self) -> None:
        while (work := self._next_photo()) is not None:
            self._read(*work)

    def _next_photo(self) -> tuple[_LiveScan, WaitingPhoto] | None:
        with self._changed:
            while not self._stopped:
                work = self._take_turn()
                if work is not None:
                    return work
                self._changed.wait()
            return None

    def _take_turn(self) -> tuple[_LiveScan, WaitingPhoto] | None:
        """The next photo of the next walk in line, putting the walk back in line if it has more."""
        while self._turns:
            scan_id = self._turns.popleft()
            scan = self._scans.get(scan_id)
            if scan is None or not scan.waiting:
                continue
            photo = scan.waiting.popleft()
            scan.in_flight += 1
            if scan.waiting:
                self._turns.append(scan_id)
            return scan, photo
        return None

    def _read(self, scan: _LiveScan, photo: WaitingPhoto) -> None:
        requests: list[ModelRequestInfo] = []
        read = False
        try:
            found = self._read_photo(
                photo.path, photo.frame_id, orientation=photo.orientation, recorded=requests, urgent=False,
            )
            scan.cache.put(photo.path, found, photo.orientation)
            read = True
        except DetectionAuthError as error:
            self._refuse(str(error))
        except DetectionError as error:
            log.info("left %s for discovery: %s", photo.frame_id, error)
        finally:
            self._settle(scan, requests, read)

    def _refuse(self, reason: str) -> None:
        """The detector will not take this server's requests, so stop offering it any."""
        with self._changed:
            if self._refused is None:
                log.warning("stopped reading photos during walks: %s", reason)
            self._refused = reason

    def _settle(self, scan: _LiveScan, requests: list[ModelRequestInfo], read: bool) -> None:
        with self._changed:
            scan.in_flight -= 1
            scan.requests.extend(requests)
            scan.read += int(read)
            scan.unread += int(not read)
            self._changed.notify_all()
