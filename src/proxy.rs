use anyhow::Result;
use axum::{
    body::Body,
    extract::{Request, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::get,
    Json, Router,
};
use reqwest::Client;
use serde::Deserialize;
use serde_json::{json, Value};
use std::sync::Arc;
use tokio::net::TcpListener;
use tower_http::trace::TraceLayer;
use tracing::{error, info, warn};

use crate::dct::DctEnforcer;

/// Shared state for the proxy
pub struct ProxyState {
    /// HTTP client for forwarding requests
    client: Client,
    /// OpenClaw upstream URL
    upstream_url: String,
    /// DCT enforcer
    pub enforcer: DctEnforcer,
    /// Whether to require DCT tokens for tool calls
    pub require_token: bool,
}

/// Start the DCT proxy server
pub async fn start_proxy(
    port: u16,
    openclaw_host: &str,
    openclaw_port: u16, 
    authority_key: Option<String>,
    require_token: bool,
) -> Result<()> {
    let upstream_url = format!("http://{}:{}", openclaw_host, openclaw_port);
    
    // Initialize DCT enforcer
    let enforcer = DctEnforcer::new(authority_key)?;
    info!("DCT Enforcer initialized");
    info!("  Authority ID: {}", enforcer.authority_id());
    info!("  Public Key: {}...", &enforcer.public_key_hex()[..16]);
    info!("  Require Token: {}", require_token);
    info!("  Upstream: {}", upstream_url);
    
    let state = Arc::new(ProxyState {
        client: Client::new(),
        upstream_url,
        enforcer,
        require_token,
    });

    let app = Router::new()
        // Health check
        .route("/health", get(health_check))
        // DCT-specific endpoints
        .route("/dct/token", get(get_root_token))
        .route("/dct/delegate", axum::routing::post(delegate_token))
        .route("/dct/verify", axum::routing::post(verify_capability))
        .route("/dct/audit", get(get_audit_log))
        // Proxy all other requests to OpenClaw
        .fallback(proxy_handler)
        .layer(TraceLayer::new_for_http())
        .with_state(state);

    let addr = format!("0.0.0.0:{}", port);
    info!("Substr8 Gateway listening on {}", addr);
    
    let listener = TcpListener::bind(&addr).await?;
    axum::serve(listener, app).await?;
    
    Ok(())
}

/// Health check endpoint
async fn health_check(State(state): State<Arc<ProxyState>>) -> impl IntoResponse {
    Json(json!({
        "status": "ok",
        "service": "substr8-gateway",
        "dct": true,
        "require_token": state.require_token,
        "authority_id": state.enforcer.authority_id()
    }))
}

/// Get a root token with all capabilities (for main agent)
async fn get_root_token(
    State(state): State<Arc<ProxyState>>,
) -> impl IntoResponse {
    let (token_b64, capabilities) = state.enforcer.create_root_token();
    
    Json(json!({
        "token": token_b64,
        "capabilities": capabilities,
        "authority_public_key": state.enforcer.public_key_hex()
    }))
}

/// Delegate token request
#[derive(Debug, Deserialize)]
struct DelegateRequest {
    parent_token: String,
    capabilities: Vec<String>,
    #[serde(default = "default_expires")]
    expires_minutes: u32,
}

fn default_expires() -> u32 { 60 }

/// Delegate (attenuate) a token
async fn delegate_token(
    State(state): State<Arc<ProxyState>>,
    Json(req): Json<DelegateRequest>,
) -> impl IntoResponse {
    match state.enforcer.delegate(&req.parent_token, &req.capabilities, req.expires_minutes) {
        Ok(token_b64) => Json(json!({
            "token": token_b64,
            "capabilities": req.capabilities,
            "expires_minutes": req.expires_minutes
        })).into_response(),
        Err(e) => (
            StatusCode::BAD_REQUEST,
            Json(json!({ "error": e.to_string() }))
        ).into_response()
    }
}

/// Verify capability request
#[derive(Debug, Deserialize)]
struct VerifyRequest {
    token: String,
    capability: String,
}

/// Verify a capability against a token
async fn verify_capability(
    State(state): State<Arc<ProxyState>>,
    Json(req): Json<VerifyRequest>,
) -> impl IntoResponse {
    let allowed = state.enforcer.verify(&req.token, &req.capability);
    
    Json(json!({
        "capability": req.capability,
        "allowed": allowed
    }))
}

/// Audit log entry
#[derive(Debug, Clone)]
struct AuditEntry {
    timestamp: chrono::DateTime<chrono::Utc>,
    path: String,
    tool: Option<String>,
    token_prefix: Option<String>,
    allowed: bool,
    reason: String,
}

// Simple in-memory audit log (last 100 entries)
static AUDIT_LOG: std::sync::LazyLock<std::sync::Mutex<Vec<AuditEntry>>> = 
    std::sync::LazyLock::new(|| std::sync::Mutex::new(Vec::with_capacity(100)));

fn log_audit(entry: AuditEntry) {
    if let Ok(mut log) = AUDIT_LOG.lock() {
        if log.len() >= 100 {
            log.remove(0);
        }
        log.push(entry);
    }
}

/// Get audit log
async fn get_audit_log() -> impl IntoResponse {
    let entries = AUDIT_LOG.lock()
        .map(|log| log.iter().map(|e| json!({
            "timestamp": e.timestamp.to_rfc3339(),
            "path": e.path,
            "tool": e.tool,
            "token_prefix": e.token_prefix,
            "allowed": e.allowed,
            "reason": e.reason
        })).collect::<Vec<_>>())
        .unwrap_or_default();
    
    Json(json!({
        "entries": entries,
        "count": entries.len()
    }))
}

/// Main proxy handler - intercepts and forwards requests to OpenClaw
async fn proxy_handler(
    State(state): State<Arc<ProxyState>>,
    request: Request,
) -> impl IntoResponse {
    let method = request.method().clone();
    let uri = request.uri().clone();
    let path = uri.path().to_string();
    let headers = request.headers().clone();
    
    // Extract DCT token from header if present
    let dct_token = headers
        .get("X-DCT-Token")
        .and_then(|v| v.to_str().ok())
        .map(String::from);
    
    let token_prefix = dct_token.as_ref().map(|t| format!("{}...", &t[..20.min(t.len())]));
    
    // Get body for tool extraction
    let body_bytes = match axum::body::to_bytes(request.into_body(), 10 * 1024 * 1024).await {
        Ok(b) => b,
        Err(e) => {
            error!("Failed to read request body: {}", e);
            return (
                StatusCode::BAD_REQUEST,
                Json(json!({ "error": "Failed to read request body" }))
            ).into_response();
        }
    };
    
    // Try to extract tool name from path or body
    let tool_name = extract_tool_name(&path, &body_bytes);
    
    // DCT enforcement for tool calls
    if let Some(ref tool) = tool_name {
        let capability = format!("tool:{}", tool);
        
        match &dct_token {
            Some(token) => {
                if !state.enforcer.verify(token, &capability) {
                    warn!("DCT DENIED: {} for capability {}", path, capability);
                    log_audit(AuditEntry {
                        timestamp: chrono::Utc::now(),
                        path: path.clone(),
                        tool: Some(tool.clone()),
                        token_prefix: token_prefix.clone(),
                        allowed: false,
                        reason: format!("Capability {} not in token", capability),
                    });
                    return (
                        StatusCode::FORBIDDEN,
                        Json(json!({
                            "error": "DCT: capability denied",
                            "capability": capability,
                            "tool": tool
                        }))
                    ).into_response();
                }
                info!("DCT ALLOWED: {} for capability {}", path, capability);
                log_audit(AuditEntry {
                    timestamp: chrono::Utc::now(),
                    path: path.clone(),
                    tool: Some(tool.clone()),
                    token_prefix: token_prefix.clone(),
                    allowed: true,
                    reason: "Token verified".to_string(),
                });
            }
            None if state.require_token => {
                warn!("DCT DENIED: {} - no token provided", path);
                log_audit(AuditEntry {
                    timestamp: chrono::Utc::now(),
                    path: path.clone(),
                    tool: Some(tool.clone()),
                    token_prefix: None,
                    allowed: false,
                    reason: "No X-DCT-Token header".to_string(),
                });
                return (
                    StatusCode::UNAUTHORIZED,
                    Json(json!({
                        "error": "DCT: token required",
                        "tool": tool,
                        "hint": "Include X-DCT-Token header"
                    }))
                ).into_response();
            }
            None => {
                // Token not required, allow through
                info!("DCT PASSTHROUGH: {} (no token required)", path);
            }
        }
    }
    
    // Forward request to OpenClaw
    let upstream_url = format!("{}{}", state.upstream_url, path);
    
    // Build forwarded request
    let mut req_builder = state.client.request(method, &upstream_url);
    
    // Copy headers (except host)
    for (key, value) in headers.iter() {
        if key != "host" {
            if let Ok(v) = value.to_str() {
                req_builder = req_builder.header(key.as_str(), v);
            }
        }
    }
    
    if !body_bytes.is_empty() {
        req_builder = req_builder.body(body_bytes.to_vec());
    }
    
    // Send to upstream
    match req_builder.send().await {
        Ok(response) => {
            let status = response.status();
            let resp_headers = response.headers().clone();
            
            match response.bytes().await {
                Ok(body) => {
                    let mut resp = Response::builder().status(status.as_u16());
                    
                    for (key, value) in resp_headers.iter() {
                        resp = resp.header(key, value);
                    }
                    
                    resp.body(Body::from(body)).unwrap().into_response()
                }
                Err(e) => {
                    error!("Failed to read upstream response: {}", e);
                    (
                        StatusCode::BAD_GATEWAY,
                        Json(json!({ "error": "Failed to read upstream response" }))
                    ).into_response()
                }
            }
        }
        Err(e) => {
            error!("Failed to forward request: {}", e);
            (
                StatusCode::BAD_GATEWAY,
                Json(json!({ "error": format!("Upstream unavailable: {}", e) }))
            ).into_response()
        }
    }
}

/// Extract tool name from request path or body
fn extract_tool_name(path: &str, body: &[u8]) -> Option<String> {
    // Check path patterns first
    // OpenClaw tool endpoints: POST /api/tool/exec, /api/tool/read, etc.
    if path.starts_with("/api/tool/") {
        return path.strip_prefix("/api/tool/")
            .map(|s| s.split('/').next().unwrap_or(s).to_string());
    }
    
    // Check for JSON-RPC style calls in body
    if let Ok(json) = serde_json::from_slice::<Value>(body) {
        // Method field for JSON-RPC
        if let Some(method) = json.get("method").and_then(|v| v.as_str()) {
            // Format: "tool.exec" or just "exec"
            let tool = method.strip_prefix("tool.").unwrap_or(method);
            return Some(tool.to_string());
        }
        
        // Tool name field
        if let Some(tool) = json.get("tool").and_then(|v| v.as_str()) {
            return Some(tool.to_string());
        }
        
        // Name field (OpenClaw tool call format)
        if let Some(name) = json.get("name").and_then(|v| v.as_str()) {
            return Some(name.to_string());
        }
    }
    
    None
}
