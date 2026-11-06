"""Helm test: write and retrieve release-scoped metric and log canaries."""
import datetime,json,os,time,urllib.parse,urllib.request,uuid
release=os.environ['RELEASE_NAME'];namespace=os.environ['RELEASE_NAMESPACE'];nonce=uuid.uuid4().hex
metrics='http://'+release+'-metrics:8428';logs='http://'+release+'-logs:9428'
def call(url,data=None,kind='application/json'):
 req=urllib.request.Request(url,data=data,headers={'Content-Type':kind})
 with urllib.request.urlopen(req,timeout=10) as r:return r.read().decode()
call(metrics+'/health');call(logs+'/health')
line=f'netclaw_helm_smoke{{project="{release}",namespace="{namespace}",nonce="{nonce}"}} 1\n'
call(metrics+'/api/v1/import/prometheus',line.encode(),'text/plain')
record={'_time':datetime.datetime.now(datetime.timezone.utc).isoformat(),'_msg':'Helm validation — synthetic observation','project':release,'namespace':namespace,'job':'helm-smoke','event_id':nonce}
call(logs+'/insert/jsonline?_stream_fields=project,namespace,job',(json.dumps(record)+'\n').encode(),'application/stream+json')
query=urllib.parse.urlencode({'query':f'netclaw_helm_smoke{{project="{release}",namespace="{namespace}",nonce="{nonce}"}}','nocache':'1'})
logquery=urllib.parse.urlencode({'query':f'project:="{release}" namespace:="{namespace}" event_id:="{nonce}" | limit 1'})
for attempt in range(60):
 if json.loads(call(metrics+'/api/v1/query?'+query))['data']['result'] and nonce in call(logs+'/select/logsql/query?'+logquery):break
 time.sleep(1)
else:raise RuntimeError('A canary was not retrievable from its project store')
collector='http://'+release+'-collector:4318'
message='Helm collector canary '+nonce
attributes=[{'key':'project','value':{'stringValue':release}},{'key':'namespace','value':{'stringValue':namespace}},{'key':'service.name','value':{'stringValue':'helm-smoke'}}]
body={'resourceLogs':[{'resource':{'attributes':attributes},'scopeLogs':[{'scope':{'name':'helm-smoke'},'logRecords':[{'timeUnixNano':str(time.time_ns()),'body':{'stringValue':message}}]}]}]}
call(collector+'/v1/logs',json.dumps(body).encode())
body={'resourceMetrics':[{'resource':{'attributes':attributes},'scopeMetrics':[{'scope':{'name':'helm-smoke'},'metrics':[{'name':'netclaw_helm_otlp_smoke','gauge':{'dataPoints':[{'timeUnixNano':str(time.time_ns()),'asDouble':1,'attributes':[{'key':'nonce','value':{'stringValue':nonce}}]}]}}]}]}]}
call(collector+'/v1/metrics',json.dumps(body).encode())
q=urllib.parse.urlencode({'query':f'netclaw_helm_otlp_smoke{{nonce="{nonce}"}}','nocache':'1'})
lq=urllib.parse.urlencode({'query':f'_msg:="{message}" | limit 1'})
for attempt in range(60):
 if json.loads(call(metrics+'/api/v1/query?'+q))['data']['result'] and nonce in call(logs+'/select/logsql/query?'+lq):break
 time.sleep(1)
else:raise RuntimeError('Collector metric/log canary failed to reach project stores')
print(json.dumps({'release':release,'namespace':namespace,'metric_roundtrip':True,'log_roundtrip':True,'otlp_metrics':True,'otlp_logs':True}))
