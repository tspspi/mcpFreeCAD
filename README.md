# FreeCAD MCP Server `mcpfreecad`

`mcpfreecad` is an MCP server for driving a running FreeCAD session. It exposes document management, modeling, inspection, snapshot, and optional workbench operations to MCP clients over `stdio` or authenticated remote HTTP.

The server is designed around a small Python bridge that is loaded into FreeCAD's embedded interpreter. The MCP process then talks to that bridge over localhost.

## Features

- `stdio` and API-key authenticated `remotehttp` transports
- document lifecycle tools for opening, saving, closing, and enumerating FreeCAD documents
- explicit model inspection via document tree, topology, sketch status, and sketch details tools
- modeling support for Part primitives, PartDesign bodies/pads/pockets, spreadsheets, Draft arrays, and snapshots
- reusable object library backed by BREP plus JSON metadata
- optional workbench integration for helpers such as `freecad.gears`, Fasteners, Curves sketch-on-surface workflows, and A2plus assembly operations when available
- remote snapshot retrieval through authenticated URLs or tokenized snapshot download URLs

For LLM-facing operating guidance, see `LLM_USAGE.md`.

## Further Information

There is an accompanying [blog article](https://www.tspi.at/2026/08/06/llmfreecad.html) that describes some of the usage and motivation behind this project.

![Obligatory comic drawing](https://raw.githubusercontent.com/tspspi/mcpFreeCAD/refs/heads/master/docs/_media/llmcad001.png)

## Installation

Install from PyPI:

```sh
pip install mcpfreecad
```

To enable remote HTTP mode:

```sh
pip install "mcpfreecad[remote]"
```

For local development:

```sh
git clone <repository-url>
cd mcpFreeCAD
pip install -e .
pip install -e ".[remote]"
```

## Quick Start

1. Start FreeCAD.
2. Load the bridge module inside the FreeCAD Python console.
3. Start `mcpfreecad` in `stdio` mode or `remotehttp` mode.
4. Connect your MCP client and call `bridge_status`.

Example bridge loading from a checkout:

```python
exec(open("/path/to/mcpFreeCAD/examples/freecad_bridge_loader.py").read(), globals(), globals())
```

The loader starts the bridge on `127.0.0.1:48111` with token `change-me`. Adjust the example or call `start_bridge_server(...)` directly if you need different values.

## Configuration

The default configuration path is `~/.config/mcpfreecad.conf`.

Example configuration:

```json
{
  "mode": "remotehttp",
  "logging": {
    "level": "INFO"
  },
  "bridge": {
    "host": "127.0.0.1",
    "port": 48111,
    "token": "change-me",
    "timeout_seconds": 30.0
  },
  "remote_server": {
    "transport": {
      "uds": "/var/run/mcpfreecad.sock"
    },
    "url_prefix": "https://mcp.example.com/freecad/"
  },
  "stdio": {
    "library_root": "/srv/mcpfreecad/stdio-library",
    "allow_code_execution": true,
    "allow_library_write": true,
    "allow_snapshots": true
  },
  "api_keys": [
    {
      "id": "cad-agent",
      "kdf": {
        "algorithm": "argon2id",
        "salt": "BASE64",
        "time_cost": 3,
        "memory_cost": 65536,
        "parallelism": 1,
        "hash_len": 32,
        "hash": "BASE64"
      },
      "library_root": "/srv/mcpfreecad/cad-agent-library",
      "allow_code_execution": true,
      "allow_library_write": true,
      "allow_snapshots": true
    }
  ]
}
```

Generate or rotate a remote API key:

```sh
mcpfreecad --config ~/.config/mcpfreecad.conf --genkey cad-agent
```

## Running

StdIO mode:

```sh
mcpfreecad --config ~/.config/mcpfreecad.conf
```

Remote HTTP mode:

```sh
mcpfreecad --config ~/.config/mcpfreecad.conf --transport remotehttp
```

The remote HTTP wrapper accepts:

- `Authorization: Bearer <token>`
- `X-API-Key: <token>`
- `?mcp=<token>`
- legacy `?api_key=<token>`

`/status` is intentionally public so it can be used for health checks.

## Reverse Proxy Notes

The FastMCP instance is created with:

```python
TransportSecuritySettings(enable_dns_rebinding_protection=False)
```

That is intentional for reverse-proxy deployments.

Example Apache layout:

```apache
ProxyPass        /freecad/status      http://127.0.0.1:18080/status
ProxyPassReverse /freecad/status      http://127.0.0.1:18080/status

ProxyPass        /freecad/mcp/        http://127.0.0.1:18080/mcp/
ProxyPassReverse /freecad/mcp/        http://127.0.0.1:18080/mcp/

ProxyPass        /freecad/snapshots/  http://127.0.0.1:18080/snapshots/
ProxyPassReverse /freecad/snapshots/  http://127.0.0.1:18080/snapshots/
```

When `remote_server.url_prefix` is configured, snapshot download URLs are returned as absolute URLs rooted there. Otherwise they fall back to relative `../snapshots/...` paths.

## Snapshot Retrieval

`capture_snapshot(...)` returns:

- `snapshot_id`
- `download_url`
- `download_url_with_token`

`download_url` requires normal MCP auth again.

`download_url_with_token` is an easier direct-fetch URL for clients that cannot conveniently resend MCP auth. It contains a random in-memory token and returns `image/png`.

You can also retrieve a registered snapshot inline through:

```text
get_snapshot_base64(snapshot_id="...")
```

Snapshots are exposed only if they were created through `capture_snapshot(...)`. Arbitrary server files are not downloadable through the snapshot route.

## FreeBSD rc.d Service

A sample rc.d script is included at `freebsd/rc.d/mcpfreecad`.

Install it as:

```sh
install -m 0555 freebsd/rc.d/mcpfreecad /usr/local/etc/rc.d/mcpfreecad
```

Default `rc.conf` settings:

```sh
mcpfreecad_enable="YES"
mcpfreecad_config="/usr/local/etc/mcpfreecad.conf"
```

Optional overrides:

```sh
mcpfreecad_daemon_user="mcpfreecad"
mcpfreecad_command="/usr/local/bin/mcpfreecad"
mcpfreecad_transport="remotehttp"
mcpfreecad_flags=""
mcpfreecad_pidfile="/var/run/mcpfreecad.pid"
```

## Optional Workbenches

`mcpfreecad` only surfaces optional workbench tools when the corresponding workbench is available on the host. This currently includes support for areas such as:

- `freecad.gears`
- Fasteners
- Curves
- A2plus

## Repository Layout

- `mcpfreecad/`: package source
- `examples/`: bridge loader and smoke examples
- `freebsd/`: FreeBSD service helper
- `tests/`: automated test suite
- `skill/`: Codex skill material

## Testing

Run the Python test suite with:

```sh
pytest -q
```

For bridge-side smoke testing from a checkout:

```sh
python3 examples/freecad_bridge_smoke.py
python3 examples/freecad_bridge_smoke.py --with-fasteners
```

