import argparse
import os
import shutil
import subprocess
import sys
from . import audio, container, project, preview


def cmd_build(a):
    p = project.load_project(a.project)
    blob, out = project.build_file(p, a.output, progress=lambda s: print(s))
    for tag, pos, size in project.size_report(blob):
        print(f'  {tag}  @0x{pos:06X}  {size:>8} bytes')
    print(f'{out}: {len(blob)} bytes')


def cmd_info(a):
    with open(a.file, 'rb') as f:
        blob = f.read()
    b = container.read(blob)
    print(f'{a.file}: {len(blob)} bytes, sealed by {container.CREDIT[:9].decode()}')
    print(f'  title  : {b.title}')
    print(f'  author : {b.author}')
    for tag, pos, size in project.size_report(blob):
        print(f'  {tag}  @0x{pos:06X}  {size:>8} bytes')
    for i, t in enumerate(b.textures):
        print(f'  texture {i}: {t.width}x{t.height}')
    for i, m in enumerate(b.models):
        print(f'  model {i}: {m.name!r} {len(m.positions)} verts {len(m.faces)} tris texture={m.texture}')
    for i, n in enumerate(b.nodes):
        print(f'  node {i}: mesh={n.mesh} anchor={n.parent} flags=0x{n.flags():02X}')
    if b.sky:
        print(f'  sky    : texture {b.sky.texture} drift {b.sky.scroll_u:g},{b.sky.scroll_v:g} strength {b.sky.strength}')
    if b.bloom:
        print(f'  bloom  : halos, intensity {b.bloom.intensity:g} size {b.bloom.radius:g}')
    print(f'  sky    : ' + ('opaque gradient' if b.view.opaque else 'transparent (wallpaper shows through)'))
    j = b.jingle
    print(f'  jingle : {audio.CODEC_NAMES.get(j.codec)} {j.rate} Hz x{len(j.channels)} {j.seconds():.2f}s')


def cmd_jingle(a):
    with open(a.file, 'rb') as f:
        b = container.read(f.read())
    audio.write_wav(a.output, b.jingle.rate, b.jingle.pcm())
    print(a.output)


def cmd_render(a):
    with open(a.file, 'rb') as f:
        b = container.read(f.read())
    preview.Renderer(b).render(a.time).save(a.output)
    print(a.output)


def cmd_video(a):
    with open(a.file, 'rb') as f:
        b = container.read(f.read())
    r = preview.Renderer(b)
    frames = int(a.seconds * a.fps)
    size = (400, 480) if a.both else (400, 240)
    ff = shutil.which('ffmpeg')
    if a.output.lower().endswith(('.mp4', '.mkv', '.mov', '.webm')) and ff:
        wav = None
        extra = []
        if not a.mute and b.jingle is not None:
            import tempfile
            fd, wav = tempfile.mkstemp(suffix='.wav')
            os.close(fd)
            audio.write_wav(wav, b.jingle.rate, b.jingle.pcm())
            extra = ['-i', wav, '-c:a', 'aac', '-b:a', '128k', '-ar', '48000']
        cmd = [ff, '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{size[0]}x{size[1]}', '-r', str(a.fps), '-i', '-'] + extra[:2] + [
               '-vf', f'scale={size[0] * a.zoom}:{size[1] * a.zoom}:flags=neighbor', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '16', '-pix_fmt', 'yuv420p'] + extra[2:] + ['-movflags', '+faststart', a.output]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        try:
            for i in range(frames):
                proc.stdin.write(r.render(i / a.fps, show_bottom=a.both).tobytes())
            proc.stdin.close()
            proc.wait()
        finally:
            if wav:
                os.remove(wav)
        print(a.output)
        return
    os.makedirs(a.output, exist_ok=True)
    for i in range(frames):
        r.render(i / a.fps, show_bottom=a.both).save(os.path.join(a.output, f'frame_{i:05d}.png'))
    print(f'{frames} frames at {a.fps} fps in {a.output}' + ('' if ff else ' (install ffmpeg to write .mp4 directly)'))


def cmd_chime(a):
    rate, chans = audio.synth_chime()
    audio.write_wav(a.output, rate, [[v * 32767 for v in c] for c in chans])
    print(a.output)


def cmd_new(a):
    project.save_project(project.default_project(), a.output)
    print(a.output)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or (len(argv) == 1 and argv[0].lower().endswith(('.json', '.bnr')) and os.path.exists(argv[0])):
        from .gui import run
        return run(argv[0] if argv else None)
    ap = argparse.ArgumentParser(prog='bnr_studio', description='BNR banner builder (Cracko298 BBM based)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('gui', help='open the GUI')
    s.add_argument('project', nargs='?')
    s = sub.add_parser('build', help='build a project .json into a .bnr')
    s.add_argument('project')
    s.add_argument('-o', '--output')
    s = sub.add_parser('info', help='show what is inside a .bnr')
    s.add_argument('file')
    s = sub.add_parser('jingle', help='decode the jingle of a .bnr to a .wav')
    s.add_argument('file')
    s.add_argument('output')
    s = sub.add_parser('render', help='render a .bnr frame to a .png')
    s.add_argument('file')
    s.add_argument('output')
    s.add_argument('-t', '--time', type=float, default=0.0)
    s = sub.add_parser('video', help='render a .bnr to an .mp4 (needs ffmpeg) or a folder of PNG frames')
    s.add_argument('file')
    s.add_argument('output')
    s.add_argument('--seconds', type=float, default=10.0)
    s.add_argument('--fps', type=int, default=60)
    s.add_argument('--zoom', type=int, default=2)
    s.add_argument('--both', action='store_true', help='include the bottom screen')
    s.add_argument('--mute', action='store_true', help='leave the jingle out of the video')
    s = sub.add_parser('chime', help='write the built-in sample jingle as a .wav')
    s.add_argument('output')
    s = sub.add_parser('new', help='write an empty project .json')
    s.add_argument('output')
    a = ap.parse_args(argv)
    if a.cmd == 'gui':
        from .gui import run
        return run(a.project)
    try:
        {'build': cmd_build, 'info': cmd_info, 'video': cmd_video, 'jingle': cmd_jingle, 'render': cmd_render, 'chime': cmd_chime, 'new': cmd_new}[a.cmd](a)
    except (container.BannerError, ValueError, OSError) as e:
        print(f'error: {e}', file=sys.stderr)
        return 1
    return 0
