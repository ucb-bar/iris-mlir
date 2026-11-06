"""Compatibility import for the shared Merlin VME kernel emitter."""

from merlin.kernels.vme_kernel import KERNEL_FUNC, OperandLayout, VmeKernelSpec, emit_kernel

__all__ = ["KERNEL_FUNC", "OperandLayout", "VmeKernelSpec", "emit_kernel"]
