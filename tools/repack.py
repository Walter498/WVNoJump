#!/usr/bin/env python3
"""deb 变体打包工具

用法:
  repack.py <in.deb> <out.deb> --arch iphoneos-arm64e [--strip-prefix var/jb]

做两件事:
  1. 改写 control 里的 Architecture 字段
  2. 可选：把 data.tar 里的路径去掉某个前缀（roothide 包就是无 /var/jb 前缀的布局）

不依赖 dpkg-deb / ar / tar 命令，纯 python3。
"""
import sys, io, os, re, gzip, lzma, tarfile


def ar_read(path):
    data = open(path, 'rb').read()
    if data[:8] != b'!<arch>\n':
        raise SystemExit('not an ar archive: %s' % path)
    off, out = 8, []
    while off + 60 <= len(data):
        hdr = data[off:off + 60]
        name = hdr[0:16].decode('ascii', 'replace').strip().rstrip('/')
        size = int(hdr[48:58].decode('ascii').strip())
        body = data[off + 60:off + 60 + size]
        out.append([name, body])
        off += 60 + size + (size % 2)
    return out


def ar_write(path, members):
    out = bytearray(b'!<arch>\n')
    for name, body in members:
        hdr = ('%-16s%-12s%-6s%-6s%-8s%-10s`\n'
               % (name, '0', '0', '0', '100644', str(len(body)))).encode('ascii')
        assert len(hdr) == 60, len(hdr)
        out += hdr + body
        if len(body) % 2:
            out += b'\n'
    open(path, 'wb').write(out)


def tar_mode(name):
    if name.endswith('.gz'):
        return 'r:gz', 'w:gz', 'gz'
    if name.endswith('.xz'):
        return 'r:xz', 'w:xz', 'xz'
    if name.endswith('.lzma'):
        return 'r:xz', 'w:xz', 'lzma'
    if name.endswith('.tar'):
        return 'r:', 'w:', ''
    raise SystemExit('unknown tar compression: %s' % name)


def rewrite_tar(body, name, arch=None, strip_prefix=None):
    rmode, wmode, ext = tar_mode(name)
    if ext == 'lzma':
        raw = lzma.decompress(body, format=lzma.FORMAT_ALONE)
        src = tarfile.open(fileobj=io.BytesIO(raw), mode='r:')
    else:
        src = tarfile.open(fileobj=io.BytesIO(body), mode=rmode)

    out = io.BytesIO()
    if ext == 'lzma':
        tmp = io.BytesIO()
        dst = tarfile.open(fileobj=tmp, mode='w:')
    else:
        dst = tarfile.open(fileobj=out, mode=wmode)

    prefix = (strip_prefix.strip('/') + '/') if strip_prefix else None

    for m in src.getmembers():
        name = m.name
        dotted = name.startswith('./')
        norm = name[2:] if dotted else name
        newname = name
        if prefix:
            if norm == prefix.rstrip('/') or norm.startswith(prefix):
                rest = norm[len(prefix):]
                if not rest:
                    continue
                newname = ('./' + rest) if dotted else rest
            elif norm in ('.', '', prefix.rstrip('/'), prefix.rstrip('/').split('/')[0]):
                # 前缀目录本身（var / var/jb）丢掉
                continue

        m2 = tarfile.TarInfo(newname)
        for attr in ('mode', 'uid', 'gid', 'uname', 'gname', 'mtime', 'type',
                     'linkname', 'devmajor', 'devminor'):
            setattr(m2, attr, getattr(m, attr))
        if m.isreg():
            f = src.extractfile(m)
            data = f.read() if f else b''
            if arch and newname.endswith('/control'):
                data = re.sub(rb'(?m)^Architecture:.*$',
                              b'Architecture: ' + arch.encode(), data)
            m2.size = len(data)
            dst.addfile(m2, io.BytesIO(data))
        else:
            m2.size = 0
            dst.addfile(m2)

    dst.close()
    if ext == 'lzma':
        return lzma.compress(tmp.getvalue(), format=lzma.FORMAT_ALONE)
    return out.getvalue()


def main():
    args = sys.argv[1:]
    if len(args) < 2:
        raise SystemExit(__doc__)
    src, dst = args[0], args[1]
    arch = None
    strip_prefix = None
    i = 2
    while i < len(args):
        if args[i] == '--arch':
            arch = args[i + 1]; i += 2
        elif args[i] == '--strip-prefix':
            strip_prefix = args[i + 1]; i += 2
        else:
            raise SystemExit('unknown arg %s' % args[i])

    members = ar_read(src)
    out = []
    for name, body in members:
        if name.startswith('control.tar'):
            body = rewrite_tar(body, name, arch=arch)
        elif name.startswith('data.tar'):
            body = rewrite_tar(body, name, strip_prefix=strip_prefix)
        out.append([name, body])
    ar_write(dst, out)
    print('wrote %s  (arch=%s strip=%s)' % (dst, arch, strip_prefix))


if __name__ == '__main__':
    main()
