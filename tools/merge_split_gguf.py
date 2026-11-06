#!/usr/bin/env python3
"""合并 llama.cpp split GGUF 为单文件：清除 split 元数据后重写。"""
import struct, sys, os

SRC1 = "/opt/models/qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf"
SRC2 = "/opt/models/qwen2.5-7b-instruct-q4_k_m-00002-of-00002.gguf"
OUT = "/opt/models/qwen2.5-7b-instruct-q4_k_m.gguf"

GGUF_MAGIC = 0x46554747  # "GGUF"

def read_header(f):
    magic = struct.unpack("<I", f.read(4))[0]
    assert magic == GGUF_MAGIC, f"bad magic {magic:#x}"
    ver = struct.unpack("<I", f.read(4))[0]
    n_tensors, n_kv = struct.unpack("<QQ", f.read(16))
    return ver, n_tensors, n_kv

def read_value(f):
    vtype = struct.unpack("<I", f.read(4))[0]
    # types: 0=uint8 1=int8 2=uint16 3=int16 4=uint32 5=int32 6=float32 7=bool 8=string 9=array 10=uint64 11=int64 12=float64
    if vtype == 0: return vtype, struct.unpack("<B", f.read(1))[0]
    if vtype == 1: return vtype, struct.unpack("<b", f.read(1))[0]
    if vtype == 2: return vtype, struct.unpack("<H", f.read(2))[0]
    if vtype == 3: return vtype, struct.unpack("<h", f.read(2))[0]
    if vtype == 4: return vtype, struct.unpack("<I", f.read(4))[0]
    if vtype == 5: return vtype, struct.unpack("<i", f.read(4))[0]
    if vtype == 6: return vtype, struct.unpack("<f", f.read(4))[0]
    if vtype == 7: return vtype, f.read(1)[0]
    if vtype == 8:
        ln = struct.unpack("<Q", f.read(8))[0]
        return vtype, f.read(ln)
    if vtype == 9:
        atype = struct.unpack("<I", f.read(4))[0]
        n = struct.unpack("<Q", f.read(8))[0]
        items = []
        for _ in range(n):
            _, v = read_value_typed(f, atype)
            items.append(v)
        return vtype, (atype, items)
    if vtype == 10: return vtype, struct.unpack("<Q", f.read(8))[0]
    if vtype == 11: return vtype, struct.unpack("<q", f.read(8))[0]
    if vtype == 12: return vtype, struct.unpack("<d", f.read(8))[0]
    raise ValueError(f"unknown vtype {vtype}")

def read_value_typed(f, vtype):
    if vtype == 0: return vtype, struct.unpack("<B", f.read(1))[0]
    if vtype == 1: return vtype, struct.unpack("<b", f.read(1))[0]
    if vtype == 2: return vtype, struct.unpack("<H", f.read(2))[0]
    if vtype == 3: return vtype, struct.unpack("<h", f.read(2))[0]
    if vtype == 4: return vtype, struct.unpack("<I", f.read(4))[0]
    if vtype == 5: return vtype, struct.unpack("<i", f.read(4))[0]
    if vtype == 6: return vtype, struct.unpack("<f", f.read(4))[0]
    if vtype == 7: return vtype, f.read(1)[0]
    if vtype == 8:
        ln = struct.unpack("<Q", f.read(8))[0]
        return vtype, f.read(ln)
    if vtype == 10: return vtype, struct.unpack("<Q", f.read(8))[0]
    if vtype == 11: return vtype, struct.unpack("<q", f.read(8))[0]
    if vtype == 12: return vtype, struct.unpack("<d", f.read(8))[0]
    raise ValueError(f"unknown vtype {vtype}")

def main():
    print("读取分卷 1 头部...")
    with open(SRC1, "rb") as f1:
        ver1, nt1, nkv1 = read_header(f1)
        print(f"  ver={ver1} tensors={nt1} kvs={nkv1}")
        kvs = []
        for i in range(nkv1):
            klen = struct.unpack("<Q", f1.read(8))[0]
            key = f.read(klen).decode() if False else f1.read(klen).decode("utf-8")
            vtype, val = read_value(f1)
            kvs.append((key, vtype, val))
        # 找到 tensor info 起点并读取
        tensor_infos = []
        for i in range(nt1):
            nlen = struct.unpack("<Q", f1.read(8))[0]
            name = f1.read(nlen).decode("utf-8")
            ndims = struct.unpack("<I", f.read(4))[0] if False else struct.unpack("<I", f1.read(4))[0]
            dims = [struct.unpack("<Q", f1.read(8))[0] for _ in range(ndims)]
            dtype = struct.unpack("<I", f1.read(4))[0]
            offset = struct.unpack("<Q", f1.read(8))[0]
            tensor_infos.append((name, ndims, dims, dtype, offset))
        data_start1 = f1.tell()
        # 对齐到 32
        align = 32
        pad = (align - data_start1 % align) % align
        f1.seek(data_start1 + pad)
        data1_pos = f1.tell()
        f1.seek(0, 2)
        size1 = f1.tell()
        data1_size = size1 - data1_pos
        print(f"  data1: pos={data1_pos} size={data1_size}")

    print("读取分卷 2 头部与张量信息...")
    with open(SRC2, "rb") as f2:
        ver2, nt2, nkv2 = read_header(f2)
        print(f"  ver={ver2} tensors={nt2} kvs={nkv2}")
        for i in range(nkv2):
            klen = struct.unpack("<Q", f2.read(8))[0]
            f2.read(klen)
            read_value(f2)
        tensor_infos2 = []
        for i in range(nt2):
            nlen = struct.unpack("<Q", f2.read(8))[0]
            name = f2.read(nlen).decode("utf-8")
            ndims = struct.unpack("<I", f2.read(4))[0]
            dims = [struct.unpack("<Q", f2.read(8))[0] for _ in range(ndims)]
            dtype = struct.unpack("<I", f2.read(4))[0]
            offset = struct.unpack("<Q", f2.read(8))[0]
            tensor_infos2.append((name, ndims, dims, dtype, offset))
        data_start2 = f2.tell()
        pad2 = (32 - data_start2 % 32) % 32
        f2.seek(data_start2 + pad2)
        data2_pos = f2.tell()
        f2.seek(0, 2)
        size2 = f2.tell()
        data2_size = size2 - data2_pos
        print(f"  data2: pos={data2_pos} size={data2_size}")

    assert data1_size % 32 == 0, f"data1_size {data1_size} not aligned"
    # 合并张量目录：卷2 的 offset 平移 data1_size
    all_tensors = list(tensor_infos)
    for (name, ndims, dims, dtype, offset) in tensor_infos2:
        all_tensors.append((name, ndims, dims, dtype, offset + data1_size))
    print(f"张量目录: 卷1 {len(tensor_infos)} + 卷2 {len(tensor_infos2)} = {len(all_tensors)}")

    # 过滤 split 相关 KV
    DROP_KEYS = {"split.count", "split.no", "split.total_count"}
    kept = [(k, t, v) for (k, t, v) in kvs if k not in DROP_KEYS]
    dropped = [k for (k, _, _) in kvs if k in DROP_KEYS]
    print(f"清除 split KV: {dropped}, 保留 {len(kept)}/{len(kvs)}")

    total_data = data1_size + data2_size
    n_all = len(all_tensors)
    print(f"写出合并文件: {OUT} (tensors={n_all}, data={total_data})")
    with open(OUT, "wb") as out:
        # header
        out.write(struct.pack("<I", GGUF_MAGIC))
        out.write(struct.pack("<IQQ", ver1, n_all, len(kept)))
        for (k, t, v) in kept:
            kbytes = k.encode("utf-8")
            out.write(struct.pack("<Q", len(kbytes)))
            out.write(kbytes)
            out.write(struct.pack("<I", t))
            write_value(out, t, v)
        for (name, ndims, dims, dtype, offset) in all_tensors:
            nb = name.encode("utf-8")
            out.write(struct.pack("<Q", len(nb)))
            out.write(nb)
            out.write(struct.pack("<I", ndims))
            for d in dims:
                out.write(struct.pack("<Q", d))
            out.write(struct.pack("<I", dtype))
            out.write(struct.pack("<Q", offset))
        data_off = out.tell()
        pad = (32 - data_off % 32) % 32
        out.write(b"\x00" * pad)
        # concat data
        with open(SRC1, "rb") as f1:
            f1.seek(data1_pos)
            while chunk := f1.read(1 << 22):
                out.write(chunk)
        with open(SRC2, "rb") as f2:
            f2.seek(data2_pos)
            while chunk := f2.read(1 << 22):
                out.write(chunk)
    print(f"完成: {os.path.getsize(OUT)} bytes")

def write_value(out, vtype, val):
    if vtype == 0: out.write(struct.pack("<B", val))
    elif vtype == 1: out.write(struct.pack("<b", val))
    elif vtype == 2: out.write(struct.pack("<H", val))
    elif vtype == 3: out.write(struct.pack("<h", val))
    elif vtype == 4: out.write(struct.pack("<I", val))
    elif vtype == 5: out.write(struct.pack("<i", val))
    elif vtype == 6: out.write(struct.pack("<f", val))
    elif vtype == 7: out.write(bytes([val]))
    elif vtype == 8:
        out.write(struct.pack("<Q", len(val)))
        out.write(val)
    elif vtype == 9:
        atype, items = val
        out.write(struct.pack("<I", atype))
        out.write(struct.pack("<Q", len(items)))
        for it in items:
            write_value(out, atype, it)
    elif vtype == 10: out.write(struct.pack("<Q", val))
    elif vtype == 11: out.write(struct.pack("<q", val))
    elif vtype == 12: out.write(struct.pack("<d", val))
    else: raise ValueError(f"bad vtype {vtype}")

if __name__ == "__main__":
    main()
