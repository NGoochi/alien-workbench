# MCP_WORKFLOW.md
### MCP Server Reference and Debug Protocol

---

## Server Details

- **URL:** `http://127.0.0.1:9876/mcp`
- **Transport:** Streamable HTTP
- **Auto-start:** The server boots when the first **Alien** (or legacy DataNode) component is placed on the GH canvas. A green dot on the MCP row confirms it's running.
- **Shared:** All components share one server instance (HTTP + MCP + WebSocket editor on the same port).
- **Browser editor:** `http://127.0.0.1:9876/editor/node/{instance_guid}` — also opened via the node's right-click **Edit Node…**
- **REST:** `GET /api/node/{guid}/state`, `POST /api/node/{guid}/values` (JSON body `{ "values": { "param": ... } }`)
- **Tools:** 15 total (canvas, Alien/script, DataNode, Rhino, **get_node_state**, **set_param_value**)

---

## Tool Reference

### Category 1: Canvas Inspection

#### `get_canvas_info`
**Purpose:** Get a complete map of every component on the GH canvas — names, types, GUIDs, positions, wire connections, and runtime status.

**Parameters:** None

**When to use:**
- First thing in a session, to understand what's on the canvas
- After placing or connecting components, to verify the graph
- To find the GUID of a component you need to inspect further

**Returns:**
```json
{
  "success": true,
  "documentName": "my_definition.gh",
  "componentCount": 12,
  "components": [
    {
      "id": "a1b2c3d4-...",
      "name": "Alien",
      "nickname": "Alien",
      "type": "AlienNodeComponent",
      "x": 450.0,
      "y": 200.0,
      "category": "Script",
      "subcategory": "Alien",
      "runtimeMessageLevel": "Blank",
      "isAlienNode": true,
      "inputs": [
        {
          "name": "script_path",
          "nickname": "script_path",
          "type": "Text",
          "sourceCount": 1,
          "sources": [
            {
              "sourceComponentId": "e5f6g7h8-...",
              "sourceParamName": "Panel"
            }
          ]
        },
        {
          "name": "origin",
          "nickname": "origin",
          "type": "Point",
          "sourceCount": 1,
          "sources": [...]
        }
      ],
      "outputs": [
        {
          "name": "points",
          "nickname": "points",
          "type": "Generic Data",
          "recipientCount": 1,
          "recipients": [...]
        }
      ]
    }
  ]
}
```

**Key fields:**
- `isAlienNode: true` — identifies Alien components vs native GH components
- `runtimeMessageLevel` — `"Blank"` (OK), `"Warning"` (orange), `"Error"` (red)
- `sources` / `recipients` — the wire connections, showing which components are connected to which

---

#### `get_component_outputs`
**Purpose:** Read the actual data values flowing through a component's output parameters.

**Parameters:**
- `component_id` (string, required) — GUID of the component

**When to use:**
- After a script runs successfully, to verify output values
- To check what data a native GH component is producing (for debugging wiring)
- To confirm that geometry objects are valid

**Returns:**
```json
{
  "success": true,
  "outputs": [
    {
      "name": "points",
      "nickname": "points",
      "type": "Generic Data",
      "dataCount": 10,
      "values": [
        "Point3d (0, 0, 0)",
        "Point3d (1, 0, 0)",
        "Point3d (2, 0, 0)"
      ],
      "truncated": false
    },
    {
      "name": "log",
      "nickname": "log",
      "type": "Generic Data",
      "dataCount": 1,
      "values": ["Generated 10 points at spacing 1.0"],
      "truncated": false
    }
  ]
}
```

**Notes:**
- Values are `.ToString()` representations — geometry appears as `"Point3d (x, y, z)"`, `"Curve (domain)"`, etc.
- Output is capped at 100 values per parameter. If `truncated: true`, there are more values than shown.
- Works on any component, not just Aliens.

---

### Category 2: Alien Inspection

#### `get_scriptnode_info`
**Purpose:** Deep-dive into a Alien's state — script path, parsed header, runtime messages, file watcher status, and wire connections.

**Parameters:**
- `component_id` (string, optional) — GUID of a specific Alien. Omit to get info for ALL Aliens on the canvas.

**When to use:**
- To check if a script loaded correctly
- To see the parsed header (confirms the parser read your `NODE_INPUTS` / `NODE_OUTPUTS` correctly)
- To read runtime error/warning messages
- Without arguments, to get an overview of all Aliens

**Returns (single node):**
```json
{
  "success": true,
  "node": {
    "id": "a1b2c3d4-...",
    "name": "Alien",
    "scriptPath": "C:\\projects\\my_script.py",
    "hasFileWatcher": true,
    "headerInputs": [
      { "Name": "origin", "TypeHint": "Point3d", "IsList": false },
      { "Name": "count", "TypeHint": "int", "IsList": false },
      { "Name": "spacing", "TypeHint": "float", "IsList": false }
    ],
    "headerOutputs": ["points", "log"],
    "runtimeMessageLevel": "Error",
    "runtimeMessages": [
      { "level": "Error", "message": "Python error: name 'rg' is not defined" }
    ],
    "inputs": [...],
    "outputs": [...]
  }
}
```

**Key fields:**
- `headerInputs` / `headerOutputs` — what the parser extracted from the file header. If these don't match what you wrote, the header has a syntax error.
- `runtimeMessages` — the actual error/warning text. This is the same info that appears in the GH component balloon tooltip.
- `runtimeMessageLevel` — `"Blank"` (OK), `"Warning"`, `"Error"`

---

#### `get_script_source`
**Purpose:** Read the full contents of the Python file a Alien is pointing at.

**Parameters:**
- `component_id` (string, required) — GUID of the Alien

**When to use:**
- To review what code is currently loaded
- To check for syntax issues before making targeted edits
- When the user says "look at my script" and you need to see it

**Returns:**
```json
{
  "success": true,
  "path": "C:\\projects\\my_script.py",
  "lineCount": 42,
  "content": "#! python 3\n# NODE_INPUTS: origin:Point3d...\nimport Rhino.Geometry as rg\n..."
}
```

---

#### `write_script_source`
**Purpose:** Write new content to the Python file. The Alien's FileSystemWatcher will detect the change and auto-reload — rebuilding parameters if the header changed, re-executing the script, and updating outputs.

**Parameters:**
- `component_id` (string, required) — GUID of the Alien
- `content` (string, required) — the full Python source code to write
- `confirm_overwrite` (boolean, optional, default `false`) — **must be `true`** if the file already exists on disk and is **non-empty**. Otherwise the tool returns `success: false` and `requires_confirm_overwrite: true` (prevents agents/tests from wiping scripts by accident).

**When to use:**
- To create or overwrite a script directly from the agent
- For rapid iteration without needing the user to save manually
- When fixing a bug — read source, fix, write back **with** `confirm_overwrite: true` when replacing an existing file

**Overwrite safety:**
- New files or **0-byte** existing files: write succeeds without `confirm_overwrite`.
- Non-empty existing file: first call fails with a clear error; call again with `confirm_overwrite: true` after `get_script_source`.
- Before overwriting a non-empty file, the plugin copies the old file to a timestamped backup next to it: `your_script.py.20260324-153045123.bak`.

**Returns (success):**
```json
{
  "success": true,
  "path": "C:\\projects\\my_script.py",
  "bytesWritten": 847,
  "backup_path": "C:\\projects\\my_script.py.20260324-153045123.bak",
  "message": "Previous contents saved to backup_path. Script written; Alien will auto-reload via FileSystemWatcher."
}
```
(`backup_path` is omitted or `null` when there was no non-empty prior file.)

**Returns (blocked — need confirm):**
```json
{
  "success": false,
  "error": "File exists and is not empty. Use get_script_source first. To replace it, call write_script_source again with confirm_overwrite: true.",
  "requires_confirm_overwrite": true,
  "path": "C:\\projects\\my_script.py",
  "existing_bytes": 1204
}
```

**Caution:** This writes the entire file. There is no merge or diff. Always `get_script_source` first, modify, then `write_script_source` with the complete file and `confirm_overwrite: true` when replacing.

---

#### `get_error_log`
**Purpose:** Read the `gh_errors.log` file that sits next to the Python script. Contains full tracebacks from the last execution failure.

**Parameters:**
- `component_id` (string, required) — GUID of the Alien

**When to use:**
- When `get_scriptnode_info` shows `runtimeMessageLevel: "Error"`
- When the runtime message is truncated and you need the full traceback
- First step in any debug cycle

**Returns (error exists):**
```json
{
  "success": true,
  "exists": true,
  "path": "C:\\projects\\gh_errors.log",
  "content": "Traceback (most recent call last):\n  File \"my_script.py\", line 15\n    result = curve.Offset(plane, dist)\nAttributeError: 'NoneType' object has no attribute 'Offset'\n"
}
```

**Returns (no errors):**
```json
{
  "success": true,
  "exists": false,
  "message": "No error log found (no errors have occurred)."
}
```

---

### Category 3: Alien Node State

#### `get_node_state`
**Purpose:** Get the full serialised state for an Alien node — manual parameter values, metadata hints, live mode status, pending changes flag, and script path.

**Parameters:**
- `component_id` (string, required) — GUID of the Alien node

**When to use:**
- To see current manual values for all parameters at once
- To check if the node has pending (unapplied) changes
- To read metadata hints that the script header declared
- To confirm what the browser editor is showing

**Returns:**
```json
{
  "success": true,
  "state": {
    "scriptPath": "/path/to/my_script.py",
    "liveMode": true,
    "hasPendingChanges": false,
    "manualValues": {
      "origin": { "X": 0, "Y": 0, "Z": 0 },
      "count": 10,
      "spacing": 5.0
    },
    "metadataHints": {
      "spacing": { "min": 0.5, "max": 50.0, "step": 0.1 }
    }
  }
}
```

---

#### `set_param_value`
**Purpose:** Set a manual parameter value on an Alien node by name, then trigger a Grasshopper recompute.

**Parameters:**
- `component_id` (string, required) — GUID of the Alien node
- `param_name` (string, required) — Input parameter name (must match a name from the script header)
- `value_json` (string, required) — Value as a number, string, or JSON for complex types (e.g., `"5.0"`, `"true"`, `"{\"X\":1,\"Y\":2,\"Z\":3}"`)

**When to use:**
- To set parameter values programmatically from the agent
- To adjust design parameters without the browser UI
- For batch parameter updates during automated workflows

**Returns:**
```json
{
  "success": true,
  "param": "spacing"
}
```

**Notes:**
- The value is parsed as JSON first; if that fails, it's treated as a raw string
- This triggers an immediate GH recompute — use `get_component_outputs` to verify the result
- The `script_path` parameter (index 0) cannot be set via this tool — it must be set manually in Grasshopper

---

### Category 4: Rhino Application

#### `get_rhino_command_history`
**Purpose:** Read the Rhino command history window. Catches `print()` output from Python scripts, Rhino warnings, plugin load messages, and anything else that goes to the command line.

**Parameters:** None

**When to use:**
- To check for Python `print()` output (which goes to Rhino, not GH)
- To see if the plugin loaded correctly
- To check for Rhino-level warnings that don't appear in GH
- After running a Rhino command via `run_rhino_command`

---

#### `clear_rhino_command_history`
**Purpose:** Clear the command history window. Use before a test to isolate that test's output.

**Parameters:** None

**When to use:**
- Before running a script or command when you want clean output
- When the history is full of noise from earlier operations

---

#### `run_rhino_command`
**Purpose:** Execute a Rhino command string as if typed into the command line.

**Parameters:**
- `command` (string, required) — the command to run, e.g. `"_Circle 0,0,0 10"` or `"_SelAll"` or `"-_Export \"C:/output.obj\""`

**When to use:**
- To execute Rhino commands that aren't available through Python scripting
- To test geometry operations directly
- To export files, change display modes, or perform viewport operations
- Prefix commands with `_` for language-independent execution

**Returns:**
```json
{
  "success": true,
  "commandRun": true
}
```

---

### Category 5: DataNode Management

> See `DATANODE.md` for full DataNode concepts, naming conventions, and workflows.

#### `get_datanode_info`
**Purpose:** Get complete state of a DataNode — schema fields (names, types, ranges), all items with their values, and active wire overrides.

**Parameters:**
- `component_id` (string, optional) — GUID of a specific DataNode. Omit to get info for ALL registered DataNodes.

**When to use:**
- To see the current schema (fields) and data (items/values)
- To check which overrides are enabled
- To verify values after setting them via `set_datanode_values`

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
      { "index": 0, "name": "Level 1", "values": { "level_heights": 5500.0 } }
    ],
    "overrides": []
  }
}
```

---

#### `set_datanode_values`
**Purpose:** Set the value for a specific item and field. Triggers a downstream recompute.

**Parameters:**
- `component_id` (string, required) — DataNode GUID
- `item` (string, required) — Item identifier: 0-based index as string (e.g. `"0"`) or item name (e.g. `"Level 1"`)
- `field` (string, required) — Field name (e.g. `"level_heights"`)
- `value` (string, required) — Value to set (as string, e.g. `"5500"`)

**When to use:**
- To set or update individual item values from the agent
- In a loop to configure all items programmatically
- To adjust design parameters without the visual editor

**Returns:**
```json
{ "success": true, "item": "Level 1", "field": "level_heights", "value": 5500.0 }
```

**Notes:**
- Values are clamped to the field's `[min, max]` range and rounded to `decimals` precision
- Each call triggers a GH solution recompute — batch calls are fine (GH coalesces rapid recomputes)

---

#### `add_datanode_items`
**Purpose:** Batch-create new items. Each item starts with default values (midpoint of each field's range).

**Parameters:**
- `component_id` (string, required) — DataNode GUID
- `count` (string, required) — Number of items to create (as string)
- `name_prefix` (string, optional) — Prefix for auto-naming (e.g. `"Level"` → `Level 1`, `Level 2`, ...)

**When to use:**
- When setting up a new DataNode from scratch
- To add more items to an existing DataNode

**Returns:**
```json
{ "success": true, "added": 14, "total_items": 14 }
```

---

#### `set_datanode_schema`
**Purpose:** Add, remove, or modify fields in the DataNode's schema.

**Parameters:**
- `component_id` (string, required) — DataNode GUID
- `action` (string, required) — `"add"`, `"remove"`, or `"modify"`
- `field_name` (string, required) — Name of the field
- `type` (string, required for `add`) — Type hint: `"float"`, `"int"`, `"str"`, `"bool"`, `"Point3d"`, etc.
- `min` (string, optional) — Minimum value for sliders
- `max` (string, optional) — Maximum value for sliders
- `decimals` (string, optional) — Decimal places (0–5)
- `is_parent` (string, optional) — `"true"` or `"false"` — if true, wire override replaces ALL items

**When to use:**
- To define the schema programmatically when creating a DataNode
- To add/remove fields during iteration
- To adjust ranges or decimal precision

**Example sequence:**
```
set_datanode_schema(action="add", field_name="level_heights", type="float", min="3000", max="6000", decimals="0")
set_datanode_schema(action="add", field_name="z_offset", type="float", min="0", max="1000", decimals="0")
add_datanode_items(count="14", name_prefix="Level")
set_datanode_values(item="0", field="level_heights", value="5500")  // repeat for each item
```

---

## The Debug Protocol

Follow this sequence when writing or fixing a Alien script:

### Step 1 — Understand the canvas
```
Call: get_canvas_info
→ Identify all Aliens and their GUIDs
→ Note wire connections and any existing errors
```

### Step 2 — Write or edit the script
```
Call: get_script_source (if editing existing)
→ Make changes
Call: write_script_source (to deploy)
→ OR: edit in external editor and save (FileSystemWatcher picks it up)
```

### Step 3 — Check status
```
Call: get_scriptnode_info (with the component GUID)
→ Check runtimeMessageLevel
```

### Step 4a — If Error
```
Call: get_error_log → read full traceback
Call: get_rhino_command_history → check for print output or Rhino-level errors
→ Fix the issue in the script
→ Return to Step 2
```

### Step 4b — If Warning
```
Call: get_scriptnode_info → read warning messages
→ Common warnings: missing inputs (safe to ignore if intentional), type mismatches
→ Fix or ignore as appropriate
```

### Step 4c — If OK (Blank)
```
Call: get_component_outputs → verify output values and counts
→ If outputs look wrong, review script logic
→ If outputs look correct, the script is working
```

### Step 5 — Verify wiring
```
Call: get_canvas_info → confirm wires are intact after edit
→ If wires dropped, check that you didn't rename a header parameter
```

### Step 6 — Clean up
```
Call: clear_rhino_command_history → clear noise for next iteration
```

---

## Tips for Agents

- **Always check status after writing.** The write → check → fix loop is the core workflow. Never assume a write was successful.
- **Use `get_error_log` before `get_scriptnode_info`** when debugging — the log has the full traceback, while the component message may be truncated.
- **`get_component_outputs` works on any component** — not just Aliens. Use it to check what data native GH components are producing, to understand what's flowing into your script's inputs.
- **`run_rhino_command` is powerful but dangerous** — it can modify the Rhino document. Use it for inspection commands (`_SelAll`, `_What`) and viewport operations, not for destructive geometry operations unless the user explicitly asks.
- **Command history is noisy** — always `clear_rhino_command_history` before a test if you need to isolate output.

---

*End of MCP_WORKFLOW.md.*
