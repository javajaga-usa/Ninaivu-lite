#!/bin/sh
# Run by the release workflow before anything is built: refuse to publish a
# version that is already out, a tag that disagrees with version.py, or a
# version with no section in CHANGELOG.md (the release notes come from it).
#
#   REF_TYPE=tag|branch  REF_NAME=<tag or branch>  (GitHub's ref_type, ref_name)
# Needs git, and gh signed in (GH_TOKEN), in a checkout of the repository.
set -eu
version=$(sed -nE 's/^__version__ *= *"([^"]+)".*/\1/p' ninaivu_lite/version.py)
[ -n "$version" ] || { echo "::error::No __version__ in ninaivu_lite/version.py"; exit 1; }
tag="v$version"
fail=0
if [ "${REF_TYPE:-}" = "tag" ]; then
    if [ "${REF_NAME:-}" != "$tag" ]; then
        echo "::error::The tag is ${REF_NAME:-?} but ninaivu_lite/version.py says $version."
        fail=1
    fi
elif git ls-remote --exit-code --tags origin "refs/tags/$tag" >/dev/null 2>&1; then
    echo "::error::$tag is already tagged. Raise __version__ in ninaivu_lite/version.py first."
    fail=1
fi
if gh release view "$tag" >/dev/null 2>&1; then
    echo "::error::Release $tag is already published; a new version is needed, not a rebuild."
    fail=1
fi
if ! grep -qE "^## $(printf '%s' "$version" | sed 's/\./\\./g')( |\$)" CHANGELOG.md; then
    echo "::error::CHANGELOG.md has no '## $version' section for the release notes."
    fail=1
fi
[ "$fail" = 0 ] && echo "Releasing $version as $tag."
exit "$fail"
