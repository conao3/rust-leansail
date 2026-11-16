use std::io::{self, BufRead, BufReader};
use std::path::Path;
use std::process::{Child, Command, Stdio};
use std::sync::mpsc::{self, SyncSender};
use std::thread;
use std::time::{Duration, Instant};

use serde_json::{Value, json};

use crate::framing;
use crate::gate::{Action, Gate};
use crate::recovery::Recovery;

enum Event {
    Client(Vec<u8>),
    Server(Vec<u8>),
    ClientClosed,
    ServerClosed,
    Failed(io::Error, bool),
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
            Err(error) => Event::Failed(error, client),
        };
        let finished = matches!(
            event,
            Event::ClientClosed | Event::ServerClosed | Event::Failed(..)
        );
        if events.send(event).is_err() || finished {
            return;
        }
    }
}

fn backend_disconnected(
    stdout: &mut impl io::Write,
    pending: impl Iterator<Item = Value>,
) -> io::Result<i32> {
    for id in pending {
        let reply = json!({"jsonrpc":"2.0", "id":id,
            "error":{"code":-32603,"message":"native Lean backend との接続が終了しました"}});
        framing::write(stdout, &serde_json::to_vec(&reply)?)?;
    }
    let notification = json!({"jsonrpc":"2.0", "method":"window/showMessage",
        "params":{"type":1,"message":"native Lean backend との接続が終了しました。言語サーバーを再起動してください"}});
    framing::write(stdout, &serde_json::to_vec(&notification)?)?;
    Err(io::Error::new(
        io::ErrorKind::UnexpectedEof,
        "exit の前に native Lean backend との接続が終了しました",
    ))
}

fn forward(input: &mut impl io::Write, restart: Option<Value>, body: &[u8]) -> io::Result<()> {
    if let Some(restart) = restart {
        framing::write(input, &serde_json::to_vec(&restart)?)?;
    }
    framing::write(input, body)
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
    let mut recovery = Recovery::default();
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
                        if let Some(input) = input.as_mut()
                            && forward(input, recovery.client(&message), &body).is_err()
                        {
                            return backend_disconnected(&mut stdout, recovery.fail_pending());
                        }
                    }
                    Action::Drop => {}
                    Action::Reply(reply) => {
                        framing::write(&mut stdout, &serde_json::to_vec(&reply)?)?
                    }
                }
                if gate.exited() {
                    input.take();
                    closed = Some(Instant::now());
                }
            }
            Event::Server(body) => {
                let message: Value = serde_json::from_slice(&body)?;
                let reply = recovery.server(&message);
                let body = if let Some(reply) = &reply {
                    serde_json::to_vec(reply)?
                } else {
                    body
                };
                match gate.server(reply.as_ref().unwrap_or(&message)) {
                    Action::Forward => framing::write(&mut stdout, &body)?,
                    Action::Drop => {}
                    Action::Reply(reply) => {
                        if let Some(input) = input.as_mut()
                            && framing::write(input, &serde_json::to_vec(&reply)?).is_err()
                        {
                            return backend_disconnected(&mut stdout, recovery.fail_pending());
                        }
                    }
                }
            }
            Event::ClientClosed => {
                input.take();
                closed = Some(Instant::now());
            }
            Event::ServerClosed => {
                if !gate.exited() && closed.is_none() {
                    return backend_disconnected(&mut stdout, recovery.fail_pending());
                }
                break;
            }
            Event::Failed(_, false) => {
                return backend_disconnected(&mut stdout, recovery.fail_pending());
            }
            Event::Failed(error, true) => return Err(error),
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
