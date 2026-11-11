use std::env;
use std::error::Error;

use memd::{mock_engine, serve_tcp};

#[cfg(unix)]
use memd::serve_unix;

fn main() -> Result<(), Box<dyn Error>> {
    let mut args = env::args().skip(1);
    let mode = args
        .next()
        .ok_or("usage: memd --socket PATH (Unix) | --tcp LOOPBACK:PORT")?;
    let address = args.next().ok_or("missing socket path or TCP address")?;
    if args.next().is_some() {
        return Err("unexpected extra arguments".into());
    }

    let engine = mock_engine();
    match mode.as_str() {
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
