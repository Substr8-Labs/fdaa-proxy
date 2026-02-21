"""
OpenClaw Gateway Protocol Types

WebSocket JSON protocol for OpenClaw Gateway communication.

Frame types:
- req: {type:"req", id, method, params}
- res: {type:"res", id, ok, payload|error}  
- event: {type:"event", event, payload, seq?, stateVersion?}
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any, Union
from enum import Enum
import json


class FrameType(Enum):
    REQUEST = "req"
    RESPONSE = "res"
    EVENT = "event"


class Role(Enum):
    OPERATOR = "operator"
    NODE = "node"


# Common operator scopes
OPERATOR_SCOPES = [
    "operator.read",
    "operator.write", 
    "operator.admin",
    "operator.approvals",
    "operator.pairing",
]


@dataclass
class Frame:
    """Base frame type."""
    type: FrameType
    raw: Dict[str, Any]
    
    @classmethod
    def parse(cls, data: str) -> "Frame":
        """Parse a JSON frame."""
        obj = json.loads(data)
        frame_type = FrameType(obj.get("type"))
        
        if frame_type == FrameType.REQUEST:
            return Request.from_dict(obj)
        elif frame_type == FrameType.RESPONSE:
            return Response.from_dict(obj)
        elif frame_type == FrameType.EVENT:
            return Event.from_dict(obj)
        
        return cls(type=frame_type, raw=obj)
    
    def to_json(self) -> str:
        """Serialize to JSON."""
        return json.dumps(self.raw)


@dataclass
class Request(Frame):
    """Request frame: {type:"req", id, method, params}"""
    id: str = ""
    method: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    
    @classmethod
    def from_dict(cls, obj: Dict[str, Any]) -> "Request":
        return cls(
            type=FrameType.REQUEST,
            raw=obj,
            id=obj.get("id", ""),
            method=obj.get("method", ""),
            params=obj.get("params", {}),
        )
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "req",
            "id": self.id,
            "method": self.method,
            "params": self.params,
        }
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict())


@dataclass
class Response(Frame):
    """Response frame: {type:"res", id, ok, payload|error}"""
    id: str = ""
    ok: bool = True
    payload: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None
    
    @classmethod
    def from_dict(cls, obj: Dict[str, Any]) -> "Response":
        return cls(
            type=FrameType.RESPONSE,
            raw=obj,
            id=obj.get("id", ""),
            ok=obj.get("ok", True),
            payload=obj.get("payload"),
            error=obj.get("error"),
        )
    
    @classmethod
    def error_response(cls, request_id: str, code: str, message: str) -> "Response":
        """Create an error response."""
        return cls(
            type=FrameType.RESPONSE,
            raw={},
            id=request_id,
            ok=False,
            error={"code": code, "message": message},
        )
    
    def to_dict(self) -> Dict[str, Any]:
        result = {
            "type": "res",
            "id": self.id,
            "ok": self.ok,
        }
        if self.ok and self.payload:
            result["payload"] = self.payload
        if not self.ok and self.error:
            result["error"] = self.error
        return result
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict())


@dataclass
class Event(Frame):
    """Event frame: {type:"event", event, payload, seq?, stateVersion?}"""
    event: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    seq: Optional[int] = None
    state_version: Optional[int] = None
    
    @classmethod
    def from_dict(cls, obj: Dict[str, Any]) -> "Event":
        return cls(
            type=FrameType.EVENT,
            raw=obj,
            event=obj.get("event", ""),
            payload=obj.get("payload", {}),
            seq=obj.get("seq"),
            state_version=obj.get("stateVersion"),
        )
    
    def to_dict(self) -> Dict[str, Any]:
        result = {
            "type": "event",
            "event": self.event,
            "payload": self.payload,
        }
        if self.seq is not None:
            result["seq"] = self.seq
        if self.state_version is not None:
            result["stateVersion"] = self.state_version
        return result
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict())


@dataclass
class ConnectParams:
    """Parameters for the connect request."""
    min_protocol: int = 3
    max_protocol: int = 3
    role: Role = Role.OPERATOR
    scopes: List[str] = field(default_factory=list)
    caps: List[str] = field(default_factory=list)
    commands: List[str] = field(default_factory=list)
    auth_token: Optional[str] = None
    acc_token: Optional[str] = None  # Our ACC token
    client_id: str = ""
    client_version: str = ""
    
    @classmethod
    def from_dict(cls, params: Dict[str, Any]) -> "ConnectParams":
        auth = params.get("auth", {})
        client = params.get("client", {})
        
        return cls(
            min_protocol=params.get("minProtocol", 3),
            max_protocol=params.get("maxProtocol", 3),
            role=Role(params.get("role", "operator")),
            scopes=params.get("scopes", []),
            caps=params.get("caps", []),
            commands=params.get("commands", []),
            auth_token=auth.get("token"),
            acc_token=auth.get("accToken"),  # Our extension
            client_id=client.get("id", ""),
            client_version=client.get("version", ""),
        )


# Methods that require specific capabilities
METHOD_CAPABILITIES = {
    # Read operations
    "status": ["operator.read"],
    "health": ["operator.read"],
    "sessions.list": ["operator.read"],
    "channels.status": ["operator.read"],
    
    # Write operations
    "chat": ["operator.write"],
    "agent": ["operator.write"],
    "sessions.send": ["operator.write"],
    "sessions.spawn": ["operator.write"],
    
    # Admin operations
    "config.apply": ["operator.admin"],
    "config.patch": ["operator.admin"],
    "gateway.restart": ["operator.admin"],
    "gateway.update": ["operator.admin"],
    
    # Approval operations
    "exec.approval.resolve": ["operator.approvals"],
    
    # Pairing operations
    "device.token.rotate": ["operator.pairing"],
    "device.token.revoke": ["operator.pairing"],
}


def get_required_scopes(method: str) -> List[str]:
    """Get required scopes for a method."""
    return METHOD_CAPABILITIES.get(method, [])
