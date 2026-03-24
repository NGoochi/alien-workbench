#! python 3
# NODE_INPUTS: start_simulation:bool, bake_trails:bool, num_agents:int, sim_steps:int, trail_length:int, bounding_breps:list[Brep], bounding_curves:list[Curve], speed:float, efficiency:float, variance:float, cohesion:float, separation:float, alignment:float, jump_radius:float, jump_chance:float, spawn_points:list[Point3d], spawn_breps:list[Brep], despawn_points:list[Point3d], despawn_breps:list[Brep], seed:int
# NODE_OUTPUTS: trails, final_positions, log
#
# Boids simulation with multi-geometry bounding, efficiency-driven pathfinding,
# behavioural variance, and fixed jump mechanics.
# Priority: curve-constrained + brep-bounded. Surface support stubbed for later.

import Rhino
import Rhino.Geometry as rg
import System.Drawing
import random
import math

# ─── GH TYPE UNWRAPPING ──────────────────────────────────────────────
# Grasshopper sometimes delivers GH_Point, GH_Brep, etc. instead of
# the underlying RhinoCommon objects. These helpers safely unwrap them.
def unwrap(obj):
    """Unwrap any GH wrapper to its .Value (RhinoCommon object)."""
    if obj is None:
        return None
    if hasattr(obj, 'Value'):
        return obj.Value
    return obj

def unwrap_list(lst):
    """Unwrap a list, filtering out None values."""
    if not lst:
        return []
    return [v for v in (unwrap(item) for item in lst) if v is not None]

# ─── DEFENSIVE DEFAULTS ──────────────────────────────────────────────
if start_simulation is None: start_simulation = False
if bake_trails is None: bake_trails = False
if num_agents is None or num_agents < 1: num_agents = 30
if sim_steps is None or sim_steps < 1: sim_steps = 200
if trail_length is None or trail_length < 2: trail_length = 50

if not bounding_breps: bounding_breps = []
if not bounding_curves: bounding_curves = []

if speed is None or speed <= 0: speed = 2.0
if efficiency is None: efficiency = 0.5  # 0=wander, 1=direct
efficiency = max(0.0, min(1.0, efficiency))
if variance is None: variance = 0.0     # 0=identical, 1=wide spread
variance = max(0.0, min(1.0, variance))
if cohesion is None: cohesion = 1.0
if separation is None: separation = 1.8
if alignment is None: alignment = 0.5
if jump_radius is None or jump_radius <= 0: jump_radius = 5.0
if jump_chance is None: jump_chance = 0.05

if not spawn_points: spawn_points = []
if not spawn_breps: spawn_breps = []
if not despawn_points: despawn_points = []
if not despawn_breps: despawn_breps = []

# Unwrap all geometry lists (fixes GH_Point / GH_Brep wrapper issues)
bounding_breps = unwrap_list(bounding_breps)
bounding_curves = unwrap_list(bounding_curves)
spawn_points = unwrap_list(spawn_points)
spawn_breps = unwrap_list(spawn_breps)
despawn_points = unwrap_list(despawn_points)
despawn_breps = unwrap_list(despawn_breps)

if seed is not None: random.seed(seed)

# ─── VALIDATE CURVES ─────────────────────────────────────────────────
valid_curves = []
for c in bounding_curves:
    if isinstance(c, rg.Curve) and c.IsValid:
        valid_curves.append(c)

# Operating mode
MODE_FREEFORM = 0      # Free 3D movement within breps
MODE_CURVE = 1         # Constrained to curve network
MODE_HYBRID = 2        # Curves within brep bounds

if valid_curves and bounding_breps:
    op_mode = MODE_HYBRID
elif valid_curves:
    op_mode = MODE_CURVE
elif bounding_breps:
    op_mode = MODE_FREEFORM
else:
    # Fallback: create a default circle so it doesn't crash
    valid_curves.append(rg.Circle(rg.Plane.WorldXY, 20000.0).ToNurbsCurve())
    op_mode = MODE_CURVE

# Combined bounding box for spawn fallback
all_bb = rg.BoundingBox.Empty
for brep in bounding_breps:
    all_bb.Union(brep.GetBoundingBox(True))
for crv in valid_curves:
    all_bb.Union(crv.GetBoundingBox(True))


# ─── HELPER: Random point in brep ────────────────────────────────────
def random_point_in_brep(brep, max_attempts=50):
    """Get a random point inside a closed brep."""
    bb = brep.GetBoundingBox(True)
    for _ in range(max_attempts):
        pt = rg.Point3d(
            random.uniform(bb.Min.X, bb.Max.X),
            random.uniform(bb.Min.Y, bb.Max.Y),
            random.uniform(bb.Min.Z, bb.Max.Z)
        )
        if brep.IsPointInside(pt, 0.1, False):
            return pt
    return bb.Center  # fallback


# ─── HELPER: Closest point on any despawn geometry ────────────────────
def closest_despawn_point(pos):
    """Find the closest point across all despawn geometry."""
    best_pt = None
    best_dist = float('inf')

    for dp in despawn_points:
        d = pos.DistanceTo(dp)
        if d < best_dist:
            best_dist = d
            best_pt = dp

    for db in despawn_breps:
        cp = db.ClosestPoint(pos)
        if cp is not None:
            d = pos.DistanceTo(cp)
            if d < best_dist:
                best_dist = d
                best_pt = cp

    return best_pt, best_dist


# ─── BOID CLASS ──────────────────────────────────────────────────────
class Boid:
    def __init__(self, position, curve_idx, direction, agent_variance):
        self.Position = rg.Point3d(position)
        self.CurrentCurveIndex = curve_idx
        self.Direction = direction  # 1.0 or -1.0 along curve
        self.Velocity = rg.Vector3d(0, 0, 0)
        self.Acceleration = rg.Vector3d(0, 0, 0)
        self.Trail = [rg.Point3d(position)]
        self.Alive = True

        # Per-agent variance
        v = agent_variance
        self.MaxSpeed = speed * (1.0 + random.uniform(-v, v) * 0.5)
        self.MaxForce = 0.2 * (1.0 + random.uniform(-v, v) * 0.3)
        self.Efficiency = max(0.0, min(1.0, efficiency + random.uniform(-v, v) * 0.3))
        self.CohesionWeight = cohesion * (1.0 + random.uniform(-v, v) * 0.3)
        self.SeparationWeight = separation * (1.0 + random.uniform(-v, v) * 0.3)
        self.AlignmentWeight = alignment * (1.0 + random.uniform(-v, v) * 0.3)

        self.JumpCooldown = random.randint(0, 20)  # desync from start
        self.WanderAngle = random.uniform(0, math.pi * 2)

    def apply_force(self, force):
        self.Acceleration += force

    def steer_toward(self, target):
        """Reynolds-style steering toward a target point."""
        desired = rg.Vector3d(target - self.Position)
        if desired.Length < 0.001:
            return rg.Vector3d(0, 0, 0)
        desired.Unitize()
        desired *= self.MaxSpeed
        steer = desired - self.Velocity
        if steer.Length > self.MaxForce:
            steer.Unitize()
            steer *= self.MaxForce
        return steer

    def update(self, t_length):
        self.Velocity += self.Acceleration
        if self.Velocity.Length > self.MaxSpeed:
            self.Velocity.Unitize()
            self.Velocity *= self.MaxSpeed

        self.Position += self.Velocity
        self.Trail.append(rg.Point3d(self.Position))
        if len(self.Trail) > t_length:
            self.Trail.pop(0)

        self.Acceleration = rg.Vector3d(0, 0, 0)
        if self.JumpCooldown > 0:
            self.JumpCooldown -= 1

    def compute_curve_forces(self, flock, curves, breps):
        """Curve-constrained mode: follow curves, jump between them."""
        if not curves:
            return

        current_curve = curves[self.CurrentCurveIndex]

        # 1. Find nearest point on current curve
        rc, t = current_curve.ClosestPoint(self.Position)
        if not rc:
            return
        closest_pt = current_curve.PointAt(t)

        # 2. Jump mechanic (FIXED: per-agent randomised, distance-weighted)
        if self.JumpCooldown == 0 and jump_chance > 0.0 and random.random() < jump_chance:
            candidates = []
            for idx, other_curve in enumerate(curves):
                if idx == self.CurrentCurveIndex:
                    continue
                rc_o, t_o = other_curve.ClosestPoint(self.Position)
                if rc_o:
                    dist = self.Position.DistanceTo(other_curve.PointAt(t_o))
                    if dist < jump_radius:
                        # Weight by inverse distance + random fuzz
                        weight = (jump_radius - dist) / jump_radius
                        weight *= random.uniform(0.3, 1.0)  # fuzz prevents herding
                        candidates.append((idx, t_o, other_curve, weight))

            if candidates:
                # Weighted random selection (not just best)
                total_w = sum(c[3] for c in candidates)
                if total_w > 0:
                    r = random.uniform(0, total_w)
                    cumul = 0
                    chosen = candidates[0]
                    for cand in candidates:
                        cumul += cand[3]
                        if cumul >= r:
                            chosen = cand
                            break

                    self.CurrentCurveIndex = chosen[0]
                    self.JumpCooldown = random.randint(10, 40)  # wide range desync
                    current_curve = chosen[2]
                    rc, t = current_curve.ClosestPoint(self.Position)
                    if rc:
                        closest_pt = current_curve.PointAt(t)

                    # Pick direction based on efficiency + destination
                    despawn_pt, _ = closest_despawn_point(self.Position)
                    if despawn_pt is not None and self.Efficiency > 0.3:
                        tan = current_curve.TangentAt(t)
                        if tan.IsValid:
                            test_fwd = closest_pt + tan
                            test_bck = closest_pt - tan
                            if test_fwd.DistanceTo(despawn_pt) < test_bck.DistanceTo(despawn_pt):
                                self.Direction = 1.0
                            else:
                                self.Direction = -1.0
                    else:
                        self.Direction = 1.0 if random.random() > 0.5 else -1.0

        # 3. Path following with efficiency
        look_ahead = self.MaxSpeed * 3.0
        tangent = current_curve.TangentAt(t)
        if not tangent.IsValid:
            tangent = rg.Vector3d.XAxis

        target_pt = closest_pt + (tangent * look_ahead * self.Direction)

        # End-of-curve handling
        if not current_curve.IsClosed:
            if t >= current_curve.Domain.Max - 0.05 and self.Direction == 1.0:
                self.Direction = -1.0
            elif t <= current_curve.Domain.Min + 0.05 and self.Direction == -1.0:
                self.Direction = 1.0

        # Destination seeking (weighted by efficiency)
        despawn_pt, despawn_dist = closest_despawn_point(self.Position)
        if despawn_pt is not None:
            dest_steer = self.steer_toward(despawn_pt)
            self.apply_force(dest_steer * self.Efficiency * 1.5)

            # Check despawn arrival
            if despawn_dist < self.MaxSpeed * 2:
                self.Alive = False
                return

        # Path following force (stronger when efficiency is low = wander along curve)
        path_steer = self.steer_toward(target_pt)
        path_weight = 2.0 - self.Efficiency * 1.0  # less path lock when efficient (seeking dest)
        self.apply_force(path_steer * path_weight)

        # Wander force (inversely proportional to efficiency)
        wander_strength = (1.0 - self.Efficiency) * 0.5
        if wander_strength > 0.01:
            self.WanderAngle += random.uniform(-0.5, 0.5)
            wander_vec = rg.Vector3d(
                math.cos(self.WanderAngle) * wander_strength,
                math.sin(self.WanderAngle) * wander_strength,
                random.uniform(-0.1, 0.1) * wander_strength
            )
            self.apply_force(wander_vec)

        # Pull back to curve if drifting
        drift_dist = self.Position.DistanceTo(closest_pt)
        if drift_dist > self.MaxSpeed * 2:
            pull = rg.Vector3d(closest_pt - self.Position)
            pull.Unitize()
            self.apply_force(pull * (drift_dist * 0.15))

        # Brep boundary enforcement (hybrid mode)
        if breps:
            inside_any = False
            for brep in breps:
                if brep.IsPointInside(self.Position, 0.1, False):
                    inside_any = True
                    break
            if not inside_any:
                # Steer back toward nearest brep interior
                for brep in breps:
                    cp = brep.ClosestPoint(self.Position)
                    if cp is not None:
                        self.apply_force(self.steer_toward(cp) * 3.0)
                        break

        # 4. Flocking: separation, alignment, cohesion
        self._apply_flocking(flock)

    def compute_freeform_forces(self, flock, breps):
        """Freeform 3D mode: boids move freely within brep bounds."""
        # Destination seeking
        despawn_pt, despawn_dist = closest_despawn_point(self.Position)
        if despawn_pt is not None:
            dest_steer = self.steer_toward(despawn_pt)
            self.apply_force(dest_steer * self.Efficiency * 2.0)

            if despawn_dist < self.MaxSpeed * 2:
                self.Alive = False
                return

        # Wander
        wander_strength = (1.0 - self.Efficiency) * 1.0
        self.WanderAngle += random.uniform(-0.5, 0.5)
        wander_vec = rg.Vector3d(
            math.cos(self.WanderAngle) * wander_strength,
            math.sin(self.WanderAngle) * wander_strength,
            random.uniform(-0.3, 0.3) * wander_strength
        )
        self.apply_force(wander_vec)

        # Brep containment
        if breps:
            inside_any = False
            for brep in breps:
                if brep.IsPointInside(self.Position, 0.1, False):
                    inside_any = True
                    break
            if not inside_any:
                for brep in breps:
                    cp = brep.ClosestPoint(self.Position)
                    if cp is not None:
                        self.apply_force(self.steer_toward(cp) * 5.0)
                        break

        # Flocking
        self._apply_flocking(flock)

    def _apply_flocking(self, flock):
        """Separation, alignment, cohesion."""
        sep_vec = rg.Vector3d(0, 0, 0)
        ali_vec = rg.Vector3d(0, 0, 0)
        coh_pt = rg.Point3d(0, 0, 0)
        neighbours = 0

        neighbour_radius = self.MaxSpeed * 10
        sep_radius = self.MaxSpeed * 4

        for other in flock:
            if other is self or not other.Alive:
                continue
            d = self.Position.DistanceTo(other.Position)
            if d > 0 and d < neighbour_radius:
                neighbours += 1
                coh_pt += rg.Point3d(other.Position)
                ali_vec += other.Velocity

                if d < sep_radius:
                    diff = rg.Vector3d(self.Position - other.Position)
                    if diff.Length > 0.001:
                        diff.Unitize()
                        diff /= max(d, 0.1)
                        sep_vec += diff

        if neighbours > 0:
            # Cohesion
            if self.CohesionWeight > 0:
                coh_pt /= neighbours
                coh_steer = self.steer_toward(coh_pt)
                self.apply_force(coh_steer * self.CohesionWeight)

            # Alignment
            if self.AlignmentWeight > 0:
                ali_vec /= neighbours
                if ali_vec.Length > 0.001:
                    ali_vec.Unitize()
                    ali_vec *= self.MaxSpeed
                    ali_steer = ali_vec - self.Velocity
                    if ali_steer.Length > self.MaxForce:
                        ali_steer.Unitize()
                        ali_steer *= self.MaxForce
                    self.apply_force(ali_steer * self.AlignmentWeight)

            # Separation
            if self.SeparationWeight > 0 and sep_vec.Length > 0:
                sep_vec.Unitize()
                sep_vec *= self.MaxSpeed
                sep_steer = sep_vec - self.Velocity
                if sep_steer.Length > self.MaxForce:
                    sep_steer.Unitize()
                    sep_steer *= self.MaxForce
                self.apply_force(sep_steer * self.SeparationWeight)


# ─── DISPLAY CONDUIT ─────────────────────────────────────────────────
class BoidConduit(Rhino.Display.DisplayConduit):
    def __init__(self, flock):
        super(BoidConduit, self).__init__()
        self.flock = flock
        self.trail_color = System.Drawing.Color.FromArgb(200, 80, 200, 255)
        self.agent_color = System.Drawing.Color.FromArgb(255, 255, 100, 100)
        self.dead_color = System.Drawing.Color.FromArgb(100, 100, 100, 100)

    def CalculateBoundingBox(self, e):
        if all_bb.IsValid:
            expanded = rg.BoundingBox(all_bb.Min, all_bb.Max)
            expanded.Inflate(all_bb.Diagonal.Length * 0.5)
            e.IncludeBoundingBox(expanded)
        else:
            e.IncludeBoundingBox(rg.BoundingBox(-100000, -100000, -100000, 100000, 100000, 100000))

    def DrawForeground(self, e):
        for b in self.flock:
            if b.Alive:
                e.Display.DrawPoint(b.Position, Rhino.Display.PointStyle.X, 4, self.agent_color)
            else:
                e.Display.DrawPoint(b.Position, Rhino.Display.PointStyle.Circle, 3, self.dead_color)
            if len(b.Trail) > 1:
                e.Display.DrawPolyline(b.Trail, self.trail_color, 2)


# ─── SPAWN AGENTS ────────────────────────────────────────────────────
flock = []

for i in range(num_agents):
    # Determine spawn position
    spawn_pt = None

    # Priority: spawn_points > spawn_breps > random on curves/breps
    if spawn_points:
        base_pt = spawn_points[i % len(spawn_points)]
        # Small jitter to prevent exact overlap
        spawn_pt = rg.Point3d(
            base_pt.X + random.uniform(-1.0, 1.0),
            base_pt.Y + random.uniform(-1.0, 1.0),
            base_pt.Z + random.uniform(-1.0, 1.0)
        )
    elif spawn_breps:
        brep = spawn_breps[i % len(spawn_breps)]
        spawn_pt = random_point_in_brep(brep)
    elif valid_curves:
        crv = valid_curves[random.randint(0, len(valid_curves) - 1)]
        t_rand = random.uniform(crv.Domain.Min, crv.Domain.Max)
        spawn_pt = crv.PointAt(t_rand)
    elif bounding_breps:
        brep = bounding_breps[random.randint(0, len(bounding_breps) - 1)]
        spawn_pt = random_point_in_brep(brep)
    else:
        spawn_pt = rg.Point3d(random.uniform(-10000, 10000), random.uniform(-10000, 10000), 0)

    # Assign to nearest curve (for curve modes)
    curve_idx = 0
    if valid_curves:
        best_dist = float('inf')
        for idx, crv in enumerate(valid_curves):
            rc, t = crv.ClosestPoint(spawn_pt)
            if rc:
                d = spawn_pt.DistanceTo(crv.PointAt(t))
                if d < best_dist:
                    best_dist = d
                    curve_idx = idx

    # Initial direction (biased by despawn if available)
    dir_val = 1.0 if random.random() > 0.5 else -1.0
    despawn_pt, _ = closest_despawn_point(spawn_pt)
    if despawn_pt is not None and valid_curves:
        crv = valid_curves[curve_idx]
        rc, t = crv.ClosestPoint(spawn_pt)
        if rc:
            tan = crv.TangentAt(t)
            if tan.IsValid:
                test_fwd = spawn_pt + tan
                test_bck = spawn_pt - tan
                if test_fwd.DistanceTo(despawn_pt) < test_bck.DistanceTo(despawn_pt):
                    dir_val = 1.0
                else:
                    dir_val = -1.0

    boid = Boid(spawn_pt, curve_idx, dir_val, variance)

    # Set initial velocity along curve tangent (smooth start)
    if valid_curves:
        crv = valid_curves[curve_idx]
        rc, t = crv.ClosestPoint(spawn_pt)
        if rc:
            tan = crv.TangentAt(t)
            if tan.IsValid:
                boid.Velocity = tan * boid.MaxSpeed * dir_val * 0.5

    flock.append(boid)


# ─── SIMULATION ──────────────────────────────────────────────────────
trails = []
final_positions = []

if start_simulation and sim_steps > 0:
    conduit = BoidConduit(flock)
    conduit.Enabled = True

    try:
        alive_count = num_agents
        for step in range(sim_steps):
            if alive_count == 0:
                break

            for b in flock:
                if not b.Alive:
                    continue
                if op_mode == MODE_CURVE:
                    b.compute_curve_forces(flock, valid_curves, [])
                elif op_mode == MODE_HYBRID:
                    b.compute_curve_forces(flock, valid_curves, bounding_breps)
                elif op_mode == MODE_FREEFORM:
                    b.compute_freeform_forces(flock, bounding_breps)

            alive_count = 0
            for b in flock:
                if b.Alive:
                    b.update(trail_length)
                    alive_count += 1

            Rhino.RhinoDoc.ActiveDoc.Views.Redraw()
            Rhino.RhinoApp.Wait()

    except Exception as e:
        Rhino.RhinoApp.WriteLine("Boid Simulation Error: {}".format(str(e)))
    finally:
        conduit.Enabled = False
        Rhino.RhinoDoc.ActiveDoc.Views.Redraw()

# ─── OUTPUT ──────────────────────────────────────────────────────────
for b in flock:
    if len(b.Trail) > 1:
        pl = rg.Polyline(b.Trail)
        crv = pl.ToNurbsCurve()
        trails.append(crv)

        if bake_trails:
            Rhino.RhinoDoc.ActiveDoc.Objects.AddCurve(crv)

    final_positions.append(rg.Point3d(b.Position))

if bake_trails:
    Rhino.RhinoDoc.ActiveDoc.Views.Redraw()

alive = sum(1 for b in flock if b.Alive)
dead = num_agents - alive
log = "Boids: {} agents | {} steps | Mode: {} | Alive: {} | Despawned: {} | Efficiency: {:.2f} | Variance: {:.2f}".format(
    num_agents, sim_steps,
    ["Freeform", "Curve", "Hybrid"][op_mode],
    alive, dead, efficiency, variance
)
