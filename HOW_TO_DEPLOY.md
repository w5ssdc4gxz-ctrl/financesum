# Deploy FinanceSum to Production (Cloud Run)

This repo includes Dockerfiles for both `backend/` and `frontend/` and can be deployed via Cloud Build → Cloud Run.

## Quick Go-Live Summary

1. Test locally before touching live.

Backend:
```bash
cd backend
pytest
```

Frontend:
```bash
cd frontend
npm run build
```

2. Deploy only the service(s) you changed.

Backend from repo root:
```bash
gcloud builds submit --tag gcr.io/financesums/financesums-backend backend

gcloud run deploy financesums-backend \
  --image gcr.io/financesums/financesums-backend \
  --platform managed \
  --region europe-west1 \
  --allow-unauthenticated
```

Frontend from repo root:
```bash
./scripts/deploy_live.sh frontend
```

If both changed, deploy backend first and frontend second.

3. Verify live immediately after deploy.

```bash
gcloud run services list --region europe-west1 --platform managed

gcloud run services describe financesums-frontend --region europe-west1 --format="value(status.latestReadyRevisionName,status.url)"
gcloud run services describe financesums-backend  --region europe-west1 --format="value(status.latestReadyRevisionName,status.url)"

curl -sSf "$(gcloud run services describe financesums-backend --region europe-west1 --format='value(status.url)')/health"
```

4. Roll back fast if needed.

```bash
gcloud run revisions list --service financesums-frontend --region europe-west1
gcloud run revisions list --service financesums-backend  --region europe-west1
```

## 0) Prereqs (one-time)

- Install and authenticate `gcloud`
- Select the correct project and region:

```bash
export PROJECT_ID="financesums"
export REGION="europe-west1"
gcloud config set project "$PROJECT_ID"
```

## 1) Test locally (always before deploy)

Backend:
```bash
cd backend
pytest
```

Frontend:
```bash
cd frontend
npm run build
```

## 2) Configure production environment variables

Set required env vars in Cloud Run for each service:
- Backend: use `.env.example` as the source of truth (Supabase, OpenAI, Redis, Stripe if enabled)
- Frontend:
  - Set `BACKEND_API_URL` (or `BACKEND_URL`) to the backend Cloud Run URL.
  - Do **not** rely on local `frontend/.env.local` during Cloud Build; only Cloud Run runtime env vars should define backend routing.
  - Browser API requests are proxy-only through `/api/backend` to avoid cross-origin CORS fragility.
  - `NEXT_PUBLIC_API_PROXY_BASE` should not be used to override production browser routing.

Tip: prefer Secrets Manager for secrets; avoid putting API keys directly into command history.

If you use `./scripts/deploy_live.sh`, it defaults `CLOUDSDK_CONFIG` to repo-local `.gcloud`. Either authenticate there first or export `CLOUDSDK_CONFIG="$HOME/.config/gcloud"` before running the script.

## 3) Deploy (Cloud Build → Cloud Run)

### Deploy backend

From repo root:
```bash
gcloud builds submit --tag "gcr.io/$PROJECT_ID/financesums-backend" backend

gcloud run deploy financesums-backend \
  --image "gcr.io/$PROJECT_ID/financesums-backend" \
  --platform managed \
  --region "$REGION" \
  --allow-unauthenticated
```

### Deploy frontend

From repo root:
```bash
./scripts/deploy_live.sh frontend
```

Why: the frontend build must inject the current `NEXT_PUBLIC_*` values at Cloud Build time through `frontend/cloudbuild.yaml`. The helper script reuses the existing Cloud Run public env values, performs the Cloud Build with substitutions, deploys the image, and shifts traffic to the latest revision.

### Deploy both (recommended order)

1) Backend first (keep it backwards compatible if possible)
2) Frontend second

## 4) Verify live after deploy

List services + URLs:
```bash
gcloud run services list --region "$REGION" --platform managed
```

See which revision is live:
```bash
gcloud run services describe financesums-frontend --region "$REGION" --format="value(status.latestReadyRevisionName,status.url)"
gcloud run services describe financesums-backend  --region "$REGION" --format="value(status.latestReadyRevisionName,status.url)"
```

Basic backend health check:
```bash
curl -sSf "$(gcloud run services describe financesums-backend --region "$REGION" --format='value(status.url)')/health"
```

Verify CORS preflight from your site origin:
```bash
BACKEND_URL="$(gcloud run services describe financesums-backend --region "$REGION" --format='value(status.url)')"
curl -i -X OPTIONS \
  -H "Origin: https://financesums.com" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: authorization,content-type" \
  "$BACKEND_URL/api/v1/filings/<FILING_ID>/summary"
```

Verify the live frontend bundle no longer embeds a direct backend host:
```bash
html="$(curl -sS https://financesums.com/company/<COMPANY_ID>?ticker=MSFT)"
echo "$html" | rg -o '/_next/static/[^"'"'"']+\.js' | sort -u | sed 's#^#https://financesums.com#' > /tmp/fs_chunks.txt
while read -r u; do curl -sS "$u"; echo; done < /tmp/fs_chunks.txt | rg -n "financesums-backend|europe-west1.run.app"
```

Verify browser requests route through the same-origin proxy:
- Open the browser devtools Network tab on `https://financesums.com/company/<COMPANY_ID>`.
- Generate a summary and confirm request URLs start with `https://financesums.com/api/backend/`.

## 5) Roll back if something breaks

Find old revisions:
```bash
gcloud run revisions list --service financesums-frontend --region "$REGION"
gcloud run revisions list --service financesums-backend  --region "$REGION"
```

Shift 100% traffic to a prior revision:
```bash
gcloud run services update-traffic financesums-frontend --region "$REGION" --to-revisions REVISION_NAME=100
gcloud run services update-traffic financesums-backend  --region "$REGION" --to-revisions REVISION_NAME=100
```
