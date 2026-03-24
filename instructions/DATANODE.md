# DATANODE.md
### DataNode Component Reference

---

## What Is DataNode?

DataNode is a Grasshopper component that lets you define a **data schema** (named fields with types, min/max ranges, and decimal precision), create **N items** (rows) conforming to that schema, and **output each field as a list**. Think of it as a spreadsheet embedded in a GH component — one output wire per column, one list value per row.

It lives in the **Script > Alien** tab alongside Alien.

---

## When to Use DataNode vs Sliders

| Scenario | Use |
|---|---|
| 1 parameter, 1 value | GH Number Slider |
| 1 parameter, N values | Gene Pool or DataNode |
| N parameters, N values (same N) | **DataNode** ← this is the sweet spot |
| Dynamic script I/O | Alien |

**Primary use case:** Configuring per-item data for list-based Alien inputs. For example, defining per-level heights, offsets, and floor areas for a 14-story building — all from one node with one set of sliders.

---

## Terminology

| Term | Meaning |
|---|---|
| **Schema** | The structure definition — which fields exist, their types, and ranges |
| **Field** | A named column in the schema (e.g. `level_heights`, `floor_area_offsets`) |
| **Item** | A named row in the data table (e.g. `Level 1`, `Level 2`, ...) |
| **Override** | A wire input that replaces the DataNode's stored value for a field/item |
| **Parent override** | A wire input that replaces ALL items' values for a field |
| **ListName** | Custom display name for the node header (optional) |

---

## Naming Conventions

### Field Names
- Use `snake_case` (e.g. `level_heights`, `floor_area_offsets`)
- Match the downstream Alien input name exactly — the DataNode output name IS the field name
- Prefer plural nouns for list outputs: `heights` not `height`
- Keep short but descriptive: `z_offset` not `vertical_offset_from_ground_plane`

### Item Names
- Use `Title Case` with a numeric suffix: `Level 1`, `Room 3`, `Bay 12`
- The prefix should describe what the item represents
- Items are named via the `name_prefix` parameter in `add_datanode_items` MCP tool

### ListName
- Use for identifying the DataNode's purpose on canvas: `Levels`, `Rooms`, `Bays`
- Shows on the node header in place of "DataNode"
- Leave blank/empty to show default "DataNode"

---

## Schema Field Properties

| Property | Type | Description |
|---|---|---|
| `name` | string | Field name (becomes the output parameter name) |
| `type` | string | Type hint: `float`, `int`, `str`, `bool`, `Point3d`, `Vector3d`, `Plane`, `Line`, `Curve`, `Surface`, `Brep`, `Mesh`, `color`, `geometry` |
| `min` | number | Minimum slider value (numeric fields only) |
| `max` | number | Maximum slider value (numeric fields only) |
| `decimals` | int (0–5) | Decimal precision for display and rounding |
| `is_parent` | bool | If true, a wire override on this field replaces ALL items |

---

## Connecting DataNode to Alien

### The Pattern
```
DataNode (output: level_heights)  →  Alien (input: level_heights : float list[])
DataNode (output: z_offsets)      →  Alien (input: level_z_offsets : float list[])
```

### Script Header Example
```python
#! python 3
# NODE_INPUTS: boundary_brep:Brep, num_levels:int, level_heights:float list[], level_z_offsets:float list[], floor_area_offsets:float list[]
# NODE_OUTPUTS: output_geo, floors, level_volumes, log
```

**Key:** The Alien input must have `list[]` in the type hint to accept a list from DataNode.

### Data Flow
- DataNode outputs one list per field — each list has N values (one per item)
- Lists are plain `double`/`string` values (not GH_Number wrappers — unwrapping is handled automatically)
- The Alien receives these as Python lists: `level_heights = [5500.0, 4200.0, 4000.0, ...]`

---

## Wire Overrides

Overrides let you replace stored DataNode values with live GH wire data. Two types:

### Per-Item Override
- Replace a single item's value for a specific field
- Input name format: `{item_name} {field_name}` (e.g. `Level 1 level_heights`)
- Enabled via the editor checkbox or `set_datanode_schema` MCP tool

### Parent Override
- Replace ALL items' values for a field with one wire
- Input name: the field name itself (e.g. `level_heights`)
- Useful when you want to drive all values from a single upstream component
- Enabled by marking `is_parent: true` on the field

---

## MCP Tools

DataNode has 4 dedicated MCP tools. All require the DataNode component's GUID (get it from `get_canvas_info` — look for `type: "DataNodeComponent"`).

### `get_datanode_info`
**Purpose:** Get complete state — schema fields, all items with values, and active overrides.

**Parameters:**
- `component_id` (string, optional) — GUID of a specific DataNode. Omit for ALL DataNodes.

**Returns:**
```json
{
  "success": true,
  "node": {
    "guid": "08219622-...",
    "name": "DataNode",
    "field_count": 3,
    "item_count": 14,
    "fields": [
      { "name": "level_heights", "type": "float", "min": 3000.0, "max": 6000.0, "decimals": 0, "is_parent": false }
    ],
    "items": [
      { "index": 0, "name": "Level 1", "values": { "level_heights": 5500.0, "level_z_offset": 0.0 } }
    ],
    "overrides": []
  }
}
```

---

### `set_datanode_values`
**Purpose:** Set a value for a specific item and field. Changes are reflected immediately.

**Parameters:**
- `component_id` (string, required) — DataNode GUID
- `item` (string, required) — Item index (0-based, as string) or item name (e.g. `"Level 1"`)
- `field` (string, required) — Field name (e.g. `"level_heights"`)
- `value` (string, required) — Value to set (number or string, as string)

**Example:**
```
set_datanode_values(component_id="08219622-...", item="0", field="level_heights", value="5500")
set_datanode_values(component_id="08219622-...", item="Level 3", field="z_offset", value="300")
```

**Notes:**
- Values are clamped to the field's min/max range
- Numeric values are rounded to the field's decimal precision
- Each call triggers a recompute of the DataNode and downstream components

---

### `add_datanode_items`
**Purpose:** Batch-create new items with default values (midpoint of each field's range).

**Parameters:**
- `component_id` (string, required) — DataNode GUID
- `count` (string, required) — Number of items to create (as string)
- `name_prefix` (string, optional) — Prefix for auto-naming (e.g. `"Level"` → `Level 1`, `Level 2`, ...)

**Example:**
```
add_datanode_items(component_id="08219622-...", count="14", name_prefix="Level")
```

---

### `set_datanode_schema`
**Purpose:** Add, remove, or modify fields in the schema.

**Parameters:**
- `component_id` (string, required) — DataNode GUID
- `action` (string, required) — `"add"`, `"remove"`, or `"modify"`
- `field_name` (string, required) — Name of the field
- `type` (string, required for `add`) — Type hint (e.g. `"float"`, `"int"`, `"str"`)
- `min` (string, optional) — Minimum value
- `max` (string, optional) — Maximum value
- `decimals` (string, optional) — Decimal places (0–5)
- `is_parent` (string, optional) — `"true"` or `"false"`

**Examples:**
```
# Add a new field
set_datanode_schema(component_id="08219622-...", action="add", field_name="floor_area", type="float", min="0", max="1000", decimals="1")

# Modify an existing field's range
set_datanode_schema(component_id="08219622-...", action="modify", field_name="floor_area", min="0", max="5000")

# Remove a field
set_datanode_schema(component_id="08219622-...", action="remove", field_name="floor_area")
```

---

## Typical Agent Workflow

### Setting up a new DataNode for a levels script:

```
1. get_canvas_info                              → find the DataNode GUID
2. set_datanode_schema (action="add")           → define fields: level_heights, z_offsets, floor_area_offsets
3. add_datanode_items (count="14", prefix="Level") → create 14 levels
4. set_datanode_values (loop)                   → set per-level values
5. get_datanode_info                            → verify final state
6. get_component_outputs                        → verify output lists
```

### Modifying existing DataNode values:

```
1. get_datanode_info                            → see current schema and values
2. set_datanode_values (×N)                     → update specific items
3. get_component_outputs                        → verify downstream data
```

---

## Persistence

- DataNode schema and all item values are **saved automatically** with the `.gh` file via Grasshopper's serialisation
- **JSON export/import** is available from the component's right-click menu
- Schema is stored as a JSON blob in the `.gh` file under the key `"DataNodeSchema"`

---

## Editor (UI)

The DataNode has a visual editor accessible by:
1. Clicking "Edit Data" on the component face
2. Right-click → "Edit Data…"

The editor uses a **GridView** (table) for editing items. Double-click a cell to edit values. The editor is **non-modal** — the GH canvas remains interactive while it's open.

---

*End of DATANODE.md.*
