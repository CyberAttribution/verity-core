# verity_core.routers — model transport adapters behind one protocol.
# GeminiRouter is imported lazily by consumers that need it
# (from verity_core.routers.gemini import GeminiRouter) so importing this
# package never requires cloud-side configuration.
from .base import CallableRouter, ModelRouter  # noqa: F401
from .openclaw import OpenClawRouter  # noqa: F401
