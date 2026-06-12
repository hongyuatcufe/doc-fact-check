---
name: doc-fact-check
description: >
  文档表述准确性核对。将宣传材料中的每条定性和定量表述与参考文档逐一比对，
  标记确认/不一致/未找到状态，生成带颜色标注的Excel核对清单。
  Use when the user needs to verify a promotional/summary document against
  reference documents, fact-check statements, or generate a compliance checklist.
---

# 文档表述准确性核对

## Overview

将宣传材料（如总结、汇报、新闻稿）中的每条表述与参考文档（规划、总结、专项报告等）
逐一比对，找出每项表述的原始出处，标记状态，输出 Excel 核对清单。

## 前置条件

- 已安装 pandoc（`brew install pandoc` 或 `apt install pandoc`）
- 已安装 openpyxl：`pip install openpyxl`

## 工作流程

### 第一步：确认文件结构

1. 确认宣传材料路径（待核对的 .docx 文件）
2. 确认参考文档目录（包含所有参考 .docx 文件，支持子目录）
3. 确认输出 Excel 路径

### 第二步：执行自动核对脚本

运行绑定脚本完成批量转换和初步检索：

```bash
python3 scripts/doc_fact_check.py "宣传材料.docx" "参考文档目录/" "核对清单.xlsx"
```

脚本会自动完成：
- 将参考文档全部转换为 txt（使用 pandoc）
- 提取宣传材料中的定性和定量表述
- 在参考文档中进行关键词全文检索
- 输出中间 JSON 和 Excel 核对清单

### 第三步：人工复查未找到的项目

自动检索后仍会有一部分表述标记为"✗ 未找到"。此时需逐条人工复查：

1. 读取 `txt_output/checklist_result.json` 获取所有未找到项目
2. 对每个未找到项目，在参考文档 txt 文件中用更宽泛的关键词检索：
   - 去掉修饰词，保留核心名词
   - 搜索数字部分（如百分比、数量）
   - 搜索专有名词和项目名称
   - 将长句拆分为短关键词组合搜索

3. 更新 JSON 中的状态和出处

### 第四步：标记不一致项目

当参考文档中有近似但不完全一致的表述时（如数字不同、名称不同），
标记为"△ 数据不一致"，在原文片段中注明差异。

常见标记：
- **✓ 已确认** - 参考文档中有完全相同或高度一致的表述
- **△ 部分匹配** - 部分内容有出处但表述不完整
- **△ 数据不一致** - 数据或表述与原始文档有差异（如49篇vs50篇）
- **✗ 未找到** - 所有参照文档均未出现该表述

### 第五步：重新生成 Excel

更新 JSON 后重新生成 Excel：

```python
import json
from openpyxl import Workbook
from openpyxl.styles import PatternFill

with open('txt_output/checklist_result.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

wb = Workbook()
green = PatternFill(start_color="90EE90", end_color="90EE90", fill_type="solid")
yellow = PatternFill(start_color="FFFF00", end_color="FFFF00", fill_type="solid")
red = PatternFill(start_color="FF6B6B", end_color="FF6B6B", fill_type="solid")

# Sheet 1: 全部
ws1 = wb.active
ws1.title = "全部核对结果"
ws1.append(["序号", "类型", "表述内容", "核对状态", "出处", "原文片段"])
for i, item in enumerate(data, 1):
    ws1.append([i, item["类型"], item["表述内容"], item["状态"], item["出处"], item["原文片段"]])
    s = item["状态"]
    if "✓" in s: ws1.cell(row=i+1, column=4).fill = green
    elif "△" in s or "不一致" in s: ws1.cell(row=i+1, column=4).fill = yellow
    elif "未找到" in s: ws1.cell(row=i+1, column=4).fill = red

# Sheet 2: 已确认
ws2 = wb.create_sheet("已确认表述")
ws2.append(["序号", "类型", "表述内容", "出处", "原文片段"])
for i, item in enumerate([x for x in data if "✓" in x["状态"]], 1):
    ws2.append([i, item["类型"], item["表述内容"], item["出处"], item["原文片段"]])

# Sheet 3: 待核实
ws3 = wb.create_sheet("待核实表述")
ws3.append(["序号", "类型", "表述内容", "备注"])
for i, item in enumerate([x for x in data if "未找到" in x["状态"] or "不一致" in x["状态"]], 1):
    ws3.append([i, item["类型"], item["表述内容"], item["原文片段"]])

wb.save("核对清单.xlsx")
```

### 第六步：最终汇总

生成最终统计报告：

```
最终核对结果汇总：
✓ 已确认：X项
△ 部分/不一致：X项
✗ 未找到：X项

未找到项目清单：[列出]
不一致项目及差异说明：[列出]
```

## 人工复查技巧

对未找到的项目，尝试以下搜索策略：

1. **主关键词搜索**：提取核心名词搜索（如"强国行"、"育人共同体"）
2. **数字搜索**：直接搜索数字部分（如"91%"、"42.87"、"2006"）
3. **分段搜索**：将长句拆成2-3个短语分别搜索
4. **近义词替换**：如"横向课题"可能为"横向科研项目"或"横向项目"
5. **全量浏览**：对重点参考文档直接 grep 相关章节

## 注意事项

- 中文引号（""）在 shell 中会导致路径问题，使用 Python glob 替代
- pandoc 转换大型 docx 可能需要较长时间，设置合理的超时
- 表述提取可能不完整，需要在人工复查阶段补充遗漏
- 宣传材料和参考文档的 docx 如有修订标记，需先接受修订
