#! python 3
# NODE_INPUTS: site_def:str, datetime_data:str, sample_month:list[float], sample_day:list[float], sample_hour:list[float], context_breps:list[Brep], latitude:float, longitude:float, analysis_step:int, coarse_step:int, preview_mode:int, include_buffer:bool
# NODE_OUTPUTS: solar_scores, heatmap_mesh, vis_mesh, log
#
# solar_analysis — composite solar scores per voxel inside subject_site (site_def v1).
# Spencer 1971 sun position; optional MeshRay shadowing from context_breps.
# Wire DataNode month/day/hour list outputs to sample_month / sample_day / sample_hour.
#
# datetime_data (optional Text): JSON array of samples, e.g.
#   [{"month":3,"day":20,"hour":9},{"month":6,"day":21,"hour":12}]
# If empty, uses sample_month/day/hour lists; if those are empty, built-in 9 defaults.
#
# include_buffer (bool): False = score site_range only; True = score buffer_range (wider band).
#
# preview_mode (int): 0 = composite mean of all samples for meshes; 1..N = sample N only
#   (N = number of time samples after parsing). Values > N fall back to composite.
#
# analysis_step (int): base cells per coarse analysis bin along each axis (>=1, default 1).
#   JSON v2 outputs scores + world-space gradients on the coarse grid only (small payload for boids).
# coarse_step (int): vis_mesh stride in coarse grid indices (>=1, default 2). With analysis_step 1, coarse == base.

import json
import math
import System.Drawing as sd
import Rhino.Geometry as rg

# ─── DEFAULTS ─────────────────────────────────────────────────────────

DEFAULT_LAT = -37.81
DEFAULT_LON = 144.96

SCORE_DECIMALS = 3
GRAD_DECIMALS = 4

DEFAULT_TIMES = [
    {"month": 3, "day": 20, "hour": 9.0},
    {"month": 3, "day": 20, "hour": 12.0},
    {"month": 3, "day": 20, "hour": 15.0},
    {"month": 6, "day": 21, "hour": 9.0},
    {"month": 6, "day": 21, "hour": 12.0},
    {"month": 6, "day": 21, "hour": 15.0},
    {"month": 12, "day": 21, "hour": 9.0},
    {"month": 12, "day": 21, "hour": 12.0},
    {"month": 12, "day": 21, "hour": 15.0},
]

if latitude is None:
    latitude = DEFAULT_LAT
if longitude is None:
    longitude = DEFAULT_LON
try:
    _as_in = analysis_step
except NameError:
    _as_in = None
if _as_in is None or int(_as_in) < 1:
    analysis_step = 1
else:
    analysis_step = int(_as_in)
if coarse_step is None or int(coarse_step) < 1:
    coarse_step = 2
else:
    coarse_step = int(coarse_step)
if preview_mode is None:
    preview_mode = 0
else:
    preview_mode = int(preview_mode)
if include_buffer is None:
    include_buffer = False
else:
    include_buffer = bool(include_buffer)

solar_scores = "{}"
heatmap_mesh = None
vis_mesh = None
log = ""

# ─── TIME SAMPLES ─────────────────────────────────────────────────────

def _parse_times():
    times = None
    s = datetime_data
    if s is not None and str(s).strip():
        try:
            raw = json.loads(str(s))
            if isinstance(raw, list) and raw:
                times = []
                for it in raw:
                    if isinstance(it, dict):
                        times.append({
                            "month": int(it.get("month", 1)),
                            "day": int(it.get("day", 1)),
                            "hour": float(it.get("hour", 12.0)),
                        })
        except Exception:
            times = None
    if times is None:
        sm = sample_month if sample_month else []
        sd_ = sample_day if sample_day else []
        sh = sample_hour if sample_hour else []
        n = min(len(sm), len(sd_), len(sh))
        if n > 0:
            times = []
            for i in range(n):
                times.append({
                    "month": int(sm[i]),
                    "day": int(sd_[i]),
                    "hour": float(sh[i]),
                })
    if not times:
        times = list(DEFAULT_TIMES)
    return times


def _local_up_height(pt, org, zax):
    """Signed height along grid vertical (z_axis): >=0 is on/above ground plane through origin."""
    vx = pt.X - org.X
    vy = pt.Y - org.Y
    vz = pt.Z - org.Z
    return vx * zax.X + vy * zax.Y + vz * zax.Z


# ─── SOLAR (SPENCER 1971) ─────────────────────────────────────────────

def _day_of_year(month, day, year):
    dim = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    if year % 400 == 0 or (year % 4 == 0 and year % 100 != 0):
        dim[2] = 29
    doy = day
    for m in range(1, month):
        doy += dim[m]
    return doy


def _solar_declination(doy):
    b = (2.0 * math.pi / 365.0) * (doy - 1)
    return (
        0.006918
        - 0.399912 * math.cos(b)
        + 0.070257 * math.sin(b)
        - 0.006758 * math.cos(2 * b)
        + 0.000907 * math.sin(2 * b)
        - 0.002697 * math.cos(3 * b)
        + 0.00148 * math.sin(3 * b)
    )


def _equation_of_time(doy):
    b = (2.0 * math.pi / 365.0) * (doy - 1)
    return 229.18 * (
        0.000075
        + 0.001868 * math.cos(b)
        - 0.032077 * math.sin(b)
        - 0.014615 * math.cos(2 * b)
        - 0.04089 * math.sin(2 * b)
    )


def _compute_sun_alt_az(month, day, hour, lat_deg, lon_deg, year):
    doy = _day_of_year(month, day, year)
    decl = _solar_declination(doy)
    eot = _equation_of_time(doy)
    lstm = 15.0 * round(lon_deg / 15.0)
    tc = 4.0 * (lon_deg - lstm) + eot
    lst = float(hour) + tc / 60.0
    hour_angle = 15.0 * (lst - 12.0)
    ha_rad = math.radians(hour_angle)
    lat_rad = math.radians(lat_deg)

    sin_alt = (
        math.sin(lat_rad) * math.sin(decl)
        + math.cos(lat_rad) * math.cos(decl) * math.cos(ha_rad)
    )
    sin_alt = max(-1.0, min(1.0, sin_alt))
    altitude_rad = math.asin(sin_alt)
    altitude_deg = math.degrees(altitude_rad)

    cos_alt = math.cos(altitude_rad)
    if abs(cos_alt) < 1e-10:
        azimuth_deg = 180.0
    else:
        cos_az = (math.sin(decl) - math.sin(lat_rad) * sin_alt) / (
            math.cos(lat_rad) * cos_alt + 1e-12
        )
        cos_az = max(-1.0, min(1.0, cos_az))
        azimuth_rad = math.acos(cos_az)
        azimuth_deg = math.degrees(azimuth_rad)
        if hour_angle > 0:
            azimuth_deg = 360.0 - azimuth_deg

    return altitude_deg, azimuth_deg


def _sun_vector_toward_sun(altitude_deg, azimuth_deg):
    """Unit vector pointing FROM scene TOWARD the sun (Rhino: +X E, +Y N, +Z up)."""
    alt_rad = math.radians(altitude_deg)
    az_rad = math.radians(azimuth_deg)
    x = math.cos(alt_rad) * math.sin(az_rad)
    y = math.cos(alt_rad) * math.cos(az_rad)
    z = math.sin(alt_rad)
    v = rg.Vector3d(x, y, z)
    if v.Length > 1e-12:
        v.Unitize()
    return v


# ─── CONTEXT MESH + RAY ───────────────────────────────────────────────

def _build_context_mesh(breps):
    if not breps:
        return None
    acc = rg.Mesh()
    mp = rg.MeshingParameters.FastRenderMesh
    for br in breps:
        if br is None:
            continue
        try:
            pieces = rg.Mesh.CreateFromBrep(br, mp)
        except Exception:
            pieces = None
        if not pieces:
            continue
        for m in pieces:
            if m is not None:
                acc.Append(m)
    if acc.Faces.Count == 0:
        return None
    try:
        acc.Weld(math.pi)
    except Exception:
        pass
    return acc


def _mesh_blocks_sun(mesh, origin, sun_dir):
    if mesh is None or mesh.Faces.Count == 0:
        return False
    ray = rg.Ray3d(origin, sun_dir)
    try:
        hit = rg.Intersect.Intersection.MeshRay(mesh, ray)
    except Exception:
        return False
    if hit is None:
        return False
    try:
        if isinstance(hit, tuple) and len(hit) >= 2:
            if hit[0]:
                t = float(hit[1])
                return t > 1e-4
            return False
        if hasattr(hit, "T"):
            t = float(hit.T)
            return t > 1e-4
    except Exception:
        pass
    return False


# ─── COLOR ────────────────────────────────────────────────────────────

def _lerp(a, b, t):
    return a + (b - a) * t


def _score_to_color(t):
    t = max(0.0, min(1.0, float(t)))
    stops = [
        (0.0, 30, 60, 180),
        (0.25, 0, 150, 180),
        (0.5, 255, 220, 0),
        (0.75, 255, 140, 0),
        (1.0, 220, 30, 30),
    ]
    for i in range(len(stops) - 1):
        t0, r0, g0, b0 = stops[i][0], stops[i][1], stops[i][2], stops[i][3]
        t1, r1, g1, b1 = stops[i + 1][0], stops[i + 1][1], stops[i + 1][2], stops[i + 1][3]
        if t > t1 and i < len(stops) - 2:
            continue
        if t1 - t0 < 1e-9:
            u = 0.0
        else:
            u = (t - t0) / (t1 - t0)
            u = max(0.0, min(1.0, u))
        r = int(round(_lerp(r0, r1, u)))
        g = int(round(_lerp(g0, g1, u)))
        b = int(round(_lerp(b0, b1, u)))
        return sd.Color.FromArgb(255, r, g, b)
    return sd.Color.FromArgb(255, 30, 60, 180)


# ─── MAIN ─────────────────────────────────────────────────────────────

times = _parse_times()
n_samples = len(times)
year_ref = 2026

breps_in = context_breps if context_breps else []
context_mesh = _build_context_mesh(breps_in)
has_shadows = context_mesh is not None

sun_dirs = []
weights = []
valid_sample = []
for ti in range(n_samples):
    tm = times[ti]
    alt, az = _compute_sun_alt_az(
        tm["month"], tm["day"], tm["hour"], float(latitude), float(longitude), year_ref
    )
    if alt <= 0.0:
        sun_dirs.append(None)
        weights.append(0.0)
        valid_sample.append(False)
    else:
        sun_dirs.append(_sun_vector_toward_sun(alt, az))
        weights.append(max(0.0, math.sin(math.radians(alt))))
        valid_sample.append(True)

if site_def is None or not str(site_def).strip():
    log = "Error: site_def empty. Wire subject_site site_def."
else:
    try:
        sdct = json.loads(str(site_def))
    except Exception:
        sdct = None
        log = "Error: site_def is not valid JSON"

    if sdct is not None:
        ver = sdct.get("version", 0)
        if ver != 1:
            log = "Error: site_def version must be 1, got {}".format(ver)
            sdct = None

    if sdct is not None:
        gd = sdct.get("grid_def")
        if not gd or gd.get("version") != 2:
            log = "Error: embedded grid_def missing or not version 2"
            sdct = None

    if sdct is not None:
        o = gd["origin"]
        xa = gd["x_axis"]
        ya = gd["y_axis"]
        za = gd["z_axis"]
        vs = gd["voxel_size"]
        gs = gd["grid_size"]

        org = rg.Point3d(o[0], o[1], o[2])
        xax = rg.Vector3d(xa[0], xa[1], xa[2])
        yax = rg.Vector3d(ya[0], ya[1], ya[2])
        zax = rg.Vector3d(za[0], za[1], za[2])
        sx = float(vs[0]) if vs[0] > 0 else 1000.0
        sy = float(vs[1]) if vs[1] > 0 else 1000.0
        sz = float(vs[2]) if vs[2] > 0 else 1000.0
        nx = max(1, int(round(gs[0])))
        ny = max(1, int(round(gs[1])))
        nz = max(1, int(round(gs[2])))
        half_nx = nx / 2.0
        half_ny = ny / 2.0
        half_nz = nz / 2.0

        site_range = sdct.get("site_range", {})
        buf_range = sdct.get("buffer_range", {})
        if include_buffer:
            rng = buf_range
        else:
            rng = site_range
        i0, i1 = rng["i"][0], rng["i"][1]
        j0, j1 = rng["j"][0], rng["j"][1]
        k0, k1 = rng["k"][0], rng["k"][1]

        if i0 < 0 or j0 < 0 or k0 < 0 or i1 < i0 or j1 < j0 or k1 < k0:
            log = "Error: invalid cell range i:{} j:{} k:{}".format(
                [i0, i1], [j0, j1], [k0, k1]
            )
        else:
            ni = i1 - i0 + 1
            nj = j1 - j0 + 1
            nk = k1 - k0 + 1
            n_cells = ni * nj * nk

            ci = 0.5 * (i0 + i1)
            cj = 0.5 * (j0 + j1)
            ck = 0.5 * (k0 + k1)
            lcx_c = (ci - half_nx + 0.5) * sx
            lcy_c = (cj - half_ny + 0.5) * sy
            lcz_c = (ck - half_nz + 0.5) * sz
            centroid = org + lcx_c * xax + lcy_c * yax + lcz_c * zax

            def cell_center(ii, jj, kk):
                lx = (ii - half_nx + 0.5) * sx
                ly = (jj - half_ny + 0.5) * sy
                lz = (kk - half_nz + 0.5) * sz
                return org + lx * xax + ly * yax + lz * zax

            def cell_center_frac(ii, jj, kk):
                lx = (float(ii) - half_nx + 0.5) * sx
                ly = (float(jj) - half_ny + 0.5) * sy
                lz = (float(kk) - half_nz + 0.5) * sz
                return org + lx * xax + ly * yax + lz * zax

            astep = max(1, analysis_step)
            nci = (ni + astep - 1) // astep
            ncj = (nj + astep - 1) // astep
            nck = (nk + astep - 1) // astep
            n_coarse = nci * ncj * nck

            def coarse_flat(ii, jj, kk):
                cci = (ii - i0) // astep
                ccj = (jj - j0) // astep
                cck = (kk - k0) // astep
                return cci + ccj * nci + cck * nci * ncj

            coarse_sum = [0.0] * n_coarse
            coarse_cnt = [0] * n_coarse
            coarse_psum = [[0.0] * n_coarse for _ in range(n_samples)]
            coarse_pcnt = [[0] * n_coarse for _ in range(n_samples)]

            for kk in range(k0, k1 + 1):
                for jj in range(j0, j1 + 1):
                    for ii in range(i0, i1 + 1):
                        pt = cell_center(ii, jj, kk)
                        if _local_up_height(pt, org, zax) < 0.0:
                            continue

                        vx = pt.X - centroid.X
                        vy = pt.Y - centroid.Y
                        vz = pt.Z - centroid.Z
                        ln = math.sqrt(vx * vx + vy * vy + vz * vz)
                        if ln < 1e-9:
                            voxel_dir = rg.Vector3d(zax)
                            voxel_dir.Unitize()
                        else:
                            voxel_dir = rg.Vector3d(vx / ln, vy / ln, vz / ln)

                        sample_vals = []
                        for ti in range(n_samples):
                            if not valid_sample[ti]:
                                sample_vals.append(0.0)
                                continue
                            sdir = sun_dirs[ti]
                            w = weights[ti]
                            base = voxel_dir.X * sdir.X + voxel_dir.Y * sdir.Y + voxel_dir.Z * sdir.Z
                            base = max(0.0, base) * w
                            if has_shadows and base > 1e-12:
                                if _mesh_blocks_sun(context_mesh, pt, sdir):
                                    base = 0.0
                            sample_vals.append(base)

                        comp = sum(sample_vals) / float(n_samples) if n_samples else 0.0

                        cix = coarse_flat(ii, jj, kk)
                        coarse_sum[cix] += comp
                        coarse_cnt[cix] += 1
                        for ti in range(n_samples):
                            coarse_psum[ti][cix] += sample_vals[ti]
                            coarse_pcnt[ti][cix] += 1

            coarse_composite = []
            for c in range(n_coarse):
                if coarse_cnt[c] > 0:
                    coarse_composite.append(coarse_sum[c] / float(coarse_cnt[c]))
                else:
                    coarse_composite.append(0.0)

            coarse_per_sample = []
            for ti in range(n_samples):
                row = []
                for c in range(n_coarse):
                    if coarse_pcnt[ti][c] > 0:
                        row.append(coarse_psum[ti][c] / float(coarse_pcnt[ti][c]))
                    else:
                        row.append(0.0)
                coarse_per_sample.append(row)

            pm = preview_mode
            if pm < 0:
                pm = 0
            if pm == 0:
                display_scores = coarse_composite
            elif pm <= n_samples:
                display_scores = coarse_per_sample[pm - 1]
            else:
                display_scores = coarse_composite

            max_score = 0.0
            for v in display_scores:
                if v > max_score:
                    max_score = v
            norm_denom = max_score if max_score > 1e-12 else 1.0

            # Heatmap: one quad per coarse (ci,cj); average over ck with data; ground plane local z=0
            hm = rg.Mesh()
            vcol = hm.VertexColors
            for ccj in range(ncj):
                for cci in range(nci):
                    ssum = 0.0
                    cnt = 0
                    for cck in range(nck):
                        cf = cci + ccj * nci + cck * nci * ncj
                        if coarse_cnt[cf] <= 0:
                            continue
                        ssum += display_scores[cf]
                        cnt += 1
                    avg = ssum / float(cnt) if cnt else 0.0
                    tcol = avg / norm_denom

                    i_lo = i0 + cci * astep
                    i_hi = min(i_lo + astep - 1, i1)
                    j_lo = j0 + ccj * astep
                    j_hi = min(j_lo + astep - 1, j1)
                    lx0 = (i_lo - half_nx) * sx
                    lx1 = (i_hi - half_nx + 1.0) * sx
                    ly0 = (j_lo - half_ny) * sy
                    ly1 = (j_hi - half_ny + 1.0) * sy
                    lz_ground = 0.0
                    p00 = org + lx0 * xax + ly0 * yax + lz_ground * zax
                    p10 = org + lx1 * xax + ly0 * yax + lz_ground * zax
                    p11 = org + lx1 * xax + ly1 * yax + lz_ground * zax
                    p01 = org + lx0 * xax + ly1 * yax + lz_ground * zax

                    b0 = hm.Vertices.Count
                    hm.Vertices.Add(p00)
                    hm.Vertices.Add(p10)
                    hm.Vertices.Add(p11)
                    hm.Vertices.Add(p01)
                    hm.Faces.AddFace(b0, b0 + 1, b0 + 2, b0 + 3)
                    c = _score_to_color(tcol)
                    vcol.Add(c)
                    vcol.Add(c)
                    vcol.Add(c)
                    vcol.Add(c)

            heatmap_mesh = hm if hm.Faces.Count > 0 else None

            # 3D vis: coarse grid subsample (stride = coarse_step in coarse indices)
            vm = rg.Mesh()
            vcol2 = vm.VertexColors
            hs = max(1, coarse_step)
            qsz = 0.45 * min(sx * astep, sy * astep)
            hx = 0.5 * qsz
            hy = 0.5 * qsz
            for cck in range(0, nck, hs):
                for ccj in range(0, ncj, hs):
                    for cci in range(0, nci, hs):
                        cf = cci + ccj * nci + cck * nci * ncj
                        if coarse_cnt[cf] <= 0:
                            continue
                        i_lo = i0 + cci * astep
                        i_hi = min(i_lo + astep - 1, i1)
                        j_lo = j0 + ccj * astep
                        j_hi = min(j_lo + astep - 1, j1)
                        k_lo = k0 + cck * astep
                        k_hi = min(k_lo + astep - 1, k1)
                        i_mid = 0.5 * (float(i_lo) + float(i_hi))
                        j_mid = 0.5 * (float(j_lo) + float(j_hi))
                        k_mid = 0.5 * (float(k_lo) + float(k_hi))
                        pt = cell_center_frac(i_mid, j_mid, k_mid)
                        if _local_up_height(pt, org, zax) < 0.0:
                            continue
                        tcol = display_scores[cf] / norm_denom
                        c = _score_to_color(tcol)
                        cx = xax
                        cy = yax
                        p0 = pt - hx * cx - hy * cy
                        p1 = pt + hx * cx - hy * cy
                        p2 = pt + hx * cx + hy * cy
                        p3 = pt - hx * cx + hy * cy
                        b0 = vm.Vertices.Count
                        vm.Vertices.Add(p0)
                        vm.Vertices.Add(p1)
                        vm.Vertices.Add(p2)
                        vm.Vertices.Add(p3)
                        vm.Faces.AddFace(b0, b0 + 1, b0 + 2, b0 + 3)
                        for _ in range(4):
                            vcol2.Add(c)

            vis_mesh = vm if vm.Faces.Count > 0 else None

            xu = rg.Vector3d(xax)
            if xu.Length > 1e-12:
                xu.Unitize()
            else:
                xu = rg.Vector3d(1, 0, 0)
            yu = rg.Vector3d(yax)
            if yu.Length > 1e-12:
                yu.Unitize()
            else:
                yu = rg.Vector3d(0, 1, 0)
            zu = rg.Vector3d(zax)
            if zu.Length > 1e-12:
                zu.Unitize()
            else:
                zu = rg.Vector3d(0, 0, 1)
            di = float(astep) * sx
            dj = float(astep) * sy
            dk = float(astep) * sz
            if di < 1e-12:
                di = 1.0
            if dj < 1e-12:
                dj = 1.0
            if dk < 1e-12:
                dk = 1.0

            def cget(arr, ci, cj, ck):
                ci = max(0, min(nci - 1, ci))
                cj = max(0, min(ncj - 1, cj))
                ck = max(0, min(nck - 1, ck))
                return arr[ci + cj * nci + ck * nci * ncj]

            def d_i(arr, ci, cj, ck):
                if nci <= 1:
                    return 0.0
                s0 = cget(arr, ci, cj, ck)
                if ci == 0:
                    return (cget(arr, 1, cj, ck) - s0) / di
                if ci == nci - 1:
                    return (s0 - cget(arr, nci - 2, cj, ck)) / di
                return (cget(arr, ci + 1, cj, ck) - cget(arr, ci - 1, cj, ck)) / (2.0 * di)

            def d_j(arr, ci, cj, ck):
                if ncj <= 1:
                    return 0.0
                s0 = cget(arr, ci, cj, ck)
                if cj == 0:
                    return (cget(arr, ci, 1, ck) - s0) / dj
                if cj == ncj - 1:
                    return (s0 - cget(arr, ci, ncj - 2, ck)) / dj
                return (cget(arr, ci, cj + 1, ck) - cget(arr, ci, cj - 1, ck)) / (2.0 * dj)

            def d_k(arr, ci, cj, ck):
                if nck <= 1:
                    return 0.0
                s0 = cget(arr, ci, cj, ck)
                if ck == 0:
                    return (cget(arr, ci, cj, 1) - s0) / dk
                if ck == nck - 1:
                    return (s0 - cget(arr, ci, cj, nck - 2)) / dk
                return (cget(arr, ci, cj, ck + 1) - cget(arr, ci, cj, ck - 1)) / (2.0 * dk)

            gradients_out = []
            for ck in range(nck):
                for cj in range(ncj):
                    for ci in range(nci):
                        gi = d_i(coarse_composite, ci, cj, ck)
                        gj = d_j(coarse_composite, ci, cj, ck)
                        gk = d_k(coarse_composite, ci, cj, ck)
                        gw = xu * gi + yu * gj + zu * gk
                        gradients_out.append(
                            [
                                round(gw.X, GRAD_DECIMALS),
                                round(gw.Y, GRAD_DECIMALS),
                                round(gw.Z, GRAD_DECIMALS),
                            ]
                        )

            scores_out = [round(v, SCORE_DECIMALS) for v in coarse_composite]

            payload = {
                "version": 2,
                "grid_def": gd,
                "base_range": {"i": [i0, i1], "j": [j0, j1], "k": [k0, k1]},
                "base_dims": {"ni": ni, "nj": nj, "nk": nk},
                "analysis_step": astep,
                "coarse_dims": {"ni": nci, "nj": ncj, "nk": nck},
                "flat_index_order": "ci_cj_ck",
                "times": times,
                "n_samples": n_samples,
                "preview_mode": preview_mode,
                "include_buffer": include_buffer,
                "score_decimals": SCORE_DECIMALS,
                "grad_decimals": GRAD_DECIMALS,
                "has_context_shadows": has_shadows,
                "scores": scores_out,
                "gradients": gradients_out,
            }
            solar_scores = json.dumps(payload, separators=(",", ":"))

            log_lines = [
                "solar_analysis v2 | step:{} samples:{} preview:{} buffer:{} shadows:{}".format(
                    astep, n_samples, preview_mode, include_buffer, has_shadows
                ),
                "Base ni:{} nj:{} nk:{} | Coarse ni:{} nj:{} nk:{} cells {}".format(
                    ni, nj, nk, nci, ncj, nck, n_coarse
                ),
                "solar_scores: {} chars".format(len(solar_scores)),
            ]
            log = "\n".join(log_lines)
