mod capabilities;
mod framing;
mod gate;
mod host;
mod lifecycle;

use std::path::PathBuf;

use clap::{Parser, Subcommand};

#[derive(Parser)]
#[command(version, about = "A Rust host for the Lean language server")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    Server {
        #[command(subcommand)]
        command: ServerCommand,
    },
}

#[derive(Subcommand)]
enum ServerCommand {
    RunServer {
        #[arg(long, env = "LEANSAIL_BACKEND")]
        backend: PathBuf,
    },
}

fn main() -> std::process::ExitCode {
    let cli = match Cli::try_parse() {
        Ok(cli) => cli,
        Err(error) => {
            let code = if error.use_stderr() { 252 } else { 0 };
            let _ = error.print();
            return std::process::ExitCode::from(code);
        }
    };
    let Command::Server {
        command: ServerCommand::RunServer { backend },
    } = cli.command;
    match host::run(&backend) {
        Ok(code) => std::process::ExitCode::from(u8::try_from(code).unwrap_or(255)),
        Err(error) => {
            eprintln!("leansail: {error}");
            std::process::ExitCode::from(255)
        }
    }
}
