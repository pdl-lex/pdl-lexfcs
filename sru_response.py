"""
SRU/FCS XML response generation for the LexFCS endpoint.

Generates XML responses conforming to:
  - SRU 2.0 (explain + searchRetrieve)
  - CLARIN-FCS Core 2.0 (Resource, DataView)
  - LexFCS v0.3 (Lexical Data View, Endpoint Description extensions)
"""

from dataclasses import dataclass
from typing import Optional
from xml.sax.saxutils import escape as xml_escape


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
        source_id = entry.get("sourceId", entry.get("_id", ""))
        source = entry.get("source", "")
        ref_url = self.base_url + "/" + source + "/" + source_id

        parts = [
            "    <sru:record>",
            "      <sru:recordSchema>http://clarin.eu/fcs/resource</sru:recordSchema>",
            "      <sru:recordXMLEscaping>xml</sru:recordXMLEscaping>",
            "      <sru:recordData>",
            '        <fcs:Resource xmlns:fcs="' + NS["fcs"]
            + '" ref="' + xml_escape(ref_url) + '">',
            self._build_hits_dataview(entry),
            self._build_lex_dataview(entry),
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

    def _build_lex_dataview(self, entry: dict) -> str:
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

        # definitions
        senses = entry.get("flatSenses", entry.get("sense", []))
        defs_added = False
        for sense in senses:
            d = sense.get("def", "")
            if d:
                if not defs_added:
                    parts.append('              <lex:Field type="definition">')
                    defs_added = True
                parts.append(
                    '                <lex:Value xml:lang="' + lang_639 + '">'
                    + xml_escape(d) + "</lex:Value>"
                )
        if defs_added:
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

        # citations
        cits = []
        for sense in senses:
            for cit in sense.get("cit", []):
                text = cit.get("text", "").strip()
                if text and cit.get("type") == "example":
                    cits.append(text)
        if cits:
            parts.append('              <lex:Field type="citation">')
            for c in cits[:10]:
                parts.append(
                    '                <lex:Value xml:lang="' + lang_639
                    + '" type="example">'
                    + xml_escape(c) + "</lex:Value>"
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
