use std::io::{self, BufRead, BufReader};
use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{self, SyncSender};
use std::thread;
use std::time::{Duration, Instant};

use serde_json::{Value, json};

use crate::framing;
use crate::gate::{Action, Gate};

enum Event {
    Client(Vec<u8>),
    Server(Vec<u8>),
    ClientClosed,
    ServerClosed,
    Failed(io::Error),
}

struct Backend(Child);

impl Drop for Backend {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}

fn pump(mut reader: impl BufRead, events: SyncSender<Event>, client: bool) {
    loop {
        let event = match framing::read(&mut reader) {
            Ok(Some(body)) => {
                if client {
                    Event::Client(body)
                } else {
                    Event::Server(body)
                }
            }
            Ok(None) => {
                if client {
                    Event::ClientClosed
                } else {
                    Event::ServerClosed
                }
            }
            Err(error) => Event::Failed(error),
        };
        let finished = matches!(
            event,
            Event::ClientClosed | Event::ServerClosed | Event::Failed(_)
        );
        if events.send(event).is_err() || finished {
            return;
        }
    }
}

pub fn run(path: &Path) -> io::Result<i32> {
    let mut command = Command::new(path);
    command
        .arg("--server")
        .env("LEAN_WORKER_PATH", path)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit());
    #[cfg(target_os = "linux")]
    {
        use std::os::unix::process::CommandExt;
        unsafe {
            command.pre_exec(|| {
                if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL) != 0 {
                    return Err(io::Error::last_os_error());
                }
                if libc::getppid() == 1 {
                    libc::_exit(1);
                }
                Ok(())
            });
        }
    }
    let mut backend = Backend(command.spawn()?);
    let mut input = backend.0.stdin.take();
    let output = backend.0.stdout.take().unwrap();
    let (events, receive) = mpsc::sync_channel(64);
    let client_events = events.clone();
    thread::spawn(move || pump(BufReader::new(io::stdin()), client_events, true));
    thread::spawn(move || pump(BufReader::new(output), events, false));
    let mut gate = Gate::new();
    let mut stdout = io::stdout().lock();
    let mut closed = None;
    loop {
        if closed.is_some_and(|at: Instant| at.elapsed() > Duration::from_secs(5)) {
            return Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "backend did not stop after client EOF",
            ));
        }
        let event = match receive.recv_timeout(Duration::from_millis(100)) {
            Ok(event) => event,
            Err(mpsc::RecvTimeoutError::Timeout) => continue,
            Err(mpsc::RecvTimeoutError::Disconnected) => break,
        };
        match event {
            Event::Client(body) => {
                let message: Value = match serde_json::from_slice(&body) {
                    Ok(message) => message,
                    Err(error) => {
                        let reply = json!({"jsonrpc":"2.0","id":null,
                            "error":{"code":-32700,"message":error.to_string()}});
                        framing::write(&mut stdout, &serde_json::to_vec(&reply)?)?;
                        continue;
                    }
                };
                match gate.client(&message) {
                    Action::Forward => {
                        if let Some(input) = input.as_mut() {
                            framing::write(input, &body)?;
                        }
                    }
                    Action::Drop => {}
                    Action::Reply(reply) => {
                        framing::write(&mut stdout, &serde_json::to_vec(&reply)?)?
                    }
                }
            }
            Event::Server(body) => {
                let message: Value = serde_json::from_slice(&body)?;
                gate.server(&message);
                framing::write(&mut stdout, &body)?;
            }
            Event::ClientClosed => {
                input.take();
                closed = Some(Instant::now());
            }
            Event::ServerClosed => break,
            Event::Failed(error) => return Err(error),
        }
    }
    let start = Instant::now();
    loop {
        if let Some(status) = backend.0.try_wait()? {
            return Ok(status.code().unwrap_or(255));
        }
        if start.elapsed() > Duration::from_secs(5) {
            return Err(io::Error::new(
                io::ErrorKind::TimedOut,
                "backend did not exit",
            ));
        }
        thread::sleep(Duration::from_millis(10));
    }
}
