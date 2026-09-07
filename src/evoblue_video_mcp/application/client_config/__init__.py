"""P5 WebUI client-config write surface (docs/CLIENT_CONFIG_WRITE_CONTRACT.md).

The package owns everything needed to install / verify / remove / restore this
project's MCP entry in supported clients: the frozen client registry
(``specs``), merge algorithms (``merge_toml`` / ``merge_json``), the single
write channel (``writes``), backups (``backup``), path resolution (``paths``),
per-tier adapters, and the orchestrating service. Contract authority lives in
the document; drift tests keep the two in lockstep.
"""
