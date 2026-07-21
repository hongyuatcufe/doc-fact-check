#!/usr/bin/env python3
"""
前置 LLM 过滤实验

目标：验证把 classify 移到检索前（带前后文）之后：
  1. 过滤比例和耗时
  2. eval 集 16 条目标声明是否全部存活（Recall 不受损）
  3. 与现有 --classify 后置方案的精度差异

用法：
  cd /Users/hongyu/project/doc-fact-check
  python3 eval/experiment_prefilter.py
"""

import json
import os
import re
import sys
import time
import urllib.request

# ── 配置 ──────────────────────────────────────────────────────

STATEMENTS_JSON = "/Users/hongyu/project/讲话稿/txt_output/checklist_result.json"
EVAL_JSONL      = "/Users/hongyu/project/doc-fact-check/eval/factcheck-recall.jsonl"
BATCH_SIZE      = 20

_SYS_PROMPT = """\
你是公文事实核查的"声明筛选助手"。
给你若干条编号句子（每条附有前句和后句作为上下文），判断每条句子是否包含"需要核实的具体事实声明"。

【需要核实的】：含具体数字/具名奖项/认证名称/排名/政策文号/专项名称/事件成果，
              可通过查阅官方文件或权威资料加以核实。
【不需要核实的】：修辞表达、过渡句、段落标题、通用原则、信念陈述、领导人引语、
               工作方法描述（不含具体数据）。

判断时请综合考虑上下文——同一句话在不同段落语境下可能性质不同。

输出严格 JSON，不加代码块：
{"results": [{"index": 1, "needs_check": true}]}
每条 index 与输入编号一一对应，不得遗漏。宁可多留，不可漏掉真声明。
"""


def _llm_config():
    key  = os.environ.get("FACTCHECK_LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", "")
    base = os.environ.get("FACTCHECK_LLM_BASE_URL", "https://api.deepseek.com")
    model = os.environ.get("FACTCHECK_LLM_MODEL", "deepseek-chat")
    return key, base, model


def _call_llm(user_msg: str, n_items: int, timeout: int = 60) -> list | None:
    key, base, model = _llm_config()
    if not key:
        print("  ⚠ 无 API key")
        return None
    max_tokens = max(100, n_items * 30)
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": _SYS_PROMPT},
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
        return results if isinstance(results, list) else None
    except Exception as e:
        print(f"  ⚠ API 调用失败: {e}")
        return None


def build_batch_msg(batch: list[tuple[int, dict, str, str]]) -> str:
    """
    batch: [(seq, item, prev_text, next_text), ...]
    """
    parts = []
    for seq, item, prev_text, next_text in batch:
        claim = item.get("表述内容", "")
        line  = f"【{seq}】{claim}"
        ctx   = []
        if prev_text:
            ctx.append(f"  前句：{prev_text[:60]}")
        if next_text:
            ctx.append(f"  后句：{next_text[:60]}")
        parts.append(line)
        if ctx:
            parts.extend(ctx)
        parts.append("")
    return "\n".join(parts)


def run_prefilter(statements: list[dict]) -> tuple[list[dict], float, int]:
    """
    返回：(过滤后保留的 statements, 耗时秒, API调用次数)
    """
    n = len(statements)
    texts = [s.get("表述内容", "") for s in statements]

    kept   = []
    t0     = time.time()
    api_calls = 0

    for start in range(0, n, BATCH_SIZE):
        end   = min(start + BATCH_SIZE, n)
        batch = []
        for i in range(start, end):
            prev_text = texts[i - 1] if i > 0 else ""
            next_text = texts[i + 1] if i < n - 1 else ""
            batch.append((i - start + 1, statements[i], prev_text, next_text))

        user_msg = build_batch_msg(batch)
        results  = _call_llm(user_msg, len(batch))
        api_calls += 1

        if results is None:
            # 降级：全部保留
            kept.extend(statements[start:end])
            continue

        result_map = {r.get("index"): r for r in results
                      if isinstance(r.get("index"), int) and 1 <= r.get("index") <= len(batch)}

        for i, (seq, item, _, _) in enumerate(batch, 1):
            r = result_map.get(i)
            if r is None or r.get("needs_check", True):
                kept.append(item)

    elapsed = time.time() - t0
    return kept, elapsed, api_calls


def check_eval_survival(kept: list[dict], eval_cases: list[dict]) -> list[dict]:
    """
    检查 eval 声明是否在过滤后的集合中（用子串匹配）。
    """
    kept_texts = [s.get("表述内容", "") for s in kept]
    missed = []
    for case in eval_cases:
        claim = case["声明文本"]
        # 双向子串：eval claim 在某条 statement 里，或 statement 在 eval claim 里
        found = any(claim in t or t in claim for t in kept_texts)
        if not found:
            missed.append(case)
    return missed


def main():
    # 加载数据
    with open(STATEMENTS_JSON, encoding="utf-8") as f:
        statements = json.load(f)

    with open(EVAL_JSONL, encoding="utf-8") as f:
        all_cases = [json.loads(l) for l in f if l.strip()]
    eval_cases = [c for c in all_cases if c.get("测试集") == "讲话稿"]

    print("=" * 60)
    print("实验：前置 LLM 过滤（带上下文）")
    print("=" * 60)
    print(f"输入：{len(statements)} 条表述")
    print(f"Eval：{len(eval_cases)} 条讲话稿目标声明")
    print(f"批大小：{BATCH_SIZE}，预计 {-(-len(statements) // BATCH_SIZE)} 次 API 调用")
    print()

    kept, elapsed, api_calls = run_prefilter(statements)

    filtered_out = len(statements) - len(kept)
    missed_eval  = check_eval_survival(kept, eval_cases)

    print()
    print("=" * 60)
    print("实验结果")
    print("=" * 60)
    print(f"耗时：{elapsed:.1f}s　　API 调用：{api_calls} 次")
    print(f"过滤前：{len(statements)} 条")
    print(f"过滤后：{len(kept)} 条（过滤掉 {filtered_out} 条，"
          f"减少 {filtered_out/len(statements)*100:.0f}%）")
    print()
    print(f"Eval 存活率：{len(eval_cases)-len(missed_eval)}/{len(eval_cases)}", end="")
    if missed_eval:
        print(f"  ⚠ {len(missed_eval)} 条被过滤掉：")
        for c in missed_eval:
            print(f"  · {c['声明文本'][:70]}")
    else:
        print("  ✓ 全部存活，Recall 不受损")

    print()
    print("过滤后保留的条目（前20条）：")
    for i, s in enumerate(kept[:20], 1):
        print(f"  {i:2d}. {s['表述内容'][:70]}")

    # 保存结果供后续分析
    out_path = "/Users/hongyu/project/doc-fact-check/eval/experiment_prefilter_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "total_in":    len(statements),
            "total_kept":  len(kept),
            "elapsed_sec": round(elapsed, 1),
            "api_calls":   api_calls,
            "eval_missed": [c["声明文本"] for c in missed_eval],
            "kept_texts":  [s["表述内容"] for s in kept],
        }, f, ensure_ascii=False, indent=2)
    print(f"\n详细结果已保存：{out_path}")


if __name__ == "__main__":
    main()
