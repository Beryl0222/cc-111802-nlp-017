"""教研材料归并工作台的应用服务层。"""

from .diffing import group_diffs, group_lineage, token_diff, unified_diff
from .grouping import (
    MergeGroup,
    confirm_group,
    manual_merge,
    reconcile_groups,
    reject_group,
    split_group,
)
from .identity import decide_relation, fingerprint, pair_key
from .links import LinkRegistry
from .records import TextRecord
from .search import material_detail, search_materials
from .text import annotate, normalize_text


class TextWorkflow:
    def __init__(self, repository, clock=None):
        self.repository = repository
        self.clock = clock

    def _now(self):
        if self.clock is None:
            from .grouping import now_iso

            return now_iso()
        return self.clock()

    # ------------------------------------------------------------------
    # 导入与身份判断
    # ------------------------------------------------------------------
    def ingest(self, values):
        if not isinstance(values, list):
            raise TypeError("导入内容必须是记录列表")
        # 先全部校验，任何一条非法都不写入，避免部分入库。
        incoming = [TextRecord.from_dict(value).to_dict() for value in values]
        state = self.repository.read_state()
        current = {record["record_id"]: record for record in state["records"]}

        for record in incoming:
            previous = current.get(record["record_id"])
            if previous is not None and record["revision"] < previous["revision"]:
                continue
            raw_text = record["text"]
            record["raw_text"] = raw_text
            record["text"] = normalize_text(raw_text)
            record["fingerprint"] = fingerprint(record["text"])
            record["analysis"] = annotate(record)
            current[record["record_id"]] = record

        # 旧版状态文件中的记录缺少指纹/原始文本字段，读取后就地补齐，
        # 保证升级后的身份判断与原文追溯对历史数据同样生效。
        for record in current.values():
            if "fingerprint" not in record:
                record["fingerprint"] = fingerprint(record["text"])
            if "raw_text" not in record:
                record["raw_text"] = record["text"]
            record.setdefault("title", "")
            record.setdefault("grade", "")
            record.setdefault("subject", "")
            if "analysis" not in record:
                record["analysis"] = annotate(record)

        records = sorted(current.values(), key=lambda row: row["record_id"])
        state["records"] = records
        groups = [MergeGroup.from_dict(row) for row in state["groups"]]
        blocked_pairs = set(state["blocked_pairs"])
        links = LinkRegistry(state["references"], state["annotations"])
        links.validate({row["record_id"] for row in records})

        groups, cache = reconcile_groups(records, groups, blocked_pairs, clock=self._now)
        state["groups"] = [group.to_dict() for group in groups]
        state["blocked_pairs"] = sorted(blocked_pairs)
        state.update(links.to_state())
        self.repository.write_state(state)
        return {
            "received": len(incoming),
            "stored": len(records),
            "merged_groups": sum(1 for group in groups if group.status == "merged"),
            "candidate_groups": sum(1 for group in groups if group.status == "candidate"),
        }

    # ------------------------------------------------------------------
    # 读取与检索
    # ------------------------------------------------------------------
    def list_records(self, source=None):
        state = self.repository.read_state()
        records = state["records"]
        if source:
            records = [record for record in records if record["source"] == source]
        return records

    def list_groups(self, status=None):
        groups = self.repository.read_state()["groups"]
        if status:
            groups = [group for group in groups if group["status"] == status]
        return groups

    def search(self, query="", source=None):
        return search_materials(self.repository.read_state(), query=query, source=source)

    def detail(self, record_id):
        return material_detail(self.repository.read_state(), record_id)

    def lineage(self, group_id):
        state = self.repository.read_state()
        group = self._group(state, group_id)
        records_by_id = {row["record_id"]: row for row in state["records"]}
        return group_lineage(records_by_id, group)

    def diffs(self, group_id):
        state = self.repository.read_state()
        group = self._group(state, group_id)
        records_by_id = {row["record_id"]: row for row in state["records"]}
        return group_diffs(records_by_id, group)

    def compare(self, left_id, right_id):
        state = self.repository.read_state()
        records_by_id = {row["record_id"]: row for row in state["records"]}
        left = records_by_id[left_id]
        right = records_by_id[right_id]
        decision = decide_relation(left, right)
        return {
            "pair": [left_id, right_id],
            "decision": decision,
            "blocked": pair_key(left_id, right_id) in set(state["blocked_pairs"]),
            "token_changes": token_diff(records_by_id, left_id, right_id),
            "unified_diff": unified_diff(records_by_id, left_id, right_id),
        }

    # ------------------------------------------------------------------
    # 引用与批注（绑定具体记录，不随归并组合并）
    # ------------------------------------------------------------------
    def add_reference(self, value):
        state = self.repository.read_state()
        links = LinkRegistry(state["references"], state["annotations"])
        values = value if isinstance(value, list) else [value]
        references = [links.add_reference(item) for item in values]
        links.validate({row["record_id"] for row in state["records"]})
        state.update(links.to_state())
        self.repository.write_state(state)
        return references if isinstance(value, list) else references[0]

    def add_annotation(self, value):
        state = self.repository.read_state()
        links = LinkRegistry(state["references"], state["annotations"])
        values = value if isinstance(value, list) else [value]
        annotations = [links.add_annotation(item) for item in values]
        links.validate({row["record_id"] for row in state["records"]})
        state.update(links.to_state())
        self.repository.write_state(state)
        return annotations if isinstance(value, list) else annotations[0]

    # ------------------------------------------------------------------
    # 人工裁决与误合并纠正
    # ------------------------------------------------------------------
    def confirm_group(self, group_id, actor):
        state = self.repository.read_state()
        groups = [MergeGroup.from_dict(row) for row in state["groups"]]
        group = confirm_group(groups, group_id, actor, clock=self._now)
        state["groups"] = [row.to_dict() for row in groups]
        self.repository.write_state(state)
        return group.to_dict()

    def reject_group(self, group_id, actor):
        state = self.repository.read_state()
        groups = [MergeGroup.from_dict(row) for row in state["groups"]]
        blocked_pairs = set(state["blocked_pairs"])
        reject_group(groups, group_id, actor, blocked_pairs, clock=self._now)
        state["groups"] = [row.to_dict() for row in groups]
        state["blocked_pairs"] = sorted(blocked_pairs)
        self.repository.write_state(state)

    def manual_merge(self, record_ids, actor):
        state = self.repository.read_state()
        groups = [MergeGroup.from_dict(row) for row in state["groups"]]
        blocked_pairs = set(state["blocked_pairs"])
        records_by_id = {row["record_id"]: row for row in state["records"]}
        group = manual_merge(groups, record_ids, records_by_id, actor, blocked_pairs, clock=self._now)
        state["groups"] = [row.to_dict() for row in groups]
        state["blocked_pairs"] = sorted(blocked_pairs)
        self.repository.write_state(state)
        return group.to_dict()

    def split_group(self, group_id, actor, detach_ids=None):
        state = self.repository.read_state()
        groups = [MergeGroup.from_dict(row) for row in state["groups"]]
        blocked_pairs = set(state["blocked_pairs"])
        group, survivors = split_group(
            groups, group_id, detach_ids or [], actor, blocked_pairs, clock=self._now
        )
        state["groups"] = [row.to_dict() for row in groups]
        state["blocked_pairs"] = sorted(blocked_pairs)
        self.repository.write_state(state)
        return {
            "dissolved_group": group.to_dict(),
            "survivor_groups": [row.to_dict() for row in survivors],
        }

    def _group(self, state, group_id):
        for row in state["groups"]:
            if row["group_id"] == group_id:
                return MergeGroup.from_dict(row)
        raise KeyError(f"归并组不存在：{group_id}")
