#!/usr/bin/env python3
"""
实验：结构化字段（命中数字/实体）对 --classify 分类质量的影响

对比两种 prompt 设计：
  A. 仅文本（当前生产方案）
  B. 文本 + 结构化字段（候选优化方案）

评估维度：
  1. 分类结果差异（哪些条目从不可核实→可核实，或反向）
  2. 人工判断可信度（对差异条目人工标注是否合理）
  3. 耗时对比（字段更多，token 更多，是否更慢）

用法：
  cd /Users/hongyu/project/doc-fact-check
  python3 eval/experiment_classify_metadata.py
"""

import json
import os
import re
import time
import urllib.request

STATEMENTS_JSON = "/Users/hongyu/project/讲话稿/txt_output/checklist_result.json"
BATCH_SIZE = 20

# ── Prompt A：仅文本（当前生产方案）────────────────────────────

_PROMPT_A = """\
判断以下每条声明是否为「可核实型」。不需要查阅证据，只根据声明文本本身判断。

- yes：声明含有具体数字、具名奖项/认证/专项名称/排名/政策文号/特定事件，
       原则上可通过查阅官方文件或权威资料加以核实
- no ：声明为修辞表达、过渡句、段落标题、通用原则、信念陈述或领导人语录，
       本身不含独立的可查证事实主张

输出严格 JSON，不加代码块：
{"results": [{"index": 1, "verifiable": "yes|no"}]}
每条 index 与输入编号一一对应，不得遗漏。
"""

# ── Prompt B：文本 + 结构化字段（候选优化方案）──────────────────

_PROMPT_B = """\
判断以下每条声明是否为「可核实型」（即：是否值得去查原始文件核实）。

每条附有自动提取的「数字」「实体」字段作为辅助参考。

- yes：声明含任何可查验的具体内容——具体数字、领导人引语、政策名称、
       认证/专项/机构名称等，写稿人均有可能出错，值得核对原始依据
- no ：纯过渡句、修辞句或段落结构句，不含任何可查证内容
      （例："下面我从八个方面"、"没有改革就没有发展"、"二是……"）

规律：「数字」或「实体」非空 → 通常为 yes；两者皆空 → 通常为 no。

输出严格 JSON，不加代码块：
{"results": [{"index": 1, "verifiable": "yes|no"}]}
每条 index 与输入编号一一对应，不得遗漏。
"""


def _llm_config():
    key   = os.environ.get("FACTCHECK_LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", "")
    base  = os.environ.get("FACTCHECK_LLM_BASE_URL", "https://api.deepseek.com")
    model = os.environ.get("FACTCHECK_LLM_MODEL", "deepseek-chat")
    return key, base, model


def _call_llm(sys_prompt: str, user_msg: str, n_items: int, timeout: int = 60) -> list | None:
    key, base, model = _llm_config()
    if not key:
        print("  ⚠ 无 API key")
        return None
    max_tokens = max(100, n_items * 30)
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": sys_prompt},
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
        print(f"  ⚠ 调用失败: {e}")
        return None


def build_msg_a(batch: list[dict]) -> str:
    """仅文本"""
    parts = []
    for seq, item in enumerate(batch, 1):
        parts.append(f"【{seq}】{item.get('表述内容', '')}")
    return "\n".join(parts)


def build_msg_b(batch: list[dict]) -> str:
    """文本 + 结构化字段"""
    parts = []
    for seq, item in enumerate(batch, 1):
        claim    = item.get("表述内容", "")
        numbers  = item.get("命中数字", [])
        entities = item.get("命中实体", [])
        line = f"【{seq}】{claim}"
        meta = []
        if numbers:
            meta.append(f"数字：{', '.join(str(n) for n in numbers)}")
        if entities:
            meta.append(f"实体：{', '.join(str(e) for e in entities[:5])}")
        if meta:
            line += f"\n  （{' / '.join(meta)}）"
        parts.append(line)
    return "\n".join(parts)


def run_classify(items: list[dict], sys_prompt: str, msg_builder, label: str):
    """对 items 批量分类，返回 (结果字典 index→yes/no, 耗时, API调用次数)"""
    results_map = {}
    t0 = time.time()
    api_calls = 0

    for start in range(0, len(items), BATCH_SIZE):
        batch = items[start:start + BATCH_SIZE]
        user_msg = msg_builder(batch)
        results  = _call_llm(sys_prompt, user_msg, len(batch))
        api_calls += 1

        if results is None:
            continue

        for r in results:
            idx = r.get("index")
            if isinstance(idx, int) and 1 <= idx <= len(batch):
                global_idx = start + idx - 1
                results_map[global_idx] = r.get("verifiable", "")

    elapsed = time.time() - t0
    return results_map, elapsed, api_calls


def main():
    with open(STATEMENTS_JSON, encoding="utf-8") as f:
        all_items = json.load(f)

    # 只对 ✗ 未找到条目做实验（与生产一致）
    x_items = [(i, item) for i, item in enumerate(all_items)
               if "✗" in item.get("状态", "")]
    items_only = [item for _, item in x_items]

    print("=" * 60)
    print("实验：结构化字段对 classify 分类质量的影响")
    print("=" * 60)
    print(f"✗ 条目数：{len(items_only)}　批大小：{BATCH_SIZE}")
    print(f"预计 API 调用：{-(-len(items_only) // BATCH_SIZE)} 次 × 2 组 = "
          f"{-(-len(items_only) // BATCH_SIZE) * 2} 次")
    print()

    print("▶ 运行 方案A（仅文本）...")
    map_a, t_a, calls_a = run_classify(items_only, _PROMPT_A, build_msg_a, "A")
    print(f"  完成：{t_a:.1f}s，{calls_a} 次 API 调用")

    print("▶ 运行 方案B（文本+字段）...")
    map_b, t_b, calls_b = run_classify(items_only, _PROMPT_B, build_msg_b, "B")
    print(f"  完成：{t_b:.1f}s，{calls_b} 次 API 调用")

    # ── 统计 ──────────────────────────────────────────────────
    yes_a = sum(1 for v in map_a.values() if v == "yes")
    yes_b = sum(1 for v in map_b.values() if v == "yes")

    # 差异条目
    a_to_b_yes  = []  # A=no → B=yes（新增可核实）
    a_to_b_no   = []  # A=yes → B=no（退回不可核实）
    both_yes    = []
    both_no     = []

    for i, item in enumerate(items_only):
        va = map_a.get(i, "?")
        vb = map_b.get(i, "?")
        claim = item.get("表述内容", "")
        numbers  = item.get("命中数字", [])
        entities = item.get("命中实体", [])
        row = (claim, numbers, entities, va, vb)
        if va == "no"  and vb == "yes": a_to_b_yes.append(row)
        elif va == "yes" and vb == "no":  a_to_b_no.append(row)
        elif va == "yes" and vb == "yes": both_yes.append(row)
        else:                             both_no.append(row)

    print()
    print("=" * 60)
    print("结果对比")
    print("=" * 60)
    print(f"方案A  可核实：{yes_a} 条　不可核实：{len(map_a)-yes_a} 条　耗时：{t_a:.1f}s")
    print(f"方案B  可核实：{yes_b} 条　不可核实：{len(map_b)-yes_b} 条　耗时：{t_b:.1f}s")
    print(f"时间差：{t_b - t_a:+.1f}s")

    print()
    print(f"── A=no → B=yes（{len(a_to_b_yes)} 条，B 新增可核实）──")
    for claim, nums, ents, va, vb in a_to_b_yes:
        print(f"  · {claim[:65]}")
        if nums:  print(f"      数字：{nums}")
        if ents:  print(f"      实体：{ents[:3]}")

    print()
    print(f"── A=yes → B=no（{len(a_to_b_no)} 条，B 退回不可核实）──")
    for claim, nums, ents, va, vb in a_to_b_no:
        print(f"  · {claim[:65]}")
        if nums:  print(f"      数字：{nums}")
        if ents:  print(f"      实体：{ents[:3]}")

    print()
    print(f"── 两方案一致：可核实 {len(both_yes)} 条 / 不可核实 {len(both_no)} 条 ──")

    # ── 保存 ──────────────────────────────────────────────────
    out = {
        "summary": {
            "total_x": len(items_only),
            "A_yes": yes_a, "A_no": len(map_a) - yes_a, "A_time": round(t_a, 1),
            "B_yes": yes_b, "B_no": len(map_b) - yes_b, "B_time": round(t_b, 1),
        },
        "A_no_to_B_yes": [
            {"claim": r[0], "numbers": r[1], "entities": r[2]} for r in a_to_b_yes
        ],
        "A_yes_to_B_no": [
            {"claim": r[0], "numbers": r[1], "entities": r[2]} for r in a_to_b_no
        ],
        "both_yes": [r[0] for r in both_yes],
        "both_no":  [r[0] for r in both_no],
    }
    out_path = "/Users/hongyu/project/doc-fact-check/eval/experiment_classify_metadata_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n详细结果已保存：{out_path}")


if __name__ == "__main__":
    main()
