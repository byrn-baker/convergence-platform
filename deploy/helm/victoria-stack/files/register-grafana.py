"""Merge a release's provisioning into shared Grafana using narrowly scoped RBAC."""
import copy
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
import yaml


def merge_sources(existing, incoming):
    merged=copy.deepcopy(existing or {'apiVersion':1,'datasources':[]})
    sources=merged.setdefault('datasources',[])
    for source in incoming:
        assert source.get('uid') and source.get('isDefault') is False
        other=next((s for s in sources if s.get('uid')==source['uid']),None)
        if other is None:
            if any(s.get('name')==source['name'] for s in sources):
                raise ValueError('Another data source already uses the requested name')
            sources.append(source)
        else:
            other.update(source)
    # Existing deleteDatasources/prune directives are not expanded or replaced.
    return merged


def project_dashboard(raw, prefix, project, aliases):
    board=copy.deepcopy(raw)
    board['id']=None
    board['uid']=prefix+'-'+str(board.get('uid','overview'))[:24]
    board['title']=project+' / '+board.get('title','Overview')
    board['tags']=list(dict.fromkeys(board.get('tags',[])+['victoria-stack',project]))
    def replace(value):
        if isinstance(value,dict):
            for key,item in list(value.items()):
                if key=='datasource' and isinstance(item,str) and item in aliases:value[key]=aliases[item]
                elif key=='uid' and isinstance(item,str) and item in aliases:value[key]=aliases[item]
                elif key=='uid' and item=='victoria-stack-metrics':value[key]=aliases['prometheus']
                else:replace(item)
        elif isinstance(value,list):
            for item in value:replace(item)
    replace(board)
    return board


class Kubernetes:
    def __init__(self):
        self.base='https://'+os.environ['KUBERNETES_SERVICE_HOST']+':'+os.environ['KUBERNETES_SERVICE_PORT_HTTPS']
        root=Path('/var/run/secrets/kubernetes.io/serviceaccount')
        self.token=(root/'token').read_text().strip()
        self.context=ssl.create_default_context(cafile=str(root/'ca.crt'))
    def request(self,path,method='GET',data=None):
        headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/merge-patch+json'}
        req=urllib.request.Request(self.base+path,data=json.dumps(data).encode() if data is not None else None,headers=headers,method=method)
        with urllib.request.urlopen(req,context=self.context,timeout=20) as response:return json.load(response)


def update_configmap(api,namespace,name,edit):
    path=f'/api/v1/namespaces/{namespace}/configmaps/{name}'
    for attempt in range(6):
        current=api.request(path)
        data=edit(dict(current.get('data',{})))
        if data==current.get('data',{}):return False
        # ConfigMaps have a 1 MiB size limit; fail before touching shared Grafana.
        if sum(len(k.encode())+len(v.encode()) for k,v in data.items())>950000:
            raise ValueError('Shared Grafana ConfigMap needs sharding before adding this release')
        try:
            api.request(path,'PATCH',{'metadata':{'resourceVersion':current['metadata']['resourceVersion']},'data':data})
            return True
        except urllib.error.HTTPError as error:
            if error.code!=409 or attempt==5:raise
            time.sleep(.25)


def main():
    api=Kubernetes();namespace=os.environ['RELEASE_NAMESPACE'];release=os.environ['RELEASE_NAME']
    uid=os.environ['STACK_UID'];project=namespace+'/'+release
    sources=[];dashboards={}
    selector=urllib.parse.urlencode({'labelSelector':'netclaw.io/grafana-bundle='+release})
    bundles=api.request(f'/api/v1/namespaces/{namespace}/configmaps?'+selector)['items']
    for bundle in bundles:
        data=bundle.get('data',{})
        sources.extend(json.loads(data.get('datasources.json','[]')))
        for key,raw in data.items():
            if key.endswith('.json') and key!='datasources.json':dashboards[key]=json.loads(raw)
    if not sources:raise ValueError('No data sources found for release')
    if any(not s.get('uid','').startswith(uid+'-') or not s.get('name','').startswith(project+' / ') for s in sources):
        raise ValueError('Data-source identities must belong to this namespace/release')
    aliases={s['jsonData']['netclawAlias']:s['uid'] for s in sources}
    aliases.update({'victoriametrics-longterm':aliases['prometheus'],'victorialogs-longterm':aliases['victorialogs']})
    projected={key:project_dashboard(board,uid,project,aliases) for key,board in dashboards.items()}
    if len({b['uid'] for b in projected.values()})!=len(projected):
        raise ValueError('Dashboard UIDs must remain unique after project prefixing')
    target=os.environ['GRAFANA_NAMESPACE']
    def edit_sources(data):
        key=os.environ['DATASOURCE_KEY']
        data[key]=yaml.safe_dump(merge_sources(yaml.safe_load(data.get(key,'apiVersion: 1\ndatasources: []')),sources),sort_keys=False)
        return data
    changed=update_configmap(api,target,os.environ['DATASOURCE_CONFIGMAP'],edit_sources)
    def edit_boards(data):
        for key,board in projected.items():
            data['project-'+uid+'-'+key]=json.dumps(board,separators=(',',':'))
        return data
    changed=update_configmap(api,target,os.environ['DASHBOARD_CONFIGMAP'],edit_boards) or changed
    if changed:
        api.request(f'/apis/apps/v1/namespaces/{target}/deployments/'+os.environ['GRAFANA_DEPLOYMENT'],'PATCH',
            {'spec':{'template':{'metadata':{'annotations':{'netclaw.io/provisioned-'+uid:str(time.time_ns())}}}}})
    print(json.dumps({'project':project,'sources':len(sources),'dashboards':len(dashboards),'grafana_updated':changed}))


if __name__=='__main__':main()
