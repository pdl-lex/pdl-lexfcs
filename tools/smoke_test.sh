#!/usr/bin/env bash
# Smoke test for a running LexFCS endpoint (local or deployed).
#
# Usage: tools/smoke_test.sh [BASE_URL]
#   default BASE_URL: https://lexfcs.lexoterm.de
#
# Checks per query: HTTP 200, well-formed XML, no SRU diagnostic,
# at least one hit, no leaked def-objects ("annotations" / "{'text'").
# Exit code 0 = all checks passed, 1 = at least one failure.
# Requires: curl, xmllint (libxml2)

set -u
BASE="${1:-https://lexfcs.lexoterm.de}"
BASE="${BASE%/}"
SR="$BASE/?operation=searchRetrieve&version=2.0&queryType=lex&maximumRecords=5&query="

# URL-encoded LexCQL queries, each expected to return >= 1 hit
QUERIES=(
    'Auge'
    'Haus'
    'lemma%3DBrot'
    'def%3DSehorgan'
    'definition%3D%22Seh*%22'
    'Auge%20AND%20def%3DOrgan'
    'pos%3DNOUN'
)

command -v xmllint >/dev/null || { echo "xmllint not found (install libxml2)"; exit 2; }

fail=0
check() {  # check <label> <url> <expect_hits:0|1>
    local label="$1" url="$2" expect_hits="$3" body code n problems=""
    body=$(curl -s -w '\n%{http_code}' "$url")
    code="${body##*$'\n'}"
    body="${body%$'\n'*}"

    [ "$code" = "200" ] || problems+=" HTTP=$code"
    echo "$body" | xmllint --noout - 2>/dev/null || problems+=" invalid-XML"
    echo "$body" | grep -q '<diag:message>' \
        && problems+=" diag=\"$(echo "$body" | grep -o '<diag:message>[^<]*' | head -1 | cut -d'>' -f2)\""
    echo "$body" | grep -q "annotations\|{'text'" && problems+=" def-object-leak"

    n=$(echo "$body" | grep -o '<sru:numberOfRecords>[0-9]*' | grep -o '[0-9]*$')
    if [ "$expect_hits" = 1 ] && [ "${n:-0}" -eq 0 ]; then problems+=" no-hits"; fi

    if [ -z "$problems" ]; then
        printf 'OK    %-28s %s\n' "$label" "${n:+n=$n}"
    else
        printf 'FAIL  %-28s%s\n' "$label" "$problems"
        fail=1
    fi
}

echo "Endpoint: $BASE"
check "explain" "$BASE/" 0
for q in "${QUERIES[@]}"; do
    check "$(printf '%b' "${q//%/\\x}")" "$SR$q" 1
done

[ "$fail" = 0 ] && echo "All checks passed." || echo "Some checks FAILED."
exit "$fail"
