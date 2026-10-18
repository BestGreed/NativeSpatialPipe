"""Extract thin feature edges from Kenney's CC0 toilet GLB for the listener glyph."""
import json
import math
import struct
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
blob = (HERE / 'toilet-kenney.glb').read_bytes()
assert blob[:4] == b'glTF'
json_size = struct.unpack_from('<I', blob, 12)[0]
gltf = json.loads(blob[20:20 + json_size])
bin_offset = 20 + json_size + 8
binary = memoryview(blob)[bin_offset:]


def accessor(index):
    item = gltf['accessors'][index]
    view = gltf['bufferViews'][item['bufferView']]
    components = {'SCALAR': 1, 'VEC3': 3}[item['type']]
    letter = {5125: 'I', 5123: 'H', 5126: 'f'}[item['componentType']]
    size = struct.calcsize('<' + letter * components)
    offset = view.get('byteOffset', 0) + item.get('byteOffset', 0)
    stride = view.get('byteStride', size)
    return [struct.unpack_from('<' + letter * components, binary, offset + i * stride)
            for i in range(item['count'])]


def rotate(p, q):
    x, y, z, w = q
    vx, vy, vz = p
    tx, ty, tz = (2 * (y * vz - z * vy), 2 * (z * vx - x * vz), 2 * (x * vy - y * vx))
    return (vx + w * tx + y * tz - z * ty,
            vy + w * ty + z * tx - x * tz,
            vz + w * tz + x * ty - y * tx)


def transform(p, chain):
    for node in chain:
        scaled = tuple(p[i] * node.get('scale', [1, 1, 1])[i] for i in range(3))
        turned = rotate(scaled, node.get('rotation', [0, 0, 0, 1]))
        p = tuple(turned[i] + node.get('translation', [0, 0, 0])[i] for i in range(3))
    return p


faces = []
def walk(node_index, parents):
    node = gltf['nodes'][node_index]
    chain = [node, *parents]
    if 'mesh' in node:
        for primitive in gltf['meshes'][node['mesh']]['primitives']:
            assert primitive.get('mode', 4) == 4
            positions = [transform(p, chain) for p in accessor(primitive['attributes']['POSITION'])]
            indices = [i[0] for i in accessor(primitive['indices'])]
            faces.extend(tuple(positions[i] for i in indices[k:k + 3]) for k in range(0, len(indices), 3))
    for child in node.get('children', []):
        walk(child, chain)


for root in gltf['scenes'][gltf.get('scene', 0)]['nodes']:
    walk(root, [])

def weld(p):
    return tuple(round(v, 5) for v in p)

def normal(face):
    a, b, c = face
    u = [b[i] - a[i] for i in range(3)]
    v = [c[i] - a[i] for i in range(3)]
    n = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
    length = math.hypot(*n)
    return tuple(x / length for x in n) if length > 1e-9 else (0, 0, 0)


edges = defaultdict(list)
for face in faces:
    n = normal(face)
    if n == (0, 0, 0):
        continue
    vertices = [weld(p) for p in face]
    for i in range(3):
        a, b = vertices[i], vertices[(i + 1) % 3]
        if a != b:
            edges[tuple(sorted((a, b)))].append(n)

segments = []
source_floor = min(p[1] for face in faces for p in face)
center = (0.156, 0.24)
point = lambda p: [round((p[0] - center[0]) * .72, 4),
                   round((p[1] - source_floor) * .72 - .8, 4),
                   round((center[1] - p[2]) * .72, 4)]
for (a, b), normals in edges.items():
    # Remove the diagonal inside flat triangulated faces. Keep boundary and crease edges.
    if len(normals) == 2 and sum(normals[0][i] * normals[1][i] for i in range(3)) > math.cos(math.radians(14)):
        continue
    # Place the prop on the reference grid (y=-0.8). The listener/head remains
    # at y=0, about 0.5 display units above the seat of this small model.
    segments.append([point(a), point(b)])

segments.sort()
triangles = [[point(p) for p in face] for face in faces]
output = ('export const toiletLines = ' + json.dumps(segments, separators=(',', ':')) + ';\n' +
          'export const toiletFaces = ' + json.dumps(triangles, separators=(',', ':')) + ';\n')
(HERE / 'toilet-wire.js').write_text(output, encoding='utf-8')
print(f'{len(faces)} triangles, {len(edges)} unique edges, {len(segments)} wire segments')
