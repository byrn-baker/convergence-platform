"""Root target CLI: receive unverified chunks, accept evidence, inspect or assemble."""
import argparse
import json
import signal
import sys

from contracts import CHUNK, Refusal, digest, load_json, valid_uuid
from target import Store, secure_path, validate_target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target-contract', required=True)
    parser.add_argument('--acquisition', required=True, help='Coordinator-persisted acquisition UUID')
    sub = parser.add_subparsers(dest='action', required=True)
    init = sub.add_parser('init')
    init.add_argument('--fingerprint', required=True)
    receive = sub.add_parser('receive')
    receive.add_argument('--attempt', required=True)
    receive.add_argument('--offset', type=int, required=True)
    receive.add_argument('--length', type=int, default=CHUNK)
    receive.add_argument('--deadline', type=int, default=90)
    accept = sub.add_parser('accept')
    accept.add_argument('--attempt', required=True)
    accept.add_argument('--source-receipt', required=True)
    accept.add_argument('--source-exit-code', type=int, required=True)
    sub.add_parser('status')
    sub.add_parser('assemble')
    args = parser.parse_args()
    materials, reserve = validate_target(load_json(secure_path(args.target_contract, owner=0)))
    valid_uuid(args.acquisition)
    if args.action == 'init':
        Store.create(materials, args.acquisition, load_json(secure_path(args.fingerprint, owner=0)), reserve)
        result = {'status': 'initialized', 'acquisition': args.acquisition}
    else:
        store = Store(materials / args.acquisition, reserve)
        if args.action == 'receive':
            if not 1 <= args.deadline <= 300:
                raise Refusal('Invalid receive deadline')
            def cancel(*_):
                raise Refusal('Receiver deadline or cancellation')
            for sig in (signal.SIGALRM, signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, cancel)
            signal.alarm(args.deadline)
            try:
                result = store.receive(args.attempt, args.offset, args.length, sys.stdin.buffer)
            finally:
                signal.alarm(0)
        elif args.action == 'accept':
            result = store.accept(args.attempt, load_json(secure_path(args.source_receipt, owner=0)), args.source_exit_code)
        elif args.action == 'status':
            with store.lock():
                result = {'status': 'verified', 'acquisition': args.acquisition,
                          'fingerprint': digest(store.config),
                          'snapshot_uid': store.config['snapshot_uid'], 'chunks': store.verified()}
        else:
            result = store.assemble()
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
