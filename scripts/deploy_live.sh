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

FRONTEND_SERVICE_JSON=""

load_frontend_service_json() {
  if [[ -n "$FRONTEND_SERVICE_JSON" ]]; then
    return 0
  fi

  FRONTEND_SERVICE_JSON="$(
    gcloud run services describe "$FRONTEND_SERVICE" \
      --region "$REGION" \
      --format=json 2>/dev/null || true
  )"
}

frontend_public_env_value() {
  local key="$1"
  local explicit="${!key:-}"
  if [[ -n "$explicit" ]]; then
    printf '%s' "$explicit"
    return 0
  fi

  load_frontend_service_json
  if [[ -z "$FRONTEND_SERVICE_JSON" ]]; then
    return 0
  fi

  printf '%s' "$FRONTEND_SERVICE_JSON" | jq -r --arg key "$key" '
    (.spec.template.spec.containers[0].env // [])
    | map(select(.name == $key))
    | .[0].value // ""
  '
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
  local supabase_url supabase_anon site_url posthog_key posthog_host posthog_debug auth_mode
  local substitutions

  echo "==> Frontend build"
  (cd "$REPO_ROOT/frontend" && npm run build)

  supabase_url="$(frontend_public_env_value NEXT_PUBLIC_SUPABASE_URL)"
  supabase_anon="$(frontend_public_env_value NEXT_PUBLIC_SUPABASE_ANON_KEY)"
  site_url="$(frontend_public_env_value NEXT_PUBLIC_SITE_URL)"
  posthog_key="$(frontend_public_env_value NEXT_PUBLIC_POSTHOG_KEY)"
  posthog_host="$(frontend_public_env_value NEXT_PUBLIC_POSTHOG_HOST)"
  posthog_debug="$(frontend_public_env_value NEXT_PUBLIC_POSTHOG_DEBUG)"
  auth_mode="${NEXT_PUBLIC_AUTH_MODE:-supabase}"

  if [[ -z "$supabase_url" || -z "$supabase_anon" ]]; then
    echo "Missing frontend public Supabase build args."
    echo "Set NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY"
    echo "or ensure they already exist on the $FRONTEND_SERVICE Cloud Run service."
    exit 1
  fi

  substitutions=(
    "_NEXT_PUBLIC_SUPABASE_URL=$supabase_url"
    "_NEXT_PUBLIC_SUPABASE_ANON_KEY=$supabase_anon"
    "_NEXT_PUBLIC_AUTH_MODE=$auth_mode"
    "_NEXT_PUBLIC_SITE_URL=$site_url"
    "_NEXT_PUBLIC_POSTHOG_KEY=$posthog_key"
    "_NEXT_PUBLIC_POSTHOG_HOST=$posthog_host"
    "_NEXT_PUBLIC_POSTHOG_DEBUG=$posthog_debug"
  )

  echo "==> Cloud Build (frontend)"
  gcloud builds submit "$REPO_ROOT/frontend" \
    --config "$REPO_ROOT/frontend/cloudbuild.yaml" \
    --substitutions "$(IFS=,; echo "${substitutions[*]}")"

  echo "==> Cloud Run deploy (frontend)"
  gcloud run deploy "$FRONTEND_SERVICE" \
    --image "$FRONTEND_IMAGE" \
    --platform managed \
    --region "$REGION" \
    --allow-unauthenticated

  echo "==> Shift frontend traffic to latest ready revision"
  gcloud run services update-traffic "$FRONTEND_SERVICE" \
    --region "$REGION" \
    --to-latest
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

  echo "==> Frontend root"
  FRONTEND_URL="$(gcloud run services describe "$FRONTEND_SERVICE" --region "$REGION" --format='value(status.url)')"
  curl -I -sSf "$FRONTEND_URL/" >/dev/null
  echo "OK: $FRONTEND_URL/"
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
