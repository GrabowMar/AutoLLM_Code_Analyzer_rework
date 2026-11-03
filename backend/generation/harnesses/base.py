"""The ``HarnessAdapter`` ABC (spec section 5.1). The data it exchanges lives in ``contracts.harness``.

Skeleton written by WP-01 (signatures from docs/design/SAMPLE_GEN_V3.md); WP-07 implements it.
"""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from typing import TYPE_CHECKING
from typing import Any
from typing import ClassVar

if TYPE_CHECKING:
    from backend.generation.contracts.harness import ExitInfo
    from backend.generation.contracts.harness import GatewayWiring
    from backend.generation.contracts.harness import HarnessCapabilities
    from backend.generation.contracts.harness import HarnessContext
    from backend.generation.contracts.harness import HarnessFacts
    from backend.generation.contracts.harness import ImageSpec
    from backend.generation.contracts.harness import ObservedInputs
    from backend.generation.contracts.harness import RenderedHarness
    from backend.generation.contracts.harness import Trajectory
    from backend.generation.contracts.harness import WireProtocol
    from backend.generation.contracts.outcome import Termination

_PENDING = "WP-07: not implemented yet"


class HarnessAdapter(ABC):
    """One coding-agent harness: image, gateway wiring, rendering, trajectory parsing and termination."""

    name: ClassVar[str]
    label: ClassVar[str]
    protocol: ClassVar[WireProtocol]
    capabilities: ClassVar[HarnessCapabilities]
    version_setting: ClassVar[str]  # settings.AGENT_<NAME>_VERSION
    option_fields: ClassVar[tuple[Any, ...]] = ()  # OptionField, ported from the v2 adapter base
    facts: ClassVar[HarnessFacts]  # section 5.3; MUST be filled before the adapter is enabled

    @abstractmethod
    def image_spec(self, version: str) -> ImageSpec: ...

    @abstractmethod
    def gateway_wiring(self, gateway_host: str) -> GatewayWiring: ...

    @abstractmethod
    def render(self, ctx: HarnessContext) -> RenderedHarness: ...

    @abstractmethod
    def parse_trajectory(self, outputs: dict[str, bytes], calls: list[dict[str, Any]]) -> Trajectory: ...

    @abstractmethod
    def observed_inputs(self, calls: list[dict[str, Any]]) -> ObservedInputs: ...

    def classify_termination(self, exit_info: ExitInfo, trajectory: Trajectory) -> Termination:
        """Termination from elapsed time and gateway facts, never from exit codes alone (section 5.1)."""
        raise NotImplementedError(_PENDING)

    def normalize_options(self, options: dict[str, Any]) -> dict[str, Any]:
        """Validated, sparse harness options (unchanged semantics from v2)."""
        raise NotImplementedError(_PENDING)

    def restrict_paths(self, ctx: HarnessContext) -> RenderedHarness | None:
        """Slot agent: a render limited to ``ctx.slot.allowlist``, or None when unsupported."""
        return None
