"""Build definitions.

Data only — deliberately does not re-export the registry. `bot.core.registry`
imports from here, so importing it back would be circular. Look builds up via
`from bot.core.registry import get_build`.
"""

from bot.builds.definition import Army, BuildDefinition, Combat, Economy

__all__ = ["Army", "BuildDefinition", "Combat", "Economy"]
