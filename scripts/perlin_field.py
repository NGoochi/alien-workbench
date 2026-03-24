#! python 3
# NODE_INPUTS: origin:Point3d, grid_count:Vector3d, cell_size:Vector3d, gap_size:Vector3d, noise_scale:float, octaves:int, threshold:float, boundary_brep:Brep, attractors:list[geometry], repulsors:list[geometry], attract_radius:float, attract_strength:float, repel_radius:float, repel_strength:float, output_mode:int, seed:int
# NODE_OUTPUTS: voxels, centers, values, count, log
#
# 3D Perlin noise field generator with attractor/repulsor geometry.
# Output modes: 0=points+values, 1=mesh voxels, 2=brep boxes

import Rhino
import Rhino.Geometry as rg
import math
import random


# ─── PERLIN NOISE ─────────────────────────────────────────────────────
class PerlinNoise(object):
    def __init__(self, seed=0):
        random.seed(seed)
        self.p = list(range(256))
        random.shuffle(self.p)
        self.p *= 2

    def noise3d(self, x, y, z):
        p = self.p
        _floor = math.floor
        xi = int(_floor(x)); yi = int(_floor(y)); zi = int(_floor(z))
        X = xi & 255; Y = yi & 255; Z = zi & 255
        x -= xi; y -= yi; z -= zi
        u = x * x * x * (x * (x * 6.0 - 15.0) + 10.0)
        v = y * y * y * (y * (y * 6.0 - 15.0) + 10.0)
        w = z * z * z * (z * (z * 6.0 - 15.0) + 10.0)
        A = p[X] + Y; AA = p[A] + Z; AB = p[A + 1] + Z
        B = p[X + 1] + Y; BA = p[B] + Z; BB = p[B + 1] + Z
        x1 = x - 1.0; y1 = y - 1.0; z1 = z - 1.0
        def _g(h, gx, gy, gz):
            h &= 15
            a = gx if h < 8 else gy
            b = gy if h < 4 else (gx if h == 12 or h == 14 else gz)
            return (a if (h & 1) == 0 else -a) + (b if (h & 2) == 0 else -b)
        g0 = _g(p[AA], x, y, z);     g1 = _g(p[BA], x1, y, z)
        g2 = _g(p[AB], x, y1, z);    g3 = _g(p[BB], x1, y1, z)
        g4 = _g(p[AA+1], x, y, z1);  g5 = _g(p[BA+1], x1, y, z1)
        g6 = _g(p[AB+1], x, y1, z1); g7 = _g(p[BB+1], x1, y1, z1)
        l0 = g0 + u * (g1 - g0); l1 = g2 + u * (g3 - g2)
        l2 = g4 + u * (g5 - g4); l3 = g6 + u * (g7 - g6)
        m0 = l0 + v * (l1 - l0); m1 = l2 + v * (l3 - l2)
        return m0 + w * (m1 - m0)

    def octave_noise(self, x, y, z, octaves=1):
        val = 0.0; freq = 1.0; amp = 1.0; max_amp = 0.0
        n3d = self.noise3d
        for _ in range(octaves):
            val += n3d(x * freq, y * freq, z * freq) * amp
            max_amp += amp; amp *= 0.5; freq *= 2.0
        return val / max_amp


# ─── DISTANCE HELPER ─────────────────────────────────────────────────
def closest_dist(pt, geo):
    try:
        if isinstance(geo, rg.Point3d):
            return pt.DistanceTo(geo)
        if isinstance(geo, rg.Curve):
            rc, t = geo.ClosestPoint(pt)
            if rc:
                return pt.DistanceTo(geo.PointAt(t))
        elif isinstance(geo, rg.Brep):
            cp = geo.ClosestPoint(pt)
            return pt.DistanceTo(cp)
        elif isinstance(geo, rg.Mesh):
            cp = geo.ClosestPoint(pt)
            return pt.DistanceTo(cp)
        elif isinstance(geo, rg.Surface):
            rc, u, v = geo.ClosestPoint(pt)
            if rc:
                return pt.DistanceTo(geo.PointAt(u, v))
    except:
        pass
    return float('inf')


# ─── GH UNWRAP ───────────────────────────────────────────────────────
def unwrap(obj):
    if obj is None: return None
    return obj.Value if hasattr(obj, 'Value') else obj


# ─── DEFENSIVE DEFAULTS ──────────────────────────────────────────────
origin = unwrap(origin)
if origin is None:
    origin = rg.Point3d(0, 0, 0)

boundary_brep = unwrap(boundary_brep)

if grid_count is None:
    gx, gy, gz = 10, 10, 10
else:
    gx = max(1, int(grid_count.X)) if grid_count.X > 0 else 10
    gy = max(1, int(grid_count.Y)) if grid_count.Y > 0 else 10
    gz = max(1, int(grid_count.Z)) if grid_count.Z > 0 else 10

if cell_size is None:
    cs_x, cs_y, cs_z = 1000.0, 1000.0, 1000.0
else:
    cs_x = cell_size.X if cell_size.X > 0 else 1000.0
    cs_y = cell_size.Y if cell_size.Y > 0 else 1000.0
    cs_z = cell_size.Z if cell_size.Z > 0 else 1000.0

if gap_size is None:
    gp_x, gp_y, gp_z = 0.0, 0.0, 0.0
else:
    gp_x = gap_size.X
    gp_y = gap_size.Y
    gp_z = gap_size.Z

if noise_scale is None or noise_scale <= 0:
    noise_scale = 0.1
if octaves is None or octaves < 1:
    octaves = 3
if threshold is None:
    threshold = 0.5
if output_mode is None:
    output_mode = 1
if seed is None:
    seed = 42

if not attractors:
    attractors = []
if not repulsors:
    repulsors = []

if attract_radius is None or attract_radius <= 0:
    attract_radius = 10000.0
if attract_strength is None:
    attract_strength = 0.5
if repel_radius is None or repel_radius <= 0:
    repel_radius = 10000.0
if repel_strength is None:
    repel_strength = 0.5

tol = 0.01
try:
    tol = Rhino.RhinoDoc.ActiveDoc.ModelAbsoluteTolerance
except:
    pass


# ─── PERLIN INSTANCE ─────────────────────────────────────────────────
perlin = PerlinNoise(seed)

# ─── STEPS ─────────────────────────────────────────────────────────────
step_x = cs_x + gp_x
step_y = cs_y + gp_y
step_z = cs_z + gp_z

inv_attr_r = 1.0 / attract_radius if attract_radius > 0.001 else 0.0
inv_repel_r = 1.0 / repel_radius if repel_radius > 0.001 else 0.0
has_attractors = len(attractors) > 0
has_repulsors = len(repulsors) > 0


# ─── SAMPLE FIELD ─────────────────────────────────────────────────────
valid_cells = []   # (center, val, scale_factor)
centers = []
values = []

for ix in range(gx):
    for iy in range(gy):
        for iz in range(gz):
            # World position
            wx = origin.X + ix * step_x + cs_x * 0.5
            wy = origin.Y + iy * step_y + cs_y * 0.5
            wz = origin.Z + iz * step_z + cs_z * 0.5
            center = rg.Point3d(wx, wy, wz)

            # Boundary check
            if boundary_brep is not None:
                if not boundary_brep.IsPointInside(center, tol, False):
                    continue

            # Perlin noise
            nx = ix * noise_scale
            ny = iy * noise_scale
            nz = iz * noise_scale
            val = (perlin.octave_noise(nx, ny, nz, octaves) + 1.0) * 0.5

            # Attractor influence
            if has_attractors:
                for geo in attractors:
                    geo = unwrap(geo)
                    if geo is None:
                        continue
                    d = closest_dist(center, geo)
                    if d < attract_radius:
                        influence = (1.0 - d * inv_attr_r) * attract_strength
                        val += influence

            # Repulsor influence
            if has_repulsors:
                for geo in repulsors:
                    geo = unwrap(geo)
                    if geo is None:
                        continue
                    d = closest_dist(center, geo)
                    if d < repel_radius:
                        influence = (1.0 - d * inv_repel_r) * repel_strength
                        val -= influence

            # Clamp
            if val < 0.0:
                val = 0.0
            elif val > 1.0:
                val = 1.0

            # Threshold
            if val > threshold:
                valid_cells.append((center, val))
                centers.append(center)
                values.append(val)


# ─── BUILD OUTPUT ─────────────────────────────────────────────────────
voxels = []
count = len(valid_cells)

if output_mode == 0:
    # Points only — centers and values are already populated
    voxels = centers

elif output_mode == 1:
    # Combined mesh
    mesh = rg.Mesh()
    for center, val in valid_cells:
        hx = cs_x * 0.5
        hy = cs_y * 0.5
        hz = cs_z * 0.5
        corners = [
            rg.Point3d(center.X - hx, center.Y - hy, center.Z - hz),
            rg.Point3d(center.X + hx, center.Y - hy, center.Z - hz),
            rg.Point3d(center.X + hx, center.Y + hy, center.Z - hz),
            rg.Point3d(center.X - hx, center.Y + hy, center.Z - hz),
            rg.Point3d(center.X - hx, center.Y - hy, center.Z + hz),
            rg.Point3d(center.X + hx, center.Y - hy, center.Z + hz),
            rg.Point3d(center.X + hx, center.Y + hy, center.Z + hz),
            rg.Point3d(center.X - hx, center.Y + hy, center.Z + hz),
        ]
        b = mesh.Vertices.Count
        for pt in corners:
            mesh.Vertices.Add(pt)
        mesh.Faces.AddFace(b, b+1, b+2, b+3)
        mesh.Faces.AddFace(b+4, b+7, b+6, b+5)
        mesh.Faces.AddFace(b, b+4, b+5, b+1)
        mesh.Faces.AddFace(b+2, b+6, b+7, b+3)
        mesh.Faces.AddFace(b, b+3, b+7, b+4)
        mesh.Faces.AddFace(b+1, b+5, b+6, b+2)

    if mesh.Vertices.Count > 0:
        mesh.Normals.ComputeNormals()
        mesh.Compact()
        voxels.append(mesh)

elif output_mode == 2:
    # Individual breps
    for center, val in valid_cells:
        box = rg.Box(
            rg.Plane(center, rg.Vector3d.ZAxis),
            rg.Interval(-cs_x / 2, cs_x / 2),
            rg.Interval(-cs_y / 2, cs_y / 2),
            rg.Interval(-cs_z / 2, cs_z / 2),
        )
        brep = box.ToBrep()
        if brep:
            voxels.append(brep)


# ─── LOG ──────────────────────────────────────────────────────────────
log = "Perlin Field | Grid: {}x{}x{} | Scale: {} | Oct: {} | Thr: {:.2f} | Voxels: {} | Attr: {} | Repel: {} | Mode: {}".format(
    gx, gy, gz, noise_scale, octaves, threshold, count,
    len(attractors), len(repulsors), output_mode
)
