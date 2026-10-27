"""Tracks the day's fixed blocks and occupied time, and searches for free slots."""

from __future__ import annotations

from dataclasses import dataclass, field

POINT_ITEM_MAX_MINUTES = 5


def is_point(duration: int) -> bool:
    """Short items such as a 1-minute dose do not occupy time."""
    return duration <= POINT_ITEM_MAX_MINUTES


@dataclass
class Block:
    """A fixed block of busy time."""

    key: str
    label: str
    start: int
    end: int
    kind: str


@dataclass
class DayState:
    """Mutable record of what is taken on one day."""

    blocks: list[Block] = field(default_factory=list)
    away: list[tuple[int, int]] = field(default_factory=list)
    occupied: list[tuple[int, int]] = field(default_factory=list)

    def block_at(self, start: int, end: int) -> Block | None:
        """Return the first fixed block overlapping the range, if any."""
        for block in sorted(self.blocks, key=lambda b: (b.start, b.key)):
            if start < block.end and block.start < max(end, start + 1):
                return block
        return None

    def is_free(self, start: int, end: int) -> bool:
        """True when the range overlaps no fixed block and no occupied time."""
        if self.block_at(start, end):
            return False
        return not any(start < e and s < end for s, e in self.occupied)

    def is_away(self, start: int, end: int) -> bool:
        """True when the range overlaps time away from home."""
        return any(start < e and s < end for s, e in self.away)

    def occupy(self, start: int, end: int) -> None:
        """Mark a range as taken by a non-point item."""
        if not is_point(end - start):
            self.occupied.append((start, end))

    def next_free(
        self, earliest: int, duration: int, latest_end: int, at_home: bool = False
    ) -> int | None:
        """Return the first start at or after `earliest` where the item fits, or None."""
        start = earliest
        while start + duration <= latest_end:
            if self.is_free(start, start + duration) and not (
                at_home and self.is_away(start, start + duration)
            ):
                return start
            start = self._next_candidate(start, duration, at_home)
        return None

    def _next_candidate(self, start: int, duration: int, at_home: bool) -> int:
        ends = [b.end for b in self.blocks] + [e for _, e in self.occupied]
        if at_home:
            ends += [e for _, e in self.away]
        later = [e for e in ends if e > start]
        return min(later) if later else start + max(duration, 1) + 24 * 60
