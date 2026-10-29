from __future__ import annotations

from dataclasses import dataclass, field


def clean_snippet(text: str) -> str:
    """去掉 Markdown 表格分隔行，避免来源卡片里出现 `| --- | --- |`。"""
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if cells and all(set(cell) <= {"-", ":", " "} for cell in cells):
                continue
        lines.append(line)
    return "\n".join(lines)


@dataclass(frozen=True)
class Chunk:
    """知识库切片的最小单元。"""

    id: str
    doc_id: str
    title: str
    section: str
    text: str
    source: str
    tags: tuple[str, ...] = ()
    ordinal: int = 0

    @property
    def heading(self) -> str:
        if self.section and self.section != self.title:
            return f"{self.title} / {self.section}"
        return self.title

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "doc_id": self.doc_id,
            "title": self.title,
            "section": self.section,
            "text": self.text,
            "source": self.source,
            "tags": list(self.tags),
            "ordinal": self.ordinal,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "Chunk":
        return cls(
            id=payload["id"],
            doc_id=payload["doc_id"],
            title=payload["title"],
            section=payload.get("section", ""),
            text=payload["text"],
            source=payload.get("source", ""),
            tags=tuple(payload.get("tags", ())),
            ordinal=int(payload.get("ordinal", 0)),
        )


@dataclass
class Retrieved:
    """一次检索命中的切片及其打分明细，前端"来源展示"直接用这个结构。"""

    chunk: Chunk
    vector_score: float = 0.0
    keyword_score: float = 0.0
    score: float = 0.0
    rank: int = 0
    extra: dict = field(default_factory=dict)

    @property
    def citation_id(self) -> str:
        return self.chunk.id

    def to_citation(self) -> dict:
        return {
            "id": self.chunk.id,
            "title": self.chunk.title,
            "section": self.chunk.section,
            "source": self.chunk.source,
            "snippet": clean_snippet(self.chunk.text)[:400],
            "score": round(self.score, 4),
            "vector_score": round(self.vector_score, 4),
            "keyword_score": round(self.keyword_score, 4),
            "rank": self.rank,
            "doc_id": self.chunk.doc_id,
        }
