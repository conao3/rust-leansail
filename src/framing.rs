use std::io::{self, BufRead, Write};

const HEADER_LIMIT: usize = 64 * 1024;
const BODY_LIMIT: usize = 64 * 1024 * 1024;

fn invalid(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

fn line(reader: &mut impl BufRead, limit: usize) -> io::Result<Option<Vec<u8>>> {
    let mut result = Vec::new();
    loop {
        let buffer = reader.fill_buf()?;
        if buffer.is_empty() {
            return if result.is_empty() {
                Ok(None)
            } else {
                Err(io::Error::new(
                    io::ErrorKind::UnexpectedEof,
                    "truncated LSP header",
                ))
            };
        }
        let length = buffer
            .iter()
            .position(|b| *b == b'\n')
            .map_or(buffer.len(), |i| i + 1);
        let length = length.min(limit.saturating_sub(result.len()) + 1);
        result.extend_from_slice(&buffer[..length]);
        reader.consume(length);
        if result.len() > limit {
            return Err(invalid("LSP headers exceed 64 KiB"));
        }
        if result.last() == Some(&b'\n') {
            return Ok(Some(result));
        }
    }
}

pub fn read(reader: &mut impl BufRead) -> io::Result<Option<Vec<u8>>> {
    let mut consumed = 0;
    let mut size = None;
    loop {
        let Some(header) = line(reader, HEADER_LIMIT - consumed)? else {
            if consumed == 0 {
                return Ok(None);
            }
            return Err(io::Error::new(
                io::ErrorKind::UnexpectedEof,
                "truncated LSP headers",
            ));
        };
        consumed += header.len();
        if !header.ends_with(b"\r\n") {
            return Err(invalid("LSP headers must end in CRLF"));
        }
        if header == b"\r\n" {
            break;
        }
        let header = std::str::from_utf8(&header[..header.len() - 2])
            .map_err(|_| invalid("LSP header is not UTF-8"))?;
        let (name, value) = header
            .split_once(':')
            .ok_or_else(|| invalid("invalid LSP header"))?;
        if name.eq_ignore_ascii_case("Content-Length") {
            if size.is_some() {
                return Err(invalid("duplicate Content-Length"));
            }
            let length = value
                .trim()
                .parse::<usize>()
                .map_err(|_| invalid("invalid Content-Length"))?;
            if length > BODY_LIMIT {
                return Err(invalid("LSP body exceeds 64 MiB"));
            }
            size = Some(length);
        }
    }
    let mut body = vec![0; size.ok_or_else(|| invalid("missing Content-Length"))?];
    reader.read_exact(&mut body)?;
    Ok(Some(body))
}

pub fn write(writer: &mut impl Write, body: &[u8]) -> io::Result<()> {
    write!(writer, "Content-Length: {}\r\n\r\n", body.len())?;
    writer.write_all(body)?;
    writer.flush()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    #[test]
    fn unicode_frames_are_counted_in_bytes_and_remain_separate() {
        let mut wire = Vec::new();
        write(&mut wire, "α😀".as_bytes()).unwrap();
        write(&mut wire, b"{}").unwrap();
        let mut reader = Cursor::new(wire);
        assert_eq!(read(&mut reader).unwrap().unwrap(), "α😀".as_bytes());
        assert_eq!(read(&mut reader).unwrap().unwrap(), b"{}");
        assert_eq!(read(&mut reader).unwrap(), None);
    }

    #[test]
    fn malformed_or_unbounded_frames_fail_before_forwarding() {
        for wire in [
            b"Content-Length: 2\r\nContent-Length: 2\r\n\r\n{}".to_vec(),
            b"Content-Length: 999999999\r\n\r\n".to_vec(),
            b"Content-Length: 10\r\n\r\n{}".to_vec(),
            b"Content-Length: 2\n\n{}".to_vec(),
            vec![b'x'; HEADER_LIMIT + 1],
        ] {
            assert!(read(&mut Cursor::new(wire)).is_err());
        }
    }
}
