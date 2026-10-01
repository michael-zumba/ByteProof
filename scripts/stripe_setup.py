"""Create (or verify) the Stripe objects ByteProof needs.

Run this against a sandbox first, then against the live account:

    STRIPE_API_KEY=sk_test_... python scripts/stripe_setup.py \
        --base-url https://api.bytemind.co.nz

It creates:
  * the "ByteProof" product and a one-time NZ$49 price (tax-inclusive),
  * a Payment Link whose success page shows the licence key,
  * the licence-service webhook endpoint, and prints its signing secret.

The script is idempotent: existing objects with the same name/URL are reused.
Nothing is deleted. Use a key with write access to Products, Prices, Payment
Links and Webhook Endpoints (a temporary ``sk_`` key from the Dashboard is
simplest; the running licence service itself should use a read-only ``rk_``
key).
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

import stripe

PRODUCT_NAME = "ByteProof"
PRODUCT_METADATA = {"product": "byteproof", "vendor": "bytemind"}
WEBHOOK_EVENTS = [
    "checkout.session.completed",
    "checkout.session.async_payment_succeeded",
    "charge.refunded",
    "charge.dispute.created",
]


def _plain(value: Any) -> Any:
    to_dict = getattr(value, "to_dict", None)
    return to_dict() if callable(to_dict) else value


def find_or_create_product(client: stripe.StripeClient) -> dict[str, Any]:
    products = client.v1.products.list({"active": True, "limit": 100})
    for product in products.auto_paging_iter():
        if product.name == PRODUCT_NAME:
            print(f"Product: reusing {product.id}")
            return _plain(product)
    product = client.v1.products.create(
        {
            "name": PRODUCT_NAME,
            "description": (
                "ByteProof desktop proofreading — one licence, up to "
                "2 computers."
            ),
            "metadata": PRODUCT_METADATA,
        }
    )
    print(f"Product: created {product.id}")
    return _plain(product)


def find_or_create_price(
    client: stripe.StripeClient, product_id: str, amount: int, currency: str
) -> dict[str, Any]:
    prices = client.v1.prices.list({"product": product_id, "active": True, "limit": 100})
    for price in prices.auto_paging_iter():
        if (
            price.unit_amount == amount
            and price.currency == currency.lower()
            and price.type == "one_time"
        ):
            print(f"Price: reusing {price.id} ({amount / 100:.2f} {currency})")
            return _plain(price)
    price = client.v1.prices.create(
        {
            "product": product_id,
            "currency": currency.lower(),
            "unit_amount": amount,
            "tax_behavior": "inclusive",
        }
    )
    print(
        f"Price: created {price.id} ({amount / 100:.2f} {currency}, "
        "tax-inclusive)"
    )
    return _plain(price)


def find_or_create_payment_link(
    client: stripe.StripeClient,
    price_id: str,
    success_url: str,
    automatic_tax: bool = True,
) -> dict[str, Any]:
    links = client.v1.payment_links.list({"active": True, "limit": 100})
    for link in links.auto_paging_iter():
        line_items = client.v1.payment_links.list_line_items(link.id)
        for item in line_items.auto_paging_iter():
            if getattr(item.price, "id", None) == price_id:
                print(f"Payment Link: reusing {link.url}")
                return _plain(link)
    params: dict[str, Any] = {
        "line_items": [{"price": price_id, "quantity": 1}],
        "after_completion": {
            "type": "redirect",
            "redirect": {"url": success_url},
        },
        "allow_promotion_codes": True,
        "metadata": PRODUCT_METADATA,
        # payment_method_types is deliberately omitted: Stripe shows the
        # dynamic payment methods enabled in the Dashboard (card, Apple Pay,
        # Google Pay, Alipay...).
    }
    if automatic_tax:
        # Stripe Tax needs a head office address and an active registration.
        # Unclaimed test sandboxes have neither, so this is switchable.
        params["automatic_tax"] = {"enabled": True}
    link = client.v1.payment_links.create(params)
    print(f"Payment Link: created {link.url}")
    return _plain(link)


def find_or_create_webhook(
    client: stripe.StripeClient, url: str
) -> dict[str, Any]:
    endpoints = client.v1.webhook_endpoints.list({"limit": 100})
    for endpoint in endpoints.auto_paging_iter():
        if endpoint.url == url:
            missing = [
                event
                for event in WEBHOOK_EVENTS
                if event not in (endpoint.enabled_events or [])
            ]
            if missing:
                endpoint = client.v1.webhook_endpoints.update(
                    endpoint.id,
                    {"enabled_events": list(WEBHOOK_EVENTS)},
                )
                print(f"Webhook: updated {endpoint.id} with {len(WEBHOOK_EVENTS)} events")
            else:
                print(f"Webhook: reusing {endpoint.id}")
            print(
                "Webhook signing secret is only shown when an endpoint is "
                "created; rotate the secret in the Dashboard if you no longer "
                "have it."
            )
            return _plain(endpoint)
    endpoint = client.v1.webhook_endpoints.create(
        {"url": url, "enabled_events": list(WEBHOOK_EVENTS)}
    )
    print(f"Webhook: created {endpoint.id}")
    return _plain(endpoint)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=os.environ.get(
            "BYTEPROOF_PUBLIC_BASE_URL", "https://api.bytemind.co.nz"
        ),
        help="Public base URL of the licence service.",
    )
    parser.add_argument("--amount", type=int, default=4900, help="Price in cents.")
    parser.add_argument("--currency", default="nzd")
    parser.add_argument(
        "--no-automatic-tax",
        action="store_true",
        help=(
            "Create the link without Stripe Tax (for an unclaimed sandbox, "
            "which has no head office address or tax registration)."
        ),
    )
    args = parser.parse_args()

    api_key = os.environ.get("STRIPE_API_KEY", "").strip()
    if not api_key:
        print("Set STRIPE_API_KEY first (sandbox key first, live key later).")
        return 2
    base = args.base_url.rstrip("/")
    client = stripe.StripeClient(api_key)

    product = find_or_create_product(client)
    price = find_or_create_price(client, product["id"], args.amount, args.currency)
    link = find_or_create_payment_link(
        client,
        price["id"],
        f"{base}/thanks?session_id={{CHECKOUT_SESSION_ID}}",
        automatic_tax=not args.no_automatic_tax,
    )
    endpoint = find_or_create_webhook(client, f"{base}/api/byteproof/stripe-webhook")

    print()
    print("=== ByteProof Stripe setup ===")
    print(f"Product:      {product['id']}")
    print(f"Price:        {price['id']}")
    print(f"Payment Link: {link.get('url')}")
    print(f"Webhook:      {endpoint.get('id')} -> {endpoint.get('url')}")
    if args.no_automatic_tax:
        print("Stripe Tax:   OFF (sandbox run; enable it for the live link)")
    else:
        print("Stripe Tax:   ON (automatic_tax enabled)")
    secret = endpoint.get("secret")
    if secret:
        print(f"Webhook secret (store as STRIPE_WEBHOOK_SECRET): {secret}")
    print()
    print("App purchase URL (BYTEPROOF_PURCHASE_URL):")
    print(f"  {link.get('url')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
