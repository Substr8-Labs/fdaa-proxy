use serde::{Deserialize, Serialize};

/// Gateway configuration
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GatewayConfig {
    /// Port for the substr8 proxy
    #[serde(default = "default_port")]
    pub port: u16,
    
    /// OpenClaw upstream port
    #[serde(default = "default_openclaw_port")]
    pub openclaw_port: u16,
    
    /// Authority private key (hex)
    pub authority_key: Option<String>,
    
    /// DCT configuration
    #[serde(default)]
    pub dct: DctConfig,
}

fn default_port() -> u16 { 18800 }
fn default_openclaw_port() -> u16 { 18789 }

/// DCT-specific configuration
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct DctConfig {
    /// Whether DCT enforcement is enabled
    #[serde(default = "default_true")]
    pub enabled: bool,
    
    /// Default token expiry in minutes
    #[serde(default = "default_expiry")]
    pub default_expiry_minutes: u32,
    
    /// Whether to require DCT for all requests
    #[serde(default)]
    pub require_token: bool,
    
    /// Tools that are always allowed (bypass DCT)
    #[serde(default)]
    pub always_allow: Vec<String>,
    
    /// Tools that are always denied
    #[serde(default)]
    pub always_deny: Vec<String>,
}

fn default_true() -> bool { true }
fn default_expiry() -> u32 { 60 }

impl Default for GatewayConfig {
    fn default() -> Self {
        Self {
            port: default_port(),
            openclaw_port: default_openclaw_port(),
            authority_key: None,
            dct: DctConfig::default(),
        }
    }
}

impl GatewayConfig {
    /// Load config from file or defaults
    pub fn load() -> Self {
        // TODO: Load from ~/.config/substr8/gateway.yaml
        Self::default()
    }
}
