#!/usr/bin/env python3
"""Launcher for Obsidian MCP server — loads .env secrets before starting."""

import os
import sys

# Add directories to path
server_dir = os.path.dirname(os.path.abspath(__file__))
project_dir = os.path.join(server_dir, "..", "..")
sys.path.insert(0, os.path.join(project_dir, "scripts"))
sys.path.insert(0, server_dir)

from runtime import read_env_file

# Load .env and apply to os.environ (OBSIDIAN_TOKEN lives in .env)
env = read_env_file()
for key, value in env.items():
    if key not in os.environ:
        os.environ[key] = value

# Now import and start server (picks up TOKEN from os.environ)
import server
server.TOKEN = os.environ.get("OBSIDIAN_TOKEN", "")
server.main()
