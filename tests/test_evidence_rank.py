from __future__ import annotations

from bili_fact_checker.evidence.rank import (
    BgeEvidenceReranker,
    LexicalEvidenceReranker,
    split_passages,
)


def test_passages_retain_exact_document_offsets():
    text = "短行\n\n  这是一个足够长的正文段落，用来验证字符区间可以回到原始正文。  \n"
    passages = split_passages(text)
    assert len(passages) == 1
    passage = passages[0]
    assert text[passage.start_char : passage.end_char] == passage.text


def test_lexical_reranker_requires_real_overlap_and_is_deterministic():
    text = (
        "这是完全无关的长段落，用于验证搜索系统不会把任意正文都当成证据材料。\n"
        "世界卫生组织的报告指出，该指标在2024年下降10%，并说明了统计范围。\n"
    )
    passages = split_passages(text)
    reranker = LexicalEvidenceReranker()
    ranked = reranker.rank("世界卫生组织 2024年 指标下降10%", passages, limit=2)
    assert len(ranked) == 1
    assert "世界卫生组织" in ranked[0].passage.text
    assert reranker.rank("火星人口调查", passages, limit=2) == []


class FakeCrossEncoder:
    def predict(self, pairs, *, show_progress_bar):
        assert show_progress_bar is False
        assert len(pairs) == 2
        return [0.1, 0.9]


def test_bge_adapter_only_ranks_and_preserves_passages():
    passages = split_passages(
        "第一段正文内容足够长，用来模拟相关性较低的候选段落。\n"
        "第二段正文内容同样足够长，用来模拟模型打分更高的候选段落。\n"
    )
    reranker = BgeEvidenceReranker("fixture", model=FakeCrossEncoder())
    ranked = reranker.rank("查询", passages, limit=1)
    assert ranked[0].passage == passages[1]
    assert ranked[0].score == 0.9
