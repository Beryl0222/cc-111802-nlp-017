from dataclasses import dataclass, asdict
from datetime import datetime


def _check_iso_datetime(value):
    datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True)
class TextRecord:
    record_id: str
    source: str
    revision: int
    observed_at: str
    text: str
    context: str = ""
    title: str = ""
    grade: str = ""
    subject: str = ""

    @classmethod
    def from_dict(cls, value):
        record = cls(
            record_id=str(value.get("record_id", "")).strip(),
            source=str(value.get("source", "")).strip(),
            revision=int(value.get("revision", 0)),
            observed_at=str(value.get("observed_at", "")).strip(),
            text=str(value.get("text", "")),
            context=str(value.get("context", "")).strip(),
            title=str(value.get("title", "")).strip(),
            grade=str(value.get("grade", "")).strip(),
            subject=str(value.get("subject", "")).strip(),
        )
        if not record.record_id or not record.source or record.revision < 1 or not record.text.strip():
            raise ValueError("文本记录缺少身份、来源、有效修订号或正文")
        _check_iso_datetime(record.observed_at)
        return record

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class Reference:
    """课程对具体材料记录的引用。引用绑定记录，不随归并组合并。"""

    reference_id: str
    course_id: str
    record_id: str
    created_at: str
    note: str = ""

    @classmethod
    def from_dict(cls, value):
        record = cls(
            reference_id=str(value.get("reference_id", "")).strip(),
            course_id=str(value.get("course_id", "")).strip(),
            record_id=str(value.get("record_id", "")).strip(),
            created_at=str(value.get("created_at", "")).strip(),
            note=str(value.get("note", "")).strip(),
        )
        if not record.reference_id or not record.course_id or not record.record_id:
            raise ValueError("引用缺少标识、课程或材料记录")
        _check_iso_datetime(record.created_at)
        return record

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class Annotation:
    """教研批注，始终挂在具体材料记录上。"""

    annotation_id: str
    record_id: str
    author: str
    body: str
    created_at: str

    @classmethod
    def from_dict(cls, value):
        record = cls(
            annotation_id=str(value.get("annotation_id", "")).strip(),
            record_id=str(value.get("record_id", "")).strip(),
            author=str(value.get("author", "")).strip(),
            body=str(value.get("body", "")),
            created_at=str(value.get("created_at", "")).strip(),
        )
        if not record.annotation_id or not record.record_id or not record.author or not record.body.strip():
            raise ValueError("批注缺少标识、材料、作者或正文")
        _check_iso_datetime(record.created_at)
        return record

    def to_dict(self):
        return asdict(self)
