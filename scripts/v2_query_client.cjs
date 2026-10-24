// Open-loop HTTP/1.1 query/burst generator. Tokens arrive through stdin, never argv/logs.
// The Python harness records hardware, images and all latency samples.
const http = require('node:http');
const {performance} = require('node:perf_hooks');
let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => input += chunk);
process.stdin.on('end', async () => {
  const config = JSON.parse(input);
  const agent = new http.Agent({keepAlive: true, maxSockets: 300, maxFreeSockets: 64});
  const samples = [], pending = new Set();
  const start = performance.now();
  function request(index, due) {
    const begun = performance.now();
    const submitting = config.mode === 'burst';
    const body = submitting ? JSON.stringify({title: 'load-fake-' + index,
      items: Array.from({length: 5}, () => ({kind: 'research', ticker: 'AAPL',
        target: {kind: 'direct', metric: 'revenue', fiscal_year: 2023}}))}) : undefined;
    return new Promise(resolve => {
      let settled = false;
      function finish(status, valid = false, batch_id = null) {
        if (settled) return;
        settled = true;
        const end = performance.now();
        samples.push({status, ms: end - begun, scheduled_ms: end - due,
          start_lag_ms: begun - due, started_ms: begun - start, index, valid, batch_id});
        resolve();
      }
      const headers = {Authorization: 'Bearer ' + (submitting ? config.tokens[index % 2] : config.tokens[0])};
      if (submitting) Object.assign(headers, {'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(body), 'Idempotency-Key': require('node:crypto').randomUUID()});
      const req = http.request(new URL(submitting ? '/api/v2/batches' : '/api/v2/tasks?limit=30', config.url), {
        agent, method: submitting ? 'POST' : 'GET', headers, timeout: 30000,
      }, response => {
        let body = '';
        response.setEncoding('utf8');
        response.on('data', chunk => body += chunk);
        response.on('end', () => {
          if (response.statusCode === (submitting ? 202 : 200)) {
            try {
              const data = JSON.parse(body);
              const valid = submitting ? typeof data.batch_id === 'string' : Array.isArray(data.items) && data.items.length === 30;
              finish(valid ? response.statusCode : 0, valid, data.batch_id || null);
            } catch { finish(0); }
          } else finish(response.statusCode);
        });
        response.on('error', () => finish(0));
      });
      req.on('timeout', () => req.destroy());
      req.on('error', () => finish(0));
      req.end(body);
    });
  }
  const count = config.mode === 'burst' ? 200 : Math.round(config.duration * config.rps);
  for (let i = 0; i < count; i++) {
    const due = config.mode === 'burst' ? start : start + i * 1000 / config.rps;
    const delay = due - performance.now();
    if (delay > 0) await new Promise(resolve => setTimeout(resolve, delay));
    const job = request(i, due);
    pending.add(job);
    job.finally(() => pending.delete(job));
  }
  await Promise.all(pending);
  agent.destroy();
  process.stdout.write(JSON.stringify(samples));
});
