#!/usr/bin/env python3
"""
文档表述准确性核对（v4 — LLM 抽取版）。

与 v3(doc_fact_check.py) 的区别只在**前端抽取**：
  v3：正则 + 通用类别词表 提取"表述"和"实体" —— 在公文/讲话稿上会把整句当实体、
      把口号/过渡/引语当事实核，误报极高。
  v4：用一个**便宜的 LLM** 只抽取"可核实的事实性声明 + 其中的专有实体/数字"，
      口号/过渡/纯观点/引语天然不进入管线，误报大幅下降。

后端完全复用 v3：抽取出的声明喂给 doc_fact_check 的 search_in_reference()
（关键词/数字检索 + 实体覆盖 + 实体-语境共现 + 子命题验证 + 张冠李戴反向验证），
再用 doc_fact_check 的 generate_excel()/print_summary() 出表。**不改动 v3 代码。**

抽取模型（OpenAI 兼容，环境变量可配）：
  FACTCHECK_LLM_API_KEY   （缺省回退 DEEPSEEK_API_KEY）
  FACTCHECK_LLM_BASE_URL  （缺省 https://api.deepseek.com）
  FACTCHECK_LLM_MODEL     （缺省 deepseek-chat）
默认 DeepSeek V4-Flash 级别：约 $0.14/M 输入、$0.28/M 输出，缓存命中输入约 $0.003/M，
一份文档抽取通常 < 1 美分。想换豆包 Lite / Qwen-turbo / Gemini Flash-Lite，
只需改 BASE_URL + MODEL + KEY（都走 OpenAI 兼容 /chat/completions）。

无 API key 时自动回退到 **jieba 名词块**离线抽取（需 `pip install jieba`）；
再无 jieba 则回退到 v3 的正则抽取（保证任何环境都能出结果）。

用法:
  python3 factcheck_llm.py <目标文档> <参考文档目录> [输出报告.md]
  python3 factcheck_llm.py --offline <目标文档> <参考文档目录> [输出报告.md]   # 强制离线(jieba/正则)
  python3 factcheck_llm.py --excel <目标文档> <参考文档目录>                    # 附加生成 Excel
  python3 factcheck_llm.py --regenerate <json路径> [输出报告.md]               # 复用 v3 重生成
支持的文档格式同 v3：docx / doc / pdf / md / txt。
"""

import sys
import os
import re
import json
import urllib.request
import urllib.error

# 复用 v3 后端（同目录导入，不修改其代码）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import doc_fact_check as v3


# ── 抽取声明的数据结构 ────────────────────────────────────
def _blank_statement(text, stype, numbers, entities):
    """构造与 v3 search_in_reference 兼容的条目 dict。"""
    return {
        "类型": stype,
        "表述内容": text,
        "状态": "✗ 未找到",
        "出处": "—",
        "原文片段": "所有参照文档均未出现此表述/数据",
        "匹配关键词": "",
        "命中数字": numbers,
        "命中实体": entities,
        "子命题": v3.decompose_statement(text),
        "匹配上下文简述": "",
        "反向验证警告": "",
    }


# ── LLM 抽取（主路径）──────────────────────────────────────
_SYS_PROMPT = """你是公文事实核查的"可核实声明"抽取助手。用户给你一段公文/讲话稿正文，\
你唯一的任务是抽取其中【可核实的事实性声明】，供后续与参考资料逐条比对。

【判定"可核实"】只要满足其一即抽取：
- 含具体数字/百分比/金额/年份/规模；
- 含具体专有名词：机构名、项目名、平台名、称号荣誉、文件/政策/方案名、学科/专业名等；
- 含具体成就动作：获批/入选/获评/建成/成立/组建/牵头/新增/达到/累计/上线/发布/签约等。

【必须排除】（视为论述性，不要抽取）：
口号与目标愿景、段落过渡句、纯观点评价（如"深切感到""表面看…深层逻辑"）、\
常识道理、领导人/文件的引语（如"习近平总书记强调…"整句引用）、比喻排比等纯修辞。

【每条声明输出字段】
- 表述内容：原文中的该句或该分句，尽量逐字照抄（不要改写、不要合并多句）；
- 类型："定量"（含数字）或"定性"（无数字但有专名/成就）；
- 命中数字：该句中的数字/百分比/金额/年份，字符串数组，逐字照抄，无则空数组；
- 命中实体：该句中的【专有名词】，字符串数组，逐字照抄。\
必须是名词性专名（如"国家数据工程与安全学院""复合型人才培养模式改革""龙马在线学堂"），\
【严禁】把动词短语、整句话、或"深化改革""推进建设"这类通用动宾词组当实体。无则空数组。

只返回一个 JSON 对象：{"claims": [ {"表述内容":..,"类型":..,"命中数字":[..],"命中实体":[..]}, ... ]}。
不要任何解释、不要 markdown 代码围栏。"""


def _llm_config():
    key = os.environ.get("FACTCHECK_LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    base = os.environ.get("FACTCHECK_LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    model = os.environ.get("FACTCHECK_LLM_MODEL", "deepseek-chat")
    return key, base, model


def _call_llm(chunk, key, base, model, timeout=120):
    # DeepSeek/OpenAI 兼容端点统一走 {base}/v1/chat/completions
    url = base + "/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYS_PROMPT},
            {"role": "user", "content": chunk},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "stream": False,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    content = body["choices"][0]["message"]["content"]
    return content


def _parse_claims(content):
    """从 LLM 返回里稳健解析出 claims 列表。"""
    content = content.strip()
    # 去掉可能的 ```json 围栏
    if content.startswith("```"):
        content = re.sub(r"^```[a-zA-Z]*\n?", "", content)
        content = re.sub(r"\n?```$", "", content).strip()
    try:
        obj = json.loads(content)
    except Exception:
        # 尝试截取第一个 { 到最后一个 }
        m = re.search(r"\{.*\}", content, re.S)
        if not m:
            return []
        try:
            obj = json.loads(m.group(0))
        except Exception:
            return []
    if isinstance(obj, list):
        return obj
    return obj.get("claims") or obj.get("表述") or []


def _chunk_text(text, max_chars=3500):
    """按段落聚合成不超过 max_chars 的块，避免单次输入/输出过长。"""
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        if cur and len(cur) + len(p) > max_chars:
            chunks.append(cur)
            cur = p
        else:
            cur = (cur + "\n" + p) if cur else p
    if cur:
        chunks.append(cur)
    return chunks


def extract_claims_llm(target_txt):
    """用 LLM 抽取可核实声明。成功返回 statements 列表，失败返回 None（触发回退）。"""
    key, base, model = _llm_config()
    if not key:
        return None
    with open(target_txt, "r", encoding="utf-8") as f:
        text = f.read()
    chunks = _chunk_text(text)
    print(f"  LLM 抽取：模型 {model} @ {base}，共 {len(chunks)} 个文本块")
    statements = []
    for i, ch in enumerate(chunks, 1):
        try:
            content = _call_llm(ch, key, base, model)
            claims = _parse_claims(content)
        except urllib.error.HTTPError as e:
            print(f"  [块{i}] HTTP {e.code}：{e.read().decode('utf-8', 'ignore')[:200]}", file=sys.stderr)
            return None
        except Exception as e:
            print(f"  [块{i}] LLM 调用失败：{e}", file=sys.stderr)
            return None
        for c in claims:
            if not isinstance(c, dict):
                continue
            t = str(c.get("表述内容") or c.get("text") or "").strip()
            if len(t) < 6:
                continue
            nums = c.get("命中数字") or c.get("numbers") or v3.extract_all_numbers(t)
            ents = c.get("命中实体") or c.get("entities") or []
            nums = [str(x).strip() for x in nums if str(x).strip()]
            ents = [str(x).strip() for x in ents if str(x).strip()]
            stype = c.get("类型") or ("定量" if nums else "定性")
            statements.append(_blank_statement(t, stype, nums, ents))
        print(f"  [块{i}/{len(chunks)}] 抽取 {len(claims)} 条")
    # 按完整表述去重
    seen, uniq = set(), []
    for s in statements:
        if s["表述内容"] not in seen:
            seen.add(s["表述内容"])
            uniq.append(s)
    return uniq


# ── jieba 名词块抽取（离线兜底）───────────────────────────
_PSEG = None
def _get_pseg():
    global _PSEG
    if _PSEG is None:
        try:
            import jieba
            import jieba.posseg as pseg
            jieba.setLogLevel(60)
            _PSEG = pseg
        except Exception:
            _PSEG = False
    return _PSEG


_NOUNISH_KEEP = ("n", "j", "eng")            # 名词/简称/外文
_NOUNISH_EXTRA = {"vn", "an", "b"}           # 动名词/形容词性名词/区别词，常是专名成分
_ACHIEVE_RE = re.compile(
    r"(?:入选|获批|获评|荣获|获得|新增|组建|成立|牵头|实施|推进|完成|突破|增长|提升|"
    r"达到|建成|上线|发布|通过|授牌|签约|设立|启用|开创|实现|累计|覆盖)")


def _noun_chunks(text, minlen=4):
    pseg = _get_pseg()
    if not pseg:
        return None
    chunks, cur = [], ""
    for w, f in pseg.cut(text):
        if f.startswith(_NOUNISH_KEEP) or f in _NOUNISH_EXTRA:
            cur += w
        else:
            if len(cur) >= minlen:
                chunks.append(cur)
            cur = ""
    if len(cur) >= minlen:
        chunks.append(cur)
    # 高精度补充：引号 / 书名号内文本
    for q in re.findall(r"“([^“”]{2,50})”", text):
        chunks.append(q.strip())
    for q in re.findall(r"《([^《》]{2,50})》", text):
        chunks.append(q.strip())
    seen, out = set(), []
    for c in chunks:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def extract_claims_offline(target_txt):
    """jieba 名词块 + 可核实性闸门；无 jieba 时回退 v3 正则抽取。"""
    with open(target_txt, "r", encoding="utf-8") as f:
        content = f.read()
    use_jieba = _get_pseg() is not False
    print(f"  离线抽取：{'jieba 名词块' if use_jieba else 'v3 正则(未装 jieba)'}")

    raw = [p.strip() for p in re.split(r"[。；\n]+", content) if len(p.strip()) > 10]
    statements = []
    for part in raw:
        if re.match(r"^[\s\d\.、）\)]+$", part):
            continue
        numbers = v3.extract_all_numbers(part)
        if use_jieba:
            entities = _noun_chunks(part) or []
        else:
            entities = v3.extract_named_entities(part)
        has_action = bool(_ACHIEVE_RE.search(part))
        # 可核实性闸门：无数字、无实体、无成就动词 → 论述性，不进核查池
        if not (numbers or entities or has_action):
            continue
        stype = "定量" if numbers else "定性"
        statements.append(_blank_statement(part, stype, numbers, entities))

    seen, uniq = set(), []
    for s in statements:
        if s["表述内容"] not in seen:
            seen.add(s["表述内容"])
            uniq.append(s)
    return uniq


# ── 主流程 ────────────────────────────────────────────────
def run(target_doc, ref_dir, output_md="", want_excel=False, force_offline=False):
    txt_dir = os.path.join(os.path.dirname(target_doc) or ".", "txt_output")
    os.makedirs(txt_dir, exist_ok=True)

    print("=" * 60)
    print("Step 1: 转换参考文档 → txt")
    print("=" * 60)
    ref_files = v3.find_doc_files(ref_dir)
    print(f"找到 {len(ref_files)} 个参考文档")
    ref_texts = {}
    for doc in ref_files:
        txt_path = v3.convert_docx_to_txt(doc, txt_dir)
        with open(txt_path, "r", encoding="utf-8") as f:
            ref_texts[txt_path] = f.read()
        print(f"  → {os.path.basename(doc)}")

    print("\n" + "=" * 60)
    print("Step 2: 抽取可核实声明（LLM 优先，离线兜底）")
    print("=" * 60)
    main_txt = v3.convert_docx_to_txt(target_doc, txt_dir)
    statements = None
    if not force_offline:
        statements = extract_claims_llm(main_txt)
        if statements is None:
            print("  ⚠ LLM 不可用（无 key 或调用失败），回退离线抽取")
    if statements is None:
        statements = extract_claims_offline(main_txt)

    entity_count = sum(1 for s in statements if s.get("命中实体"))
    quant_count = sum(1 for s in statements if s.get("类型") == "定量")
    print(f"抽取到 {len(statements)} 条可核实声明"
          f"（定量 {quant_count} / 含专有实体 {entity_count}）")
    if not statements:
        print("⚠ 未抽取到任何可核实声明，流程结束。")
        return

    print("\n" + "=" * 60)
    print("Step 3: 全文检索 + 实体覆盖 + 张冠李戴反向验证（复用 v3 后端）")
    print("=" * 60)
    v3.search_in_reference(statements, list(ref_texts.items()))

    print("\n" + "=" * 60)
    print("Step 4: 保存中间结果")
    print("=" * 60)
    json_path = os.path.join(txt_dir, "checklist_result.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(statements, f, ensure_ascii=False, indent=2)
    print(f"中间结果已保存: {json_path}")
    rev = [s for s in statements if s.get("反向验证警告")]
    rev_path = os.path.join(txt_dir, "checklist_reverse_check.json")
    with open(rev_path, "w", encoding="utf-8") as f:
        json.dump(rev, f, ensure_ascii=False, indent=2)
    print(f"反向验证报告已保存: {rev_path}")

    print("\n" + "=" * 60)
    print("Step 5: 生成核对报告")
    print("=" * 60)
    doc_basename = os.path.splitext(os.path.basename(target_doc))[0]
    base_dir = os.path.dirname(target_doc) or "."
    if not output_md:
        output_md = os.path.join(base_dir, f"{doc_basename}_核对清单.md")
    elif not os.path.isabs(output_md):
        output_md = os.path.join(base_dir, output_md)
    v3.generate_markdown(statements, output_md, doc_name=doc_basename,
                         ref_count=len(ref_texts))
    if want_excel:
        output_xlsx = os.path.splitext(output_md)[0] + ".xlsx"
        v3.generate_excel(statements, output_xlsx)
    v3.print_summary(statements)


def main():
    args = sys.argv[1:]
    if args and args[0] == "--regenerate":
        sys.argv = [sys.argv[0]] + args
        v3.cmd_regenerate()
        return
    if args and args[0] in ("--help", "-h"):
        print(__doc__)
        return
    flags = [a for a in args if a.startswith("--")]
    pos   = [a for a in args if not a.startswith("--")]
    force_offline = "--offline" in flags
    want_excel    = "--excel"   in flags
    if len(pos) < 2:
        print(__doc__)
        sys.exit(1)
    target  = pos[0]
    ref_dir = pos[1]
    out_md  = pos[2] if len(pos) > 2 else ""
    run(target, ref_dir, output_md=out_md, want_excel=want_excel,
        force_offline=force_offline)


if __name__ == "__main__":
    main()
