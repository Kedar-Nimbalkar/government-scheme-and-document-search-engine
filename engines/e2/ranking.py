"""Passage BM25, bounded heap selection, and UTF-16 snippet spans."""

import math
from bisect import bisect_left, bisect_right
from dataclasses import dataclass

from .query import Atom

SNIPPET_LENGTH = 280
LEADING_CONTEXT = 64


@dataclass(frozen=True)
class BM25:
    k1: float = 1.2
    b: float = 0.75

    def __post_init__(self):
        if not math.isfinite(self.k1) or self.k1 <= 0 or not 0 <= self.b <= 1:
            raise ValueError("BM25 requires finite k1 > 0 and 0 <= b <= 1")

    def contribution(self, frequency, length, avgdl, idf):
        norm = self.k1 * (1 - self.b + self.b * length / avgdl)
        return idf * frequency * (self.k1 + 1) / (frequency + norm)


def inverse_document_frequency(count: int, frequency: int) -> float:
    return math.log1p((count - frequency + 0.5) / (frequency + 0.5))


class TopK:
    """A custom min-heap that retains only the best capacity items."""

    def __init__(self, capacity: int):
        if capacity < 1:
            raise ValueError("Heap capacity must be positive")
        self.capacity = capacity
        self.items = []

    def push(self, key: tuple, item):
        entry = (key, item)
        heap = self.items
        if len(heap) < self.capacity:
            heap.append(entry)
            index = len(heap) - 1
            while index:
                parent = (index - 1) // 2
                if heap[parent][0] <= key:
                    break
                heap[index] = heap[parent]
                index = parent
            heap[index] = entry
        elif key > heap[0][0]:
            index = 0
            while (child := index * 2 + 1) < len(heap):
                right = child + 1
                if right < len(heap) and heap[right][0] < heap[child][0]:
                    child = right
                if heap[child][0] >= key:
                    break
                heap[index] = heap[child]
                index = child
            heap[index] = entry

    def descending(self):
        return [item for _, item in sorted(self.items, key=lambda entry: entry[0], reverse=True)]


def snippet(passage: dict, matches: dict[Atom, list[int]]) -> tuple[str, list[str], list[dict]]:
    positions = {}
    for atom, starts in matches.items():
        for start in starts:
            for offset, term in enumerate(atom.terms):
                positions[start + offset] = term
    raw_spans = passage["token_spans"]
    spans = [(raw_spans[position]["start"], raw_spans[position]["end"], term) for position, term in positions.items()]
    text = passage["text"]
    offsets = [0]
    for char in text:
        offsets.append(offsets[-1] + (2 if ord(char) > 0xFFFF else 1))
    first = min((start for start, _, _ in spans), default=0)
    wanted = min(max(0, first - LEADING_CONTEXT), max(0, offsets[-1] - SNIPPET_LENGTH))
    left = bisect_left(offsets, wanted)
    right = bisect_right(offsets, offsets[left] + SNIPPET_LENGTH) - 1
    start, end = offsets[left], offsets[right]
    visible_terms = set()
    visible_spans = []
    for a, b, term in sorted(spans):
        a, b = max(a, start) - start, min(b, end) - start
        if a >= b:
            continue
        visible_terms.add(term)
        if visible_spans and a < visible_spans[-1]["end"]:
            visible_spans[-1]["end"] = max(visible_spans[-1]["end"], b)
        else:
            visible_spans.append({"start": a, "end": b})
    return text[left:right], sorted(visible_terms), visible_spans
