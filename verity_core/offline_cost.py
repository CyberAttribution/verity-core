"""Offline arithmetic only; no provider pricing, usage attribution or dispatch."""
from .inference_budget import money


def estimate_cost(input_tokens, output_tokens, *, input_price, output_price):
    """Caller-supplied microUSD per million tokens, rounded up once.

    This estimate is neither reported provider usage nor a maximum charge. It
    never reconciles a ledger reservation, including uncertain completions.
    """
    for count in (input_tokens, output_tokens):
        if type(count) is not int or not 0 <= count <= 10**15:
            raise ValueError("Bounded integer token count required")
    money(input_price)
    money(output_price)
    amount = (input_tokens * input_price + output_tokens * output_price + 999999) // 1000000
    money(amount)
    return {"kind": "estimate", "currency": "USD", "unit": "microUSD",
            "amount": amount, "pricing_source": "caller supplied",
            "reported_usage": False, "provider_budget_enforced": False}
