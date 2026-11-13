"""E2E-8: the eval runtime tolerates a disconnect race when it re-attaches a container under its alias."""

from __future__ import annotations

import pytest

from backend.generation.evaluation.runtime import _attach_alias


class _Net:
    def __init__(self, disconnect_error: Exception | None = None) -> None:
        self.disconnect_error = disconnect_error
        self.calls: list[tuple[str, object, object]] = []

    def disconnect(self, container: object) -> None:
        self.calls.append(("disconnect", container, None))
        if self.disconnect_error is not None:
            raise self.disconnect_error

    def connect(self, container: object, aliases: list[str]) -> None:
        self.calls.append(("connect", container, aliases))


def test_attach_reconnects_with_alias() -> None:
    net = _Net()
    _attach_alias(net, "c1", "app")
    assert net.calls == [("disconnect", "c1", None), ("connect", "c1", ["app"])]


def test_not_connected_answer_is_ignored_and_connect_proceeds() -> None:
    err = RuntimeError(
        '500 Server Error: Internal Server Error ("container 3ad4 is not connected to the network 3200")',
    )
    net = _Net(err)
    _attach_alias(net, "c1", "db")
    assert net.calls[-1] == ("connect", "c1", ["db"])


def test_other_disconnect_errors_propagate() -> None:
    net = _Net(RuntimeError("500 Server Error: daemon unavailable"))
    with pytest.raises(RuntimeError, match="daemon unavailable"):
        _attach_alias(net, "c1", "app")
    assert [c[0] for c in net.calls] == ["disconnect"]
