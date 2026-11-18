# 教研语料归并台

教研语料归并台用于处理本地合成教研文本，解决校际共享材料的重复导入与
版本归并问题：**按材料内容而非文件名判断身份**，可确认的重复材料合并
展示，无法可靠判断的保留并列候选供人工裁决，同时完整保留各校原始出处、
版本差异、课程引用与教研批注。

## 设计要点

### 材料身份判断（`identity.py`）

* 正文先做规范化（NFKC 全半角统一、排版空白折叠、中文片段间空格消除），
  再计算稳定的内容哈希与术语/相邻二元组指纹；格式、署名排版差异不会产生
  新身份。
* 两两材料给出三级关系：
  * `duplicate`：规范化正文一致，或相似度达到自动合并阈值且年级兼容；
  * `candidate`：相似度处于候选区间，或存在年级冲突——禁止自动合并；
  * `unrelated`：相似度不足，不提示归并。
* 年级是硬边界：不同年级的同主题版本（如三年级/五年级课案）永远不会被
  自动合并，只会进入候选列表。

### 版本归并（`grouping.py`）

* `duplicate` 关系用并查集求连通分量，组内逐对均为可确认重复才自动折叠
  （`merged`），不做传递性推断，避免 A≈B、B≈C 而 A、C 无关时误合并。
* `candidate` 关系同样求连通分量，生成 `candidate` 组并列保留，组内逐对
  相似度、判定理由（含年级冲突）完整留存。
* 归并组状态机：`merged` / `candidate` / `rejected` / `dissolved`。
  人工否决写入**阻断对**，重新导入不会复原；人工否决持久保留，纠正误合并
  的事件链可追溯。

### 来源记录与差异追查（`diffing.py`）

* 每条材料永久保存原始正文 `raw_text`、来源单位、修订号、观测时间；
  归并只改展示分组，不覆盖任何原文。
* `lineage` 查看归并组内各校版本谱系与决策日志；`diffs` / `compare`
  提供 token 级差异与行级 unified diff，可从归并结果逐级追查回原文。

### 引用与批注（`links.py`）

* 课程引用与教研批注始终绑定**具体材料记录**，从不绑定归并组。
* 同一材料被不同课程引用时，引用各自独立，不会因材料合并而合并。
* 纠正误合并只调整归并组、不删除记录，已建立的引用与批注自动回到正确
  材料，无需迁移。

### 检索视图（`search.py`）

`merged` 组折叠为一个条目（来源可展开），`candidate` 组并列展示并附
判定依据，`rejected` / `dissolved` 不参与折叠。支持关键词与来源单位过滤。

## 环境

需要 Python 3.11 或更新版本。应用运行不依赖第三方包；运行测试和构建前，
安装固定版本的开发工具：`python -m pip install -r requirements-dev.txt`。

## 运行

```bash
# 导入演练记录，自动完成身份判断与归并
PYTHONPATH=src python -m nlp_reading_corpus_deduper.cli ingest fixtures/records.json

# 去重检索：合并项折叠、候选并列
PYTHONPATH=src python -m nlp_reading_corpus_deduper.cli search
PYTHONPATH=src python -m nlp_reading_corpus_deduper.cli search 五年级

# 查看归并组与单条材料的原文/引用/批注
PYTHONPATH=src python -m nlp_reading_corpus_deduper.cli groups
PYTHONPATH=src python -m nlp_reading_corpus_deduper.cli detail reading-corpus-deduper-lesson-a

# 追查来源谱系与版本差异
PYTHONPATH=src python -m nlp_reading_corpus_deduper.cli lineage group-0001
PYTHONPATH=src python -m nlp_reading_corpus_deduper.cli diffs group-0002
PYTHONPATH=src python -m nlp_reading_corpus_deduper.cli compare reading-corpus-deduper-lesson-a lesson-a-north-1

# 人工裁决
PYTHONPATH=src python -m nlp_reading_corpus_deduper.cli confirm group-0001 --actor 王老师
PYTHONPATH=src python -m nlp_reading_corpus_deduper.cli reject group-0001 --actor 李老师
PYTHONPATH=src python -m nlp_reading_corpus_deduper.cli merge r1 r4 --actor 王老师

# 纠正误合并：把材料从归并组拆出（阻断对自动写入，重新导入不复原）
PYTHONPATH=src python -m nlp_reading_corpus_deduper.cli split group-0001 --detach lesson-a-west-5 --actor 李老师

# 登记课程引用与教研批注（绑定具体记录）
PYTHONPATH=src python -m nlp_reading_corpus_deduper.cli add-reference fixtures/references.json
PYTHONPATH=src python -m nlp_reading_corpus_deduper.cli add-annotation fixtures/annotations.json
```

运行数据保存在 `var/records.json`（带版本号的完整状态，旧版状态文件可
自动升级）。

## 测试

执行 `python -m pytest -q` 检查文本规范化、身份判定、归并状态机、引用与
批注回正、来源追溯、差异计算、人工裁决及本地持久化。
