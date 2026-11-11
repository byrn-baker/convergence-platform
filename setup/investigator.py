#!/usr/bin/env python3
"""Add Convergence's alert investigator to a NetClaw Risk as an on-demand member.

The investigator is a NetClaw member spec (integrations/netclaw/investigator.json):
the case tools, read-only evidence servers, the alert-triage skill and a tool
allowlist. This helper drives NetClaw's own tooling to provision it; it adds no
NetClaw code:

  python3 setup/investigator.py plan
  python3 setup/investigator.py apply
  python3 setup/investigator.py status
  python3 setup/investigator.py remove

Needs NetClaw with member specs (1.21.0 or later), a Border (`netclaw risk status`
shows role border) with its mesh daemon running, and setup/netclaw.py applied, so
the Border has convergence-case-mcp. A spec server the Border does not have is left
out, with its allowlist entries and skills; convergence-case-mcp is required.

`apply` stages the member's least-privilege .env (in2n-migrate.py --spec), builds
its home ~/.openclaw-<risk>-<name> (in2n-member-home.py --spec), registers it
on-demand with its launch command and writes its enrollment token into the staged
.env. The Border starts it on the first routed task. `plan` changes nothing.
`remove` undoes only what `apply` recorded. The token is never printed.
"""
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT/'integrations/netclaw/investigator.json'
REQUIRED = ('convergence-case-mcp',)
BASE_SERVERS = ('memory-mcp', 'gait-mcp')
# Skills that need one server; one whose server the Border lacks is left out with it.
SKILL_SERVERS = {'prometheus-monitoring': 'prometheus-mcp', 'grafana-observability': 'grafana-mcp',
                 'pyats-network': 'pyats-mcp', 'pyats-troubleshoot': 'pyats-mcp',
                 'pfsense-threat-intel': 'pfsense-mcp', 'alert-triage': 'convergence-case-mcp'}
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}')
MODEL = re.compile(r'([A-Za-z0-9][A-Za-z0-9_.-]{0,63}/)?[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}')
STATE = 'investigator.json'
ROUTE_HINT = 'alert-triage'


def state_dir(name):
    return Path(os.environ.get('XDG_STATE_HOME') or Path.home()/'.local/state')/'convergence'/f'investigator-{name}'


def read_state(path):
    try:
        return json.loads((path/STATE).read_text())
    except FileNotFoundError:
        return {}


def write_state(path, record):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    (path/STATE).write_text(json.dumps(record, indent=2)+'\n')


def api(base, path, body=None, timeout=30):
    """The mesh daemon's loopback API (the same one `netclaw risk` uses)."""
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(base+path, data=data, method='GET' if body is None else 'POST',
                                     headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read() or b'{}')
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read() or b'{}').get('error') or exc.reason
        except ValueError:
            detail = exc.reason
        raise RuntimeError(f'mesh daemon {path}: {detail}') from None
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError(f'mesh daemon not reachable at {base} ({exc}); start it, then retry') from None


def effective_spec(spec, border, name=None, model=None, auth=None):
    """The spec cut to the servers the Border has: (spec, dropped servers, dropped skills)."""
    spec = json.loads(json.dumps(spec))
    keep = [s for s in spec['servers'] if s in border]
    dropped = [s for s in spec['servers'] if s not in border]
    # NetClaw keeps a base server (memory-mcp, gait-mcp) only when the Border has it.
    usable = set(keep) | (set(BASE_SERVERS) & set(border))
    spec['servers'] = keep
    spec['tools']['allow'] = [entry for entry in spec['tools']['allow']
                              if '__' not in entry or entry.split('__', 1)[0] in usable]
    skills = spec.get('skills') or []
    spec['skills'] = [s for s in skills if s not in SKILL_SERVERS or SKILL_SERVERS[s] in usable]
    dropped_skills = [s for s in skills if s not in spec['skills']]
    if name:
        spec['name'] = name
    if model:
        spec['model'] = model
    if auth:
        spec['auth'] = auth
    return spec, dropped, dropped_skills


def subprocess_env(openclaw_dir):
    """Point NetClaw's runtime selection at this Border, whatever the caller's shell selects."""
    env = {k: v for k, v in os.environ.items()
           if k not in ('OPENCLAW_CONFIG_PATH', 'OPENCLAW_STATE_DIR', 'OPENCLAW_HOME', 'HERMES_HOME')}
    env.update(NETCLAW_RUNTIME='openclaw', OPENCLAW_HOME=str(openclaw_dir),
               OPENCLAW_STATE_DIR=str(openclaw_dir), OPENCLAW_CONFIG_PATH=str(openclaw_dir/'openclaw.json'))
    return env


def run(cmd, env, check=True):
    result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=600)
    if check and result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()[-5:]
        raise RuntimeError(f'{Path(cmd[1]).name} failed ({result.returncode}): ' + ' | '.join(detail))
    return result


def options(args):
    netclaw = Path(args.netclaw_dir).expanduser().absolute()
    openclaw_dir = Path(args.openclaw_dir).expanduser().absolute()
    if args.name is not None and not NAME.fullmatch(args.name):
        raise ValueError(f'invalid --name {args.name!r}')
    if args.model is not None and not MODEL.fullmatch(args.model):
        raise ValueError('--model must be an Anthropic model id or <provider>/<model> for a provider the '
                         'Border configures (NetClaw 1.22.0+; NetClaw validates it)')
    if args.auth == 'subscription' and not args.model:
        raise ValueError("--auth subscription needs --model anthropic/<id> or openai/<id>")
    return netclaw, openclaw_dir


def member_home(risk, name):
    return Path.home()/f'.openclaw-{risk}-{name}'


def plan(args):
    netclaw, openclaw_dir = options(args)
    conflicts, notes = [], []
    profiles = netclaw/'scripts/in2n-profiles.py'
    if not profiles.is_file() or 'def load_member_spec' not in profiles.read_text():
        conflicts.append(f'{netclaw} has no member spec support: NetClaw 1.21.0 or later is required '
                         '(--netclaw-dir names the checkout)')
    config_path = openclaw_dir/'openclaw.json'
    try:
        border = set(((json.loads(config_path.read_text()).get('mcp') or {}).get('servers') or {}))
    except (OSError, ValueError) as exc:
        raise ValueError(f'cannot read the Border config {config_path}: {exc}') from None
    for server in REQUIRED:
        if server not in border:
            conflicts.append(f'the Border has no {server}: run `python3 setup/netclaw.py apply` first')
    spec, dropped, dropped_skills = effective_spec(json.loads(Path(args.spec).read_text()), border,
                                                   args.name, args.model, args.auth)
    name = spec['name']
    if dropped:
        notes.append('left out (not on the Border): ' + ', '.join(dropped)
                     + (f'; skills {", ".join(dropped_skills)}' if dropped_skills else ''))
    risk = api(args.api, '/n2n/risk')
    if risk.get('role') != 'border':
        conflicts.append(f'this claw is {risk.get("role") or "unknown"}, not a Border: '
                         'promote it first (`netclaw risk role border <risk>`)')
    risk_name = risk.get('risk_name') or ''
    state = state_dir(name)
    record = read_state(state)
    ours = record.get('member_id') == f'{risk_name}/{name}'
    done = ours and record.get('member_added') and record.get('token_written')
    if ours and record.get('member_added') and not record.get('token_written'):
        conflicts.append('a previous apply registered the member but did not write its enrollment token: '
                         'run remove, then apply again')
    members = {m.get('member_id') for m in api(args.api, '/n2n/members').get('members', [])}
    if f'{risk_name}/{name}' in members and not ours:
        conflicts.append(f'member {risk_name}/{name} already exists and was not added by this helper '
                         '(use --name for another member, or `netclaw risk remove` it)')
    home = member_home(risk_name, name)
    if (home.exists() or home.is_symlink()) and not (ours and record.get('home_created')):
        conflicts.append(f'{home} already exists and was not created by this helper')
    staging = state/'staging'
    result = {'member_id': f'{risk_name}/{name}', 'home': str(home), 'staging': str(staging),
              'netclaw_dir': str(netclaw), 'border_config': str(config_path),
              'servers': spec['servers'], 'skills': spec['skills'], 'spec': spec,
              'route_hint': ROUTE_HINT, 'already_applied': bool(done),
              'conflicts': conflicts, 'notes': notes}
    if not conflicts:
        # NetClaw's own validation, against this Border: errors refuse, warnings are notes.
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        candidate = state/'spec.plan.json'
        candidate.write_text(json.dumps(spec, indent=2)+'\n')
        check = run([sys.executable, str(profiles), 'spec', str(candidate), '--border-config', str(config_path)],
                    subprocess_env(openclaw_dir), check=False)
        candidate.unlink()
        if not record:
            try:
                state.rmdir()
            except OSError:
                pass
        if check.returncode:
            conflicts.append('NetClaw refused the spec: ' + check.stderr.strip())
        else:
            notes += [line for line in check.stderr.splitlines() if line.startswith('warning:')]
            result['scope'] = json.loads(check.stdout).get('scope')
    return result


def secure_append(path, line):
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise RuntimeError(f'{path} is not a private file of this user; not writing the token')
        os.write(fd, (line+'\n').encode())
    finally:
        os.close(fd)


def apply(args):
    result = plan(args)
    if result['conflicts']:
        raise ValueError('; '.join(result['conflicts']))
    if result['already_applied']:
        return {'status': 'already applied', **status(args)}
    netclaw, openclaw_dir = options(args)
    env = subprocess_env(openclaw_dir)
    name = result['member_id'].split('/', 1)[1]
    risk = result['member_id'].split('/', 1)[0]
    state, staging = state_dir(name), Path(result['staging'])
    record = {'member_id': result['member_id'], 'risk': risk, 'name': name, 'api': args.api,
              'netclaw_dir': str(netclaw), 'openclaw_dir': str(openclaw_dir)}
    spec, spec_path = result['spec'], state/'spec.json'
    record.update({k: v for k, v in read_state(state).items() if k in ('staging_created', 'home_created')})
    write_state(state, record)
    spec_path.write_text(json.dumps(spec, indent=2)+'\n')
    if not staging.exists():
        record['staging_created'] = True
    write_state(state, record)
    run([sys.executable, str(netclaw/'scripts/in2n-migrate.py'), '--risk', risk, '--spec', str(spec_path),
         '--specs-only', '--staging', str(staging), '--border-endpoint', args.border_endpoint, '--hot', ''], env)
    member_dir = staging/'members'/name
    member_env, launcher = member_dir/'.env', member_dir/'run.sh'
    if not member_env.is_file() or not launcher.is_file():
        raise RuntimeError(f'in2n-migrate.py did not stage {member_dir}')
    home = Path(result['home'])
    record['home'] = str(home)
    record['home_created'] = record.get('home_created') or not home.exists()
    write_state(state, record)
    home_out = run([sys.executable, str(netclaw/'scripts/in2n-member-home.py'), '--runtime', 'openclaw',
                    '--risk', risk, '--spec', str(spec_path), '--member-env', str(member_env),
                    '--border-config', str(openclaw_dir/'openclaw.json')], env).stdout
    # A subscription member signs in once on its own home; NetClaw prints the exact command.
    login = [line.strip().rstrip(')') for line in home_out.splitlines() if 'openclaw models auth login' in line]
    added = api(args.api, '/n2n/members/add', {
        'name': name, 'profile': 'custom', 'specialty': result['scope'],
        'launch_cmd': 'bash ' + shlex.quote(str(launcher)), 'on_demand': True})
    if 'error' in added:
        raise RuntimeError(f'mesh daemon refused the member: {added["error"]}')
    record['member_added'] = True
    write_state(state, record)
    secure_append(member_env, f'N2N_ENROLLMENT_TOKEN={added["enrollment_token"]}')
    record['token_written'] = True
    write_state(state, record)
    return {'status': 'applied', 'member_id': result['member_id'], 'home': str(home),
            'staged_env': str(member_env), 'launch': str(launcher), 'mode': 'on-demand',
            'servers': result['servers'], 'skills': result['skills'], 'model': spec.get('model'),
            'notes': result['notes'] + [f'Sign the member in before its first task: {line}' for line in login] + [
                f'Test it: netclaw risk route "<an alert case question>" {ROUTE_HINT} (the Border starts the member)',
                'The Border\'s alert agent delegates only when it has the n2n tools; see '
                'docs/convergence/setup/investigator.md.']}


def status(args):
    name = args.name or json.loads(Path(args.spec).read_text())['name']
    record = read_state(state_dir(name))
    result = {'applied': bool(record), 'member_id': record.get('member_id'), 'home': record.get('home')}
    if record.get('member_id'):
        try:
            members = api(record.get('api') or args.api, '/n2n/members').get('members', [])
            match = [m for m in members if m.get('member_id') == record['member_id']]
            result['member'] = ({k: match[0].get(k) for k in ('state', 'profile', 'specialty_count', 'node_type')}
                                if match else 'not registered')
        except RuntimeError as exc:
            result['member'] = str(exc)
    return result


def owned_dir_problem(path, expected):
    """Why `path` must not be deleted, or None: it must be exactly the recorded directory."""
    if path != expected:
        return f'recorded path {path} is not {expected}'
    if path.is_symlink() or not path.is_dir():
        return 'not a plain directory'
    if path.stat().st_uid != os.getuid():
        return 'not owned by this user'
    if path in (Path('/'), Path.home()) or len(path.parts) < 3:
        return 'refusing a top-level directory'
    return None


def remove(args):
    name = args.name or json.loads(Path(args.spec).read_text())['name']
    state = state_dir(name)
    record = read_state(state)
    if not record:
        return {'status': 'nothing to remove', 'removed': [], 'kept': []}
    removed, kept = [], []
    if record.get('member_added'):
        out = api(record.get('api') or args.api, '/n2n/members/remove', {'member_id': record['member_id']})
        (removed if out.get('removed') else kept).append(f'member {record["member_id"]}'
                                                         + ('' if out.get('removed') else ' (daemon: not removed)'))
        record.pop('member_added')
        write_state(state, record)
    home = record.get('home')
    if home and record.get('home_created'):
        expected = member_home(record['risk'], record['name'])
        problem = owned_dir_problem(Path(home), expected) if Path(home).exists() else None
        if problem:
            kept.append(f'{home} ({problem})')
        elif Path(home).exists():
            shutil.rmtree(home)
            removed.append(home)
    elif home and Path(home).exists():
        kept.append(f'{home} (existed before apply)')
    staging = state/'staging'
    if record.get('staging_created') and staging.exists():
        problem = owned_dir_problem(staging, state_dir(name)/'staging')
        if problem:
            kept.append(f'{staging} ({problem})')
        else:
            shutil.rmtree(staging)
            removed.append(str(staging))
    (state/'spec.json').unlink(missing_ok=True)
    (state/STATE).unlink(missing_ok=True)
    try:
        state.rmdir()
        removed.append(str(state))
    except OSError:
        kept.append(f'{state} (not empty)')
    return {'status': 'removed', 'removed': removed, 'kept': kept}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('command', choices=('plan', 'apply', 'status', 'remove'))
    parser.add_argument('--spec', default=str(SPEC), help='member spec file (default: the Convergence investigator)')
    parser.add_argument('--name', help='member name (default: the spec name, alert-investigator)')
    parser.add_argument('--model', help='the member\'s model: an Anthropic model id, or <provider>/<model> for a '
                        'provider the Border configures (default: NetClaw\'s member tier)')
    parser.add_argument('--auth', choices=('api-key', 'subscription'),
                        help='subscription: the member signs in with its own Claude or ChatGPT login '
                             '(anthropic/<id> or openai/<id>); apply prints the login command')
    parser.add_argument('--netclaw-dir', default=os.environ.get('NETCLAW_DIR') or '~/netclaw')
    parser.add_argument('--openclaw-dir', default=os.environ.get('OPENCLAW_HOME') or '~/.openclaw',
                        help='the Border\'s OpenClaw home')
    parser.add_argument('--api', default=os.environ.get('BGP_API') or 'http://127.0.0.1:8179',
                        help='the mesh daemon API (loopback)')
    parser.add_argument('--border-endpoint', default='127.0.0.1:11790',
                        help='where the member dials the Border\'s iN2N listener')
    args = parser.parse_args(argv)
    try:
        result = {'plan': plan, 'apply': apply, 'status': status, 'remove': remove}[args.command](args)
        print(json.dumps(result, indent=2))
        return 1 if args.command == 'plan' and result['conflicts'] else 0
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as exc:
        print(f'investigator: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
