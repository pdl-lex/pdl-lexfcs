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
  no modifiers) we therefore add $expr/$regexMatch conditions alongside the
  standard $regex conditions.  These treat stored "(x)" as "(?:x)?" so that
  both "Windbraut" and "Windsbraut" match the stored value "Wind(s)braut".

  Requires MongoDB 4.4+ ($replaceAll).
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
    # Add $expr conditions alongside the standard $regex ones so that both
    # "Windbraut" and "Windsbraut" find the stored value "Wind(s)braut".
    # Only applies to relation "=" without modifiers (the default flexible search).
    if index == "lemma" and relation == "=" and not modifiers:
        field_conditions = [{f: condition} for f in fields]
        for f in fields:
            field_conditions.append(_build_paren_aware_condition(term, f"${f}"))
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
        # Case-insensitive exact match
        return {"$regex": f"^{re.escape(term)}$", "$options": "i"}

    if "startswith" in modifiers:
        return {"$regex": f"^{re.escape(term)}", "$options": "i"}

    if "endswith" in modifiers:
        return {"$regex": f"{re.escape(term)}$", "$options": "i"}

    if "contains" in modifiers or "partialmatch" in modifiers:
        return {"$regex": re.escape(term), "$options": "i"}

    # Default "=" behavior: flexible, beginner-friendly matching
    # For lemma: case-insensitive match (full or prefix)
    # For definition/citation: substring match
    # For pos: exact match (case-insensitive)
    if index in ("lemma",):
        # Match full lemma, case-insensitive
        return {"$regex": f"^{re.escape(term)}$", "$options": "i"}
    elif index in ("pos",):
        # Case-insensitive exact match for POS
        return {"$regex": f"^{re.escape(term)}$", "$options": "i"}
    elif index in ("definition", "def", "etymology", "citation"):
        # Substring match for text fields
        return {"$regex": re.escape(term), "$options": "i"}
    elif index in ("entryId",):
        # Exact match for IDs
        return term
    else:
        # Fallback: case-insensitive substring
        return {"$regex": re.escape(term), "$options": "i"}


def _build_paren_aware_condition(term: str, field_ref: str) -> dict:
    """
    Build a MongoDB $expr condition that matches entries where a stored field
    value, interpreted as a pattern with "(x)" meaning x is optional, matches
    the search term.

    Example:  stored headword.lemma = "Wind(s)braut"
              → pattern  "^Wind(?:s)?braut$"
              → matches  "Windbraut"  ✓   and  "Windsbraut"  ✓

    Works for both string fields (headword.lemma) and array fields (variants):
    for arrays it checks whether ANY element matches.

    Implementation: use $replaceAll to convert "(" → "(?:" and ")" → ")?" in
    the stored value, then check the search term against that PCRE pattern via
    $regexMatch.  Requires MongoDB 4.4+ ($replaceAll).

    Args:
        term:      the user's search term (constant string in the query)
        field_ref: MongoDB expression for the field, e.g. "$headword.lemma"
    """

    def _match_one(value_expr) -> dict:
        """$regexMatch: does `term` match the pattern derived from value_expr?"""
        return {
            "$regexMatch": {
                "input": term,
                "regex": {
                    "$concat": [
                        "^",
                        {
                            "$replaceAll": {
                                "input": {
                                    "$replaceAll": {
                                        "input": value_expr,
                                        "find": "(",
                                        "replacement": "(?:",
                                    }
                                },
                                "find": ")",
                                "replacement": ")?",
                            }
                        },
                        "$",
                    ]
                },
                "options": "i",
            }
        }

    # For array fields: match if any element satisfies the condition.
    # For string fields: match directly.
    match_condition = {
        "$cond": {
            "if": {"$isArray": field_ref},
            "then": {
                "$anyElementTrue": {
                    "$map": {
                        "input": field_ref,
                        "as": "item",
                        "in": _match_one("$$item"),
                    }
                }
            },
            "else": _match_one(field_ref),
        }
    }

    return {
        "$expr": {
            "$and": [
                # Guard: only evaluate if field exists and is non-null
                {"$gt": [field_ref, None]},
                match_condition,
            ]
        }
    }
