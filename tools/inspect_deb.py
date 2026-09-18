#!/usr/bin/env python3
"""列出 deb 的关键信息：ar 成员 / Architecture / data 路径前缀 / dylib 依赖

用法: inspect_deb.py <a.deb> [b.deb ...]
"""
import sys, io, tarfile
sys.path.insert(0, __file__.rsplit('/', 1)[0])
from repack import ar_read


def open_member(name, body):
    if name.endswith('.gz'):
        return tarfile.open(fileobj=io.BytesIO(body), mode='r:gz')
    if name.endswith('.xz') or name.endswith('.lzma'):
        import lzma
        try:
            return tarfile.open(fileobj=io.BytesIO(body), mode='r:xz')
        except Exception:
            raw = lzma.decompress(body, format=lzma.FORMAT_ALONE)
            return tarfile.open(fileobj=io.BytesIO(raw), mode='r:')
    return tarfile.open(fileobj=io.BytesIO(body), mode='r:')


def main():
    for path in sys.argv[1:]:
        members = ar_read(path)
        print('== %s' % path)
        print('   members: %s' % ', '.join(n for n, _ in members))
        for name, body in members:
            if not name.startswith(('control.tar', 'data.tar')):
                continue
            tf = open_member(name, body)
            names = [m.name for m in tf.getmembers()]
            if name.startswith('control.tar'):
                for m in tf.getmembers():
                    if m.name.endswith('/control') or m.name == 'control':
                        arch = [l for l in tf.extractfile(m).read().decode().splitlines()
                                if l.startswith('Architecture')]
                        print('   %s' % (arch[0] if arch else 'Architecture: ?'))
            else:
                top = sorted({n.split('/')[0] for n in names if n.strip('./')})
                print('   data 顶层: %s   (%d entries)' % (top, len(names)))
                for n in names:
                    if n.endswith('.dylib'):
                        print('   dylib: %s  (%.1f KB)'
                              % (n, len(tf.extractfile(n).read()) / 1024.0))
        print()


if __name__ == '__main__':
    main()
