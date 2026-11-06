#!/usr/bin/env python3
"""Download and validate IPinfo Lite locally; never logs credential-bearing URLs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time

import httpx
import maxminddb


def validate(path):
    with maxminddb.open_database(str(path)) as reader:
        metadata = reader.metadata()
        if 'ipinfo' not in metadata.database_type.lower() or not 0 < metadata.build_epoch <= time.time()+86400:
            raise ValueError('Unexpected MMDB metadata')
        for address in ('8.8.8.8', '1.1.1.1', '2001:4860:4860::8888'):
            record = reader.get(address)
            if not isinstance(record, dict) or not record.get('country_code') or not record.get('asn'):
                raise ValueError('IPinfo Lite country/ASN schema check failed')
        return metadata.build_epoch


def update(output, token):
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix='.ipinfo-', delete=False) as handle:
            temporary = Path(handle.name)
            digest, size = hashlib.sha256(), 0
            # Only a database download leaves this host. No observed addresses
            # or network traffic are submitted to the provider.
            with httpx.stream('GET', 'https://ipinfo.io/data/ipinfo_lite.mmdb',
                              params={'token': token}, follow_redirects=True, timeout=60) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes(1024*1024):
                    size += len(chunk)
                    if size > 256*1024*1024:
                        raise ValueError('Database exceeds 256 MiB limit')
                    handle.write(chunk)
                    digest.update(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        epoch = validate(temporary)
        if output.exists():
            try:
                old_epoch = validate(output)
            except Exception:
                old_epoch = 0  # A validated download can repair a corrupt old file.
            if epoch < old_epoch:
                raise ValueError('Refusing database downgrade')
        os.replace(temporary, output)
        directory = os.open(output.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return {'provider': 'IPinfo Lite', 'databaseBuiltAt': epoch, 'bytes': size, 'sha256': digest.hexdigest()}
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    token = os.environ.get('IPINFO_TOKEN')
    if not token:
        raise SystemExit('IPINFO_TOKEN is required in the private environment')
    try:
        print(json.dumps(update(args.output, token)))
    except Exception as exc:
        # HTTP exception messages and redirect URLs may contain the secret.
        response = getattr(exc, 'response', None)
        print(json.dumps({'status': 'failed', 'error': type(exc).__name__,
                          'httpStatus': response.status_code if response is not None else None}))
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
