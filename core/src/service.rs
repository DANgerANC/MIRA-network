use serde::{Deserialize, Serialize};

use crate::{system::{self, SystemReport}, VERSION};

#[derive(Debug, Serialize, Deserialize)]
pub struct VersionReport {
    pub core: String,
}

#[derive(Debug, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum CoreStatus {
    Ok,
    Stopped,
    Unreachable,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct StatusReport {
    pub core: String,
    pub status: CoreStatus,
}

pub fn version() -> VersionReport {
    VersionReport { core: VERSION.into() }
}

pub fn status(state: CoreStatus) -> StatusReport {
    StatusReport { core: VERSION.into(), status: state }
}

pub fn system() -> SystemReport {
    system::collect()
}
