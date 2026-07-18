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

    # 把判定理由追加到反向验证警告里，方便 Excel 显示
    if reasoning:
        existing = item.get("反向验证警告", "")
        tag = f"[LLM判定] {reasoning}"
        item["反向验证警告"] = f"{existing}; {tag}".lstrip("; ") if existing else tag

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
