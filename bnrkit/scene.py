import math
from . import mathx
from .bitio import Writer, Reader

F_OFFSET = 1
F_ROTATION = 2
F_SCALE = 4
F_SPIN = 8
F_ORBIT = 16
F_BOB = 32
F_INHERIT = 64
F_TINT = 128
F_GLOW = 256
F_BILLBOARD = 512
F_UNLIT = 1024
F_ADDITIVE = 2048
F_PULSE = 4096


def to565(c):
    r, g, b = (int(v) for v in c[:3])
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def from565(v):
    r, g, b = (v >> 11) & 31, (v >> 5) & 63, v & 31
    return ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2))


class Node:
    def __init__(self, mesh=-1, parent=-1, offset=(0, 0, 0), rotation=(0, 0, 0), scale=(1, 1, 1), spin=(0, 0, 0), orbit=(0, 0, 0, 0, 0, 0), bob=(0, 0, 0), inherit=False, tint=None,
                 glow=0, billboard=False, unlit=False, additive=False, pulse=(0, 0, 0)):
        self.mesh = mesh
        self.parent = parent
        self.offset = list(offset)
        self.rotation = list(rotation)
        self.scale = list(scale)
        self.spin = list(spin)
        self.orbit = list(orbit)
        self.bob = list(bob)
        self.inherit = bool(inherit)
        self.tint = tuple(tint[:3]) if tint else None
        self.glow = max(0, min(255, int(glow)))
        self.billboard = bool(billboard)
        self.unlit = bool(unlit)
        self.additive = bool(additive)
        self.pulse = list(pulse)

    def clone(self, **kw):
        n = Node(self.mesh, self.parent, self.offset, self.rotation, self.scale, self.spin, self.orbit, self.bob, self.inherit, self.tint, self.glow, self.billboard, self.unlit, self.additive, self.pulse)
        for k, v in kw.items():
            setattr(n, k, v)
        return n

    def flags(self):
        f = 0
        if any(self.offset):
            f |= F_OFFSET
        if any(self.rotation):
            f |= F_ROTATION
        if any(v != 1 for v in self.scale):
            f |= F_SCALE
        if any(self.spin):
            f |= F_SPIN
        if self.orbit[0] or self.orbit[1]:
            f |= F_ORBIT
        if self.bob[0]:
            f |= F_BOB
        if self.inherit:
            f |= F_INHERIT
        if self.tint and tuple(self.tint) != (255, 255, 255):
            f |= F_TINT
        if self.glow:
            f |= F_GLOW
        if self.billboard:
            f |= F_BILLBOARD
        if self.unlit:
            f |= F_UNLIT
        if self.additive:
            f |= F_ADDITIVE
        if self.pulse[0] and self.pulse[1]:
            f |= F_PULSE
        return f

    def brightness(self, t):
        if not (self.pulse[0] and self.pulse[1]):
            return 1.0
        hz, depth, phase = self.pulse
        return 1.0 - depth + depth * (0.5 + 0.5 * math.sin(2 * math.pi * hz * t + math.radians(phase)))

    def local(self, t):
        x, y, z = self.offset
        if self.orbit[0] or self.orbit[1]:
            a, b, speed, phase, tx, tz = self.orbit
            th = math.radians(phase + speed * t)
            p = mathx.apply(mathx.mul(mathx.rot_z(tz), mathx.rot_x(tx)), (a * math.cos(th), 0.0, -b * math.sin(th)))
            x, y, z = x + p[0], y + p[1], z + p[2]
        if self.bob[0]:
            y += self.bob[0] * math.sin(2 * math.pi * self.bob[1] * t + math.radians(self.bob[2]))
        r = [self.rotation[i] + self.spin[i] * t for i in range(3)]
        return mathx.mul(mathx.translate((x, y, z)), mathx.mul(mathx.euler(*r), mathx.scale(self.scale)))


def billboard_matrix(m, node, t, cam):
    right, up, back = cam
    roll = math.radians(node.rotation[2] + node.spin[2] * t)
    c, s = math.cos(roll), math.sin(roll)
    rx = [c * right[k] + s * up[k] for k in range(3)]
    uy = [-s * right[k] + c * up[k] for k in range(3)]
    sx = math.sqrt(m[0][0] ** 2 + m[1][0] ** 2 + m[2][0] ** 2)
    sy = math.sqrt(m[0][1] ** 2 + m[1][1] ** 2 + m[2][1] ** 2)
    sz = math.sqrt(m[0][2] ** 2 + m[1][2] ** 2 + m[2][2] ** 2)
    return [[rx[0] * sx, uy[0] * sy, back[0] * sz, m[0][3]],
            [rx[1] * sx, uy[1] * sy, back[1] * sz, m[1][3]],
            [rx[2] * sx, uy[2] * sy, back[2] * sz, m[2][3]],
            [0.0, 0.0, 0.0, 1.0]]


def camera_axes(view):
    if view is None:
        return None
    v = mathx.look_at(view.eye, view.target)
    return (v[0][:3], v[1][:3], v[2][:3])


def world_matrices(nodes, t, view=None):
    cam = camera_axes(view)
    out = []
    for n in nodes:
        m = n.local(t)
        if 0 <= n.parent < len(out):
            p = out[n.parent]
            m = mathx.mul(p if n.inherit else mathx.translate((p[0][3], p[1][3], p[2][3])), m)
        if n.billboard and cam is not None:
            m = billboard_matrix(m, n, t, cam)
        out.append(m)
    return out


def topo_sort(nodes):
    order = []
    state = {}

    def visit(i, depth=0):
        if state.get(i) == 2:
            return
        if state.get(i) == 1 or depth > 255:
            raise ValueError('anchor loop detected: a node cannot be anchored to its own child')
        state[i] = 1
        p = nodes[i].parent
        if 0 <= p < len(nodes):
            visit(p, depth + 1)
        state[i] = 2
        order.append(i)
    for i in range(len(nodes)):
        visit(i)
    remap = {old: new for new, old in enumerate(order)}
    out = [nodes[old].clone(parent=remap.get(nodes[old].parent, -1) if nodes[old].parent >= 0 else -1) for old in order]
    return out, remap


def encode_nodes(nodes):
    if len(nodes) > 255:
        raise ValueError('a banner can hold at most 255 nodes')
    w = Writer().u8(len(nodes))
    for n in nodes:
        f = n.flags()
        w.u8(0xFF if n.mesh < 0 else n.mesh).u8(0xFF if n.parent < 0 else n.parent).u16(f)
        if f & F_OFFSET:
            w.halves(n.offset)
        if f & F_ROTATION:
            w.halves(n.rotation)
        if f & F_SCALE:
            w.halves(n.scale)
        if f & F_SPIN:
            w.halves(n.spin)
        if f & F_ORBIT:
            w.halves(n.orbit)
        if f & F_BOB:
            w.halves(n.bob)
        if f & F_TINT:
            w.u16(to565(n.tint))
        if f & F_GLOW:
            w.u8(n.glow)
        if f & F_PULSE:
            w.halves(n.pulse)
    return w.bytes()


def decode_nodes(data):
    r = Reader(data)
    out = []
    for _ in range(r.u8()):
        m, p, f = r.u8(), r.u8(), r.u16()
        n = Node(-1 if m == 0xFF else m, -1 if p == 0xFF else p, inherit=bool(f & F_INHERIT), billboard=bool(f & F_BILLBOARD), unlit=bool(f & F_UNLIT), additive=bool(f & F_ADDITIVE))
        if f & F_OFFSET:
            n.offset = r.halves(3)
        if f & F_ROTATION:
            n.rotation = r.halves(3)
        if f & F_SCALE:
            n.scale = r.halves(3)
        if f & F_SPIN:
            n.spin = r.halves(3)
        if f & F_ORBIT:
            n.orbit = r.halves(6)
        if f & F_BOB:
            n.bob = r.halves(3)
        if f & F_TINT:
            n.tint = from565(r.u16())
        if f & F_GLOW:
            n.glow = r.u8()
        if f & F_PULSE:
            n.pulse = r.halves(3)
        out.append(n)
    return out


class View:
    def __init__(self, eye=(0, 1, 4), target=(0, 0, 0), fov=40.0, background=(16, 20, 36), background_top=None, opaque=True):
        self.eye = list(eye)
        self.target = list(target)
        self.fov = fov
        self.background = tuple(background[:3])
        self.background_top = tuple(background_top[:3]) if background_top else tuple(background[:3])
        self.opaque = bool(opaque)

    def encode(self):
        return Writer().halves(self.eye).halves(self.target).halves([self.fov]).u16(to565(self.background_top)).u16(to565(self.background)).u8(1 if self.opaque else 0).bytes()

    @staticmethod
    def decode(data):
        r = Reader(data)
        eye, target, fov = r.halves(3), r.halves(3), r.halves(1)[0]
        top, bottom = from565(r.u16()), from565(r.u16())
        flags = r.u8() if len(data) > 18 else 1
        return View(eye, target, fov, bottom, top, bool(flags & 1))


class Sky:
    def __init__(self, texture=-1, scroll_u=0.0, scroll_v=0.0, strength=255, image=None):
        self.texture = texture
        self.scroll_u = scroll_u
        self.scroll_v = scroll_v
        self.strength = max(0, min(255, int(strength)))
        self.image = image

    def encode(self):
        return Writer().u8(0xFF if self.texture < 0 else self.texture).halves([self.scroll_u, self.scroll_v]).u8(self.strength).bytes()

    @staticmethod
    def decode(data):
        r = Reader(data)
        t = r.u8()
        su, sv = r.halves(2)
        return Sky(-1 if t == 0xFF else t, su, sv, r.u8())


class Bloom:
    def __init__(self, intensity=1.0, radius=6.0, enabled=True):
        self.intensity = float(intensity)
        self.radius = float(radius)
        self.enabled = bool(enabled)

    def encode(self):
        return Writer().halves([self.intensity, self.radius]).bytes()

    @staticmethod
    def decode(data):
        i, rad = Reader(data).halves(2)
        return Bloom(i, rad, True)


def scene_bounds(nodes, mesh_bounds, duration=12.0, steps=48):
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    for s in range(steps):
        mats = world_matrices(nodes, duration * s / steps)
        for n, m in zip(nodes, mats):
            if n.mesh < 0 or n.mesh >= len(mesh_bounds):
                continue
            a, b = mesh_bounds[n.mesh]
            for cx in (a[0], b[0]):
                for cy in (a[1], b[1]):
                    for cz in (a[2], b[2]):
                        p = mathx.apply(m, (cx, cy, cz))
                        for k in range(3):
                            lo[k] = min(lo[k], p[k])
                            hi[k] = max(hi[k], p[k])
    if lo[0] == math.inf:
        return [-1, -1, -1], [1, 1, 1]
    return lo, hi


def auto_view(nodes, mesh_bounds, fov=40.0, pitch=20.0, yaw=0.0, background=(16, 20, 36), aspect=400 / 240, background_top=None, opaque=True):
    lo, hi = scene_bounds(nodes, mesh_bounds)
    c = [(lo[k] + hi[k]) / 2 for k in range(3)]
    r = max(1e-3, max(hi[k] - lo[k] for k in range(3)) / 2)
    half = math.radians(fov) / 2
    fit = min(math.tan(half), math.tan(half) * aspect)
    dist = r / fit * 0.92 + r
    pr, yr = math.radians(pitch), math.radians(yaw)
    d = (math.sin(yr) * math.cos(pr), math.sin(pr), math.cos(yr) * math.cos(pr))
    return View([c[k] + d[k] * dist for k in range(3)], c, fov, background, background_top, opaque)
