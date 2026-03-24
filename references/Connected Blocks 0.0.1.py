#! python3
"""
Grow Fill  —  Rooms + Connectors
==================================
Fills a user-selected closed volume by chaining room blocks to one another
through connector blocks:

    Room ──► ConnectorBlock ──► Room ──► ConnectorBlock ──► …

HOW IT WORKS
────────────
1.  Any block whose Point objects carry the four required user-strings
    (conn_type, mate_type, dir, up) is treated as "connector-capable".
    Both the room block and the connector block must be connector-capable.
    The dropdown lists are filtered to show only connector-capable blocks.

2.  A seed room is placed at the centre (or first valid location) of the
    target volume.

3.  A BFS queue of world-space open connectors grows outward:
      • Room open connectors   → snap a connector block
      • ConnectorBlock outputs → snap a new room
    …until the volume is full or max-rooms is reached.

4.  Rooms are checked for:  (a) centre inside target, (b) no overlap with
    other rooms.  Connector blocks only need to avoid each other (they may
    sit at a room boundary), so they are guarded against a separate list.

CONNECTOR POINT USER-STRINGS  (Object Properties → User Text)
───────────────────────────────────────────────────────────────
    conn_type   e.g. "room_port"   — what this port IS
    mate_type   e.g. "conn_port"   — what it CAN JOIN
    dir         e.g. "1,0,0"       — outward unit vector (world / local)
    up          e.g. "0,0,1"       — up unit vector
    role        (optional) "side" | "in" | "out"
    priority    (optional) integer — higher wins when multiple matches exist

Two connectors A and B can join when:
    A.conn_type == B.mate_type   AND   A.mate_type == B.conn_type
"""

import Rhino
import scriptcontext as sc
import System

import Eto.Forms  as forms
import Eto.Drawing as drawing
from Rhino.UI import RhinoEtoApp, EtoExtensions


# ─────────────────────────────────────────────────────────────────────────────
# DATA
# ─────────────────────────────────────────────────────────────────────────────

class Connector(object):
    """A single connector point in either local (definition) or world space."""

    def __init__(self, owner_block_name, point, direction, up,
                 conn_type="", mate_type="", role="side", priority=0):
        self.owner_block_name = owner_block_name
        self.point     = point        # Rhino.Geometry.Point3d
        self.direction = direction    # Rhino.Geometry.Vector3d  (outward normal)
        self.up        = up           # Rhino.Geometry.Vector3d
        self.conn_type = conn_type
        self.mate_type = mate_type
        self.role      = role
        self.priority  = priority

    def plane(self):
        """Connector plane: origin = point, X = direction, Y = up."""
        x = Rhino.Geometry.Vector3d(self.direction)
        y = Rhino.Geometry.Vector3d(self.up)
        if not x.Unitize() or not y.Unitize():
            return None
        return Rhino.Geometry.Plane(self.point, x, y)

    def world_key(self, bucket=1.0):
        """Rounded integer tuple — used to deduplicate connectors by position."""
        p = self.point
        b = max(bucket, 1e-9)
        return (int(round(p.X / b)),
                int(round(p.Y / b)),
                int(round(p.Z / b)))


# ─────────────────────────────────────────────────────────────────────────────
# DOCUMENT HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _parse_vector(text):
    try:
        vals = [float(v.strip()) for v in text.split(",")]
        if len(vals) != 3:
            return None
        v = Rhino.Geometry.Vector3d(vals[0], vals[1], vals[2])
        return v if v.Unitize() else None
    except Exception:
        return None


def _get_idef(name):
    for idef in sc.doc.InstanceDefinitions:
        if idef and not idef.IsDeleted and idef.Name == name:
            return idef
    return None


def get_connectors_from_block(block_name):
    """Return all Connector objects defined inside a block definition."""
    idef = _get_idef(block_name)
    if idef is None:
        return []

    out = []
    for obj_id in idef.GetObjectIds():
        obj = sc.doc.Objects.FindId(obj_id)
        if obj is None:
            continue
        if not isinstance(obj.Geometry, Rhino.Geometry.Point):
            continue

        attr = obj.Attributes
        ct = attr.GetUserString("conn_type")
        mt = attr.GetUserString("mate_type")
        ds = attr.GetUserString("dir")
        us = attr.GetUserString("up")
        if not ct or not mt or not ds or not us:
            continue

        direction = _parse_vector(ds)
        up        = _parse_vector(us)
        if direction is None or up is None:
            continue

        role = attr.GetUserString("role") or "side"
        pri  = 0
        try:
            ps = attr.GetUserString("priority")
            if ps:
                pri = int(ps)
        except Exception:
            pri = 0

        out.append(Connector(
            owner_block_name=block_name,
            point=Rhino.Geometry.Point3d(obj.Geometry.Location),
            direction=direction,
            up=up,
            conn_type=ct,
            mate_type=mt,
            role=role,
            priority=pri,
        ))
    return out


def block_local_bbox(block_name):
    """Axis-aligned bounding box of all geometry inside a block (local space)."""
    idef = _get_idef(block_name)
    if idef is None:
        return None
    bbox  = Rhino.Geometry.BoundingBox.Empty
    first = True
    for obj_id in idef.GetObjectIds():
        obj = sc.doc.Objects.FindId(obj_id)
        if obj is None:
            continue
        gbox = obj.Geometry.GetBoundingBox(True)
        if not gbox.IsValid:
            continue
        if first:
            bbox  = gbox
            first = False
        else:
            bbox.Union(gbox)
    return bbox if bbox.IsValid else None


def list_all_blocks():
    """All non-deleted block definition names, sorted."""
    names = []
    for idef in sc.doc.InstanceDefinitions:
        if idef and not idef.IsDeleted:
            names.append(idef.Name)
    names.sort()
    return names


def diagnose_block(block_name):
    """
    Return a short human-readable string explaining the connector status of
    a block — used in the status bar so the user can see what is missing.
    """
    idef = _get_idef(block_name)
    if idef is None:
        return "block '{}' not found".format(block_name)

    point_count = 0
    tagged_count = 0
    missing = []

    for obj_id in idef.GetObjectIds():
        obj = sc.doc.Objects.FindId(obj_id)
        if obj is None:
            continue
        if not isinstance(obj.Geometry, Rhino.Geometry.Point):
            continue
        point_count += 1
        attr = obj.Attributes
        ct = attr.GetUserString("conn_type")
        mt = attr.GetUserString("mate_type")
        ds = attr.GetUserString("dir")
        us = attr.GetUserString("up")
        if ct and mt and ds and us:
            tagged_count += 1
        else:
            lacking = [k for k, v in
                       [("conn_type", ct), ("mate_type", mt),
                        ("dir", ds), ("up", us)] if not v]
            missing.append("point missing: {}".format(", ".join(lacking)))

    if tagged_count > 0:
        return "'{}' OK — {} connector(s)".format(block_name, tagged_count)
    if point_count == 0:
        return "'{}' has no Point objects — add Points with user-strings".format(
            block_name)
    return "'{}' has {} point(s) but none fully tagged. {}".format(
        block_name, point_count,
        missing[0] if missing else "check user-strings"
    )

# ─────────────────────────────────────────────────────────────────────────────
# AUTO-SETUP: ADD / REMOVE CONNECTOR POINTS IN A BLOCK DEFINITION
# ─────────────────────────────────────────────────────────────────────────────

# Face key -> (point_factory, dir_string, up_string)
# Points are placed at the centre of each bounding-box face.
def _face_data(bbox):
    cx = (bbox.Min.X + bbox.Max.X) / 2.0
    cy = (bbox.Min.Y + bbox.Max.Y) / 2.0
    cz = (bbox.Min.Z + bbox.Max.Z) / 2.0
    return {
        "+X": (Rhino.Geometry.Point3d(bbox.Max.X, cy, cz), "1,0,0",  "0,0,1"),
        "-X": (Rhino.Geometry.Point3d(bbox.Min.X, cy, cz), "-1,0,0", "0,0,1"),
        "+Y": (Rhino.Geometry.Point3d(cx, bbox.Max.Y, cz), "0,1,0",  "0,0,1"),
        "-Y": (Rhino.Geometry.Point3d(cx, bbox.Min.Y, cz), "0,-1,0", "0,0,1"),
        "+Z": (Rhino.Geometry.Point3d(cx, cy, bbox.Max.Z), "0,0,1",  "1,0,0"),
        "-Z": (Rhino.Geometry.Point3d(cx, cy, bbox.Min.Z), "0,0,-1", "1,0,0"),
    }


def add_connector_points_to_block(block_name, face_keys, conn_type, mate_type):
    """
    Inject tagged Point objects at the chosen bbox face centres of block_name.

    face_keys  : list of strings from {"+X","-X","+Y","-Y","+Z","-Z"}
    conn_type  : user-string value for conn_type on every new point
    mate_type  : user-string value for mate_type on every new point

    Uses InstanceDefinitions.ModifyGeometry to rebuild the definition
    in-place without touching existing instances in the document.
    Returns (ok, message).
    """
    if not face_keys:
        return False, "No faces selected."

    idef = _get_idef(block_name)
    if idef is None:
        return False, "Block '{}' not found.".format(block_name)

    bbox = block_local_bbox(block_name)
    if bbox is None:
        return False, "Cannot compute bounding box for '{}'.".format(block_name)

    face_map = _face_data(bbox)

    # Collect existing geometry + attributes (preserve everything already there)
    geo_list   = []
    attr_list  = []
    for obj_id in idef.GetObjectIds():
        obj = sc.doc.Objects.FindId(obj_id)
        if obj is None:
            continue
        geo_list.append(obj.Geometry.Duplicate())
        attr_list.append(obj.Attributes.Duplicate())

    added = 0
    for key in face_keys:
        if key not in face_map:
            continue
        pt, dir_str, up_str = face_map[key]

        point_geo = Rhino.Geometry.Point(pt)
        attr = Rhino.DocObjects.ObjectAttributes()
        attr.SetUserString("conn_type", conn_type)
        attr.SetUserString("mate_type", mate_type)
        attr.SetUserString("dir",       dir_str)
        attr.SetUserString("up",        up_str)

        geo_list.append(point_geo)
        attr_list.append(attr)
        added += 1

    if added == 0:
        return False, "No valid face keys matched."

    ok = sc.doc.InstanceDefinitions.ModifyGeometry(idef.Index, geo_list, attr_list)
    if ok:
        sc.doc.Views.Redraw()
        return True, "Added {} connector point(s) to '{}'.".format(added, block_name)
    return False, "ModifyGeometry failed for '{}'.".format(block_name)


def remove_connector_points_from_block(block_name):
    """
    Strip all tagged connector Point objects from a block definition.
    Non-point geometry is preserved.
    Returns (ok, message).
    """
    idef = _get_idef(block_name)
    if idef is None:
        return False, "Block '{}' not found.".format(block_name)

    geo_list  = []
    attr_list = []
    removed   = 0

    for obj_id in idef.GetObjectIds():
        obj = sc.doc.Objects.FindId(obj_id)
        if obj is None:
            continue

        # Drop any Point that has conn_type tagged — those are our connector points
        if isinstance(obj.Geometry, Rhino.Geometry.Point):
            ct = obj.Attributes.GetUserString("conn_type")
            if ct:
                removed += 1
                continue   # skip — do not add to new list

        geo_list.append(obj.Geometry.Duplicate())
        attr_list.append(obj.Attributes.Duplicate())

    if removed == 0:
        return False, "No connector points found in '{}'.".format(block_name)

    ok = sc.doc.InstanceDefinitions.ModifyGeometry(idef.Index, geo_list, attr_list)
    if ok:
        sc.doc.Views.Redraw()
        return True, "Removed {} connector point(s) from '{}'.".format(removed, block_name)
    return False, "ModifyGeometry failed for '{}'.".format(block_name)




def add_block_instance(block_name, xform):
    idef = _get_idef(block_name)
    if idef is None:
        return System.Guid.Empty
    return sc.doc.Objects.AddInstanceObject(idef.Index, xform)


# ─────────────────────────────────────────────────────────────────────────────
# GEOMETRY HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def transform_bbox(bbox, xform):
    pts = []
    for c in bbox.GetCorners():
        p = Rhino.Geometry.Point3d(c)
        p.Transform(xform)
        pts.append(p)
    return Rhino.Geometry.BoundingBox(pts)


def transform_connector(conn, xform):
    """Return a new Connector with position + direction mapped through xform."""
    pt = Rhino.Geometry.Point3d(conn.point)
    pt.Transform(xform)

    dv = Rhino.Geometry.Vector3d(conn.direction)
    dv.Transform(xform)
    if not dv.Unitize():
        dv = Rhino.Geometry.Vector3d(1.0, 0.0, 0.0)

    uv = Rhino.Geometry.Vector3d(conn.up)
    uv.Transform(xform)
    if not uv.Unitize():
        uv = Rhino.Geometry.Vector3d(0.0, 0.0, 1.0)

    return Connector(
        owner_block_name=conn.owner_block_name,
        point=pt,
        direction=dv,
        up=uv,
        conn_type=conn.conn_type,
        mate_type=conn.mate_type,
        role=conn.role,
        priority=conn.priority,
    )


def bbox_centre_inside(bbox, target_kind, target_geom, tol):
    """Test whether the bbox centre is inside the target geometry."""
    c = bbox.Center
    if target_kind == "brep":
        return target_geom.IsPointInside(c, tol, True)
    if target_kind == "mesh":
        return target_geom.IsPointInside(c, tol, False)
    return False


def bboxes_overlap(a, b, tol):
    """True only when bboxes interpenetrate by more than tol (touching is OK)."""
    return (
        a.Min.X < b.Max.X - tol and a.Max.X > b.Min.X + tol and
        a.Min.Y < b.Max.Y - tol and a.Max.Y > b.Min.Y + tol and
        a.Min.Z < b.Max.Z - tol and a.Max.Z > b.Min.Z + tol
    )


def pick_target_geometry():
    go = Rhino.Input.Custom.GetObject()
    go.SetCommandPrompt("Select closed polysurface or mesh to fill")
    go.GeometryFilter = (Rhino.DocObjects.ObjectType.PolysrfFilter |
                         Rhino.DocObjects.ObjectType.Mesh)
    go.SubObjectSelect = False
    go.Get()
    if go.CommandResult() != Rhino.Commands.Result.Success:
        return None, None
    objref = go.Object(0)
    if not objref:
        return None, None
    brep = objref.Brep()
    if brep:
        return "brep", brep
    mesh = objref.Mesh()
    if mesh:
        return "mesh", mesh
    return None, None


# ─────────────────────────────────────────────────────────────────────────────
# CONNECTOR MATCHING + ATTACH TRANSFORM
# ─────────────────────────────────────────────────────────────────────────────

def connectors_match(a, b):
    """Two connectors can join when their type tags are perfectly reciprocal."""
    return (a.conn_type == b.mate_type) and (a.mate_type == b.conn_type)


def attach_xform(open_world_conn, local_conn):
    """
    Compute the transform that snaps a new block so that local_conn
    'plugs into' open_world_conn.

    After the transform:
        local_conn is positioned at  open_world_conn.point
        local_conn faces back into the source  (-open_world_conn.direction)

    Implementation:  PlaneToPlane( local_conn.plane,  target_plane )
    where target_plane has:
        origin  = open_world_conn.point
        X-axis  = -open_world_conn.direction   (anti-parallel = facing back)
        Y-axis  =  open_world_conn.up
    """
    src = local_conn.plane()
    if src is None:
        return None

    tx = Rhino.Geometry.Vector3d(open_world_conn.direction)
    tx.Negate()
    ty = Rhino.Geometry.Vector3d(open_world_conn.up)
    if not tx.Unitize() or not ty.Unitize():
        return None

    tgt = Rhino.Geometry.Plane(open_world_conn.point, tx, ty)
    return Rhino.Geometry.Transform.PlaneToPlane(src, tgt)


# ─────────────────────────────────────────────────────────────────────────────
# SEED PLACEMENT
# ─────────────────────────────────────────────────────────────────────────────

def find_seed_xform(room_local_bbox, target_kind, target_geom, tol):
    """
    Find the first valid xform that places the room block fully inside
    the target.  Tries the target centre first, then a coarse grid scan.
    """
    tgt_bbox = target_geom.GetBoundingBox(True)

    # Attempt 1: centre of target
    move  = tgt_bbox.Center - room_local_bbox.Center
    xform = Rhino.Geometry.Transform.Translation(move)
    if bbox_centre_inside(transform_bbox(room_local_bbox, xform),
                          target_kind, target_geom, tol):
        return xform

    # Attempt 2: coarse grid scan, one-room-sized steps
    dx = room_local_bbox.Diagonal.X or 1.0
    dy = room_local_bbox.Diagonal.Y or 1.0
    dz = room_local_bbox.Diagonal.Z or 1.0

    x = tgt_bbox.Min.X
    while x <= tgt_bbox.Max.X:
        y = tgt_bbox.Min.Y
        while y <= tgt_bbox.Max.Y:
            z = tgt_bbox.Min.Z
            while z <= tgt_bbox.Max.Z:
                pt    = Rhino.Geometry.Point3d(x, y, z)
                move  = pt - room_local_bbox.Min
                xform = Rhino.Geometry.Transform.Translation(move)
                if bbox_centre_inside(transform_bbox(room_local_bbox, xform),
                                      target_kind, target_geom, tol):
                    return xform
                z += dz
            y += dy
        x += dx

    return None


# ─────────────────────────────────────────────────────────────────────────────
# GROWTH-FILL ALGORITHM
# ─────────────────────────────────────────────────────────────────────────────

def grow_fill(room_block_name, cb_block_name,
              target_kind, target_geom, max_rooms=200):
    """
    BFS growth that fills target_geom with the repeating pattern:

        Room ──► ConnectorBlock ──► Room ──► ConnectorBlock ──► …

    Overlap rules
    ─────────────
    Rooms       : must not overlap any other room; centre must be inside target.
    ConnBlocks  : must not overlap any other connector block.
                  (They straddle room boundaries, so room overlap is intentional.)

    Returns (ok, message, room_xforms, cb_xforms).
    """

    # ── Definitions ──────────────────────────────────────────────────────────
    room_local_conns = get_connectors_from_block(room_block_name)
    cb_local_conns   = get_connectors_from_block(cb_block_name)
    room_local_bbox  = block_local_bbox(room_block_name)
    cb_local_bbox    = block_local_bbox(cb_block_name)

    if not room_local_conns:
        return False, "Room block has no connector points.", [], []
    if not cb_local_conns:
        return False, "Connector block has no connector points.", [], []
    if room_local_bbox is None:
        return False, "Cannot compute room bounding box.", [], []

    tol = sc.doc.ModelAbsoluteTolerance

    # Bucket size for deduplicating connector positions.
    # Using 10 x model tolerance avoids float noise flagging the same port twice.
    bucket = max(tol * 10.0, 1e-3)

    # ── Seed ─────────────────────────────────────────────────────────────────
    seed_xform = find_seed_xform(room_local_bbox, target_kind, target_geom, tol)
    if seed_xform is None:
        return False, "No valid seed placement found inside target.", [], []

    # ── Mutable state ─────────────────────────────────────────────────────────
    room_xforms     = []
    cb_xforms       = []
    placed_room_bbs = []   # world-space room bboxes — for room-vs-room overlap
    placed_cb_bbs   = []   # world-space cb bboxes   — for cb-vs-cb overlap
    used_keys       = set()  # deduplication set for world-space connector positions

    def _key(wc):
        return wc.world_key(bucket=bucket)

    # ── Inner: register a room placement ─────────────────────────────────────
    def try_place_room(xform, skip_local_idx):
        """
        Validate and record a room at xform.
        skip_local_idx : local connector index already consumed by the
                         connector block that brought us here (-1 = none).
        Returns list of (local_idx, world_conn) for each new open port,
        or None if the placement is invalid.
        """
        bbox = transform_bbox(room_local_bbox, xform)

        # Room centre must be inside target
        if not bbox_centre_inside(bbox, target_kind, target_geom, tol):
            return None

        # Room must not overlap any other room
        for pb in placed_room_bbs:
            if bboxes_overlap(bbox, pb, tol):
                return None

        placed_room_bbs.append(bbox)
        room_xforms.append(xform)

        # Collect open ports (all connectors except the one used to arrive)
        open_ports = []
        for i, lc in enumerate(room_local_conns):
            if i == skip_local_idx:
                continue
            wc = transform_connector(lc, xform)
            k  = _key(wc)
            if k not in used_keys:
                open_ports.append((i, wc))
        return open_ports

    # ── Inner: snap a connector block to a room port ──────────────────────────
    def try_place_connector(open_room_wc):
        """
        Find the best-matching connector block connector for open_room_wc,
        position the block, and record it.
        Returns list of (local_idx, world_conn) for the cb's output ports,
        or None on failure.
        """
        best_xf    = None
        best_idx   = -1
        best_score = -999999

        for i, lc in enumerate(cb_local_conns):
            if not connectors_match(open_room_wc, lc):
                continue
            xf = attach_xform(open_room_wc, lc)
            if xf is None:
                continue
            score = open_room_wc.priority + lc.priority
            if score > best_score:
                best_score = score
                best_xf    = xf
                best_idx   = i

        if best_xf is None:
            return None

        # Connector block must not overlap any other connector block
        if cb_local_bbox is not None:
            cb_world_bb = transform_bbox(cb_local_bbox, best_xf)
            for pb in placed_cb_bbs:
                if bboxes_overlap(cb_world_bb, pb, tol):
                    return None
            placed_cb_bbs.append(cb_world_bb)

        cb_xforms.append(best_xf)

        # Collect output ports (all cb connectors except the one used to attach)
        outputs = []
        for j, lc in enumerate(cb_local_conns):
            if j == best_idx:
                continue
            wc = transform_connector(lc, best_xf)
            outputs.append((j, wc))
        return outputs

    # ── Place seed room (no incoming connector, so skip_local_idx = -1) ───────
    seed_ports = try_place_room(seed_xform, -1)
    if seed_ports is None:
        return False, "Seed room is not inside the target volume.", [], []

    # BFS queue contains (local_room_conn_idx, world_connector) tuples.
    # Each item is an open room port waiting to receive a connector block.
    queue = list(seed_ports)

    max_iter   = max_rooms * 20
    iterations = 0

    # ── BFS ───────────────────────────────────────────────────────────────────
    while queue and len(room_xforms) < max_rooms and iterations < max_iter:
        iterations += 1
        _local_idx, open_wc = queue.pop(0)

        k = _key(open_wc)
        if k in used_keys:
            continue
        used_keys.add(k)

        # Step 1: try to attach a connector block to this room port
        cb_outputs = try_place_connector(open_wc)
        if cb_outputs is None:
            continue

        # Step 2: for each output port of the connector block, grow a new room
        for _, cb_out_wc in cb_outputs:
            ck = _key(cb_out_wc)
            if ck in used_keys:
                continue

            # Find the best room connector that matches this cb output
            best_ri    = -1
            best_score = -999999
            for ri, rlc in enumerate(room_local_conns):
                if not connectors_match(cb_out_wc, rlc):
                    continue
                score = cb_out_wc.priority + rlc.priority
                if score > best_score:
                    best_score = score
                    best_ri    = ri

            if best_ri == -1:
                continue

            xf = attach_xform(cb_out_wc, room_local_conns[best_ri])
            if xf is None:
                continue

            new_ports = try_place_room(xf, best_ri)
            if new_ports is None:
                continue

            # Mark the cb output as consumed and enqueue the new room's open ports
            used_keys.add(ck)
            queue.extend(new_ports)

    msg = "Placed {} room(s) and {} connector block(s).".format(
        len(room_xforms), len(cb_xforms)
    )
    return True, msg, room_xforms, cb_xforms


# ─────────────────────────────────────────────────────────────────────────────
# INSERT INTO DOCUMENT
# ─────────────────────────────────────────────────────────────────────────────

def insert_results(room_block_name, cb_block_name, room_xforms, cb_xforms):
    rooms = []
    cbs   = []
    for xf in room_xforms:
        gid = add_block_instance(room_block_name, xf)
        if gid != System.Guid.Empty:
            rooms.append(gid)
    for xf in cb_xforms:
        gid = add_block_instance(cb_block_name, xf)
        if gid != System.Guid.Empty:
            cbs.append(gid)
    sc.doc.Views.Redraw()
    return rooms, cbs


# ─────────────────────────────────────────────────────────────────────────────
# UI HELPERS  —  IronPython 2.7: Eto constructors take NO keyword arguments
# ─────────────────────────────────────────────────────────────────────────────

def _label(text):
    w = forms.Label()
    w.Text = text
    return w

def _button(text):
    w = forms.Button()
    w.Text = text
    return w

def _numeric(value=0.0, decimals=0, increment=10.0):
    w = forms.NumericStepper()
    w.DecimalPlaces = decimals
    w.Increment     = increment
    w.Value         = value
    return w


# ─────────────────────────────────────────────────────────────────────────────
# DIALOG
# ─────────────────────────────────────────────────────────────────────────────

class GrowFillDialog(forms.Dialog):

    def __init__(self):
        super(GrowFillDialog, self).__init__()

        self.Title     = "Grow Fill  —  Rooms + Connectors"
        self.Padding   = drawing.Padding(0)
        self.Resizable = True
        self.Width     = 480
        self.Height    = 380

        EtoExtensions.UseRhinoStyle(self)

        blocks = list_all_blocks()

        # ── Block selectors ────────────────────────────────────────────────
        self.dd_room = forms.DropDown()
        self.dd_room.DataStore = blocks
        self.dd_room.SelectedIndexChanged += self.on_selection_changed

        self.dd_connector = forms.DropDown()
        self.dd_connector.DataStore = blocks
        self.dd_connector.SelectedIndexChanged += self.on_selection_changed

        # ── Max rooms limit ────────────────────────────────────────────────
        self.num_max = _numeric(value=100, decimals=0, increment=25)

        # ── Buttons ────────────────────────────────────────────────────────
        self.btn_pick    = _button("Pick Fill Geometry")
        self.btn_run     = _button("Grow Fill")
        self.btn_refresh = _button("Refresh Blocks")
        self.btn_clear   = _button("Clear Last")

        self.btn_pick.Click    += self.on_pick
        self.btn_run.Click     += self.on_run
        self.btn_refresh.Click += self.on_refresh
        self.btn_clear.Click   += self.on_clear

        # ── Status labels ──────────────────────────────────────────────────
        self.lbl_target = _label("No target selected")
        self.lbl_status = _label("Ready")

        # ── Internal state ─────────────────────────────────────────────────
        self.target_kind = None
        self.target_geom = None
        self._last_rooms = []
        self._last_cbs   = []

        if blocks:
            self.dd_room.SelectedIndex      = 0
            self.dd_connector.SelectedIndex = min(1, len(blocks) - 1)

        # ── Setup: auto-add connector points ───────────────────────────────
        self.dd_setup = forms.DropDown()
        self.dd_setup.DataStore = blocks
        if blocks:
            self.dd_setup.SelectedIndex = 0

        self.txt_conn_type = forms.TextBox()
        self.txt_conn_type.Text = "room_port"

        self.txt_mate_type = forms.TextBox()
        self.txt_mate_type.Text = "conn_port"

        # Face checkboxes — default +X/-X/+Y/-Y on, +Z/-Z off
        def _chk(label, checked=True):
            w = forms.CheckBox()
            w.Text    = label
            w.Checked = checked
            return w

        self.chk_px = _chk("+X", True)
        self.chk_nx = _chk("-X", True)
        self.chk_py = _chk("+Y", True)
        self.chk_ny = _chk("-Y", True)
        self.chk_pz = _chk("+Z", False)
        self.chk_nz = _chk("-Z", False)

        self.btn_add_pts    = _button("Add Connector Points")
        self.btn_remove_pts = _button("Remove Connector Points")
        self.btn_add_pts.Click    += self.on_add_connector_points
        self.btn_remove_pts.Click += self.on_remove_connector_points

        # ── Layout ─────────────────────────────────────────────────────────
        layout = forms.DynamicLayout()
        layout.Padding = drawing.Padding(10)
        layout.Spacing = drawing.Size(6, 6)

        # -- Grow Fill section --
        layout.AddRow(_label("Room block:"),      self.dd_room)
        layout.AddRow(_label("Connector block:"), self.dd_connector)
        layout.AddRow(_label("Max rooms:"),       self.num_max)
        layout.AddRow(self.btn_pick,               self.lbl_target)
        layout.AddRow(self.btn_refresh,            self.btn_clear, self.btn_run)
        layout.AddRow(_label("Status:"),           self.lbl_status)

        # -- Divider --
        div = forms.Label()
        div.Text = " "
        layout.AddRow(div)
        layout.AddRow(_label("─── Auto-Setup Connector Points ───────────────"))

        # -- Setup section --
        layout.AddRow(_label("Block to set up:"),  self.dd_setup)
        layout.AddRow(_label("conn_type:"),        self.txt_conn_type)
        layout.AddRow(_label("mate_type:"),        self.txt_mate_type)

        # Face toggle row
        faces_layout = forms.DynamicLayout()
        faces_layout.Spacing = drawing.Size(4, 0)
        faces_layout.AddRow(
            self.chk_px, self.chk_nx,
            self.chk_py, self.chk_ny,
            self.chk_pz, self.chk_nz,
        )
        layout.AddRow(_label("Faces:"), faces_layout)
        layout.AddRow(self.btn_remove_pts, None, self.btn_add_pts)

        # Wrap in Scrollable so nothing is clipped on small screens
        scroll = forms.Scrollable()
        scroll.Content             = layout
        scroll.ExpandContentWidth  = True
        scroll.ExpandContentHeight = False

        self.Content = scroll

    # ── Handlers ───────────────────────────────────────────────────────────

    def on_selection_changed(self, sender, e):
        """Show connector status of both selected blocks in the status bar."""
        room_name = self.dd_room.SelectedValue
        cb_name   = self.dd_connector.SelectedValue
        parts = []
        if room_name:
            parts.append(diagnose_block(room_name))
        if cb_name and cb_name != room_name:
            parts.append(diagnose_block(cb_name))
        if parts:
            self.lbl_status.Text = " | ".join(parts)

    def on_refresh(self, sender, e):
        blocks = list_all_blocks()
        self.dd_room.DataStore      = blocks
        self.dd_connector.DataStore = blocks
        self.dd_setup.DataStore     = blocks
        if blocks:
            self.dd_room.SelectedIndex      = 0
            self.dd_connector.SelectedIndex = min(1, len(blocks) - 1)
            self.dd_setup.SelectedIndex     = 0
        self.lbl_status.Text = "Found {} block(s) total.".format(len(blocks))

    def on_add_connector_points(self, sender, e):
        block_name = self.dd_setup.SelectedValue
        if not block_name:
            self.lbl_status.Text = "Choose a block to set up."
            return

        conn_type = (self.txt_conn_type.Text or "").strip()
        mate_type = (self.txt_mate_type.Text or "").strip()
        if not conn_type or not mate_type:
            self.lbl_status.Text = "Enter conn_type and mate_type."
            return

        face_keys = []
        for chk, key in [
            (self.chk_px, "+X"), (self.chk_nx, "-X"),
            (self.chk_py, "+Y"), (self.chk_ny, "-Y"),
            (self.chk_pz, "+Z"), (self.chk_nz, "-Z"),
        ]:
            if chk.Checked:
                face_keys.append(key)

        if not face_keys:
            self.lbl_status.Text = "Select at least one face."
            return

        ok, msg = add_connector_points_to_block(block_name, face_keys, conn_type, mate_type)
        self.lbl_status.Text = msg

    def on_remove_connector_points(self, sender, e):
        block_name = self.dd_setup.SelectedValue
        if not block_name:
            self.lbl_status.Text = "Choose a block to clear."
            return
        ok, msg = remove_connector_points_from_block(block_name)
        self.lbl_status.Text = msg

    def on_pick(self, sender, e):
        def pick_action(s, evt):
            kind, geom = pick_target_geometry()
            if not kind or geom is None:
                self.lbl_status.Text = "No valid geometry selected."
                return
            self.target_kind     = kind
            self.target_geom     = geom
            self.lbl_target.Text = kind.upper()
            self.lbl_status.Text = "Fill geometry set ({}).".format(kind)
        EtoExtensions.PushPickButton(self, pick_action)

    def on_clear(self, sender, e):
        all_ids = self._last_rooms + self._last_cbs
        removed = 0
        for guid in all_ids:
            if sc.doc.Objects.Delete(guid, True):
                removed += 1
        self._last_rooms = []
        self._last_cbs   = []
        sc.doc.Views.Redraw()
        self.lbl_status.Text = "Cleared {} object(s).".format(removed)

    def on_run(self, sender, e):
        room_name = self.dd_room.SelectedValue
        cb_name   = self.dd_connector.SelectedValue

        if not room_name:
            self.lbl_status.Text = "Choose a room block."
            return
        if not cb_name:
            self.lbl_status.Text = "Choose a connector block."
            return
        if room_name == cb_name:
            self.lbl_status.Text = "Room and connector block must be different."
            return
        if self.target_geom is None:
            self.lbl_status.Text = "Pick fill geometry first."
            return

        max_rooms = int(self.num_max.Value or 100)
        self.lbl_status.Text = "Growing — please wait..."

        ok, msg, room_xforms, cb_xforms = grow_fill(
            room_name, cb_name,
            self.target_kind, self.target_geom,
            max_rooms,
        )

        if not ok:
            self.lbl_status.Text = msg
            return

        rooms, cbs = insert_results(room_name, cb_name, room_xforms, cb_xforms)
        self._last_rooms = rooms
        self._last_cbs   = cbs
        self.lbl_status.Text = msg


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY
# ─────────────────────────────────────────────────────────────────────────────

def main():
    dlg    = GrowFillDialog()
    parent = RhinoEtoApp.MainWindowForDocument(sc.doc)
    EtoExtensions.ShowSemiModal(dlg, sc.doc, parent)

if __name__ == "__main__":
    main()