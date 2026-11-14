'use strict';

require('dotenv').config();
const fs = require('node:fs');
const {setTimeout:pause} = require('node:timers/promises');
const {pool} = require('./src/db');
const {createReceiverClient,createRelay} = require('./src/reviewer/relay');

async function main() {
  if (process.env.REVIEWER_DELIVERY_ENABLED !== '1' || !pool || process.argv.length!==2) {
    throw new Error('Reviewer delivery must be explicitly enabled');
  }
  const receiver = createReceiverClient({origin:process.env.REVIEWER_RECEIVER_ORIGIN,
    token:fs.readFileSync(process.env.REVIEWER_DELIVERY_TOKEN_FILE,'utf8').trim(),
    ca:process.env.REVIEWER_RECEIVER_CA ? fs.readFileSync(process.env.REVIEWER_RECEIVER_CA) : undefined});
  const relay = createRelay(pool,receiver);
  let stopped=false;
  const stop=()=>{stopped=true;};
  process.once('SIGTERM',stop);process.once('SIGINT',stop);
  try {
    await pool.query('SELECT delivery_id FROM reviewer_investigation_outbox LIMIT 0');
    console.log('Convergence reviewer delivery relay started');
    while(!stopped) {
      try { if (!await relay.tick()) await pause(1000); }
      catch {console.error('Reviewer delivery unavailable; pending leases are retained');await pause(5000);}
    }
  } finally {receiver.close();await pool.end();}
}

if(require.main===module) main().catch(()=>{
  console.error('Reviewer relay could not start. Check the dedicated role, receiver identity and TLS configuration.');
  process.exitCode=1;if(pool&&!pool.ended)pool.end().catch(()=>{});
});
