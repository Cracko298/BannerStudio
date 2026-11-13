import base64
import copy
import io
import os
import queue
import threading
import time
import traceback
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, filedialog, messagebox, colorchooser
from . import audio, container, project, preview, texture
from .meshio import PRIMITIVES

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.normpath(os.path.join(HERE, '..', 'examples'))
MODEL_TYPES = [('3D models', '*.glb *.gltf *.obj *.ply *.stl'), ('All files', '*.*')]
IMAGE_TYPES = [('Images', '*.png *.jpg *.jpeg *.bmp *.gif *.tga *.webp'), ('All files', '*.*')]
WAV_TYPES = [('WAV audio', '*.wav *.wave'), ('All files', '*.*')]
ICON_TYPES = [('Aurora icon or image', '*.icon *.png *.jpg *.jpeg *.bmp *.gif *.webp'), ('All files', '*.*')]
PROJECT_TYPES = [('BNR project', '*.json'), ('All files', '*.*')]
BNR_TYPES = [('BNR banner', '*.bnr'), ('All files', '*.*')]
TEX_FORMATS = ['auto', 'i4', 'i8', 'rgb565', 'rgba4444', 'rgba5551']
NONE_PARENT = '(none)'

NODE_FORM = [
    ('Model', [
        ('name', 'Name', 'text'),
        ('source', 'Model file / shape', 'source'),
        ('texture', 'Texture (optional)', 'texture'),
        ('tint', 'Tint colour', 'tint'),
        ('size', 'Fit to size (0 = keep)', 'float'),
        ('center', 'Center the model', 'bool'),
        ('detail', 'Shape detail', 'float'),
        ('tube', 'Ring thickness (torus)', 'float'),
        ('texture_size', 'Texture size (0 = Look tab)', 'int'),
        ('texture_format', 'Texture format (blank = Look tab)', 'format'),
    ]),
    ('Glow & shine', [
        ('glow', 'Glow for bloom (0-255)', 'int'),
        ('unlit', 'Self-lit (ignore shading)', 'bool'),
        ('additive', 'Additive (black = see-through)', 'bool'),
        ('billboard', 'Always face the camera', 'bool'),
        ('pulse.hz', 'Twinkle speed (Hz)', 'float'),
        ('pulse.depth', 'Twinkle depth (0-1)', 'float'),
        ('pulse.phase', 'Twinkle start °', 'float'),
    ]),
    ('Anchor', [
        ('parent', 'Anchored to', 'parent'),
        ('follow_rotation', 'Turn with the anchor', 'bool'),
    ]),
    ('Placement', [
        ('offset', 'Offset  X Y Z', 'vec3'),
        ('rotation', 'Rotation °  X Y Z', 'vec3'),
        ('scale', 'Scale  X Y Z', 'vec3'),
    ]),
    ('Motion', [
        ('spin', 'Spin °/s  X Y Z', 'vec3'),
        ('orbit.radius_a', 'Orbit radius A', 'float'),
        ('orbit.radius_b', 'Orbit radius B', 'float'),
        ('orbit.speed', 'Orbit speed °/s', 'float'),
        ('orbit.phase', 'Orbit start °', 'float'),
        ('orbit.tilt_x', 'Orbit tilt X °', 'float'),
        ('orbit.tilt_z', 'Orbit tilt Z °', 'float'),
        ('bob.amplitude', 'Bob height', 'float'),
        ('bob.hz', 'Bob speed (Hz)', 'float'),
        ('bob.phase', 'Bob start °', 'float'),
    ]),
]


def get_path(d, path):
    for k in path.split('.'):
        d = d[k]
    return d


def set_path(d, path, v):
    keys = path.split('.')
    for k in keys[:-1]:
        d = d[k]
    d[keys[-1]] = v


def fnum(s, default=None):
    try:
        return float(str(s).strip())
    except ValueError:
        return default


def hexcol(c):
    return '#%02x%02x%02x' % tuple(int(v) for v in c[:3])


def fmt_num(v):
    v = float(v)
    return str(int(v)) if v == int(v) else f'{v:g}'


class ScrollFrame(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.bar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self.window = self.canvas.create_window((0, 0), window=self.inner, anchor='nw')
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.pack(side='left', fill='both', expand=True)
        self.bar.pack(side='right', fill='y')
        self.inner.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(self.window, width=e.width))
        self.inner.bind('<Enter>', lambda e: self.bind_wheel(True))
        self.inner.bind('<Leave>', lambda e: self.bind_wheel(False))

    def bind_wheel(self, on):
        if on:
            self.canvas.bind_all('<MouseWheel>', self.wheel)
            self.canvas.bind_all('<Button-4>', lambda e: self.canvas.yview_scroll(-2, 'units'))
            self.canvas.bind_all('<Button-5>', lambda e: self.canvas.yview_scroll(2, 'units'))
        else:
            self.canvas.unbind_all('<MouseWheel>')
            self.canvas.unbind_all('<Button-4>')
            self.canvas.unbind_all('<Button-5>')

    def wheel(self, e):
        d = e.delta
        step = -1 if d > 0 else 1
        if abs(d) >= 120:
            step *= max(1, abs(d) // 120)
        self.canvas.yview_scroll(int(step), 'units')


class App:
    def __init__(self, root, path=None):
        self.root = root
        self.cache = project.Cache()
        self.project = project.default_project()
        self.project_path = None
        self.selected = None
        self.loading = False
        self.preview_due = time.time()
        self.preview_busy = False
        self.renderer = None
        self.inspecting = None
        self.t0 = time.time()
        self.paused_at = None
        self.photo = None
        self.jobs = queue.Queue()
        self.node_vars = {}
        self.color_swatches = {}
        self.frame_times = []
        self.ppm_ok = True
        root.title('BNR Studio · BBM format by Cracko298')
        root.minsize(980, 640)
        self.style()
        self.build_toolbar()
        body = ttk.PanedWindow(root, orient='horizontal')
        body.pack(fill='both', expand=True, padx=6, pady=(0, 6))
        self.tabs = ttk.Notebook(body)
        right = ttk.Frame(body)
        body.add(self.tabs, weight=3)
        body.add(right, weight=2)
        self.build_models_tab()
        self.build_look_tab()
        self.build_jingle_tab()
        self.build_extras_tab()
        self.build_build_tab()
        self.build_preview(right)
        self.status = tk.StringVar(value='Step 1: add a model (.glb with a texture works best), or load an example from the toolbar.')
        ttk.Label(root, textvariable=self.status, anchor='w', style='Status.TLabel').pack(fill='x', side='bottom')
        self.load_into_ui()
        if path:
            if path.lower().endswith('.bnr'):
                self.inspect_bnr(path)
            else:
                self.open_project(path)
        root.after(80, self.tick)
        root.protocol('WM_DELETE_WINDOW', root.destroy)

    def style(self):
        s = ttk.Style(self.root)
        if 'clam' in s.theme_names() and s.theme_use() in ('default', 'classic'):
            s.theme_use('clam')
        s.configure('Hint.TLabel', foreground='#666666')
        s.configure('Status.TLabel', padding=(8, 3))
        bold = tkfont.nametofont('TkDefaultFont').copy()
        bold.configure(weight='bold')
        self.bold = bold
        s.configure('Head.TLabel', font=bold)
        s.configure('Big.TButton', padding=(14, 8))

    def build_toolbar(self):
        bar = ttk.Frame(self.root, padding=(6, 6))
        bar.pack(fill='x')
        for text, cmd in (('New', self.new_project), ('Open project…', self.ask_open_project), ('Save', self.save), ('Save as…', self.save_as)):
            ttk.Button(bar, text=text, command=cmd).pack(side='left', padx=2)
        ttk.Separator(bar, orient='vertical').pack(side='left', fill='y', padx=6)
        mb = ttk.Menubutton(bar, text='Examples ▾')
        menu = tk.Menu(mb, tearoff=False)
        for name, label in (('novalauncher/novalauncher', 'NovaLauncher showcase'), ('atom', 'Atom (anchors + orbits)'), ('starship', 'Ship around a star (.glb + texture)')):
            menu.add_command(label=label, command=lambda n=name: self.open_project(os.path.join(EXAMPLES, *n.split('/')) + '.json', example=True))
        mb['menu'] = menu
        mb.pack(side='left', padx=2)
        ttk.Button(bar, text='Inspect .bnr…', command=self.ask_inspect).pack(side='left', padx=2)
        ttk.Label(bar, text='BBM by Cracko298', style='Hint.TLabel').pack(side='right')

    def hint(self, parent, text):
        ttk.Label(parent, text=text, style='Hint.TLabel', wraplength=520, justify='left').pack(fill='x', anchor='w', pady=(0, 6))

    def build_models_tab(self):
        tab = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(tab, text='1 · Models')
        self.hint(tab, 'Add one or more models. Each model gets one texture. Anchor a model to another to make it orbit or turn with it (an atom: electrons anchored to the nucleus).')
        top = ttk.Frame(tab)
        top.pack(fill='x')
        self.tree = ttk.Treeview(top, columns=('source', 'anchor', 'motion'), height=7, selectmode='browse')
        self.tree.heading('#0', text='Name')
        self.tree.heading('source', text='Source')
        self.tree.heading('anchor', text='Anchored to')
        self.tree.heading('motion', text='Motion')
        self.tree.column('#0', width=140)
        self.tree.column('source', width=150)
        self.tree.column('anchor', width=100)
        self.tree.column('motion', width=110)
        self.tree.pack(side='left', fill='x', expand=True)
        sb = ttk.Scrollbar(top, orient='vertical', command=self.tree.yview)
        sb.pack(side='left', fill='y')
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind('<<TreeviewSelect>>', self.on_select)
        btns = ttk.Frame(tab)
        btns.pack(fill='x', pady=6)
        ttk.Button(btns, text='Add model file…', command=self.add_model_file).pack(side='left', padx=2)
        shape = ttk.Menubutton(btns, text='Add shape ▾')
        sm = tk.Menu(shape, tearoff=False)
        for kind in PRIMITIVES:
            sm.add_command(label=kind.title(), command=lambda k=kind: self.add_node(project.PRIM + k, k.title()))
        shape['menu'] = sm
        shape.pack(side='left', padx=2)
        ttk.Button(btns, text='Add image (3D)…', command=self.add_image_node).pack(side='left', padx=2)
        ttk.Button(btns, text='Add anchor', command=lambda: self.add_node(project.ANCHOR, 'Anchor')).pack(side='left', padx=2)
        ttk.Button(btns, text='Duplicate', command=self.duplicate_node).pack(side='left', padx=2)
        ttk.Button(btns, text='Remove', command=self.remove_node).pack(side='left', padx=2)
        ttk.Button(btns, text='▲', width=3, command=lambda: self.move_node(-1)).pack(side='left', padx=2)
        ttk.Button(btns, text='▼', width=3, command=lambda: self.move_node(1)).pack(side='left', padx=2)
        self.form_holder = ScrollFrame(tab)
        self.form_holder.pack(fill='both', expand=True)
        self.build_node_form(self.form_holder.inner)

    def build_node_form(self, parent):
        self.form = parent
        self.parent_boxes = []
        for title, fields in NODE_FORM:
            box = ttk.LabelFrame(parent, text=title, padding=6)
            box.pack(fill='x', pady=4, padx=2)
            box.columnconfigure(1, weight=1)
            for row, (key, label, kind) in enumerate(fields):
                ttk.Label(box, text=label).grid(row=row, column=0, sticky='w', padx=(0, 8), pady=2)
                cell = ttk.Frame(box)
                cell.grid(row=row, column=1, sticky='ew', pady=2)
                self.make_field(cell, key, kind)
            if title == 'Motion':
                pre = ttk.Frame(box)
                pre.grid(row=len(fields), column=0, columnspan=2, sticky='w', pady=(6, 0))
                ttk.Label(pre, text='Quick:').pack(side='left')
                for text, preset in (('Keep still', 'still'), ('Spin', 'spin'), ('Orbit anchor', 'orbit'), ('Orbit + spin', 'both'), ('Bob', 'bob')):
                    ttk.Button(pre, text=text, command=lambda p=preset: self.motion_preset(p)).pack(side='left', padx=2)
        self.set_form_state(False)

    def make_field(self, cell, key, kind):
        if kind == 'vec3':
            vs = [tk.StringVar() for _ in range(3)]
            for v in vs:
                ttk.Entry(cell, textvariable=v, width=8).pack(side='left', padx=(0, 4))
                v.trace_add('write', lambda *a, k=key: self.node_changed(k))
            self.node_vars[key] = ('vec3', vs)
        elif kind == 'bool':
            v = tk.BooleanVar()
            ttk.Checkbutton(cell, variable=v, command=lambda k=key: self.node_changed(k)).pack(side='left')
            self.node_vars[key] = ('bool', v)
        elif kind == 'parent':
            v = tk.StringVar()
            cb = ttk.Combobox(cell, textvariable=v, state='readonly', width=24)
            cb.pack(side='left')
            cb.bind('<<ComboboxSelected>>', lambda e, k=key: self.node_changed(k))
            self.parent_boxes.append(cb)
            self.node_vars[key] = ('parent', v)
        elif kind == 'format':
            v = tk.StringVar()
            cb = ttk.Combobox(cell, textvariable=v, values=[''] + TEX_FORMATS, state='readonly', width=12)
            cb.pack(side='left')
            cb.bind('<<ComboboxSelected>>', lambda e, k=key: self.node_changed(k))
            self.node_vars[key] = ('format', v)
        elif kind == 'tint':
            v = tk.StringVar()
            sw = tk.Label(cell, width=4, relief='groove', bg='#dcdcdc')
            sw.pack(side='left', padx=(0, 6))
            ttk.Button(cell, text='Pick…', command=self.pick_tint).pack(side='left')
            ttk.Button(cell, text='Clear', command=self.clear_tint).pack(side='left', padx=4)
            ttk.Label(cell, textvariable=v, style='Hint.TLabel').pack(side='left', padx=4)
            self.tint_swatch = sw
            self.node_vars[key] = ('tint', v)
        elif kind in ('source', 'texture'):
            v = tk.StringVar()
            ttk.Entry(cell, textvariable=v).pack(side='left', fill='x', expand=True)
            ttk.Button(cell, text='Browse…', command=lambda k=kind: self.browse_node_file(k)).pack(side='left', padx=(4, 0))
            if kind == 'texture':
                ttk.Button(cell, text='Clear', command=lambda: self.node_vars['texture'][1].set('')).pack(side='left', padx=(4, 0))
            v.trace_add('write', lambda *a, k=key: self.node_changed(k))
            self.node_vars[key] = ('text', v)
        else:
            v = tk.StringVar()
            ttk.Entry(cell, textvariable=v, width=30 if kind == 'text' else 10).pack(side='left', fill='x' if kind == 'text' else None, expand=kind == 'text')
            v.trace_add('write', lambda *a, k=key: self.node_changed(k))
            self.node_vars[key] = (kind, v)

    def set_form_state(self, enabled):
        def walk(w):
            for c in w.winfo_children():
                try:
                    if isinstance(c, ttk.Combobox):
                        c.state(['!disabled', 'readonly'] if enabled else ['disabled'])
                    elif isinstance(c, (ttk.Entry, ttk.Button, ttk.Checkbutton)):
                        c.state(['!disabled'] if enabled else ['disabled'])
                except tk.TclError:
                    pass
                walk(c)
        walk(self.form)

    def build_look_tab(self):
        tab = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(tab, text='2 · Look')
        self.hint(tab, 'Textures are stored uncompressed but palettised (I4 = 16 colours, I8 = 256) at power-of-two sizes, so they stay tiny and map straight onto 3DS GPU formats.')
        self.look_vars = {}
        g = ttk.LabelFrame(tab, text='Textures', padding=6)
        g.pack(fill='x', pady=4)
        self.combo_row(g, 0, 'Max texture size', ('texture', 'max_size'), ['16', '32', '64', '128', '256', '512'])
        self.combo_row(g, 1, 'Texture format', ('texture', 'format'), TEX_FORMATS)
        self.check_row(g, 2, 'Dither when reducing colours', ('texture', 'dither'))
        m = ttk.LabelFrame(tab, text='Geometry (BBM quantized record)', padding=6)
        m.pack(fill='x', pady=4)
        self.combo_row(m, 0, 'Position bits per axis', ('mesh', 'position_bits'), ['8', '10', '11', '12', '13', '14', '16', '0'])
        ttk.Label(m, text='12 is a good default. 0 stores lossless half-floats instead.', style='Hint.TLabel').grid(row=1, column=0, columnspan=2, sticky='w')
        c = ttk.LabelFrame(tab, text='Camera', padding=6)
        c.pack(fill='x', pady=4)
        self.check_row(c, 0, 'Frame the scene automatically', ('view', 'auto'))
        self.entry_row(c, 1, 'Look-down angle °', ('view', 'pitch'))
        self.entry_row(c, 2, 'Turn angle °', ('view', 'yaw'))
        self.entry_row(c, 3, 'Field of view °', ('view', 'fov'))
        self.vec_row(c, 4, 'Manual eye  X Y Z', ('view', 'eye'))
        self.vec_row(c, 5, 'Manual target  X Y Z', ('view', 'target'))
        self.check_row(c, 8, 'Opaque sky colours (off = Aurora\'s wallpaper shows through)', ('view', 'opaque'))
        self.color_row(c, 6, 'Sky colour (top)', ('view', 'background_top'))
        self.color_row(c, 7, 'Sky colour (bottom)', ('view', 'background'))
        k = ttk.LabelFrame(tab, text='Sky texture (added over the colours, e.g. a nebula)', padding=6)
        k.pack(fill='x', pady=4)
        k.columnconfigure(1, weight=1)
        self.file_row(k, 0, 'Image', ('sky', 'path'), IMAGE_TYPES)
        self.entry_row(k, 1, 'Drift X (screens/s)', ('sky', 'scroll_u'))
        self.entry_row(k, 2, 'Drift Y (screens/s)', ('sky', 'scroll_v'))
        self.entry_row(k, 3, 'Strength (0-255)', ('sky', 'strength'))
        bl = ttk.LabelFrame(tab, text='Bloom', padding=6)
        bl.pack(fill='x', pady=4)
        self.check_row(bl, 0, 'Enable bloom (glow halos)', ('bloom', 'enabled'))
        self.entry_row(bl, 1, 'Intensity', ('bloom', 'intensity'))
        self.entry_row(bl, 2, 'Halo size', ('bloom', 'radius'))
        ttk.Label(bl, text='Every object with Glow (tab 1) gets a soft additive halo, the way Aurora draws bloom on the 3DS GPU.', style='Hint.TLabel').grid(row=3, column=0, columnspan=2, sticky='w')

    def color_row(self, parent, row, label, path):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky='w', pady=2)
        f = ttk.Frame(parent)
        f.grid(row=row, column=1, sticky='w')
        sw = tk.Label(f, width=4, relief='groove')
        sw.pack(side='left', padx=(0, 6))
        ttk.Button(f, text='Pick…', command=lambda: self.pick_color(path, sw)).pack(side='left')
        self.color_swatches[path] = sw

    def pick_color(self, path, sw):
        cur = self.project[path[0]][path[1]]
        res = colorchooser.askcolor(color=hexcol(cur), parent=self.root)
        if res and res[0]:
            self.project[path[0]][path[1]] = [int(c) for c in res[0]]
            sw.configure(bg=hexcol(self.project[path[0]][path[1]]))
            self.invalidate()

    def combo_row(self, parent, row, label, path, values, width=12):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky='w', padx=(0, 8), pady=2)
        v = tk.StringVar()
        cb = ttk.Combobox(parent, textvariable=v, values=values, width=width, state='readonly')
        cb.grid(row=row, column=1, sticky='w', pady=2)
        cb.bind('<<ComboboxSelected>>', lambda e: self.setting_changed())
        self.look_vars[path] = ('combo', v)
        return cb

    def check_row(self, parent, row, label, path):
        v = tk.BooleanVar()
        ttk.Checkbutton(parent, text=label, variable=v, command=self.setting_changed).grid(row=row, column=0, columnspan=2, sticky='w', pady=2)
        self.look_vars[path] = ('bool', v)

    def entry_row(self, parent, row, label, path, width=10):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky='w', padx=(0, 8), pady=2)
        v = tk.StringVar()
        ttk.Entry(parent, textvariable=v, width=width).grid(row=row, column=1, sticky='w', pady=2)
        v.trace_add('write', lambda *a: self.setting_changed())
        self.look_vars[path] = ('entry', v)

    def text_row(self, parent, row, label, path):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky='w', padx=(0, 8), pady=2)
        v = tk.StringVar()
        ttk.Entry(parent, textvariable=v, width=36).grid(row=row, column=1, sticky='ew', pady=2)
        v.trace_add('write', lambda *a: self.setting_changed())
        self.look_vars[path] = ('text', v)

    def vec_row(self, parent, row, label, path):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky='w', padx=(0, 8), pady=2)
        f = ttk.Frame(parent)
        f.grid(row=row, column=1, sticky='w', pady=2)
        vs = [tk.StringVar() for _ in range(3)]
        for v in vs:
            ttk.Entry(f, textvariable=v, width=8).pack(side='left', padx=(0, 4))
            v.trace_add('write', lambda *a: self.setting_changed())
        self.look_vars[path] = ('vec', vs)

    def file_row(self, parent, row, label, path, types, clear=True):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky='w', padx=(0, 8), pady=2)
        f = ttk.Frame(parent)
        f.grid(row=row, column=1, sticky='ew', pady=2)
        v = tk.StringVar()
        ttk.Entry(f, textvariable=v).pack(side='left', fill='x', expand=True)

        def browse():
            fn = filedialog.askopenfilename(parent=self.root, filetypes=types)
            if fn:
                v.set(fn)
        ttk.Button(f, text='Browse…', command=browse).pack(side='left', padx=(4, 0))
        if clear:
            ttk.Button(f, text='Clear', command=lambda: v.set('')).pack(side='left', padx=(4, 0))
        v.trace_add('write', lambda *a: self.setting_changed())
        self.look_vars[path] = ('text', v)

    def build_jingle_tab(self):
        tab = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(tab, text='3 · Jingle')
        self.hint(tab, 'Every banner carries a jingle. Use the built-in chime or any WAV (8/16/24/32-bit, float, A-law/µ-law, mono or stereo, any sample rate). It is resampled and encoded to ADPCM.')
        g = ttk.LabelFrame(tab, text='Sound', padding=6)
        g.pack(fill='x', pady=4)
        g.columnconfigure(1, weight=1)
        self.jingle_mode = tk.StringVar(value='builtin')
        ttk.Radiobutton(g, text='Built-in chime (soft bell arpeggio)', variable=self.jingle_mode, value='builtin', command=self.jingle_mode_changed).grid(row=0, column=0, columnspan=2, sticky='w')
        ttk.Radiobutton(g, text='My WAV file:', variable=self.jingle_mode, value='file', command=self.jingle_mode_changed).grid(row=1, column=0, sticky='w')
        f = ttk.Frame(g)
        f.grid(row=1, column=1, sticky='ew')
        self.jingle_path = tk.StringVar()
        ttk.Entry(f, textvariable=self.jingle_path).pack(side='left', fill='x', expand=True)
        ttk.Button(f, text='Browse…', command=self.browse_wav).pack(side='left', padx=(4, 0))
        self.jingle_path.trace_add('write', lambda *a: self.jingle_mode_changed())
        e = ttk.LabelFrame(tab, text='Encoding', padding=6)
        e.pack(fill='x', pady=4)
        self.combo_row(e, 0, 'Codec', ('jingle', 'codec'), ['dsp', 'ima'])
        ttk.Label(e, text='dsp = GameCube/3DS DSP-ADPCM (NDSP plays it natively), ima = IMA-ADPCM', style='Hint.TLabel').grid(row=1, column=0, columnspan=2, sticky='w')
        self.combo_row(e, 2, 'Sample rate (Hz)', ('jingle', 'rate'), [str(r) for r in audio.RATES])
        self.combo_row(e, 3, 'Channels', ('jingle', 'channels'), ['1', '2'])
        self.entry_row(e, 4, 'Max length (seconds)', ('jingle', 'max_seconds'))
        self.check_row(e, 5, 'Normalise loudness', ('jingle', 'normalise'))
        self.check_row(e, 6, 'Trim silence at start/end', ('jingle', 'trim'))
        self.check_row(e, 7, 'Loop the jingle', ('jingle', 'loop'))
        self.jingle_est = tk.StringVar()
        ttk.Label(tab, textvariable=self.jingle_est, style='Hint.TLabel').pack(anchor='w', pady=4)
        b = ttk.Frame(tab)
        b.pack(fill='x', pady=4)
        ttk.Button(b, text='Save built-in chime as WAV…', command=self.save_chime).pack(side='left', padx=2)
        ttk.Button(b, text='Encode & save preview WAV…', command=self.save_jingle_preview).pack(side='left', padx=2)

    def build_extras_tab(self):
        tab = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(tab, text='4 · Icon & Image')
        i = ttk.LabelFrame(tab, text='Home-menu icon (Aurora)', padding=6)
        i.pack(fill='x', pady=4)
        i.columnconfigure(1, weight=1)
        self.hint(i, 'The icon on the bottom screen in Aurora. Any image is cropped square and stored as Aurora\'s own 32×32 colour icon. A .icon text bitmap is embedded byte for byte.')
        inner = ttk.Frame(i)
        inner.pack(fill='x')
        inner.columnconfigure(1, weight=1)
        self.file_row(inner, 0, 'Icon file', ('icon', 'path'), ICON_TYPES)
        self.check_row(inner, 1, 'Galaxy shimmer on the icon (Aurora)', ('icon', 'animation'))
        o = ttk.LabelFrame(tab, text='Still image (never moves)', padding=6)
        o.pack(fill='x', pady=4)
        self.hint(o, 'Flat on top of the 3D scene. Stored at up to 256×144, then drawn at your position and scale. To put a picture inside the 3D scene, use Add image (3D)… in tab 1.')
        inner = ttk.Frame(o)
        inner.pack(fill='x')
        inner.columnconfigure(1, weight=1)
        self.file_row(inner, 0, 'Image file', ('image', 'path'), IMAGE_TYPES)
        self.combo_row(inner, 1, 'Screen', ('image', 'screen'), ['top', 'bottom'])
        self.entry_row(inner, 2, 'X (pixels from left)', ('image', 'x'))
        self.entry_row(inner, 3, 'Y (pixels from top)', ('image', 'y'))
        self.entry_row(inner, 4, 'Scale (1 = 100%)', ('image', 'scale'))
        self.combo_row(inner, 5, 'Format', ('image', 'format'), TEX_FORMATS)

    def build_build_tab(self):
        tab = ttk.Frame(self.tabs, padding=8)
        self.tabs.add(tab, text='5 · Build')
        g = ttk.LabelFrame(tab, text='Banner info', padding=6)
        g.pack(fill='x', pady=4)
        g.columnconfigure(1, weight=1)
        self.text_row(g, 0, 'Title', ('title',))
        self.text_row(g, 1, 'Author', ('author',))
        o = ttk.LabelFrame(tab, text='Output', padding=6)
        o.pack(fill='x', pady=4)
        o.columnconfigure(1, weight=1)
        ttk.Label(o, text='Save as').grid(row=0, column=0, sticky='w', padx=(0, 8))
        f = ttk.Frame(o)
        f.grid(row=0, column=1, sticky='ew')
        self.out_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.out_var).pack(side='left', fill='x', expand=True)
        ttk.Button(f, text='Browse…', command=self.browse_output).pack(side='left', padx=(4, 0))
        self.out_var.trace_add('write', lambda *a: self.setting_changed())
        self.build_btn = ttk.Button(tab, text='Build .bnr', style='Big.TButton', command=self.build)
        self.build_btn.pack(anchor='w', pady=8)
        self.log = tk.Text(tab, height=14, wrap='none', font='TkFixedFont')
        self.log.pack(fill='both', expand=True)
        self.log.configure(state='disabled')

    def build_preview(self, parent):
        ttk.Label(parent, text='Preview (3DS top + bottom screens)', style='Head.TLabel').pack(anchor='w', padx=6, pady=(6, 2))
        self.view_label = tk.Label(parent, bg='#0a0a0c', text='preview', fg='#555555')
        self.view_label.pack(padx=6, pady=4)
        c = ttk.Frame(parent)
        c.pack(fill='x', padx=6)
        self.play_btn = ttk.Button(c, text='Pause', command=self.toggle_play)
        self.play_btn.pack(side='left')
        ttk.Button(c, text='Restart', command=self.restart).pack(side='left', padx=4)
        self.textured = tk.BooleanVar(value=True)
        ttk.Checkbutton(c, text='Textured', variable=self.textured).pack(side='left', padx=8)
        self.bloom_on = tk.BooleanVar(value=True)
        ttk.Checkbutton(c, text='Bloom', variable=self.bloom_on).pack(side='left')
        self.time_var = tk.StringVar(value='t = 0.0 s')
        ttk.Label(c, textvariable=self.time_var, style='Hint.TLabel').pack(side='right')
        self.preview_note = tk.StringVar(value='')
        ttk.Label(parent, textvariable=self.preview_note, style='Hint.TLabel', wraplength=400, justify='left').pack(anchor='w', padx=6, pady=4)

    def load_into_ui(self):
        self.loading = True
        try:
            p = self.project
            for path, (kind, v) in self.look_vars.items():
                val = p
                for k in path:
                    val = val[k]
                if kind == 'vec':
                    for var, x in zip(v, val):
                        var.set(fmt_num(x))
                elif kind == 'bool':
                    v.set(bool(val))
                elif kind == 'entry':
                    v.set(fmt_num(val) if isinstance(val, (int, float)) and not isinstance(val, bool) else str(val))
                else:
                    v.set('' if val is None else str(val))
            src = p['jingle']['source']
            self.jingle_mode.set('builtin' if src in ('', 'builtin', None) else 'file')
            self.jingle_path.set('' if src in ('', 'builtin', None) else src)
            self.out_var.set(p.get('output') or '')
            for path, sw in self.color_swatches.items():
                sw.configure(bg=hexcol(p[path[0]][path[1]]))
        finally:
            self.loading = False
        self.refresh_tree()
        self.update_estimate()
        self.select(0 if p['nodes'] else None)
        self.invalidate()

    def setting_changed(self):
        if self.loading:
            return
        p = self.project
        for path, (kind, v) in self.look_vars.items():
            tgt = p
            for k in path[:-1]:
                tgt = tgt[k]
            key = path[-1]
            if kind == 'vec':
                vals = [fnum(x.get()) for x in v]
                if None not in vals:
                    tgt[key] = vals
            elif kind == 'bool':
                tgt[key] = bool(v.get())
            elif kind == 'entry':
                n = fnum(v.get())
                if n is not None:
                    tgt[key] = int(n) if key in ('x', 'y', 'strength') else n
            elif kind == 'combo':
                s = v.get()
                tgt[key] = int(s) if s.lstrip('-').isdigit() and key in ('max_size', 'position_bits', 'rate', 'channels') else s
            else:
                tgt[key] = v.get()
        p['output'] = self.out_var.get().strip()
        self.update_estimate()
        self.invalidate()

    def jingle_mode_changed(self):
        if self.loading:
            return
        if self.jingle_mode.get() == 'builtin':
            self.project['jingle']['source'] = 'builtin'
        else:
            self.project['jingle']['source'] = self.jingle_path.get().strip() or 'builtin'
        self.update_estimate()

    def update_estimate(self):
        j = self.project['jingle']
        try:
            secs = float(j['max_seconds'])
            if j['source'] in ('builtin', '', None):
                secs = min(secs, 2.4)
            est = audio.estimate_bytes(secs, int(j['rate']), int(j['channels']), audio.CODEC_KEYS.get(j['codec'], audio.DSP))
            self.jingle_est.set(f'≈ {est / 1024:.1f} KB for {secs:g} s (lower the sample rate or length to shrink the banner).')
        except (ValueError, KeyError, TypeError):
            self.jingle_est.set('')

    def refresh_tree(self):
        sel = self.selected
        self.tree.delete(*self.tree.get_children())
        for i, n in enumerate(self.project['nodes']):
            self.tree.insert('', 'end', iid=str(i), text=n['name'], values=self.row_values(n))
        names = [NONE_PARENT] + [n['name'] for n in self.project['nodes']]
        for cb in self.parent_boxes:
            cb.configure(values=names)
        if sel is not None and sel < len(self.project['nodes']):
            self.tree.selection_set(str(sel))

    def row_values(self, n):
        src = n['source']
        if src == project.ANCHOR:
            s = 'anchor'
        elif src.startswith(project.IMAGE):
            s = 'image ' + os.path.basename(src[len(project.IMAGE):])
        elif src.startswith(project.PRIM):
            s = src[len(project.PRIM):]
        else:
            s = os.path.basename(src)
        if n.get('texture'):
            s += ' + ' + os.path.basename(n['texture'])
        motion = []
        if any(n['spin']):
            motion.append('spin')
        if n['orbit']['radius_a'] or n['orbit']['radius_b']:
            motion.append('orbit')
        if n['bob']['amplitude']:
            motion.append('bob')
        if n.get('glow'):
            motion.append('glow')
        if (n.get('pulse') or {}).get('depth'):
            motion.append('twinkle')
        return (s, n['parent'] or '', ' + '.join(motion) or 'still')

    def on_select(self, e=None):
        sel = self.tree.selection()
        idx = int(sel[0]) if sel else None
        if idx != self.selected:
            self.select(idx, from_tree=True)

    def select(self, idx, from_tree=False):
        self.selected = idx
        self.loading = True
        try:
            if idx is None or idx >= len(self.project['nodes']):
                self.selected = None
                for key, (kind, v) in self.node_vars.items():
                    if kind == 'vec3':
                        for x in v:
                            x.set('')
                    elif kind == 'bool':
                        v.set(False)
                    else:
                        v.set('')
                self.tint_swatch.configure(bg='#dcdcdc')
                self.set_form_state(False)
                return
            n = self.project['nodes'][idx]
            names = [NONE_PARENT] + [m['name'] for i, m in enumerate(self.project['nodes']) if i != idx]
            for cb in self.parent_boxes:
                cb.configure(values=names)
            for key, (kind, v) in self.node_vars.items():
                val = get_path(n, key)
                if kind == 'vec3':
                    for x, c in zip(v, val):
                        x.set(fmt_num(c))
                elif kind == 'bool':
                    v.set(bool(val))
                elif kind == 'parent':
                    v.set(val or NONE_PARENT)
                elif kind == 'tint':
                    v.set(hexcol(val) if val else 'none')
                    self.tint_swatch.configure(bg=hexcol(val) if val else '#dcdcdc')
                elif kind in ('float', 'int'):
                    v.set(fmt_num(val or 0))
                else:
                    v.set(val or '')
            self.set_form_state(True)
        finally:
            self.loading = False
        if not from_tree:
            if idx is not None:
                self.tree.selection_set(str(idx))
                self.tree.see(str(idx))

    def node_changed(self, key):
        if self.loading or self.selected is None:
            return
        n = self.project['nodes'][self.selected]
        kind, v = self.node_vars[key]
        if kind == 'vec3':
            vals = [fnum(x.get()) for x in v]
            if None in vals:
                return
            set_path(n, key, vals)
        elif kind == 'bool':
            set_path(n, key, bool(v.get()))
        elif kind == 'parent':
            s = v.get()
            set_path(n, key, None if s == NONE_PARENT else s)
        elif kind in ('float', 'int'):
            x = fnum(v.get())
            if x is None:
                return
            set_path(n, key, int(x) if kind == 'int' else x)
        elif kind == 'format':
            set_path(n, key, v.get())
        elif key == 'name':
            new = v.get().strip()
            if not new or any(m['name'] == new for i, m in enumerate(self.project['nodes']) if i != self.selected):
                return
            old = n['name']
            for m in self.project['nodes']:
                if m['parent'] == old:
                    m['parent'] = new
            n['name'] = new
            self.refresh_tree()
        else:
            set_path(n, key, v.get().strip())
        if self.tree.exists(str(self.selected)):
            self.tree.item(str(self.selected), text=n['name'], values=self.row_values(n))
        self.invalidate()

    def unique_name(self, base):
        names = {n['name'] for n in self.project['nodes']}
        if base not in names:
            return base
        i = 2
        while f'{base} {i}' in names:
            i += 1
        return f'{base} {i}'

    def add_node(self, source, name, extra=None):
        n = project.default_node(self.unique_name(name), source)
        if extra:
            n.update(extra)
        if source == project.ANCHOR:
            n['size'] = 0
        self.project['nodes'].append(n)
        self.refresh_tree()
        self.select(len(self.project['nodes']) - 1)
        self.invalidate()
        self.status.set(f"Added {n['name']}. Set its anchor and motion below; the preview updates live.")

    def add_model_file(self):
        fns = filedialog.askopenfilenames(parent=self.root, filetypes=MODEL_TYPES)
        for fn in fns:
            self.add_node(fn, os.path.splitext(os.path.basename(fn))[0][:16] or 'Model')

    def add_image_node(self):
        fn = filedialog.askopenfilename(parent=self.root, filetypes=IMAGE_TYPES)
        if fn:
            self.add_node(project.IMAGE + fn, os.path.splitext(os.path.basename(fn))[0][:16] or 'Image', {'billboard': True, 'unlit': True, 'size': 2.0})

    def duplicate_node(self):
        if self.selected is None:
            return
        n = copy.deepcopy(self.project['nodes'][self.selected])
        n['name'] = self.unique_name(n['name'])
        self.project['nodes'].insert(self.selected + 1, n)
        self.refresh_tree()
        self.select(self.selected + 1)
        self.invalidate()

    def remove_node(self):
        if self.selected is None:
            return
        n = self.project['nodes'].pop(self.selected)
        for m in self.project['nodes']:
            if m['parent'] == n['name']:
                m['parent'] = n['parent']
        nxt = min(self.selected, len(self.project['nodes']) - 1)
        self.selected = None
        self.refresh_tree()
        self.select(nxt if nxt >= 0 else None)
        self.invalidate()

    def move_node(self, d):
        i = self.selected
        nodes = self.project['nodes']
        if i is None or not (0 <= i + d < len(nodes)):
            return
        nodes[i], nodes[i + d] = nodes[i + d], nodes[i]
        self.selected = i + d
        self.refresh_tree()
        self.select(i + d)

    def browse_node_file(self, kind):
        if kind == 'source':
            fn = filedialog.askopenfilename(parent=self.root, filetypes=MODEL_TYPES)
        else:
            fn = filedialog.askopenfilename(parent=self.root, filetypes=IMAGE_TYPES)
        if fn:
            self.node_vars[kind][1].set(fn)

    def pick_tint(self):
        if self.selected is None:
            return
        n = self.project['nodes'][self.selected]
        res = colorchooser.askcolor(color=hexcol(n['tint']) if n.get('tint') else '#dcdcdc', parent=self.root)
        if res and res[0]:
            n['tint'] = [int(c) for c in res[0]]
            self.select(self.selected)
            self.invalidate()

    def clear_tint(self):
        if self.selected is None:
            return
        self.project['nodes'][self.selected]['tint'] = None
        self.select(self.selected)
        self.invalidate()

    def motion_preset(self, kind):
        if self.selected is None:
            return
        n = self.project['nodes'][self.selected]
        if kind == 'still':
            n['spin'] = [0.0, 0.0, 0.0]
            n['orbit'].update(radius_a=0.0, radius_b=0.0, speed=0.0)
            n['bob']['amplitude'] = 0.0
        if kind in ('spin', 'both'):
            n['spin'] = [0.0, 60.0, 0.0]
        if kind in ('orbit', 'both'):
            n['orbit'].update(radius_a=n['orbit']['radius_a'] or 1.5, radius_b=n['orbit']['radius_b'] or 1.5, speed=n['orbit']['speed'] or 90.0)
            if not n['parent']:
                others = [m['name'] for i, m in enumerate(self.project['nodes']) if i != self.selected]
                if others:
                    n['parent'] = others[0]
        if kind == 'bob':
            n['bob'].update(amplitude=n['bob']['amplitude'] or 0.15, hz=n['bob']['hz'] or 0.5)
        self.select(self.selected)
        self.tree.item(str(self.selected), values=self.row_values(n))
        self.invalidate()

    def browse_wav(self):
        fn = filedialog.askopenfilename(parent=self.root, filetypes=WAV_TYPES)
        if fn:
            self.jingle_path.set(fn)
            self.jingle_mode.set('file')
            self.jingle_mode_changed()

    def browse_output(self):
        fn = filedialog.asksaveasfilename(parent=self.root, defaultextension='.bnr', filetypes=BNR_TYPES, initialfile=(self.project['title'] or 'banner') + '.bnr')
        if fn:
            self.out_var.set(fn)

    def save_chime(self):
        fn = filedialog.asksaveasfilename(parent=self.root, defaultextension='.wav', filetypes=WAV_TYPES, initialfile='bnr_chime.wav')
        if fn:
            rate, chans = audio.synth_chime()
            audio.write_wav(fn, rate, [[v * 32767 for v in c] for c in chans])
            self.status.set(f'Saved {fn}')

    def save_jingle_preview(self):
        fn = filedialog.asksaveasfilename(parent=self.root, defaultextension='.wav', filetypes=WAV_TYPES, initialfile='jingle_preview.wav')
        if not fn:
            return
        cfg = copy.deepcopy(self.project['jingle'])
        self.status.set('Encoding jingle…')

        def work():
            j = self.cache.jingle(cfg)
            audio.write_wav(fn, j.rate, j.pcm())
            return f'Saved {fn} - this is exactly what the banner will play ({j.seconds():.2f} s).'
        self.run_job(work, lambda msg: self.status.set(msg))

    def run_job(self, work, done):
        def runner():
            try:
                res = work()
                self.jobs.put((done, res, None))
            except Exception as e:
                self.jobs.put((done, None, e))
        threading.Thread(target=runner, daemon=True).start()

    def invalidate(self):
        self.inspecting = None
        self.preview_due = time.time() + 0.35

    def tick(self):
        self.tick_start = time.time()
        try:
            while True:
                done, res, err = self.jobs.get_nowait()
                if err is not None:
                    self.status.set(f'Error: {err}')
                    if done == self.build_done:
                        self.build_done(None, err)
                else:
                    done(res)
        except queue.Empty:
            pass
        if self.preview_due and time.time() >= self.preview_due and not self.preview_busy:
            self.preview_due = 0
            self.start_preview()
        self.draw_frame()
        self.root.after(max(1, int(1000 / 60 - (time.time() - self.tick_start) * 1000)), self.tick)

    def start_preview(self):
        self.preview_busy = True
        snap = copy.deepcopy(self.project)
        cache = self.cache

        def work():
            return preview.Renderer(project.preview_banner(snap, cache))

        def done(r):
            self.preview_busy = False
            if self.inspecting is None:
                self.renderer = r
                tris = sum(len(r.banner.models[n.mesh].faces) for n in r.banner.nodes if 0 <= n.mesh < len(r.banner.models))
                self.preview_note.set(f'{len(r.banner.nodes)} nodes · {len(r.banner.models)} unique meshes · {len(r.banner.textures)} textures · {tris} triangles on screen')

        def wrapped():
            try:
                return work()
            finally:
                self.preview_busy = False
        self.run_job(wrapped, done)

    def current_time(self):
        return (self.paused_at if self.paused_at is not None else time.time()) - self.t0

    def draw_frame(self):
        r = self.renderer
        if r is None:
            return
        t = self.current_time()
        key = (id(r), round(t, 4), self.textured.get(), self.bloom_on.get())
        if key == getattr(self, 'last_frame', None):
            return
        self.last_frame = key
        try:
            img = r.render(t, self.textured.get(), bloom=self.bloom_on.get())
        except Exception as e:
            self.preview_note.set(f'Preview error: {e}')
            return
        self.show_image(img)
        now = time.time()
        self.frame_times = [x for x in self.frame_times if now - x < 1.0] + [now]
        self.time_var.set(f't = {t:.1f} s · {len(self.frame_times)} fps')

    def show_image(self, img):
        data = None
        if self.ppm_ok:
            buf = io.BytesIO()
            img.save(buf, 'PPM')
            data = buf.getvalue()
        try:
            if data is None:
                raise tk.TclError('png')
            if self.photo is None:
                self.photo = tk.PhotoImage(data=data, format='PPM')
            else:
                self.photo.configure(data=data, format='PPM')
        except tk.TclError:
            self.ppm_ok = False
            buf = io.BytesIO()
            img.save(buf, 'PNG', compress_level=1)
            data = base64.b64encode(buf.getvalue()).decode('ascii')
            if self.photo is None:
                self.photo = tk.PhotoImage(data=data)
            else:
                self.photo.configure(data=data)
        self.view_label.configure(image=self.photo, text='')

    def toggle_play(self):
        if self.paused_at is None:
            self.paused_at = time.time()
            self.play_btn.configure(text='Play')
        else:
            self.t0 += time.time() - self.paused_at
            self.paused_at = None
            self.play_btn.configure(text='Pause')

    def restart(self):
        self.t0 = time.time()
        if self.paused_at is not None:
            self.paused_at = self.t0

    def write_log(self, text):
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.insert('end', text)
        self.log.configure(state='disabled')

    def build(self):
        out = self.out_var.get().strip()
        if not out:
            self.browse_output()
            out = self.out_var.get().strip()
            if not out:
                return
        if not self.project['nodes']:
            messagebox.showwarning('Nothing to build', 'Add at least one model first (tab 1).', parent=self.root)
            return
        self.build_btn.state(['disabled'])
        self.write_log('Building…\n')
        snap = copy.deepcopy(self.project)
        cache = self.cache
        self.run_job(lambda: project.build_file(snap, out, cache), self.build_done)

    def build_done(self, res, err=None):
        self.build_btn.state(['!disabled'])
        if err is not None or res is None:
            self.write_log('Build failed:\n' + ''.join(traceback.format_exception(type(err), err, err.__traceback__)) if err else 'Build failed.')
            return
        blob, path = res
        lines = [f'Built {path}', f'{len(blob)} bytes ({len(blob) / 1024:.1f} KB)', '']
        names = {'HEAD': 'header + Cracko298 seal', 'INFO': 'title / author', 'VIEW': 'camera', 'TEXR': 'texture', 'MODL': 'model (BBM record + UVs)', 'NODE': 'anchors + motion', 'IMAG': 'still image', 'ICON': 'home-menu icon', 'JNGL': 'jingle'}
        for tag, pos, size in project.size_report(blob):
            lines.append(f'{tag}  0x{pos:06X}  {size:>7} B  {names.get(tag, "")}')
        self.write_log('\n'.join(lines) + '\n')
        self.status.set(f'Built {os.path.basename(path)} - {len(blob)} bytes.')

    def ask_inspect(self):
        fn = filedialog.askopenfilename(parent=self.root, filetypes=BNR_TYPES)
        if fn:
            self.inspect_bnr(fn)

    def inspect_bnr(self, fn):
        try:
            with open(fn, 'rb') as f:
                blob = f.read()
            b = container.read(blob)
        except (container.BannerError, OSError, ValueError) as e:
            messagebox.showerror('Cannot open banner', str(e), parent=self.root)
            return
        self.renderer = preview.Renderer(b)
        self.inspecting = fn
        self.restart()
        j = b.jingle
        info = f'Inspecting {os.path.basename(fn)}: {len(blob)} bytes · "{b.title}" by {b.author or "?"} · jingle {audio.CODEC_NAMES.get(j.codec)} {j.rate} Hz {j.seconds():.2f}s · seal OK'
        self.preview_note.set(info + '  (edit anything to return to your project)')
        lines = [info, '']
        for tag, pos, size in project.size_report(blob):
            lines.append(f'{tag}  0x{pos:06X}  {size:>7} B')
        self.write_log('\n'.join(lines) + '\n')
        if messagebox.askyesno('Jingle', 'Save the decoded jingle of this banner as a WAV?', parent=self.root):
            out = filedialog.asksaveasfilename(parent=self.root, defaultextension='.wav', filetypes=WAV_TYPES, initialfile=os.path.splitext(os.path.basename(fn))[0] + '_jingle.wav')
            if out:
                audio.write_wav(out, j.rate, j.pcm())

    def new_project(self):
        self.project = project.default_project()
        self.project_path = None
        self.selected = None
        self.renderer = None
        self.load_into_ui()
        self.status.set('New project. Step 1: add a model.')

    def ask_open_project(self):
        fn = filedialog.askopenfilename(parent=self.root, filetypes=PROJECT_TYPES)
        if fn:
            self.open_project(fn)

    def open_project(self, fn, example=False):
        try:
            self.project = project.load_project(fn)
        except (OSError, ValueError) as e:
            messagebox.showerror('Cannot open project', str(e), parent=self.root)
            return
        self.project_path = None if example else fn
        if example:
            self.project['output'] = ''
        self.selected = None
        self.load_into_ui()
        self.restart()
        self.status.set(f'Opened {os.path.basename(fn)}' + (' (example - Save as… to keep your changes)' if example else ''))

    def save(self):
        if not self.project_path:
            return self.save_as()
        project.save_project(self.project, self.project_path)
        self.status.set(f'Saved {self.project_path}')

    def save_as(self):
        fn = filedialog.asksaveasfilename(parent=self.root, defaultextension='.json', filetypes=PROJECT_TYPES, initialfile=(self.project['title'] or 'banner') + '.json')
        if fn:
            self.project_path = fn
            self.save()


def run(path=None):
    root = tk.Tk()
    App(root, path)
    root.mainloop()
    return 0
