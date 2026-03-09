use anyhow::{anyhow, Result};
use dct_core::authority::RootAuthority;
use dct_core::capability::Capability;
use dct_core::token::CapabilityToken;
use chrono::Duration;
use std::fs;
use std::path::Path;
use std::sync::RwLock;
use tracing::info;

/// All OpenClaw tools that can be capability-gated
const ALL_TOOLS: &[&str] = &[
    "read", "write", "edit", "exec", "process",
    "web_search", "web_fetch", "browser", "canvas",
    "nodes", "cron", "message", "gateway", "tts", "image",
    "sessions_list", "sessions_history", "sessions_send", "sessions_spawn",
    "memory_search", "memory_get",
    "agents_list", "session_status",
];

/// DCT Enforcer - handles token creation, delegation, and verification
pub struct DctEnforcer {
    authority: RootAuthority,
    /// Cache of verified tokens (token_hash -> capabilities)
    #[allow(dead_code)]
    cache: RwLock<std::collections::HashMap<String, Vec<String>>>,
}

/// Default keystore path
const DEFAULT_KEYSTORE_PATH: &str = "/home/node/.openclaw/secrets/dct-authority.key";

impl DctEnforcer {
    /// Create a new enforcer with optional private key
    /// 
    /// Priority:
    /// 1. Explicit private_key_hex parameter
    /// 2. Load from keystore file
    /// 3. Generate new and save to keystore
    pub fn new(private_key_hex: Option<String>) -> Result<Self> {
        let authority = match private_key_hex {
            Some(key) => {
                info!("Using provided authority key");
                RootAuthority::from_private_key_hex(&key, "substr8-gateway")
                    .map_err(|e| anyhow!("Invalid authority key: {}", e))?
            }
            None => {
                // Try to load from keystore
                if let Some(key) = Self::load_from_keystore()? {
                    info!("Loaded authority key from keystore");
                    RootAuthority::from_private_key_hex(&key, "substr8-gateway")
                        .map_err(|e| anyhow!("Invalid keystore key: {}", e))?
                } else {
                    info!("Generating new authority keypair");
                    let auth = RootAuthority::generate_with_id("substr8-gateway");
                    // Save to keystore for persistence
                    Self::save_to_keystore(&auth.private_key_hex())?;
                    auth
                }
            }
        };
        
        Ok(Self {
            authority,
            cache: RwLock::new(std::collections::HashMap::new()),
        })
    }
    
    /// Load private key from keystore file
    fn load_from_keystore() -> Result<Option<String>> {
        let path = Path::new(DEFAULT_KEYSTORE_PATH);
        if path.exists() {
            let key = fs::read_to_string(path)
                .map_err(|e| anyhow!("Failed to read keystore: {}", e))?;
            Ok(Some(key.trim().to_string()))
        } else {
            Ok(None)
        }
    }
    
    /// Save private key to keystore file
    fn save_to_keystore(private_key_hex: &str) -> Result<()> {
        let path = Path::new(DEFAULT_KEYSTORE_PATH);
        
        // Create parent directory if needed
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)
                .map_err(|e| anyhow!("Failed to create keystore directory: {}", e))?;
        }
        
        // Write key with restricted permissions
        fs::write(path, private_key_hex)
            .map_err(|e| anyhow!("Failed to write keystore: {}", e))?;
        
        // Set file permissions to owner-only (Unix)
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let perms = fs::Permissions::from_mode(0o600);
            fs::set_permissions(path, perms)
                .map_err(|e| anyhow!("Failed to set keystore permissions: {}", e))?;
        }
        
        info!("Authority key saved to keystore: {}", path.display());
        Ok(())
    }
    
    /// Export current private key (for backup)
    pub fn export_private_key(&self) -> String {
        self.authority.private_key_hex()
    }
    
    /// Get authority ID
    pub fn authority_id(&self) -> &str {
        &self.authority.authority_id
    }
    
    /// Get public key hex
    pub fn public_key_hex(&self) -> String {
        self.authority.public_key_hex()
    }
    
    /// Convert string capabilities to Capability enum
    fn parse_capabilities(caps: &[String]) -> Vec<Capability> {
        caps.iter()
            .filter_map(|s| {
                if let Some(tool) = s.strip_prefix("tool:") {
                    Some(Capability::tool(tool))
                } else if let Some(mount) = s.strip_prefix("mount:") {
                    // Parse mount:path:mode format
                    let parts: Vec<&str> = mount.splitn(2, ':').collect();
                    if parts.len() == 2 {
                        let mode = match parts[1] {
                            "rw" => dct_core::capability::MountMode::ReadWrite,
                            _ => dct_core::capability::MountMode::ReadOnly,
                        };
                        Some(Capability::mount(parts[0], mode))
                    } else {
                        None
                    }
                } else {
                    // Assume it's a tool name without prefix
                    Some(Capability::tool(s))
                }
            })
            .collect()
    }
    
    /// Create a root token with all capabilities
    pub fn create_root_token(&self) -> (String, Vec<String>) {
        let cap_strings: Vec<String> = ALL_TOOLS
            .iter()
            .map(|t| format!("tool:{}", t))
            .collect();
        
        let capabilities = Self::parse_capabilities(&cap_strings);
        
        let token = self.authority.create_root_token(&capabilities)
            .expect("Failed to create root token");
        let b64 = token.to_base64().expect("Failed to serialize token");
        (b64, cap_strings)
    }
    
    /// Create a token with specific capabilities
    pub fn create_token(&self, capabilities: &[String]) -> Result<String> {
        // Validate capabilities are in allowed set
        for cap in capabilities {
            if cap.starts_with("tool:") {
                let tool = cap.strip_prefix("tool:").unwrap();
                if !ALL_TOOLS.contains(&tool) {
                    return Err(anyhow!("Unknown tool capability: {}", cap));
                }
            }
            // Allow other capability types (mount:, ipc:, etc.)
        }
        
        let caps = Self::parse_capabilities(capabilities);
        let token = self.authority.create_root_token(&caps)
            .map_err(|e| anyhow!("Failed to create token: {}", e))?;
        token.to_base64().map_err(|e| anyhow!("Failed to serialize token: {}", e))
    }
    
    /// Delegate (attenuate) a parent token
    pub fn delegate(
        &self, 
        parent_token_b64: &str, 
        capabilities: &[String],
        expires_minutes: u32,
    ) -> Result<String> {
        // Parse parent token
        let parent = CapabilityToken::from_base64(parent_token_b64, &self.authority.public_key())
            .map_err(|e| anyhow!("Invalid parent token: {}", e))?;
        
        let caps = Self::parse_capabilities(capabilities);
        let expiry = Duration::minutes(expires_minutes as i64);
        
        // Delegate with attenuation
        let child = self.authority.delegate(&parent, &caps, expiry)
            .map_err(|e| anyhow!("Delegation failed: {}", e))?;
        
        child.to_base64().map_err(|e| anyhow!("Failed to serialize token: {}", e))
    }
    
    /// Verify a capability against a token
    pub fn verify(&self, token_b64: &str, capability: &str) -> bool {
        // Parse token
        let token = match CapabilityToken::from_base64(token_b64, &self.authority.public_key()) {
            Ok(t) => t,
            Err(e) => {
                tracing::warn!("Invalid token: {}", e);
                return false;
            }
        };
        
        // Check expiry
        if token.is_expired() {
            tracing::warn!("Token expired");
            return false;
        }
        
        // Parse the capability string
        let cap = if let Some(tool) = capability.strip_prefix("tool:") {
            Capability::tool(tool)
        } else {
            Capability::tool(capability)
        };
        
        // Verify capability
        token.verify_capability(&self.authority.public_key(), &cap).is_ok()
    }
    
    /// Verify multiple capabilities (all must pass)
    pub fn verify_all(&self, token_b64: &str, capabilities: &[&str]) -> bool {
        capabilities.iter().all(|cap| self.verify(token_b64, cap))
    }
    
    /// Verify any capability (at least one must pass)  
    pub fn verify_any(&self, token_b64: &str, capabilities: &[&str]) -> bool {
        capabilities.iter().any(|cap| self.verify(token_b64, cap))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    
    #[test]
    fn test_create_and_verify() {
        let enforcer = DctEnforcer::new(None).unwrap();
        
        // Create root token
        let (token, caps) = enforcer.create_root_token();
        assert!(!token.is_empty());
        assert!(caps.contains(&"tool:exec".to_string()));
        
        // Verify capability
        assert!(enforcer.verify(&token, "tool:exec"));
        assert!(enforcer.verify(&token, "tool:read"));
        
        // Non-existent capability should fail
        assert!(!enforcer.verify(&token, "tool:nonexistent"));
    }
    
    #[test]
    fn test_delegation() {
        let enforcer = DctEnforcer::new(None).unwrap();
        
        // Create root token
        let (root_token, _) = enforcer.create_root_token();
        
        // Delegate to subset
        let child_caps = vec!["tool:read".to_string(), "tool:write".to_string()];
        let child_token = enforcer.delegate(&root_token, &child_caps, 60).unwrap();
        
        // Child should have delegated capabilities
        assert!(enforcer.verify(&child_token, "tool:read"));
        assert!(enforcer.verify(&child_token, "tool:write"));
        
        // Child should NOT have non-delegated capabilities
        assert!(!enforcer.verify(&child_token, "tool:exec"));
        assert!(!enforcer.verify(&child_token, "tool:gateway"));
    }
    
    #[test]
    fn test_escalation_blocked() {
        let enforcer = DctEnforcer::new(None).unwrap();
        
        // Create limited parent token
        let parent_caps = vec!["tool:read".to_string()];
        let parent_token = enforcer.create_token(&parent_caps).unwrap();
        
        // Try to escalate
        let escalated_caps = vec!["tool:read".to_string(), "tool:exec".to_string()];
        let result = enforcer.delegate(&parent_token, &escalated_caps, 60);
        
        // Should fail or child should not have escalated capability
        if let Ok(child_token) = result {
            assert!(!enforcer.verify(&child_token, "tool:exec"));
        }
    }
}
