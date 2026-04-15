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

    # If multiple fields, combine with $or
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
