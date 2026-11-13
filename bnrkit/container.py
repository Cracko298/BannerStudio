import binascii
import struct
from . import bbm, texture
from .audio import Jingle
from .bitio import Writer, Reader, bitpack, bitunpack, packed_len
from .scene import encode_nodes, decode_nodes, View, Sky, Bloom

MAGIC = b'BNR\x03'
VERSION = 3
CREDIT = b'Cracko298\x00\x02\x98'
HEADER = struct.Struct('<4s12sIHHII')
SEED = binascii.crc32(b'Cracko298')
CRC_AT = 28
ICON_RAW = 0
ICON_IMAGE_48 = 1
ICON_AURORA = 2
SCREENS = {'top': 0, 'bottom': 1}


class BannerError(ValueError):
    pass


def rgb565(c):
    r, g, b = c[:3]
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def from565(v):
    r, g, b = (v >> 11) & 31, (v >> 5) & 63, v & 31
    return ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2))


class Model:
    def __init__(self, positions, faces, uvs=None, texture=-1, tint=(255, 255, 255), name='model', source='.glb'):
        self.positions = positions
        self.faces = faces
        self.uvs = uvs
        self.texture = texture
        self.tint = tuple(tint)
        self.name = name
        self.source = source


class Overlay:
    def __init__(self, image, screen=0, x=0, y=0, scale=1.0):
        self.image = image
        self.screen = screen
        self.x = x
        self.y = y
        self.scale = scale


class Banner:
    def __init__(self):
        self.title = ''
        self.author = ''
        self.view = View()
        self.sky = None
        self.bloom = None
        self.textures = []
        self.texture_formats = []
        self.models = []
        self.nodes = []
        self.overlay = None
        self.overlay_format = None
        self.icon = None
        self.icon_kind = ICON_RAW
        self.jingle = None
        self.pos_bits = 12
        self.uv_bits = None
        self.dither = True
        self.texture_dither = []
        self.icon_format = None
        self.icon_animation = False


def encode_uvs(uvs, bits):
    ub = [bbm.axis_bounds([t[a] for t in uvs]) for a in range(2)]
    q = [bbm.quantize_axis([t[a] for t in uvs], bits, *ub[a]) for a in range(2)]
    return struct.pack('<B4f', bits, ub[0][0], ub[0][1], ub[1][0], ub[1][1]) + bitpack(q[0], bits) + bitpack(q[1], bits)


def decode_uvs(data, pos, count):
    bits, u0, u1, v0, v1 = struct.unpack_from('<B4f', data, pos)
    pos += 17
    ln = packed_len(count, bits)
    us = bbm.dequantize_axis(bitunpack(data[pos:pos + ln], bits, count), bits, u0, u1)
    vs = bbm.dequantize_axis(bitunpack(data[pos + ln:pos + 2 * ln], bits, count), bits, v0, v1)
    return list(zip(us, vs)), 17 + 2 * ln


def encode_model(m, pos_bits, uv_bits):
    flags = 1 if m.uvs else 0
    out = struct.pack('<BHB', 0xFF if m.texture < 0 else m.texture, rgb565(m.tint), flags)
    out += bbm.encode_record(m.positions, m.faces, m.name, pos_bits, m.source)
    if m.uvs:
        out += encode_uvs(m.uvs, uv_bits)
    return out


def decode_model(data):
    tex, tint, flags = struct.unpack_from('<BHB', data)
    positions, faces, name, used = bbm.decode_record(data, 4)
    uvs = None
    if flags & 1:
        uvs, _ = decode_uvs(data, 4 + used, len(positions))
    return Model(positions, faces, uvs, -1 if tex == 0xFF else tex, from565(tint), name)


def aurora_icon(img, animation=False):
    from PIL import Image
    im = texture.prepare_icon(img, 32).convert('RGBA')
    bg = Image.new('RGBA', im.size, (0, 0, 0, 255))
    px = Image.alpha_composite(bg, im).convert('RGB').tobytes()
    vals = [((px[i] >> 3) << 11) | ((px[i + 1] >> 2) << 5) | (px[i + 2] >> 3) for i in range(0, len(px), 3)]
    return b'AIC1' + bytes([1 if animation else 0, 0, 0, 0]) + struct.pack('<1024H', *vals)


def read_aurora_icon(data):
    from PIL import Image
    if data[:4] != b'AIC1' or len(data) < 2056:
        raise BannerError('the Aurora icon block is damaged')
    vals = struct.unpack_from('<1024H', data, 8)
    px = bytearray()
    for v in vals:
        px += bytes(from565(v))
    return Image.frombytes('RGB', (32, 32), bytes(px)), bool(data[4])


def chunk(tag, payload):
    return tag + struct.pack('<I', len(payload)) + payload


def checksum(blob):
    b = bytearray(blob)
    b[CRC_AT:CRC_AT + 4] = b'\x00\x00\x00\x00'
    return binascii.crc32(bytes(b), SEED) & 0xFFFFFFFF


def uv_bits_for(size):
    w, h = size
    return max(6, min(14, max(w, h).bit_length() + 3))


def write(banner):
    if banner.jingle is None:
        raise BannerError('every banner needs a jingle')
    parts = []
    w = Writer()
    w.text(banner.title).text(banner.author)
    parts.append(chunk(b'INFO', w.bytes()))
    parts.append(chunk(b'VIEW', banner.view.encode()))
    if banner.sky is not None:
        parts.append(chunk(b'SKY ', banner.sky.encode()))
    if banner.bloom is not None and banner.bloom.enabled:
        parts.append(chunk(b'BLOM', banner.bloom.encode()))
    for i, (img, fmt) in enumerate(zip(banner.textures, banner.texture_formats)):
        d = banner.texture_dither[i] if i < len(banner.texture_dither) else banner.dither
        parts.append(chunk(b'TEXR', texture.encode(img, fmt, d)))
    for m in banner.models:
        bits = banner.uv_bits or (uv_bits_for(banner.textures[m.texture].size) if 0 <= m.texture < len(banner.textures) else 10)
        parts.append(chunk(b'MODL', encode_model(m, banner.pos_bits, bits)))
    parts.append(chunk(b'NODE', encode_nodes(banner.nodes)))
    if banner.overlay is not None:
        o = banner.overlay
        img = texture.prepare_overlay(o.image, 256, 144)
        head = struct.pack('<BhhH', o.screen, int(o.x), int(o.y), max(1, min(65535, int(round(o.scale * 256)))))
        parts.append(chunk(b'IMAG', head + texture.encode(img, banner.overlay_format, banner.dither)))
    if banner.icon is not None:
        if banner.icon_kind == ICON_RAW:
            parts.append(chunk(b'ICON', bytes([ICON_RAW]) + bytes(banner.icon)))
        elif banner.icon_kind == ICON_AURORA:
            parts.append(chunk(b'ICON', bytes([ICON_AURORA]) + aurora_icon(banner.icon, banner.icon_animation)))
        else:
            parts.append(chunk(b'ICON', bytes([ICON_IMAGE_48]) + texture.encode(texture.prepare_icon(banner.icon), banner.icon_format, banner.dither)))
    parts.append(chunk(b'JNGL', banner.jingle.encode()))
    body = b''.join(parts)
    size = HEADER.size + len(body)
    blob = HEADER.pack(MAGIC, CREDIT, size, len(parts), 0, HEADER.size, 0) + body
    crc = checksum(blob)
    return blob[:CRC_AT] + struct.pack('<I', crc) + blob[CRC_AT + 4:]


def verify(blob):
    if len(blob) < HEADER.size:
        raise BannerError('file is too small to be a banner')
    magic, credit, size, count, flags, first, crc = HEADER.unpack_from(blob)
    if magic[:3] != b'BNR':
        raise BannerError('not a .bnr file')
    if magic[3] != VERSION:
        raise BannerError(f'unsupported BNR version {magic[3]} (this tool reads version {VERSION}; rebuild the banner)')
    if credit != CREDIT:
        raise BannerError('the Cracko298 seal is missing or altered - this banner is not genuine')
    if size != len(blob):
        raise BannerError(f'size mismatch: header says {size}, file is {len(blob)}')
    if checksum(blob) != crc:
        raise BannerError('checksum failed - the file was modified or damaged')
    return count, first


def chunks(blob):
    count, pos = verify(blob)
    out = []
    for _ in range(count):
        tag, ln = struct.unpack_from('<4sI', blob, pos)
        out.append((tag.decode('ascii', errors='replace'), pos, ln))
        pos += 8 + ln
    return out


def read(blob):
    b = Banner()
    for tag, pos, ln in chunks(blob):
        data = blob[pos + 8:pos + 8 + ln]
        if tag == 'INFO':
            r = Reader(data)
            b.title, b.author = r.text(), r.text()
        elif tag == 'VIEW':
            b.view = View.decode(data)
        elif tag == 'SKY ':
            b.sky = Sky.decode(data)
        elif tag == 'BLOM':
            b.bloom = Bloom.decode(data)
        elif tag == 'TEXR':
            img, _ = texture.decode(data)
            b.textures.append(img)
            b.texture_formats.append(data[4])
        elif tag == 'MODL':
            b.models.append(decode_model(data))
        elif tag == 'NODE':
            b.nodes = decode_nodes(data)
        elif tag == 'IMAG':
            screen, x, y, sc = struct.unpack_from('<BhhH', data)
            img, _ = texture.decode(data, 7)
            b.overlay = Overlay(img, screen, x, y, sc / 256)
            b.overlay_format = data[11]
        elif tag == 'ICON':
            b.icon_kind = data[0]
            if data[0] == ICON_IMAGE_48:
                b.icon = texture.decode(data, 1)[0]
            elif data[0] == ICON_AURORA:
                b.icon, b.icon_animation = read_aurora_icon(data[1:])
            else:
                b.icon = bytes(data[1:])
        elif tag == 'JNGL':
            b.jingle = Jingle.decode(data)
    return b
