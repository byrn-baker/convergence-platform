import {test} from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {HomeView} from '../modules/convergence/HomeView.js';
import {caseIPContext, ipContextHTML} from '../modules/convergence/IPContext.js';
import {caseCaptureContext, captureContextHTML} from '../modules/convergence/CaptureContext.js';

test('Diary exposes observed VLANs, raw repeated tags, and missing capture context distinctly', () => {
  const events = [
    {alert_name:'SuricataIntrusion', capture_context:{status:'recorded', vlan_tags:[100,100], sensor_id:'<img src=x>'}},
    {alert_name:'SuricataIntrusion', message:'10.42.3.68 → 10.42.4.57', capture_context:{status:'not_recorded', vlan_tags:[]}},
    {alert_name:'SuricataIntrusion', capture_context:{status:'recorded', vlan_tags:[0,13]}},
    {alert_name:'FirewallDown'},
  ];
  const document = new JSDOM(HomeView.prototype.htmlDiary.call({cache:{events:{events}}})).window.document;
  assert.match(document.querySelector('thead').textContent, /Observed VLAN/);
  const rows = [...document.querySelectorAll('tbody > tr')];
  assert.equal(rows[0].querySelector('.home-observed-vlan').textContent, '100');
  assert.match(rows[0].textContent, /\[100,100\]/);
  assert.match(rows[0].textContent, /<img src=x>/);
  assert.equal(document.querySelector('img'), null);
  assert.equal(rows[1].querySelector('.home-observed-vlan').textContent, 'Not recorded');
  assert.doesNotMatch(rows[1].textContent, /VLAN 13/);
  assert.match(rows[1].textContent, /no VLAN is inferred from IP addresses/);
  assert.equal(rows[2].querySelector('.home-observed-vlan').textContent, '0 (priority tag) 13');
  assert.match(rows[2].textContent, /not VLAN membership/);
  assert.equal(rows[3].querySelector('.home-observed-vlan').textContent, '—');
});

test('Triage shows capture context alongside the original immutable evidence', () => {
  const selected = {id:'case', alert_name:'SuricataIntrusion', source_evidence:{original:true},
    capture_context:{status:'recorded', vlan_tags:[104], sensor_id:'sensor'}};
  const html = HomeView.prototype.htmlTriage.call({cache:{}, getEscalatedEvents:()=>[selected],
    getSelectedEvent:()=>selected, notesDrafts:new Map()});
  const document = new JSDOM(html).window.document;
  assert.match(document.querySelector('.home-triage-meta').textContent, /Observed VLAN: 104/);
  assert.match(document.querySelector('.home-capture-context').textContent, /Sensor: sensor/);
  assert.match(document.body.textContent, /Original sensor \/ alert evidence/);
});

test('unavailable and invalid VLAN metadata cannot render as capture evidence', () => {
  for (const context of [null, {status:'recorded', vlan_tags:['<img src=x>']},
    {status:'recorded', vlan_tags:[]}, {status:'recorded', vlan_tags:[4095]},
    {status:'not_recorded', vlan_tags:[13]}]) {
    const parsed = caseCaptureContext({alert_name:'SuricataIntrusion', capture_context:context});
    assert.equal(parsed.status, 'unavailable');
    const document = new JSDOM(captureContextHTML(parsed)).window.document;
    assert.equal(document.querySelector('img'), null);
    assert.match(document.body.textContent, /Unavailable/);
  }
});

test('IP context renders untrusted provider fields as text and does not imply identity', () => {
  const context = {status: 'active', databaseBuiltAt: 'invalid',
    source: {ip: '192.168.3.2', scope: 'internal'},
    destination: {ip: '1.1.1.1', scope: 'external', asn: 'AS13335',
      as_name: '<img src=x onerror=alert(1)>', as_domain: 'javascript:alert(1)', country: 'Test country'}};
  const document = new JSDOM(ipContextHTML(caseIPContext({ip_context: JSON.stringify(context)}))).window.document;
  assert.equal(document.querySelector('img'), null);
  assert.match(document.body.textContent, /AS13335/);
  assert.match(document.body.textContent, /not the requested hostname/);
  assert.equal(document.querySelector('a').href, 'https://ipinfo.io/');
  assert.equal(caseIPContext({ip_context: 'broken JSON'}), null);
  assert.equal(caseIPContext({}), null);
});

test('sensor strings remain text and quiet VLANs remain unverified', () => {
  const security = {status: 'active', blockedConnections: {lastHour: 0, last24h: 0},
    intrusionDetection: {status: 'active', sensors: [{id: '<img src=x onerror=alert(1)>',
      status: 'active', packets: 12, captureDrops: 0, decoderErrors: 0, pendingEvents: 0,
      captureSources: [{name: '<img src=x>', status: 'stale', scope: 'Node uplinks'}],
      coverageGaps: ['<script>bad()</script>'],
      coverage: [{vlan: 104, status: 'observed'}, {vlan: 301, status: 'unverified'}]}]}};
  const html = HomeView.prototype.htmlSecurity.call({cache: {security}});
  const document = new JSDOM(html).window.document;
  assert.equal(document.querySelector('img'), null);
  assert.match(document.body.textContent, /<img src=x onerror=alert\(1\)>/);
  assert.match(document.body.textContent, /301unverified/);
  assert.match(document.body.textContent, /disabled during tuning/);
  assert.equal(document.querySelector('script'), null);
  assert.match(document.body.textContent, /stal[e]/);
});

test('TLS observations never imply decrypted payloads', () => {
  const html = HomeView.prototype.htmlSecurity.call({cache: {security: {
    status: 'active', intrusionDetection: {status: 'active', sensors: [],
      encryptedTraffic: {window: '15m', tlsObservations: 10, fingerprinted: 8, serverNamesObserved: 2}},
  }}});
  const text = new JSDOM(html).window.document.body.textContent;
  assert.match(text, /10 TLS observations/);
  assert.match(text, /Payload decryption is not enabled/);
});

test('firewall outage does not hide independent IDS observations', () => {
  const html = HomeView.prototype.htmlSecurity.call({cache: {security: {
    status: 'unavailable', intrusionDetection: {status: 'active', sensors: []},
  }}});
  const text = new JSDOM(html).window.document.body.textContent;
  assert.match(text, /Firewall telemetry: unavailable/);
  assert.match(text, /Intrusion detection · active/);
  assert.match(text, /Unknown/);
});
