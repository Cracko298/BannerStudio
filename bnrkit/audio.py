import cmath
import math
import struct
import wave
from functools import lru_cache
from .bitio import Writer, Reader

DSP = 1
IMA = 2
CODEC_NAMES = {DSP: 'DSP-ADPCM (GC/3DS)', IMA: 'IMA-ADPCM'}
CODEC_KEYS = {'dsp': DSP, 'ima': IMA}
RATES = (8000, 11025, 16000, 22050, 32000, 32728)
NO_LOOP = 0xFFFFFFFF


def clamp16(v):
    return -32768 if v < -32768 else (32767 if v > 32767 else v)


def alaw(b):
    b ^= 0x55
    t = (b & 0x0F) << 4
    seg = (b & 0x70) >> 4
    if seg == 0:
        t += 8
    elif seg == 1:
        t += 0x108
    else:
        t = (t + 0x108) << (seg - 1)
    return (t if b & 0x80 else -t) / 32768.0


def ulaw(b):
    b = ~b & 0xFF
    t = (((b & 0x0F) << 3) + 0x84) << ((b & 0x70) >> 4)
    return ((0x84 - t) if b & 0x80 else (t - 0x84)) / 32768.0


def read_wav(src):
    if isinstance(src, (bytes, bytearray)):
        blob = bytes(src)
    else:
        with open(src, 'rb') as f:
            blob = f.read()
    if blob[:4] != b'RIFF' or blob[8:12] != b'WAVE':
        raise ValueError('not a RIFF/WAVE file')
    pos = 12
    fmt = None
    data = None
    while pos + 8 <= len(blob):
        cid = blob[pos:pos + 4]
        size = struct.unpack_from('<I', blob, pos + 4)[0]
        body = blob[pos + 8:pos + 8 + size]
        if cid == b'fmt ':
            fmt = body
        elif cid == b'data':
            data = body
        pos += 8 + size + (size & 1)
    if fmt is None or data is None:
        raise ValueError('WAV is missing its fmt or data chunk')
    tag, nch, rate, _, align, bits = struct.unpack_from('<HHIIHH', fmt)
    if tag == 0xFFFE and len(fmt) >= 26:
        tag = struct.unpack_from('<H', fmt, 24)[0]
    width = max(1, bits // 8) if tag in (1, 3) else 1
    frame = width * nch
    n = len(data) // frame
    data = data[:n * frame]
    count = n * nch
    if tag == 1 and bits <= 8:
        vals = [(b - 128) / 128.0 for b in data]
    elif tag == 1 and width == 2:
        vals = [v / 32768.0 for v in struct.unpack(f'<{count}h', data)]
    elif tag == 1 and width == 3:
        vals = [int.from_bytes(data[i:i + 3], 'little', signed=True) / 8388608.0 for i in range(0, len(data), 3)]
    elif tag == 1 and width == 4:
        vals = [v / 2147483648.0 for v in struct.unpack(f'<{count}i', data)]
    elif tag == 3 and width == 4:
        vals = list(struct.unpack(f'<{count}f', data))
    elif tag == 3 and width == 8:
        vals = list(struct.unpack(f'<{count}d', data))
    elif tag == 6:
        vals = [alaw(b) for b in data]
    elif tag == 7:
        vals = [ulaw(b) for b in data]
    else:
        raise ValueError(f'unsupported WAV encoding (format tag {tag}, {bits} bit). Re-save it as PCM or float WAV.')
    vals = [0.0 if v != v else max(-1.0, min(1.0, v)) for v in vals]
    return rate, [vals[c::nch] for c in range(nch)]


def write_wav(path, rate, chans):
    n = min(len(c) for c in chans)
    inter = [clamp16(int(chans[c][i])) for i in range(n) for c in range(len(chans))]
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(len(chans))
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack(f'<{len(inter)}h', *inter))


def resample(x, src, dst):
    if src == dst or not x:
        return list(x)
    ratio = dst / src
    fc = min(1.0, ratio) * 0.94
    half = min(64, int(math.ceil(8 / fc)))
    phases = 256
    table = []
    for p in range(phases):
        fr = p / phases
        row = []
        for k in range(-half + 1, half + 1):
            d = k - fr
            s = fc if d == 0 else math.sin(math.pi * fc * d) / (math.pi * d)
            w = 0.5 + 0.5 * math.cos(math.pi * d / half) if abs(d) < half else 0.0
            row.append(s * w)
        tot = sum(row) or 1.0
        table.append([v / tot for v in row])
    n_in = len(x)
    n_out = int(n_in * ratio)
    pad = [0.0] * (half + 2)
    xp = pad + list(x) + pad
    off = len(pad) - half + 1
    out = []
    step = src / dst
    for n in range(n_out):
        t = n * step
        i0 = int(t)
        row = table[int((t - i0) * phases)]
        seg = xp[i0 + off:i0 + off + len(row)]
        out.append(sum(a * b for a, b in zip(seg, row)))
    return out


@lru_cache(maxsize=2)
def synth_chime(rate=32000):
    dur = 2.4
    n = int(dur * rate)
    left = [0.0] * n
    right = [0.0] * n

    def bell(start, freq, amp, pan, tau):
        s0 = int(start * rate)
        for k, (mult, a, dk) in enumerate(((1.0, 1.0, 1.0), (2.0, 0.32, 1.8), (3.003, 0.11, 2.6), (4.2, 0.045, 3.4))):
            f = freq * mult
            if f >= rate * 0.45:
                continue
            t = tau / dk
            w = cmath.exp(complex(-1.0 / (t * rate), 2 * math.pi * f / rate))
            z = complex(0.0, 0.0) + amp * a
            lg, rg = math.cos(pan * math.pi / 2), math.sin(pan * math.pi / 2)
            attack = int(0.004 * rate)
            end = min(n, s0 + int(t * 7 * rate))
            for i in range(s0, end):
                v = z.imag
                j = i - s0
                if j < attack:
                    v *= j / attack
                left[i] += v * lg
                right[i] += v * rg
                z *= w

    def pad(freq, amp, start, rise):
        s0 = int(start * rate)
        w = cmath.exp(complex(0, 2 * math.pi * freq / rate))
        z = complex(1.0, 0.0)
        span = n - s0
        for j in range(span):
            e = min(1.0, j / (rise * rate)) * (1.0 - j / span) ** 1.6
            v = z.imag * amp * e
            left[s0 + j] += v
            right[s0 + j] += v
            z *= w

    for start, f, a, p in ((0.00, 783.99, 0.34, 0.30), (0.11, 1046.50, 0.30, 0.45), (0.22, 1318.51, 0.27, 0.60), (0.33, 1567.98, 0.24, 0.72), (0.55, 1046.50, 0.30, 0.50), (0.55, 1318.51, 0.16, 0.38), (0.56, 2093.00, 0.08, 0.66)):
        bell(start, f, a, p, 0.85 if start < 0.5 else 1.4)
    pad(261.63, 0.07, 0.40, 0.30)
    pad(392.00, 0.05, 0.45, 0.30)
    pad(523.25, 0.035, 0.50, 0.35)
    for d, g, src, dst in ((0.083, 0.28, left, right), (0.127, 0.24, right, left), (0.191, 0.16, left, left), (0.233, 0.14, right, right)):
        k = int(d * rate)
        for i in range(n - 1, k - 1, -1):
            dst[i] += src[i - k] * g
    peak = max(max(abs(v) for v in left), max(abs(v) for v in right)) or 1.0
    fade = int(0.3 * rate)
    for i in range(n - fade, n):
        g = (n - i) / fade
        left[i] *= g
        right[i] *= g
    return rate, (tuple(v * 0.89 / peak for v in left), tuple(v * 0.89 / peak for v in right))


def prepare(rate, chans, target_rate=16000, channels=1, max_seconds=3.0, normalise=True, trim=True):
    chans = [list(c) for c in chans]
    if channels == 1:
        mix = [sum(v) / len(chans) for v in zip(*chans)] if len(chans) > 1 else chans[0]
        chans = [mix]
    else:
        chans = [chans[0], chans[1]] if len(chans) > 1 else [chans[0], list(chans[0])]
    n = len(chans[0])
    if trim and n:
        thr = 0.003
        lead = next((i for i in range(n) if any(abs(c[i]) > thr for c in chans)), n)
        tail = next((i for i in range(n - 1, -1, -1) if any(abs(c[i]) > thr for c in chans)), -1)
        lead = max(0, lead - int(0.005 * rate))
        chans = [c[lead:tail + 1] for c in chans]
        n = len(chans[0])
    limit = int(max_seconds * rate) if max_seconds and max_seconds > 0 else n
    if n > limit:
        fade = min(limit, int(0.12 * rate))
        chans = [c[:limit] for c in chans]
        for c in chans:
            for i in range(fade):
                c[limit - fade + i] *= 1.0 - (i + 1) / fade
    if normalise:
        peak = max((abs(v) for c in chans for v in c), default=0.0)
        if peak > 1e-6:
            g = 0.89 / peak
            chans = [[v * g for v in c] for c in chans]
    chans = [resample(c, rate, target_rate) for c in chans]
    return [[clamp16(int(round(v * 32767))) for v in c] for c in chans]


def frame_stats(x):
    out = []
    n = len(x)
    for f in range(0, n, 14):
        xx = xa = xb = aa = ab = bb = 0.0
        for i in range(f, min(n, f + 14)):
            s = x[i]
            a = x[i - 1] if i >= 1 else 0
            b = x[i - 2] if i >= 2 else 0
            xx += s * s
            xa += s * a
            xb += s * b
            aa += a * a
            ab += a * b
            bb += b * b
        out.append((xx, xa, xb, aa, ab, bb))
    return out


def frame_error(st, c1, c2):
    xx, xa, xb, aa, ab, bb = st
    return xx - 2 * c1 * xa - 2 * c2 * xb + c1 * c1 * aa + 2 * c1 * c2 * ab + c2 * c2 * bb


def stable(c1, c2):
    c2 = max(-0.98, min(0.98, c2))
    lim = 0.995 * (1.0 - c2)
    return max(-lim, min(lim, c1)), c2


def solve(st):
    xx, xa, xb, aa, ab, bb = st
    reg = 1e-9 * (aa + bb) + 1e-9
    aa += reg
    bb += reg
    det = aa * bb - ab * ab
    if abs(det) < 1e-12:
        return stable(xa / aa if aa > 1e-9 else 0.0, 0.0)
    return stable((xa * bb - xb * ab) / det, (aa * xb - ab * xa) / det)


def dsp_coefs(x):
    stats = [s for s in frame_stats(x) if s[0] > 0]
    if not stats:
        return [0] * 16
    lpcs = [solve(s) for s in stats]
    centres = [(0.0, 0.0)]
    best = [frame_error(s, 0.0, 0.0) for s in stats]
    while len(centres) < 8:
        k = max(range(len(stats)), key=lambda i: best[i])
        if best[k] <= 0:
            break
        c = lpcs[k]
        centres.append(c)
        best = [min(b, frame_error(s, *c)) for b, s in zip(best, stats)]
    for _ in range(10):
        groups = [[0.0] * 6 for _ in centres]
        for s in stats:
            j = min(range(len(centres)), key=lambda q: frame_error(s, *centres[q]))
            g = groups[j]
            for t in range(6):
                g[t] += s[t]
        centres = [solve(g) if g[3] > 0 else c for g, c in zip(groups, centres)]
    while len(centres) < 8:
        centres.append((0.0, 0.0))
    out = []
    for c1, c2 in centres:
        out += [max(-32768, min(32767, int(round(c1 * 2048)))), max(-32768, min(32767, int(round(c2 * 2048))))]
    return out


def dsp_encode(x):
    coefs = dsp_coefs(x)
    pairs = [(coefs[i * 2], coefs[i * 2 + 1]) for i in range(8)]
    n = len(x)
    out = bytearray()
    h1 = h2 = 0
    for f in range(0, n, 14):
        xs = list(x[f:f + 14]) + [0] * max(0, f + 14 - n)
        p1 = x[f - 1] if f >= 1 else 0
        p2 = x[f - 2] if f >= 2 else 0
        ranked = []
        for ci, (c1, c2) in enumerate(pairs):
            a, b = p1, p2
            err = 0
            peak = 0
            for s in xs:
                e = s - ((c1 * a + c2 * b + 1024) >> 11)
                err += e * e
                if abs(e) > peak:
                    peak = abs(e)
                a, b = s, a
            ranked.append((err, ci, peak))
        ranked.sort()
        best = None
        for _, ci, peak in ranked[:3]:
            c1, c2 = pairs[ci]
            s0 = 0
            while s0 < 12 and 7 * (1 << s0) < peak:
                s0 += 1
            for sh in {max(0, s0 - 1), s0, min(12, s0 + 1)}:
                sc = 1 << sh
                half = sc >> 1
                a, b = h1, h2
                err = 0
                nibs = []
                for s in xs:
                    p = (c1 * a + c2 * b + 1024) >> 11
                    q = (s - p + half) // sc
                    q = -8 if q < -8 else (7 if q > 7 else q)
                    v = clamp16(q * sc + p)
                    err += (s - v) * (s - v)
                    nibs.append(q & 15)
                    a, b = v, a
                if best is None or err < best[0]:
                    best = (err, ci, sh, nibs, a, b)
        _, ci, sh, nibs, h1, h2 = best
        out.append((ci << 4) | sh)
        for i in range(0, 14, 2):
            out.append((nibs[i] << 4) | nibs[i + 1])
    return coefs, bytes(out)


def dsp_decode(data, coefs, count, h1=0, h2=0):
    out = []
    for f in range(0, len(data), 8):
        ps = data[f]
        sc = 1 << (ps & 15)
        ci = (ps >> 4) & 7
        c1, c2 = coefs[ci * 2], coefs[ci * 2 + 1]
        for i in range(14):
            if len(out) >= count:
                return out
            byte = data[f + 1 + (i >> 1)]
            q = (byte >> 4) if i % 2 == 0 else (byte & 15)
            if q >= 8:
                q -= 16
            v = clamp16(q * sc + ((c1 * h1 + c2 * h2 + 1024) >> 11))
            out.append(v)
            h1, h2 = v, h1
    return out


IMA_STEPS = [7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767]
IMA_INDEX = [-1, -1, -1, -1, 2, 4, 6, 8]


def ima_step(pred, idx, code):
    step = IMA_STEPS[idx]
    diff = step >> 3
    if code & 4:
        diff += step
    if code & 2:
        diff += step >> 1
    if code & 1:
        diff += step >> 2
    pred = clamp16(pred - diff if code & 8 else pred + diff)
    idx = max(0, min(88, idx + IMA_INDEX[code & 7]))
    return pred, idx


def ima_encode(x):
    if not x:
        return 0, 0, b''
    pred = x[0]
    first = abs(x[1] - x[0]) if len(x) > 1 else 0
    idx = next((i for i, s in enumerate(IMA_STEPS) if s >= first), 88)
    p0, i0 = pred, idx
    codes = []
    for s in x:
        d = s - pred
        code = 8 if d < 0 else 0
        d = abs(d)
        step = IMA_STEPS[idx]
        if d >= step:
            code |= 4
            d -= step
        step >>= 1
        if d >= step:
            code |= 2
            d -= step
        step >>= 1
        if d >= step:
            code |= 1
        pred, idx = ima_step(pred, idx, code)
        codes.append(code)
    if len(codes) & 1:
        codes.append(0)
    return p0, i0, bytes(codes[i] | (codes[i + 1] << 4) for i in range(0, len(codes), 2))


def ima_decode(data, pred, idx, count):
    out = []
    for b in data:
        for code in (b & 15, b >> 4):
            if len(out) >= count:
                return out
            pred, idx = ima_step(pred, idx, code)
            out.append(pred)
    return out


def ima_state_at(data, pred, idx, count):
    for b in data:
        for code in (b & 15, b >> 4):
            if count <= 0:
                return pred, idx
            pred, idx = ima_step(pred, idx, code)
            count -= 1
    return pred, idx


class Jingle:
    def __init__(self, codec, rate, count, channels, loop_start=NO_LOOP):
        self.codec = codec
        self.rate = rate
        self.count = count
        self.channels = channels
        self.loop_start = loop_start

    @staticmethod
    def from_pcm(pcm, rate, codec=DSP, loop=False):
        count = len(pcm[0])
        if codec == DSP:
            loop_start = 0 if loop else NO_LOOP
            chans = []
            for c in pcm:
                coefs, data = dsp_encode(c)
                chans.append({'coefs': coefs, 'data': data, 'loop': (data[0] if data else 0, 0, 0)})
        else:
            loop_start = 0 if loop else NO_LOOP
            chans = []
            for c in pcm:
                p, i, data = ima_encode(c)
                chans.append({'pred': p, 'index': i, 'data': data, 'loop': (p, i)})
        return Jingle(codec, rate, count, chans, loop_start)

    def encode(self):
        w = Writer().u8(self.codec).u8(len(self.channels)).u16(self.rate).u32(self.count).u32(self.loop_start)
        for c in self.channels:
            if self.codec == DSP:
                for v in c['coefs']:
                    w.i16(v)
                w.i16(c['loop'][1]).i16(c['loop'][2]).u8(c['loop'][0])
            else:
                w.i16(c['pred']).u8(c['index']).i16(c['loop'][0]).u8(c['loop'][1])
        for c in self.channels:
            w.raw(c['data'])
        return w.bytes()

    @staticmethod
    def decode(data):
        r = Reader(data)
        codec, nch, rate, count, loop = r.u8(), r.u8(), r.u16(), r.u32(), r.u32()
        chans = []
        for _ in range(nch):
            if codec == DSP:
                coefs = [r.i16() for _ in range(16)]
                y1, y2, ps = r.i16(), r.i16(), r.u8()
                chans.append({'coefs': coefs, 'loop': (ps, y1, y2)})
            elif codec == IMA:
                p, i, lp, li = r.i16(), r.u8(), r.i16(), r.u8()
                chans.append({'pred': p, 'index': i, 'loop': (lp, li)})
            else:
                raise ValueError(f'unknown jingle codec {codec}')
        size = ((count + 13) // 14) * 8 if codec == DSP else (count + 1) // 2
        for c in chans:
            c['data'] = r.raw(size)
        return Jingle(codec, rate, count, chans, loop)

    def pcm(self):
        if self.codec == DSP:
            return [dsp_decode(c['data'], c['coefs'], self.count) for c in self.channels]
        return [ima_decode(c['data'], c['pred'], c['index'], self.count) for c in self.channels]

    def seconds(self):
        return self.count / self.rate if self.rate else 0.0


def make_jingle(source=None, codec=DSP, rate=16000, channels=1, max_seconds=3.0, normalise=True, trim=True, loop=False):
    if source in (None, '', 'builtin'):
        src_rate, chans = synth_chime()
    else:
        src_rate, chans = read_wav(source)
    pcm = prepare(src_rate, chans, rate, channels, max_seconds, normalise, trim)
    if not pcm[0]:
        raise ValueError('the audio is empty (or entirely silent)')
    return Jingle.from_pcm(pcm, rate, codec, loop)


def estimate_bytes(seconds, rate, channels, codec):
    n = int(seconds * rate)
    per = ((n + 13) // 14) * 8 + 37 if codec == DSP else (n + 1) // 2 + 6
    return 12 + per * channels
