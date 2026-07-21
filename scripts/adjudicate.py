#!/usr/bin/env python3
"""
Stage 3 — LLM 判定层（adjudicate.py）

对每条声明 + retrieval 候选 chunk，调 LLM 逐子命题核对，
把"命中关键词"升级为"证据真的支持声明"。

开关：--llm-judge（在 doc_fact_check.py 中透传）
默认关闭，原因：LLM 判定增加每任务延迟，影响用户体验。
Stage 3 完成后做延迟测试，决定是否改默认。

降级安全：LLM 不可用（无 key / 超时 / 解析失败）时保留原规则判定结果，
状态附注"未经 LLM 复核"。

模型配置（OpenAI 兼容，同 factcheck_llm.py）：
  FACTCHECK_LLM_API_KEY   （缺省回退 DEEPSEEK_API_KEY）
  FACTCHECK_LLM_BASE_URL  （缺省 https://api.deepseek.com）
  FACTCHECK_LLM_MODEL     （缺省 deepseek-chat）
"""

import os
import re
import json
import urllib.request
import urllib.error

# ── 判定语义 ──────────────────────────────────────────────────
# confirmed    : 同一证据上下文支持全部要素，必须 ≥1 引用
# needs_review : 部分支持 / 名称近似 / 多源口径不一 / 子命题未全覆盖
# inconsistent : 同主体、时间、口径下直接冲突，必须引用冲突证据
# not_found    : 给定参考库无支持证据（≠ 声明为假）

_STATUS_MAP = {
    "confirmed":    "✓ 已确认",
    "needs_review": "△ 需核实",
    "inconsistent": "△ 数据不一致",
    "not_found":    "✗ 未找到",
}

_SYS_PROMPT = """\
你是公文事实核查的"判定助手"。给你一条【待核实声明】和若干【参考证据片段】，
你的唯一任务是：判断参考证据是否支持该声明，逐子命题核对。

【判定规则】
- confirmed（已确认）：参考证据明确支持声明的全部要素（主体/时间/数字/单位/专名/口径），
  必须给出至少1条引用原文。近似允许合理舍入（如 42.87亿≈43亿）。
- needs_review（需核实）：证据仅部分支持，或名称近似但不完全一致，或多源口径不一，
  或复合声明中有子命题未覆盖。
- inconsistent（数据不一致）：同主体、同时间范围、同口径下，证据与声明直接冲突
  （如声明"50%"但证据写"45%"）。必须引用冲突原文。
- not_found（未找到）：给定证据片段中无任何支持，不代表声明为假，只代表参考库未覆盖。

【严格禁止】
- 不得根据常识或训练知识判断声明真假，只能依据给定证据。
- 不得把"证据多列"判为冲突（列举≠冲突）。
- 不得因增长率/总数/分项分布在不同句子而降级，只要能逐项对应即可。

【输出格式】严格 JSON，不加 markdown 代码块：
{
  "verdict": "confirmed|needs_review|inconsistent|not_found",
  "citation": "最关键的证据原文片段（≤120字，confirmed/inconsistent 必填，其余可空）",
  "reasoning": "一句话说明判定理由（≤60字）"
}
"""


def _llm_config():
    key = (os.environ.get("FACTCHECK_LLM_API_KEY")
           or os.environ.get("DEEPSEEK_API_KEY", ""))
    base = os.environ.get("FACTCHECK_LLM_BASE_URL", "https://api.deepseek.com")
    model = os.environ.get("FACTCHECK_LLM_MODEL", "deepseek-chat")
    return key, base, model


def _call_llm(user_msg: str, timeout: int = 60) -> dict | None:
    key, base, model = _llm_config()
    if not key:
        return None
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": _SYS_PROMPT},
            {"role": "user",   "content": user_msg},
        ],
        "temperature": 0,
        "max_tokens": 300,
        "response_format": {"type": "json_object"},
    }).encode()
    req = urllib.request.Request(
        f"{base.rstrip('/')}/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read())
        content = body["choices"][0]["message"]["content"]
        # 剥除可能的 markdown 代码块
        content = re.sub(r"^```json\s*|```\s*$", "", content.strip(), flags=re.MULTILINE)
        return json.loads(content)
    except Exception:
        return None


def _build_user_msg(item: dict, candidates: list[dict]) -> str:
    claim = item.get("表述内容", "")
    numbers = item.get("命中数字", [])
    entities = item.get("命中实体", [])
    sub_claims = item.get("子命题", [])

    parts = [f"【待核实声明】\n{claim}"]
    if numbers:
        parts.append(f"（声明中的数字：{', '.join(numbers)}）")
    if entities:
        parts.append(f"（声明中的专名：{', '.join(entities)}）")
    if sub_claims and len(sub_claims) > 1:
        parts.append(f"（可分解子命题：{'；'.join(sub_claims)}）")

    parts.append("\n【参考证据片段】")
    for i, cand in enumerate(candidates[:5], 1):
        src = cand.get("sourcePath", "未知来源")
        text = cand.get("text", "")[:400]
        parts.append(f"[{i}] 来源：{src}\n{text}")

    return "\n".join(parts)


def adjudicate_item(item: dict, candidates: list[dict]) -> dict:
    """
    对单条声明 + 候选证据调 LLM 判定。
    返回更新后的 item（in-place 修改并返回）。
    LLM 不可用时保留原规则结果，附注"未经 LLM 复核"。
    """
    if not candidates:
        # 无候选证据，维持原状态
        _append_note(item, "无候选证据，未经 LLM 复核")
        return item

    user_msg = _build_user_msg(item, candidates)
    result = _call_llm(user_msg)

    if result is None:
        _append_note(item, "LLM 不可用，维持规则判定结果")
        return item

    verdict = result.get("verdict", "")
    if verdict not in _STATUS_MAP:
        _append_note(item, f"LLM 返回未知 verdict={verdict!r}，维持规则判定结果")
        return item

    citation = result.get("citation", "").strip()
    reasoning = result.get("reasoning", "").strip()

    item["状态"] = _STATUS_MAP[verdict]
    if citation:
        item["判定引用"] = citation
    if reasoning:
        item["判定理由"] = reasoning

    # 只在有问题的 verdict 时才追加警告，避免 confirmed 条目膨胀计数
    if reasoning and verdict in ("needs_review", "inconsistent"):
        _append_note(item, f"[LLM判定] {reasoning}")

    return item


def adjudicate_all(items: list[dict], verbose: bool = True) -> None:
    """
    对 items 列表做 in-place LLM 判定（--llm-judge 开关触发）。
    只处理有 候选证据 字段的条目（即走过 retrieval 路径的）。
    """
    key, _, model = _llm_config()
    if not key:
        print("  [LLM判定] 无 API key，跳过 LLM 判定（维持规则结果）")
        return

    total = sum(1 for it in items if it.get("候选证据"))
    if verbose:
        print(f"  [LLM判定] 开始判定 {total} 条有候选证据的声明（模型：{model}）")

    done = 0
    for item in items:
        candidates = item.get("候选证据")
        if not candidates:
            continue
        adjudicate_item(item, candidates)
        done += 1
        if verbose and done % 10 == 0:
            print(f"  [LLM判定] {done}/{total}")

    if verbose:
        print(f"  [LLM判定] 完成，共判定 {done} 条")


def _append_note(item: dict, note: str) -> None:
    existing = item.get("反向验证警告", "")
    item["反向验证警告"] = f"{existing}; {note}".lstrip("; ") if existing else note


# ── 批量判定 ──────────────────────────────────────────────────

_BATCH_SYS_PROMPT = """\
你是公文事实核查的"批量判定助手"。给你若干条编号声明，每条附参考证据片段（可能为空），
逐条完成两项任务：①判定证据支持度；②判断声明是否属于"可核实型"。

【判定规则（verdict）】
- confirmed：证据明确支持全部要素（主体/数字/专名/口径），给出引用原文
- needs_review：部分支持/名称近似/数字或口径有出入/多源说法不一
- inconsistent：同主体同口径下证据与声明直接冲突，给出冲突原文
- not_found：给定证据中无任何支持（≠ 声明为假）；证据为空时也选此项

【关键注意】
1. 专项全名匹配：声明涉及某一具名专项时，证据须包含完全同名的专项（主题修饰词 +
   通用词均一致）。证据仅含通用词相同但修饰词不同的其他专项，不构成支持。
2. 标题型声明判定：若声明本身即为专项名称（不含数字或具体数据主张），只要证据中
   明确出现完全同名的专项，即判 confirmed；不需要证据逐条重新列举该专项下的子项。
3. 多条目证据：证据片段可能同时包含多个不同专项，只对声明所指专项评分，其余条目
   不影响该条判定。
4. 不得从证据的"存在"或"相关性"推断声明成立：证据须明确描述声明所陈述的事实，
   而非仅与声明话题相关。无明确表述时选 not_found，不得推断。
5. 不得用常识判断；给定证据无任何支持时选 not_found。

【可核实性判断（verifiable）】
判断声明是否包含可供文件核查的具体事实主张：
- yes：声明含有具体数字、具名奖项/认证/专项、排名、具体数量或特定事件，
       原则上可通过查阅官方文件、数据库或权威资料加以核实。
- no ：声明为修辞表达、过渡语、段落标题、通用原则、观点/信念或领导人语录，
       本身不含独立的可查证事实。

【输出格式】严格 JSON 对象，不加代码块：
{"results": [
  {"index": 1,
   "verdict": "confirmed|needs_review|inconsistent|not_found",
   "verifiable": "yes|no",
   "citation": "最关键证据原文≤80字，confirmed/inconsistent必填其余可空",
   "reasoning": "判定理由≤40字"}
]}
每条 index 与输入编号一一对应，不得遗漏或新增。
"""


def _build_batch_msg(batch: list) -> str:
    """
    batch: list of (item_dict, evidence_list)
    evidence_list: [{"sourcePath": ..., "text": ...}, ...]
    """
    parts = []
    for i, (item, evidence) in enumerate(batch, 1):
        claim    = item.get("表述内容", "")
        numbers  = item.get("命中数字", [])
        entities = item.get("命中实体", [])
        sub      = item.get("子命题", [])

        line = f"【声明 {i}】{claim}"
        if numbers:
            line += f"（数字：{', '.join(numbers)}）"
        if entities:
            line += f"（专名：{', '.join(entities)}）"
        if sub and len(sub) > 1:
            sub_texts = [s["text"] if isinstance(s, dict) else s for s in sub]
            line += f"（子命题：{'；'.join(sub_texts)}）"
        parts.append(line)

        if evidence:
            parts.append("【证据】")
            for j, ev in enumerate(evidence[:3], 1):
                src  = ev.get("sourcePath", "未知来源")
                text = ev.get("text", "")[:500]
                parts.append(f"  [{j}]{src}: {text}")
        else:
            parts.append("【证据】（无候选证据）")
        parts.append("")
    return "\n".join(parts)


def _call_llm_batch(user_msg: str, n_items: int, timeout: int = 90) -> list | None:
    """
    批量 LLM 调用，返回 results 列表（长度 = n_items）或 None（失败）。
    """
    key, base, model = _llm_config()
    if not key:
        return None
    max_tokens = max(600, n_items * 160)
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": _BATCH_SYS_PROMPT},
            {"role": "user",   "content": user_msg},
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }).encode()
    req = urllib.request.Request(
        f"{base.rstrip('/')}/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read())
        content = body["choices"][0]["message"]["content"]
        content = re.sub(r"^```json\s*|```\s*$", "", content.strip(), flags=re.MULTILINE)
        data = json.loads(content)
        results = data.get("results", [])
        if isinstance(results, list):
            return results
        return None
    except Exception:
        return None


def _select_evidence(item: dict) -> list:
    """
    为条目选取证据列表：
    - 优先用 候选证据 字段（retrieval 结果）
    - 其次把 原文片段 包装成单条证据
    """
    cands = item.get("候选证据")
    if cands:
        return cands[:3]
    snippet = item.get("原文片段", "").strip()
    if snippet:
        return [{"sourcePath": item.get("出处", ""), "text": snippet}]
    return []


def adjudicate_batch(items: list, batch_size: int = 6, verbose: bool = True) -> None:
    """
    批量 LLM 判定，N/batch_size 次 API 调用（默认 6 条/批）。

    判定范围：
    - 「✗ 未找到」且有候选证据（retrieval 找到的候选块）
    - 「△」状态或有反向验证警告，且有原文片段

    降级安全：整批 LLM 失败时保留原规则结果，附注"未经 LLM 复核"。
    """
    key, _, model = _llm_config()
    if not key:
        if verbose:
            print("  [LLM批判定] 无 API key，跳过（维持规则结果）")
        return

    queue = []
    for item in items:
        status      = item.get("状态", "")
        has_warning = bool(item.get("反向验证警告", ""))
        evidence    = _select_evidence(item)
        if "✗" in status:                        # ✗ 未找到：有无证据均入队（无证据时只做可核实性分类）
            queue.append((item, evidence))
        elif "需核实" in status and evidence:     # △ 需核实（弱匹配，LLM 深判）
            queue.append((item, evidence))
        elif has_warning and "✓" in status and evidence:  # ✓ 已确认 + 反向验证警告（潜在假✓）
            queue.append((item, evidence))
        # △ 部分匹配 / △ 数据不一致：规则层已正确分类，不交 LLM 改写

    if not queue:
        if verbose:
            print("  [LLM批判定] 无需判定的条目")
        return

    total = len(queue)
    if verbose:
        print(f"  [LLM批判定] {total} 条待判定，"
              f"批大小={batch_size}，模型={model}，"
              f"预计 {-(-total // batch_size)} 次 API 调用")

    done = 0
    for start in range(0, total, batch_size):
        batch = queue[start:start + batch_size]
        user_msg = _build_batch_msg(batch)
        results  = _call_llm_batch(user_msg, len(batch))

        if results is None:
            for item, _ in batch:
                _append_note(item, "LLM 批判定失败，维持规则结果")
            done += len(batch)
            continue

        # 按 index 应用结果（容错：index 可能缺失或越界）
        result_map = {}
        for r in results:
            idx = r.get("index")
            if isinstance(idx, int) and 1 <= idx <= len(batch):
                result_map[idx] = r

        for i, (item, evidence) in enumerate(batch, 1):
            r = result_map.get(i)
            if r is None:
                _append_note(item, "LLM 批判定未返回此条结果，维持规则结果")
                continue
            verdict = r.get("verdict", "")
            if verdict not in _STATUS_MAP:
                _append_note(item, f"LLM 返回未知 verdict={verdict!r}，维持规则结果")
                continue
            # 可核实性标签（无论 verdict 结果如何都写入）
            verifiable = r.get("verifiable", "")
            if verifiable in ("yes", "no"):
                item["可核实性"] = "可核实" if verifiable == "yes" else "不可核实"
            # verdict 只在有候选证据时才覆盖状态（无证据的 ✗ 只做分类，不改状态）
            if evidence:
                item["状态"] = _STATUS_MAP[verdict]
            citation  = r.get("citation", "").strip()
            reasoning = r.get("reasoning", "").strip()
            if citation:
                item["判定引用"] = citation
            if reasoning:
                item["判定理由"] = reasoning
                if verdict in ("needs_review", "inconsistent"):
                    _append_note(item, f"[LLM判定] {reasoning}")

        done += len(batch)
        if verbose and done % (batch_size * 2) == 0:
            print(f"  [LLM批判定] {done}/{total}")

    if verbose:
        print(f"  [LLM批判定] 完成，共判定 {done} 条")


# ── 可核实性快速分类（--classify 通道）──────────────────────────

_CLASSIFY_SYS_PROMPT = """\
判断以下每条声明是否为「可核实型」。不需要查阅证据，只根据声明文本本身判断。

- yes：声明含有具体数字、具名奖项/认证/专项名称/排名/政策文号/特定事件，
       原则上可通过查阅官方文件或权威资料加以核实
- no ：声明为修辞表达、过渡句、段落标题、通用原则、信念陈述或领导人语录，
       本身不含独立的可查证事实主张

输出严格 JSON，不加代码块：
{"results": [{"index": 1, "verifiable": "yes|no"}]}
每条 index 与输入编号一一对应，不得遗漏。
"""


def _call_classify_batch(user_msg: str, n_items: int, timeout: int = 60) -> list | None:
    """轻量分类批次调用：只返回 verifiable yes/no，无证据输入，token 极少。"""
    key, base, model = _llm_config()
    if not key:
        return None
    max_tokens = max(100, n_items * 30)
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": _CLASSIFY_SYS_PROMPT},
            {"role": "user",   "content": user_msg},
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }).encode()
    req = urllib.request.Request(
        f"{base.rstrip('/')}/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read())
        content = body["choices"][0]["message"]["content"]
        content = re.sub(r"^```json\s*|```\s*$", "", content.strip(), flags=re.MULTILINE)
        data = json.loads(content)
        results = data.get("results", [])
        if isinstance(results, list):
            return results
        return None
    except Exception:
        return None


def classify_verifiability(items: list, batch_size: int = 20, verbose: bool = True) -> None:
    """
    对 ✗ 未找到条目快速分类可核实性（不看证据，只看声明文本）。
    比 adjudicate_batch 快约 8×：无证据输入，批大小 20，prompt 更短。
    已有 可核实性 标签的条目（来自 --llm-judge）直接跳过。
    """
    key, _, model = _llm_config()
    if not key:
        if verbose:
            print("  [可核实性分类] 无 API key，跳过")
        return

    targets = [item for item in items
               if "✗" in item.get("状态", "") and "可核实性" not in item]

    if not targets:
        if verbose:
            print("  [可核实性分类] 无待分类条目（已全部标注或无 ✗ 条目）")
        return

    total = len(targets)
    if verbose:
        print(f"  [可核实性分类] {total} 条 ✗ 待分类，"
              f"批大小={batch_size}，模型={model}，"
              f"预计 {-(-total // batch_size)} 次 API 调用")

    done = 0
    for start in range(0, total, batch_size):
        batch = targets[start:start + batch_size]
        parts = []
        for seq, item in enumerate(batch, 1):
            parts.append(f"【{seq}】{item.get('表述内容', '')}")
        user_msg = "\n".join(parts)

        results = _call_classify_batch(user_msg, len(batch))
        if results is None:
            done += len(batch)
            continue

        result_map = {r.get("index"): r for r in results
                      if isinstance(r.get("index"), int) and 1 <= r.get("index") <= len(batch)}

        for seq, item in enumerate(batch, 1):
            r = result_map.get(seq)
            if r is None:
                continue
            v = r.get("verifiable", "")
            if v in ("yes", "no"):
                item["可核实性"] = "可核实" if v == "yes" else "不可核实"

        done += len(batch)

    if verbose:
        v_count  = sum(1 for it in targets if it.get("可核实性") == "可核实")
        nv_count = sum(1 for it in targets if it.get("可核实性") == "不可核实")
        print(f"  [可核实性分类] 完成：可核实 {v_count} 条 / 不可核实 {nv_count} 条")


# ── 烟雾测试 ──────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    print("=" * 60)
    print("adjudicate.py 烟雾测试")
    print("=" * 60)

    key, base, model = _llm_config()
    if not key:
        print("  ⚠ 无 API key（FACTCHECK_LLM_API_KEY / DEEPSEEK_API_KEY），")
        print("    跳过 LLM 调用测试，仅测试降级路径。")
        item = {
            "表述内容": "学校获批国家级科研项目356项",
            "命中数字": ["356"],
            "命中实体": ["国家级科研项目"],
            "子命题": ["学校获批国家级科研项目356项"],
            "状态": "✓ 已确认",
            "反向验证警告": "",
        }
        result = adjudicate_item(item, [])
        assert "未经 LLM 复核" in result.get("反向验证警告", ""), "降级路径未触发"
        print("  PASS — 无候选证据降级路径正常")
        sys.exit(0)

    # 有 key：测试真实调用
    test_item = {
        "表述内容": "学校ESI前1%学科达到5个",
        "命中数字": ["5"],
        "命中实体": ["ESI前1%学科"],
        "子命题": ["学校ESI前1%学科达到5个"],
        "状态": "✓ 已确认",
        "反向验证警告": "",
        "候选证据": [
            {
                "sourcePath": "参考文档A",
                "text": "学校现有ESI全球前1%学科5个，分别为经济学、管理学、数学、计算机科学和统计学。",
                "score": 0.9,
            }
        ],
    }
    print(f"  测试声明：{test_item['表述内容']}")
    print(f"  候选证据：{test_item['候选证据'][0]['text'][:60]}...")
    result = adjudicate_item(test_item, test_item["候选证据"])
    print(f"  LLM 判定结果：{result['状态']}")
    print(f"  判定理由：{result.get('判定理由', '（无）')}")
    assert result["状态"] in _STATUS_MAP.values(), "状态值不在合法范围"
    print("  PASS — LLM 判定路径正常")
    print("=" * 60)
    print("adjudicate.py smoke test passed")
    print("=" * 60)
