'use strict';

const https = require('node:https');
const {randomUUID} = require('node:crypto');
const {envelope} = require('./investigations');
const UUID = /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/;
const receiverStates = ['enqueued','dispatched','completed','failed','unknown'];

function createReceiverClient({origin, token, ca, deadlineMs = 5000}) {
  const url = new URL(origin);
  if (url.protocol !== 'https:' || url.origin !== origin ||
      typeof token !== 'string' || !/^[\x21-\x7e]{32,512}$/.test(token) ||
      !Number.isInteger(deadlineMs) || deadlineMs < 100 || deadlineMs > 10000) {
    throw new Error('Expected HTTPS receiver origin and a dedicated service credential');
  }
  const agent = new https.Agent({ca, rejectUnauthorized:true,minVersion:'TLSv1.2',
    keepAlive:true,maxSockets:2,maxFreeSockets:1});
  function call(method,path,body) {
    const data = body === undefined ? undefined : Buffer.from(JSON.stringify(body));
    return new Promise((resolve,reject) => {
      const unavailable = () => reject(Object.assign(new Error('Receiver unavailable'),{status:503}));
      const req = https.request(url,{method,path,agent,signal:AbortSignal.timeout(deadlineMs),
        headers:{authorization:'Bearer '+token,...(data?{'content-type':'application/json','content-length':data.length}:{})}},res=>{
        // Never follow redirects; never include receiver error bodies in logs.
        if (res.statusCode !== 200) {
          reject(Object.assign(new Error('Receiver rejected request'),{status:res.statusCode}));res.destroy();return;
        }
        let size=0;const chunks=[];
        res.on('error',unavailable);res.on('aborted',unavailable);
        res.on('data',part=>{size+=part.length;if(size>4096){res.destroy();unavailable();}else chunks.push(part);});
        res.on('end',()=>{try{if(!res.complete)return unavailable();resolve(JSON.parse(Buffer.concat(chunks).toString('utf8')));}catch{unavailable();}});
      });
      req.on('error',unavailable);req.end(data);
    });
  }
  return {
    submit: command => call('POST','/reviewer/commands',command),
    read: id => {if(!UUID.test(id))throw new Error('Invalid delivery identity');return call('GET','/reviewer/commands/'+id);},
    close:()=>agent.destroy(),
  };
}

function createRelay(pool, receiver, {leaseMs=30000, retryMs=10000, pollMs=5000}={}) {
  for (const value of [leaseMs,retryMs,pollMs]) {
    if (!Number.isInteger(value) || value<1 || value>300000) throw new Error('Invalid relay timing');
  }
  async function claim() {
    const {rows:[row]} = await pool.query(`WITH candidate AS (
      SELECT delivery_id FROM reviewer_investigation_outbox
      WHERE state IN ('accepted','enqueued','dispatched') AND next_attempt_at<=clock_timestamp()
        AND (leased_until IS NULL OR leased_until<=clock_timestamp())
      ORDER BY next_attempt_at,delivery_id FOR UPDATE SKIP LOCKED LIMIT 1)
      UPDATE reviewer_investigation_outbox o SET lease_id=$1,
        leased_until=clock_timestamp()+$2*interval '1 millisecond',attempts=attempts+1
      FROM candidate c WHERE o.delivery_id=c.delivery_id RETURNING o.*`,[randomUUID(),leaseMs]);
    if (!row) return null;
    const {rows:[command]} = await pool.query('SELECT * FROM reviewer_investigations WHERE delivery_id=$1',[row.delivery_id]);
    return {...row,envelope:envelope(command)};
  }
  async function settle(lease, {state,jobId,error=null,delayMs=pollMs}) {
    // A worker whose lease expired or was replaced has no authority to publish.
    // A late receiver acknowledgement may lag a newer state; retain the newer one.
    const {rowCount} = await pool.query(`UPDATE reviewer_investigation_outbox SET
      state=CASE WHEN state='dispatched' AND $3='enqueued' THEN state ELSE COALESCE($3,state) END,
      job_id=COALESCE(job_id,$4),last_error=$5,updated_at=clock_timestamp(),
      lease_id=NULL,leased_until=NULL,next_attempt_at=clock_timestamp()+$6*interval '1 millisecond'
      WHERE delivery_id=$1 AND lease_id=$2 AND leased_until>clock_timestamp()
        AND state IN ('accepted','enqueued','dispatched')`,
    [lease.delivery_id,lease.lease_id,state??null,jobId??null,error,delayMs]);
    return rowCount===1;
  }
  async function deliver(lease) {
    let receipt;
    try {
      receipt = lease.state==='accepted' ? await receiver.submit(lease.envelope) : await receiver.read(lease.delivery_id);
    } catch(error) {
      if ([401,403].includes(error.status)) return settle(lease,{state:'failed',error:'unauthorized'});
      if (error.status===409) return settle(lease,{state:'unknown',error:'conflict'});
      // Lost receiver history is not permission to re-enqueue already-admitted work.
      if (error.status===404 && lease.state!=='accepted') return settle(lease,{state:'unknown',error:'missing_receipt'});
      if (error.status>=300 && error.status<500 && ![408,429].includes(error.status)) {
        return settle(lease,{state:'failed',error:'invalid_receipt'});
      }
      return settle(lease,{error:'unavailable',delayMs:retryMs});
    }
    if (!receipt || Object.keys(receipt).length!==3 || receipt.delivery_id!==lease.delivery_id ||
        typeof receipt.job_id!=='string' || !UUID.test(receipt.job_id) ||
        !receiverStates.includes(receipt.state) || (lease.job_id && lease.job_id!==receipt.job_id)) {
      return settle(lease,{state:'unknown',error:'invalid_receipt'});
    }
    return settle(lease,{state:receipt.state,jobId:receipt.job_id});
  }
  async function tick() {
    const lease=await claim();if(!lease)return false;await deliver(lease);return true;
  }
  return {claim,deliver,tick};
}

module.exports = {createReceiverClient,createRelay};
