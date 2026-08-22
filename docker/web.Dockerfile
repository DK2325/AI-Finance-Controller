# Phase 0 placeholder. The real Next.js + Tailwind + shadcn/ui frontend lands in Phase 6.
# Deliberately dependency-free so "docker compose up" never waits on an npm install.
FROM node:22-alpine

WORKDIR /app
COPY web/ ./

EXPOSE 3000
CMD ["node", "server.js"]
