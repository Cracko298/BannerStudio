import struct


def bitpack(values, bits):
    if bits <= 0:
        return b''
    out = bytearray()
    acc = 0
    n = 0
    mask = (1 << bits) - 1
    for v in values:
        if v < 0 or v > mask:
            raise ValueError(f'{v} does not fit in {bits} bits')
        acc |= v << n
        n += bits
        while n >= 8:
            out.append(acc & 0xFF)
            acc >>= 8
            n -= 8
    if n:
        out.append(acc & 0xFF)
    return bytes(out)


def bitunpack(data, bits, count):
    if bits <= 0:
        return [0] * count
    out = []
    acc = 0
    n = 0
    mask = (1 << bits) - 1
    it = iter(data)
    while len(out) < count:
        while n < bits:
            acc |= next(it) << n
            n += 8
        out.append(acc & mask)
        acc >>= bits
        n -= bits
    return out


def packed_len(count, bits):
    return (count * bits + 7) // 8


def zigzag(v):
    return (v << 1) if v >= 0 else ((-v) << 1) - 1


def unzigzag(v):
    return (v >> 1) ^ -(v & 1)


def varints(values):
    out = bytearray()
    for v in values:
        while v >= 0x80:
            out.append((v & 0x7F) | 0x80)
            v >>= 7
        out.append(v)
    return bytes(out)


def read_varints(data, count, pos=0):
    out = []
    while len(out) < count:
        v = 0
        shift = 0
        while True:
            b = data[pos]
            pos += 1
            v |= (b & 0x7F) << shift
            shift += 7
            if b < 0x80:
                break
        out.append(v)
    return out, pos


def f32(v):
    return struct.unpack('<f', struct.pack('<f', v))[0]


def half(v):
    v = max(-65504.0, min(65504.0, float(v)))
    return struct.unpack('<e', struct.pack('<e', v))[0]


class Writer:
    def __init__(self):
        self.buf = bytearray()

    def u8(self, v):
        self.buf += struct.pack('<B', v)
        return self

    def u16(self, v):
        self.buf += struct.pack('<H', v)
        return self

    def i16(self, v):
        self.buf += struct.pack('<h', v)
        return self

    def u32(self, v):
        self.buf += struct.pack('<I', v)
        return self

    def f32(self, v):
        self.buf += struct.pack('<f', v)
        return self

    def halves(self, values):
        for v in values:
            self.buf += struct.pack('<e', max(-65504.0, min(65504.0, float(v))))
        return self

    def raw(self, b):
        self.buf += b
        return self

    def text(self, s, limit=64):
        b = s.encode('utf-8')[:limit]
        self.u8(len(b))
        self.buf += b
        return self

    def bytes(self):
        return bytes(self.buf)


class Reader:
    def __init__(self, data, pos=0):
        self.data = data
        self.pos = pos

    def take(self, fmt):
        v = struct.unpack_from('<' + fmt, self.data, self.pos)
        self.pos += struct.calcsize('<' + fmt)
        return v if len(v) > 1 else v[0]

    def u8(self):
        return self.take('B')

    def u16(self):
        return self.take('H')

    def i16(self):
        return self.take('h')

    def u32(self):
        return self.take('I')

    def f32(self):
        return self.take('f')

    def halves(self, n):
        v = struct.unpack_from('<' + 'e' * n, self.data, self.pos)
        self.pos += 2 * n
        return list(v)

    def raw(self, n):
        b = bytes(self.data[self.pos:self.pos + n])
        if len(b) != n:
            raise ValueError('unexpected end of data')
        self.pos += n
        return b

    def text(self):
        return self.raw(self.u8()).decode('utf-8', errors='replace')
