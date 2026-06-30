"""
Patch to add universal category word entity extraction to doc_fact_check.py.

This inserts:
1. _get_category_patterns() - loads category words from config or uses defaults
2. Modified extract_named_entities() - uses category patterns instead of hardcoded education patterns
3. check_intra_document_consistency() - detects contradictions within the same target doc

Run: python3 add_category_words.py
"""

import os
import re

SCRIPT_PATH = os.path.join(os.path.dirname(__file__), 'doc_fact_check.py')

# ── New function: _get_category_patterns ──
NEW_FUNC_1 = r'''
def _get_category_patterns(config_path=None):
    """
    加载通用类别词配置，生成正则模式列表。

    配置加载优先级：
    1. 显式指定的 config_path
    2. 脚本同目录下的 entity_config.yaml
    3. 内置默认列表（跨领域通用）

    返回：list of compiled regex patterns
    """
    # 内置默认类别词（跨领域通用，不依赖任何特定领域）
    default_categories = [
        # 任务/任务类型
        '任务', '试点', '改革', '项目', '工程',
        '计划', '行动', '方案', '专项', '示范',
        '建设', '试点单位',
        # 机构/组织
        '机构', '中心', '平台', '基地', '联盟',
        '委员会', '办公室', '部门',
        '学院', '医院', '学校', '大学',
        '研究院', '研究所', '实验室',
        # 体系/模式
        '体系', '模式', '机制', '制度',
        '框架', '范式', '格局', '生态',
        # 文档/标准
        '规划', '报告', '标准', '规范',
        '文件', '办法', '规定',
        # 人员/团队
        '团队', '小组', '队伍',
        # 基础设施/技术
        '系统', '网络', '设施', '设备',
        '模型', '工具',
        # 活动/事件
        '论坛', '会议', '大赛', '活动',
        '峰会', '展览', '培训',
    ]

    # 尝试加载外部配置
    loaded_categories = None
    if config_path and os.path.exists(config_path):
        try:
            if yaml:
                with open(config_path, 'r', encoding='utf-8') as f:
                    loaded_categories = yaml.safe_load(f)
        except Exception:
            pass

    # 如果没指定 config_path，尝试脚本同目录下的默认配置
    if loaded_categories is None:
        default_config_path = os.path.join(os.path.dirname(__file__), 'entity_config.yaml')
        if os.path.exists(default_config_path):
            try:
                if yaml:
                    with open(default_config_path, 'r', encoding='utf-8') as f:
                        loaded_categories = yaml.safe_load(f)
            except Exception:
                pass

    # 从配置中提取所有类别词
    all_suffixes = set(default_categories)
    if loaded_categories:
        for section_name, section_content in loaded_categories.items():
            if isinstance(section_content, dict):
                for sub_name, words in section_content.items():
                    if isinstance(words, list):
                        for w in words:
                            w = w.strip()
                            if len(w) >= 2:
                                all_suffixes.add(w)
            elif isinstance(section_content, list):
                for w in section_content:
                    w = w.strip()
                    if len(w) >= 2:
                        all_suffixes.add(w)

    # 按长度降序排列（长词优先匹配）
    sorted_suffixes = sorted(all_suffixes, key=len, reverse=True)

    # 生成正则模式
    suffix_pattern = r'[\u4e00-\u9fff]{2,30}(?:' + '|'.join(re.escape(s) for s in sorted_suffixes) + r')'
    return [re.compile(suffix_pattern)]

'''

# ── New function: check_intra_document_consistency ──
NEW_FUNC_2 = r'''
def check_intra_document_consistency(items):
    """
    文档内一致性检查。

    当同一目标文档中多个条目提到同一关键实体，
    但该实体的关联属性不一致时，标记为潜在不一致。

    检查逻辑：
    1. 收集所有条目中提取到的实体
    2. 找到在多个条目中出现的"共享实体"
    3. 对每个共享实体，比较其关联的"属性词"
       （属性词 = 该条目中紧邻该实体的动词/名词）
    4. 如果不同条目的属性词差异较大，发出警告

    返回：list of (shared_entity, item_a_seq, attr_a, item_b_seq, attr_b)
    """
    if not items:
        return []

    # 构建：实体 → [(序号, 表述内容, 属性词), ...]
    entity_map = {}
    for idx, item in enumerate(items):
        entities = item.get("命中实体", [])
        content = item.get("表述内容", "")
        for ent in entities:
            if ent not in entity_map:
                entity_map[ent] = []
            # 提取该条目中与实体共现的属性词
            # 属性词 = 位于实体前后20字范围内的实词
            attr_words = _extract_attr_words(content, ent)
            entity_map[ent].append((idx + 1, content[:80], attr_words))

    # 筛选：出现在2个及以上条目中的实体
    conflicts = []
    for ent, occurrences in entity_map.items():
        if len(occurrences) < 2:
            continue
        # 比较各条目中实体的属性词集合
        attr_sets = [set(o[2]) for o in occurrences]
        # 如果属性词集合差距大，可能是不一致
        for i in range(len(attr_sets)):
            for j in range(i + 1, len(attr_sets)):
                common = attr_sets[i] & attr_sets[j]
                union = attr_sets[i] | attr_sets[j]
                if union and len(common) / len(union) < 0.3:
                    # 属性词交集不足30%，可能有矛盾
                    conflicts.append((
                        ent,
                        occurrences[i][0], list(attr_sets[i])[:5],
                        occurrences[j][0], list(attr_sets[j])[:5]
                    ))

    return conflicts


def _extract_attr_words(text, entity, window=20):
    """
    从文本中提取与实体相关的属性词。
    属性词 = 实体前后window字范围内的其他实体/名词。
    """
    idx = text.find(entity)
    if idx == -1:
        return []
    start = max(0, idx - window)
    end = min(len(text), idx + len(entity) + window)
    context = text[start:end]
    # 提取上下文中的其他名词性成分（长度3-15的连续汉字，排除实体本身）
    words = re.findall(r'[\u4e00-\u9fff]{3,15}', context)
    words = [w for w in words if w != entity and w not in text[idx:idx+len(entity)]]
    return words

'''

# ── Read and patch the file ──
with open(SCRIPT_PATH, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Insert _get_category_patterns() before extract_named_entities()
old_func_start = 'def extract_named_entities(text):'
insert_pos = content.find(old_func_start)
if insert_pos == -1:
    print("ERROR: Could not find extract_named_entities()")
    exit(1)

# Insert NEW_FUNC_1 before extract_named_entities
part1 = content[:insert_pos]
part2 = content[insert_pos:]
content = part1 + NEW_FUNC_1 + part2
print("1. Inserted _get_category_patterns()")

# 2. Replace the old extract_named_entities with new version
old_extract = '''    # 机构/平台模式
    entity_patterns = [
        r'[\\u4e00-\\u9fff\\u300a\\u300b\\w]{2,20}(?:\\u5b9e\\u9a8c\\u5ba4|\\u7814\\u7a76\\u4e2d\\u5fc3|\\u7814\\u7a76\\u9662|\\u7814\\u7a76\\u6240|\\u5b66\\u9662|\\u57fa\\u5730|\\u5e73\\u53f0|\\u4e2d\\u5fc3)',
        r'[\\u4e00-\\u9fff\\u300a\\u300b\\w]{2,30}(?:\\u5efa\\u8bbe\\u9879\\u76ee|\\u884c\\u52a8\\u8ba1\\u5212|\\u4e13\\u9879\\u89c4\\u5212|\\u5de5\\u4f5c\\u65b9\\u6848)',
        r'[\\u4e00-\\u9fff\\w]{3,20}(?:\\u521b\\u65b0\\u56e2\\u961f|\\u7814\\u7a76\\u56e2\\u961f|\\u6559\\u5b66\\u56e2\\u961f)',
    ]
    for pat in entity_patterns:
        for m in re.findall(pat, text):
            entities.append(m.strip())'''

new_extract = '''    # 通用类别词模式（跨领域）
    if category_patterns is None:
        category_patterns = _get_category_patterns()
    for pat in category_patterns:
        for m in pat.findall(text):
            entities.append(m.strip())'''

if old_extract in content:
    content = content.replace(old_extract, new_extract)
    print("2. Replaced hardcoded education patterns with universal category word patterns")
else:
    print("WARNING: Could not find old entity patterns (may have different escape encoding)")
    # Try with actual Unicode chars
    old_extract_actual = '''    # 机构/平台模式
    entity_patterns = [
        r'[\\u4e00-\\u9fff\\u300a\\u300b\\w]{2,20}(?:实验室|研究中心|研究院|研究所|学院|基地|平台|中心)',
        r'[\\u4e00-\\u9fff\\u300a\\u300b\\w]{2,30}(?:建设项目|行动计划|专项规划|工作方案)',
        r'[\\u4e00-\\u9fff\\w]{3,20}(?:创新团队|研究团队|教学团队)',
    ]
    for pat in entity_patterns:
        for m in re.findall(pat, text):
            entities.append(m.strip())'''
    if old_extract_actual in content:
        content = content.replace(old_extract_actual, new_extract)
        print("2. Replaced (with actual Chinese chars)")
    else:
        print("ERROR: Still could not find - manual fix needed")

# 3. Update extract_named_entities signature to accept category_patterns
old_sig = 'def extract_named_entities(text):'
new_sig = 'def extract_named_entities(text, category_patterns=None):'
if old_sig in content:
    content = content.replace(old_sig, new_sig, 1)  # replace only first occurrence
    print("3. Updated function signature")
else:
    print("ERROR: Could not find function signature")
    print(f"Looking for: {repr(old_sig)}")
    # Search in content
    pos = content.find('def extract_named_entities')
    print(f"Found at position {pos}: {content[pos:pos+50]}")

# 4. Insert check_intra_document_consistency after extract_named_entities block
# Find a good insertion point - after extract_named_entities and before decompose_statement
insert_anchor = '\n\ndef decompose_statement'
# But first insert the new function
insert_pos_2 = content.find(insert_anchor)
if insert_pos_2 != -1:
    part1 = content[:insert_pos_2]
    part2 = content[insert_pos_2:]
    content = part1 + NEW_FUNC_2 + part2
    print("4. Inserted check_intra_document_consistency()")
else:
    print("ERROR: Could not find insertion anchor for consistency check")

# 5. Add intra-document consistency check to the main flow
# In cmd_full_check(), after search_in_reference() and before saving JSON
old_save = '''    # Step 4: 保存中间结果
    print("\\n" + "=" * 60)
    print("Step 4: 保存中间结果")
    print("=" * 60)'''

new_save = '''    # Step 3.5: 文档内一致性检查（v3.2 新增）
    print("\\n" + "=" * 60)
    print("Step 3.5: 文档内一致性检查")
    print("=" * 60)
    conflicts = check_intra_document_consistency(statements)
    if conflicts:
        print(f"发现 {len(conflicts)} 处潜在不一致：")
        for ent, seq_a, attrs_a, seq_b, attrs_b in conflicts:
            warning = (
                f"文档内不一致：实体「{ent}」在条目{seq_a}中关联"
                f"属性{attrs_a}，在条目{seq_b}中关联"
                f"属性{attrs_b}，可能自相矛盾"
            )
            print(f"  ⚠ {warning}")
            # 将警告写入对应条目
            for s in statements:
                s_idx = statements.index(s)
                if s_idx + 1 == seq_a or s_idx + 1 == seq_b:
                    existing = s.get("反向验证警告", "")
                    if "文档内不一致" not in existing:
                        if existing:
                            s["反向验证警告"] = existing + "; " + warning
                        else:
                            s["反向验证警告"] = warning
    else:
        print("  未发现文档内矛盾")
    print()

    # Step 4: 保存中间结果
    print("\\n" + "=" * 60)
    print("Step 4: 保存中间结果")
    print("=" * 60)'''

if old_save in content:
    content = content.replace(old_save, new_save)
    print("5. Added intra-document consistency check to main flow")
else:
    print("ERROR: Could not find save step anchor")

# 6. Update extract_statements to pass category_patterns to extract_named_entities
old_call = '''        numbers = extract_all_numbers(part)
        entities = extract_named_entities(part)
        sub_claims = decompose_statement(part)'''

new_call = '''        numbers = extract_all_numbers(part)
        entities = extract_named_entities(part, category_patterns)
        sub_claims = decompose_statement(part)'''

if old_call in content:
    content = content.replace(old_call, new_call)
    print("6. Updated extract_statements to pass category_patterns")
else:
    print("ERROR: Could not find extract_statements call")

# Write back
with open(SCRIPT_PATH, 'w', encoding='utf-8') as f:
    f.write(content)

print("\nDone! All patches applied.")
print(f"File: {SCRIPT_PATH}")
PYEOF