import argparse
import re

from pip._internal.req.req_file import parse_requirements
from pip._internal.download import PipSession


def clean_name(name):
    # Escape any illegal characters with underscore.
    return re.sub("[-.+]", "_", name)


def is_pinned_requirement(ireq):
    """
    Returns whether an InstallRequirement is a "pinned" requirement.
    An InstallRequirement is considered pinned if:
    - Is not editable
    - It has exactly one specifier
    - That specifier is "=="
    - The version does not contain a wildcard
    Examples:
        django==1.8   # pinned
        django>1.8    # NOT pinned
        django~=1.8   # NOT pinned
        django==1.*   # NOT pinned
    """
    if ireq.editable:
        return False

    if ireq.req is None or len(ireq.specifier._specs) != 1:
        return False

    op, version = next(iter(ireq.specifier._specs))._spec
    return (op == "==" or op == "===") and not version.endswith(".*")


def as_tuple(ireq):
    """
    Pulls out the (name: str, version:str, extras:(str)) tuple from
    the pinned InstallRequirement.
    """
    if not is_pinned_requirement(ireq):
        raise TypeError("Expected a pinned InstallRequirement, got {}".format(ireq))

    name = ireq.name
    version = next(iter(ireq.specifier._specs))._spec[1]
    extras = tuple(sorted(ireq.extras))
    return name, version, extras


def repository_name(name, version):
    """Returns the canonical name of the Bazel repository for a package.

    :param name: package nane
    :param version: package verion
    :returns: repo name
    :rtype: str

    """
    canonical = "pypi__{}_{}".format(name, version)
    return clean_name(canonical)


def whl_library(req, pip_repo_name):
    # Indentation here matters
    name, version, extras = as_tuple(req)
    return """
  if "{repo_name}" not in native.existing_rules():
    whl_library(
        name = "{repo_name}",
        pkg = "{name}",
        requirements_repo = "@{pip_repo_name}",
        extras = [{extras}]
    )""".format(
        name=name,
        repo_name=repository_name(name, version),
        pip_repo_name=pip_repo_name,
        extras=",".join(['"%s"' % extra for extra in extras]),
    )


def get_requirements(requirement):
    session = PipSession()
    return parse_requirements(requirement, session=session)


def main():
    parser = argparse.ArgumentParser(
        description="Import Python dependencies into Bazel."
    )
    parser.add_argument("--name", action="store", help=("The namespace of the import."))
    parser.add_argument(
        "--input",
        action="store",
        help=("The requirements.txt file to import."),
        required=True,
    )
    parser.add_argument(
        "--output",
        action="store",
        help=("The requirements.bzl file to export."),
        required=True,
    )
    args = parser.parse_args()
    reqs = get_requirements(args.input)

    whl_targets = []
    whl_libraries = []
    for req in reqs:
        name, version, extras = as_tuple(req)
        whl_targets.append(
            ",".join(
                ['"%s": "@%s//:pkg"' % (name.lower(), repository_name(name, version))]
                + [
                    # For every extra that is possible from this requirements.txt
                    '"%s[%s]": "@%s//:%s"'
                    % (
                        name.lower(),
                        extra.lower(),
                        repository_name(name, version),
                        extra,
                    )
                    for extra in extras
                ]
            )
        )
        whl_libraries.append(whl_library(req, args.name))

    with open(args.output, "w") as _f:
        _f.write(
            """\
# Install pip requirements.
#
# Generated from {input}

load("@com_github_alish_rules_pip_lock//:defs.bzl", "whl_library")

def pip_install():
  {whl_libraries}

_requirements = {{
  {mappings}
}}

def requirement(name):
  name_key = name.lower()
  if name_key not in _requirements:
    fail("Could not find pip-provided dependency: '%s'" % name)
  return _requirements[name_key]
""".format(
                input=args.input,
                whl_libraries="\n".join(whl_libraries),
                mappings=",".join(whl_targets),
            )
        )


if __name__ == "__main__":
    main()