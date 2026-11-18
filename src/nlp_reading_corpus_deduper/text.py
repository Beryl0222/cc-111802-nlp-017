import re
import unicodedata

TOKEN_RE = re.compile(r"[㐀-鿿]|[A-Za-z]+(?:['’-][A-Za-z]+)*|\d+(?:\.\d+)?|[^\w\s]", re.UNICODE)

# 标点/符号类 token 不参与身份指纹，避免署名、标点调整影响身份判断。
PUNCTUATION_RE = re.compile(r"[^\w]", re.UNICODE)


def normalize_text(text):
    """规范化正文，消除格式类差异对材料身份的影响。

    先做 NFKC 统一全半角，再按任意 Unicode 空白（含全角空格 U+3000、
    换行、制表符）切分。切分结果中，纯 CJK 片段直接相连，含字母/数字
    的片段之间保留单个空格，从而既消除中文排版缩进、换行等格式差异，
    又不把英文词或数字粘连成新词。
    """
    normalized = unicodedata.normalize("NFKC", text)
    parts = normalized.split()
    if not parts:
        return ""
    result = [parts[0]]
    for part in parts[1:]:
        previous = result[-1]
        if _is_cjk(previous[-1]) and _is_cjk(part[0]):
            result.append(part)
        else:
            result.append(" ")
            result.append(part)
    return "".join(result)


def _is_cjk(char):
    return "一" <= char <= "鿿" or "㐀" <= char <= "䶿"


def tokenize(text):
    return TOKEN_RE.findall(normalize_text(text))


def content_tokens(text):
    """仅保留内容 token（汉字、字母词、数字），过滤标点与符号。"""
    return [token for token in tokenize(text) if not PUNCTUATION_RE.fullmatch(token)]


def bigrams(tokens):
    """相邻 token 二元组，用来捕捉词序层面的措辞差异。"""
    return [(tokens[index], tokens[index + 1]) for index in range(len(tokens) - 1)]


def annotate(record):
    tokens = tokenize(record["text"])
    unique = []
    for token in tokens:
        if token not in unique:
            unique.append(token)
    return {
        "tokens": tokens,
        "terms": unique,
        "character_count": len(normalize_text(record["text"])),
        "token_count": len(tokens),
    }
