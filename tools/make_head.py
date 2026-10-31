"""Build docs/head.bin, the humanoid's face in Voice mode, from a 3D head scan.

    python tools/make_head.py LeePerrySmith.glb docs/head.bin

The scan: "Infinite, 3D Head Scan" by Lee Perry-Smith (Infinite Realities, www.ir-ltd.net), licensed CC BY 3.0, from
the three.js examples (examples/models/gltf/LeePerrySmith/LeePerrySmith.glb). Only its wireframe goes into head.bin.
"""
import json
import struct
import sys

import numpy as np

COMP = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


def load(path):
    data = open(path, "rb").read()
    magic, version, length = struct.unpack_from("<III", data, 0)
    assert magic == 0x46546C67, "not a glb"
    off, doc, binary = 12, None, None
    while off < length:
        clen, ctype = struct.unpack_from("<II", data, off)
        chunk = data[off + 8: off + 8 + clen]
        if ctype == 0x4E4F534A:
            doc = json.loads(chunk)
        elif ctype == 0x004E4942:
            binary = chunk
        off += 8 + clen

    def accessor(i):
        a = doc["accessors"][i]
        bv = doc["bufferViews"][a["bufferView"]]
        start = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        dt, n = COMP[a["componentType"]], NCOMP[a["type"]]
        count = a["count"]
        stride = bv.get("byteStride")
        item = np.dtype(dt).itemsize * n
        if stride and stride != item:
            raw = np.frombuffer(binary, np.uint8, count=stride * count, offset=start).reshape(count, stride)[:, :item]
            return np.frombuffer(raw.tobytes(), dt).reshape(count, n)
        return np.frombuffer(binary, dt, count=count * n, offset=start).reshape(count, n)

    prim = doc["meshes"][0]["primitives"][0]
    pos = accessor(prim["attributes"]["POSITION"]).astype(np.float64)
    tri = accessor(prim["indices"]).reshape(-1, 3).astype(np.int64)
    return pos, tri, doc


pos, tri, _ = load(sys.argv[1])
OUT = sys.argv[2]
Y_CENTER, SCALE = 1.75, 1 / 2.15   # model units -> head units (the eyes near y 0, the crown near -1)
MOUTH_Y = .47

edges = set()
for t in tri:
    e = [(t[0], t[1]), (t[1], t[2]), (t[2], t[0])]
    longest = int(np.argmax([np.linalg.norm(pos[a] - pos[b]) for a, b in e]))
    for k, (a, b) in enumerate(e):
        if k != longest:
            edges.add((min(a, b), max(a, b)))
edges = np.array(sorted(edges))
vis = (pos[:, 2] > -1.2) & (pos[:, 1] > -2.8)
edges = edges[vis[edges[:, 0]] & vis[edges[:, 1]]]
used = np.unique(edges)
remap = -np.ones(len(pos), np.int64)
remap[used] = np.arange(len(used))
P = pos[used]
E = remap[edges]

clamp = lambda v: np.clip(v, 0, 1)
x, y, z = P[:, 0], P[:, 1], P[:, 2]
front = clamp((z - .4) / .9)
# the jaw: below the mouth line, at the front, fading out into the neck and towards the ears
jaw = clamp((MOUTH_Y - .02 - y) / .14) * front * clamp((y + 1.1) / .45) * clamp((1.45 - np.abs(x)) / .5)
# the upper lip lifts a little as the mouth opens
lip = clamp((y - MOUTH_Y) / .03) * clamp((MOUTH_Y + .22 - y) / .12) * clamp((.6 - np.abs(x)) / .2) * clamp((z - 1.6) / .3)
# the body (neck base and shoulders) turns less than the head
body = clamp((-.9 - y) / .9)

head = np.stack([x * SCALE, -(y - Y_CENTER) * SCALE, z * SCALE], axis=1)


def landmark(cx, cy, r=.12):
    near = (np.abs(x - cx) < r) & (np.abs(y - cy) < r)
    zz = z[near].max() if near.any() else 1.9
    return [cx * SCALE, -(cy - Y_CENTER) * SCALE, zz * SCALE]


marks = landmark(-.72, 1.72) + landmark(.72, 1.72) + landmark(0, MOUTH_Y, .1)
with open(OUT, "wb") as f:
    f.write(b"JHD1" + struct.pack("<II", len(P), len(E)))
    f.write(np.clip(np.round(head * 10000), -32767, 32767).astype("<i2").tobytes())
    f.write(np.stack([jaw, lip, body], axis=1).__mul__(255).round().astype(np.uint8).tobytes())
    f.write(E.astype("<u2").tobytes())
    f.write(np.array(marks, "<f4").tobytes())
print("verts", len(P), "edges", len(E), "jaw verts", int((jaw > .05).sum()), "lip verts", int((lip > .05).sum()))
print("landmarks", np.round(marks, 3))
