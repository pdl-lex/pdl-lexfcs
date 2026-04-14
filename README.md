# LexFCS Endpoint für das BDO

LexFCS-Endpoint für das **Bayerische Dialektwörterbuch Online (BDO)**.
Implementiert die [LexFCS-Spezifikation v0.3](https://github.com/textplus/LexFCS)
als Python/FastAPI-Anwendung mit MongoDB als Datenquelle.

## Ressourcen

Der Endpoint stellt drei Wörterbücher als durchsuchbare Ressourcen bereit:

| Kürzel | Name | Einträge |
|--------|------|----------|
| `bwb`  | Bayerisches Wörterbuch (BWB) | ~34.000 |
| `wbf`  | Wörterbuch der fränkischen Mundarten (WBF) | ~69.000 |
| `dibs` | Dialektologisches Informationssystem von Bayerisch-Schwaben (DIBS) | ~41.500 |

## Projektstruktur

```
lexfcs-endpoint/
├── app.py              # FastAPI-Anwendung, SRU-Routing, MongoDB-Anbindung
├── sru_response.py     # XML-Generierung (Explain + SearchRetrieve)
├── cql_parser.py       # CQL/LexCQL-Parser (rekursiver Abstieg)
├── mongo_query.py      # CQL-AST → MongoDB-Query-Übersetzung
├── pyproject.toml      # Projektdefinition und Dependencies
├── Dockerfile
├── docker-compose.yml  # Verbindung zum bestehenden MongoDB-Container
├── .env.template       # Konfigurationsvorlage
└── README.md
```

### Modulübersicht

**`app.py`** — Einstiegspunkt. Definiert die SRU-Parameter, routet Anfragen
an `handle_explain` oder `handle_search_retrieve`, verwaltet die
MongoDB-Verbindung. Die App greift ausschließlich lesend auf die Datenbank zu.

**`sru_response.py`** — Baut die XML-Antworten gemäß SRU 2.0, FCS Core 2.0
und LexFCS v0.3. Zwei Klassen: `SRUExplainResponse` für die
Selbstbeschreibung, `SRUSearchRetrieveResponse` für Suchergebnisse mit
Hits Data View und Lexical Data View.

**`cql_parser.py`** — Zerlegt CQL-Query-Strings in einen Syntaxbaum (AST).
Unterstützt Term-Queries, Index-Queries, Boolean-Operatoren und
Relation-Modifikatoren.

**`mongo_query.py`** — Übersetzt den CQL-AST in MongoDB-Filterdokumente.
Definiert das Mapping von LexFCS-Feldnamen auf MongoDB-Feldpfade.

## Deployment

### Voraussetzungen

- Docker und Docker Compose
- Zugang zum bestehenden MongoDB-Container

### Setup

```bash
# .env anlegen und anpassen
cp .env.template .env
nano .env

# Container bauen und starten
docker compose up -d

# Logs prüfen
docker compose logs -f
```

### Konfiguration (.env)

```
MONGO_PASSWORD=...                          # MongoDB-Passwort
BASE_URL=http://localhost:8080              # Öffentliche URL des Endpoints
DOCKER_NETWORK=...                          # Docker-Netzwerk der MongoDB
```

## SRU-Operationen

### Explain — Selbstbeschreibung

Basis:

    GET /

Mit Endpoint Description (für FCS-Clients):

    GET /?x-fcs-endpoint-description=true

Formatierte Ausgabe (zum Debuggen):

    GET /?x-fcs-endpoint-description=true&x-indent-response=2

### SearchRetrieve — Suche

    GET /?query=<LexCQL-Query>[&Parameter...]

Parameter:

| Parameter | Default | Beschreibung |
|-----------|---------|--------------|
| `query` | — | LexCQL-Suchausdruck (Pflicht) |
| `operation` | auto | `explain` oder `searchRetrieve` |
| `startRecord` | 1 | Erster Treffer (1-basiert, für Paging) |
| `maximumRecords` | 25 | Maximale Trefferzahl pro Seite |
| `x-fcs-context` | alle | Einschränkung auf Ressource(n), z.B. `bwb` |
| `x-indent-response` | — | Beliebiger Wert aktiviert Pretty-Print |

## LexCQL-Suchoptionen

### Einfache Suche (Term-Query)

Sucht im Lemma-Feld:

    GET /?query=Haus
    GET /?query="Haus"

### Feldsuche (Index-Query)

    GET /?query=lemma = "Haus"
    GET /?query=definition = "Getreide"
    GET /?query=pos = "Verb"

Verfügbare Felder:

| LexCQL-Feld | Beschreibung | MongoDB-Feld |
|-------------|--------------|--------------|
| `lemma` | Stichwort (Default) | `headword.lemma`, `variants` |
| `definition` / `def` | Bedeutungserklärung | `flatSenses.def` |
| `pos` | Wortart | `pos` |
| `entryId` | Eintrags-ID | `sourceId` |
| `etymology` | Herkunft | `etym.text` |
| `citation` | Belegstelle | `flatSenses.cit.text` |
| `related` | Komposita, Ableitungen | `compounds.text`, `derivations.text` |

### Relationen

| Relation | Beschreibung |
|----------|--------------|
| `=` | Flexibler Match (case-insensitive, bei Definitionen Teilstring) |
| `==` | Exakte Gleichheit |

### Relation-Modifikatoren

    GET /?query=lemma =/startswith "Haus"
    GET /?query=definition =/contains "Brot"

| Modifikator | Beschreibung |
|-------------|--------------|
| `/contains` | Teilstring-Suche |
| `/startswith` | Präfix-Suche |
| `/endswith` | Suffix-Suche |
| `/fullmatch` | Exakter Match (case-insensitive) |

### Boolean-Operatoren

    GET /?query=pos = "Verb" AND definition = "essen"
    GET /?query=lemma = "Haus" OR lemma = "Hütte"
    GET /?query=pos = "Substantiv" AND NOT definition = "Tier"

Klammern werden unterstützt:

    GET /?query=(pos = "Verb" OR pos = "Substantiv") AND definition = "Wasser"

### Einschränkung auf Wörterbuch

    GET /?query=Haus&x-fcs-context=bwb
    GET /?query=Haus&x-fcs-context=bwb,wbf

## Beispielabfragen

```bash
# Explain mit Endpoint Description
curl 'http://localhost:8080/?x-fcs-endpoint-description=true&x-indent-response=2'

# Einfache Lemma-Suche
curl 'http://localhost:8080/?query=Haus&x-indent-response=2'

# Alle Verben mit "essen" in der Definition, nur BWB
curl 'http://localhost:8080/?query=pos+%3D+%22Verb%22+AND+definition+%3D+%22essen%22&x-fcs-context=bwb&x-indent-response=2'

# Einträge mit Etymologie-Verweis auf Althochdeutsch
curl 'http://localhost:8080/?query=etymology+%3D+%22Ahd.%22&maximumRecords=5&x-indent-response=2'

# Lemmata die mit "Brot" anfangen, alle Wörterbücher
curl 'http://localhost:8080/?query=lemma+%3D%2Fstartswith+%22Brot%22&x-indent-response=2'

# Zweite Ergebnisseite (Treffer 26–50)
curl 'http://localhost:8080/?query=pos+%3D+%22Substantiv%22&startRecord=26&maximumRecords=25'
```

## Antwortformat

Jeder Treffer enthält zwei Data Views:

**Generic Hits Data View** — Kurzvorschau mit Lemma (hervorgehoben)
und den ersten Definitionen. Pflicht laut FCS Core 2.0.

**Lexical Data View** — Strukturierte Darstellung gemäß LexFCS v0.3
mit `<lex:Entry>`, `<lex:Field>` und `<lex:Value>` Elementen.
Enthält: Lemma + Varianten, Entry-ID, Wortart, alle Definitionen,
Etymologie, Belegstellen (max. 10) und verwandte Wörter.

## Validierung

Der Endpoint kann mit dem FCS Endpoint Validator getestet werden:
https://github.com/saw-leipzig/fcs-endpoint-validator

## Protokoll-Stack

| Schicht | Standard | Verantwortlich für |
|---------|----------|--------------------|
| Transport | HTTP GET | URL-Parameter, XML-Antworten |
| Protokoll | SRU 2.0 | `operation`, `query`, Paging, Diagnostics |
| Suchsprache | CQL / LexCQL | Feld-Abfragen, Booleans, Modifikatoren |
| Inhaltsformat | FCS Core 2.0 | Resource, DataView, Endpoint Description |
| Lexik-Erweiterung | LexFCS v0.3 | Lex Data View, Lex Fields, lex-search Capability |
