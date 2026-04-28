"""
SRU/FCS XML response generation for the LexFCS endpoint.

Generates XML responses conforming to:
  - SRU 2.0 (explain + searchRetrieve)
  - CLARIN-FCS Core 2.0 (Resource, DataView)
  - LexFCS v0.3 (Lexical Data View, Endpoint Description extensions)
"""

from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode
from xml.sax.saxutils import escape as xml_escape


# ---------------------------------------------------------------------------
# BDO search URL builder
# ---------------------------------------------------------------------------

BDO_SEARCH_BASE = "https://bdo.badw.de/suche"


def build_bdo_ref_url(source: str, lemma: str) -> str:
    """Build a BDO search URL for a given dictionary source and lemma.

    BDO has no permalinks, but a parameterized search URL reproduces the
    relevant entry. Brackets in parameter names are percent-encoded.
    """
    params = [
        (f"options[dict][{source}]", "1"),
        ("stichwort", ""),
        ("options[case]", "1"),
        ("options[exact]", "1"),
        ("lemma", lemma),
        ("bedeutung", ""),
        ("beleg", ""),
        ("wortfamilie", ""),
        ("etymologie", ""),
        ("options[createPermalink]", "0"),
    ]
    return BDO_SEARCH_BASE + "?" + urlencode(params)


def _normalize_ws(s: str) -> str:
    return " ".join(s.split())


def _slice_out(text: str, spans: list[tuple[int, int]]) -> str:
    """Return ``text`` with all ``(start, end)`` ranges removed."""
    if not spans:
        return text
    pieces: list[str] = []
    pos = 0
    for start, end in sorted(spans):
        if start > pos:
            pieces.append(text[pos:start])
        pos = max(pos, end)
    if pos < len(text):
        pieces.append(text[pos:])
    return "".join(pieces)


def extract_citation_parts(cit: dict) -> dict:
    """Decompose a citation's standoff annotations.

    Returns a dict with:
      - ``italic_text``: italic-labeled span(s) concatenated, or ``None``
      - ``gloss_text``: text outside both italic and bibref spans, or ``None``
        (only meaningful when ``italic_text`` is set; this is the gloss/paraphrase
        that LexFCS expects to be linked back to a definition via ``@idRefs``)
      - ``full_cleaned``: full text minus bibref spans (fallback when there's
        no italic span)
      - ``sources``: bibref names for use in ``@source``
    """
    text = cit.get("text", "") or ""
    annotations = cit.get("annotations", [])

    italic_spans: list[tuple[int, int]] = []
    bibref_spans: list[tuple[int, int]] = []
    sources: list[str] = []
    for a in annotations:
        start = a.get("start", 0)
        end = a.get("end", 0)
        atype = a.get("type")
        if atype == "text" and "italic" in (a.get("labels") or []):
            italic_spans.append((start, end))
        elif atype == "bibref":
            bibref_spans.append((start, end))
            src = (a.get("text", "") or "").strip()
            if src:
                sources.append(src)

    italic_text = None
    if italic_spans:
        joined = " ".join(text[s:e] for s, e in sorted(italic_spans))
        italic_text = _normalize_ws(joined) or None

    gloss_text = None
    if italic_text is not None:
        gloss_raw = _slice_out(text, italic_spans + bibref_spans)
        gloss_text = _normalize_ws(gloss_raw) or None

    full_cleaned = _normalize_ws(_slice_out(text, bibref_spans)) or None

    return {
        "italic_text": italic_text,
        "gloss_text": gloss_text,
        "full_cleaned": full_cleaned,
        "sources": sources,
    }


# ---------------------------------------------------------------------------
# XML Namespaces
# ---------------------------------------------------------------------------

NS = {
    "sru": "http://docs.oasis-open.org/ns/search-ws/sruResponse",
    "diag": "http://docs.oasis-open.org/ns/search-ws/diagnostic",
    "ed": "http://clarin.eu/fcs/endpoint-description",
    "fcs": "http://clarin.eu/fcs/resource",
    "hits": "http://clarin.eu/fcs/dataview/hits",
    "lex": "http://clarin.eu/fcs/dataview/lex",
}

# Workaround: Python interprets \n in "</name>" inside f-strings as newline.
# We build the closing tag via concatenation.
_CLOSE_NAME = "</" + "name>"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SRUDiagnostic:
    uri: str
    details: str = ""
    message: str = ""


# ---------------------------------------------------------------------------
# Explain Response
# ---------------------------------------------------------------------------

class SRUExplainResponse:
    """Builds the SRU explain response XML."""

    def __init__(
        self,
        base_url: str = "",
        resources: Optional[dict] = None,
        supported_lex_fields: Optional[list] = None,
        diagnostics: Optional[list] = None,
    ):
        self.base_url = base_url
        self.resources = resources
        self.supported_lex_fields = supported_lex_fields
        self.diagnostics = diagnostics or []

    def to_xml(self) -> str:
        parts = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<sru:explainResponse xmlns:sru="' + NS["sru"] + '">',
            "  <sru:version>2.0</sru:version>",
            "  <sru:record>",
            "    <sru:recordSchema>http://explain.z3950.org/dtd/2.0/</sru:recordSchema>",
            "    <sru:recordXMLEscaping>xml</sru:recordXMLEscaping>",
            "    <sru:recordData>",
            self._build_explain_record(),
            "    </sru:recordData>",
            "  </sru:record>",
        ]

        if self.resources:
            parts.append("  <sru:extraResponseData>")
            parts.append(self._build_endpoint_description())
            parts.append("  </sru:extraResponseData>")

        if self.diagnostics:
            parts.append("  <sru:diagnostics>")
            for diag in self.diagnostics:
                parts.append(self._build_diagnostic(diag))
            parts.append("  </sru:diagnostics>")

        parts.append("</sru:explainResponse>")
        return "\n".join(parts)

    def _build_explain_record(self) -> str:
        indexes = [
            ("Lemma", "lemma"),
            ("Definition", "definition"),
            ("Part of Speech", "pos"),
            ("Entry ID", "entryId"),
            ("Etymology", "etymology"),
            ("Citation", "citation"),
            ("Related", "related"),
        ]
        index_lines = []
        for title, idx in indexes:
            index_lines.append("          <index>")
            index_lines.append("            <title>" + title + "</title>")
            index_lines.append(
                '            <map><name set="lexres">'
                + idx + _CLOSE_NAME + "</map>"
            )
            index_lines.append("          </index>")
        index_xml = "\n".join(index_lines)

        host = xml_escape(self.base_url)
        lines = [
            '      <explain xmlns="http://explain.z3950.org/dtd/2.0/">',
            '        <serverInfo protocol="SRU" version="2.0" transport="http">',
            "          <host>" + host + "</host>",
            "          <port>80</port>",
            "          <database>bdo</database>",
            "        </serverInfo>",
            "        <databaseInfo>",
            '          <title lang="de" primary="true">'
            + "Bayerisches Dialektw\u00f6rterbuch Online (BDO)</title>",
            '          <title lang="en">'
            + "Bavarian Dialect Dictionary Online (BDO)</title>",
            '          <description lang="de" primary="true">',
            "            Bayerische Dialektw\u00f6rterb\u00fccher: BWB, WBF und DIBS",
            "          </description>",
            '          <description lang="en">',
            "            Bavarian dialect dictionaries: BWB, WBF and DIBS",
            "          </description>",
            "        </databaseInfo>",
            "        <indexInfo>",
            '          <set name="lexres" identifier="http://text-plus.org/cql/lexres/1.0/">',
            "            <title>LexFCS CQL Context Set</title>",
            "          </set>",
            index_xml,
            "        </indexInfo>",
            "        <schemaInfo>",
            '          <schema identifier="http://clarin.eu/fcs/resource" name="fcs">',
            "            <title>CLARIN FCS</title>",
            "          </schema>",
            "        </schemaInfo>",
            "      </explain>",
        ]
        return "\n".join(lines)

    def _build_endpoint_description(self) -> str:
        parts = [
            '    <ed:EndpointDescription xmlns:ed="' + NS["ed"] + '" version="2">',
            "      <ed:Capabilities>",
            "        <ed:Capability>http://clarin.eu/fcs/capability/basic-search</ed:Capability>",
            "        <ed:Capability>http://clarin.eu/fcs/capability/lex-search</ed:Capability>",
            "      </ed:Capabilities>",
            "      <ed:SupportedDataViews>",
            '        <ed:SupportedDataView id="hits" delivery-policy="send-by-default">'
            + "application/x-clarin-fcs-hits+xml</ed:SupportedDataView>",
            '        <ed:SupportedDataView id="lex" delivery-policy="send-by-default">'
            + "application/x-clarin-fcs-lex+xml</ed:SupportedDataView>",
            "      </ed:SupportedDataViews>",
        ]

        if self.supported_lex_fields:
            parts.append("      <ed:SupportedLexFields>")
            for f in self.supported_lex_fields:
                parts.append(
                    '        <ed:SupportedLexField id="' + f + '">'
                    + f + "</ed:SupportedLexField>"
                )
            parts.append("      </ed:SupportedLexFields>")

        parts.append("      <ed:Resources>")
        for key, res in self.resources.items():
            lex_field_refs = " ".join(self.supported_lex_fields or [])
            parts.append(
                '        <ed:Resource pid="' + xml_escape(res["pid"]) + '">'
            )
            parts.append(
                '          <ed:Title xml:lang="de">'
                + xml_escape(res["title_de"]) + "</ed:Title>"
            )
            parts.append(
                '          <ed:Title xml:lang="en">'
                + xml_escape(res["title_en"]) + "</ed:Title>"
            )
            parts.append(
                '          <ed:Description xml:lang="de">'
                + xml_escape(res["description_de"]) + "</ed:Description>"
            )
            parts.append(
                '          <ed:Description xml:lang="en">'
                + xml_escape(res["description_en"]) + "</ed:Description>"
            )
            parts.append(
                "          <ed:LandingPageURI>"
                + xml_escape(res["landing_page"]) + "</ed:LandingPageURI>"
            )
            parts.append("          <ed:Languages>")
            for lang in res["languages"]:
                parts.append(
                    "            <ed:Language>" + lang + "</ed:Language>"
                )
            parts.append("          </ed:Languages>")
            parts.append('          <ed:AvailableDataViews ref="hits lex" />')
            parts.append(
                '          <ed:AvailableLexFields ref="'
                + lex_field_refs + '" />'
            )
            parts.append("        </ed:Resource>")
        parts.append("      </ed:Resources>")
        parts.append("    </ed:EndpointDescription>")
        return "\n".join(parts)

    def _build_diagnostic(self, diag: SRUDiagnostic) -> str:
        lines = [
            '    <diag:diagnostic xmlns:diag="' + NS["diag"] + '">',
            "      <diag:uri>" + xml_escape(diag.uri) + "</diag:uri>",
            "      <diag:details>" + xml_escape(diag.details) + "</diag:details>",
            "      <diag:message>" + xml_escape(diag.message) + "</diag:message>",
            "    </diag:diagnostic>",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Scan Response (unsupported, diagnostics only)
# ---------------------------------------------------------------------------

class SRUScanResponse:
    """Builds a minimal SRU scanResponse XML (for returning diagnostics)."""

    def __init__(self, diagnostics: Optional[list] = None):
        self.diagnostics = diagnostics or []

    def to_xml(self) -> str:
        scan_ns = "http://docs.oasis-open.org/ns/search-ws/scan"
        parts = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<scan:scanResponse xmlns:scan="' + scan_ns + '">',
            "  <scan:version>2.0</scan:version>",
        ]

        if self.diagnostics:
            parts.append("  <scan:diagnostics>")
            for diag in self.diagnostics:
                parts.append(
                    '    <diag:diagnostic xmlns:diag="' + NS["diag"] + '">'
                )
                parts.append(
                    "      <diag:uri>" + xml_escape(diag.uri) + "</diag:uri>"
                )
                parts.append(
                    "      <diag:details>"
                    + xml_escape(diag.details) + "</diag:details>"
                )
                parts.append(
                    "      <diag:message>"
                    + xml_escape(diag.message) + "</diag:message>"
                )
                parts.append("    </diag:diagnostic>")
            parts.append("  </scan:diagnostics>")

        parts.append("</scan:scanResponse>")
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# searchRetrieve Response
# ---------------------------------------------------------------------------

class SRUSearchRetrieveResponse:
    """Builds the SRU searchRetrieve response XML."""

    def __init__(
        self,
        entries: Optional[list] = None,
        total_count: int = 0,
        query: str = "",
        start_record: int = 1,
        maximum_records: int = 25,
        base_url: str = "",
        diagnostics: Optional[list] = None,
    ):
        self.entries = entries or []
        self.total_count = total_count
        self.query = query
        self.start_record = start_record
        self.maximum_records = maximum_records
        self.base_url = base_url
        self.diagnostics = diagnostics or []

    def to_xml(self) -> str:
        parts = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<sru:searchRetrieveResponse xmlns:sru="' + NS["sru"] + '">',
            "  <sru:version>2.0</sru:version>",
            "  <sru:numberOfRecords>"
            + str(self.total_count) + "</sru:numberOfRecords>",
        ]

        if self.entries:
            parts.append("  <sru:records>")
            for i, entry in enumerate(self.entries):
                record_pos = self.start_record + i
                parts.append(self._build_record(entry, record_pos))
            parts.append("  </sru:records>")

        if self.diagnostics:
            parts.append("  <sru:diagnostics>")
            for diag in self.diagnostics:
                parts.append(
                    '    <diag:diagnostic xmlns:diag="' + NS["diag"] + '">'
                )
                parts.append(
                    "      <diag:uri>" + xml_escape(diag.uri) + "</diag:uri>"
                )
                parts.append(
                    "      <diag:details>"
                    + xml_escape(diag.details) + "</diag:details>"
                )
                parts.append(
                    "      <diag:message>"
                    + xml_escape(diag.message) + "</diag:message>"
                )
                parts.append("    </diag:diagnostic>")
            parts.append("  </sru:diagnostics>")

        parts.append("</sru:searchRetrieveResponse>")
        return "\n".join(parts)

    def _build_record(self, entry: dict, position: int) -> str:
        source = entry.get("source", "")
        lemma = entry.get("headword", {}).get("lemma", "")
        ref_url = build_bdo_ref_url(source, lemma)

        parts = [
            "    <sru:record>",
            "      <sru:recordSchema>http://clarin.eu/fcs/resource</sru:recordSchema>",
            "      <sru:recordXMLEscaping>xml</sru:recordXMLEscaping>",
            "      <sru:recordData>",
            '        <fcs:Resource xmlns:fcs="' + NS["fcs"]
            + '" ref="' + xml_escape(ref_url) + '">',
            self._build_hits_dataview(entry),
            self._build_lex_dataview(entry, position),
            "        </fcs:Resource>",
            "      </sru:recordData>",
            "      <sru:recordPosition>" + str(position) + "</sru:recordPosition>",
            "    </sru:record>",
        ]
        return "\n".join(parts)

    def _build_hits_dataview(self, entry: dict) -> str:
        """Build the mandatory Generic Hits Data View."""
        lemma = entry.get("headword", {}).get("lemma", "")
        defs = []
        for s in entry.get("flatSenses", entry.get("sense", [])):
            d = s.get("def", "")
            if d:
                defs.append(d)
        hit_text = "; ".join(defs[:3]) if defs else ""

        lines = [
            '          <fcs:DataView type="application/x-clarin-fcs-hits+xml">',
            '            <hits:Result xmlns:hits="' + NS["hits"]
            + '"><hits:Hit>' + xml_escape(lemma)
            + "</hits:Hit> " + xml_escape(hit_text) + "</hits:Result>",
            "          </fcs:DataView>",
        ]
        return "\n".join(lines)

    def _build_lex_dataview(self, entry: dict, position: int = 1) -> str:
        """Build the LexFCS Lexical Data View."""
        lang = entry.get("xml:lang", "DE").lower()
        lang_map = {"de": "deu", "en": "eng"}
        lang_639 = lang_map.get(lang, lang)

        parts = [
            '          <fcs:DataView type="application/x-clarin-fcs-lex+xml">',
            '            <lex:Entry xmlns:lex="' + NS["lex"]
            + '" xml:lang="' + lang_639 + '">',
        ]

        # lemma (mandatory)
        lemma = entry.get("headword", {}).get("lemma", "")
        parts.append('              <lex:Field type="lemma">')
        parts.append(
            '                <lex:Value xml:lang="' + lang_639 + '">'
            + xml_escape(lemma) + "</lex:Value>"
        )
        for variant in entry.get("variants", []):
            if variant:
                parts.append(
                    '                <lex:Value xml:lang="' + lang_639 + '">'
                    + xml_escape(variant) + "</lex:Value>"
                )
        parts.append("              </lex:Field>")

        # entryId
        source_id = entry.get("sourceId", "")
        if source_id:
            parts.append('              <lex:Field type="entryId">')
            parts.append(
                "                <lex:Value>"
                + xml_escape(source_id) + "</lex:Value>"
            )
            parts.append("              </lex:Field>")

        # pos
        pos = entry.get("pos", "")
        if pos:
            parts.append('              <lex:Field type="pos">')
            parts.append(
                "                <lex:Value>"
                + xml_escape(pos) + "</lex:Value>"
            )
            parts.append("              </lex:Field>")

        # senses, definitions, citations — built together because LexFCS v0.3
        # links citations back to their definitions via @xml:id / @idRefs
        # (see best-practices.adoc, "Connecting Values within Fields …")
        senses = entry.get("flatSenses", entry.get("sense", []))
        sense_def_ids: dict[int, str] = {}
        for si, sense in enumerate(senses):
            if sense.get("def"):
                sense_def_ids[si] = f"e{position}-d{si + 1}"

        # Pre-extract citation parts so we can decide which need a gloss sub-def
        cit_parts: list[list[dict | None]] = []
        for sense in senses:
            row: list[dict | None] = []
            for cit in sense.get("cit", []):
                row.append(
                    extract_citation_parts(cit)
                    if cit.get("type") == "example"
                    else None
                )
            cit_parts.append(row)

        # Limit total citations across senses (keeps response size sane)
        kept_cits: list[tuple[int, int, dict]] = []
        for si, row in enumerate(cit_parts):
            for ci, cp in enumerate(row):
                if cp is None:
                    continue
                if cp["italic_text"] or cp["full_cleaned"]:
                    kept_cits.append((si, ci, cp))
                    if len(kept_cits) >= 10:
                        break
            if len(kept_cits) >= 10:
                break

        # Glosses are only emitted for kept citations whose sense has a def
        # AND whose gloss is not just a duplicate of that def
        gloss_ids: dict[tuple[int, int], str] = {}
        for si, ci, cp in kept_cits:
            if (
                si in sense_def_ids
                and cp["italic_text"]
                and cp["gloss_text"]
                and _normalize_ws(cp["gloss_text"])
                != _normalize_ws(senses[si].get("def", ""))
            ):
                gloss_ids[(si, ci)] = f"{sense_def_ids[si]}-g{ci + 1}"

        # Emit definition field (main defs + gloss sub-values)
        if sense_def_ids:
            parts.append('              <lex:Field type="definition">')
            for si, sense in enumerate(senses):
                def_id = sense_def_ids.get(si)
                if not def_id:
                    continue
                parts.append(
                    '                <lex:Value xml:id="' + def_id
                    + '" xml:lang="' + lang_639 + '">'
                    + xml_escape(sense["def"]) + "</lex:Value>"
                )
                for (gsi, gci), gid in gloss_ids.items():
                    if gsi != si:
                        continue
                    gloss = cit_parts[gsi][gci]["gloss_text"]
                    parts.append(
                        '                <lex:Value xml:id="' + gid
                        + '" idRefs="' + def_id
                        + '" xml:lang="' + lang_639 + '">'
                        + xml_escape(gloss) + "</lex:Value>"
                    )
            parts.append("              </lex:Field>")

        # etymology
        etym = entry.get("etym")
        if etym and etym.get("text"):
            parts.append('              <lex:Field type="etymology">')
            parts.append(
                '                <lex:Value xml:lang="' + lang_639 + '">'
                + xml_escape(etym["text"]) + "</lex:Value>"
            )
            parts.append("              </lex:Field>")

        # Emit citation field (italic span if present, else full cleaned text;
        # @idRefs points to the gloss sub-def if any, else the main def)
        if kept_cits:
            parts.append('              <lex:Field type="citation">')
            for si, ci, cp in kept_cits:
                cit_text = cp["italic_text"] or cp["full_cleaned"]
                if not cit_text:
                    continue
                target_id = gloss_ids.get((si, ci)) or sense_def_ids.get(si)
                attrs = ' xml:lang="' + lang_639 + '" type="example"'
                if target_id:
                    attrs += ' idRefs="' + target_id + '"'
                if cp["sources"]:
                    attrs += (
                        ' source="'
                        + xml_escape("; ".join(cp["sources"])) + '"'
                    )
                parts.append(
                    '                <lex:Value' + attrs + ">"
                    + xml_escape(cit_text) + "</lex:Value>"
                )
            parts.append("              </lex:Field>")

        # related (compounds + derivations)
        related = []
        for comp in entry.get("compounds", []):
            t = comp.get("text", "").strip()
            if t:
                related.append(t)
        for deriv in entry.get("derivations", []):
            t = deriv.get("text", "").strip()
            if t:
                related.append(t)
        if related:
            parts.append('              <lex:Field type="related">')
            for r in related:
                parts.append(
                    '                <lex:Value xml:lang="' + lang_639 + '">'
                    + xml_escape(r) + "</lex:Value>"
                )
            parts.append("              </lex:Field>")

        parts.append("            </lex:Entry>")
        parts.append("          </fcs:DataView>")
        return "\n".join(parts)
