//! Transport-independent models, system discovery and persistent configuration.
pub mod config;
pub mod service;
pub mod system;

pub const VERSION: &str = env!("CARGO_PKG_VERSION");
