"""ACC (Agent Capability Certificate) validation with ED25519 signatures."""

from .validator import ACCValidator, ACCToken, ACCValidationResult

# Crypto is optional (requires cryptography library)
try:
    from .crypto import (
        ACCKeyPair,
        ACCSigner,
        ACCVerifier,
        generate_keypair,
        sign_token,
        verify_token,
        HAS_CRYPTO,
    )
except ImportError:
    HAS_CRYPTO = False
    ACCKeyPair = None
    ACCSigner = None
    ACCVerifier = None
    generate_keypair = None
    sign_token = None
    verify_token = None

__all__ = [
    "ACCValidator",
    "ACCToken",
    "ACCValidationResult",
    "ACCKeyPair",
    "ACCSigner",
    "ACCVerifier",
    "generate_keypair",
    "sign_token",
    "verify_token",
    "HAS_CRYPTO",
]
