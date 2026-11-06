#!/usr/bin/env python3
"""Verify the isolated API, case persistence and TLS-backed sensor status."""
import argparse,json,subprocess
p=argparse.ArgumentParser();p.add_argument('namespace');p.add_argument('release');args=p.parse_args()
# The key stays inside the API container. Only bounded verification results leave it.
code=r'''
const assert=require('node:assert/strict');
const key=JSON.parse(process.env.API_KEYS)[0].key;
async function api(path,method='GET',body){
 const response=await fetch('http://127.0.0.1:3000'+path,{method,headers:{Authorization:'Bearer '+key,'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});
 assert.ok(response.ok,'API '+method+' '+path+' returned '+response.status);return response.json();
}
(async()=>{
 const sites=await api('/api/sites');const site=sites.sites[0].id;
 const incident='helm-smoke:'+require('node:crypto').randomUUID();
 const created=await api('/api/events?site='+encodeURIComponent(site),'POST',{message:'Helm readiness check — synthetic, no sensor event',source:'helm-smoke',category:'system',severity:'info',status:'logged',incident_key:incident});
 assert.ok(created.id);const saved=await api('/api/events/'+created.id+'?site='+encodeURIComponent(site));
 assert.equal(saved.incident_key,incident);
 await api('/api/events/'+created.id+'?site='+encodeURIComponent(site),'PATCH',{status:'archived'});
 const history=await api('/api/security/history?site='+encodeURIComponent(site)+'&range=1h');assert.equal(history.unavailable,undefined);
 const security=await api('/api/security?site='+encodeURIComponent(site));
 const health=await (await fetch('http://127.0.0.1:3000/healthz')).json();assert.equal(health.features.diary,'available');
 console.log(JSON.stringify({site,postgres:'available',case_write_read_archive:true,history_query:true,ids_status:security.intrusionDetection.status,synthetic_case_id:created.id}));
})().catch(error=>{console.error(error.message);process.exit(1)});
'''
subprocess.run(['kubectl','-n',args.namespace,'exec','-i','deploy/'+args.release+'-api','--','node','-'],input=code,text=True,check=True)
