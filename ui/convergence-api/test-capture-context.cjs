const {test} = require('node:test');
const assert = require('node:assert/strict');
const {captureContext} = require('./src/lib/captureContext');
const prefix = 'Passive IDS observation; not proof of compromise. ';
const evidence = annotations => ({labels: {source: 'suricata', sensor_id: 'sensor'}, annotations});
const structured = tags => evidence({capture_context: JSON.stringify({version: 1, vlan_tags: tags})});
const legacy = fields => evidence({description: prefix + JSON.stringify(fields)});

test('structured and legacy cases preserve observed tag order, duplicates, and priority tags', () => {
  for (const tags of [[100], [100, 100], [300, 301], [0, 13], [4094]]) {
    const expected = {sensor_id: 'sensor', status: 'recorded', vlan_tags: tags};
    assert.deepEqual(captureContext(structured(tags)), expected);
    assert.deepEqual(captureContext(legacy({vlan: tags})), expected);
  }
  const raw = structured([13]);
  raw.annotations.description = prefix + '{"vlan":[100]';
  assert.deepEqual(captureContext(raw).vlan_tags, [13], 'structured annotation is independent of truncated descriptions');
});

test('missing VLANs remain unknown even for addresses associated with a known network', () => {
  for (const raw of [structured([]), legacy({}), legacy({src_ip:'192.168.13.2', dest_ip:'10.42.4.57'}), legacy({vlan:[]})]) {
    assert.deepEqual(captureContext(raw), {sensor_id:'sensor', status:'not_recorded', vlan_tags:[]});
  }
  assert.equal(captureContext(null), null);
  assert.equal(captureContext({labels:{source:'prometheus'},annotations:legacy({vlan:[13]}).annotations}), null);
  assert.equal(captureContext(null, 'SuricataIntrusion').status, 'unavailable');
});

test('malformed capture metadata never produces a guessed or partial VLAN list', () => {
  for (const tags of [null, '13', [true], ['13'], [13.5], [-1], [4095], [13, '<img>'], [1,2,3,4]]) {
    assert.equal(captureContext(structured(tags)).status, 'unavailable');
    assert.equal(captureContext(legacy({vlan:tags})).status, 'unavailable');
  }
  for (const annotations of [
    {}, {description:prefix+'{"vlan":[13],"alert":'}, {description:'other {"vlan":[13]}'},
    {description:prefix+'[]'}, {capture_context:'broken'},
    {capture_context:{version:2,vlan_tags:[13]}}, {capture_context:{version:1}},
  ]) assert.deepEqual(captureContext(evidence(annotations)), {sensor_id:'sensor',status:'unavailable',vlan_tags:[]});
});
