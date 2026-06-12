# doc-fact-check

## 中文

文档表述准确性核对工具 —— 将目标文档中的每条定性和定量表述与参考文档逐一比对，
通过**自动初查 → 人工复检 → 反向验证**三轮闭环，生成带颜色标注的 Excel 核对清单。

### 特性

- 🏷️ 自动将目标文档拆解为独立的定性和定量表述
- 🔍 在参考文档中全文检索每条表述的出处，并辅以反向验证警告
- 🏷️ 标记每条表述的状态：已确认 / 部分匹配 / 数据不一致 / 未找到
- 📊 输出带颜色标注的 Excel 核对清单（4 个工作表）
- 🔄 支持 `--regenerate` 模式：人工修改 JSON 后直接重生成 Excel，无需重新转换
- 📋 自动生成反向验证报告，标记潜在误匹配供第三轮重点关注

### 三轮复核法

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

### 依赖

```bash
brew install pandoc          # macOS
pip install openpyxl
```

### 使用方式

#### 在 Qoder CLI 中调用

在 Qoder 会话中直接说明需求即可，此 Skill 会自动触发。例如：

> 帮我核验这份工作总结中的表述是否都能在参考文档里找到出处

#### 命令行

```bash
# 第一轮：自动核对
python3 scripts/doc_fact_check.py "目标文档.docx" "参考文档目录/" "核对清单.xlsx"

# 人工复查后重生成 Excel
python3 scripts/doc_fact_check.py --regenerate txt_output/checklist_result.json "核对清单.xlsx"
```

### 核对标记说明

| 标记 | 含义 |
|------|------|
| ✓ 已确认 | 参考文档中有完全相同或高度一致的表述 |
| △ 部分匹配 | 部分内容有出处但表述不完整 |
| △ 数据不一致 | 数据或表述与原始文档有差异 |
| ✗ 未找到 | 所有参照文档均未出现该表述 |

### Excel 输出说明

| 工作表 | 内容 |
|--------|------|
| 全部核对结果 | 所有表述的完整核对状态、出处、原文片段、命中数字 |
| 已确认表述 | 仅列出 ✓ 已确认 的条目 |
| 待核实表述 | 未找到 或 需进一步确认 的条目 |
| 反向验证重点关注 | 存在误判风险的条目及人工核查建议 |

### 反向验证：自动匹配的常见误判

自动关键词匹配可能在以下场景产生误判，第三轮反向验证专门排查：

1. 关键词碰巧出现在无关段落（如"100万"→网络设备参数而非科研经费）
2. 不同项目/机构因共有词被混为一谈
3. 基数数字匹配但增长率未能独立验证
4. 统计口径不一致导致张冠李戴

### 目录结构

```
doc-fact-check/
├── SKILL.md                      # Skill 定义文件
├── scripts/
│   └── doc_fact_check.py         # 核对脚本（v2 三轮复核增强版）
└── README.md
```

---

## English

A document fact-checking tool that verifies each qualitative and quantitative statement
in a target document against reference documents through a **three-round review process**
(automated search → manual re-check → reverse validation), producing a color-coded Excel checklist.

### Features

- 🏷️ Automatically decomposes target documents into individual qualitative/quantitative statements
- 🔍 Full-text searches each statement's source across reference documents with reverse validation warnings
- 🏷️ Status markers: Confirmed / Partial Match / Data Mismatch / Not Found
- 📊 Color-coded Excel checklist with 4 worksheets
- 🔄 `--regenerate` mode for re-generating Excel from manually corrected JSON
- 📋 Auto-generated reverse-validation report highlighting potential false positives

### Three-Round Review

```
Round 1: Automated keyword matching ──→ Preliminary results (risk of false positives)
   │
   ▼
Round 2: Manual review of "Not Found" items ──→ Recover missed matches
   │
   ▼
Round 3: Reverse validation of "Confirmed" items ──→ Catch false positives
   │
   ▼
Final results
```

### Dependencies

```bash
brew install pandoc          # macOS
pip install openpyxl
```

### Usage

#### From Qoder CLI

Describe your fact-checking needs in a Qoder session — the skill triggers automatically:

> Help me verify whether all statements in this document can be traced back to the reference materials.

#### Command Line

```bash
# Round 1: Automated check
python3 scripts/doc_fact_check.py "target.docx" "reference_docs/" "checklist.xlsx"

# Re-generate Excel after manual review
python3 scripts/doc_fact_check.py --regenerate txt_output/checklist_result.json "checklist.xlsx"
```

### Status Markers

| Marker | Meaning |
|--------|---------|
| ✓ Confirmed | Exact or highly consistent match found in reference documents |
| △ Partial Match | Some content sourced but the statement is incomplete |
| △ Data Mismatch | Data or wording differs from reference documents |
| ✗ Not Found | Statement not found in any reference document |

### Common False Positives in Automated Matching

The automated keyword matching can produce false positives in these scenarios:

1. Keywords appearing in unrelated contexts
2. Different entities conflated due to shared keywords
3. Base numbers matching but growth rates not independently verified
4. Statistical scope/unit mismatches

Round 3 reverse validation is specifically designed to catch these.

### Directory Structure

```
doc-fact-check/
├── SKILL.md                      # Skill definition file
├── scripts/
│   └── doc_fact_check.py         # Fact-checking script (v2, three-round enhanced)
└── README.md
```

## License

MIT
