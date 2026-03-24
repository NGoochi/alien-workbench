# ALGORITHMS.md
### Generative Design Systems Reference

This is a living document. Update it as new algorithms are implemented or existing ones evolve. Each system is described in terms of what it does, its Alien header, a skeleton implementation, and how it chains with other systems.

---

## 1. Perlin Noise

### What it does
Generates smooth, continuous random variation across a field. Values transition gradually from point to point, creating organic-looking gradients rather than harsh random noise. Use it for any design parameter that should vary smoothly across space.

### Alien header
```python
#! python 3
# NODE_INPUTS: points:list[Point3d], frequency:float, amplitude:float, octaves:int, seed:int
# NODE_OUTPUTS: values, colored_points
```

### Skeleton
```python
import Rhino.Geometry as rg
import math

# Defaults
if not points: points = []
if frequency is None: frequency = 0.1
if amplitude is None: amplitude = 1.0
if octaves is None: octaves = 3
if seed is None: seed = 0

def simple_noise_3d(x, y, z, seed_val):
    """Minimal noise function — replace with a proper Perlin implementation or import a library."""
    n = int(x * 1000 + y * 57000 + z * 131000 + seed_val * 7919) & 0x7fffffff
    n = (n >> 13) ^ n
    n = (n * (n * n * 60493 + 19990303) + 1376312589) & 0x7fffffff
    return (n / 1073741824.0) - 1.0

def fbm(x, y, z, octaves, frequency, seed_val):
    """Fractional Brownian Motion — layered noise."""
    value = 0.0
    amp = 1.0
    freq = frequency
    for _ in range(octaves):
        value += amp * simple_noise_3d(x * freq, y * freq, z * freq, seed_val)
        amp *= 0.5
        freq *= 2.0
    return value

values = []
colored_points = []
for pt in points:
    v = fbm(pt.X, pt.Y, pt.Z, octaves, frequency, seed) * amplitude
    values.append(v)
    # Optional: move points by noise value for preview
    colored_points.append(rg.Point3d(pt.X, pt.Y, pt.Z + v))
```

### Chains with
- **Voxel Grid** → sample noise at voxel centers to drive activation/culling
- **Attractors** → modulate attractor strength with noise for organic falloff
- **Any geometry** → use `values` output to drive scale, offset, rotation, density

---

## 2. Attractors

### What it does
Creates distance-based influence fields from point, curve, or surface attractors. Geometry near the attractor is affected strongly; geometry far away is affected weakly. The falloff curve (linear, exponential, smoothstep) controls the transition.

### Alien header
```python
#! python 3
# NODE_INPUTS: sample_points:list[Point3d], attractor_points:list[Point3d], radius:float, falloff:str, strength:float
# NODE_OUTPUTS: values, vectors
```

### Skeleton
```python
import Rhino.Geometry as rg
import math

if not sample_points: sample_points = []
if not attractor_points: attractor_points = []
if radius is None or radius <= 0: radius = 50.0
if strength is None: strength = 1.0
if falloff is None: falloff = "linear"

def apply_falloff(t, mode):
    """t is normalised distance [0..1], returns influence [0..1]."""
    t = max(0.0, min(1.0, t))
    if mode == "linear": return 1.0 - t
    if mode == "exponential": return math.exp(-3.0 * t)
    if mode == "inverse": return 1.0 / (1.0 + t * 5.0)
    if mode == "smoothstep": return 1.0 - (3*t*t - 2*t*t*t)
    return 1.0 - t

values = []
vectors = []

for pt in sample_points:
    max_influence = 0.0
    pull_vector = rg.Vector3d(0, 0, 0)
    
    for attr in attractor_points:
        dist = pt.DistanceTo(attr)
        if dist > radius:
            continue
        t = dist / radius
        influence = apply_falloff(t, falloff) * strength
        if influence > max_influence:
            max_influence = influence
        direction = rg.Vector3d(attr) - rg.Vector3d(pt)
        if direction.Length > 0.001:
            direction.Unitize()
        pull_vector += direction * influence
    
    values.append(max_influence)
    vectors.append(pull_vector)
```

### Chains with
- **Any geometry input** → `values` list drives scaling, culling, density
- **Boids** → attractor fields as steering targets
- **Voxel Grid** → attractor proximity drives voxel activation

---

## 3. Boids / Multi-Agent Simulation

### What it does
Simulates flocking behaviour using Reynolds' rules: separation, alignment, cohesion. Agents move through 3D space, generating path curves that can be used as reference geometry for structural member placement, circulation routes, or spatial organisation.

### Alien header
```python
#! python 3
# NODE_INPUTS: bounds:Brep, agent_count:int, iterations:int, separation:float, alignment:float, cohesion:float, max_speed:float, seed:int
# NODE_OUTPUTS: trails, final_positions, log
```

### Skeleton
```python
import Rhino.Geometry as rg
import random
import math

if agent_count is None or agent_count < 1: agent_count = 20
if iterations is None or iterations < 1: iterations = 100
if separation is None: separation = 2.0
if alignment is None: alignment = 1.0
if cohesion is None: cohesion = 1.0
if max_speed is None: max_speed = 1.0
if seed is not None: random.seed(seed)

# Get bounding box for spawn area
if bounds:
    bb = bounds.GetBoundingBox(True)
else:
    bb = rg.BoundingBox(rg.Point3d(0,0,0), rg.Point3d(50,50,50))

# Initialise agents
positions = []
velocities = []
histories = []

for _ in range(agent_count):
    x = random.uniform(bb.Min.X, bb.Max.X)
    y = random.uniform(bb.Min.Y, bb.Max.Y)
    z = random.uniform(bb.Min.Z, bb.Max.Z)
    positions.append(rg.Point3d(x, y, z))
    velocities.append(rg.Vector3d(random.uniform(-1,1), random.uniform(-1,1), random.uniform(-1,1)))
    histories.append([rg.Point3d(x, y, z)])

# Simulation loop
for _ in range(iterations):
    for i in range(agent_count):
        sep = rg.Vector3d(0,0,0)
        ali = rg.Vector3d(0,0,0)
        coh = rg.Vector3d(0,0,0)
        neighbours = 0
        
        for j in range(agent_count):
            if i == j: continue
            dist = positions[i].DistanceTo(positions[j])
            if dist < 10.0:  # neighbour radius
                neighbours += 1
                # Separation
                if dist < 3.0 and dist > 0.001:
                    diff = rg.Vector3d(positions[i]) - rg.Vector3d(positions[j])
                    diff.Unitize()
                    sep += diff / dist
                # Alignment
                ali += velocities[j]
                # Cohesion
                coh += rg.Vector3d(positions[j])
        
        if neighbours > 0:
            ali /= neighbours
            coh = coh / neighbours - rg.Vector3d(positions[i])
        
        steer = sep * separation + ali * alignment + coh * cohesion
        velocities[i] += steer
        if velocities[i].Length > max_speed:
            velocities[i].Unitize()
            velocities[i] *= max_speed
        
        new_pos = positions[i] + velocities[i]
        # Clamp to bounds
        new_pos.X = max(bb.Min.X, min(bb.Max.X, new_pos.X))
        new_pos.Y = max(bb.Min.Y, min(bb.Max.Y, new_pos.Y))
        new_pos.Z = max(bb.Min.Z, min(bb.Max.Z, new_pos.Z))
        positions[i] = new_pos
        histories[i].append(rg.Point3d(new_pos))

# Build output curves from histories
trails = []
for h in histories:
    if len(h) > 1:
        polyline = rg.Polyline(h)
        trails.append(polyline.ToNurbsCurve())

final_positions = list(positions)
log = f"{agent_count} agents, {iterations} iterations, {len(trails)} trails"
```

### Chains with
- **Attractors** → attractor points as steering targets (add attractor force to the steer vector)
- **Voxel Grid** → trail curves drive voxel activation (voxels near trails are filled)
- **Component placement** → trails as reference curves for timber member distribution
- **Perlin Noise** → noise field as environmental force on agent movement

---

## 4. Voxel Grid

### What it does
Discretises a 3D volume into a regular grid of cubic cells. Each voxel can be active/inactive and carry metadata (density, program zone, material). The grid serves as the spatial scaffolding for building massing, program zoning, and discrete element placement.

### Alien header
```python
#! python 3
# NODE_INPUTS: bounds:Brep, x_count:int, y_count:int, z_count:int, cell_size:float
# NODE_OUTPUTS: centers, boxes, active_indices, count
```

### Skeleton
```python
import Rhino.Geometry as rg

if x_count is None or x_count < 1: x_count = 8
if y_count is None or y_count < 1: y_count = 12
if z_count is None or z_count < 1: z_count = 12
if cell_size is None or cell_size <= 0: cell_size = 5.0

# Origin from bounds or default
if bounds:
    bb = bounds.GetBoundingBox(True)
    origin = bb.Min
else:
    origin = rg.Point3d(0, 0, 0)

centers = []
boxes = []
active_indices = []
idx = 0

for z in range(z_count):
    for y in range(y_count):
        for x in range(x_count):
            cx = origin.X + (x + 0.5) * cell_size
            cy = origin.Y + (y + 0.5) * cell_size
            cz = origin.Z + (z + 0.5) * cell_size
            center = rg.Point3d(cx, cy, cz)
            
            # Activation test — default all active, override with field data
            active = True
            
            if active:
                centers.append(center)
                half = cell_size / 2.0
                box_corners = rg.BoundingBox(
                    rg.Point3d(cx - half, cy - half, cz - half),
                    rg.Point3d(cx + half, cy + half, cz + half)
                )
                boxes.append(rg.Brep.CreateFromBox(box_corners))
                active_indices.append(idx)
            idx += 1

count = len(centers)
```

### Chains with
- **Boids** → trail proximity activates/deactivates voxels
- **Attractors** → attractor field values drive voxel density or program assignment
- **Perlin Noise** → noise values modulate voxel activation threshold
- **Component placement** → voxel centers as placement points for discrete elements

---

## 5. Pathfinding / Agent Navigation

### What it does
Agents spawn at defined points within a volume and navigate to destination points. An urgency/efficiency parameter controls whether paths are direct (structural-looking) or wandering (space-filling, organic). The resulting path curves serve as reference geometry for structural member placement.

### Alien header
```python
#! python 3
# NODE_INPUTS: spawn_points:list[Point3d], dest_points:list[Point3d], bounds:Brep, urgency:float, resolution:int
# NODE_OUTPUTS: paths, log
```

### Key parameters
- `urgency` [0.0 → 1.0]: 0.0 = maximum wandering (longest path, space-filling), 1.0 = direct path (shortest, structural)
- `resolution`: number of intermediate steps

### Chains with
- **Voxel Grid** → paths navigate through active voxels only
- **Boids** → paths influenced by flocking behaviour
- **Component placement** → paths as curves for member distribution

---

## 6. Component Placement / Aggregation

### What it does
Places discrete elements (timber members, blocks, panels) along reference curves or at grid points. Element size, rotation, and density can be modulated by field values from other systems.

### Alien header
```python
#! python 3
# NODE_INPUTS: curves:list[Curve], element_length:float, element_section:float, density_values:list[float], seed:int
# NODE_OUTPUTS: members, joints, log
```

### Chains with
- **Boids/Pathfinding** → curves as placement paths
- **Voxel Grid** → voxel centers as placement points
- **Attractors/Noise** → density values modulate spacing and scale
- **Joint system** → intersection detection at shared grid points

---

## Adding New Systems

When implementing a new algorithm:
1. Add a section to this file following the same format
2. Include the Alien header, skeleton implementation, and chaining notes
3. Add example scripts to `scripts/` or `references/`

---

*End of ALGORITHMS.md. Last updated: 2026-03-15.*
