namespace model

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

end model
