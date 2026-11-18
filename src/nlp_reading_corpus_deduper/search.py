"""去重后的检索视图。

已确认的重复材料（``merged`` 组）折叠为一个材料条目，组内各校来源仍可
展开追查；``candidate`` 组不折叠，成员并列展示并附判定依据，等待人工
决定；``rejected`` / ``dissolved`` 不参与折叠，材料各自独立出现。

同一份材料可能同时出现在自动合并组和候选组中（例如与原稿完全一致的
东区稿折叠进 merged 组，同时又与五年级改写稿存在候选关系）：检索结果
中 merged 组优先折叠，候选组单独列出并跳过已被折叠的成员，保证每个
材料只在一个位置出现、但所有待裁决关系都可见。
"""

from .grouping import canonical_record, MergeGroup


def _record_matches(record, terms):
    if not terms:
        return True
    haystack = " ".join(
        [
            record.get("title", ""),
            record.get("context", ""),
            record.get("grade", ""),
            record.get("subject", ""),
            record["source"],
            record["text"],
        ]
    )
    return all(term in haystack for term in terms)


def search_materials(state, query="", source=None):
    records = state["records"]
    if source:
        records = [row for row in records if row["source"] == source]
    terms = [part for part in query.split() if part]
    records_by_id = {row["record_id"]: row for row in state["records"]}
    groups = [MergeGroup.from_dict(row) for row in state["groups"]]
    merged_groups = [group for group in groups if group.status == "merged"]
    candidate_groups = [group for group in groups if group.status == "candidate"]

    folded_ids = set()
    entries = []
    for group in merged_groups:
        members = [records_by_id[item] for item in group.record_ids if item in records_by_id]
        if source:
            members = [row for row in members if row["source"] == source]
        if not members or not any(_record_matches(row, terms) for row in members):
            continue
        folded_ids.update(row["record_id"] for row in members)
        entries.append(_group_entry("merged_group", group, members, records_by_id))

    for group in candidate_groups:
        members = [
            records_by_id[item]
            for item in group.record_ids
            if item in records_by_id and item not in folded_ids
        ]
        if source:
            members = [row for row in members if row["source"] == source]
        if not members or not any(_record_matches(row, terms) for row in members):
            continue
        if len(members) >= 2:
            entries.append(_group_entry("candidate_group", group, members, records_by_id))
        else:
            # 其余成员已在 merged 组折叠，剩余成员以独立条目保留可见性。
            entries.append(_solo_entry(members[0]))

    candidate_visible_ids = {
        item
        for group in candidate_groups
        for item in group.record_ids
        if not source or records_by_id.get(item, {}).get("source") == source
    }
    grouped_ids = folded_ids | candidate_visible_ids
    for record in records:
        if record["record_id"] in grouped_ids:
            continue
        if not _record_matches(record, terms):
            continue
        entries.append(_solo_entry(record))

    entries.sort(key=lambda entry: entry["sort_key"])
    return {
        "query": query,
        "source": source or "",
        "total_entries": len(entries),
        "collapsed_groups": sum(1 for entry in entries if entry["kind"] == "merged_group"),
        "candidate_groups": sum(1 for entry in entries if entry["kind"] == "candidate_group"),
        "entries": [_without_sort_key(entry) for entry in entries],
    }


def _group_entry(kind, group, members, records_by_id):
    member_ids = [row["record_id"] for row in members]
    canonical_id = group.canonical_id if group.canonical_id in member_ids else canonical_record(members)["record_id"]
    canonical = records_by_id[canonical_id]
    return {
        "kind": kind,
        "group_id": group.group_id,
        "status": group.status,
        "canonical_id": canonical_id,
        "title": canonical.get("title", ""),
        "grade": canonical.get("grade", ""),
        "preview": canonical["text"],
        "sort_key": (canonical.get("title", ""), canonical_id, group.group_id),
        "sources": [
            {
                "record_id": row["record_id"],
                "source": row["source"],
                "revision": row["revision"],
                "grade": row.get("grade", ""),
            }
            for row in members
        ],
        "decision_reasons": _reasons(group),
    }


def _solo_entry(record):
    return {
        "kind": "material",
        "group_id": None,
        "status": "standalone",
        "canonical_id": record["record_id"],
        "title": record.get("title", ""),
        "grade": record.get("grade", ""),
        "preview": record["text"],
        "sort_key": (record.get("title", ""), record["record_id"], ""),
        "sources": [
            {
                "record_id": record["record_id"],
                "source": record["source"],
                "revision": record["revision"],
                "grade": record.get("grade", ""),
            }
        ],
        "decision_reasons": [],
    }


def _reasons(group):
    return [reason for relation in group.relations for reason in relation.get("reasons", [])]


def _without_sort_key(entry):
    return {key: value for key, value in entry.items() if key != "sort_key"}


def material_detail(state, record_id):
    """单条材料的完整视图：原文、归并关系、引用与批注。"""
    from .links import LinkRegistry

    records_by_id = {row["record_id"]: row for row in state["records"]}
    if record_id not in records_by_id:
        raise KeyError(f"材料不存在：{record_id}")
    links = LinkRegistry(state.get("references", []), state.get("annotations", []))
    memberships = [
        {"group_id": row["group_id"], "status": row["status"]}
        for row in state["groups"]
        if record_id in row["record_ids"] and row["status"] in ("merged", "candidate")
    ]
    return {
        "record_id": record_id,
        "record": records_by_id[record_id],
        "memberships": memberships,
        "references": links.references_for(record_id),
        "annotations": links.annotations_for(record_id),
    }
