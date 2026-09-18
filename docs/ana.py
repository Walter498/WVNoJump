#!/usr/bin/env python3
import struct, sys
from capstone import *

PATH = sys.argv[1] if len(sys.argv) > 1 else 'WTVRBGLauncher.dylib'
data = open(PATH, 'rb').read()
magic, nfat = struct.unpack_from('>II', data, 0)
base = 0x4000
for i in range(nfat):
    cpu, sub, off, size, align = struct.unpack_from('>IIIII', data, 8 + i*20)
    if sub == 0:
        base = off
img = data[base:]

ncmds = struct.unpack_from('<I', img, 16)[0]
off = 32
sections, segs = {}, []
symtab_off = nsyms = stroff = 0
for _ in range(ncmds):
    cmd, cmdsize = struct.unpack_from('<II', img, off)
    if cmd == 0x19:
        nsects = struct.unpack_from('<I', img, off+64)[0]
        for j in range(nsects):
            so = off + 72 + j*80
            sname = img[so:so+16].rstrip(b'\0').decode()
            seg = img[so+16:so+32].rstrip(b'\0').decode()
            addr, size2, foff, al = struct.unpack_from('<QQII', img, so+32)
            sections[seg+'.'+sname] = (addr, size2, foff)
        segs.append((img[off+8:off+24].rstrip(b'\0').decode(),
                     *struct.unpack_from('<QQQQ', img, off+24)))
    elif cmd == 0x2:
        symtab_off, nsyms, stroff, strsize = struct.unpack_from('<IIII', img, off+8)
    off += cmdsize

# imports table from chained fixups
imps=[]
off2=32
for _ in range(ncmds):
    cmd,cs=struct.unpack_from('<II',img,off2)
    if cmd==0x80000034:
        dataoff,datasize=struct.unpack_from('<II',img,off2+8)
        ver,so,io,syo,ic,ifmt,sfmt=struct.unpack_from('<IIIIIII',img,dataoff)
        for i in range(ic):
            if ifmt==1:
                v=struct.unpack_from('<I',img,dataoff+io+i*4)[0]; no=v>>9
            else:
                v,add=struct.unpack_from('<II',img,dataoff+io+i*8); no=v>>9
            e=img.index(b'\0',dataoff+syo+no)
            imps.append(img[dataoff+syo+no:e].decode())
    off2+=cs

syms = []
for i in range(nsyms):
    strx, ntype, sect, desc, val = struct.unpack_from('<IBBHQ', img, symtab_off+i*16)
    e = img.index(b'\0', stroff+strx)
    syms.append(img[stroff+strx:e].decode())

def rd(v, n):
    for nm, (a, s, f) in sections.items():
        if a <= v < a+s:
            return img[f+(v-a):f+(v-a)+n]
    return b'\0'*n

def cstr(v):
    if not v: return ''
    o = b''
    while len(o) < 200:
        b = rd(v+len(o), 1)
        if not b or b == b'\0': break
        o += b
    return o.decode('utf-8', 'replace')

# chained fixup decode (DYLD_CHAINED_PTR_64_OFFSET)
def dec(v):
    if v >> 63:
        return ('B', v & 0xFFFFFF)
    return ('R', v & 0xFFFFFFFFF)

allptr = {}
secmap = {}
for sn, tag in [('__DATA.__objc_selrefs', 'SELREF'),
                ('__DATA_CONST.__got', 'GOT'),
                ('__DATA.__objc_classrefs', 'CLSREF'),
                ('__DATA_CONST.__cfstring', 'CFSTR')]:
    a, sz, fo = sections[sn]
    for i in range(0, sz, 8):
        v = struct.unpack_from('<Q', img, fo+i)[0]
        allptr[a+i] = (tag,) + dec(v)

# CF strings are 32-byte structs: isa,flags,char*,len
cfmap = {}
a, sz, fo = sections['__DATA_CONST.__cfstring']
for i in range(0, sz, 32):
    isa = struct.unpack_from('<Q', img, fo+i)[0]
    cp = dec(struct.unpack_from('<Q', img, fo+i+16)[0])[1]
    ln = struct.unpack_from('<Q', img, fo+i+24)[0]
    cfmap[a+i] = rd(cp, ln).decode('utf-8', 'replace') if ln < 100 else '?'

MD = Cs(CS_ARCH_ARM64, CS_MODE_ARM)

def adrp_target(w, pc):
    immlo = (w >> 29) & 3
    immhi = (w >> 5) & 0x7ffff
    imm = (immhi << 2) | immlo
    if imm & (1 << 20): imm -= 1 << 21
    return (pc & ~0xfff) + (imm << 12)

# __stubs : 12-byte objc_msgSend/plt stubs
stubs = {}
a, sz, fo = sections['__TEXT.__stubs']
for i in range(0, sz, 12):
    va = a+i
    w = struct.unpack_from('<III', img, fo+i)
    t = adrp_target(w[0], va)
    o2 = ((w[1] >> 10) & 0xfff)*8
    k = allptr.get(t+o2)
    nm = None
    if k:
        if k[1] == 'B': nm = imps[k[2]]
        else: nm = 'R:' + cstr(k[2])
    stubs[va] = ('PLT', nm, t+o2)

# __objc_stubs : 32-byte
a, sz, fo = sections['__TEXT.__objc_stubs']
for i in range(0, sz, 32):
    va = a+i
    w = struct.unpack_from('<8I', img, fo+i)
    if (w[0] >> 24) & 0x9f != 0x90: continue
    t0 = adrp_target(w[0], va); o1 = ((w[1] >> 10) & 0xfff)*8
    k0 = allptr.get(t0+o1)
    sel = cstr(k0[2]) if k0 and k0[0] == 'SELREF' else '?' + hex(t0+o1)
    t2 = adrp_target(w[2], va+8); o3 = ((w[3] >> 10) & 0xfff)*8
    k2 = allptr.get(t2+o3)
    cl = (imps[k2[2]] if k2[1] == 'B' else 'R') if k2 else '?'
    stubs[va] = ('OBJC', sel, cl)

L = []
MD2 = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
ta, tsz, tfo = sections['__TEXT.__text']
for ins in MD2.disasm(img[tfo:tfo+tsz], ta):
    L.append((ins.address, ins.mnemonic, ins.op_str, ins.bytes.hex()))
addrset = set(x[0] for x in L)

def try_adrp(idx):
    addr, m, o, _ = L[idx]
    if m != 'adrp': return (None, None)
    r = o.split(',')[0].strip()
    tgt = int(o.split(",")[1].strip().lstrip("#"), 0)
    for j in range(idx+1, min(idx+4, len(L))):
        a2, m2, o2, _ = L[j]
        p = o2.split(',')
        if not p or p[0].strip() != r: continue
        if m2 == 'add' and len(p) >= 3:
            try: imm = int(p[2].strip().lstrip('#'), 0)
            except Exception: return (None, None)
            return (tgt+imm, j)
        if m2 == 'ldr' and '#' in o2:
            try: imm = int(o2.split('#')[1].strip().rstrip(']'), 0)
            except Exception: return (None, None)
            return (tgt+imm, j)
    return (tgt, None)

def describe(tgt):
    k = allptr.get(tgt)
    if k:
        if k[0] == 'SELREF': return '@sel(%s)' % cstr(k[2])
        if k[0] == 'GOT': return '&%s' % (imps[k[2]] if k[1] == 'B' else '?')
        if k[0] == 'CLSREF': return 'clsref:' + (imps[k[2]] if k[1] == 'B' else '?')
        if k[0] == 'CFSTR': return 'NSSTR %r' % cfmap.get(tgt, '?')
    if tgt in stubs: return 'STUB[%s]' % (stubs[tgt][1],)
    for nm, (a2, s2, f2) in sections.items():
        if a2 <= tgt < a2+s2:
            s = cstr(tgt)
            if s and len(s) < 60: return '%s "%s"' % (nm, s)
            return '%s %s' % (nm, hex(tgt))
    return hex(tgt)

out = []
for idx, (addr, m, o, by) in enumerate(L):
    cm = ''
    if m == 'adrp':
        tgt, jj = try_adrp(idx)
        if tgt is not None:
            cm = '; ' + describe(tgt)
    elif m in ('bl', 'b') and o.startswith('#'):
        t = int(o.lstrip('#'), 0)
        if t in stubs: cm = '; ==%s' % (stubs[t][1],)
        elif t in addrset: cm = '; -> %x' % t
        else: cm = '; -> %s?' % hex(t)
    out.append('%04x  %-7s %-40s %s' % (addr, m, o, cm))
open('/var/minis/workspace/v2t/text.asm', 'w').write('\n'.join(out)+'\n')

print('== stubs ==')
for k, v in sorted(stubs.items()):
    print(hex(k), v[0], v[1], '' if v[0] == 'OBJC' else v[2])
print('== unwind/globals section __DATA.__bss 0xc128..0xc170 ==')
print('== text written ==')
