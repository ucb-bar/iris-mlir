"""Iris's versioned accelerator extension over the inherited RVV backend."""

from merlin.targetgen.extension_offers import MatrixExtension

API_VERSION = 1


def bind(routing):
    return MatrixExtension(routing)
