"""Show where a long ingest is: one live line per stage, then a line that stays when the stage is done."""

import time
from collections.abc import Callable
from typing import TextIO

_BAR = 20
_QUIET_EVERY = 10  # when output is not a terminal, print every this many units


def _duration(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"


class ProgressPrinter:
    """Receives `begin`, `step` and `end` from the pipeline. The time left is worked out from the units that really
    ran (cached ones are instant and would make it look too optimistic once the real work starts)."""

    def __init__(self, stream: TextIO, clock: Callable[[], float] = time.monotonic, live: bool | None = None) -> None:
        self._out = stream
        self._clock = clock
        self._live = stream.isatty() if live is None else live
        self._book = ""
        self._stage = ""
        self._total = 0
        self._done = 0
        self._ran = 0
        self._cached = 0
        self._failed = 0
        self._ran_seconds = 0.0
        self._cost = 0.0
        self._tokens = 0
        self._started = 0.0
        self._last = 0.0
        self._announced = ""

    def begin(self, book: str, stage: str, total: int) -> None:
        if book != self._announced:
            self._announced = book
            self._write(f"\n▸ {book[:90]}\n")
        self._stage, self._total = stage, total
        self._done = self._ran = self._cached = self._failed = 0
        self._ran_seconds = self._cost = 0.0
        self._tokens = 0
        self._started = self._last = self._clock()

    def step(self, status: str, cost_usd: float = 0.0, tokens: int = 0) -> None:
        now = self._clock()
        self._done += 1
        if status == "cached":
            self._cached += 1
        else:
            self._ran += 1
            self._ran_seconds += now - self._last
            if status == "failed":
                self._failed += 1
        self._last = now
        self._cost += cost_usd
        self._tokens += tokens
        if self._live:
            self._write("\r\x1b[K" + self._line())
        elif self._done % _QUIET_EVERY == 0 and self._done < self._total:
            self._write(self._line() + "\n")

    def end(self) -> None:
        took = _duration(self._clock() - self._started)
        parts = [f"{self._stage:<9}{self._done}/{self._total}", f"{self._ran} new", f"{self._cached} cached"]
        if self._failed:
            parts.append(f"{self._failed} FAILED")
        if self._ran and self._cost:
            parts.append(f"${self._cost:.4f}")
        parts.append(took)
        self._write(("\r\x1b[K" if self._live else "") + "  ✓ " + " · ".join(parts) + "\n")

    def _line(self) -> str:
        filled = _BAR * self._done // self._total if self._total else _BAR
        parts = [f"  {self._stage:<9}[{'█' * filled}{'░' * (_BAR - filled)}] {self._done}/{self._total}"]
        parts.append(f"{self._cached} cached")
        if self._ran:
            average = self._ran_seconds / self._ran
            parts.append(f"{average:.1f}s each")
            left = self._total - self._done
            if left:
                parts.append(f"~{_duration(average * left)} left")
        if self._cost:
            parts.append(f"${self._cost:.4f}")
        if self._tokens:
            parts.append(f"{self._tokens / 1000:.1f}k tokens")
        return " · ".join(parts)

    def _write(self, text: str) -> None:
        self._out.write(text)
        self._out.flush()
