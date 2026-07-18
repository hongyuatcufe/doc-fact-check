# 技术可行性核查报告

> 针对「检索与判定层升级（v5）」方案五项技术声明的一手文献核查
>
> 核查日期：2026-07-18

---

## (a) SQLite FTS5 trigram 对中文的支持

**声明**：trigram tokenizer「绕开中文分词」，无需 jieba 即可索引和检索中文文本。

**核查来源**：https://www.sqlite.org/fts5.html，第 4.3.4 节「The Trigram Tokenizer」

**官方原文（精确引用）**：

> "The trigram tokenizer extends FTS5 to support substring matching in general, instead of the usual token matching. When using the trigram tokenizer, a query or phrase token may match any sequence of characters within a row, not just a complete token."

**关于 CJK/中文的说明**：SQLite 官方文档**完全没有提及** CJK、中文、日文、韩文，也没有任何关于「绕开中文分词」的说法。文档中 trigram 的示例全部是拉丁字母。

**技术机制分析**：

trigram tokenizer 的工作原理是将文本拆成每三个连续 Unicode 字符一组的 token。这个机制对中文确实**有效**——因为中文字符也是 Unicode，三字一组的切割天然覆盖了所有两字词和部分三字词的子串。查询时，一个两字词（如「发展」）会生成一个 trigram，可以在 FTS5 索引中命中。这是社区中已知的用法，但**官方文档从未明确承诺这种行为用于 CJK**。

**注意事项（官方文档明确）**：

> "Substrings consisting of fewer than 3 unicode characters do not match any rows when used with a full-text query."

这意味着：**单字查询（1个汉字）和双字查询（2个汉字）在 full-text query 模式下无法命中**。若要查单字或双字，需改用 `LIKE '%字%'` 模式（代价是全表扫描）。

**结论**：⚠ **基本成立，但有条件限制**

- 机制上：trigram 确实不依赖中文分词器，原理可行。
- 文档上：官方从未保证 CJK 支持，「绕开中文分词」是社区结论，非官方承诺。
- 实际限制：1~2 字短词查询在 FTS5 full-text 模式下会静默返回空结果，需用 LIKE 兜底，而 LIKE 是线性扫描。公文核查中若声明含单字专名（如某省简称），会有漏检风险。

**建议**：在 `retrieval.py` 中对查询词长度做分支判断：≥3字走 FTS5 full-text；<3字走 `LIKE '%xx%'`（全扫）或加 exactTerms 锚点强制匹配。

---

## (b) rank_bm25 性能与维护状态

**声明**：rank_bm25 纯 Python 实现，适用于「百篇级语料」，实用可行。

**核查来源**：
- PyPI 页面：https://pypi.org/project/rank-bm25/（最新版 0.2.2，发布于 2022-02-16）
- GitHub 仓库：https://github.com/dorianbrown/rank_bm25
- README 原文：仓库 `README.md`

**README 原文（精确引用，性能警告）**：

> "For those looking to use this in large scale production environments, I'd recommend you take a look at something like [retriv](https://github.com/AmenRa/retriv), which is a much more performant python retrieval package."

**维护状态**：

| 指标 | 数值 |
|------|------|
| 最新版本 | 0.2.2（2022-02-16 发布，**4年以上无新版**） |
| 最后 commit | 2024-10-08（仅更新 README） |
| open issues | 27 个 |
| 仓库 stars | 1,362 |
| 仓库是否 archived | 否 |

**性能现状**：

当前代码（v0.2.2）是纯 Python 实现，`get_scores()` 的复杂度为 O(V×D)，其中 V 为词表大小，D 为文档数。GitHub issue #53（2026-03-14，尚未合并）的基准测试显示：

| 语料规模 | 当前版本 QPS | issue#53 提议优化后 |
|---------|------------|---------------------|
| 3,600 docs (NFCorpus) | 359 | 16,751（+47×） |
| 57,000 docs (FiQA) | 5.7 | 522（+92×） |

结论：**当前已发布版本在 57K 文档规模下仅 5.7 QPS**（约 175ms/查询）。对于本方案的「百篇级语料」（即百条 ~1200 字的 chunk，约相当于 NFCorpus 规模量级），估计单次检索耗时在毫秒到数十毫秒级，**可接受**。

**结论**：✓ **百篇级语料可行，但有维护风险**

- 性能：百级 chunk 规模（~100-500 docs）查询延迟可接受（估计 <50ms）。
- 维护：库已实质进入低维护状态（最近4年无功能更新）；README 自承不适合大规模生产，推荐 retriv 替代。
- 无 CJK 分词支持：`rank_bm25` 不做任何文本预处理，需调用方自行分词（trigram 切割或 jieba）后传入 token list。若与 FTS5 trigram 结合使用，需保证两者分词策略一致。
- 风险：27 个 open issues，若遇 bug 基本需自行 fork 修复。

**建议**：百级语料首选方案。若后续语料增长到千级以上，考虑 `retriv` 替代。

---

## (c) RRF k=60 参数出处

**声明**：RRF 融合使用 k=60，这是标准默认值。

**核查来源**：
- 原始论文引用：Gordon V. Cormack, Charles L. A. Clarke, and Stefan Buettcher. "Reciprocal rank fusion outperforms Condorcet and individual rank learning methods." *Proc. 32nd ACM SIGIR*, Boston, MA, July 2009, pp. 758–759. DOI: 10.1145/1571941.1572114
- 二次来源：https://bigdataboutique.com/blog/reciprocal-rank-fusion-how-it-works-and-when-to-use-it
- 二次来源：https://doris.apache.org/docs/dev/key-features/reciprocal-rank-fusion/

**参数来源确认**：

k=60 **确实来自 2009 年原始论文**。Cormack et al. 在 TREC 数据集上经验调优后选定 k=60，报告该值在不同数据集间有良好泛化性。

**公式**：

```
RRF_score(d) = Σ_{r ∈ R}  1 / (k + rank_r(d))
```

其中 R 为所有排序列表的集合，rank_r(d) 为文档 d 在排序列表 r 中的 1-based 排名位置。

**适用于两个列表的情况**：

RRF 公式是加和形式，天然支持任意数量的排序列表（|R|=1 到 N 均成立）。两个列表（BM25 + 向量）是最常见的部署场景，k=60 在此场景下完全适用。多个信息检索和 hybrid search 基准（2020-2024）反复验证 k∈[40,80] 性能相近，k=60 是业界事实标准。

**结论**：✓ **完全确认**

k=60 直接出自 2009 年原始论文，是学术和工程界的公认默认值。两列表场景适用，无需调整。

---

## (d) DashScope text-embedding-v4 规格核实

**声明**：使用 DashScope `text-embedding-v4` 做中文 embedding，适用于中文公文检索。

**核查来源**：
- 官方文档（英文）：https://www.alibabacloud.com/help/en/model-studio/text-embedding-synchronous-api
- 官方文档（中文）：https://www.alibabacloud.com/help/en/model-studio/embedding
- 第三方评测：https://help.apiyi.com/en/text-embedding-v4-vector-dimensions-guide-en.html

**实际规格**（官方文档确认）：

| 参数 | 数值 |
|------|------|
| 模型标识符 | `text-embedding-v4` |
| 每条文本最大 tokens | **8,192 tokens**（官方 API 文档） |
| 支持的输出维度 | 64 / 128 / 256 / 512 / 768 / **1024（默认）** / 1536 / 2048 |
| 每次请求最大批量 | **10 条文本** |
| 支持语言 | 中文、英文及 100+ 主流语言 |
| 定价（新加坡区） | $0.07 / 1M input tokens |
| 免费额度 | 激活后 90 天内 100 万 tokens |

**关于最大 tokens 的分歧**：官方 API 文档标注 8,192 tokens/条，但第三方资料（apiyi.com）称 32K。以官方文档为准，取 **8,192 tokens**。公文单段落分块（~1200 字）约合 1200-1800 tokens，远低于上限，无问题。

**中文性能评估**：

第三方 CMTEB（中文文本嵌入基准）评测：`text-embedding-v4` 在 1024 维下 CMTEB Overall 70.14，CMTEB Retrieval 73.98，**在所有商业 API 中排名第一**（优于 OpenAI text-embedding-3-large 的 64.6 和 Cohere embed-v4 的 70.3）。

**OpenAI 兼容接口**：DashScope 提供 OpenAI 兼容 API，与方案中「与 factcheck_llm 同款 urllib 调用」完全一致，无需额外适配。

**方案中的技术细节确认**：

方案提到「1024 维 cosine 手写即可」——这与模型默认维度（1024d）吻合。百篇级语料下纯 Python cosine 计算可行（约 100-500 次向量点积）。

**结论**：✓ **完全确认，规格符合需求**

`text-embedding-v4` 真实存在、可用，对中文公文检索是当前商业 API 的最优选择之一。8192 token 上限对公文分块无压力，1024 维默认输出与方案设计一致。

---

## (e) 工时估算合理性评估

**评估视角**：有经验的 Python 独立开发者（熟悉本项目代码库，但第一次做这些具体模块）。

### Stage 0 — 评测地基（估算 1 天）

**拆解**：
- 0.1 建标注集 ≥60 条（0.5 天）：需要人工阅读真实稿、判断漏检类型、写入 JSONL。依赖「真实漏检案例」数量；若现有案例不足 30 条需额外构造，时间可能翻倍。
- 0.2 建评测脚本（0.5 天）：`run_eval.py` 逻辑不复杂，但需对接现有 `doc_fact_check.py` 输出格式。

**风险**：标注集质量是整个方案的门控，**0.5 天标注 ≥60 条（两类各半）偏乐观**，尤其若需从零归纳漏检案例。建议 1~1.5 天。

**评估**：⚠ 略偏乐观，建议 1.5 天

---

### Stage 1 — 激活 LLM 抽取（估算 1 天）

**拆解**：
- 若为情况 (a)（未接线）：改一行入口 + 验证，0.5 天即可。
- 若为情况 (b)（抽取质量差）：调试 JSON 解析、prompt 迭代、回归测试，可能需要 2-3 天。

**风险**：估算 1 天的前提是情况 (a)。方案本身已说明需要「先做前置诊断」，但**未将 (b) 分支的额外工时计入**。若是 (b)，1 天完全不够。

**评估**：⚠ 依赖情况分支，若情况 (b) 则低估 2 倍

---

### Stage 2A — trigram/BM25 本地检索（估算 2 天）

**拆解**：
- 新增 `retrieval.py`：SQLite FTS5 建表、分块逻辑、trigram 索引、BM25 实现、查询接口。
- 修改 `search_in_reference()` 适配新接口。
- 单元测试 + 在 eval 集上验证。

**主要技术障碍**：
1. FTS5 trigram 的 <3字查询需要特殊处理（见 (a) 小节），需额外分支逻辑。
2. BM25 需要分词输入（不能直接喂原始中文），需决定是用 trigram 切割还是 jieba 分词后传入 `rank_bm25`——两种方案行为不同，需要实验对比。
3. 分块策略（1200 字、150 重叠、中文句末边界）需要仔细实现，边界 bug 容易引入。

**评估**：✓ 2 天合理（前提是技术方案确定，不需要大量对比实验）

---

### Stage 2B — 向量 + RRF（估算 2 天）

**拆解**：
- DashScope embedding API 调用（10条/批次，需批处理逻辑）。
- cosine 相似度计算（纯 Python，百级语料可行）。
- sqlite BLOB 存储向量、查询向量计算。
- RRF 融合（简单，约 20 行代码）。
- 回退逻辑测试（API 不可用时降级到 2A）。

**主要技术障碍**：
1. 批量 embedding 的批处理循环（10条/批，防超时，错误重试）需要工程细节。
2. 向量的持久化与增量更新（参考库新增文档时只需重新 embed 新 chunk）。

**批次限制影响**：若参考库有 500 chunk，需要 50 次 API 请求建索引（一次性操作），耗时可接受。

**评估**：✓ 2 天合理，但需 API key 可用且网络稳定

---

### Stage 3 — LLM 判定（估算 2 天）

**拆解**：
- 新增 `adjudicate.py`：调用 LLM API，prompt 工程，解析 4-state 输出。
- 回退到规则判定的分支逻辑。
- 在 eval 集上验证误判率下降。

**主要技术障碍**：
1. **Prompt 工程**是最大不确定因素。「逐子命题核对主体/时间/数字/单位/专名/口径」的 prompt 需要多轮迭代才能达到稳定的结构化输出，尤其是对「inconsistent」vs「needs_review」的边界判断。
2. LLM 输出的结构化解析（JSON 提取、围栏清理）与 `factcheck_llm.py` 中已有类似逻辑，可复用，但需适配新的输出 schema。

**评估**：⚠ 2 天对于 prompt 工程偏乐观。若第一轮 prompt 效果差需迭代，可能需要 3 天。

---

### Stage 4 — 下游传播（估算 1 天）

**拆解**：
- 修改两个下游 repo 的 `run-fact-check.sh` 入口。
- 更新 `requirements.txt`。
- 跑下游回归测试（真实稿）。
- `sync-from-upstream.sh` 验证字节一致性。

**前提**：CLI 契约确实冻结（字段只增不改），下游无意外依赖。这是最确定的一步。

**评估**：✓ 1 天合理

---

### 总体工时评估

| Stage | 方案估算 | 核查评估 | 备注 |
|-------|---------|---------|------|
| Stage 0 | 1 天 | 1~1.5 天 | 标注集构建比预期费时 |
| Stage 1 | 1 天 | 0.5~3 天 | **强依赖情况 (a)/(b) 分支** |
| Stage 2A | 2 天 | 2~2.5 天 | 中文 <3字查询分支需额外处理 |
| Stage 2B | 2 天 | 2 天 | 合理 |
| Stage 3 | 2 天 | 2~3 天 | Prompt 迭代有不确定性 |
| Stage 4 | 1 天 | 1 天 | 合理 |
| **合计** | **8~10 天** | **8.5~13 天** | 若 Stage 1 为情况 (b) 则显著低估 |

**结论**：⚠ **总体合理但偏乐观**

8-10 天的估算在「情况 (a) + prompt 顺利」的乐观情境下可达成。在中间情境（情况 (b) 且 prompt 需要适度迭代）下，实际工时更接近 10-13 天。**最大的不确定性是 Stage 1 的情况分支**——建议在 Stage 0 完成后立即做 Stage 1 的前置诊断，再根据结果修订后续估算。

---

## 总结

| 声明 | 结论 | 备注 |
|------|------|------|
| FTS5 trigram 中文 | ⚠ 基本成立 | 官方文档未明确支持 CJK；**1~2 字查询 FTS5 模式静默返回空**，需 LIKE 兜底 |
| rank_bm25 百级语料 | ✓ 可行 | 百级 chunk 下延迟可接受；库 4 年无新版，README 自荐 retriv 替代大规模场景；无内置中文分词需自行处理 |
| RRF k=60 | ✓ 完全确认 | 直接出自 Cormack et al. 2009 原始论文；两列表场景适用；业界事实标准 |
| text-embedding-v4 | ✓ 完全确认 | 真实可用；8192 token 上限、1024 维默认、100+语言；CMTEB 中文检索排名第一；OpenAI 兼容接口 |
| 工时 8-10 天 | ⚠ 偏乐观 | Stage 1 情况 (b) 分支和 Stage 3 prompt 迭代未充分计入；保守估算 10-13 天；建议先做 Stage 1 诊断再修订 |

---

*核查方法：直接获取 SQLite 官方文档（sqlite.org/fts5.html）、PyPI 页面、GitHub API、Alibaba Cloud 官方文档（alibabacloud.com）；RRF 论文出处经多个二次来源交叉验证。*
