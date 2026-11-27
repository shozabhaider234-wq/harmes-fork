"""Install this directory as the opt-in hermes-memd MemoryProvider plugin."""


def register(ctx):
    from .prime_integration import register as register_runtime
    register_runtime(ctx)
