# doc-fact-check 优化方案：检索与判定层升级（v5）

> 目标：从根本上解决 doc-fact-check 的**漏检**问题。当前实际运行的核查管线
> （抽取 / 检索 / 判定）三条轴全部停留在正则 + 子串匹配，对中文近义改写、
> 跨文档聚合、复合声明的判定能力弱。本方案在**权威 skill 仓库**上分阶段升级，
> 保持 Python 自包含与 CLI 契约稳定，每一步用固定评测集量化收益，
> 再由 `sync-from-upstream` 传播到 `gongwen_web_agent` 与单机版 `gongwen-agent`。
>
> 适用范围：`github.com/hongyuatcufe/doc-fact-check`（权威源），
> 下游 vendored 副本 `gongwen_web_agent/tools/doc-fact-check/`、
> `gongwen-agent/tools/doc-fact-check/`。

最后更新：2026-07-18

---

## 一、背景与现状

### 1.1 漏检的三条轴

把核查看成一条流水线，漏检可能发生在三个彼此独立的环节：

| 轴 | 环节 | 当前实现（live 路径） | 问题 |
|---|---|---|---|
| ① | **抽取**：从目标稿挑出要核的声明/实体/数字 | `doc_fact_check.py` 正则 + `entity_config.yaml` 类别词 | 整句当实体、口号/引语混入；漏抽可核声明 |
| ② | **检索**：拿声明去参考库找证据 | `search_in_reference()` 的 `keyword in content` **子串匹配** | 近义/改写抓不到；无排序；跨文档不聚合 —— **漏检主源** |
| ③ | **判定**：证据到底支不支持声明 | 命中计数 + 实体覆盖率 + 300 字共现窗口（规则启发式） | 复合声明、口径一致性靠启发式；假✓与漏判都在这层 |

### 1.2 已有但未启用的资产

仓库里 `factcheck_llm.py`（标称 v4）**已实现 ① 的 LLM 抽取**（DeepSeek/OpenAI 兼容，
urllib 调用，jieba→正则兜底链完整），但：

- **live 路径没走它**：`gongwen_web_agent/.pi/verifier/scripts/run-fact-check.sh`
  直接调 `doc_fact_check.py`（v3 正则），不是 `factcheck_llm.py`。
- v4 只替换了 ①，**后端 ②③ 完全复用 v3**（`search_in_reference` / `generate_excel`）。

因此：**即使激活 v4，② 的子串匹配仍是漏检瓶颈。** ① 有现成解（激活即可），
真正的硬骨头是 ② 和 ③。

### 1.3 关键判断

- **换编排框架（mastra / ai-sdk）不解决漏检** —— 那是编排层，漏检是检索/判定层的召回问题，两者正交。
- **坚持 jieba 优化实体识别只能改善 ①** —— 结构上补不了 ② 的近义匹配和 ③ 的复合判定，天花板低。
- **收益必须实测** —— 向量语义（②B）的收益集中在"近义/改写/跨文档"子集；
  公文里大量精确声明（数字/文号/专名）用本地 trigram（②A）即可 100% 召回。
  两个子集的占比决定各阶段 ROI，须先测再投。

---

## 二、设计约束（决定技术选型）

1. **Python 自包含、pip 可装**
   skill 被 vendor 进两个 Python 项目。检索升级**用 Python 实现**，镜像 academic_wiki
   已验证的*设计*（trigram + 向量 + RRF + exactTerms 锚点），但**不引入其 TS/Node 代码**。
   引 Node 会给 gongwen SaaS 部署添一整条工具链，与 skill 的分发模型冲突。

2. **CLI 契约冻结**
   入参 `(target, ref_dir, out.xlsx)` 与产物 `txt_output/checklist_result.json` 的
   字段 schema 保持不变（`类型/表述内容/状态/出处/原文片段/匹配关键词/命中数字/命中实体/
   子命题/反向验证警告`）。下游 `run-fact-check.sh`、`parse-reports.py` 无需改动，
   升级只是"换引擎"。**新增字段只增不改**（如 `候选证据[]`、`判定理由`）。

3. **每步 eval 门控**
   先建标注集与基线，之后每个 Stage 只有 Recall 实测提升才合入。这是解决
   "收益多大不确定"的唯一手段。

4. **降级安全**
   任何外部依赖（LLM API、embedding API）不可用时，逐层回退到本地能力，
   保证任何环境都能出结果。SaaS 数据外发做成显式开关。

---

## 三、目标架构（v5）

```
目标稿.docx ─┐
             ▼
  ① 抽取   extract_claims()
           LLM(factcheck_llm) ──miss──▶ jieba 名词块 ──miss──▶ v3 正则
             │  产出：可核实声明 [{表述内容, 命中数字, 命中实体, 子命题}]
             ▼
  ② 检索   retrieve_evidence()          ┌── 参考库一次性建索引 ──┐
           2A 本地 trigram/BM25  ───────┤  sqlite: chunks + FTS5  │
           2B 向量语义(可选,--embed) ────┤  sqlite: chunk_vectors  │
           RRF 融合 → top-k 候选 chunk   └─────────────────────────┘
             │  产出：每条声明的排序候选证据（带 offset 引用）
             ▼
  ③ 判定   adjudicate()
           规则(快, 默认) │ LLM 独立判定(--llm-judge, 逐子命题核对)
             │  产出：confirmed/needs_review/inconsistent/not_found + 引用 + 理由
             ▼
         checklist_result.json（schema 兼容）→ generate_excel()
```

**分层原则**：三轴各自可独立开关与回退，互不阻塞。默认档（无 key、无 --embed、
无 --llm-judge）= 纯本地 trigram 管线，比现状（子串）已经更强且零外发。

---

## 四、阶段划分

### Stage 0 — 评测地基（先做，~1 天）

没有这步，后面全是拍脑袋。**这是整个方案的前置门。**

**0.1 建标注集**（~0.5 天）
- 语料来源：gongwen 真实稿的已知漏检案例 + academic_wiki 那份 141 条讲话稿核查案例
  + `rstacks-fixtures/corpus`。
- 标注单元：`{声明文本, 期望状态, 期望出处(sourcePath+片段), 漏检类型}`。
- **漏检类型二分类**（决定后续 ROI）：
  - `精确型`：专名/数字/文号，trigram 即可召回。
  - `近义型`：近义改写 / 跨文档聚合 / 复合子命题，必须语义或 LLM。
- 产出：`eval/factcheck-recall.jsonl`（≥ 60 条，两类各半）。

**0.2 建评测脚本**（~0.5 天）
- `eval/run_eval.py`：对给定管线跑标注集，输出 Recall@{1,5}、各状态混淆矩阵、
  精确型/近义型分层 Recall、平均延迟、外部 API 调用次数。
- 跑一遍 v3 现状 → 记录基线（预期：精确型高、近义型接近 0）。

**验收**：基线数字落纸；精确型 vs 近义型漏检占比明确 —— 这个比例直接决定 Stage 2B 是否值得做。

---

### Stage 1 — 激活并验证 ① LLM 抽取（~1 天）

**前置诊断**：先确认 v4"失效"是哪种：
- (a) 未接线 —— live 脚本调了 v3 → Stage 1 = 改入口一行 + 验证。
- (b) 接了但质量差 —— JSON 解析失败率高 / 漏抽 / 误抽 → Stage 1 = 修抽取逻辑。

**1.1 若 (a)**
- `run-fact-check.sh` 入口 `doc_fact_check.py` → `factcheck_llm.py`（DeepSeek key 已在 gongwen 环境）。

**1.2 若 (b)**
- 加固 `_parse_claims`（已有围栏剥离 + 截取兜底，补：分块重试、失败块降级到离线抽取而非整体 return None）。
- prompt 迭代：用 0.1 标注集回归"抽取召回 / 误抽率"。

**1.3 通用**
- 保留 `--offline`（jieba→正则）兜底。
- 记录每文档抽取 token 用量（校验 <1 美分/文档 估算）。

**验收**：抽取召回（该核声明被抽出比例）较正则提升可量化；误抽（口号/引语/纯观点）显著下降；无 key/超时可离线出结果。

---

### Stage 2 — ② 检索升级（漏检主战场，分两跳）

后端从"布尔子串命中"改为"排序候选证据检索"。**这是本方案的核心。**

**2A. 子串 → 本地 trigram / BM25（零 API，先上）**（~2 天）
- 新增 `retrieval.py`：参考正文按段落/句边界分块（复用 academic_wiki 分块设计：
  目标 ~1200 字、重叠 ~150、优先中文句末边界），存 sqlite；
  用 FTS5 `trigram`（绕开中文分词）或纯 Python `rank_bm25` 建关键词检索。
- `search_in_reference()` 改为调 `retrieval.retrieve()`：输入声明 + `命中数字/命中实体`
  作为 exactTerms 锚点，输出**排序候选 chunk**（带 sourcePath + offset），替代原布尔命中。
- 立即收益：精确型漏检里因关键词抽取不全/顺序丢失而 miss 的部分；
  且为 ③ 判定喂真正的证据上下文（而非全文 grep 片段）。
- 纯本地、免费、无外发。

**2B. 向量语义 + RRF（治近义，条件触发）**（~2 天，仅当 0.1 显示近义型占比可观）
- `retrieval.py` 加 embedding：参考 chunk 调 DashScope `text-embedding-v4`
  （urllib，与 factcheck_llm 同款 OpenAI 兼容调用），归一化向量存 sqlite BLOB；
  查询侧同样 embed，cosine 召回，与 2A 的 BM25 候选做 **RRF 融合**（k=60）。
- 纯 Python 实现（1024 维 cosine 手写即可，百篇级语料无需 numpy/faiss）。
  这是 academic_wiki M2C/M2D 已验证方案（改写集 Recall@5：关键词 0% → hybrid 91.7%~100%）的 Python 移植。
- **门控**：只有 2A 之后剩余漏检仍以近义型为主时才做；若剩余多为精确型则跳过，省掉外发/成本/延迟。
- SaaS 边界：`--embed` 显式开关；缺向量回退 2A 关键词（照搬 academic_wiki 的回退设计）。
  模型/维度变化时重建向量。

**验收**：
- 2A 后：精确型分层 Recall@5 ≥ 90%，纯本地无 API 调用，延迟可接受。
- 2B 后（若启用）：近义型分层 Recall@5 较 2A 显著提升；缺向量/Provider 失败自动回退且有测试覆盖。

---

### Stage 3 — ③ LLM 判定（把"命中"变成"支持"）（~2 天）

检索到证据不等于证据支持声明。现规则判定（命中计数/覆盖率/共现窗口）在复合声明、
口径一致性上误判多。

**3.1 判定层**
- 新增 `adjudicate.py`：对每条声明 + 2A/2B 召回的 top-k 候选 chunk，调 LLM 做**独立判定**，
  逐子命题核对主体/时间/数字/单位/专名/口径，输出
  `confirmed / needs_review / inconsistent / not_found` + 引用 + 判定理由。
- 复用 `factcheck_llm.py` 的 OpenAI 兼容 urllib 管线，新增 adjudication prompt。
- 用更可靠的 LLM 语境判断替代 v3 的"实体-语境共现"张冠李戴启发式。

**3.2 开关与回退**
- `--llm-judge` 显式开启；默认仍走规则判定（快、零外发）。
- LLM 不可用时回退规则判定，状态标注注明"未经 LLM 复核"。

**3.3 判定规则对齐**（沿用 academic_wiki skill 的判定语义）
- `confirmed`：同一证据上下文支持全部要素，必须 ≥1 引用。
- `needs_review`：部分支持 / 名称近似 / 多源口径不一 / 子命题未全覆盖。
- `inconsistent`：同主体、时间、口径下直接冲突，必须引用冲突证据。
- `not_found`：给定参考库无支持证据（≠ 声明为假）。
- 近似允许合理舍入；证据多列不构成冲突；增长率/总数/分项分别验证。

**验收**：复合声明与口径类误判较规则判定下降；confirmed/inconsistent 均带有效引用；关闭 --llm-judge 时行为与 Stage 2 一致。

---

### Stage 4 — 下游传播（契约稳，成本低）（~1 天）

因 CLI 入参 + JSON schema 冻结，下游改动最小化。

**4.1 gongwen_web_agent**
- `run-fact-check.sh` 换引擎入口（→ `factcheck_llm.py` 或新 `factcheck.py` 统一入口）。
- `requirements.txt` 增依赖（`rank_bm25` 或仅 stdlib+sqlite；jieba 已可选）。
- 回归真实稿，确认 `parse-reports.py` 解析不破、`checklist_result.json` 字段兼容。
- SaaS 决策：默认档是否开 `--embed`/`--llm-judge`（涉及每任务成本与数据外发，见风险）。

**4.2 单机版 gongwen-agent**
- 通过 `sync-from-upstream.sh` 拉取，`verify byte-identical`。
- 单机无 SaaS 隐私约束，可默认开启 LLM 判定。

**验收**：两个下游项目现有真实稿回归通过；vendored 副本与权威源字节一致。

---

## 五、里程碑与依赖

```
Stage 0 (评测地基) ──┬──▶ Stage 1 (① 激活)  ─┐
                     │                          ├──▶ Stage 3 (③ LLM 判定) ──▶ Stage 4 (传播)
                     └──▶ Stage 2A (trigram) ──▶ Stage 2B (向量, 条件) ─┘
```

- Stage 0 是硬前置，阻塞所有后续。
- Stage 1 与 2A 可并行。
- Stage 2B 由 Stage 0 的近义型占比 + Stage 2A 的剩余漏检**共同门控**，可能不做。
- Stage 3 依赖 Stage 2 提供的候选证据上下文。
- 工时合计：约 8–10 人日（不含 2B 则 6–8）。

---

## 六、风险与决策点

| 项 | 说明 | 缓解 |
|---|---|---|
| 数据外发 | 2B/3 把参考正文/声明发去 DashScope/DeepSeek。SaaS 多用户需过隐私 | `--embed`/`--llm-judge` 显式开关 + 本地回退；默认档纯本地 |
| 成本/延迟 | 每任务 embedding + 逐声明 LLM 判定。SaaS 规模需估 QPS 与单任务成本 | v4 抽取实测 <1 美分/文档；判定按 top-k 截断；结果可缓存 |
| Python 向量栈 | 避免重依赖 | 纯 urllib + sqlite BLOB + 手写 cosine，百篇级语料够快 |
| CLI 契约破坏 | 下游脚本依赖 JSON 字段 | 字段只增不改；Stage 4 前跑下游回归 |
| v4 抽取稳定性 | JSON 解析失败/漏抽 | 分块重试 + 失败块降级离线，不整体失败 |

**需你拍板的决策点**：
1. **v4"失效"归类**：(a) 未接线 还是 (b) 抽取质量问题？→ 定 Stage 1 工作量。
2. **SaaS 默认档**：gongwen_web_agent 是否默认开 `--embed` / `--llm-judge`？（成本 vs 召回 vs 隐私）
3. **2B 触发阈值**：近义型漏检占比达到多少才投向量层？（建议 ≥ 20% 才做）

---

## 七、验收总标准

- 标注集上：总 Recall@5 较 v3 基线显著提升，且**近义型分层 Recall** 是主要增量来源。
- 假✓（规则误确认）率下降，复合声明逐子命题判定可追溯。
- 默认档（纯本地）已优于现状且零外发；开关档收益经 eval 量化。
- 下游两项目真实稿回归通过，vendored 副本与权威源字节一致。
- 全过程无静默截断：任何被跳过的参考资料（OCR/转换失败）在结果中显式披露。
