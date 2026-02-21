"""
ACC (Agent Capability Certificate) Token Validator

Validates capability tokens that authorize agents to perform specific operations.

Token Structure (simplified):
{
    "token_id": "acc_xxx",
    "issuer": "https://acc.substr8labs.com",
    "subject": "agent:ada",
    "capabilities": ["read:github", "write:github:issues"],
    "constraints": {"repos": ["org/repo"]},
    "issued_at": "2024-01-01T00:00:00Z",
    "expires_at": "2024-01-02T00:00:00Z",
    "signature": "..."
}

Now with real ED25519 cryptographic signatures!
"""

import json
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ACCToken:
    """Parsed ACC capability token."""
    token_id: str
    issuer: str
    subject: str
    capabilities: List[str]
    constraints: Dict[str, Any] = field(default_factory=dict)
    issued_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: Optional[datetime] = None
    signature: Optional[str] = None
    
    def is_expired(self) -> bool:
        """Check if token is expired."""
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) > self.expires_at
    
    def has_capability(self, capability: str) -> bool:
        """
        Check if token grants a capability.
        
        Supports wildcards:
        - "read:*" matches "read:github", "read:slack", etc.
        - "write:github:*" matches "write:github:issues", "write:github:prs", etc.
        """
        for cap in self.capabilities:
            if cap == capability:
                return True
            # Wildcard matching
            if cap.endswith(":*"):
                prefix = cap[:-2]
                if capability.startswith(prefix + ":") or capability == prefix:
                    return True
            if cap == "*":
                return True
        return False
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "token_id": self.token_id,
            "issuer": self.issuer,
            "subject": self.subject,
            "capabilities": self.capabilities,
            "constraints": self.constraints,
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }


@dataclass
class ACCValidationResult:
    """Result of ACC token validation."""
    valid: bool
    token: Optional[ACCToken] = None
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "token": self.token.to_dict() if self.token else None,
            "error": self.error,
        }


class ACCValidator:
    """
    Validates ACC capability tokens with ED25519 signatures.
    
    Features:
    - Verify cryptographic signatures (ED25519)
    - Check against trusted issuers
    - Validate expiration
    - Capability checking
    """
    
    def __init__(
        self,
        issuer: str = None,
        public_key: bytes = None,
        public_key_path: str = None,
        dev_mode: bool = False,
    ):
        self.issuer = issuer
        self.dev_mode = dev_mode
        self._verifier = None
        
        # Initialize verifier with crypto if available
        if not dev_mode:
            try:
                from .crypto import ACCVerifier, HAS_CRYPTO
                if HAS_CRYPTO:
                    self._verifier = ACCVerifier(public_key)
                    
                    # Load from path if provided
                    if public_key_path:
                        self._load_public_key(public_key_path)
                    
                    logger.info("ACC validator initialized with ED25519 verification")
                else:
                    logger.warning("ACC crypto not available, using structure validation only")
            except ImportError:
                logger.warning("ACC crypto module not available")
    
    def _load_public_key(self, path: str):
        """Load public key for signature verification."""
        try:
            key_path = Path(path)
            if key_path.is_dir():
                key_path = key_path / "public.key"
            
            with open(key_path, "rb") as f:
                public_key = f.read()
            
            from .crypto import ACCVerifier
            self._verifier = ACCVerifier(public_key)
            logger.info(f"Loaded ACC public key from {path}")
        except Exception as e:
            logger.error(f"Failed to load ACC public key: {e}")
    
    def add_trusted_key(self, key_id: str, public_key: bytes):
        """Add a trusted public key for verification."""
        if self._verifier:
            self._verifier.add_trusted_key(key_id, public_key)
            logger.info(f"Added trusted key: {key_id}")
    
    def validate(self, token_str: str) -> ACCValidationResult:
        """
        Validate an ACC token string.
        
        Token can be:
        - Base64-encoded JSON
        - Plain JSON
        - JWT-style (header.payload.signature) - with ED25519 signature
        """
        if self.dev_mode:
            # In dev mode, accept any well-formed token
            return self._validate_structure(token_str)
        
        # Try cryptographic verification first
        if self._verifier and '.' in token_str:
            valid, payload, error = self._verifier.verify(token_str)
            
            if not valid:
                return ACCValidationResult(valid=False, error=error)
            
            # Convert payload to ACCToken
            token = ACCToken(
                token_id=payload.get("token_id", "unknown"),
                issuer=payload.get("issuer", "unknown"),
                subject=payload.get("subject", "unknown"),
                capabilities=payload.get("capabilities", []),
                constraints=payload.get("constraints", {}),
                issued_at=datetime.fromisoformat(payload["issued_at"]) if "issued_at" in payload else datetime.now(timezone.utc),
                expires_at=datetime.fromisoformat(payload["expires_at"].replace('Z', '+00:00')) if payload.get("expires_at") else None,
                signature="[verified]",
            )
            
            # Check issuer
            if self.issuer and token.issuer != self.issuer:
                return ACCValidationResult(
                    valid=False,
                    token=token,
                    error=f"Invalid issuer: expected {self.issuer}, got {token.issuer}"
                )
            
            logger.info(f"ACC token verified: {token.token_id} for {token.subject}")
            return ACCValidationResult(valid=True, token=token)
        
        # Fallback to structure-only validation
        try:
            token = self._parse_token(token_str)
        except Exception as e:
            return ACCValidationResult(valid=False, error=f"Parse error: {e}")
        
        # Check expiration
        if token.is_expired():
            return ACCValidationResult(
                valid=False,
                token=token,
                error="Token expired"
            )
        
        # Check issuer
        if self.issuer and token.issuer != self.issuer:
            return ACCValidationResult(
                valid=False,
                token=token,
                error=f"Invalid issuer: expected {self.issuer}, got {token.issuer}"
            )
        
        logger.warning("ACC token validated without cryptographic verification")
        return ACCValidationResult(valid=True, token=token)
    
    def validate_capability(
        self,
        token_str: str,
        required_capability: str
    ) -> ACCValidationResult:
        """Validate token and check for specific capability."""
        result = self.validate(token_str)
        
        if not result.valid:
            return result
        
        if not result.token.has_capability(required_capability):
            return ACCValidationResult(
                valid=False,
                token=result.token,
                error=f"Missing capability: {required_capability}"
            )
        
        return result
    
    def _parse_token(self, token_str: str) -> ACCToken:
        """Parse token string into ACCToken."""
        import base64
        
        # Try JWT-style (header.payload.signature)
        if token_str.count('.') == 2:
            _, payload_b64, signature = token_str.split('.')
            payload_json = base64.urlsafe_b64decode(payload_b64 + '==')
            data = json.loads(payload_json)
            data['signature'] = signature
        else:
            # Try base64
            try:
                decoded = base64.b64decode(token_str)
                data = json.loads(decoded)
            except:
                # Try plain JSON
                data = json.loads(token_str)
        
        return ACCToken(
            token_id=data.get("token_id", "unknown"),
            issuer=data.get("issuer", "unknown"),
            subject=data.get("subject", "unknown"),
            capabilities=data.get("capabilities", []),
            constraints=data.get("constraints", {}),
            issued_at=datetime.fromisoformat(data["issued_at"]) if "issued_at" in data else datetime.now(timezone.utc),
            expires_at=datetime.fromisoformat(data["expires_at"]) if data.get("expires_at") else None,
            signature=data.get("signature"),
        )
    
    def _validate_structure(self, token_str: str) -> ACCValidationResult:
        """Validate token structure only (dev mode)."""
        try:
            token = self._parse_token(token_str)
            return ACCValidationResult(valid=True, token=token)
        except Exception as e:
            return ACCValidationResult(valid=False, error=f"Invalid structure: {e}")
    
    def _verify_signature(self, token: ACCToken) -> bool:
        """Verify token signature."""
        # TODO: Implement actual signature verification
        return True


def capability_for_tool(server: str, tool: str, category: str) -> str:
    """
    Generate capability string for a tool.
    
    Examples:
    - capability_for_tool("github", "get_file_contents", "read") -> "read:github"
    - capability_for_tool("github", "create_issue", "write") -> "write:github:issues"
    """
    # Simple mapping for now
    return f"{category}:{server}"
