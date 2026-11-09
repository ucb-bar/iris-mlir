"""Selected chip contracts and ordinary prepared-IR compilation service."""

from merlin.compiler.lower import prepared

name = "iris"
_REMAINING = ("CPU/RVV lowering", "ABI admission", "linking", "platform construction")


def adapter(unit):
    if unit != "saturn_vme":
        raise KeyError(unit)
    from types import SimpleNamespace

    from .src.iris import pipelines, placement
    from .src.iris.lower import vme_shim

    return SimpleNamespace(
        load_contract=vme_shim.load_contract,
        derive_encodings=vme_shim.derive_encodings,
        selector=placement.selector,
        rewrite_prepared_file=pipelines.rewrite_prepared_file,
        load_signatures=pipelines.load_signatures,
        build_object=vme_shim.build_object,
        SIDECAR_NAME=vme_shim.SIDECAR_NAME,
    )


def selection(unit, config):
    provider = adapter(unit)
    contract = provider.load_contract(unit)
    edge, alignment = contract.geometry(config)
    sources = [
        *(contract.source(key) for key in sorted(contract.sources)),
        *contract.config_scala(),
        *contract.mixin_scala(),
    ]
    sources.append(contract.core_source())
    if contract.host.get("source"):
        sources.append(contract.root() / str(contract.host["source"]))
    return provider, contract, edge, alignment, sources


def validate(request):
    prepared.validate(request, namespace=name, select=selection)


def compile(request):
    return prepared.compile(
        request, namespace=name, select=selection, remaining=_REMAINING
    )
