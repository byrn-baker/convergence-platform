import assert from 'node:assert/strict';
import { test } from 'node:test';
import { JSDOM } from 'jsdom';
import { vmSecurityHTML } from '../modules/convergence/VMSecurity.js';

const site = 'branch';
const data = {
  site,
  sources: [],
  hostTelemetry: {
    status: 'configured',
    agents: [{
      vm_id: 'guest-42',
      status: 'stale',
      reason: '<img src=x>',
      heartbeat_observed: 1770000000,
      heartbeat: { collection_ok: false, queue_events: 7 },
    }],
  },
};

test('VM Security includes stale guest heartbeat evidence and escapes its reported reason', () => {
  const { document } = new JSDOM(vmSecurityHTML(data, { site })).window;
  const heartbeat = document.getElementById('host-heartbeat-title').closest('section');
  const row = heartbeat.querySelector('tbody tr');
  assert.match(row.textContent, /guest-42/);
  assert.match(row.textContent, /Stale · visibility lost/);
  assert.match(row.textContent, /Collection failed/);
  assert.match(row.textContent, /Events: 7/);
  assert.ok(row.textContent.includes('<img src=x>'));
  assert.equal(row.querySelector('img'), null);
});

test('a VM Security response for another site never exposes its heartbeat rows', () => {
  const { document } = new JSDOM(vmSecurityHTML(data, { site: 'other' })).window;
  assert.match(document.body.textContent, /observations unavailable/);
  assert.doesNotMatch(document.body.textContent, /guest-42/);
});
