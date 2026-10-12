"""
MongoDB query builder for LexCQL queries.

Translates the CQL AST (from cql_parser) into MongoDB filter documents.

Field mapping (LexCQL index -> MongoDB field):
  lemma      -> headword.lemma (+ variants)
  definition -> flatSenses.def.text
  pos        -> nPos
  entryId    -> sourceId
  etymology  -> etym.text
  citation   -> flatSenses.cit.text
  related    -> compounds.text, derivations.text

Parenthesis notation in stored lemma fields:
  Both headword.lemma and variants may store "(x)" to denote an optional
  character, e.g. "Wind(s)braut".  For default lemma searches (relation "=",
  no modifiers, no wildcards) a paren-aware $regex is added so that both
  "Windbraut" and "Windsbraut" match the stored value "Wind(s)braut".  The
  regex is built from the search term in Python — no aggregation operators
  required.

CQL masking (LexFCS spec, default modifier):
  * → .* (zero or more characters)
  ? → .  (exactly one character)
  \\* / \\? → literal * / ?  (escaped wildcards)
  /unmasked modifier → all characters are literal (re.escape)
  /regexp modifier   → term is used as a raw MongoDB regex pattern
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
    "definition": ["flatSenses.def.text"],
    "def": ["flatSenses.def.text"],                  # Alias
    "pos": ["pos"],
    "entryId": ["sourceId"],
    "etymology": ["etym.text"],
    "citation": ["flatSenses.cit.text"],
    "related": ["compounds.text", "derivations.text"],
    "lang": ["xml:lang"],
}

# ---------------------------------------------------------------------------
# Belegstellen extension for bare-term (no explicit index) searches
# ---------------------------------------------------------------------------
# When a search term is given without an explicit index (e.g. "Humboldt AND Amerika"),
# the parser defaults to the "lemma" index. To also find entries where the terms
# appear in citation texts or definitions, list additional MongoDB fields here.
#
# These fields are always matched with a simple case-insensitive substring regex
# (no paren-aware logic, no start/end anchors). Paren-aware matching remains
# restricted to headword.lemma and variants.
#
# Set to an empty list to disable Belegstellen extension (lemma-only default).
BARE_TERM_EXTRA_FIELDS: list = [
    "flatSenses.cit.text",
    "flatSenses.def.text",
]


# ---------------------------------------------------------------------------
# Query builder
# ---------------------------------------------------------------------------

def cql_to_mongo_query(
    node: Union[SearchClause, BooleanQuery],
    source_filter: Optional[list] = None,
    include_bare_term_extra: bool = True,
) -> dict:
    """
    Convert a CQL AST node to a MongoDB query filter.

    Args:
        node: Parsed CQL AST
        source_filter: Optional list of source keys (e.g. ["bwb", "wbf"])
                       to restrict the search to specific dictionaries.
        include_bare_term_extra: If False, bare-term searches only match
                       headword.lemma and variants — the extra full-text fields
                       (BARE_TERM_EXTRA_FIELDS) are excluded.  Used to build
                       the "primary" filter for priority-sorted results.

    Returns:
        MongoDB filter document.
    """
    query = _build_filter(node, include_bare_term_extra)

    # Apply source restriction if specified
    if source_filter:
        if len(source_filter) == 1:
            query = {"$and": [{"source": source_filter[0]}, query]}
        else:
            query = {"$and": [{"source": {"$in": source_filter}}, query]}

    return query


def _build_filter(
    node: Union[SearchClause, BooleanQuery],
    include_bare_term_extra: bool = True,
) -> dict:
    """Recursively build a MongoDB filter from a CQL AST node."""

    if isinstance(node, BooleanQuery):
        left = _build_filter(node.left, include_bare_term_extra)
        right = _build_filter(node.right, include_bare_term_extra)

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
        return _build_search_clause(node, include_bare_term_extra)

    else:
        raise ValueError(f"Unknown AST node type: {type(node)}")


def _mask_to_regex(term: str) -> str:
    """
    Convert a CQL masked search term to a regex pattern fragment.

    CQL masking rules (the implicit default modifier):
      *          →  .*   (zero or more characters)
      ?          →  .    (exactly one character)
      \\*, \\?   →  literal * or ?  (escaped wildcards)
      \\\\       →  literal \\
      \\"        →  literal "
      all other  →  re.escape(c)
    """
    parts = []
    i = 0
    while i < len(term):
        c = term[i]
        if c == '\\' and i + 1 < len(term) and term[i + 1] in ('*', '?', '\\', '"'):
            # Escaped special character → treat as literal
            parts.append(re.escape(term[i + 1]))
            i += 2
        elif c == '*':
            parts.append('.*')
            i += 1
        elif c == '?':
            parts.append('.')
            i += 1
        else:
            parts.append(re.escape(c))
            i += 1
    return ''.join(parts)


def _has_unescaped_wildcards(term: str) -> bool:
    """Return True if term contains unescaped CQL wildcard characters (* or ?)."""
    i = 0
    while i < len(term):
        c = term[i]
        if c == '\\' and i + 1 < len(term) and term[i + 1] in ('*', '?', '\\', '"'):
            i += 2  # skip escaped character
        elif c in ('*', '?'):
            return True
        else:
            i += 1
    return False


def _build_search_clause(
    clause: SearchClause,
    include_bare_term_extra: bool = True,
) -> dict:
    """Build a MongoDB filter for a single CQL search clause."""

    index = clause.index.lower()
    relation = clause.relation
    term = clause.term
    modifiers = [m.lower() for m in clause.modifiers]

    # Get MongoDB field paths
    fields = FIELD_MAP.get(index)
    if not fields:
        raise UnsupportedIndexError(clause.index)

    # For pos: translate English/UD query terms to German stored values.
    # Only for simple "=" searches without modifiers.
    # E.g. "pos = NOUN" → searches stored "Substantiv";
    #      "pos = DET"  → searches "Determinativ" OR "Possessivum".
    if index == "pos" and relation == "=" and not modifiers:
        from pos_mapping import EN_TO_DE
        de_terms = EN_TO_DE.get(term.upper())
        if de_terms:
            if len(de_terms) == 1:
                term = de_terms[0]
            else:
                # Multiple German equivalents (e.g. DET → Determinativ + Possessivum)
                return {"$or": [
                    {"pos": _build_condition(t, relation, modifiers, index)}
                    for t in de_terms
                ]}

    # Build the match condition based on relation and modifiers
    condition = _build_condition(term, relation, modifiers, index)

    # For bare-term searches (no explicit index given), optionally extend the
    # search to Belegstellen fields (flatSenses.cit.text, flatSenses.def.text).
    # These are always matched with citation-style substring regex — no paren-aware,
    # no start/end anchors.  See BARE_TERM_EXTRA_FIELDS above.
    # When include_bare_term_extra=False, only headword.lemma and variants are
    # searched — used to build the "primary" filter for priority-sorted results.
    extra_fields: list = []
    belegstellen_cond: dict = {}
    if index == "lemma" and clause.is_default and BARE_TERM_EXTRA_FIELDS and include_bare_term_extra:
        extra_fields = BARE_TERM_EXTRA_FIELDS
        # Use citation-style condition (substring, case-insensitive) for Belegstellen
        belegstellen_cond = _build_condition(term, relation, modifiers, "citation")

    # For lemma index with default relation "=" and no modifiers:
    # add paren-aware $regex conditions for headword.lemma and variants.
    # Only applies without CQL wildcards (for wildcard terms, .* naturally traverses
    # the stored "(x)" groups, so the standard condition is sufficient).
    if index == "lemma" and relation == "=" and not modifiers:
        if not _has_unescaped_wildcards(term):
            field_conditions = [{f: condition} for f in fields]
            for f in fields:
                field_conditions.append(_build_paren_aware_condition(term, f))
            # Append Belegstellen conditions (simple substring, no paren-aware)
            for f in extra_fields:
                field_conditions.append({f: belegstellen_cond})
            return {"$or": field_conditions}
        # Wildcard term: fall through to standard case below.

    # Standard case: apply condition to all mapped fields, plus Belegstellen extras
    all_conditions = [{f: condition} for f in fields]
    for f in extra_fields:
        all_conditions.append({f: belegstellen_cond})

    if len(all_conditions) == 1:
        return all_conditions[0]
    return {"$or": all_conditions}


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
      /unmasked    : treat * and ? as literal characters (no CQL wildcards)
      /regexp      : term is a raw MongoDB regex pattern (no escaping)

    CQL masking (default, per LexFCS spec):
      * → .* (zero or more characters)
      ? → .  (exactly one character)
      \\*, \\? → literal * or ?

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

    # /regexp: term is a raw regex pattern, used as-is (no anchor wrapping)
    if "regexp" in modifiers:
        return {"$regex": term, "$options": "i"}

    # Determine the regex pattern fragment from the search term.
    # CQL default is "masked": * → .*, ? → .
    # /unmasked overrides: all characters are literal.
    if "unmasked" in modifiers:
        pfrag = re.escape(term)
    else:
        pfrag = _mask_to_regex(term)

    # Apply match-type modifiers if present
    if "fullmatch" in modifiers:
        # Case-insensitive exact match, tolerant of surrounding whitespace in
        # stored values.
        return {"$regex": rf"^\s*{pfrag}\s*$", "$options": "i"}

    if "startswith" in modifiers:
        # Allow optional leading whitespace in the stored value.
        return {"$regex": rf"^\s*{pfrag}", "$options": "i"}

    if "endswith" in modifiers:
        # Allow optional trailing whitespace in the stored value.
        return {"$regex": rf"{pfrag}\s*$", "$options": "i"}

    if "contains" in modifiers or "partialmatch" in modifiers:
        return {"$regex": pfrag, "$options": "i"}

    # Default "=" behavior: flexible, beginner-friendly matching
    # For lemma: case-insensitive match (full or prefix)
    # For definition/citation: substring match
    # For pos: exact match (case-insensitive)
    if index in ("lemma",):
        # Match full lemma, case-insensitive, tolerant of surrounding whitespace
        # in stored values (e.g. "Brot " from <lemma>Brot </lemma>).
        return {"$regex": rf"^\s*{pfrag}\s*$", "$options": "i"}
    elif index in ("pos",):
        # Case-insensitive exact match for POS
        return {"$regex": rf"^\s*{pfrag}\s*$", "$options": "i"}
    elif index in ("definition", "def", "etymology", "citation"):
        # Substring match for text fields
        return {"$regex": pfrag, "$options": "i"}
    elif index in ("entryId",):
        # Exact match for IDs
        return term
    else:
        # Fallback: case-insensitive substring
        return {"$regex": pfrag, "$options": "i"}


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
