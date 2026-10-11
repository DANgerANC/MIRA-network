use std::collections::BTreeSet;

use serde::Serialize;
use sysinfo::{Disks, Networks, System};

#[derive(Debug, Serialize)]
pub struct SystemReport {
    pub os: String,
    pub os_version: Option<String>,
    pub architecture: String,
    pub hostname: Option<String>,
    pub cpu: CpuReport,
    pub ram_total_bytes: u64,
    pub ram_available_bytes: u64,
    pub disks: Vec<DiskReport>,
    pub network_interfaces: Vec<NetworkReport>,
    pub local_ips: Vec<String>,
    pub warnings: Vec<String>,
}

#[derive(Debug, Serialize)]
pub struct CpuReport {
    pub model: Option<String>,
    pub logical_cores: usize,
    pub physical_cores: Option<usize>,
}

#[derive(Debug, Serialize)]
pub struct DiskReport {
    pub name: String,
    pub mount_point: String,
    pub total_bytes: u64,
    pub available_bytes: u64,
}

#[derive(Debug, Serialize)]
pub struct NetworkReport {
    pub name: String,
    pub mac_address: String,
    pub addresses: Vec<String>,
}

pub fn collect() -> SystemReport {
    let mut system = System::new();
    system.refresh_memory();
    system.refresh_cpu_all();
    let disks = Disks::new_with_refreshed_list();
    let networks = Networks::new_with_refreshed_list();
    let mut local_ips = BTreeSet::new();
    let mut network_interfaces: Vec<_> = networks.iter().map(|(name, network)| {
        let mut addresses = BTreeSet::new();
        for ip in network.ip_networks() {
            addresses.insert(format!("{}/{}", ip.addr, ip.prefix));
            if !ip.addr.is_loopback() && !ip.addr.is_unspecified() && !ip.addr.is_multicast() {
                local_ips.insert(ip.addr.to_string());
            }
        }
        NetworkReport {
            name: name.clone(),
            mac_address: network.mac_address().to_string(),
            addresses: addresses.into_iter().collect(),
        }
    }).collect();
    network_interfaces.sort_by(|a, b| a.name.cmp(&b.name));
    let mut disks: Vec<_> = disks.iter().map(|disk| DiskReport {
        name: disk.name().to_string_lossy().into_owned(),
        mount_point: disk.mount_point().to_string_lossy().into_owned(),
        total_bytes: disk.total_space(),
        available_bytes: disk.available_space(),
    }).collect();
    disks.sort_by(|a, b| a.mount_point.cmp(&b.mount_point));
    let cpu_model = system.cpus().first().map(|cpu| cpu.brand().trim().to_owned())
        .filter(|model| !model.is_empty());
    let hostname = System::host_name();
    let mut warnings = Vec::new();
    if cpu_model.is_none() { warnings.push("CPU model is unavailable".into()); }
    if hostname.is_none() { warnings.push("Hostname is unavailable".into()); }
    if system.total_memory() == 0 { warnings.push("RAM information is unavailable".into()); }
    if disks.is_empty() { warnings.push("No disks were reported by the OS".into()); }
    if network_interfaces.is_empty() { warnings.push("No network interfaces were reported by the OS".into()); }
    for warning in &warnings { tracing::warn!(%warning, "Incomplete system information"); }
    tracing::debug!(interfaces = network_interfaces.len(), disks = disks.len(), "System information collected");
    SystemReport {
        os: System::name().unwrap_or_else(|| std::env::consts::OS.into()),
        os_version: System::long_os_version(),
        architecture: match System::cpu_arch().to_ascii_lowercase().as_str() {
            "amd64" | "x86-64" | "x86_64" => "x86_64".into(),
            "arm64" | "aarch64" => "aarch64".into(),
            architecture => architecture.into(),
        },
        hostname,
        cpu: CpuReport {
            model: cpu_model,
            logical_cores: system.cpus().len(),
            physical_cores: System::physical_core_count(),
        },
        ram_total_bytes: system.total_memory(),
        ram_available_bytes: system.available_memory(),
        disks,
        network_interfaces,
        local_ips: local_ips.into_iter().collect(),
        warnings,
    }
}
