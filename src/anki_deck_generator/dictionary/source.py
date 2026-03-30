from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterator


class DictionarySource(ABC):
    @abstractmethod
    def iter_lines(self) -> Iterator[str]:
        raise NotImplementedError


class FileLineDictionarySource(DictionarySource):
    def __init__(self, path: Path, *, encoding: str = "utf-8") -> None:
        self._path = path
        self._encoding = encoding

    def iter_lines(self) -> Iterator[str]:
        with self._path.open(encoding=self._encoding, errors="replace") as f:
            for line in f:
                yield line.rstrip("\n\r")
