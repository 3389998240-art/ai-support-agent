from __future__ import annotations

import re
from pathlib import Path

from app.rag.types import Chunk

HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;\n])")


def parse_front_matter(raw: str) -> tuple[dict[str, str], str]:
    """解析极简 front matter（title / tags），避免引入 yaml 依赖。"""
    if not raw.startswith("---"):
        return {}, raw

    lines = raw.splitlines()
    if lines[0].strip() != "---":
        return {}, raw

    meta: dict[str, str] = {}
    end = None
    for index in range(1, len(lines)):
        line = lines[index].strip()
        if line == "---":
            end = index
            break
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip().lower()] = value.strip()

    if end is None:
        return {}, raw
    return meta, "\n".join(lines[end + 1 :])


def _split_long_paragraph(paragraph: str, limit: int) -> list[str]:
    """超长段落按句子切，避免单块过大导致检索噪声。"""
    if len(paragraph) <= limit:
        return [paragraph]

    pieces: list[str] = []
    buffer = ""
    for sentence in SENTENCE_SPLIT.split(paragraph):
        if not sentence:
            continue
        if len(buffer) + len(sentence) <= limit:
            buffer += sentence
        else:
            if buffer:
                pieces.append(buffer.strip())
            buffer = sentence
    if buffer.strip():
        pieces.append(buffer.strip())
    return pieces or [paragraph[:limit]]


def _pack(paragraphs: list[str], chunk_size: int, overlap: int) -> list[str]:
    """把段落贪心装箱成 chunk，并做尾部重叠，保证跨块语义不被切断。"""
    chunks: list[str] = []
    buffer = ""

    for paragraph in paragraphs:
        candidate = f"{buffer}\n{paragraph}".strip() if buffer else paragraph
        if len(candidate) <= chunk_size:
            buffer = candidate
            continue

        if buffer:
            chunks.append(buffer)
            tail = buffer[-overlap:] if overlap > 0 else ""
            buffer = f"{tail}\n{paragraph}".strip() if tail else paragraph
        else:
            buffer = paragraph

        # 单个段落仍然超长，先切碎
        while len(buffer) > chunk_size:
            head = buffer[:chunk_size]
            chunks.append(head)
            buffer = buffer[chunk_size - overlap :].strip()

    if buffer.strip():
        chunks.append(buffer.strip())
    return chunks


def chunk_markdown(
    text: str,
    *,
    doc_id: str,
    fallback_title: str,
    source: str,
    tags: tuple[str, ...] = (),
    chunk_size: int = 420,
    overlap: int = 80,
) -> list[Chunk]:
    """按标题层级切分 Markdown，保留"文档 / 章节"两级来源信息。"""
    meta, body = parse_front_matter(text)
    title = meta.get("title") or fallback_title
    if meta.get("tags"):
        tags = tuple(t.strip() for t in re.split(r"[,，]", meta["tags"]) if t.strip())

    sections: list[tuple[str, list[str]]] = []
    section_path: list[str] = []
    paragraphs: list[str] = []
    current_section = ""

    def flush_section() -> None:
        nonlocal paragraphs, current_section
        if paragraphs:
            sections.append((current_section, paragraphs))
        paragraphs = []

    for line in body.splitlines():
        match = HEADING.match(line)
        if not match:
            if line.strip():
                paragraphs.append(line.strip())
            else:
                flush_section()
            continue

        flush_section()
        level = len(match.group(1))
        heading_text = match.group(2).strip()

        if level == 1:
            section_path = []
            title = title or heading_text
            current_section = ""
            continue

        section_path = section_path[: level - 2]
        section_path.append(heading_text)
        current_section = " / ".join(section_path)

    flush_section()

    chunks: list[Chunk] = []
    ordinal = 0
    for section, paras in sections:
        expanded: list[str] = []
        for paragraph in paras:
            expanded.extend(_split_long_paragraph(paragraph, chunk_size))

        for piece in _pack(expanded, chunk_size, overlap):
            prefix = f"【{section}】\n" if section else ""
            chunks.append(
                Chunk(
                    id=f"{doc_id}::{ordinal:03d}",
                    doc_id=doc_id,
                    title=title,
                    section=section,
                    text=f"{prefix}{piece}".strip(),
                    source=source,
                    tags=tags,
                    ordinal=ordinal,
                )
            )
            ordinal += 1

    return chunks


def chunk_file(path: Path, *, chunk_size: int, overlap: int) -> list[Chunk]:
    raw = path.read_text(encoding="utf-8", errors="ignore")
    return chunk_markdown(
        raw,
        doc_id=path.stem,
        fallback_title=path.stem,
        source=path.name,
        chunk_size=chunk_size,
        overlap=overlap,
    )
