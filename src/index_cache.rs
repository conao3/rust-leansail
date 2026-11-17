use std::fs::{self, File};
use std::io::{self, Read};
use std::path::{Path, PathBuf};

use serde_json::{Value, json};
use sha2::{Digest, Sha256};

leanfran::lean! {
    pub mod model;
    r#"
    def cacheValid (cachedFiles files : String) (cachedVersion version : UInt64)
        (cachedToolchain toolchain cachedPayload payload : String) : Bool :=
      cachedFiles == files && cachedVersion == version &&
      cachedToolchain == toolchain && cachedPayload == payload

    theorem valid_requires_current_files (cachedFiles files : String)
        (cachedVersion version : UInt64) (cachedToolchain toolchain cachedPayload payload : String)
        (h : cacheValid cachedFiles files cachedVersion version cachedToolchain toolchain cachedPayload payload = true) :
        cachedFiles = files := by
      simp_all [cacheValid, Bool.and_assoc]

    theorem valid_requires_current_version (cachedFiles files : String)
        (cachedVersion version : UInt64) (cachedToolchain toolchain cachedPayload payload : String)
        (h : cacheValid cachedFiles files cachedVersion version cachedToolchain toolchain cachedPayload payload = true) :
        cachedVersion = version := by
      simp_all [cacheValid, Bool.and_assoc]

    theorem valid_requires_current_toolchain (cachedFiles files : String)
        (cachedVersion version : UInt64) (cachedToolchain toolchain cachedPayload payload : String)
        (h : cacheValid cachedFiles files cachedVersion version cachedToolchain toolchain cachedPayload payload = true) :
        cachedToolchain = toolchain := by
      simp_all [cacheValid, Bool.and_assoc]

    theorem valid_requires_current_payload (cachedFiles files : String)
        (cachedVersion version : UInt64) (cachedToolchain toolchain cachedPayload payload : String)
        (h : cacheValid cachedFiles files cachedVersion version cachedToolchain toolchain cachedPayload payload = true) :
        cachedPayload = payload := by
      simp_all [cacheValid, Bool.and_assoc]

    theorem matching_cache_is_valid (files toolchain payload : String) (version : UInt64) :
        cacheValid files files version version toolchain toolchain payload payload = true := by
      simp [cacheValid]
    "#
}

fn string<'a>(value: &'a Value, key: &str) -> io::Result<&'a str> {
    value[key]
        .as_str()
        .ok_or_else(|| io::Error::other(format!("索引キャッシュの {key} が文字列ではありません")))
}

fn hash_file(path: &Path) -> io::Result<String> {
    let mut file = File::open(path)?;
    let mut hash = Sha256::new();
    let mut buffer = [0u8; 64 * 1024];
    loop {
        let length = file.read(&mut buffer)?;
        if length == 0 {
            break;
        }
        hash.update(&buffer[..length]);
    }
    Ok(hex(&hash.finalize()))
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

pub fn run(commit: bool) -> io::Result<i32> {
    #[cfg(target_os = "linux")]
    unsafe {
        if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL) != 0 {
            return Err(io::Error::last_os_error());
        }
        if libc::getppid() == 1 {
            libc::_exit(1);
        }
    }
    let input: Value = serde_json::from_reader(io::stdin())?;
    let toolchain = string(&input, "toolchain")?;
    let files = input["files"]
        .as_array()
        .ok_or_else(|| io::Error::other("索引キャッシュのファイル一覧が必要です"))?;
    let mut entries = files
        .iter()
        .map(|path| {
            let path = path
                .as_str()
                .ok_or_else(|| io::Error::other("索引ファイルのパスが文字列ではありません"))?;
            Ok(json!([path, hash_file(Path::new(path))?]))
        })
        .collect::<io::Result<Vec<_>>>()?;
    entries.sort_by_key(|entry| entry[0].as_str().unwrap().to_owned());
    let working_directory = std::env::current_dir()?;
    let manifest = working_directory
        .ancestors()
        .map(|directory| directory.join("lake-manifest.json"))
        .find(|path| path.is_file())
        .map(|path| hash_file(&path))
        .transpose()?;
    let identity = json!({
        "toolchain": toolchain,
        "backend": hash_file(Path::new(string(&input, "backend")?))?,
        "host": hash_file(&std::env::current_exe()?)?,
        "source_path": input["source_path"],
        "search_path": input["search_path"],
        "working_directory": working_directory,
        "manifest": manifest,
        "schema": 1,
    });
    let key = hex(&Sha256::digest(serde_json::to_vec(&json!([
        identity, entries
    ]))?));
    let root = if let Some(root) = std::env::var_os("LEANSAIL_INDEX_CACHE_DIR") {
        PathBuf::from(root)
    } else if let Some(root) = std::env::var_os("XDG_CACHE_HOME") {
        PathBuf::from(root).join("leansail/index")
    } else {
        PathBuf::from(std::env::var_os("HOME").ok_or_else(|| io::Error::other("HOME が必要です"))?)
            .join(".cache/leansail/index")
    };
    let directory = root.join(&key);
    fs::create_dir_all(&directory)?;
    let payload = directory.join("snapshot.bin");
    let metadata = directory.join("metadata.json");
    if commit {
        let value = json!({"key":key, "version":1, "toolchain":toolchain, "payload_sha256":hash_file(&payload)?});
        let temporary = directory.join(format!("metadata.{}.json", std::process::id()));
        fs::write(&temporary, serde_json::to_vec(&value)?)?;
        fs::rename(temporary, &metadata)?;
        println!(
            "{}",
            json!({"path":payload, "key":key, "valid":true, "files":files.len()})
        );
        return Ok(0);
    }
    let stored = match fs::read(&metadata) {
        Ok(bytes) => serde_json::from_slice::<Value>(&bytes).ok(),
        Err(error) if error.kind() == io::ErrorKind::NotFound => None,
        Err(error) => return Err(error),
    };
    let valid = if let Some(stored) = &stored {
        match (
            stored["key"].as_str(),
            stored["version"].as_u64(),
            stored["toolchain"].as_str(),
            stored["payload_sha256"].as_str(),
        ) {
            (
                Some(cached_files),
                Some(cached_version),
                Some(cached_toolchain),
                Some(cached_payload),
            ) => match hash_file(&payload) {
                Ok(payload_hash) => model::cache_valid(
                    cached_files,
                    &key,
                    cached_version,
                    1,
                    cached_toolchain,
                    toolchain,
                    cached_payload,
                    &payload_hash,
                ),
                Err(error) if error.kind() == io::ErrorKind::NotFound => false,
                Err(error) => return Err(error),
            },
            _ => false,
        }
    } else {
        false
    };
    println!(
        "{}",
        json!({"path":payload, "key":key, "valid":valid, "files":files.len()})
    );
    Ok(0)
}
