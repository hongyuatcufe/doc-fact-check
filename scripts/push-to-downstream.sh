#!/usr/bin/env bash
# push-to-downstream.sh
# 将权威源 scripts/ 同步到下游 vendored 副本。
#
# 默认只同步到 gongwen-agent（单机版，仅影响本地）。
# gongwen_web_agent 已上线部署，需显式传 --include-web 才会同步。
#
# 用法：
#   bash scripts/push-to-downstream.sh                  # 只同步 gongwen-agent
#   bash scripts/push-to-downstream.sh --include-web    # 同时同步 gongwen_web_agent
#
# 安全前提：同步前自动检查两个下游是否已是 GitHub 最新版本，有落后则中止。

set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
AGENT_DIR="${AGENT_DIR:-/Users/hongyu/project/pi_agent_build/gongwen-agent}"
WEB_DIR="${WEB_DIR:-/Users/hongyu/project/pi_agent_build/gongwen_web_agent}"
INCLUDE_WEB=false

for arg in "$@"; do
  [[ "$arg" == "--include-web" ]] && INCLUDE_WEB=true
done

SRC="$HERE/scripts"
TARGETS=("$AGENT_DIR/tools/doc-fact-check/scripts")
$INCLUDE_WEB && TARGETS+=("$WEB_DIR/tools/doc-fact-check/scripts")

# ── 同步前检查：下游必须在 GitHub 最新版本 ─────────────────────
check_up_to_date() {
  local repo_dir="$1"
  local name="$2"
  if [[ ! -d "$repo_dir/.git" ]]; then
    echo "❌ $name 不是 git 仓库：$repo_dir" >&2; exit 1
  fi
  pushd "$repo_dir" > /dev/null
  git fetch origin --quiet
  local behind
  behind=$(git rev-list --count HEAD..origin/main 2>/dev/null || echo "?")
  popd > /dev/null
  if [[ "$behind" != "0" ]]; then
    echo "❌ $name 落后 origin/main $behind 个 commit，请先 git pull 再同步。" >&2
    exit 1
  fi
  echo "  ✓ $name 已是 GitHub 最新版本"
}

echo "── 检查下游版本 ──────────────────────────────────────────"
check_up_to_date "$AGENT_DIR" "gongwen-agent"
$INCLUDE_WEB && check_up_to_date "$WEB_DIR" "gongwen_web_agent"

# ── 同步文件 ───────────────────────────────────────────────────
SYNC_FILES=(
  doc_fact_check.py
  factcheck_llm.py
  retrieval.py
  adjudicate.py
  entity_config.yaml
  add_category_words.py
)

echo ""
echo "── 同步 scripts/ ─────────────────────────────────────────"
for target in "${TARGETS[@]}"; do
  echo "  → $target"
  for f in "${SYNC_FILES[@]}"; do
    src_file="$SRC/$f"
    if [[ ! -f "$src_file" ]]; then
      echo "    ⚠ 跳过（源文件不存在）：$f"
      continue
    fi
    cp "$src_file" "$target/$f"
    echo "    · $f"
  done
done

echo ""
echo "── 同步 docs (SKILL.md / REVIEW-CHECKLIST.md) ───────────"
DOC_FILES=(SKILL.md REVIEW-CHECKLIST.md)
for target in "${TARGETS[@]}"; do
  doc_dir="$(dirname "$target")"   # tools/doc-fact-check/
  echo "  → $doc_dir"
  for f in "${DOC_FILES[@]}"; do
    src_file="$HERE/$f"
    if [[ ! -f "$src_file" ]]; then
      echo "    ⚠ 跳过（源文件不存在）：$f"
      continue
    fi
    cp "$src_file" "$doc_dir/$f"
    echo "    · $f"
  done
done

echo ""
echo "✓ 同步完成。请到下游仓库检查 git status 并决定是否 commit。"
if ! $INCLUDE_WEB; then
  echo "  （gongwen_web_agent 已上线，本次未同步。需要时传 --include-web）"
fi
