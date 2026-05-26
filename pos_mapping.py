"""
POS tag mapping: BDO source values (German) ↔ output values (English/UD).

Source values are the German POS labels stored in MongoDB (field "pos").
Output values are used in the LexFCS XML response and for CQL search.

The output values follow Universal Dependencies (UD) POS tags where applicable:
  https://universaldependencies.org/u/pos/
Non-UD values (CONJ, MWE, prefix, suffix) are used for categories without
a dedicated UD tag.
"""

UD_POS_VOCAB_REF = "https://universaldependencies.org/u/pos"

# Forward map: German source value → English/UD output value
DE_TO_EN: dict[str, str] = {
    "Adjektiv":        "ADJ",
    "Adverb":          "ADV",
    "Determinativ":    "DET",
    "Interjektion":    "INTJ",
    "Konjunktor":      "CONJ",
    "Mehrwortausdruck": "MWE",
    "Partikel":        "PART",
    "Possessivum":     "DET",
    "Pronomen":        "PRON",
    "Präfix":          "prefix",
    "Präposition":     "ADP",
    "Substantiv":      "NOUN",
    "Suffix":          "suffix",
    "Verb":            "VERB",
    "Zahlwort":        "NUM",
    "Sonstige":        "X",
}

# vocabValueRef URIs for proper UD tags (no entry for CONJ, MWE, prefix, suffix)
VOCAB_VALUE_REF: dict[str, str] = {
    "ADJ":  "https://universaldependencies.org/u/pos/ADJ",
    "ADV":  "https://universaldependencies.org/u/pos/ADV",
    "ADP":  "https://universaldependencies.org/u/pos/ADP",
    "DET":  "https://universaldependencies.org/u/pos/DET",
    "INTJ": "https://universaldependencies.org/u/pos/INTJ",
    "NOUN": "https://universaldependencies.org/u/pos/NOUN",
    "NUM":  "https://universaldependencies.org/u/pos/NUM",
    "PART": "https://universaldependencies.org/u/pos/PART",
    "PRON": "https://universaldependencies.org/u/pos/PRON",
    "VERB": "https://universaldependencies.org/u/pos/VERB",
    "X":    "https://universaldependencies.org/u/pos/X",
}

# Reverse map: English/UD value (uppercased) → list of German source values.
# Used in CQL search to translate incoming English queries to stored German values.
# Note: DET maps to two German values (Determinativ, Possessivum).
EN_TO_DE: dict[str, list[str]] = {}
for _de, _en in DE_TO_EN.items():
    EN_TO_DE.setdefault(_en.upper(), []).append(_de)
