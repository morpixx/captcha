"""Runnable processes. Composition roots live here and nowhere else.

Each subpackage is one process: an entrypoint (cli.py / __main__.py) that wires
adapters into application objects, plus the transport surface it exposes.
"""
