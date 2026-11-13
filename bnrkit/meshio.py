import base64
import io
import json
import math
import os
import struct
from urllib.parse import unquote
from PIL import Image
from . import mathx

MODEL_EXTS = ('.glb', '.gltf', '.obj', '.ply', '.stl')
PRIMITIVES = ('sphere', 'cube', 'torus', 'plane', 'cylinder', 'sprite')


class MeshData:
    def __init__(self, positions, faces, uvs=None, image=None, tint=(255, 255, 255), name='model', source='.glb'):
        self.positions = positions
        self.faces = faces
        self.uvs = uvs
        self.image = image
        self.tint = tint
        self.name = name
        self.source = source

    def copy(self):
        return MeshData(list(self.positions), list(self.faces), list(self.uvs) if self.uvs else None, self.image, self.tint, self.name, self.source)


def tinted(img, color):
    img = img.convert('RGBA')
    if tuple(color) == (255, 255, 255, 255):
        return img
    bands = img.split()
    bands = [b.point(lambda v, c=c: v * c // 255) for b, c in zip(bands, color)]
    return Image.merge('RGBA', bands)


def assemble(tris, materials, name, source):
    used = sorted({t[2] for t in tris})
    if not used:
        raise ValueError(f'{name}: no triangles found')
    textured = [k for k in used if materials[k].get('image') is not None]
    if len(used) == 1 or (not textured and len({tuple(materials[k]['color']) for k in used}) == 1):
        m = materials[used[0]]
        if m.get('image') is not None:
            return build(tris, lambda t, i: t[1][i] if t[1] else (0.0, 0.0), tinted(m['image'], m['color']), (255, 255, 255), name, source)
        return build(tris, None, None, tuple(m['color'][:3]), name, source)
    tiles = {}
    for k in used:
        m = materials[k]
        if m.get('image') is not None:
            im = tinted(m['image'], m['color'])
            s = 256 / max(im.size)
            if s < 1:
                im = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), Image.LANCZOS)
            tiles[k] = im
        else:
            tiles[k] = Image.new('RGBA', (8, 8), tuple(int(c) for c in m['color']))
    atlas, rects = pack_atlas(tiles)
    aw, ah = atlas.size

    def uvmap(t, i):
        x, y, w, h = rects[t[2]]
        if materials[t[2]].get('image') is None or not t[1]:
            return ((x + w / 2) / aw, (y + h / 2) / ah)
        us = [c[0] for c in t[1]]
        vs = [c[1] for c in t[1]]
        ou, ov = math.floor(min(us)), math.floor(min(vs))
        u = min(1.0, max(0.0, t[1][i][0] - ou))
        v = min(1.0, max(0.0, t[1][i][1] - ov))
        return ((x + 0.5 + u * (w - 1)) / aw, (y + 0.5 + v * (h - 1)) / ah)
    return build(tris, uvmap, atlas, (255, 255, 255), name, source)


def pack_atlas(tiles, pad=4):
    order = sorted(tiles, key=lambda k: -tiles[k].height)
    area = sum((tiles[k].width + pad * 2) * (tiles[k].height + pad * 2) for k in order)
    width = max(max(tiles[k].width + pad * 2 for k in order), int(math.ceil(math.sqrt(area))))
    x = y = shelf = 0
    places = {}
    for k in order:
        w, h = tiles[k].width + pad * 2, tiles[k].height + pad * 2
        if x + w > width:
            x, y, shelf = 0, y + shelf, 0
        places[k] = (x + pad, y + pad)
        x += w
        shelf = max(shelf, h)
    height = y + shelf
    atlas = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    rects = {}
    for k in order:
        im = tiles[k]
        px, py = places[k]
        atlas.paste(im.resize((im.width + pad * 2, im.height + pad * 2), Image.NEAREST), (px - pad, py - pad))
        atlas.paste(im, (px, py))
        rects[k] = (px, py, im.width, im.height)
    return atlas, rects


def build(tris, uvmap, image, tint, name, source):
    index = {}
    positions = []
    uvs = [] if uvmap else None
    faces = []
    for t in tris:
        f = []
        for i in range(3):
            p = tuple(t[0][i])
            uv = tuple(uvmap(t, i)) if uvmap else None
            key = (p, uv)
            if key not in index:
                index[key] = len(positions)
                positions.append(p)
                if uvmap:
                    uvs.append(uv)
            f.append(index[key])
        if len(set(f)) == 3:
            faces.append(tuple(f))
    return MeshData(positions, faces, uvs, image, tint, name, source)


def open_image(data_or_path):
    if isinstance(data_or_path, (bytes, bytearray)):
        im = Image.open(io.BytesIO(data_or_path))
    else:
        im = Image.open(data_or_path)
    im.load()
    return im.convert('RGBA')


def load_gltf(path):
    with open(path, 'rb') as f:
        blob = f.read()
    base = os.path.dirname(os.path.abspath(path))
    bin_chunk = None
    if blob[:4] == b'glTF':
        _, _, total = struct.unpack_from('<4sII', blob)
        pos = 12
        doc = None
        while pos < total:
            ln, kind = struct.unpack_from('<II', blob, pos)
            chunk = blob[pos + 8:pos + 8 + ln]
            if kind == 0x4E4F534A:
                doc = json.loads(chunk.decode('utf-8'))
            elif kind == 0x004E4942:
                bin_chunk = chunk
            pos += 8 + ln
    else:
        doc = json.loads(blob.decode('utf-8'))

    def fetch(uri):
        if uri.startswith('data:'):
            return base64.b64decode(uri.split(',', 1)[1])
        with open(os.path.join(base, unquote(uri)), 'rb') as fh:
            return fh.read()
    buffers = []
    for b in doc.get('buffers', []):
        buffers.append(fetch(b['uri']) if 'uri' in b else bin_chunk)

    def view_bytes(i):
        bv = doc['bufferViews'][i]
        off = bv.get('byteOffset', 0)
        return buffers[bv['buffer']][off:off + bv['byteLength']], bv.get('byteStride')

    comp = {5120: ('b', 1, 127.0), 5121: ('B', 1, 255.0), 5122: ('h', 2, 32767.0), 5123: ('H', 2, 65535.0), 5125: ('I', 4, 4294967295.0), 5126: ('f', 4, None)}
    width = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT2': 4, 'MAT3': 9, 'MAT4': 16}

    def accessor(i):
        a = doc['accessors'][i]
        ch, size, norm = comp[a['componentType']]
        n, k = a['count'], width[a['type']]
        st = struct.Struct('<' + ch * k)
        if 'bufferView' in a:
            data, stride = view_bytes(a['bufferView'])
            stride = stride or st.size
            off = a.get('byteOffset', 0)
            out = [list(st.unpack_from(data, off + j * stride)) for j in range(n)]
        else:
            out = [[0] * k for _ in range(n)]
        sp = a.get('sparse')
        if sp:
            ich, isz, _ = comp[sp['indices']['componentType']]
            idata, _ = view_bytes(sp['indices']['bufferView'])
            vdata, _ = view_bytes(sp['values']['bufferView'])
            io_, vo = sp['indices'].get('byteOffset', 0), sp['values'].get('byteOffset', 0)
            for j in range(sp['count']):
                target = struct.unpack_from('<' + ich, idata, io_ + j * isz)[0]
                out[target] = list(st.unpack_from(vdata, vo + j * st.size))
        if a.get('normalized') and norm:
            out = [[max(-1.0, c / norm) for c in row] for row in out]
        return out

    images = {}

    def image(i):
        if i not in images:
            im = doc['images'][i]
            try:
                if 'bufferView' in im:
                    data, _ = view_bytes(im['bufferView'])
                    images[i] = open_image(bytes(data))
                else:
                    images[i] = open_image(fetch(im['uri']))
            except Exception:
                images[i] = None
        return images[i]

    materials = []
    for m in doc.get('materials', []):
        pbr = m.get('pbrMetallicRoughness', {})
        sg = m.get('extensions', {}).get('KHR_materials_pbrSpecularGlossiness', {})
        factor = pbr.get('baseColorFactor', sg.get('diffuseFactor', [1, 1, 1, 1]))
        tinfo = pbr.get('baseColorTexture') or sg.get('diffuseTexture')
        img = None
        tc = 0
        if tinfo is not None:
            tex = doc['textures'][tinfo['index']]
            src = tex.get('source')
            if src is None:
                for ext in tex.get('extensions', {}).values():
                    if 'source' in ext:
                        src = ext['source']
            if src is not None:
                img = image(src)
            tc = tinfo.get('texCoord', 0)
        color = tuple(int(round(max(0.0, min(1.0, c)) * 255)) for c in factor)
        materials.append({'image': img, 'color': color, 'texcoord': tc})
    materials.append({'image': None, 'color': (200, 200, 200, 255), 'texcoord': 0})
    default_mat = len(materials) - 1

    tris = []
    meshes_cache = {}

    def emit(mesh_index, world):
        flip = mathx.det3(world) < 0
        for prim in doc['meshes'][mesh_index]['primitives']:
            mode = prim.get('mode', 4)
            if mode not in (4, 5, 6):
                continue
            at = prim['attributes']
            key = (mesh_index, id(prim))
            if key not in meshes_cache:
                mi = prim.get('material', default_mat)
                pos = accessor(at['POSITION'])
                tcname = f"TEXCOORD_{materials[mi]['texcoord']}"
                uv = accessor(at[tcname]) if tcname in at else None
                idx = [r[0] for r in accessor(prim['indices'])] if 'indices' in prim else list(range(len(pos)))
                meshes_cache[key] = (mi, pos, uv, idx)
            mi, pos, uv, idx = meshes_cache[key]
            if mode == 4:
                corners = [idx[i:i + 3] for i in range(0, len(idx) - 2, 3)]
            elif mode == 5:
                corners = [(idx[i], idx[i + 1], idx[i + 2]) if i % 2 == 0 else (idx[i + 1], idx[i], idx[i + 2]) for i in range(len(idx) - 2)]
            else:
                corners = [(idx[0], idx[i], idx[i + 1]) for i in range(1, len(idx) - 1)]
            wp = [mathx.apply(world, p) for p in pos]
            for c in corners:
                if flip:
                    c = (c[0], c[2], c[1])
                tris.append(([wp[j] for j in c], [tuple(uv[j][:2]) for j in c] if uv else None, mi))

    def walk(ni, parent):
        node = doc['nodes'][ni]
        if 'matrix' in node:
            m = node['matrix']
            local = [[m[c * 4 + r] for c in range(4)] for r in range(4)]
        else:
            local = mathx.trs(node.get('translation', [0, 0, 0]), node.get('rotation', [0, 0, 0, 1]), node.get('scale', [1, 1, 1]))
        world = mathx.mul(parent, local)
        if 'mesh' in node:
            emit(node['mesh'], world)
        for c in node.get('children', []):
            walk(c, world)
    scenes = doc.get('scenes')
    if scenes:
        roots = scenes[doc.get('scene', 0)].get('nodes', [])
    else:
        children = {c for n in doc.get('nodes', []) for c in n.get('children', [])}
        roots = [i for i in range(len(doc.get('nodes', []))) if i not in children]
    for r in roots:
        walk(r, mathx.identity())
    if not tris:
        for i in range(len(doc.get('meshes', []))):
            emit(i, mathx.identity())
    return assemble(tris, materials, os.path.splitext(os.path.basename(path))[0], '.gltf' if blob[:4] != b'glTF' else '.glb')


def load_mtl(path):
    mats = {}
    cur = None
    base = os.path.dirname(path)
    try:
        lines = open(path, 'r', encoding='utf-8', errors='ignore').read().splitlines()
    except OSError:
        return mats
    for line in lines:
        p = line.strip().split()
        if not p:
            continue
        if p[0] == 'newmtl':
            cur = ' '.join(p[1:])
            mats[cur] = {'image': None, 'color': [255, 255, 255, 255]}
        elif cur is None:
            continue
        elif p[0] == 'Kd' and len(p) >= 4:
            mats[cur]['color'][:3] = [int(round(min(1.0, float(c)) * 255)) for c in p[1:4]]
        elif p[0] == 'd' and len(p) >= 2:
            mats[cur]['color'][3] = int(round(min(1.0, float(p[1])) * 255))
        elif p[0] == 'map_Kd' and len(p) >= 2:
            fn = os.path.join(base, p[-1].replace('\\', os.sep))
            try:
                mats[cur]['image'] = open_image(fn)
            except Exception:
                mats[cur]['image'] = None
    for m in mats.values():
        if m['image'] is not None:
            m['color'] = [255, 255, 255, m['color'][3]]
        m['color'] = tuple(m['color'])
    return mats


def load_obj(path):
    base = os.path.dirname(path)
    v, vt = [], []
    mats = {}
    names = []
    materials = []
    cur = None
    tris = []

    def mat_index(name):
        if name not in names:
            names.append(name)
            m = mats.get(name, {'image': None, 'color': (200, 200, 200, 255)})
            materials.append(m)
        return names.index(name)
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            p = line.split()
            if not p or p[0].startswith('#'):
                continue
            if p[0] == 'v':
                v.append(tuple(float(c) for c in p[1:4]))
            elif p[0] == 'vt':
                vt.append((float(p[1]), 1.0 - (float(p[2]) if len(p) > 2 else 0.0)))
            elif p[0] == 'mtllib':
                mats.update(load_mtl(os.path.join(base, ' '.join(p[1:]))))
            elif p[0] == 'usemtl':
                cur = mat_index(' '.join(p[1:]))
            elif p[0] == 'f':
                if cur is None:
                    cur = mat_index('__default__')
                cs = []
                for tok in p[1:]:
                    parts = tok.split('/')
                    vi = int(parts[0])
                    vi = vi - 1 if vi > 0 else len(v) + vi
                    ti = None
                    if len(parts) > 1 and parts[1]:
                        ti = int(parts[1])
                        ti = ti - 1 if ti > 0 else len(vt) + ti
                    cs.append((vi, ti))
                for i in range(1, len(cs) - 1):
                    tri = (cs[0], cs[i], cs[i + 1])
                    uv = [vt[c[1]] for c in tri] if all(c[1] is not None for c in tri) else None
                    tris.append(([v[c[0]] for c in tri], uv, cur))
    return assemble(tris, materials, os.path.splitext(os.path.basename(path))[0], '.obj')


def load_ply(path):
    with open(path, 'rb') as f:
        blob = f.read()
    end = blob.index(b'end_header')
    end = blob.index(b'\n', end) + 1
    header = blob[:end].decode('ascii', errors='ignore').splitlines()
    fmt = 'ascii'
    elements = []
    texfile = None
    for line in header:
        p = line.split()
        if not p:
            continue
        if p[0] == 'format':
            fmt = p[1]
        elif p[0] == 'comment' and len(p) > 2 and p[1].lower() == 'texturefile':
            texfile = ' '.join(p[2:])
        elif p[0] == 'element':
            elements.append((p[1], int(p[2]), []))
        elif p[0] == 'property' and elements:
            if p[1] == 'list':
                elements[-1][2].append((p[4], p[2], p[3]))
            else:
                elements[-1][2].append((p[2], None, p[1]))
    types = {'char': 'b', 'int8': 'b', 'uchar': 'B', 'uint8': 'B', 'short': 'h', 'int16': 'h', 'ushort': 'H', 'uint16': 'H', 'int': 'i', 'int32': 'i', 'uint': 'I', 'uint32': 'I', 'float': 'f', 'float32': 'f', 'double': 'd', 'float64': 'd'}
    data = {}
    if fmt == 'ascii':
        toks = blob[end:].decode('ascii', errors='ignore').split()
        ti = 0
        for name, count, props in elements:
            rows = []
            for _ in range(count):
                row = {}
                for pname, lt, vt_ in props:
                    if lt:
                        n = int(toks[ti])
                        ti += 1
                        row[pname] = [float(x) for x in toks[ti:ti + n]]
                        ti += n
                    else:
                        row[pname] = float(toks[ti])
                        ti += 1
                rows.append(row)
            data[name] = rows
    else:
        bo = '<' if 'little' in fmt else '>'
        pos = end
        for name, count, props in elements:
            rows = []
            for _ in range(count):
                row = {}
                for pname, lt, vt_ in props:
                    if lt:
                        n = struct.unpack_from(bo + types[lt], blob, pos)[0]
                        pos += struct.calcsize(types[lt])
                        st = bo + types[vt_] * n
                        row[pname] = list(struct.unpack_from(st, blob, pos))
                        pos += struct.calcsize(st)
                    else:
                        st = bo + types[vt_]
                        row[pname] = struct.unpack_from(st, blob, pos)[0]
                        pos += struct.calcsize(st)
                rows.append(row)
            data[name] = rows
    verts = data.get('vertex', [])
    ukeys = [('s', 't'), ('u', 'v'), ('texture_u', 'texture_v'), ('texture_s', 'texture_t')]
    uk = next((k for k in ukeys if verts and k[0] in verts[0]), None)
    pos_ = [(r['x'], r['y'], r['z']) for r in verts]
    uv_ = [(r[uk[0]], 1.0 - r[uk[1]]) for r in verts] if uk else None
    img = None
    if texfile:
        try:
            img = open_image(os.path.join(os.path.dirname(path), texfile))
        except Exception:
            img = None
    tris = []
    for r in data.get('face', []):
        idx = r.get('vertex_indices', r.get('vertex_index'))
        if idx is None:
            continue
        idx = [int(i) for i in idx]
        tc = r.get('texcoord')
        for i in range(1, len(idx) - 1):
            c = (0, i, i + 1)
            if tc and len(tc) >= 2 * len(idx):
                uv = [(tc[2 * k], 1.0 - tc[2 * k + 1]) for k in c]
            elif uv_:
                uv = [uv_[idx[k]] for k in c]
            else:
                uv = None
            tris.append(([pos_[idx[k]] for k in c], uv, 0))
    has_uv = any(t[1] for t in tris)
    mat = {'image': img if has_uv else None, 'color': (255, 255, 255, 255) if img and has_uv else (200, 200, 200, 255)}
    return assemble(tris, [mat], os.path.splitext(os.path.basename(path))[0], '.ply')


def load_stl(path):
    with open(path, 'rb') as f:
        blob = f.read()
    tris = []
    if len(blob) >= 84 and 84 + struct.unpack_from('<I', blob, 80)[0] * 50 == len(blob):
        n = struct.unpack_from('<I', blob, 80)[0]
        for i in range(n):
            vals = struct.unpack_from('<12f', blob, 84 + i * 50)
            tris.append(([vals[3:6], vals[6:9], vals[9:12]], None, 0))
    else:
        cur = []
        for line in blob.decode('utf-8', errors='ignore').splitlines():
            p = line.split()
            if len(p) >= 4 and p[0].lower() == 'vertex':
                cur.append(tuple(float(c) for c in p[1:4]))
                if len(cur) == 3:
                    tris.append((cur, None, 0))
                    cur = []
    return assemble(tris, [{'image': None, 'color': (200, 200, 200, 255)}], os.path.splitext(os.path.basename(path))[0], '.stl')


def grid(nu, nv, fn):
    pos, uvs, faces = [], [], []
    for j in range(nv + 1):
        for i in range(nu + 1):
            u, v = i / nu, j / nv
            pos.append(fn(u, v))
            uvs.append((u, v))
    for j in range(nv):
        for i in range(nu):
            a = j * (nu + 1) + i
            b, c, d = a + 1, a + nu + 1, a + nu + 2
            faces.append((a, c, b))
            faces.append((b, c, d))
    return pos, uvs, faces


def primitive(kind, detail=1.0, tube=0.15):
    seg = max(6, int(24 * detail))
    if kind == 'sphere':
        pos, uvs, faces = grid(seg, max(4, seg // 2), lambda u, v: (0.5 * math.sin(math.pi * v) * math.cos(2 * math.pi * u), 0.5 * math.cos(math.pi * v), -0.5 * math.sin(math.pi * v) * math.sin(2 * math.pi * u)))
    elif kind == 'torus':
        r = max(0.005, min(0.45, tube / 2))
        R = 0.5 - r
        pos, uvs, faces = grid(seg * 2, max(3, seg // 4), lambda u, v: ((R + r * math.cos(2 * math.pi * v)) * math.cos(2 * math.pi * u), r * math.sin(2 * math.pi * v), -(R + r * math.cos(2 * math.pi * v)) * math.sin(2 * math.pi * u)))
        faces = [(f[0], f[2], f[1]) for f in faces]
    elif kind == 'cylinder':
        pos, uvs, faces = grid(seg, 1, lambda u, v: (0.5 * math.cos(2 * math.pi * u), 0.5 - v, -0.5 * math.sin(2 * math.pi * u)))
        for y, flip in ((0.5, False), (-0.5, True)):
            c = len(pos)
            pos.append((0.0, y, 0.0))
            uvs.append((0.5, 0.5))
            for i in range(seg + 1):
                a = 2 * math.pi * i / seg
                pos.append((0.5 * math.cos(a), y, -0.5 * math.sin(a)))
                uvs.append((0.5 + 0.5 * math.cos(a), 0.5 - 0.5 * math.sin(a)))
            for i in range(seg):
                f = (c, c + 1 + i, c + 2 + i)
                faces.append((f[0], f[2], f[1]) if flip else f)
    elif kind in ('plane', 'sprite'):
        pos, uvs, faces = grid(1, 1, lambda u, v: (u - 0.5, 0.5 - v, 0.0))
    else:
        pos, uvs, faces = [], [], []
        quads = [((1, 0, 0), (0, 0, -1), (0, 1, 0)), ((-1, 0, 0), (0, 0, 1), (0, 1, 0)), ((0, 1, 0), (1, 0, 0), (0, 0, -1)), ((0, -1, 0), (1, 0, 0), (0, 0, 1)), ((0, 0, 1), (1, 0, 0), (0, 1, 0)), ((0, 0, -1), (-1, 0, 0), (0, 1, 0))]
        for n, ax, ay in quads:
            b = len(pos)
            for u, v in ((0, 0), (1, 0), (0, 1), (1, 1)):
                pos.append(tuple(0.5 * n[k] + (u - 0.5) * ax[k] + (0.5 - v) * ay[k] for k in range(3)))
                uvs.append((u, v))
            faces += [(b, b + 2, b + 1), (b + 1, b + 2, b + 3)]
    return pos, uvs, faces


def load_primitive(kind, image=None, color=(200, 200, 200), detail=1.0, tube=0.15):
    pos, uvs, faces = primitive(kind, detail, tube)
    if image is None:
        return MeshData(pos, faces, None, None, tuple(color[:3]), kind, '.glb')
    return MeshData(pos, faces, uvs, image.convert('RGBA'), (255, 255, 255), kind, '.glb')


def load_image_quad(img, max_w=256, max_h=144):
    from .texture import fit_box, pot_floor
    img = img.convert('RGBA')
    w, h = fit_box(img.width, img.height, max_w, max_h)
    if (w, h) != img.size:
        img = img.resize((w, h), Image.LANCZOS)
    tw = pot_floor(w) * (1 if pot_floor(w) == w else 2)
    th = pot_floor(h) * (1 if pot_floor(h) == h else 2)
    tw, th = max(8, tw), max(8, th)
    canvas = Image.new('RGBA', (tw, th), (0, 0, 0, 0))
    canvas.paste(img, (0, 0))
    u1, v1 = (w - 0.5) / tw, (h - 0.5) / th
    u0, v0 = 0.5 / tw, 0.5 / th
    a = w / h
    pos = [(-a / 2, 0.5, 0.0), (a / 2, 0.5, 0.0), (-a / 2, -0.5, 0.0), (a / 2, -0.5, 0.0)]
    uvs = [(u0, v0), (u1, v0), (u0, v1), (u1, v1)]
    return MeshData(pos, [(0, 2, 1), (1, 2, 3)], uvs, canvas, (255, 255, 255), 'image', '.glb')


def load_model(path):
    ext = os.path.splitext(path)[1].lower()
    if ext in ('.glb', '.gltf'):
        return load_gltf(path)
    if ext == '.obj':
        return load_obj(path)
    if ext == '.ply':
        return load_ply(path)
    if ext == '.stl':
        return load_stl(path)
    raise ValueError(f'unsupported model type: {ext}')


def bounds(positions):
    lo = [min(p[a] for p in positions) for a in range(3)]
    hi = [max(p[a] for p in positions) for a in range(3)]
    return lo, hi


def normalise(mesh, size=1.0, center=True):
    m = mesh.copy()
    if not m.positions:
        return m
    lo, hi = bounds(m.positions)
    c = [(lo[a] + hi[a]) / 2 for a in range(3)] if center else [0.0, 0.0, 0.0]
    ext = max(hi[a] - lo[a] for a in range(3)) or 1.0
    s = size / ext if size and size > 0 else 1.0
    m.positions = [tuple((p[a] - c[a]) * s for a in range(3)) for p in m.positions]
    return m


def weld(positions, uvs, faces, pbits, uvbits):
    from .bbm import axis_bounds, quantize_axis
    pb = [axis_bounds([p[a] for p in positions]) for a in range(3)]
    qp = list(zip(*[quantize_axis([p[a] for p in positions], pbits, *pb[a]) for a in range(3)])) if pbits > 0 else [tuple(struct.unpack('<3e', struct.pack('<3e', *p))) for p in positions]
    if uvs:
        ub = [axis_bounds([t[a] for t in uvs]) for a in range(2)]
        qu = list(zip(*[quantize_axis([t[a] for t in uvs], uvbits, *ub[a]) for a in range(2)]))
    else:
        qu = [None] * len(positions)
    index = {}
    remap = []
    npos, nuv = [], []
    for i in range(len(positions)):
        key = (qp[i], qu[i])
        if key not in index:
            index[key] = len(npos)
            npos.append(positions[i])
            if uvs:
                nuv.append(uvs[i])
        remap.append(index[key])
    seen = set()
    nf = []
    for f in faces:
        g = tuple(remap[i] for i in f)
        if len(set(g)) < 3 or len({qp[i] for i in f}) < 3:
            continue
        k = min((g, (g[1], g[2], g[0]), (g[2], g[0], g[1])))
        if k in seen:
            continue
        seen.add(k)
        nf.append(k)
    nf.sort()
    order = {}
    fp, fu = [], []
    out = []
    for f in nf:
        g = []
        for i in f:
            if i not in order:
                order[i] = len(fp)
                fp.append(npos[i])
                if uvs:
                    fu.append(nuv[i])
            g.append(order[i])
        a, b, c = g
        if b < a and b < c:
            g = [b, c, a]
        elif c < a and c < b:
            g = [c, a, b]
        out.append(tuple(g))
    out.sort()
    return fp, (fu if uvs else None), out
