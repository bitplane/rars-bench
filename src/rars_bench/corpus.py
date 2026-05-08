"""Enumerate corpus files under corpora/<corpus_class>/ and hash them once."""
import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CorpusItem:
    corpus_class: str           # subdir name immediately under corpora/
    corpus_path: str            # relative to corpora/, posix-style
    abs_path: Path
    size: int
    sha256: str


def enumerate_class(corpora_root: Path, corpus_class: str) -> list[CorpusItem]:
    """Walk corpora_root / corpus_class, hashing each regular file."""
    base = corpora_root / corpus_class
    if not base.is_dir():
        raise FileNotFoundError(f"corpus class not found: {base}")
    items: list[CorpusItem] = []
    for path in sorted(p for p in base.rglob("*") if p.is_file()):
        rel = path.relative_to(corpora_root).as_posix()
        items.append(
            CorpusItem(
                corpus_class=corpus_class,
                corpus_path=rel,
                abs_path=path,
                size=path.stat().st_size,
                sha256=_sha256_file(path),
            )
        )
    return items


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
