"""Install this directory as the opt-in hermes-memd MemoryProvider plugin."""


def register(ctx):
    from .hermes_bridge import MemdMemoryProvider
    ctx.register_memory_provider(MemdMemoryProvider())
