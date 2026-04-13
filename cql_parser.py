"""
CQL/LexCQL parser for the LexFCS endpoint.

Parses Contextual Query Language (CQL) queries into an intermediate
representation that can be translated to MongoDB queries.

Supports:
  - Term-only queries: car, "car wash"
  - Index/relation/term: lemma = "Haus"
  - Boolean operators: AND, OR, NOT
  - Relations: = (flexible), == (exact)
  - Relation modifiers: /contains, /startswith, /endswith, /fullmatch
"""

from dataclasses import dataclass
from typing import Optional, Union
import re


# ---------------------------------------------------------------------------
# AST Node types
# ---------------------------------------------------------------------------

@dataclass
class SearchClause:
    """A single search clause: index relation term"""
    index: str        # e.g. "lemma", "definition", "pos"
    relation: str     # e.g. "=", "==", "is"
    modifiers: list   # e.g. ["contains", "startswith"]
    term: str         # the search term


@dataclass
class BooleanQuery:
    """Two clauses joined by a boolean operator"""
    left: Union["SearchClause", "BooleanQuery"]
    operator: str     # "AND", "OR", "NOT"
    right: Union["SearchClause", "BooleanQuery"]


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------

# Token patterns
TOKEN_PATTERNS = [
    ("STRING", r'"(?:[^"\\]|\\.)*"'),   # Quoted string
    ("BOOLEAN", r'\b(AND|OR|NOT)\b'),   # Boolean operators (case-sensitive)
    ("MODIFIER", r'/[a-zA-Z]+'),        # Relation modifiers like /contains
    ("RELATION", r'==|='),              # Relations
    ("LPAREN", r'\('),
    ("RPAREN", r'\)'),
    ("WORD", r'[^\s()=/"]+'),           # Unquoted word
    ("WS", r'\s+'),                     # Whitespace (skip)
]

TOKEN_RE = re.compile(
    "|".join(f"(?P<{name}>{pattern})" for name, pattern in TOKEN_PATTERNS)
)


def tokenize(query: str) -> list:
    """Tokenize a CQL query string."""
    tokens = []
    for match in TOKEN_RE.finditer(query):
        kind = match.lastgroup
        value = match.group()
        if kind == "WS":
            continue
        if kind == "STRING":
            # Remove surrounding quotes and unescape
            value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        tokens.append((kind, value))
    return tokens


# ---------------------------------------------------------------------------
# Parser (recursive descent)
# ---------------------------------------------------------------------------

class CQLParser:
    """Recursive descent parser for CQL queries."""

    def __init__(self, tokens: list):
        self.tokens = tokens
        self.pos = 0

    def peek(self) -> Optional[tuple]:
        if self.pos < len(self.tokens):
            return self.tokens[self.pos]
        return None

    def consume(self) -> tuple:
        token = self.tokens[self.pos]
        self.pos += 1
        return token

    def expect(self, kind: str) -> tuple:
        token = self.peek()
        if token is None or token[0] != kind:
            raise ValueError(
                f"Expected {kind}, got {token[0] if token else 'EOF'}"
            )
        return self.consume()

    def parse(self) -> Union[SearchClause, BooleanQuery]:
        result = self.parse_boolean()
        if self.pos < len(self.tokens):
            raise ValueError(
                f"Unexpected token: {self.tokens[self.pos][1]}"
            )
        return result

    def parse_boolean(self) -> Union[SearchClause, BooleanQuery]:
        """Parse boolean expressions: clause (AND|OR|NOT clause)*"""
        left = self.parse_clause()

        while self.peek() and self.peek()[0] == "BOOLEAN":
            _, op = self.consume()
            right = self.parse_clause()
            left = BooleanQuery(left=left, operator=op.upper(), right=right)

        return left

    def parse_clause(self) -> Union[SearchClause, BooleanQuery]:
        """Parse a single clause or parenthesized expression."""
        token = self.peek()

        if token is None:
            raise ValueError("Unexpected end of query")

        # Parenthesized expression
        if token[0] == "LPAREN":
            self.consume()
            result = self.parse_boolean()
            self.expect("RPAREN")
            return result

        # Could be: term, or index relation term
        # Look ahead to determine which
        if token[0] in ("WORD", "STRING"):
            first_value = self.consume()[1]

            # Check if next token is a relation
            next_token = self.peek()
            if next_token and next_token[0] == "RELATION":
                # This is index relation term
                _, relation = self.consume()

                # Check for relation modifiers
                modifiers = []
                while self.peek() and self.peek()[0] == "MODIFIER":
                    _, mod = self.consume()
                    modifiers.append(mod[1:])  # Strip leading /

                # Get the term
                term_token = self.peek()
                if term_token is None:
                    raise ValueError("Expected search term after relation")
                _, term = self.consume()

                return SearchClause(
                    index=first_value,
                    relation=relation,
                    modifiers=modifiers,
                    term=term,
                )
            else:
                # Term-only query — default to lemma search
                return SearchClause(
                    index="lemma",
                    relation="=",
                    modifiers=[],
                    term=first_value,
                )

        raise ValueError(f"Unexpected token: {token[1]}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_cql(query: str) -> Union[SearchClause, BooleanQuery]:
    """Parse a CQL query string into an AST."""
    query = query.strip()
    if not query:
        raise ValueError("Empty query")

    tokens = tokenize(query)
    if not tokens:
        raise ValueError("No valid tokens in query")

    parser = CQLParser(tokens)
    return parser.parse()
