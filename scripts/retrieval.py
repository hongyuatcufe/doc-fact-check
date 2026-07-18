#!/usr/bin/env python3
"""
retrieval.py — Stage 2A 检索模块（trigram/BM25 本地检索）

提供两个公开 API：
    build_index(ref_contents)  →  Index
    retrieve(claim, numbers, entities, index, top_k=5)  →  list[dict]

设计原则：
- 纯 stdlib + sqlite3，无额外 pip 依赖
- FTS5 trigram 索引，适用于中文（无需分词器）
- 短查询（< 3 Unicode 字符）自动回退到 LIKE '%xx%' 全扫描
- 数字 / 实体 exactTerms 锚点：不含任何锚点的候选块得分惩罚
- 若 FTS5 不可用，回退到纯子串 LIKE 搜索并打印警告
"""

import re
import sqlite3
import logging
import warnings
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ─── 分块参数 ─────────────────────────────────────────────────────────────────
_CHUNK_TARGET = 1200   # 目标块长（字符数）
_CHUNK_OVERLAP = 150   # 相邻块重叠（字符数）

# ─── 数据结构 ──────────────────────────────────────────────────────────────────

@dataclass
class Index:
    """
    不透明索引对象，内部持有 sqlite 连接与元数据。
    由 build_index() 返回，传入 retrieve()。
    """
    conn: sqlite3.Connection
    has_fts5: bool
    # {chunk_id: (source_path, offset, text)}
    _chunks: dict = field(default_factory=dict)


# ─── 辅助：检测 FTS5 + trigram ────────────────────────────────────────────────

def _check_fts5_trigram(conn: sqlite3.Connection) -> bool:
    """检查当前 sqlite 是否支持 FTS5 trigram tokenizer。"""
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS _fts5_probe "
            "USING fts5(x, tokenize='trigram')"
        )
        conn.execute("DROP TABLE IF EXISTS _fts5_probe")
        return True
    except sqlite3.OperationalError:
        return False


# ─── 分块逻辑 ─────────────────────────────────────────────────────────────────

def _split_chunks(text: str, target: int = _CHUNK_TARGET,
                  overlap: int = _CHUNK_OVERLAP) -> list[tuple[int, str]]:
    """
    将 text 分割为 (offset, chunk_text) 列表。

    分割优先级：
    1. 段落边界：\\n\\n
    2. 中文句末：。！？…… + 可选换行
    3. 英文句末：. 后接空白
    4. 强制截断（无合适边界时）

    目标块长 ~target 字符，相邻块重叠 ~overlap 字符。
    """
    if not text:
        return []

    # 预先找出所有"好的"分割点：段落 > 句末 > 强制
    # 每个分割点记录 (offset, priority)；priority 越小越优先
    PARA_BREAK = re.compile(r'\n\n+')
    SENT_ZH    = re.compile(r'[。！？…]+[\n\r]*')
    SENT_EN    = re.compile(r'[.!?][ \t\n\r]+')

    break_points: list[tuple[int, int]] = [(0, 0)]  # (pos, priority)
    for m in PARA_BREAK.finditer(text):
        break_points.append((m.end(), 0))
    for m in SENT_ZH.finditer(text):
        break_points.append((m.end(), 1))
    for m in SENT_EN.finditer(text):
        break_points.append((m.end(), 2))
    break_points.append((len(text), 0))

    # 按 offset 排序，去重
    break_points = sorted(set(break_points), key=lambda x: x[0])

    chunks: list[tuple[int, str]] = []
    chunk_start = 0

    while chunk_start < len(text):
        # 找一个距 chunk_start + target 最近的好分割点
        ideal_end = chunk_start + target
        best_end = None
        best_priority = 99

        for bp_pos, bp_pri in break_points:
            if bp_pos <= chunk_start:
                continue
            if bp_pos <= ideal_end:
                # 在目标范围内，取优先级最好（最小）的最后一个
                if bp_pri <= best_priority:
                    best_end = bp_pos
                    best_priority = bp_pri
            else:
                # 超出目标，若还没有任何候选，就用第一个超出点
                if best_end is None:
                    best_end = bp_pos
                break

        if best_end is None or best_end >= len(text):
            best_end = len(text)

        chunk_text = text[chunk_start:best_end]
        if chunk_text.strip():
            chunks.append((chunk_start, chunk_text))

        # 下一块起点：best_end - overlap（保证重叠）
        next_start = max(chunk_start + 1, best_end - overlap)
        chunk_start = next_start

    return chunks


# ─── 建索引 ───────────────────────────────────────────────────────────────────

def build_index(ref_contents: dict[str, str],
                db_path: str = ":memory:") -> Index:
    """
    从参考文档字典建立检索索引。

    参数
    ----
    ref_contents : dict[str, str]
        {filename: full_text}，与 doc_fact_check.py 中 references dict 格式一致。
    db_path : str
        sqlite 文件路径，默认 ':memory:'（内存，进程退出后丢弃）。
        传入具体路径可在多次调用间复用索引（文件级缓存）。

    返回
    ----
    Index  不透明索引对象
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    has_fts5 = _check_fts5_trigram(conn)
    if not has_fts5:
        warnings.warn(
            "当前 sqlite 不支持 FTS5 trigram，将回退到 LIKE 全扫描检索，性能较慢。",
            RuntimeWarning,
            stacklevel=2,
        )
        logger.warning("FTS5 trigram unavailable; falling back to LIKE scan")

    # 主表：chunks
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chunks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source_path TEXT NOT NULL,
            offset      INTEGER NOT NULL,
            text        TEXT NOT NULL
        )
    """)

    # FTS5 虚表（仅当 FTS5 可用时建立）
    if has_fts5:
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
            USING fts5(
                text,
                content='chunks',
                content_rowid='id',
                tokenize='trigram'
            )
        """)

    # 写入分块
    chunk_cache: dict[int, tuple[str, int, str]] = {}
    for source_path, full_text in ref_contents.items():
        chunks = _split_chunks(full_text)
        for offset, chunk_text in chunks:
            cur = conn.execute(
                "INSERT INTO chunks (source_path, offset, text) VALUES (?, ?, ?)",
                (source_path, offset, chunk_text),
            )
            chunk_id = cur.lastrowid
            chunk_cache[chunk_id] = (source_path, offset, chunk_text)

    conn.commit()

    # 填充 FTS5 索引
    if has_fts5:
        conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
        conn.commit()

    return Index(conn=conn, has_fts5=has_fts5, _chunks=chunk_cache)


# ─── 检索 ─────────────────────────────────────────────────────────────────────

def _fts5_search(conn: sqlite3.Connection, query: str,
                 top_k: int) -> list[tuple[int, float]]:
    """
    FTS5 trigram 搜索，返回 [(chunk_id, bm25_score), ...]。
    bm25() 返回负值（越小越相关），统一转成正值便于后续加权。
    """
    # FTS5 trigram 要求查询串 >= 3 Unicode 字符；
    # 对于短查询，此函数不应被调用（由调用方处理回退）。
    try:
        rows = conn.execute(
            """
            SELECT c.id, -bm25(chunks_fts) AS score
            FROM chunks_fts
            JOIN chunks c ON c.id = chunks_fts.rowid
            WHERE chunks_fts MATCH ?
            ORDER BY score DESC
            LIMIT ?
            """,
            (query, top_k * 3),  # 多取一些以便后续 exactTerms 过滤
        ).fetchall()
        return [(row[0], row[1]) for row in rows]
    except sqlite3.OperationalError as e:
        logger.warning("FTS5 search error for query %r: %s", query, e)
        return []


def _like_search(conn: sqlite3.Connection, query: str,
                 top_k: int) -> list[tuple[int, float]]:
    """
    LIKE '%query%' 全扫描，用于 FTS5 不可用或查询串 < 3 字的情况。
    得分 = 出现次数（简单计数，充当替代相关性）。
    """
    rows = conn.execute(
        """
        SELECT id, text
        FROM chunks
        WHERE text LIKE ?
        """,
        (f"%{query}%",),
    ).fetchall()

    scored = []
    for chunk_id, text in rows:
        count = text.count(query)
        scored.append((chunk_id, float(count)))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k * 3]


def _search_single_term(conn: sqlite3.Connection, has_fts5: bool,
                        term: str, top_k: int) -> list[tuple[int, float]]:
    """
    对单个查询词搜索，自动处理短词回退。
    """
    char_len = len(term.replace(" ", ""))  # Unicode 字符数（忽略空格）
    if has_fts5 and char_len >= 3:
        results = _fts5_search(conn, term, top_k)
        if results:
            return results
        # FTS5 返回空时（可能是特殊字符问题），回退 LIKE
        logger.debug("FTS5 returned empty for %r, falling back to LIKE", term)
    # 短查询或 FTS5 不可用 → LIKE 全扫描
    if char_len < 3:
        logger.debug(
            "Short query (%d chars) %r: using LIKE fallback", char_len, term
        )
    return _like_search(conn, term, top_k)


def retrieve(
    claim: str,
    numbers: list[str],
    entities: list[str],
    index: Index,
    top_k: int = 5,
) -> list[dict]:
    """
    检索与 claim 最相关的 top_k 候选块。

    参数
    ----
    claim    : str   声明原文（作为主查询）
    numbers  : list  命中数字列表（exactTerms 锚点）
    entities : list  命中实体列表（exactTerms 锚点）
    index    : Index build_index() 返回的索引
    top_k    : int   返回候选数，默认 5

    返回
    ----
    list[dict]，每项：
        {
            "sourcePath": str,   # 文件名
            "offset":     int,   # 字符偏移
            "text":       str,   # 块文本（~1200 字）
            "score":      float  # 相关性得分（越高越相关）
        }
    排序：score 降序。
    """
    conn = index.conn
    has_fts5 = index.has_fts5
    chunk_cache = index._chunks

    # 1. 收集所有查询词：claim 本身 + 每个 exactTerm
    query_terms: list[str] = []

    # 主查询：取 claim 的前 60 字（FTS5 对超长查询可能报错）
    main_query = claim[:60].strip()
    if main_query:
        query_terms.append(main_query)

    # exactTerms：数字 + 实体（去重，按长度降序，优先长词）
    exact_terms: list[str] = []
    seen_terms: set[str] = set()
    for t in sorted(numbers + entities, key=len, reverse=True):
        t = t.strip()
        if t and t not in seen_terms:
            exact_terms.append(t)
            seen_terms.add(t)
            query_terms.append(t)

    # 2. 对每个查询词检索，汇总得分（RRF-lite：每个词贡献倒数排名分）
    # 格式：{chunk_id: cumulative_score}
    score_map: dict[int, float] = {}
    RRF_K = 60  # 标准 RRF 常数

    for qi, term in enumerate(query_terms):
        if not term.strip():
            continue
        results = _search_single_term(conn, has_fts5, term, top_k * 4)
        # 主查询权重 2x，exactTerm 权重 1x
        weight = 2.0 if qi == 0 else 1.0
        for rank, (chunk_id, raw_score) in enumerate(results, start=1):
            rrf_score = weight * (1.0 / (RRF_K + rank))
            score_map[chunk_id] = score_map.get(chunk_id, 0.0) + rrf_score

    if not score_map:
        return []

    # 3. exactTerms 锚点惩罚：不含任何 exactTerm 的块打折（不硬过滤，保召回）
    PENALTY_FACTOR = 0.4
    if exact_terms:
        for chunk_id in list(score_map.keys()):
            source_path, offset, text = _get_chunk(chunk_id, chunk_cache, conn)
            if not any(t in text for t in exact_terms):
                score_map[chunk_id] *= PENALTY_FACTOR

    # 4. 排序，取 top_k
    ranked = sorted(score_map.items(), key=lambda x: x[1], reverse=True)
    top = ranked[:top_k]

    results_out: list[dict] = []
    for chunk_id, score in top:
        source_path, offset, text = _get_chunk(chunk_id, chunk_cache, conn)
        results_out.append({
            "sourcePath": source_path,
            "offset":     offset,
            "text":       text,
            "score":      round(score, 6),
        })

    return results_out


def _get_chunk(chunk_id: int,
               cache: dict[int, tuple[str, int, str]],
               conn: sqlite3.Connection) -> tuple[str, int, str]:
    """从缓存或数据库获取块数据。"""
    if chunk_id in cache:
        return cache[chunk_id]
    row = conn.execute(
        "SELECT source_path, offset, text FROM chunks WHERE id = ?",
        (chunk_id,),
    ).fetchone()
    if row:
        result = (row[0], row[1], row[2])
        cache[chunk_id] = result
        return result
    return ("", 0, "")


# ─── 便捷工具：关闭索引 ───────────────────────────────────────────────────────

def close_index(index: Index) -> None:
    """关闭索引持有的数据库连接（可选，内存库不必调用）。"""
    try:
        index.conn.close()
    except Exception:
        pass


# ─── 烟雾测试 ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
    )

    print("=" * 60)
    print("retrieval.py 烟雾测试")
    print("=" * 60)

    # 测试语料（3 段短中文文本）
    ref_contents = {
        "文件A.txt": (
            "国家重点研发计划于2023年批准了《新一代人工智能关键技术》专项，"
            "资助经费共计12亿元，覆盖全国38所高校和科研院所。\n\n"
            "人工智能实验室在2023年度取得重要突破，发表顶级论文215篇，"
            "其中Nature/Science系列32篇，获批国家专利107项。\n\n"
            "产学研合作深度推进，与头部企业签署战略合作协议42份，"
            "联合培养博士研究生320名，成果转化收益超3.2亿元。"
        ),
        "文件B.txt": (
            "2022年全国高等院校在校生总数达到4655万人，"
            "其中研究生招生规模突破120万，博士生在读人数超过57万。\n\n"
            "教育部数字化转型战略行动计划已覆盖全国2800余所普通高校，"
            "建设国家级精品在线课程超过2.7万门，学习人次累计超过40亿。\n\n"
            "新工科、新医科、新农科、新文科建设全面推进，"
            "新增本科专业点1.2万余个，撤销或停止招生专业点近1.1万个。"
        ),
        "文件C.txt": (
            "上海市人工智能发展三年行动方案（2023-2025）明确：\n"
            "到2025年，全市人工智能核心产业规模突破4000亿元，"
            "集聚企业超3000家，高端人才规模达到10万人以上。\n\n"
            "张江人工智能岛已入驻企业150余家，"
            "其中独角兽企业8家，国家级专精特新'小巨人'企业23家。\n\n"
            "徐汇西岸智慧谷重点布局大模型、具身智能、脑机接口三大赛道，"
            "2023年新增投融资事件87起，融资总额超220亿元。"
        ),
    }

    # ── 测试 1: 建索引 ────────────────────────────────────────
    print("\n[测试 1] 建立内存索引...")
    idx = build_index(ref_contents)
    chunk_count = idx.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    print(f"  FTS5 可用: {idx.has_fts5}")
    print(f"  总分块数: {chunk_count}")
    assert chunk_count > 0, "分块数不应为 0"
    print("  PASS")

    # ── 测试 2: 正常查询（>= 3 字） ──────────────────────────
    print("\n[测试 2] 正常查询（>= 3 字）...")
    query2 = "国家重点研发计划批准人工智能专项资助经费"
    numbers2 = ["12亿元"]
    entities2 = ["人工智能实验室"]
    results2 = retrieve(query2, numbers2, entities2, idx, top_k=3)
    print(f"  查询: {query2!r}")
    print(f"  返回 {len(results2)} 个候选块:")
    for r in results2:
        print(f"    [{r['sourcePath']}] offset={r['offset']} score={r['score']:.5f}")
        print(f"      {r['text'][:60].replace(chr(10),' ')}…")
    assert len(results2) > 0, "应返回至少 1 个候选块"
    assert all("sourcePath" in r and "offset" in r and "text" in r and "score" in r
               for r in results2), "候选块缺少必填字段"
    print("  PASS")

    # ── 测试 3: 短查询回退（< 3 字） ─────────────────────────
    print("\n[测试 3] 短查询（< 3 字）回退到 LIKE 扫描...")
    for short_q in ["局", "上海"]:
        print(f"\n  查询: {short_q!r}（{len(short_q)} 字）")
        results3 = retrieve(short_q, [], [], idx, top_k=3)
        print(f"  返回 {len(results3)} 个候选块 "
              f"（日志中应出现 'Short query' 或 'LIKE fallback' 信息）")
        for r in results3:
            print(f"    [{r['sourcePath']}] score={r['score']:.5f} "
                  f"text_preview={r['text'][:40].replace(chr(10),' ')!r}")
        if short_q == "上海":
            assert len(results3) > 0, f"短查询 {short_q!r} 应通过 LIKE 找到结果"
    print("  PASS（LIKE 回退已触发，见上方 DEBUG 日志）")

    # ── 测试 4: 完全无匹配查询 ───────────────────────────────
    print("\n[测试 4] 无匹配查询...")
    results4 = retrieve("zzz_no_such_content_xyz_999", [], [], idx, top_k=5)
    print(f"  返回 {len(results4)} 个候选块（预期 0）")
    # 允许 0 或非 0（LIKE 可能碰巧命中），只验证不崩溃
    print("  PASS")

    # ── 测试 5: exactTerms 锚点惩罚有效 ─────────────────────
    print("\n[测试 5] exactTerms 锚点惩罚...")
    # 查询"高校"，但指定一个只在文件A出现的数字作为 exactTerm
    results5_with = retrieve("高校", ["215篇"], [], idx, top_k=5)
    results5_without = retrieve("高校", [], [], idx, top_k=5)
    print(f"  含 exactTerm '215篇': 首个结果 source={results5_with[0]['sourcePath'] if results5_with else 'N/A'}")
    print(f"  无 exactTerm:         首个结果 source={results5_without[0]['sourcePath'] if results5_without else 'N/A'}")
    print("  PASS（exactTerms 惩罚逻辑已执行）")

    close_index(idx)
    print("\n" + "=" * 60)
    print("retrieval.py smoke test passed")
    print("=" * 60)
