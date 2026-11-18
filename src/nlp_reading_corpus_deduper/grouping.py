"""材料版本归并。

身份判定把材料对分为三类（见 :mod:`identity`）：``duplicate``（可确认
重复）、``candidate``（相似但无法可靠判定，含年级冲突）、``unrelated``。
归并流程据此产出两种可同时存在、成员可交叉的归并组：

* ``duplicate`` 边用并查集求连通分量：分量内逐对均为 duplicate 才自动
  折叠（``merged``），不做传递性推断，杜绝 A≈B、B≈C 而 A、C 无关时的
  误合并；
* ``candidate`` 边同样求连通分量，生成 ``candidate`` 组并列保留，组内
  逐对关系与判定理由完整留存，供人工裁决。

归并组状态机：

* ``merged``：可确认的重复材料，检索时折叠展示；
* ``candidate``：无法可靠判定（措辞差异较大或年级冲突），保留并列候选；
* ``rejected``：人工裁决为不应合并，并把成员对写入阻断列表；
* ``dissolved``：误合并被纠正后的历史归档，仍可追溯当时的归并依据。

阻断列表（blocked pairs）保证纠正后的拆分不会在下次导入时自动复原。
"""

from dataclasses import dataclass, field, asdict
from itertools import combinations

from .identity import decide_relation, pair_key

GROUP_STATUSES = ("merged", "candidate", "rejected", "dissolved")


def now_iso():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def canonical_record(records):
    """归并组的代表记录：修订号最高 → 观测时间最早 → 记录号字典序。"""
    if not records:
        raise ValueError("空归并组没有代表记录")
    return min(records, key=lambda row: (-row["revision"], row["observed_at"], row["record_id"]))


@dataclass
class MergeGroup:
    group_id: str
    record_ids: list
    status: str
    canonical_id: str
    created_at: str
    events: list = field(default_factory=list)
    relations: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        return cls(
            group_id=str(value["group_id"]),
            record_ids=list(value["record_ids"]),
            status=str(value["status"]),
            canonical_id=str(value["canonical_id"]),
            created_at=str(value.get("created_at", "")),
            events=list(value.get("events", [])),
            relations=list(value.get("relations", [])),
        )


class _UnionFind:
    def __init__(self, items):
        self.parent = {item: item for item in items}

    def find(self, item):
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != item:
            self.parent[item], item = root, self.parent[item]
        return root

    def union(self, left, right):
        self.parent[self.find(left)] = self.find(right)

    def components(self):
        result = {}
        for item in self.parent:
            result.setdefault(self.find(item), []).append(item)
        return [sorted(member) for member in result.values()]


def _relation_cache(records):
    """计算全部记录两两之间的身份判定。"""
    cache = {}
    for index, left in enumerate(records):
        for right in records[index + 1 :]:
            decision = decide_relation(left, right)
            cache[pair_key(left["record_id"], right["record_id"])] = decision
    return cache


def get_relation(cache, left_id, right_id):
    return cache.get(pair_key(left_id, right_id))


def build_clusters(records, blocked_pairs):
    """返回 (归并簇列表, 关系判定缓存)。

    每个簇为 ``(状态, 成员)``：duplicate 边连通分量产出 ``merged`` 簇，
    candidate 边连通分量产出 ``candidate`` 簇；阻断列表中的材料对不连边。
    """
    cache = _relation_cache(records)
    record_ids = [record["record_id"] for record in records]
    duplicate_uf = _UnionFind(record_ids)
    candidate_uf = _UnionFind(record_ids)

    for key, decision in cache.items():
        if key in blocked_pairs:
            continue
        left_id, right_id = key.split("|")
        if decision["relation"] == "duplicate":
            duplicate_uf.union(left_id, right_id)
            candidate_uf.union(left_id, right_id)
        elif decision["relation"] == "candidate":
            candidate_uf.union(left_id, right_id)

    clusters = [("merged", member) for member in duplicate_uf.components() if len(member) >= 2]
    clusters.extend(("candidate", member) for member in candidate_uf.components() if len(member) >= 2)
    clusters.sort(key=lambda item: (item[0], item[1]))
    return clusters, cache


def _cluster_relations(cluster, cache):
    relations = []
    for index, left_id in enumerate(cluster):
        for right_id in cluster[index + 1 :]:
            decision = dict(get_relation(cache, left_id, right_id))
            decision["pair"] = [left_id, right_id]
            relations.append(decision)
    relations.sort(key=lambda item: item["pair"])
    return relations


def _next_group_id(existing):
    return f"group-{len(existing) + 1:04d}"


def reconcile_groups(records, existing_groups, blocked_pairs, clock=now_iso):
    """重新聚类并与历史归并组对账，保留人工裁决与可追溯事件。

    对账键为（状态, 成员集合）：聚类结果与历史组完全一致时沿用原组号
    与事件日志；成员扩展时继承父组；``rejected`` 组在重新导入后保持
    rejected（人工否决持久生效）；其余消失的历史组归档为 dissolved，
    保证每次裁决都可追溯。
    """
    records_by_id = {row["record_id"]: row for row in records}
    clusters, cache = build_clusters(records, blocked_pairs)
    merged_members = [frozenset(member) for status, member in clusters if status == "merged"]
    # 与某个 merged 组成员完全相同的 candidate 簇是冗余视图：这些材料
    # 已经过逐对确认折叠，不再需要并列候选。
    clusters = [
        (status, member)
        for status, member in clusters
        if not (status == "candidate" and frozenset(member) in merged_members)
    ]
    old_by_key = {
        (group.status, frozenset(group.record_ids)): group
        for group in existing_groups
        if group.status in ("merged", "candidate")
    }
    consumed = set()
    result = []

    for status, cluster in clusters:
        members = frozenset(cluster)
        relations = _cluster_relations(cluster, cache)
        old = old_by_key.get((status, members))
        if old is not None:
            consumed.add(old.group_id)
            old.record_ids = list(cluster)
            old.relations = relations or old.relations
            result.append(old)
            continue

        parent = next(
            (
                group
                for group in existing_groups
                if group.group_id not in consumed
                and group.status in ("merged", "candidate")
                and set(group.record_ids) < members
            ),
            None,
        )
        if parent is not None:
            consumed.add(parent.group_id)
            added = sorted(members - set(parent.record_ids))
            final_status = status
            if parent.status == "rejected":
                final_status = "candidate"
            parent.events.append(
                {
                    "at": clock(),
                    "action": "cluster_extended",
                    "added_record_ids": added,
                    "detail": "新导入材料进入归并范围，状态按最新相似度重新评估",
                }
            )
            result.append(
                MergeGroup(
                    group_id=parent.group_id,
                    record_ids=list(cluster),
                    status=final_status,
                    canonical_id=canonical_record([records_by_id[item] for item in cluster])["record_id"],
                    created_at=parent.created_at,
                    events=parent.events,
                    relations=relations,
                )
            )
            continue

        result.append(
            MergeGroup(
                group_id=_next_group_id(existing_groups + result),
                record_ids=list(cluster),
                status=status,
                canonical_id=canonical_record([records_by_id[item] for item in cluster])["record_id"],
                created_at=clock(),
                events=[
                    {
                        "at": clock(),
                        "action": "cluster_created",
                        "detail": "逐对均为可确认重复，自动合并" if status == "merged" else "存在候选关系或年级冲突，保留人工裁决",
                    }
                ],
                relations=relations,
            )
        )

    # 未被新聚类继承的历史组：
    # * rejected 组保持 rejected——人工否决必须持久，不能因重新导入而
    #   被新组号覆盖或悄悄复活（阻断对已阻止其成员重新自动归并）；
    # * merged/candidate 组归档为 dissolved，事件链仍可追溯。
    for old in existing_groups:
        if old.group_id in consumed:
            continue
        if old.status == "rejected":
            result.append(old)
            continue
        if old.status not in ("merged", "candidate"):
            continue
        old.status = "dissolved"
        old.events.append({"at": clock(), "action": "cluster_dissolved", "detail": "新聚类不再产生相同的归并组"})
        result.append(old)
    return result, cache


def _find_group(groups, group_id):
    for group in groups:
        if group.group_id == group_id:
            return group
    raise KeyError(f"归并组不存在：{group_id}")


def confirm_group(groups, group_id, actor, clock=now_iso):
    """人工确认候选：并列候选升级为合并展示（允许人工裁决年级差异）。"""
    group = _find_group(groups, group_id)
    if len(group.record_ids) < 2:
        raise ValueError("单条材料的归并组无需确认")
    group.status = "merged"
    group.events.append({"at": clock(), "action": "manual_confirm", "actor": actor, "detail": "人工确认重复关系"})
    return group


def reject_group(groups, group_id, actor, blocked_pairs, clock=now_iso):
    """人工否决候选：把组内的候选关系对写入阻断列表。

    已经过身份确认的 duplicate 对（它们同时属于某个 merged 组）不写入
    阻断列表——人工否决的是"无法可靠判定的候选关系"，不能连带拆散
    已确认的重复材料；下次导入时 merged 组照常生成。
    """
    group = _find_group(groups, group_id)
    group.status = "rejected"
    for relation in group.relations:
        if relation.get("relation") == "duplicate":
            continue
        left_id, right_id = relation["pair"]
        blocked_pairs.add(pair_key(left_id, right_id))
    group.events.append(
        {
            "at": clock(),
            "action": "manual_reject",
            "actor": actor,
            "detail": "人工否决候选关系并加入阻断列表（已确认重复对不受影响）",
        }
    )
    return group


def manual_merge(groups, record_ids, records_by_id, actor, blocked_pairs, clock=now_iso):
    """人工把指定材料强制归入同一组（用于候选列表的人工决定）。"""
    record_ids = sorted(record_ids)
    if len(record_ids) < 2:
        raise ValueError("至少需要两份材料才能人工归并")
    missing = [item for item in record_ids if item not in records_by_id]
    if missing:
        raise KeyError(f"材料记录不存在：{', '.join(missing)}")
    for left_id, right_id in combinations(record_ids, 2):
        blocked_pairs.discard(pair_key(left_id, right_id))
    holders = [group for group in groups if set(record_ids) & set(group.record_ids)]
    for group in holders:
        group.status = "dissolved"
        group.events.append({"at": clock(), "action": "cluster_dissolved", "detail": "人工归并取代原归并组"})
    merged = MergeGroup(
        group_id=_next_group_id(groups),
        record_ids=record_ids,
        status="merged",
        canonical_id=canonical_record([records_by_id[item] for item in record_ids])["record_id"],
        created_at=clock(),
        events=[{"at": clock(), "action": "manual_merge", "actor": actor, "detail": "人工指定归并关系"}],
        relations=[],
    )
    groups.append(merged)
    return merged


def split_group(groups, group_id, detach_ids, actor, blocked_pairs, clock=now_iso):
    """纠正误合并：把指定材料从归并组拆出（detach 为空则整组解散）。

    引用与批注绑定在具体记录上，本操作不改动它们，拆分后自然各归其主。
    """
    group = _find_group(groups, group_id)
    detach_ids = sorted(set(detach_ids)) if detach_ids else list(group.record_ids)
    unknown = [item for item in detach_ids if item not in group.record_ids]
    if unknown:
        raise KeyError(f"材料不在归并组内：{', '.join(unknown)}")
    remaining = [item for item in group.record_ids if item not in detach_ids]
    # 拆出材料与留存材料之间、拆出材料彼此之间，全部写入阻断对，
    # 保证纠正后的归并关系不会在下次导入时自动复原。
    for left_id, right_id in combinations(detach_ids, 2):
        blocked_pairs.add(pair_key(left_id, right_id))
    for left_id in detach_ids:
        for right_id in remaining:
            blocked_pairs.add(pair_key(left_id, right_id))
    group.status = "dissolved"
    group.events.append(
        {
            "at": clock(),
            "action": "manual_split",
            "actor": actor,
            "detached_record_ids": detach_ids,
            "detail": "纠正误合并，拆分对写入阻断列表",
        }
    )
    survivors = [
        MergeGroup(
            group_id=_next_group_id(groups),
            record_ids=remaining,
            status="candidate",
            canonical_id=group.canonical_id if group.canonical_id in remaining else remaining[0],
            created_at=clock(),
            events=[{"at": clock(), "action": "split_survivor", "actor": actor, "detail": "误合并拆分后保留的候选组"}],
            relations=[],
        )
    ] if len(remaining) >= 2 else []
    groups.extend(survivors)
    return group, survivors
