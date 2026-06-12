#!/usr/bin/env python3
"""
文档表述准确性核对脚本（v3 — 复合表述分解增强版）。

工作流程：
1. 将reference_dir下的所有docx/doc文件转换为txt（使用pandoc，.doc用textutil回退）
2. 将目标文档docx转换为txt并提取所有定性和定量表述
3. 将复合表述按分隔符拆解为多个子命题，在全部参考文档中逐一检索
4. 增加实体覆盖检查：核实表述中所有专有名词是否在参考文档中出现
5. 标记状态并输出到Excel

v3 新增能力：
- 复合表述分解：将"A入选X，B获批Y，C实现Z"拆分为独立子命题分别验证
- 实体覆盖检查：提取句内专有名词（引号、书名号内文本），检查与参考文档的一致性
- 子命题独立性评分：任一子命题无出处则整体降级
- .doc 文件自动回退到 textutil 转换

用法:
  python3 doc_fact_check.py <目标文档.docx> <参考文档目录> [输出Excel路径]
  python3 doc_fact_check.py --regenerate <json路径> <输出Excel路径>

输出：
- txt_output/checklist_result.json         第一轮初步结果
- txt_output/checklist_reverse_check.json  反向验证辅助报告
- 核对清单.xlsx                            完整Excel核对清单（5个工作表）
"""

import sys
import os
import json
import glob
import re
import subprocess
from pathlib import Path


# ── 工具函数 ──────────────────────────────────────────────

def convert_docx_to_txt(docx_path, txt_dir):
    """使用pandoc将docx/doc文件转换为txt，.doc文件自动用textutil回退"""
    os.makedirs(txt_dir, exist_ok=True)
    docx_path = os.path.abspath(docx_path)
    basename = os.path.splitext(os.path.basename(docx_path))[0]
    txt_path = os.path.join(txt_dir, basename + ".txt")

    if os.path.exists(txt_path):
        print(f"  [跳过] {basename}.txt 已存在")
        return txt_path

    ext = os.path.splitext(docx_path)[1].lower()
    if ext == '.doc':
        # .doc 文件用 textutil 先转 .docx 再转 txt
        import tempfile
        tmp_docx = os.path.join(tempfile.gettempdir(), basename + "_temp.docx")
        try:
            subprocess.run(['textutil', '-convert', 'docx', docx_path, '-output', tmp_docx],
                           capture_output=True, text=True, timeout=60, check=True)
            docx_path = tmp_docx
        except Exception as e:
            print(f"  [失败] {basename}: .doc转换失败 - {e}")
            return txt_path

    try:
        result = subprocess.run(
            ["pandoc", docx_path, "-t", "plain", "--wrap=none", "-o", txt_path],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            print(f"  [完成] {basename}.txt")
        else:
            print(f"  [失败] {basename}: {result.stderr[:200]}")
    except Exception as e:
        print(f"  [错误] {basename}: {e}")
    return txt_path


def find_doc_files(ref_dir):
    """在目录中查找所有 .docx 和 .doc 文件（递归）"""
    patterns = ["*.docx", "*.doc"]
    files = []
    for pat in patterns:
        found = glob.glob(os.path.join(ref_dir, pat))
        if not found:
            found = glob.glob(os.path.join(ref_dir, "**", pat), recursive=True)
        files.extend(found)
    return sorted(set(files))


# ── 表述提取 ──────────────────────────────────────────────

_NUMERIC_SUFFIXES = (
    r'[万亿]?[个项篇章人次所支部门家国种届轮次场类]'
    r'|[万亿]?%'
    r'|[万亿]?元'
    r'|[万亿]?倍'
    r'|[万亿]?(?:平方米|平米|m\u00b2)'
    r'|[年月日]'
)


def extract_all_numbers(text):
    """从文本中提取所有「数字+单位」组合"""
    pat = re.compile(r'([\d.]+)\s*(' + _NUMERIC_SUFFIXES + r')')
    results = []
    for m in pat.finditer(text):
        results.append(m.group(0).strip())
    return sorted(set(results), key=lambda x: len(x), reverse=True)


def extract_named_entities(text):
    """
    从文本中提取专有名词实体，用于实体覆盖检查。
    返回实体列表，按长度降序。

    提取来源：
    - 中文双引号 \u201c\u201d 内文本
    - 书名号《》内文本
    - 英文引号 "..." 内文本
    - 中文单引号 '' 内文本
    - 特定模式：XX实验室、XX中心、XX项目、XX平台、XX团队、XX学院
    """
    entities = []

    # 中文双引号
    for q in re.findall(r'\u201c([^\u201c\u201d]{2,50})\u201d', text):
        entities.append(q.strip())

    # 英文引号
    for q in re.findall(r'"([^"]{3,50})"', text):
        entities.append(q.strip())

    # 书名号
    for q in re.findall(r'\u300a([^\u300a\u300b]{2,50})\u300b', text):
        entities.append(q.strip())

    # 机构/平台模式
    entity_patterns = [
        r'[\u4e00-\u9fff\u300a\u300b\w]{2,20}(?:实验室|研究中心|研究院|研究所|学院|基地|平台|中心)',
        r'[\u4e00-\u9fff\u300a\u300b\w]{2,30}(?:建设项目|行动计划|专项规划|工作方案)',
        r'[\u4e00-\u9fff\w]{3,20}(?:创新团队|研究团队|教学团队)',
    ]
    for pat in entity_patterns:
        for m in re.findall(pat, text):
            entities.append(m.strip())

    # 去重，按长度降序
    seen = set()
    result = []
    for e in entities:
        if e not in seen:
            seen.add(e)
            result.append(e)
    result.sort(key=len, reverse=True)
    return result


def decompose_statement(text):
    """
    将复合表述按中文标点拆分为子命题。

    分隔符：分号、逗号（但保留顿号分隔的并列项不拆分）
    策略：
    1. 先按 ； 拆分（强分隔）
    2. 再按 ， 拆分（弱分隔，但保留明显列举的短项）

    返回：[(子命题文本, 是否独立验证), ...]
    独立验证 = True 表示这个子命题包含独立可验证的信息
    """
    # 先按分号拆分
    parts = re.split(r'[；]', text)
    sub_claims = []

    for part in parts:
        part = part.strip()
        if len(part) < 8:
            continue
        # 对逗号分隔的长子命题进一步拆分
        comma_parts = re.split(r'[，]', part)
        for cp in comma_parts:
            cp = cp.strip()
            if len(cp) >= 8:
                # 判断是否含可验证信息
                has_numbers = bool(extract_all_numbers(cp))
                has_entities = bool(extract_named_entities(cp))
                has_action = bool(re.search(
                    r'(?:入选|获批|荣获|获得|新增|组建|成立|牵头|实施|推进|完成|突破|增长|提升|达到|建成|上线|发布|通过|授牌|签约|设立|启用|开创|实现)',
                    cp))
                sub_claims.append({
                    "text": cp,
                    "independent": has_numbers or has_entities or has_action,
                    "numbers": extract_all_numbers(cp),
                    "entities": extract_named_entities(cp),
                })

    return sub_claims


def extract_statements(main_txt_path):
    """从目标文档txt中提取所有定性和定量表述"""
    with open(main_txt_path, 'r', encoding='utf-8') as f:
        content = f.read()

    statements = []
    raw_parts = re.split(r'[。；\n]+', content)
    raw_parts = [p.strip() for p in raw_parts if len(p.strip()) > 10]

    for part in raw_parts:
        if re.match(r'^[\s\d\.\u3001\uff09\)]+$', part):
            continue
        if len(part) < 10:
            continue

        numbers = extract_all_numbers(part)
        entities = extract_named_entities(part)
        sub_claims = decompose_statement(part)
        stype = "定量" if numbers else "定性"

        statements.append({
            "类型": stype,
            "表述内容": part,
            "状态": "\u2717 \u672a\u627e\u5230",
            "出处": "\u2014",
            "原文片段": "所有参照文档均未出现此表述/数据",
            "匹配关键词": "",
            "命中数字": numbers,
            "命中实体": entities,
            "子命题": sub_claims,
            "匹配上下文简述": "",
            "反向验证警告": "",
        })

    # 去重
    seen = set()
    unique = []
    for s in statements:
        key = s["表述内容"][:30]
        if key not in seen:
            seen.add(key)
            unique.append(s)
    return unique


# ── 关键词提取与匹配 ──────────────────────────────────────

def extract_keywords(text):
    """从表述文本中提取用于搜索的关键词，按优先级返回"""
    keywords = []

    # 第一优先：引号内的专有名词（逐对匹配，避免跨类型误匹配）
    quote_pairs = [
        (r'\u201c', r'\u201d'),   # 中文双引号 ""
        (r'\u2018', r'\u2019'),   # 中文单引号 ''
        (r'\u300c', r'\u300d'),   # 直角引号「」
        (r'\u300e', r'\u300f'),   # 直角双引号『』
        (r'\u300a', r'\u300b'),   # 书名号《》
        (r'"',      r'"'),        # ASCII 双引号
    ]
    for open_q, close_q in quote_pairs:
        for m in re.findall(open_q + r'([^' + open_q + close_q + r']{2,})' + close_q, text):
            if 3 <= len(m.strip()) <= 40:
                keywords.append(m.strip())

    # 第二优先：数字+单位组合
    num_matches = re.findall(r'[\d.]+' + _NUMERIC_SUFFIXES, text)
    keywords.extend(n.strip() for n in num_matches if 3 <= len(n.strip()) <= 40)

    # 第三优先：特定动作+名词短语
    action_verbs = r'(?:入选|获批|荣获|获得|新增|组建|成立|牵头|实施|推进|完成|突破|增长|提升|达到|建成|上线|发布|通过|授牌|签约|设立|启用|开创)'
    action_phrases = re.findall(action_verbs + r'[\u4e00-\u9fff]{2,20}', text)
    keywords.extend(a.strip() for a in action_phrases if 3 <= len(a.strip()) <= 40)

    # 第四优先：4~40 字连续中文
    long_chinese = re.findall(r'[\u4e00-\u9fff]{4,40}', text)
    long_chinese.sort(key=len, reverse=True)
    keywords.extend(long_chinese[:5])

    # 去重，按长度降序
    seen = set()
    result = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            result.append(kw)
    result.sort(key=len, reverse=True)
    return result[:8] if result else [text[:20]]


def get_context_snippet(content, keyword, context_width=80):
    """获取关键词在内容中的上下文片段"""
    idx = content.find(keyword)
    if idx == -1:
        return ""
    start = max(0, idx - context_width)
    end = min(len(content), idx + len(keyword) + context_width)
    return content[start:end].replace('\n', ' ')


def build_ref_index(references):
    """构建参考文档索引"""
    index = {}
    for txt_path, txt_content in references:
        short_name = os.path.splitext(os.path.basename(txt_path))[0][:40]
        index[short_name] = (txt_path, txt_content)
    return index


def verify_sub_claims(sub_claims, references):
    """
    独立验证每个子命题在参考文档中的匹配情况。

    返回：
    - match_flags: [bool, ...] 每个子命题是否找到匹配
    - total: 子命题总数
    - matched: 匹配到的子命题数
    - details: list of str 详细说明
    """
    independent_claims = [sc for sc in sub_claims if sc.get("independent")]
    if not independent_claims:
        return [], 0, 0, []

    match_flags = []
    details = []

    for sc in independent_claims:
        sub_kws = extract_keywords(sc["text"])
        sub_found = False

        for kw in sub_kws:
            for _, ref_content in references:
                if kw in ref_content:
                    sub_found = True
                    break
            if sub_found:
                break

        match_flags.append(sub_found)
        if not sub_found:
            details.append(f"子命题「{sc['text'][:50]}」无独立出处")

    total = len(independent_claims)
    matched = sum(match_flags)
    return match_flags, total, matched, details


def verify_entity_coverage(entities, all_ref_contents):
    """
    实体覆盖检查：目标表述中的专有名词，在所有参考文档中出现了多少。

    all_ref_contents: list of (ref_name, ref_content) tuples
    返回：(覆盖率 %, 未覆盖实体列表)
    """
    if not entities:
        return 100.0, []

    uncovered = []
    for entity in entities:
        found = any(entity in content for _, content in all_ref_contents)
        if not found:
            uncovered.append(entity)

    coverage = (len(entities) - len(uncovered)) / len(entities) * 100
    return coverage, uncovered


def verify_entity_context_cooccurrence(entities, matched_kw, references, context_radius=300):
    """
    v3.1 新增：实体-语境共现验证。

    仅检查实体字符串是否存在是不够的——实体必须与匹配关键词出现在同一语境中。
    例如：目标文档说"金融大模型实验室入选两重项目"，
    关键词"两重"在参考文档中匹配了，实体"金融大模型实验室"也存在于参考文档中，
    但它们在参考文档中从未在同一段落出现——这是张冠李戴。

    参数：
    - entities: 目标表述中的专有名词列表
    - matched_kw: 触发匹配的关键词
    - references: list of (ref_name, ref_content)
    - context_radius: 共现窗口大小（字符数）

    返回：
    - misattributed: list of (entity, ref_name_where_found_but_not_near_kw)
      实体在参考文档中存在但不在关键词附近的列表
    - cooccurring: list of entity 与关键词共现的实体
    """
    if not entities or not matched_kw:
        return [], list(entities)

    misattributed = []
    cooccurring = []

    for entity in entities:
        if entity == matched_kw:
            cooccurring.append(entity)
            continue

        entity_found_anywhere = False
        entity_near_kw = False
        found_in_ref = ""

        for ref_name, ref_content in references:
            if entity in ref_content:
                entity_found_anywhere = True
                if not found_in_ref:
                    # 用文件名（去路径和扩展名）作为标识
                    found_in_ref = os.path.splitext(os.path.basename(ref_name))[0]

            # 检查实体是否与关键词在同一语境中共现
            if entity in ref_content and matched_kw in ref_content:
                # 找到实体和关键词在参考文档中最近的位置
                kw_positions = [m.start() for m in re.finditer(re.escape(matched_kw), ref_content)]
                entity_positions = [m.start() for m in re.finditer(re.escape(entity), ref_content)]
                for ep in entity_positions:
                    for kp in kw_positions:
                        if abs(ep - kp) <= context_radius:
                            entity_near_kw = True
                            break
                    if entity_near_kw:
                        break

        if entity_near_kw:
            cooccurring.append(entity)
        elif entity_found_anywhere:
            # 实体存在但不与关键词共现——张冠李戴风险
            misattributed.append((entity, found_in_ref))
        else:
            # 实体完全不存在——已有 verify_entity_coverage 处理
            cooccurring.append(entity)  # 不在 misattributed 里，由 coverage 负责

    return misattributed, cooccurring


def search_in_reference(texts, references):
    """
    增强版全文检索：
    - 找到首个关键词匹配后继续搜全部参考文档做实体覆盖 + 数字验证
    - 实体覆盖检查跨全部参考文档
    - 子命题独立验证
    - 反向验证标记
    """
    index = build_ref_index(references)
    total = len(texts)
    last_pct = 0

    for idx, item in enumerate(texts):
        # 进度指示
        pct = (idx + 1) * 100 // total
        if pct >= last_pct + 10:
            print(f"  检索进度: {idx+1}/{total} ({pct}%)")
            last_pct = pct

        query = item["表述内容"]
        keywords = extract_keywords(query)
        all_numbers = item.get("命中数字", [])
        all_entities = item.get("命中实体", [])
        sub_claims = item.get("子命题", [])

        found_any = False
        matched_kw = ""
        matched_ref = ""
        matched_content = ""

        # 第一遍：找到首个关键词匹配（确定出处和原文片段）
        for kw in keywords:
            for ref_name, (ref_path, ref_content) in index.items():
                if kw in ref_content:
                    snippet = get_context_snippet(ref_content, kw, 80)
                    item["状态"] = "\u2713 \u5df2\u786e\u8ba4"
                    item["出处"] = ref_name
                    item["原文片段"] = f"{ref_name}: ...{snippet}..."
                    item["匹配关键词"] = kw
                    item["匹配上下文简述"] = snippet[:120]
                    matched_kw = kw
                    matched_ref = ref_name
                    matched_content = ref_content
                    found_any = True
                    break
            if found_any:
                break

        if not found_any:
            continue

        # ── 反向验证标记 ──
        warnings = []

        # === v3: 子命题独立性验证 ===
        mf, total_sp, matched_sp, sub_details = verify_sub_claims(sub_claims, references)
        if total_sp > 1:
            if matched_sp < total_sp:
                if "\u25b3" not in item["状态"]:
                    item["状态"] = "\u25b3 \u90e8\u5206\u5339\u914d"
                sub_warning = f"复合表述含{total_sp}个可独立验证子命题，{matched_sp}个匹配({matched_sp/total_sp*100:.0f}%)，" + "; ".join(sub_details)
                warnings.append(sub_warning)

        # === v3: 实体覆盖检查（跨全部参考文档） ===
        if all_entities:
            coverage, uncovered = verify_entity_coverage(all_entities, references)
            if coverage < 100:
                entity_warning = (
                    f"实体覆盖率{coverage:.0f}%，未在任何参考文档中找到的实体: {uncovered}。"
                    f"可能将不同实体张冠李戴"
                )
                warnings.append(entity_warning)
                if coverage < 50 and len(all_entities) >= 2:
                    if "\u2713" in item["状态"]:
                        item["状态"] = "\u25b3 \u6570\u636e\u4e0d\u4e00\u81f4"

            # === v3.1: 实体-语境共现验证 ===
            # 即使实体覆盖率100%，也需检查实体是否与匹配关键词在同一语境
            if matched_kw:
                misattributed, cooccurring = verify_entity_context_cooccurrence(
                    all_entities, matched_kw, references
                )
                if misattributed:
                    # 有实体存在于参考文档中，但不与匹配关键词共现
                    mis_names = [f"{e}(仅见于{ref[:30]}，非'{matched_kw}'语境)"
                                 for e, ref in misattributed]
                    entity_ctx_warning = (
                        f"⚠ 实体-语境不匹配: {', '.join(mis_names)}。"
                        f"关键词'{matched_kw}'匹配成功，但这些实体在参考文档中"
                        f"从不与'{matched_kw}'在同一语境中出现，可能张冠李戴"
                    )
                    warnings.append(entity_ctx_warning)
                    # 关键实体不共现 → 直接降级为数据不一致
                    # 只要有任何实体被张冠李戴（不少于1个且不是仅关键词本身），就降级
                    non_kw_mis = [(e, r) for e, r in misattributed if e != matched_kw]
                    if "\u2713" in item.get("状态", "") and len(non_kw_mis) >= 1:
                        item["状态"] = "\u25b3 \u6570\u636e\u4e0d\u4e00\u81f4"

        # === 原有检查 ===

        # 数字缺失检查（跨全部参考文档）
        if all_numbers:
            missing = [n for n in all_numbers
                       if not any(n in ref_content for _, ref_content in references)]
            if missing:
                warnings.append(f"部分数字未在参考文档中找到: {missing}")

        # 增长率语境检查
        growth_keywords = re.findall(r'增长|增幅|提高|提升|同比|突破|跃升|攀升|翻番', query)
        if growth_keywords:
            pct_nums = [n for n in all_numbers if n.endswith('%')]
            for pn in pct_nums:
                in_growth_ctx = False
                for _, ref_content in references:
                    if pn in ref_content:
                        idx2 = ref_content.find(pn)
                        ctx = ref_content[max(0, idx2-25):idx2+25]
                        if any(gk in ctx for gk in growth_keywords):
                            in_growth_ctx = True
                            break
                if not in_growth_ctx:
                    warnings.append(f"增长率 {pn} 未在增长语境中出现，需人工核实")

        # 关键词过短检查
        if len(matched_kw) <= 3:
            warnings.append(f"匹配关键词过短({len(matched_kw)}字)，可能为误匹配，需人工确认上下文")

        # 关键词过于通用检查
        total_occurrences = sum(ref_content.count(matched_kw) for _, ref_content in references)
        if total_occurrences > 20:
            warnings.append(
                f"匹配关键词\u300c{matched_kw}\u300d在参考文档中出现{total_occurrences}次，过于通用，"
                f"请确认匹配到的上下文是否与目标表述一致"
            )

        item["反向验证警告"] = "; ".join(warnings) if warnings else ""


# ── Excel 生成 ─────────────────────────────────────────────

def generate_excel(items, output_path):
    """生成5工作表的Excel核对清单"""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import PatternFill, Border, Side
    except ImportError:
        print("请先安装 openpyxl: pip install openpyxl")
        return

    wb = Workbook()

    green  = PatternFill(start_color="90EE90", end_color="90EE90", fill_type="solid")
    yellow = PatternFill(start_color="FFFF00", end_color="FFFF00", fill_type="solid")
    red    = PatternFill(start_color="FF6B6B", end_color="FF6B6B", fill_type="solid")
    orange = PatternFill(start_color="FFA500", end_color="FFA500", fill_type="solid")

    thin_border = Border(
        left=Side(style='thin'), right=Side(style='thin'),
        top=Side(style='thin'), bottom=Side(style='thin'))

    def _auto_width(ws, widths):
        for col_letter, w in widths.items():
            ws.column_dimensions[col_letter].width = w

    def _color_cell(ws, row, col, status):
        cell = ws.cell(row=row, column=col)
        if "\u2713" in status:
            cell.fill = green
        elif "\u6570\u636e\u4e0d\u4e00\u81f4" in status:
            cell.fill = orange
        elif "\u25b3" in status:
            cell.fill = yellow
        elif "\u672a\u627e\u5230" in status:
            cell.fill = red

    # Sheet 1: 全部核对结果
    ws1 = wb.active
    ws1.title = "全部核对结果"
    headers1 = ["序号", "类型", "表述内容", "核对状态", "出处", "原文片段", "反向验证警告", "命中数字"]
    ws1.append(headers1)
    for i, item in enumerate(items, 1):
        row_data = [
            i, item["类型"], item["表述内容"], item["状态"],
            item["出处"], item["原文片段"],
            item.get("反向验证警告", ""),
            ", ".join(item.get("命中数字", []))
        ]
        ws1.append(row_data)
        _color_cell(ws1, i+1, 4, item["状态"])
        if item.get("反向验证警告", ""):
            ws1.cell(row=i+1, column=7).fill = yellow
        for c in range(1, 9):
            ws1.cell(row=i+1, column=c).border = thin_border
    _auto_width(ws1, {'A': 6, 'B': 8, 'C': 55, 'D': 18, 'E': 40, 'F': 90, 'G': 50, 'H': 30})

    # Sheet 2: 已确认
    ws2 = wb.create_sheet("已确认表述")
    ws2.append(["序号", "类型", "表述内容", "出处", "原文片段", "反向验证警告"])
    for i, item in enumerate((x for x in items if "\u2713" in x.get("状态", "")), 1):
        ws2.append([i, item["类型"], item["表述内容"], item["出处"], item["原文片段"],
                    item.get("反向验证警告", "")])
    _auto_width(ws2, {'A': 6, 'B': 8, 'C': 55, 'D': 40, 'E': 90, 'F': 50})

    # Sheet 3: 待核实
    ws3 = wb.create_sheet("待核实表述")
    ws3.append(["序号", "类型", "表述内容", "核对状态", "备注说明"])
    for i, item in enumerate((x for x in items if "\u672a\u627e\u5230" in x.get("状态", "") or "\u25b3" in x.get("状态", "")), 1):
        ws3.append([i, item["类型"], item["表述内容"], item["状态"], item["原文片段"]])
        _color_cell(ws3, i+1, 4, item["状态"])
    _auto_width(ws3, {'A': 6, 'B': 8, 'C': 55, 'D': 18, 'E': 90})

    # Sheet 4: 反向验证重点关注
    ws4 = wb.create_sheet("反向验证重点关注（第三轮）")
    ws4.append(["序号", "类型", "表述内容", "当前状态", "命中数字", "匹配关键词", "反向验证警告", "人工核查建议"])
    suspicious = [item for item in items if item.get("反向验证警告", "")]
    for i, item in enumerate(suspicious, 1):
        warnings = item["反向验证警告"]
        advice = _build_advice(warnings)
        ws4.append([i, item["类型"], item["表述内容"], item["状态"],
                    ", ".join(item.get("命中数字", [])),
                    item.get("匹配关键词", ""), warnings, advice])
        ws4.cell(row=i+1, column=7).fill = yellow
    _auto_width(ws4, {'A': 6, 'B': 8, 'C': 50, 'D': 16, 'E': 30, 'F': 25, 'G': 55, 'H': 60})

    # Sheet 5: 实体覆盖分析（v3新增）
    ws5 = wb.create_sheet("实体覆盖分析")
    ws5.append(["序号", "表述内容", "提取的实体", "匹配状态", "实体覆盖情况"])
    for i, item in enumerate(items, 1):
        entities = item.get("命中实体", [])
        if entities:
            ws5.append([
                i, item["表述内容"][:80],
                ", ".join(entities),
                item["状态"],
                _build_entity_coverage_note(item.get("反向验证警告", ""))
            ])
            _color_cell(ws5, i+1, 4, item["状态"])
    _auto_width(ws5, {'A': 6, 'B': 55, 'C': 50, 'D': 16, 'E': 50})

    wb.save(output_path)
    print(f"\nExcel已保存: {output_path}")
    print(f"  工作表: {wb.sheetnames}")
    print(f"  其中\u300c反向验证重点关注\u300d工作表包含 {len(suspicious)} 条需第三轮复查的条目")


def _build_advice(warnings):
    """根据警告内容生成人工核查建议"""
    parts = []
    if "过短" in warnings:
        parts.append("\u2460 匹配关键词太短，请读取原文片段确认是否真的对应同一表述")
    if "出现" in warnings and "次" in warnings:
        parts.append("\u2461 关键词过于通用，请检查上下文是否与目标表述一致")
    if "数字未找到" in warnings:
        parts.append("\u2462 部分数字无出处，请核实这些数字的来源")
    if "增长率" in warnings:
        parts.append("\u2463 增长率数字缺少对应语境，请确认增长率是否准确")
    if "子命题" in warnings:
        parts.append("\u2464 复合表述有子命题无出处，请逐个子命题核实")
    if "实体覆盖率" in warnings:
        parts.append("\u2465 实体覆盖率不足，可能存在张冠李戴，请逐名称比对")
    return "; ".join(parts) if parts else "请逐字比对目标表述与原文片段是否一致"


def _build_entity_coverage_note(warnings):
    """从警告中提取实体覆盖相关信息"""
    if not warnings:
        return "\u2713 实体全部匹配"
    if "实体覆盖率" in warnings:
        return warnings
    return "见反向验证警告"


# ── 统计 ──────────────────────────────────────────────────

def print_summary(statements, stage="第一轮自动核对"):
    """打印汇总统计"""
    confirmed  = len([s for s in statements if "\u2713" in s["\u72b6\u6001"]])
    not_found  = len([s for s in statements if "\u672a\u627e\u5230" in s["\u72b6\u6001"]])
    partial    = len(statements) - confirmed - not_found
    suspicious = len([s for s in statements if s.get("反向验证警告", "")])

    print(f"\n{'='*60}")
    print(f"{stage} 完成")
    print(f"{'='*60}")
    print(f"总表述数:     {len(statements)}")
    print(f"  \u2713 已确认:   {confirmed}")
    print(f"  \u25b3 需核实:   {partial}")
    print(f"  \u2717 未找到:   {not_found}")
    if suspicious:
        print(f"{'='*60}")
        print(f"\u26a0 反向验证警告: {suspicious} 条\u300c已确认\u300d条目存在潜在问题")
        print(f"  请在 Excel\u300c反向验证重点关注\u300d工作表中逐条复查")
        print(f"{'='*60}")


# ── 入口 ──────────────────────────────────────────────────

def cmd_full_check():
    """运行完整的第一轮自动核对流程"""
    if len(sys.argv) < 3:
        print("用法: python3 doc_fact_check.py <目标文档.docx> <参考文档目录> [输出Excel路径]")
        sys.exit(1)

    main_docx   = sys.argv[1]
    ref_dir     = sys.argv[2]
    output_xlsx = sys.argv[3] if len(sys.argv) > 3 else "核对清单.xlsx"

    txt_dir = os.path.join(os.path.dirname(main_docx) or ".", "txt_output")
    os.makedirs(txt_dir, exist_ok=True)

    # Step 1: 转换参考文档
    print("=" * 60)
    print("Step 1: 转换参考文档 docx/doc \u2192 txt")
    print("=" * 60)
    ref_files = find_doc_files(ref_dir)
    print(f"找到 {len(ref_files)} 个参考文档")

    ref_texts = {}
    for doc in ref_files:
        txt_path = convert_docx_to_txt(doc, txt_dir)
        with open(txt_path, 'r', encoding='utf-8') as f:
            ref_texts[txt_path] = f.read()
        print(f"  \u2192 {os.path.basename(doc)}")

    # Step 2: 提取表述
    print("\n" + "=" * 60)
    print("Step 2: 提取目标文档表述 (v3: 含子命题分解 + 实体提取)")
    print("=" * 60)
    main_txt = convert_docx_to_txt(main_docx, txt_dir)
    statements = extract_statements(main_txt)

    # 统计复合表述
    compound_count = sum(1 for s in statements if len(s.get("子命题", [])) > 1)
    entity_count = sum(1 for s in statements if s.get("命中实体", []))
    print(f"提取到 {len(statements)} 条表述（定性+定量）")
    print(f"  其中复合表述 {compound_count} 条，含专有实体的表述 {entity_count} 条")

    # Step 3: 检索 + 反向验证
    print("\n" + "=" * 60)
    print("Step 3: 全文检索 + 子命题验证 + 实体覆盖检查")
    print("=" * 60)
    ref_list = list(ref_texts.items())
    search_in_reference(statements, ref_list)

    # Step 4: 保存中间结果
    print("\n" + "=" * 60)
    print("Step 4: 保存中间结果")
    print("=" * 60)
    json_path = os.path.join(txt_dir, "checklist_result.json")
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(statements, f, ensure_ascii=False, indent=2)
    print(f"中间结果已保存: {json_path}")

    reverse_check = [s for s in statements if s.get("反向验证警告", "")]
    rev_path = os.path.join(txt_dir, "checklist_reverse_check.json")
    with open(rev_path, 'w', encoding='utf-8') as f:
        json.dump(reverse_check, f, ensure_ascii=False, indent=2)
    print(f"反向验证报告已保存: {rev_path}")

    # Step 5: Excel
    print("\n" + "=" * 60)
    print("Step 5: 生成Excel核对清单 (v3: 含实体覆盖分析工作表)")
    print("=" * 60)
    if not output_xlsx.startswith("/"):
        output_xlsx = os.path.join(os.path.dirname(main_docx) or ".", output_xlsx)
    generate_excel(statements, output_xlsx)

    print_summary(statements)


def cmd_regenerate():
    """从已有的 JSON 重新生成 Excel"""
    if len(sys.argv) < 3:
        print("用法: python3 doc_fact_check.py --regenerate <checklist_result.json> [输出Excel路径]")
        sys.exit(1)

    json_path  = sys.argv[2]
    output_xlsx = sys.argv[3] if len(sys.argv) > 3 else "核对清单.xlsx"

    with open(json_path, 'r', encoding='utf-8') as f:
        statements = json.load(f)
    print(f"从 {json_path} 加载 {len(statements)} 条表述")

    if not output_xlsx.startswith("/"):
        output_xlsx = os.path.join(os.path.dirname(os.path.abspath(json_path)) or ".", output_xlsx)
    generate_excel(statements, output_xlsx)
    print_summary(statements, stage="人工复查后重生成")


def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "--regenerate":
        cmd_regenerate()
    elif len(sys.argv) >= 2 and sys.argv[1] in ("--help", "-h"):
        print(__doc__)
    else:
        cmd_full_check()


if __name__ == "__main__":
    main()
