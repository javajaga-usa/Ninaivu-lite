"""Ninaivu Lite — your family's photographs, at home. The small, steady edition.

Ninaivu's own screens (the family gallery, the admin console at /admin and the
share page) on one port, served by a lighter engine: no AI, no cloud, nothing
that changes or deletes a photograph. :func:`create_app` builds it;
``python -m ninaivu_lite`` runs it.
"""

from __future__ import annotations

# Kept light on purpose: the Control Panel imports this package too, and should
# open without loading Flask and Pillow. The web app is built on first use.
from .version import APP_NAME, COPYRIGHT, LICENCE, __version__, about  # noqa: F401


def create_app(cfg=None, *, addresses=None, scanner=None):
    """Build the web application (see :mod:`ninaivu_lite.app`)."""
    from .app import create_app as build
    return build(cfg, addresses=addresses, scanner=scanner)
