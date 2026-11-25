FROM node:22-alpine AS deps

WORKDIR /app

COPY package.json ./
COPY apps/web/package.json ./apps/web/

RUN npm ci --ignore-scripts --no-audit --no-fund

FROM node:22-alpine AS builder

WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY --from=deps /app/apps/web/node_modules ./apps/web/node_modules
COPY package.json ./
COPY apps/web ./apps/web

RUN npm run build --workspace @repopilot/web

FROM node:22-alpine AS runner

WORKDIR /app

ENV NODE_ENV=production
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 nextjs

COPY --from=builder /app/package.json ./
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/apps/web ./apps/web

USER nextjs

EXPOSE 3000

CMD ["npm", "run", "start", "--workspace", "@repopilot/web"]
