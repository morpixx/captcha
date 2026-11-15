// createTask throughput scenario for the gateway.
//
// Constant-arrival-rate, not VU-based: the target is 1000 requests per second
// arriving at the gateway, independent of how fast this machine can loop. VUs
// are only workers for that schedule; raise PRE_VUS/MAX_VUS if k6 reports
// "dropped_iterations".
//
//   k6 run --env BASE_URL=http://127.0.0.1:8080 \\
//          --env CLIENT_KEY=<key from make seed> \\
//          tools/load/create_task.js
//
// Every task is solved by tools/load/fake_solver.py; no captcha vendor is
// contacted. See tools/load/README.md for the full stack.

import http from 'k6/http';
import { check, fail } from 'k6';
import { Counter, Trend } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://127.0.0.1:8080';
const CLIENT_KEY = __ENV.CLIENT_KEY || '';
const RATE = Number(__ENV.RATE || 1000);
const DURATION = __ENV.DURATION || '60s';
const PRE_VUS = Number(__ENV.PRE_VUS || 300);
const MAX_VUS = Number(__ENV.MAX_VUS || 3000);
const TYPE = __ENV.TYPE || 'text_captcha';
const TYPES = TYPE.split(',').map((value) => value.trim()).filter(Boolean);

const accepted = new Counter('create_task_accepted');
const rejected = new Counter('create_task_rejected');
const createLatency = new Trend('create_task_latency_ms', true);
// k6 metrics are write-only; keep a plain counter for log throttling.
let rejectedCount = 0;

export const options = {
  scenarios: {
    create_task: {
      executor: 'constant-arrival-rate',
      rate: RATE,
      timeUnit: '1s',
      duration: DURATION,
      preAllocatedVUs: PRE_VUS,
      maxVUs: MAX_VUS,
      tags: { scenario: 'create_task' },
    },
  },
  thresholds: {
    // The gateway answers business errors with HTTP 200, so http_req_failed is
    // about transport/5xx. The checks threshold is the real acceptance bar.
    http_req_failed: ['rate<0.001'],
    checks: ['rate>0.999'],
    create_task_latency_ms: ['p(95)<300', 'p(99)<750'],
    dropped_iterations: ['count<1'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
  // createTask answers a business rejection inside the body; dropping it would
  // make every response look like a failure here.
  discardResponseBodies: false,
};

export function setup() {
  if (!CLIENT_KEY) {
    fail('CLIENT_KEY is required: k6 run --env CLIENT_KEY=<key> tools/load/create_task.js');
  }
  const balance = http.get(`${BASE_URL}/getBalance?clientKey=${encodeURIComponent(CLIENT_KEY)}`);
  const body = balance.json();
  if (balance.status !== 200 || !body || body.errorId !== 0) {
    fail(`getBalance failed (HTTP ${balance.status}): ${balance.body}`);
  }
  // 1k/s for one minute costs ~60k * catalog price; warn instead of failing so
  // a short smoke run against a small balance still works.
  if (body.balance < 1) {
    console.warn(`client balance is ${body.balance}; top up before a long run`);
  }
  console.log(`target ${BASE_URL}, ${RATE}/s for ${DURATION}, types=${TYPES.join(',')}, balance=${body.balance}`);
  return { startedAt: Date.now() };
}

export default function () {
  const type = TYPES[Math.floor(Math.random() * TYPES.length)];
  const payload = JSON.stringify({
    clientKey: CLIENT_KEY,
    task: {
      type,
      websiteURL: 'https://loadtest.invalid/login',
      websiteKey: 'loadtest-site-key',
    },
  });

  const response = http.post(`${BASE_URL}/createTask`, payload, {
    headers: { 'Content-Type': 'application/json' },
    tags: { name: 'createTask', type },
  });

  let body = null;
  try {
    body = response.json();
  } catch (_error) {
    body = null;
  }

  const ok = response.status === 200 && body !== null && body.errorId === 0 && Boolean(body.taskId);
  check(response, {
    'createTask accepted': () => ok,
    'no 5xx': () => response.status < 500,
  });

  if (ok) {
    accepted.add(1);
    createLatency.add(response.timings.duration);
  } else {
    rejected.add(1);
    rejectedCount += 1;
    if (rejectedCount % 100 === 1) {
      console.warn(`rejected: HTTP ${response.status} ${String(response.body || '').slice(0, 200)}`);
    }
  }
}
