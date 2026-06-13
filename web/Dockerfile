# Next.js (standalone output) container for Cloud Run.
# The gateway URL is baked in at build time — NEXT_PUBLIC_* vars are inlined by
# `next build` — so it is passed as a build arg, not a runtime env var.

FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ARG NEXT_PUBLIC_GATEWAY_WS
ARG NEXT_PUBLIC_GATEWAY_HTTP
ENV NEXT_PUBLIC_GATEWAY_WS=$NEXT_PUBLIC_GATEWAY_WS \
    NEXT_PUBLIC_GATEWAY_HTTP=$NEXT_PUBLIC_GATEWAY_HTTP \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0
# Next standalone server + its static assets. standalone output does not bundle
# public/, so copy it explicitly (this is where the logo and other static files live).
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public
EXPOSE 8080
# Cloud Run injects $PORT (8080); the standalone server reads process.env.PORT.
CMD ["node", "server.js"]
