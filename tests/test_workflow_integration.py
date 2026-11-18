import json
import tempfile
import unittest
from pathlib import Path

from nlp_reading_corpus_deduper.repository import RecordRepository, STATE_VERSION
from nlp_reading_corpus_deduper.workflow import TextWorkflow

TICKS = [f"2026-11-18T10:{minute:02d}:00Z" for minute in range(20)]


def base_record(record_id, source, text, **extra):
    value = {
        "record_id": record_id,
        "source": source,
        "revision": 1,
        "observed_at": "2026-11-18T09:00:00Z",
        "text": text,
        "grade": "三年级",
        "subject": "语文",
    }
    value.update(extra)
    return value


class WorkflowIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.ticks = iter(TICKS)
        self.repository = RecordRepository(Path(self.directory.name) / "state.json")
        self.workflow = TextWorkflow(self.repository, clock=lambda: next(self.ticks))

    def tearDown(self):
        self.directory.cleanup()

    def _records(self):
        return [
            base_record("r1", "school-synthetic", "教研语料归并台需要保留材料身份和原文依据。",
                        title="材料身份", context="教研演练"),
            base_record("r2", "school-east", "　教研语料归并台\n需要保留材料身份和原文依据。\n",
                        title="材料身份（东区稿）", context="校际共享"),
            base_record("r3", "school-north",
                        "教研语料归并台应当保留材料身份以及原文依据，方便教师课后复核。",
                        title="材料身份（北区稿）", context="同题异构"),
            base_record("r4", "school-west",
                        "教研语料归并台需要保留材料身份、原文依据和年级信息。",
                        title="材料身份（五年级版）", grade="五年级", revision=3),
            base_record("r5", "school-east",
                        "校园运动会安全预案包含疏散路线、急救站位和志愿者分工。",
                        title="运动会安全预案", grade="四年级", subject="德育"),
        ]

    def test_ingest_folds_confirmed_duplicates_and_keeps_candidates_parallel(self):
        summary = self.workflow.ingest(self._records())
        self.assertEqual(summary["stored"], 5)
        self.assertEqual(summary["merged_groups"], 1)
        self.assertEqual(summary["candidate_groups"], 1)

        groups = {group["status"]: group for group in self.workflow.list_groups()}
        self.assertEqual(sorted(groups["merged"]["record_ids"]), ["r1", "r2"])
        self.assertEqual(set(groups["candidate"]["record_ids"]), {"r1", "r2", "r3", "r4"})

        result = self.workflow.search()
        kinds = [entry["kind"] for entry in result["entries"]]
        self.assertIn("merged_group", kinds)
        self.assertIn("candidate_group", kinds)
        # 折叠后 r1/r2 只出现一次；无关材料独立出现。
        canonical_ids = [entry["canonical_id"] for entry in result["entries"]]
        self.assertEqual(canonical_ids.count("r1"), 1)
        self.assertNotIn("r2", canonical_ids)
        self.assertIn("r5", canonical_ids)

        merged_entry = next(entry for entry in result["entries"] if entry["kind"] == "merged_group")
        self.assertEqual(
            {source["source"] for source in merged_entry["sources"]},
            {"school-synthetic", "school-east"},
        )

    def test_search_filters_by_term_and_source(self):
        self.workflow.ingest(self._records())
        hits = self.workflow.search("运动会")
        self.assertEqual(len(hits["entries"]), 1)
        self.assertEqual(hits["entries"][0]["canonical_id"], "r5")
        east = self.workflow.search(source="school-east")
        self.assertTrue(all(
            all(source["source"] == "school-east" for source in entry["sources"])
            for entry in east["entries"]
        ))

    def test_references_and_annotations_stay_bound_after_wrong_merge_correction(self):
        self.workflow.ingest(self._records())
        self.workflow.add_reference([
            {"reference_id": "ref-1", "course_id": "course-1", "record_id": "r1",
             "created_at": "2026-11-18T14:00:00Z", "note": "课程一引用原稿"},
            {"reference_id": "ref-2", "course_id": "course-2", "record_id": "r2",
             "created_at": "2026-11-18T14:05:00Z", "note": "课程二引用东区稿"},
        ])
        self.workflow.add_annotation([
            {"annotation_id": "ann-1", "record_id": "r1", "author": "王老师",
             "body": "原稿作为代表版本。", "created_at": "2026-11-18T15:00:00Z"},
            {"annotation_id": "ann-2", "record_id": "r4", "author": "李老师",
             "body": "五年级版本不可与三年级稿混并。", "created_at": "2026-11-18T15:05:00Z"},
        ])

        # 不同课程的引用独立存在，不会因材料归并而合并。
        self.assertEqual(len(self.workflow.detail("r1")["references"]), 1)
        self.assertEqual(len(self.workflow.detail("r2")["references"]), 1)
        self.assertEqual(
            {ref["course_id"] for ref in self.workflow.detail("r1")["references"]},
            {"course-1"},
        )

        candidate = self.workflow.list_groups("candidate")[0]
        self.workflow.confirm_group(candidate["group_id"], "王老师")
        split = self.workflow.split_group(candidate["group_id"], "李老师", ["r4"])
        self.assertEqual(split["dissolved_group"]["status"], "dissolved")

        # 纠正后引用与批注自动回到正确材料，无需迁移。
        detail_r4 = self.workflow.detail("r4")
        self.assertEqual(detail_r4["memberships"], [])
        self.assertEqual([ann["annotation_id"] for ann in detail_r4["annotations"]], ["ann-2"])
        self.assertEqual(
            [ref["reference_id"] for ref in self.workflow.detail("r1")["references"]],
            ["ref-1"],
        )
        self.assertEqual(
            [ref["reference_id"] for ref in self.workflow.detail("r2")["references"]],
            ["ref-2"],
        )

        # 阻断对持久化：再次导入不会把 r4 自动并回候选组。
        self.workflow.ingest(self._records())
        for group in self.workflow.list_groups():
            if group["status"] == "candidate":
                self.assertNotIn("r4", group["record_ids"])

    def test_lineage_preserves_raw_text_and_diffs_trace_each_version(self):
        self.workflow.ingest(self._records())
        merged = self.workflow.list_groups("merged")[0]
        lineage = self.workflow.lineage(merged["group_id"])
        east_version = next(row for row in lineage["versions"] if row["record_id"] == "r2")
        # 原始文本（含全角空格与换行）未被规范化覆盖。
        self.assertIn("　", east_version["raw_text"])
        self.assertIn("\n", east_version["raw_text"])
        self.assertNotIn("\n", east_version["normalized_text"])
        self.assertEqual(lineage["canonical_id"], "r1")

        diffs = self.workflow.diffs(merged["group_id"])
        self.assertEqual(len(diffs), 1)
        unified = "".join(diffs[0]["unified_diff"])
        self.assertIn("school-synthetic", unified)
        self.assertIn("school-east", unified)

        comparison = self.workflow.compare("r1", "r3")
        self.assertEqual(comparison["decision"]["relation"], "candidate")
        self.assertTrue(any(change["change"] == "insert" for change in comparison["token_changes"]))

    def test_reingest_is_idempotent_and_revision_updates_canonical(self):
        records = self._records()
        self.workflow.ingest(records)
        first_state = json.loads(self.repository.path.read_text(encoding="utf-8"))
        self.workflow.ingest(records)
        second_state = json.loads(self.repository.path.read_text(encoding="utf-8"))
        self.assertEqual(
            [group["group_id"] for group in first_state["groups"]],
            [group["group_id"] for group in second_state["groups"]],
        )

        # r1 出修订 2 后成为代表记录（同修订号下按观测时间/记录号兜底）。
        updated = [dict(row, revision=2) if row["record_id"] == "r1" else row for row in records]
        self.workflow.ingest(updated)
        merged = self.workflow.list_groups("merged")[0]
        self.assertEqual(merged["canonical_id"], "r1")

    def test_manual_merge_and_reject_round_trip(self):
        self.workflow.ingest(self._records())
        candidate = self.workflow.list_groups("candidate")[0]
        self.workflow.reject_group(candidate["group_id"], "李老师")
        state = json.loads(self.repository.path.read_text(encoding="utf-8"))
        # 候选关系对被阻断，但 r1/r2 的已确认重复对不受影响。
        self.assertIn("r3|r4", state["blocked_pairs"])
        self.assertNotIn("r1|r2", state["blocked_pairs"])

        # 重新导入：merged 组照常生成；被人工否决的组保持 rejected，
        # 不会自行复活为待裁决候选。
        self.workflow.ingest(self._records())
        statuses = {group["status"] for group in self.workflow.list_groups()}
        self.assertIn("merged", statuses)
        rejected = self.workflow.list_groups("rejected")
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["group_id"], candidate["group_id"])

        merged = self.workflow.manual_merge(["r1", "r4"], "王老师")
        self.assertEqual(merged["status"], "merged")
        self.assertEqual(sorted(merged["record_ids"]), ["r1", "r4"])
        state = json.loads(self.repository.path.read_text(encoding="utf-8"))
        self.assertNotIn("r1|r4", state["blocked_pairs"])

    def test_dangling_link_is_rejected_without_partial_write(self):
        self.workflow.ingest(self._records())
        with self.assertRaises(ValueError):
            self.workflow.add_reference(
                {"reference_id": "ref-x", "course_id": "course-9", "record_id": "missing",
                 "created_at": "2026-11-18T14:00:00Z"}
            )
        self.assertEqual(self.repository.read_state()["references"], [])

    def test_legacy_state_file_is_migrated_without_losing_records(self):
        legacy_path = self.repository.path
        legacy_path.write_text(
            json.dumps({"records": [
                base_record("old-1", "school-synthetic", "独立历史材料。")
            ]}, ensure_ascii=False),
            encoding="utf-8",
        )
        state = self.repository.read_state()
        self.assertEqual(state["state_version"], 1)
        self.assertEqual(state["groups"], [])
        self.workflow.ingest(self._records())
        migrated = self.repository.read_state()
        self.assertEqual(migrated["state_version"], STATE_VERSION)
        self.assertIn("old-1", [row["record_id"] for row in migrated["records"]])
        self.assertIn("blocked_pairs", migrated)


if __name__ == "__main__":
    unittest.main()
