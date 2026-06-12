#!/usr/bin/env python3
"""
文档表述准确性核对脚本。
工作流程：
1. 将reference_dir下的所有docx文件转换为txt（使用pandoc）
2. 将宣传材料docx转换为txt并提取所有定性和定量表述
3. 在全部参考文档中全文检索每个表述的出处
4. 标记状态并输出到Excel

用法: python3 doc_fact_check.py <宣传材料.docx> <参考文档目录> [输出Excel路径]
"""

import sys
import os
import json
import glob
import re
import subprocess
from pathlib import Path


def convert_docx_to_txt(docx_path, txt_dir):
    """使用pandoc将docx文件转换为txt"""
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


def extract_statements(main_txt_path):
    """
    从宣传材料txt中提取所有定性和定量表述。
    按段落拆分，识别含具体数据/指标/成就的表述。
    """
    with open(main_txt_path, 'r', encoding='utf-8') as f:
        content = f.read()

    statements = []
    # 按句号、分号、换行拆分
    raw_parts = re.split(r'[。；\n]+', content)
    raw_parts = [p.strip() for p in raw_parts if len(p.strip()) > 10]

    for part in raw_parts:
        # 跳过纯标题、纯标点、目录行
        if re.match(r'^[\s\d\.、）\)]+$', part):
            continue
        if len(part) < 10:
            continue

        stype = "定量" if re.search(r'[\d.]+%|[\d.]+万|[\d.]+亿|[\d.]+篇|[\d.]+项|[\d.]+个|[\d.]+人次|[\d.]+所|[\d.]+支|[\d.]+部', part) else "定性"
        statements.append({
            "类型": stype,
            "表述内容": part,
            "状态": "✗ 未找到",
            "出处": "—",
            "原文片段": "所有参照文档均未出现此表述/数据"
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


def search_in_reference(texts, references):
    """在参考文档中全文检索每个表述"""
    for item in texts:
        query = item["表述内容"]
        # 生成搜索关键词：取表述中的关键部分
        keywords = extract_keywords(query)

        found_any = False
        for kw in keywords:
            for ref_path, ref_content in references:
                if kw in ref_content:
                    idx = ref_content.find(kw)
                    start = max(0, idx - 50)
                    end = min(len(ref_content), idx + len(kw) + 150)
                    snippet = ref_content[start:end].replace('\n', ' ')
                    ref_name = os.path.splitext(os.path.basename(ref_path))[0][:30]

                    if not found_any:
                        item["状态"] = "✓ 已确认"
                        item["出处"] = ref_name
                        item["原文片段"] = f"{ref_name}: ...{snippet}..."
                        found_any = True
                    else:
                        item["出处"] += "; " + ref_name
                    break
            if found_any:
                break
    return texts


def extract_keywords(text):
    """从表述文本中提取用于搜索的关键词"""
    keywords = []
    # 提取数字+单位组合
    num_matches = re.findall(r'[\d.]+[万亿千百个项篇章人次所支部门%倍]+', text)
    keywords.extend(num_matches)

    # 提取专有名词（引号内的内容）
    quoted = re.findall(r'[「""《》]([^「""《》]+)[」""《》]', text)
    keywords.extend(quoted)

    # 提取长于4个字的连续中文
    long_chinese = re.findall(r'[\u4e00-\u9fff]{6,}', text)
    keywords.extend(long_chinese[:3])

    # 去重并优先较长的关键词
    keywords = sorted(set(keywords), key=len, reverse=True)
    return keywords[:5] if keywords else [text[:20]]


def generate_excel(items, output_path):
    """生成带颜色标注的Excel核对清单"""
    try:
        from openpyxl import Workbook
        from openpyxl.styles import PatternFill
    except ImportError:
        print("请先安装 openpyxl: pip install openpyxl")
        return

    wb = Workbook()

    green_fill = PatternFill(start_color="90EE90", end_color="90EE90", fill_type="solid")
    yellow_fill = PatternFill(start_color="FFFF00", end_color="FFFF00", fill_type="solid")
    red_fill = PatternFill(start_color="FF6B6B", end_color="FF6B6B", fill_type="solid")

    # Sheet 1: 全部核对结果
    ws1 = wb.active
    ws1.title = "全部核对结果"
    ws1.append(["序号", "类型", "表述内容", "核对状态", "出处", "原文片段"])

    for i, item in enumerate(items, 1):
        row = [i, item["类型"], item["表述内容"], item["状态"], item["出处"], item["原文片段"]]
        ws1.append(row)
        status = item["状态"]
        if "✓" in status:
            ws1.cell(row=i+1, column=4).fill = green_fill
        elif "△" in status or "不一致" in status:
            ws1.cell(row=i+1, column=4).fill = yellow_fill
        elif "未找到" in status:
            ws1.cell(row=i+1, column=4).fill = red_fill

    ws1.column_dimensions['A'].width = 6
    ws1.column_dimensions['B'].width = 8
    ws1.column_dimensions['C'].width = 55
    ws1.column_dimensions['D'].width = 15
    ws1.column_dimensions['E'].width = 30
    ws1.column_dimensions['F'].width = 70

    # Sheet 2: 已确认
    ws2 = wb.create_sheet("已确认表述")
    ws2.append(["序号", "类型", "表述内容", "出处", "原文片段"])
    confirmed = [item for item in items if "✓" in item.get("状态", "")]
    for i, item in enumerate(confirmed, 1):
        ws2.append([i, item["类型"], item["表述内容"], item["出处"], item["原文片段"]])
    ws2.column_dimensions['A'].width = 6
    ws2.column_dimensions['B'].width = 8
    ws2.column_dimensions['C'].width = 55
    ws2.column_dimensions['D'].width = 30
    ws2.column_dimensions['E'].width = 70

    # Sheet 3: 待核实
    ws3 = wb.create_sheet("待核实表述")
    ws3.append(["序号", "类型", "表述内容", "备注"])
    not_found = [item for item in items if "未找到" in item.get("状态", "") or "不一致" in item.get("状态", "")]
    for i, item in enumerate(not_found, 1):
        note = item.get("原文片段", "需补充出处")
        ws3.append([i, item["类型"], item["表述内容"], note])
        ws3.cell(row=i+1, column=4).fill = red_fill
    ws3.column_dimensions['A'].width = 6
    ws3.column_dimensions['B'].width = 8
    ws3.column_dimensions['C'].width = 55
    ws3.column_dimensions['D'].width = 50

    wb.save(output_path)
    print(f"\nExcel已保存: {output_path}")


def main():
    if len(sys.argv) < 3:
        print("用法: python3 doc_fact_check.py <宣传材料.docx> <参考文档目录> [输出Excel路径]")
        sys.exit(1)

    main_docx = sys.argv[1]
    ref_dir = sys.argv[2]
    output_xlsx = sys.argv[3] if len(sys.argv) > 3 else "核对清单.xlsx"

    txt_dir = os.path.join(os.path.dirname(main_docx) or ".", "txt_output")
    os.makedirs(txt_dir, exist_ok=True)

    # Step 1: 转换参考文档
    print("=" * 60)
    print("Step 1: 转换参考文档docx -> txt")
    print("=" * 60)
    ref_docx_files = glob.glob(os.path.join(ref_dir, "*.docx"))
    if not ref_docx_files:
        # 也搜索子目录
        ref_docx_files = glob.glob(os.path.join(ref_dir, "**/*.docx"), recursive=True)
    print(f"找到 {len(ref_docx_files)} 个参考文档")

    ref_texts = {}
    for docx in ref_docx_files:
        txt_path = convert_docx_to_txt(docx, txt_dir)
        with open(txt_path, 'r', encoding='utf-8') as f:
            ref_texts[txt_path] = f.read()
        print(f"  → {os.path.basename(docx)}")

    # Step 2: 转换宣传材料并提取表述
    print("\n" + "=" * 60)
    print("Step 2: 提取宣传材料表述")
    print("=" * 60)
    main_txt = convert_docx_to_txt(main_docx, txt_dir)
    statements = extract_statements(main_txt)
    print(f"提取到 {len(statements)} 条表述（定性+定量）")

    # Step 3: 全文检索
    print("\n" + "=" * 60)
    print("Step 3: 在参考文档中全文检索每个表述")
    print("=" * 60)
    ref_list = list(ref_texts.items())
    statements = search_in_reference(statements, ref_list)

    confirmed = len([s for s in statements if "✓" in s["状态"]])
    not_found = len([s for s in statements if "未找到" in s["状态"]])
    partial = len(statements) - confirmed - not_found
    print(f"已确认: {confirmed}项, 部分匹配: {partial}项, 未找到: {not_found}项")

    # Step 4: 保存中间JSON和Excel
    print("\n" + "=" * 60)
    print("Step 4: 生成Excel核对清单")
    print("=" * 60)
    json_path = os.path.join(txt_dir, "checklist_result.json")
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(statements, f, ensure_ascii=False, indent=2)
    print(f"中间结果已保存: {json_path}")

    if not output_xlsx.startswith("/"):
        output_xlsx = os.path.join(os.path.dirname(main_docx) or ".", output_xlsx)
    generate_excel(statements, output_xlsx)


if __name__ == "__main__":
    main()
