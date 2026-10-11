use std::{sync::Arc, time::Instant};

use anyhow::{Context, Result};
use axum::{
    extract::{Request, State},
    http::{header, HeaderValue, StatusCode},
    middleware::{self, Next},
    response::{Html, IntoResponse, Response},
    routing::get,
    Json, Router,
};
use mira::{config::{AppPaths, Config}, service::{self, CoreStatus}};
use tokio::sync::Semaphore;

pub async fn serve(config: &Config, paths: &AppPaths) -> Result<()> {
    let _server_lock = paths.lock_server()?;
    let listener = tokio::net::TcpListener::bind(config.bind_address()).await
        .with_context(|| format!("Cannot listen on {}", config.bind_address()))?;
    // Status continues to use this live address after config set changes the next-start port.
    paths.save_endpoint(config.client_address())?;
    let shutdown = shutdown_signal()?;
    let app = Router::new()
        .route("/", get(|| async { Html(include_str!("../../web/index.html")) }))
        .route("/app.js", get(|| async {
            ([(header::CONTENT_TYPE, "text/javascript; charset=utf-8")], include_str!("../../web/app.js"))
        }))
        .route("/style.css", get(|| async {
            ([(header::CONTENT_TYPE, "text/css; charset=utf-8")], include_str!("../../web/style.css"))
        }))
        .route("/api/v1/status", get(|| async { Json(service::status(CoreStatus::Ok)) }))
        .route("/api/v1/version", get(|| async { Json(service::version()) }))
        .route("/api/v1/system", get(system))
        .fallback(|| async {
            (StatusCode::NOT_FOUND, Json(serde_json::json!({ "error": "not_found" })))
        })
        .layer(middleware::from_fn(observe))
        .with_state(Arc::new(Semaphore::new(1)));

    if !config.http.host.is_loopback() {
        tracing::warn!(address = %config.bind_address(), "HTTP diagnostics are exposed without authentication or TLS");
    }
    tracing::info!(version = mira::VERSION, node = %config.node_name,
        address = %config.bind_address(), "MIRA Core started");
    eprintln!("MIRA Core {}\nWeb UI: http://{}\nPress Ctrl+C to stop.", mira::VERSION, config.client_address());
    axum::serve(listener, app).with_graceful_shutdown(shutdown).await?;
    tracing::info!("MIRA Core stopped");
    Ok(())
}

async fn system(State(gate): State<Arc<Semaphore>>) -> Response {
    // Serialize expensive OS queries; status/version remain responsive.
    let permit = match gate.try_acquire_owned() {
        Ok(permit) => permit,
        Err(_) => return (StatusCode::SERVICE_UNAVAILABLE,
            Json(serde_json::json!({ "error": "system_query_busy" }))).into_response(),
    };
    match tokio::task::spawn_blocking(move || {
        let _permit = permit;
        service::system()
    }).await {
        Ok(report) => Json(report).into_response(),
        Err(error) => {
            tracing::error!(%error, "System query failed");
            (StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({ "error": "system_query_failed" }))).into_response()
        }
    }
}

async fn observe(request: Request, next: Next) -> Response {
    let started = Instant::now();
    let method = request.method().clone();
    let path = request.uri().path().to_owned();
    let mut response = next.run(request).await;
    let headers = response.headers_mut();
    headers.insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    headers.insert("x-content-type-options", HeaderValue::from_static("nosniff"));
    headers.insert("content-security-policy", HeaderValue::from_static(
        "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"));
    tracing::debug!(%method, %path, status = response.status().as_u16(),
        elapsed_ms = started.elapsed().as_millis() as u64, "HTTP request");
    response
}

fn shutdown_signal() -> Result<impl std::future::Future<Output = ()> + Send> {
    #[cfg(unix)]
    let mut terminate = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
        .context("Cannot register SIGTERM handler")?;
    Ok(async move {
        let interrupt = async {
            if let Err(error) = tokio::signal::ctrl_c().await {
                tracing::error!(%error, "Cannot listen for Ctrl+C; stopping server");
            }
        };
        #[cfg(unix)]
        tokio::select! { _ = interrupt => {}, _ = terminate.recv() => {} }
        #[cfg(not(unix))]
        interrupt.await;
        tracing::info!("Shutdown requested");
    })
}
