"""Typed failures. Each one carries a message a human can act on, because every one of
them ends up rendered verbatim in the UI."""

from __future__ import annotations


class AgentError(Exception):
    """Base for everything Agent raises on purpose."""


class NotConfigured(AgentError):
    """Something real was asked of a dependency that has not been connected yet."""


class McpError(AgentError):
    """An MCP server could not be reached, started, or answered with an error."""


class ToolError(AgentError):
    """A tool ran and failed. The message is shown to both the model and the user."""


class RunCancelled(AgentError):
    """The user stopped the run."""


class GuardrailTripped(AgentError):
    """A budget ceiling (steps, wall clock, stagnation) stopped the run."""
