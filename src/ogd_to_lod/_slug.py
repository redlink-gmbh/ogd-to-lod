"""Shared slug helper.

Produces an IRI-safe, kebab-cased version of a human-readable name. Used to
derive per-dataset cube and ObservationSet IRIs from the CLI
``--output-folder`` value, and shared with the GitHub service for branch
and file naming.
"""


def slugify(name: str, default: str = "mapping") -> str:
    """Lowercase, kebab-case slug containing only ``[a-z0-9-]``.

    Spaces and underscores collapse into hyphens; any other character is
    dropped. Runs of hyphens are collapsed and leading/trailing hyphens
    stripped. If the result would be empty, ``default`` is returned.
    """
    s = name.replace(" ", "-").replace("_", "-")
    s = "".join(c for c in s if c.isalnum() or c == "-")
    while "--" in s:
        s = s.replace("--", "-")
    s = s.strip("-").lower()
    return s or default
