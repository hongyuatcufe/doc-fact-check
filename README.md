# doc-fact-check

## 中文

文档表述准确性核对工具 —— 将目标文档中的每条定性和定量表述与参考文档逐一比对，
通过**自动初查 → 人工复检 → 反向验证**三轮闭环，生成带颜色标注的 Excel 核对清单。

### 特性

- 🏷️ **复合表述分解**：自动将"A入选X，B获批Y"拆分为独立子命题分别验证
- 🏷️ **实体-语境共现验证**：提取专有名词后检查其是否与匹配关键词在同一语境出现，防止张冠李戴
- 🔍 在参考文档中全文检索每条表述的出处，并辅以反向验证警告
- 🏷️ 标记每条表述的状态：已确认 / 部分匹配 / 数据不一致 / 未找到
- 📊 输出带颜色标注的 Excel 核对清单（5 个工作表，含实体覆盖分析）
- 🔄 支持 `--regenerate` 模式：人工修改 JSON 后直接重生成 Excel，无需重新转换
- 📋 自动生成反向验证报告，标记潜在误匹配供第三轮重点关注
- 📄 支持 `.doc` 文件自动回退（macOS 上用 textutil 转换）

### 三轮复核法

```
第一轮：自动关键词匹配 + 子命题分解 + 实体覆盖 + 实体-语境共现
   │  （自动拆分复合表述、提取实体、验证共现）
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

#### 在 Qoder / Pi CLI 中调用

在会话中直接说明需求即可，此 Skill 会自动触发。例如：

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
| 全部核对结果 | 所有表述的完整核对状态、出处、原文片段、反向验证警告 |
| 已确认表述 | 仅列出 ✓ 已确认 的条目 |
| 待核实表述 | 未找到 或 需进一步确认 的条目 |
| 反向验证重点关注 | 存在误判风险的条目及人工核查建议 |
| 实体覆盖分析 | 专有名词实体在各表述中的覆盖情况 |

### 反向验证：自动匹配的常见误判

自动关键词匹配可能在以下场景产生误判，第三轮反向验证专门排查：

| 最危险信号 | 说明 |
|-----------|------|
| 数字搜索返回0结果但已标"✓" | 极可能是误匹配，关键词匹配到的是文字而非数字 |
| 关键词过于通用 | 如"平台""建设""水平""**两重**"等词→必须检查上下文 |
| 一条表述含 2+ 数字 | 每个数字都要独立验证 |
| 表述中有增长率/增幅 | 增长率必须去参考文档中单独找到 |
| 名称/机构/项目名 | 与参考文档中找到的名称逐字比对 |
| 表述含多个并列实体 | 可能仅部分匹配，需拆开分别验证 |
| 实体-语境不匹配 | 实体在参考文档中存在但从不与匹配关键词共现 → 张冠李戴 |

### 目录结构

```
doc-fact-check/
├── SKILL.md                      # Skill 定义文件
├── README.md                     # 本文件
├── .gitignore
├── scripts/
│   └── doc_fact_check.py         # 核对脚本（v3.1 实体-语境共现增强版）
└── txt_output/                   # 中间输出（自动生成，可选纳入 .gitignore）
```

---

## English

A document fact-checking tool that verifies each qualitative and quantitative statement
in a target document against reference documents through a **three-round review process**
(automated search → manual re-check → reverse validation), producing a color-coded Excel checklist.

### Features

- 🏷️ **Compound statement decomposition**: Automatically splits "A入选X，B获批Y" into independent sub-claims for separate verification
- 🏷️ **Entity-context co-occurrence check**: Verifies that named entities appear in the same context as matched keywords, preventing misattribution
- 🔍 Full-text searches each statement's source across reference documents with reverse validation warnings
- 🏷️ Status markers: Confirmed / Partial Match / Data Mismatch / Not Found
- 📊 Color-coded Excel checklist with 5 worksheets (including entity coverage analysis)
- 🔄 `--regenerate` mode for re-generating Excel from manually corrected JSON
- 📋 Auto-generated reverse-validation report highlighting potential false positives
- 📄 Auto fallback for `.doc` files (textutil on macOS)

### Three-Round Review

```
Round 1: Auto keyword matching + sub-claim decomposition + entity coverage + entity-context co-occurrence
   │  (auto-decomposition, entity extraction, co-occurrence validation)
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

#### From Qoder / Pi CLI

Describe your fact-checking needs in a session — the skill triggers automatically:

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

| Highest Risk Signals | Description |
|---------------------|-------------|
| Zero hits for numbers but "Confirmed" | Keyword matched text, not numbers |
| Overly generic keywords | Words like "platform", "construction" — must check context |
| Statement contains 2+ numbers | Each number must be independently verified |
| Growth rates / percentage changes | Base number match ≠ growth rate match |
| Named entities / project names | Compare character-by-character with reference documents |
| Statement with multiple entities | May have partial match only — verify each separately |
| Entity-context mismatch | Entity exists in reference but never near matched keyword → misattribution |

### Directory Structure

```
doc-fact-check/
├── SKILL.md                      # Skill definition file
├── README.md                     # This file
├── .gitignore
├── scripts/
│   └── doc_fact_check.py         # Fact-checking script (v3.1, entity-context co-occurrence enhanced)
└── txt_output/                   # Intermediate output (auto-generated, optionally in .gitignore)
```

## License

MIT
