"""
Scan the BDO MongoDB for citation texts that contain orphaned signal words
(e.g. "vgl.", "s.", "siehe") in inter-bibref gaps.

Usage:
    python tools/scan_signal_words.py [--limit N] [--source bwb|wbf|dibs]

The script connects to the MongoDB instance configured in .env (MONGODB_URI)
and iterates all entries in lex.entries, checking each citation for signal
words that survive after the bibref spans are stripped out.  Matches are
printed to stdout so you can judge whether the filter list is complete.
"""

import argparse
import os
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Load .env from repo root (simple key=value parser, no external dependency)
# ---------------------------------------------------------------------------

def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv(Path(__file__).parent.parent / ".env")

# ---------------------------------------------------------------------------
# Signal-word regex (kept in sync with sru_response.py)
# ---------------------------------------------------------------------------

SIGNAL_WORD_RE = re.compile(
    r"(?:(?<=\s)|^)"
    r"(?:vgl\.\s*auch|vgl\.|s\.\s*auch|s\.\s*[aouv]\.|s\.|siehe\s+auch|siehe)"
    r"(?=\s|$)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Helpers (mirror of sru_response._slice_out / _normalize_ws)
# ---------------------------------------------------------------------------

def _normalize_ws(s: str) -> str:
    return " ".join(s.split())


def _slice_out(text: str, spans: list) -> str:
    if not spans:
        return text
    pieces = []
    pos = 0
    for start, end in sorted(spans):
        if start > pos:
            pieces.append(text[pos:start])
        pos = max(pos, end)
    if pos < len(text):
        pieces.append(text[pos:])
    return "".join(pieces)


def _inter_bibref_text(cit: dict) -> str:
    """Return the text that remains after stripping italic + bibref spans."""
    text = cit.get("text", "") or ""
    annotations = cit.get("annotations", [])
    italic_spans = []
    bibref_spans = []
    for a in annotations:
        start = a.get("start", 0)
        end = a.get("end", 0)
        if a.get("type") == "text" and "italic" in (a.get("labels") or []):
            italic_spans.append((start, end))
        elif a.get("type") == "bibref":
            bibref_spans.append((start, end))
    if not bibref_spans:
        return ""
    return _normalize_ws(_slice_out(text, italic_spans + bibref_spans))


# ---------------------------------------------------------------------------
# Main scan
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Scan BDO citations for signal words.")
    parser.add_argument("--limit", type=int, default=0, help="Stop after N matches (0 = unlimited)")
    parser.add_argument("--source", help="Restrict to one dictionary source (bwb, wbf, dibs)")
    args = parser.parse_args()

    try:
        import pymongo
    except ImportError:
        sys.exit("pymongo is not installed. Run: uv pip install pymongo")

    password = os.environ.get("MONGO_PASSWORD", "")
    uri = os.environ.get("MONGODB_URI", f"mongodb://admin:{password}@localhost:27017/admin")
    db_name = os.environ.get("MONGODB_DB", "lex")

    client = pymongo.MongoClient(uri, serverSelectionTimeoutMS=5000)
    try:
        client.admin.command("ping")
    except Exception as exc:
        sys.exit(f"Cannot connect to MongoDB: {exc}")

    coll = client[db_name]["entries"]

    query: dict = {}
    if args.source:
        query["source"] = args.source

    projection = {"_id": 1, "flatSenses": 1}

    total_entries = 0
    total_hits = 0

    cursor = coll.find(query, projection, batch_size=200)

    for entry in cursor:
        entry_id = entry.get("_id", "?")
        total_entries += 1

        for si, sense in enumerate(entry.get("flatSenses", [])):
            for ci, cit in enumerate(sense.get("cit", [])):
                if cit.get("type") != "example":
                    continue
                inter = _inter_bibref_text(cit)
                if not inter:
                    continue
                match = SIGNAL_WORD_RE.search(inter)
                if match:
                    total_hits += 1
                    raw_text = (cit.get("text", "") or "").strip()
                    preview = raw_text[:120] + ("…" if len(raw_text) > 120 else "")
                    print(
                        f"[{entry_id}] sense {si+1}, cit {ci+1} | "
                        f"match={match.group()!r} | {preview}"
                    )
                    if args.limit and total_hits >= args.limit:
                        print(f"\n-- stopped after {args.limit} matches --")
                        _print_summary(total_entries, total_hits)
                        return

    _print_summary(total_entries, total_hits)


def _print_summary(entries: int, hits: int) -> None:
    print(f"\nScanned {entries} entries, found {hits} citation(s) with signal words.")


if __name__ == "__main__":
    main()
