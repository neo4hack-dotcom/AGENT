"""Typed failures. Each one carries a message a human can act on, because every one of
them ends up rendered verbatim in the UI."""

from __future__ import annotations


class LumenError(Exception):
    """Base for everything Lumen raises on purpose."""


class NotConfigured(LumenError):
    """Something real was asked of a dependency that has not been connected yet."""


class McpError(LumenError):
    """An MCP server could not be reached, started, or answered with an error."""


class ToolError(LumenError):
    """A tool ran and failed. The message is shown to both the model and the user."""


class RunCancelled(LumenError):
    """The user stopped the run."""


class GuardrailTripped(LumenError):
    """A budget ceiling (steps, wall clock, stagnation) stopped the run."""
