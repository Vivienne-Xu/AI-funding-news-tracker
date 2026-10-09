"""Step 3: group items that report the same event. One cluster = one event."""

from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz

from app.collect import RawItem
from app.prefilter import Candidate, Kind

NAME_MATCH_MIN = 93  # similarity (0-100) for two company names to count as the same
TITLE_MATCH_MIN = 85  # used only when a headline has no recognisable company name

HEADLINE_VERBS = (
    r"(?:to\s+)?raises?|raised|secures?|secured|lands?|landed|closes?|closed|nabs?|bags?|snags?|banks?|"
    r"scores?|gets|picks\s+up|reels?\s+in|announces?|completes?|wins?|receives?|attracts?|reveals?|"
    r"confirms?|emerges?|unveils?|"
    r"shuts?\s+down|winds?\s+down|lays?\s+off|laid\s+off|joins?"
)
COMPANY_RE = re.compile(rf"^(?P<company>.{{2,60}}?)\s+(?:{HEADLINE_VERBS})\b", re.IGNORECASE)
PREFIX_RE = re.compile(r"^(exclusive|breaking|report|update|scoop)\s*[:\-\u2013]\s*", re.IGNORECASE)
# Descriptions in front of the name: "UK-based Acme", "AI startup Acme", "Edtech firm Acme"
BASED_RE = re.compile(r"^.*-based\s+(?=\S)|^\w+['\u2019]s\s+(?=\S)", re.IGNORECASE)  # also "France's Acme"
DESCRIPTOR_RE = re.compile(
    r"^.*\b(?:startup|start-up|firm|company|unicorn|maker|developer|provider|platform)\s+(?=\S)", re.IGNORECASE
)
SUFFIX_WORDS = {"ai", "inc", "ltd", "llc", "corp", "co", "gmbh", "limited"}
MIN_SUFFIX_NAME_LENGTH = 5


def company_from_title(title: str) -> str | None:
    """Guesses the company from a headline like 'Acme raises $10M'. None if unclear."""
    match = COMPANY_RE.match(PREFIX_RE.sub("", title.strip()))
    if not match:
        return None
    name = match.group("company").split(",")[0].strip()
    name = DESCRIPTOR_RE.sub("", BASED_RE.sub("", name)).strip()
    return name or None


def normalise_name(name: str) -> str:
    """Lower case, no punctuation, no 'AI' / 'Inc' endings: 'Mistral AI' -> 'mistral'."""
    words = re.sub(r"[^a-z0-9]+", " ", name.lower()).split()
    if words and words[0] == "the":
        words = words[1:]
    while len(words) > 1 and words[-1] in SUFFIX_WORDS:
        words.pop()
    return " ".join(words)


def same_company(norm_a: str, norm_b: str) -> bool:
    """Equal, nearly equal, or one is the other with extra words in front ('insurtech acme' / 'acme')."""
    if norm_a == norm_b or fuzz.ratio(norm_a, norm_b) >= NAME_MATCH_MIN:
        return True
    short, long = sorted((norm_a, norm_b), key=len)
    return len(short) >= MIN_SUFFIX_NAME_LENGTH and long.endswith(" " + short)


def same_event(a: Candidate, b: Candidate) -> bool:
    if a.kind != b.kind:
        return False
    name_a, name_b = company_from_title(a.item.title), company_from_title(b.item.title)
    if name_a and name_b:
        return same_company(normalise_name(name_a), normalise_name(name_b))
    return fuzz.token_sort_ratio(a.item.title.lower(), b.item.title.lower()) >= TITLE_MATCH_MIN


@dataclass(frozen=True)
class Cluster:
    kind: Kind
    company: str | None
    best: RawItem  # highest-tier source, then longest summary
    items: tuple[RawItem, ...]

    @property
    def urls(self) -> list[str]:
        return [i.url for i in self.items]


def _best_item(items: list[RawItem]) -> RawItem:
    return min(items, key=lambda i: (i.tier, -len(i.summary)))


def cluster(candidates: list[Candidate]) -> list[Cluster]:
    """Groups candidates into events. Largest clusters (most outlets) come first."""
    parent = list(range(len(candidates)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            if same_event(candidates[i], candidates[j]):
                parent[find(j)] = find(i)

    groups: dict[int, list[Candidate]] = {}
    for index, candidate in enumerate(candidates):
        groups.setdefault(find(index), []).append(candidate)

    clusters = []
    for members in groups.values():
        items = [m.item for m in members]
        best = _best_item(items)
        clusters.append(
            Cluster(
                kind=members[0].kind,
                company=company_from_title(best.title),
                best=best,
                items=tuple(sorted(items, key=lambda i: (i.tier, i.source))),
            )
        )
    clusters.sort(key=lambda c: (-len(c.items), c.best.tier, c.best.title))
    return clusters
