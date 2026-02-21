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
                SELECT id, timestamp, event_type, gateway_id, entry_hash, prev_hash,
                       tool, arguments, result, error, persona, role, reasoning, acc_token_id
                FROM dct_entries
                ORDER BY timestamp DESC
                LIMIT 1000
            """)
            for row in cursor.fetchall():
                entry = dict(row)
                # Rename entry_hash to hash for consistency
                if "entry_hash" in entry:
                    entry["hash"] = entry.pop("entry_hash")
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
    """Verification UI home page with chain graph."""
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>FDAA Verification</title>
        <style>
            * { box-sizing: border-box; }
            body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 20px; background: #0d1117; color: #c9d1d9; }
            .container { max-width: 1400px; margin: 0 auto; }
            h1 { color: #58a6ff; border-bottom: 1px solid #30363d; padding-bottom: 10px; display: flex; align-items: center; gap: 10px; }
            h2 { color: #8b949e; margin-top: 20px; margin-bottom: 10px; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px; }
            .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
            .card { background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 16px; }
            .card.full { grid-column: 1 / -1; }
            .status { display: inline-flex; align-items: center; gap: 8px; padding: 6px 14px; border-radius: 20px; font-size: 13px; font-weight: 600; }
            .status.valid { background: #238636; color: white; }
            .status.invalid { background: #da3633; color: white; }
            .status-bar { display: flex; align-items: center; gap: 20px; margin-bottom: 16px; }
            .stat { text-align: center; padding: 8px 16px; background: #0d1117; border-radius: 6px; }
            .stat-value { font-size: 24px; font-weight: 700; color: #58a6ff; }
            .stat-label { font-size: 11px; color: #8b949e; text-transform: uppercase; }
            .hash { font-family: 'SF Mono', Monaco, monospace; font-size: 11px; color: #8b949e; }
            .trace-link { color: #58a6ff; text-decoration: none; }
            .trace-link:hover { text-decoration: underline; }
            table { width: 100%; border-collapse: collapse; font-size: 13px; }
            th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid #21262d; }
            th { color: #8b949e; font-weight: 600; font-size: 11px; text-transform: uppercase; }
            tr:hover { background: #1c2128; }
            .scroll-table { max-height: 300px; overflow-y: auto; }
            
            /* Chain Graph Styles */
            #chain-graph { height: 200px; overflow-x: auto; overflow-y: hidden; white-space: nowrap; padding: 20px 0; }
            .chain-node { display: inline-flex; flex-direction: column; align-items: center; margin: 0 5px; cursor: pointer; transition: transform 0.2s; }
            .chain-node:hover { transform: scale(1.1); }
            .chain-node.selected { transform: scale(1.15); }
            .node-circle { width: 40px; height: 40px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 11px; font-weight: 600; color: white; position: relative; }
            .node-circle.connect { background: #238636; }
            .node-circle.request { background: #1f6feb; }
            .node-circle.response { background: #8957e5; }
            .node-circle.error { background: #da3633; }
            .node-circle.default { background: #30363d; }
            .node-time { font-size: 10px; color: #8b949e; margin-top: 4px; }
            .node-hash { font-size: 9px; color: #484f58; font-family: monospace; }
            .chain-arrow { display: inline-block; color: #30363d; font-size: 20px; vertical-align: middle; margin: 0 -2px; }
            
            /* Detail Panel */
            #detail-panel { display: none; margin-top: 16px; padding: 16px; background: #0d1117; border-radius: 6px; border: 1px solid #30363d; }
            #detail-panel.active { display: block; }
            .detail-row { display: flex; margin: 8px 0; }
            .detail-label { width: 120px; color: #8b949e; font-size: 12px; }
            .detail-value { flex: 1; font-family: monospace; font-size: 12px; word-break: break-all; }
            .detail-json { background: #0d1117; padding: 12px; border-radius: 4px; font-size: 11px; max-height: 150px; overflow: auto; }
            
            /* Tabs */
            .tabs { display: flex; gap: 2px; margin-bottom: 16px; }
            .tab { padding: 8px 16px; background: #21262d; border: none; color: #8b949e; cursor: pointer; font-size: 13px; border-radius: 6px 6px 0 0; }
            .tab.active { background: #30363d; color: #c9d1d9; }
            .tab-content { display: none; }
            .tab-content.active { display: block; }
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🔐 FDAA Verification <span id="chain-status"></span></h1>
            
            <div class="status-bar">
                <div class="stat">
                    <div class="stat-value" id="stat-entries">-</div>
                    <div class="stat-label">Entries</div>
                </div>
                <div class="stat">
                    <div class="stat-value" id="stat-traces">-</div>
                    <div class="stat-label">Traces</div>
                </div>
                <div class="stat">
                    <div class="stat-value" id="stat-last">-</div>
                    <div class="stat-label">Last Event</div>
                </div>
            </div>
            
            <div class="card full">
                <h2>🔗 Hash Chain</h2>
                <div id="chain-graph">Loading chain...</div>
                <div id="detail-panel">
                    <div class="detail-row">
                        <span class="detail-label">Entry ID</span>
                        <span class="detail-value" id="detail-id">-</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-label">Timestamp</span>
                        <span class="detail-value" id="detail-time">-</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-label">Event Type</span>
                        <span class="detail-value" id="detail-event">-</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-label">Hash</span>
                        <span class="detail-value" id="detail-hash">-</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-label">Prev Hash</span>
                        <span class="detail-value" id="detail-prev">-</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-label">Trace ID</span>
                        <span class="detail-value" id="detail-trace">-</span>
                    </div>
                    <div class="detail-row">
                        <span class="detail-label">Arguments</span>
                        <pre class="detail-json" id="detail-args">-</pre>
                    </div>
                </div>
            </div>
            
            <div class="grid">
                <div class="card">
                    <h2>📊 Recent Traces</h2>
                    <div class="scroll-table" id="traces">Loading...</div>
                </div>
                
                <div class="card">
                    <h2>📝 Audit Log</h2>
                    <div class="scroll-table" id="entries">Loading...</div>
                </div>
            </div>
        </div>
        
        <script>
            const JAEGER_URL = '""" + JAEGER_URL + """';
            let chainData = [];
            let selectedNode = null;
            
            function getEventColor(eventType) {
                if (eventType.includes('connect')) return 'connect';
                if (eventType.includes('request')) return 'request';
                if (eventType.includes('response')) return 'response';
                if (eventType.includes('error') || eventType.includes('denied')) return 'error';
                return 'default';
            }
            
            function getEventIcon(eventType) {
                if (eventType.includes('connect')) return '🔌';
                if (eventType.includes('request')) return '→';
                if (eventType.includes('response')) return '←';
                if (eventType.includes('denied')) return '🚫';
                return '•';
            }
            
            function renderChain(entries) {
                chainData = entries;
                const sorted = [...entries].sort((a, b) => a.timestamp.localeCompare(b.timestamp));
                const recent = sorted.slice(-30); // Show last 30 entries
                
                let html = '';
                recent.forEach((entry, i) => {
                    const color = getEventColor(entry.event_type);
                    const icon = getEventIcon(entry.event_type);
                    const time = new Date(entry.timestamp).toLocaleTimeString();
                    const hash = entry.hash?.slice(0, 6) || '?';
                    
                    if (i > 0) {
                        html += '<span class="chain-arrow">→</span>';
                    }
                    
                    html += `
                        <div class="chain-node" data-index="${entries.indexOf(entry)}" onclick="selectNode(${entries.indexOf(entry)})">
                            <div class="node-circle ${color}">${icon}</div>
                            <div class="node-time">${time}</div>
                            <div class="node-hash">${hash}</div>
                        </div>
                    `;
                });
                
                document.getElementById('chain-graph').innerHTML = html || '<p style="color:#8b949e">No chain entries yet</p>';
                
                // Scroll to end
                const graph = document.getElementById('chain-graph');
                graph.scrollLeft = graph.scrollWidth;
            }
            
            function selectNode(index) {
                const entry = chainData[index];
                if (!entry) return;
                
                // Update selection
                document.querySelectorAll('.chain-node').forEach(n => n.classList.remove('selected'));
                document.querySelector(`.chain-node[data-index="${index}"]`)?.classList.add('selected');
                
                // Show detail panel
                const panel = document.getElementById('detail-panel');
                panel.classList.add('active');
                
                document.getElementById('detail-id').textContent = entry.id || '-';
                document.getElementById('detail-time').textContent = entry.timestamp || '-';
                document.getElementById('detail-event').textContent = entry.event_type || '-';
                document.getElementById('detail-hash').textContent = entry.hash || '-';
                document.getElementById('detail-prev').textContent = entry.prev_hash || '(genesis)';
                
                const traceId = entry.arguments?.trace_id;
                if (traceId) {
                    document.getElementById('detail-trace').innerHTML = 
                        `<a class="trace-link" href="${JAEGER_URL}/trace/${traceId}" target="_blank">${traceId}</a>`;
                } else {
                    document.getElementById('detail-trace').textContent = '-';
                }
                
                document.getElementById('detail-args').textContent = 
                    entry.arguments ? JSON.stringify(entry.arguments, null, 2) : '-';
            }
            
            async function loadStatus() {
                try {
                    const res = await fetch('/api/verify');
                    const data = await res.json();
                    document.getElementById('chain-status').innerHTML = `
                        <span class="status ${data.valid ? 'valid' : 'invalid'}">
                            ${data.valid ? '✓ Chain Valid' : '✗ Chain Broken'}
                        </span>
                    `;
                    document.getElementById('stat-entries').textContent = data.entries_checked;
                } catch (e) {
                    document.getElementById('chain-status').innerHTML = '<span class="status invalid">Error</span>';
                }
            }
            
            async function loadTraces() {
                try {
                    const res = await fetch('/api/traces?limit=10');
                    const data = await res.json();
                    document.getElementById('stat-traces').textContent = data.traces?.length || 0;
                    
                    if (data.traces && data.traces.length) {
                        document.getElementById('traces').innerHTML = `
                            <table>
                                <tr><th>Trace</th><th>Op</th><th>Duration</th><th></th></tr>
                                ${data.traces.map(t => `
                                    <tr>
                                        <td class="hash">${t.trace_id.slice(0,12)}...</td>
                                        <td>${t.operation}</td>
                                        <td>${t.duration_ms.toFixed(0)}ms</td>
                                        <td><a class="trace-link" href="${JAEGER_URL}/trace/${t.trace_id}" target="_blank">→</a></td>
                                    </tr>
                                `).join('')}
                            </table>
                        `;
                    } else {
                        document.getElementById('traces').innerHTML = '<p style="color:#8b949e">No traces</p>';
                    }
                } catch (e) {
                    document.getElementById('traces').innerHTML = '<p style="color:#da3633">Error loading</p>';
                }
            }
            
            async function loadEntries() {
                try {
                    const res = await fetch('/api/entries?limit=100');
                    const data = await res.json();
                    
                    if (data.entries && data.entries.length) {
                        // Update last event time
                        const lastTime = new Date(data.entries[0].timestamp);
                        const now = new Date();
                        const diffSec = Math.floor((now - lastTime) / 1000);
                        document.getElementById('stat-last').textContent = 
                            diffSec < 60 ? `${diffSec}s ago` : 
                            diffSec < 3600 ? `${Math.floor(diffSec/60)}m ago` : 
                            `${Math.floor(diffSec/3600)}h ago`;
                        
                        // Render chain graph
                        renderChain(data.entries);
                        
                        // Render table (last 20)
                        document.getElementById('entries').innerHTML = `
                            <table>
                                <tr><th>Time</th><th>Event</th><th>Hash</th></tr>
                                ${data.entries.slice(0, 20).map((e, i) => `
                                    <tr onclick="selectNode(${i})" style="cursor:pointer">
                                        <td>${new Date(e.timestamp).toLocaleTimeString()}</td>
                                        <td>${e.event_type}</td>
                                        <td class="hash">${e.hash?.slice(0,8) || '-'}...</td>
                                    </tr>
                                `).join('')}
                            </table>
                        `;
                    } else {
                        document.getElementById('entries').innerHTML = '<p style="color:#8b949e">No entries</p>';
                        document.getElementById('chain-graph').innerHTML = '<p style="color:#8b949e">No chain entries yet</p>';
                    }
                } catch (e) {
                    document.getElementById('entries').innerHTML = '<p style="color:#da3633">Error loading</p>';
                }
            }
            
            // Initial load
            loadStatus();
            loadTraces();
            loadEntries();
            
            // Refresh every 5s
            setInterval(() => { loadStatus(); loadTraces(); loadEntries(); }, 5000);
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
