# doc-fact-check

## 中文

文档表述准确性核对工具 —— 将目标文档中的每条定性和定量表述与参考文档逐一比对，
通过**自动初查 → 人工复检 → 反向验证**三轮闭环，生成 Markdown 核对报告。

### 特性

- 🏷️ **复合表述分解**：自动将"A入选X，B获批Y"拆分为独立子命题分别验证
- 🏷️ **通用类别词实体提取（v3.2）**：跨领域类别词（任务/试点/中心/体系/规划…）识别专有名词，
  经 `scripts/entity_config.yaml` 配置，不再依赖领域专属尾缀
- 🏷️ **实体-语境共现验证**：提取专有名词后检查其是否与匹配关键词在同一语境出现，防止张冠李戴
- 🏷️ **假✓降级（v3.3）**：仅靠过短/高频通用词命中且无数字/专名佐证的"✓"自动降级为「△ 需核实」，防止静默假✓
- 🔍 在参考文档中全文检索每条表述的出处，并辅以反向验证警告
- 🏷️ 标记每条表述的状态：已确认 / 需核实 / 部分匹配 / 数据不一致 / 未找到
- 📄 输出 Markdown 核对报告（重要优先：✗ → ⚠△ → ✓折叠），可选附加 `--excel`
- 🔄 支持 `--regenerate` 模式：人工修改 JSON 后直接重新生成报告，无需重新转换文档
- 📋 自动生成反向验证报告，标记潜在误匹配供第三轮重点关注
- 📄 支持 `.txt` 参考文件直接读入（无需 pandoc）；`.doc` 文件 macOS 上用 textutil 自动转换

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
brew install pandoc          # macOS（转换 .docx/.doc 参考文件用）
pip install pyyaml           # 可选：加载 entity_config.yaml 类别词（缺失时回退内置默认）
pip install openpyxl         # 可选：仅在使用 --excel 时需要
```

### 使用方式

#### 命令行

```bash
# 第一轮：自动核对 → 生成 {文档名}_核对清单.md
python3 scripts/doc_fact_check.py "目标文档.docx" "参考文档目录/"

# 指定输出路径
python3 scripts/doc_fact_check.py "目标文档.docx" "参考文档目录/" "报告.md"

# 同时附加 Excel
python3 scripts/doc_fact_check.py "目标文档.docx" "参考文档目录/" --excel

# 人工修改 JSON 后重新生成报告
python3 scripts/doc_fact_check.py --regenerate txt_output/checklist_result.json

# --llm-judge：启用 LLM 第三轮判定层
python3 scripts/doc_fact_check.py "目标文档.docx" "参考文档目录/" --llm-judge
```

#### 在 Qoder / Pi CLI 中调用

在会话中直接说明需求即可，此 Skill 会自动触发。例如：

> 帮我核验这份工作总结中的表述是否都能在参考文档里找到出处

### 核对标记说明

| 标记 | 含义 |
|------|------|
| ✓ 已确认 | 参考文档中有完全相同或高度一致的表述 |
| △ 需核实 | 仅靠过短/高频通用词命中、缺强佐证，自动从「✓」降级，需人工确认（v3.3） |
| △ 部分匹配 | 部分内容有出处但表述不完整 |
| △ 数据不一致 | 数据或表述与原始文档有差异 |
| ✗ 未找到 | 所有参照文档均未出现该表述 |

### Markdown 报告结构

报告按"重要优先"排列，方便审阅时从最需要人工处理的条目开始：

| 章节 | 内容 | 格式 |
|------|------|------|
| 概览 | 各状态数量汇总 | 一行统计 |
| ✗ 未找到 | 需人工逐条核查的条目 | 紧凑表格（实体 + 数字辅助搜索） |
| ⚠ 反向验证重点关注 | △/⚠ 条目，含完整原文片段和警告 | 每条独立展开 |
| ✓ 已确认 | 已核实条目 | `<details>` 折叠 |

### 反向验证：自动匹配的常见误判

| 最危险信号 | 说明 |
|-----------|------|
| 数字搜索返回0结果但已标"✓" | 极可能是误匹配，关键词匹配到的是文字而非数字 |
| 关键词过于通用 | 如"平台""建设""水平"等词→必须检查上下文 |
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
│   ├── doc_fact_check.py         # 核对主脚本（v3.3）
│   ├── adjudicate.py             # LLM 判定层（--llm-judge 调用）
│   ├── retrieval.py              # 向量检索模块（Stage 2A）
│   ├── factcheck_llm.py          # LLM 工具函数
│   ├── entity_config.yaml        # 通用类别词配置（跨领域，可扩展）
│   └── add_category_words.py     # 类别词批量增补辅助脚本
├── tests/                        # TDD 测试套件（210 tests）
├── eval/                         # 标注评测集（factcheck-recall.jsonl）
└── txt_output/                   # 中间输出（自动生成）
```

---

## English

A document fact-checking tool that verifies each qualitative and quantitative statement
in a target document against reference documents through a **three-round review process**
(automated search → manual re-check → reverse validation), producing a Markdown checklist.

### Features

- 🏷️ **Compound statement decomposition**: Automatically splits "A入选X，B获批Y" into independent sub-claims for separate verification
- 🏷️ **Generic category-word entity extraction (v3.2)**: Cross-domain category words (task/pilot/center/system/plan…) identify named entities, configured via `scripts/entity_config.yaml`
- 🏷️ **Entity-context co-occurrence check**: Verifies that named entities appear in the same context as matched keywords, preventing misattribution
- 🏷️ **False-✓ downgrade (v3.3)**: A "✓" backed only by short/high-frequency generic keywords is auto-downgraded to "△ Needs Review"
- 🔍 Full-text search with reverse validation warnings
- 📄 Markdown report output (priority order: ✗ → ⚠△ → ✓ collapsed); `--excel` adds Excel
- 🔄 `--regenerate` mode: re-generate report from manually corrected JSON
- 📄 Supports `.txt` reference files directly; `.doc` auto-converted via textutil on macOS

### Dependencies

```bash
brew install pandoc          # macOS (for .docx/.doc reference files)
pip install pyyaml           # optional: extended category-word config
pip install openpyxl         # optional: only needed with --excel
```

### Usage

```bash
# Round 1: automated check → generates {doc}_核对清单.md
python3 scripts/doc_fact_check.py "target.docx" "reference_docs/"

# Re-generate report after manual JSON edits
python3 scripts/doc_fact_check.py --regenerate txt_output/checklist_result.json

# Also generate Excel
python3 scripts/doc_fact_check.py "target.docx" "reference_docs/" --excel
```

### Status Markers

| Marker | Meaning |
|--------|---------|
| ✓ Confirmed | Exact or highly consistent match found in reference documents |
| △ Needs Review | Matched only by short/high-frequency generic keywords; auto-downgraded (v3.3) |
| △ Partial Match | Some content sourced but statement is incomplete |
| △ Data Mismatch | Data or wording differs from reference documents |
| ✗ Not Found | Statement not found in any reference document |

## License

MIT
