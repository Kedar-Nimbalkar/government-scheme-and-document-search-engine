"""Vocabulary Trie, pruned edit-distance search, and curated query rewrites."""

import json
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from heapq import nsmallest
from pathlib import Path
from typing import Callable

from search_shared.tokenizer import tokenize

from .query import Atom, Query, positive_atoms

MAX_PREFIX = 64
MAX_SUGGESTIONS = 8
MAX_EDIT_NODES = 2048
MAX_EDIT_CANDIDATES = 32
MAX_EDIT_TERMS = 8
MAX_EXPANSIONS_PER_ATOM = 3


@dataclass
class TrieNode:
    children: dict[str, "TrieNode"] = field(default_factory=dict)
    term: str | None = None
    best: tuple[str, ...] = ()


@dataclass(frozen=True)
class Correction:
    term: str
    automatic: bool


def edit_row(previous: list[int], query: str, char: str) -> list[int]:
    row = [previous[0] + 1]
    for index, expected in enumerate(query, 1):
        row.append(min(row[-1] + 1, previous[index] + 1, previous[index - 1] + (char != expected)))
    return row


class Trie:
    def __init__(self, frequencies: dict[str, int]):
        self.frequencies = frequencies
        self.root = TrieNode()
        nodes = [self.root]
        for term in sorted(frequencies):
            node = self.root
            for char in term:
                if char not in node.children:
                    node.children[char] = TrieNode()
                    nodes.append(node.children[char])
                node = node.children[char]
            node.term = term

        for node in reversed(nodes):
            combined = [term for child in node.children.values() for term in child.best]
            if node.term is not None:
                combined.append(node.term)
            node.best = tuple(nsmallest(MAX_SUGGESTIONS, combined, key=self._ranking))

    def _ranking(self, term):
        return -self.frequencies[term], term

    def suggest(self, prefix: str) -> list[str]:
        node = self.root
        if not prefix:
            return []
        for char in prefix:
            node = node.children.get(char)
            if node is None:
                return []
        return list(node.best)

    def correct(self, term: str, check: Callable[[], None]) -> Correction | None:
        if (term in self.frequencies or not 4 <= len(term) <= 32
                or any(unicodedata.category(c)[0] not in "LM" for c in term)):
            return None
        candidates, complete, visits = self._nearby(term, 1, MAX_EDIT_NODES, check)
        if candidates:
            return Correction(candidates[0][1], automatic=complete and len(candidates) == 1 and len(term) >= 5)
        if not complete or len(term) < 6:
            return None
        # A wider pass can suggest a severe correction but never applies it.
        candidates, _, _ = self._nearby(term, 3 if len(term) >= 7 else 2, MAX_EDIT_NODES - visits, check)
        return Correction(candidates[0][1], automatic=False) if candidates else None

    def _nearby(self, term, distance, node_limit, check):
        stack = [(iter(self.root.children.items()), list(range(len(term) + 1)))]
        candidates = []
        visits = 0
        while stack:
            children, parent_row = stack[-1]
            edge = next(children, None)
            if edge is None:
                stack.pop()
                continue
            if visits >= node_limit or len(candidates) >= MAX_EDIT_CANDIDATES:
                break
            if visits % 32 == 0:
                check()
            visits += 1
            char, node = edge
            row = edit_row(parent_row, term, char)
            if min(row) > distance:
                continue
            if node.term is not None and 0 < row[-1] <= distance:
                candidates.append((row[-1], node.term))
            if node.children:
                stack.append((iter(node.children.items()), row))
        candidates.sort(key=lambda item: (item[0], *self._ranking(item[1])))
        return candidates, not stack, visits


@lru_cache(maxsize=1)
def curated_aliases() -> dict[str, tuple[tuple[str, ...], ...]]:
    path = Path(__file__).with_name("aliases.json")
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {
        term: tuple(dict.fromkeys(tuple(tokenize(alternative)) for alternative in item["alternatives"]))
        for term, item in rows.items()
    }


def assist(query: str, parsed: Query, trie: Trie, check: Callable[[], None]) -> tuple[Query, str | None]:
    if parsed.warning:
        return parsed, None
    aliases = curated_aliases()
    corrections = {}
    replacements = {}
    postfix = []
    for token in parsed.postfix:
        if not isinstance(token, Atom) or token.quoted:
            postfix.append(token)
            continue
        term = token.terms[0]
        if (term not in aliases and term not in trie.frequencies and not token.acronym and token.source_span):
            if term not in corrections and len(corrections) < MAX_EDIT_TERMS:
                corrections[term] = trie.correct(term, check)
            if (correction := corrections.get(term)) is not None:
                replacements[token.source_span] = correction.term
                if correction.automatic:
                    term = correction.term

        postfix.append(Atom((term,)))
        for alternative in aliases.get(term, ())[:MAX_EXPANSIONS_PER_ATOM]:
            if alternative and alternative != (term,) and all(word in trie.frequencies for word in alternative):
                postfix.extend((Atom(alternative, quoted=len(alternative) > 1), "OR"))

    suggested = None
    if replacements:
        suggested = query
        for (start, end), replacement in sorted(replacements.items(), reverse=True):
            suggested = suggested[:start] + replacement + suggested[end:]
    return Query(postfix, positive_atoms(postfix)), suggested
