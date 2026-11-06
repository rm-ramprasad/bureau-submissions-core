#!/usr/bin/env bash
# The admin jobs from the deploy workflow (as deploy/ops.ps1 does from a workstation): calls POST /ops/<job> on the
# Function App with its function key (fetched now, masked in the log, never stored).
#   ci_ops.sh migrate   records each migration in $MARK_APPLIED (comma-separated; run in SSMS by the owner), then
#                       migrates; fails on any failed migration, and unless the deployed version is $VERSION.
set -euo pipefail
job="$1"
key=$(az functionapp keys list -g "$RG" -n "$FUNCTION_APP" --query functionKeys.default -o tsv)
echo "::add-mask::$key"
call() {  # call <json body> -> the answer (and fails on HTTP errors, printing the answer)
  local out code
  out=$(mktemp)
  code=$(curl -s -o "$out" -w '%{http_code}' -X POST -H "x-functions-key: $key" -H "Content-Type: application/json" \
         -d "$1" "https://$FUNCTION_APP.azurewebsites.net/ops/$job")
  cat "$out"; echo
  [ "$code" = "200" ] || { echo "ops/$job answered HTTP $code" >&2; return 1; }
  cp "$out" last.json
}
if [ "$job" = "migrate" ]; then
  IFS=',' read -ra versions <<< "${MARK_APPLIED:-}"
  for v in "${versions[@]}"; do
    v=$(echo "$v" | xargs)
    [ -n "$v" ] && call "{\"args\": [\"--mark-applied\", \"$v\"]}"
  done
  call '{}'
  python3 - "$VERSION" <<'PY'
import json, sys
result = json.load(open("last.json"))
if result.get("failed"):
    sys.exit(f"Migration {result['failed']} failed: {result.get('error')}\n{result.get('hint', '')}")
if result.get("version") != sys.argv[1]:
    sys.exit(f"The Function App runs {result.get('version')!r}, not {sys.argv[1]!r}")
print(f"Migrations OK; the Function App runs {result['version']}")
PY
else
  call '{}'
fi
