# Build context: frontend/
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
# ci (not install) so the build is pinned to the committed lockfile.
RUN npm ci --no-audit --no-fund

FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# Baked into the client bundle at build time.
ARG NEXT_PUBLIC_API_URL=http://localhost:8001
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
# This image serves the app from a Node server (single-origin compose); the
# default `next build` target is the static export used by Cloudflare Pages.
ENV NEXT_OUTPUT=standalone
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
# Run as the built-in unprivileged `node` user, not root.
COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static
COPY --from=builder --chown=node:node /app/public ./public
USER node
EXPOSE 3000
CMD ["node", "server.js"]
