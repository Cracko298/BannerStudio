import copy
import json
import os
from . import audio, container, meshio, scene, texture
from .container import Banner, Model, Overlay

ANCHOR = 'anchor'
PRIM = 'primitive:'
IMAGE = 'image:'


def default_node(name='Model', source=ANCHOR):
    return {
        'name': name,
        'source': source,
        'texture': '',
        'tint': None,
        'size': 1.0,
        'center': True,
        'detail': 1.0,
        'tube': 0.15,
        'parent': None,
        'offset': [0.0, 0.0, 0.0],
        'rotation': [0.0, 0.0, 0.0],
        'scale': [1.0, 1.0, 1.0],
        'spin': [0.0, 0.0, 0.0],
        'orbit': {'radius_a': 0.0, 'radius_b': 0.0, 'speed': 0.0, 'phase': 0.0, 'tilt_x': 0.0, 'tilt_z': 0.0},
        'bob': {'amplitude': 0.0, 'hz': 0.0, 'phase': 0.0},
        'follow_rotation': False,
        'glow': 0,
        'unlit': False,
        'additive': False,
        'billboard': False,
        'pulse': {'hz': 0.0, 'depth': 0.0, 'phase': 0.0},
        'texture_size': 0,
        'texture_format': '',
    }


def default_project():
    return {
        'title': 'My Banner',
        'author': '',
        'texture': {'max_size': 128, 'format': 'auto', 'dither': True},
        'mesh': {'position_bits': 12},
        'view': {'auto': True, 'pitch': 20.0, 'yaw': 0.0, 'fov': 40.0, 'background_top': [30, 26, 70], 'background': [12, 18, 40], 'opaque': True, 'eye': [0.0, 1.0, 4.0], 'target': [0.0, 0.0, 0.0]},
        'sky': {'path': '', 'scroll_u': 0.0, 'scroll_v': 0.0, 'strength': 255, 'size': 64, 'format': 'i8'},
        'bloom': {'enabled': False, 'intensity': 1.0, 'radius': 6.0},
        'nodes': [],
        'jingle': {'source': 'builtin', 'codec': 'dsp', 'rate': 16000, 'channels': 1, 'max_seconds': 3.0, 'normalise': True, 'trim': True, 'loop': False},
        'icon': {'path': '', 'format': 'auto', 'animation': False},
        'image': {'path': '', 'screen': 'top', 'x': 0, 'y': 0, 'scale': 1.0, 'format': 'auto'},
        'output': '',
    }


def merged(defaults, data):
    out = copy.deepcopy(defaults)
    for k, v in (data or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merged(out[k], v)
        else:
            out[k] = v
    return out


def normalise_project(p):
    p = merged(default_project(), p)
    p['nodes'] = [merged(default_node(), n) for n in p.get('nodes', [])]
    return p


PATH_KEYS = (('jingle', 'source'), ('icon', 'path'), ('image', 'path'), ('sky', 'path'))


def is_file_ref(v):
    return isinstance(v, str) and v and v != ANCHOR and not v.startswith(PRIM) and not v.startswith(IMAGE) and v != 'builtin'


def map_source(v, fn):
    if isinstance(v, str) and v.startswith(IMAGE) and len(v) > len(IMAGE):
        return IMAGE + fn(v[len(IMAGE):])
    return fn(v)


def load_project(path):
    with open(path, 'r', encoding='utf-8') as f:
        p = normalise_project(json.load(f))
    base = os.path.dirname(os.path.abspath(path))

    def absol(v):
        return os.path.normpath(os.path.join(base, v)) if is_file_ref(v) and not os.path.isabs(v) else v
    for n in p['nodes']:
        n['source'] = map_source(n['source'], absol)
        n['texture'] = absol(n['texture'])
    for a, b in PATH_KEYS:
        p[a][b] = absol(p[a][b])
    p['output'] = absol(p['output'])
    return p


def save_project(p, path):
    p = copy.deepcopy(p)
    base = os.path.dirname(os.path.abspath(path))

    def rel(v):
        if not is_file_ref(v):
            return v
        try:
            return os.path.relpath(v, base).replace(os.sep, '/')
        except ValueError:
            return v
    for n in p['nodes']:
        n['source'] = map_source(n['source'], rel)
        n['texture'] = rel(n['texture'])
    for a, b in PATH_KEYS:
        p[a][b] = rel(p[a][b])
    p['output'] = rel(p['output'])
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(p, f, indent=2)


class Cache:
    def __init__(self):
        self.models = {}
        self.images = {}
        self.jingles = {}

    def image(self, path):
        key = (path, os.path.getmtime(path))
        if key not in self.images:
            self.images[key] = meshio.open_image(path)
        return self.images[key]

    def model(self, path):
        key = (path, os.path.getmtime(path))
        if key not in self.models:
            self.models[key] = meshio.load_model(path)
        return self.models[key]

    def jingle(self, cfg):
        src = cfg.get('source') or 'builtin'
        stamp = os.path.getmtime(src) if is_file_ref(src) and os.path.exists(src) else 0
        key = (src, stamp, cfg['codec'], int(cfg['rate']), int(cfg['channels']), float(cfg['max_seconds']), bool(cfg['normalise']), bool(cfg['trim']), bool(cfg['loop']))
        if key not in self.jingles:
            self.jingles[key] = audio.make_jingle(src, audio.CODEC_KEYS[cfg['codec']], int(cfg['rate']), int(cfg['channels']), float(cfg['max_seconds']), bool(cfg['normalise']), bool(cfg['trim']), bool(cfg['loop']))
        return self.jingles[key]


def mesh_for(n, cache):
    src = n['source']
    img = cache.image(n['texture']) if n.get('texture') else None
    if src.startswith(IMAGE):
        m = meshio.load_image_quad(cache.image(src[len(IMAGE):]))
    elif src.startswith(PRIM):
        m = meshio.load_primitive(src[len(PRIM):], img, (255, 255, 255), float(n.get('detail', 1.0)), float(n.get('tube', 0.15)))
    else:
        m = cache.model(src).copy()
        if img is not None and m.uvs:
            m.image = img
            m.tint = (255, 255, 255)
        elif m.image is None and n.get('tint'):
            m.tint = (255, 255, 255)
    return meshio.normalise(m, float(n['size']) if n.get('size') else 0.0, bool(n.get('center', True)))


def node_tint(n):
    if n.get('tint'):
        return tuple(int(c) for c in n['tint'][:3])
    if n['source'].startswith(PRIM) and not n.get('texture') and n['source'] != PRIM + 'sprite':
        return (220, 220, 220)
    return None


def node_parent(nodes, ref, me):
    if ref is None or ref == '':
        return -1
    if isinstance(ref, int):
        return ref if 0 <= ref < len(nodes) and ref != me else -1
    for i, n in enumerate(nodes):
        if n['name'] == ref and i != me:
            return i
    return -1


def vec(v, n=3, d=0.0):
    v = list(v or [])
    return [float(x) for x in (v + [d] * n)[:n]]


def build_banner(p, cache=None, with_audio=True, progress=None):
    cache = cache or Cache()
    say = progress or (lambda s: None)
    p = normalise_project(p)
    b = Banner()
    b.title = p['title']
    b.author = p['author']
    b.pos_bits = int(p['mesh']['position_bits'])
    b.dither = bool(p['texture']['dither'])
    tfmt = texture.FORMAT_KEYS.get(str(p['texture']['format']).lower())
    cap = int(p['texture']['max_size'])
    mesh_keys = {}
    tex_keys = {}
    node_mesh = []

    def add_texture(ik, img, size, fmt, dither=None):
        key = (ik, size, fmt)
        if key not in tex_keys:
            tex_keys[key] = len(b.textures)
            b.textures.append(texture.prepare_texture(img, size))
            b.texture_formats.append(fmt if fmt is not None else texture.choose_format(b.textures[-1]))
            b.texture_dither.append(b.dither if dither is None else dither)
        return tex_keys[key]
    for i, n in enumerate(p['nodes']):
        if n['source'] == ANCHOR or not n['source']:
            node_mesh.append(-1)
            continue
        nsize = int(n.get('texture_size') or 0) or cap
        nfmt = texture.FORMAT_KEYS.get(str(n.get('texture_format') or '').lower(), tfmt) if n.get('texture_format') else tfmt
        key = (n['source'], n.get('texture', ''), bool(n.get('tint')) and not n['source'].startswith(PRIM), float(n['size'] or 0), bool(n['center']), float(n.get('detail', 1)), float(n.get('tube', 0.15)), nsize, nfmt)
        if key not in mesh_keys:
            say(f"Loading {n['name']}")
            m = mesh_for(n, cache)
            tex = -1
            uvbits = 10
            if m.image is not None and m.uvs:
                ik = ('file', n['texture']) if n.get('texture') else ('src', n['source'], id(m.image))
                tex = add_texture(ik, m.image, nsize, nfmt)
                uvbits = container.uv_bits_for(b.textures[tex].size)
            pos, uvs, faces = meshio.weld(m.positions, m.uvs if tex >= 0 else None, m.faces, b.pos_bits, uvbits)
            mesh_keys[key] = len(b.models)
            b.models.append(Model(pos, faces, uvs, tex, m.tint if tex < 0 else (255, 255, 255), (n['name'] or m.name)[:16], m.source))
        node_mesh.append(mesh_keys[key])
    nodes = []
    for i, n in enumerate(p['nodes']):
        o = n['orbit']
        bo = n['bob']
        pu = n.get('pulse') or {}
        nodes.append(scene.Node(node_mesh[i], node_parent(p['nodes'], n.get('parent'), i), vec(n['offset']), vec(n['rotation']), vec(n['scale'], d=1.0), vec(n['spin']),
                                [float(o['radius_a']), float(o['radius_b']), float(o['speed']), float(o['phase']), float(o['tilt_x']), float(o['tilt_z'])],
                                [float(bo['amplitude']), float(bo['hz']), float(bo['phase'])], bool(n['follow_rotation']), node_tint(n),
                                int(n.get('glow') or 0), bool(n.get('billboard')), bool(n.get('unlit')), bool(n.get('additive')),
                                [float(pu.get('hz', 0)), float(pu.get('depth', 0)), float(pu.get('phase', 0))]))
    b.nodes, _ = scene.topo_sort(nodes)
    sk = p['sky']
    if sk.get('path'):
        img = cache.image(sk['path'])
        sfmt = texture.FORMAT_KEYS.get(str(sk.get('format') or 'i8').lower(), texture.I8) or texture.I8
        b.sky = scene.Sky(add_texture(('sky', sk['path']), img, int(sk.get('size') or cap), sfmt, False), float(sk['scroll_u']), float(sk['scroll_v']), int(sk['strength']))
    bl = p['bloom']
    if bl.get('enabled'):
        b.bloom = scene.Bloom(float(bl['intensity']), float(bl['radius']), True)
    v = p['view']
    bg = tuple(int(c) for c in v['background'][:3])
    bgt = tuple(int(c) for c in (v.get('background_top') or v['background'])[:3])
    if v['auto']:
        mb = [meshio.bounds(m.positions) if m.positions else ([0, 0, 0], [0, 0, 0]) for m in b.models]
        b.view = scene.auto_view(b.nodes, mb, float(v['fov']), float(v['pitch']), float(v['yaw']), bg, background_top=bgt, opaque=bool(v.get('opaque', True)))
    else:
        b.view = scene.View(vec(v['eye']), vec(v['target']), float(v['fov']), bg, bgt, bool(v.get('opaque', True)))
    im = p['image']
    if im.get('path'):
        b.overlay = Overlay(cache.image(im['path']), container.SCREENS.get(im.get('screen', 'top'), 0), int(im['x']), int(im['y']), float(im['scale']))
        b.overlay_format = texture.FORMAT_KEYS.get(str(im.get('format', 'auto')).lower())
    ic = p['icon'].get('path')
    if ic:
        if ic.lower().endswith('.icon'):
            with open(ic, 'rb') as f:
                b.icon = f.read()
            b.icon_kind = container.ICON_RAW
        else:
            b.icon = cache.image(ic)
            b.icon_kind = container.ICON_AURORA
            b.icon_animation = bool(p['icon'].get('animation'))
    if with_audio:
        say('Encoding jingle')
        b.jingle = cache.jingle(p['jingle'])
    return b


def build_file(p, out=None, cache=None, progress=None):
    b = build_banner(p, cache, True, progress)
    blob = container.write(b)
    out = out or p.get('output')
    if out:
        if not out.lower().endswith('.bnr'):
            out += '.bnr'
        with open(out, 'wb') as f:
            f.write(blob)
    return blob, out


def preview_banner(p, cache=None):
    b = build_banner(p, cache, with_audio=False)
    b.textures = [texture.decode(texture.encode(t, f, b.texture_dither[i] if i < len(b.texture_dither) else b.dither))[0] for i, (t, f) in enumerate(zip(b.textures, b.texture_formats))]
    if b.overlay is not None:
        img = texture.prepare_overlay(b.overlay.image, 256, 144)
        b.overlay.image = texture.decode(texture.encode(img, b.overlay_format, b.dither))[0]
    return b


def size_report(blob):
    rows = [('HEAD', 0, container.HEADER.size)]
    for tag, pos, ln in container.chunks(blob):
        rows.append((tag, pos, ln + 8))
    return rows
