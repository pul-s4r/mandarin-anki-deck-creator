from __future__ import annotations

from dataclasses import dataclass
from random import Random

from anki_deck_generator.linking.term_index import TermIndex


@dataclass(frozen=True)
class TermMatch:
    term: str
    start: int
    end: int


def _all_matches(sentence: str, term: str) -> list[TermMatch]:
    out: list[TermMatch] = []
    if not term:
        return out
    start = 0
    while True:
        i = sentence.find(term, start)
        if i == -1:
            return out
        out.append(TermMatch(term=term, start=i, end=i + len(term)))
        start = i + 1


def _dominant_matches(matches: list[TermMatch]) -> list[TermMatch]:
    """
    Apply a simple longest-match dominance rule for overlaps:
    - Sort by start asc, length desc.
    - Greedily keep non-overlapping matches.
    """
    matches_sorted = sorted(matches, key=lambda m: (m.start, -(m.end - m.start), m.term))
    kept: list[TermMatch] = []
    last_end = -1
    for m in matches_sorted:
        if m.start < last_end:
            continue
        kept.append(m)
        last_end = m.end
    return kept


def find_candidate_matches(sentence: str, terms: list[str]) -> list[TermMatch]:
    """
    Return candidate matches after longest-match dominance filtering.
    """
    all_m: list[TermMatch] = []
    for t in terms:
        all_m.extend(_all_matches(sentence, t))
    if not all_m:
        return []
    return _dominant_matches(all_m)


def choose_winner_key(
    sentence: str,
    *,
    index: TermIndex,
    candidate_matches: list[TermMatch],
    strategy: str = "importance",
    random_seed: int | None = None,
) -> int | None:
    """
    Choose exactly one Key for a sentence, or None if no candidates.

    importance strategy (deterministic):
    - longer term wins
    - rarer term wins (lower frequency in index)
    - earlier occurrence wins
    - lexicographic term as final tie-break

    random strategy:
    - choose a random candidate term; seedable
    """
    if not candidate_matches:
        return None

    if strategy == "random":
        rng = Random(random_seed)
        m = rng.choice(candidate_matches)
        keys = index.keys_for(m.term)
        return min(keys) if keys else None

    if strategy != "importance":
        raise ValueError(f"unknown strategy: {strategy}")

    def score(m: TermMatch) -> tuple[int, int, int, str]:
        # negative length (so max length sorts first), frequency (lower is better), start (lower is better)
        return (-(m.end - m.start), index.frequency(m.term), m.start, m.term)

    winner = min(candidate_matches, key=score)
    keys = index.keys_for(winner.term)
    return min(keys) if keys else None

