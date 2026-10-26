"""Order helpers.

CANARY FIXTURE: summarize_orders and summarize_refunds are a deliberate
copy-paste clone (duplication control for jscpd).
"""


def summarize_orders(rows):
    totals = {}
    counts = {}
    for customer, amount, status in rows:
        if status not in ("paid", "shipped", "delivered"):
            continue
        totals[customer] = totals.get(customer, 0) + amount
        counts[customer] = counts.get(customer, 0) + 1
    report = []
    for customer in sorted(totals):
        average = totals[customer] / counts[customer]
        report.append({"customer": customer, "total": round(totals[customer], 2), "average": round(average, 2)})
    report.sort(key=lambda entry: entry["total"], reverse=True)
    return report


def summarize_refunds(rows):
    totals = {}
    counts = {}
    for customer, amount, status in rows:
        if status not in ("paid", "shipped", "delivered"):
            continue
        totals[customer] = totals.get(customer, 0) + amount
        counts[customer] = counts.get(customer, 0) + 1
    report = []
    for customer in sorted(totals):
        average = totals[customer] / counts[customer]
        report.append({"customer": customer, "total": round(totals[customer], 2), "average": round(average, 2)})
    report.sort(key=lambda entry: entry["total"], reverse=True)
    return report
