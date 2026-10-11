"""The component base the E3/DC register map builds on."""

from __future__ import annotations

from modbus_connection.model import Component, UpdateReport

__all__ = ["E3dcComponent", "UpdateReport", "register"]


def register(index: int) -> int:
    """The protocol address of register ``index`` as the manual numbers it.

    The manual counts from 1, so its register 40001 is address 40000.
    """
    return index - 1


class E3dcComponent(Component):
    """A sub-system of the E3/DC Simple Mode register map."""
