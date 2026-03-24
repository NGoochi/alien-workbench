#! python 3
# NODE_INPUTS: grid_def:str, ext_pos_x:float, ext_neg_x:float, ext_pos_y:float, ext_neg_y:float, ext_pos_z:float, ext_neg_z:float, buffer_dist:float, override_brep:Brep
# NODE_OUTPUTS: site_def, site_brep, buffer_brep, log
#
# subject_site — site envelope inside base_grid (grid_def v2). Core + buffer Breps;
# site_def JSON carries cell index ranges (any intersecting voxel). Optional Brep override.

import math
import json
import Rhino.Geometry as rg

# ─── DEFAULTS ───────────────────────────────────────────────────────

if ext_pos_x is None or ext_pos_x < 0:
    ext_pos_x = 20000.0
if ext_neg_x is None or ext_neg_x < 0:
    ext_neg_x = 20000.0
if ext_pos_y is None or ext_pos_y < 0:
    ext_pos_y = 20000.0
if ext_neg_y is None or ext_neg_y < 0:
    ext_neg_y = 20000.0
if ext_pos_z is None or ext_pos_z < 0:
    ext_pos_z = 20000.0
if ext_neg_z is None or ext_neg_z < 0:
    ext_neg_z = 20000.0
if buffer_dist is None or buffer_dist < 0:
    buffer_dist = 5000.0

site_brep = None
buffer_brep = None
site_def = "{}"
log = ""

# ─── PARSE grid_def ─────────────────────────────────────────────────

if grid_def is None or not str(grid_def).strip():
    log = "Error: grid_def is empty. Wire base_grid grid_def output."
else:
    try:
        gd = json.loads(str(grid_def))
    except Exception:
        log = "Error: grid_def is not valid JSON"
        gd = None

    if gd is not None:
        ver = gd.get("version", 0)
        if ver != 2:
            log = "Error: grid_def version must be 2, got {}".format(ver)
            gd = None

    if gd is not None:
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

        grid_plane = rg.Plane(org, xax, yax)

        def world_to_local(pt):
            vx = pt.X - org.X
            vy = pt.Y - org.Y
            vz = pt.Z - org.Z
            lx = vx * xax.X + vy * xax.Y + vz * xax.Z
            ly = vx * yax.X + vy * yax.Y + vz * yax.Z
            lz = vx * zax.X + vy * zax.Y + vz * zax.Z
            return lx, ly, lz

        def axis_range_from_ext(ep, en):
            return -float(en), float(ep)

        use_override = override_brep is not None

        if use_override:
            bb = override_brep.GetBoundingBox(True)
            corners = bb.GetCorners()
            lx0 = ly0 = lz0 = float("inf")
            lx1 = ly1 = lz1 = float("-inf")
            for ci in range(len(corners)):
                lx, ly, lz = world_to_local(corners[ci])
                if lx < lx0:
                    lx0 = lx
                if lx > lx1:
                    lx1 = lx
                if ly < ly0:
                    ly0 = ly
                if ly > ly1:
                    ly1 = ly
                if lz < lz0:
                    lz0 = lz
                if lz > lz1:
                    lz1 = lz
            sx_lo, sx_hi = lx0, lx1
            sy_lo, sy_hi = ly0, ly1
            sz_lo, sz_hi = lz0, lz1
            site_extents_list = [
                max(0.0, -sx_lo),
                max(0.0, sx_hi),
                max(0.0, -sy_lo),
                max(0.0, sy_hi),
                max(0.0, -sz_lo),
                max(0.0, sz_hi),
            ]
        else:
            sx_lo, sx_hi = axis_range_from_ext(ext_pos_x, ext_neg_x)
            sy_lo, sy_hi = axis_range_from_ext(ext_pos_y, ext_neg_y)
            sz_lo, sz_hi = axis_range_from_ext(ext_pos_z, ext_neg_z)
            site_extents_list = [
                float(ext_neg_x),
                float(ext_pos_x),
                float(ext_neg_y),
                float(ext_pos_y),
                float(ext_neg_z),
                float(ext_pos_z),
            ]

        bx_lo = sx_lo - buffer_dist
        bx_hi = sx_hi + buffer_dist
        by_lo = sy_lo - buffer_dist
        by_hi = sy_hi + buffer_dist
        bz_lo = sz_lo - buffer_dist
        bz_hi = sz_hi + buffer_dist

        gx_lo = -half_nx * sx
        gx_hi = half_nx * sx
        gy_lo = -half_ny * sy
        gy_hi = half_ny * sy
        gz_lo = -half_nz * sz
        gz_hi = half_nz * sz

        def clamp_interval(lo, hi, g0, g1):
            return max(lo, g0), min(hi, g1)

        sx_lo, sx_hi = clamp_interval(sx_lo, sx_hi, gx_lo, gx_hi)
        sy_lo, sy_hi = clamp_interval(sy_lo, sy_hi, gy_lo, gy_hi)
        sz_lo, sz_hi = clamp_interval(sz_lo, sz_hi, gz_lo, gz_hi)
        bx_lo, bx_hi = clamp_interval(bx_lo, bx_hi, gx_lo, gx_hi)
        by_lo, by_hi = clamp_interval(by_lo, by_hi, gy_lo, gy_hi)
        bz_lo, bz_hi = clamp_interval(bz_lo, bz_hi, gz_lo, gz_hi)

        def cell_range_axis(lo, hi, half_n, n, cell):
            if lo > hi or cell <= 0:
                return -1, -1
            t0 = lo / cell + half_n - 0.5
            t1 = hi / cell + half_n + 0.5
            i0 = int(math.ceil(t0))
            i1 = int(math.floor(t1))
            if i0 < 0:
                i0 = 0
            if i1 > n - 1:
                i1 = n - 1
            if i0 > i1:
                return -1, -1
            return i0, i1

        i0, i1 = cell_range_axis(sx_lo, sx_hi, half_nx, nx, sx)
        j0, j1 = cell_range_axis(sy_lo, sy_hi, half_ny, ny, sy)
        k0, k1 = cell_range_axis(sz_lo, sz_hi, half_nz, nz, sz)
        bi0, bi1 = cell_range_axis(bx_lo, bx_hi, half_nx, nx, sx)
        bj0, bj1 = cell_range_axis(by_lo, by_hi, half_ny, ny, sy)
        bk0, bk1 = cell_range_axis(bz_lo, bz_hi, half_nz, nz, sz)

        def range_count(a0, a1):
            if a0 < 0 or a1 < a0:
                return 0
            return (a1 - a0 + 1)

        site_cell_count = range_count(i0, i1) * range_count(j0, j1) * range_count(k0, k1)
        buffer_cell_count = range_count(bi0, bi1) * range_count(bj0, bj1) * range_count(bk0, bk1)

        site_brep = None
        if sx_lo <= sx_hi and sy_lo <= sy_hi and sz_lo <= sz_hi:
            site_ivx = rg.Interval(sx_lo, sx_hi)
            site_ivy = rg.Interval(sy_lo, sy_hi)
            site_ivz = rg.Interval(sz_lo, sz_hi)
            site_box = rg.Box(grid_plane, site_ivx, site_ivy, site_ivz)
            sb = site_box.ToBrep()
            site_brep = sb if sb else None

        buffer_brep = None
        if bx_lo <= bx_hi and by_lo <= by_hi and bz_lo <= bz_hi:
            buf_ivx = rg.Interval(bx_lo, bx_hi)
            buf_ivy = rg.Interval(by_lo, by_hi)
            buf_ivz = rg.Interval(bz_lo, bz_hi)
            buf_box = rg.Box(grid_plane, buf_ivx, buf_ivy, buf_ivz)
            bbrep = buf_box.ToBrep()
            buffer_brep = bbrep if bbrep else None

        payload = {
            "version": 1,
            "grid_def": gd,
            "site_range": {"i": [i0, i1], "j": [j0, j1], "k": [k0, k1]},
            "buffer_range": {"i": [bi0, bi1], "j": [bj0, bj1], "k": [bk0, bk1]},
            "site_extents": site_extents_list,
            "buffer_dist": buffer_dist,
            "site_cell_count": site_cell_count,
            "buffer_cell_count": buffer_cell_count,
            "override_brep": use_override,
        }
        site_def = json.dumps(payload, separators=(",", ":"))

        log_lines = [
            "subject_site v1 | mode: {}".format("override_brep" if use_override else "extents"),
            "Site cells i:{} j:{} k:{} (count {})".format(
                [i0, i1], [j0, j1], [k0, k1], site_cell_count,
            ),
            "Buffer cells i:{} j:{} k:{} (count {})".format(
                [bi0, bi1], [bj0, bj1], [bk0, bk1], buffer_cell_count,
            ),
            "site_def: {} chars".format(len(site_def)),
        ]
        log = "\n".join(log_lines)
