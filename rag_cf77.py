from __future__ import annotations

import re
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Optional, Tuple

import fitz  # PyMuPDF
from rank_bm25 import BM25Okapi

log = logging.getLogger("ANACLETO")


def _tokenize(text: str) -> List[str]:
    text = text.lower()
    text = re.sub(r"[^\wàèéìòù]+", " ", text, flags=re.UNICODE)
    return [t for t in text.split() if len(t) > 1]


@dataclass
class Chunk:
    book: str
    page: int
    text: str
    tokens: List[str]


class CF77Rag:
    """
    RAG super semplice:
    - carica PDF
    - spezza per pagina
    - indicizza con BM25
    - restituisce top-k estratti con citazione (file + pagina)
    """

    def __init__(self, pdf_dir: Path):
        self.pdf_dir = pdf_dir
        self.chunks: List[Chunk] = []
        self.bm25: Optional[BM25Okapi] = None
        self._corpus_tokens: List[List[str]] = []

    def _load_pdf_pages(self, pdf_path: Path) -> int:
        pages_count = 0
        doc = fitz.open(str(pdf_path))
        try:
            for i, page in enumerate(doc, start=1):
                txt = (page.get_text("text") or "").strip()
                if not txt:
                    continue
                tokens = _tokenize(txt)
                if not tokens:
                    continue
                self.chunks.append(
                    Chunk(
                        book=pdf_path.name,
                        page=i,
                        text=txt,
                        tokens=tokens,
                    )
                )
                pages_count += 1
        finally:
            doc.close()
        return pages_count

    def build(self) -> Tuple[int, int]:
        if not self.pdf_dir.exists():
            log.warning("CF77 PDF dir non esiste: %s", self.pdf_dir)
            return (0, 0)

        pdfs = sorted([p for p in self.pdf_dir.glob("*.pdf") if p.is_file()])
        log.info("CF77 PDF scan: dir=%s | trovati=%d", self.pdf_dir, len(pdfs))

        books = 0
        pages = 0
        for pdf in pdfs:
            try:
                p = self._load_pdf_pages(pdf)
                if p > 0:
                    books += 1
                    pages += p
            except Exception as e:
                log.exception("CF77: errore leggendo %s: %s", pdf.name, e)

        self._corpus_tokens = [c.tokens for c in self.chunks]
        if self._corpus_tokens:
            self.bm25 = BM25Okapi(self._corpus_tokens)
        else:
            self.bm25 = None

        return (books, pages)

    def query(self, question: str, top_k: int = 5) -> List[Chunk]:
        if not self.bm25 or not self.chunks:
            return []

        q_tokens = _tokenize(question)
        if not q_tokens:
            return []

        scores = self.bm25.get_scores(q_tokens)
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        out: List[Chunk] = []
        for idx in ranked[: max(1, top_k)]:
            out.append(self.chunks[idx])
        return out