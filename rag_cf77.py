from __future__ import annotations

import os
import re
import json
import time
import hashlib
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional

# PyMuPDF
import fitz  # pip: pymupdf
from rank_bm25 import BM25Okapi  # pip: rank-bm25


# ------------------ utils ------------------
_WORD_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ0-9']+")


def _tokenize(text: str) -> List[str]:
    text = (text or "").lower()
    return _WORD_RE.findall(text)


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_read_json(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _safe_write_json(path: str, obj: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def _excerpt(text: str, max_chars: int = 360) -> str:
    t = re.sub(r"\s+", " ", (text or "")).strip()
    if len(t) <= max_chars:
        return t
    return t[: max_chars - 1].rstrip() + "…"


def _best_sentences(text: str, query_tokens: List[str], max_sentences: int = 2) -> str:
    """
    Estrae 1-2 frasi 'buone' dal testo in modo deterministico (niente LLM).
    Separa per punteggiatura forte.
    """
    if not text:
        return ""
    cleaned = re.sub(r"\s+", " ", text).strip()
    # split frasi
    parts = re.split(r"(?<=[\.\!\?])\s+", cleaned)
    if not parts:
        return _excerpt(cleaned, 300)

    scored: List[Tuple[int, str]] = []
    qset = set(query_tokens)
    for s in parts:
        toks = set(_tokenize(s))
        score = len(qset.intersection(toks))
        if score > 0 and len(s) >= 60:
            scored.append((score, s))

    if not scored:
        # fallback: usa inizio pagina
        return _excerpt(cleaned, 300)

    scored.sort(key=lambda x: x[0], reverse=True)
    chosen = [scored[i][1] for i in range(min(max_sentences, len(scored)))]
    return " ".join(chosen)


# ------------------ data ------------------
@dataclass
class PageChunk:
    book: str          # filename
    page: int          # 1-based
    text: str          # full text
    tokens: List[str]  # tokenized


@dataclass
class RagResult:
    answer: str
    citations: List[Dict[str, str]]  # {book,page,quote}
    confidence: str  # "alta" | "media" | "bassa"


class CFR77RAG:
    def __init__(self, pdf_dir: str, cache_dir: str = "data/.cache"):
        self.pdf_dir = pdf_dir
        self.cache_dir = cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)

        self.chunks: List[PageChunk] = []
        self.bm25: Optional[BM25Okapi] = None

        self._manifest_path = os.path.join(self.cache_dir, "cf77_manifest.json")
        self._chunks_path = os.path.join(self.cache_dir, "cf77_chunks.json")

    def _list_pdfs(self) -> List[str]:
        if not os.path.isdir(self.pdf_dir):
            return []
        items = []
        for name in os.listdir(self.pdf_dir):
            if name.lower().endswith(".pdf"):
                items.append(os.path.join(self.pdf_dir, name))
        items.sort()
        return items

    def _build_manifest(self, pdf_paths: List[str]) -> dict:
        entries = []
        for p in pdf_paths:
            try:
                entries.append({
                    "path": p,
                    "name": os.path.basename(p),
                    "sha256": _sha256_file(p),
                    "mtime": os.path.getmtime(p),
                    "size": os.path.getsize(p),
                })
            except Exception:
                continue
        return {
            "created_at": time.time(),
            "pdf_dir": self.pdf_dir,
            "entries": entries,
        }

    def _same_manifest(self, a: dict, b: dict) -> bool:
        try:
            if not a or not b:
                return False
            if a.get("pdf_dir") != b.get("pdf_dir"):
                return False
            ea = sorted(a.get("entries", []), key=lambda x: x.get("name", ""))
            eb = sorted(b.get("entries", []), key=lambda x: x.get("name", ""))
            if len(ea) != len(eb):
                return False
            for x, y in zip(ea, eb):
                if x.get("name") != y.get("name"):
                    return False
                if x.get("sha256") != y.get("sha256"):
                    return False
            return True
        except Exception:
            return False

    def _extract_pdf_pages(self, pdf_path: str) -> List[PageChunk]:
        book = os.path.basename(pdf_path)
        chunks: List[PageChunk] = []
        doc = fitz.open(pdf_path)
        for i in range(doc.page_count):
            page = doc.load_page(i)
            txt = page.get_text("text") or ""
            txt = txt.strip()
            if not txt:
                continue
            toks = _tokenize(txt)
            if len(toks) < 12:
                continue
            chunks.append(PageChunk(book=book, page=i + 1, text=txt, tokens=toks))
        doc.close()
        return chunks

    def build_or_load(self) -> Dict[str, int]:
        """
        Ritorna stats: {"books":..., "pages":...}
        """
        pdfs = self._list_pdfs()
        if not pdfs:
            self.chunks = []
            self.bm25 = None
            return {"books": 0, "pages": 0}

        new_manifest = self._build_manifest(pdfs)
        old_manifest = _safe_read_json(self._manifest_path)

        # se cache valida → carica chunks
        if self._same_manifest(old_manifest, new_manifest):
            cached = _safe_read_json(self._chunks_path)
            if cached and "chunks" in cached:
                self.chunks = [
                    PageChunk(
                        book=c["book"],
                        page=int(c["page"]),
                        text=c["text"],
                        tokens=c["tokens"],
                    )
                    for c in cached["chunks"]
                ]
                self.bm25 = BM25Okapi([c.tokens for c in self.chunks]) if self.chunks else None
                return {"books": len(set(c.book for c in self.chunks)), "pages": len(self.chunks)}

        # altrimenti ricostruisci
        all_chunks: List[PageChunk] = []
        for p in pdfs:
            all_chunks.extend(self._extract_pdf_pages(p))

        self.chunks = all_chunks
        self.bm25 = BM25Okapi([c.tokens for c in self.chunks]) if self.chunks else None

        _safe_write_json(self._manifest_path, new_manifest)
        _safe_write_json(self._chunks_path, {
            "created_at": time.time(),
            "chunks": [
                {"book": c.book, "page": c.page, "text": c.text, "tokens": c.tokens}
                for c in self.chunks
            ]
        })

        return {"books": len(set(c.book for c in self.chunks)), "pages": len(self.chunks)}

    def query(self, question: str, top_k: int = 4) -> RagResult:
        if not self.bm25 or not self.chunks:
            return RagResult(
                answer="Non ho ancora indicizzato i PDF del Cerchio Firenze 77. (RAG non pronto).",
                citations=[],
                confidence="bassa",
            )

        q_tokens = _tokenize(question)
        if len(q_tokens) < 2:
            return RagResult(
                answer="Dammi una domanda un po’ più specifica (almeno 2-3 parole sensate).",
                citations=[],
                confidence="bassa",
            )

        scores = self.bm25.get_scores(q_tokens)
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)

        picked = []
        for idx in ranked[: max(10, top_k * 3)]:
            s = float(scores[idx])
            if s <= 0:
                continue
            picked.append((s, self.chunks[idx]))
            if len(picked) >= top_k:
                break

        if not picked:
            return RagResult(
                answer=(
                    "Ho cercato nei PDF, ma non trovo un passaggio chiaro su questa domanda.\n"
                    "Riformula (più parole chiave) oppure dimmi in quale libro pensi che sia."
                ),
                citations=[],
                confidence="bassa",
            )

        # stima confidence semplice
        best = picked[0][0]
        confidence = "media" if best < 6 else "alta"
        if best < 2.5:
            confidence = "bassa"

        # costruisci risposta “safe”
        # 1) estrai frasi buone dal top1/top2
        synthesis_parts = []
        for _, ch in picked[:2]:
            synthesis_parts.append(_best_sentences(ch.text, q_tokens, max_sentences=2))
        synthesis = " ".join([p for p in synthesis_parts if p]).strip()
        if not synthesis:
            synthesis = _excerpt(picked[0][1].text, 320)

        answer = (
            "📚 *Secondo i testi del Cerchio Firenze 77 (dai PDF indicizzati):*\n"
            f"{_excerpt(synthesis, 700)}\n\n"
            f"_(Confidenza: {confidence})_"
        )

        citations = []
        for _, ch in picked:
            citations.append({
                "book": ch.book,
                "page": str(ch.page),
                "quote": _excerpt(ch.text, 380),
            })

        return RagResult(answer=answer, citations=citations, confidence=confidence)


def init_cf77_rag(pdf_dir: str = "data/pdfs") -> CFR77RAG:
    rag = CFR77RAG(pdf_dir=pdf_dir)
    rag.build_or_load()
    return rag