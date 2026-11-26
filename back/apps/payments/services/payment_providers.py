"""
Payments providers: who takes the money of a Payment (services/payments.py).

``settings.PAYMENTS_PROVIDER`` names the provider. Empty (the default, and
production until a real one exists): ``get_payment_provider()`` is None and
starting a payment answers 501 ``payments_unavailable``. Nothing is ever
faked in that case.

``simulated`` stands in for a real provider in development and demos: its
checkout is the web app's test page (``/dev/checkout``), and that page
settles the payment through ``POST /token-purchases/<id>/simulate/``. It only
runs with DEBUG or with PAYMENTS_ALLOW_SIMULATED=True; production settings
refuse it without that flag (backend/settings/prod.py). Without DEBUG only
staff and PAYMENTS_SIMULATE_ALLOWED_EMAILS may settle a payment (``may_simulate``).

A real provider (PAY-02) implements ``PaymentProvider.start_checkout`` and,
when its webhook confirms or rejects a payment, calls
``services.payments.settle_payment``. Settling is idempotent, so a webhook
that arrives twice changes nothing the second time.
"""
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

from django.conf import settings

SIMULATED = 'simulated'


class PaymentsUnavailable(Exception):
    """No provider is configured: nothing can be paid."""


@dataclass(frozen=True)
class Checkout:
    """Where the browser goes to pay, and the provider's id for that checkout."""

    url: str
    reference: str


class PaymentProvider:
    """
    ``start_checkout`` and ``cancel_checkout`` must each finish within
    settings.PAYMENTS_PROVIDER_TIMEOUT_SECONDS, as a hard total deadline
    (connection, retries and reading included), and raise past it. A pending
    payment still without a checkout twice that long after its call started
    is rejected as lost (services/payments.py, fail_stale_checkouts): a call
    that runs longer gets its checkout closed instead of stored.
    """

    name = ''

    def start_checkout(self, payment, return_url):
        """
        Open the provider's checkout for ``payment`` (amount ``amount_usd``).
        ``return_url`` is a page of the web app: the provider sends the browser
        back to it with ``compra=<payment id>`` added.
        """
        raise NotImplementedError

    def cancel_checkout(self, payment):
        """Close a checkout nobody paid (another plan was chosen meanwhile). Must not raise."""


class SimulatedProvider(PaymentProvider):
    """The web app's test checkout. It charges nothing and only exists with DEBUG or a demo flag."""

    name = SIMULATED

    def start_checkout(self, payment, return_url):
        parts = urlsplit(return_url)
        # The page adds `compra` to `volver` itself when it sends the browser back.
        url = f"{parts.scheme}://{parts.netloc}/dev/checkout?compra={payment.pk}&volver={quote(return_url, safe='')}"
        return Checkout(url=url, reference=f'sim_{payment.pk.hex}')


def simulated_payments_allowed():
    """The simulated provider is on and allowed here: DEBUG, or a demo that asked for it."""
    return getattr(settings, 'PAYMENTS_PROVIDER', '') == SIMULATED and (
        settings.DEBUG or getattr(settings, 'PAYMENTS_ALLOW_SIMULATED', False)
    )


def may_simulate(user):
    """
    Who may settle a simulated payment: anyone with DEBUG; otherwise (a demo
    on production settings) staff and the verified emails of PAYMENTS_SIMULATE_ALLOWED_EMAILS.
    """
    if settings.DEBUG:
        return True
    if getattr(user, 'is_staff', False):
        return True
    allowed = {email.strip().lower() for email in getattr(settings, 'PAYMENTS_SIMULATE_ALLOWED_EMAILS', []) if email.strip()}
    # Only a verified address: anyone can type a listed one into their account.
    return bool(getattr(user, 'is_email_verified', False)) and bool(getattr(user, 'email', '')) and user.email.lower() in allowed


def get_payment_provider():
    """The configured provider, or None when there is none (or it is not allowed here)."""
    if simulated_payments_allowed():
        return SimulatedProvider()
    return None


def require_payment_provider():
    provider = get_payment_provider()
    if provider is None:
        raise PaymentsUnavailable()
    return provider
