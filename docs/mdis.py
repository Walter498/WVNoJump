#!/usr/bin/env python3
import struct, sys, json
from capstone import *
from capstone.arm64 import *

PATH = sys.argv[1] if len(sys.argv) > 1 else 'WTVRBGLauncher.dylib'
data = open(PATH, 'rb').read()
magic, nfat = struct.unpack_from('>II', data, 0)
base = None
for i in range(nfat):
    cpu, sub, off, size, align = struct.unpack_from('>IIIII', data, 8 + i*20)
    if sub == 0:
        base = off
base = base or 0x4000
img = data[base:]

ncmds = struct.unpack_from('<I', img, 16)[0]
off = 32
sections = {}
segs = []
symtab_off = nsyms = stroff = 0
for _ in range(ncmds):
    cmd, cmdsize = struct.unpack_from('<II', img, off)
    if cmd == 0x19:
        segname = img[off+8:off+24].rstrip(b'\0').decode()
        vmaddr, vmsize, fileoff, filesize = struct.unpack_from('<QQQQ', img, off+24)
        nsects = struct.unpack_from('<I', img, off+64)[0]
        s = []
        for j in range(nsects):
            so = off + 72 + j*80
            sname = img[so:so+16].rstrip(b'\0').decode()
            seg = img[so+16:so+32].rstrip(b'\0').decode()
            addr, size2, foff, align2 = struct.unpack_from('<QQII', img, so+32)
            s.append((sname, addr, size2, foff))
            sections[seg+'.'+sname] = (addr, size2, foff)
        segs.append((segname, vmaddr, vmsize, fileoff, filesize))
    elif cmd == 0x2:
        symtab_off, nsyms, stroff, strsize = struct.unpack_from('<IIII', img, off+8)
    elif cmd == 0x22:  # LC_DYLD_INFO / 0x80000022 chained
        pass
    off += cmdsize

syms = []
for i in range(nsyms):
    so = symtab_off + i*16
    strx, ntype, sect, desc, val = struct.unpack_from('<IBBHQ', img, so)
    e = img.index(b'\0', stroff+strx)
    syms.append(img[stroff+strx:e].decode())
print('# symtab:', syms)

def sec(n):
    return sections.get(n)

def rd(vaddr, n):
    for nm, (addr, size, foff) in sections.items():
        if addr <= vaddr < addr + size:
            return img[foff + (vaddr-addr): foff + (vaddr-addr) + n]
    return b'\0'*n

def cstr(vaddr):
    if not vaddr:
        return ''
    out = b''
    while len(out) < 200:
        b = rd(vaddr+len(out), 1)
        if not b or b == b'\0':
            break
        out += b
    return out.decode('utf-8', 'replace')

def decode_slot(v):
    if v >> 63:
        return ('BIND', v & 0xFFFFFF)
    return ('REBASE', v & 0xFFFFFFFFF)

allptr = {}
def map_section(secname, tag):
    a, sz, fo = sections[secname]
    m = {}
    for i in range(0, sz, 8):
        v = struct.unpack_from('<Q', img, fo+i)[0]
        kind, val = decode_slot(v)
        m[a+i] = (tag, kind, val)
    allptr.update(m)
    return m

selrefs = map_section('__DATA.__objc_selrefs', 'SELREF')
classrefs = map_section('__DATA.__objc_classrefs', 'CLASSREF')
got = map_section('__DATA_CONST.__got', 'GOT')
cfstr = map_section('__DATA_CONST.__cfstring', 'CFSTR')

print('# selrefs')
for k, v in sorted(selrefs.items()):
    print('#  ', hex(k), cstr(v[2]))
print('# classrefs (bind ordinal -> sym)')
for k, v in sorted(classrefs.items()):
    print('#  ', hex(k), v, syms[v[2]] if v[1] == 'BIND' and v[2] < len(syms) else '?')
print('# got')
for k, v in sorted(got.items()):
    print('#  ', hex(k), v, syms[v[2]] if v[1] == 'BIND' and v[2] < len(syms) else '?')

# cfstring -> NSString literal: struct {isa, flags, char*, len}
print('# cfstrings')
cfmap = {}
for k, v in sorted(cfstr.items()):
    isa, flags, cp, ln = struct.unpack('<QQQQ', rd(k, 32))
    s = rd(cp, ln).decode('utf-8', 'replace') if ln < 200 else '?'
    cfmap[k] = s
    print('#  ', hex(k), 'flags', hex(flags), repr(s))

# objc stubs, stride 32
oa, osz, ofo = sections['__TEXT.__objc_stubs']
objstubs = {}
STRIDE = 0x20
for i in range(0, osz, STRIDE):
    vaddr = oa + i
    words = struct.unpack_from('<8I', img, ofo+i)
    if words[0] >> 24 != 0x90:
        continue
    def adrp_target(w, pc):
        immlo = (w >> 29) & 3
        immhi = (w >> 5) & 0x7ffff
        imm = (immhi << 2) | immlo
        if imm & (1 << 20):
            imm -= 1 << 21
        return (pc & ~0xfff) + (imm << 12)
    t0 = adrp_target(words[0], vaddr)
    off1 = ((words[1] >> 10) & 0xfff) * 8
    # ldr x1, [x1, #imm]
    slot = t0 + off1
    kind = allptr.get(slot)
    nm = None
    if kind:
        if kind[0] == 'SELREF':
            nm = '@selector(' + cstr(kind[2]) + ')'
        else:
            nm = kind[0]
    t2 = adrp_target(words[2], vaddr+8)
    off3 = ((words[3] >> 10) & 0xfff) * 8
    slot2 = t2 + off3
    k2 = allptr.get(slot2)
    nm2 = None
    if k2:
        nm2 = ('GOT[' + syms[k2[2]] + ']') if k2[1] == 'BIND' and k2[2] < len(syms) else str(k2)
    objstubs[vaddr] = (nm, nm2, slot, slot2)
print('# objc stubs')
for k, v in sorted(objstubs.items()):
    print('#  ', hex(k), v)

MD = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
MD.detail = False
ta, tsz, tfo = sec('__TEXT.__text')
insns = list(MD.disasm(img[tfo:tfo+tsz], ta))
print('# text %d insns %s..%s' % (len(insns), hex(ta), hex(ta+tsz)))

L = [(i.address, i.mnemonic, i.op_str, i.bytes.hex()) for i in insns]

def try_resolve(addr, idx):
    _, m, o, _ = L[idx]
    if m != 'adrp':
        return None
    rd_ = o.split(',')[0].strip()
    tgt = int(o.split(',')[1].strip(), 0)
    for j in range(idx+1, min(idx+4, len(L))):
        a2, m2, o2, _ = L[j]
        parts = o2.split(',')
        if not parts or parts[0].strip() != rd_:
            continue
        if m2 == 'add' and len(parts) >= 3:
            try:
                imm = int(parts[2].strip().lstrip('#'), 0)
            except Exception:
                return None
            return (tgt+imm, j)
        if m2 == 'ldr' and '#' in o2:
            try:
                imm = int(o2.split('#')[1].strip().rstrip(']'), 0)
            except Exception:
                return None
            return (tgt+imm, j)
    return (None, None)

out = []
for idx, (addr, m, o, by) in enumerate(L):
    txt = ''
    if m == 'adrp':
        tgt, jj = try_resolve(addr, idx)
        if tgt is not None:
            k = allptr.get(tgt)
            if k:
                if k[0] == 'SELREF':
                    txt = '; %s' % cstr(k[2])
                elif k[0] == 'CLASSREF':
                    txt = '; [%s class]' % (syms[k[2]] if k[1] == 'BIND' and k[2] < len(syms) else '?')
                elif k[0] == 'GOT':
                    txt = '; &%s' % (syms[k[2]] if k[1] == 'BIND' and k[2] < len(syms) else '?')
                elif k[0] == 'CFSTR':
                    txt = '; NSSTR %r' % cfmap.get(tgt, '?')
            elif tgt in objstubs:
                txt = '; stub %s' % (objstubs[tgt],)
            else:
                s = cstr(tgt)
                if s and len(s) < 70 and all(32 <= ord(c) < 127 or ord(c) > 127 for c in s):
                    txt = '; "%s"' % s
                else:
                    txt = '; -> %s' % hex(tgt)
    if m in ('bl', 'b') and o.startswith('#'):
        try:
            t = int(o.lstrip('#'), 0)
            if t in objstubs:
                txt = '; -> stub %s' % (objstubs[t],)
            elif t in set(a for a, _, _, _ in L):
                txt = '; -> L_%x' % t
            else:
                txt = '; -> %s' % hex(t)
        except Exception:
            pass
    if m == 'nop' or m == 'brk':
        pass
    out.append('%04x:  %-7s %-42s %s' % (addr, m, o, txt))

open('/var/minis/workspace/v2t/text.asm', 'w').write('\n'.join(out)+'\n')
print('# wrote text.asm')
