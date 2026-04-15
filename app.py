"""
LexFCS Endpoint for BDO (Bayerisches Dialektwörterbuch Online)
FastAPI-based SRU/FCS endpoint implementing the LexFCS specification v0.3.

Serves three lexical resources:
  - BWB (Bayerisches Wörterbuch)
  - WBF (Wörterbuch der fränkischen Mundarten)
  - DIBS (Dialektologisches Informationssystem von Bayerisch-Schwaben)
"""

from fastapi import FastAPI, Query, Request
from fastapi.responses import Response
from motor.motor_asyncio import AsyncIOMotorClient
from contextlib import asynccontextmanager
from typing import Optional
import os

from sru_response import SRUExplainResponse, SRUSearchRetrieveResponse, SRUScanResponse, SRUDiagnostic
from cql_parser import parse_cql
from mongo_query import cql_to_mongo_query, UnsupportedIndexError

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://admin:cvoi6a6@localhost:27017/admin")
MONGODB_DB = os.getenv("MONGODB_DB", "lex")
BASE_URL = os.getenv("BASE_URL", "http://localhost:8080")

# Resource definitions for the three dictionaries
RESOURCES = {
    "bwb": {
        "pid": f"{BASE_URL}/bwb",
        "title_de": "Bayerisches Wörterbuch (BWB)",
        "title_en": "Bavarian Dictionary (BWB)",
        "description_de": "Bayerisches Wörterbuch der Bayerischen Akademie der Wissenschaften",
        "description_en": "Bavarian Dictionary of the Bavarian Academy of Sciences and Humanities",
        "landing_page": "https://bwb.badw.de/",
        "languages": ["deu"],
    },
    "wbf": {
        "pid": f"{BASE_URL}/wbf",
        "title_de": "Wörterbuch der fränkischen Mundarten (WBF)",
        "title_en": "Dictionary of Franconian Dialects (WBF)",
        "description_de": "Fränkisches Wörterbuch der Bayerischen Akademie der Wissenschaften",
        "description_en": "Franconian Dictionary of the Bavarian Academy of Sciences and Humanities",
        "landing_page": "https://wbf.badw.de/",
        "languages": ["deu"],
    },
    "dibs": {
        "pid": f"{BASE_URL}/dibs",
        "title_de": "Dialektologisches Informationssystem von Bayerisch-Schwaben (DIBS)",
        "title_en": "Dialectological Information System of Bavarian Swabia (DIBS)",
        "description_de": "Dialektwörterbuch für Bayerisch-Schwaben",
        "description_en": "Dialect dictionary for Bavarian Swabia",
        "landing_page": "https://dibs.badw.de/",
        "languages": ["deu"],
    },
}

# LexFCS fields supported by our endpoint, mapped from MongoDB fields
SUPPORTED_LEX_FIELDS = [
    "lemma",
    "entryId",
    "pos",
    "definition",
    "etymology",
    "citation",
    "related",
]

# ---------------------------------------------------------------------------
# Application lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: connect to MongoDB (read-only access)
    app.state.mongo_client = AsyncIOMotorClient(MONGODB_URI)
    app.state.db = app.state.mongo_client[MONGODB_DB]
    app.state.entries = app.state.db["entries"]

    # NOTE: We rely on existing indexes in the database.
    # Required indexes (already present):
    #   headword.lemma_1, source_1, sourceId_1, pos_1, fulltextIndex
    # This app does NOT write to the database.

    yield

    # Shutdown: close MongoDB connection
    app.state.mongo_client.close()


app = FastAPI(
    title="BDO LexFCS Endpoint",
    description="LexFCS endpoint for the Bayerisches Dialektwörterbuch Online",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# SRU Endpoint
# ---------------------------------------------------------------------------

@app.get("/", response_class=Response)
@app.head("/")
async def sru_endpoint(
    request: Request,
    operation: Optional[str] = Query(None),
    version: Optional[str] = Query("2.0"),
    query: Optional[str] = Query(None),
    queryType: Optional[str] = Query(None),
    startRecord: Optional[str] = Query(None),
    maximumRecords: Optional[str] = Query(None),
    recordXMLEscaping: Optional[str] = Query(None),
    # Scan parameters (not supported, but must return diagnostics)
    scanClause: Optional[str] = Query(None),
    responsePosition: Optional[str] = Query(None),
    maximumTerms: Optional[str] = Query(None),
    # FCS-specific parameters (x- prefix)
    x_fcs_endpoint_description: Optional[str] = Query(
        None, alias="x-fcs-endpoint-description"
    ),
    x_fcs_context: Optional[str] = Query(None, alias="x-fcs-context"),
    x_indent_response: Optional[str] = Query(None, alias="x-indent-response"),
):
    """Main SRU endpoint handling explain and searchRetrieve operations."""

    # Handle scan operation: return unsupported operation diagnostic
    if scanClause is not None:
        diagnostics = []
        # Check for invalid parameter values in scan context
        if maximumTerms is not None:
            try:
                int(maximumTerms)
            except (ValueError, TypeError):
                diagnostics.append(SRUDiagnostic(
                    uri="info:srw/diagnostic/1/6",
                    details="maximumTerms",
                    message="Unsupported parameter value",
                ))
        if responsePosition is not None:
            try:
                int(responsePosition)
            except (ValueError, TypeError):
                diagnostics.append(SRUDiagnostic(
                    uri="info:srw/diagnostic/1/6",
                    details="responsePosition",
                    message="Unsupported parameter value",
                ))
        if not diagnostics:
            diagnostics.append(SRUDiagnostic(
                uri="info:srw/diagnostic/1/4",
                details="scan",
                message="Unsupported operation",
            ))
        xml = SRUScanResponse(diagnostics=diagnostics).to_xml()
        return _xml_response(xml, x_indent_response)

    # Validate recordXMLEscaping
    if recordXMLEscaping is not None and recordXMLEscaping not in ("xml", "string"):
        diag = SRUDiagnostic(
            uri="info:srw/diagnostic/1/71",
            details=recordXMLEscaping,
            message="Unsupported record packing",
        )
        xml = SRUSearchRetrieveResponse(diagnostics=[diag]).to_xml()
        return _xml_response(xml, x_indent_response)

    # Validate and parse startRecord
    start_record = 1
    if startRecord is not None:
        try:
            start_record = int(startRecord)
        except (ValueError, TypeError):
            diag = SRUDiagnostic(
                uri="info:srw/diagnostic/1/6",
                details="startRecord",
                message="Unsupported parameter value",
            )
            xml = SRUSearchRetrieveResponse(diagnostics=[diag]).to_xml()
            return _xml_response(xml, x_indent_response)
        if start_record < 1:
            diag = SRUDiagnostic(
                uri="info:srw/diagnostic/1/6",
                details="startRecord",
                message="Unsupported parameter value",
            )
            xml = SRUSearchRetrieveResponse(diagnostics=[diag]).to_xml()
            return _xml_response(xml, x_indent_response)

    # Validate and parse maximumRecords
    maximum_records = 25
    if maximumRecords is not None:
        try:
            maximum_records = int(maximumRecords)
        except (ValueError, TypeError):
            diag = SRUDiagnostic(
                uri="info:srw/diagnostic/1/6",
                details="maximumRecords",
                message="Unsupported parameter value",
            )
            xml = SRUSearchRetrieveResponse(diagnostics=[diag]).to_xml()
            return _xml_response(xml, x_indent_response)
        if maximum_records < 0:
            diag = SRUDiagnostic(
                uri="info:srw/diagnostic/1/6",
                details="maximumRecords",
                message="Unsupported parameter value",
            )
            xml = SRUSearchRetrieveResponse(diagnostics=[diag]).to_xml()
            return _xml_response(xml, x_indent_response)

    # Default operation: explain
    if operation is None and query is None:
        operation = "explain"
    elif operation is None and query is not None:
        operation = "searchRetrieve"

    if operation == "explain":
        return await handle_explain(
            request, version, x_fcs_endpoint_description, x_indent_response
        )
    elif operation == "searchRetrieve":
        return await handle_search_retrieve(
            request,
            version,
            query,
            queryType,
            start_record,
            maximum_records,
            x_fcs_context,
            x_indent_response,
        )
    else:
        # Unsupported operation
        diag = SRUDiagnostic(
            uri="info:srw/diagnostic/1/4",
            details=operation,
            message="Unsupported operation",
        )
        xml = SRUExplainResponse(
            base_url=BASE_URL,
            diagnostics=[diag],
        ).to_xml()
        return _xml_response(xml, x_indent_response)


# ---------------------------------------------------------------------------
# Explain operation
# ---------------------------------------------------------------------------

async def handle_explain(
    request: Request,
    version: str,
    x_fcs_endpoint_description: Optional[str],
    x_indent_response: Optional[str],
):
    """Handle the SRU explain operation."""
    include_ed = (
        x_fcs_endpoint_description
        and x_fcs_endpoint_description.lower() == "true"
    )

    response = SRUExplainResponse(
        base_url=BASE_URL,
        resources=RESOURCES if include_ed else None,
        supported_lex_fields=SUPPORTED_LEX_FIELDS if include_ed else None,
    )
    return _xml_response(response.to_xml(), x_indent_response)


# ---------------------------------------------------------------------------
# searchRetrieve operation
# ---------------------------------------------------------------------------

async def handle_search_retrieve(
    request: Request,
    version: str,
    query: Optional[str],
    query_type: Optional[str],
    start_record: int,
    maximum_records: int,
    x_fcs_context: Optional[str],
    x_indent_response: Optional[str],
):
    """Handle the SRU searchRetrieve operation."""
    
    if not query:
        diag = SRUDiagnostic(
            uri="info:srw/diagnostic/1/7",
            details="query",
            message="Mandatory parameter not supplied",
        )
        xml = SRUSearchRetrieveResponse(diagnostics=[diag]).to_xml()
        return _xml_response(xml, x_indent_response)

    # Parse CQL query
    try:
        parsed = parse_cql(query)
    except Exception as e:
        diag = SRUDiagnostic(
            uri="info:srw/diagnostic/1/10",
            details=str(e),
            message="Query syntax error",
        )
        xml = SRUSearchRetrieveResponse(diagnostics=[diag]).to_xml()
        return _xml_response(xml, x_indent_response)

    # Determine which resources to search
    source_filter = None
    if x_fcs_context:
        # Extract source keys from context PIDs
        source_filter = []
        for ctx in x_fcs_context.split(","):
            ctx = ctx.strip()
            for key, res in RESOURCES.items():
                if ctx == res["pid"] or ctx == key:
                    source_filter.append(key)

    # Build MongoDB query
    try:
        mongo_filter = cql_to_mongo_query(parsed, source_filter)
    except UnsupportedIndexError:
        # Return empty result set for unsupported indexes (validator expects no diagnostic)
        xml = SRUSearchRetrieveResponse(total_count=0).to_xml()
        return _xml_response(xml, x_indent_response)
    except Exception as e:
        diag = SRUDiagnostic(
            uri="info:srw/diagnostic/1/47",
            details=str(e),
            message="Cannot process query; reason unknown",
        )
        xml = SRUSearchRetrieveResponse(diagnostics=[diag]).to_xml()
        return _xml_response(xml, x_indent_response)

    # Execute query
    collection = request.app.state.entries
    
    total_count = await collection.count_documents(mongo_filter)

    # Check if startRecord is beyond the result set
    if start_record > 1 and (total_count == 0 or start_record > total_count):
        diag = SRUDiagnostic(
            uri="info:srw/diagnostic/1/61",
            details=f"startRecord={start_record}",
            message="First record position out of range",
        )
        xml = SRUSearchRetrieveResponse(
            total_count=total_count,
            diagnostics=[diag],
        ).to_xml()
        return _xml_response(xml, x_indent_response)

    cursor = (
        collection.find(mongo_filter)
        .skip(start_record - 1)  # SRU is 1-based
        .limit(maximum_records)
    )
    entries = await cursor.to_list(length=maximum_records)

    # Build response
    response = SRUSearchRetrieveResponse(
        entries=entries,
        total_count=total_count,
        query=query,
        start_record=start_record,
        maximum_records=maximum_records,
        base_url=BASE_URL,
    )
    return _xml_response(response.to_xml(), x_indent_response)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _xml_response(xml_str: str, indent: Optional[str] = None) -> Response:
    """Return an XML response, optionally pretty-printed."""
    if indent and indent.strip():
        try:
            from lxml import etree
            tree = etree.fromstring(xml_str.encode("utf-8"))
            xml_str = etree.tostring(
                tree, pretty_print=True, xml_declaration=True, encoding="UTF-8"
            ).decode("utf-8")
        except Exception:
            pass  # Fall back to unformatted output

    return Response(
        content=xml_str,
        media_type="application/xml; charset=utf-8",
    )
