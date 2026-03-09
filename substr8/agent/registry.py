"""Agent registry client."""

import os
import requests
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

from .hash import IdentityHash


DEFAULT_REGISTRY_URL = os.environ.get(
    "AGENT_REGISTRY_URL", 
    "http://localhost:8099"
)


@dataclass
class RegistrationResult:
    """Result of agent registration."""
    success: bool
    agent: str
    version: str
    identity_hash: str
    registered_at: Optional[str] = None
    error: Optional[str] = None


@dataclass
class VerificationResult:
    """Result of hash verification."""
    verified: bool
    agent_name: Optional[str] = None
    version: Optional[str] = None
    registered_at: Optional[str] = None
    status: Optional[str] = None


class RegistryClient:
    """Client for the Agent Registry service."""
    
    def __init__(self, base_url: str = DEFAULT_REGISTRY_URL):
        self.base_url = base_url.rstrip("/")
    
    def health(self) -> Dict[str, Any]:
        """Check registry health."""
        resp = requests.get(f"{self.base_url}/health", timeout=5)
        resp.raise_for_status()
        return resp.json()
    
    def register(
        self,
        identity: IdentityHash,
        framework: str = "custom",
        source_type: Optional[str] = None,
        source_uri: Optional[str] = None,
        source_ref: Optional[str] = None,
        publisher_name: Optional[str] = None,
        publisher_org: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Dict] = None
    ) -> RegistrationResult:
        """Register an agent with the registry."""
        payload = {
            "name": identity.agent_name,
            "version": identity.agent_version,
            "identity_hash": identity.identity_hash,
            "manifest_hash": identity.manifest_hash,
            "framework": framework,
            "files": [
                {"path": f.path, "hash": f.hash, "size": f.size}
                for f in identity.files
            ]
        }
        
        if source_type:
            payload["source"] = {
                "type": source_type,
                "uri": source_uri,
                "ref": source_ref
            }
        
        if publisher_name or publisher_org:
            payload["publisher"] = {
                "name": publisher_name,
                "org": publisher_org
            }
        
        if description:
            payload["description"] = description
        
        if metadata:
            payload["metadata"] = metadata
        
        try:
            resp = requests.post(
                f"{self.base_url}/api/v1/agents",
                json=payload,
                timeout=30
            )
            
            if resp.status_code == 201:
                data = resp.json()
                return RegistrationResult(
                    success=True,
                    agent=data["agent"],
                    version=data["version"],
                    identity_hash=data["identity_hash"],
                    registered_at=data["registered_at"]
                )
            elif resp.status_code == 409:
                return RegistrationResult(
                    success=False,
                    agent=identity.agent_name,
                    version=identity.agent_version,
                    identity_hash=identity.identity_hash,
                    error="Version already registered"
                )
            else:
                return RegistrationResult(
                    success=False,
                    agent=identity.agent_name,
                    version=identity.agent_version,
                    identity_hash=identity.identity_hash,
                    error=f"Registration failed: {resp.status_code} - {resp.text}"
                )
                
        except requests.RequestException as e:
            return RegistrationResult(
                success=False,
                agent=identity.agent_name,
                version=identity.agent_version,
                identity_hash=identity.identity_hash,
                error=f"Connection error: {e}"
            )
    
    def verify(self, identity_hash: str) -> VerificationResult:
        """Verify an identity hash against the registry."""
        try:
            resp = requests.post(
                f"{self.base_url}/api/v1/verify",
                json={"identity_hash": identity_hash},
                timeout=10
            )
            resp.raise_for_status()
            data = resp.json()
            
            return VerificationResult(
                verified=data["verified"],
                agent_name=data.get("agent_name"),
                version=data.get("version"),
                registered_at=data.get("registered_at"),
                status=data.get("status")
            )
            
        except requests.RequestException as e:
            return VerificationResult(verified=False)
    
    def get_agent(self, name: str) -> Optional[Dict]:
        """Get agent details."""
        try:
            resp = requests.get(
                f"{self.base_url}/api/v1/agents/{name}",
                timeout=10
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException:
            return None
    
    def list_agents(
        self, 
        org: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict]:
        """List registered agents."""
        params = {"limit": limit}
        if org:
            params["org"] = org
        
        try:
            resp = requests.get(
                f"{self.base_url}/api/v1/agents",
                params=params,
                timeout=10
            )
            resp.raise_for_status()
            return resp.json().get("agents", [])
        except requests.RequestException:
            return []
    
    def stats(self) -> Dict[str, int]:
        """Get registry statistics."""
        try:
            resp = requests.get(
                f"{self.base_url}/api/v1/stats",
                timeout=5
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException:
            return {"agents": 0, "versions": 0, "organizations": 0}
