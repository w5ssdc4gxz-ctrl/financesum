#!/usr/bin/env bash
set -euo pipefail

# Repo-local gcloud config to avoid permission issues writing to ~/.config/gcloud
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export CLOUDSDK_CONFIG="${CLOUDSDK_CONFIG:-"$REPO_ROOT/.gcloud"}"
mkdir -p "$CLOUDSDK_CONFIG"

PROJECT_ID="${PROJECT_ID:-financesums}"
REGION="${REGION:-europe-west1}"

BACKEND_IMAGE="${BACKEND_IMAGE:-gcr.io/${PROJECT_ID}/financesums-backend}"
FRONTEND_IMAGE="${FRONTEND_IMAGE:-gcr.io/${PROJECT_ID}/financesums-frontend}"

BACKEND_SERVICE="${BACKEND_SERVICE:-financesums-backend}"
FRONTEND_SERVICE="${FRONTEND_SERVICE:-financesums-frontend}"

TARGET="${1:-both}" # backend|frontend|both|verify

need_auth() {
  # `gcloud auth list` output fields have changed across versions; the most reliable
  # way to verify auth is to ask gcloud for an access token.
  if ! gcloud auth print-access-token >/dev/null 2>&1; then
    echo "No credentialed gcloud account found for CLOUDSDK_CONFIG=\"$CLOUDSDK_CONFIG\"."
    echo "Run:"
    echo "  CLOUDSDK_CONFIG=\"$CLOUDSDK_CONFIG\" gcloud auth login"
    echo "  CLOUDSDK_CONFIG=\"$CLOUDSDK_CONFIG\" gcloud config set project \"$PROJECT_ID\""
    exit 1
  fi
}

run_backend() {
  echo "==> Backend tests"
  (cd "$REPO_ROOT/backend" && pytest)

  echo "==> Cloud Build (backend)"
  gcloud builds submit --tag "$BACKEND_IMAGE" "$REPO_ROOT/backend"

  echo "==> Cloud Run deploy (backend)"
  gcloud run deploy "$BACKEND_SERVICE" \
    --image "$BACKEND_IMAGE" \
    --platform managed \
    --region "$REGION" \
    --allow-unauthenticated
}

run_frontend() {
  echo "==> Frontend build"
  (cd "$REPO_ROOT/frontend" && npm run build)

  echo "==> Cloud Build (frontend)"
  gcloud builds submit --tag "$FRONTEND_IMAGE" "$REPO_ROOT/frontend"

  echo "==> Cloud Run deploy (frontend)"
  gcloud run deploy "$FRONTEND_SERVICE" \
    --image "$FRONTEND_IMAGE" \
    --platform managed \
    --region "$REGION" \
    --allow-unauthenticated
}

verify_live() {
  echo "==> Services"
  gcloud run services list --region "$REGION" --platform managed

  echo "==> Revisions + URLs"
  gcloud run services describe "$FRONTEND_SERVICE" --region "$REGION" --format="value(status.latestReadyRevisionName,status.url)"
  gcloud run services describe "$BACKEND_SERVICE"  --region "$REGION" --format="value(status.latestReadyRevisionName,status.url)"

  echo "==> Backend health"
  BACKEND_URL="$(gcloud run services describe "$BACKEND_SERVICE" --region "$REGION" --format='value(status.url)')"
  curl -sSf "$BACKEND_URL/health" >/dev/null
  echo "OK: $BACKEND_URL/health"
}

case "$TARGET" in
  backend)
    need_auth
    gcloud config set project "$PROJECT_ID" >/dev/null
    run_backend
    ;;
  frontend)
    need_auth
    gcloud config set project "$PROJECT_ID" >/dev/null
    run_frontend
    ;;
  both)
    need_auth
    gcloud config set project "$PROJECT_ID" >/dev/null
    run_backend
    run_frontend
    ;;
  verify)
    need_auth
    gcloud config set project "$PROJECT_ID" >/dev/null
    verify_live
    ;;
  *)
    echo "Usage: $0 [backend|frontend|both|verify]"
    exit 2
    ;;
esac
