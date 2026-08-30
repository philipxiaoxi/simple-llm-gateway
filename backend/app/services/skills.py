from __future__ import annotations

import io
import json
import re
import shutil
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.config import get_settings
from app.models import Skill, SkillCategory, SkillClassificationSettings, UpstreamAccount
from app.services.credentials import require_upstream_credential
from app.services.bridge import call_chat
from app.services.model_caps import first_model_id

DEFAULT_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "UI设计": ("ui", "ux", "figma", "design system", "界面", "设计"),
    "编程开发": ("code", "coding", "git", "debug", "refactor", "编程", "开发", "测试"),
    "办公效率": ("excel", "ppt", "word", "office", "文档", "办公", "会议"),
    "内容创作": ("write", "blog", "copy", "写作", "文案", "创作"),
    "数据分析": ("sql", "pandas", "chart", "分析", "数据", "统计"),
    "研究学习": ("research", "paper", "study", "研究", "学习", "论文"),
    "自动化": ("automat", "workflow", "cron", "自动化", "工作流"),
    "安全": ("security", "auth", "owasp", "安全", "漏洞"),
    "记忆与上下文": ("memory", "context", "rag", "记忆", "上下文"),
    "Agent工具与平台": ("agent", "mcp", "orchestr", "平台", "工具"),
    "产品与商业": ("product", "business", "saas", "产品", "商业"),
    "技能开发": ("skill.md", "authoring", "技能开发"),
    "技能合集": ("collection", "awesome", "合集", "bundle"),
    "其他": (),
}
SKILL_CATEGORIES = tuple(DEFAULT_CATEGORY_KEYWORDS)
FALLBACK_CATEGORY = "其他"
RESERVED_CATEGORY_NAMES = {"全部", "自动识别"}

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_EXTRACTED_BYTES = 40 * 1024 * 1024
MAX_FILE_COUNT = 400
MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_SKILL_MD_BYTES = 256 * 1024
MAX_ANALYSIS_TEXT_FILE_CHARS = 2000

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)
_TEXT_SUFFIXES = {
    ".md",
    ".txt",
    ".json",
    ".yml",
    ".yaml",
    ".toml",
    ".xml",
    ".csv",
    ".tsv",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".sh",
    ".bash",
    ".zsh",
    ".ps1",
    ".html",
    ".css",
    ".svg",
    ".ini",
    ".cfg",
    ".conf",
    ".env.example",
}
_SKIP_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    ".DS_Store",
}
_SKIP_FILE_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}
_ARCHIVE_SUFFIXES = {".zip", ".tar", ".tgz", ".gz"}


class SkillError(ValueError):
    pass


@dataclass
class UploadedFile:
    path: str
    data: bytes


@dataclass
class ParsedSkill:
    slug: str
    name: str
    description: str
    category: str
    platforms: list[str]
    license: str | None
    version: str | None
    author: str | None
    skill_md: str
    files: dict[str, bytes]
    source_name: str | None = None


@dataclass
class ImportResult:
    items: list[Skill] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)


def skills_root() -> Path:
    return get_settings().resolved_skills_path


def skill_dir(skill: Skill) -> Path:
    return skills_root() / skill.storage_dir


def parse_platforms(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]


def parse_keywords(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    seen: set[str] = set()
    keywords: list[str] = []
    for item in parsed:
        text = str(item).strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        keywords.append(text[:64])
    return keywords[:40]


def dump_keywords(values: Iterable[str] | None) -> str | None:
    keywords = parse_keywords(json.dumps(list(values or []), ensure_ascii=False))
    return json.dumps(keywords, ensure_ascii=False) if keywords else None


def category_to_dict(item: SkillCategory, count: int = 0) -> dict:
    return {
        "id": item.id,
        "name": item.name,
        "sort_order": item.sort_order,
        "keywords": parse_keywords(item.keywords_json),
        "is_protected": item.is_protected,
        "count": count,
        "created_at": item.created_at,
    }


def list_managed_categories(db: Session) -> list[SkillCategory]:
    ensure_skill_categories(db)
    return list(db.scalars(select(SkillCategory).order_by(SkillCategory.sort_order.asc(), SkillCategory.id.asc())).all())


def list_category_names(db: Session) -> list[str]:
    return [item.name for item in list_managed_categories(db)]


def list_category_rules(db: Session) -> list[tuple[str, tuple[str, ...]]]:
    rules: list[tuple[str, tuple[str, ...]]] = []
    for item in list_managed_categories(db):
        keywords = parse_keywords(item.keywords_json)
        if keywords:
            rules.append((item.name, tuple(keywords)))
    return rules


def ensure_skill_categories(db: Session) -> None:
    existing = {item.name: item for item in db.scalars(select(SkillCategory)).all()}
    if not existing:
        now = utcnow()
        for index, (name, keywords) in enumerate(DEFAULT_CATEGORY_KEYWORDS.items()):
            db.add(
                SkillCategory(
                    name=name,
                    sort_order=index,
                    keywords_json=dump_keywords(keywords),
                    is_protected=name == FALLBACK_CATEGORY,
                    created_at=now,
                )
            )
        db.flush()
        existing = {item.name: item for item in db.scalars(select(SkillCategory)).all()}
    if FALLBACK_CATEGORY not in existing:
        max_order = max((item.sort_order for item in existing.values()), default=-1)
        db.add(
            SkillCategory(
                name=FALLBACK_CATEGORY,
                sort_order=max_order + 1,
                keywords_json=None,
                is_protected=True,
                created_at=utcnow(),
            )
        )
        db.flush()
        existing[FALLBACK_CATEGORY] = db.scalar(select(SkillCategory).where(SkillCategory.name == FALLBACK_CATEGORY))
    used_names = {str(name).strip() for name in db.scalars(select(Skill.category).distinct()).all() if str(name).strip()}
    max_order = max((item.sort_order for item in existing.values()), default=-1)
    for name in sorted(used_names):
        if name in existing:
            continue
        max_order += 1
        db.add(
            SkillCategory(
                name=name[:64],
                sort_order=max_order,
                keywords_json=None,
                is_protected=name == FALLBACK_CATEGORY,
                created_at=utcnow(),
            )
        )
        existing[name] = None
    if FALLBACK_CATEGORY in existing and existing[FALLBACK_CATEGORY] is not None:
        existing[FALLBACK_CATEGORY].is_protected = True
    db.flush()


def _clean_category_name(value: str | None) -> str:
    return (value or "").strip()[:64]


def create_skill_category(db: Session, name: str, keywords: Iterable[str] | None = None) -> SkillCategory:
    ensure_skill_categories(db)
    cleaned = _clean_category_name(name)
    if not cleaned:
        raise SkillError("分类名称不能为空")
    if cleaned in RESERVED_CATEGORY_NAMES:
        raise SkillError("该名称被系统保留")
    exists = db.scalar(select(SkillCategory).where(SkillCategory.name == cleaned))
    if exists is not None:
        raise SkillError("分类已存在")
    max_order = db.scalar(select(func.max(SkillCategory.sort_order))) or 0
    item = SkillCategory(
        name=cleaned,
        sort_order=int(max_order) + 1,
        keywords_json=dump_keywords(keywords),
        is_protected=False,
        created_at=utcnow(),
    )
    db.add(item)
    db.flush()
    return item


def update_skill_category(
    db: Session,
    category_id: int,
    *,
    name: str | None = None,
    keywords: Iterable[str] | None = None,
    sort_order: int | None = None,
) -> SkillCategory:
    ensure_skill_categories(db)
    item = db.get(SkillCategory, category_id)
    if item is None:
        raise SkillError("分类不存在")
    if name is not None:
        cleaned = _clean_category_name(name)
        if not cleaned:
            raise SkillError("分类名称不能为空")
        if cleaned in RESERVED_CATEGORY_NAMES:
            raise SkillError("该名称被系统保留")
        if item.is_protected and cleaned != item.name:
            raise SkillError("系统分类不能改名")
        if cleaned != item.name:
            clash = db.scalar(select(SkillCategory).where(SkillCategory.name == cleaned, SkillCategory.id != item.id))
            if clash is not None:
                raise SkillError("分类已存在")
            old_name = item.name
            item.name = cleaned
            db.execute(
                update(Skill)
                .where(Skill.category == old_name)
                .values(category=cleaned, updated_at=utcnow())
            )
    if keywords is not None:
        item.keywords_json = dump_keywords(keywords)
    if sort_order is not None:
        item.sort_order = sort_order
    db.flush()
    return item


def delete_skill_category(db: Session, category_id: int) -> None:
    ensure_skill_categories(db)
    item = db.get(SkillCategory, category_id)
    if item is None:
        raise SkillError("分类不存在")
    if item.is_protected or item.name == FALLBACK_CATEGORY:
        raise SkillError("系统分类不能删除")
    db.execute(
        update(Skill)
        .where(Skill.category == item.name)
        .values(category=FALLBACK_CATEGORY, updated_at=utcnow())
    )
    db.delete(item)
    db.flush()


def normalize_category(value: str | None, names: Iterable[str] | None = None) -> str:
    text = (value or "").strip()
    allowed = set(names) if names is not None else set(SKILL_CATEGORIES)
    return text if text in allowed else FALLBACK_CATEGORY


def guess_category(
    name: str,
    description: str,
    skill_md: str,
    rules: Iterable[tuple[str, Iterable[str]]] | None = None,
) -> str:
    blob = f"{name}\n{description}\n{skill_md}".lower()
    chosen_rules = (
        list(rules)
        if rules is not None
        else [(category, keywords) for category, keywords in DEFAULT_CATEGORY_KEYWORDS.items() if keywords]
    )
    for category, keywords in chosen_rules:
        if any(str(keyword).lower() in blob for keyword in keywords if str(keyword).strip()):
            return category
    return FALLBACK_CATEGORY


def get_classification_settings(db: Session) -> SkillClassificationSettings:
    item = db.scalar(select(SkillClassificationSettings).order_by(SkillClassificationSettings.id).limit(1))
    if item is None:
        item = SkillClassificationSettings(enabled=False)
        db.add(item)
        db.flush()
    return item


async def classify_with_gateway(
    db: Session,
    parsed_skills: list[ParsedSkill],
    category_names: Iterable[str],
    category_rules: Iterable[tuple[str, Iterable[str]]] | None = None,
) -> None:
    settings = get_classification_settings(db)
    if not settings.enabled or not settings.account_id or not parsed_skills:
        return
    account = db.get(UpstreamAccount, settings.account_id)
    if account is None:
        return
    model = (settings.model or "").strip()
    if not model:
        model = first_model_id(account.models_json)
    if not model:
        return
    try:
        credential = require_upstream_credential(account)
        categories = [str(item) for item in category_names]
        payload = [
            {"index": index, "name": item.name, "description": item.description, "content": item.skill_md[:4000]}
            for index, item in enumerate(parsed_skills)
        ]
        messages = [
            {
                "role": "system",
                "content": (
                    "你是 Agent Skill 分类器。只能从给定分类中选择一个分类。"
                    "必须只输出 JSON 数组，每项格式为 {\"index\":数字,\"category\":\"分类名\"}，不要输出 Markdown。"
                ),
            },
            {"role": "user", "content": json.dumps({"categories": categories, "skills": payload}, ensure_ascii=False)},
        ]
        result = await call_chat(account, messages, model, False, {}, credential)
        content = result.choices[0].message.content if hasattr(result, "choices") else result.get("choices", [{}])[0].get("message", {}).get("content", "")
        if isinstance(content, list):
            content = "".join(str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in content)
        match = re.search(r"\[[\s\S]*\]", str(content))
        if not match:
            return
        decisions = json.loads(match.group(0))
        allowed = set(categories)
        for decision in decisions if isinstance(decisions, list) else []:
            if not isinstance(decision, dict):
                continue
            index = decision.get("index")
            category = str(decision.get("category") or "").strip()
            if isinstance(index, int) and 0 <= index < len(parsed_skills) and category in allowed:
                parsed_skills[index].category = category
    except Exception:
        return


async def analyze_skill_with_gateway(db: Session, skill: Skill) -> dict | None:
    settings = get_classification_settings(db)
    if not settings.report_enabled or not settings.report_account_id:
        return None
    account = db.get(UpstreamAccount, settings.report_account_id)
    if account is None:
        return None
    model = (settings.report_model or "").strip()
    if not model and account.models_json:
        model = first_model_id(account.models_json)
    if not model:
        return None
    categories = list_category_names(db)
    prompt = {
        "name": skill.name,
        "description": skill.description,
        "category": skill.category,
        "platforms": parse_platforms(skill.platforms_json),
        "license": skill.license,
        "version": skill.version,
        "author": skill.author,
        "files": list_skill_files(skill),
        **_build_skill_analysis_material(skill),
    }
    messages = [
        {
            "role": "system",
            "content": (
                "你是 Agent Skill 安全与可用性评估专家。请基于给定 Skill 信息生成客观报告。"
                "只输出 JSON 对象，不要 Markdown。数组字段每项是简洁中文句子；不要臆测未提供的能力，"
                "不确定时明确写‘未在材料中发现’。字段必须包含：summary、use_cases、capabilities、"
                "inputs_outputs、trigger_and_workflow、dependencies、permissions_and_risks、limitations、"
                "setup_suggestions、example_tasks、recommendation、fit_score。fit_score 为 0 到 100 的整数。"
            ),
        },
        {"role": "user", "content": json.dumps({"categories": categories, "skill": prompt}, ensure_ascii=False)},
    ]
    try:
        credential = require_upstream_credential(account)
        result = await call_chat(account, messages, model, False, {}, credential)
        content = result.choices[0].message.content if hasattr(result, "choices") else result.get("choices", [{}])[0].get("message", {}).get("content", "")
        if isinstance(content, list):
            content = "".join(str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in content)
        match = re.search(r"\{[\s\S]*\}", str(content))
        if not match:
            return None
        report = json.loads(match.group(0))
        if not isinstance(report, dict):
            return None
        return report
    except Exception:
        return None


def skill_to_dict(skill: Skill) -> dict:
    return {
        "id": skill.id,
        "slug": skill.slug,
        "name": skill.name,
        "description": skill.description,
        "category": skill.category,
        "platforms": parse_platforms(skill.platforms_json),
        "license": skill.license,
        "version": skill.version,
        "author": skill.author,
        "source_name": skill.source_name,
        "file_count": skill.file_count,
        "size_bytes": skill.size_bytes,
        "created_at": skill.created_at,
        "updated_at": skill.updated_at,
    }


def list_skill_files(skill: Skill) -> list[dict]:
    root = skill_dir(skill)
    if not root.exists():
        return []
    files: list[dict] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        files.append(
            {
                "path": relative,
                "size": path.stat().st_size,
                "is_text": _is_text_name(relative),
            }
        )
    return files


def _build_skill_analysis_material(skill: Skill) -> dict:
    root = skill_dir(skill)
    if not root.exists():
        return {"directory_structure": [], "text_files": []}

    directory_structure: list[str] = []
    text_files: list[dict[str, str]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            directory_structure.append(f"{relative}/")
            continue
        if not path.is_file():
            continue
        directory_structure.append(relative)
        data = path.read_bytes()
        if _is_text_data(data):
            text_files.append(
                {
                    "path": relative,
                    "content": _decode_text(data)[:MAX_ANALYSIS_TEXT_FILE_CHARS],
                }
            )
    return {"directory_structure": directory_structure, "text_files": text_files}


def read_skill_file(skill: Skill, relative_path: str) -> tuple[bytes, str]:
    safe = _safe_relative_path(relative_path)
    target = (skill_dir(skill) / safe).resolve()
    root = skill_dir(skill).resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise SkillError("文件不存在") from error
    if not target.is_file():
        raise SkillError("文件不存在")
    return target.read_bytes(), target.name


def build_skill_zip(skill: Skill) -> bytes:
    root = skill_dir(skill)
    if not root.exists():
        raise SkillError("技能文件已丢失")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                archive.write(path, arcname=f"{skill.slug}/{path.relative_to(root).as_posix()}")
    return buffer.getvalue()


def import_uploaded_files(
    files: Iterable[UploadedFile],
    *,
    category: str | None = None,
    source_name: str | None = None,
    existing_slugs: set[str] | None = None,
    category_names: Iterable[str] | None = None,
    category_rules: Iterable[tuple[str, Iterable[str]]] | None = None,
) -> tuple[list[ParsedSkill], list[tuple[str, str]]]:
    collected = list(files)
    if not collected:
        raise SkillError("没有可导入的文件")
    _assert_upload_budget(collected)
    if _looks_like_single_archive(collected):
        extracted = _extract_archive(collected[0])
        label = collected[0].path
        return _parse_file_map(
            extracted,
            category=category,
            source_name=source_name or label,
            existing_slugs=existing_slugs,
            category_names=category_names,
            category_rules=category_rules,
        )
    return _parse_file_map(
        {item.path: item.data for item in collected},
        category=category,
        source_name=source_name,
        existing_slugs=existing_slugs,
        category_names=category_names,
        category_rules=category_rules,
    )


def persist_parsed_skills(db: Session, parsed_skills: list[ParsedSkill]) -> list[Skill]:
    created: list[Skill] = []
    for parsed in parsed_skills:
        storage = f"{parsed.slug}-{uuid4().hex[:10]}"
        target = skills_root() / storage
        target.mkdir(parents=True, exist_ok=False)
        try:
            for relative, data in parsed.files.items():
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(data)
            now = utcnow()
            item = Skill(
                slug=parsed.slug,
                name=parsed.name,
                description=parsed.description,
                category=parsed.category,
                platforms_json=json.dumps(parsed.platforms, ensure_ascii=False) if parsed.platforms else None,
                license=parsed.license,
                version=parsed.version,
                author=parsed.author,
                source_name=parsed.source_name,
                storage_dir=storage,
                file_count=len(parsed.files),
                size_bytes=sum(len(data) for data in parsed.files.values()),
                skill_md=parsed.skill_md,
                created_at=now,
                updated_at=now,
            )
            db.add(item)
            db.flush()
            created.append(item)
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            raise
    return created


def replace_skill_with_parsed(db: Session, skill: Skill, parsed: ParsedSkill) -> Skill:
    old_root = skill_dir(skill)
    storage = f"{parsed.slug}-{uuid4().hex[:10]}"
    target = skills_root() / storage
    target.mkdir(parents=True, exist_ok=False)
    try:
        for relative, data in parsed.files.items():
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        now = utcnow()
        skill.slug = parsed.slug
        skill.name = parsed.name
        skill.description = parsed.description
        skill.category = parsed.category
        skill.platforms_json = json.dumps(parsed.platforms, ensure_ascii=False) if parsed.platforms else None
        skill.license = parsed.license
        skill.version = parsed.version
        skill.author = parsed.author
        skill.source_name = parsed.source_name
        skill.storage_dir = storage
        skill.file_count = len(parsed.files)
        skill.size_bytes = sum(len(data) for data in parsed.files.values())
        skill.skill_md = parsed.skill_md
        skill.analysis_json = None
        skill.analysis_generated_at = None
        skill.updated_at = now
        db.flush()
        if old_root != target:
            shutil.rmtree(old_root, ignore_errors=True)
        return skill
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise


def delete_skill_files(skill: Skill) -> None:
    root = skill_dir(skill)
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)


def _parse_file_map(
    files: dict[str, bytes],
    *,
    category: str | None,
    source_name: str | None,
    existing_slugs: set[str] | None,
    category_names: Iterable[str] | None = None,
    category_rules: Iterable[tuple[str, Iterable[str]]] | None = None,
) -> tuple[list[ParsedSkill], list[tuple[str, str]]]:
    cleaned = _normalize_file_map(files)
    if not cleaned:
        raise SkillError("压缩包或目录里没有可用文件")
    groups = _group_skill_files(cleaned)
    if not groups:
        raise SkillError("未找到 SKILL.md，请上传符合 Agent Skills 规范的目录或压缩包")
    used = set(existing_slugs or ())
    parsed_skills: list[ParsedSkill] = []
    skipped: list[tuple[str, str]] = []
    for root, group_files in groups:
        skill_md_path = _find_skill_md(group_files)
        if skill_md_path is None:
            skipped.append((root or "unknown", "缺少 SKILL.md"))
            continue
        try:
            parsed = _parse_one_skill(
                group_files,
                skill_md_path,
                category=category,
                source_name=source_name,
                category_names=category_names,
                category_rules=category_rules,
            )
        except SkillError as error:
            skipped.append((root or skill_md_path, str(error)))
            continue
        parsed.slug = _unique_slug(parsed.slug, used)
        used.add(parsed.slug)
        parsed_skills.append(parsed)
    if not parsed_skills and not skipped:
        raise SkillError("未解析到可用 Skill")
    return parsed_skills, skipped


def _parse_one_skill(
    files: dict[str, bytes],
    skill_md_path: str,
    *,
    category: str | None,
    source_name: str | None,
    category_names: Iterable[str] | None = None,
    category_rules: Iterable[tuple[str, Iterable[str]]] | None = None,
) -> ParsedSkill:
    raw = files[skill_md_path]
    if len(raw) > MAX_SKILL_MD_BYTES:
        raise SkillError("SKILL.md 过大")
    text = _decode_text(raw)
    meta, body = _split_frontmatter(text)
    name = str(meta.get("name") or Path(skill_md_path).parent.name or "untitled-skill").strip()
    if not name:
        name = "untitled-skill"
    description = str(meta.get("description") or "").strip()
    if not description:
        description = _first_paragraph(body) or name
    slug = _slugify(str(meta.get("slug") or name))
    platforms = _as_string_list(meta.get("compatibility") or meta.get("platforms") or meta.get("allowed-tools"))
    license_value = _optional_str(meta.get("license"))
    nested_meta = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}
    version = _optional_str(meta.get("version") or nested_meta.get("version"))
    author = _optional_str(meta.get("author") or nested_meta.get("author"))
    chosen_category = (
        normalize_category(category, category_names)
        if category and category != "自动识别"
        else guess_category(name, description, body, category_rules)
    )
    prefix = str(Path(skill_md_path).parent.as_posix())
    stored: dict[str, bytes] = {}
    for path, data in files.items():
        relative = path if prefix in {"", "."} else path[len(prefix) :].lstrip("/")
        if not relative:
            continue
        stored[_safe_relative_path(relative)] = data
    if "SKILL.md" not in stored:
        stored["SKILL.md"] = raw
    return ParsedSkill(
        slug=slug,
        name=name[:128],
        description=description[:2000],
        category=chosen_category,
        platforms=platforms[:12],
        license=license_value[:64] if license_value else None,
        version=version[:32] if version else None,
        author=author[:128] if author else None,
        skill_md=text[:MAX_SKILL_MD_BYTES],
        files=stored,
        source_name=(source_name or name)[:256],
    )


def _group_skill_files(files: dict[str, bytes]) -> list[tuple[str, dict[str, bytes]]]:
    skill_mds = [path for path in files if Path(path).name.lower() == "skill.md"]
    if not skill_mds:
        return []
    groups: list[tuple[str, dict[str, bytes]]] = []
    claimed: set[str] = set()
    for skill_md in sorted(skill_mds, key=lambda item: (-item.count("/"), item)):
        root = str(Path(skill_md).parent.as_posix())
        grouped: dict[str, bytes] = {}
        for path, data in files.items():
            if path in claimed:
                continue
            if root in {"", "."}:
                if "/" not in path:
                    grouped[path] = data
                continue
            if path == root or path.startswith(f"{root}/"):
                grouped[path] = data
        claimed.update(grouped)
        groups.append((root, grouped))
    return groups


def _find_skill_md(files: dict[str, bytes]) -> str | None:
    matches = [path for path in files if Path(path).name.lower() == "skill.md"]
    if not matches:
        return None
    matches.sort(key=lambda item: (item.count("/"), item))
    return matches[0]


def _looks_like_single_archive(files: list[UploadedFile]) -> bool:
    if len(files) != 1:
        return False
    name = files[0].path.lower()
    return name.endswith(".zip") or name.endswith(".tar") or name.endswith(".tar.gz") or name.endswith(".tgz")


def _extract_archive(item: UploadedFile) -> dict[str, bytes]:
    name = item.path.lower()
    if name.endswith(".zip"):
        return _extract_zip(item.data)
    if name.endswith(".tar") or name.endswith(".tar.gz") or name.endswith(".tgz"):
        return _extract_tar(item.data)
    raise SkillError("仅支持 zip / tar / tar.gz 压缩包")


def _extract_zip(data: bytes) -> dict[str, bytes]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as error:
        raise SkillError("无法解析 zip 压缩包") from error
    extracted: dict[str, bytes] = {}
    total = 0
    with archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        if len(infos) > MAX_FILE_COUNT:
            raise SkillError("压缩包内文件过多")
        for info in infos:
            relative = _safe_relative_path(info.filename)
            if _should_skip(relative):
                continue
            if info.file_size > MAX_FILE_BYTES:
                raise SkillError(f"{relative} 单个文件过大")
            payload = archive.read(info)
            total += len(payload)
            if total > MAX_EXTRACTED_BYTES:
                raise SkillError("压缩包解压后体积过大")
            extracted[relative] = payload
    return extracted


def _extract_tar(data: bytes) -> dict[str, bytes]:
    try:
        archive = tarfile.open(fileobj=io.BytesIO(data), mode="r:*")
    except tarfile.TarError as error:
        raise SkillError("无法解析 tar 压缩包") from error
    extracted: dict[str, bytes] = {}
    total = 0
    with archive:
        members = [member for member in archive.getmembers() if member.isfile()]
        if len(members) > MAX_FILE_COUNT:
            raise SkillError("压缩包内文件过多")
        for member in members:
            relative = _safe_relative_path(member.name)
            if _should_skip(relative):
                continue
            if member.size > MAX_FILE_BYTES:
                raise SkillError(f"{relative} 单个文件过大")
            handle = archive.extractfile(member)
            if handle is None:
                continue
            payload = handle.read()
            total += len(payload)
            if total > MAX_EXTRACTED_BYTES:
                raise SkillError("压缩包解压后体积过大")
            extracted[relative] = payload
    return extracted


def _normalize_file_map(files: dict[str, bytes]) -> dict[str, bytes]:
    cleaned: dict[str, bytes] = {}
    total = 0
    for raw_path, data in files.items():
        relative = _safe_relative_path(raw_path)
        if _should_skip(relative):
            continue
        if len(data) > MAX_FILE_BYTES:
            raise SkillError(f"{relative} 单个文件过大")
        total += len(data)
        if total > MAX_EXTRACTED_BYTES:
            raise SkillError("上传文件总体积过大")
        cleaned[relative] = data
    if len(cleaned) > MAX_FILE_COUNT:
        raise SkillError("文件数量过多")
    return cleaned


def _assert_upload_budget(files: list[UploadedFile]) -> None:
    total = sum(len(item.data) for item in files)
    if total > MAX_UPLOAD_BYTES:
        raise SkillError("上传体积超过 20MB")
    if len(files) > MAX_FILE_COUNT:
        raise SkillError("一次最多上传 400 个文件")


def _safe_relative_path(value: str) -> str:
    raw = (value or "").replace("\\", "/").strip()
    if not raw or raw.endswith("/"):
        raise SkillError("非法文件路径")
    parts: list[str] = []
    for part in raw.split("/"):
        if part in {"", "."}:
            continue
        if part == ".." or part.startswith("/") or ":" in part:
            raise SkillError("非法文件路径")
        parts.append(part)
    if not parts:
        raise SkillError("非法文件路径")
    return "/".join(parts)


def _should_skip(path: str) -> bool:
    parts = path.split("/")
    if any(part in _SKIP_DIR_NAMES for part in parts[:-1]):
        return True
    name = parts[-1]
    if name.lower() in _SKIP_FILE_NAMES:
        return True
    return name.startswith("._")


def _is_text_name(path: str) -> bool:
    lowered = path.lower()
    if Path(lowered).name.lower() == "skill.md":
        return True
    return Path(lowered).suffix in _TEXT_SUFFIXES


def _is_text_data(data: bytes) -> bool:
    if b"\x00" in data:
        return False
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        control_count = sum(ord(char) < 32 and char not in "\t\n\r\f" for char in text)
        return control_count <= max(1, len(text) // 100)
    return False


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _split_frontmatter(text: str) -> tuple[dict, str]:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text.strip()
    return _parse_simple_yaml(match.group(1)), text[match.end() :].strip()


def _parse_simple_yaml(raw: str) -> dict:
    result: dict = {}
    current_key: str | None = None
    current_list: list[str] | None = None
    for line in raw.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        if current_list is not None and (line.startswith("  - ") or line.startswith("- ")):
            current_list.append(line.split("-", 1)[1].strip().strip("'\""))
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if value == "":
            current_key = key
            current_list = []
            result[key] = current_list
            continue
        current_list = None
        current_key = None
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            result[key] = [item.strip().strip("'\"") for item in inner.split(",") if item.strip()]
            continue
        result[key] = value
        _ = current_key
    return result


def _as_string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    if "," in text:
        return [item.strip() for item in text.split(",") if item.strip()]
    return [text]


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first_paragraph(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:400]
    return ""


def _slugify(value: str) -> str:
    slug = _SLUG_RE.sub("-", value.strip().lower()).strip("-")
    return (slug or "skill")[:80]


def _unique_slug(slug: str, used: set[str]) -> str:
    candidate = slug or "skill"
    index = 2
    while candidate in used:
        suffix = f"-{index}"
        candidate = f"{slug[: 80 - len(suffix)]}{suffix}"
        index += 1
    return candidate
