"""Fail-closed Linux statx qualification for the exact direct-I/O fd and buffer."""
import ctypes
import os
import struct
import sys

from contracts import BLOCK, Refusal

STATX_DIOALIGN = 0x2000
AT_EMPTY_PATH = 0x1000
AT_STATX_DONT_SYNC = 0x4000


def statx_alignment(fd):
    if sys.platform != 'linux':
        raise Refusal('Linux statx DIO alignment required')
    try:
        statx = ctypes.CDLL(None, use_errno=True).statx
    except (AttributeError, OSError) as error:
        raise Refusal('libc statx unavailable') from error
    statx.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                     ctypes.c_uint, ctypes.c_void_p]
    statx.restype = ctypes.c_int
    # Linux UAPI struct statx is 256 bytes. These fixed-width fields retain
    # their ABI offsets even when newer kernels assign previously spare bytes.
    result = ctypes.create_string_buffer(256)
    if statx(fd, b'', AT_EMPTY_PATH | AT_STATX_DONT_SYNC, STATX_DIOALIGN, result):
        raise Refusal('statx DIO alignment failed: ' + os.strerror(ctypes.get_errno()))
    mask = struct.unpack_from('=I', result.raw, 0)[0]
    memory, offset = struct.unpack_from('=II', result.raw, 152)
    if not mask & STATX_DIOALIGN or not memory or not offset:
        raise Refusal('statx DIO alignment missing or unsupported')
    return mask, memory, offset


def qualify(fd, buffer, offset, length):
    mask, memory_align, offset_align = statx_alignment(fd)
    address = ctypes.addressof(ctypes.c_char.from_buffer(buffer))
    if (address % memory_align or offset % offset_align or length % offset_align or
            BLOCK % offset_align or len(buffer) != BLOCK):
        raise Refusal('Direct IO buffer or range alignment incompatible')
    return dict(statx_mask=mask, memory_alignment=memory_align,
                offset_alignment=offset_align, buffer_address_mod=0,
                offset_mod=offset % offset_align, length_mod=length % offset_align,
                block_size=BLOCK)


def validate_proof(proof, offset, length):
    fields = {'statx_mask', 'memory_alignment', 'offset_alignment',
              'buffer_address_mod', 'offset_mod', 'length_mod', 'block_size'}
    if (not isinstance(proof, dict) or set(proof) != fields or
            any(type(value) is not int for value in proof.values())):
        raise Refusal('Direct IO alignment proof missing or invalid')
    memory, alignment = proof['memory_alignment'], proof['offset_alignment']
    if (not 0 <= proof['statx_mask'] <= 0xffffffff or
            not proof['statx_mask'] & STATX_DIOALIGN or
            not 0 < memory <= 0xffffffff or not 0 < alignment <= 0xffffffff or
            proof['block_size'] != BLOCK or BLOCK % alignment or
            offset % alignment or length % alignment or
            any(proof[key] != 0 for key in ('buffer_address_mod', 'offset_mod', 'length_mod'))):
        raise Refusal('Direct IO alignment proof incompatible')
