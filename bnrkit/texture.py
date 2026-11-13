import struct
from PIL import Image
from .bitio import Reader

RGB565 = 0
RGBA4444 = 1
RGBA5551 = 2
I8 = 3
I4 = 4

FORMAT_NAMES = {RGB565: 'RGB565', RGBA4444: 'RGBA4444', RGBA5551: 'RGBA5551', I8: 'I8 (256 colours)', I4: 'I4 (16 colours)'}
FORMAT_KEYS = {'auto': None, 'rgb565': RGB565, 'rgba4444': RGBA4444, 'rgba5551': RGBA5551, 'i8': I8, 'i4': I4}


def enc_px(fmt, r, g, b, a):
    if fmt == RGB565:
        return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
    if fmt == RGBA4444:
        return ((r >> 4) << 12) | ((g >> 4) << 8) | ((b >> 4) << 4) | (a >> 4)
    return ((r >> 3) << 11) | ((g >> 3) << 6) | ((b >> 3) << 1) | (1 if a >= 128 else 0)


def dec_px(fmt, v):
    if fmt == RGB565:
        r, g, b = (v >> 11) & 31, (v >> 5) & 63, v & 31
        return (r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2), 255
    if fmt == RGBA4444:
        return tuple(((v >> s) & 15) * 17 for s in (12, 8, 4, 0))
    r, g, b = (v >> 11) & 31, (v >> 6) & 31, (v >> 1) & 31
    return (r << 3) | (r >> 2), (g << 3) | (g >> 2), (b << 3) | (b >> 2), 255 if v & 1 else 0


def alpha_kind(img):
    lo, hi = img.getchannel('A').getextrema()
    if lo == 255:
        return 'opaque'
    hist = img.getchannel('A').histogram()
    if sum(hist[1:255]) * 50 < img.width * img.height:
        return 'binary'
    return 'smooth'


def pot_floor(n):
    p = 1
    while p * 2 <= n:
        p *= 2
    return p


def pot_size(w, h, cap, minimum=8):
    s = min(1.0, cap / max(w, h))
    w, h = w * s, h * s

    def fit(n):
        p = pot_floor(max(1, int(n)))
        if p * 1.5 < n:
            p *= 2
        return max(minimum, min(cap, p))
    return fit(w), fit(h)


def fit_box(w, h, bw, bh):
    s = min(bw / w, bh / h, 1.0)
    return max(1, int(round(w * s))), max(1, int(round(h * s)))


def choose_format(img):
    cols = img.getcolors(16)
    return I4 if cols is not None else I8


def quantize(img, colours, dither):
    kind = alpha_kind(img)
    d = Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE
    if kind == 'opaque':
        q = img.convert('RGB').quantize(colors=colours, method=Image.Quantize.MEDIANCUT, dither=d)
        full = q.convert('RGBA')
    else:
        q = img.quantize(colors=colours, method=Image.Quantize.FASTOCTREE, dither=d)
        full = q.convert('RGBA')
    idx = q.tobytes()
    rgba = full.tobytes()
    remap = {}
    pal = []
    out = bytearray(len(idx))
    for i, k in enumerate(idx):
        if k not in remap:
            remap[k] = len(pal)
            pal.append(tuple(rgba[i * 4:i * 4 + 4]))
        out[i] = remap[k]
    palfmt = RGB565 if kind == 'opaque' else (RGBA5551 if kind == 'binary' else RGBA4444)
    return bytes(out), pal, palfmt


def encode(img, fmt=None, dither=True):
    img = img.convert('RGBA')
    if fmt is None:
        fmt = choose_format(img)
    w, h = img.size
    head = struct.pack('<HHB', w, h, fmt)
    if fmt in (I8, I4):
        idx, pal, palfmt = quantize(img, 16 if fmt == I4 else 256, dither)
        body = struct.pack('<BH', palfmt, len(pal)) + b''.join(struct.pack('<H', enc_px(palfmt, *c)) for c in pal)
        if fmt == I8:
            body += idx
        else:
            packed = bytearray((len(idx) + 1) // 2)
            for i, v in enumerate(idx):
                packed[i >> 1] |= (v & 15) << (4 * (i & 1))
            body += bytes(packed)
        return head + body
    if fmt == RGB565:
        data = img.convert('RGB').tobytes()
        vals = [enc_px(RGB565, data[i], data[i + 1], data[i + 2], 255) for i in range(0, len(data), 3)]
    else:
        data = img.tobytes()
        vals = [enc_px(fmt, *data[i:i + 4]) for i in range(0, len(data), 4)]
    return head + struct.pack(f'<{len(vals)}H', *vals)


def decode(data, pos=0):
    r = Reader(data, pos)
    w, h, fmt = r.u16(), r.u16(), r.u8()
    n = w * h
    if fmt in (I8, I4):
        palfmt, count = r.u8(), r.u16()
        pal = [dec_px(palfmt, r.u16()) for _ in range(count)]
        if fmt == I8:
            idx = r.raw(n)
        else:
            raw = r.raw((n + 1) // 2)
            idx = [(raw[i >> 1] >> (4 * (i & 1))) & 15 for i in range(n)]
        px = bytearray()
        for k in idx:
            px += bytes(pal[k] if k < count else (0, 0, 0, 0))
    else:
        vals = struct.unpack_from(f'<{n}H', data, r.pos)
        r.pos += 2 * n
        px = bytearray()
        for v in vals:
            px += bytes(dec_px(fmt, v))
    return Image.frombytes('RGBA', (w, h), bytes(px)), r.pos - pos


def prepare_texture(img, cap=128):
    img = img.convert('RGBA')
    tw, th = pot_size(img.width, img.height, cap)
    if (tw, th) != img.size:
        img = img.resize((tw, th), Image.LANCZOS)
    return img


def prepare_overlay(img, max_w=256, max_h=144):
    img = img.convert('RGBA')
    w, h = fit_box(img.width, img.height, max_w, max_h)
    if (w, h) != img.size:
        img = img.resize((w, h), Image.LANCZOS)
    return img


def prepare_icon(img, size=48):
    img = img.convert('RGBA')
    s = min(img.width, img.height)
    l, t = (img.width - s) // 2, (img.height - s) // 2
    return img.crop((l, t, l + s, t + s)).resize((size, size), Image.LANCZOS)
