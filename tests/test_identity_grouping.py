import tempfile
import unittest
from pathlib import Path

from nlp_reading_corpus_deduper.grouping import (
    MergeGroup,
    build_clusters,
    confirm_group,
    manual_merge,
    reconcile_groups,
    reject_group,
    split_group,
)
from nlp_reading_corpus_deduper.identity import decide_relation, fingerprint, pair_key


def make_record(record_id, text, grade="三年级", source="school-x", revision=1):
    from nlp_reading_corpus_deduper.records import TextRecord

    record = TextRecord(
        record_id=record_id,
        source=source,
        revision=revision,
        observed_at="2026-11-18T09:00:00Z",
        text=text,
        grade=grade,
    ).to_dict()
    record["fingerprint"] = fingerprint(record["text"])
    return record


class IdentityTests(unittest.TestCase):
    def test_format_and_whitespace_differences_share_identity(self):
        from nlp_reading_corpus_deduper.text import normalize_text

        left = make_record("a", "教研语料归并台需要保留材料身份和原文依据。")
        right = make_record("b", "　教研语料归并台\n需要保留材料身份和原文依据。\n")
        decision = decide_relation(left, right)
        self.assertEqual(decision["relation"], "duplicate")
        # 身份指纹建立在规范化正文之上：格式差异消除后哈希一致。
        self.assertEqual(
            fingerprint(normalize_text(left["text"]))["content_hash"],
            fingerprint(normalize_text(right["text"]))["content_hash"],
        )

    def test_grade_conflict_blocks_auto_merge_even_for_identical_text(self):
        left = make_record("a", "同一份课案正文", grade="三年级")
        right = make_record("b", "同一份课案正文", grade="五年级")
        decision = decide_relation(left, right)
        self.assertEqual(decision["relation"], "candidate")
        self.assertTrue(any("年级不同" in reason for reason in decision["reasons"]))

    def test_rewrite_stays_candidate_and_unrelated_text_is_unrelated(self):
        base = make_record("a", "教研语料归并台需要保留材料身份和原文依据。")
        rewrite = make_record(
            "b", "教研语料归并台应当保留材料身份以及原文依据，方便教师课后复核。"
        )
        sports = make_record("c", "校园运动会安全预案包含疏散路线、急救站位和志愿者分工。")
        self.assertEqual(decide_relation(base, rewrite)["relation"], "candidate")
        self.assertEqual(decide_relation(base, sports)["relation"], "unrelated")


class ClusteringTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            make_record("r1", "教研语料归并台需要保留材料身份和原文依据。"),
            make_record("r2", "　教研语料归并台\n需要保留材料身份和原文依据。", source="school-east"),
            make_record(
                "r3",
                "教研语料归并台应当保留材料身份以及原文依据，方便教师课后复核。",
                source="school-north",
            ),
            make_record(
                "r4",
                "教研语料归并台需要保留材料身份、原文依据和年级信息。",
                grade="五年级",
                source="school-west",
            ),
            make_record("r5", "校园运动会安全预案包含疏散路线、急救站位和志愿者分工。", grade="四年级"),
        ]

    def test_duplicate_and_candidate_clusters_coexist(self):
        clusters, _ = build_clusters(self.records, set())
        statuses = {(status, tuple(members)) for status, members in clusters}
        self.assertIn(("merged", ("r1", "r2")), statuses)
        candidate = next(members for status, members in clusters if status == "candidate")
        self.assertEqual(set(candidate), {"r1", "r2", "r3", "r4"})
        # 年级版本不会进入任何自动合并组，无关材料独立存在。
        self.assertFalse(any(status == "merged" and "r4" in members for status, members in clusters))
        self.assertFalse(any("r5" in members for _, members in clusters))

    def test_blocked_pair_cuts_the_edge(self):
        blocked = {pair_key("r1", "r2")}
        clusters, _ = build_clusters(self.records, blocked)
        self.assertFalse(any(status == "merged" for status, _ in clusters))

    def test_reconcile_keeps_group_id_and_events_then_archives_when_gone(self):
        groups, cache = reconcile_groups(self.records, [], set(), clock=lambda: "2026-11-18T10:00:00Z")
        merged = next(group for group in groups if group.status == "merged")
        merged_id = merged.group_id
        merged.events.append({"at": "2026-11-18T11:00:00Z", "action": "manual_confirm", "actor": "王老师"})
        # 再次导入相同材料：组号与事件日志延续。
        again, _ = reconcile_groups(self.records, groups, set(), clock=lambda: "2026-11-18T12:00:00Z")
        same = next(group for group in again if group.group_id == merged_id)
        self.assertEqual(same.status, "merged")
        self.assertTrue(any(event["action"] == "manual_confirm" for event in same.events))
        # 阻断后重算：merged 组消失并归档为 dissolved（候选边仍在，候选组另行保留）。
        blocked = {pair_key("r1", "r2")}
        dissolved, _ = reconcile_groups(self.records, again, blocked, clock=lambda: "2026-11-18T13:00:00Z")
        archived = next(group for group in dissolved if group.group_id == merged_id)
        self.assertEqual(archived.status, "dissolved")
        self.assertTrue(any(event["action"] == "cluster_dissolved" for event in archived.events))
        self.assertFalse(any(group.status == "merged" for group in dissolved))

    def test_confirm_reject_split_and_manual_merge_lifecycle(self):
        groups, _ = reconcile_groups(self.records, [], set(), clock=lambda: "2026-11-18T10:00:00Z")
        candidate = next(group for group in groups if group.status == "candidate")
        blocked = set()

        confirm_group(groups, candidate.group_id, "王老师", clock=lambda: "2026-11-18T10:01:00Z")
        self.assertEqual(candidate.status, "merged")

        # 纠正误合并：把五年级稿拆出，阻断对覆盖全部相关材料对。
        dissolved, survivors = split_group(
            groups, candidate.group_id, ["r4"], "李老师", blocked,
            clock=lambda: "2026-11-18T10:02:00Z",
        )
        self.assertEqual(dissolved.status, "dissolved")
        self.assertIn(pair_key("r1", "r4"), blocked)
        self.assertEqual(survivors[0].status, "candidate")
        self.assertNotIn("r4", survivors[0].record_ids)

        # 人工否决候选组只阻断候选关系对；已确认的 duplicate 对不被连带阻断。
        fresh_groups, _ = reconcile_groups(self.records, [], set(), clock=lambda: "2026-11-18T10:03:00Z")
        fresh_candidate = next(group for group in fresh_groups if group.status == "candidate")
        reject_group(fresh_groups, fresh_candidate.group_id, "李老师", blocked,
                     clock=lambda: "2026-11-18T10:03:30Z")
        self.assertIn(pair_key("r1", "r3"), blocked)
        self.assertNotIn(pair_key("r1", "r2"), blocked)

        # 人工归并会解散原持有组并清除对应阻断对。
        merged = manual_merge(
            groups, ["r1", "r3"], {row["record_id"]: row for row in self.records},
            "王老师", blocked, clock=lambda: "2026-11-18T10:04:00Z",
        )
        self.assertEqual(merged.status, "merged")
        self.assertNotIn(pair_key("r1", "r3"), blocked)

    def test_full_dissolution_blocks_only_distinct_pairs_never_self_pairs(self):
        groups, _ = reconcile_groups(self.records, [], set(), clock=lambda: "2026-11-18T10:00:00Z")
        candidate = next(group for group in groups if group.status == "candidate")
        blocked = set()
        split_group(groups, candidate.group_id, [], "李老师", blocked,
                    clock=lambda: "2026-11-18T10:02:00Z")
        # 4 个成员的两两组合恰为 6 对，不允许出现 x|x 自配对。
        self.assertEqual(len(blocked), 6)
        self.assertTrue(all(left != right for key in blocked for left, right in [key.split("|")]))


if __name__ == "__main__":
    unittest.main()
