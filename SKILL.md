---
name: doc-fact-check
description: >
  核对目标文档（宣传稿/汇报/总结）中每条表述是否有参考文档依据。
  Use when the user wants to fact-check a document, verify claims against source
  documents, find unsupported statements, or run a compliance audit.
---

# 文档表述核对（三轮法）

核对的目标是**找出参考库未能支撑的声明**，而非证明文档正确。宁可多看，不漏检。

## 前置条件

- `brew install pandoc`（转换 .docx/.doc 参考文件，.txt 无需）
- `pip install pyyaml`（可选，加载领域类别词配置）
- `pip install openpyxl`（可选，`--excel` 时需要）

---

## 工作流程

### 第一步：确认路径

确认目标文档路径、参考文档目录、输出报告路径。

**完成标志**：三条路径均已确认存在。

---

### 第二步：第一轮核对（自动）

```bash
# 默认（无 LLM，~4s，Recall 最高）
python3 scripts/doc_fact_check.py "目标文档.docx" "参考文档目录/"

# 指定输出路径
python3 scripts/doc_fact_check.py "目标文档.docx" "参考文档目录/" "报告.md"

# 为 ✗ 条目打「可核实/不可核实」路由标签（agent 场景推荐，~15s，Recall 不变）
python3 scripts/doc_fact_check.py "目标文档.docx" "参考文档目录/" --classify

# LLM 深度判定（仅限人工复核场景，~43s，测试集 Recall 降至 93.8%）
python3 scripts/doc_fact_check.py "目标文档.docx" "参考文档目录/" --llm-judge

# 附加生成 Excel
python3 scripts/doc_fact_check.py "目标文档.docx" "参考文档目录/" --excel
```

脚本输出：
- `txt_output/checklist_result.json` — 核对结果（供后续步骤修改）
- `{文档名}_核对清单.md` — Markdown 核对报告

**完成标志**：JSON 已生成，报告概览数字正常显示。

> **若下游是 AI Agent**：到此结束。用 `--classify` 让报告标注哪些 ✗ 值得追查，将报告交给下游。

---

### 第三步：第二轮 ── 复查「✗ 未找到」

逐条人工核查 ✗ 条目，参考以下策略：

- **分段搜索**：长句拆成 2-3 个短关键词分别检索
- **数字搜索**：直接搜数字本身（如"356项""67%"）
- **近义词替换**：项目↔课题、平台↔中心/基地、课题↔研究
- **全量浏览**：对最权威参考文档 grep 相关章节

找到出处后更新 JSON 中的 `状态`、`出处`、`原文片段`。

**完成标志**：每条 ✗ 条目均已核查，状态已更新（升为 ✓ 或维持 ✗）。

---

### 第四步：第三轮 ── 反向验证「✓ 已确认」

**这是最容易遗漏的环节。** 自动标记的 ✓ 中可能隐藏静默误判。

逐条对 ✓ 条目执行六项检查，见 → [**反向验证清单**](REVIEW-CHECKLIST.md)。

最危险信号（详见清单）：数字搜索返回 0 结果但标 ✓、关键词过于通用、实体名称与参考文档不逐字一致。

**完成标志**：每条 ✓ 条目均已过六项检查，误判已降级，无遗漏。

#### 第四步附：复查「△」条目

`反向验证警告` 字段已列出具体疑点。逐条对照警告执行：

1. 子命题逐项独立验证
2. 专有名词与参考文档逐字比对
3. 同名实体在目标文档内前后一致性检查

**完成标志**：每条 △ 条目的警告已处理，状态已最终确定。

---

### 第五步：标记不一致项目

将差异写入 JSON 对应条目的 `状态` 字段：

| 状态 | 触发条件 |
|------|---------|
| ✓ 已确认 | 六项检查全部通过 |
| △ 需核实 | 仅通用词命中、缺数字/专名强佐证（自动降级） |
| △ 部分匹配 | 数据有出处但不完整 |
| △ 数据不一致 | 数据或名称有差异 |
| ✗ 未找到 | 无对应出处 |

**完成标志**：所有条目状态已确定，无「待定」条目。

---

### 第六步：重新生成报告

```bash
python3 scripts/doc_fact_check.py --regenerate txt_output/checklist_result.json

# 同时生成 Excel
python3 scripts/doc_fact_check.py --regenerate txt_output/checklist_result.json --excel
```

**完成标志**：Markdown 报告已更新，概览数字与 JSON 一致。

---

### 第七步：汇总

报告概览章节呈现最终各状态数量。人工核查至此完成。
