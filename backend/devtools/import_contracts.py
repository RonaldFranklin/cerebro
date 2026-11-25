"""Custom import-linter contract types.

Built-in contracts cannot express "standard library only": with ``include_external_packages`` the standard
library shows up in the import graph as external packages too, so a wildcard ``forbidden`` contract would also
reject ``dataclasses`` or ``json``, and listing third-party packages one by one would silently miss new ones.
"""

import sys

from grimp import ImportGraph
from importlinter import Contract, ContractCheck, fields, output

_ALWAYS_ALLOWED = frozenset({"__future__"})


class StdlibOnlyContract(Contract):
    """Source packages may import only the standard library and modules of the root package.

    Imports between internal packages (e.g. application -> adapters) are the job of the ``layers`` contract.

    Configuration options:
        - source_modules: packages whose external imports must all be standard library modules.
    """

    type_name = "stdlib_only"

    source_modules = fields.ListField(subfield=fields.ModuleField())

    def check(self, graph: ImportGraph, verbose: bool) -> ContractCheck:
        root_packages = {name.split(".")[0] for name in self.session_options["root_packages"]}
        allowed_top_level = sys.stdlib_module_names | _ALWAYS_ALLOWED | root_packages

        violations: list[dict[str, object]] = []
        for source in self.source_modules:  # type: ignore[attr-defined]
            source_name: str = source.name
            for importer in sorted({source_name} | graph.find_descendants(source_name)):
                for imported in sorted(graph.find_modules_directly_imported_by(importer)):
                    if imported.split(".")[0] in allowed_top_level:
                        continue
                    details = graph.get_import_details(importer=importer, imported=imported)
                    violations.append(
                        {
                            "importer": importer,
                            "imported": imported,
                            "line_numbers": sorted(int(d["line_number"]) for d in details),
                        }
                    )

        return ContractCheck(kept=not violations, metadata={"violations": violations})

    def render_broken_contract(self, check: ContractCheck) -> None:
        for violation in check.metadata["violations"]:
            lines = ", ".join(f"l.{n}" for n in violation["line_numbers"])
            output.print_error(
                f"{violation['importer']} imports non-standard-library module {violation['imported']} ({lines})",
                bold=False,
            )
            output.new_line()
