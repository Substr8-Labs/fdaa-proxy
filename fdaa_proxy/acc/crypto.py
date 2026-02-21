"""
ACC Cryptographic Operations

ED25519 signing and verification for ACC tokens.
"""

import base64
import json
import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Tuple
from pathlib import Path

# Try to import cryptography library
try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )
    from cryptography.hazmat.primitives import serialization
    from cryptography.exceptions import InvalidSignature
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False


@dataclass
class ACCKeyPair:
    """ED25519 key pair for ACC signing."""
    
    private_key: bytes  # 32 bytes seed
    public_key: bytes   # 32 bytes
    key_id: str         # Unique identifier for this key
    
    @classmethod
    def generate(cls, key_id: Optional[str] = None) -> "ACCKeyPair":
        """Generate a new ED25519 key pair."""
        if not HAS_CRYPTO:
            raise ImportError("cryptography library required for key generation")
        
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        
        # Get raw bytes
        private_bytes = private_key.private_bytes_raw()
        public_bytes = public_key.public_bytes_raw()
        
        # Generate key ID from public key hash
        if key_id is None:
            key_id = "acc_" + hashlib.sha256(public_bytes).hexdigest()[:16]
        
        return cls(
            private_key=private_bytes,
            public_key=public_bytes,
            key_id=key_id,
        )
    
    def save(self, path: Path):
        """Save key pair to files."""
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        
        # Save private key
        with open(path / "private.key", "wb") as f:
            f.write(self.private_key)
        os.chmod(path / "private.key", 0o600)
        
        # Save public key
        with open(path / "public.key", "wb") as f:
            f.write(self.public_key)
        
        # Save key ID
        with open(path / "key_id.txt", "w") as f:
            f.write(self.key_id)
    
    @classmethod
    def load(cls, path: Path) -> "ACCKeyPair":
        """Load key pair from files."""
        path = Path(path)
        
        with open(path / "private.key", "rb") as f:
            private_key = f.read()
        
        with open(path / "public.key", "rb") as f:
            public_key = f.read()
        
        with open(path / "key_id.txt", "r") as f:
            key_id = f.read().strip()
        
        return cls(
            private_key=private_key,
            public_key=public_key,
            key_id=key_id,
        )
    
    @property
    def public_key_b64(self) -> str:
        """Base64-encoded public key."""
        return base64.urlsafe_b64encode(self.public_key).decode()
    
    @property
    def did(self) -> str:
        """DID-style identifier for this key."""
        return f"did:key:z{self.public_key_b64}"


class ACCSigner:
    """Signs ACC tokens with ED25519."""
    
    def __init__(self, key_pair: ACCKeyPair):
        if not HAS_CRYPTO:
            raise ImportError("cryptography library required for signing")
        
        self.key_pair = key_pair
        self._private_key = Ed25519PrivateKey.from_private_bytes(key_pair.private_key)
    
    def sign(self, payload: dict) -> str:
        """
        Sign a token payload.
        
        Returns a complete signed token string (JWT-like format).
        """
        # Add metadata
        payload = payload.copy()
        payload["iat"] = datetime.now(timezone.utc).isoformat()
        payload["kid"] = self.key_pair.key_id
        
        # Create header
        header = {
            "alg": "EdDSA",
            "typ": "ACC",
            "kid": self.key_pair.key_id,
        }
        
        # Encode parts
        header_b64 = base64.urlsafe_b64encode(
            json.dumps(header).encode()
        ).rstrip(b'=').decode()
        
        payload_b64 = base64.urlsafe_b64encode(
            json.dumps(payload).encode()
        ).rstrip(b'=').decode()
        
        # Sign
        message = f"{header_b64}.{payload_b64}".encode()
        signature = self._private_key.sign(message)
        signature_b64 = base64.urlsafe_b64encode(signature).rstrip(b'=').decode()
        
        return f"{header_b64}.{payload_b64}.{signature_b64}"
    
    def create_token(
        self,
        subject: str,
        capabilities: list,
        issuer: str = "https://acc.substr8labs.com",
        expires_in_seconds: int = 3600,
        constraints: Optional[dict] = None,
    ) -> str:
        """Create a signed ACC token."""
        import uuid
        
        now = datetime.now(timezone.utc)
        
        payload = {
            "token_id": f"acc_{uuid.uuid4().hex[:16]}",
            "issuer": issuer,
            "subject": subject,
            "capabilities": capabilities,
            "constraints": constraints or {},
            "issued_at": now.isoformat(),
            "expires_at": (
                now.replace(microsecond=0) + 
                __import__('datetime').timedelta(seconds=expires_in_seconds)
            ).isoformat(),
        }
        
        return self.sign(payload)


class ACCVerifier:
    """Verifies ACC token signatures."""
    
    def __init__(self, public_key: Optional[bytes] = None):
        if not HAS_CRYPTO:
            raise ImportError("cryptography library required for verification")
        
        self._public_key = None
        self._trusted_keys: dict[str, Ed25519PublicKey] = {}
        
        if public_key:
            self._public_key = Ed25519PublicKey.from_public_bytes(public_key)
    
    def add_trusted_key(self, key_id: str, public_key: bytes):
        """Add a trusted public key."""
        self._trusted_keys[key_id] = Ed25519PublicKey.from_public_bytes(public_key)
    
    def verify(self, token_str: str) -> Tuple[bool, Optional[dict], Optional[str]]:
        """
        Verify a signed token.
        
        Returns: (valid, payload, error)
        """
        try:
            parts = token_str.split('.')
            if len(parts) != 3:
                return False, None, "Invalid token format"
            
            header_b64, payload_b64, signature_b64 = parts
            
            # Decode header
            header = json.loads(
                base64.urlsafe_b64decode(header_b64 + '==')
            )
            
            # Decode payload
            payload = json.loads(
                base64.urlsafe_b64decode(payload_b64 + '==')
            )
            
            # Get public key
            key_id = header.get("kid")
            if key_id and key_id in self._trusted_keys:
                public_key = self._trusted_keys[key_id]
            elif self._public_key:
                public_key = self._public_key
            else:
                return False, payload, f"Unknown key: {key_id}"
            
            # Decode signature
            signature = base64.urlsafe_b64decode(signature_b64 + '==')
            
            # Verify
            message = f"{header_b64}.{payload_b64}".encode()
            
            try:
                public_key.verify(signature, message)
            except InvalidSignature:
                return False, payload, "Invalid signature"
            
            # Check expiration
            if payload.get("expires_at"):
                expires_at = datetime.fromisoformat(payload["expires_at"].replace('Z', '+00:00'))
                if datetime.now(timezone.utc) > expires_at:
                    return False, payload, "Token expired"
            
            return True, payload, None
            
        except Exception as e:
            return False, None, f"Verification error: {e}"


# === Convenience Functions ===

def generate_keypair(path: Optional[Path] = None) -> ACCKeyPair:
    """Generate and optionally save a new key pair."""
    key_pair = ACCKeyPair.generate()
    if path:
        key_pair.save(path)
    return key_pair


def sign_token(
    key_pair: ACCKeyPair,
    subject: str,
    capabilities: list,
    **kwargs
) -> str:
    """Create a signed token."""
    signer = ACCSigner(key_pair)
    return signer.create_token(subject, capabilities, **kwargs)


def verify_token(
    token_str: str,
    public_key: bytes,
) -> Tuple[bool, Optional[dict], Optional[str]]:
    """Verify a token."""
    verifier = ACCVerifier(public_key)
    return verifier.verify(token_str)
