#!/usr/bin/env bash
set -euo pipefail

SERVICE="${1:-financesums-backend}"
REGION="${2:-europe-west1}"
EXPECTED_DIGEST="${3:-}"

SERVICE_JSON="$(gcloud run services describe "$SERVICE" --region "$REGION" --format=json)"
read -r LIVE_REVISION SERVICE_URL < <(
  python3 - <<'PY' "$SERVICE_JSON"
import json, sys
doc = json.loads(sys.argv[1])
status = doc.get("status") or {}
traffic = list(status.get("traffic") or [])
live = None
for item in traffic:
    if (item.get("percent") or 0) > 0:
        live = item
        break
if live is None:
    for item in traffic:
        if item.get("latestRevision"):
            live = item
            break
if live is None and traffic:
    live = traffic[0]
revision = (live or {}).get("revisionName") or status.get("latestReadyRevisionName") or ""
url = status.get("url") or ""
print(revision, url)
PY
)

LIVE_DIGEST="$(gcloud run revisions describe "$LIVE_REVISION" --region "$REGION" --format='value(status.imageDigest)')"

echo "service=$SERVICE"
echo "region=$REGION"
echo "live_revision=$LIVE_REVISION"
echo "live_digest=$LIVE_DIGEST"
echo "service_url=$SERVICE_URL"

if [[ -n "$EXPECTED_DIGEST" ]]; then
  if [[ "$LIVE_DIGEST" == *"$EXPECTED_DIGEST"* ]]; then
    echo "digest_match=true"
  else
    echo "digest_match=false"
    echo "expected_digest=$EXPECTED_DIGEST"
    exit 2
  fi
fi

if [[ "$SERVICE" == *"backend"* ]]; then
  curl -sSf "$SERVICE_URL/health"
  echo
fi
