# Alien — Grasshopper Scripting Workbench

A scripting workbench for Rhino 8 / Grasshopper that pairs a custom plugin (Alien node) with an MCP server, letting AI coding agents write, test, and iterate on Python scripts directly inside the Grasshopper environment.

Write Python scripts in your preferred editor with full LLM assistance. The Alien node watches for file changes, rebuilds its parameters from header comments, and re-executes instantly — no manual relay needed.

## Quick Start

### 1. Install the plugin

- **macOS:** `bash install/install_macos.sh`
- **Windows:** double-click `install\install_windows.bat`
- **Manual:** unzip `install/alien-plugin.zip`, copy contents to your Grasshopper Libraries folder

### 2. Configure MCP

Add the Alien MCP server to your editor:

**Cursor** — `.cursor/mcp.json` (included in this repo):
```json
{ "mcpServers": { "alien": { "url": "http://127.0.0.1:9876/mcp" } } }
```

**Claude Desktop** — `claude_desktop_config.json`:
```json
{ "mcpServers": { "alien": { "url": "http://127.0.0.1:9876/mcp", "transport": "streamable-http" } } }
```

### 3. Start scripting

1. Open Rhino 8, launch Grasshopper
2. Place an **Alien** node from the **Script** tab
3. Connect a Panel with the absolute path to a `.py` file in `scripts/`
4. The MCP server auto-starts — your AI agent now has 15 tools to inspect the canvas, read/write scripts, and verify outputs

### 4. Read the instructions

Point your LLM agent at `instructions/FIRST_PROMPT.md` — it contains the full reading order and workflow protocol.

## How It Works

```
You / LLM agent                    Grasshopper
      |                                 |
      |-- write .py file -------------->|
      |   (via editor or MCP)           |
      |                                 |-- Alien node detects change
      |                                 |-- parses header → rebuilds params
      |                                 |-- executes script
      |                                 |-- outputs geometry + data
      |                                 |
      |<-- MCP: check status -----------|
      |<-- MCP: read outputs -----------|
      |<-- MCP: read errors ------------|
      |                                 |
      |-- fix + re-save --------------->|  (repeat)
```

## Script Header Format

```python
#! python 3
# NODE_INPUTS: origin:Point3d, count:int, spacing:float
# NODE_OUTPUTS: geometry, log
```

Supported types: `Point3d`, `Vector3d`, `Plane`, `Line`, `Curve`, `Surface`, `Brep`, `Mesh`, `int`, `float`, `str`, `bool`, `color`, `geometry`. Use `list[Type]` for list access.

## MCP Tools (15 total)

| Category | Tools |
|----------|-------|
| Canvas | `get_canvas_info`, `get_component_outputs` |
| Script | `get_scriptnode_info`, `get_script_source`, `write_script_source`, `get_error_log` |
| Node State | `get_node_state`, `set_param_value` |
| Rhino | `get_rhino_command_history`, `clear_rhino_command_history`, `run_rhino_command` |
| DataNode | `get_datanode_info`, `set_datanode_values`, `add_datanode_items`, `set_datanode_schema` |

## Project Structure

```
alien-workbench/
├── README.md                   ← you are here
├── .cursor/
│   └── mcp.json                  MCP config for Cursor
├── install/
│   ├── alien-plugin.zip          pre-built plugin (mac + windows)
│   ├── install_macos.sh          one-click macOS installer
│   └── install_windows.bat       one-click Windows installer
├── scripts/                      your Python scripts live here
│   ├── template.py               starter template
│   └── (13 example scripts)
├── references/                   reference scripts and docs
│   └── (17 files)
└── instructions/                 LLM agent instructions
    ├── FIRST_PROMPT.md           ← entry point for agents
    ├── HEADER_PROTOCOL.md          input/output syntax
    ├── TYPE_LEXICON.md             complete type reference
    ├── MCP_WORKFLOW.md             15 MCP tools + debug protocol
    ├── CHAINING.md                 wiring scripts together
    ├── GOTCHAS.md                  known issues + platform diffs
    ├── ALGORITHMS.md               generative design patterns
    └── DATANODE.md                 DataNode component reference
```

## Prerequisites

- **Rhino 8** (Service Release 18 or newer)
- **Windows 10/11** or **macOS** (Apple Silicon + Intel)
- An MCP-capable code editor (Cursor, Claude Desktop, etc.)

---

*Created by Nick Gauci with AI assistance.*
