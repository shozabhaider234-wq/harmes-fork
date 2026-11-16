use std::env;
use std::error::Error;

use memcore::{SqliteConfig, SyntheticEmbedding};
use memd::MemCoreEngine;
use memd::{mock_engine, serve_tcp, MemoryEngine};
use std::path::PathBuf;
use std::sync::Arc;

#[cfg(feature = "local-fastembed")]
use memcore::{FastEmbedBackend, LocalModelConfig};
#[cfg(unix)]
use memd::serve_unix;

fn main() -> Result<(), Box<dyn Error>> {
    let mut args = env::args().skip(1);
    let mode = args.next().ok_or(
        "usage: memd (--socket PATH | --pipe NAME | --tcp LOOPBACK:PORT) [--store DB --model-dir PATH --ort-dylib LIB] [--synthetic | --mock]",
    )?;
    let address = args.next().ok_or("missing socket path or TCP address")?;
    let mut store = None;
    let mut model_dir = None;
    let mut ort_dylib = None;
    let mut model_revision = "model-v1".to_owned();
    let mut mock = false;
    let mut synthetic = false;
    let mut dimension = 128usize;
    let mut config = SqliteConfig::default();
    while let Some(option) = args.next() {
        match option.as_str() {
            "--store" => store = Some(PathBuf::from(args.next().ok_or("--store needs a path")?)),
            "--model-dir" => {
                model_dir = Some(PathBuf::from(
                    args.next().ok_or("--model-dir needs a path")?,
                ))
            }
            "--mock" => mock = true,
            "--ort-dylib" => {
                ort_dylib = Some(PathBuf::from(
                    args.next().ok_or("--ort-dylib needs a library path")?,
                ))
            }
            "--model-revision" => {
                model_revision = args
                    .next()
                    .ok_or("--model-revision needs an immutable revision")?
            }
            "--synthetic" => synthetic = true,
            "--dimension" => {
                dimension = args.next().ok_or("--dimension needs an integer")?.parse()?
            }
            "--cache-kib" => {
                config.cache_kib = args.next().ok_or("--cache-kib needs an integer")?.parse()?
            }
            "--max-top-k" => {
                config.max_top_k = args.next().ok_or("--max-top-k needs an integer")?.parse()?
            }
            "--busy-timeout-ms" => {
                config.busy_timeout_ms = args
                    .next()
                    .ok_or("--busy-timeout-ms needs an integer")?
                    .parse()?
            }
            "--rrf-constant" => {
                config.rrf_constant = args
                    .next()
                    .ok_or("--rrf-constant needs a number")?
                    .parse()?
            }
            _ => return Err(format!("unknown option: {option}").into()),
        }
    }

    if mock && synthetic {
        return Err("--mock and --synthetic are mutually exclusive".into());
    }
    if synthetic && model_dir.is_some() {
        return Err("--synthetic cannot be combined with --model-dir".into());
    }
    let engine: Arc<dyn MemoryEngine> = if mock {
        mock_engine()
    } else if synthetic {
        eprintln!("WARNING: synthetic embeddings are for tests/benchmarks, not semantic retrieval");
        let backend = SyntheticEmbedding::new(dimension).map_err(|e| e.to_string())?;
        Arc::new(MemCoreEngine::open_with_config(
            store.ok_or("--store is required (SQLite database path)")?,
            backend,
            config,
        )?)
    } else {
        #[cfg(feature = "local-fastembed")]
        {
            let store = store.ok_or("--store is required")?;
            let model_dir = model_dir.ok_or("--model-dir is required")?;
            if model_revision.trim().is_empty() {
                return Err("model revision must be non-empty".into());
            }
            let mut model_config = LocalModelConfig::all_minilm_l6_v2();
            model_config.model_id =
                format!("all-MiniLM-L6-v2@{model_revision};mean;no-prefix;l2-v1");
            let backend = match ort_dylib {
                Some(path) => FastEmbedBackend::load_with_runtime(&model_dir, model_config, &path)?,
                None => FastEmbedBackend::load_from_dir(&model_dir, model_config)?,
            };
            Arc::new(MemCoreEngine::open_with_config(store, backend, config)?)
        }
        #[cfg(not(feature = "local-fastembed"))]
        {
            let _ = (store, model_dir, model_revision, ort_dylib);
            return Err("build with local-fastembed and supply --model-dir for real embeddings, or explicitly use --synthetic for tests".into());
        }
    };
    match mode.as_str() {
        "--pipe" => {
            #[cfg(windows)]
            memd::serve_named_pipe(&address, engine)?;
            #[cfg(not(windows))]
            return Err("named pipes require Windows; use --socket on Unix".into());
        }
        "--tcp" => serve_tcp(&address, engine)?,
        "--socket" => {
            #[cfg(unix)]
            serve_unix(&address, engine)?;
            #[cfg(not(unix))]
            return Err(
                "Unix domain sockets are unavailable on this platform; use --tcp 127.0.0.1:PORT"
                    .into(),
            );
        }
        _ => return Err("expected --socket or --tcp".into()),
    }
    Ok(())
}
