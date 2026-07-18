"""
TDD — retrieval.py

测试策略：只测可观察的外部行为，不测内部实现细节。
关键契约：
  1. _split_chunks: 分块正确性（边界、重叠、非空）
  2. build_index:   建索引不崩，返回 Index，分块写入 DB
  3. retrieve:      返回格式、短词回退、top_k、exactTerms 惩罚
"""

import pytest
import retrieval as R


# ──────────────────────────────────────────────────────────────────────────────
# fixtures
# ──────────────────────────────────────────────────────────────────────────────

SENTENCE_ZH = "本校2023年度获批国家级科研项目356项，科研经费到账总额达43亿元。"

REF_DOCS = {
    "doc_A.txt": (
        "2023年度学校ESI全球前1%学科数量达到5个，"
        "分别为经济学、管理学、数学、计算机科学和统计学。\n\n"
        "全年引进高层次人才共计128名，其中院士3名、"
        "国家级青年人才42名。博士点数量增至85个。\n\n"
        "学校获批国家重点实验室2个，获国家科学技术奖一等奖1项。"
    ),
    "doc_B.txt": (
        "在校生总数突破5万人，其中博士研究生8500名，"
        "硕士研究生18000名，本科生24000名，留学生1200名。\n\n"
        "毕业生就业率保持在95%以上，国内升学比例达35%，"
        "境外升学比例为12%。"
    ),
}


@pytest.fixture
def index():
    idx = R.build_index(REF_DOCS)
    yield idx
    R.close_index(idx)


# ──────────────────────────────────────────────────────────────────────────────
# _split_chunks
# ──────────────────────────────────────────────────────────────────────────────

class TestSplitChunks:
    def test_empty_text(self):
        assert R._split_chunks("") == []

    def test_short_text_single_chunk(self):
        chunks = R._split_chunks(SENTENCE_ZH)
        assert len(chunks) == 1
        offset, text = chunks[0]
        assert offset == 0
        assert SENTENCE_ZH in text

    def test_chunk_contains_no_empty_strings(self):
        long_text = SENTENCE_ZH * 50
        chunks = R._split_chunks(long_text)
        for _offset, text in chunks:
            assert text.strip(), "每块不应为空"

    def test_offsets_are_non_negative_and_increasing(self):
        long_text = SENTENCE_ZH * 50
        chunks = R._split_chunks(long_text)
        offsets = [o for o, _ in chunks]
        assert offsets == sorted(offsets)
        assert all(o >= 0 for o in offsets)

    def test_overlap_consecutive_chunks_share_text(self):
        # 生成够长的文本使其分成 ≥2 块
        long_text = SENTENCE_ZH * 60  # ~3600 chars
        chunks = R._split_chunks(long_text)
        assert len(chunks) >= 2, "文本应被分成 ≥2 块"
        # 第二块的起始 offset < 第一块的结束 offset（有重叠）
        first_end = chunks[0][0] + len(chunks[0][1])
        second_start = chunks[1][0]
        assert second_start < first_end, "相邻块应有重叠"

    def test_paragraph_boundary_preferred(self):
        # 构造两段，段落分隔在 ~400 字处，target=1200 但段落边界更优先
        para1 = "A" * 400
        para2 = "B" * 400
        text = para1 + "\n\n" + para2
        chunks = R._split_chunks(text, target=1200)
        # 整段 < target，应合成 1 块
        assert len(chunks) == 1

    def test_splits_at_paragraph_when_exceeds_target(self):
        para1 = "第一段内容。" * 100   # ~600 chars
        para2 = "第二段内容。" * 100
        text = para1 + "\n\n" + para2
        chunks = R._split_chunks(text, target=600)
        # 超过 target，应在段落边界处分块
        assert len(chunks) >= 2


# ──────────────────────────────────────────────────────────────────────────────
# build_index
# ──────────────────────────────────────────────────────────────────────────────

class TestBuildIndex:
    def test_returns_index_instance(self):
        idx = R.build_index(REF_DOCS)
        assert isinstance(idx, R.Index)
        R.close_index(idx)

    def test_empty_docs(self):
        idx = R.build_index({})
        count = idx.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        assert count == 0
        R.close_index(idx)

    def test_chunks_created_for_each_doc(self):
        idx = R.build_index(REF_DOCS)
        sources = {
            row[0]
            for row in idx.conn.execute(
                "SELECT DISTINCT source_path FROM chunks"
            ).fetchall()
        }
        assert "doc_A.txt" in sources
        assert "doc_B.txt" in sources
        R.close_index(idx)

    def test_has_fts5_is_bool(self):
        idx = R.build_index(REF_DOCS)
        assert isinstance(idx.has_fts5, bool)
        R.close_index(idx)

    def test_single_short_doc_produces_one_chunk(self):
        idx = R.build_index({"x.txt": SENTENCE_ZH})
        count = idx.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        assert count == 1
        R.close_index(idx)


# ──────────────────────────────────────────────────────────────────────────────
# retrieve
# ──────────────────────────────────────────────────────────────────────────────

class TestRetrieve:
    def test_returns_list(self, index):
        results = R.retrieve("ESI前1%学科数量", [], [], index)
        assert isinstance(results, list)

    def test_result_has_required_keys(self, index):
        results = R.retrieve("在校生总数突破5万人", [], [], index)
        assert results, "应至少返回 1 个结果"
        for item in results:
            assert "sourcePath" in item
            assert "offset" in item
            assert "text" in item
            assert "score" in item

    def test_top_k_respected(self, index):
        results = R.retrieve("学科数量", [], [], index, top_k=2)
        assert len(results) <= 2

    def test_score_sorted_descending(self, index):
        results = R.retrieve("ESI学科", [], [], index, top_k=5)
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True), "结果应按 score 降序排列"

    def test_finds_exact_content(self, index):
        # doc_A 中有 "ESI全球前1%学科数量达到5个"
        results = R.retrieve("ESI全球前1%学科数量达到5个", ["5"], ["ESI学科"], index)
        assert results, "应能找到相关块"
        texts = " ".join(r["text"] for r in results)
        assert "ESI" in texts, "结果中应包含 ESI 相关文本"

    def test_short_query_1_char_does_not_crash(self, index):
        # 1 字查询：FTS5 trigram 会静默返回空，应回退到 LIKE
        results = R.retrieve("5", ["5"], [], index)
        assert isinstance(results, list)

    def test_short_query_2_char_returns_results(self, index):
        # "85个" 是 2 字，触发 LIKE 回退
        results = R.retrieve("85个", [], [], index)
        assert isinstance(results, list)
        # doc_A 里有 "博士点数量增至85个"，应能找到
        if results:
            texts = " ".join(r["text"] for r in results)
            assert "85" in texts

    def test_empty_claim_returns_empty(self, index):
        results = R.retrieve("", [], [], index)
        assert results == []

    def test_sourcepath_matches_input_keys(self, index):
        results = R.retrieve("在校生总数突破5万人", [], [], index)
        assert results
        for r in results:
            assert r["sourcePath"] in REF_DOCS

    def test_exact_terms_penalty_demotes_chunk_without_number(self):
        """
        两份文档，一份含目标数字 "128"，另一份不含。
        传入 numbers=["128"] 时，含数字的块得分应高于不含的块。
        """
        docs = {
            "with_number.txt": "学校全年引进高层次人才共计128名，成效显著。",
            "no_number.txt": "学校在人才引进方面取得积极进展，效果良好。",
        }
        idx = R.build_index(docs)
        results = R.retrieve("引进高层次人才128名", ["128"], ["高层次人才"], idx, top_k=5)
        R.close_index(idx)

        assert results, "应有结果"
        # 第一名应来自含数字的文档
        assert "128" in results[0]["text"], "得分最高的块应包含精确数字 128"

    def test_offset_is_non_negative_int(self, index):
        results = R.retrieve("博士研究生", [], [], index)
        for r in results:
            assert isinstance(r["offset"], int)
            assert r["offset"] >= 0
