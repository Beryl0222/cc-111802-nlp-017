import json
from pathlib import Path

STATE_VERSION = 2


class RecordRepository:
    """持久化整个归并工作台状态。

    旧版状态文件（``{"records": [...]}``）仍可读取，读取时补齐新版字段；
    写入统一为带版本号的完整状态，保证升级过程不丢数据。

    ``read()`` 保持历史契约、仅返回记录列表；需要完整归并状态时使用
    :meth:`read_state`。
    """

    def __init__(self, path):
        self.path = Path(path)

    def read_state(self):
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return self.empty_state()
        if not isinstance(value, dict) or not isinstance(value.get("records"), list):
            raise ValueError("记录库格式无效")
        state = self.empty_state()
        state["records"] = value["records"]
        for key in ("groups", "blocked_pairs", "references", "annotations"):
            if isinstance(value.get(key), list):
                state[key] = value[key]
        state["state_version"] = int(value.get("state_version", 1))
        return state

    def write_state(self, state):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(state)
        payload["state_version"] = STATE_VERSION
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.path)

    def read(self):
        return self.read_state()["records"]

    def replace(self, records):
        state = self.read_state()
        state["records"] = records
        self.write_state(state)

    @staticmethod
    def empty_state():
        return {
            "state_version": STATE_VERSION,
            "records": [],
            "groups": [],
            "blocked_pairs": [],
            "references": [],
            "annotations": [],
        }
