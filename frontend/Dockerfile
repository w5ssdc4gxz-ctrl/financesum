# Frontend (Next.js) Dockerfile for Cloud Run
FROM node:20-slim AS deps
WORKDIR /app
ENV npm_config_audit=false \
    npm_config_fund=false \
    npm_config_progress=false
COPY package*.json ./
RUN npm ci --prefer-offline --fetch-retries=5 --fetch-retry-factor=2 --fetch-retry-mintimeout=20000 --fetch-retry-maxtimeout=120000

FROM node:20-slim AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ARG NEXT_PUBLIC_SUPABASE_URL
ARG NEXT_PUBLIC_SUPABASE_ANON_KEY
ARG NEXT_PUBLIC_AUTH_MODE
ARG NEXT_PUBLIC_SITE_URL
ARG NEXT_PUBLIC_POSTHOG_KEY
ARG NEXT_PUBLIC_POSTHOG_HOST
ARG NEXT_PUBLIC_POSTHOG_DEBUG

# Create a local .env.production file so Next.js statically embeds these during SSG
RUN echo "NEXT_PUBLIC_SUPABASE_URL=${NEXT_PUBLIC_SUPABASE_URL}" >> .env.production
RUN echo "NEXT_PUBLIC_SUPABASE_ANON_KEY=${NEXT_PUBLIC_SUPABASE_ANON_KEY}" >> .env.production
RUN echo "NEXT_PUBLIC_AUTH_MODE=${NEXT_PUBLIC_AUTH_MODE}" >> .env.production
RUN echo "NEXT_PUBLIC_SITE_URL=${NEXT_PUBLIC_SITE_URL}" >> .env.production
RUN echo "NEXT_PUBLIC_POSTHOG_KEY=${NEXT_PUBLIC_POSTHOG_KEY}" >> .env.production
RUN echo "NEXT_PUBLIC_POSTHOG_HOST=${NEXT_PUBLIC_POSTHOG_HOST}" >> .env.production
RUN echo "NEXT_PUBLIC_POSTHOG_DEBUG=${NEXT_PUBLIC_POSTHOG_DEBUG}" >> .env.production

ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:20-slim AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV npm_config_audit=false \
    npm_config_fund=false \
    npm_config_progress=false

COPY package*.json ./
RUN npm ci --omit=dev --prefer-offline --fetch-retries=5 --fetch-retry-factor=2 --fetch-retry-mintimeout=20000 --fetch-retry-maxtimeout=120000 \
  && npm cache clean --force

COPY --from=builder /app/.next ./.next
COPY --from=builder /app/public ./public
COPY --from=builder /app/next.config.js ./next.config.js

EXPOSE 8080
CMD ["sh", "-c", "npm start -- -p ${PORT:-8080}"]
