# MCP client configs

Copy-paste snippets for connecting an MCP client to the Ghost Hands MCP
server. Every client below launches the same command with the same
environment; only the wrapper format changes.

Install first:

```bash
pip install ghost-hands
```

The server runs on stdio as `ghost-hands mcp` and exposes five tools:
`hands_perceive`, `hands_act`, `hands_run`, `hands_trail`, and
`hands_verify_trail`. No install at all is also possible — the MCP
Registry entry (`server.json`) launches it with
`uvx ghost-hands mcp`; substitute that command/args pair
(`"command": "uvx"`, `"args": ["ghost-hands", "mcp"]`) in any snippet
below.

## Configuration (environment variables)

The server reads its configuration from the environment, once per
process:

| Variable | Values | Default |
| --- | --- | --- |
| `GHOST_HANDS_DRIVER` | `fake`, `chromium`, `simphone`, `android` | `fake` (the demo web) |
| `GHOST_HANDS_POLICY` | a policy pack name (`readonly`, `standard`, `strict`, `seatbelt`) | `standard` |
| `GHOST_HANDS_APPROVER` | `phone` routes "ask" verdicts to the phone approver | unset — asks are denied with an explanation |
| `GHOST_HANDS_START_URL` | URL opened on the first perceive (real bodies) | unset |

Android sessions also read `GHOST_HANDS_ANDROID_BRIDGE` and
`GHOST_HANDS_ANDROID_TOKEN`. The snippets below use the `chromium`
body with the `phone` approver and the `standard` pack, matching the
README; drop the `env` block entirely for the fake demo web.

## Generic (stdio JSON)

The `mcpServers` shape most clients share (Claude Desktop's
`claude_desktop_config.json`, Cline's `cline_mcp_settings.json`, and
others):

```json
{
  "mcpServers": {
    "ghost-hands": {
      "command": "ghost-hands",
      "args": ["mcp"],
      "env": {
        "GHOST_HANDS_DRIVER": "chromium",
        "GHOST_HANDS_APPROVER": "phone",
        "GHOST_HANDS_POLICY": "standard"
      }
    }
  }
}
```

## Claude Code

```bash
claude mcp add ghost-hands -e GHOST_HANDS_DRIVER=chromium -e GHOST_HANDS_APPROVER=phone -e GHOST_HANDS_POLICY=standard -- ghost-hands mcp
```

## Cursor

Add to `.cursor/mcp.json` in a project, or `~/.cursor/mcp.json` for all
projects:

```json
{
  "mcpServers": {
    "ghost-hands": {
      "command": "ghost-hands",
      "args": ["mcp"],
      "env": {
        "GHOST_HANDS_DRIVER": "chromium",
        "GHOST_HANDS_APPROVER": "phone",
        "GHOST_HANDS_POLICY": "standard"
      }
    }
  }
}
```

## VS Code

Add to `.vscode/mcp.json`. Note that VS Code names the top-level key
`servers`, not `mcpServers`:

```json
{
  "servers": {
    "ghost-hands": {
      "command": "ghost-hands",
      "args": ["mcp"],
      "env": {
        "GHOST_HANDS_DRIVER": "chromium",
        "GHOST_HANDS_APPROVER": "phone",
        "GHOST_HANDS_POLICY": "standard"
      }
    }
  }
}
```
