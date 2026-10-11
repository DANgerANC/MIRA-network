mod http;
mod logging;

use std::{path::PathBuf, process::ExitCode, time::Duration};

use anyhow::{bail, Context, Result};
use clap::{Parser, Subcommand};
use mira::{config::{AppPaths, Config}, service::{self, CoreStatus, StatusReport}, system::SystemReport};
use serde::Serialize;

#[derive(Parser)]
#[command(name = "mira", version = mira::VERSION, about = "MIRA Core — standalone system diagnostics")]
struct Cli {
    /// Data root containing config/, data/ and logs/.
    #[arg(long, env = "MIRA_HOME", global = true)]
    home: Option<PathBuf>,
    /// Print machine-readable JSON (logs go to stderr).
    #[arg(long, global = true)]
    json: bool,
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Show the version of this executable.
    Version,
    /// Query the server using this data directory.
    Status,
    /// Discover local hardware, disks and network interfaces.
    System,
    /// Show or update persistent configuration.
    Config {
        #[command(subcommand)]
        action: Option<ConfigCommand>,
    },
    /// Run the HTTP API and diagnostic Web UI in the foreground.
    Serve,
}

#[derive(Subcommand)]
enum ConfigCommand {
    /// Show all settings.
    Show,
    /// Show configuration and data paths.
    Paths,
    /// Save a setting; a running server applies it after restart.
    Set { key: String, value: String },
}

#[tokio::main]
async fn main() -> ExitCode {
    match run(Cli::parse()).await {
        Ok(code) => ExitCode::from(code),
        Err(error) => {
            eprintln!("MIRA error: {error:#}");
            ExitCode::FAILURE
        }
    }
}

async fn run(cli: Cli) -> Result<u8> {
    let paths = AppPaths::create(cli.home.clone())?;
    let config = paths.load()?;
    let _log_guard = logging::init(&paths, &config.logging.level)?;
    tracing::debug!(home = %paths.root.display(), "Configuration loaded");
    let result = execute(cli, &config, &paths).await;
    if let Err(error) = &result {
        tracing::error!(error = %format!("{error:#}"), "Command failed");
    }
    result // WorkerGuard flushes all queued records before returning to main.
}

async fn execute(cli: Cli, config: &Config, paths: &AppPaths) -> Result<u8> {
    match cli.command {
        Command::Version => {
            let report = service::version();
            if cli.json { print_json(&report)?; }
            else { println!("MIRA Core {}", report.core); }
        }
        Command::Status => {
            let report = server_status(paths).await?;
            let running = report.status == CoreStatus::Ok;
            if cli.json { print_json(&report)?; }
            else {
                let status = match report.status {
                    CoreStatus::Ok => "running",
                    CoreStatus::Stopped => "stopped",
                    CoreStatus::Unreachable => "unreachable",
                };
                println!("MIRA Core {}\nStatus: {status}", report.core);
            }
            return Ok(if running { 0 } else { 1 });
        }
        Command::System => {
            let report = service::system();
            if cli.json { print_json(&report)?; } else { print_system(&report); }
        }
        Command::Config { action } => match action.unwrap_or(ConfigCommand::Show) {
            ConfigCommand::Show => {
                if cli.json { print_json(config)?; }
                else { print!("{}", toml::to_string_pretty(config)?); }
            }
            ConfigCommand::Paths => {
                if cli.json { print_json(paths)?; }
                else {
                    println!("Home: {}\nConfig: {}\nData: {}\nLogs: {}",
                        paths.root.display(), paths.config_file.display(),
                        paths.data_dir.display(), paths.logs_dir.display());
                }
            }
            ConfigCommand::Set { key, value } => {
                let updated = paths.set(&key, &value)?;
                tracing::info!(%key, "Configuration saved; restart server to apply");
                if cli.json { print_json(&updated)?; }
                else { println!("Saved {key}. Restart mira serve to apply the change."); }
            }
        },
        Command::Serve => {
            if cli.json { bail!("--json is available for version, status, system and config commands"); }
            http::serve(config, paths).await?;
        }
    }
    Ok(0)
}

async fn server_status(paths: &AppPaths) -> Result<StatusReport> {
    if !paths.server_is_running()? {
        return Ok(service::status(CoreStatus::Stopped));
    }
    let endpoint = match paths.running_endpoint() {
        Ok(endpoint) => endpoint,
        Err(error) => {
            tracing::warn!(%error, "Server is starting or its endpoint is unavailable");
            return Ok(service::status(CoreStatus::Unreachable));
        }
    };
    let client = reqwest::Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(3))
        .build()?;
    let response = match client.get(format!("http://{endpoint}/api/v1/status")).send().await {
        Ok(response) => response,
        Err(error) => {
            tracing::warn!(%error, "Server is not responding");
            return Ok(service::status(CoreStatus::Unreachable));
        }
    };
    let report: StatusReport = response.error_for_status().context("Status endpoint returned an error")?
        .json().await.context("Invalid MIRA status response")?;
    Ok(report)
}

fn print_json(value: &impl Serialize) -> Result<()> {
    println!("{}", serde_json::to_string_pretty(value)?);
    Ok(())
}

fn gib(bytes: u64) -> f64 { bytes as f64 / 1_073_741_824.0 }

fn print_system(report: &SystemReport) {
    println!("OS: {}", report.os_version.as_deref().unwrap_or(&report.os));
    println!("Architecture: {}", report.architecture);
    println!("CPU: {} ({} logical cores)", report.cpu.model.as_deref().unwrap_or("unknown"), report.cpu.logical_cores);
    println!("RAM: {:.2} GiB ({:.2} GiB available)", gib(report.ram_total_bytes), gib(report.ram_available_bytes));
    println!("Hostname: {}", report.hostname.as_deref().unwrap_or("unknown"));
    println!("Local IPs: {}", if report.local_ips.is_empty() { "none reported".into() } else { report.local_ips.join(", ") });
    println!("Disks:");
    for disk in &report.disks {
        println!("  {} — {:.2} / {:.2} GiB free", disk.mount_point, gib(disk.available_bytes), gib(disk.total_bytes));
    }
    println!("Network interfaces:");
    for network in &report.network_interfaces {
        println!("  {} [{}]: {}", network.name, network.mac_address, network.addresses.join(", "));
    }
    for warning in &report.warnings { println!("Warning: {warning}"); }
}
