'use strict';
const {execFileSync} = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
function certificates(t) {
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'telemetry-tls-'));
  t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
  const run=args=>execFileSync('openssl',args,{cwd:dir,stdio:'pipe'});
  const read=name=>fs.readFileSync(path.join(dir,name));
  run(['req','-x509','-newkey','ec','-pkeyopt','ec_paramgen_curve:P-256','-nodes','-days','1','-subj','/CN=fixture-ca','-keyout','ca.key','-out','ca.pem']);
  for (const name of ['localhost','client']) {
    run(['req','-new','-newkey','ec','-pkeyopt','ec_paramgen_curve:P-256','-nodes','-subj','/CN='+name,'-keyout',name+'.key','-out',name+'.csr']);
    fs.writeFileSync(path.join(dir,'leaf.ext'),'basicConstraints=CA:FALSE\nsubjectAltName=DNS:'+name+'\nextendedKeyUsage='+(name==='localhost'?'serverAuth':'clientAuth'));
    run(['x509','-req','-days','1','-in',name+'.csr','-CA','ca.pem','-CAkey','ca.key','-set_serial',name==='localhost'?'2':'3','-extfile','leaf.ext','-out',name+'.pem']);
  }
  return {ca:read('ca.pem'),server:{cert:read('localhost.pem'),key:read('localhost.key')},client:{cert:read('client.pem'),key:read('client.key')}};
}
module.exports={certificates};
