"""Target-path resolution (contract §2).

Only two sources are legal: the user home directory (injected;
``Path.home()`` in production) and the ``APPDATA`` environment variable.
Anything else — scanning install directories, querying the registry, probing
executables — is path guessing and out of scope (contract §0). A template
that cannot be resolved here (``%APPDATA%`` missing, i.e. claude_desktop on
a non-Windows machine) yields ``None`` = "unsupported on this machine".
"""

from pathlib import Path

_APPDATA_PREFIX = "%APPDATA%/"
_HOME_PREFIX = "~/"


def resolve_target(template: str, *, home: Path, appdata: str | None) -> Path | None:
    """Expand one frozen write-target template; ``None`` = unsupported here."""
    if template.startswith(_HOME_PREFIX):
        return home / template[len(_HOME_PREFIX) :]
    if template.startswith(_APPDATA_PREFIX):
        if not appdata:
            return None
        return Path(appdata) / template[len(_APPDATA_PREFIX) :]
    return None
