# Voxel Field Tool v02 — Multi-Field Edition
# Multiple field algorithms (Perlin, Gyroid, Schwarz-P, Diamond, SDF, Curl, RD, Composite)
# with real-time Eto UI, boid pathfinding, pipes, and melt
# Environment: Rhino 8 Python
# Inputs: Field algorithm selection, grid dimensions, field parameters, attractor points, base geometry
# Outputs: Voxel box geometry previewed live via display conduit

import Rhino
import Rhino.Geometry as rg
import Rhino.Display as rd
import scriptcontext as sc
import System
import System.Drawing
import math
import random

try:
    import Eto.Forms as forms
    import Eto.Drawing as drawing
except:
    import Rhino.UI
    forms = Rhino.UI.EtoExtensions


# ---------------------------------------------------------------------------
# Perlin Noise
# Deterministic 3D gradient noise generator. Produces smooth, continuous
# pseudorandom values in [-1, 1] that tile naturally. Used as the primary
# density field for voxel generation.
# ---------------------------------------------------------------------------
class PerlinNoise(object):
    def __init__(self, seed=0):
        """Build a shuffled permutation table from the given seed."""
        random.seed(seed)
        self.p = list(range(256))
        random.shuffle(self.p)
        self.p *= 2

    def noise3d(self, x, y, z):
        """Single-octave 3D Perlin noise with inlined fade/lerp/grad for speed."""
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
        """Layer multiple noise frequencies (octaves) for richer detail.
        Each octave doubles frequency and halves amplitude."""
        val = 0.0; freq = 1.0; amp = 1.0; max_amp = 0.0
        n3d = self.noise3d
        for _ in range(octaves):
            val += n3d(x * freq, y * freq, z * freq) * amp
            max_amp += amp; amp *= 0.5; freq *= 2.0
        return val / max_amp


# ---------------------------------------------------------------------------
# Field Algorithms
# Collection of scalar field evaluators. Each returns a value in [0, 1] for
# a given 3D position. The VoxelSystem dispatches to the selected algorithm.
# ---------------------------------------------------------------------------
class FieldAlgorithms(object):
    """Pluggable scalar field evaluators for voxel density generation."""

    def __init__(self):
        self.perlin = PerlinNoise(0)
        self.sdf_geometries = []
        self.pathway_geometries = []
        self.view_origin = None
        self.view_target = None
        self.rd_field = None
        self.dla_field = None
        self.dla_particles = 2000
        self.dla_stickiness = 1.0
        self.dla_bias_z = 0.0
        self.dla_seed_mode = 0
        self.sc_field = None
        self.sc_density = 0.3
        self.sc_kill_dist = 2
        self.sc_influence_radius = 5
        self.sc_step_length = 1
        self.sc_root_mode = 0
        self.sc_iterations = 200
        self.eden_field = None
        self.eden_birth = 2
        self.eden_survival_lo = 1
        self.eden_survival_hi = 6
        self.eden_iterations = 50
        self.eden_seed_density = 0.05
        self.eden_field_bias = 0.0
        self.phys_field = None
        self.phys_agents = 2000
        self.phys_sensor_angle = 45.0
        self.phys_sensor_dist = 3.0
        self.phys_turn_angle = 45.0
        self.phys_deposit = 1.0
        self.phys_decay = 0.1
        self.phys_iterations = 200
        self.growth_trails = []
        self.growth_points = []
        self.growth_playback_mode = None
        self.growth_all_trails = []
        self.growth_all_points = []
        self.growth_frame_indices = []
        self.growth_frame_snapshots = []
        self.growth_attractors = []
        self.growth_repellents = []
        self.growth_attract_radius = 10.0
        self.growth_attract_strength = 1.0
        self.growth_repel_radius = 10.0
        self.growth_repel_strength = 1.0
        self.growth_origin = None
        self.growth_cell_sizes = (1.0, 1.0, 1.0)
        self.myc_field = None
        self.myc_initial_tips = 10
        self.myc_branch_prob = 0.05
        self.myc_branch_angle = 45.0
        self.myc_turn_rate = 15.0
        self.myc_anastomosis = 0.5
        self.myc_iterations = 200
        self.myc_max_tips = 500

    def set_seed(self, seed):
        self.perlin = PerlinNoise(seed)

    def eval_perlin(self, nx, ny, nz, octaves):
        """Standard Perlin noise normalised to [0, 1]."""
        val = self.perlin.octave_noise(nx, ny, nz, octaves)
        return (val + 1.0) * 0.5

    def eval_gyroid(self, x, y, z, scale, thickness):
        """Gyroid TPMS: sin(x)*cos(y) + sin(y)*cos(z) + sin(z)*cos(x).
        Returns 1.0 on the surface, fading to 0.0 beyond thickness."""
        s = scale if scale > 1e-10 else 1.0
        sx = x / s; sy = y / s; sz = z / s
        val = (math.sin(sx) * math.cos(sy) +
               math.sin(sy) * math.cos(sz) +
               math.sin(sz) * math.cos(sx))
        th = thickness if thickness > 1e-10 else 0.1
        t = 1.0 - abs(val) / th
        if t < 0.0: t = 0.0
        elif t > 1.0: t = 1.0
        return t

    def eval_schwarzp(self, x, y, z, scale, thickness):
        """Schwarz Primitive: cos(x) + cos(y) + cos(z). Cubic lattice."""
        s = scale if scale > 1e-10 else 1.0
        sx = x / s; sy = y / s; sz = z / s
        val = math.cos(sx) + math.cos(sy) + math.cos(sz)
        th = thickness if thickness > 1e-10 else 0.1
        t = 1.0 - abs(val) / (th * 3.0)
        if t < 0.0: t = 0.0
        elif t > 1.0: t = 1.0
        return t

    def eval_diamond(self, x, y, z, scale, thickness):
        """Schwarz Diamond TPMS. Tetrahedral lattice."""
        s = scale if scale > 1e-10 else 1.0
        sx = x / s; sy = y / s; sz = z / s
        cs = math.cos(sx); cc = math.cos(sy); cz_v = math.cos(sz)
        ss = math.sin(sx); sc_v = math.sin(sy); sz_v = math.sin(sz)
        val = (ss * sc_v * sz_v + ss * cc * cz_v +
               cs * sc_v * cz_v + cs * cc * sz_v)
        th = thickness if thickness > 1e-10 else 0.1
        t = 1.0 - abs(val) / th
        if t < 0.0: t = 0.0
        elif t > 1.0: t = 1.0
        return t

    def eval_sdf(self, pt, falloff_dist, invert):
        """Signed distance field from assigned geometries."""
        if not self.sdf_geometries:
            return 0.5
        min_d = float('inf')
        for geo in self.sdf_geometries:
            d = self._closest_dist(pt, geo)
            if d < min_d:
                min_d = d
        fd = falloff_dist if falloff_dist > 1e-10 else 1.0
        val = 1.0 - min_d / fd
        if val < 0.0: val = 0.0
        elif val > 1.0: val = 1.0
        if invert:
            val = 1.0 - val
        return val

    def _closest_dist(self, pt, geo):
        """Shortest distance from pt to any Rhino geometry type."""
        try:
            if isinstance(geo, rg.Curve):
                rc, t = geo.ClosestPoint(pt)
                if rc: return pt.DistanceTo(geo.PointAt(t))
            elif isinstance(geo, rg.Mesh):
                cp = geo.ClosestPoint(pt)
                return pt.DistanceTo(cp)
            elif isinstance(geo, rg.Brep):
                cp = geo.ClosestPoint(pt)
                return pt.DistanceTo(cp)
            elif isinstance(geo, rg.Surface):
                rc, u, v = geo.ClosestPoint(pt)
                if rc: return pt.DistanceTo(geo.PointAt(u, v))
        except:
            pass
        return float('inf')

    def eval_curl_magnitude(self, x, y, z, scale, octaves):
        """Magnitude of curl of Perlin noise. Divergence-free, swirling patterns."""
        s = scale if scale > 1e-10 else 0.1
        eps = 0.01 * s
        n = self.perlin.octave_noise
        sx = x * s; sy = y * s; sz = z * s
        dndy = n(sx, sy + eps, sz, octaves) - n(sx, sy - eps, sz, octaves)
        dndz = n(sx, sy, sz + eps, octaves) - n(sx, sy, sz - eps, octaves)
        dndx = n(sx + eps, sy, sz, octaves) - n(sx - eps, sy, sz, octaves)
        dndy2 = n(sx+31.4, sy+eps, sz+7.1, octaves) - n(sx+31.4, sy-eps, sz+7.1, octaves)
        dndz2 = n(sx+31.4, sy, sz+eps+7.1, octaves) - n(sx+31.4, sy, sz-eps+7.1, octaves)
        dndx2 = n(sx+eps+31.4, sy, sz+7.1, octaves) - n(sx-eps+31.4, sy, sz+7.1, octaves)
        dndy3 = n(sx+53.7, sy+eps, sz+17.3, octaves) - n(sx+53.7, sy-eps, sz+17.3, octaves)
        dndz3 = n(sx+53.7, sy, sz+eps+17.3, octaves) - n(sx+53.7, sy, sz-eps+17.3, octaves)
        dndx3 = n(sx+eps+53.7, sy, sz+17.3, octaves) - n(sx-eps+53.7, sy, sz+17.3, octaves)
        inv2e = 1.0 / (2.0 * eps)
        cx = (dndz2 - dndy3) * inv2e
        cy = (dndx3 - dndz) * inv2e
        cz = (dndy - dndx2) * inv2e
        mag = math.sqrt(cx*cx + cy*cy + cz*cz)
        val = mag * 0.3
        if val > 1.0: val = 1.0
        return val

    def compute_reaction_diffusion(self, nx, ny, nz, feed, kill, da, db,
                                    iterations, seed):
        """Pre-compute 3D Gray-Scott reaction-diffusion field."""
        rng = random.Random(seed + 777)
        a = [[[1.0]*nz for _ in range(ny)] for _ in range(nx)]
        b = [[[0.0]*nz for _ in range(ny)] for _ in range(nx)]
        num_seeds = max(1, (nx * ny * nz) // 200)
        for _ in range(num_seeds):
            si = rng.randint(1, max(1, nx-2))
            sj = rng.randint(1, max(1, ny-2))
            sk = rng.randint(1, max(1, nz-2))
            for di in range(-1, 2):
                for dj in range(-1, 2):
                    for dk in range(-1, 2):
                        ni = si+di; nj = sj+dj; nk = sk+dk
                        if 0 <= ni < nx and 0 <= nj < ny and 0 <= nk < nz:
                            b[ni][nj][nk] = 1.0
        for _ in range(iterations):
            if sc.escape_test(False): break
            na = [[[0.0]*nz for _ in range(ny)] for _ in range(nx)]
            nb = [[[0.0]*nz for _ in range(ny)] for _ in range(nx)]
            for i in range(nx):
                ip = (i+1) % nx; im = (i-1) % nx
                for j in range(ny):
                    jp = (j+1) % ny; jm = (j-1) % ny
                    for k in range(nz):
                        kp = (k+1) % nz; km = (k-1) % nz
                        av = a[i][j][k]; bv = b[i][j][k]
                        lap_a = (a[ip][j][k]+a[im][j][k]+a[i][jp][k]+
                                 a[i][jm][k]+a[i][j][kp]+a[i][j][km]-6.0*av)
                        lap_b = (b[ip][j][k]+b[im][j][k]+b[i][jp][k]+
                                 b[i][jm][k]+b[i][j][kp]+b[i][j][km]-6.0*bv)
                        abb = av * bv * bv
                        na[i][j][k] = av + (da*lap_a - abb + feed*(1.0-av))
                        nb[i][j][k] = bv + (db*lap_b + abb - (kill+feed)*bv)
                        if na[i][j][k] < 0.0: na[i][j][k] = 0.0
                        elif na[i][j][k] > 1.0: na[i][j][k] = 1.0
                        if nb[i][j][k] < 0.0: nb[i][j][k] = 0.0
                        elif nb[i][j][k] > 1.0: nb[i][j][k] = 1.0
            a = na; b = nb
        self.rd_field = b

    def eval_reaction_diffusion(self, ix, iy, iz):
        """Sample pre-computed reaction-diffusion field."""
        if self.rd_field is None: return 0.0
        try: return self.rd_field[ix][iy][iz]
        except IndexError: return 0.0

    # -- Pathway Field ----------------------------------------------------
    def eval_pathway(self, pt, corridor_width, falloff, invert):
        """Density based on distance to pathway curves.
        Inside corridor_width: full effect. Falls off over falloff distance.
        Default (not inverted): high density near path (builds walls/enclosure).
        Inverted: low density near path (carves corridors through mass)."""
        if not self.pathway_geometries:
            return 0.5
        min_d = 1e12
        for geo in self.pathway_geometries:
            d = self._closest_dist(pt, geo)
            if d < min_d:
                min_d = d
        total_r = corridor_width + falloff
        if min_d >= total_r:
            val = 0.0
        elif min_d <= corridor_width:
            val = 1.0
        else:
            val = 1.0 - (min_d - corridor_width) / falloff
        if invert:
            val = 1.0 - val
        return val

    # -- Solar Exposure ---------------------------------------------------
    def eval_solar(self, cx, cy, cz, sun_az, sun_el, grid_cx, grid_cy,
                   grid_cz, grid_diag):
        """Solar exposure field based on projection onto sun direction.
        Voxels facing the sun get higher values. No ray casting — fast
        enough for real-time on large grids.
        sun_az: azimuth in radians (0=East, pi/2=North)
        sun_el: elevation in radians (0=horizon, pi/2=zenith)"""
        cos_el = math.cos(sun_el)
        sx = math.cos(sun_az) * cos_el
        sy = math.sin(sun_az) * cos_el
        sz = math.sin(sun_el)
        rx = cx - grid_cx
        ry = cy - grid_cy
        rz = cz - grid_cz
        proj = rx * sx + ry * sy + rz * sz
        if grid_diag > 0.001:
            val = (proj / (grid_diag * 0.5)) * 0.5 + 0.5
        else:
            val = 0.5
        if val < 0.0:
            val = 0.0
        elif val > 1.0:
            val = 1.0
        return val

    # -- View Corridor ----------------------------------------------------
    def eval_view_corridor(self, cx, cy, cz, radius, falloff):
        """Density field that carves a cylindrical corridor between two
        picked points (view_origin -> view_target). Voxels on the axis
        return 0 (carved), voxels beyond radius+falloff return 1 (solid)."""
        vo = self.view_origin
        vt = self.view_target
        if vo is None or vt is None:
            return 0.5
        ax = vt.X - vo.X
        ay = vt.Y - vo.Y
        az = vt.Z - vo.Z
        len_sq = ax * ax + ay * ay + az * az
        if len_sq < 0.001:
            return 0.5
        inv_len = 1.0 / (len_sq ** 0.5)
        ax *= inv_len; ay *= inv_len; az *= inv_len
        dx = cx - vo.X; dy = cy - vo.Y; dz = cz - vo.Z
        t = dx * ax + dy * ay + dz * az
        length = len_sq ** 0.5
        margin = falloff * 2.0
        if t < -margin or t > length + margin:
            return 1.0
        px = dx - t * ax; py = dy - t * ay; pz = dz - t * az
        perp = (px * px + py * py + pz * pz) ** 0.5
        if perp <= radius:
            return 0.0
        elif perp <= radius + falloff:
            return (perp - radius) / falloff
        return 1.0

    # -- Gravity Gradient -------------------------------------------------
    def eval_gravity(self, cz, origin_z, grid_height, grav_mode, strength):
        """Height-based density gradient for structural expression.
        grav_mode 0: linear (dense base)
        grav_mode 1: quadratic (super-dense base)
        grav_mode 2: inverse (dense top / canopy)
        grav_mode 3: bell (dense middle band)"""
        if grid_height < 0.001:
            return 0.5
        t = (cz - origin_z) / grid_height
        if t < 0.0:
            t = 0.0
        elif t > 1.0:
            t = 1.0
        if grav_mode == 0:
            val = 1.0 - t
        elif grav_mode == 1:
            val = (1.0 - t) * (1.0 - t)
        elif grav_mode == 2:
            val = t
        elif grav_mode == 3:
            val = math.exp(-((t - 0.5) ** 2) / 0.08)
        else:
            val = 0.5
        val = 0.5 + (val - 0.5) * strength
        if val < 0.0:
            val = 0.0
        elif val > 1.0:
            val = 1.0
        return val

    def composite(self, val_a, val_b, mode, weight):
        """Blend two field values. Modes: 0=Add, 1=Multiply, 2=Max, 3=Min,
        4=Smooth Union, 5=Subtract."""
        w = weight; iw = 1.0 - w
        if mode == 0: val = val_a * iw + val_b * w
        elif mode == 1: val = val_a * val_b
        elif mode == 2: val = max(val_a, val_b)
        elif mode == 3: val = min(val_a, val_b)
        elif mode == 4:
            k = 0.3
            h = max(k - abs(val_a - val_b), 0.0) / k
            val = max(val_a, val_b) + h*h*h*k/6.0
        elif mode == 5: val = val_a - val_b * w
        else: val = val_a
        if val < 0.0: val = 0.0
        elif val > 1.0: val = 1.0
        return val

    # -- DLA (Diffusion-Limited Aggregation) --------------------------------

    def compute_dla(self, nx, ny, nz, seed):
        """Particles random-walk and stick on contact with existing solid,
        producing branching coral/lightning structures."""
        rng = random.Random(seed + 1313)
        field = [[[0.0] * nz for _ in range(ny)] for _ in range(nx)]
        solid = set()
        dirs6 = ((1, 0, 0), (-1, 0, 0), (0, 1, 0),
                 (0, -1, 0), (0, 0, 1), (0, 0, -1))

        mode = self.dla_seed_mode
        if mode == 0:
            cx, cy, cz = nx // 2, ny // 2, nz // 2
            solid.add((cx, cy, cz))
        elif mode == 1:
            solid.add((nx // 2, ny // 2, 0))
        elif mode == 2:
            for x in range(nx):
                for y in range(ny):
                    solid.add((x, y, 0))
        else:
            cnt = max(1, (nx * ny * nz) // 500)
            for _ in range(cnt):
                solid.add((rng.randint(0, nx - 1),
                           rng.randint(0, ny - 1),
                           rng.randint(0, nz - 1)))

        bias = self.dla_bias_z
        stick = self.dla_stickiness
        max_walk = (nx + ny + nz) * 2
        trails = []
        points = []
        max_frames = 100
        n_particles = self.dla_particles
        frame_step = max(1, n_particles // max_frames)
        frame_indices = [(0, 0)]
        stuck_count = 0
        influence = self._growth_influence_field(nx, ny, nz)
        has_inf = bool(self.growth_attractors) or bool(self.growth_repellents)

        for _pi in range(n_particles):
            if sc.escape_test(False):
                break
            px = rng.randint(0, nx - 1)
            py = rng.randint(0, ny - 1)
            pz = rng.randint(0, nz - 1)
            if (px, py, pz) in solid:
                continue
            path = [(px, py, pz)]
            stuck = False
            for _step in range(max_walk):
                adjacent = False
                for dx, dy, dz in dirs6:
                    if (px + dx, py + dy, pz + dz) in solid:
                        adjacent = True
                        break
                if adjacent and rng.random() < stick:
                    solid.add((px, py, pz))
                    stuck = True
                    break
                choices = []
                total_w = 0.0
                for dx, dy, dz in dirs6:
                    ni, nj, nk = px + dx, py + dy, pz + dz
                    if 0 <= ni < nx and 0 <= nj < ny and 0 <= nk < nz:
                        if (ni, nj, nk) not in solid:
                            w = 1.0 + dz * bias
                            if has_inf:
                                w *= max(0.05, 1.0 + influence[ni][nj][nk])
                            if w < 0.05:
                                w = 0.05
                            choices.append((ni, nj, nk, w))
                            total_w += w
                if not choices:
                    break
                r = rng.random() * total_w
                cum = 0.0
                for ni, nj, nk, w in choices:
                    cum += w
                    if r <= cum:
                        px, py, pz = ni, nj, nk
                        break
                path.append((px, py, pz))
            if stuck:
                stuck_count += 1
                points.append((px, py, pz))
                if len(path) >= 2:
                    trails.append(path)
                if stuck_count % frame_step == 0:
                    frame_indices.append((len(trails), len(points)))

        if not frame_indices or frame_indices[-1] != (len(trails), len(points)):
            frame_indices.append((len(trails), len(points)))

        for (sx, sy, sz) in solid:
            field[sx][sy][sz] = 1.0
        self.dla_field = field
        self.growth_trails = trails
        self.growth_points = points
        self.growth_playback_mode = "cumulative"
        self.growth_all_trails = trails
        self.growth_all_points = points
        self.growth_frame_indices = frame_indices

    def eval_dla(self, ix, iy, iz):
        if self.dla_field is None:
            return 0.0
        try:
            return self.dla_field[ix][iy][iz]
        except IndexError:
            return 0.0

    # -- Space Colonization ------------------------------------------------

    def compute_space_colonization(self, nx, ny, nz, seed):
        """Attractor-driven branching growth. Scatters nutrient points, grows
        branches from a root toward the nearest attractors. Produces tree-like
        structures that efficiently fill a volume."""
        rng = random.Random(seed + 1414)
        field = [[[0.0] * nz for _ in range(ny)] for _ in range(nx)]
        _sqrt = math.sqrt

        influence = self._growth_influence_field(nx, ny, nz)
        has_inf = bool(self.growth_attractors) or bool(self.growth_repellents)
        num_attr = max(1, int(nx * ny * nz * self.sc_density))
        attractors = set()
        for _ in range(num_attr):
            ax = rng.randint(0, nx - 1)
            ay = rng.randint(0, ny - 1)
            az = rng.randint(0, nz - 1)
            if has_inf and influence[ax][ay][az] < -0.5:
                continue
            attractors.add((ax, ay, az))
        if has_inf:
            bonus = int(num_attr * 0.3)
            for _ in range(bonus):
                bx = rng.randint(0, nx - 1)
                by = rng.randint(0, ny - 1)
                bz = rng.randint(0, nz - 1)
                if influence[bx][by][bz] > 0.3:
                    attractors.add((bx, by, bz))

        branches = set()
        mode = self.sc_root_mode
        if mode == 0:
            branches.add((nx // 2, ny // 2, 0))
        elif mode == 1:
            branches.add((nx // 2, ny // 2, nz // 2))
        elif mode == 2:
            branches.add((nx // 2, ny // 2, 0))
            branches.add((0, 0, 0))
            branches.add((nx - 1, 0, 0))
            branches.add((0, ny - 1, 0))
            branches.add((nx - 1, ny - 1, 0))
        else:
            branches.add((rng.randint(0, nx - 1),
                          rng.randint(0, ny - 1),
                          rng.randint(0, nz - 1)))

        kill_sq = self.sc_kill_dist * self.sc_kill_dist
        inf_sq = self.sc_influence_radius * self.sc_influence_radius
        step = max(1, int(round(self.sc_step_length)))
        frontier = set(branches)
        edges = []
        branch_list = list(branches)
        frame_indices = [(0, len(branch_list))]

        for _it in range(self.sc_iterations):
            if sc.escape_test(False):
                break
            if not attractors:
                break

            influence = {}
            consumed = set()

            for attr in attractors:
                best_br = None
                best_sq = float('inf')
                for br in frontier:
                    dx = attr[0] - br[0]
                    dy = attr[1] - br[1]
                    dz = attr[2] - br[2]
                    d_sq = dx * dx + dy * dy + dz * dz
                    if d_sq < best_sq:
                        best_sq = d_sq
                        best_br = br
                if best_br is None:
                    for br in branches:
                        dx = attr[0] - br[0]
                        dy = attr[1] - br[1]
                        dz = attr[2] - br[2]
                        d_sq = dx * dx + dy * dy + dz * dz
                        if d_sq < best_sq:
                            best_sq = d_sq
                            best_br = br
                if best_sq <= kill_sq:
                    consumed.add(attr)
                if best_sq <= inf_sq and best_br is not None:
                    if best_br not in influence:
                        influence[best_br] = []
                    influence[best_br].append(attr)

            attractors -= consumed

            new_branches = set()
            for br, attrs in influence.items():
                dx, dy, dz = 0.0, 0.0, 0.0
                for a in attrs:
                    dx += a[0] - br[0]
                    dy += a[1] - br[1]
                    dz += a[2] - br[2]
                length = _sqrt(dx * dx + dy * dy + dz * dz)
                if length < 0.001:
                    continue
                inv_l = step / length
                ni = int(round(br[0] + dx * inv_l))
                nj = int(round(br[1] + dy * inv_l))
                nk = int(round(br[2] + dz * inv_l))
                ni = max(0, min(nx - 1, ni))
                nj = max(0, min(ny - 1, nj))
                nk = max(0, min(nz - 1, nk))
                if (ni, nj, nk) not in branches:
                    new_branches.add((ni, nj, nk))
                    edges.append([br, (ni, nj, nk)])
                    branch_list.append((ni, nj, nk))

            for nb in new_branches:
                branches.add(nb)
            frontier = new_branches if new_branches else frontier
            cur = (len(edges), len(branch_list))
            if cur != frame_indices[-1]:
                frame_indices.append(cur)

        final = (len(edges), len(branch_list))
        if frame_indices[-1] != final:
            frame_indices.append(final)

        for (bx, by, bz) in branches:
            field[bx][by][bz] = 1.0
        self.sc_field = field
        self.growth_trails = edges
        self.growth_points = branch_list
        self.growth_playback_mode = "cumulative"
        self.growth_all_trails = edges
        self.growth_all_points = branch_list
        self.growth_frame_indices = frame_indices

    def eval_space_colonization(self, ix, iy, iz):
        if self.sc_field is None:
            return 0.0
        try:
            return self.sc_field[ix][iy][iz]
        except IndexError:
            return 0.0

    # -- Eden Growth (3D Cellular Automata) --------------------------------

    def compute_eden(self, nx, ny, nz, seed):
        """3D cellular automaton growth. Cells are born when they have enough
        live neighbors, and survive within a neighbor-count band. Optional
        Perlin noise bias makes growth favor certain regions."""
        rng = random.Random(seed + 1515)
        dirs6 = ((1, 0, 0), (-1, 0, 0), (0, 1, 0),
                 (0, -1, 0), (0, 0, 1), (0, 0, -1))

        alive = set()
        num_seeds = max(1, int(nx * ny * nz * self.eden_seed_density))
        for _ in range(num_seeds):
            alive.add((rng.randint(0, nx - 1),
                        rng.randint(0, ny - 1),
                        rng.randint(0, nz - 1)))

        influence = self._growth_influence_field(nx, ny, nz)
        has_inf = bool(self.growth_attractors) or bool(self.growth_repellents)
        birth = self.eden_birth
        surv_lo = self.eden_survival_lo
        surv_hi = self.eden_survival_hi
        field_bias = self.eden_field_bias
        use_bias = field_bias > 0.001
        n_iters = self.eden_iterations
        max_frames = 100
        frame_step = max(1, n_iters // max_frames)
        frame_snapshots = [([], list(alive))]

        for _it in range(n_iters):
            if sc.escape_test(False):
                break
            candidates = set()
            for cell in alive:
                candidates.add(cell)
                for dx, dy, dz in dirs6:
                    ni, nj, nk = cell[0] + dx, cell[1] + dy, cell[2] + dz
                    if 0 <= ni < nx and 0 <= nj < ny and 0 <= nk < nz:
                        candidates.add((ni, nj, nk))

            new_alive = set()
            for cell in candidates:
                x, y, z = cell
                count = 0
                for dx, dy, dz in dirs6:
                    if (x + dx, y + dy, z + dz) in alive:
                        count += 1
                if cell in alive:
                    if surv_lo <= count <= surv_hi:
                        new_alive.add(cell)
                else:
                    if count >= birth:
                        prob = 1.0
                        if use_bias:
                            nv = self.perlin.octave_noise(
                                x * 0.15, y * 0.15, z * 0.15, 2)
                            prob = (0.5 + nv * 0.5) * field_bias + (1.0 - field_bias)
                        if has_inf:
                            inf_v = influence[x][y][z]
                            if inf_v < -0.3:
                                prob *= max(0.0, 1.0 + inf_v)
                            elif inf_v > 0.1:
                                prob = min(1.0, prob * (1.0 + inf_v))
                        if rng.random() > prob:
                            continue
                        new_alive.add(cell)
            alive = new_alive
            if (_it + 1) % frame_step == 0 or _it == n_iters - 1:
                frame_snapshots.append(([], list(alive)))

        field = [[[0.0] * nz for _ in range(ny)] for _ in range(nx)]
        for (ax, ay, az) in alive:
            field[ax][ay][az] = 1.0
        self.eden_field = field
        self.growth_trails = []
        self.growth_points = list(alive)
        self.growth_playback_mode = "snapshot"
        self.growth_frame_snapshots = frame_snapshots

    def eval_eden(self, ix, iy, iz):
        if self.eden_field is None:
            return 0.0
        try:
            return self.eden_field[ix][iy][iz]
        except IndexError:
            return 0.0

    # -- Physarum (Slime Mold) ---------------------------------------------

    def compute_physarum(self, nx, ny, nz, seed):
        """3D slime mold simulation. Agents sense trail concentration ahead,
        turn toward higher values, deposit trail, and trail decays. Produces
        efficient network structures."""
        rng = random.Random(seed + 1616)
        _sin = math.sin
        _cos = math.cos
        _sqrt = math.sqrt
        _acos = math.acos
        _pi = math.pi

        trail = [[[0.0] * nz for _ in range(ny)] for _ in range(nx)]

        sa_rad = self.phys_sensor_angle * _pi / 180.0
        ta_rad = self.phys_turn_angle * _pi / 180.0
        sd = self.phys_sensor_dist
        deposit = self.phys_deposit
        decay = 1.0 - self.phys_decay

        agents = []
        agent_paths = []
        for _ in range(self.phys_agents):
            px = rng.random() * (nx - 1)
            py = rng.random() * (ny - 1)
            pz = rng.random() * (nz - 1)
            theta = rng.random() * 2.0 * _pi
            phi = _acos(max(-1.0, min(1.0, 1.0 - 2.0 * rng.random())))
            dx = _sin(phi) * _cos(theta)
            dy = _sin(phi) * _sin(theta)
            dz = _cos(phi)
            agents.append([px, py, pz, dx, dy, dz])
            agent_paths.append([(px, py, pz)])

        influence = self._growth_influence_field(nx, ny, nz)
        has_inf = bool(self.growth_attractors) or bool(self.growth_repellents)
        if has_inf:
            for ix_i in range(nx):
                for iy_i in range(ny):
                    for iz_i in range(nz):
                        v = influence[ix_i][iy_i][iz_i]
                        if v > 0.0:
                            trail[ix_i][iy_i][iz_i] += v * deposit * 2.0

        sample_rate = max(1, self.phys_iterations // 50)
        frame_snapshots = []

        def sample(x, y, z):
            ix = int(x)
            iy = int(y)
            iz = int(z)
            if 0 <= ix < nx and 0 <= iy < ny and 0 <= iz < nz:
                return trail[ix][iy][iz]
            return 0.0

        def perp_vec(dx, dy, dz):
            if abs(dz) < 0.9:
                rx = -dz
                ry = 0.0
                rz = dx
            else:
                rx = 0.0
                ry = dz
                rz = -dy
            rl = _sqrt(rx * rx + ry * ry + rz * rz)
            if rl < 1e-10:
                return 1.0, 0.0, 0.0
            return rx / rl, ry / rl, rz / rl

        cos_sa = _cos(sa_rad)
        sin_sa = _sin(sa_rad)
        cos_ta = _cos(ta_rad)
        sin_ta = _sin(ta_rad)

        for _it in range(self.phys_iterations):
            if sc.escape_test(False):
                break
            record = (_it % sample_rate == 0)

            for ai, ag in enumerate(agents):
                px, py, pz = ag[0], ag[1], ag[2]
                hdx, hdy, hdz = ag[3], ag[4], ag[5]

                rx, ry, rz = perp_vec(hdx, hdy, hdz)

                fx = px + hdx * sd
                fy = py + hdy * sd
                fz = pz + hdz * sd
                f_val = sample(fx, fy, fz)

                ldx = hdx * cos_sa + rx * sin_sa
                ldy = hdy * cos_sa + ry * sin_sa
                ldz = hdz * cos_sa + rz * sin_sa
                l_val = sample(px + ldx * sd, py + ldy * sd, pz + ldz * sd)

                rdx = hdx * cos_sa - rx * sin_sa
                rdy = hdy * cos_sa - ry * sin_sa
                rdz = hdz * cos_sa - rz * sin_sa
                r_val = sample(px + rdx * sd, py + rdy * sd, pz + rdz * sd)

                if f_val >= l_val and f_val >= r_val:
                    pass
                elif l_val > r_val:
                    hdx = hdx * cos_ta + rx * sin_ta
                    hdy = hdy * cos_ta + ry * sin_ta
                    hdz = hdz * cos_ta + rz * sin_ta
                elif r_val > l_val:
                    hdx = hdx * cos_ta - rx * sin_ta
                    hdy = hdy * cos_ta - ry * sin_ta
                    hdz = hdz * cos_ta - rz * sin_ta
                else:
                    if rng.random() < 0.5:
                        hdx = hdx * cos_ta + rx * sin_ta
                        hdy = hdy * cos_ta + ry * sin_ta
                        hdz = hdz * cos_ta + rz * sin_ta
                    else:
                        hdx = hdx * cos_ta - rx * sin_ta
                        hdy = hdy * cos_ta - ry * sin_ta
                        hdz = hdz * cos_ta - rz * sin_ta

                hl = _sqrt(hdx * hdx + hdy * hdy + hdz * hdz)
                if hl > 1e-10:
                    hdx /= hl
                    hdy /= hl
                    hdz /= hl

                px += hdx
                py += hdy
                pz += hdz
                px = px % nx
                py = py % ny
                pz = pz % nz

                ix2 = int(px)
                iy2 = int(py)
                iz2 = int(pz)
                if 0 <= ix2 < nx and 0 <= iy2 < ny and 0 <= iz2 < nz:
                    trail[ix2][iy2][iz2] += deposit

                ag[0] = px
                ag[1] = py
                ag[2] = pz
                ag[3] = hdx
                ag[4] = hdy
                ag[5] = hdz
                if record:
                    agent_paths[ai].append((px, py, pz))

            if record:
                frame_snapshots.append(
                    ([], [(ag[0], ag[1], ag[2]) for ag in agents]))

            for i in range(nx):
                for j in range(ny):
                    for k in range(nz):
                        trail[i][j][k] *= decay

        max_val = 0.0
        for i in range(nx):
            for j in range(ny):
                for k in range(nz):
                    if trail[i][j][k] > max_val:
                        max_val = trail[i][j][k]
        if max_val > 0.001:
            inv_max = 1.0 / max_val
            for i in range(nx):
                for j in range(ny):
                    for k in range(nz):
                        trail[i][j][k] *= inv_max

        self.phys_field = trail
        final_trails = [p for p in agent_paths if len(p) >= 2]
        final_points = [(ag[0], ag[1], ag[2]) for ag in agents]
        self.growth_trails = final_trails
        self.growth_points = final_points
        frame_snapshots.append((final_trails, final_points))
        self.growth_playback_mode = "snapshot"
        self.growth_frame_snapshots = frame_snapshots

    def eval_physarum(self, ix, iy, iz):
        if self.phys_field is None:
            return 0.0
        try:
            return self.phys_field[ix][iy][iz]
        except IndexError:
            return 0.0

    # -- Growth influence field --------------------------------------------

    def _growth_influence_field(self, nx, ny, nz):
        """Pre-compute a 3D influence grid from attractor/repellent geometries.
        Returns values in [-1, 1]: positive=attract, negative=repel.
        Requires growth_origin and growth_cell_sizes to be set first."""
        inf = [[[0.0] * nz for _ in range(ny)] for _ in range(nx)]
        has_a = bool(self.growth_attractors)
        has_r = bool(self.growth_repellents)
        if not has_a and not has_r:
            return inf
        if self.growth_origin is None:
            return inf
        ox = self.growth_origin.X
        oy = self.growth_origin.Y
        oz = self.growth_origin.Z
        cw, cl, ch = self.growth_cell_sizes
        hw, hl, hh = cw * 0.5, cl * 0.5, ch * 0.5
        a_rad = self.growth_attract_radius
        a_str = self.growth_attract_strength
        r_rad = self.growth_repel_radius
        r_str = self.growth_repel_strength
        inv_a = 1.0 / a_rad if a_rad > 0.001 else 0.0
        inv_r = 1.0 / r_rad if r_rad > 0.001 else 0.0
        _Pt = rg.Point3d
        _cd = self._closest_dist
        for ix in range(nx):
            wx = ox + ix * cw + hw
            for iy in range(ny):
                wy = oy + iy * cl + hl
                for iz in range(nz):
                    wz = oz + iz * ch + hh
                    pt = _Pt(wx, wy, wz)
                    val = 0.0
                    if has_a:
                        for geo in self.growth_attractors:
                            d = _cd(pt, geo)
                            t = d * inv_a
                            if t < 1.0:
                                val += (1.0 - t) * a_str
                    if has_r:
                        for geo in self.growth_repellents:
                            d = _cd(pt, geo)
                            t = d * inv_r
                            if t < 1.0:
                                val -= (1.0 - t) * r_str
                    if val > 1.0:
                        val = 1.0
                    elif val < -1.0:
                        val = -1.0
                    inf[ix][iy][iz] = val
        return inf

    # -- Mycelium Growth ---------------------------------------------------

    def compute_mycelium(self, nx, ny, nz, seed):
        """Fungal network growth. Tips extend forward with random deviation,
        branch probabilistically, and fuse on contact (anastomosis). Produces
        dense, branching filament networks."""
        rng = random.Random(seed + 1717)
        _sqrt = math.sqrt
        _pi = math.pi
        _sin = math.sin
        _cos = math.cos
        _acos = math.acos
        field = [[[0.0] * nz for _ in range(ny)] for _ in range(nx)]
        solid = set()
        influence = self._growth_influence_field(nx, ny, nz)
        has_inf = bool(self.growth_attractors) or bool(self.growth_repellents)

        trails = []
        all_points = []
        branch_prob = self.myc_branch_prob
        branch_angle = self.myc_branch_angle * _pi / 180.0
        turn_rate = self.myc_turn_rate * _pi / 180.0
        anast = self.myc_anastomosis
        max_tips = self.myc_max_tips
        n_iters = self.myc_iterations

        tips = []
        for _ in range(self.myc_initial_tips):
            x = rng.randint(1, max(1, nx - 2))
            y = rng.randint(1, max(1, ny - 2))
            z = rng.randint(1, max(1, nz - 2))
            theta = rng.random() * 2.0 * _pi
            phi = _acos(max(-1.0, min(1.0, 1.0 - 2.0 * rng.random())))
            dx = _sin(phi) * _cos(theta)
            dy = _sin(phi) * _sin(theta)
            dz = _cos(phi)
            trail_idx = len(trails)
            trails.append([(x, y, z)])
            tips.append([float(x), float(y), float(z),
                         dx, dy, dz, True, trail_idx])
            solid.add((x, y, z))
            all_points.append((x, y, z))

        max_frames = 100
        frame_step = max(1, n_iters // max_frames)
        frame_indices = [(len(trails), len(all_points))]

        dirs6 = ((1, 0, 0), (-1, 0, 0), (0, 1, 0),
                 (0, -1, 0), (0, 0, 1), (0, 0, -1))

        for _it in range(n_iters):
            if sc.escape_test(False):
                break
            active_count = 0
            for t in tips:
                if t[6]:
                    active_count += 1
            if active_count == 0:
                break

            new_tips = []
            for tip in tips:
                if not tip[6]:
                    continue
                px, py, pz = tip[0], tip[1], tip[2]
                dx, dy, dz = tip[3], tip[4], tip[5]

                ic = int(round(px))
                jc = int(round(py))
                kc = int(round(pz))
                if has_inf and 0 <= ic < nx and 0 <= jc < ny and 0 <= kc < nz:
                    gx, gy, gz = 0.0, 0.0, 0.0
                    for ddx, ddy, ddz in dirs6:
                        ni = ic + ddx
                        nj = jc + ddy
                        nk = kc + ddz
                        if 0 <= ni < nx and 0 <= nj < ny and 0 <= nk < nz:
                            v = influence[ni][nj][nk]
                            gx += ddx * v
                            gy += ddy * v
                            gz += ddz * v
                    gl = _sqrt(gx * gx + gy * gy + gz * gz)
                    if gl > 0.01:
                        gx /= gl
                        gy /= gl
                        gz /= gl
                        w = 0.3
                        dx = dx * (1.0 - w) + gx * w
                        dy = dy * (1.0 - w) + gy * w
                        dz = dz * (1.0 - w) + gz * w

                dx += rng.gauss(0, 1) * turn_rate
                dy += rng.gauss(0, 1) * turn_rate
                dz += rng.gauss(0, 1) * turn_rate
                dl = _sqrt(dx * dx + dy * dy + dz * dz)
                if dl > 1e-10:
                    dx /= dl
                    dy /= dl
                    dz /= dl

                npx = px + dx
                npy = py + dy
                npz = pz + dz
                nix = int(round(npx))
                niy = int(round(npy))
                niz = int(round(npz))

                if not (0 <= nix < nx and 0 <= niy < ny and 0 <= niz < nz):
                    tip[6] = False
                    continue

                if (nix, niy, niz) in solid:
                    if rng.random() < anast:
                        trails[tip[7]].append((nix, niy, niz))
                        tip[6] = False
                        continue
                    found = False
                    for ddx, ddy, ddz in dirs6:
                        ci = nix + ddx
                        cj = niy + ddy
                        ck = niz + ddz
                        if (0 <= ci < nx and 0 <= cj < ny and 0 <= ck < nz
                                and (ci, cj, ck) not in solid):
                            nix, niy, niz = ci, cj, ck
                            npx, npy, npz = float(ci), float(cj), float(ck)
                            found = True
                            break
                    if not found:
                        tip[6] = False
                        continue

                solid.add((nix, niy, niz))
                all_points.append((nix, niy, niz))
                trails[tip[7]].append((nix, niy, niz))
                tip[0] = npx
                tip[1] = npy
                tip[2] = npz
                tip[3] = dx
                tip[4] = dy
                tip[5] = dz

                if active_count + len(new_tips) < max_tips:
                    if rng.random() < branch_prob:
                        prx, pry, prz = 0.0, 0.0, 0.0
                        if abs(dz) < 0.9:
                            prx, pry, prz = -dz, 0.0, dx
                        else:
                            prx, pry, prz = 0.0, dz, -dy
                        pl = _sqrt(prx * prx + pry * pry + prz * prz)
                        if pl > 1e-10:
                            prx /= pl
                            pry /= pl
                            prz /= pl
                        az = rng.random() * 2.0 * _pi
                        cx_v = dy * prz - dz * pry
                        cy_v = dz * prx - dx * prz
                        cz_v = dx * pry - dy * prx
                        cos_az = _cos(az)
                        sin_az = _sin(az)
                        rpx = cos_az * prx + sin_az * cx_v
                        rpy = cos_az * pry + sin_az * cy_v
                        rpz = cos_az * prz + sin_az * cz_v
                        cos_ba = _cos(branch_angle)
                        sin_ba = _sin(branch_angle)
                        bdx = dx * cos_ba + rpx * sin_ba
                        bdy = dy * cos_ba + rpy * sin_ba
                        bdz = dz * cos_ba + rpz * sin_ba
                        bl = _sqrt(bdx * bdx + bdy * bdy + bdz * bdz)
                        if bl > 1e-10:
                            bdx /= bl
                            bdy /= bl
                            bdz /= bl
                        new_trail_idx = len(trails) + len(new_tips)
                        new_tips.append([npx, npy, npz,
                                         bdx, bdy, bdz, True, new_trail_idx])

            for nt in new_tips:
                nt[7] = len(trails)
                trails.append([(int(round(nt[0])),
                                int(round(nt[1])),
                                int(round(nt[2])))])
                tips.append(nt)

            if (_it + 1) % frame_step == 0 or _it == n_iters - 1:
                cur = (len(trails), len(all_points))
                if not frame_indices or frame_indices[-1] != cur:
                    frame_indices.append(cur)

        final = (len(trails), len(all_points))
        if frame_indices[-1] != final:
            frame_indices.append(final)

        for (sx, sy, sz) in solid:
            field[sx][sy][sz] = 1.0
        self.myc_field = field
        self.growth_trails = trails
        self.growth_points = all_points
        self.growth_playback_mode = "cumulative"
        self.growth_all_trails = trails
        self.growth_all_points = all_points
        self.growth_frame_indices = frame_indices

    def eval_mycelium(self, ix, iy, iz):
        if self.myc_field is None:
            return 0.0
        try:
            return self.myc_field[ix][iy][iz]
        except IndexError:
            return 0.0


# ---------------------------------------------------------------------------
# Display Conduit
# Rhino DisplayConduit subclass that draws all preview geometry (voxel mesh,
# pipe mesh, melt mesh, boid trails, bounding box) directly into the viewport
# without baking to the document. Toggled on/off via .Enabled.
# ---------------------------------------------------------------------------
class VoxelConduit(rd.DisplayConduit):
    def __init__(self):
        super(VoxelConduit, self).__init__()
        self.mesh = None
        self.edge_mesh = None
        self.bbox = rg.BoundingBox.Empty
        self.bound_lines = []
        self.bound_color = System.Drawing.Color.FromArgb(80, 80, 80)
        self.edge_color = System.Drawing.Color.FromArgb(40, 40, 40)
        self.show_bounds = True
        self.show_edges = True
        self.trail_polylines = []
        self.trail_color = System.Drawing.Color.FromArgb(255, 120, 50)
        self.trail_thickness = 2
        self.show_trails = False
        self.use_vertex_colors = True
        self.shaded_material = rd.DisplayMaterial()
        self.pipe_mesh = None
        self.pipe_material = rd.DisplayMaterial()
        self.show_pipes = False
        self.melt_mesh = None
        self.show_melt = False
        self.view_origin = None
        self.view_target = None
        self.show_view_origin = True
        self.show_view_target = True
        self.show_view_line = True
        self.view_origin_color = System.Drawing.Color.FromArgb(0, 200, 80)
        self.view_target_color = System.Drawing.Color.FromArgb(255, 60, 60)
        self.view_line_color = System.Drawing.Color.FromArgb(255, 200, 0)
        self.view_point_size = 12
        self.growth_trails = []
        self.growth_points = []
        self.show_growth_trails = False
        self.show_growth_points = False
        self.hide_voxels_for_growth = False
        self.growth_trail_color = System.Drawing.Color.FromArgb(120, 220, 80)
        self.growth_point_color = System.Drawing.Color.FromArgb(255, 200, 50)
        self.growth_trail_thickness = 2
        self.growth_point_size = 4

    def CalculateBoundingBox(self, e):
        """Expand the viewport clipping box to include all displayed geometry."""
        if self.bbox.IsValid:
            e.IncludeBoundingBox(self.bbox)
        if self.pipe_mesh and self.pipe_mesh.Vertices.Count > 0:
            e.IncludeBoundingBox(self.pipe_mesh.GetBoundingBox(False))
        if self.melt_mesh and self.melt_mesh.Vertices.Count > 0:
            e.IncludeBoundingBox(self.melt_mesh.GetBoundingBox(False))
        if self.view_origin:
            e.IncludeBoundingBox(rg.BoundingBox(self.view_origin, self.view_origin))
        if self.view_target:
            e.IncludeBoundingBox(rg.BoundingBox(self.view_target, self.view_target))

    def PostDrawObjects(self, e):
        """Draw geometry each frame. Priority: melt mesh > (voxel mesh + pipes) > trails > bounds.
        Voxel mesh rendered as false-colour (density) or shaded (flat) based on toggle."""
        skip_voxels = self.hide_voxels_for_growth and (
            self.show_growth_trails or self.show_growth_points)
        if self.show_melt and self.melt_mesh and self.melt_mesh.Vertices.Count > 0:
            e.Display.DrawMeshShaded(self.melt_mesh, self.shaded_material)
            if self.show_edges:
                e.Display.DrawMeshWires(self.melt_mesh, self.edge_color)
        elif not skip_voxels:
            if self.mesh and self.mesh.Vertices.Count > 0:
                if self.use_vertex_colors:
                    e.Display.DrawMeshFalseColors(self.mesh)
                else:
                    e.Display.DrawMeshShaded(self.mesh, self.shaded_material)
                if self.show_edges:
                    wire = self.edge_mesh if self.edge_mesh else self.mesh
                    e.Display.DrawMeshWires(wire, self.edge_color)
            if self.show_pipes and self.pipe_mesh and self.pipe_mesh.Vertices.Count > 0:
                e.Display.DrawMeshShaded(self.pipe_mesh, self.pipe_material)
        if self.show_trails and self.trail_polylines:
            for pl in self.trail_polylines:
                e.Display.DrawPolyline(pl, self.trail_color, self.trail_thickness)
        if self.show_growth_trails and self.growth_trails:
            gc = self.growth_trail_color
            gt = self.growth_trail_thickness
            try:
                for pl in self.growth_trails:
                    e.Display.DrawPolyline(pl, gc, gt)
            except Exception:
                pass
        if self.show_growth_points and self.growth_points:
            gpc = self.growth_point_color
            gps = self.growth_point_size
            try:
                e.Display.DrawPoints(
                    self.growth_points,
                    rd.PointStyle.RoundControlPoint, gps, gpc)
            except Exception:
                for pt in self.growth_points:
                    e.Display.DrawPoint(
                        pt, rd.PointStyle.RoundControlPoint, gps, gpc)
        if self.show_bounds and self.bound_lines:
            for ln in self.bound_lines:
                e.Display.DrawLine(ln, self.bound_color, 1)
        if self.show_view_line and self.view_origin and self.view_target:
            e.Display.DrawLine(
                rg.Line(self.view_origin, self.view_target),
                self.view_line_color, 2)
        if self.show_view_origin and self.view_origin:
            e.Display.DrawPoint(
                self.view_origin, rd.PointStyle.RoundControlPoint,
                self.view_point_size, self.view_origin_color)
        if self.show_view_target and self.view_target:
            e.Display.DrawPoint(
                self.view_target, rd.PointStyle.RoundControlPoint,
                self.view_point_size, self.view_target_color)


# ---------------------------------------------------------------------------
# Voxel System
# Core engine: generates voxel fields from Perlin noise, builds meshes for
# display, runs boid pathfinding along exposed edges, creates pipe meshes,
# and applies Laplacian smoothing for the melt/blend effect.
# ---------------------------------------------------------------------------
class VoxelSystem(object):
    def __init__(self):
        self.conduit = VoxelConduit()
        self.conduit.Enabled = True
        self.fields = FieldAlgorithms()
        self.voxels = []
        self.boid_graph = {}
        self.boid_vertex_normals = {}
        self.boid_trails = []
        self.custom_base_mesh = None
        self.custom_base_edges = []

    def _eval_field(self, mode, ix, iy, iz, cx, cy, cz,
                    noise_scale, octaves, tpms_scale, tpms_thick,
                    sdf_falloff, sdf_invert,
                    comp_mode, comp_weight, _Point3d,
                    pw_width=3.0, pw_falloff=5.0, pw_invert=False,
                    sun_az=0.785, sun_el=0.785,
                    view_radius=3.0, view_falloff=3.0,
                    grav_mode=0, grav_strength=1.0,
                    grid_cx=0.0, grid_cy=0.0, grid_cz=0.0,
                    grid_diag=100.0, origin_z=0.0, grid_height=50.0,
                    multi_a=0, multi_b=1):
        """Dispatch to the selected field algorithm."""
        f = self.fields
        if mode == 0:
            return f.eval_perlin(ix * noise_scale, iy * noise_scale,
                                 iz * noise_scale, octaves)
        elif mode == 1:
            return f.eval_gyroid(cx, cy, cz, tpms_scale, tpms_thick)
        elif mode == 2:
            return f.eval_schwarzp(cx, cy, cz, tpms_scale, tpms_thick)
        elif mode == 3:
            return f.eval_diamond(cx, cy, cz, tpms_scale, tpms_thick)
        elif mode == 4:
            return f.eval_sdf(_Point3d(cx, cy, cz), sdf_falloff, sdf_invert)
        elif mode == 5:
            return f.eval_curl_magnitude(cx, cy, cz, noise_scale, octaves)
        elif mode == 6:
            return f.eval_reaction_diffusion(ix, iy, iz)
        elif mode == 7:
            va = f.eval_perlin(ix * noise_scale, iy * noise_scale,
                               iz * noise_scale, octaves)
            vb = f.eval_gyroid(cx, cy, cz, tpms_scale, tpms_thick)
            return f.composite(va, vb, comp_mode, comp_weight)
        elif mode == 8:
            return f.eval_pathway(
                _Point3d(cx, cy, cz), pw_width, pw_falloff, pw_invert)
        elif mode == 9:
            return f.eval_solar(cx, cy, cz, sun_az, sun_el,
                                grid_cx, grid_cy, grid_cz, grid_diag)
        elif mode == 10:
            return f.eval_view_corridor(cx, cy, cz, view_radius, view_falloff)
        elif mode == 11:
            return f.eval_gravity(cz, origin_z, grid_height,
                                  grav_mode, grav_strength)
        elif mode == 12:
            safe_a = multi_a if multi_a != 12 else 0
            safe_b = multi_b if multi_b != 12 else 0
            kw = dict(noise_scale=noise_scale, octaves=octaves,
                      tpms_scale=tpms_scale, tpms_thick=tpms_thick,
                      sdf_falloff=sdf_falloff, sdf_invert=sdf_invert,
                      comp_mode=comp_mode, comp_weight=comp_weight,
                      _Point3d=_Point3d,
                      pw_width=pw_width, pw_falloff=pw_falloff,
                      pw_invert=pw_invert,
                      sun_az=sun_az, sun_el=sun_el,
                      view_radius=view_radius, view_falloff=view_falloff,
                      grav_mode=grav_mode, grav_strength=grav_strength,
                      grid_cx=grid_cx, grid_cy=grid_cy, grid_cz=grid_cz,
                      grid_diag=grid_diag, origin_z=origin_z,
                      grid_height=grid_height,
                      multi_a=0, multi_b=0)
            va = self._eval_field(safe_a, ix, iy, iz, cx, cy, cz, **kw)
            vb = self._eval_field(safe_b, ix, iy, iz, cx, cy, cz, **kw)
            return f.composite(va, vb, comp_mode, comp_weight)
        elif mode == 13:
            return f.eval_dla(ix, iy, iz)
        elif mode == 14:
            return f.eval_space_colonization(ix, iy, iz)
        elif mode == 15:
            return f.eval_eden(ix, iy, iz)
        elif mode == 16:
            return f.eval_physarum(ix, iy, iz)
        elif mode == 17:
            return f.eval_mycelium(ix, iy, iz)
        return 0.5

    def generate(self, grid_x, grid_y, grid_z, cell_w, cell_l, cell_h,
                 noise_scale, threshold, octaves, seed,
                 use_attractors, attractor_pts, attractor_curves, attractor_geos,
                 attr_radius, attr_strength,
                 hollow, shell_thickness,
                 use_base, base_geos, base_radius, base_strength, base_carve,
                 grid_origin,
                 field_mode=0,
                 tpms_scale=5.0, tpms_thick=0.8,
                 sdf_falloff=20.0, sdf_invert=False,
                 rd_feed=0.055, rd_kill=0.062, rd_da=0.2, rd_db=0.1,
                 rd_iterations=50,
                 comp_mode=0, comp_weight=0.5,
                 pw_width=3.0, pw_falloff=5.0, pw_invert=False,
                 sun_az=0.785, sun_el=0.785,
                 view_radius=3.0, view_falloff=3.0,
                 grav_mode=0, grav_strength=1.0,
                 multi_a=0, multi_b=1):
        """Sample a field algorithm across a 3D grid and collect voxels above
        threshold. Supports multiple field sources dispatched by field_mode.
        Returns list of (ix, iy, iz, val)."""
        self.fields.set_seed(seed)
        _closest = self.fields._closest_dist
        voxels = []
        _append = voxels.append
        ox = grid_origin.X; oy = grid_origin.Y; oz = grid_origin.Z
        hw = cell_w * 0.5; hl = cell_l * 0.5; hh = cell_h * 0.5
        inv_attr_r = 1.0 / attr_radius if attr_radius > 1e-10 else 0.0
        inv_base_r = 1.0 / base_radius if base_radius > 1e-10 else 0.0
        need_pt = ((use_base and base_geos) or
                   (use_attractors and (attractor_pts or attractor_curves or attractor_geos)) or
                   field_mode in (4, 8))
        half_bs = base_strength * 0.5
        _Point3d = rg.Point3d
        face_dirs = ((-1,0,0),(1,0,0),(0,-1,0),(0,1,0),(0,0,-1),(0,0,1))

        grid_w = grid_x * cell_w
        grid_l = grid_y * cell_l
        grid_h = grid_z * cell_h
        grid_cx = ox + grid_w * 0.5
        grid_cy = oy + grid_l * 0.5
        grid_cz = oz + grid_h * 0.5
        grid_diag = (grid_w * grid_w + grid_l * grid_l + grid_h * grid_h) ** 0.5

        env_kw = dict(pw_width=pw_width, pw_falloff=pw_falloff,
                      pw_invert=pw_invert, sun_az=sun_az, sun_el=sun_el,
                      view_radius=view_radius, view_falloff=view_falloff,
                      grav_mode=grav_mode, grav_strength=grav_strength,
                      grid_cx=grid_cx, grid_cy=grid_cy, grid_cz=grid_cz,
                      grid_diag=grid_diag, origin_z=oz,
                      grid_height=grid_h, multi_a=multi_a, multi_b=multi_b)

        if field_mode == 6:
            self.fields.compute_reaction_diffusion(
                grid_x, grid_y, grid_z,
                rd_feed, rd_kill, rd_da, rd_db, rd_iterations, seed)
        elif field_mode == 13:
            self.fields.compute_dla(grid_x, grid_y, grid_z, seed)
        elif field_mode == 14:
            self.fields.compute_space_colonization(grid_x, grid_y, grid_z, seed)
        elif field_mode == 15:
            self.fields.compute_eden(grid_x, grid_y, grid_z, seed)
        elif field_mode == 16:
            self.fields.compute_physarum(grid_x, grid_y, grid_z, seed)
        elif field_mode == 17:
            self.fields.compute_mycelium(grid_x, grid_y, grid_z, seed)

        if hollow:
            raw = [[[0.0] * grid_z for _ in range(grid_y)] for _ in range(grid_x)]
            for ix in range(grid_x):
                cx_h = ox + ix * cell_w + hw
                for iy in range(grid_y):
                    cy_h = oy + iy * cell_l + hl
                    for iz in range(grid_z):
                        cz_h = oz + iz * cell_h + hh
                        raw[ix][iy][iz] = self._eval_field(
                            field_mode, ix, iy, iz, cx_h, cy_h, cz_h,
                            noise_scale, octaves, tpms_scale, tpms_thick,
                            sdf_falloff, sdf_invert, comp_mode, comp_weight,
                            _Point3d, **env_kw)

        for ix in range(grid_x):
            cx_b = ox + ix * cell_w + hw
            for iy in range(grid_y):
                cy_b = oy + iy * cell_l + hl
                for iz in range(grid_z):
                    cz_b = oz + iz * cell_h + hh

                    if hollow:
                        val = raw[ix][iy][iz]
                    else:
                        val = self._eval_field(
                            field_mode, ix, iy, iz, cx_b, cy_b, cz_b,
                            noise_scale, octaves, tpms_scale, tpms_thick,
                            sdf_falloff, sdf_invert, comp_mode, comp_weight,
                            _Point3d, **env_kw)

                    if need_pt:
                        pt = _Point3d(cx_b, cy_b, cz_b)

                    if use_base and base_geos:
                        min_d = float('inf')
                        for geo in base_geos:
                            d = _closest(pt, geo)
                            if d < min_d:
                                min_d = d
                        if base_carve:
                            if min_d < base_radius:
                                val -= (1.0 - min_d * inv_base_r) * base_strength
                        else:
                            if min_d < base_radius:
                                val += (1.0 - min_d * inv_base_r) * base_strength
                            else:
                                val -= half_bs

                    if use_attractors:
                        if attractor_pts:
                            for apt in attractor_pts:
                                d = pt.DistanceTo(apt)
                                if d < attr_radius:
                                    val += (1.0 - d * inv_attr_r) * attr_strength
                        if attractor_curves:
                            for crv in attractor_curves:
                                d = _closest(pt, crv)
                                if d < attr_radius:
                                    val += (1.0 - d * inv_attr_r) * attr_strength
                        if attractor_geos:
                            for geo in attractor_geos:
                                d = _closest(pt, geo)
                                if d < attr_radius:
                                    val += (1.0 - d * inv_attr_r) * attr_strength

                    if val < 0.0:
                        val = 0.0
                    elif val > 1.0:
                        val = 1.0

                    if val > threshold:
                        if hollow:
                            is_interior = True
                            for dx, dy, dz in face_dirs:
                                nix = ix + dx; niy = iy + dy; niz = iz + dz
                                if (nix < 0 or nix >= grid_x or
                                    niy < 0 or niy >= grid_y or
                                    niz < 0 or niz >= grid_z):
                                    is_interior = False
                                    break
                                if raw[nix][niy][niz] <= threshold:
                                    is_interior = False
                                    break
                            depth = 0
                            if is_interior:
                                depth = min(ix, grid_x-1-ix, iy, grid_y-1-iy, iz, grid_z-1-iz)
                            if not is_interior or depth <= shell_thickness:
                                _append((ix, iy, iz, val))
                        else:
                            _append((ix, iy, iz, val))

        self.voxels = voxels
        return voxels

    def set_custom_geometry(self, meshes):
        """Combine user-selected meshes, center at origin, normalise to unit size.
        Stores as the template shape replicated at each voxel position."""
        if not meshes:
            self.custom_base_mesh = None
            self.custom_base_edges = []
            return
        combined = rg.Mesh()
        for m in meshes:
            combined.Append(m)
        bb = combined.GetBoundingBox(True)
        if not bb.IsValid:
            self.custom_base_mesh = None
            self.custom_base_edges = []
            return
        center = bb.Center
        dims = [bb.Max.X - bb.Min.X, bb.Max.Y - bb.Min.Y, bb.Max.Z - bb.Min.Z]
        max_dim = max(dims) if max(dims) > 0 else 1.0
        scale_factor = 1.0 / max_dim
        xform = rg.Transform.Translation(-center.X, -center.Y, -center.Z)
        combined.Transform(xform)
        xform = rg.Transform.Scale(rg.Point3d.Origin, scale_factor)
        combined.Transform(xform)
        self.custom_base_mesh = combined
        self.custom_base_edges = self._extract_feature_edges(combined)

    def build_mesh_custom(self, voxels, cell_w, cell_l, cell_h, color,
                          grid_origin, custom_scale,
                          rotate=False, rot_max_rad=0.0, rot_axis=2,
                          rotate2=False, rot_max_rad2=0.0, rot_axis2=0,
                          dscale=False, dscale_min=1.0):
        """Build display mesh using the user-assigned custom shape at each voxel.
        Each shape is scaled to cell dims * custom_scale, optionally rotated on
        two axes and density-scaled. Falls back to build_mesh if no custom geo."""
        if not self.custom_base_mesh:
            return self.build_mesh(voxels, cell_w, cell_l, cell_h, color,
                                   grid_origin, rotate, rot_max_rad, rot_axis,
                                   rotate2, rot_max_rad2, rot_axis2,
                                   dscale, dscale_min)
        mesh = rg.Mesh()
        verts = mesh.Vertices
        faces = mesh.Faces
        colors = mesh.VertexColors
        base = self.custom_base_mesh
        bv = base.Vertices; bf = base.Faces
        base_vcount = bv.Count; base_fcount = bf.Count
        ox0 = grid_origin.X; oy0 = grid_origin.Y; oz0 = grid_origin.Z
        cr = color.R; cg = color.G; cb = color.B
        _FromArgb = System.Drawing.Color.FromArgb
        _rotate = self._rotate_pt
        _cos = math.cos; _sin = math.sin

        bv_cache = [(bv[i].X, bv[i].Y, bv[i].Z) for i in range(base_vcount)]
        bf_cache = []
        for fi in range(base_fcount):
            f = bf[fi]
            if f.IsQuad:
                bf_cache.append((f.A, f.B, f.C, f.D))
            else:
                bf_cache.append((f.A, f.B, f.C))

        sx_base = cell_w * custom_scale
        sy_base = cell_l * custom_scale
        sz_base = cell_h * custom_scale
        do_rot = rotate and abs(rot_max_rad) > 1e-6
        do_rot2 = rotate2 and abs(rot_max_rad2) > 1e-6
        ds_range = 1.0 - dscale_min
        hw = cell_w * 0.5; hl = cell_l * 0.5; hh = cell_h * 0.5

        for (ix, iy, iz, val) in voxels:
            cx = ox0 + ix * cell_w + hw
            cy = oy0 + iy * cell_l + hl
            cz = oz0 + iz * cell_h + hh
            if dscale:
                ds = dscale_min + val * ds_range
                sx = sx_base * ds; sy = sy_base * ds; sz = sz_base * ds
            else:
                sx = sx_base; sy = sy_base; sz = sz_base
            if do_rot:
                ca = _cos(val * rot_max_rad); sa = _sin(val * rot_max_rad)
            if do_rot2:
                ca2 = _cos(val * rot_max_rad2); sa2 = _sin(val * rot_max_rad2)
            base_idx = verts.Count
            for (bx, by, bz) in bv_cache:
                dx = bx * sx; dy = by * sy; dz = bz * sz
                if do_rot:
                    dx, dy, dz = _rotate(dx, dy, dz, ca, sa, rot_axis)
                if do_rot2:
                    dx, dy, dz = _rotate(dx, dy, dz, ca2, sa2, rot_axis2)
                verts.Add(dx + cx, dy + cy, dz + cz)
            for fd in bf_cache:
                if len(fd) == 4:
                    faces.AddFace(fd[0]+base_idx, fd[1]+base_idx,
                                  fd[2]+base_idx, fd[3]+base_idx)
                else:
                    faces.AddFace(fd[0]+base_idx, fd[1]+base_idx, fd[2]+base_idx)
            rv = int(cr * val); gv = int(cg * val); bv_c = int(cb * val)
            if rv < 30: rv = 30
            elif rv > 255: rv = 255
            if gv < 30: gv = 30
            elif gv > 255: gv = 255
            if bv_c < 30: bv_c = 30
            elif bv_c > 255: bv_c = 255
            vc = _FromArgb(rv, gv, bv_c)
            for _ in range(base_vcount):
                colors.Add(vc)

        mesh.Normals.ComputeNormals()
        mesh.Compact()
        return mesh

    def _rotate_pt(self, dx, dy, dz, cos_a, sin_a, axis):
        """Rotate point (dx,dy,dz) around a single axis. axis: 0=X, 1=Y, 2=Z."""
        if axis == 0:
            return (dx,
                    dy * cos_a - dz * sin_a,
                    dy * sin_a + dz * cos_a)
        elif axis == 1:
            return (dx * cos_a + dz * sin_a,
                    dy,
                    -dx * sin_a + dz * cos_a)
        else:
            return (dx * cos_a - dy * sin_a,
                    dx * sin_a + dy * cos_a,
                    dz)

    def build_mesh(self, voxels, cell_w, cell_l, cell_h, color, grid_origin,
                   rotate=False, rot_max_rad=0.0, rot_axis=2,
                   rotate2=False, rot_max_rad2=0.0, rot_axis2=0,
                   dscale=False, dscale_min=1.0):
        """Build a combined mesh of axis-aligned boxes (8 verts, 6 quad faces each).
        Vertex colours encode density (darker = lower val). Density-based scaling
        is neighbour-aware: shared faces stay full-size to avoid gaps. Two
        independent rotations can be applied sequentially per voxel."""
        mesh = rg.Mesh()
        verts = mesh.Vertices
        faces = mesh.Faces
        colors = mesh.VertexColors
        ox0 = grid_origin.X; oy0 = grid_origin.Y; oz0 = grid_origin.Z
        cr = color.R; cg = color.G; cb = color.B
        _FromArgb = System.Drawing.Color.FromArgb
        _rotate = self._rotate_pt
        _cos = math.cos; _sin = math.sin

        hw = cell_w * 0.5; hl = cell_l * 0.5; hh = cell_h * 0.5
        default_offsets = (
            (-hw, -hl, -hh), ( hw, -hl, -hh), ( hw,  hl, -hh), (-hw,  hl, -hh),
            (-hw, -hl,  hh), ( hw, -hl,  hh), ( hw,  hl,  hh), (-hw,  hl,  hh))
        do_rot = rotate and abs(rot_max_rad) > 1e-6
        do_rot2 = rotate2 and abs(rot_max_rad2) > 1e-6
        ds_range = 1.0 - dscale_min
        voxel_set = set((v[0], v[1], v[2]) for v in voxels) if dscale else None

        for (ix, iy, iz, val) in voxels:
            cx = ox0 + ix * cell_w + hw
            cy = oy0 + iy * cell_l + hl
            cz = oz0 + iz * cell_h + hh
            if dscale:
                s = dscale_min + val * ds_range
                x0 = -hw if (ix-1,iy,iz) in voxel_set else -hw*s
                x1 =  hw if (ix+1,iy,iz) in voxel_set else  hw*s
                y0 = -hl if (ix,iy-1,iz) in voxel_set else -hl*s
                y1 =  hl if (ix,iy+1,iz) in voxel_set else  hl*s
                z0 = -hh if (ix,iy,iz-1) in voxel_set else -hh*s
                z1 =  hh if (ix,iy,iz+1) in voxel_set else  hh*s
                pts = ((x0,y0,z0),(x1,y0,z0),(x1,y1,z0),(x0,y1,z0),
                       (x0,y0,z1),(x1,y0,z1),(x1,y1,z1),(x0,y1,z1))
            else:
                pts = default_offsets
            b = verts.Count
            if do_rot or do_rot2:
                if do_rot:
                    ca = _cos(val * rot_max_rad); sa = _sin(val * rot_max_rad)
                if do_rot2:
                    ca2 = _cos(val * rot_max_rad2); sa2 = _sin(val * rot_max_rad2)
                for (dx, dy, dz) in pts:
                    rx, ry, rz = dx, dy, dz
                    if do_rot:
                        rx, ry, rz = _rotate(rx, ry, rz, ca, sa, rot_axis)
                    if do_rot2:
                        rx, ry, rz = _rotate(rx, ry, rz, ca2, sa2, rot_axis2)
                    verts.Add(cx + rx, cy + ry, cz + rz)
            else:
                for (dx, dy, dz) in pts:
                    verts.Add(cx + dx, cy + dy, cz + dz)
            faces.AddFace(b, b+1, b+2, b+3)
            faces.AddFace(b+4, b+7, b+6, b+5)
            faces.AddFace(b, b+4, b+5, b+1)
            faces.AddFace(b+2, b+6, b+7, b+3)
            faces.AddFace(b, b+3, b+7, b+4)
            faces.AddFace(b+1, b+5, b+6, b+2)
            rv = int(cr * val); gv = int(cg * val); bv = int(cb * val)
            if rv < 30: rv = 30
            elif rv > 255: rv = 255
            if gv < 30: gv = 30
            elif gv > 255: gv = 255
            if bv < 30: bv = 30
            elif bv > 255: bv = 255
            vc = _FromArgb(rv, gv, bv)
            for _ in range(8):
                colors.Add(vc)
        mesh.Normals.ComputeNormals()
        mesh.Compact()
        return mesh

    def _extract_feature_edges(self, mesh, angle_deg=20.0):
        """Find sharp edges (dihedral angle > angle_deg) and naked edges on a mesh.
        Used to generate wireframe overlay for custom voxel geometry."""
        if not mesh or mesh.Faces.Count == 0:
            return []
        mesh.FaceNormals.ComputeFaceNormals()
        topo = mesh.TopologyEdges
        cos_thresh = math.cos(angle_deg * math.pi / 180.0)
        lines = []
        for ei in range(topo.Count):
            faces = topo.GetConnectedFaces(ei)
            if faces.Length == 1:
                lines.append(topo.EdgeLine(ei))
            elif faces.Length == 2:
                n0 = mesh.FaceNormals[faces[0]]
                n1 = mesh.FaceNormals[faces[1]]
                dot = n0.X * n1.X + n0.Y * n1.Y + n0.Z * n1.Z
                if dot < cos_thresh:
                    lines.append(topo.EdgeLine(ei))
        return lines

    def _build_edge_mesh(self, voxels, cell_w, cell_l, cell_h, grid_origin,
                         custom_scale):
        """Create a degenerate-triangle mesh from feature edge lines so they can
        be drawn as wireframe via DrawMeshWires for custom geometry voxels."""
        if not self.custom_base_edges:
            return None
        em = rg.Mesh()
        verts = em.Vertices
        faces = em.Faces
        ox0 = grid_origin.X
        oy0 = grid_origin.Y
        oz0 = grid_origin.Z
        sx = cell_w * custom_scale
        sy = cell_l * custom_scale
        sz = cell_h * custom_scale

        for (ix, iy, iz, val) in voxels:
            cx = ox0 + ix * cell_w + cell_w * 0.5
            cy = oy0 + iy * cell_l + cell_l * 0.5
            cz = oz0 + iz * cell_h + cell_h * 0.5

            for be in self.custom_base_edges:
                fr = be.From
                to = be.To
                b = verts.Count
                verts.Add(fr.X * sx + cx, fr.Y * sy + cy, fr.Z * sz + cz)
                verts.Add(to.X * sx + cx, to.Y * sy + cy, to.Z * sz + cz)
                verts.Add(to.X * sx + cx, to.Y * sy + cy, to.Z * sz + cz)
                faces.AddFace(b, b + 1, b + 2)
        return em

    def build_edge_graph(self, voxels, diagonals=True):
        """Build a graph of vertices on exposed voxel faces for boid pathfinding.
        Each exposed quad face contributes 4 edge connections (+ 2 diagonals if
        enabled). Also accumulates per-vertex outward normals for trail offset."""
        voxel_set = set()
        for (ix, iy, iz, val) in voxels:
            voxel_set.add((ix, iy, iz))
        graph = {}
        normals = {}
        _sqrt = math.sqrt
        for (ix, iy, iz, val) in voxels:
            for (dx, dy, dz) in ((1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1)):
                if (ix+dx, iy+dy, iz+dz) in voxel_set:
                    continue
                if dx == 1:
                    x = ix+1; fv = ((x,iy,iz),(x,iy+1,iz),(x,iy+1,iz+1),(x,iy,iz+1))
                elif dx == -1:
                    fv = ((ix,iy,iz),(ix,iy,iz+1),(ix,iy+1,iz+1),(ix,iy+1,iz))
                elif dy == 1:
                    y = iy+1; fv = ((ix,y,iz),(ix,y,iz+1),(ix+1,y,iz+1),(ix+1,y,iz))
                elif dy == -1:
                    fv = ((ix,iy,iz),(ix+1,iy,iz),(ix+1,iy,iz+1),(ix,iy,iz+1))
                elif dz == 1:
                    z = iz+1; fv = ((ix,iy,z),(ix+1,iy,z),(ix+1,iy+1,z),(ix,iy+1,z))
                else:
                    fv = ((ix,iy,iz),(ix,iy+1,iz),(ix+1,iy+1,iz),(ix+1,iy,iz))
                v0,v1,v2,v3 = fv
                for a, b in ((v0,v1),(v1,v2),(v2,v3),(v3,v0)):
                    if a not in graph: graph[a] = set()
                    if b not in graph: graph[b] = set()
                    graph[a].add(b); graph[b].add(a)
                if diagonals:
                    if v0 not in graph: graph[v0] = set()
                    if v2 not in graph: graph[v2] = set()
                    graph[v0].add(v2); graph[v2].add(v0)
                    if v1 not in graph: graph[v1] = set()
                    if v3 not in graph: graph[v3] = set()
                    graph[v1].add(v3); graph[v3].add(v1)
                for vert in fv:
                    if vert not in normals:
                        normals[vert] = [0.0, 0.0, 0.0]
                    n = normals[vert]
                    n[0] += dx; n[1] += dy; n[2] += dz
        for v in normals:
            n = normals[v]
            l = _sqrt(n[0]*n[0] + n[1]*n[1] + n[2]*n[2])
            if l > 1e-10:
                inv = 1.0 / l
                normals[v] = (n[0]*inv, n[1]*inv, n[2]*inv)
            else:
                normals[v] = (0.0, 0.0, 0.0)
        self.boid_graph = graph
        self.boid_vertex_normals = normals

    def run_edge_boids(self, count, steps, min_angle, max_angle,
                       turn_chance, seed, cell_w, cell_l, cell_h,
                       grid_origin, offset=0.0, offset_tightness=0,
                       straight_angle=5.0, overlap=0.0,
                       boid_attractors=None, boid_attr_strength=0.0):
        """Simulate boid agents walking along the exposed-face edge graph.
        Each boid picks a random start vertex, then at each step chooses between
        continuing straight or turning based on turn_chance and angle limits.
        Visited edges are tracked globally to reduce overlap (unless overlap > 0).
        Attractor curves bias direction. Offset pushes trails outward along
        surface normals, smoothed by tightness iterations."""
        graph = self.boid_graph
        if not graph:
            self.boid_trails = []
            return
        _sqrt = math.sqrt; _acos = math.acos; _pi = math.pi
        _min = min; _max = max
        _Point3d = rg.Point3d; _Polyline = rg.Polyline
        rng = random.Random(seed + 99)
        _random = rng.random; _randint = rng.randint
        vertices = list(graph.keys())
        vcount = len(vertices)
        vnormals = self.boid_vertex_normals
        ox = grid_origin.X; oy = grid_origin.Y; oz = grid_origin.Z
        deg2rad = _pi / 180.0
        min_rad = min_angle * deg2rad; max_rad = max_angle * deg2rad
        straight_thresh = straight_angle * deg2rad
        allow_overlap = overlap > 1e-6
        use_attr = (boid_attractors and len(boid_attractors) > 0
                    and boid_attr_strength > 1e-6)
        trails = []
        visited_global = set()

        for _ in range(count):
            start = vertices[_randint(0, vcount - 1)]
            nbs = graph.get(start)
            if not nbs:
                continue
            free_nbs = []
            for nb in nbs:
                ek = (start, nb) if start < nb else (nb, start)
                if ek not in visited_global:
                    free_nbs.append(nb)
                elif allow_overlap and _random() < overlap:
                    free_nbs.append(nb)
            if not free_nbs:
                continue
            first = free_nbs[_randint(0, len(free_nbs) - 1)]
            h0 = first[0]-start[0]; h1 = first[1]-start[1]; h2 = first[2]-start[2]
            trail_v = [start, first]
            current = first
            ek = (start, first) if start < first else (first, start)
            visited_global.add(ek)

            for _ in range(steps - 1):
                cur_nbs = graph.get(current)
                if not cur_nbs:
                    break
                straight = None; turns = []
                for nb in cur_nbs:
                    ek = (current, nb) if current < nb else (nb, current)
                    if ek in visited_global:
                        if not (allow_overlap and _random() < overlap):
                            continue
                    d0 = nb[0]-current[0]; d1 = nb[1]-current[1]; d2 = nb[2]-current[2]
                    l1sq = h0*h0+h1*h1+h2*h2; l2sq = d0*d0+d1*d1+d2*d2
                    if l1sq < 1e-20 or l2sq < 1e-20:
                        ang = _pi
                    else:
                        dot = (h0*d0+h1*d1+h2*d2) / _sqrt(l1sq * l2sq)
                        if dot > 1.0: dot = 1.0
                        elif dot < -1.0: dot = -1.0
                        ang = _acos(dot)
                    if ang < straight_thresh:
                        straight = (nb, (d0, d1, d2))
                    elif min_rad <= ang <= max_rad:
                        turns.append((nb, (d0, d1, d2)))

                chosen = None
                if use_attr:
                    all_cands = []
                    if straight:
                        all_cands.append(straight)
                    all_cands.extend(turns)
                    if all_cands and _random() < boid_attr_strength:
                        best = None; best_dist = float('inf')
                        for cand in all_cands:
                            cnb = cand[0]
                            nb_pt = _Point3d(ox+cnb[0]*cell_w, oy+cnb[1]*cell_l, oz+cnb[2]*cell_h)
                            for crv in boid_attractors:
                                try:
                                    rc, t = crv.ClosestPoint(nb_pt)
                                    if rc:
                                        dd = nb_pt.DistanceTo(crv.PointAt(t))
                                        if dd < best_dist:
                                            best_dist = dd; best = cand
                                except:
                                    pass
                        if best:
                            chosen = best
                if not chosen:
                    if straight and turns:
                        if _random() < turn_chance:
                            chosen = turns[_randint(0, len(turns) - 1)]
                        else:
                            chosen = straight
                    elif straight:
                        chosen = straight
                    elif turns:
                        chosen = turns[_randint(0, len(turns) - 1)]
                if not chosen:
                    break
                next_v = chosen[0]; h0, h1, h2 = chosen[1]
                trail_v.append(next_v)
                ek = (current, next_v) if current < next_v else (next_v, current)
                visited_global.add(ek)
                current = next_v

            if len(trail_v) > 1:
                pts = []
                do_offset = abs(offset) > 1e-6
                if do_offset:
                    tn = []
                    for v in trail_v:
                        if v in vnormals:
                            n = vnormals[v]
                            tn.append([n[0], n[1], n[2]])
                        else:
                            tn.append([0.0, 0.0, 0.0])
                    for _ in range(offset_tightness):
                        sm = [tn[0][:]]
                        tlen = len(tn)
                        for j in range(1, tlen - 1):
                            p = tn[j-1]; c = tn[j]; nx = tn[j+1]
                            sm.append([(p[0]+c[0]+nx[0])*0.333333,
                                       (p[1]+c[1]+nx[1])*0.333333,
                                       (p[2]+c[2]+nx[2])*0.333333])
                        sm.append(tn[-1][:])
                        for sn in sm:
                            sl = _sqrt(sn[0]*sn[0]+sn[1]*sn[1]+sn[2]*sn[2])
                            if sl > 1e-10:
                                inv = 1.0/sl
                                sn[0] *= inv; sn[1] *= inv; sn[2] *= inv
                        tn = sm
                    for j in range(len(trail_v)):
                        vx, vy, vz = trail_v[j]
                        pts.append(_Point3d(ox+vx*cell_w+tn[j][0]*offset,
                                            oy+vy*cell_l+tn[j][1]*offset,
                                            oz+vz*cell_h+tn[j][2]*offset))
                else:
                    for (vx, vy, vz) in trail_v:
                        pts.append(_Point3d(ox+vx*cell_w, oy+vy*cell_l, oz+vz*cell_h))
                trails.append(_Polyline(pts))

        self.boid_trails = trails

    def fillet_trails(self, trails, radius):
        """Round sharp corners of boid polylines using quadratic Bezier arcs.
        Radius is clamped to 45% of adjacent segment lengths to prevent overlap."""
        if radius < 1e-6 or not trails:
            return trails
        filleted = []
        arc_steps = 6
        for pl in trails:
            if pl.Count < 3:
                filleted.append(pl)
                continue
            new_pts = [pl[0]]
            for i in range(1, pl.Count - 1):
                p_prev = pl[i - 1]
                p_curr = pl[i]
                p_next = pl[i + 1]
                v1 = rg.Vector3d(p_prev - p_curr)
                v2 = rg.Vector3d(p_next - p_curr)
                l1 = v1.Length
                l2 = v2.Length
                if l1 < 1e-10 or l2 < 1e-10:
                    new_pts.append(p_curr)
                    continue
                r = min(radius, l1 * 0.45, l2 * 0.45)
                if r < 1e-6:
                    new_pts.append(p_curr)
                    continue
                v1.Unitize()
                v2.Unitize()
                f_start = p_curr + v1 * r
                f_end = p_curr + v2 * r
                for j in range(arc_steps + 1):
                    t = j / float(arc_steps)
                    u = 1.0 - t
                    new_pts.append(rg.Point3d(
                        u * u * f_start.X + 2.0 * u * t * p_curr.X + t * t * f_end.X,
                        u * u * f_start.Y + 2.0 * u * t * p_curr.Y + t * t * f_end.Y,
                        u * u * f_start.Z + 2.0 * u * t * p_curr.Z + t * t * f_end.Z))
            new_pts.append(pl[pl.Count - 1])
            filleted.append(rg.Polyline(new_pts))
        return filleted

    def build_pipe_mesh(self, trails, radius, segments=8):
        """Extrude circular cross-sections along each trail polyline to create
        tube meshes. Uses parallel transport to prevent twist. Each tube gets
        start/end cap faces for a watertight result."""
        if radius < 1e-6 or not trails:
            return None
        combined = rg.Mesh()
        cv = combined.Vertices; cf = combined.Faces
        segs = max(4, segments)
        two_pi = 2.0 * math.pi
        ring_cs = []
        for j in range(segs):
            a = two_pi * j / segs
            ring_cs.append((math.cos(a), math.sin(a)))
        _CrossProduct = rg.Vector3d.CrossProduct
        for pl in trails:
            n = pl.Count
            if n < 2:
                continue
            bv = cv.Count
            prev_x = None
            for i in range(n):
                pt = pl[i]
                ptx = pt.X; pty = pt.Y; ptz = pt.Z
                if i == 0:
                    tan = rg.Vector3d(pl[1] - pl[0])
                elif i == n - 1:
                    tan = rg.Vector3d(pl[n - 1] - pl[n - 2])
                else:
                    tan = rg.Vector3d(pl[i + 1] - pl[i - 1])
                tan.Unitize()
                if prev_x is None:
                    up = rg.Vector3d(0, 0, 1) if abs(tan.Z) < 0.9 else rg.Vector3d(1, 0, 0)
                    x_ax = _CrossProduct(tan, up)
                    x_ax.Unitize()
                else:
                    d = prev_x.X*tan.X + prev_x.Y*tan.Y + prev_x.Z*tan.Z
                    x_ax = rg.Vector3d(prev_x.X - tan.X*d,
                                       prev_x.Y - tan.Y*d,
                                       prev_x.Z - tan.Z*d)
                    if x_ax.Length < 1e-10:
                        x_ax = prev_x
                    else:
                        x_ax.Unitize()
                y_ax = _CrossProduct(tan, x_ax)
                y_ax.Unitize()
                prev_x = x_ax
                xx = x_ax.X; xy = x_ax.Y; xz = x_ax.Z
                yx = y_ax.X; yy = y_ax.Y; yz = y_ax.Z
                for ca, sa in ring_cs:
                    cv.Add(ptx + (xx*ca + yx*sa) * radius,
                           pty + (xy*ca + yy*sa) * radius,
                           ptz + (xz*ca + yz*sa) * radius)
            for i in range(n - 1):
                b = bv + i * segs; nb = b + segs
                for j in range(segs):
                    jn = (j + 1) % segs
                    cf.AddFace(b + j, b + jn, nb + jn, nb + j)
            ci = cv.Count
            cv.Add(pl[0])
            for j in range(segs):
                cf.AddFace(ci, bv + (j+1)%segs, bv + j)
            ci = cv.Count
            cv.Add(pl[n - 1])
            lb = bv + (n - 1) * segs
            for j in range(segs):
                cf.AddFace(ci, lb + j, lb + (j+1)%segs)
        combined.Normals.ComputeNormals()
        combined.Compact()
        return combined

    def _laplacian_smooth(self, mesh, factor, iterations):
        """Iterative Laplacian mesh smoothing: each vertex moves toward the
        average of its neighbours by factor per iteration. Pre-builds adjacency
        into flat Python arrays to avoid .NET interop overhead in tight loops."""
        topo = mesh.TopologyVertices
        verts = mesh.Vertices
        tv_count = topo.Count
        if tv_count == 0 or iterations < 1:
            return
        adj = [None] * tv_count
        t2m = [None] * tv_count
        px = [0.0] * tv_count
        py = [0.0] * tv_count
        pz = [0.0] * tv_count
        for ti in range(tv_count):
            adj[ti] = list(topo.ConnectedTopologyVertices(ti))
            t2m[ti] = list(topo.MeshVertexIndices(ti))
            v = verts[t2m[ti][0]]
            px[ti] = v.X
            py[ti] = v.Y
            pz[ti] = v.Z
        for _ in range(iterations):
            nx = [0.0] * tv_count
            ny = [0.0] * tv_count
            nz = [0.0] * tv_count
            for ti in range(tv_count):
                nbs = adj[ti]
                nc = len(nbs)
                if nc == 0:
                    nx[ti] = px[ti]; ny[ti] = py[ti]; nz[ti] = pz[ti]
                    continue
                ax = ay = az = 0.0
                for ci in nbs:
                    ax += px[ci]; ay += py[ci]; az += pz[ci]
                inv = 1.0 / nc
                ax *= inv; ay *= inv; az *= inv
                nx[ti] = px[ti] + (ax - px[ti]) * factor
                ny[ti] = py[ti] + (ay - py[ti]) * factor
                nz[ti] = pz[ti] + (az - pz[ti]) * factor
            px = nx; py = ny; pz = nz
        _set = verts.SetVertex
        for ti in range(tv_count):
            x, y, z = px[ti], py[ti], pz[ti]
            for vi in t2m[ti]:
                _set(vi, x, y, z)
        mesh.Normals.ComputeNormals()

    def melt(self, smooth_iters, smooth_factor):
        """Combine the voxel mesh and pipe mesh into one, then apply Laplacian
        smoothing to blend them together. More iterations = more melted."""
        vm = self.conduit.mesh
        pm = self.conduit.pipe_mesh
        if not vm or vm.Vertices.Count == 0:
            return None
        result = vm.DuplicateMesh()
        if pm and pm.Vertices.Count > 0:
            result.Append(pm)
        if smooth_iters > 0 and result.Vertices.Count > 0:
            self._laplacian_smooth(result, smooth_factor, smooth_iters)
        result.Compact()
        return result

    def update_display(self, voxels, cell_w, cell_l, cell_h, color,
                       show_bounds, bounds_color,
                       show_edges, edge_color,
                       grid_x, grid_y, grid_z, grid_origin,
                       use_custom=False, custom_scale=1.0,
                       rotate=False, rot_max_rad=0.0, rot_axis=2,
                       rotate2=False, rot_max_rad2=0.0, rot_axis2=0,
                       dscale=False, dscale_min=1.0):
        """Rebuild the conduit's display mesh and bounding box from current
        parameters. Chooses custom or default box mesh, sets edge/bound overlays,
        and triggers a viewport redraw."""
        if use_custom and self.custom_base_mesh:
            self.conduit.mesh = self.build_mesh_custom(
                voxels, cell_w, cell_l, cell_h, color, grid_origin,
                custom_scale, rotate, rot_max_rad, rot_axis,
                rotate2, rot_max_rad2, rot_axis2,
                dscale, dscale_min)
            if show_edges:
                self.conduit.edge_mesh = self._build_edge_mesh(
                    voxels, cell_w, cell_l, cell_h, grid_origin, custom_scale)
            else:
                self.conduit.edge_mesh = None
        else:
            self.conduit.mesh = self.build_mesh(
                voxels, cell_w, cell_l, cell_h, color, grid_origin,
                rotate, rot_max_rad, rot_axis,
                rotate2, rot_max_rad2, rot_axis2,
                dscale, dscale_min)
            self.conduit.edge_mesh = None
        self.conduit.show_bounds = show_bounds
        self.conduit.show_edges = show_edges
        self.conduit.edge_color = edge_color

        ox = grid_origin.X
        oy = grid_origin.Y
        oz = grid_origin.Z
        bx = grid_x * cell_w
        by = grid_y * cell_l
        bz = grid_z * cell_h

        if show_bounds:
            p = [rg.Point3d(ox,    oy,    oz),
                 rg.Point3d(ox+bx, oy,    oz),
                 rg.Point3d(ox+bx, oy+by, oz),
                 rg.Point3d(ox,    oy+by, oz),
                 rg.Point3d(ox,    oy,    oz+bz),
                 rg.Point3d(ox+bx, oy,    oz+bz),
                 rg.Point3d(ox+bx, oy+by, oz+bz),
                 rg.Point3d(ox,    oy+by, oz+bz)]
            edges = [(0,1),(1,2),(2,3),(3,0),
                     (4,5),(5,6),(6,7),(7,4),
                     (0,4),(1,5),(2,6),(3,7)]
            self.conduit.bound_lines = [rg.Line(p[a], p[b]) for a, b in edges]
        else:
            self.conduit.bound_lines = []

        self.conduit.bound_color = bounds_color
        self.conduit.bbox = rg.BoundingBox(
            rg.Point3d(ox, oy, oz),
            rg.Point3d(ox + bx, oy + by, oz + bz))

        sc.doc.Views.Redraw()

    def bake(self, color, grid_origin, use_vertex_colors=True):
        """Add voxel mesh to the Rhino document. With vertex colours: density-
        shaded mesh. Without: plain mesh with no colour attributes."""
        if not self.conduit.mesh:
            return
        if use_vertex_colors:
            attr = Rhino.DocObjects.ObjectAttributes()
            attr.ColorSource = Rhino.DocObjects.ObjectColorSource.ColorFromObject
            attr.ObjectColor = color
            sc.doc.Objects.AddMesh(self.conduit.mesh, attr)
        else:
            clean = self.conduit.mesh.DuplicateMesh()
            clean.VertexColors.Clear()
            sc.doc.Objects.AddMesh(clean)
        sc.doc.Views.Redraw()

    def dispose(self):
        """Disable the display conduit so preview geometry disappears."""
        self.conduit.Enabled = False
        sc.doc.Views.Redraw()


# ---------------------------------------------------------------------------
# UI Dialog
# Eto.Forms window with all sliders, checkboxes and buttons. Uses a debounced
# timer (UITimer at 0.12s) so slider drags don't trigger a full recompute on
# every pixel of movement. Two dirty flags separate heavy work (noise
# recompute) from cheap work (mesh rebuild with different rotation/scale).
# ---------------------------------------------------------------------------
class VoxelDialog(forms.Form):
    def __init__(self):
        super(VoxelDialog, self).__init__()
        self.Title = "Voxel Field Tool v02 — Multi-Field"
        self.Padding = drawing.Padding(8)
        self.Resizable = True
        self.MinimumSize = drawing.Size(390, 500)
        try:
            screen = forms.Screen.PrimaryScreen.WorkingArea
            max_h = int(screen.Height * 0.88)
        except Exception:
            max_h = 900
        self.Size = drawing.Size(420, min(max_h, 1100))

        self.system = VoxelSystem()
        self.attractor_pts = []
        self.attractor_curves = []
        self.attractor_geos = []
        self.boid_attractor_curves = []
        self.base_geometries = []
        self.bound_geometry = None
        self.voxel_color = System.Drawing.Color.FromArgb(100, 180, 255)
        self.system.conduit.shaded_material = rd.DisplayMaterial(
            System.Drawing.Color.FromArgb(100, 180, 255))
        self.edge_color = System.Drawing.Color.FromArgb(40, 40, 40)
        self.bounds_color = System.Drawing.Color.FromArgb(80, 80, 80)
        self.trail_color = System.Drawing.Color.FromArgb(255, 120, 50)

        self._compute_dirty = False
        self._display_dirty = False
        self._growth_playing = False
        self._growth_frame = 0
        self._growth_total_frames = 0
        self._all_world_trails = []
        self._all_world_points = []
        self._world_frame_snapshots = []

        self._build_ui()

        self._timer = forms.UITimer()
        self._timer.Interval = 0.12
        self._timer.Elapsed += self._on_timer_tick
        self._timer.Start()

        self._full_regenerate()

    # -- UI ----------------------------------------------------------------
    def _build_ui(self):
        layout = forms.DynamicLayout()
        layout.DefaultSpacing = drawing.Size(4, 4)
        layout.DefaultPadding = drawing.Padding(6)

        self.chk_live = forms.CheckBox()
        self.chk_live.Text = "Live Update"
        self.chk_live.Checked = True
        layout.AddRow(self.chk_live)

        # -- field source --------------------------------------------------
        sec_field = forms.DynamicLayout()
        sec_field.DefaultSpacing = drawing.Size(4, 4)

        lbl_field = forms.Label()
        lbl_field.Text = "Algorithm"
        lbl_field.Width = 105
        self.dd_field_mode = forms.DropDown()
        for fname in ["Perlin Noise", "Gyroid (TPMS)", "Schwarz-P (TPMS)",
                       "Diamond (TPMS)", "Signed Distance Field",
                       "Curl Noise", "Reaction-Diffusion",
                       "Perlin + Gyroid",
                       "Pathway Field", "Solar Exposure",
                       "View Corridor", "Gravity Gradient",
                       "Multi-Layer Blend",
                       "DLA Growth", "Space Colonization",
                       "Eden Growth (CA)", "Physarum (Slime Mold)",
                       "Mycelium Growth"]:
            self.dd_field_mode.Items.Add(fname)
        self.dd_field_mode.SelectedIndex = 0
        self.dd_field_mode.SelectedIndexChanged += self._on_field_mode_changed
        r = forms.TableLayout()
        r.Spacing = drawing.Size(4, 0)
        r.Rows.Add(forms.TableRow(
            forms.TableCell(lbl_field, False),
            forms.TableCell(self.dd_field_mode, True)))
        sec_field.AddRow(r)

        self.panel_tpms = forms.DynamicLayout()
        self.panel_tpms.DefaultSpacing = drawing.Size(4, 4)
        self.sld_tpms_scale, self.txt_tpms_scale = self._float_slider(
            self.panel_tpms, "TPMS Scale", 0.1, 50.0, 5.0, self._mark_compute)
        self.sld_tpms_thick, self.txt_tpms_thick = self._float_slider(
            self.panel_tpms, "Wall Thickness", 0.01, 3.0, 0.8, self._mark_compute)
        self.panel_tpms.Visible = False
        sec_field.AddRow(self.panel_tpms)

        self.panel_sdf = forms.DynamicLayout()
        self.panel_sdf.DefaultSpacing = drawing.Size(4, 4)
        self.sld_sdf_falloff, self.txt_sdf_falloff = self._float_slider(
            self.panel_sdf, "Falloff Distance", 1.0, 100.0, 20.0, self._mark_compute)
        self.chk_sdf_invert = forms.CheckBox()
        self.chk_sdf_invert.Text = "Invert SDF"
        self.chk_sdf_invert.Checked = False
        self.chk_sdf_invert.CheckedChanged += lambda s, e: self._mark_compute()
        self.panel_sdf.AddRow(self.chk_sdf_invert)
        btn_pick_sdf = forms.Button()
        btn_pick_sdf.Text = "Assign SDF Geometry"
        btn_pick_sdf.Click += self._on_pick_sdf
        btn_clr_sdf = forms.Button()
        btn_clr_sdf.Text = "Clear SDF"
        btn_clr_sdf.Click += self._on_clear_sdf
        self.panel_sdf.AddRow(self._hrow(btn_pick_sdf, btn_clr_sdf))
        self.lbl_sdf = forms.Label()
        self.lbl_sdf.Text = "SDF Geometry: None"
        self.panel_sdf.AddRow(self.lbl_sdf)
        self.panel_sdf.Visible = False
        sec_field.AddRow(self.panel_sdf)

        self.panel_rd = forms.DynamicLayout()
        self.panel_rd.DefaultSpacing = drawing.Size(4, 4)
        self.sld_rd_feed, self.txt_rd_feed = self._float_slider(
            self.panel_rd, "Feed Rate", 0.01, 0.1, 0.055, self._mark_compute)
        self.sld_rd_kill, self.txt_rd_kill = self._float_slider(
            self.panel_rd, "Kill Rate", 0.01, 0.1, 0.062, self._mark_compute)
        self.sld_rd_da, self.txt_rd_da = self._float_slider(
            self.panel_rd, "Diffusion A", 0.05, 1.0, 0.2, self._mark_compute)
        self.sld_rd_db, self.txt_rd_db = self._float_slider(
            self.panel_rd, "Diffusion B", 0.01, 0.5, 0.1, self._mark_compute)
        self.sld_rd_iters, self.txt_rd_iters = self._int_slider(
            self.panel_rd, "RD Iterations", 1, 200, 50, self._mark_compute)
        lbl_rd_note = forms.Label()
        lbl_rd_note.Text = "(RD is slow on large grids)"
        self.panel_rd.AddRow(lbl_rd_note)
        self.panel_rd.Visible = False
        sec_field.AddRow(self.panel_rd)

        self.panel_composite = forms.DynamicLayout()
        self.panel_composite.DefaultSpacing = drawing.Size(4, 4)
        lbl_blend = forms.Label()
        lbl_blend.Text = "Blend Mode"
        lbl_blend.Width = 105
        self.dd_blend_mode = forms.DropDown()
        for bm in ["Add", "Multiply", "Max (Union)", "Min (Intersect)",
                    "Smooth Union", "Subtract"]:
            self.dd_blend_mode.Items.Add(bm)
        self.dd_blend_mode.SelectedIndex = 0
        self.dd_blend_mode.SelectedIndexChanged += lambda s, e: self._mark_compute()
        r = forms.TableLayout()
        r.Spacing = drawing.Size(4, 0)
        r.Rows.Add(forms.TableRow(
            forms.TableCell(lbl_blend, False),
            forms.TableCell(self.dd_blend_mode, True)))
        self.panel_composite.AddRow(r)
        self.sld_comp_weight, self.txt_comp_weight = self._float_slider(
            self.panel_composite, "Blend Weight", 0.0, 1.0, 0.5, self._mark_compute)
        self.panel_composite.Visible = False
        sec_field.AddRow(self.panel_composite)

        self.panel_pathway = forms.DynamicLayout()
        self.panel_pathway.DefaultSpacing = drawing.Size(4, 4)
        self.sld_pw_width, self.txt_pw_width = self._float_slider(
            self.panel_pathway, "Corridor Width", 0.5, 20.0, 3.0, self._mark_compute)
        self.sld_pw_falloff, self.txt_pw_falloff = self._float_slider(
            self.panel_pathway, "Falloff Dist", 1.0, 30.0, 5.0, self._mark_compute)
        self.chk_pw_invert = forms.CheckBox()
        self.chk_pw_invert.Text = "Invert (carve corridor)"
        self.chk_pw_invert.Checked = True
        self.chk_pw_invert.CheckedChanged += lambda s, e: self._mark_compute()
        self.panel_pathway.AddRow(self.chk_pw_invert)
        btn_pick_pw = forms.Button()
        btn_pick_pw.Text = "Pick Pathway Curves"
        btn_pick_pw.Click += self._on_pick_pathway
        btn_clr_pw = forms.Button()
        btn_clr_pw.Text = "Clear"
        btn_clr_pw.Click += self._on_clear_pathway
        self.panel_pathway.AddRow(self._hrow(btn_pick_pw, btn_clr_pw))
        self.lbl_pathway = forms.Label()
        self.lbl_pathway.Text = "Pathway: None"
        self.panel_pathway.AddRow(self.lbl_pathway)
        self.panel_pathway.Visible = False
        sec_field.AddRow(self.panel_pathway)

        self.panel_solar = forms.DynamicLayout()
        self.panel_solar.DefaultSpacing = drawing.Size(4, 4)
        self.sld_sun_az, self.txt_sun_az = self._float_slider(
            self.panel_solar, "Sun Azimuth (deg)", 0.0, 360.0, 135.0, self._mark_compute)
        self.sld_sun_el, self.txt_sun_el = self._float_slider(
            self.panel_solar, "Sun Elevation (deg)", 0.0, 90.0, 45.0, self._mark_compute)
        self.panel_solar.Visible = False
        sec_field.AddRow(self.panel_solar)

        self.panel_view = forms.DynamicLayout()
        self.panel_view.DefaultSpacing = drawing.Size(4, 4)
        self.sld_view_radius, self.txt_view_radius = self._float_slider(
            self.panel_view, "Corridor Radius", 0.5, 30.0, 3.0, self._mark_compute)
        self.sld_view_falloff, self.txt_view_falloff = self._float_slider(
            self.panel_view, "Falloff", 0.5, 20.0, 3.0, self._mark_compute)
        btn_pick_viewer = forms.Button()
        btn_pick_viewer.Text = "Pick Viewer Point"
        btn_pick_viewer.Click += self._on_pick_viewer
        btn_pick_target = forms.Button()
        btn_pick_target.Text = "Pick Target Point"
        btn_pick_target.Click += self._on_pick_target
        self.panel_view.AddRow(self._hrow(btn_pick_viewer, btn_pick_target))
        self.lbl_view = forms.Label()
        self.lbl_view.Text = "View: not set"
        self.panel_view.AddRow(self.lbl_view)

        self.chk_show_view_origin = forms.CheckBox()
        self.chk_show_view_origin.Text = "Show Viewer Pt"
        self.chk_show_view_origin.Checked = True
        self.chk_show_view_origin.CheckedChanged += self._on_view_vis_changed
        self.chk_show_view_target = forms.CheckBox()
        self.chk_show_view_target.Text = "Show Target Pt"
        self.chk_show_view_target.Checked = True
        self.chk_show_view_target.CheckedChanged += self._on_view_vis_changed
        self.chk_show_view_line = forms.CheckBox()
        self.chk_show_view_line.Text = "Show Line"
        self.chk_show_view_line.Checked = True
        self.chk_show_view_line.CheckedChanged += self._on_view_vis_changed
        self.panel_view.AddRow(self._hrow(self.chk_show_view_origin,
                                         self.chk_show_view_target,
                                         self.chk_show_view_line))

        self.btn_view_origin_col = forms.Button()
        self.btn_view_origin_col.Text = "Viewer Col"
        self.btn_view_origin_col.BackgroundColor = drawing.Color.FromArgb(0, 200, 80)
        self.btn_view_origin_col.Click += self._on_pick_view_origin_color
        self.btn_view_target_col = forms.Button()
        self.btn_view_target_col.Text = "Target Col"
        self.btn_view_target_col.BackgroundColor = drawing.Color.FromArgb(255, 60, 60)
        self.btn_view_target_col.Click += self._on_pick_view_target_color
        self.btn_view_line_col = forms.Button()
        self.btn_view_line_col.Text = "Line Col"
        self.btn_view_line_col.BackgroundColor = drawing.Color.FromArgb(255, 200, 0)
        self.btn_view_line_col.Click += self._on_pick_view_line_color
        self.panel_view.AddRow(self._hrow(self.btn_view_origin_col,
                                         self.btn_view_target_col,
                                         self.btn_view_line_col))

        self.panel_view.Visible = False
        sec_field.AddRow(self.panel_view)

        self.panel_gravity = forms.DynamicLayout()
        self.panel_gravity.DefaultSpacing = drawing.Size(4, 4)
        lbl_grav = forms.Label()
        lbl_grav.Text = "Profile"
        lbl_grav.Width = 105
        self.dd_grav_mode = forms.DropDown()
        for gm in ["Linear (dense base)", "Quadratic (heavy base)",
                    "Inverse (dense top)", "Bell (dense middle)"]:
            self.dd_grav_mode.Items.Add(gm)
        self.dd_grav_mode.SelectedIndex = 0
        self.dd_grav_mode.SelectedIndexChanged += lambda s, e: self._mark_compute()
        r = forms.TableLayout()
        r.Spacing = drawing.Size(4, 0)
        r.Rows.Add(forms.TableRow(
            forms.TableCell(lbl_grav, False),
            forms.TableCell(self.dd_grav_mode, True)))
        self.panel_gravity.AddRow(r)
        self.sld_grav_str, self.txt_grav_str = self._float_slider(
            self.panel_gravity, "Strength", 0.0, 2.0, 1.0, self._mark_compute)
        self.panel_gravity.Visible = False
        sec_field.AddRow(self.panel_gravity)

        self.panel_multi = forms.DynamicLayout()
        self.panel_multi.DefaultSpacing = drawing.Size(4, 4)
        lbl_ma = forms.Label()
        lbl_ma.Text = "Field A"
        lbl_ma.Width = 105
        self.dd_multi_a = forms.DropDown()
        lbl_mb = forms.Label()
        lbl_mb.Text = "Field B"
        lbl_mb.Width = 105
        self.dd_multi_b = forms.DropDown()
        for mname in ["Perlin", "Gyroid", "Schwarz-P", "Diamond", "SDF",
                       "Curl", "R-D", "Perlin+Gyroid",
                       "Pathway", "Solar", "View Corridor", "Gravity"]:
            self.dd_multi_a.Items.Add(mname)
            self.dd_multi_b.Items.Add(mname)
        self.dd_multi_a.SelectedIndex = 0
        self.dd_multi_b.SelectedIndex = 1
        self.dd_multi_a.SelectedIndexChanged += lambda s, e: self._mark_compute()
        self.dd_multi_b.SelectedIndexChanged += lambda s, e: self._mark_compute()
        r = forms.TableLayout()
        r.Spacing = drawing.Size(4, 0)
        r.Rows.Add(forms.TableRow(
            forms.TableCell(lbl_ma, False),
            forms.TableCell(self.dd_multi_a, True)))
        self.panel_multi.AddRow(r)
        r2 = forms.TableLayout()
        r2.Spacing = drawing.Size(4, 0)
        r2.Rows.Add(forms.TableRow(
            forms.TableCell(lbl_mb, False),
            forms.TableCell(self.dd_multi_b, True)))
        self.panel_multi.AddRow(r2)
        lbl_ml_note = forms.Label()
        lbl_ml_note.Text = "(Uses Blend Mode / Weight from Composite panel)"
        self.panel_multi.AddRow(lbl_ml_note)
        self.panel_multi.Visible = False
        sec_field.AddRow(self.panel_multi)

        # -- DLA panel (mode 13) -------------------------------------------
        self.panel_dla = forms.DynamicLayout()
        self.panel_dla.DefaultSpacing = drawing.Size(4, 4)
        self.sld_dla_particles, self.txt_dla_particles = self._int_slider(
            self.panel_dla, "Particles", 100, 10000, 2000, self._mark_compute)
        self.sld_dla_stick, self.txt_dla_stick = self._float_slider(
            self.panel_dla, "Stickiness", 0.1, 1.0, 1.0, self._mark_compute)
        self.sld_dla_bias, self.txt_dla_bias = self._float_slider(
            self.panel_dla, "Growth Bias Z", -1.0, 1.0, 0.0, self._mark_compute)
        lbl_dsm = forms.Label()
        lbl_dsm.Text = "Seed Placement"
        lbl_dsm.Width = 105
        self.dd_dla_seed = forms.DropDown()
        for sn in ["Center", "Bottom Center", "Bottom Face", "Random"]:
            self.dd_dla_seed.Items.Add(sn)
        self.dd_dla_seed.SelectedIndex = 0
        self.dd_dla_seed.SelectedIndexChanged += lambda s, e: self._mark_compute()
        r_dla = forms.TableLayout()
        r_dla.Spacing = drawing.Size(4, 0)
        r_dla.Rows.Add(forms.TableRow(
            forms.TableCell(lbl_dsm, False),
            forms.TableCell(self.dd_dla_seed, True)))
        self.panel_dla.AddRow(r_dla)
        lbl_dla_note = forms.Label()
        lbl_dla_note.Text = "(Slow on large grids with many particles)"
        self.panel_dla.AddRow(lbl_dla_note)
        self.panel_dla.Visible = False
        sec_field.AddRow(self.panel_dla)

        # -- Space Colonization panel (mode 14) ----------------------------
        self.panel_sc = forms.DynamicLayout()
        self.panel_sc.DefaultSpacing = drawing.Size(4, 4)
        self.sld_sc_density, self.txt_sc_density = self._float_slider(
            self.panel_sc, "Attractor Density", 0.05, 1.0, 0.3, self._mark_compute)
        self.sld_sc_kill, self.txt_sc_kill = self._int_slider(
            self.panel_sc, "Kill Distance", 1, 5, 2, self._mark_compute)
        self.sld_sc_inf, self.txt_sc_inf = self._int_slider(
            self.panel_sc, "Influence Radius", 2, 15, 5, self._mark_compute)
        self.sld_sc_step, self.txt_sc_step = self._int_slider(
            self.panel_sc, "Step Length", 1, 3, 1, self._mark_compute)
        self.sld_sc_iters, self.txt_sc_iters = self._int_slider(
            self.panel_sc, "Iterations", 10, 500, 200, self._mark_compute)
        lbl_scr = forms.Label()
        lbl_scr.Text = "Root Position"
        lbl_scr.Width = 105
        self.dd_sc_root = forms.DropDown()
        for rn in ["Bottom Center", "Center", "Bottom Corners", "Random"]:
            self.dd_sc_root.Items.Add(rn)
        self.dd_sc_root.SelectedIndex = 0
        self.dd_sc_root.SelectedIndexChanged += lambda s, e: self._mark_compute()
        r_sc = forms.TableLayout()
        r_sc.Spacing = drawing.Size(4, 0)
        r_sc.Rows.Add(forms.TableRow(
            forms.TableCell(lbl_scr, False),
            forms.TableCell(self.dd_sc_root, True)))
        self.panel_sc.AddRow(r_sc)
        lbl_sc_note = forms.Label()
        lbl_sc_note.Text = "(Tree-like branching from root toward nutrients)"
        self.panel_sc.AddRow(lbl_sc_note)
        self.panel_sc.Visible = False
        sec_field.AddRow(self.panel_sc)

        # -- Eden Growth panel (mode 15) -----------------------------------
        self.panel_eden = forms.DynamicLayout()
        self.panel_eden.DefaultSpacing = drawing.Size(4, 4)
        self.sld_eden_birth, self.txt_eden_birth = self._int_slider(
            self.panel_eden, "Birth Threshold", 1, 6, 2, self._mark_compute)
        self.sld_eden_surv_lo, self.txt_eden_surv_lo = self._int_slider(
            self.panel_eden, "Survival Min", 0, 6, 1, self._mark_compute)
        self.sld_eden_surv_hi, self.txt_eden_surv_hi = self._int_slider(
            self.panel_eden, "Survival Max", 1, 6, 6, self._mark_compute)
        self.sld_eden_iters, self.txt_eden_iters = self._int_slider(
            self.panel_eden, "Iterations", 1, 200, 50, self._mark_compute)
        self.sld_eden_seed_d, self.txt_eden_seed_d = self._float_slider(
            self.panel_eden, "Seed Density", 0.01, 0.5, 0.05, self._mark_compute)
        self.sld_eden_bias, self.txt_eden_bias = self._float_slider(
            self.panel_eden, "Noise Bias", 0.0, 1.0, 0.0, self._mark_compute)
        lbl_eden_note = forms.Label()
        lbl_eden_note.Text = "(3D cellular automaton — birth/survival rules)"
        self.panel_eden.AddRow(lbl_eden_note)
        self.panel_eden.Visible = False
        sec_field.AddRow(self.panel_eden)

        # -- Physarum panel (mode 16) --------------------------------------
        self.panel_phys = forms.DynamicLayout()
        self.panel_phys.DefaultSpacing = drawing.Size(4, 4)
        self.sld_phys_agents, self.txt_phys_agents = self._int_slider(
            self.panel_phys, "Agents", 100, 10000, 2000, self._mark_compute)
        self.sld_phys_sa, self.txt_phys_sa = self._float_slider(
            self.panel_phys, "Sensor Angle", 10.0, 90.0, 45.0, self._mark_compute)
        self.sld_phys_sd, self.txt_phys_sd = self._float_slider(
            self.panel_phys, "Sensor Distance", 1.0, 5.0, 3.0, self._mark_compute)
        self.sld_phys_ta, self.txt_phys_ta = self._float_slider(
            self.panel_phys, "Turn Angle", 10.0, 90.0, 45.0, self._mark_compute)
        self.sld_phys_dep, self.txt_phys_dep = self._float_slider(
            self.panel_phys, "Deposit Rate", 0.1, 5.0, 1.0, self._mark_compute)
        self.sld_phys_decay, self.txt_phys_decay = self._float_slider(
            self.panel_phys, "Decay Rate", 0.01, 0.5, 0.1, self._mark_compute)
        self.sld_phys_iters, self.txt_phys_iters = self._int_slider(
            self.panel_phys, "Iterations", 10, 500, 200, self._mark_compute)
        lbl_phys_note = forms.Label()
        lbl_phys_note.Text = "(Slime mold network — slow on large grids)"
        self.panel_phys.AddRow(lbl_phys_note)
        self.panel_phys.Visible = False
        sec_field.AddRow(self.panel_phys)

        # -- Mycelium panel (mode 17) --------------------------------------
        self.panel_myc = forms.DynamicLayout()
        self.panel_myc.DefaultSpacing = drawing.Size(4, 4)
        self.sld_myc_tips, self.txt_myc_tips = self._int_slider(
            self.panel_myc, "Initial Tips", 1, 50, 10, self._mark_compute)
        self.sld_myc_branch, self.txt_myc_branch = self._float_slider(
            self.panel_myc, "Branch Probability", 0.0, 0.3, 0.05, self._mark_compute)
        self.sld_myc_bangle, self.txt_myc_bangle = self._float_slider(
            self.panel_myc, "Branch Angle", 10.0, 90.0, 45.0, self._mark_compute)
        self.sld_myc_turn, self.txt_myc_turn = self._float_slider(
            self.panel_myc, "Turn Rate", 1.0, 60.0, 15.0, self._mark_compute)
        self.sld_myc_anast, self.txt_myc_anast = self._float_slider(
            self.panel_myc, "Anastomosis", 0.0, 1.0, 0.5, self._mark_compute)
        self.sld_myc_iters, self.txt_myc_iters = self._int_slider(
            self.panel_myc, "Iterations", 10, 1000, 200, self._mark_compute)
        self.sld_myc_max_tips, self.txt_myc_max_tips = self._int_slider(
            self.panel_myc, "Max Active Tips", 10, 2000, 500, self._mark_compute)
        lbl_myc_note = forms.Label()
        lbl_myc_note.Text = "(Fungal network — branching filaments with fusion)"
        self.panel_myc.AddRow(lbl_myc_note)
        self.panel_myc.Visible = False
        sec_field.AddRow(self.panel_myc)

        # -- growth display + playback (shown inside sec_field for modes 13-17)
        self.panel_growth_disp = forms.DynamicLayout()
        self.panel_growth_disp.DefaultSpacing = drawing.Size(4, 4)

        self.panel_growth_disp.AddRow(self._bold("Growth Attractors / Repellents"))

        btn_pick_gattr = forms.Button()
        btn_pick_gattr.Text = "Assign Attractors"
        btn_pick_gattr.Click += self._on_pick_growth_attractors
        btn_clr_gattr = forms.Button()
        btn_clr_gattr.Text = "Clear"
        btn_clr_gattr.Click += self._on_clear_growth_attractors
        self.panel_growth_disp.AddRow(self._hrow(btn_pick_gattr, btn_clr_gattr))

        btn_pick_grep = forms.Button()
        btn_pick_grep.Text = "Assign Repellents"
        btn_pick_grep.Click += self._on_pick_growth_repellents
        btn_clr_grep = forms.Button()
        btn_clr_grep.Text = "Clear"
        btn_clr_grep.Click += self._on_clear_growth_repellents
        self.panel_growth_disp.AddRow(self._hrow(btn_pick_grep, btn_clr_grep))

        self.lbl_gattr = forms.Label()
        self.lbl_gattr.Text = "Attract: 0 | Repel: 0"
        self.panel_growth_disp.AddRow(self.lbl_gattr)

        self.sld_gattr_r, self.txt_gattr_r = self._float_slider(
            self.panel_growth_disp, "Attract Radius", 1.0, 100.0, 10.0,
            self._mark_compute)
        self.sld_gattr_s, self.txt_gattr_s = self._float_slider(
            self.panel_growth_disp, "Attract Strength", 0.1, 3.0, 1.0,
            self._mark_compute)
        self.sld_grep_r, self.txt_grep_r = self._float_slider(
            self.panel_growth_disp, "Repel Radius", 1.0, 100.0, 10.0,
            self._mark_compute)
        self.sld_grep_s, self.txt_grep_s = self._float_slider(
            self.panel_growth_disp, "Repel Strength", 0.1, 3.0, 1.0,
            self._mark_compute)

        self.panel_growth_disp.AddRow(self._bold("Growth Display"))

        self.chk_growth_trails = forms.CheckBox()
        self.chk_growth_trails.Text = "Show Growth Trails"
        self.chk_growth_trails.Checked = False
        self.chk_growth_trails.CheckedChanged += lambda s, e: self._on_growth_disp_changed()
        self.panel_growth_disp.AddRow(self.chk_growth_trails)

        self.chk_growth_points = forms.CheckBox()
        self.chk_growth_points.Text = "Show Agent Points"
        self.chk_growth_points.Checked = False
        self.chk_growth_points.CheckedChanged += lambda s, e: self._on_growth_disp_changed()
        self.panel_growth_disp.AddRow(self.chk_growth_points)

        self.chk_hide_voxels = forms.CheckBox()
        self.chk_hide_voxels.Text = "Hide Voxels (Curve Mode)"
        self.chk_hide_voxels.Checked = False
        self.chk_hide_voxels.CheckedChanged += lambda s, e: self._on_growth_disp_changed()
        self.panel_growth_disp.AddRow(self.chk_hide_voxels)

        self.sld_gtrail_thick, self.txt_gtrail_thick = self._int_slider(
            self.panel_growth_disp, "Trail Thickness", 1, 8, 2,
            self._on_growth_disp_changed)
        self.sld_gpoint_size, self.txt_gpoint_size = self._int_slider(
            self.panel_growth_disp, "Point Size", 2, 16, 4,
            self._on_growth_disp_changed)

        self.btn_gtrail_col = forms.Button()
        self.btn_gtrail_col.Text = "Trail Colour"
        self.btn_gtrail_col.BackgroundColor = drawing.Color.FromArgb(120, 220, 80)
        self.btn_gtrail_col.Click += self._on_pick_gtrail_color
        self.btn_gpoint_col = forms.Button()
        self.btn_gpoint_col.Text = "Point Colour"
        self.btn_gpoint_col.BackgroundColor = drawing.Color.FromArgb(255, 200, 50)
        self.btn_gpoint_col.Click += self._on_pick_gpoint_color
        self.panel_growth_disp.AddRow(self._hrow(
            self.btn_gtrail_col, self.btn_gpoint_col))

        self.panel_growth_disp.AddRow(self._bold("Playback"))

        self.btn_play_growth = forms.Button()
        self.btn_play_growth.Text = "Play"
        self.btn_play_growth.Click += self._on_play_growth
        self.btn_restart_growth = forms.Button()
        self.btn_restart_growth.Text = "Restart"
        self.btn_restart_growth.Click += self._on_restart_growth
        self.panel_growth_disp.AddRow(self._hrow(
            self.btn_play_growth, self.btn_restart_growth))

        self.sld_growth_speed, self.txt_growth_speed = self._int_slider(
            self.panel_growth_disp, "Speed", 1, 10, 1, lambda: None)

        self.lbl_growth_frame = forms.Label()
        self.lbl_growth_frame.Text = "Frame: — / —"
        self.panel_growth_disp.AddRow(self.lbl_growth_frame)

        self.panel_growth_disp.Visible = False
        sec_field.AddRow(self.panel_growth_disp)

        layout.AddRow(self._make_section("Field Source", sec_field, expanded=True))

        # -- geometry input ------------------------------------------------
        sec_geo = forms.DynamicLayout()
        sec_geo.DefaultSpacing = drawing.Size(4, 4)

        btn_pick_base = forms.Button()
        btn_pick_base.Text = "Assign Base Geometry"
        btn_pick_base.Click += self._on_pick_base
        btn_clr_base = forms.Button()
        btn_clr_base.Text = "Clear Base"
        btn_clr_base.Click += self._on_clear_base
        sec_geo.AddRow(self._hrow(btn_pick_base, btn_clr_base))

        self.lbl_base = forms.Label()
        self.lbl_base.Text = "Base: None"
        sec_geo.AddRow(self.lbl_base)

        self.chk_use_base = forms.CheckBox()
        self.chk_use_base.Text = "Use Base Geometry"
        self.chk_use_base.Checked = False
        self.chk_use_base.CheckedChanged += lambda s, e: self._mark_compute()
        sec_geo.AddRow(self.chk_use_base)

        self.chk_auto_center = forms.CheckBox()
        self.chk_auto_center.Text = "Auto-Center Grid on Base"
        self.chk_auto_center.Checked = True
        self.chk_auto_center.CheckedChanged += lambda s, e: self._mark_compute()
        sec_geo.AddRow(self.chk_auto_center)

        self.chk_carve = forms.CheckBox()
        self.chk_carve.Text = "Carve Mode (invert base effect)"
        self.chk_carve.Checked = False
        self.chk_carve.CheckedChanged += lambda s, e: self._mark_compute()
        sec_geo.AddRow(self.chk_carve)

        self.sld_base_r, self.txt_base_r = self._float_slider(sec_geo, "Base Radius", 1.0, 80.0, 20.0, self._mark_compute)
        self.sld_base_s, self.txt_base_s = self._float_slider(sec_geo, "Base Strength", 0.0, 1.0, 0.6, self._mark_compute)

        layout.AddRow(self._make_section("Geometry Input", sec_geo))

        # -- grid dimensions -----------------------------------------------
        sec_grid = forms.DynamicLayout()
        sec_grid.DefaultSpacing = drawing.Size(4, 4)

        btn_pick_bounds = forms.Button()
        btn_pick_bounds.Text = "Assign Bounds Geometry"
        btn_pick_bounds.Click += self._on_pick_bounds
        btn_clr_bounds = forms.Button()
        btn_clr_bounds.Text = "Clear Bounds"
        btn_clr_bounds.Click += self._on_clear_bounds
        sec_grid.AddRow(self._hrow(btn_pick_bounds, btn_clr_bounds))

        self.lbl_bounds = forms.Label()
        self.lbl_bounds.Text = "Bounds: None"
        sec_grid.AddRow(self.lbl_bounds)

        self.sld_gx, self.txt_gx = self._int_slider(sec_grid, "Grid X", 1, 200, 10, self._mark_compute)
        self.sld_gy, self.txt_gy = self._int_slider(sec_grid, "Grid Y", 1, 200, 10, self._mark_compute)
        self.sld_gz, self.txt_gz = self._int_slider(sec_grid, "Grid Z", 1, 200, 10, self._mark_compute)
        self.sld_cw, self.txt_cw = self._float_slider(sec_grid, "Voxel Width (X)", 0.1, 50.0, 2.0, self._mark_compute)
        self.sld_cl, self.txt_cl = self._float_slider(sec_grid, "Voxel Length (Y)", 0.1, 50.0, 2.0, self._mark_compute)
        self.sld_ch, self.txt_ch = self._float_slider(sec_grid, "Voxel Height (Z)", 0.1, 50.0, 2.0, self._mark_compute)
        layout.AddRow(self._make_section("Grid Dimensions", sec_grid, expanded=True))

        # -- voxel rotation ------------------------------------------------
        sec_rot = forms.DynamicLayout()
        sec_rot.DefaultSpacing = drawing.Size(4, 4)

        self.chk_rotate = forms.CheckBox()
        self.chk_rotate.Text = "Enable Density Rotation"
        self.chk_rotate.Checked = False
        self.chk_rotate.CheckedChanged += lambda s, e: self._mark_display()
        sec_rot.AddRow(self.chk_rotate)

        self.sld_rot_angle, self.txt_rot_angle = self._float_slider(
            sec_rot, "Max Angle", 0.0, 360.0, 10.0, self._mark_display)

        lbl_axis = forms.Label()
        lbl_axis.Text = "Rotation Axis 1"
        lbl_axis.Width = 105
        self.dd_rot_axis = forms.DropDown()
        self.dd_rot_axis.Items.Add("X Axis")
        self.dd_rot_axis.Items.Add("Y Axis")
        self.dd_rot_axis.Items.Add("Z Axis")
        self.dd_rot_axis.SelectedIndex = 2
        self.dd_rot_axis.SelectedIndexChanged += lambda s, e: self._mark_display()
        r = forms.TableLayout()
        r.Spacing = drawing.Size(4, 0)
        r.Rows.Add(forms.TableRow(
            forms.TableCell(lbl_axis, False),
            forms.TableCell(self.dd_rot_axis, True)))
        sec_rot.AddRow(r)

        self.chk_rotate2 = forms.CheckBox()
        self.chk_rotate2.Text = "Enable 2nd Rotation"
        self.chk_rotate2.Checked = False
        self.chk_rotate2.CheckedChanged += lambda s, e: self._mark_display()
        sec_rot.AddRow(self.chk_rotate2)

        self.sld_rot_angle2, self.txt_rot_angle2 = self._float_slider(
            sec_rot, "Max Angle 2", 0.0, 360.0, 10.0, self._mark_display)

        lbl_axis2 = forms.Label()
        lbl_axis2.Text = "Rotation Axis 2"
        lbl_axis2.Width = 105
        self.dd_rot_axis2 = forms.DropDown()
        self.dd_rot_axis2.Items.Add("X Axis")
        self.dd_rot_axis2.Items.Add("Y Axis")
        self.dd_rot_axis2.Items.Add("Z Axis")
        self.dd_rot_axis2.SelectedIndex = 0
        self.dd_rot_axis2.SelectedIndexChanged += lambda s, e: self._mark_display()
        r = forms.TableLayout()
        r.Spacing = drawing.Size(4, 0)
        r.Rows.Add(forms.TableRow(
            forms.TableCell(lbl_axis2, False),
            forms.TableCell(self.dd_rot_axis2, True)))
        sec_rot.AddRow(r)

        layout.AddRow(self._make_section("Voxel Rotation", sec_rot))

        # -- voxel density scale -------------------------------------------
        sec_dscale = forms.DynamicLayout()
        sec_dscale.DefaultSpacing = drawing.Size(4, 4)

        self.chk_dscale = forms.CheckBox()
        self.chk_dscale.Text = "Enable Density Scale"
        self.chk_dscale.Checked = False
        self.chk_dscale.CheckedChanged += lambda s, e: self._mark_display()
        sec_dscale.AddRow(self.chk_dscale)

        self.sld_dscale_min, self.txt_dscale_min = self._float_slider(
            sec_dscale, "Min Scale", 0.01, 1.0, 0.5, self._mark_display)

        layout.AddRow(self._make_section("Voxel Density Scale", sec_dscale))

        # -- noise parameters ----------------------------------------------
        sec_noise = forms.DynamicLayout()
        sec_noise.DefaultSpacing = drawing.Size(4, 4)
        self.sld_scale, self.txt_scale = self._float_slider(sec_noise, "Noise Scale", 0.01, 1.0, 0.15, self._mark_compute)
        self.sld_thresh, self.txt_thresh = self._float_slider(sec_noise, "Threshold", 0.0, 1.0, 0.45, self._mark_compute)
        self.sld_oct, self.txt_oct = self._int_slider(sec_noise, "Octaves", 1, 6, 3, self._mark_compute)
        self.sld_seed, self.txt_seed = self._int_slider(sec_noise, "Seed", 0, 100, 0, self._mark_compute)
        layout.AddRow(self._make_section("Noise Parameters", sec_noise, expanded=True))

        # -- hollow shell --------------------------------------------------
        sec_hollow = forms.DynamicLayout()
        sec_hollow.DefaultSpacing = drawing.Size(4, 4)
        self.chk_hollow = forms.CheckBox()
        self.chk_hollow.Text = "Enable Hollow Mode"
        self.chk_hollow.Checked = False
        self.chk_hollow.CheckedChanged += lambda s, e: self._mark_compute()
        sec_hollow.AddRow(self.chk_hollow)
        self.sld_shell, self.txt_shell = self._int_slider(sec_hollow, "Shell Thickness", 1, 5, 1, self._mark_compute)
        layout.AddRow(self._make_section("Hollow Shell", sec_hollow))

        # -- attractor -----------------------------------------------------
        sec_attr = forms.DynamicLayout()
        sec_attr.DefaultSpacing = drawing.Size(4, 4)
        self.chk_attr = forms.CheckBox()
        self.chk_attr.Text = "Use Attractors"
        self.chk_attr.Checked = False
        self.chk_attr.CheckedChanged += lambda s, e: self._mark_compute()
        sec_attr.AddRow(self.chk_attr)
        self.sld_attr_r, self.txt_attr_r = self._float_slider(sec_attr, "Attr Radius", 1.0, 50.0, 15.0, self._mark_compute)
        self.sld_attr_s, self.txt_attr_s = self._float_slider(sec_attr, "Attr Strength", 0.0, 1.0, 0.5, self._mark_compute)

        btn_pick = forms.Button()
        btn_pick.Text = "Assign Attr Pts"
        btn_pick.Click += self._on_pick_attractors
        btn_clr_attr = forms.Button()
        btn_clr_attr.Text = "Clear Pts"
        btn_clr_attr.Click += self._on_clear_attractors
        sec_attr.AddRow(self._hrow(btn_pick, btn_clr_attr))

        self.lbl_attr_count = forms.Label()
        self.lbl_attr_count.Text = "Points: 0"
        sec_attr.AddRow(self.lbl_attr_count)

        btn_pick_crv = forms.Button()
        btn_pick_crv.Text = "Assign Attr Curves"
        btn_pick_crv.Click += self._on_pick_attractor_curves
        btn_clr_crv = forms.Button()
        btn_clr_crv.Text = "Clear Curves"
        btn_clr_crv.Click += self._on_clear_attractor_curves
        sec_attr.AddRow(self._hrow(btn_pick_crv, btn_clr_crv))

        self.lbl_attr_crv_count = forms.Label()
        self.lbl_attr_crv_count.Text = "Curves: 0"
        sec_attr.AddRow(self.lbl_attr_crv_count)

        btn_pick_geo = forms.Button()
        btn_pick_geo.Text = "Assign Attr Geos"
        btn_pick_geo.Click += self._on_pick_attractor_geos
        btn_clr_geo = forms.Button()
        btn_clr_geo.Text = "Clear Geos"
        btn_clr_geo.Click += self._on_clear_attractor_geos
        sec_attr.AddRow(self._hrow(btn_pick_geo, btn_clr_geo))

        self.lbl_attr_geo_count = forms.Label()
        self.lbl_attr_geo_count.Text = "Geometries: 0"
        sec_attr.AddRow(self.lbl_attr_geo_count)

        layout.AddRow(self._make_section("Attractor", sec_attr))

        # -- custom voxel geometry -----------------------------------------
        sec_custom = forms.DynamicLayout()
        sec_custom.DefaultSpacing = drawing.Size(4, 4)

        btn_pick_custom = forms.Button()
        btn_pick_custom.Text = "Assign Custom Geo"
        btn_pick_custom.Click += self._on_pick_custom
        btn_clr_custom = forms.Button()
        btn_clr_custom.Text = "Clear Custom Geo"
        btn_clr_custom.Click += self._on_clear_custom
        sec_custom.AddRow(self._hrow(btn_pick_custom, btn_clr_custom))

        self.lbl_custom = forms.Label()
        self.lbl_custom.Text = "Custom: None"
        sec_custom.AddRow(self.lbl_custom)

        self.chk_use_custom = forms.CheckBox()
        self.chk_use_custom.Text = "Show Custom Voxels"
        self.chk_use_custom.Checked = False
        self.chk_use_custom.CheckedChanged += lambda s, e: self._mark_display()
        sec_custom.AddRow(self.chk_use_custom)

        self.sld_custom_s, self.txt_custom_s = self._float_slider(
            sec_custom, "Custom Scale", 0.1, 2.0, 1.0, self._mark_display)

        layout.AddRow(self._make_section("Custom Voxel Geometry", sec_custom))

        # -- edge boids ----------------------------------------------------
        sec_boids = forms.DynamicLayout()
        sec_boids.DefaultSpacing = drawing.Size(4, 4)

        self.chk_boids = forms.CheckBox()
        self.chk_boids.Text = "Show Trails"
        self.chk_boids.Checked = False
        self.chk_boids.CheckedChanged += lambda s, e: self._toggle_trails()
        sec_boids.AddRow(self.chk_boids)

        self.chk_diagonals = forms.CheckBox()
        self.chk_diagonals.Text = "Include Diagonal Edges (45\u00b0)"
        self.chk_diagonals.Checked = True
        sec_boids.AddRow(self.chk_diagonals)

        noop = lambda: None
        self.sld_boid_count, self.txt_boid_count = self._int_slider(
            sec_boids, "Agent Count", 1, 100, 20, noop)
        self.sld_boid_steps, self.txt_boid_steps = self._int_slider(
            sec_boids, "Trail Steps", 10, 2000, 500, noop)
        self.sld_boid_turn, self.txt_boid_turn = self._float_slider(
            sec_boids, "Turn Chance", 0.0, 1.0, 0.3, noop)
        self.sld_boid_straight, self.txt_boid_straight = self._float_slider(
            sec_boids, "Straight Threshold", 0.0, 90.0, 5.0, noop)
        self.chk_boid_overlap = forms.CheckBox()
        self.chk_boid_overlap.Text = "Allow Path Overlap"
        self.chk_boid_overlap.Checked = False
        sec_boids.AddRow(self.chk_boid_overlap)
        self.sld_boid_overlap, self.txt_boid_overlap = self._float_slider(
            sec_boids, "Overlap Amount", 0.0, 1.0, 0.0, noop)
        self.sld_boid_min_a, self.txt_boid_min_a = self._float_slider(
            sec_boids, "Min Turn Angle", 0.0, 180.0, 45.0, noop)
        self.sld_boid_max_a, self.txt_boid_max_a = self._float_slider(
            sec_boids, "Max Turn Angle", 0.0, 180.0, 90.0, noop)
        self.sld_boid_thick, self.txt_boid_thick = self._int_slider(
            sec_boids, "Trail Width", 1, 8, 2, noop)
        self.sld_pipe_rad, self.txt_pipe_rad = self._float_slider(
            sec_boids, "Pipe Radius", 0.0, 5.0, 0.0, noop)
        self.sld_boid_offset, self.txt_boid_offset = self._float_slider(
            sec_boids, "Offset Distance", 0.0, 10.0, 0.0, noop)
        self.sld_boid_tight, self.txt_boid_tight = self._int_slider(
            sec_boids, "Offset Tightness", 0, 50, 10, noop)
        self.sld_boid_fillet, self.txt_boid_fillet = self._float_slider(
            sec_boids, "Fillet Radius", 0.0, 10.0, 0.0, noop)

        lbl_battr_hdr = self._bold("Boid Path Attractor")
        sec_boids.AddRow(lbl_battr_hdr)

        btn_pick_battr = forms.Button()
        btn_pick_battr.Text = "Assign Boid Attr Curves"
        btn_pick_battr.Click += self._on_pick_boid_attractor
        btn_clr_battr = forms.Button()
        btn_clr_battr.Text = "Clear"
        btn_clr_battr.Click += self._on_clear_boid_attractor
        sec_boids.AddRow(self._hrow(btn_pick_battr, btn_clr_battr))

        self.lbl_boid_attr = forms.Label()
        self.lbl_boid_attr.Text = "Boid Attractors: 0"
        sec_boids.AddRow(self.lbl_boid_attr)

        self.sld_boid_attr_s, self.txt_boid_attr_s = self._float_slider(
            sec_boids, "Boid Attr Strength", 0.0, 1.0, 0.5, noop)

        btn_run_boids = forms.Button()
        btn_run_boids.Text = "Run Boids"
        btn_run_boids.Click += self._on_run_boids
        btn_clr_trails = forms.Button()
        btn_clr_trails.Text = "Clear Trails"
        btn_clr_trails.Click += self._on_clear_trails
        btn_bake_trails = forms.Button()
        btn_bake_trails.Text = "Bake Trails"
        btn_bake_trails.Click += self._on_bake_trails
        btn_bake_trails_brep = forms.Button()
        btn_bake_trails_brep.Text = "Bake Trails Brep"
        btn_bake_trails_brep.Click += self._on_bake_trails_brep
        sec_boids.AddRow(self._hrow(btn_run_boids, btn_clr_trails, btn_bake_trails))
        sec_boids.AddRow(btn_bake_trails_brep)

        self.btn_trail_col = forms.Button()
        self.btn_trail_col.Text = "Trail Colour"
        self.btn_trail_col.BackgroundColor = drawing.Color.FromArgb(255, 120, 50)
        self.btn_trail_col.Click += self._on_pick_trail_color
        sec_boids.AddRow(self.btn_trail_col)

        self.lbl_boid_status = forms.Label()
        self.lbl_boid_status.Text = "Boids: idle"
        sec_boids.AddRow(self.lbl_boid_status)

        layout.AddRow(self._make_section("Edge Boids", sec_boids))

        # -- melt / blend --------------------------------------------------
        sec_melt = forms.DynamicLayout()
        sec_melt.DefaultSpacing = drawing.Size(4, 4)

        self.sld_melt_smooth, self.txt_melt_smooth = self._int_slider(
            sec_melt, "Smooth Iterations", 0, 100, 10, noop)
        self.sld_melt_factor, self.txt_melt_factor = self._float_slider(
            sec_melt, "Smooth Strength", 0.01, 1.0, 0.5, noop)

        btn_melt = forms.Button()
        btn_melt.Text = "Melt"
        btn_melt.Click += self._on_melt
        btn_clr_melt = forms.Button()
        btn_clr_melt.Text = "Clear Melt"
        btn_clr_melt.Click += self._on_clear_melt
        btn_bake_melt = forms.Button()
        btn_bake_melt.Text = "Bake Melt"
        btn_bake_melt.Click += self._on_bake_melt
        sec_melt.AddRow(self._hrow(btn_melt, btn_clr_melt, btn_bake_melt))

        self.lbl_melt_status = forms.Label()
        self.lbl_melt_status.Text = "Melt: idle"
        sec_melt.AddRow(self.lbl_melt_status)

        layout.AddRow(self._make_section("Melt / Blend", sec_melt))

        # -- display -------------------------------------------------------
        sec_disp = forms.DynamicLayout()
        sec_disp.DefaultSpacing = drawing.Size(4, 4)
        self.chk_bounds = forms.CheckBox()
        self.chk_bounds.Text = "Show Bounding Box"
        self.chk_bounds.Checked = True
        self.chk_bounds.CheckedChanged += lambda s, e: self._mark_display()

        self.chk_edges = forms.CheckBox()
        self.chk_edges.Text = "Show Voxel Edges"
        self.chk_edges.Checked = True
        self.chk_edges.CheckedChanged += lambda s, e: self._mark_display()
        sec_disp.AddRow(self._hrow(self.chk_bounds, self.chk_edges))

        self.chk_vcol = forms.CheckBox()
        self.chk_vcol.Text = "Vertex Colours"
        self.chk_vcol.Checked = True
        self.chk_vcol.CheckedChanged += lambda s, e: self._toggle_vertex_colors()
        sec_disp.AddRow(self.chk_vcol)

        self.btn_vcol = forms.Button()
        self.btn_vcol.Text = "Voxel Colour"
        self.btn_vcol.BackgroundColor = drawing.Color.FromArgb(100, 180, 255)
        self.btn_vcol.Click += self._on_pick_voxel_color

        self.btn_ecol = forms.Button()
        self.btn_ecol.Text = "Edge Colour"
        self.btn_ecol.BackgroundColor = drawing.Color.FromArgb(40, 40, 40)
        self.btn_ecol.Click += self._on_pick_edge_color

        self.btn_bcol = forms.Button()
        self.btn_bcol.Text = "Bounds Colour"
        self.btn_bcol.BackgroundColor = drawing.Color.FromArgb(80, 80, 80)
        self.btn_bcol.Click += self._on_pick_bounds_color
        sec_disp.AddRow(self._hrow(self.btn_vcol, self.btn_ecol, self.btn_bcol))

        lbl_low = forms.Label()
        lbl_low.Text = "Low"
        lbl_low.Width = 30
        self.gradient_bar = forms.Drawable()
        self.gradient_bar.Size = drawing.Size(200, 18)
        self.gradient_bar.Paint += self._on_gradient_paint
        lbl_high = forms.Label()
        lbl_high.Text = "High"
        lbl_high.Width = 30
        sec_disp.AddRow(self._hrow(lbl_low, self.gradient_bar, lbl_high))

        lbl_grad_desc = forms.Label()
        lbl_grad_desc.Text = "Colour = noise density (threshold \u2192 max)"
        sec_disp.AddRow(lbl_grad_desc)

        layout.AddRow(self._make_section("Display", sec_disp))

        # -- controls ------------------------------------------------------
        # Refresh = full recompute of noise + mesh. Bake = add mesh to doc
        # with colours. Bake Brep = convert each voxel to a NURBS polysurface
        # with no colour. Clear = remove all preview geometry.
        layout.AddRow(self._bold("Controls"))

        btn_refresh = forms.Button()
        btn_refresh.Text = "Refresh"
        btn_refresh.Click += self._on_refresh

        btn_bake = forms.Button()
        btn_bake.Text = "Bake"
        btn_bake.Click += self._on_bake

        btn_bake_brep = forms.Button()
        btn_bake_brep.Text = "Bake Brep"
        btn_bake_brep.Click += self._on_bake_brep

        btn_clear = forms.Button()
        btn_clear.Text = "Clear"
        btn_clear.Click += self._on_clear

        try:
            ctrl_wrap = forms.WrapPanel()
            ctrl_wrap.Spacing = drawing.Size(4, 4)
            ctrl_wrap.Items.Add(btn_refresh)
            ctrl_wrap.Items.Add(btn_bake)
            ctrl_wrap.Items.Add(btn_bake_brep)
            ctrl_wrap.Items.Add(btn_clear)
            layout.AddRow(ctrl_wrap)
        except Exception:
            layout.AddRow(self._hrow(btn_refresh, btn_bake))
            layout.AddRow(self._hrow(btn_bake_brep, btn_clear))

        self.lbl_status = forms.Label()
        self.lbl_status.Text = "Ready"
        layout.AddRow(self.lbl_status)

        scrollable = forms.Scrollable()
        scrollable.ExpandContentWidth = True
        scrollable.ExpandContentHeight = False
        scrollable.Content = layout
        self.Content = scrollable
        self.Closed += self._on_closed

    # -- widget factories --------------------------------------------------
    def _bold(self, text):
        """Create a bold label for section headers."""
        lbl = forms.Label()
        lbl.Text = text
        lbl.Font = drawing.Font(lbl.Font.Family, lbl.Font.Size, drawing.FontStyle.Bold)
        return lbl

    def _hrow(self, *controls):
        """Pack multiple controls into a single-cell horizontal TableLayout.
        Prevents DynamicLayout from creating extra columns that break
        single-item row stretching."""
        tbl = forms.TableLayout()
        tbl.Spacing = drawing.Size(4, 0)
        cells = [forms.TableCell(c, True) for c in controls]
        tbl.Rows.Add(forms.TableRow(*cells))
        return tbl

    def _make_section(self, title, content, expanded=False):
        """Wrap a layout in a collapsible Expander panel."""
        try:
            exp = forms.Expander()
            exp.Header = self._bold(title)
            exp.Expanded = expanded
            exp.Content = content
            exp.Padding = drawing.Padding(0, 2, 0, 2)
            return exp
        except Exception:
            gb = forms.GroupBox()
            gb.Text = title
            gb.Content = content
            gb.Padding = drawing.Padding(4)
            return gb

    def _int_slider(self, layout, name, lo, hi, default, on_change):
        """Create a label + slider + text box row for integer parameters.
        Slider and text box stay synced; on_change fires after either changes."""
        lbl = forms.Label()
        lbl.Text = name
        lbl.Width = 105

        sld = forms.Slider()
        sld.MinValue = lo
        sld.MaxValue = hi
        sld.Value = default

        txt = forms.TextBox()
        txt.Text = str(default)
        txt.Width = 50

        guard = {"u": False}

        def _sld(s, e):
            if guard["u"]:
                return
            guard["u"] = True
            txt.Text = str(sld.Value)
            guard["u"] = False
            on_change()

        def _txt(s, e):
            if guard["u"]:
                return
            guard["u"] = True
            try:
                v = int(txt.Text)
                if v >= 1:
                    sld.Value = max(lo, min(hi, v))
            except:
                pass
            guard["u"] = False
            on_change()

        sld.ValueChanged += _sld
        txt.TextChanged += _txt
        row = forms.TableLayout()
        row.Spacing = drawing.Size(4, 0)
        row.Rows.Add(forms.TableRow(
            forms.TableCell(lbl, False),
            forms.TableCell(sld, True),
            forms.TableCell(txt, False)))
        layout.AddRow(row)
        return sld, txt

    def _float_slider(self, layout, name, lo, hi, default, on_change):
        """Create a label + slider + text box row for float parameters.
        Slider range 0-1000 is mapped to [lo, hi]. Text box accepts direct input."""
        lbl = forms.Label()
        lbl.Text = name
        lbl.Width = 105

        sld = forms.Slider()
        sld.MinValue = 0
        sld.MaxValue = 1000
        sld.Value = int((default - lo) / (hi - lo) * 1000)

        txt = forms.TextBox()
        txt.Text = "{:.3f}".format(default)
        txt.Width = 50

        guard = {"u": False}

        def _sld(s, e):
            if guard["u"]:
                return
            guard["u"] = True
            fv = lo + (sld.Value / 1000.0) * (hi - lo)
            txt.Text = "{:.3f}".format(fv)
            guard["u"] = False
            on_change()

        def _txt(s, e):
            if guard["u"]:
                return
            guard["u"] = True
            try:
                fv = float(txt.Text)
                if fv >= 0:
                    clamped = max(lo, min(hi, fv))
                    sld.Value = int((clamped - lo) / (hi - lo) * 1000)
            except:
                pass
            guard["u"] = False
            on_change()

        sld.ValueChanged += _sld
        txt.TextChanged += _txt
        row = forms.TableLayout()
        row.Spacing = drawing.Size(4, 0)
        row.Rows.Add(forms.TableRow(
            forms.TableCell(lbl, False),
            forms.TableCell(sld, True),
            forms.TableCell(txt, False)))
        layout.AddRow(row)
        return sld, txt

    def _fval(self, txt, sld, lo, hi):
        """Read a float from the text box, falling back to the slider position."""
        try:
            return float(txt.Text)
        except:
            return lo + (sld.Value / 1000.0) * (hi - lo)

    # -- dirty flags -------------------------------------------------------
    def _mark_compute(self):
        """Flag that noise field needs full recomputation (heavy)."""
        if self.chk_live.Checked == True:
            self._compute_dirty = True

    def _mark_display(self):
        """Flag that only mesh rebuild is needed (rotation, scale, colour)."""
        if self.chk_live.Checked == True:
            self._display_dirty = True

    # -- timer tick (debounce) ---------------------------------------------
    def _on_timer_tick(self, sender, e):
        """Fires every 0.12s. If compute dirty, do full regenerate (which also
        rebuilds display). If only display dirty, just rebuild mesh.
        Also advances growth playback when playing."""
        if self._compute_dirty:
            self._compute_dirty = False
            self._display_dirty = False
            self._full_regenerate()
        elif self._display_dirty:
            self._display_dirty = False
            self._display_only()
        if self._growth_playing and self._growth_total_frames > 1:
            try:
                speed = max(1, self._ival(
                    self.txt_growth_speed, self.sld_growth_speed))
            except Exception:
                speed = 1
            self._growth_frame += speed
            if self._growth_frame >= self._growth_total_frames:
                self._growth_frame = self._growth_total_frames - 1
                self._growth_playing = False
                self.btn_play_growth.Text = "Play"
            self._update_growth_frame()

    # -- read params -------------------------------------------------------
    def _ival(self, txt, sld):
        """Read an integer from the text box, falling back to the slider value."""
        try:
            v = int(txt.Text)
            if v >= 1:
                return v
        except:
            pass
        return sld.Value

    def _read_params(self):
        """Collect all UI parameter values into a single tuple for passing
        to generate() and update_display(). When a bounds geometry is
        assigned, cell sizes are derived from its bounding box."""
        gx = self._ival(self.txt_gx, self.sld_gx)
        gy = self._ival(self.txt_gy, self.sld_gy)
        gz = self._ival(self.txt_gz, self.sld_gz)
        cw = self._fval(self.txt_cw, self.sld_cw, 0.1, 50.0)
        cl = self._fval(self.txt_cl, self.sld_cl, 0.1, 50.0)
        ch = self._fval(self.txt_ch, self.sld_ch, 0.1, 50.0)
        if self.bound_geometry and self.bound_geometry.IsValid:
            bb = self.bound_geometry
            cw = (bb.Max.X - bb.Min.X) / max(gx, 1)
            cl = (bb.Max.Y - bb.Min.Y) / max(gy, 1)
            ch = (bb.Max.Z - bb.Min.Z) / max(gz, 1)
        scale = self._fval(self.txt_scale, self.sld_scale, 0.01, 1.0)
        thresh = self._fval(self.txt_thresh, self.sld_thresh, 0.0, 1.0)
        octaves = self._ival(self.txt_oct, self.sld_oct)
        seed = self._ival(self.txt_seed, self.sld_seed)
        hollow = self.chk_hollow.Checked == True
        shell = self._ival(self.txt_shell, self.sld_shell)
        use_attr = self.chk_attr.Checked == True
        attr_r = self._fval(self.txt_attr_r, self.sld_attr_r, 1.0, 50.0)
        attr_s = self._fval(self.txt_attr_s, self.sld_attr_s, 0.0, 1.0)
        use_base = self.chk_use_base.Checked == True
        base_r = self._fval(self.txt_base_r, self.sld_base_r, 1.0, 80.0)
        base_s = self._fval(self.txt_base_s, self.sld_base_s, 0.0, 1.0)
        base_carve = self.chk_carve.Checked == True
        field_mode = self.dd_field_mode.SelectedIndex
        tpms_scale = self._fval(self.txt_tpms_scale, self.sld_tpms_scale, 0.1, 50.0)
        tpms_thick = self._fval(self.txt_tpms_thick, self.sld_tpms_thick, 0.01, 3.0)
        sdf_falloff = self._fval(self.txt_sdf_falloff, self.sld_sdf_falloff, 1.0, 100.0)
        sdf_invert = self.chk_sdf_invert.Checked == True
        rd_feed = self._fval(self.txt_rd_feed, self.sld_rd_feed, 0.01, 0.1)
        rd_kill = self._fval(self.txt_rd_kill, self.sld_rd_kill, 0.01, 0.1)
        rd_da = self._fval(self.txt_rd_da, self.sld_rd_da, 0.05, 1.0)
        rd_db = self._fval(self.txt_rd_db, self.sld_rd_db, 0.01, 0.5)
        rd_iters = self._ival(self.txt_rd_iters, self.sld_rd_iters)
        comp_mode = self.dd_blend_mode.SelectedIndex
        comp_weight = self._fval(self.txt_comp_weight, self.sld_comp_weight, 0.0, 1.0)
        pw_width = self._fval(self.txt_pw_width, self.sld_pw_width, 0.5, 20.0)
        pw_falloff = self._fval(self.txt_pw_falloff, self.sld_pw_falloff, 1.0, 30.0)
        pw_invert = self.chk_pw_invert.Checked == True
        sun_az_deg = self._fval(self.txt_sun_az, self.sld_sun_az, 0.0, 360.0)
        sun_el_deg = self._fval(self.txt_sun_el, self.sld_sun_el, 0.0, 90.0)
        sun_az = sun_az_deg * math.pi / 180.0
        sun_el = sun_el_deg * math.pi / 180.0
        view_radius = self._fval(self.txt_view_radius, self.sld_view_radius, 0.5, 30.0)
        view_falloff = self._fval(self.txt_view_falloff, self.sld_view_falloff, 0.5, 20.0)
        grav_mode = self.dd_grav_mode.SelectedIndex
        grav_str = self._fval(self.txt_grav_str, self.sld_grav_str, 0.0, 2.0)
        multi_a = self.dd_multi_a.SelectedIndex
        multi_b = self.dd_multi_b.SelectedIndex
        return (gx, gy, gz, cw, cl, ch, scale, thresh, octaves, seed,
                hollow, shell, use_attr, attr_r, attr_s,
                use_base, base_r, base_s, base_carve,
                field_mode, tpms_scale, tpms_thick,
                sdf_falloff, sdf_invert,
                rd_feed, rd_kill, rd_da, rd_db, rd_iters,
                comp_mode, comp_weight,
                pw_width, pw_falloff, pw_invert,
                sun_az, sun_el,
                view_radius, view_falloff,
                grav_mode, grav_str,
                multi_a, multi_b)

    # -- compute grid origin -----------------------------------------------
    def _grid_origin(self, gx, gy, gz, cw, cl, ch):
        """Return the world-space corner of the grid.
        Priority: bounds geometry > auto-center on base > world origin."""
        if self.bound_geometry and self.bound_geometry.IsValid:
            return rg.Point3d(self.bound_geometry.Min)
        if (self.chk_auto_center.Checked == True and
                self.base_geometries and
                self.chk_use_base.Checked == True):
            bbox = rg.BoundingBox.Empty
            for geo in self.base_geometries:
                gb = geo.GetBoundingBox(True)
                bbox.Union(gb)
            if bbox.IsValid:
                c = bbox.Center
                return rg.Point3d(
                    c.X - (gx * cw) * 0.5,
                    c.Y - (gy * cl) * 0.5,
                    c.Z - (gz * ch) * 0.5)
        return rg.Point3d.Origin

    # -- full regenerate ---------------------------------------------------
    def _full_regenerate(self):
        """Recompute field from scratch, rebuild mesh, and update display.
        Triggered by changes to grid size, field params, or attractors."""
        p = self._read_params()
        gx, gy, gz = p[0], p[1], p[2]
        cw, cl, ch = p[3], p[4], p[5]
        scale, thresh, octaves, seed = p[6], p[7], p[8], p[9]
        hollow, shell = p[10], p[11]
        use_attr, attr_r, attr_s = p[12], p[13], p[14]
        use_base, base_r, base_s, base_carve = p[15], p[16], p[17], p[18]
        field_mode = p[19]
        tpms_scale, tpms_thick = p[20], p[21]
        sdf_falloff, sdf_invert = p[22], p[23]
        rd_feed, rd_kill, rd_da, rd_db, rd_iters = p[24], p[25], p[26], p[27], p[28]
        comp_mode, comp_weight = p[29], p[30]
        pw_width, pw_falloff, pw_invert = p[31], p[32], p[33]
        sun_az, sun_el = p[34], p[35]
        view_radius, view_falloff = p[36], p[37]
        grav_mode, grav_str = p[38], p[39]
        multi_a, multi_b = p[40], p[41]

        f = self.system.fields
        f.dla_particles = self._ival(self.txt_dla_particles, self.sld_dla_particles)
        f.dla_stickiness = self._fval(self.txt_dla_stick, self.sld_dla_stick, 0.1, 1.0)
        f.dla_bias_z = self._fval(self.txt_dla_bias, self.sld_dla_bias, -1.0, 1.0)
        f.dla_seed_mode = self.dd_dla_seed.SelectedIndex
        f.sc_density = self._fval(self.txt_sc_density, self.sld_sc_density, 0.05, 1.0)
        f.sc_kill_dist = self._ival(self.txt_sc_kill, self.sld_sc_kill)
        f.sc_influence_radius = self._ival(self.txt_sc_inf, self.sld_sc_inf)
        f.sc_step_length = self._ival(self.txt_sc_step, self.sld_sc_step)
        f.sc_root_mode = self.dd_sc_root.SelectedIndex
        f.sc_iterations = self._ival(self.txt_sc_iters, self.sld_sc_iters)
        f.eden_birth = self._ival(self.txt_eden_birth, self.sld_eden_birth)
        f.eden_survival_lo = self._ival(self.txt_eden_surv_lo, self.sld_eden_surv_lo)
        f.eden_survival_hi = self._ival(self.txt_eden_surv_hi, self.sld_eden_surv_hi)
        f.eden_iterations = self._ival(self.txt_eden_iters, self.sld_eden_iters)
        f.eden_seed_density = self._fval(
            self.txt_eden_seed_d, self.sld_eden_seed_d, 0.01, 0.5)
        f.eden_field_bias = self._fval(
            self.txt_eden_bias, self.sld_eden_bias, 0.0, 1.0)
        f.phys_agents = self._ival(self.txt_phys_agents, self.sld_phys_agents)
        f.phys_sensor_angle = self._fval(
            self.txt_phys_sa, self.sld_phys_sa, 10.0, 90.0)
        f.phys_sensor_dist = self._fval(
            self.txt_phys_sd, self.sld_phys_sd, 1.0, 5.0)
        f.phys_turn_angle = self._fval(
            self.txt_phys_ta, self.sld_phys_ta, 10.0, 90.0)
        f.phys_deposit = self._fval(
            self.txt_phys_dep, self.sld_phys_dep, 0.1, 5.0)
        f.phys_decay = self._fval(
            self.txt_phys_decay, self.sld_phys_decay, 0.01, 0.5)
        f.phys_iterations = self._ival(self.txt_phys_iters, self.sld_phys_iters)
        f.myc_initial_tips = self._ival(self.txt_myc_tips, self.sld_myc_tips)
        f.myc_branch_prob = self._fval(
            self.txt_myc_branch, self.sld_myc_branch, 0.0, 0.3)
        f.myc_branch_angle = self._fval(
            self.txt_myc_bangle, self.sld_myc_bangle, 10.0, 90.0)
        f.myc_turn_rate = self._fval(
            self.txt_myc_turn, self.sld_myc_turn, 1.0, 60.0)
        f.myc_anastomosis = self._fval(
            self.txt_myc_anast, self.sld_myc_anast, 0.0, 1.0)
        f.myc_iterations = self._ival(self.txt_myc_iters, self.sld_myc_iters)
        f.myc_max_tips = self._ival(self.txt_myc_max_tips, self.sld_myc_max_tips)
        f.growth_attract_radius = self._fval(
            self.txt_gattr_r, self.sld_gattr_r, 1.0, 100.0)
        f.growth_attract_strength = self._fval(
            self.txt_gattr_s, self.sld_gattr_s, 0.1, 3.0)
        f.growth_repel_radius = self._fval(
            self.txt_grep_r, self.sld_grep_r, 1.0, 100.0)
        f.growth_repel_strength = self._fval(
            self.txt_grep_s, self.sld_grep_s, 0.1, 3.0)

        origin = self._grid_origin(gx, gy, gz, cw, cl, ch)
        f.growth_origin = origin
        f.growth_cell_sizes = (cw, cl, ch)
        total = gx * gy * gz
        field_names = ["Perlin", "Gyroid", "Schwarz-P", "Diamond", "SDF",
                       "Curl", "R-D", "Composite",
                       "Pathway", "Solar", "View Corridor", "Gravity",
                       "Multi-Layer", "DLA", "Space Colon.", "Eden/CA",
                       "Physarum", "Mycelium"]
        fname = field_names[field_mode] if field_mode < len(field_names) else "?"
        self.lbl_status.Text = "Computing {} cells ({})...".format(total, fname)

        voxels = self.system.generate(
            gx, gy, gz, cw, cl, ch, scale, thresh, octaves, seed,
            use_attr, self.attractor_pts, self.attractor_curves,
            self.attractor_geos, attr_r, attr_s,
            hollow, shell,
            use_base, self.base_geometries, base_r, base_s, base_carve,
            origin,
            field_mode, tpms_scale, tpms_thick,
            sdf_falloff, sdf_invert,
            rd_feed, rd_kill, rd_da, rd_db, rd_iters,
            comp_mode, comp_weight,
            pw_width, pw_falloff, pw_invert,
            sun_az, sun_el,
            view_radius, view_falloff,
            grav_mode, grav_str,
            multi_a, multi_b)

        self.system.boid_trails = []
        self.system.boid_graph = {}
        self.system.conduit.trail_polylines = []

        self._growth_playing = False
        self.btn_play_growth.Text = "Play"

        if field_mode in (13, 14, 15, 16, 17):
            raw_trails = self.system.fields.growth_trails
            raw_points = self.system.fields.growth_points
            ox = origin.X
            oy = origin.Y
            oz = origin.Z
            hw = cw * 0.5
            hl = cl * 0.5
            hh = ch * 0.5
            _Pt = rg.Point3d
            _Poly = rg.Polyline

            def _to_world_pt(pt):
                return _Pt(ox + pt[0] * cw + hw,
                           oy + pt[1] * cl + hl,
                           oz + pt[2] * ch + hh)

            def _to_world_trail(tr):
                pl = _Poly()
                for pt in tr:
                    pl.Add(_to_world_pt(pt))
                return pl if pl.Count >= 2 else None

            w_trails = []
            for tr in raw_trails:
                wt = _to_world_trail(tr)
                if wt:
                    w_trails.append(wt)
            w_points = [_to_world_pt(pt) for pt in raw_points]
            self.system.conduit.growth_trails = w_trails
            self.system.conduit.growth_points = w_points

            fields = self.system.fields
            pb_mode = fields.growth_playback_mode
            if pb_mode == "cumulative":
                self._all_world_trails = w_trails
                self._all_world_points = w_points
                self._world_frame_snapshots = []
                self._growth_total_frames = len(fields.growth_frame_indices)
            elif pb_mode == "snapshot":
                self._all_world_trails = []
                self._all_world_points = []
                snaps = []
                for raw_t, raw_p in fields.growth_frame_snapshots:
                    ft = []
                    for tr in raw_t:
                        wt = _to_world_trail(tr)
                        if wt:
                            ft.append(wt)
                    fp = [_to_world_pt(pt) for pt in raw_p]
                    snaps.append((ft, fp))
                self._world_frame_snapshots = snaps
                self._growth_total_frames = len(snaps)
            else:
                self._growth_total_frames = 0
            self._growth_frame = max(0, self._growth_total_frames - 1)
            self.lbl_growth_frame.Text = "Frame: {} / {}".format(
                self._growth_frame + 1, self._growth_total_frames)
        else:
            self.system.conduit.growth_trails = []
            self.system.conduit.growth_points = []
            self._all_world_trails = []
            self._all_world_points = []
            self._world_frame_snapshots = []
            self._growth_total_frames = 0
            self._growth_frame = 0
            self.lbl_growth_frame.Text = "Frame: — / —"

        show_bounds = self.chk_bounds.Checked == True
        show_edges = self.chk_edges.Checked == True
        use_custom = self.chk_use_custom.Checked == True
        custom_scale = self._fval(self.txt_custom_s, self.sld_custom_s, 0.1, 2.0)
        do_rot = self.chk_rotate.Checked == True
        rot_deg = self._fval(self.txt_rot_angle, self.sld_rot_angle, 0.0, 360.0)
        rot_rad = rot_deg * math.pi / 180.0
        rot_axis = self.dd_rot_axis.SelectedIndex
        do_rot2 = self.chk_rotate2.Checked == True
        rot_deg2 = self._fval(self.txt_rot_angle2, self.sld_rot_angle2, 0.0, 360.0)
        rot_rad2 = rot_deg2 * math.pi / 180.0
        rot_axis2 = self.dd_rot_axis2.SelectedIndex
        do_dscale = self.chk_dscale.Checked == True
        dscale_min = self._fval(self.txt_dscale_min, self.sld_dscale_min, 0.01, 1.0)
        self.system.update_display(
            voxels, cw, cl, ch, self.voxel_color,
            show_bounds, self.bounds_color,
            show_edges, self.edge_color,
            gx, gy, gz, origin,
            use_custom, custom_scale,
            do_rot, rot_rad, rot_axis,
            do_rot2, rot_rad2, rot_axis2,
            do_dscale, dscale_min)

        self.lbl_status.Text = "Showing {} / {} voxels".format(len(voxels), total)
        self.gradient_bar.Invalidate()

    # -- display-only refresh ----------------------------------------------
    def _display_only(self):
        """Rebuild mesh from existing voxel data without recomputing noise.
        Triggered by rotation, scale, colour, or display toggle changes."""
        p = self._read_params()
        gx, gy, gz = p[0], p[1], p[2]
        cw, cl, ch = p[3], p[4], p[5]
        origin = self._grid_origin(gx, gy, gz, cw, cl, ch)
        voxels = self.system.voxels
        show_bounds = self.chk_bounds.Checked == True
        show_edges = self.chk_edges.Checked == True
        use_custom = self.chk_use_custom.Checked == True
        custom_scale = self._fval(self.txt_custom_s, self.sld_custom_s, 0.1, 2.0)
        do_rot = self.chk_rotate.Checked == True
        rot_deg = self._fval(self.txt_rot_angle, self.sld_rot_angle, 0.0, 360.0)
        rot_rad = rot_deg * math.pi / 180.0
        rot_axis = self.dd_rot_axis.SelectedIndex
        do_rot2 = self.chk_rotate2.Checked == True
        rot_deg2 = self._fval(self.txt_rot_angle2, self.sld_rot_angle2, 0.0, 360.0)
        rot_rad2 = rot_deg2 * math.pi / 180.0
        rot_axis2 = self.dd_rot_axis2.SelectedIndex
        do_dscale = self.chk_dscale.Checked == True
        dscale_min = self._fval(self.txt_dscale_min, self.sld_dscale_min, 0.01, 1.0)
        self.system.update_display(
            voxels, cw, cl, ch, self.voxel_color,
            show_bounds, self.bounds_color,
            show_edges, self.edge_color,
            gx, gy, gz, origin,
            use_custom, custom_scale,
            do_rot, rot_rad, rot_axis,
            do_rot2, rot_rad2, rot_axis2,
            do_dscale, dscale_min)
        self.gradient_bar.Invalidate()

    # -- button handlers ---------------------------------------------------
    def _on_refresh(self, sender, e):
        """Manual full recompute (ignores live-update toggle)."""
        self._full_regenerate()

    def _on_bake(self, sender, e):
        """Add the voxel mesh to the Rhino document as a mesh object."""
        p = self._read_params()
        gx, gy, gz = p[0], p[1], p[2]
        cw, cl, ch = p[3], p[4], p[5]
        origin = self._grid_origin(gx, gy, gz, cw, cl, ch)
        use_vc = self.chk_vcol.Checked == True
        self.system.bake(self.voxel_color, origin, use_vc)
        self.lbl_status.Text = "Baked {} voxels to document".format(len(self.system.voxels))

    def _on_bake_brep(self, sender, e):
        """Convert each voxel to a NURBS brep (polysurface) and add to document.
        Creates planar surfaces from mesh face corners, then joins per voxel.
        No colour or material attributes are applied."""
        mesh = self.system.conduit.mesh
        if not mesh or mesh.Faces.Count == 0:
            self.lbl_status.Text = "Nothing to bake"
            return
        self.lbl_status.Text = "Converting to Brep..."
        try:
            tol = sc.doc.ModelAbsoluteTolerance
            mverts = mesh.Vertices
            mfaces = mesh.Faces
            if self.system.custom_base_mesh:
                fpv = self.system.custom_base_mesh.Faces.Count
            else:
                fpv = 6
            total = mfaces.Count
            num_groups = total // fpv
            count = 0
            _Pt3d = rg.Point3d
            _Corner = rg.Brep.CreateFromCornerPoints
            _Join = rg.Brep.JoinBreps
            for gi in range(num_groups):
                start = gi * fpv
                surfs = []
                for fi in range(start, start + fpv):
                    f = mfaces[fi]
                    if f.IsQuad:
                        srf = _Corner(
                            _Pt3d(mverts[f.A]), _Pt3d(mverts[f.B]),
                            _Pt3d(mverts[f.C]), _Pt3d(mverts[f.D]), tol)
                    else:
                        srf = _Corner(
                            _Pt3d(mverts[f.A]), _Pt3d(mverts[f.B]),
                            _Pt3d(mverts[f.C]), tol)
                    if srf:
                        surfs.append(srf)
                if surfs:
                    joined = _Join(surfs, tol)
                    if joined:
                        for b in joined:
                            sc.doc.Objects.AddBrep(b)
                            count += 1
                    else:
                        for s in surfs:
                            sc.doc.Objects.AddBrep(s)
                            count += 1
            sc.doc.Views.Redraw()
            self.lbl_status.Text = "Baked {} brep(s)".format(count)
        except Exception as ex:
            self.lbl_status.Text = "Brep failed: {}".format(str(ex))

    def _on_clear(self, sender, e):
        """Remove all preview geometry and reset state."""
        self.system.conduit.mesh = None
        self.system.conduit.edge_mesh = None
        self.system.conduit.bound_lines = []
        self.system.conduit.trail_polylines = []
        self.system.conduit.pipe_mesh = None
        self.system.conduit.show_pipes = False
        self.system.conduit.melt_mesh = None
        self.system.conduit.show_melt = False
        self.system.voxels = []
        self.system.boid_trails = []
        self.system.boid_graph = {}
        sc.doc.Views.Redraw()
        self.lbl_status.Text = "Cleared"
        self.lbl_boid_status.Text = "Boids: idle"
        self.lbl_melt_status.Text = "Melt: idle"

    def _on_closed(self, sender, e):
        """Clean up when the dialog window is closed."""
        self._timer.Stop()
        self.system.dispose()

    # -- base geometry -----------------------------------------------------
    def _on_pick_base(self, sender, e):
        """Prompt user to select base geometry objects from the Rhino viewport."""
        self.Visible = False
        go = Rhino.Input.Custom.GetObject()
        go.SetCommandPrompt("Select base geometry (curves, meshes, surfaces, breps)")
        go.GeometryFilter = (
            Rhino.DocObjects.ObjectType.Curve |
            Rhino.DocObjects.ObjectType.Mesh |
            Rhino.DocObjects.ObjectType.Surface |
            Rhino.DocObjects.ObjectType.Brep)
        go.EnablePreSelect(False, True)
        go.GetMultiple(1, 0)
        if go.CommandResult() == Rhino.Commands.Result.Success:
            self.base_geometries = []
            for i in range(go.ObjectCount):
                geo = go.Object(i).Geometry()
                if geo:
                    self.base_geometries.append(geo.Duplicate())
            self.lbl_base.Text = "Base: {} object(s)".format(len(self.base_geometries))
            self.chk_use_base.Checked = True
        self.Visible = True
        self._full_regenerate()

    def _on_clear_base(self, sender, e):
        self.base_geometries = []
        self.chk_use_base.Checked = False
        self.lbl_base.Text = "Base: None"
        self._full_regenerate()

    # -- bounds geometry ---------------------------------------------------
    def _on_pick_bounds(self, sender, e):
        """Prompt user to select a geometry whose bounding box defines the grid extent."""
        self.Visible = False
        go = Rhino.Input.Custom.GetObject()
        go.SetCommandPrompt("Select bounding geometry (box, mesh, brep, surface)")
        go.GeometryFilter = (
            Rhino.DocObjects.ObjectType.Curve |
            Rhino.DocObjects.ObjectType.Mesh |
            Rhino.DocObjects.ObjectType.Surface |
            Rhino.DocObjects.ObjectType.Brep)
        go.EnablePreSelect(False, True)
        go.GetMultiple(1, 0)
        if go.CommandResult() == Rhino.Commands.Result.Success:
            bbox = rg.BoundingBox.Empty
            for i in range(go.ObjectCount):
                geo = go.Object(i).Geometry()
                if geo:
                    bbox.Union(geo.GetBoundingBox(True))
            if bbox.IsValid:
                self.bound_geometry = bbox
                sx = bbox.Max.X - bbox.Min.X
                sy = bbox.Max.Y - bbox.Min.Y
                sz = bbox.Max.Z - bbox.Min.Z
                self.lbl_bounds.Text = "Bounds: {:.1f} x {:.1f} x {:.1f}".format(sx, sy, sz)
            else:
                self.bound_geometry = None
                self.lbl_bounds.Text = "Bounds: invalid selection"
        self.Visible = True
        self._full_regenerate()

    def _on_clear_bounds(self, sender, e):
        self.bound_geometry = None
        self.lbl_bounds.Text = "Bounds: None"
        self._full_regenerate()

    # -- field mode --------------------------------------------------------
    def _on_field_mode_changed(self, sender, e):
        """Show/hide parameter panels based on selected field algorithm."""
        mode = self.dd_field_mode.SelectedIndex
        self.panel_tpms.Visible = mode in (1, 2, 3, 7)
        self.panel_sdf.Visible = (mode == 4)
        self.panel_rd.Visible = (mode == 6)
        self.panel_composite.Visible = (mode in (7, 12))
        self.panel_pathway.Visible = (mode == 8)
        self.panel_solar.Visible = (mode == 9)
        self.panel_view.Visible = (mode == 10)
        self.panel_gravity.Visible = (mode == 11)
        self.panel_multi.Visible = (mode == 12)
        self.panel_dla.Visible = (mode == 13)
        self.panel_sc.Visible = (mode == 14)
        self.panel_eden.Visible = (mode == 15)
        self.panel_phys.Visible = (mode == 16)
        self.panel_myc.Visible = (mode == 17)
        self.panel_growth_disp.Visible = mode in (13, 14, 15, 16, 17)
        if mode not in (13, 14, 15, 16, 17):
            self.system.conduit.show_growth_trails = False
            self.system.conduit.show_growth_points = False
            self.system.conduit.hide_voxels_for_growth = False
            self.system.conduit.growth_trails = []
            self.system.conduit.growth_points = []
        self._growth_playing = False
        self.btn_play_growth.Text = "Play"
        self._mark_compute()

    def _on_pick_sdf(self, sender, e):
        """Prompt user to select geometry for the SDF field."""
        self.Visible = False
        go = Rhino.Input.Custom.GetObject()
        go.SetCommandPrompt("Select SDF geometry (curves, meshes, surfaces, breps)")
        go.GeometryFilter = (
            Rhino.DocObjects.ObjectType.Curve |
            Rhino.DocObjects.ObjectType.Mesh |
            Rhino.DocObjects.ObjectType.Surface |
            Rhino.DocObjects.ObjectType.Brep)
        go.EnablePreSelect(False, True)
        go.GetMultiple(1, 0)
        if go.CommandResult() == Rhino.Commands.Result.Success:
            self.system.fields.sdf_geometries = []
            for i in range(go.ObjectCount):
                geo = go.Object(i).Geometry()
                if geo:
                    self.system.fields.sdf_geometries.append(geo.Duplicate())
            self.lbl_sdf.Text = "SDF Geometry: {} object(s)".format(
                len(self.system.fields.sdf_geometries))
        self.Visible = True
        self._full_regenerate()

    def _on_clear_sdf(self, sender, e):
        self.system.fields.sdf_geometries = []
        self.lbl_sdf.Text = "SDF Geometry: None"
        self._full_regenerate()

    # -- pathway -----------------------------------------------------------
    def _on_pick_pathway(self, sender, e):
        """Select curves to define pathway corridors."""
        self.Visible = False
        go = Rhino.Input.Custom.GetObject()
        go.SetCommandPrompt("Select pathway curves")
        go.GeometryFilter = Rhino.DocObjects.ObjectType.Curve
        go.EnablePreSelect(False, True)
        go.GetMultiple(1, 0)
        if go.CommandResult() == Rhino.Commands.Result.Success:
            self.system.fields.pathway_geometries = []
            for i in range(go.ObjectCount):
                geo = go.Object(i).Geometry()
                if geo:
                    self.system.fields.pathway_geometries.append(geo.Duplicate())
            self.lbl_pathway.Text = "Pathway: {} curve(s)".format(
                len(self.system.fields.pathway_geometries))
        self.Visible = True
        self._full_regenerate()

    def _on_clear_pathway(self, sender, e):
        self.system.fields.pathway_geometries = []
        self.lbl_pathway.Text = "Pathway: None"
        self._full_regenerate()

    # -- view corridor -----------------------------------------------------
    def _on_pick_viewer(self, sender, e):
        """Pick the viewer (eye) point for the view corridor."""
        self.Visible = False
        rc, pt = Rhino.Input.RhinoGet.GetPoint("Pick viewer/eye position", False)
        if rc == Rhino.Commands.Result.Success:
            self.system.fields.view_origin = pt
            self.system.conduit.view_origin = pt
            self._update_view_label()
        self.Visible = True
        self._full_regenerate()

    def _on_pick_target(self, sender, e):
        """Pick the target (look-at) point for the view corridor."""
        self.Visible = False
        rc, pt = Rhino.Input.RhinoGet.GetPoint("Pick view target point", False)
        if rc == Rhino.Commands.Result.Success:
            self.system.fields.view_target = pt
            self.system.conduit.view_target = pt
            self._update_view_label()
        self.Visible = True
        self._full_regenerate()

    def _update_view_label(self):
        vo = self.system.fields.view_origin
        vt = self.system.fields.view_target
        if vo and vt:
            self.lbl_view.Text = "View: ({:.0f},{:.0f},{:.0f}) -> ({:.0f},{:.0f},{:.0f})".format(
                vo.X, vo.Y, vo.Z, vt.X, vt.Y, vt.Z)
        elif vo:
            self.lbl_view.Text = "View: origin set, need target"
        elif vt:
            self.lbl_view.Text = "View: need origin, target set"
        else:
            self.lbl_view.Text = "View: not set"

    def _on_view_vis_changed(self, sender, e):
        """Sync view corridor visibility toggles to the conduit."""
        self.system.conduit.show_view_origin = (
            self.chk_show_view_origin.Checked == True)
        self.system.conduit.show_view_target = (
            self.chk_show_view_target.Checked == True)
        self.system.conduit.show_view_line = (
            self.chk_show_view_line.Checked == True)
        sc.doc.Views.Redraw()

    def _on_pick_view_origin_color(self, sender, e):
        cur = self.system.conduit.view_origin_color
        cd = forms.ColorDialog()
        cd.Color = drawing.Color.FromArgb(cur.R, cur.G, cur.B)
        if cd.ShowDialog(self) == forms.DialogResult.Ok:
            c = cd.Color
            self.system.conduit.view_origin_color = System.Drawing.Color.FromArgb(
                c.Rb, c.Gb, c.Bb)
            self.btn_view_origin_col.BackgroundColor = c
            sc.doc.Views.Redraw()

    def _on_pick_view_target_color(self, sender, e):
        cur = self.system.conduit.view_target_color
        cd = forms.ColorDialog()
        cd.Color = drawing.Color.FromArgb(cur.R, cur.G, cur.B)
        if cd.ShowDialog(self) == forms.DialogResult.Ok:
            c = cd.Color
            self.system.conduit.view_target_color = System.Drawing.Color.FromArgb(
                c.Rb, c.Gb, c.Bb)
            self.btn_view_target_col.BackgroundColor = c
            sc.doc.Views.Redraw()

    def _on_pick_view_line_color(self, sender, e):
        cur = self.system.conduit.view_line_color
        cd = forms.ColorDialog()
        cd.Color = drawing.Color.FromArgb(cur.R, cur.G, cur.B)
        if cd.ShowDialog(self) == forms.DialogResult.Ok:
            c = cd.Color
            self.system.conduit.view_line_color = System.Drawing.Color.FromArgb(
                c.Rb, c.Gb, c.Bb)
            self.btn_view_line_col.BackgroundColor = c
            sc.doc.Views.Redraw()

    # -- attractors --------------------------------------------------------
    def _on_pick_attractors(self, sender, e):
        """Prompt user to select point objects as density attractors."""
        self.Visible = False
        go = Rhino.Input.Custom.GetObject()
        go.SetCommandPrompt("Select attractor points")
        go.GeometryFilter = Rhino.DocObjects.ObjectType.Point
        go.EnablePreSelect(False, True)
        go.GetMultiple(1, 0)
        if go.CommandResult() == Rhino.Commands.Result.Success:
            self.attractor_pts = []
            for i in range(go.ObjectCount):
                pt = go.Object(i).Point().Location
                self.attractor_pts.append(pt)
            self.lbl_attr_count.Text = "Points: {}".format(len(self.attractor_pts))
        self.Visible = True
        self._full_regenerate()

    def _on_clear_attractors(self, sender, e):
        self.attractor_pts = []
        self.lbl_attr_count.Text = "Points: 0"
        self._full_regenerate()

    # -- attractor curves --------------------------------------------------
    def _on_pick_attractor_curves(self, sender, e):
        """Prompt user to select curves as density attractors."""
        self.Visible = False
        go = Rhino.Input.Custom.GetObject()
        go.SetCommandPrompt("Select attractor curves")
        go.GeometryFilter = Rhino.DocObjects.ObjectType.Curve
        go.EnablePreSelect(False, True)
        go.GetMultiple(1, 0)
        if go.CommandResult() == Rhino.Commands.Result.Success:
            self.attractor_curves = []
            for i in range(go.ObjectCount):
                geo = go.Object(i).Geometry()
                if geo:
                    self.attractor_curves.append(geo.Duplicate())
            self.lbl_attr_crv_count.Text = "Curves: {}".format(len(self.attractor_curves))
        self.Visible = True
        self._full_regenerate()

    def _on_clear_attractor_curves(self, sender, e):
        self.attractor_curves = []
        self.lbl_attr_crv_count.Text = "Curves: 0"
        self._full_regenerate()

    # -- attractor geometries ----------------------------------------------
    def _on_pick_attractor_geos(self, sender, e):
        """Prompt user to select meshes/breps/surfaces as density attractors."""
        self.Visible = False
        go = Rhino.Input.Custom.GetObject()
        go.SetCommandPrompt("Select attractor geometries (meshes, surfaces, breps)")
        go.GeometryFilter = (
            Rhino.DocObjects.ObjectType.Mesh |
            Rhino.DocObjects.ObjectType.Surface |
            Rhino.DocObjects.ObjectType.Brep |
            Rhino.DocObjects.ObjectType.Extrusion)
        go.EnablePreSelect(False, True)
        go.GetMultiple(1, 0)
        if go.CommandResult() == Rhino.Commands.Result.Success:
            self.attractor_geos = []
            for i in range(go.ObjectCount):
                geo = go.Object(i).Geometry()
                if geo:
                    dup = geo.Duplicate()
                    if isinstance(dup, rg.Extrusion):
                        brep = dup.ToBrep()
                        if brep:
                            dup = brep
                    self.attractor_geos.append(dup)
            self.lbl_attr_geo_count.Text = "Geometries: {}".format(len(self.attractor_geos))
        self.Visible = True
        self._full_regenerate()

    def _on_clear_attractor_geos(self, sender, e):
        self.attractor_geos = []
        self.lbl_attr_geo_count.Text = "Geometries: 0"
        self._full_regenerate()

    # -- custom voxel geometry ---------------------------------------------
    def _on_pick_custom(self, sender, e):
        """Prompt user to select geometry to use as the voxel shape template.
        Accepts mesh, brep, surface, or extrusion; converts all to mesh."""
        self.Visible = False
        go = Rhino.Input.Custom.GetObject()
        go.SetCommandPrompt("Select geometry to use as voxel shape")
        go.GeometryFilter = (
            Rhino.DocObjects.ObjectType.Mesh |
            Rhino.DocObjects.ObjectType.Brep |
            Rhino.DocObjects.ObjectType.Surface |
            Rhino.DocObjects.ObjectType.Extrusion)
        go.EnablePreSelect(False, True)
        go.GetMultiple(1, 0)
        if go.CommandResult() == Rhino.Commands.Result.Success:
            meshes = []
            for i in range(go.ObjectCount):
                geo = go.Object(i).Geometry()
                if not geo:
                    continue
                geo = geo.Duplicate()
                if isinstance(geo, rg.Mesh):
                    meshes.append(geo)
                elif isinstance(geo, rg.Extrusion):
                    brep = geo.ToBrep()
                    if brep:
                        ms = rg.Mesh.CreateFromBrep(brep, rg.MeshingParameters())
                        if ms:
                            for m in ms:
                                meshes.append(m)
                elif isinstance(geo, rg.Brep):
                    ms = rg.Mesh.CreateFromBrep(geo, rg.MeshingParameters())
                    if ms:
                        for m in ms:
                            meshes.append(m)
                elif isinstance(geo, rg.Surface):
                    brep = geo.ToBrep()
                    if brep:
                        ms = rg.Mesh.CreateFromBrep(brep, rg.MeshingParameters())
                        if ms:
                            for m in ms:
                                meshes.append(m)
            if meshes:
                self.system.set_custom_geometry(meshes)
                self.lbl_custom.Text = "Custom: {} object(s)".format(go.ObjectCount)
                self.chk_use_custom.Checked = True
        self.Visible = True
        self._display_only()

    def _on_clear_custom(self, sender, e):
        self.system.set_custom_geometry(None)
        self.chk_use_custom.Checked = False
        self.lbl_custom.Text = "Custom: None"
        self._display_only()

    # -- edge boids --------------------------------------------------------
    def _toggle_trails(self):
        """Toggle trail polyline visibility in the conduit."""
        self.system.conduit.show_trails = self.chk_boids.Checked == True
        sc.doc.Views.Redraw()

    def _toggle_vertex_colors(self):
        """Switch between density-coloured and flat-shaded voxel rendering."""
        self.system.conduit.use_vertex_colors = self.chk_vcol.Checked == True
        sc.doc.Views.Redraw()

    def _on_pick_boid_attractor(self, sender, e):
        self.Visible = False
        go = Rhino.Input.Custom.GetObject()
        go.SetCommandPrompt("Select attractor curves for boid paths")
        go.GeometryFilter = Rhino.DocObjects.ObjectType.Curve
        go.EnablePreSelect(False, True)
        go.GetMultiple(1, 0)
        if go.CommandResult() == Rhino.Commands.Result.Success:
            self.boid_attractor_curves = []
            for i in range(go.ObjectCount):
                geo = go.Object(i).Geometry()
                if geo:
                    self.boid_attractor_curves.append(geo.Duplicate())
            self.lbl_boid_attr.Text = "Boid Attractors: {}".format(
                len(self.boid_attractor_curves))
        self.Visible = True

    def _on_clear_boid_attractor(self, sender, e):
        self.boid_attractor_curves = []
        self.lbl_boid_attr.Text = "Boid Attractors: 0"

    def _on_run_boids(self, sender, e):
        """Build the edge graph and run boid simulation. Reads all boid params,
        generates trails, applies fillet and offset, builds pipe meshes if
        radius > 0, and updates the conduit display."""
        if not self.system.voxels:
            self.lbl_boid_status.Text = "Boids: no voxels"
            return

        p = self._read_params()
        gx, gy, gz = p[0], p[1], p[2]
        cw, cl, ch = p[3], p[4], p[5]
        seed = p[9]
        origin = self._grid_origin(gx, gy, gz, cw, cl, ch)

        diags = self.chk_diagonals.Checked == True
        count = self._ival(self.txt_boid_count, self.sld_boid_count)
        steps = self._ival(self.txt_boid_steps, self.sld_boid_steps)
        turn_c = self._fval(self.txt_boid_turn, self.sld_boid_turn, 0.0, 1.0)
        str_ang = self._fval(self.txt_boid_straight, self.sld_boid_straight, 0.0, 90.0)
        if self.chk_boid_overlap.Checked:
            ovlap = self._fval(self.txt_boid_overlap, self.sld_boid_overlap, 0.0, 1.0)
        else:
            ovlap = 0.0
        min_a = self._fval(self.txt_boid_min_a, self.sld_boid_min_a, 0.0, 180.0)
        max_a = self._fval(self.txt_boid_max_a, self.sld_boid_max_a, 0.0, 180.0)
        thick = self._ival(self.txt_boid_thick, self.sld_boid_thick)
        offset = self._fval(self.txt_boid_offset, self.sld_boid_offset, 0.0, 10.0)
        tightness = self._ival(self.txt_boid_tight, self.sld_boid_tight)
        fillet = self._fval(self.txt_boid_fillet, self.sld_boid_fillet, 0.0, 10.0)
        battr_s = self._fval(self.txt_boid_attr_s, self.sld_boid_attr_s, 0.0, 1.0)

        self.lbl_boid_status.Text = "Building graph..."
        self.system.build_edge_graph(self.system.voxels, diags)

        self.lbl_boid_status.Text = "Running {} boids...".format(count)
        self.system.run_edge_boids(count, steps, min_a, max_a, turn_c,
                                   seed, cw, cl, ch, origin, offset,
                                   tightness, str_ang, ovlap,
                                   self.boid_attractor_curves, battr_s)

        trails = self.system.boid_trails
        if fillet > 1e-6:
            trails = self.system.fillet_trails(trails, fillet)

        self.system.conduit.trail_polylines = trails
        self.system.conduit.trail_color = self.trail_color
        self.system.conduit.trail_thickness = thick
        self.system.conduit.show_trails = True
        self.chk_boids.Checked = True

        pipe_r = self._fval(self.txt_pipe_rad, self.sld_pipe_rad, 0.0, 5.0)
        if pipe_r > 1e-6 and trails:
            self.lbl_boid_status.Text = "Building pipes..."
            self.system.conduit.pipe_mesh = self.system.build_pipe_mesh(trails, pipe_r)
            self.system.conduit.pipe_material = rd.DisplayMaterial(self.trail_color)
            self.system.conduit.show_pipes = True
        else:
            self.system.conduit.pipe_mesh = None
            self.system.conduit.show_pipes = False

        sc.doc.Views.Redraw()

        total_segs = sum(pl.Count - 1 for pl in trails)
        self.lbl_boid_status.Text = "Boids: {} trails, {} segments".format(
            len(trails), total_segs)

    def _on_clear_trails(self, sender, e):
        self.system.boid_trails = []
        self.system.boid_graph = {}
        self.system.boid_vertex_normals = {}
        self.system.conduit.trail_polylines = []
        self.system.conduit.pipe_mesh = None
        self.system.conduit.show_pipes = False
        sc.doc.Views.Redraw()
        self.lbl_boid_status.Text = "Boids: cleared"

    def _on_bake_trails(self, sender, e):
        """Add trail polylines and pipe mesh (if present) to the document
        with the chosen trail colour."""
        if not self.system.boid_trails:
            return
        attr = Rhino.DocObjects.ObjectAttributes()
        attr.ColorSource = Rhino.DocObjects.ObjectColorSource.ColorFromObject
        attr.ObjectColor = self.trail_color
        for pl in self.system.boid_trails:
            if pl.Count > 1:
                sc.doc.Objects.AddPolyline(pl, attr)
        pm = self.system.conduit.pipe_mesh
        if pm and pm.Vertices.Count > 0:
            sc.doc.Objects.AddMesh(pm, attr)
        sc.doc.Views.Redraw()
        self.lbl_boid_status.Text = "Baked {} trails".format(
            len(self.system.boid_trails))

    def _on_bake_trails_brep(self, sender, e):
        """Convert pipe mesh to NURBS breps (one per pipe segment) or trails to
        NURBS curves, and add to document with no colour attributes."""
        has_trails = self.system.boid_trails and len(self.system.boid_trails) > 0
        has_pipes = (self.system.conduit.pipe_mesh and
                     self.system.conduit.pipe_mesh.Vertices.Count > 0)
        if not has_trails and not has_pipes:
            self.lbl_boid_status.Text = "No trails to bake"
            return
        self.lbl_boid_status.Text = "Converting to Brep..."
        try:
            count = 0
            tol = sc.doc.ModelAbsoluteTolerance
            _Pt3d = rg.Point3d
            _Corner = rg.Brep.CreateFromCornerPoints
            _Join = rg.Brep.JoinBreps
            if has_pipes:
                pm = self.system.conduit.pipe_mesh
                pieces = pm.SplitDisjointPieces()
                if not pieces or len(pieces) == 0:
                    pieces = [pm]
                for piece in pieces:
                    pverts = piece.Vertices
                    pfaces = piece.Faces
                    surfs = []
                    for fi in range(pfaces.Count):
                        f = pfaces[fi]
                        if f.IsQuad:
                            srf = _Corner(
                                _Pt3d(pverts[f.A]), _Pt3d(pverts[f.B]),
                                _Pt3d(pverts[f.C]), _Pt3d(pverts[f.D]), tol)
                        else:
                            srf = _Corner(
                                _Pt3d(pverts[f.A]), _Pt3d(pverts[f.B]),
                                _Pt3d(pverts[f.C]), tol)
                        if srf:
                            surfs.append(srf)
                    if surfs:
                        joined = _Join(surfs, tol)
                        if joined:
                            for b in joined:
                                sc.doc.Objects.AddBrep(b)
                                count += 1
                        else:
                            for s in surfs:
                                sc.doc.Objects.AddBrep(s)
                                count += 1
            elif has_trails:
                for pl in self.system.boid_trails:
                    if pl.Count > 1:
                        crv = pl.ToNurbsCurve()
                        if crv:
                            sc.doc.Objects.AddCurve(crv)
                            count += 1
            sc.doc.Views.Redraw()
            self.lbl_boid_status.Text = "Baked {} brep(s)/curve(s)".format(count)
        except Exception as ex:
            self.lbl_boid_status.Text = "Brep failed: {}".format(str(ex))

    def _on_melt(self, sender, e):
        """Run the melt/blend operation and display the result."""
        iters = self._ival(self.txt_melt_smooth, self.sld_melt_smooth)
        factor = self._fval(self.txt_melt_factor, self.sld_melt_factor, 0.01, 1.0)
        self.lbl_melt_status.Text = "Melting..."
        result = self.system.melt(iters, factor)
        if result and result.Vertices.Count > 0:
            self.system.conduit.melt_mesh = result
            self.system.conduit.show_melt = True
            sc.doc.Views.Redraw()
            self.lbl_melt_status.Text = "Melt: {} vertices".format(
                result.Vertices.Count)
        else:
            self.lbl_melt_status.Text = "Melt: failed (no geometry)"

    def _on_clear_melt(self, sender, e):
        self.system.conduit.melt_mesh = None
        self.system.conduit.show_melt = False
        sc.doc.Views.Redraw()
        self.lbl_melt_status.Text = "Melt: cleared"

    def _on_bake_melt(self, sender, e):
        """Add the melted mesh to the Rhino document."""
        mm = self.system.conduit.melt_mesh
        if not mm or mm.Vertices.Count == 0:
            self.lbl_melt_status.Text = "Melt: nothing to bake"
            return
        attr = Rhino.DocObjects.ObjectAttributes()
        attr.ColorSource = Rhino.DocObjects.ObjectColorSource.ColorFromObject
        attr.ObjectColor = self.voxel_color
        sc.doc.Objects.AddMesh(mm, attr)
        sc.doc.Views.Redraw()
        self.lbl_melt_status.Text = "Melt: baked"

    def _on_pick_trail_color(self, sender, e):
        cd = forms.ColorDialog()
        cd.Color = drawing.Color.FromArgb(
            self.trail_color.R, self.trail_color.G, self.trail_color.B)
        if cd.ShowDialog(self) == forms.DialogResult.Ok:
            c = cd.Color
            self.trail_color = System.Drawing.Color.FromArgb(c.Rb, c.Gb, c.Bb)
            self.btn_trail_col.BackgroundColor = c
            self.system.conduit.trail_color = self.trail_color
            sc.doc.Views.Redraw()

    # -- growth attractor / repellent handlers -----------------------------
    def _pick_growth_geos(self, prompt):
        """Prompt user to select geometry objects for growth influence."""
        self.Visible = False
        go = Rhino.Input.Custom.GetObject()
        go.SetCommandPrompt(prompt)
        go.GeometryFilter = (
            Rhino.DocObjects.ObjectType.Curve |
            Rhino.DocObjects.ObjectType.Mesh |
            Rhino.DocObjects.ObjectType.Surface |
            Rhino.DocObjects.ObjectType.Brep |
            Rhino.DocObjects.ObjectType.Point)
        go.EnablePreSelect(False, True)
        go.GetMultiple(1, 0)
        result = []
        if go.CommandResult() == Rhino.Commands.Result.Success:
            for i in range(go.ObjectCount):
                geo = go.Object(i).Geometry()
                if geo:
                    result.append(geo.Duplicate())
        self.Visible = True
        return result

    def _update_gattr_label(self):
        na = len(self.system.fields.growth_attractors)
        nr = len(self.system.fields.growth_repellents)
        self.lbl_gattr.Text = "Attract: {} | Repel: {}".format(na, nr)

    def _on_pick_growth_attractors(self, sender, e):
        geos = self._pick_growth_geos("Select attractor geometry")
        if geos:
            self.system.fields.growth_attractors.extend(geos)
            self._update_gattr_label()
            self._full_regenerate()

    def _on_clear_growth_attractors(self, sender, e):
        self.system.fields.growth_attractors = []
        self._update_gattr_label()
        self._full_regenerate()

    def _on_pick_growth_repellents(self, sender, e):
        geos = self._pick_growth_geos("Select repellent geometry")
        if geos:
            self.system.fields.growth_repellents.extend(geos)
            self._update_gattr_label()
            self._full_regenerate()

    def _on_clear_growth_repellents(self, sender, e):
        self.system.fields.growth_repellents = []
        self._update_gattr_label()
        self._full_regenerate()

    # -- growth display handlers -------------------------------------------
    def _on_growth_disp_changed(self):
        cond = self.system.conduit
        cond.show_growth_trails = self.chk_growth_trails.Checked == True
        cond.show_growth_points = self.chk_growth_points.Checked == True
        cond.hide_voxels_for_growth = self.chk_hide_voxels.Checked == True
        cond.growth_trail_thickness = self._ival(
            self.txt_gtrail_thick, self.sld_gtrail_thick)
        cond.growth_point_size = self._ival(
            self.txt_gpoint_size, self.sld_gpoint_size)
        Rhino.RhinoDoc.ActiveDoc.Views.Redraw()

    def _on_pick_gtrail_color(self, sender, e):
        cur = self.system.conduit.growth_trail_color
        cd = forms.ColorDialog()
        cd.Color = drawing.Color.FromArgb(cur.R, cur.G, cur.B)
        if cd.ShowDialog(self) == forms.DialogResult.Ok:
            c = cd.Color
            self.system.conduit.growth_trail_color = (
                System.Drawing.Color.FromArgb(c.Rb, c.Gb, c.Bb))
            self.btn_gtrail_col.BackgroundColor = c
            Rhino.RhinoDoc.ActiveDoc.Views.Redraw()

    def _on_pick_gpoint_color(self, sender, e):
        cur = self.system.conduit.growth_point_color
        cd = forms.ColorDialog()
        cd.Color = drawing.Color.FromArgb(cur.R, cur.G, cur.B)
        if cd.ShowDialog(self) == forms.DialogResult.Ok:
            c = cd.Color
            self.system.conduit.growth_point_color = (
                System.Drawing.Color.FromArgb(c.Rb, c.Gb, c.Bb))
            self.btn_gpoint_col.BackgroundColor = c
            Rhino.RhinoDoc.ActiveDoc.Views.Redraw()

    # -- playback handlers -------------------------------------------------
    def _on_play_growth(self, sender, e):
        if self._growth_total_frames < 2:
            return
        if self._growth_playing:
            self._growth_playing = False
            self.btn_play_growth.Text = "Play"
        else:
            if self._growth_frame >= self._growth_total_frames - 1:
                self._growth_frame = 0
            if (self.chk_growth_trails.Checked != True and
                    self.chk_growth_points.Checked != True):
                self.chk_growth_points.Checked = True
                self.chk_growth_trails.Checked = True
                self._on_growth_disp_changed()
            self._growth_playing = True
            self.btn_play_growth.Text = "Pause"

    def _on_restart_growth(self, sender, e):
        self._growth_playing = False
        self.btn_play_growth.Text = "Play"
        self._growth_frame = 0
        if self._growth_total_frames > 0:
            self._update_growth_frame()

    def _update_growth_frame(self):
        """Push the current playback frame data to the conduit and redraw."""
        try:
            f = self._growth_frame
            if f < 0:
                f = 0
            fields = self.system.fields
            cond = self.system.conduit
            mode = fields.growth_playback_mode
            if mode == "cumulative":
                idx = fields.growth_frame_indices
                if idx and f < len(idx):
                    nt, np_count = idx[f]
                    cond.growth_trails = self._all_world_trails[:nt]
                    cond.growth_points = self._all_world_points[:np_count]
            elif mode == "snapshot":
                if self._world_frame_snapshots and f < len(self._world_frame_snapshots):
                    trails, points = self._world_frame_snapshots[f]
                    cond.growth_trails = trails
                    cond.growth_points = points
            self.lbl_growth_frame.Text = "Frame: {} / {}".format(
                f + 1, self._growth_total_frames)
            Rhino.RhinoDoc.ActiveDoc.Views.Redraw()
        except Exception:
            pass

    # -- colour pickers ----------------------------------------------------
    def _on_pick_voxel_color(self, sender, e):
        """Open colour dialog for voxel face colour and update display."""
        cd = forms.ColorDialog()
        cd.Color = drawing.Color.FromArgb(self.voxel_color.R, self.voxel_color.G, self.voxel_color.B)
        if cd.ShowDialog(self) == forms.DialogResult.Ok:
            c = cd.Color
            self.voxel_color = System.Drawing.Color.FromArgb(c.Rb, c.Gb, c.Bb)
            self.btn_vcol.BackgroundColor = c
            self.system.conduit.shaded_material = rd.DisplayMaterial(
                System.Drawing.Color.FromArgb(c.Rb, c.Gb, c.Bb))
            self._display_only()

    def _on_pick_edge_color(self, sender, e):
        cd = forms.ColorDialog()
        cd.Color = drawing.Color.FromArgb(self.edge_color.R, self.edge_color.G, self.edge_color.B)
        if cd.ShowDialog(self) == forms.DialogResult.Ok:
            c = cd.Color
            self.edge_color = System.Drawing.Color.FromArgb(c.Rb, c.Gb, c.Bb)
            self.btn_ecol.BackgroundColor = c
            self._display_only()

    def _on_pick_bounds_color(self, sender, e):
        cd = forms.ColorDialog()
        cd.Color = drawing.Color.FromArgb(self.bounds_color.R, self.bounds_color.G, self.bounds_color.B)
        if cd.ShowDialog(self) == forms.DialogResult.Ok:
            c = cd.Color
            self.bounds_color = System.Drawing.Color.FromArgb(c.Rb, c.Gb, c.Bb)
            self.btn_bcol.BackgroundColor = c
            self._display_only()

    # -- gradient key ------------------------------------------------------
    def _on_gradient_paint(self, sender, e):
        """Paint the colour gradient bar showing density-to-colour mapping
        from threshold (dark) to 1.0 (brightest)."""
        g = e.Graphics
        w = self.gradient_bar.Width
        h = self.gradient_bar.Height
        if w <= 0 or h <= 0:
            return
        thresh = self._fval(self.txt_thresh, self.sld_thresh, 0.0, 1.0)
        steps = 50
        step_w = w / float(steps)
        cr = self.voxel_color.R
        cg = self.voxel_color.G
        cb = self.voxel_color.B
        for i in range(steps):
            t = i / float(steps - 1) if steps > 1 else 1.0
            val = thresh + t * (1.0 - thresh)
            r = max(30, min(255, int(cr * val)))
            gv = max(30, min(255, int(cg * val)))
            b = max(30, min(255, int(cb * val)))
            col = drawing.Color.FromArgb(r, gv, b)
            g.FillRectangle(col, float(i) * step_w, 0.0, step_w + 0.5, float(h))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    """Launch the Voxel Field Tool v02 dialog as a modeless Eto window."""
    dlg = VoxelDialog()
    dlg.Owner = Rhino.UI.RhinoEtoApp.MainWindow
    dlg.Show()

if __name__ == "__main__":
    main()
