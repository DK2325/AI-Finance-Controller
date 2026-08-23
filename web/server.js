// The web container for `docker compose up`. Zero dependencies, on purpose.
//
// Phase 0 made this call and Phase 6 keeps it: the compose stack must never block on an
// npm install, because "a stranger runs docker compose up and understands the product in
// 60 seconds" is an exit criterion and a cold `npm install` is the most reliable way to
// fail it. See BUILD.md for the recorded deviation from Next.js + Tailwind + shadcn/ui.
//
// This serves the same static bundle the API serves when hosted, and proxies /api and
// /health through to the API container. So the two run paths -- compose locally, one
// service hosted -- show byte-identical screens without sharing a deployment.

const http = require('http');
const fs = require('fs');
const path = require('path');

const PORT = process.env.PORT || 3000;
const API_BASE_URL = process.env.API_BASE_URL || 'http://api:8000';
const STATIC_DIR = path.join(__dirname, 'static');

const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
};

function proxy(req, res) {
  const target = new URL(req.url, API_BASE_URL);
  const upstream = http.request(
    target,
    { method: req.method, headers: { ...req.headers, host: target.host } },
    (up) => {
      res.writeHead(up.statusCode || 502, up.headers);
      up.pipe(res);
    }
  );
  // The API being down must not take the frontend down with it -- a reviewer should see
  // an error in the page rather than a dead port.
  upstream.on('error', (err) => {
    res.writeHead(502, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ error: `api unreachable: ${err.message}` }));
  });
  req.pipe(upstream);
}

function serveStatic(req, res) {
  // Resolve inside STATIC_DIR only. A path escaping it is a 404, never a file read.
  const requested = req.url === '/' ? '/index.html' : req.url.split('?')[0];
  const resolved = path.join(STATIC_DIR, path.normalize(requested));
  if (!resolved.startsWith(STATIC_DIR)) {
    res.writeHead(404).end('not found');
    return;
  }

  fs.readFile(resolved, (err, body) => {
    if (err) {
      res.writeHead(404, { 'Content-Type': 'text/plain' }).end('not found');
      return;
    }
    res.writeHead(200, { 'Content-Type': TYPES[path.extname(resolved)] || 'application/octet-stream' });
    res.end(body);
  });
}

http
  .createServer((req, res) => {
    if (req.url === '/healthz') {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      return res.end(JSON.stringify({ status: 'ok' }));
    }
    if (req.url.startsWith('/api/') || req.url === '/health') return proxy(req, res);
    return serveStatic(req, res);
  })
  .listen(PORT, () => console.log(`[web] listening on ${PORT}, api at ${API_BASE_URL}`));
