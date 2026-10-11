use anyhow::{Context, Result};
use mira::config::AppPaths;
use tracing_appender::{non_blocking::WorkerGuard, rolling::{RollingFileAppender, Rotation}};
use tracing_subscriber::{filter::LevelFilter, layer::SubscriberExt, util::SubscriberInitExt};

pub fn init(paths: &AppPaths, level: &str) -> Result<WorkerGuard> {
    let appender = RollingFileAppender::builder()
        .rotation(Rotation::DAILY)
        .filename_prefix("mira")
        .filename_suffix("log")
        .max_log_files(14)
        .build(&paths.logs_dir)
        .context("Cannot initialize log file")?;
    let (writer, guard) = tracing_appender::non_blocking::NonBlockingBuilder::default()
        .lossy(false)
        .finish(appender);
    let level: LevelFilter = level.parse().context("Invalid logging level")?;
    tracing_subscriber::registry()
        .with(level)
        .with(tracing_subscriber::fmt::layer()
            .with_ansi(false)
            .with_writer(std::io::stderr))
        .with(tracing_subscriber::fmt::layer()
            .with_ansi(false)
            .with_writer(writer))
        .try_init()
        .context("Cannot initialize logging")?;
    Ok(guard)
}
