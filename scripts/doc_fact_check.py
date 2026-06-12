#!/usr/bin/env python3
"""
文档表述准确性核对脚本（v2 — 三轮复核增强版）。

工作流程：
1. 将reference_dir下的所有docx/doc文件转换为txt（使用pandoc）
2. 将目标文档docx转换为txt并提取所有定性和定量表述
3. 在全部参考文档中全文检索每个表述的出处
4. 标记状态并输出到Excel

用法:
  python3 doc_fact_check.py <目标文档.docx> <参考文档目录> [输出Excel路径]
  python3 doc_fact_check.py --regenerate <json路径> <输出Excel路径>

输出：
- txt_output/checklist_result.json         第一轮初步结果
- txt_output/checklist_reverse_check.json  第三轮反向验证辅助报告
- 核对清单.xlsx                            完整Excel核对清单（4个工作表）
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
    """使用pandoc将docx/doc文件转换为txt"""
    os.makedirs(txt_dir, exist_ok=True)
    docx_path = os.path.abspath(docx_path)
    basename = os.path.splitext(os.path.basename(docx_path))[0]
    txt_path = os.path.join(txt_dir, basename + ".txt")

    if os.path.exists(txt_path):
        print(f"  [跳过] {basename}.txt 已存在")
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
            # 也搜索子目录
            found = glob.glob(os.path.join(ref_dir, "**", pat), recursive=True)
        files.extend(found)
    # 去重
    return sorted(set(files))


# ── 表述提取 ──────────────────────────────────────────────

# 常见中文数字单位（按语义分组，[万亿]? 前缀覆盖量词前缀）
_NUMERIC_SUFFIXES = (
    # 计数类
    r'[万亿]?[个项篇章人次所支部门家国种届轮次场类]'
    # 百分比
    r'|[万亿]?%'
    # 金额类
    r'|[万亿]?元'
    # 倍数
    r'|[万亿]?倍'
    # 面积
    r'|[万亿]?(?:平方米|平米|m²)'
    # 时间类
    r'|[年月日]'
)


def extract_all_numbers(text):
    """
    从文本中提取所有「数字+单位」组合。
    返回去重后的完整匹配字符串列表，如 ['356项', '91%', '4.88亿元']。
    """
    pat = re.compile(r'([\d.]+)\s*(' + _NUMERIC_SUFFIXES + r')')
    results = []
    for m in pat.finditer(text):
        results.append(m.group(0).strip())
    return sorted(set(results), key=lambda x: len(x), reverse=True)


def extract_statements(main_txt_path):
    """
    从目标文档txt中提取所有定性和定量表述。
    按段落拆分，识别含具体数据/指标/成就的表述。
    """
    with open(main_txt_path, 'r', encoding='utf-8') as f:
        content = f.read()

    statements = []
    raw_parts = re.split(r'[。；\n]+', content)
    raw_parts = [p.strip() for p in raw_parts if len(p.strip()) > 10]

    for part in raw_parts:
        if re.match(r'^[\s\d\.、）\)]+$', part):
            continue
        if len(part) < 10:
            continue

        numbers = extract_all_numbers(part)
        stype = "定量" if numbers else "定性"

        statements.append({
            "类型": stype,
            "表述内容": part,
            "状态": "✗ 未找到",
            "出处": "—",
            "原文片段": "所有参照文档均未出现此表述/数据",
            "匹配关键词": "",
            "命中数字": numbers,
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
    """
    从表述文本中提取用于搜索的关键词。
    按优先级返回：专有名词 > 数字+单位 > 动作短语 > 中文片段。
    每条关键词限制在 3~40 字之间，避免过短误匹配和过长无效串。
    """
    keywords = []

    # 第一优先：引号内的专有名词
    quoted = re.findall(r'[「""《》\u201c\u201d]([^「""《》\u201c\u201d]{2,})[」""《》\u201c\u201d]', text)
    keywords.extend(q.strip() for q in quoted if 3 <= len(q.strip()) <= 40)

    # 第二优先：数字+单位组合
    num_matches = re.findall(r'[\d.]+' + _NUMERIC_SUFFIXES, text)
    keywords.extend(n.strip() for n in num_matches if 3 <= len(n.strip()) <= 40)

    # 第三优先：特定动作+名词短语（覆盖常见的成就性动词）
    action_verbs = r'(?:入选|获批|荣获|获得|新增|组建|成立|牵头|实施|推进|完成|突破|增长|提升|达到|建成|上线|发布|通过|授牌|签约|设立|启用|开创)'
    action_phrases = re.findall(action_verbs + r'[\u4e00-\u9fff]{2,20}', text)
    keywords.extend(a.strip() for a in action_phrases if 3 <= len(a.strip()) <= 40)

    # 第四优先：4~40 字连续中文（核心名词）
    long_chinese = re.findall(r'[\u4e00-\u9fff]{4,40}', text)
    # 只取前 5 个最长的，避免爆量
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
    """
    references: list of (txt_path, txt_content)
    返回:
      index: { short_name: (txt_path, txt_content) }  保留完整引用
    """
    index = {}
    for txt_path, txt_content in references:
        short_name = os.path.splitext(os.path.basename(txt_path))[0][:40]
        index[short_name] = (txt_path, txt_content)
    return index


def search_in_reference(texts, references):
    """
    在参考文档中全文检索每个表述。
    - 记录匹配使用的关键词和出处
    - 对含多数字的表述，检查每个数字是否都能独立找到
    - 对含增长率的表述，检查增长率是否出现在合理语境中
    - 对过短/过泛的关键词发出警告
    
    references: list of (txt_path, txt_content)
    """
    index = build_ref_index(references)

    for item in texts:
        query = item["表述内容"]
        keywords = extract_keywords(query)
        all_numbers = item.get("命中数字", [])

        found_any = False
        matched_kw = ""
        matched_ref = ""

        for kw in keywords:
            for ref_name, (ref_path, ref_content) in index.items():
                if kw in ref_content:
                    snippet = get_context_snippet(ref_content, kw, 80)
                    if not found_any:
                        item["状态"] = "✓ 已确认"
                        item["出处"] = ref_name
                        item["原文片段"] = f"{ref_name}: ...{snippet}..."
                        item["匹配关键词"] = kw
                        item["匹配上下文简述"] = snippet[:120]
                        matched_kw = kw
                        matched_ref = ref_name
                        found_any = True
                    else:
                        item["出处"] += "; " + ref_name
                    break
            if found_any:
                break

        if not found_any:
            continue

        # ── 反向验证标记 ──
        warnings = []

        # 检查：数字是否每个都能在参考文档中找到
        if all_numbers:
            missing = [n for n in all_numbers
                       if not any(n in ref_content for _, ref_content in references)]
            if missing:
                warnings.append(f"部分数字未在参考文档中找到: {missing}")

        # 检查：增长率数字是否存在且出现在合理语境中
        growth_keywords = re.findall(r'增长|增幅|提高|提升|同比|突破|跃升|攀升|翻番', query)
        if growth_keywords:
            pct_nums = [n for n in all_numbers if n.endswith('%')]
            for pn in pct_nums:
                in_growth_ctx = False
                for _, ref_content in references:
                    if pn in ref_content:
                        idx = ref_content.find(pn)
                        ctx = ref_content[max(0, idx-25):idx+25]
                        if any(gk in ctx for gk in growth_keywords):
                            in_growth_ctx = True
                            break
                if not in_growth_ctx:
                    warnings.append(f"增长率 {pn} 未在增长语境中出现，需人工核实")

        # 检查：关键词过短（≤3字）→ 误判风险高
        if len(matched_kw) <= 3:
            warnings.append(f"匹配关键词过短({len(matched_kw)}字)，可能为误匹配，需人工确认上下文")

        # 检查：关键词在参考文档中出现次数过多（>20次）→ 过于通用
        total_occurrences = sum(ref_content.count(matched_kw) for _, ref_content in references)
        if total_occurrences > 20:
            warnings.append(
                f"匹配关键词「{matched_kw}」在参考文档中出现{total_occurrences}次，过于通用，"
                f"请确认匹配到的上下文是否与目标表述一致"
            )

        item["反向验证警告"] = "; ".join(warnings) if warnings else ""


# ── Excel 生成 ─────────────────────────────────────────────

def generate_excel(items, output_path):
    """
    生成4工作表的Excel核对清单：
    Sheet 1: 全部核对结果（带颜色标注和完整信息列）
    Sheet 2: 已确认表述
    Sheet 3: 待核实表述
    Sheet 4: 反向验证重点关注（第三轮复查用）
    """
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
        if "✓" in status:
            cell.fill = green
        elif "数据不一致" in status:
            cell.fill = orange
        elif "△" in status:
            cell.fill = yellow
        elif "未找到" in status:
            cell.fill = red

    # ── Sheet 1: 全部核对结果 ──
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

    # ── Sheet 2: 已确认 ──
    ws2 = wb.create_sheet("已确认表述")
    ws2.append(["序号", "类型", "表述内容", "出处", "原文片段", "反向验证警告"])
    for i, item in enumerate((x for x in items if "✓" in x.get("状态", "")), 1):
        ws2.append([i, item["类型"], item["表述内容"], item["出处"], item["原文片段"],
                    item.get("反向验证警告", "")])
    _auto_width(ws2, {'A': 6, 'B': 8, 'C': 55, 'D': 40, 'E': 90, 'F': 50})

    # ── Sheet 3: 待核实 ──
    ws3 = wb.create_sheet("待核实表述")
    ws3.append(["序号", "类型", "表述内容", "核对状态", "备注说明"])
    for i, item in enumerate((x for x in items if "未找到" in x.get("状态", "") or "△" in x.get("状态", "")), 1):
        ws3.append([i, item["类型"], item["表述内容"], item["状态"], item["原文片段"]])
        _color_cell(ws3, i+1, 4, item["状态"])
    _auto_width(ws3, {'A': 6, 'B': 8, 'C': 55, 'D': 18, 'E': 90})

    # ── Sheet 4: 反向验证重点关注 ──
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

    wb.save(output_path)
    print(f"\nExcel已保存: {output_path}")
    print(f"  工作表: {wb.sheetnames}")
    print(f"  其中「反向验证重点关注」工作表包含 {len(suspicious)} 条需第三轮复查的条目")


def _build_advice(warnings):
    """根据警告内容生成人工核查建议"""
    parts = []
    if "过短" in warnings:
        parts.append("① 匹配关键词太短，请读取原文片段确认是否真的对应同一表述")
    if "出现" in warnings and "次" in warnings:
        parts.append("② 关键词过于通用，请检查上下文是否与目标表述一致")
    if "数字未找到" in warnings:
        parts.append("③ 部分数字无出处，请核实这些数字的来源")
    if "增长率" in warnings:
        parts.append("④ 增长率数字缺少对应语境，请确认增长率是否准确")
    return "; ".join(parts) if parts else "请逐字比对目标表述与原文片段是否一致"


# ── 统计 ──────────────────────────────────────────────────

def print_summary(statements, stage="第一轮自动核对"):
    """打印汇总统计"""
    confirmed  = len([s for s in statements if "✓" in s["状态"]])
    not_found  = len([s for s in statements if "未找到" in s["状态"]])
    partial    = len(statements) - confirmed - not_found
    suspicious = len([s for s in statements if s.get("反向验证警告", "")])

    print(f"\n{'='*60}")
    print(f"{stage} 完成")
    print(f"{'='*60}")
    print(f"总表述数:     {len(statements)}")
    print(f"  ✓ 已确认:   {confirmed}")
    print(f"  △ 需核实:   {partial}")
    print(f"  ✗ 未找到:   {not_found}")
    if suspicious:
        print(f"{'='*60}")
        print(f"⚠ 反向验证警告: {suspicious} 条「已确认」条目存在潜在问题")
        print(f"  请在 Excel「反向验证重点关注」工作表中逐条复查")
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
    print("Step 1: 转换参考文档 docx/doc → txt")
    print("=" * 60)
    ref_files = find_doc_files(ref_dir)
    print(f"找到 {len(ref_files)} 个参考文档")

    ref_texts = {}
    for doc in ref_files:
        txt_path = convert_docx_to_txt(doc, txt_dir)
        with open(txt_path, 'r', encoding='utf-8') as f:
            ref_texts[txt_path] = f.read()
        print(f"  → {os.path.basename(doc)}")

    # Step 2: 提取表述
    print("\n" + "=" * 60)
    print("Step 2: 提取目标文档表述")
    print("=" * 60)
    main_txt = convert_docx_to_txt(main_docx, txt_dir)
    statements = extract_statements(main_txt)
    print(f"提取到 {len(statements)} 条表述（定性+定量）")

    # Step 3: 检索 + 反向验证
    print("\n" + "=" * 60)
    print("Step 3: 全文检索 + 反向验证")
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
    print("Step 5: 生成Excel核对清单")
    print("=" * 60)
    if not output_xlsx.startswith("/"):
        output_xlsx = os.path.join(os.path.dirname(main_docx) or ".", output_xlsx)
    generate_excel(statements, output_xlsx)

    print_summary(statements)


def cmd_regenerate():
    """从已有的 JSON 重新生成 Excel（用于第二轮/第三轮人工复查后）"""
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
