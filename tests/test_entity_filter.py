"""
TDD — _is_valid_entity() 实体噪声过滤

问题：LLM 有时把整个句子片段（"我们培养的不仅是掌握财经专业"）或
序号结构（"四是纵深推进校院两级管理体制改革"）误识别为具名实体，
导致实体覆盖率 0% 并触发假"△ 数据不一致"降级。

过滤契约：
  无效（应过滤）：
    - 含第一人称代词：我们/他们/你们
    - 以序号起头：一是/二是/.../十是
    - 以连接词起头：因此/所以/而且/并且/但是/然而
  有效（应保留）：
    - 普通具名实体：ESI前1%学科、国家级科研项目、高层次人才
    - 机构/文件名：中央财经大学、国家自然科学基金
    - 带序号但 ≤2字前缀的项目名：不触发过滤
"""

import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import doc_fact_check as D


class TestIsValidEntity:
    # ── 应过滤（返回 False）──────────────────────────────────────
    def test_filters_first_person_women(self):
        assert D._is_valid_entity("我们培养的不仅是掌握财经专业") is False

    def test_filters_first_person_tamen(self):
        assert D._is_valid_entity("他们主动对接各类学科评估与排名体系") is False

    def test_filters_first_person_nimen(self):
        assert D._is_valid_entity("你们在评估工作中表现突出") is False

    def test_filters_ordinal_yishi(self):
        assert D._is_valid_entity("一是纵深推进校院两级管理体制改革") is False

    def test_filters_ordinal_sishi(self):
        assert D._is_valid_entity("四是持续推进教学改革") is False

    def test_filters_ordinal_shishi(self):
        assert D._is_valid_entity("十是总结全年工作") is False

    def test_filters_conjunction_yinci(self):
        assert D._is_valid_entity("因此我们决定推进改革") is False

    def test_filters_conjunction_suoyi(self):
        assert D._is_valid_entity("所以学校加快了步伐") is False

    def test_filters_conjunction_erqie(self):
        assert D._is_valid_entity("而且成效显著") is False

    def test_filters_empty_string(self):
        assert D._is_valid_entity("") is False

    # ── 应保留（返回 True）──────────────────────────────────────
    def test_keeps_esi_entity(self):
        assert D._is_valid_entity("ESI前1%学科") is True

    def test_keeps_named_project(self):
        assert D._is_valid_entity("国家级科研项目") is True

    def test_keeps_talent_term(self):
        assert D._is_valid_entity("高层次人才") is True

    def test_keeps_institution_name(self):
        assert D._is_valid_entity("中央财经大学") is True

    def test_keeps_fund_name(self):
        assert D._is_valid_entity("国家自然科学基金") is True

    def test_keeps_number_entity(self):
        assert D._is_valid_entity("十四五") is True  # 三字期间名

    def test_keeps_short_ordinal_not_sishi(self):
        # "第四批" 不是 "四是..." 格式，应保留
        assert D._is_valid_entity("第四批国家级项目") is True

    def test_keeps_document_with_brackets(self):
        # 文件名含《》，属于具名实体
        assert D._is_valid_entity("《人工智能行动计划》") is True


class TestEntityFilterAppliedInVerification:
    """验证过滤器在 search_in_reference 中实际生效。"""

    def _ref(self, name, text):
        return (f"/fake/{name}", text)

    def test_noisy_entity_does_not_cause_inconsistent_status(self):
        """
        声明含噪声实体 "我们培养的不仅是掌握财经专业"，
        过滤后该实体不参与覆盖率计算，不应触发 △ 数据不一致。
        """
        ref_text = "学校坚持立德树人，培养高素质财经人才，成效显著。"
        item = {
            "表述内容": "我们培养的不仅是掌握财经专业知识的优秀人才",
            "命中数字": [],
            "命中实体": ["我们培养的不仅是掌握财经专业", "优秀人才"],  # 第一个是噪声
            "子命题": [],
            "状态": "待核实",
            "反向验证警告": "",
            "出处": "", "匹配上下文简述": "", "原文片段": "", "匹配关键词": "",
        }
        D.search_in_reference([item], [self._ref("ref.txt", ref_text)])
        assert "数据不一致" not in item["状态"], \
            f"噪声实体不应导致数据不一致，实际状态: {item['状态']}"
