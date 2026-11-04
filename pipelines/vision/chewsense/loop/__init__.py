"""Guided collection and evaluation loop built on the schema-v2 session bundle.

The loop coordinates existing Collect → Vision → Review → Motion components.
It never converts instructions, model output or assisted edits into
independent evaluation truth, and it never promotes a model.
"""
