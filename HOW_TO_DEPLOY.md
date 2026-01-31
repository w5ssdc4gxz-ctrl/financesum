# Deploy FinanceSum to Production (Cloud Run)

This repo includes Dockerfiles for both `backend/` and `frontend/` and can be deployed via Cloud Build → Cloud Run.

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
- Backend: use `.env.example` as the source of truth (Supabase, Gemini, Redis, Stripe if enabled)
- Frontend: set `NEXT_PUBLIC_*` and backend URL values (see `frontend/.env.local.example`)

Tip: prefer Secrets Manager for secrets; avoid putting API keys directly into command history.

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
gcloud builds submit --tag "gcr.io/$PROJECT_ID/financesums-frontend" frontend

gcloud run deploy financesums-frontend \
  --image "gcr.io/$PROJECT_ID/financesums-frontend" \
  --platform managed \
  --region "$REGION" \
  --allow-unauthenticated
```

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

