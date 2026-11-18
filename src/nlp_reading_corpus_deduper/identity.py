"""材料身份判断。

身份由"规范化正文内容"决定，而不是文件名或导入编号：

* ``content_hash``：规范化正文的稳定哈希，完全相同的材料（仅格式/署名差异
  在预处理中被消除后）哈希一致；
* 术语集合与相邻二元组集合用于估算措辞差异，配合分级阈值决定自动合并、
  保留候选还是视为无关材料；
* 年级是教学维度上的硬边界：年级不同的材料即便正文高度相似，也禁止自动
  合并，只能作为待人工裁决的候选。
"""

import hashlib

from .text import bigrams, content_tokens, normalize_text

# 综合相似度 >= 该阈值且年级兼容，才可自动合并。
AUTO_MERGE_THRESHOLD = 0.90
# 综合相似度 >= 该阈值（但低于自动阈值）时保留为并列候选。
CANDIDATE_THRESHOLD = 0.55


def content_hash(normalized_text):
    digest = hashlib.sha256(normalized_text.encode("utf-8"))
    return digest.hexdigest()


def fingerprint(text):
    normalized = normalize_text(text)
    tokens = content_tokens(normalized)
    terms = set(tokens)
    pairs = set(bigrams(tokens))
    return {
        "normalized_text": normalized,
        "content_hash": content_hash(normalized),
        "tokens": tokens,
        "terms": sorted(terms),
        "bigrams": sorted("".join(pair) for pair in pairs),
    }


def _jaccard(left, right):
    if not left and not right:
        return 1.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _overlap(left, right):
    """Szymkiewicz–Simpson 重叠系数，对长短版本的包含关系更友好。"""
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def similarity(left, right):
    """综合 0~1 相似度：术语与二元组各取重叠系数/Jaccard 的均值。"""
    left_terms = set(left["terms"])
    right_terms = set(right["terms"])
    left_pairs = set(map(tuple, (item for item in left["bigrams"])))
    right_pairs = set(map(tuple, (item for item in right["bigrams"])))
    term_score = (_overlap(left_terms, right_terms) + _jaccard(left_terms, right_terms)) / 2
    pair_score = (_overlap(left_pairs, right_pairs) + _jaccard(left_pairs, right_pairs)) / 2
    return (term_score + pair_score) / 2


def grade_compatible(left, right):
    """年级未知（空）视为兼容；双方都给出且不同则不兼容。"""
    left_grade = (left.get("grade") or "").strip()
    right_grade = (right.get("grade") or "").strip()
    return not left_grade or not right_grade or left_grade == right_grade


def decide_relation(left, right):
    """返回两份材料之间的身份关系判定。

    返回字段：
    * ``relation``：``duplicate``（可自动合并）/ ``candidate``（保留候选）/
      ``unrelated``（无关，不提示）；
    * ``score``：综合相似度；
    * ``reasons``：判定依据，年级冲突会显式记录，避免静默误合并。
    """
    score = similarity(left["fingerprint"], right["fingerprint"])
    same_hash = left["fingerprint"]["content_hash"] == right["fingerprint"]["content_hash"]
    reasons = []
    if same_hash:
        reasons.append("规范化正文完全一致")
    if score >= AUTO_MERGE_THRESHOLD:
        reasons.append(f"相似度 {score:.3f} 达到自动合并阈值 {AUTO_MERGE_THRESHOLD:.2f}")
    elif score >= CANDIDATE_THRESHOLD:
        reasons.append(f"相似度 {score:.3f} 处于候选区间（{CANDIDATE_THRESHOLD:.2f}~{AUTO_MERGE_THRESHOLD:.2f}）")
    if not grade_compatible(left, right):
        reasons.append(f"年级不同：{left.get('grade')} 与 {right.get('grade')}，禁止自动合并")

    if same_hash and grade_compatible(left, right):
        relation = "duplicate"
    elif score >= AUTO_MERGE_THRESHOLD and grade_compatible(left, right):
        relation = "duplicate"
    elif score >= CANDIDATE_THRESHOLD:
        relation = "candidate"
    else:
        relation = "unrelated"
    return {"relation": relation, "score": round(score, 4), "reasons": reasons}


def pair_key(left_id, right_id):
    return "|".join(sorted((left_id, right_id)))
