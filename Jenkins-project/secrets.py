# src/secrets.py
"""
AWS SSM Parameter Store secrets loader.
All sensitive values (URLs, tokens, passwords) are stored as
SecureString parameters and fetched at runtime.
boto3 uses ~/.aws/credentials — no hardcoding needed.
"""

from __future__ import annotations
import logging
from functools import lru_cache
from typing import Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class SecretsManager:
    """Fetches and caches SSM SecureString parameters."""

    def __init__(self, region: str = "us-east-1"):
        self.ssm = boto3.client("ssm", region_name=region)
        self._cache: dict[str, str] = {}

    def get(self, param_name: str, required: bool = True) -> Optional[str]:
        """Fetch a parameter, using in-memory cache to avoid redundant API calls."""
        if param_name in self._cache:
            return self._cache[param_name]

        try:
            resp = self.ssm.get_parameter(Name=param_name, WithDecryption=True)
            value = resp["Parameter"]["Value"]
            self._cache[param_name] = value
            logger.debug("Loaded SSM param: %s", param_name)
            return value
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code == "ParameterNotFound":
                msg = f"SSM parameter not found: {param_name}"
                if required:
                    raise RuntimeError(msg) from exc
                logger.warning(msg)
                return None
            raise

    def get_batch(self, param_names: list[str]) -> dict[str, str]:
        """Fetch multiple parameters in one API call (max 10 per AWS limit)."""
        # Filter out already-cached
        to_fetch = [p for p in param_names if p not in self._cache]
        results = dict(self._cache)  # Start with cached

        for i in range(0, len(to_fetch), 10):
            batch = to_fetch[i:i+10]
            try:
                resp = self.ssm.get_parameters(Names=batch, WithDecryption=True)
                for param in resp["Parameters"]:
                    self._cache[param["Name"]] = param["Value"]
                    results[param["Name"]] = param["Value"]
                if resp.get("InvalidParameters"):
                    logger.warning("Invalid SSM params: %s", resp["InvalidParameters"])
            except ClientError as exc:
                logger.error("SSM batch fetch error: %s", exc)
                raise

        return results
