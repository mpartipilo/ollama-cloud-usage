"""Ollama Cloud Usage — Hermes plugin package.

This plugin provides no agent-side hooks; its surface is the dashboard API
route (``dashboard/plugin_api.py``, mounted at ``/api/plugins/ollama-cloud-usage/``)
and the desktop UI (``desktop/plugin.js``). The no-op ``register()`` below
satisfies the agent plugin loader (which otherwise logs "no register() function").
"""


def register(ctx):
    """No agent-side hooks to register — dashboard API + desktop UI only."""
    return None
