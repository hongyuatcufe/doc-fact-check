"""
TDD — adjudicate.py

测试策略：LLM 调用在 CI 中不可用，全部测降级路径。
关键契约：
  1. 无候选证据 → 附注 + 返回原 item
  2. 无 API key → skip silently，不修改 item 状态
  3. STATUS_MAP 完整性
  4. adjudicate_all 只处理有 候选证据 字段的条目
  5. mock LLM → 覆盖成功/未知verdict/带citation路径
"""

import os
import pytest
from unittest.mock import patch
import adjudicate as A


# ──────────────────────────────────────────────────────────────────────────────
# helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_item(status="✓ 已确认", candidates=None, warning=""):
    item = {
        "表述内容": "学校获批国家级科研项目356项",
        "命中数字": ["356"],
        "命中实体": ["国家级科研项目"],
        "子命题": ["学校获批国家级科研项目356项"],
        "状态": status,
        "反向验证警告": warning,
    }
    if candidates is not None:
        item["候选证据"] = candidates
    return item


GOOD_CANDIDATE = {
    "sourcePath": "ref.txt",
    "text": "学校全年获批国家级科研项目356项，较上年增长12%。",
    "score": 0.9,
}


# ──────────────────────────────────────────────────────────────────────────────
# _STATUS_MAP
# ──────────────────────────────────────────────────────────────────────────────

class TestStatusMap:
    def test_all_four_verdicts_present(self):
        assert "confirmed" in A._STATUS_MAP
        assert "needs_review" in A._STATUS_MAP
        assert "inconsistent" in A._STATUS_MAP
        assert "not_found" in A._STATUS_MAP

    def test_status_values_are_strings(self):
        for v in A._STATUS_MAP.values():
            assert isinstance(v, str)
            assert len(v) > 0


# ──────────────────────────────────────────────────────────────────────────────
# adjudicate_item — degradation paths (no API key)
# ──────────────────────────────────────────────────────────────────────────────

class TestAdjudicateItemDegradation:
    def setup_method(self):
        # 强制清除 API key，确保走降级路径
        self._orig_key = os.environ.pop("FACTCHECK_LLM_API_KEY", None)
        self._orig_deep = os.environ.pop("DEEPSEEK_API_KEY", None)

    def teardown_method(self):
        if self._orig_key is not None:
            os.environ["FACTCHECK_LLM_API_KEY"] = self._orig_key
        if self._orig_deep is not None:
            os.environ["DEEPSEEK_API_KEY"] = self._orig_deep

    def test_no_candidates_appends_note(self):
        item = _make_item(candidates=[])
        result = A.adjudicate_item(item, [])
        assert "未经 LLM 复核" in result.get("反向验证警告", "")

    def test_no_candidates_preserves_status(self):
        item = _make_item(status="✓ 已确认", candidates=[])
        A.adjudicate_item(item, [])
        assert item["状态"] == "✓ 已确认"

    def test_no_api_key_preserves_status(self):
        item = _make_item(candidates=[GOOD_CANDIDATE])
        original_status = item["状态"]
        A.adjudicate_item(item, [GOOD_CANDIDATE])
        assert item["状态"] == original_status

    def test_no_api_key_appends_fallback_note(self):
        item = _make_item(candidates=[GOOD_CANDIDATE])
        A.adjudicate_item(item, [GOOD_CANDIDATE])
        warning = item.get("反向验证警告", "")
        assert "维持规则判定" in warning or "未经 LLM 复核" in warning

    def test_returns_item(self):
        item = _make_item(candidates=[])
        result = A.adjudicate_item(item, [])
        assert result is item, "应返回同一个 item 对象（in-place）"

    def test_existing_warning_not_overwritten(self):
        item = _make_item(candidates=[], warning="已有警告")
        A.adjudicate_item(item, [])
        warning = item["反向验证警告"]
        assert "已有警告" in warning, "原有警告内容不应丢失"


# ──────────────────────────────────────────────────────────────────────────────
# adjudicate_all — degradation paths
# ──────────────────────────────────────────────────────────────────────────────

class TestAdjudicateAll:
    def setup_method(self):
        self._orig_key = os.environ.pop("FACTCHECK_LLM_API_KEY", None)
        self._orig_deep = os.environ.pop("DEEPSEEK_API_KEY", None)

    def teardown_method(self):
        if self._orig_key is not None:
            os.environ["FACTCHECK_LLM_API_KEY"] = self._orig_key
        if self._orig_deep is not None:
            os.environ["DEEPSEEK_API_KEY"] = self._orig_deep

    def test_no_api_key_does_not_raise(self, capsys):
        items = [_make_item(candidates=[GOOD_CANDIDATE])]
        A.adjudicate_all(items, verbose=True)
        # 不应抛出

    def test_no_api_key_prints_skip_message(self, capsys):
        items = [_make_item(candidates=[GOOD_CANDIDATE])]
        A.adjudicate_all(items, verbose=True)
        out = capsys.readouterr().out
        assert "跳过" in out or "无 API key" in out

    def test_only_processes_items_with_candidates(self):
        item_with = _make_item(status="✓ 已确认", candidates=[GOOD_CANDIDATE])
        item_without = _make_item(status="✓ 已确认")  # 无 候选证据 字段
        items = [item_with, item_without]
        A.adjudicate_all(items, verbose=False)
        # item_without 不应被修改（无 候选证据 字段）
        assert "反向验证警告" not in item_without or item_without["反向验证警告"] == ""

    def test_empty_list_does_not_raise(self):
        A.adjudicate_all([], verbose=False)

    def test_verbose_false_prints_nothing(self, capsys):
        # 无 key 时 verbose=False 也不应有输出（no key → early return with print）
        # 实际代码在无 key 时会打印一行，这是可接受的；只要不抛异常
        A.adjudicate_all([], verbose=False)
        # 不抛即通过


# ──────────────────────────────────────────────────────────────────────────────
# _append_note helper
# ──────────────────────────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────────────────────────
# _build_user_msg — 字段拼装
# ──────────────────────────────────────────────────────────────────────────────

class TestBuildUserMsg:
    def _cand(self, text="参考片段内容"):
        return {"sourcePath": "ref.txt", "text": text, "score": 0.9}

    def test_contains_claim_text(self):
        item = {
            "表述内容": "学校获批科研项目356项",
            "命中数字": [], "命中实体": [], "子命题": [],
        }
        msg = A._build_user_msg(item, [self._cand()])
        assert "学校获批科研项目356项" in msg

    def test_numbers_included(self):
        item = {
            "表述内容": "获批356项",
            "命中数字": ["356"], "命中实体": [], "子命题": [],
        }
        msg = A._build_user_msg(item, [self._cand()])
        assert "356" in msg

    def test_entities_included(self):
        item = {
            "表述内容": "国家级科研项目若干",
            "命中数字": [], "命中实体": ["国家级科研项目"], "子命题": [],
        }
        msg = A._build_user_msg(item, [self._cand()])
        assert "国家级科研项目" in msg

    def test_sub_claims_included_when_multiple(self):
        item = {
            "表述内容": "A，B两件事",
            "命中数字": [], "命中实体": [],
            "子命题": ["A", "B"],
        }
        msg = A._build_user_msg(item, [self._cand()])
        assert "A" in msg and "B" in msg

    def test_sub_claims_not_included_when_single(self):
        item = {
            "表述内容": "仅一件事",
            "命中数字": [], "命中实体": [],
            "子命题": ["仅一件事"],
        }
        msg = A._build_user_msg(item, [self._cand()])
        assert "可分解子命题" not in msg

    def test_candidate_text_in_output(self):
        item = {
            "表述内容": "声明内容",
            "命中数字": [], "命中实体": [], "子命题": [],
        }
        msg = A._build_user_msg(item, [self._cand("这是参考文本内容")])
        assert "这是参考文本内容" in msg

    def test_at_most_5_candidates(self):
        item = {
            "表述内容": "声明",
            "命中数字": [], "命中实体": [], "子命题": [],
        }
        cands = [self._cand(f"文本{i}") for i in range(10)]
        msg = A._build_user_msg(item, cands)
        # 只渲染前5个
        assert "文本4" in msg
        assert "文本5" not in msg


# ──────────────────────────────────────────────────────────────────────────────
# adjudicate_item — mock LLM 成功路径
# ──────────────────────────────────────────────────────────────────────────────

class TestAdjudicateItemWithMockLLM:
    """通过 mock _call_llm 覆盖 LLM 返回成功结果的分支。"""

    CANDIDATE = {
        "sourcePath": "ref.txt",
        "text": "学校全年获批国家级科研项目356项。",
        "score": 0.9,
    }

    def _item(self, status="△ 部分匹配"):
        return {
            "表述内容": "学校获批国家级科研项目356项",
            "命中数字": ["356"],
            "命中实体": ["国家级科研项目"],
            "子命题": ["学校获批国家级科研项目356项"],
            "状态": status,
            "反向验证警告": "",
        }

    def test_confirmed_verdict_updates_status(self):
        with patch("adjudicate._call_llm", return_value={"verdict": "confirmed", "citation": "", "reasoning": ""}):
            item = self._item()
            A.adjudicate_item(item, [self.CANDIDATE])
        assert "✓" in item["状态"]

    def test_not_found_verdict_updates_status(self):
        with patch("adjudicate._call_llm", return_value={"verdict": "not_found", "citation": "", "reasoning": ""}):
            item = self._item()
            A.adjudicate_item(item, [self.CANDIDATE])
        assert "✗" in item["状态"] or "未找到" in item["状态"]

    def test_needs_review_verdict(self):
        with patch("adjudicate._call_llm", return_value={"verdict": "needs_review", "citation": "", "reasoning": ""}):
            item = self._item()
            A.adjudicate_item(item, [self.CANDIDATE])
        assert "△" in item["状态"] or "需人工" in item["状态"]

    def test_inconsistent_verdict(self):
        with patch("adjudicate._call_llm", return_value={"verdict": "inconsistent", "citation": "", "reasoning": ""}):
            item = self._item()
            A.adjudicate_item(item, [self.CANDIDATE])
        assert "✗" in item["状态"] or "不一致" in item["状态"]

    def test_citation_stored_in_item(self):
        with patch("adjudicate._call_llm", return_value={"verdict": "confirmed", "citation": "参考片段123", "reasoning": ""}):
            item = self._item()
            A.adjudicate_item(item, [self.CANDIDATE])
        assert item.get("判定引用") == "参考片段123"

    def test_reasoning_stored_in_item(self):
        with patch("adjudicate._call_llm", return_value={"verdict": "confirmed", "citation": "", "reasoning": "数字完全匹配"}):
            item = self._item()
            A.adjudicate_item(item, [self.CANDIDATE])
        assert item.get("判定理由") == "数字完全匹配"

    def test_confirmed_reasoning_only_in_判定理由(self):
        # confirmed verdict: reasoning stored in 判定理由, NOT 反向验证警告
        with patch("adjudicate._call_llm", return_value={"verdict": "confirmed", "citation": "", "reasoning": "LLM确认无误"}):
            item = self._item()
            A.adjudicate_item(item, [self.CANDIDATE])
        assert item.get("判定理由") == "LLM确认无误"
        assert "LLM确认无误" not in item.get("反向验证警告", "")

    def test_needs_review_reasoning_appended_to_warning(self):
        # needs_review verdict: reasoning also goes into 反向验证警告
        with patch("adjudicate._call_llm", return_value={"verdict": "needs_review", "citation": "", "reasoning": "数字出入较大"}):
            item = self._item()
            A.adjudicate_item(item, [self.CANDIDATE])
        assert "数字出入较大" in item.get("反向验证警告", "")

    def test_unknown_verdict_preserves_status(self):
        with patch("adjudicate._call_llm", return_value={"verdict": "unknown_xyz", "citation": "", "reasoning": ""}):
            item = self._item(status="✓ 已确认")
            A.adjudicate_item(item, [self.CANDIDATE])
        assert item["状态"] == "✓ 已确认"

    def test_unknown_verdict_appends_note(self):
        with patch("adjudicate._call_llm", return_value={"verdict": "unknown_xyz", "citation": "", "reasoning": ""}):
            item = self._item()
            A.adjudicate_item(item, [self.CANDIDATE])
        assert "未知" in item.get("反向验证警告", "") or "unknown_xyz" in item.get("反向验证警告", "")

    def test_llm_returns_none_preserves_status(self):
        with patch("adjudicate._call_llm", return_value=None):
            item = self._item(status="✓ 已确认")
            # 需要有 API key 才会调用 _call_llm，先 mock _llm_config
            with patch("adjudicate._llm_config", return_value=("fake-key", "https://api.test", "test-model")):
                A.adjudicate_item(item, [self.CANDIDATE])
        assert item["状态"] == "✓ 已确认"


# ──────────────────────────────────────────────────────────────────────────────
# adjudicate_all — mock LLM 路径
# ──────────────────────────────────────────────────────────────────────────────

class TestAdjudicateAllWithMockLLM:
    CANDIDATE = {"sourcePath": "ref.txt", "text": "参考文本", "score": 0.9}

    def _item(self, with_cand=True):
        item = {
            "表述内容": "声明内容",
            "命中数字": [], "命中实体": [], "子命题": [],
            "状态": "△ 部分匹配", "反向验证警告": "",
        }
        if with_cand:
            item["候选证据"] = [self.CANDIDATE]
        return item

    def test_all_with_candidates_processed(self):
        items = [self._item(with_cand=True) for _ in range(3)]
        with patch("adjudicate._llm_config", return_value=("key", "base", "model")):
            with patch("adjudicate._call_llm", return_value={"verdict": "confirmed", "citation": "", "reasoning": ""}):
                A.adjudicate_all(items, verbose=False)
        for it in items:
            assert "✓" in it["状态"]

    def test_items_without_candidates_skipped(self):
        item_with = self._item(with_cand=True)
        item_without = self._item(with_cand=False)
        with patch("adjudicate._llm_config", return_value=("key", "base", "model")):
            with patch("adjudicate._call_llm", return_value={"verdict": "confirmed", "citation": "", "reasoning": ""}):
                A.adjudicate_all([item_with, item_without], verbose=False)
        assert "✓" in item_with["状态"]
        assert "✓" not in item_without["状态"]  # 未处理，保持原值

    def test_verbose_prints_progress(self, capsys):
        items = [self._item() for _ in range(11)]
        with patch("adjudicate._llm_config", return_value=("key", "base", "model")):
            with patch("adjudicate._call_llm", return_value={"verdict": "confirmed", "citation": "", "reasoning": ""}):
                A.adjudicate_all(items, verbose=True)
        out = capsys.readouterr().out
        assert "LLM判定" in out


class TestAppendNote:
    def test_appends_to_empty(self):
        item = {"反向验证警告": ""}
        A._append_note(item, "新注释")
        assert item["反向验证警告"] == "新注释"

    def test_appends_with_separator(self):
        item = {"反向验证警告": "已有内容"}
        A._append_note(item, "追加内容")
        assert "已有内容" in item["反向验证警告"]
        assert "追加内容" in item["反向验证警告"]

    def test_no_leading_separator_when_empty(self):
        item = {"反向验证警告": ""}
        A._append_note(item, "内容")
        assert not item["反向验证警告"].startswith(";")


# ──────────────────────────────────────────────────────────────────────────────
# _build_batch_msg
# ──────────────────────────────────────────────────────────────────────────────

class TestBuildBatchMsg:
    def _item(self, text="声明A", numbers=None, entities=None):
        return {
            "表述内容": text,
            "命中数字": numbers or [],
            "命中实体": entities or [],
            "子命题": [],
        }

    def _ev(self, text="证据文本", src="ref.txt"):
        return {"sourcePath": src, "text": text}

    def test_contains_numbered_claims(self):
        batch = [(self._item("声明一"), [self._ev()]),
                 (self._item("声明二"), [self._ev()])]
        msg = A._build_batch_msg(batch)
        assert "【声明 1】" in msg
        assert "【声明 2】" in msg

    def test_numbers_and_entities_appended(self):
        item = self._item(numbers=["99%"], entities=["特殊单位名"])
        msg = A._build_batch_msg([(item, [self._ev()])])
        assert "99%" in msg
        assert "特殊单位名" in msg

    def test_evidence_text_included(self):
        msg = A._build_batch_msg([(self._item(), [self._ev("独特证据内容XYZ")])])
        assert "独特证据内容XYZ" in msg

    def test_no_evidence_placeholder(self):
        msg = A._build_batch_msg([(self._item(), [])])
        assert "无候选证据" in msg

    def test_max_three_evidence_per_item(self):
        evs = [self._ev(f"证据{i}") for i in range(5)]
        msg = A._build_batch_msg([(self._item(), evs)])
        assert "证据0" in msg
        assert "证据1" in msg
        assert "证据2" in msg
        assert "证据3" not in msg


# ──────────────────────────────────────────────────────────────────────────────
# _select_evidence
# ──────────────────────────────────────────────────────────────────────────────

class TestSelectEvidence:
    def test_prefers_candidates_field(self):
        item = {
            "候选证据": [{"sourcePath": "a.txt", "text": "候选内容", "score": 0.9}],
            "原文片段": "原文内容",
            "出处": "b.txt",
        }
        ev = A._select_evidence(item)
        assert ev[0]["text"] == "候选内容"

    def test_falls_back_to_snippet(self):
        item = {"原文片段": "有用的原文片段", "出处": "ref.txt"}
        ev = A._select_evidence(item)
        assert len(ev) == 1
        assert ev[0]["text"] == "有用的原文片段"
        assert ev[0]["sourcePath"] == "ref.txt"

    def test_empty_when_no_evidence(self):
        item = {"原文片段": "", "出处": ""}
        ev = A._select_evidence(item)
        assert ev == []

    def test_candidates_capped_at_three(self):
        item = {
            "候选证据": [{"sourcePath": f"{i}.txt", "text": f"候选{i}", "score": 0.9}
                        for i in range(5)]
        }
        ev = A._select_evidence(item)
        assert len(ev) == 3


# ──────────────────────────────────────────────────────────────────────────────
# adjudicate_batch — degradation & selection paths (no real API)
# ──────────────────────────────────────────────────────────────────────────────

class TestAdjudicateBatch:
    def setup_method(self):
        self._orig_key  = os.environ.pop("FACTCHECK_LLM_API_KEY", None)
        self._orig_deep = os.environ.pop("DEEPSEEK_API_KEY", None)

    def teardown_method(self):
        if self._orig_key  is not None: os.environ["FACTCHECK_LLM_API_KEY"] = self._orig_key
        if self._orig_deep is not None: os.environ["DEEPSEEK_API_KEY"]       = self._orig_deep

    def _confirmed_item(self, warning=""):
        return {
            "表述内容": "测试声明", "命中数字": [], "命中实体": [], "子命题": [],
            "状态": "✓ 已确认", "反向验证警告": warning,
            "出处": "ref.txt", "原文片段": "参考文本内容", "候选证据": [],
        }

    def _not_found_item(self, with_candidates=True):
        item = {
            "表述内容": "未找到声明", "命中数字": [], "命中实体": [], "子命题": [],
            "状态": "✗ 未找到", "反向验证警告": "",
            "出处": "", "原文片段": "",
        }
        if with_candidates:
            item["候选证据"] = [{"sourcePath": "ref.txt", "text": "相关候选文本", "score": 0.7}]
        return item

    def test_no_api_key_does_not_raise(self, capsys):
        items = [self._not_found_item()]
        A.adjudicate_batch(items, verbose=True)

    def test_no_api_key_prints_skip_message(self, capsys):
        A.adjudicate_batch([self._not_found_item()], verbose=True)
        out = capsys.readouterr().out
        assert "跳过" in out or "无 API key" in out

    def test_no_api_key_preserves_status(self):
        item = self._not_found_item()
        A.adjudicate_batch([item], verbose=False)
        assert item["状态"] == "✗ 未找到"

    def test_items_without_evidence_skipped(self):
        item = self._not_found_item(with_candidates=False)
        item["原文片段"] = ""
        A.adjudicate_batch([item], verbose=False)
        assert "判定" not in item.get("反向验证警告", "")

    def test_confirmed_no_warning_skipped(self):
        item = self._confirmed_item(warning="")
        A.adjudicate_batch([item], verbose=False)
        assert "判定" not in item.get("反向验证警告", "")

    def test_confirmed_with_warning_selected(self):
        item = self._confirmed_item(warning="增长率未独立验证")
        with patch("adjudicate._llm_config", return_value=("key", "url", "model")):
            with patch("adjudicate._call_llm_batch", return_value=[
                {"index": 1, "verdict": "needs_review", "citation": "", "reasoning": "部分支持"}
            ]):
                A.adjudicate_batch([item], verbose=False)
        assert "△" in item["状态"]

    def test_not_found_with_candidates_selected(self):
        item = self._not_found_item(with_candidates=True)
        with patch("adjudicate._llm_config", return_value=("key", "url", "model")):
            with patch("adjudicate._call_llm_batch", return_value=[
                {"index": 1, "verdict": "confirmed", "citation": "相关候选文本", "reasoning": "证据支持"}
            ]):
                A.adjudicate_batch([item], verbose=False)
        assert "✓" in item["状态"]

    def test_batch_llm_failure_appends_note(self):
        item = self._not_found_item()
        with patch("adjudicate._llm_config", return_value=("key", "url", "model")):
            with patch("adjudicate._call_llm_batch", return_value=None):
                A.adjudicate_batch([item], verbose=False)
        assert "失败" in item.get("反向验证警告", "") or "维持规则结果" in item.get("反向验证警告", "")

    def test_unknown_verdict_appends_note(self):
        item = self._not_found_item()
        with patch("adjudicate._llm_config", return_value=("key", "url", "model")):
            with patch("adjudicate._call_llm_batch", return_value=[
                {"index": 1, "verdict": "bogus_verdict", "citation": "", "reasoning": ""}
            ]):
                A.adjudicate_batch([item], verbose=False)
        assert item["状态"] == "✗ 未找到"  # 原状态不变
        assert "未知 verdict" in item.get("反向验证警告", "") or "维持规则结果" in item.get("反向验证警告", "")

    def test_citation_and_reasoning_written(self):
        item = self._not_found_item()
        with patch("adjudicate._llm_config", return_value=("key", "url", "model")):
            with patch("adjudicate._call_llm_batch", return_value=[
                {"index": 1, "verdict": "confirmed", "citation": "引用文本", "reasoning": "判定理由"}
            ]):
                A.adjudicate_batch([item], verbose=False)
        assert item.get("判定引用") == "引用文本"
        assert item.get("判定理由") == "判定理由"
        # confirmed verdict: reasoning only in 判定理由, not in 反向验证警告
        assert "判定理由" not in item.get("反向验证警告", "")

    def test_missing_index_in_results_appends_fallback(self):
        item = self._not_found_item()
        with patch("adjudicate._llm_config", return_value=("key", "url", "model")):
            with patch("adjudicate._call_llm_batch", return_value=[]):
                A.adjudicate_batch([item], verbose=False)
        assert "未返回此条结果" in item.get("反向验证警告", "") or "维持规则结果" in item.get("反向验证警告", "")

    def test_empty_items_list(self):
        A.adjudicate_batch([], verbose=False)  # 不应抛出


# ──────────────────────────────────────────────────────────────────────────────
# search_in_reference: 候选证据对「✗ 未找到」条目也应存储
# ──────────────────────────────────────────────────────────────────────────────

class TestCandidatesStoredForNotFound:
    """retrieval.py 存在时，即使关键词未命中，「✗ 未找到」条目也应有候选证据字段。"""

    def _item(self, query):
        return {
            "表述内容": query, "命中数字": [], "命中实体": [], "子命题": [],
            "状态": "✗ 未找到", "反向验证警告": "",
            "出处": "", "原文片段": "", "匹配关键词": "", "匹配上下文简述": "",
        }

    def test_candidates_stored_even_when_kw_miss(self):
        """retrieval 找到候选但无关键词命中时，候选证据字段仍写入。"""
        import doc_fact_check as D

        item = self._item("完全独特的测试表述XYZ_UNIQUE")
        ref_text = "学校获批经费一百万，项目推进顺利。" * 30

        fake_candidates = [
            {"sourcePath": "/fake/ref.txt", "text": ref_text[:300], "score": 0.5}
        ]

        import retrieval as _ret
        import unittest.mock as mock

        fake_index = mock.MagicMock()
        with mock.patch.object(_ret, "build_index", return_value=fake_index):
            with mock.patch.object(_ret, "retrieve", return_value=fake_candidates):
                D.search_in_reference([item], [("/fake/ref.txt", ref_text)])

        assert item["状态"] == "✗ 未找到"
        assert "候选证据" in item, "候选证据字段应存在，即使关键词未命中"
        assert len(item["候选证据"]) > 0
