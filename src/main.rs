use anyhow::Result;
use clap::{Parser, Subcommand};
use std::path::PathBuf;
use std::process::Command;
use tracing::info;

mod proxy;
mod dct;
mod config;

#[derive(Parser)]
#[command(name = "substr8")]
#[command(about = "Substr8 CLI - Provable AI agent infrastructure")]
#[command(version)]
#[command(propagate_version = true)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// DCT security gateway (proxy with capability enforcement)
    Gateway {
        #[command(subcommand)]
        action: GatewayAction,
    },
    // Future: Fdaa, Acc, Audit, etc.
}

#[derive(Subcommand)]
enum GatewayAction {
    /// Start the gateway (DCT proxy + OpenClaw)
    Start {
        /// Port for DCT proxy
        #[arg(long, default_value = "18800")]
        port: u16,
        
        /// OpenClaw gateway host (upstream)
        #[arg(long, default_value = "localhost")]
        openclaw_host: String,
        
        /// OpenClaw gateway port (upstream)
        #[arg(long, default_value = "18789")]
        openclaw_port: u16,
        
        /// Path to authority private key (hex)
        #[arg(long, env = "SUBSTR8_AUTHORITY_KEY")]
        authority_key: Option<String>,
        
        /// Skip starting OpenClaw (assume already running)
        #[arg(long)]
        no_openclaw: bool,
        
        /// Require DCT token for all tool calls
        #[arg(long)]
        require_token: bool,
    },
    /// Stop the gateway
    Stop,
    /// Check gateway status  
    Status,
    /// Start gateway stack via Docker Compose (proxy + OpenClaw)
    Up {
        /// Detach (run in background)
        #[arg(short, long)]
        detach: bool,
        
        /// Path to docker-compose.yml
        #[arg(long)]
        compose_file: Option<PathBuf>,
        
        /// Workspace path to mount
        #[arg(long, env = "WORKSPACE_PATH")]
        workspace: Option<PathBuf>,
        
        /// Secrets path (for DCT authority key)
        #[arg(long, env = "SECRETS_PATH")]
        secrets: Option<PathBuf>,
    },
    /// Stop Docker Compose stack
    Down {
        /// Path to docker-compose.yml
        #[arg(long)]
        compose_file: Option<PathBuf>,
        
        /// Remove volumes
        #[arg(short, long)]
        volumes: bool,
    },
    /// View logs from Docker Compose stack
    Logs {
        /// Follow log output
        #[arg(short, long)]
        follow: bool,
        
        /// Service name (proxy, openclaw)
        service: Option<String>,
    },
}

#[tokio::main]
async fn main() -> Result<()> {
    // Initialize tracing
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::from_default_env()
                .add_directive("substr8_gateway=info".parse()?)
        )
        .init();

    let cli = Cli::parse();

    match cli.command {
        Commands::Gateway { action } => match action {
            GatewayAction::Start { 
                port,
                openclaw_host,
                openclaw_port, 
                authority_key,
                no_openclaw,
                require_token,
            } => {
                info!("Starting Substr8 Gateway");
                info!("  Proxy port: {}", port);
                info!("  OpenClaw: {}:{}", openclaw_host, openclaw_port);
                info!("  Require token: {}", require_token);
                
                // Start OpenClaw if not disabled
                if !no_openclaw {
                    info!("Starting OpenClaw gateway...");
                    start_openclaw(openclaw_port)?;
                }
                
                // Start DCT proxy
                proxy::start_proxy(port, &openclaw_host, openclaw_port, authority_key, require_token).await?;
            }
            GatewayAction::Stop => {
                info!("Stopping Substr8 Gateway...");
                stop_gateway()?;
            }
            GatewayAction::Status => {
                check_status()?;
            }
            GatewayAction::Up { detach, compose_file, workspace, secrets } => {
                docker_up(detach, compose_file, workspace, secrets)?;
            }
            GatewayAction::Down { compose_file, volumes } => {
                docker_down(compose_file, volumes)?;
            }
            GatewayAction::Logs { follow, service } => {
                docker_logs(follow, service)?;
            }
        },
    }

    Ok(())
}

fn start_openclaw(port: u16) -> Result<()> {
    // Check if OpenClaw is already running
    let output = Command::new("openclaw")
        .args(["gateway", "probe"])
        .output();
    
    if let Ok(out) = output {
        if out.status.success() {
            info!("OpenClaw already running");
            return Ok(());
        }
    }
    
    // Start OpenClaw in background
    Command::new("openclaw")
        .args(["gateway", "start"])
        .spawn()?;
    
    info!("OpenClaw gateway started on port {}", port);
    Ok(())
}

fn stop_gateway() -> Result<()> {
    // Stop OpenClaw
    let _ = Command::new("openclaw")
        .args(["gateway", "stop"])
        .output();
    
    info!("Gateway stopped");
    Ok(())
}

fn check_status() -> Result<()> {
    // Check OpenClaw
    let openclaw = Command::new("openclaw")
        .args(["gateway", "probe"])
        .output();
    
    match openclaw {
        Ok(out) if out.status.success() => {
            println!("OpenClaw: ✅ running");
        }
        _ => {
            println!("OpenClaw: ❌ not running");
        }
    }
    
    // TODO: Check proxy status
    println!("DCT Proxy: checking...");
    
    Ok(())
}

fn get_compose_file(compose_file: Option<PathBuf>) -> PathBuf {
    compose_file.unwrap_or_else(|| {
        // Default: look relative to binary or use known path
        let default = PathBuf::from("/home/node/.openclaw/workspace/substr8-cli/docker/docker-compose.yml");
        if default.exists() {
            default
        } else {
            // Fallback to current directory
            PathBuf::from("docker-compose.yml")
        }
    })
}

fn docker_up(
    detach: bool,
    compose_file: Option<PathBuf>,
    workspace: Option<PathBuf>,
    secrets: Option<PathBuf>,
) -> Result<()> {
    let compose_path = get_compose_file(compose_file);
    info!("Starting gateway stack with Docker Compose");
    info!("  Compose file: {}", compose_path.display());
    
    let mut cmd = Command::new("docker");
    cmd.args(["compose", "-f", compose_path.to_str().unwrap(), "up", "--build"]);
    
    if detach {
        cmd.arg("-d");
    }
    
    // Set environment variables for paths
    if let Some(ws) = workspace {
        cmd.env("WORKSPACE_PATH", ws);
    }
    if let Some(sec) = secrets {
        cmd.env("SECRETS_PATH", sec);
    }
    
    let status = cmd.status()?;
    
    if status.success() {
        if detach {
            println!("✅ Gateway stack started in background");
            println!("   Proxy: localhost:18800");
            println!("   Logs:  substr8 gateway logs -f");
        }
    } else {
        anyhow::bail!("Docker Compose failed");
    }
    
    Ok(())
}

fn docker_down(compose_file: Option<PathBuf>, volumes: bool) -> Result<()> {
    let compose_path = get_compose_file(compose_file);
    info!("Stopping gateway stack");
    
    let mut cmd = Command::new("docker");
    cmd.args(["compose", "-f", compose_path.to_str().unwrap(), "down"]);
    
    if volumes {
        cmd.arg("-v");
    }
    
    let status = cmd.status()?;
    
    if status.success() {
        println!("✅ Gateway stack stopped");
    }
    
    Ok(())
}

fn docker_logs(follow: bool, service: Option<String>) -> Result<()> {
    let compose_path = get_compose_file(None);
    
    let mut cmd = Command::new("docker");
    cmd.args(["compose", "-f", compose_path.to_str().unwrap(), "logs"]);
    
    if follow {
        cmd.arg("-f");
    }
    
    if let Some(svc) = service {
        cmd.arg(svc);
    }
    
    cmd.status()?;
    Ok(())
}
