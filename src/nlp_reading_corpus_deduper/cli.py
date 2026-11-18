import argparse
import json
from pathlib import Path

from .repository import RecordRepository
from .workflow import TextWorkflow


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _print(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def build_parser():
    parser = argparse.ArgumentParser(description="本地教研材料归并工作台")
    parser.add_argument("--state", default="var/records.json", help="状态文件路径")
    sub = parser.add_subparsers(dest="action")

    ingest = sub.add_parser("ingest", help="导入材料记录并执行身份判断与归并")
    ingest.add_argument("path", nargs="?", default="fixtures/records.json")

    list_parser = sub.add_parser("list", help="列出已存材料（原始记录视图）")
    list_parser.add_argument("--source")

    search = sub.add_parser("search", help="去重检索：合并项折叠、候选并列")
    search.add_argument("query", nargs="?", default="")
    search.add_argument("--source")

    groups = sub.add_parser("groups", help="查看归并组（merged/candidate/rejected/dissolved）")
    groups.add_argument("--status")

    detail = sub.add_parser("detail", help="查看单条材料的原文、引用与批注")
    detail.add_argument("record_id")

    compare = sub.add_parser("compare", help="对比两份材料的身份判定与差异")
    compare.add_argument("left_id")
    compare.add_argument("right_id")

    lineage = sub.add_parser("lineage", help="追查归并组内各校原始出处与决策日志")
    lineage.add_argument("group_id")

    diffs = sub.add_parser("diffs", help="归并组各版本相对代表记录的差异")
    diffs.add_argument("group_id")

    confirm = sub.add_parser("confirm", help="人工确认候选归并组")
    confirm.add_argument("group_id")
    confirm.add_argument("--actor", required=True)

    reject = sub.add_parser("reject", help="人工否决候选归并组并加入阻断列表")
    reject.add_argument("group_id")
    reject.add_argument("--actor", required=True)

    merge = sub.add_parser("merge", help="人工把指定材料归入同一组")
    merge.add_argument("record_ids", nargs="+")
    merge.add_argument("--actor", required=True)

    split = sub.add_parser("split", help="纠正误合并：从归并组拆出材料（不指定则整组解散）")
    split.add_argument("group_id")
    split.add_argument("--detach", nargs="*", default=[])
    split.add_argument("--actor", required=True)

    reference = sub.add_parser("add-reference", help="登记课程对具体材料的引用")
    reference.add_argument("path")

    annotation = sub.add_parser("add-annotation", help="给具体材料添加教研批注")
    annotation.add_argument("path")

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.action:
        parser.print_help()
        return
    workflow = TextWorkflow(RecordRepository(args.state))

    if args.action == "ingest":
        result = workflow.ingest(_load(args.path))
    elif args.action == "list":
        result = workflow.list_records(args.source)
    elif args.action == "search":
        result = workflow.search(args.query, args.source)
    elif args.action == "groups":
        result = workflow.list_groups(args.status)
    elif args.action == "detail":
        result = workflow.detail(args.record_id)
    elif args.action == "compare":
        result = workflow.compare(args.left_id, args.right_id)
    elif args.action == "lineage":
        result = workflow.lineage(args.group_id)
    elif args.action == "diffs":
        result = workflow.diffs(args.group_id)
    elif args.action == "confirm":
        result = workflow.confirm_group(args.group_id, args.actor)
    elif args.action == "reject":
        workflow.reject_group(args.group_id, args.actor)
        result = {"status": "rejected", "group_id": args.group_id}
    elif args.action == "merge":
        result = workflow.manual_merge(args.record_ids, args.actor)
    elif args.action == "split":
        result = workflow.split_group(args.group_id, args.actor, args.detach)
    elif args.action == "add-reference":
        result = workflow.add_reference(_load(args.path))
    elif args.action == "add-annotation":
        result = workflow.add_annotation(_load(args.path))
    else:  # pragma: no cover - argparse 已限定取值
        parser.error(f"未知操作：{args.action}")
    _print(result)


if __name__ == "__main__":
    main()
