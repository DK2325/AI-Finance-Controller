// Phase 0 placeholder web service. Zero dependencies on purpose: the compose stack must
// never block on an npm install. Replaced by Next.js + Tailwind + shadcn/ui in Phase 6.
const http = require('http');

const PORT = process.env.PORT || 3000;
const API_BASE_URL = process.env.API_BASE_URL || 'http://localhost:8000';

const page = `<!doctype html>
<meta charset="utf-8">
<title>LedgerLoop</title>
<style>
  body { font: 16px/1.6 system-ui, sans-serif; max-width: 40rem; margin: 4rem auto; padding: 0 1rem; }
  code { background: #f4f4f5; padding: .15em .4em; border-radius: 4px; }
</style>
<h1>LedgerLoop</h1>
<p>Phase 0 placeholder. The three-screen frontend arrives in Phase 6.</p>
<p>API: <code>${API_BASE_URL}</code></p>
`;

http.createServer((req, res) => {
  if (req.url === '/health') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    return res.end(JSON.stringify({ status: 'ok', phase: 0 }));
  }
  res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
  res.end(page);
}).listen(PORT, () => console.log(`[web] listening on ${PORT}`));
