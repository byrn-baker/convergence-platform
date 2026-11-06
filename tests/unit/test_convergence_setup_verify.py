"""Acceptance checks must exercise database recovery, not poll cached liveness forever."""
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import sys
from urllib.error import HTTPError

ROOT=Path(__file__).resolve().parents[2]
SCRIPTS=ROOT/'workspace/skills/convergence-setup/scripts'
sys.path.insert(0,str(SCRIPTS))
spec=importlib.util.spec_from_file_location('setup_verify',SCRIPTS/'verify.py')
verify=importlib.util.module_from_spec(spec);spec.loader.exec_module(verify)


def test_case_read_recovers_cached_database_health_before_verdict(tmp_path,monkeypatch):
    bundle=tmp_path/'bundle';bundle.mkdir()
    (bundle/'deployment.json').write_text(json.dumps({'options':{'site':'lab'}}))
    @contextmanager
    def endpoints(*_):
        yield {'api':'http://api','metrics':'http://metrics','alertmanager':'http://alerts'},'fixture-token',None
    recovered=False
    calls=[]
    def request(url,**kwargs):
        nonlocal recovered
        calls.append((url,kwargs))
        if '/healthz' in url:
            return {'features':{'diary':'available' if recovered else 'unavailable'}}
        if '/api/sites' in url:return {'sites':[{'id':'lab'}]}
        if '/api/events' in url:
            if not kwargs.get('token') or 'unassigned-' in url:
                raise HTTPError(url,403,'forbidden',{},None)
            recovered=True
            return {'events':[]}
        if '/query?' in url:return {'data':{'result':[{'value':[0,'1']}]}}
        if '/api/v2/status' in url:return {'versionInfo':{'version':'fixture'}}
        raise AssertionError('Unexpected endpoint')
    monkeypatch.setattr(verify,'endpoints',endpoints)
    monkeypatch.setattr(verify,'request',request)
    result=verify.verify(ROOT,bundle,wait_seconds=0)
    assert result['passed'],result['checks']
    assert all('data' not in kwargs and 'method' not in kwargs for _,kwargs in calls)
