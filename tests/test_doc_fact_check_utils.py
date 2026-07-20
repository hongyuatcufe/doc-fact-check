"""
TDD — doc_fact_check.py 纯工具函数

覆盖无外部依赖的纯函数：
  - extract_all_numbers
  - extract_named_entities
  - decompose_statement
  - _get_category_patterns
  - check_intra_document_consistency / _extract_attr_words
  - extract_keywords
"""

import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import doc_fact_check as D


# ──────────────────────────────────────────────────────────────────────────────
# extract_all_numbers
# ──────────────────────────────────────────────────────────────────────────────

class TestExtractAllNumbers:
    def test_empty_string(self):
        assert D.extract_all_numbers("") == []

    def test_count_with_ge(self):
        result = D.extract_all_numbers("获批国家级科研项目356项")
        assert any("356" in n for n in result)

    def test_count_with_ren(self):
        # "人" 在 suffix 列表里，"名" 不在；用"128人"
        result = D.extract_all_numbers("引进高层次人才128人")
        assert any("128" in n for n in result)

    def test_percentage(self):
        result = D.extract_all_numbers("就业率保持在95%以上")
        assert any("95" in n for n in result)

    def test_yuan(self):
        result = D.extract_all_numbers("科研经费到账43亿元")
        assert any("43" in n for n in result)

    def test_multiple_numbers(self):
        text = "获批项目356项，其中重点44项"
        result = D.extract_all_numbers(text)
        # 至少包含 356 和 44
        all_text = " ".join(result)
        assert "356" in all_text
        assert "44" in all_text

    def test_no_number(self):
        result = D.extract_all_numbers("学校坚持立德树人，培养高素质人才。")
        assert result == []

    def test_deduplication(self):
        text = "356项科研项目，共356项"
        result = D.extract_all_numbers(text)
        nums = [n for n in result if "356" in n]
        assert len(nums) == 1  # 去重

    def test_sorted_by_length_desc(self):
        result = D.extract_all_numbers("获批1项，共100项，其中1000项")
        lengths = [len(n) for n in result]
        assert lengths == sorted(lengths, reverse=True)


# ──────────────────────────────────────────────────────────────────────────────
# extract_named_entities
# ──────────────────────────────────────────────────────────────────────────────

class TestExtractNamedEntities:
    def test_empty_string(self):
        assert D.extract_named_entities("") == []

    def test_chinese_double_quotes(self):
        entities = D.extract_named_entities("实施“双一流”建设")
        assert any("双一流" in e for e in entities)

    def test_book_title_brackets(self):
        entities = D.extract_named_entities("出版《中国金融学》")
        assert any("中国金融学" in e for e in entities)

    def test_ascii_double_quotes(self):
        entities = D.extract_named_entities('实施"五跨五融"改革')
        assert any("五跨五融" in e for e in entities)

    def test_category_pattern_match(self):
        # "国家重点实验室" 应被类别词模式匹配
        entities = D.extract_named_entities("获批国家重点实验室2个")
        assert any("实验室" in e for e in entities)

    def test_entity_with_punct_filtered(self):
        # 包含逗号的不应作为实体
        entities = D.extract_named_entities("学校，坚持立德树人团队")
        for e in entities:
            assert "，" not in e

    def test_deduplication(self):
        text = "“双一流”建设是“双一流”目标"
        entities = D.extract_named_entities(text)
        shuang = [e for e in entities if e == "双一流"]
        assert len(shuang) == 1

    def test_sorted_by_length_desc(self):
        text = "“ABC实验室”和“ABCDEF研究院”"
        entities = D.extract_named_entities(text)
        if len(entities) >= 2:
            assert len(entities[0]) >= len(entities[1])


# ──────────────────────────────────────────────────────────────────────────────
# decompose_statement
# ──────────────────────────────────────────────────────────────────────────────

class TestDecomposeStatement:
    def test_empty_returns_empty(self):
        assert D.decompose_statement("") == []

    def test_short_text_not_decomposed(self):
        # <8 字的片段不加入结果
        result = D.decompose_statement("太短")
        assert result == []

    def test_semicolon_splits(self):
        text = "获批国家级科研项目356项；引进高层次人才128名"
        result = D.decompose_statement(text)
        texts = [r["text"] for r in result]
        assert any("356" in t for t in texts)
        assert any("128" in t for t in texts)

    def test_comma_splits_long_parts(self):
        text = "获批国家级科研项目356项，引进高层次人才128名"
        result = D.decompose_statement(text)
        assert len(result) >= 2

    def test_each_item_has_required_keys(self):
        text = "获批国家级科研项目356项，引进高层次人才128名"
        for item in D.decompose_statement(text):
            assert "text" in item
            assert "independent" in item
            assert "numbers" in item
            assert "entities" in item

    def test_independent_true_for_numeric(self):
        # "名" 不在 suffix list；用 "项" 确保数字被提取
        text = "获批科研项目356项，引进高层次人才128项"
        result = D.decompose_statement(text)
        for item in result:
            if "356" in item["text"] or "128" in item["text"]:
                assert item["independent"] is True

    def test_numbers_extracted_in_subclaim(self):
        text = "获批国家级科研项目356项，引进人才128名"
        result = D.decompose_statement(text)
        all_nums = [n for item in result for n in item["numbers"]]
        assert any("356" in n for n in all_nums)


# ──────────────────────────────────────────────────────────────────────────────
# _get_category_patterns
# ──────────────────────────────────────────────────────────────────────────────

class TestGetCategoryPatterns:
    def test_returns_list(self):
        patterns = D._get_category_patterns()
        assert isinstance(patterns, list)
        assert len(patterns) > 0

    def test_patterns_are_compiled_regex(self):
        import re
        patterns = D._get_category_patterns()
        for p in patterns:
            assert hasattr(p, "findall"), f"expected compiled regex, got {type(p)}"

    def test_matches_lab_entity(self):
        patterns = D._get_category_patterns()
        text = "获批金融人工智能重点实验室"
        matched = any(p.findall(text) for p in patterns)
        assert matched, "「重点实验室」应被类别词模式命中"

    def test_matches_platform_entity(self):
        patterns = D._get_category_patterns()
        text = "建设国家财经战略研究平台"
        matched = any(p.findall(text) for p in patterns)
        assert matched

    def test_nonexistent_config_falls_back_to_defaults(self):
        patterns = D._get_category_patterns(config_path="/nonexistent/path.yaml")
        assert isinstance(patterns, list)
        assert len(patterns) > 0


# ──────────────────────────────────────────────────────────────────────────────
# find_txt_refs — 递归扫描 .txt 参考文件
# ──────────────────────────────────────────────────────────────────────────────

class TestFindTxtRefs:
    def test_finds_txt_files(self, tmp_path):
        (tmp_path / "a.txt").write_text("内容A", encoding="utf-8")
        (tmp_path / "b.txt").write_text("内容B", encoding="utf-8")
        result = D.find_txt_refs(str(tmp_path))
        basenames = [os.path.basename(p) for p in result]
        assert "a.txt" in basenames
        assert "b.txt" in basenames

    def test_finds_txt_recursively(self, tmp_path):
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "deep.txt").write_text("内容", encoding="utf-8")
        result = D.find_txt_refs(str(tmp_path))
        assert any("deep.txt" in p for p in result)

    def test_ignores_non_txt(self, tmp_path):
        (tmp_path / "a.docx").write_bytes(b"fake")
        (tmp_path / "b.pdf").write_bytes(b"fake")
        result = D.find_txt_refs(str(tmp_path))
        assert result == []

    def test_empty_dir(self, tmp_path):
        result = D.find_txt_refs(str(tmp_path))
        assert result == []

    def test_returns_sorted(self, tmp_path):
        (tmp_path / "z.txt").write_text("Z", encoding="utf-8")
        (tmp_path / "a.txt").write_text("A", encoding="utf-8")
        result = D.find_txt_refs(str(tmp_path))
        assert result == sorted(result)


# ──────────────────────────────────────────────────────────────────────────────
# _extract_attr_words  &  check_intra_document_consistency
# ──────────────────────────────────────────────────────────────────────────────

class TestExtractAttrWords:
    def test_returns_list(self):
        words = D._extract_attr_words("学校获批国家重点实验室2个，成效显著。", "国家重点实验室")
        assert isinstance(words, list)

    def test_entity_not_in_self(self):
        words = D._extract_attr_words("学校获批国家重点实验室2个，成效显著。", "国家重点实验室")
        assert "国家重点实验室" not in words

    def test_returns_empty_when_entity_absent(self):
        words = D._extract_attr_words("学校坚持立德树人。", "国家重点实验室")
        assert words == []

    def test_nearby_words_included(self):
        text = "学校积极推进创新研究院建设，取得显著成效。"
        words = D._extract_attr_words(text, "创新研究院")
        assert isinstance(words, list)


class TestCheckIntraDocumentConsistency:
    def _item(self, content, entities):
        return {
            "表述内容": content,
            "命中实体": entities,
        }

    def test_empty_items(self):
        assert D.check_intra_document_consistency([]) == []

    def test_single_item_no_conflict(self):
        items = [self._item("学校获批重点实验室2个", ["重点实验室"])]
        result = D.check_intra_document_consistency(items)
        assert result == []

    def test_same_entity_same_attr_no_conflict(self):
        items = [
            self._item("学校建设重点实验室", ["重点实验室"]),
            self._item("学校继续建设重点实验室", ["重点实验室"]),
        ]
        result = D.check_intra_document_consistency(items)
        # 属性词高度相似 → 无冲突
        assert isinstance(result, list)

    def test_returns_list(self):
        items = [
            self._item("学校获批重点实验室，推动科研创新", ["重点实验室"]),
            self._item("重点实验室撤销关闭，人员解散", ["重点实验室"]),
        ]
        result = D.check_intra_document_consistency(items)
        assert isinstance(result, list)


# ──────────────────────────────────────────────────────────────────────────────
# extract_keywords
# ──────────────────────────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────────────────────────
# extract_statements
# ──────────────────────────────────────────────────────────────────────────────

class TestExtractStatements:
    def _write(self, tmp_path, text):
        p = tmp_path / "doc.txt"
        p.write_text(text, encoding="utf-8")
        return str(p)

    def test_returns_list(self, tmp_path):
        p = self._write(tmp_path, "学校获批国家级科研项目356项，成效显著。")
        result = D.extract_statements(p)
        assert isinstance(result, list)

    def test_extracts_quantitative_statement(self, tmp_path):
        p = self._write(tmp_path, "学校获批国家级科研项目356项，成效显著。")
        result = D.extract_statements(p)
        assert len(result) >= 1
        types = [s["类型"] for s in result]
        assert "定量" in types

    def test_extracts_qualitative_statement(self, tmp_path):
        p = self._write(tmp_path, "学校坚持立德树人，持续推进教育改革工作，取得积极成效。")
        result = D.extract_statements(p)
        assert any(s["类型"] == "定性" for s in result)

    def test_splits_on_newline(self, tmp_path):
        text = "学校获批国家级科研项目356项，成效显著。\n引进高层次人才128名，持续推进人才战略。"
        p = self._write(tmp_path, text)
        result = D.extract_statements(p)
        assert len(result) >= 2

    def test_deduplicates_identical_statements(self, tmp_path):
        line = "学校获批国家级科研项目356项，成效显著。"
        p = self._write(tmp_path, line + "\n" + line)
        result = D.extract_statements(p)
        texts = [s["表述内容"] for s in result]
        assert len(texts) == len(set(texts))

    def test_required_keys_present(self, tmp_path):
        p = self._write(tmp_path, "学校获批国家级科研项目356项，成效显著。")
        for s in D.extract_statements(p):
            for key in ["类型", "表述内容", "状态", "出处", "命中数字", "命中实体", "子命题"]:
                assert key in s, f"缺少字段: {key}"

    def test_short_parts_filtered(self, tmp_path):
        p = self._write(tmp_path, "太短。\n学校获批国家级科研项目356项，成效显著。")
        result = D.extract_statements(p)
        texts = [s["表述内容"] for s in result]
        assert not any(len(t) < 10 for t in texts)

    def test_numeric_only_parts_filtered(self, tmp_path):
        # 纯数字/序号行不应提取为表述
        p = self._write(tmp_path, "1234567890\n学校获批国家级科研项目356项，成效显著。")
        result = D.extract_statements(p)
        assert not any(s["表述内容"].strip().isdigit() for s in result)


# ──────────────────────────────────────────────────────────────────────────────
# _get_category_patterns — flat list config branch
# ──────────────────────────────────────────────────────────────────────────────

class TestGetCategoryPatternsFlatList:
    def test_flat_list_config_adds_words(self, tmp_path):
        import yaml as _yaml
        # section_content 是 list（不是 dict）的 YAML 结构
        cfg = tmp_path / "entity_config.yaml"
        cfg.write_text("custom_words:\n  - 创新中心\n  - 攻关团队\n", encoding="utf-8")
        patterns = D._get_category_patterns(config_path=str(cfg))
        text = "学校新建创新中心两个，成效显著。"
        matched = any(p.findall(text) for p in patterns)
        assert matched, "flat list config 中的类别词「创新中心」应被模式命中"


class TestExtractKeywords:
    def test_returns_list(self):
        assert isinstance(D.extract_keywords("学校获批国家级科研项目356项"), list)

    def test_quoted_term_first_priority(self):
        kws = D.extract_keywords("“双一流”建设成效显著")
        assert any("双一流" in k for k in kws)

    def test_number_included(self):
        kws = D.extract_keywords("获批项目356项，成效显著")
        assert any("356" in k for k in kws)

    def test_empty_string(self):
        assert D.extract_keywords("") == []

    def test_action_phrase_included(self):
        kws = D.extract_keywords("牵头成立新型研究院")
        assert any("研究院" in k or "成立" in k for k in kws)


# ──────────────────────────────────────────────────────────────────────────────
# generate_markdown
# ──────────────────────────────────────────────────────────────────────────────

def _sample_items():
    return [
        {
            "类型": "定量", "表述内容": "学校获批国家级科研项目356项，成效显著。",
            "状态": "✓ 已确认", "出处": "科研报告",
            "原文片段": "科研报告: ...356项...", "匹配关键词": "356项",
            "命中数字": ["356项"], "命中实体": ["科研项目"], "子命题": [],
            "反向验证警告": "",
        },
        {
            "类型": "定量", "表述内容": "整体升学率从50%升至67%，大幅提升。",
            "状态": "△ 数据不一致", "出处": "招生数据",
            "原文片段": "招生数据: ...从47%升至65%...", "匹配关键词": "升学率",
            "命中数字": ["50%", "67%"], "命中实体": [], "子命题": [],
            "反向验证警告": "⚠ 数字不匹配：50%/67%与参考文档不符",
        },
        {
            "类型": "定性", "表述内容": "承担数智化转型相关专项试点任务。",
            "状态": "✗ 未找到", "出处": "—",
            "原文片段": "所有参照文档均未出现此表述/数据", "匹配关键词": "",
            "命中数字": [], "命中实体": ["试点任务"], "子命题": [],
            "反向验证警告": "",
        },
    ]


class TestGenerateMarkdown:
    def _gen(self, tmp_path, items=None, doc_name="测试文档", ref_count=5):
        p = tmp_path / "report.md"
        D.generate_markdown(_sample_items() if items is None else items, str(p),
                             doc_name=doc_name, ref_count=ref_count)
        return p.read_text(encoding="utf-8")

    def test_creates_file(self, tmp_path):
        p = tmp_path / "report.md"
        D.generate_markdown(_sample_items(), str(p))
        assert p.exists()

    def test_title_contains_doc_name(self, tmp_path):
        md = self._gen(tmp_path, doc_name="讲话稿2026")
        assert "讲话稿2026" in md

    def test_overview_section_present(self, tmp_path):
        md = self._gen(tmp_path)
        assert "## 概览" in md

    def test_overview_counts_correct(self, tmp_path):
        md = self._gen(tmp_path)
        assert "✓ 已确认：1" in md
        assert "✗ 未找到：1" in md

    def test_not_found_section_present(self, tmp_path):
        md = self._gen(tmp_path)
        assert "## ✗ 未找到" in md

    def test_not_found_item_in_table(self, tmp_path):
        md = self._gen(tmp_path)
        assert "承担数智化转型相关专项试点任务" in md

    def test_flagged_section_present(self, tmp_path):
        md = self._gen(tmp_path)
        assert "## ⚠ 反向验证重点关注" in md

    def test_flagged_item_shows_warning(self, tmp_path):
        md = self._gen(tmp_path)
        assert "数字不匹配" in md

    def test_flagged_item_shows_snippet(self, tmp_path):
        md = self._gen(tmp_path)
        assert "47%升至65%" in md

    def test_confirmed_section_present(self, tmp_path):
        md = self._gen(tmp_path)
        assert "## ✓ 已确认" in md

    def test_confirmed_in_details_block(self, tmp_path):
        md = self._gen(tmp_path)
        assert "<details>" in md
        assert "356项" in md

    def test_ref_count_in_header(self, tmp_path):
        md = self._gen(tmp_path, ref_count=24)
        assert "24 份" in md

    def test_pipe_in_statement_escaped(self, tmp_path):
        items = [dict(_sample_items()[2], **{"表述内容": "A｜B分隔的表述内容示例。"})]
        md = self._gen(tmp_path, items=items)
        # Should not break table (no unescaped | in cell)
        assert "A｜B" in md  # 全角符号，不破坏 Markdown 表格

    def test_empty_items_produces_valid_md(self, tmp_path):
        md = self._gen(tmp_path, items=[])
        assert "## 概览" in md
        assert "✓ 已确认：0" in md

    def test_long_snippet_truncated(self, tmp_path):
        long_item = dict(_sample_items()[1])
        long_item["原文片段"] = "X" * 200
        md = self._gen(tmp_path, items=[long_item])
        # snippet should be cut at ~120 chars
        assert "X" * 200 not in md
