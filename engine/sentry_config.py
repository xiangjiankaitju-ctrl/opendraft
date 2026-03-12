#!/usr/bin/env python3
"""
Sentry configuration for error tracking.
"""

import os
import logging

logger = logging.getLogger(__name__)


def init_sentry():
    """Initialize Sentry for error tracking."""
    dsn = os.getenv('SENTRY_DSN')
    if not dsn:
        return False
    
    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import LoggingIntegration
        
        sentry_sdk.init(
            dsn=dsn,
            integrations=[
                LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
            ],
            traces_sample_rate=0.1,  # 10% of transactions
            environment=os.getenv('ENVIRONMENT', 'production'),
            release=os.getenv('APP_VERSION', 'unknown'),
        )
        logger.info("Sentry error tracking enabled")
        return True
    except ImportError:
        logger.warning("sentry-sdk not installed. Install with: pip install sentry-sdk")
        return False
    except Exception as e:
        logger.warning(f"Failed to initialize Sentry: {e}")
        return False

