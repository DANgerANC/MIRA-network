use std::{
    fs::{self, File, OpenOptions},
    io::{ErrorKind, Write},
    net::{IpAddr, Ipv4Addr, Ipv6Addr, SocketAddr},
    path::{Path, PathBuf},
    thread,
    time::{Duration, Instant},
};

use anyhow::{bail, Context, Result};
use directories::BaseDirs;
use serde::{Deserialize, Serialize};

fn lock_is_contended(error: &std::io::Error) -> bool {
    // Windows ERROR_LOCK_VIOLATION is not consistently mapped to WouldBlock.
    error.kind() == ErrorKind::WouldBlock
        || error.raw_os_error() == fs2::lock_contended_error().raw_os_error()
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub schema_version: u32,
    pub node_name: String,
    pub http: HttpConfig,
    pub logging: LoggingConfig,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HttpConfig {
    pub host: IpAddr,
    pub port: u16,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LoggingConfig {
    pub level: String,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            schema_version: 1,
            node_name: "MIRA".into(),
            http: HttpConfig { host: Ipv4Addr::LOCALHOST.into(), port: 8787 },
            logging: LoggingConfig { level: "info".into() },
        }
    }
}

impl Config {
    pub fn validate(&self) -> Result<()> {
        if self.schema_version != 1 {
            bail!("Unsupported configuration schema: {}", self.schema_version);
        }
        if self.node_name.trim().is_empty()
            || self.node_name.chars().count() > 128
            || self.node_name.chars().any(char::is_control)
        {
            bail!("node_name must contain 1..128 characters without control characters");
        }
        if self.http.port == 0 || self.http.host.is_multicast() {
            bail!("http.port must be 1..65535 and http.host must not be multicast");
        }
        match self.logging.level.as_str() {
            "error" | "warn" | "info" | "debug" => Ok(()),
            _ => bail!("logging.level must be error, warn, info or debug"),
        }
    }

    pub fn bind_address(&self) -> SocketAddr {
        SocketAddr::new(self.http.host, self.http.port)
    }

    pub fn client_address(&self) -> SocketAddr {
        let host = match self.http.host {
            IpAddr::V4(ip) if ip.is_unspecified() => Ipv4Addr::LOCALHOST.into(),
            IpAddr::V6(ip) if ip.is_unspecified() => Ipv6Addr::LOCALHOST.into(),
            ip => ip,
        };
        SocketAddr::new(host, self.http.port)
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct AppPaths {
    pub root: PathBuf,
    pub config_file: PathBuf,
    pub data_dir: PathBuf,
    pub logs_dir: PathBuf,
}

impl AppPaths {
    pub fn create(root: Option<PathBuf>) -> Result<Self> {
        let root = match root {
            Some(path) if path.as_os_str().is_empty() => bail!("MIRA home must not be empty"),
            Some(path) => path,
            None => BaseDirs::new()
                .context("Cannot locate user data directory; use --home or MIRA_HOME")?
                .data_local_dir()
                .join(if cfg!(windows) { "MIRA" } else { "mira" }),
        };
        let root = if root.is_absolute() { root } else { std::env::current_dir()?.join(root) };
        let paths = Self {
            config_file: root.join("config").join("mira.toml"),
            data_dir: root.join("data"),
            logs_dir: root.join("logs"),
            root,
        };
        for directory in [paths.config_dir(), &paths.data_dir, &paths.logs_dir] {
            fs::create_dir_all(directory)
                .with_context(|| format!("Cannot create {}", directory.display()))?;
        }
        Ok(paths)
    }

    fn config_dir(&self) -> &Path {
        // The path is constructed with config/mira.toml above.
        self.config_file.parent().expect("configuration has a parent directory")
    }

    fn config_lock(&self) -> Result<File> {
        let file = OpenOptions::new().create(true).truncate(false).read(true).write(true)
            .open(self.config_dir().join("config.lock"))?;
        let started = Instant::now();
        loop {
            match fs2::FileExt::try_lock_exclusive(&file) {
                Ok(()) => return Ok(file),
                Err(error) if lock_is_contended(&error)
                    && started.elapsed() < Duration::from_secs(3) => {
                        thread::sleep(Duration::from_millis(25));
                    }
                Err(error) => return Err(error).context("Cannot lock configuration"),
            }
        }
    }

    fn read_or_create(&self) -> Result<Config> {
        match fs::read_to_string(&self.config_file) {
            Ok(content) => {
                let config: Config = toml::from_str(&content)
                    .with_context(|| format!("Invalid configuration: {}", self.config_file.display()))?;
                config.validate()?;
                Ok(config)
            }
            Err(error) if error.kind() == ErrorKind::NotFound => {
                let config = Config::default();
                self.save(&config)?;
                Ok(config)
            }
            Err(error) => Err(error).context("Cannot read configuration"),
        }
    }

    pub fn load(&self) -> Result<Config> {
        let _lock = self.config_lock()?;
        self.read_or_create()
    }

    pub fn set(&self, key: &str, value: &str) -> Result<Config> {
        // Keep the lock across the entire read-modify-write operation.
        let _lock = self.config_lock()?;
        let mut config = self.read_or_create()?;
        match key {
            "node_name" => config.node_name = value.into(),
            "http.host" => config.http.host = value.parse().context("http.host must be an IPv4 or IPv6 address")?,
            "http.port" => config.http.port = value.parse().context("http.port must be 1..65535")?,
            "logging.level" => config.logging.level = value.to_ascii_lowercase(),
            _ => bail!("Unknown setting {key}; use node_name, http.host, http.port or logging.level"),
        }
        config.validate()?;
        self.save(&config)?;
        Ok(config)
    }

    fn save(&self, config: &Config) -> Result<()> {
        let content = toml::to_string_pretty(config)?;
        // Same filesystem, synced contents, then atomic replacement on Windows/Linux.
        let mut temporary = tempfile::NamedTempFile::new_in(self.config_dir())?;
        temporary.write_all(content.as_bytes())?;
        temporary.as_file().sync_all()?;
        temporary.persist(&self.config_file).context("Cannot persist configuration")?;
        #[cfg(unix)]
        File::open(self.config_dir())?.sync_all()?;
        Ok(())
    }

    pub fn lock_server(&self) -> Result<File> {
        let file = OpenOptions::new().create(true).truncate(false).read(true).write(true)
            .open(self.data_dir.join("server.lock"))?;
        fs2::FileExt::try_lock_exclusive(&file)
            .context("Another MIRA server is using this data directory, or its lock is unavailable")?;
        Ok(file)
    }

    pub fn server_is_running(&self) -> Result<bool> {
        let file = OpenOptions::new().create(true).truncate(false).read(true).write(true)
            .open(self.data_dir.join("server.lock"))?;
        match fs2::FileExt::try_lock_exclusive(&file) {
            Ok(()) => Ok(false), // Closing the file releases the probe lock.
            Err(error) if lock_is_contended(&error) => Ok(true),
            Err(error) => Err(error).context("Cannot inspect server lock"),
        }
    }

    pub fn save_endpoint(&self, address: SocketAddr) -> Result<()> {
        let mut temporary = tempfile::NamedTempFile::new_in(&self.data_dir)?;
        serde_json::to_writer(&mut temporary, &address)?;
        temporary.as_file().sync_all()?;
        temporary.persist(self.data_dir.join("endpoint.json"))
            .context("Cannot save running server endpoint")?;
        Ok(())
    }

    pub fn running_endpoint(&self) -> Result<SocketAddr> {
        let content = fs::read(self.data_dir.join("endpoint.json"))?;
        serde_json::from_slice(&content).context("Invalid running server endpoint")
    }
}
