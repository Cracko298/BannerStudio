import math
from PIL import Image, ImageChops, ImageDraw
from . import mathx
from .scene import world_matrices

TOP = (400, 240)
BOTTOM = (320, 240)
LIGHT = mathx.norm((-0.35, 0.6, 0.75))
TEX_AREA = 90.0
STAND_IN = ((44, 47, 58), (12, 13, 18))


def solve_affine(src, dst):
    (x0, y0), (x1, y1), (x2, y2) = src
    det = x0 * (y1 - y2) - y0 * (x1 - x2) + (x1 * y2 - x2 * y1)
    if abs(det) < 1e-9:
        return None
    out = []
    for k in range(2):
        t0, t1, t2 = dst[0][k], dst[1][k], dst[2][k]
        a = (t0 * (y1 - y2) - y0 * (t1 - t2) + (t1 * y2 - t2 * y1)) / det
        b = (x0 * (t1 - t2) - t0 * (x1 - x2) + (x1 * t2 - x2 * t1)) / det
        c = (x0 * (y1 * t2 - y2 * t1) - y0 * (x1 * t2 - x2 * t1) + t0 * (x1 * y2 - x2 * y1)) / det
        out += [a, b, c]
    return out


def gradient(size, top, bottom):
    w, h = size
    col = Image.new('RGB', (1, h))
    px = col.load()
    for y in range(h):
        t = y / max(1, h - 1)
        px[0, y] = tuple(int(top[k] + (bottom[k] - top[k]) * t) for k in range(3))
    return col.resize((w, h), Image.NEAREST)


def halo_texture(size=32):
    img = Image.new('RGB', (size, size))
    px = img.load()
    c = (size - 1) / 2.0
    for y in range(size):
        for x in range(size):
            r = math.hypot(x - c, y - c) / (size / 2.0)
            g = int(round(max(0.0, 1.0 - r) ** 2 * 255))
            px[x, y] = (g, g, g)
    return img


def average_colour(img):
    data = img.convert('RGBA').tobytes()
    tot = [0, 0, 0]
    wsum = 0
    for i in range(0, len(data), 4):
        a = data[i + 3] + 1
        tot[0] += data[i] * a
        tot[1] += data[i + 1] * a
        tot[2] += data[i + 2] * a
        wsum += a
    return tuple(v // max(1, wsum) for v in tot)


class Renderer:
    def __init__(self, banner, wallpaper=None):
        self.banner = banner
        self.tex = []
        self.avg = []
        for t in banner.textures:
            rgba = t.convert('RGBA')
            self.tex.append((rgba, rgba.tobytes(), rgba.width, rgba.height, rgba.getchannel('A'), rgba.convert('RGB')))
            self.avg.append(average_colour(rgba))
        halo = halo_texture()
        self.halo_ti = len(self.tex)
        hr = halo.convert('RGBA')
        self.tex.append((hr, hr.tobytes(), 32, 32, hr.getchannel('A'), halo))
        self.radius = [max((math.sqrt(x * x + y * y + z * z) for x, y, z in m.positions), default=0.0) for m in banner.models]
        v = banner.view
        if getattr(v, 'opaque', True):
            self.base = gradient(TOP, v.background_top, v.background)
        elif wallpaper is not None:
            self.base = wallpaper.convert('RGB').resize(TOP, Image.BILINEAR)
        else:
            self.base = gradient(TOP, *STAND_IN)
        self.sky = None
        sk = getattr(banner, 'sky', None)
        if sk is not None and 0 <= sk.texture < len(banner.textures):
            img = self.tex[sk.texture][5].resize(TOP, Image.BILINEAR)
            if sk.strength < 255:
                img = img.point(lambda c, s=sk.strength: c * s // 255)
            self.sky = (img, sk)
        bl = getattr(banner, 'bloom', None)
        self.bloom = bl if bl is not None and bl.enabled and bl.intensity > 0 else None
        self.has_add = any(n.additive for n in banner.nodes) or (self.bloom is not None and any(n.glow for n in banner.nodes))
        self.bottom_cache = None
        self.sprite_cache = {}

    def sample(self, ti, u, v):
        _, data, w, h, _, _ = self.tex[ti]
        x = int(u * w) % w
        y = int(v * h) % h
        i = (y * w + x) * 4
        return data[i], data[i + 1], data[i + 2], data[i + 3]

    def background(self, t):
        if self.sky is None:
            return self.base.copy()
        img, sk = self.sky
        dx = int(round(sk.scroll_u * t * TOP[0])) % TOP[0]
        dy = int(round(sk.scroll_v * t * TOP[1])) % TOP[1]
        moved = ImageChops.offset(img, dx, dy) if dx or dy else img
        return ImageChops.add(self.base, moved)

    def render(self, t=0.0, textured=True, budget=None, show_bottom=True, bloom=True):
        b = self.banner
        top = self.background(t)
        view = mathx.look_at(b.view.eye, b.view.target)
        f = 1.0 / math.tan(math.radians(max(5.0, min(150.0, b.view.fov))) / 2)
        aspect = TOP[0] / TOP[1]
        hw, hh = TOP[0] / 2, TOP[1] / 2
        fx, fy = f / aspect * hw, f * hh
        tris = []
        mats = world_matrices(b.nodes, t, b.view)
        do_halo = bloom and self.bloom is not None
        halo_k = (1.0 + self.bloom.radius / 8.0) if do_halo else 0.0
        for node, m in zip(b.nodes, mats):
            if node.mesh < 0 or node.mesh >= len(b.models):
                continue
            mdl = b.models[node.mesh]
            mv = mathx.mul(view, m)
            nt = node.tint or (255, 255, 255)
            br = node.brightness(t)
            ti = mdl.texture if 0 <= mdl.texture < len(b.textures) and mdl.uvs else -1
            if do_halo and node.glow and mv[2][3] < -0.05:
                cz = mv[2][3]
                sc = max(math.sqrt(m[0][k] ** 2 + m[1][k] ** 2 + m[2][k] ** 2) for k in range(3))
                hs = self.radius[node.mesh] * sc * halo_k
                cx, cy = hw + mv[0][3] * fx / -cz, hh - mv[1][3] * fy / -cz
                rx, ry = hs * fx / -cz, hs * fy / -cz
                base = self.avg[ti] if ti >= 0 else (255, 255, 255)
                g = node.glow / 255.0 * self.bloom.intensity * br
                hc = tuple(min(1.0, base[k] * mdl.tint[k] * nt[k] / 16581375.0 * g) for k in range(3))
                tris.append((cz * 3 + 1e-4, (cx - rx, cy - ry, cx + rx, cy + ry), 1.0, self.halo_ti, (0.0, 0.0, 1.0, 1.0), hc, 3, 0))
            cm = tuple(mdl.tint[k] * nt[k] / 65025.0 * br for k in range(3))
            if node.additive and node.billboard and len(mdl.positions) == 4 and ti >= 0 and not (node.rotation[2] or node.spin[2]):
                cz = mv[2][3]
                if cz < -0.05:
                    ps = [(mv[0][0] * x + mv[0][1] * y + mv[0][3], mv[1][0] * x + mv[1][1] * y + mv[1][3], mv[2][0] * x + mv[2][1] * y + mv[2][3]) for x, y, z in mdl.positions]
                    sx = [hw + q[0] * fx / -q[2] for q in ps]
                    sy = [hh - q[1] * fy / -q[2] for q in ps]
                    us = [q[0] for q in mdl.uvs]
                    vv = [q[1] for q in mdl.uvs]
                    tris.append((cz * 3, (min(sx), min(sy), max(sx), max(sy)), 1.0, ti, (min(us), min(vv), max(us), max(vv)), cm, 3, 0))
                continue
            (a0, a1, a2, a3), (b0, b1, b2, b3), (c0, c1, c2, c3) = mv[0], mv[1], mv[2]
            vs = []
            proj = []
            for x, y, z in mdl.positions:
                px, py, pz = a0 * x + a1 * y + a2 * z + a3, b0 * x + b1 * y + b2 * z + b3, c0 * x + c1 * y + c2 * z + c3
                vs.append((px, py, pz))
                proj.append((hw + px * fx / -pz, hh - py * fy / -pz) if pz < -0.02 else None)
            mode = 2 if node.additive else 0
            uvs = mdl.uvs
            for a, c, d in mdl.faces:
                pa, pc, pd = proj[a], proj[c], proj[d]
                if pa is None or pc is None or pd is None:
                    continue
                area = (pc[0] - pa[0]) * (pd[1] - pa[1]) - (pc[1] - pa[1]) * (pd[0] - pa[0])
                if area >= 0:
                    continue
                if node.unlit:
                    shade = 1.0
                else:
                    va, vc, vd = vs[a], vs[c], vs[d]
                    n = mathx.norm(mathx.cross((vc[0] - va[0], vc[1] - va[1], vc[2] - va[2]), (vd[0] - va[0], vd[1] - va[1], vd[2] - va[2])))
                    shade = 0.32 + 0.68 * max(0.0, n[0] * LIGHT[0] + n[1] * LIGHT[1] + n[2] * LIGHT[2])
                depth = vs[a][2] + vs[c][2] + vs[d][2]
                tris.append((depth, (pa, pc, pd), shade, ti, (uvs[a], uvs[c], uvs[d]) if ti >= 0 else None, cm, mode, -area * 0.5))
        tris.sort(key=lambda q: q[0])
        draw = ImageDraw.Draw(top)
        add = Image.new('RGB', TOP, (0, 0, 0)) if self.has_add else None
        adraw = ImageDraw.Draw(add) if add is not None else None
        addp = Image.new('RGB', TOP, (0, 0, 0)) if self.has_add else None
        apdraw = ImageDraw.Draw(addp) if addp is not None else None
        use_tex = textured and (budget is None or len(tris) <= budget)
        for depth, pts, shade, ti, uv, cm, mode, area in tris:
            if mode == 3:
                self.blit(add, pts, ti, uv, cm)
                continue
            if ti >= 0:
                cu = (uv[0][0] + uv[1][0] + uv[2][0]) / 3
                cv = (uv[0][1] + uv[1][1] + uv[2][1]) / 3
                r, g, bb, al = self.sample(ti, cu, cv)
                base = (r * cm[0], g * cm[1], bb * cm[2])
            else:
                al = 255
                base = (255 * cm[0], 255 * cm[1], 255 * cm[2])
            col = (min(255, int(base[0] * shade)), min(255, int(base[1] * shade)), min(255, int(base[2] * shade)))
            drew = False
            canvas = addp if mode == 2 else top
            if ti >= 0 and use_tex and area >= TEX_AREA:
                drew = self.textured(canvas, pts, uv, ti, shade, cm)
            if not drew:
                if al < 128 and ti >= 0:
                    continue
                (apdraw if mode == 2 else draw).polygon(pts, fill=col)
            if mode == 0 and add is not None:
                adraw.polygon(pts, fill=(0, 0, 0))
                apdraw.polygon(pts, fill=(0, 0, 0))
        if add is not None:
            top = ImageChops.add(ImageChops.add(top, add), addp)
        o = b.overlay
        if o is not None and o.screen == 0:
            self.paste_overlay(top, o)
        if not show_bottom:
            return top
        if self.bottom_cache is None:
            bottom = Image.new('RGB', BOTTOM, (28, 28, 32))
            if o is not None and o.screen == 1:
                self.paste_overlay(bottom, o)
            frame = Image.new('RGB', (TOP[0], TOP[1] + BOTTOM[1]), (10, 10, 12))
            frame.paste(bottom, ((TOP[0] - BOTTOM[0]) // 2, TOP[1]))
            self.bottom_cache = frame
        out = self.bottom_cache.copy()
        out.paste(top, (0, 0))
        return out

    def blit(self, canvas, rect, ti, uvr, cm):
        x0, y0, x1, y1 = rect
        w, h = int(round(x1 - x0)), int(round(y1 - y0))
        if w < 1 or h < 1:
            return
        ix, iy = int(round(x0)), int(round(y0))
        if ix >= canvas.width or iy >= canvas.height or ix + w <= 0 or iy + h <= 0:
            return
        col = tuple(min(255, int(c * 255)) >> 3 for c in cm)
        key = (ti, w, h, uvr, col)
        spr = self.sprite_cache.get(key)
        if spr is None:
            if len(self.sprite_cache) > 6000:
                self.sprite_cache.clear()
            src = self.tex[ti][5]
            tw, th = src.size
            box = (int(uvr[0] * tw), int(uvr[1] * th), max(int(uvr[0] * tw) + 1, int(round(uvr[2] * tw))), max(int(uvr[1] * th) + 1, int(round(uvr[3] * th))))
            spr = src.crop(box).resize((w, h), Image.BILINEAR)
            mul = tuple(min(255, (c << 3) + 4) for c in col)
            if mul != (255, 255, 255):
                spr = ImageChops.multiply(spr, Image.new('RGB', (w, h), mul))
            self.sprite_cache[key] = spr
        cx0, cy0 = max(0, ix), max(0, iy)
        cx1, cy1 = min(canvas.width, ix + w), min(canvas.height, iy + h)
        part = spr.crop((cx0 - ix, cy0 - iy, cx1 - ix, cy1 - iy)) if (cx0, cy0, cx1, cy1) != (ix, iy, ix + w, iy + h) else spr
        region = canvas.crop((cx0, cy0, cx1, cy1))
        canvas.paste(ImageChops.add(region, part), (cx0, cy0))

    def paste_overlay(self, canvas, o):
        img = o.image.convert('RGBA')
        w, h = max(1, int(round(img.width * o.scale))), max(1, int(round(img.height * o.scale)))
        img = img.resize((w, h), Image.NEAREST)
        canvas.paste(img, (int(o.x), int(o.y)), img)

    def textured(self, canvas, pts, uv, ti, shade, cm):
        rgba, _, tw, th, alpha, rgb = self.tex[ti]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x0c, y0c = max(0, int(math.floor(min(xs)))), max(0, int(math.floor(min(ys))))
        x1c, y1c = min(canvas.width, int(math.ceil(max(xs))) + 1), min(canvas.height, int(math.ceil(max(ys))) + 1)
        w, h = x1c - x0c, y1c - y0c
        if w < 2 or h < 2:
            return False
        local = [(p[0] - x0c, p[1] - y0c) for p in pts]
        aff = solve_affine(local, [(q[0] * tw, q[1] * th) for q in uv])
        if aff is None:
            return False
        tile = rgba.transform((w, h), Image.AFFINE, aff, resample=Image.NEAREST)
        mask = Image.new('L', (w, h), 0)
        ImageDraw.Draw(mask).polygon(local, fill=255)
        mask = ImageChops.multiply(mask, tile.getchannel('A'))
        lit = tile.convert('RGB')
        mul = tuple(min(255, int(shade * c * 255)) for c in cm)
        if mul != (255, 255, 255):
            lit = ImageChops.multiply(lit, Image.new('RGB', (w, h), mul))
        canvas.paste(lit, (x0c, y0c), mask)
        return True
