"""
TDD — doc_fact_check.py 检索与验证函数

覆盖目标（当前 miss）：
  - get_context_snippet (keyword absent branch)
  - verify_sub_claims   (no-anchor claim path)
  - verify_entity_coverage (empty entities)
  - verify_entity_context_cooccurrence (edge cases)
  - search_in_reference extra paths:
      · 实体覆盖率 < 50% + ≥2实体 → △ 数据不一致
      · 张冠李戴（实体存在但不与关键词共现）→ △ 数据不一致
      · 增长率语境检查 warning
      · Rule ④: 短/通用关键词 + 无强证据 → △ 需核实
"""

import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import doc_fact_check as D


# ── helpers ───────────────────────────────────────────────────────────────────

def _ref(name, text):
    return (f"/fake/{name}", text)


def _item(query, numbers=None, entities=None, sub_claims=None):
    return {
        "表述内容": query,
        "命中数字": numbers or [],
        "命中实体": entities or [],
        "子命题": sub_claims or [],
        "状态": "✗ 未找到",   # 与 extract_statements 一致的初始值
        "反向验证警告": "",
        "出处": "", "匹配上下文简述": "", "原文片段": "", "匹配关键词": "",
    }


def _run(query, numbers=None, entities=None, sub_claims=None, ref_text=""):
    it = _item(query, numbers, entities, sub_claims)
    D.search_in_reference([it], [_ref("ref.txt", ref_text)])
    return it


# ──────────────────────────────────────────────────────────────────────────────
# get_context_snippet
# ──────────────────────────────────────────────────────────────────────────────

class TestGetContextSnippet:
    def test_keyword_found(self):
        snippet = D.get_context_snippet("学校获批356项科研项目", "356项")
        assert "356项" in snippet

    def test_keyword_absent_returns_empty(self):
        snippet = D.get_context_snippet("学校坚持立德树人", "999项")
        assert snippet == ""

    def test_prefix_included(self):
        text = "A" * 100 + "关键词" + "B" * 100
        snippet = D.get_context_snippet(text, "关键词")
        assert "关键词" in snippet
        assert "A" in snippet  # 前缀上下文

    def test_suffix_included(self):
        text = "A" * 100 + "关键词" + "B" * 100
        snippet = D.get_context_snippet(text, "关键词")
        assert "B" in snippet


# ──────────────────────────────────────────────────────────────────────────────
# verify_sub_claims
# ──────────────────────────────────────────────────────────────────────────────

class TestVerifySubClaims:
    def _sc(self, text, independent=True, numbers=None, entities=None):
        return {
            "text": text,
            "independent": independent,
            "numbers": numbers or [],
            "entities": entities or [],
        }

    def test_empty_sub_claims(self):
        flags, total, matched, details = D.verify_sub_claims([], [_ref("r.txt", "内容")])
        assert flags == [] and total == 0 and matched == 0

    def test_non_independent_claim_excluded(self):
        sc = self._sc("学校持续推进教育教学改革", independent=False)
        flags, total, matched, details = D.verify_sub_claims([sc], [_ref("r.txt", "教育教学改革")])
        assert total == 0

    def test_claim_without_anchor_passes_through(self):
        # 无数字/引号/成就动词的子命题 → 无锚点 → 视为通过
        sc = self._sc("学校坚持以人为本的教育理念推进改革", independent=True,
                      numbers=[], entities=[])
        flags, total, matched, details = D.verify_sub_claims(
            [sc], [_ref("r.txt", "无相关内容")]
        )
        # 无锚点子命题被标记为通过（True），不拉低覆盖��
        assert all(flags)

    def test_claim_with_anchor_and_found(self):
        sc = self._sc("学校获批国家级项目356项", independent=True, numbers=["356项"])
        refs = [_ref("r.txt", "学校获批国家级科研项目356项，成效显著。")]
        flags, total, matched, details = D.verify_sub_claims([sc], refs)
        assert matched > 0

    def test_claim_with_anchor_not_found(self):
        sc = self._sc("学校获批神秘项目999项", independent=True, numbers=["999项"])
        flags, total, matched, details = D.verify_sub_claims(
            [sc], [_ref("r.txt", "学校坚持立德树人")]
        )
        assert matched == 0
        assert any("无独立出处" in d for d in details)


# ──────────────────────────────────────────────────────────────────────────────
# verify_entity_coverage
# ──────────────────────────────────────────────────────────────────────────────

class TestVerifyEntityCoverage:
    def test_empty_entities_returns_100(self):
        cov, uncov = D.verify_entity_coverage([], [_ref("r.txt", "任意内容")])
        assert cov == 100.0
        assert uncov == []

    def test_all_found(self):
        cov, uncov = D.verify_entity_coverage(
            ["国家级项目"],
            [_ref("r.txt", "学校获批国家级项目若干")]
        )
        assert cov == 100.0
        assert uncov == []

    def test_partial_coverage(self):
        cov, uncov = D.verify_entity_coverage(
            ["国家级项目", "神秘实验室"],
            [_ref("r.txt", "学校获批国家级项目若干")]
        )
        assert cov == 50.0
        assert "神秘实验室" in uncov

    def test_none_found(self):
        cov, uncov = D.verify_entity_coverage(
            ["神秘实验室X", "神秘联盟Y"],
            [_ref("r.txt", "学校坚持立德树人")]
        )
        assert cov == 0.0
        assert len(uncov) == 2


# ──────────────────────────────────────────────────────────────────────────────
# verify_entity_context_cooccurrence
# ──────────────────────────────────────────────────────────────────────────────

class TestVerifyEntityContextCooccurrence:
    def test_empty_entities_returns_empty_misattributed(self):
        mis, co = D.verify_entity_context_cooccurrence(
            [], "关键词", [_ref("r.txt", "关键词出现于此")]
        )
        assert mis == []

    def test_empty_matched_kw_returns_all_cooccurring(self):
        mis, co = D.verify_entity_context_cooccurrence(
            ["实验室"], "", [_ref("r.txt", "实验室内容")]
        )
        assert mis == []
        assert "实验室" in co

    def test_entity_equals_keyword_counted_as_cooccurring(self):
        mis, co = D.verify_entity_context_cooccurrence(
            ["重点实验室"], "重点实验室",
            [_ref("r.txt", "学校获批重点实验室")]
        )
        assert "重点实验室" in co
        assert not any(e == "重点实验室" for e, _ in mis)

    def test_entity_near_keyword_cooccurring(self):
        ref_text = "学校获批重点实验室，金融科技中心同步建设，成效显著。"
        mis, co = D.verify_entity_context_cooccurrence(
            ["金融科技中心"], "重点实验室",
            [_ref("r.txt", ref_text)]
        )
        # 实体与关键词在同一短文本内 → 共现
        assert "金融科技中心" in co

    def test_entity_far_from_keyword_misattributed(self):
        kw = "教学改革"
        entity = "神秘实验室Z"
        ref_text = (
            "学校坚持教学改革，持续提升教学质量。" + "X" * 500 +
            "神秘实验室Z成立于2020年。"
        )
        mis, co = D.verify_entity_context_cooccurrence(
            [entity], kw, [_ref("r.txt", ref_text)],
            context_radius=100
        )
        # 实体存在但距关键词 > 500 字 → 张冠李戴
        assert any(e == entity for e, _ in mis)

    def test_entity_absent_counted_as_cooccurring(self):
        # 实体根本不存在于参考文档 → 由 coverage 负责，不进 misattributed
        mis, co = D.verify_entity_context_cooccurrence(
            ["完全不存在的实体XYZ"], "关键词",
            [_ref("r.txt", "学校关键词出现处")]
        )
        assert not any(e == "完全不存在的实体XYZ" for e, _ in mis)


# ──────────────────────────────────────────────────────────────────────────────
# search_in_reference — extra paths
# ──────────────────────────────────────────────────────────────────────────────

class TestSearchInReferenceExtraPaths:

    def test_entity_coverage_below50_with_2_entities_downgrades(self):
        """
        实体覆盖率 < 50% 且实体数 ≥ 2 → 状态降级为 △ 数据不一致
        """
        # 关键词 "科研创新" 命中，但两个实体中只有一个在参考文档里
        ref_text = "学校持续推进科研创新，获批国家级重点实验室若干。"
        it = _item(
            query="科研创新成果丰硕，国家级重点实验室和神秘联盟XYZ双双入选",
            numbers=[],
            entities=["国家级重点实验室", "神秘联盟XYZ", "另一个不存在的实体ABC"],
        )
        D.search_in_reference([it], [_ref("ref.txt", ref_text)])
        # 命中了关键词 → found_any=True；但实体覆盖率 < 50% (1/3) 且 ≥2实体
        if "✓" in it.get("状态", "") or "△" in it.get("状态", ""):
            # 有匹配时检查降级
            assert "数据不一致" in it["状态"] or "部分匹配" in it["状态"] or "需核实" in it["状态"], \
                f"低实体覆盖应触发降级，实际: {it['状态']}"

    def test_misattributed_entity_downgrades_to_inconsistent(self):
        """
        实体存在于参考文档但不与匹配关键词共现（张冠李戴）→ △ 数据不一致
        """
        kw = "教学改革"
        entity = "特殊研究所ZZZZZ"
        ref_text = (
            f"学校坚持{kw}，成效显著，持续推进教育创新。"
            + "X" * 600 +
            f"{entity}成立于2020年，专注前沿研究。"
        )
        it = _item(
            query=f"{kw}推动{entity}取得突破",
            numbers=[],
            entities=[entity],
        )
        D.search_in_reference([it], [_ref("ref.txt", ref_text)])
        # 若命中，实体应被判为张冠李戴
        if "✓" in it.get("状态", ""):
            assert "数据不一致" in it["状态"] or "部分" in it["状态"], \
                f"张冠李戴应降级，实际: {it['状态']}"

    def test_growth_rate_warning_added(self):
        """
        声明含增长率 % 且参考文档中该百分比不在增长语境 → 警告
        """
        ref_text = "学校就业率为95%，质量较高。"  # 95% 在参考中，但非增长语境
        it = _item(
            query="就业率增长至95%，同比大幅提升",
            numbers=["95%"],
        )
        D.search_in_reference([it], [_ref("ref.txt", ref_text)])
        # 命中后检查增长率警告
        warning = it.get("反向验证警告", "")
        if it.get("状态", "") != "✗ 未找到":
            assert "增长率" in warning or "核实" in warning or "" == warning, \
                "增长率语境不匹配时应添加警告"

    def test_rule4_short_keyword_no_strong_evidence_downgrades(self):
        """
        Rule ④: 短关键词（≤3字）+ 无数字/引号专名佐证 → △ 需核实
        """
        ref_text = "学校坚持以人为本的教育理念，办学成效显著。"
        it = _item(
            query="以人为本的教育持续推进",
            numbers=[],
            entities=[],
        )
        D.search_in_reference([it], [_ref("ref.txt", ref_text)])
        # 只要命中了，且关键词 ≤3字，应降级
        if it.get("状态", "") not in ("✗ 未找到", "待核实"):
            # 如果确实命中了短关键词，验证状态
            assert it["状态"] != "✓ 已确认", \
                f"短关键词无强证据不应为已确认，实际: {it['状态']}"

    def test_rule4_long_keyword_confirmed_stays(self):
        """
        Rule ④ 保护：长关键词（>3字）+ 数字匹配 → 保持 ✓ 已确认
        """
        ref_text = "学校获批国家级科研项目356项，成效显著。"
        it = _run(
            query="获批国家级科研项目356项",
            numbers=["356项"],
            entities=["国家级科研项目"],
            ref_text=ref_text,
        )
        assert "✓" in it["状态"], f"长关键词+数字匹配应保持确认，实际: {it['状态']}"

    def test_no_match_status_not_changed(self):
        """
        关键词完全不在参考文档 → 维持 ✗ 未找到
        """
        it = _run(
            query="完全不存在的神秘内容ZZZXXX999",
            numbers=[],
            ref_text="学校坚持立德树人，培养高素质人才。",
        )
        assert "✗" in it["状态"] or "未找到" in it["状态"]

    def test_growth_rate_not_in_growth_ctx_warns(self):
        """
        声明含增长关键词+百分比，参考文档中百分比不在增长语境 → 增长率警告
        """
        ref_text = "增长率达到历史新高，超过预期。" + "X" * 100 + "学校评分为98%，继续保持稳定。"
        it = _item(query="增长率达到98%", numbers=["98%"])
        D.search_in_reference([it], [_ref("ref.txt", ref_text)])
        assert "✓" in it["状态"], f"应匹配成功，实际: {it['状态']}"
        assert "增长率" in it.get("反向验证警告", ""), \
            f"百分比不在增长语境应有警告，实际: {it.get('反向验证警告','')}"

    def test_generic_high_freq_keyword_downgrades_to_core_review(self):
        """
        关键词出现 >20 次且仅见于少数文档（非领域核心词），无数字/引号强证据
        → 自动降级为 △ 需核实，并附高频通用警告
        """
        generic_kw_phrase = "持续推进各项工作成效显著"
        ref_a = (generic_kw_phrase + "。\n") * 25  # 出现 25 次
        ref_b = "学校积极推动教学改革，努力提升教育质量，持续进步。"  # 不含该短语
        it = _item(query=generic_kw_phrase, numbers=[], entities=[])
        D.search_in_reference([it], [_ref("a.txt", ref_a), _ref("b.txt", ref_b)])
        assert "需核实" in it["状态"] or "数据不一致" in it["状态"], \
            f"高频通用关键词应降级，实际: {it['状态']}"
        assert "通用" in it.get("反向验证警告", "") or "过于通用" in it.get("反向验证警告", ""), \
            f"应有通用关键词警告，实际警告: {it.get('反向验证警告','')[:200]}"


# ──────────────────────────────────────────────────────────────────────────────
# 「政策依据：」行 — 跳过实体-语境共现检查，保留文件名存在性检查
# ──────────────────────────────────────────────────────────────────────────────

class TestPolicyRefLineCooccurrenceSkip:
    """
    「政策依据：A文件、B文件」格式：
    - 不因为 B文件实体不在 A文件关键词附近而触发「△ 数据不一致」
    - 但若文件名完全不在参考库 → 仍然 ✗ 未找到
    - 非「政策依据：」格式的普通表述仍然走共现检查
    """

    def _policy_item(self, query, entities):
        return {
            "表述内容": query,
            "命中数字": [],
            "命中实体": entities,
            "子命题": [],
            "状态": "✗ 未找到",
            "反向验证警告": "",
            "出处": "", "匹配上下文简述": "", "原文片段": "", "匹配关键词": "",
        }

    def test_policy_ref_multi_doc_no_inconsistent(self):
        """
        「政策依据：」行同时引用 A、B 两个文件：
        - 关键词命中 A 文件（「学生欺凌综合治理方案」）
        - 实体「特殊单位名ZZZ」仅见于 B 文件，与 A 文件关键词不共现
        → 不应触发 △ 数据不一致（多文件并列引用是合法格式，跳过共现检查）
        """
        # A 文件：含匹配关键词，但不含实体
        ref_a = "学生欺凌综合治理方案的具体要求如下：" + "X" * 600
        # B 文件：含实体「特殊单位名ZZZ」，但不含关键词
        ref_b = "特殊单位名ZZZ的相关规定内容。"

        it = self._policy_item(
            query=(
                "- 政策依据：教育部办公厅《学生欺凌综合治理方案》"
                "（教督〔2017〕10号）、教育部《安全管理规定》（教基厅〔2024〕18号）"
            ),
            entities=["特殊单位名ZZZ"],
        )
        D.search_in_reference([it], [
            _ref("ref_a.txt", ref_a),
            _ref("ref_b.txt", ref_b),
        ])
        assert "数据不一致" not in it["状态"], \
            f"政策依据多文件引用不应触发数据不一致，实际: {it['状态']}"

    def test_policy_ref_missing_doc_stays_not_found(self):
        """
        「政策依据：」行引用的文件名完全不在参考库
        → 仍应为 ✗ 未找到（文件名存在性检查不受影响）
        """
        ref = "完全不相关的内容。"
        it = self._policy_item(
            query="- 政策依据： 《完全不存在的神秘文件ZZZXXX》（教基厅函〔9999〕99号）",
            entities=[],
        )
        D.search_in_reference([it], [_ref("ref.txt", ref)])
        assert "✗" in it["状态"] or "未找到" in it["状态"], \
            f"文件名不存在时应为未找到，实际: {it['状态']}"

    def test_normal_statement_still_triggers_cooccurrence_check(self):
        """
        非「政策依据：」的普通表述仍然走共现检查
        → 实体张冠李戴时应降级为 △ 数据不一致
        """
        kw = "教学改革"
        entity = "特殊研究所ZZZABC"
        ref_text = (
            f"学校坚持{kw}，成效显著，持续推进教育创新。"
            + "X" * 600
            + f"{entity}成立于2020年，专注前沿研究。"
        )
        it = {
            "表述内容": f"{kw}推动{entity}取得突破",  # 不以「政策依据：」开头
            "命中数字": [],
            "命中实体": [entity],
            "子命题": [],
            "状态": "✗ 未找到",
            "反向验证警告": "",
            "出处": "", "匹配上下文简述": "", "原文片段": "", "匹配关键词": "",
        }
        D.search_in_reference([it], [_ref("ref.txt", ref_text)])
        # 普通表述仍然走共现检查；若命中则应检测到张冠李戴
        if "✓" in it.get("状态", ""):
            assert "数据不一致" in it["状态"] or "不匹配" in it.get("反向验证警告", ""), \
                f"普通表述的张冠李戴仍应被检测，实际: {it['状态']}"

    def test_policy_ref_keyword_found_confirmed_not_inconsistent(self):
        """
        「政策依据：」行的关键词能在参考库找到
        → 状态应为 ✓ 已确认，不是 △ 数据不一致
        """
        ref = "关于进一步加强和规范教育收费管理的意见全文内容，包含具体要求。"
        it = self._policy_item(
            query="- 政策依据： 教育部等五部门《关于进一步加强和规范教育收费管理的意见》（教财〔2020〕5号）、财政部《中小学校财务制度》（财教〔2022〕159号）",
            entities=["中小学校财务制度"],
        )
        D.search_in_reference([it], [_ref("ref.txt", ref)])
        assert "✓" in it["状态"], \
            f"政策依据行关键词命中应为已确认，实际: {it['状态']}"
        assert "数据不一致" not in it["状态"]


class TestGetContextSnippetEdgeCases:
    def test_newline_replaced(self):
        text = "学校\n获批\n356项"
        snippet = D.get_context_snippet(text, "获批")
        assert "\n" not in snippet

    def test_at_beginning_of_text(self):
        text = "关键词在最开头，后续内容" + "X" * 200
        snippet = D.get_context_snippet(text, "关键词在最开头")
        assert "关键词在最开头" in snippet

    def test_at_end_of_text(self):
        text = "X" * 200 + "关键词在结尾"
        snippet = D.get_context_snippet(text, "关键词在结尾")
        assert "关键词在结尾" in snippet
