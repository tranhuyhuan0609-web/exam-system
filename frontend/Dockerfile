# Next.js frontend of the pilot: a standalone server on :3000 that proxies /api/v1/* (and /media/*) to the API.
#   docker build -f frontend/Dockerfile --build-arg BACKEND_INTERNAL_URL=http://app:8000 frontend
FROM node:22-alpine AS deps
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS build
WORKDIR /web
ARG BACKEND_INTERNAL_URL=http://app:8000
ENV BACKEND_INTERNAL_URL=$BACKEND_INTERNAL_URL NEXT_OUTPUT=standalone NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /web/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-alpine AS run
WORKDIR /web
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
COPY --from=build --chown=node:node /web/.next/standalone ./
COPY --from=build --chown=node:node /web/.next/static ./.next/static
# the image's own non-root user (uid 1000)
USER node
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD wget -qO- http://127.0.0.1:3000/dang-nhap >/dev/null || exit 1
CMD ["node", "server.js"]
