# doc-fact-check

## 中文

文档表述准确性核对工具 —— 将宣传材料中的每条定性和定量表述与参考文档逐一比对，生成带颜色标注的 Excel 核对清单。

### 功能

- 自动将宣传材料拆解为独立的定性和定量表述
- 在参考文档中全文检索每条表述的出处
- 标记每条表述的状态：已确认 / 部分匹配 / 数据不一致 / 未找到
- 输出带颜色标注的 Excel 核对清单（三个 Sheet）
- 支持人工复查、更新结果后重新生成 Excel

### 依赖

```bash
brew install pandoc          # macOS
pip install openpyxl
```

### 使用方式

#### 在 Qoder CLI 中调用

在 Qoder 会话中，当你需要核验某份宣传材料的准确性时，直接说明需求即可，此 Skill 会自动触发。例如：

> 帮我核验这份工作总结中的表述是否都能在参考文档里找到出处

#### 命令行直接使用

```bash
python3 scripts/doc_fact_check.py "input.docx" "reference_docs/" "output.xlsx"
```

脚本会自动：
1. 使用 pandoc 将参考文档全部转为 txt
2. 提取宣传材料中的定性和定量表述
3. 在参考文档中进行关键词全文检索
4. 输出中间 JSON (`txt_output/checklist_result.json`) 和 Excel 核对清单

### 核对标记说明

| 标记 | 含义 |
|------|------|
| ✓ 已确认 | 参考文档中有完全相同或高度一致的表述 |
| △ 部分匹配 | 部分内容有出处但表述不完整 |
| △ 数据不一致 | 数据或表述与原始文档有差异 |
| ✗ 未找到 | 所有参照文档均未出现该表述 |

### 人工复查

自动检索后标记为"✗ 未找到"的项目建议人工复查：

1. 读取 `txt_output/checklist_result.json`
2. 对未找到的项目用更宽泛的关键词在参考文档 txt 中搜索
3. 更新 JSON 中的状态和出处
4. 运行第五步脚本重新生成 Excel

### 目录结构

```
doc-fact-check/
├── SKILL.md                      # Qoder Skill 定义文件
├── scripts/
│   └── doc_fact_check.py         # 核对脚本
└── README.md
```

---

## English

A document fact-checking tool that compares each qualitative and quantitative statement in a promotional document against reference documents, then generates a color-coded Excel checklist.

### Features

- Automatically splits a promotional document into individual qualitative and quantitative statements
- Full-text searches each statement's source across reference documents
- Marks each statement with a status: Confirmed / Partial Match / Data Mismatch / Not Found
- Outputs a color-coded Excel checklist with three sheets
- Supports manual review and re-generation of the Excel after updating results

### Dependencies

```bash
brew install pandoc          # macOS
pip install openpyxl
```

### Usage

#### From Qoder CLI

Simply describe your fact-checking needs in a Qoder session — the skill will trigger automatically. For example:

> Help me verify whether all statements in this summary document can be traced back to the reference documents.

#### Command Line

```bash
python3 scripts/doc_fact_check.py "input.docx" "reference_docs/" "output.xlsx"
```

The script automatically:
1. Converts all reference documents to plain text using pandoc
2. Extracts qualitative and quantitative statements from the promotional document
3. Performs keyword-based full-text search across reference documents
4. Outputs a JSON intermediate file (`txt_output/checklist_result.json`) and an Excel checklist

### Status Markers

| Marker | Meaning |
|--------|---------|
| ✓ Confirmed | Exact or highly consistent match found in reference documents |
| △ Partial Match | Some content sourced but the statement is incomplete |
| △ Data Mismatch | Data or wording differs from the original document |
| ✗ Not Found | Statement not found in any reference document |

### Manual Review

Items marked "✗ Not Found" after automated search are recommended for manual review:

1. Read `txt_output/checklist_result.json`
2. Search reference document txt files with broader keywords
3. Update the status and source in the JSON
4. Re-run the Excel generation script

### Directory Structure

```
doc-fact-check/
├── SKILL.md                      # Qoder Skill definition file
├── scripts/
│   └── doc_fact_check.py         # Fact-checking script
└── README.md
```

## License

MIT
