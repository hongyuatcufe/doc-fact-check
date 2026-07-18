"""
TDD — doc_fact_check.py 判定规则

测试策略：通过 search_in_reference() 直接验证关键状态降级规则。
不测 Excel 生成、CLI 解析等 I/O 逻辑，只测核心判定契约。
"""

import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import doc_fact_check as D


# ──────────────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────────────

def _ref(name: str, text: str):
    """(txt_path, txt_content) — references 格式（与 build_ref_index 一致）"""
    return (f"/fake/{name}", text)


def _run(query: str, numbers: list, entities: list, ref_text: str,
         sub_claims=None) -> dict:
    """最小化跑 search_in_reference，返回第一条的结果 item。"""
    item = {
        "表述内容": query,
        "命中数字": numbers,
        "命中实体": entities,
        "子命题": sub_claims or [],
        "状态": "待核实",
        "反向验证警告": "",
        "出处": "",
        "匹配上下文简述": "",
        "原文片段": "",
        "匹配关键词": "",
    }
    references = [_ref("ref.txt", ref_text)]
    D.search_in_reference([item], references)
    return item


# ──────────────────────────────────────────────────────────────────────────────
# Rule ④ smoke test — 长关键词 + 数字都匹配 → 保持 confirmed（回归保护）
# ──────────────────────────────────────────────────────────────────────────────

class TestRule4Smoke:
    def test_long_kw_with_number_stays_confirmed(self):
        ref = "学校获批国家级科研项目356项，成绩显著。"
        item = _run(
            query="获批国家级科研项目356项",
            numbers=["356"],
            entities=["国家级科研项目"],
            ref_text=ref,
        )
        assert "✓" in item["状态"], \
            f"长关键词+数字匹配，应为 confirmed，实际: {item['状态']}"


# ──────────────────────────────────────────────────────────────────────────────
# Rule ⑤ — 短关键词 + 所有数字缺失 → "✗ 未找到"（防 △部分匹配 假阳性）
# 这是针对 case #14 "722篇" false-positive trap 的精确规则
# ──────────────────────────────────────────────────────────────────────────────

class TestRule5ShortKwAllNumbersMissing:
    def test_short_kw_all_numbers_missing_returns_not_found(self):
        """
        引号内 "十四五"(3字) 命中，长关键词均不在参考文档，
        且所有声明数字 722篇/90篇 均不在参考文档 → "✗ 未找到"
        （Rule ⑤ 防止 △ 部分匹配 假阳性，对应 eval case #14）
        """
        ref = "十四五期间，学校在教学科研方面取得了重要进展。"
        item = _run(
            query='"十四五"期间，研究报告获采纳批示内参刊发722篇，其中90篇获中央领导批示',
            numbers=["722篇", "90篇"],
            entities=["研究报告"],
            ref_text=ref,
            sub_claims=[
                {"text": "研究报告获采纳批示内参刊发722篇",
                 "numbers": ["722篇"], "entities": ["研究报告"], "independent": True},
                {"text": "其中90篇获中央领导批示",
                 "numbers": ["90篇"], "entities": [], "independent": True},
            ],
        )
        assert "✗" in item["状态"] or "未找到" in item["状态"], \
            f"短关键词+所有数字缺失，应为 '✗ 未找到'，实际: {item['状态']}"

    def test_short_kw_some_numbers_present_stays_partial(self):
        """
        短关键词命中，且至少一个数字（90篇）存在于参考文档 → 不降级到未找到
        """
        ref = '"十四五"期间，研究报告内参刊发90篇，内容丰富。'
        item = _run(
            query='"十四五"期间，研究报告获采纳批示内参刊发722篇，其中90篇获中央领导批示',
            numbers=["722篇", "90篇"],
            entities=["研究报告"],
            ref_text=ref,
            sub_claims=[
                {"text": "研究报告获采纳批示内参刊发722篇",
                 "numbers": ["722篇"], "entities": ["研究报告"], "independent": True},
                {"text": "其中90篇获中央领导批示",
                 "numbers": ["90篇"], "entities": [], "independent": True},
            ],
        )
        # "90篇" 在文档中 → 不应降级到未找到
        assert "✗" not in item["状态"], \
            f"部分数字存在时不应降为未找到，实际: {item['状态']}"

    def test_no_numbers_in_claim_does_not_trigger_rule5(self):
        """
        声明本身不含数字 → Rule ⑤ 不触发，不应误降级
        """
        ref = "十四五期间，学校持续推进教育教学改革。"
        item = _run(
            query="十四五期间，学校取得了重要进展",
            numbers=[],
            entities=[],
            ref_text=ref,
        )
        assert "✗" not in item["状态"], \
            f"无数字声明不应触发 Rule ⑤，实际: {item['状态']}"

    def test_long_kw_all_numbers_missing_stays_partial_not_not_found(self):
        """
        长关键词（>3字）命中但数字缺失 → 维持 △ 部分匹配（不触发 Rule ⑤）
        Rule ⑤ 仅针对低区分度的短关键词，避免过度降级
        """
        ref = "学校获批国家级科研项目，数量可观，成效显著。"
        item = _run(
            query="学校获批国家级科研项目356项，其中重点项目44项",
            numbers=["356项", "44项"],
            entities=["国家级科研项目"],
            ref_text=ref,
        )
        # 长关键词（>3字） → 不应由 Rule ⑤ 降为未找到
        assert "✗" not in item["状态"], \
            f"长关键词匹配不应触发 Rule ⑤，实际: {item['状态']}"
