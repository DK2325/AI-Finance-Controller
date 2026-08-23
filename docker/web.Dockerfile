# The web container for `docker compose up`. No build step, no node_modules.
#
# Phase 0 chose this and Phase 6 kept it: a cold `npm install` inside the path a reviewer
# runs is the most reliable way to fail "docker compose up and a stranger understands the
# product in 60 seconds". BUILD.md records the deviation from Next.js + Tailwind.

FROM node:22-alpine

WORKDIR /app
COPY web/server.js ./server.js
COPY web/static/ ./static/

EXPOSE 3000
CMD ["node", "server.js"]
