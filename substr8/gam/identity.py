"""
GAM Identity - Cryptographic Identity Layer

Implements:
- Agent DIDs using did:key method
- BIP-32 HD key derivation from user master seed
- GPG integration for human signatures
- Signature verification for memory provenance
"""

import base64
import hashlib
import hmac
import json
import os
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

# Optional cryptography imports
try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.backends import default_backend
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False


# === Constants ===

# BIP-32 constants for Ed25519
ED25519_CURVE = b"ed25519 seed"
HARDENED_OFFSET = 0x80000000

# did:key multicodec prefix for Ed25519 public keys
ED25519_MULTICODEC = b'\xed\x01'


# === Data Types ===

@dataclass
class AgentIdentity:
    """An agent's cryptographic identity."""
    did: str  # did:key:z6Mk...
    public_key: bytes
    private_key: bytes  # Keep secure!
    derivation_path: str  # e.g., "m/44'/0'/0'/0"
    name: str
    
    def sign(self, message: bytes) -> bytes:
        """Sign a message with this identity."""
        if not HAS_CRYPTO:
            raise ImportError("cryptography package required for signing")
        
        private_key = ed25519.Ed25519PrivateKey.from_private_bytes(self.private_key)
        return private_key.sign(message)
    
    def verify(self, message: bytes, signature: bytes) -> bool:
        """Verify a signature against this identity."""
        if not HAS_CRYPTO:
            raise ImportError("cryptography package required for verification")
        
        try:
            public_key = ed25519.Ed25519PublicKey.from_public_bytes(self.public_key)
            public_key.verify(signature, message)
            return True
        except Exception:
            return False
    
    def to_dict(self) -> dict:
        """Serialize identity (excluding private key)."""
        return {
            "did": self.did,
            "public_key": base64.b64encode(self.public_key).decode(),
            "derivation_path": self.derivation_path,
            "name": self.name,
        }
    
    @classmethod
    def from_dict(cls, data: dict, private_key: Optional[bytes] = None) -> "AgentIdentity":
        """Deserialize identity."""
        return cls(
            did=data["did"],
            public_key=base64.b64decode(data["public_key"]),
            private_key=private_key or b"",
            derivation_path=data["derivation_path"],
            name=data["name"],
        )


@dataclass
class HumanIdentity:
    """A human's GPG identity."""
    key_id: str  # GPG key ID
    email: str
    name: str
    fingerprint: str
    
    def sign_commit(self, repo_path: Path, message: str) -> str:
        """Create a GPG-signed commit."""
        result = subprocess.run(
            ["git", "-C", str(repo_path), "commit", "-S", "-m", message],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"GPG signing failed: {result.stderr}")
        return result.stdout
    
    def verify_commit(self, repo_path: Path, commit_sha: str) -> bool:
        """Verify a GPG-signed commit."""
        result = subprocess.run(
            ["git", "-C", str(repo_path), "verify-commit", commit_sha],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0


# === BIP-32 HD Key Derivation ===

def _hmac_sha512(key: bytes, data: bytes) -> bytes:
    """HMAC-SHA512."""
    return hmac.new(key, data, hashlib.sha512).digest()


def _derive_master_key(seed: bytes) -> Tuple[bytes, bytes]:
    """Derive master key and chain code from seed."""
    h = _hmac_sha512(ED25519_CURVE, seed)
    return h[:32], h[32:]


def _derive_child_key(
    parent_key: bytes,
    parent_chain: bytes,
    index: int,
) -> Tuple[bytes, bytes]:
    """Derive child key using BIP-32 hardened derivation."""
    # Ed25519 only supports hardened derivation
    if index < HARDENED_OFFSET:
        index += HARDENED_OFFSET
    
    data = b'\x00' + parent_key + struct.pack('>I', index)
    h = _hmac_sha512(parent_chain, data)
    
    return h[:32], h[32:]


def derive_key_from_path(seed: bytes, path: str) -> Tuple[bytes, bytes]:
    """
    Derive a key from a BIP-32 path.
    
    Args:
        seed: 64-byte seed (e.g., from BIP-39 mnemonic)
        path: Derivation path like "m/44'/0'/0'/0"
    
    Returns:
        (private_key, chain_code)
    """
    # Parse path
    if not path.startswith("m"):
        raise ValueError("Path must start with 'm'")
    
    components = path.split("/")[1:]  # Skip 'm'
    
    # Derive master key
    key, chain = _derive_master_key(seed)
    
    # Derive each level
    for component in components:
        if component.endswith("'"):
            index = int(component[:-1]) + HARDENED_OFFSET
        else:
            index = int(component)
        
        key, chain = _derive_child_key(key, chain, index)
    
    return key, chain


def generate_seed_from_passphrase(passphrase: str, salt: str = "gam-agent-seed") -> bytes:
    """Generate a 64-byte seed from a passphrase using PBKDF2."""
    return hashlib.pbkdf2_hmac(
        'sha512',
        passphrase.encode(),
        salt.encode(),
        iterations=100000,
        dklen=64,
    )


# === DID Key Generation ===

def create_did_key(public_key: bytes) -> str:
    """
    Create a did:key from an Ed25519 public key.
    
    Format: did:key:z<base58btc-encoded-multicodec-public-key>
    """
    # Prepend multicodec prefix
    prefixed = ED25519_MULTICODEC + public_key
    
    # Base58btc encode (simplified implementation)
    did_suffix = _base58btc_encode(prefixed)
    
    return f"did:key:z{did_suffix}"


def _base58btc_encode(data: bytes) -> str:
    """Base58btc encoding (Bitcoin alphabet)."""
    ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    
    # Count leading zeros
    leading_zeros = 0
    for byte in data:
        if byte == 0:
            leading_zeros += 1
        else:
            break
    
    # Convert to integer
    num = int.from_bytes(data, 'big')
    
    # Encode
    result = []
    while num > 0:
        num, remainder = divmod(num, 58)
        result.append(ALPHABET[remainder])
    
    # Add leading '1's for each leading zero byte
    result.extend(['1'] * leading_zeros)
    
    return ''.join(reversed(result))


# === Identity Manager ===

class IdentityManager:
    """
    Manages agent and human identities for a GAM repository.
    """
    
    def __init__(self, gam_dir: Path):
        self.gam_dir = gam_dir
        self.identity_dir = gam_dir / "identity"
        self.identity_dir.mkdir(parents=True, exist_ok=True)
        
        self._master_seed: Optional[bytes] = None
        self._agents: dict[str, AgentIdentity] = {}
        self._human: Optional[HumanIdentity] = None
        
        self._load_identities()
    
    def _load_identities(self):
        """Load existing identities from disk."""
        agents_file = self.identity_dir / "agents.json"
        if agents_file.exists():
            with open(agents_file) as f:
                data = json.load(f)
                for name, agent_data in data.get("agents", {}).items():
                    self._agents[name] = AgentIdentity.from_dict(agent_data)
        
        human_file = self.identity_dir / "human.json"
        if human_file.exists():
            with open(human_file) as f:
                data = json.load(f)
                self._human = HumanIdentity(**data)
    
    def _save_agents(self):
        """Persist agent identities to disk."""
        agents_file = self.identity_dir / "agents.json"
        data = {
            "agents": {name: agent.to_dict() for name, agent in self._agents.items()}
        }
        with open(agents_file, "w") as f:
            json.dump(data, f, indent=2)
    
    def init_master_seed(self, passphrase: str) -> bytes:
        """
        Initialize master seed from passphrase.
        
        This should be derived from the user's GPG key or a secure passphrase.
        The seed is NOT stored — it must be provided each session.
        """
        self._master_seed = generate_seed_from_passphrase(passphrase)
        return self._master_seed
    
    def create_agent(self, name: str, index: int = 0) -> AgentIdentity:
        """
        Create a new agent identity derived from master seed.
        
        Args:
            name: Human-readable agent name (e.g., "ada", "grace")
            index: Agent index for key derivation
        
        Returns:
            AgentIdentity with DID and keys
        """
        if not HAS_CRYPTO:
            raise ImportError("cryptography package required")
        
        if not self._master_seed:
            raise RuntimeError("Master seed not initialized. Call init_master_seed() first.")
        
        # Derive agent key using BIP-32 path
        # m/44'/0'/0'/<agent_index>
        path = f"m/44'/0'/0'/{index}"
        private_key_bytes, _ = derive_key_from_path(self._master_seed, path)
        
        # Generate Ed25519 keypair
        private_key = ed25519.Ed25519PrivateKey.from_private_bytes(private_key_bytes)
        public_key = private_key.public_key()
        public_key_bytes = public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        
        # Create DID
        did = create_did_key(public_key_bytes)
        
        # Create identity
        agent = AgentIdentity(
            did=did,
            public_key=public_key_bytes,
            private_key=private_key_bytes,
            derivation_path=path,
            name=name,
        )
        
        self._agents[name] = agent
        self._save_agents()
        
        return agent
    
    def get_agent(self, name: str) -> Optional[AgentIdentity]:
        """Get an agent identity by name."""
        return self._agents.get(name)
    
    def list_agents(self) -> list[AgentIdentity]:
        """List all agent identities."""
        return list(self._agents.values())
    
    def register_human(
        self,
        key_id: str,
        email: str,
        name: str,
        fingerprint: str,
    ) -> HumanIdentity:
        """Register the human owner's GPG identity."""
        self._human = HumanIdentity(
            key_id=key_id,
            email=email,
            name=name,
            fingerprint=fingerprint,
        )
        
        human_file = self.identity_dir / "human.json"
        with open(human_file, "w") as f:
            json.dump({
                "key_id": key_id,
                "email": email,
                "name": name,
                "fingerprint": fingerprint,
            }, f, indent=2)
        
        return self._human
    
    def get_human(self) -> Optional[HumanIdentity]:
        """Get the human owner's identity."""
        return self._human
    
    def detect_gpg_key(self) -> Optional[dict]:
        """Detect available GPG keys."""
        try:
            result = subprocess.run(
                ["gpg", "--list-secret-keys", "--keyid-format", "long"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                # Parse first key (simplified)
                lines = result.stdout.split('\n')
                for i, line in enumerate(lines):
                    if line.startswith("sec"):
                        parts = line.split("/")
                        if len(parts) >= 2:
                            key_id = parts[1].split()[0]
                            # Look for uid line
                            for uid_line in lines[i:i+5]:
                                if uid_line.strip().startswith("uid"):
                                    # Extract name and email
                                    uid_parts = uid_line.split("]")[-1].strip()
                                    return {
                                        "key_id": key_id,
                                        "uid": uid_parts,
                                    }
        except Exception:
            pass
        return None


# === Signature Verification ===

def verify_agent_signature(
    did: str,
    message: bytes,
    signature: bytes,
    public_key: bytes,
) -> bool:
    """
    Verify an agent's signature on a message.
    
    Args:
        did: The agent's DID (did:key:z...)
        message: The original message
        signature: The signature to verify
        public_key: The agent's public key
    
    Returns:
        True if signature is valid
    """
    if not HAS_CRYPTO:
        raise ImportError("cryptography package required")
    
    # Verify DID matches public key
    expected_did = create_did_key(public_key)
    if did != expected_did:
        return False
    
    # Verify signature
    try:
        key = ed25519.Ed25519PublicKey.from_public_bytes(public_key)
        key.verify(signature, message)
        return True
    except Exception:
        return False


def verify_git_commit_signature(repo_path: Path, commit_sha: str) -> dict:
    """
    Verify a Git commit's GPG signature.
    
    Returns:
        {
            "signed": bool,
            "valid": bool,
            "signer": str or None,
            "key_id": str or None,
        }
    """
    result = subprocess.run(
        ["git", "-C", str(repo_path), "verify-commit", "--raw", commit_sha],
        capture_output=True,
        text=True,
    )
    
    if result.returncode != 0:
        return {"signed": False, "valid": False, "signer": None, "key_id": None}
    
    # Parse GPG output
    output = result.stderr
    valid = "[GNUPG:] GOODSIG" in output or "[GNUPG:] VALIDSIG" in output
    
    signer = None
    key_id = None
    for line in output.split('\n'):
        if "GOODSIG" in line:
            parts = line.split()
            if len(parts) >= 3:
                key_id = parts[2]
                signer = " ".join(parts[3:])
    
    return {
        "signed": True,
        "valid": valid,
        "signer": signer,
        "key_id": key_id,
    }
