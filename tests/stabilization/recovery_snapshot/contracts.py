"""Pure contracts shared by the source reader and offline acquisition store."""
import hashlib
import json
import os
import stat
import uuid

BLOCK = 1024 * 1024
CHUNK = 64 * BLOCK
RATE = 4 * BLOCK


class Refusal(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def identity(info):
    return dict(device_major=os.major(info.st_dev), device_minor=os.minor(info.st_dev),
                inode=info.st_ino, size=info.st_size, mtime_ns=info.st_mtime_ns,
                ctime_ns=info.st_ctime_ns, uid=info.st_uid, gid=info.st_gid,
                mode=stat.S_IMODE(info.st_mode), nlink=info.st_nlink)


def valid_uuid(value):
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise Refusal('Invalid canonical UUID') from None
    return value


def sha(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise Refusal('Invalid SHA256')
    return value


def fingerprint(value):
    fields = {'version', 'snapshot_uid', 'file', 'metadata_file', 'metadata_sha256',
              'created', 'labels', 'identities'}
    if not isinstance(value, dict) or set(value) != fields or type(value['version']) is not int or value['version'] != 1:
        raise Refusal('Invalid fingerprint schema')
    valid_uuid(value['snapshot_uid'])
    keys = {'device_major', 'device_minor', 'inode', 'size', 'mtime_ns', 'ctime_ns',
            'uid', 'gid', 'mode', 'nlink'}
    for name in ('file', 'metadata_file'):
        item = value[name]
        if not isinstance(item, dict) or set(item) != keys or any(type(v) is not int or v < 0 for v in item.values()):
            raise Refusal('Invalid file identity')
        if item['nlink'] != 1 or item['mode'] & 0o022:
            raise Refusal('Source must be single-linked and not writable by others')
    if value['file']['size'] <= 0 or value['file']['size'] % BLOCK or not 0 < value['metadata_file']['size'] <= 4096:
        raise Refusal('Invalid source geometry')
    sha(value['metadata_sha256'])
    if not isinstance(value['created'], str) or not value['created'] or not isinstance(value['labels'], dict):
        raise Refusal('Invalid snapshot metadata')
    ids = value['identities']
    if not isinstance(ids, dict) or not ids or ids.get('snapshot_uid') != value['snapshot_uid']:
        raise Refusal('Snapshot identity missing')
    if any(not isinstance(k, str) or not isinstance(v, str) or not v for k, v in ids.items()):
        raise Refusal('Invalid watchdog identities')
    if len(canonical(value)) > 32768:
        raise Refusal('Oversized fingerprint')
    return value


def range_check(config, offset, length):
    if (type(offset) is not int or type(length) is not int or offset < 0 or
            length <= 0 or length > CHUNK or offset % BLOCK or length % BLOCK or
            offset + length > config['file']['size']):
        raise Refusal('Invalid bounded aligned range')


def binding(config, attempt, offset, length):
    fingerprint(config)
    range_check(config, offset, length)
    valid_uuid(attempt)
    return dict(fingerprint=digest(config), snapshot_uid=config['snapshot_uid'],
                attempt=attempt, offset=offset, length=length)


def load_json(path, maximum=65536):
    with open(path, 'rb') as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise Refusal('Oversized JSON')
    def pairs(items):
        obj = {}
        for key, value in items:
            if key in obj:
                raise Refusal('Duplicate JSON key')
            obj[key] = value
        return obj
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(Refusal('Non-finite JSON')))
