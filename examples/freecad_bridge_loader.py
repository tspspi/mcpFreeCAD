"""Convenience loader for starting the bridge from FreeCAD's Python console."""

import os
import sys

repo_root = os.environ.get("MCPFREECAD_REPO_ROOT")
if repo_root and repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from mcpfreecad.freecad.bridge_server import start_bridge_server


start_bridge_server(host="127.0.0.1", port=48111, token="change-me")
