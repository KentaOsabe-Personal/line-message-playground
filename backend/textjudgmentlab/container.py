"""文章判定ラボの外部通信と利用制限を組み立てるcomposition root。"""

from django.conf import settings

from .jev_gateway import JevGateway
from .limits import LabLimits
from .runtime import LabRuntimeConfigured
from .services import JudgmentService

_limits = LabLimits()


def build_judgment_service() -> JudgmentService:
    runtime = settings.TEXT_JUDGMENT_LAB_RUNTIME
    if not isinstance(runtime, LabRuntimeConfigured):
        raise RuntimeError("TEXT_JUDGMENT_LAB_CONFIGURATION_UNAVAILABLE")
    return JudgmentService(
        model=runtime.model,
        gateway=JevGateway(runtime.api_key),
        limits=_limits,
    )
