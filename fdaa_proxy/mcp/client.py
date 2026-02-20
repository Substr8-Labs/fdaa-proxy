"""
MCP Client - Communication with MCP servers via stdio.

Implements the Model Context Protocol for connecting to MCP servers
that communicate via stdin/stdout JSON-RPC.
"""

import json
import subprocess
import threading
import queue
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, field


@dataclass
class MCPTool:
    """Representation of an MCP tool."""
    name: str
    description: str = ""
    input_schema: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


@dataclass
class MCPResult:
    """Result from an MCP tool call."""
    success: bool
    content: Optional[List[Dict[str, Any]]] = None
    error: Optional[str] = None
    is_error: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "content": self.content,
            "error": self.error,
            "isError": self.is_error,
        }


class MCPClient:
    """
    Client for MCP servers using stdio transport.
    
    Manages subprocess lifecycle and JSON-RPC communication.
    
    Usage:
        client = MCPClient(
            command="npx",
            args=["-y", "@anthropic/mcp-server-github"],
            env={"GITHUB_TOKEN": "..."}
        )
        client.connect()
        tools = client.list_tools()
        result = client.call_tool("get_file_contents", {"repo": "...", "path": "..."})
        client.disconnect()
    """
    
    def __init__(
        self,
        command: str,
        args: List[str] = None,
        env: Dict[str, str] = None,
        server_name: str = "unknown",
        timeout: float = 30.0,
    ):
        self.command = command
        self.args = args or []
        self.env = env or {}
        self.server_name = server_name
        self.timeout = timeout
        
        self._process: Optional[subprocess.Popen] = None
        self._request_id = 0
        self._responses: Dict[int, Any] = {}
        self._response_events: Dict[int, threading.Event] = {}
        self._reader_thread: Optional[threading.Thread] = None
        self._running = False
    
    def connect(self) -> Dict[str, Any]:
        """
        Start the MCP server process and initialize connection.
        
        Returns server info from initialize response.
        """
        import os
        
        # Merge environment
        process_env = os.environ.copy()
        process_env.update(self.env)
        
        # Start subprocess
        self._process = subprocess.Popen(
            [self.command] + self.args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=process_env,
            text=True,
            bufsize=1,  # Line buffered
        )
        
        self._running = True
        
        # Start reader thread
        self._reader_thread = threading.Thread(target=self._read_responses, daemon=True)
        self._reader_thread.start()
        
        # Send initialize request
        result = self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {
                "name": "fdaa-proxy",
                "version": "0.1.0"
            }
        })
        
        # Send initialized notification
        self._send_notification("notifications/initialized", {})
        
        return result
    
    def disconnect(self):
        """Stop the MCP server process."""
        self._running = False
        
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None
    
    def list_tools(self) -> List[MCPTool]:
        """Get list of available tools from the server."""
        result = self._send_request("tools/list", {})
        
        tools = []
        for tool_data in result.get("tools", []):
            tools.append(MCPTool(
                name=tool_data.get("name", ""),
                description=tool_data.get("description", ""),
                input_schema=tool_data.get("inputSchema", {}),
            ))
        
        return tools
    
    def call_tool(self, name: str, arguments: Dict[str, Any] = None) -> MCPResult:
        """
        Call a tool on the MCP server.
        
        Returns MCPResult with success/error status.
        """
        try:
            result = self._send_request("tools/call", {
                "name": name,
                "arguments": arguments or {},
            })
            
            return MCPResult(
                success=True,
                content=result.get("content", []),
                is_error=result.get("isError", False),
            )
        except Exception as e:
            return MCPResult(
                success=False,
                error=str(e),
                is_error=True,
            )
    
    def _send_request(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Send a JSON-RPC request and wait for response."""
        self._request_id += 1
        request_id = self._request_id
        
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        
        # Create event for response
        event = threading.Event()
        self._response_events[request_id] = event
        
        # Send request
        self._write_message(request)
        
        # Wait for response
        if not event.wait(timeout=self.timeout):
            raise TimeoutError(f"Request {method} timed out")
        
        response = self._responses.pop(request_id)
        del self._response_events[request_id]
        
        if "error" in response:
            error = response["error"]
            raise Exception(f"MCP error: {error.get('message', 'Unknown error')}")
        
        return response.get("result", {})
    
    def _send_notification(self, method: str, params: Dict[str, Any]):
        """Send a JSON-RPC notification (no response expected)."""
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        self._write_message(notification)
    
    def _write_message(self, message: Dict[str, Any]):
        """Write a JSON-RPC message to the server."""
        if not self._process or not self._process.stdin:
            raise RuntimeError("Not connected")
        
        content = json.dumps(message)
        self._process.stdin.write(content + "\n")
        self._process.stdin.flush()
    
    def _read_responses(self):
        """Background thread to read responses from the server."""
        while self._running and self._process:
            try:
                line = self._process.stdout.readline()
                if not line:
                    break
                
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                
                # Handle response
                if "id" in message:
                    request_id = message["id"]
                    self._responses[request_id] = message
                    if request_id in self._response_events:
                        self._response_events[request_id].set()
                
                # Handle notifications (log them for now)
                elif "method" in message:
                    pass  # Could emit events here
                    
            except Exception:
                if self._running:
                    continue
                break
    
    @property
    def is_connected(self) -> bool:
        """Check if client is connected to server."""
        return self._process is not None and self._process.poll() is None
    
    def __enter__(self):
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
        return False
