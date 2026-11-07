"""参考图库（人像 / 姿势 / 服装 / 手绘风格）的字段映射与内容边界。

上游仓库的 JSON 使用中文键，且三套库的字段并不一致；这里把差异收敛到
`ReferenceSpec`，source / provider / 管理接口只处理统一后的记录。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ReferenceSpec:
    kind: str
    route: str
    name: str
    description: str
    data_path: str
    meta_path: str | None
    title_field: str
    group_field: str
    category_field: str | None
    detail_field: str | None
    tag_field: str | None
    keyword_field: str | None
    image_field: str
    thumb_field: str
    curated_field: str | None
    hot_field: str | None
    adult_groups: frozenset[str]
    adult_categories: frozenset[str]
    group_label: str
    category_label: str
    detail_label: str
    tag_label: str
    icon: str
    prompt_prefix: str
    extra_prompt_fields: tuple[str, ...] = field(default_factory=tuple)
    source_base: str = ""
    image_base: str = ""
    source_label: str = ""
    source_attribution: str = ""


PORTRAIT = ReferenceSpec(
    kind="portrait",
    route="portraits",
    name="人像参考库",
    description="按分区 / 发长 / 发型 / 关键词检索人像参考图，返回图片直链与中文提示词，可直接用于图片生成",
    data_path="data/portrait-zh.json",
    meta_path="data/portrait-meta-zh.json",
    title_field="标题",
    group_field="分区",
    category_field="发长",
    detail_field="发型名称",
    tag_field=None,
    keyword_field=None,
    image_field="原图",
    thumb_field="缩略图",
    curated_field="精选",
    hot_field="热门",
    adult_groups=frozenset(),
    adult_categories=frozenset(),
    group_label="分区",
    category_label="发长",
    detail_label="发型",
    tag_label="标签",
    icon="image",
    prompt_prefix="人物参考",
)

POSE = ReferenceSpec(
    kind="pose",
    route="poses",
    name="姿势参考库",
    description="按体系 / 分类 / 视角 / 关键词检索姿势参考图，返回图片直链与中文提示词，可直接用于图片生成",
    data_path="data/pose-zh.json",
    meta_path=None,
    title_field="姿势名称",
    group_field="体系",
    category_field="分类",
    detail_field="角度",
    tag_field=None,
    keyword_field=None,
    image_field="原图",
    thumb_field="缩略图",
    curated_field=None,
    hot_field=None,
    adult_groups=frozenset({"全身单人・性感系", "双人缠绵"}),
    adult_categories=frozenset({"性感・大胆系", "挤胸・强调事业线系"}),
    group_label="体系",
    category_label="分类",
    detail_label="视角",
    tag_label="标签",
    icon="user",
    prompt_prefix="姿势参考",
    extra_prompt_fields=("姿势编号",),
)

HANDDRAW = ReferenceSpec(
    kind="handdraw",
    route="handdraws",
    name="手绘风格库",
    description="按风格家族 / 编号 / 关键词检索手绘风格图例，返回示例图直链与核心特征提示词，可直接用于图片生成",
    data_path="skills/handdraw-style-prompter/references/styles.json",
    meta_path=None,
    title_field="generation_name",
    group_field="family",
    category_field="reference",
    detail_field="number",
    tag_field="tags",
    keyword_field=None,
    image_field="image",
    thumb_field="thumb",
    curated_field=None,
    hot_field=None,
    adult_groups=frozenset(),
    adult_categories=frozenset(),
    group_label="风格家族",
    category_label="参考作者",
    detail_label="风格编号",
    tag_label="标签",
    icon="image",
    prompt_prefix="手绘风格",
    extra_prompt_fields=("核心特征",),
    source_base="https://raw.githubusercontent.com/philipxiaoxi/handraw-style/master",
    image_base="https://raw.githubusercontent.com/philipxiaoxi/handraw-style/master/images",
    source_label="philipxiaoxi/handraw-style",
    source_attribution="手绘风格库来自 philipxiaoxi/handraw-style（原作者 yang0），MIT with Attribution。",
)

COSTUME = ReferenceSpec(
    kind="costume",
    route="costumes",
    name="服装参考库",
    description="按穿搭分类 / 标签 / 关键词检索服装参考图，返回图片直链、中文描述与英文 Prompt",
    data_path="data/costume-zh.json",
    meta_path="data/costume-meta-zh.json",
    title_field="服装名称",
    group_field="分类",
    category_field=None,
    detail_field=None,
    tag_field="标签",
    keyword_field="关键词",
    image_field="原图",
    thumb_field="缩略图",
    curated_field=None,
    hot_field=None,
    adult_groups=frozenset({"内衣・睡衣", "性感穿搭", "泳装・度假"}),
    adult_categories=frozenset(),
    group_label="分类",
    category_label="分类",
    detail_label="细节",
    tag_label="标签",
    icon="shirt",
    prompt_prefix="服装参考",
)

SPECS: dict[str, ReferenceSpec] = {
    PORTRAIT.kind: PORTRAIT,
    POSE.kind: POSE,
    COSTUME.kind: COSTUME,
    HANDDRAW.kind: HANDDRAW,
}

KINDS = tuple(SPECS)


def get_spec(kind: str) -> ReferenceSpec | None:
    return SPECS.get(kind)
