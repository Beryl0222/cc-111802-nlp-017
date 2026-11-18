"""原始文本追溯与版本差异。

所有材料的原始正文（``raw_text``）、来源单位、修订号和观测时间都随记录
永久保存；归并只影响展示分组，不覆盖任何原始文本。差异在查询时按
具体版本现算，保证从归并结果可以逐级追查回原文。
"""

import difflib

from .grouping import canonical_record


def record_lineage(record):
    """单条记录的来源出处信息。"""
    return {
        "record_id": record["record_id"],
        "source": record["source"],
        "revision": record["revision"],
        "observed_at": record["observed_at"],
        "title": record.get("title", ""),
        "grade": record.get("grade", ""),
        "context": record.get("context", ""),
        "raw_text": record["raw_text"],
        "normalized_text": record["text"],
    }


def group_lineage(records_by_id, group):
    """归并组的完整来源谱系：代表记录 + 各校原始版本。"""
    members = [records_by_id[item] for item in group.record_ids if item in records_by_id]
    canonical_id = group.canonical_id if group.canonical_id in records_by_id else (
        canonical_record(members)["record_id"] if members else None
    )
    return {
        "group_id": group.group_id,
        "status": group.status,
        "canonical_id": canonical_id,
        "versions": [record_lineage(row) for row in members],
        "events": list(group.events),
    }


def unified_diff(records_by_id, left_id, right_id):
    """行级 unified diff，直接对照两份原始文本。"""
    left = records_by_id[left_id]
    right = records_by_id[right_id]
    left_lines = [f"{line}\n" for line in left["raw_text"].splitlines() or [left["raw_text"]]]
    right_lines = [f"{line}\n" for line in right["raw_text"].splitlines() or [right["raw_text"]]]
    return list(
        difflib.unified_diff(
            left_lines,
            right_lines,
            fromfile=f"{left['source']}:r{left['revision']}:{left_id}",
            tofile=f"{right['source']}:r{right['revision']}:{right_id}",
        )
    )


def token_diff(records_by_id, left_id, right_id):
    """token 级差异：equal/replace/delete/insert 片段，便于定位措辞差异。"""
    left = records_by_id[left_id]["fingerprint"]["tokens"]
    right = records_by_id[right_id]["fingerprint"]["tokens"]
    matcher = difflib.SequenceMatcher(a=left, b=right, autojunk=False)
    changes = []
    for tag, left_start, left_end, right_start, right_end in matcher.get_opcodes():
        if tag == "equal":
            continue
        changes.append(
            {
                "change": tag,
                "left": left[left_start:left_end],
                "right": right[right_start:right_end],
            }
        )
    return changes


def group_diffs(records_by_id, group):
    """归并组内每个版本相对代表记录的差异摘要。"""
    if group.canonical_id not in records_by_id:
        return []
    result = []
    for record_id in group.record_ids:
        if record_id == group.canonical_id:
            continue
        result.append(
            {
                "record_id": record_id,
                "source": records_by_id[record_id]["source"],
                "token_changes": token_diff(records_by_id, group.canonical_id, record_id),
                "unified_diff": unified_diff(records_by_id, group.canonical_id, record_id),
            }
        )
    return result
