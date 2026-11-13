import struct
from .bitio import bitpack, bitunpack, packed_len, zigzag, unzigzag, varints, read_varints, f32

HEADER = struct.Struct('<4sIIHHQQ16s')

VERT_STREAM_FLOAT16 = 4
VERT_STREAM_FLOAT32 = 5
VERT_QUANTIZED = 6

FACE_UINT32 = 0
FACE_UINT16 = 1
FACE_DELTA_INT32 = 2
FACE_DELTA_INT16 = 3
FACE_BITPACKED = 4
FACE_DELTA_VARINT = 5

SOURCE_TAGS = {'.obj': 1, '.ply': 2, '.stl': 3, '.glb': 4, '.gltf': 4}


def axis_bounds(values):
    if not values:
        return 0.0, 0.0
    return f32(min(values)), f32(max(values))


def quantize_axis(values, bits, lo, hi):
    if hi == lo:
        return [0] * len(values)
    top = (1 << bits) - 1
    s = top / (hi - lo)
    return [min(top, max(0, int(round((v - lo) * s)))) for v in values]


def dequantize_axis(q, bits, lo, hi):
    top = (1 << bits) - 1
    if top == 0 or hi == lo:
        return [lo] * len(q)
    s = (hi - lo) / top
    return [lo + v * s for v in q]


def shuffle(data, size):
    if size <= 1 or not data or len(data) % size:
        return data
    n = len(data) // size
    out = bytearray(len(data))
    mv = memoryview(data)
    for i in range(size):
        out[i * n:(i + 1) * n] = mv[i::size]
    return bytes(out)


def unshuffle(data, size):
    if size <= 1 or not data or len(data) % size:
        return data
    n = len(data) // size
    out = bytearray(len(data))
    mv = memoryview(data)
    for i in range(size):
        out[i::size] = mv[i * n:(i + 1) * n]
    return bytes(out)


def pack_vertices(positions, bits):
    if bits <= 0:
        streams = [shuffle(b''.join(struct.pack('<e', p[a]) for p in positions), 2) for a in range(3)]
        return struct.pack('<BIII', VERT_STREAM_FLOAT16, *map(len, streams)) + b''.join(streams)
    bounds = [axis_bounds([p[a] for p in positions]) for a in range(3)]
    streams = [bitpack(quantize_axis([p[a] for p in positions], bits, *bounds[a]), bits) for a in range(3)]
    head = struct.pack('<BB6f3I', VERT_QUANTIZED, bits, bounds[0][0], bounds[0][1], bounds[1][0], bounds[1][1], bounds[2][0], bounds[2][1], *map(len, streams))
    return head + b''.join(streams)


def unpack_vertices(data, count):
    flag = data[0]
    if flag in (VERT_STREAM_FLOAT16, VERT_STREAM_FLOAT32):
        _, lx, ly, lz = struct.unpack_from('<BIII', data)
        fmt, size = ('e', 2) if flag == VERT_STREAM_FLOAT16 else ('f', 4)
        pos = 13
        axes = []
        for ln in (lx, ly, lz):
            raw = unshuffle(data[pos:pos + ln], size)
            axes.append(list(struct.unpack('<' + fmt * (ln // size), raw)))
            pos += ln
        return list(zip(*axes))
    if flag == VERT_QUANTIZED:
        v = struct.unpack_from('<BB6f3I', data)
        bits = v[1]
        bounds = [(v[2], v[3]), (v[4], v[5]), (v[6], v[7])]
        pos = 38
        axes = []
        for a, ln in enumerate(v[8:11]):
            q = bitunpack(data[pos:pos + ln], bits, count)
            axes.append(dequantize_axis(q, bits, *bounds[a]))
            pos += ln
        return list(zip(*axes))
    raise ValueError(f'unsupported BBM vertex flag {flag}')


def pack_faces(faces, vertex_count):
    flat = [i for f in faces for i in f]
    out = []
    if vertex_count <= 0xFFFF:
        out.append(bytes([FACE_UINT16]) + struct.pack(f'<{len(flat)}H', *flat))
    else:
        out.append(bytes([FACE_UINT32]) + struct.pack(f'<{len(flat)}I', *flat))
    deltas = []
    prev = 0
    for i in flat:
        deltas.append(i - prev)
        prev = i
    if all(-32768 <= d <= 32767 for d in deltas):
        out.append(bytes([FACE_DELTA_INT16]) + struct.pack(f'<{len(deltas)}h', *deltas))
    else:
        out.append(bytes([FACE_DELTA_INT32]) + struct.pack(f'<{len(deltas)}i', *deltas))
    bits = max(1, max(flat, default=0).bit_length())
    out.append(struct.pack('<BBI', FACE_BITPACKED, bits, len(flat)) + bitpack(flat, bits))
    out.append(struct.pack('<BI', FACE_DELTA_VARINT, len(flat)) + varints(zigzag(d) for d in deltas))
    return min(out, key=len)


def unpack_faces(data):
    flag = data[0]
    body = data[1:]
    if flag == FACE_UINT16:
        flat = struct.unpack(f'<{len(body) // 2}H', body)
    elif flag == FACE_UINT32:
        flat = struct.unpack(f'<{len(body) // 4}I', body)
    elif flag in (FACE_DELTA_INT16, FACE_DELTA_INT32):
        fmt, size = ('h', 2) if flag == FACE_DELTA_INT16 else ('i', 4)
        flat = []
        prev = 0
        for d in struct.unpack(f'<{len(body) // size}{fmt}', body):
            prev += d
            flat.append(prev)
    elif flag == FACE_BITPACKED:
        _, bits, n = struct.unpack_from('<BBI', data)
        flat = bitunpack(data[6:], bits, n)
    elif flag == FACE_DELTA_VARINT:
        _, n = struct.unpack_from('<BI', data)
        zz, _ = read_varints(data, n, 5)
        flat = []
        prev = 0
        for z in zz:
            prev += unzigzag(z)
            flat.append(prev)
    else:
        raise ValueError(f'unsupported BBM face flag {flag}')
    return [tuple(flat[i:i + 3]) for i in range(0, len(flat) - 2, 3)]


def encode_record(positions, faces, name='model', bits=12, source='.glb'):
    vblock = pack_vertices(positions, bits)
    fblock = pack_faces(faces, len(positions))
    tag = b'BBM' + bytes([SOURCE_TAGS.get(source, 4)])
    nm = name.encode('utf-8')[:16].ljust(16, b'\x00')
    return HEADER.pack(tag, len(positions), len(faces), 0, 1, len(vblock), len(fblock), nm) + vblock + fblock


def decode_record(data, pos=0):
    tag, vc, fc, comp, _, vl, fl, nm = HEADER.unpack_from(data, pos)
    if tag[:3] != b'BBM':
        raise ValueError('not a BBM record')
    if comp != 0:
        raise ValueError('compressed BBM records are not allowed inside BNR')
    p = pos + HEADER.size
    positions = unpack_vertices(data[p:p + vl], vc)
    faces = unpack_faces(data[p + vl:p + vl + fl])
    name = nm.rstrip(b'\x00').decode('utf-8', errors='replace')
    return positions, faces, name, HEADER.size + vl + fl
