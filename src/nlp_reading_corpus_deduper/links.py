"""课程引用与教研批注。

引用和批注都绑定到具体的材料记录（``record_id``），从不绑定归并组：

* 同一材料被不同课程引用时，每条引用各自独立，归并展示不会把引用合并；
* 纠正误合并时，记录本身不被删除，只是退出归并组，挂在记录上的引用与
  批注因此自动回到正确材料，无需迁移、也不会串组。
"""

from .records import Annotation, Reference


def _index(items, key):
    result = {}
    for item in items:
        value = item[key]
        if value in result:
            raise ValueError(f"{key} 重复：{value}")
        result[value] = item
    return result


class LinkRegistry:
    def __init__(self, references=None, annotations=None):
        self.references = _index(list(references or []), "reference_id")
        self.annotations = _index(list(annotations or []), "annotation_id")

    def add_reference(self, value):
        reference = Reference.from_dict(value).to_dict()
        if reference["reference_id"] in self.references:
            raise ValueError(f"引用已存在：{reference['reference_id']}")
        self.references[reference["reference_id"]] = reference
        return reference

    def add_annotation(self, value):
        annotation = Annotation.from_dict(value).to_dict()
        if annotation["annotation_id"] in self.annotations:
            raise ValueError(f"批注已存在：{annotation['annotation_id']}")
        self.annotations[annotation["annotation_id"]] = annotation
        return annotation

    def references_for(self, record_id):
        return [row for row in self.references.values() if row["record_id"] == record_id]

    def annotations_for(self, record_id):
        return [row for row in self.annotations.values() if row["record_id"] == record_id]

    def references_for_course(self, course_id):
        return [row for row in self.references.values() if row["course_id"] == course_id]

    def validate(self, known_record_ids):
        """所有链接必须指向仍存在的材料记录。"""
        for reference in self.references.values():
            if reference["record_id"] not in known_record_ids:
                raise ValueError(f"引用 {reference['reference_id']} 指向不存在的材料 {reference['record_id']}")
        for annotation in self.annotations.values():
            if annotation["record_id"] not in known_record_ids:
                raise ValueError(f"批注 {annotation['annotation_id']} 指向不存在的材料 {annotation['record_id']}")

    def to_state(self):
        return {
            "references": sorted(self.references.values(), key=lambda row: row["reference_id"]),
            "annotations": sorted(self.annotations.values(), key=lambda row: row["annotation_id"]),
        }
