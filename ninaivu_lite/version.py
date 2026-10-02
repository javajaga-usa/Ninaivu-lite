"""One place for the version and who made it: read by the package, the build and the pages."""

__version__ = "1.4.1"
APP_NAME = "Ninaivu Lite"
#: Who holds the copyright, shown in About, the console and the Control Panel
#: (as in Ninaivu). The licence stays MIT (see LICENSE).
COPYRIGHT_HOLDER = "Jagadeesh Rajendran"
COPYRIGHT = f"© 2026 {COPYRIGHT_HOLDER}"
LICENCE = "MIT"


def about() -> dict[str, str]:
    """Name, version, copyright and licence, for any page or API that shows them."""
    return {"name": APP_NAME, "version": __version__, "copyright": COPYRIGHT, "licence": LICENCE}
