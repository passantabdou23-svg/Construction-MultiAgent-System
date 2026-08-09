"""Extract controlled PDF sources into auditable, citation-ready JSONL chunks."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from pdfminer.high_level import extract_pages
from pdfminer.layout import LAParams, LTTextContainer
from pypdf import PdfReader

from settings import settings


DEFAULT_MAX_CHARACTERS = 1_800
DEFAULT_OVERLAP_CHARACTERS = 240
_ONLINE_VERSION_RE = re.compile(
    r"\bO\s*N\s*L\s*I\s*N\s*E\s+V\s*E\s*R\s*S\s*I\s*O\s*N\b",
    re.IGNORECASE,
)
_CLAUSE_RE = re.compile(
    r"^(?P<label>(?:\d+(?:\.\d+)+|[A-Z]\d+(?:\.\d+)*))\s*[.:]?\s+(?P<body>\S.*)$"
)
_CLAUSE_ONLY_RE = re.compile(
    r"^(?P<label>(?:\d+(?:\.\d+)+|[A-Z]\d+(?:\.\d+)*))\s*[.:]?$"
)
_SECTION_RE = re.compile(r"^Section\s+\d+[A-Z]?\s*[:.-]?\s*.+$", re.IGNORECASE)


class ManifestError(ValueError):
    """Raised when source provenance metadata is incomplete or invalid."""


class SourceIntegrityError(ValueError):
    """Raised when a source file does not match its controlled manifest record."""


@dataclass(frozen=True)
class SourceDocument:
    document_id: str
    file_path: Path
    title: str
    edition: str
    publication_date: str
    jurisdiction: str
    source_url: str
    download_url: str
    license_name: str
    license_url: str
    expected_sha256: str
    expected_pages: int
    expected_text_pages: int
    retrieval_excluded_pages: tuple[int, ...]
    usage_note: str


@dataclass(frozen=True)
class DocumentChunk:
    chunk_id: str
    document_id: str
    title: str
    edition: str
    publication_date: str
    jurisdiction: str
    page_number: int
    printed_page_label: str
    section: str
    clause: str
    text: str
    source_url: str
    download_url: str
    license_name: str
    license_url: str
    source_sha256: str
    retrieval_eligible: bool

    @property
    def citation(self) -> str:
        location = self.section or "Unsectioned content"
        if self.clause:
            location = f"{location}, clause {self.clause}"
        page = f"PDF p. {self.page_number}"
        if self.printed_page_label:
            page = f"printed p. {self.printed_page_label} ({page})"
        return f"{self.title} ({self.edition}), {location}, {page}"

    def to_json_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["citation"] = self.citation
        return record


@dataclass(frozen=True)
class IngestionResult:
    document: SourceDocument
    page_count: int
    nonempty_page_count: int
    non_text_page_numbers: tuple[int, ...]
    chunks: tuple[DocumentChunk, ...]


@dataclass(frozen=True)
class IngestionSummary:
    document_count: int
    page_count: int
    nonempty_page_count: int
    chunk_count: int
    output_path: Path


@dataclass(frozen=True)
class _TextUnit:
    section: str
    clause: str
    text: str


@dataclass(frozen=True)
class _ExtractedPage:
    lines: tuple[str, ...]
    printed_page_label: str


def _required_string(entry: dict[str, Any], key: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"Manifest field '{key}' must be a non-empty string")
    return value.strip()


def load_manifest(manifest_path: str | Path) -> tuple[SourceDocument, ...]:
    """Load and validate source records, resolving PDF paths beside the manifest."""
    path = Path(manifest_path).resolve()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ManifestError(f"Manifest does not exist: {path}") from error
    except json.JSONDecodeError as error:
        raise ManifestError(f"Manifest is not valid JSON: {path}") from error

    if payload.get("schema_version") != 1:
        raise ManifestError("Only source manifest schema_version 1 is supported")
    entries = payload.get("documents")
    if not isinstance(entries, list) or not entries:
        raise ManifestError("Manifest must contain at least one document")

    documents: list[SourceDocument] = []
    seen_ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ManifestError("Every manifest document must be an object")
        document_id = _required_string(entry, "document_id")
        if document_id in seen_ids:
            raise ManifestError(f"Duplicate document_id: {document_id}")
        seen_ids.add(document_id)

        file_name = _required_string(entry, "file")
        file_path = (path.parent / file_name).resolve()
        if path.parent not in file_path.parents:
            raise ManifestError(f"Document path must remain inside the manifest folder: {file_name}")

        sha256 = _required_string(entry, "sha256").casefold()
        if not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise ManifestError(f"Invalid SHA-256 for document: {document_id}")
        expected_pages = entry.get("expected_pages")
        if not isinstance(expected_pages, int) or isinstance(expected_pages, bool) or expected_pages < 1:
            raise ManifestError(f"expected_pages must be a positive integer: {document_id}")
        expected_text_pages = entry.get("expected_text_pages")
        if (
            not isinstance(expected_text_pages, int)
            or isinstance(expected_text_pages, bool)
            or not 1 <= expected_text_pages <= expected_pages
        ):
            raise ManifestError(
                f"expected_text_pages must be between 1 and expected_pages: {document_id}"
            )
        retrieval_excluded_pages = entry.get("retrieval_excluded_pages", [])
        if (
            not isinstance(retrieval_excluded_pages, list)
            or any(
                not isinstance(page, int)
                or isinstance(page, bool)
                or not 1 <= page <= expected_pages
                for page in retrieval_excluded_pages
            )
            or len(set(retrieval_excluded_pages)) != len(retrieval_excluded_pages)
        ):
            raise ManifestError(
                f"retrieval_excluded_pages must contain unique valid PDF pages: {document_id}"
            )

        documents.append(
            SourceDocument(
                document_id=document_id,
                file_path=file_path,
                title=_required_string(entry, "title"),
                edition=_required_string(entry, "edition"),
                publication_date=_required_string(entry, "publication_date"),
                jurisdiction=_required_string(entry, "jurisdiction"),
                source_url=_required_string(entry, "source_url"),
                download_url=_required_string(entry, "download_url"),
                license_name=_required_string(entry, "license_name"),
                license_url=_required_string(entry, "license_url"),
                expected_sha256=sha256,
                expected_pages=expected_pages,
                expected_text_pages=expected_text_pages,
                retrieval_excluded_pages=tuple(sorted(retrieval_excluded_pages)),
                usage_note=_required_string(entry, "usage_note"),
            )
        )
    return tuple(documents)


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_source(document: SourceDocument) -> str:
    if not document.file_path.is_file():
        raise SourceIntegrityError(f"Source PDF does not exist: {document.file_path}")
    actual_sha256 = file_sha256(document.file_path)
    if actual_sha256 != document.expected_sha256:
        raise SourceIntegrityError(
            f"SHA-256 mismatch for {document.document_id}: "
            f"expected {document.expected_sha256}, received {actual_sha256}"
        )
    return actual_sha256


def _normalize_line(line: str) -> str:
    without_marker = _ONLINE_VERSION_RE.sub(" ", line.replace("\u00a0", " "))
    return re.sub(r"\s+", " ", without_marker).strip(" \t|•")


def _repeated_page_lines(raw_pages: list[list[str]]) -> set[str]:
    """Find short lines repeatedly occurring near page headers or footers."""
    appearances: Counter[str] = Counter()
    for page_lines in raw_pages:
        normalized_lines = [
            normalized
            for line in page_lines
            if (normalized := _normalize_line(line))
        ]
        margin_lines = normalized_lines[:4] + normalized_lines[-4:]
        candidates = {
            normalized.casefold()
            for normalized in margin_lines
            if 4 <= len(normalized) <= 100
        }
        appearances.update(candidates)
    threshold = max(3, math.ceil(len(raw_pages) * 0.05))
    return {line for line, count in appearances.items() if count >= threshold}


def _clean_page_lines(lines: Iterable[str], repeated_lines: set[str]) -> list[str]:
    cleaned: list[str] = []
    previous_blank = True
    for raw_line in lines:
        line = _normalize_line(raw_line)
        if not line:
            if not previous_blank:
                cleaned.append("")
            previous_blank = True
            continue
        if line.casefold() in repeated_lines or re.fullmatch(r"\d{1,3}", line):
            continue
        cleaned.append(line)
        previous_blank = False
    while cleaned and not cleaned[-1]:
        cleaned.pop()
    return cleaned


def _looks_like_section(line: str) -> bool:
    if _SECTION_RE.match(line):
        return True
    if line.endswith((".", ",", ";", ":")):
        return False
    if any(symbol in line for symbol in ("=", "×", "≤", "≥")):
        return False
    if re.search(
        r"\b(?:ISBN|BSI|BS|EN|ISO|PD\s*\d|SW\d|WWW|HTTP|TEL|EMAIL)\b",
        line,
        re.IGNORECASE,
    ):
        return False
    letters = [character for character in line if character.isalpha()]
    if not 3 <= len(line) <= 100 or len(letters) < 4:
        return False
    uppercase_ratio = sum(character.isupper() for character in letters) / len(letters)
    return uppercase_ratio >= 0.88 and len(line.split()) <= 12


def _extract_raw_pages(path: Path) -> list[_ExtractedPage]:
    """Extract clean words while preserving left-column then right-column reading order."""
    previous_level = logging.getLogger("pdfminer").level
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    try:
        extracted_pages: list[_ExtractedPage] = []
        layouts = extract_pages(str(path), laparams=LAParams(boxes_flow=None))
        for layout in layouts:
            boxes = [element for element in layout if isinstance(element, LTTextContainer)]
            midpoint = float(layout.width) / 2
            left_column = sorted(
                (box for box in boxes if float(box.x0) < midpoint),
                key=lambda box: (-float(box.y1), float(box.x0)),
            )
            right_column = sorted(
                (box for box in boxes if float(box.x0) >= midpoint),
                key=lambda box: (-float(box.y1), float(box.x0)),
            )
            footer_labels = [
                box.get_text().strip()
                for box in boxes
                if float(box.y1) < 50 and re.fullmatch(r"\d{1,3}", box.get_text().strip())
            ]
            text = "\n\n".join(box.get_text().strip() for box in left_column + right_column)
            extracted_pages.append(
                _ExtractedPage(
                    lines=tuple(text.splitlines()),
                    printed_page_label=footer_labels[0] if len(footer_labels) == 1 else "",
                )
            )
        return extracted_pages
    finally:
        logging.getLogger("pdfminer").setLevel(previous_level)


def _page_units(
    lines: list[str],
    current_section: str,
    current_clause: str,
) -> tuple[list[_TextUnit], str, str]:
    units: list[_TextUnit] = []
    buffer: list[str] = []
    unit_section = current_section
    unit_clause = current_clause

    def flush() -> None:
        nonlocal buffer
        text = re.sub(r"\s+", " ", " ".join(buffer)).strip()
        if text:
            units.append(_TextUnit(unit_section, unit_clause, text))
        buffer = []

    for line in lines:
        if not line:
            if buffer:
                buffer.append(" ")
            continue
        if _looks_like_section(line):
            flush()
            current_section = line.title() if line.isupper() else line
            current_clause = ""
            unit_section = current_section
            unit_clause = ""
            buffer.append(line)
            continue
        clause_match = _CLAUSE_RE.match(line) or _CLAUSE_ONLY_RE.match(line)
        if clause_match:
            flush()
            current_clause = clause_match.group("label")
            unit_section = current_section
            unit_clause = current_clause
            buffer.append(line)
            continue
        buffer.append(line)
    flush()
    return units, current_section, current_clause


def _coalesce_short_units(units: list[_TextUnit], minimum_characters: int = 80) -> list[_TextUnit]:
    """Attach labels and short headings to nearby substantive text."""
    coalesced: list[_TextUnit] = []
    pending: list[_TextUnit] = []
    for unit in units:
        if len(unit.text) < minimum_characters:
            pending.append(unit)
            continue
        if pending:
            prefix = " ".join(item.text for item in pending)
            section = unit.section or pending[-1].section
            clause = unit.clause or pending[-1].clause
            unit = _TextUnit(section, clause, f"{prefix} {unit.text}".strip())
            pending = []
        coalesced.append(unit)
    if pending:
        suffix = " ".join(item.text for item in pending)
        if coalesced:
            last = coalesced[-1]
            coalesced[-1] = _TextUnit(last.section, last.clause, f"{last.text} {suffix}".strip())
        elif suffix:
            last_pending = pending[-1]
            coalesced.append(_TextUnit(last_pending.section, last_pending.clause, suffix))
    return coalesced


def _split_text(text: str, max_characters: int, overlap_characters: int) -> list[str]:
    if len(text) <= max_characters:
        return [text]
    parts: list[str] = []
    start = 0
    while start < len(text):
        hard_end = min(len(text), start + max_characters)
        end = hard_end
        if hard_end < len(text):
            boundary = text.rfind(" ", start + max_characters // 2, hard_end)
            if boundary > start:
                end = boundary
        part = text[start:end].strip()
        if part:
            parts.append(part)
        if end >= len(text):
            break
        next_start = max(start + 1, end - overlap_characters)
        while next_start < end and not text[next_start].isspace():
            next_start += 1
        start = next_start
    return parts


def _make_chunk(
    document: SourceDocument,
    source_sha256: str,
    page_number: int,
    printed_page_label: str,
    section: str,
    clause: str,
    text: str,
    sequence: int,
) -> DocumentChunk:
    identity = "\x1f".join(
        (document.document_id, str(page_number), section, clause, str(sequence), text)
    )
    suffix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return DocumentChunk(
        chunk_id=f"{document.document_id}-{suffix}",
        document_id=document.document_id,
        title=document.title,
        edition=document.edition,
        publication_date=document.publication_date,
        jurisdiction=document.jurisdiction,
        page_number=page_number,
        printed_page_label=printed_page_label,
        section=section,
        clause=clause,
        text=text,
        source_url=document.source_url,
        download_url=document.download_url,
        license_name=document.license_name,
        license_url=document.license_url,
        source_sha256=source_sha256,
        retrieval_eligible=page_number not in document.retrieval_excluded_pages,
    )


def ingest_document(
    document: SourceDocument,
    *,
    max_characters: int = DEFAULT_MAX_CHARACTERS,
    overlap_characters: int = DEFAULT_OVERLAP_CHARACTERS,
) -> IngestionResult:
    """Verify, extract, clean, and chunk one controlled PDF source."""
    if max_characters < 400:
        raise ValueError("max_characters must be at least 400")
    if not 0 <= overlap_characters < max_characters // 2:
        raise ValueError("overlap_characters must be non-negative and less than half the chunk size")

    source_sha256 = verify_source(document)
    reader = PdfReader(str(document.file_path))
    if len(reader.pages) != document.expected_pages:
        raise SourceIntegrityError(
            f"Page-count mismatch for {document.document_id}: "
            f"expected {document.expected_pages}, received {len(reader.pages)}"
        )

    extracted_pages = _extract_raw_pages(document.file_path)
    if len(extracted_pages) != len(reader.pages):
        raise SourceIntegrityError(
            f"Extractor page-count mismatch for {document.document_id}: "
            f"PDF has {len(reader.pages)}, extractor returned {len(extracted_pages)}"
        )
    raw_pages = [list(page.lines) for page in extracted_pages]
    repeated_lines = _repeated_page_lines(raw_pages)
    chunks: list[DocumentChunk] = []
    nonempty_pages = 0
    non_text_page_numbers: list[int] = []
    current_section = ""
    current_clause = ""

    for page_number, raw_lines in enumerate(raw_pages, start=1):
        printed_page_label = extracted_pages[page_number - 1].printed_page_label
        lines = _clean_page_lines(raw_lines, repeated_lines)
        if lines:
            nonempty_pages += 1
        else:
            non_text_page_numbers.append(page_number)
        units, current_section, current_clause = _page_units(lines, current_section, current_clause)
        units = _coalesce_short_units(units)
        sequence = 0
        for unit in units:
            for part in _split_text(unit.text, max_characters, overlap_characters):
                sequence += 1
                chunks.append(
                    _make_chunk(
                        document,
                        source_sha256,
                        page_number,
                        printed_page_label,
                        unit.section,
                        unit.clause,
                        part,
                        sequence,
                    )
                )

    if nonempty_pages != document.expected_text_pages:
        raise SourceIntegrityError(
            f"Searchable-page mismatch for {document.document_id}: expected "
            f"{document.expected_text_pages}/{len(reader.pages)}, received "
            f"{nonempty_pages}/{len(reader.pages)}"
        )
    if not chunks:
        raise SourceIntegrityError(f"No searchable chunks were produced for {document.document_id}")
    if len({chunk.chunk_id for chunk in chunks}) != len(chunks):
        raise SourceIntegrityError(f"Duplicate chunk IDs were produced for {document.document_id}")

    return IngestionResult(
        document=document,
        page_count=len(reader.pages),
        nonempty_page_count=nonempty_pages,
        non_text_page_numbers=tuple(non_text_page_numbers),
        chunks=tuple(chunks),
    )


def write_chunks_jsonl(chunks: Iterable[DocumentChunk], output_path: str | Path) -> int:
    """Write deterministic UTF-8 JSONL through an atomic same-directory replacement."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    count = 0
    with temporary_path.open("w", encoding="utf-8", newline="\n") as output:
        for chunk in chunks:
            output.write(json.dumps(chunk.to_json_record(), ensure_ascii=False, sort_keys=True))
            output.write("\n")
            count += 1
    temporary_path.replace(path)
    return count


def ingest_manifest(
    manifest_path: str | Path,
    output_path: str | Path,
    *,
    max_characters: int = DEFAULT_MAX_CHARACTERS,
    overlap_characters: int = DEFAULT_OVERLAP_CHARACTERS,
) -> IngestionSummary:
    results = [
        ingest_document(
            document,
            max_characters=max_characters,
            overlap_characters=overlap_characters,
        )
        for document in load_manifest(manifest_path)
    ]
    chunks = tuple(chunk for result in results for chunk in result.chunks)
    written_count = write_chunks_jsonl(chunks, output_path)
    return IngestionSummary(
        document_count=len(results),
        page_count=sum(result.page_count for result in results),
        nonempty_page_count=sum(result.nonempty_page_count for result in results),
        chunk_count=written_count,
        output_path=Path(output_path).resolve(),
    )


def _parse_args() -> argparse.Namespace:
    default_documents = Path(settings.rag_documents_path)
    default_data = Path(settings.rag_data_path)
    parser = argparse.ArgumentParser(
        description="Verify controlled PDFs and generate citation-ready JSONL chunks."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=default_documents / "manifest.json",
        help="Controlled source manifest (default: rag_documents/manifest.json)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=default_data / "chunks.jsonl",
        help="Generated JSONL path (default: rag_data/chunks.jsonl)",
    )
    parser.add_argument("--max-characters", type=int, default=DEFAULT_MAX_CHARACTERS)
    parser.add_argument("--overlap-characters", type=int, default=DEFAULT_OVERLAP_CHARACTERS)
    return parser.parse_args()


def main() -> None:
    arguments = _parse_args()
    summary = ingest_manifest(
        arguments.manifest,
        arguments.output,
        max_characters=arguments.max_characters,
        overlap_characters=arguments.overlap_characters,
    )
    print("RAG document ingestion complete")
    print(f"  Documents: {summary.document_count}")
    print(f"  Searchable pages: {summary.nonempty_page_count}/{summary.page_count}")
    print(f"  Chunks: {summary.chunk_count}")
    print(f"  Output: {summary.output_path}")


if __name__ == "__main__":
    main()
