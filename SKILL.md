---
name: doc-fact-check
description: >
  文档表述准确性核对。将目标文档中的每条定性与定量表述与参考文档逐一比对，
  通过自动初查→人工复检→反向验证三轮闭环，标记确认/不一致/未找到等状态，
  生成带颜色标注的Excel核对清单。
  Use when the user needs to verify a summary/promotional document against
  reference documents, fact-check statements, or generate a compliance checklist.
---

# 文档表述准确性核对（三轮复核法）

## Overview

将一份**目标文档**（如宣传稿、总结、汇报、新闻稿）中的每条定性和定量表述，
与一组**参考文档**（如规划、总结、专项报告）逐一比对，通过三轮复核确保准确。

```
第一轮：自动关键词匹配 ──→ 初步结果（含误判风险）
   │
   ▼
第二轮：人工复查「未找到」项 ──→ 找回遗漏匹配
   │
   ▼
第三轮：反向验证「已确认」项 ──→ 揪出自动误判
   │
   ▼
最终结果
```

**适用场景：**
- 宣传材料事实核对
- 汇报材料数据核实
- 新闻稿表述出处查找
- 合规审查中的文档一致性检查

## 前置条件

- 已安装 pandoc（`brew install pandoc` 或 `apt install pandoc`）
- 已安装 openpyxl：`pip install openpyxl`

---

## 工作流程

### 第一步：确认文件结构

1. 确认目标文档路径（待核对的 .docx 文件）
2. 确认参考文档目录（包含所有参考 .docx/.doc 文件，支持子目录）
3. 确认输出 Excel 路径

---

### 第二步：第一轮 ── 自动核对

运行脚本完成批量转换和关键词全文检索：

```bash
python3 scripts/doc_fact_check.py "目标文档.docx" "参考文档目录/" "核对清单.xlsx"
```

脚本输出：
- `txt_output/` 目录下的参考文档 txt 和目标文档 txt
- `txt_output/checklist_result.json` — 第一轮初步结果
- `txt_output/checklist_reverse_check.json` — 反向验证辅助报告
- `核对清单.xlsx` — 4个工作表

**⚠️ 自动匹配的局限（需要后续两轮人工复核）：**
1. 关键词碰巧出现在无关段落（如"100万"→网络设备参数而非科研经费）
2. 不同项目/机构因共有词被混为一谈
3. 基数数字匹配但增长率未能独立验证
4. 统计口径不一致导致张冠李戴（如"省部级平台"≠"国家级平台"）

---

### 第三步：第二轮 ── 复查「✗ 未找到」项

逐条人工复查被标记为"✗ 未找到"的表述。

1. 读取 `txt_output/checklist_result.json` 获取全部未找到项目
2. 在参考文档 txt 文件中用更宽泛的策略检索：
   - **分段搜索**：将长句拆成 2~3 个独立关键词分别搜索
   - **核心名词搜索**：去掉修饰词，保留核心名词
   - **数字搜索**：直接搜数字部分（如"91%"、"42.87"）
   - **近义词替换**：如"课题"↔"项目"；"平台"↔"中心/基地"
   - **全量浏览**：对最重要的参考文档（如总规划）直接 grep 相关章节
3. 找到出处后更新 JSON 中的 `状态`、`出处`、`原文片段` 字段

---

### 第四步：第三轮 ── 反向验证「✓ 已确认」项

**这是最容易遗漏的环节。** 自动标记的"✓ 已确认"条目中可能隐藏误判。
必须逐一对每条已确认条目执行以下六项检查。

#### 检查①：上下文语义一致性

匹配到的原文片段，是否真的在说同一件事？
- ✅ 通过：原文与目标表述指向同一概念
- ❌ 误判：关键词仅碰巧出现在无关上下文中

#### 检查②：专有名词精确性

目标文档中的项目/机构/平台名称，与参考文档中的名称**逐字比对**。
- ✅ 通过：名称完全一致
- ❌ 不一致：存在任何差异

#### 检查③：数值精确性

目标文档中的**每一个**数字逐项核对是否在参考文档中有完全一致的出处。
- ✅ 通过：所有数字与参考文档完全一致
- ⚠️ 部分：部分数字有出处、部分缺失
- ❌ 不一致：数字不一致

#### 检查④：增长率/派生数据独立验证

基数匹配 ≠ 增长率匹配。目标文档中的增长率必须在参考文档中独立找到。
- ✅ 通过：增长率数字在参考文档中有对应的增长语境
- ❌ 不一致：增长率数字在参考文档中存在但值不同
- ❌ 缺失：增长率数字在参考文档中完全不存在

#### 检查⑤：复合数据完整性

当一条表述包含多个并列子项（"A、B和C"）时，每个子项是否都有独立出处？
- ✅ 通过：所有子项均可独立验证
- ⚠️ 部分：部分子项有出处、部分无

#### 检查⑥：口径一致性

统计范围、单位和分级口径是否一致？
- 如"AA类及以上"≠"AA类"
- 如"到账经费增长70.4%"≠"科研事业收入4.43亿"
- 如"教学育人平台"≠"科研平台"

#### 反向验证结果操作

根据六项检查结果修改 JSON 中的 `状态`：

| 修改后状态 | 触发条件 |
|-----------|---------|
| 保持「✓ 已确认」 | 六项检查全部通过 |
| 「△ 数据不一致」 | 检查③/④/⑤/⑥ 发现数据差异 |
| 「△ 部分匹配」 | 数据有出处但不完整或含概括性内容 |
| 「✗ 未找到」 | 检查① 发现完全误判，实际无对应出处 |

---

### 第五步：标记不一致项目

当目标文档与参考文档有近似但不完全一致的表述时，标记为"△ 数据不一致"
并在 `原文片段` 中注明差异。

四个标准标记：
- **✓ 已确认** — 参考文档中有完全相同或高度一致的表述
- **△ 部分匹配** — 部分有出处但不完整
- **△ 数据不一致** — 数据/名称有差异
- **✗ 未找到** — 所有参考文档均未出现

---

### 第六步：重新生成 Excel

人工更新 JSON 后，使用 `--regenerate` 重新生成 Excel（无需重新转换文档）：

```bash
python3 scripts/doc_fact_check.py --regenerate txt_output/checklist_result.json "核对清单.xlsx"
```

如不需要自定义，也可以在第六步中直接用以下 Python 代码生成：

```python
import json
from openpyxl import Workbook
from openpyxl.styles import PatternFill

with open('txt_output/checklist_result.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

wb = Workbook()

green  = PatternFill(start_color="90EE90", end_color="90EE90", fill_type="solid")
yellow = PatternFill(start_color="FFFF00", end_color="FFFF00", fill_type="solid")
red    = PatternFill(start_color="FF6B6B", end_color="FF6B6B", fill_type="solid")
orange = PatternFill(start_color="FFA500", end_color="FFA500", fill_type="solid")

def fill_cell(ws, row, col, status):
    cell = ws.cell(row=row, column=col)
    if "✓" in status:         cell.fill = green
    elif "数据不一致" in status: cell.fill = orange
    elif "△" in status:       cell.fill = yellow
    elif "未找到" in status:   cell.fill = red

# Sheet 1: 全部
ws1 = wb.active; ws1.title = "全部核对结果"
ws1.append(["序号", "类型", "表述内容", "核对状态", "出处", "原文片段"])
for i, item in enumerate(data, 1):
    ws1.append([i, item["类型"], item["表述内容"], item["状态"], item["出处"], item["原文片段"]])
    fill_cell(ws1, i+1, 4, item["状态"])

# Sheet 2: 已确认
ws2 = wb.create_sheet("已确认表述")
ws2.append(["序号", "类型", "表述内容", "出处", "原文片段"])
for i, item in enumerate([x for x in data if "✓" in x["状态"]], 1):
    ws2.append([i, item["类型"], item["表述内容"], item["出处"], item["原文片段"]])

# Sheet 3: 待核实
ws3 = wb.create_sheet("待核实表述")
ws3.append(["序号", "类型", "表述内容", "核对状态", "备注"])
for i, item in enumerate([x for x in data if "未找到" in x["状态"] or "△" in x["状态"]], 1):
    ws3.append([i, item["类型"], item["表述内容"], item["状态"], item["原文片段"]])
    fill_cell(ws3, i+1, 4, item["状态"])

# Sheet 4: 数据不一致重点条目
ws4 = wb.create_sheet("数据不一致重点条目")
ws4.append(["序号", "类型", "表述内容", "差异说明"])
for i, item in enumerate([x for x in data if "数据不一致" in x["状态"]], 1):
    ws4.append([i, item["类型"], item["表述内容"], item["原文片段"]])

ws1.column_dimensions['C'].width = 55
ws1.column_dimensions['D'].width = 18
ws1.column_dimensions['E'].width = 40
ws1.column_dimensions['F'].width = 90

wb.save("核对清单.xlsx")
```

---

### 第七步：最终汇总

输出统计报告：

```
最终核对结果汇总：
✓ 已确认：      XX 项
△ 部分匹配：    XX 项
△ 数据不一致：  XX 项
✗ 未找到：      XX 项
────────────────────
合计：          XX 项

=== 数据不一致重点条目（需优先处理）===
1. [名称/数字]  目标文档"XXX" vs 参考文档"YYY"
   差异说明：...
...

=== 未找到项目清单（需向相关部门核实出处）===
1. ...
2. ...
```

---

## 人工复查技巧

### 第二轮（复查未找到项）

| 策略 | 说明 | 示例 |
|------|------|------|
| 分段搜索 | 将长句拆成2-3个独立的短关键词 | 复杂长句 → 分别搜索每个独立概念 |
| 核心名词搜索 | 去掉修饰词，仅保留核心名词 | "XX大学XX研究中心" → "XX研究" |
| 数字搜索 | 直接搜表述中的数字 | 搜索"91%"、"2006"、"42.87" |
| 近义词替换 | 用参考文档中可能使用的词替换 | 科研项目 ↔ 科研课题；平台 ↔ 中心/基地 |
| 全量浏览 | 对最权威的参考文档全文 grep | 直接 open 总规划 txt，grep 章节标题 |

### 第三轮（反向验证已确认项）

| 最危险信号 | 说明 |
|-----------|------|
| 数字搜索返回0结果但已标"✓" | **极可能是误匹配**，关键词匹配到的是文字而非数字 |
| 关键词过于通用 | 如"平台""建设""水平"等词→必须检查上下文 |
| 一条表述含 2+ 数字 | 每个数字都要独立验证 |
| 表述中有增长率/增幅 | 增长率必须去参考文档中单独找到，基数对≠增长率对 |
| 名称/机构/项目名 | 与参考文档中找到的名称逐字比对 |

---

## 注意事项

- 中文引号（`""`）在 shell 中会导致路径问题，用 Python glob 或先复制为简单文件名
- pandoc 转换大型 docx 可能需要较长时间，脚本默认超时 120 秒
- 目标文档和参考文档的 docx 如有修订标记（Track Changes），需先接受修订后转换
- 脚本的自动匹配基于关键词检索，**不要仅凭第一轮结果下结论**，必须走完三轮
- `--regenerate` 模式假设 txt 文件已存在，如需重新转换请删除 `txt_output/` 后重跑
- `.doc` 格式文件（旧版 Word）目前已支持，但 pandoc 转换效果可能不如 `.docx`
