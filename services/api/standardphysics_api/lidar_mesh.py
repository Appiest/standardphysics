"""Strict validation for uploaded raw AR mesh JSON, read from disk one part at a time.

A mesh is a JSON object whose `parts` array holds almost all of its bytes.
Parsing the whole document at once holds several times its size in Python
objects, which for the largest walk on file would be most of the API's memory.
Instead the file is read in chunks and each part is decoded and checked on its
own, then dropped, so the most held at once is one part and one read's worth
of text. The top-level fields are checked by the same `LidarMesh` contract as
before. A document this accepts is one `LidarMesh.model_validate(json.loads(...))`
accepts too; it also refuses a repeated top-level key and anything but UTF-8.
"""

from __future__ import annotations

import codecs
import json
import pathlib
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import BinaryIO

from pydantic import ValidationError
from standardphysics_contracts import LidarMesh
from standardphysics_contracts.lidar import MAX_PARTS, MAX_TOTAL_TRIANGLES, MAX_TOTAL_VERTICES, LidarMeshPart

MAX_LIDAR_MESH_BYTES = 640 * 1024 * 1024
"""The largest mesh on file, from the Moffitt library's full-floor walk, is 408 MB of JSON.
The cap sits at about one and a half times that rather than at the general artifact cap."""

READ_CHUNK_BYTES = 1024 * 1024
MAX_PART_CHARS = 192 * 1024 * 1024
"""The most text one part may take. A part holds at most three million coordinates and six million
indices, which at 25 characters a coordinate and 8 an index comes to about 123 MB."""
MAX_FIELD_CHARS = 64 * 1024
"""The most text a key or a top-level field other than `parts` may take. Those are a flag and a number."""

_WHITESPACE = re.compile(r"[ \t\n\r]*")
_DECODER = json.JSONDecoder()
_STAND_IN_PART = {
    "id": "00000000-0000-0000-0000-000000000000",
    "transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
    "vertices": [0, 0, 0],
    "triangles": [0, 0, 0],
}
"""Put where `parts` was when the top-level fields are checked, since the real parts were checked one by one."""


class InvalidLidarMesh(ValueError):
    pass


@dataclass(frozen=True)
class MeshSize:
    parts: int = 0
    vertices: int = 0
    triangles: int = 0

    def plus(self, part: LidarMeshPart) -> MeshSize:
        grown = MeshSize(
            self.parts + 1, self.vertices + len(part.vertices) // 3, self.triangles + len(part.triangles) // 3
        )
        if grown.parts > MAX_PARTS or grown.vertices > MAX_TOTAL_VERTICES or grown.triangles > MAX_TOTAL_TRIANGLES:
            raise InvalidLidarMesh("mesh exceeds its part, vertex or triangle limit")
        return grown


def _past_whitespace(text: str, at: int) -> int:
    match = _WHITESPACE.match(text, at)
    return match.end() if match else at


class _MeshText:
    """The file as UTF-8 text, read a chunk at a time, with one JSON value decoded at a time."""

    def __init__(self, handle: BinaryIO):
        self._handle = handle
        self._utf8 = codecs.getincrementaldecoder("utf-8")()
        self._text = ""
        self._at = 0
        self._ended = False

    def _read_more(self, at_least: int) -> bool:
        """Append the next chunk, at least `at_least` bytes of it. False once the file is used up."""
        if self._ended:
            return False
        chunk = self._handle.read(max(READ_CHUNK_BYTES, at_least))
        self._ended = not chunk
        self._text = self._text[self._at:] + self._utf8.decode(chunk, final=self._ended)
        self._at = 0
        return True

    def next_char(self) -> str:
        """The next character that isn't whitespace, taken; empty at the end of the file."""
        while True:
            self._at = _past_whitespace(self._text, self._at)
            if self._at < len(self._text):
                self._at += 1
                return self._text[self._at - 1]
            if not self._read_more(0):
                return ""

    def peek_char(self) -> str:
        char = self.next_char()
        self._at -= len(char)
        return char

    def expect(self, char: str) -> None:
        if self.next_char() != char:
            raise InvalidLidarMesh(f"expected {char!r}")

    def value(self, max_chars: int) -> object:
        """Decode the next JSON value, reading more of the file until it is whole or passes `max_chars`.

        A decode that stops at the end of the text read so far may be cut short
        (a number, or a value split across reads), so it is retried with more.
        Each retry reads at least as much again as is pending, so a large value
        costs about twice its decode, never a decode per chunk.
        """
        self.peek_char()
        while True:
            end = self._decoded_end()
            if end is not None:
                value, self._at = end
                return value
            pending = len(self._text) - self._at
            if pending > max_chars or not self._read_more(pending):
                raise InvalidLidarMesh("a value is malformed or larger than it may be")

    def _decoded_end(self) -> tuple[object, int] | None:
        try:
            value, end = _DECODER.raw_decode(self._text, self._at)
        except json.JSONDecodeError:
            return None
        if end == len(self._text) and not self._ended:
            return None
        return value, end


def _object_keys(text: _MeshText) -> Iterator[str]:
    """Each key of the top-level object in turn, leaving the text at that key's value."""
    text.expect("{")
    if text.peek_char() == "}":
        text.next_char()
        return
    while True:
        key = text.value(MAX_FIELD_CHARS)
        if not isinstance(key, str):
            raise InvalidLidarMesh("a key must be a string")
        text.expect(":")
        yield key
        separator = text.next_char()
        if separator == "}":
            return
        if separator != ",":
            raise InvalidLidarMesh("expected ',' or '}'")


def _checked_parts(text: _MeshText) -> MeshSize:
    text.expect("[")
    size = MeshSize()
    while True:
        size = size.plus(LidarMeshPart.model_validate(text.value(MAX_PART_CHARS)))
        separator = text.next_char()
        if separator == "]":
            return size
        if separator != ",":
            raise InvalidLidarMesh("expected ',' or ']'")


def _checked_mesh(text: _MeshText) -> MeshSize:
    fields: dict[str, object] = {}
    size = MeshSize()
    for key in _object_keys(text):
        if key in fields:
            raise InvalidLidarMesh(f"{key} appears twice")
        if key == "parts":
            size = _checked_parts(text)
            fields[key] = [_STAND_IN_PART]
        else:
            fields[key] = text.value(MAX_FIELD_CHARS)
    if text.next_char():
        raise InvalidLidarMesh("text after the mesh")
    LidarMesh.model_validate(fields)
    return size


def validate_lidar_mesh_file(path: pathlib.Path) -> MeshSize:
    """Check the mesh at `path` against the `LidarMesh` contract, and say how big it is."""
    try:
        with path.open("rb") as handle:
            return _checked_mesh(_MeshText(handle))
    except (UnicodeDecodeError, ValidationError, RecursionError) as error:
        raise InvalidLidarMesh("invalid lidar mesh") from error
