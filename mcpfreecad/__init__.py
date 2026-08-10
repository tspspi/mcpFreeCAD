"""mcpfreecad package initializer."""

from importlib import metadata


def __getattr__(name: str):
    if name == "__version__":
        return metadata.version("mcpfreecad")
    raise AttributeError(name)


__all__ = ["__version__"]
