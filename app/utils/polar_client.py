from functools import lru_cache
from typing import Optional

from app.core.config import polar_settings


@lru_cache(maxsize=1)
def get_polar():
    """
    Returns a configured Polar SDK client.

    Imported lazily so that environments without the SDK installed (e.g. CI lint)
    don't fail at import time. Cached so we reuse a single underlying httpx client.
    """
    if not polar_settings.access_token:
        raise RuntimeError(
            "POLAR_ACCESS_TOKEN is not configured. Set it in your environment to use Polar."
        )
    from polar_sdk import Polar

    return Polar(
        server=polar_settings.server,
        access_token=polar_settings.access_token,
    )


def get_product_id_for_slug(slug: str) -> Optional[str]:
    """Map a pricing-plan slug to its configured Polar product UUID."""
    return polar_settings.product_ids.get(slug) or None
