#!/usr/bin/env python3
"""Load a local image into one K3s node through a short-lived import pod.

The temporary HTTP endpoint serves one unguessable path to the selected node
only. The importer verifies SHA-256 before opening the node's containerd socket.
No registry credentials, host filesystem, or Kubernetes API token are mounted.
"""
import argparse,gzip,hashlib,http.server,json,os,shlex,shutil,socket,subprocess,tempfile,threading,time,uuid
p=argparse.ArgumentParser();p.add_argument('namespace');p.add_argument('node');p.add_argument('image');args=p.parse_args()
if not os.environ.get('KUBECONFIG'):p.error('Set KUBECONFIG explicitly')
def run(*cmd,**kw):return subprocess.run(cmd,check=True,**kw)
node=json.loads(subprocess.check_output(['kubectl','get','node',args.node,'-o','json']))
node_ip=next(a['address'] for a in node['status']['addresses'] if a['type']=='InternalIP')
with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as s:
 s.connect((node_ip,6443));source_ip=s.getsockname()[0]
name='convergence-image-loader';route='/'+uuid.uuid4().hex
with tempfile.TemporaryDirectory(prefix='convergence-image-') as directory:
 raw=directory+'/image.tar';archive=directory+'/image.tar.gz'
 run('docker','save','-o',raw,args.image)
 with open(raw,'rb') as src,gzip.open(archive,'wb',compresslevel=1) as dst:shutil.copyfileobj(src,dst)
 os.unlink(raw)
 with open(archive,'rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
 class Handler(http.server.BaseHTTPRequestHandler):
  def do_GET(self):
   if self.path!=route or self.client_address[0]!=node_ip:self.send_error(404);return
   self.send_response(200);self.send_header('Content-Length',str(os.path.getsize(archive)));self.end_headers()
   with open(archive,'rb') as f:shutil.copyfileobj(f,self.wfile)
  def log_message(self,*items):pass
 server=http.server.ThreadingHTTPServer((source_ip,0),Handler)
 thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
 url=f'http://{source_ip}:{server.server_port}{route}'
 command=f'set -eu; wget -T 60 -q -O /tmp/image.tar.gz {shlex.quote(url)}; echo "{digest}  /tmp/image.tar.gz" | sha256sum -c -; /bin/ctr --address=/run/k3s/containerd/containerd.sock --namespace=k8s.io images import /tmp/image.tar.gz'
 pod={'apiVersion':'v1','kind':'Pod','metadata':{'name':name,'namespace':args.namespace,'labels':{'app.kubernetes.io/part-of':'convergence-bootstrap'}},'spec':{
  'nodeSelector':{'kubernetes.io/hostname':args.node},'automountServiceAccountToken':False,'restartPolicy':'Never','activeDeadlineSeconds':240,
  'containers':[{'name':'import','image':'rancher/k3s:v1.35.6-k3s1','command':['/bin/sh','-c',command],
   'securityContext':{'runAsUser':0,'allowPrivilegeEscalation':False,'capabilities':{'drop':['ALL']}},
   'resources':{'requests':{'cpu':'50m','memory':'64Mi'},'limits':{'cpu':'1','memory':'256Mi'}},
   'volumeMounts':[{'name':'containerd','mountPath':'/run/k3s/containerd/containerd.sock'}]}],
  'volumes':[{'name':'containerd','hostPath':{'path':'/run/k3s/containerd/containerd.sock','type':'Socket'}}]}}
 try:
  run('kubectl','apply','-f','-',input=json.dumps(pod),text=True,stdout=subprocess.DEVNULL)
  deadline=time.monotonic()+250
  while time.monotonic()<deadline:
   current=json.loads(subprocess.check_output(['kubectl','-n',args.namespace,'get','pod',name,'-o','json']))
   phase=current['status']['phase']
   if phase=='Succeeded':print('Verified and imported',args.image,'on',args.node);break
   if phase=='Failed':
    run('kubectl','-n',args.namespace,'logs',name);raise RuntimeError('Image import failed')
   time.sleep(2)
  else:raise TimeoutError('Image import timed out')
 finally:
  run('kubectl','-n',args.namespace,'delete','pod',name,'--ignore-not-found','--wait=false',stdout=subprocess.DEVNULL)
  server.shutdown();server.server_close()
