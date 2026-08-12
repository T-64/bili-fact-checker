"""Passage ranking for exact evidence extraction.

Rankers select relevant passages; they never assign evidence stance or verdicts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from bili_fact_checker.config import Settings


class RerankerUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class Passage:
    text: str
    start_char: int
    end_char: int


@dataclass(frozen=True)
class RankedPassage:
    passage: Passage
    score: float


class EvidenceReranker(Protocol):
    name: str

    def rank(
        self, query: str, passages: Sequence[Passage], *, limit: int
    ) -> list[RankedPassage]: ...


def split_passages(text: str, *, min_chars: int = 20) -> list[Passage]:
    """Return paragraph passages with exact offsets into the retained document."""

    passages: list[Passage] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        raw = line.rstrip("\r\n")
        start = offset
        end = start + len(raw)
        offset += len(line)
        clean = raw.strip()
        if len(clean) < min_chars:
            continue
        leading = len(raw) - len(raw.lstrip())
        trailing = len(raw) - len(raw.rstrip())
        passages.append(
            Passage(
                text=clean,
                start_char=start + leading,
                end_char=end - trailing,
            )
        )
    return passages


def search_terms(query: str) -> set[str]:
    lower = query.lower()
    terms = set(re.findall(r"[a-z0-9][a-z0-9.-]{2,}", lower))
    for run in re.findall(r"[\u3400-\u9fff]{2,}", lower):
        if len(run) <= 6:
            terms.add(run)
        terms.update(run[index : index + 2] for index in range(len(run) - 1))
    return {term for term in terms if term.strip()}


class LexicalEvidenceReranker:
    name = "lexical"

    def rank(
        self, query: str, passages: Sequence[Passage], *, limit: int
    ) -> list[RankedPassage]:
        terms = search_terms(query)
        if not terms or limit <= 0:
            return []
        ranked: list[RankedPassage] = []
        for passage in passages:
            lower = passage.text.lower()
            matches = {term for term in terms if term in lower}
            if not matches:
                continue
            weighted = sum(min(len(term), 8) for term in matches)
            coverage = len(matches) / len(terms)
            density = weighted / max(len(passage.text), 80)
            ranked.append(
                RankedPassage(
                    passage=passage,
                    score=float(weighted + 4 * coverage + density),
                )
            )
        ranked.sort(
            key=lambda item: (-item.score, item.passage.start_char)
        )
        return ranked[:limit]


class BgeEvidenceReranker:
    name = "bge"

    def __init__(self, model_name: str, *, model: Any | None = None) -> None:
        if model is None:
            try:
                from sentence_transformers import CrossEncoder
            except ImportError as exc:
                raise RerankerUnavailableError(
                    "BGE 重排需要可选依赖：pip install 'bili-fact-checker[local-ml]'"
                ) from exc
            model = CrossEncoder(model_name)
        self.model_name = model_name
        self._model = model

    def rank(
        self, query: str, passages: Sequence[Passage], *, limit: int
    ) -> list[RankedPassage]:
        if not query.strip() or not passages or limit <= 0:
            return []
        scores = self._model.predict(
            [(query, passage.text) for passage in passages],
            show_progress_bar=False,
        )
        ranked = [
            RankedPassage(passage=passage, score=float(score))
            for passage, score in zip(passages, scores, strict=True)
        ]
        ranked.sort(
            key=lambda item: (-item.score, item.passage.start_char)
        )
        return ranked[:limit]


def build_evidence_reranker(settings: Settings) -> EvidenceReranker:
    selected = settings.evidence_reranker.strip().lower() or "lexical"
    if selected in {"lexical", "keyword"}:
        return LexicalEvidenceReranker()
    if selected in {"bge", "local-ml"}:
        return BgeEvidenceReranker(settings.evidence_reranker_model)
    raise RerankerUnavailableError(f"unknown evidence reranker: {selected}")
