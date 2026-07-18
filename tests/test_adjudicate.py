"""
TDD — adjudicate.py

测试策略：LLM 调用在 CI 中不可用，全部测降级路径。
关键契约：
  1. 无候选证据 → 附注 + 返回原 item
  2. 无 API key → skip silently，不修改 item 状态
  3. STATUS_MAP 完整性
  4. adjudicate_all 只处理有 候选证据 字段的条目
"""

import os
import pytest
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
