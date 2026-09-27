"""Locate the external compilers the IR pipeline drives.

Lookup order per tool: environment override (e.g. BCA_QBE), ~/.local/bin, then PATH.
"""

import os
import shutil
from functools import cache
from pathlib import Path

LOCAL_BIN = Path.home() / ".local" / "bin"

# tool -> (env override, candidate executable names)
TOOLS = {
    "qbe": ("BCA_QBE", ["qbe"]),
    "llvm-mc": ("BCA_LLVM_MC", ["llvm-mc", "llvm-mc-18"]),
    # GNU as starts ~10x faster than llvm-mc; used for QBE output when present.
    "gas-aarch64": ("BCA_GAS_AARCH64", ["aarch64-linux-gnu-as"]),
    "gas-x86_64": ("BCA_GAS_X86_64", ["x86_64-linux-gnu-as"]),
    "clang": ("BCA_CLANG", ["clang", "clang-18"]),
    "tcc-aarch64": ("BCA_TCC_AARCH64", ["tcc", "aarch64-tcc", "arm64-tcc"]),
    "tcc-x86_64": ("BCA_TCC_X86_64", ["x86_64-tcc"]),
    "wasmtime": ("BCA_WASMTIME", ["wasmtime"]),
}


class ToolMissing(RuntimeError):
    pass


@cache
def find_tool(tool: str) -> str | None:
    env, names = TOOLS[tool]
    if os.environ.get(env):
        return os.environ[env]
    for name in names:
        local = LOCAL_BIN / name
        if local.is_file() and os.access(local, os.X_OK):
            return str(local)
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def require_tool(tool: str) -> str:
    path = find_tool(tool)
    if path is None:
        env, names = TOOLS[tool]
        raise ToolMissing(f"{tool} not found (looked for {names} in ${env}, {LOCAL_BIN}, PATH)")
    return path
