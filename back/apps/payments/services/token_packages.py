"""
What tokens cost (GET /api/v1/token-packages/): three fixed packages with a
volume discount, and a free amount at the base price.

The price of a package comes from its tokens and its discount, never typed by
hand, so a package can never cost more per token than the free amount.
"""
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings

CUSTOM_MIN_TOKENS = 100
# Ceiling of one purchase, so a typo cannot open a checkout of thousands of dollars.
CUSTOM_MAX_TOKENS = 1_000_000

CENT = Decimal('0.01')


def token_price_usd():
    """
    Price of one token without discount (settings.TOKEN_PRICE_USD, the same
    reference GET /api/v1/pricing/ shows): a free amount pays exactly this.
    """
    return Decimal(settings.TOKEN_PRICE_USD)


@dataclass(frozen=True)
class TokenPackage:
    id: str
    tokens: int
    discount_rate: Decimal

    @property
    def price_usd(self):
        return (self.tokens * token_price_usd() * (1 - self.discount_rate)).quantize(CENT, rounding=ROUND_HALF_UP)


PACKAGES = (
    TokenPackage('pack-1000', 1000, Decimal('0.00')),
    TokenPackage('pack-5000', 5000, Decimal('0.10')),
    TokenPackage('pack-15000', 15000, Decimal('0.20')),
)


def find_package(package_id):
    return next((package for package in PACKAGES if package.id == package_id), None)


def custom_price_usd(tokens):
    return (tokens * token_price_usd()).quantize(CENT, rounding=ROUND_HALF_UP)


def token_packages_payload():
    """The body of GET /api/v1/token-packages/ (front contract `TokenPackages`)."""
    return {
        'packages': [
            {
                'id': package.id,
                'tokens': package.tokens,
                'price_usd': str(package.price_usd),
                'discount_rate': str(package.discount_rate),
            }
            for package in PACKAGES
        ],
        'custom_price_per_token_usd': str(token_price_usd()),
        'custom_min_tokens': CUSTOM_MIN_TOKENS,
        'local_currency': settings.PAYMENTS_LOCAL_CURRENCY,
        'usd_to_local_rate': settings.PAYMENTS_USD_TO_LOCAL_RATE,
    }
