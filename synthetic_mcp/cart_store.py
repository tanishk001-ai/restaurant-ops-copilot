"""
In-memory cart for the synthetic MCP session.
One CartStore instance per server process (stdio = one session).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CartStore:
    # product_id → item dict
    _items: dict[str, dict] = field(default_factory=dict)

    def add(self, product_id: str, name: str, qty: int, unit_price: float) -> None:
        if product_id in self._items:
            self._items[product_id]["qty"] += qty
            self._items[product_id]["subtotal"] = round(
                self._items[product_id]["qty"] * unit_price, 2
            )
        else:
            self._items[product_id] = {
                "product_id": product_id,
                "name": name,
                "qty": qty,
                "unit_price": unit_price,
                "subtotal": round(qty * unit_price, 2),
            }

    def remove(self, product_id: str) -> bool:
        if product_id in self._items:
            del self._items[product_id]
            return True
        return False

    def clear(self) -> None:
        self._items.clear()

    def total(self) -> float:
        return round(sum(item["subtotal"] for item in self._items.values()), 2)

    @property
    def items(self) -> list[dict]:
        return list(self._items.values())

    def __len__(self) -> int:
        return len(self._items)


# Module-level singleton shared by all tools in this process
cart = CartStore()
