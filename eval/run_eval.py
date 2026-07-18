#!/usr/bin/env python3
"""
eval/run_eval.py — Evaluation harness for doc-fact-check pipeline.

Loads eval/factcheck-recall.jsonl, matches each annotated claim against
the pipeline's checklist_result.json output, and reports:
  - Recall@1 and Recall@5 overall
  - Same split by 漏检类型 (精确型 / 近义型)
  - Per-status confusion matrix
  - Average latency per document run
  - External API call count (reads from pipeline JSON if available)

Usage:
  python3 eval/run_eval.py [--pipeline scripts/doc_fact_check.py]
  python3 eval/run_eval.py --json txt_output/checklist_result.json

The harness works in two modes:
  1. Run mode (default): calls the pipeline via subprocess and captures output.
     Requires --target and --ref-dir to be set (or uses defaults if present).
  2. JSON mode (--json): reads an already-produced checklist_result.json
     and compares it against the JSONL annotations. This is the fast path
     when the pipeline has already been run.
"""

import argparse
import json
import os
import sys
import time
import subprocess
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent
DEFAULT_JSONL = SCRIPT_DIR / "factcheck-recall.jsonl"
DEFAULT_PIPELINE = REPO_ROOT / "scripts" / "doc_fact_check.py"

# Default corpus paths — adjust if your workspace differs
DEFAULT_TARGET = Path("/Users/hongyu/project/讲话稿/第三部分-实践探索.docx")
DEFAULT_REF_DIR = Path("/Users/hongyu/project/讲话稿/参考资料/")
DEFAULT_JSON_OUTPUT = Path("/Users/hongyu/project/讲话稿/txt_output/checklist_result.json")


# ── JSONL loading ──────────────────────────────────────────

def load_annotations(jsonl_path: Path):
    """Load and validate eval/factcheck-recall.jsonl."""
    annotations = []
    if not jsonl_path.exists():
        print(f"[警告] 标注集不存在: {jsonl_path}", file=sys.stderr)
        return annotations

    with open(jsonl_path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"[警告] 第{lineno}行 JSON 解析失败: {e}", file=sys.stderr)
                continue

            # Validate required fields
            missing = [k for k in ("声明文本", "期望状态", "漏检类型") if k not in rec]
            if missing:
                print(f"[警告] 第{lineno}行缺少字段: {missing}", file=sys.stderr)
                continue
            annotations.append(rec)

    return annotations


# ── Pipeline runner ────────────────────────────────────────

def run_pipeline(pipeline_path: Path, target: Path, ref_dir: Path,
                 output_xlsx: Path) -> tuple[float, int]:
    """
    Call the pipeline as a subprocess.
    Returns (elapsed_seconds, api_call_count).
    api_call_count is estimated from subprocess output (0 for v3 which has no API calls).
    """
    cmd = [
        sys.executable, str(pipeline_path),
        str(target), str(ref_dir), str(output_xlsx)
    ]
    t0 = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True)
    elapsed = time.time() - t0

    if result.returncode != 0:
        print(f"[错误] 管线运行失败 (returncode={result.returncode})")
        print(result.stderr[-500:])
        return elapsed, 0

    # Count API calls heuristically: look for LLM call markers in output
    api_calls = result.stdout.count("[LLM]") + result.stdout.count("API call")
    return elapsed, api_calls


# ── Result loader ──────────────────────────────────────────

def load_pipeline_results(json_path: Path) -> list[dict]:
    """Load checklist_result.json produced by the pipeline."""
    if not json_path.exists():
        print(f"[错误] 管线输出不存在: {json_path}", file=sys.stderr)
        return []
    with open(json_path, encoding="utf-8") as f:
        return json.load(f)


# ── Matching logic ─────────────────────────────────────────

def normalize_status(s: str) -> str:
    """Normalize status strings for comparison."""
    if "✓" in s or "已确认" in s:
        return "confirmed"
    if "未找到" in s:
        return "not_found"
    if "△" in s or "部分" in s or "需核实" in s or "数据不一致" in s:
        return "partial"
    return "other"


def match_claim_to_results(claim_text: str, results: list[dict]) -> dict | None:
    """
    Find the pipeline result entry that best matches the annotated claim text.

    Strategy:
    1. Exact match on 表述内容
    2. Substring match (claim text contained in result, or vice versa, > 30 chars overlap)
    """
    # Exact match first
    for r in results:
        if r.get("表述内容", "") == claim_text:
            return r

    # Substring match: find longest common overlap
    best = None
    best_overlap = 0
    claim_stripped = claim_text.replace(" ", "")

    for r in results:
        result_text = r.get("表述内容", "").replace(" ", "")
        # Check if a significant portion of the claim appears in the result text
        # Use a sliding window: find the longest common substring
        min_len = min(len(claim_stripped), len(result_text))
        for length in range(min(min_len, len(claim_stripped)), 15, -1):
            substr = claim_stripped[:length]
            if substr in result_text:
                if length > best_overlap:
                    best_overlap = length
                    best = r
                break

    if best_overlap >= 20:
        return best
    return None


# ── Recall calculation ─────────────────────────────────────

def compute_recall(annotations: list[dict], results: list[dict]) -> dict:
    """
    Compute Recall@1 and Recall@5 by checking whether the pipeline
    found the expected source for each annotation.

    For Recall@1: the top-1 result status matches expected status
    For Recall@5: the expected source appears anywhere in results
                  (proxy: result has any source mention matching expected)

    Since v3 outputs one result per claim (not a ranked list), we treat
    Recall@1 as: status match + source found,
    and Recall@5 as: status match OR source partial match.
    """
    metrics = {
        "total": 0,
        "recall_1_hits": 0,
        "recall_5_hits": 0,
        "by_type": {"精确型": {"total": 0, "recall_1": 0, "recall_5": 0},
                    "近义型": {"total": 0, "recall_1": 0, "recall_5": 0}},
        "confusion": {},
        "unmatched": [],
        "details": [],
    }

    for ann in annotations:
        claim = ann["声明文本"]
        expected_status = normalize_status(ann["期望状态"])
        expected_source = ann.get("期望出处", {}).get("sourcePath", "")
        expected_fragment = ann.get("期望出处", {}).get("片段", "")
        miss_type = ann.get("漏检类型", "未知")

        matched_result = match_claim_to_results(claim, results)

        metrics["total"] += 1
        if miss_type in metrics["by_type"]:
            metrics["by_type"][miss_type]["total"] += 1

        if matched_result is None:
            metrics["unmatched"].append(claim[:60])
            predicted_status = "not_found"
        else:
            predicted_status = normalize_status(matched_result.get("状态", ""))

        # Confusion matrix
        key = f"{expected_status}→{predicted_status}"
        metrics["confusion"][key] = metrics["confusion"].get(key, 0) + 1

        # Recall@1: status must match exactly
        recall_1_hit = (predicted_status == expected_status)

        # Recall@5 (relaxed): status matches OR source found (v3 single-result proxy)
        # For "not_found" expected: both recall_1 and recall_5 use same criterion
        # For "confirmed/partial" expected: check if any source keyword appears
        recall_5_hit = recall_1_hit
        if not recall_5_hit and matched_result is not None:
            if expected_source:
                # Check if the expected source file name is in the result's 出处
                src_name = Path(expected_source).stem[:20]
                result_source = matched_result.get("出处", "")
                if src_name and src_name in result_source:
                    recall_5_hit = True
            if expected_fragment and matched_result.get("原文片段", ""):
                # Check if a good portion of the fragment appears in the pipeline result
                frag_short = expected_fragment[:20]
                if frag_short in matched_result.get("原文片段", ""):
                    recall_5_hit = True

        if recall_1_hit:
            metrics["recall_1_hits"] += 1
        if recall_5_hit:
            metrics["recall_5_hits"] += 1

        if miss_type in metrics["by_type"]:
            if recall_1_hit:
                metrics["by_type"][miss_type]["recall_1"] += 1
            if recall_5_hit:
                metrics["by_type"][miss_type]["recall_5"] += 1

        metrics["details"].append({
            "claim": claim[:80],
            "expected": ann["期望状态"],
            "predicted": matched_result.get("状态", "—") if matched_result else "—（未匹配）",
            "recall_1": recall_1_hit,
            "recall_5": recall_5_hit,
            "type": miss_type,
        })

    return metrics


# ── Report printer ─────────────────────────────────────────

def print_report(metrics: dict, elapsed: float = 0.0, api_calls: int = 0,
                 total_docs: int = 1):
    """Print the evaluation report to stdout."""
    total = metrics["total"]
    if total == 0:
        print("[警告] 标注集为空，无法计算指标")
        return

    r1 = metrics["recall_1_hits"] / total
    r5 = metrics["recall_5_hits"] / total

    print()
    print("=" * 60)
    print("  doc-fact-check 评测报告")
    print("=" * 60)
    print(f"  标注样本总数:    {total}")
    print(f"  未匹配到管线结果: {len(metrics['unmatched'])} 条")
    print()
    print(f"  Recall@1 (状态精确匹配): {r1:.1%}  ({metrics['recall_1_hits']}/{total})")
    print(f"  Recall@5 (状态或出处匹配): {r5:.1%}  ({metrics['recall_5_hits']}/{total})")
    print()

    print("  按漏检类型分层:")
    for t, v in metrics["by_type"].items():
        n = v["total"]
        if n == 0:
            continue
        r1t = v["recall_1"] / n
        r5t = v["recall_5"] / n
        print(f"    {t:6s}: Recall@1={r1t:.1%}  Recall@5={r5t:.1%}  (n={n})")
    print()

    print("  混淆矩阵 (期望状态→预测状态):")
    for k, cnt in sorted(metrics["confusion"].items()):
        expected_s, predicted_s = k.split("→")
        marker = "✓" if expected_s == predicted_s else "✗"
        print(f"    {marker}  {k:<30s}  {cnt} 条")
    print()

    if elapsed > 0:
        avg_latency = elapsed / max(total_docs, 1)
        print(f"  管线运行耗时: {elapsed:.1f}s  (平均 {avg_latency:.1f}s/文档)")
    print(f"  外部 API 调用次数: {api_calls}  (v3 纯本地，应为 0)")
    print()

    # Detail table
    print("  逐条明细:")
    print(f"  {'#':>3}  {'类型':6s}  {'R@1':>4}  {'R@5':>4}  {'期望':8s}  {'预测':10s}  声明文本")
    print("  " + "-" * 100)
    for i, d in enumerate(metrics["details"], 1):
        r1_sym = "✓" if d["recall_1"] else "✗"
        r5_sym = "✓" if d["recall_5"] else "✗"
        expected_short = d["expected"][:8]
        predicted_short = d["predicted"][:10]
        print(f"  {i:>3}  {d['type']:6s}  {r1_sym:>4}  {r5_sym:>4}  {expected_short:8s}  {predicted_short:10s}  {d['claim']}")
    print("=" * 60)

    if metrics["unmatched"]:
        print()
        print("  [注意] 以下标注条目未在管线输出中找到匹配:")
        for u in metrics["unmatched"]:
            print(f"    - {u}")


# ── Main ───────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="doc-fact-check 评测工具 — 对比标注集与管线输出，计算 Recall 指标"
    )
    parser.add_argument(
        "--pipeline",
        default=str(DEFAULT_PIPELINE),
        help=f"管线脚本路径 (默认: {DEFAULT_PIPELINE})"
    )
    parser.add_argument(
        "--json",
        default=None,
        help="直接指定已有的 checklist_result.json 路径（跳过重新运行管线）"
    )
    parser.add_argument(
        "--jsonl",
        default=str(DEFAULT_JSONL),
        help=f"标注集 JSONL 路径 (默认: {DEFAULT_JSONL})"
    )
    parser.add_argument(
        "--target",
        default=str(DEFAULT_TARGET),
        help=f"目标文档路径 (默认: {DEFAULT_TARGET})"
    )
    parser.add_argument(
        "--ref-dir",
        default=str(DEFAULT_REF_DIR),
        help=f"参考文档目录 (默认: {DEFAULT_REF_DIR})"
    )
    parser.add_argument(
        "--rerun",
        action="store_true",
        help="强制重新运行管线（即使 checklist_result.json 已存在）"
    )

    args = parser.parse_args()

    # Load annotations
    jsonl_path = Path(args.jsonl)
    annotations = load_annotations(jsonl_path)

    if not annotations:
        print("[警告] 标注集为空或加载失败，无法评测", file=sys.stderr)
        print("  请确保标注集文件存在且格式正确:", jsonl_path)
        sys.exit(0)

    print(f"加载标注集: {len(annotations)} 条  ({jsonl_path})")

    # Determine pipeline results source
    elapsed = 0.0
    api_calls = 0

    if args.json:
        # JSON mode: use existing file
        json_path = Path(args.json)
        print(f"使用已有管线输出: {json_path}")
    else:
        json_path = DEFAULT_JSON_OUTPUT
        pipeline_path = Path(args.pipeline)

        # Run pipeline if needed
        if args.rerun or not json_path.exists():
            target = Path(args.target)
            ref_dir = Path(args.ref_dir)

            if not target.exists():
                print(f"[错误] 目标文档不存在: {target}", file=sys.stderr)
                print("  请使用 --target 指定目标文档路径，或先手动运行管线")
                sys.exit(1)
            if not ref_dir.exists():
                print(f"[错误] 参考文档目录不存在: {ref_dir}", file=sys.stderr)
                print("  请使用 --ref-dir 指定参考文档目录")
                sys.exit(1)
            if not pipeline_path.exists():
                print(f"[错误] 管线脚本不存在: {pipeline_path}", file=sys.stderr)
                sys.exit(1)

            output_xlsx = Path("/tmp/eval-run.xlsx")
            print(f"运行管线: {pipeline_path}")
            print(f"  目标文档: {target}")
            print(f"  参考目录: {ref_dir}")
            elapsed, api_calls = run_pipeline(pipeline_path, target, ref_dir, output_xlsx)
            print(f"  管线完成，耗时 {elapsed:.1f}s，API 调用 {api_calls} 次")
        else:
            print(f"使用已有管线输出 (--rerun 可强制重跑): {json_path}")

    # Load pipeline results
    results = load_pipeline_results(json_path)
    if not results:
        print("[错误] 管线输出为空或加载失败", file=sys.stderr)
        sys.exit(1)

    print(f"管线输出: {len(results)} 条结果  ({json_path})")

    # Compute metrics
    metrics = compute_recall(annotations, results)

    # Print report
    print_report(metrics, elapsed=elapsed, api_calls=api_calls, total_docs=1)

    # Exit code: 0 always (evaluation is informational)
    sys.exit(0)


if __name__ == "__main__":
    main()
