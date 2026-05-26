"""
MongoDB query builder for LexCQL queries.

Translates the CQL AST (from cql_parser) into MongoDB filter documents.

Field mapping (LexCQL index -> MongoDB field):
  lemma      -> headword.lemma (+ variants)
  definition -> flatSenses.def
  pos        -> nPos
  entryId    -> sourceId
  etymology  -> etym.text
  citation   -> flatSenses.cit.text
  related    -> compounds.text, derivations.text

Parenthesis notation in stored lemma fields:
  Both headword.lemma and variants may store "(x)" to denote an optional
  character, e.g. "Wind(s)braut".  For default lemma searches (relation "=",
  no modifiers) a paren-aware $regex is added so that both "Windbraut" and
  "Windsbraut" match the stored value "Wind(s)braut".  The regex is built
  from the search term in Python — no aggregation operators required.
"""

from cql_parser import SearchClause, BooleanQuery
from typing import Optional, Union
import re


class UnsupportedIndexError(ValueError):
    """Raised when a CQL query references an unsupported index/field."""
    def __init__(self, index: str):
        self.index = index
        super().__init__(f"Unsupported index: {index}")


# ---------------------------------------------------------------------------
# Field mapping: LexCQL index name -> MongoDB field path(s)
# ---------------------------------------------------------------------------

FIELD_MAP = {
    "lemma": ["headword.lemma", "variants"],
    "definition": ["flatSenses.def"],
    "def": ["flatSenses.def"],                  # Alias
    "pos": ["pos"],
    "entryId": ["sourceId"],
    "etymology": ["etym.text"],
    "citation": ["flatSenses.cit.text"],
    "related": ["compounds.text", "derivations.text"],
    "lang": ["xml:lang"],
}


# ---------------------------------------------------------------------------
# Query builder
# ---------------------------------------------------------------------------

def cql_to_mongo_query(
    node: Union[SearchClause, BooleanQuery],
    source_filter: Optional[list] = None,
) -> dict:
    """
    Convert a CQL AST node to a MongoDB query filter.
    
    Args:
        node: Parsed CQL AST
        source_filter: Optional list of source keys (e.g. ["bwb", "wbf"])
                       to restrict the search to specific dictionaries.
    
    Returns:
        MongoDB filter document.
    """
    query = _build_filter(node)

    # Apply source restriction if specified
    if source_filter:
        if len(source_filter) == 1:
            query = {"$and": [{"source": source_filter[0]}, query]}
        else:
            query = {"$and": [{"source": {"$in": source_filter}}, query]}

    return query


def _build_filter(node: Union[SearchClause, BooleanQuery]) -> dict:
    """Recursively build a MongoDB filter from a CQL AST node."""

    if isinstance(node, BooleanQuery):
        left = _build_filter(node.left)
        right = _build_filter(node.right)

        if node.operator == "AND":
            return {"$and": [left, right]}
        elif node.operator == "OR":
            return {"$or": [left, right]}
        elif node.operator == "NOT":
            # NOT in CQL: left AND NOT right
            return {"$and": [left, {"$nor": [right]}]}
        else:
            raise ValueError(f"Unknown boolean operator: {node.operator}")

    elif isinstance(node, SearchClause):
        return _build_search_clause(node)

    else:
        raise ValueError(f"Unknown AST node type: {type(node)}")


def _build_search_clause(clause: SearchClause) -> dict:
    """Build a MongoDB filter for a single CQL search clause."""

    index = clause.index.lower()
    relation = clause.relation
    term = clause.term
    modifiers = [m.lower() for m in clause.modifiers]

    # Get MongoDB field paths
    fields = FIELD_MAP.get(index)
    if not fields:
        raise UnsupportedIndexError(clause.index)

    # Build the match condition based on relation and modifiers
    condition = _build_condition(term, relation, modifiers, index)

    # For default lemma searches, headword.lemma and variants may contain stored
    # "(x)" notation meaning x is optional, e.g. "Wind(s)braut".
    # For default lemma searches, headword.lemma and variants may store the
    # (x) optional-char convention, e.g. "Wind(s)braut".  Add paren-aware
    # $regex conditions so that "Windbraut", "Windsbraut", and
    # "Windspreuderer" all find "Wind(s)braut" / "Wind(s)spreuderer".
    # Only applies to relation "=" without modifiers (the default flexible search).
    if index == "lemma" and relation == "=" and not modifiers:
        field_conditions = [{f: condition} for f in fields]
        for f in fields:
            field_conditions.append(_build_paren_aware_condition(term, f))
        return {"$or": field_conditions}

    # Standard case: apply condition to all mapped fields
    if len(fields) == 1:
        return {fields[0]: condition}
    else:
        return {"$or": [{f: condition} for f in fields]}


def _build_condition(
    term: str, relation: str, modifiers: list, index: str
) -> dict:
    """
    Build a MongoDB condition for a term, respecting relation and modifiers.

    Relations:
      =  : flexible match (case-insensitive regex by default)
      == : exact match
      is : exact match (for URIs)

    Modifiers:
      /contains    : substring match
      /startswith  : prefix match
      /endswith    : suffix match
      /fullmatch   : exact match
      /partialmatch: substring match

    Note on whitespace tolerance: stored lemma values may carry leading or
    trailing whitespace that originates from the source XML (e.g. the BWB
    stores ``<lemma>Brot </lemma>`` with a trailing space).  All patterns
    that use start/end anchors therefore include ``\\s*`` at the boundary so
    that "Brot " is matched by a search for "Brot".
    """

    if relation == "==":
        # Exact equality
        return term

    if relation == "is":
        # Exact match (typically for URIs)
        return term

    # Relation "=" — flexible matching
    # Apply modifiers if present
    if "fullmatch" in modifiers:
        # Case-insensitive exact match, tolerant of surrounding whitespace in
        # stored values.
        return {"$regex": rf"^\s*{re.escape(term)}\s*$", "$options": "i"}

    if "startswith" in modifiers:
        # Allow optional leading whitespace in the stored value.
        return {"$regex": rf"^\s*{re.escape(term)}", "$options": "i"}

    if "endswith" in modifiers:
        # Allow optional trailing whitespace in the stored value.
        return {"$regex": rf"{re.escape(term)}\s*$", "$options": "i"}

    if "contains" in modifiers or "partialmatch" in modifiers:
        return {"$regex": re.escape(term), "$options": "i"}

    # Default "=" behavior: flexible, beginner-friendly matching
    # For lemma: case-insensitive match (full or prefix)
    # For definition/citation: substring match
    # For pos: exact match (case-insensitive)
    if index in ("lemma",):
        # Match full lemma, case-insensitive, tolerant of surrounding whitespace
        # in stored values (e.g. "Brot " from <lemma>Brot </lemma>).
        return {"$regex": rf"^\s*{re.escape(term)}\s*$", "$options": "i"}
    elif index in ("pos",):
        # Case-insensitive exact match for POS
        return {"$regex": rf"^\s*{re.escape(term)}\s*$", "$options": "i"}
    elif index in ("definition", "def", "etymology", "citation"):
        # Substring match for text fields
        return {"$regex": re.escape(term), "$options": "i"}
    elif index in ("entryId",):
        # Exact match for IDs
        return term
    else:
        # Fallback: case-insensitive substring
        return {"$regex": re.escape(term), "$options": "i"}


def _build_paren_aware_condition(term: str, field: str) -> dict:
    """
    Build a MongoDB $regex condition that finds stored values using the (x)
    optional-char convention.

    Stored values like "Wind(s)braut" encode an optional character:
      - expansion WITH  the optional char: "Windsbraut"
      - expansion WITHOUT the optional char: "Windbraut"

    This function generates a regex FROM the search term that matches both
    forms in the stored representation, without touching the stored values.

    How it works — for each character c in the search term the regex component:

        (?:\\([^)]+\\))??  \\(?  c  \\)?

      ① Non-greedy optional (x) group: skips a stored "(x)" group whose
        character is NOT present in the search term (Case: "Windbraut" finds
        stored "Wind(s)braut" — the (s) group is skipped).

      ② \\(? c \\)?: matches c when it is wrapped in "(...)" in the stored
        value (Case: "Windsbraut" finds stored "Wind(s)braut" — the s is
        matched via its "(s)" wrapper).

      The non-greedy quantifier on ① ensures the regex engine backtracks
      correctly when a stored "(x)" should be consumed by component ② rather
      than skipped.

    Examples:
      "Windsbraut"      matches stored "Wind(s)braut"      ✓
      "Windbraut"       matches stored "Wind(s)braut"      ✓
      "Windspreuderer"  matches stored "Wind(s)spreuderer" ✓
      "Windsspreuderer" matches stored "Wind(s)spreuderer" ✓

    Works with any MongoDB version; no $expr / aggregation operators needed.
    For array fields (variants), MongoDB automatically applies $regex to each
    element and returns the document if any element matches.
    """
    parts = []
    for c in term:
        ec = re.escape(c)
        # ① skip a stored "(x)" group not present in search term (non-greedy)
        # ② match c, which may itself be wrapped in "(c)" in the stored value
        parts.append(r'(?:\([^)]+\))??' + r'\(?' + ec + r'\)?')
    # Allow optional surrounding whitespace in the stored value so that e.g.
    # "Brot " (from <lemma>Brot </lemma>) is matched by a search for "Brot".
    pattern = r'^\s*' + ''.join(parts) + r'\s*$'
    return {field: {"$regex": pattern, "$options": "i"}}
