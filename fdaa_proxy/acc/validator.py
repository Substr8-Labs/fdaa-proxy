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
"""

import json
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any


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
    Validates ACC capability tokens.
    
    In production, this would:
    - Verify cryptographic signatures
    - Check against revocation lists
    - Validate issuer
    - Check expiration
    
    For now, this is a stub that validates token structure.
    """
    
    def __init__(
        self,
        issuer: str = None,
        public_key_path: str = None,
        dev_mode: bool = False,
    ):
        self.issuer = issuer
        self.public_key_path = public_key_path
        self.dev_mode = dev_mode
        self._public_key = None
        
        # Load public key if provided
        if public_key_path and not dev_mode:
            self._load_public_key()
    
    def _load_public_key(self):
        """Load public key for signature verification."""
        # TODO: Implement actual key loading
        pass
    
    def validate(self, token_str: str) -> ACCValidationResult:
        """
        Validate an ACC token string.
        
        Token can be:
        - Base64-encoded JSON
        - Plain JSON
        - JWT-style (header.payload.signature)
        """
        if self.dev_mode:
            # In dev mode, accept any well-formed token
            return self._validate_structure(token_str)
        
        # Parse token
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
        
        # TODO: Verify signature
        # if not self._verify_signature(token):
        #     return ACCValidationResult(valid=False, error="Invalid signature")
        
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
