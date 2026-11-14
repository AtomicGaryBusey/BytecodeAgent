"""Run vLLM 0.29.0's benchmarks/kernels/benchmark_moe.py for Kolibri-1 (run inside the serving image, cwd = that dir).

Two things the stock tuner cannot do for this model (found 2026-11-13 when re-tuning the lost config):
- its config type `kolibri1` is registered by the Aleph-Alpha vLLM plugin, which the standalone tuner never loads,
  so the config read fails ("Transformers does not recognize this architecture");
- `Kolibri1ForCausalLM` is not in get_model_params(), whose fallback (Mixtral) would read `intermediate_size` (6144,
  the dense MLP) instead of `moe_intermediate_size` (512, the experts): it would tune the wrong shape.
So this loads the plugins, and answers get_model_params() for Kolibri from num_local_experts (384),
num_experts_per_tok (6), moe_intermediate_size (512) and hidden_size (2560) -> the E=384,N=512 file the server reads.
Arguments are benchmark_moe.py's own."""
import sys

from vllm.plugins import load_general_plugins

load_general_plugins()
sys.path.insert(0, ".")
import benchmark_moe as bm  # noqa: E402  (after the plugins; ray workers import the same module by name)

_stock = bm.get_model_params


def get_model_params(config):
    arch = (getattr(config, "architectures", None) or [type(config).__name__])[0]
    if arch == "Kolibri1ForCausalLM":
        return config.num_local_experts, config.num_experts_per_tok, config.moe_intermediate_size, config.hidden_size
    return _stock(config)


bm.get_model_params = get_model_params

if __name__ == "__main__":
    sys.argv[0] = "benchmark_moe.py"
    src = open("benchmark_moe.py").read()
    main_block = src[src.index('if __name__ == "__main__":'):]
    exec(compile(main_block.replace('if __name__ == "__main__":', "if True:", 1), "benchmark_moe.py", "exec"),
         {**vars(bm), "main": bm.main})
