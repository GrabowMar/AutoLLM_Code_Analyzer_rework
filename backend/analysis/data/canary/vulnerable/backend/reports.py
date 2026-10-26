"""Report helpers.

CANARY FIXTURE: performance anti-patterns planted for perflint (positive
control): a list built by a loop instead of a comprehension, a loop-invariant
expression and a needless list() copy of a list.
"""


def order_lines(orders: list[dict]) -> list[str]:
    lines = []
    for order in orders:
        lines.append(f"{order['id']}: {order['total']}")
    return lines


def taxed_prices(rate: float) -> list[float]:
    prices = [9.99, 24.5, 3.0]
    taxed = []
    for price in list(prices):
        taxed.append(round(price * (1 + rate / 100), 2))
    return taxed
