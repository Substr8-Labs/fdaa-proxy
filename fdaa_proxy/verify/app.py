"""
Verification UI - FastAPI Application

Provides:
- DCT chain browser
- Hash verification
- Trace correlation (Jaeger link)
- Provenance explorer
"""

import os
import json
import httpx
from datetime import datetime
from typing import Optional, List, Dict, Any
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Initialize app
app = FastAPI(
    title="FDAA Verification",
    description="Audit chain browser and verification",
    version="0.1.0",
)


# === Configuration ===

JAEGER_URL = os.getenv("JAEGER_URL", "http://localhost:16686")
DCT_STORAGE_PATH = os.getenv("DCT_STORAGE_PATH", "/data/dct")


# === Models ===

class DCTEntry(BaseModel):
    """A DCT audit entry."""
    id: str
    timestamp: str
    event_type: str
    gateway_id: str
    hash: str
    prev_hash: Optional[str] = None
    arguments: Optional[Dict[str, Any]] = None
    trace_id: Optional[str] = None


class VerificationResult(BaseModel):
    """Result of chain verification."""
    valid: bool
    entries_checked: int
    errors: List[str]
    first_entry: Optional[str] = None
    last_entry: Optional[str] = None


class TraceInfo(BaseModel):
    """Trace information from Jaeger."""
    trace_id: str
    service: str
    operation: str
    duration_ms: float
    spans: int
    start_time: str


# === DCT Storage ===

def load_dct_entries() -> List[Dict[str, Any]]:
    """Load DCT entries from storage (SQLite or JSONL)."""
    import sqlite3
    
    entries = []
    storage_path = Path(DCT_STORAGE_PATH)
    
    if not storage_path.exists():
        return entries
    
    # Try SQLite first
    db_path = storage_path / "audit.db"
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, timestamp, event_type, gateway_id, entry_hash as hash, prev_hash,
                       tool, arguments, result, error, persona, role, reasoning, acc_token_id
                FROM dct_entries
                ORDER BY timestamp DESC
                LIMIT 1000
            """)
            for row in cursor.fetchall():
                entry = dict(row)
                # Parse JSON fields
                if entry.get("arguments"):
                    try:
                        entry["arguments"] = json.loads(entry["arguments"])
                    except:
                        pass
                entries.append(entry)
            conn.close()
            return entries
        except Exception as e:
            print(f"SQLite error: {e}")
    
    # Fallback to JSONL
    jsonl_path = storage_path / "audit.jsonl"
    if jsonl_path.exists():
        with open(jsonl_path, "r") as f:
            for line in f:
                if line.strip():
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    
    return entries


def verify_chain(entries: List[Dict[str, Any]]) -> VerificationResult:
    """Verify the hash chain integrity."""
    import hashlib
    
    errors = []
    
    if not entries:
        return VerificationResult(
            valid=True,
            entries_checked=0,
            errors=["No entries to verify"],
        )
    
    # Sort by timestamp
    sorted_entries = sorted(entries, key=lambda x: x.get("timestamp", ""))
    
    # Verify chain
    prev_hash = None
    for i, entry in enumerate(sorted_entries):
        entry_hash = entry.get("hash", "")
        entry_prev = entry.get("prev_hash")
        
        # First entry should have no prev_hash
        if i == 0 and entry_prev is not None:
            errors.append(f"Entry {entry.get('id')}: First entry has prev_hash")
        
        # Subsequent entries should reference previous hash
        if i > 0 and entry_prev != prev_hash:
            errors.append(
                f"Entry {entry.get('id')}: prev_hash mismatch. "
                f"Expected {prev_hash[:16]}..., got {entry_prev[:16] if entry_prev else 'None'}..."
            )
        
        prev_hash = entry_hash
    
    return VerificationResult(
        valid=len(errors) == 0,
        entries_checked=len(sorted_entries),
        errors=errors,
        first_entry=sorted_entries[0].get("id") if sorted_entries else None,
        last_entry=sorted_entries[-1].get("id") if sorted_entries else None,
    )


# === API Endpoints ===

@app.get("/", response_class=HTMLResponse)
async def home():
    """Verification UI home page."""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>FDAA Verification</title>
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 20px; background: #0d1117; color: #c9d1d9; }
            .container { max-width: 1200px; margin: 0 auto; }
            h1 { color: #58a6ff; border-bottom: 1px solid #30363d; padding-bottom: 10px; }
            h2 { color: #8b949e; margin-top: 30px; }
            .card { background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 16px; margin: 10px 0; }
            .status { display: inline-block; padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: 600; }
            .status.valid { background: #238636; color: white; }
            .status.invalid { background: #da3633; color: white; }
            .entry { border-left: 3px solid #58a6ff; padding-left: 12px; margin: 10px 0; }
            .hash { font-family: monospace; font-size: 12px; color: #8b949e; }
            .trace-link { color: #58a6ff; text-decoration: none; }
            .trace-link:hover { text-decoration: underline; }
            table { width: 100%; border-collapse: collapse; }
            th, td { text-align: left; padding: 8px 12px; border-bottom: 1px solid #30363d; }
            th { color: #8b949e; font-weight: 600; }
            .btn { background: #238636; color: white; border: none; padding: 8px 16px; border-radius: 6px; cursor: pointer; }
            .btn:hover { background: #2ea043; }
            #entries { max-height: 500px; overflow-y: auto; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🔐 FDAA Verification</h1>
            
            <div class="card">
                <h2>Chain Status</h2>
                <div id="status">Loading...</div>
            </div>
            
            <div class="card">
                <h2>Recent Traces</h2>
                <div id="traces">Loading...</div>
            </div>
            
            <div class="card">
                <h2>Audit Entries</h2>
                <div id="entries">Loading...</div>
            </div>
        </div>
        
        <script>
            const JAEGER_URL = '""" + JAEGER_URL + """';
            
            async function loadStatus() {
                try {
                    const res = await fetch('/api/verify');
                    const data = await res.json();
                    document.getElementById('status').innerHTML = `
                        <span class="status ${data.valid ? 'valid' : 'invalid'}">
                            ${data.valid ? '✓ VALID' : '✗ INVALID'}
                        </span>
                        <p>Entries checked: ${data.entries_checked}</p>
                        ${data.errors.length ? '<p style="color:#da3633">Errors: ' + data.errors.join(', ') + '</p>' : ''}
                    `;
                } catch (e) {
                    document.getElementById('status').innerHTML = '<p>Error loading status</p>';
                }
            }
            
            async function loadTraces() {
                try {
                    const res = await fetch('/api/traces?limit=10');
                    const data = await res.json();
                    if (data.traces && data.traces.length) {
                        document.getElementById('traces').innerHTML = `
                            <table>
                                <tr><th>Trace ID</th><th>Operation</th><th>Duration</th><th>Spans</th><th>Link</th></tr>
                                ${data.traces.map(t => `
                                    <tr>
                                        <td class="hash">${t.trace_id.slice(0,16)}...</td>
                                        <td>${t.operation}</td>
                                        <td>${t.duration_ms.toFixed(1)}ms</td>
                                        <td>${t.spans}</td>
                                        <td><a class="trace-link" href="${JAEGER_URL}/trace/${t.trace_id}" target="_blank">View →</a></td>
                                    </tr>
                                `).join('')}
                            </table>
                        `;
                    } else {
                        document.getElementById('traces').innerHTML = '<p>No traces found</p>';
                    }
                } catch (e) {
                    document.getElementById('traces').innerHTML = '<p>Error loading traces</p>';
                }
            }
            
            async function loadEntries() {
                try {
                    const res = await fetch('/api/entries?limit=20');
                    const data = await res.json();
                    if (data.entries && data.entries.length) {
                        document.getElementById('entries').innerHTML = `
                            <table>
                                <tr><th>Time</th><th>Event</th><th>Client</th><th>Hash</th><th>Trace</th></tr>
                                ${data.entries.map(e => `
                                    <tr>
                                        <td>${new Date(e.timestamp).toLocaleTimeString()}</td>
                                        <td>${e.event_type}</td>
                                        <td>${e.arguments?.client_id || '-'}</td>
                                        <td class="hash">${e.hash?.slice(0,12) || '-'}...</td>
                                        <td>${e.arguments?.trace_id ? 
                                            `<a class="trace-link" href="${JAEGER_URL}/trace/${e.arguments.trace_id}" target="_blank">${e.arguments.trace_id.slice(0,8)}...</a>` 
                                            : '-'}</td>
                                    </tr>
                                `).join('')}
                            </table>
                        `;
                    } else {
                        document.getElementById('entries').innerHTML = '<p>No entries yet</p>';
                    }
                } catch (e) {
                    document.getElementById('entries').innerHTML = '<p>Error loading entries</p>';
                }
            }
            
            loadStatus();
            loadTraces();
            loadEntries();
            
            // Refresh every 10s
            setInterval(() => { loadStatus(); loadTraces(); loadEntries(); }, 10000);
        </script>
    </body>
    </html>
    """


@app.get("/api/verify")
async def verify():
    """Verify the DCT chain integrity."""
    entries = load_dct_entries()
    return verify_chain(entries)


@app.get("/api/entries")
async def list_entries(
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    event_type: Optional[str] = None,
    trace_id: Optional[str] = None,
):
    """List DCT entries with optional filtering."""
    entries = load_dct_entries()
    
    # Filter
    if event_type:
        entries = [e for e in entries if e.get("event_type") == event_type]
    if trace_id:
        entries = [e for e in entries if e.get("arguments", {}).get("trace_id") == trace_id]
    
    # Sort by timestamp descending
    entries = sorted(entries, key=lambda x: x.get("timestamp", ""), reverse=True)
    
    # Paginate
    paginated = entries[offset:offset + limit]
    
    return {
        "entries": paginated,
        "total": len(entries),
        "limit": limit,
        "offset": offset,
    }


@app.get("/api/entries/{entry_id}")
async def get_entry(entry_id: str):
    """Get a specific DCT entry."""
    entries = load_dct_entries()
    
    for entry in entries:
        if entry.get("id") == entry_id:
            return entry
    
    raise HTTPException(status_code=404, detail="Entry not found")


@app.get("/api/traces")
async def list_traces(
    service: str = Query("fdaa-proxy"),
    limit: int = Query(20, ge=1, le=100),
):
    """Fetch recent traces from Jaeger."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{JAEGER_URL}/api/traces",
                params={"service": service, "limit": limit},
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
            
            traces = []
            for trace in data.get("data", []):
                if trace.get("spans"):
                    first_span = trace["spans"][0]
                    traces.append({
                        "trace_id": trace["traceID"],
                        "service": service,
                        "operation": first_span.get("operationName", "unknown"),
                        "duration_ms": first_span.get("duration", 0) / 1000,
                        "spans": len(trace["spans"]),
                        "start_time": datetime.fromtimestamp(
                            first_span.get("startTime", 0) / 1_000_000
                        ).isoformat(),
                    })
            
            return {"traces": traces}
            
    except Exception as e:
        return {"traces": [], "error": str(e)}


@app.get("/api/trace/{trace_id}")
async def get_trace(trace_id: str):
    """Get full trace details from Jaeger."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{JAEGER_URL}/api/traces/{trace_id}",
                timeout=10.0,
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=e.response.status_code, detail="Trace not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/correlate/{trace_id}")
async def correlate_trace(trace_id: str):
    """
    Correlate a trace with DCT entries.
    
    Returns all DCT entries that reference this trace.
    """
    entries = load_dct_entries()
    
    # Find entries with this trace_id
    correlated = [
        e for e in entries
        if e.get("arguments", {}).get("trace_id") == trace_id
    ]
    
    return {
        "trace_id": trace_id,
        "entries": correlated,
        "count": len(correlated),
    }


# === Server ===

def create_app():
    """Create the FastAPI application."""
    return app


def run_server(host: str = "0.0.0.0", port: int = 8080):
    """Run the verification server."""
    import uvicorn
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run_server()
