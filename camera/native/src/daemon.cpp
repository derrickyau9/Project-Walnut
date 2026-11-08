#include "hf/daemon.hpp"
#include "hf/core.hpp"
#include "hf/dng.hpp"
#include "hf/calibration.hpp"
#include <sqlite3.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cerrno>
#include <cmath>
#include <condition_variable>
#include <cstdlib>
#include <functional>
#include <limits>
#include <map>
#include <mutex>
#include <set>
#include <thread>
#include <utility>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <bcrypt.h>
#else
#include <fcntl.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

namespace hf {
namespace {
using Clock = std::chrono::steady_clock;

std::int64_t unix_ms() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
}

Json defaults() {
    return {{"sensor_mode", "sim-small"}, {"requested_exposure_us", 10000},
            {"requested_analogue_gain", 1.0}, {"output_format", "dng"},
            {"simulation_scene", "scene"}};
}

bool terminal(const std::string& status) {
    return status == "complete" || status == "failed" || status == "cancelled" || status == "unknown";
}

bool valid_uuid(const std::string& id) {
    if (id.size() != 36) return false;
    for (std::size_t i = 0; i < id.size(); ++i) {
        if (i == 8 || i == 13 || i == 18 || i == 23) {
            if (id[i] != '-') return false;
        } else if (!((id[i] >= '0' && id[i] <= '9') || (id[i] >= 'a' && id[i] <= 'f'))) return false;
    }
    return true;
}

std::string random_token() {
    unsigned char bytes[32]{};
#ifdef _WIN32
    if (BCryptGenRandom(nullptr, bytes, sizeof(bytes), BCRYPT_USE_SYSTEM_PREFERRED_RNG) != 0)
        throw DaemonError("internal_error", "operating system random source failed");
#else
    const int fd = ::open("/dev/urandom", O_RDONLY);
    if (fd < 0) throw DaemonError("internal_error", "cannot open operating system random source");
    std::size_t offset = 0;
    while (offset < sizeof(bytes)) {
        const auto count = ::read(fd, bytes + offset, sizeof(bytes) - offset);
        if (count <= 0) { ::close(fd); throw DaemonError("internal_error", "operating system random source failed"); }
        offset += static_cast<std::size_t>(count);
    }
    ::close(fd);
#endif
    constexpr char hex[] = "0123456789abcdef";
    std::string result;
    result.reserve(64);
    for (const auto value : bytes) {
        result.push_back(hex[value >> 4]); result.push_back(hex[value & 15]);
    }
    return result;
}

bool equal_token(const std::string& first, const std::string& second) {
    if (first.size() != second.size()) return false;
    unsigned char different = 0;
    for (std::size_t i = 0; i < first.size(); ++i)
        different |= static_cast<unsigned char>(first[i] ^ second[i]);
    return different == 0;
}

bool integer(const Json& value) { return value.is_number_integer() && !value.is_boolean(); }

Json validated_settings(const Json& settings) {
    if (!settings.is_object()) throw DaemonError("invalid_request", "settings must be an object");
    for (auto item = settings.begin(); item != settings.end(); ++item)
        if (!defaults().contains(item.key())) throw DaemonError("invalid_request", "unsupported settings field");
    for (const auto& name : {"sensor_mode", "requested_exposure_us", "requested_analogue_gain", "output_format"})
        if (!settings.contains(name)) throw DaemonError("invalid_request", "capture requires complete settings");
    const auto mode = settings.at("sensor_mode");
    if (!mode.is_string() || (mode != "sim-small" && mode != "sim-full") || settings.at("output_format") != "dng")
        throw DaemonError("invalid_request", "unsupported sensor mode or output format");
    const auto& exposure = settings.at("requested_exposure_us");
    if (!integer(exposure) || exposure < 100 || exposure > 30000000)
        throw DaemonError("invalid_request", "exposure must be an integer from 100 to 30000000 us");
    const auto& gain = settings.at("requested_analogue_gain");
    if (!gain.is_number() || !std::isfinite(gain.get<double>()) || gain < 1 || gain > 16)
        throw DaemonError("invalid_request", "gain must be between 1 and 16");
    const auto scene = settings.value("simulation_scene", Json("scene"));
    if (!scene.is_string() || (scene != "scene" && scene != "dark" && scene != "flat"))
        throw DaemonError("invalid_request", "unsupported simulated scene");
    return {{"sensor_mode", mode}, {"requested_exposure_us", exposure.get<std::uint32_t>()},
            {"requested_analogue_gain", gain.get<double>()}, {"output_format", "dng"},
            {"simulation_scene", scene}};
}

constexpr const char* settings_format = "hf-device-settings-v1";
constexpr const char* calibration_format = "hf-active-calibration-v1";

Json strict_json(const std::string& text, std::size_t maximum) {
    if (text.empty() || text.size() > maximum || text.find('\0') != std::string::npos)
        throw std::runtime_error("JSON state is empty, oversized or contains NUL");
    std::vector<std::set<std::string>> objects;
    const auto callback = [&](int depth, Json::parse_event_t event, Json& parsed) {
        if (depth > 16) throw std::runtime_error("JSON state nesting exceeds limit");
        if (event == Json::parse_event_t::object_start) objects.emplace_back();
        if (event == Json::parse_event_t::key &&
                (objects.empty() || !objects.back().insert(parsed.get<std::string>()).second))
            throw std::runtime_error("duplicate JSON state key");
        if (event == Json::parse_event_t::object_end) objects.pop_back();
        return true;
    };
    return Json::parse(text, callback);
}

Json settings_envelope(const Json& value) {
    return {{"format", settings_format}, {"settings", value}};
}

Json load_settings(const std::string& serialized) {
    try {
        const auto envelope = strict_json(serialized, 16384);
        if (!envelope.is_object() || envelope.size() != 2 || envelope.at("format") != settings_format ||
                !envelope.at("settings").is_object() || envelope.at("settings").size() != defaults().size())
            throw std::runtime_error("unsupported settings state format");
        return validated_settings(envelope.at("settings"));
    } catch (const std::exception&) {
        throw DaemonError("journal_error", "invalid or unsupported persisted device settings; preserve state and repair explicitly");
    }
}

Json calibration_envelope(const Json& binding) {
    return {{"format", calibration_format}, {"binding", binding}};
}

Json load_calibration(const std::string& serialized) {
    try {
        const auto envelope = strict_json(serialized, 131072 + 128);
        if (!envelope.is_object() || envelope.size() != 2 || envelope.at("format") != calibration_format)
            throw std::runtime_error("unsupported calibration state format");
        return envelope.at("binding").is_null() ? Json(nullptr) : validate_calibration_binding(envelope.at("binding"));
    } catch (const std::exception&) {
        throw DaemonError("journal_error", "invalid or unsupported persisted calibration binding; preserve state and repair explicitly");
    }
}

class DirectoryLock {
public:
    explicit DirectoryLock(const std::filesystem::path& path) {
#ifdef _WIN32
        handle_ = CreateFileW(path.c_str(), GENERIC_READ | GENERIC_WRITE, 0, nullptr,
                              OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (handle_ == INVALID_HANDLE_VALUE) {
            const auto error = GetLastError();
            throw DaemonError(error == ERROR_SHARING_VIOLATION || error == ERROR_LOCK_VIOLATION ?
                              "already_running" : "storage_error", "cannot exclusively lock data directory");
        }
#else
        fd_ = ::open(path.c_str(), O_RDWR | O_CREAT, 0600);
        if (fd_ < 0) throw DaemonError("storage_error", "cannot open data directory lock");
        if (::flock(fd_, LOCK_EX | LOCK_NB) != 0) {
            ::close(fd_); fd_ = -1;
            throw DaemonError("already_running", "another service owns this data directory");
        }
#endif
    }
    ~DirectoryLock() {
#ifdef _WIN32
        if (handle_ != INVALID_HANDLE_VALUE) CloseHandle(handle_);
#else
        if (fd_ >= 0) { ::flock(fd_, LOCK_UN); ::close(fd_); }
#endif
    }
private:
#ifdef _WIN32
    HANDLE handle_ = INVALID_HANDLE_VALUE;
#else
    int fd_ = -1;
#endif
};

// File publication and retained lookup must refer to the same startup photo
// directory. These checks detect redirects/replacements at capture boundaries;
// they do not claim atomic protection from a hostile same-UID namespace race.
class PhotoDirectory {
public:
    explicit PhotoDirectory(std::filesystem::path path) : path_(std::move(path)), identity_(read()) {}
    void verify() const {
        if (read() != identity_)
            throw DaemonError("storage_error", "photo directory identity changed");
    }
private:
    std::filesystem::path path_;
    std::pair<std::uintmax_t, std::uintmax_t> identity_;
    std::pair<std::uintmax_t, std::uintmax_t> read() const {
        std::error_code error;
        const auto status = std::filesystem::symlink_status(path_, error);
        if (error || !std::filesystem::is_directory(status) ||
                std::filesystem::canonical(path_, error) != path_ || error)
            throw DaemonError("storage_error", "photo directory is unavailable or redirected");
#ifdef _WIN32
        const auto handle = CreateFileW(path_.c_str(), FILE_READ_ATTRIBUTES,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr, OPEN_EXISTING,
            FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT, nullptr);
        if (handle == INVALID_HANDLE_VALUE)
            throw DaemonError("storage_error", "cannot inspect photo directory identity");
        BY_HANDLE_FILE_INFORMATION info{};
        const bool inspected = GetFileInformationByHandle(handle, &info) != 0;
        CloseHandle(handle);
        if (!inspected || !(info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) ||
                (info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT))
            throw DaemonError("storage_error", "photo directory is unavailable or redirected");
        return {info.dwVolumeSerialNumber,
            (static_cast<std::uintmax_t>(info.nFileIndexHigh) << 32) | info.nFileIndexLow};
#else
        struct stat info{};
        if (::lstat(path_.c_str(), &info) != 0 || !S_ISDIR(info.st_mode))
            throw DaemonError("storage_error", "cannot inspect photo directory identity");
        return {static_cast<std::uintmax_t>(info.st_dev), static_cast<std::uintmax_t>(info.st_ino)};
#endif
    }
};

struct Database {
    sqlite3* value = nullptr;
    ~Database() { if (value) sqlite3_close_v2(value); }
};

class Statement {
public:
    Statement(sqlite3* db, const char* sql) : db_(db) {
        if (sqlite3_prepare_v2(db, sql, -1, &value_, nullptr) != SQLITE_OK)
            throw DaemonError("journal_error", sqlite3_errmsg(db));
    }
    ~Statement() { if (value_) sqlite3_finalize(value_); }
    void bind(int index, const std::string& value) {
        if (sqlite3_bind_text(value_, index, value.c_str(), static_cast<int>(value.size()), SQLITE_TRANSIENT) != SQLITE_OK)
            throw DaemonError("journal_error", sqlite3_errmsg(db_));
    }
    void bind_integer(int index, std::int64_t value) {
        if (sqlite3_bind_int64(value_, index, value) != SQLITE_OK)
            throw DaemonError("journal_error", sqlite3_errmsg(db_));
    }
    int step() {
        const auto result = sqlite3_step(value_);
        if (result != SQLITE_ROW && result != SQLITE_DONE)
            throw DaemonError("journal_error", sqlite3_errmsg(db_));
        return result;
    }
    std::int64_t number(int column) { return sqlite3_column_int64(value_, column); }
    std::string text(int column) {
        const auto* value = sqlite3_column_text(value_, column);
        if (!value) throw DaemonError("journal_error", "unexpected null journal field");
        const auto bytes = sqlite3_column_bytes(value_, column);
        if (bytes < 0 || bytes > 1024 * 1024)
            throw DaemonError("journal_error", "journal field exceeds metadata byte limit");
        return std::string(reinterpret_cast<const char*>(value), static_cast<std::size_t>(bytes));
    }
private:
    sqlite3* db_;
    sqlite3_stmt* value_ = nullptr;
};

struct OutputSnapshot {
    std::uint64_t device, file_id, bytes, modified, modified_ns, changed, changed_ns;
    bool operator==(const OutputSnapshot& other) const {
        return device == other.device && file_id == other.file_id && bytes == other.bytes &&
            modified == other.modified && modified_ns == other.modified_ns &&
            changed == other.changed && changed_ns == other.changed_ns;
    }
};

class ExistingOutput {
public:
    explicit ExistingOutput(const std::filesystem::path& path) {
#ifdef _WIN32
        handle_ = CreateFileW(path.c_str(), GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ, nullptr,
            OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT, nullptr);
        if (handle_ == INVALID_HANDLE_VALUE) throw DaemonError("storage_error", "cannot reopen owned output");
#else
        fd_ = ::open(path.c_str(), O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
        if (fd_ < 0) throw DaemonError("storage_error", "cannot reopen owned output");
#endif
    }
    ~ExistingOutput() {
#ifdef _WIN32
        if (handle_ != INVALID_HANDLE_VALUE) CloseHandle(handle_);
#else
        if (fd_ >= 0) ::close(fd_);
#endif
    }
    ExistingOutput(const ExistingOutput&) = delete;
    ExistingOutput& operator=(const ExistingOutput&) = delete;
    OutputSnapshot snapshot() const {
#ifdef _WIN32
        BY_HANDLE_FILE_INFORMATION info{};
        FILE_BASIC_INFO basic{};
        if (!GetFileInformationByHandle(handle_, &info) || GetFileType(handle_) != FILE_TYPE_DISK ||
                !GetFileInformationByHandleEx(handle_, FileBasicInfo, &basic, sizeof(basic)) ||
                (info.dwFileAttributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)))
            throw DaemonError("storage_error", "output is not a regular owned file");
        return {info.dwVolumeSerialNumber,
            (static_cast<std::uint64_t>(info.nFileIndexHigh) << 32) | info.nFileIndexLow,
            (static_cast<std::uint64_t>(info.nFileSizeHigh) << 32) | info.nFileSizeLow,
            static_cast<std::uint64_t>(basic.LastWriteTime.QuadPart), 0,
            static_cast<std::uint64_t>(basic.ChangeTime.QuadPart), 0};
#else
        struct stat info{};
        if (::fstat(fd_, &info) != 0 || !S_ISREG(info.st_mode) || info.st_size < 0)
            throw DaemonError("storage_error", "output is not a regular owned file");
#ifdef __APPLE__
        const auto modified = info.st_mtimespec, changed = info.st_ctimespec;
#else
        const auto modified = info.st_mtim, changed = info.st_ctim;
#endif
        return {static_cast<std::uint64_t>(info.st_dev), static_cast<std::uint64_t>(info.st_ino),
            static_cast<std::uint64_t>(info.st_size), static_cast<std::uint64_t>(modified.tv_sec),
            static_cast<std::uint64_t>(modified.tv_nsec), static_cast<std::uint64_t>(changed.tv_sec),
            static_cast<std::uint64_t>(changed.tv_nsec)};
#endif
    }
    std::uint64_t checksum(std::uint64_t expected_bytes) {
        std::array<unsigned char, 65536> buffer{};
        std::uint64_t total = 0, hash = 14695981039346656037ull;
        for (;;) {
#ifdef _WIN32
            DWORD count = 0;
            if (!ReadFile(handle_, buffer.data(), static_cast<DWORD>(buffer.size()), &count, nullptr))
                throw DaemonError("storage_error", "owned output read failed");
#else
            const auto count = ::read(fd_, buffer.data(), buffer.size());
            if (count < 0 && errno == EINTR) continue;
            if (count < 0) throw DaemonError("storage_error", "owned output read failed");
#endif
            if (count == 0) break;
            total += static_cast<std::uint64_t>(count);
            if (total > expected_bytes) throw DaemonError("storage_error", "owned output grew during verification");
            for (std::size_t i = 0; i < static_cast<std::size_t>(count); ++i)
                hash = (hash ^ buffer[i]) * 1099511628211ull;
        }
        if (total != expected_bytes) throw DaemonError("storage_error", "owned output size changed");
        return hash;
    }
    void sync() const {
#ifdef _WIN32
        if (!FlushFileBuffers(handle_)) throw DaemonError("storage_error", "output file sync failed");
#else
        if (::fsync(fd_) != 0) throw DaemonError("storage_error", "output file sync failed");
#endif
    }
private:
#ifdef _WIN32
    HANDLE handle_ = INVALID_HANDLE_VALUE;
#else
    int fd_ = -1;
#endif
};

bool owned_identity(const OutputSnapshot& snapshot, const DngOutputReceipt& receipt) {
    return receipt.created && snapshot.device == receipt.device && snapshot.file_id == receipt.file_id;
}

OutputSnapshot verify_output(const std::filesystem::path& path, const DngOutputReceipt& receipt, bool sync) {
    ExistingOutput output(path);
    const auto before = output.snapshot();
    if (!receipt.complete || !owned_identity(before, receipt) || before.bytes != receipt.bytes)
        throw DaemonError("storage_error", "output differs from the file created by this capture");
    if (output.checksum(receipt.bytes) != receipt.checksum || !(output.snapshot() == before))
        throw DaemonError("storage_error", "owned DNG bytes changed before publication");
    if (sync) output.sync();
    if (!(output.snapshot() == before)) throw DaemonError("storage_error", "owned output changed during sync");
    return before;
}

void verify_snapshot(const std::filesystem::path& path, const OutputSnapshot& expected) {
    ExistingOutput output(path);
    if (!(output.snapshot() == expected)) throw DaemonError("storage_error", "owned output changed before publication");
}

void publish_file(const std::filesystem::path& temporary, const std::filesystem::path& final,
                  bool& owns_staging, bool& published) {
#ifdef _WIN32
    if (!MoveFileExW(temporary.c_str(), final.c_str(), MOVEFILE_WRITE_THROUGH))
        throw DaemonError("storage_error", "exclusive output rename failed");
    published = true;
    owns_staging = false;
#else
    // Same-directory link/unlink atomically publishes without overwriting a
    // pre-existing final path; plain POSIX rename would overwrite that path.
    if (::link(temporary.c_str(), final.c_str()) != 0)
        throw DaemonError("storage_error", "exclusive output publication failed");
    published = true;
    if (::unlink(temporary.c_str()) != 0)
        throw DaemonError("storage_error", "temporary output removal failed");
    owns_staging = false;
    const int fd = ::open(final.parent_path().c_str(), O_RDONLY | O_DIRECTORY);
    if (fd < 0) throw DaemonError("storage_error", "cannot open output directory for sync");
    const int result = ::fsync(fd);
    ::close(fd);
    if (result != 0) throw DaemonError("storage_error", "output directory sync failed");
#endif
}
} // namespace

DaemonError::DaemonError(std::string code, const std::string& message)
    : std::runtime_error(message), code_(std::move(code)) {}

struct CameraDaemon::Impl {
    std::filesystem::path root, photos;
    std::unique_ptr<DirectoryLock> directory_lock;
    std::unique_ptr<PhotoDirectory> photo_directory;
    Database database;
    std::mutex mutex;
    std::condition_variable wake;
    double lease_seconds;
    bool closed = false, connected = true, journal_fault = false;
    std::uint64_t epoch = 0, pipeline_generation = 0;
    Json settings = defaults(), calibration = nullptr, control = nullptr;
    Clock::time_point control_expiry{};
    std::string active;
    std::atomic_bool cancelled{false};
    std::thread worker;
    std::map<std::string, Json> uncertain;
    SyntheticCamera camera;
    std::string library_id;
    std::map<std::string, std::string> library_pins;

    explicit Impl(const std::filesystem::path& directory, double seconds) : lease_seconds(seconds) {
        if (!std::isfinite(seconds) || seconds <= 0 || seconds > 3600)
            throw DaemonError("invalid_request", "lease_seconds must be greater than zero and at most 3600");
        std::filesystem::create_directories(directory);
        root = std::filesystem::canonical(directory);
        directory_lock = std::make_unique<DirectoryLock>(root / ".service.lock");
        photos = root / "photos";
        std::filesystem::create_directories(photos);
        photo_directory = std::make_unique<PhotoDirectory>(photos);
        const auto database_path = (root / "journal.sqlite3").u8string();
        if (sqlite3_open_v2(database_path.c_str(), &database.value,
                            SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX, nullptr) != SQLITE_OK)
            throw DaemonError("journal_error", "cannot open native request journal");
        sqlite3_busy_timeout(database.value, 1000);
        exec("PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;");
        exec("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);"
             "CREATE TABLE IF NOT EXISTS captures(request_id TEXT PRIMARY KEY,request_json TEXT NOT NULL,"
             "record_json TEXT NOT NULL,status TEXT NOT NULL);");
        std::string authority;
        { Statement statement(database.value, "SELECT value FROM meta WHERE key='authority'");
          if (statement.step() == SQLITE_ROW) authority = statement.text(0); }
        if (authority.empty()) {
            Statement statement(database.value, "SELECT request_id FROM captures LIMIT 1");
            if (statement.step() == SQLITE_ROW)
                throw DaemonError("journal_error", "populated journal is not native; use a separate data directory");
        } else if (authority != "native") {
            throw DaemonError("journal_error", "unsupported journal authority");
        }
        bool have_settings = false, have_settings_format = false;
        std::string serialized_settings, stored_settings_format;
        {
            Statement statement(database.value, "SELECT key,value,typeof(value) FROM meta WHERE key IN ('settings','settings_format')");
            while (statement.step() == SQLITE_ROW) {
                if (statement.text(2) != "text") throw DaemonError("journal_error", "persisted settings must be text");
                if (statement.text(0) == "settings") { have_settings = true; serialized_settings = statement.text(1); }
                else { have_settings_format = true; stored_settings_format = statement.text(1); }
            }
        }
        if (have_settings != have_settings_format || (have_settings_format && stored_settings_format != settings_format))
            throw DaemonError("journal_error", "missing or unsupported persisted settings marker; preserve state and repair explicitly");
        if (have_settings) settings = load_settings(serialized_settings);
        bool have_calibration = false, have_calibration_format = false;
        std::string serialized_calibration, stored_calibration_format;
        {
            Statement statement(database.value, "SELECT key,value,typeof(value) FROM meta WHERE key IN ('calibration_binding','calibration_binding_format')");
            while (statement.step() == SQLITE_ROW) {
                if (statement.text(2) != "text") throw DaemonError("journal_error", "persisted calibration binding must be text");
                if (statement.text(0) == "calibration_binding") { have_calibration = true; serialized_calibration = statement.text(1); }
                else { have_calibration_format = true; stored_calibration_format = statement.text(1); }
            }
        }
        if (have_calibration != have_calibration_format || (have_calibration_format && stored_calibration_format != calibration_format))
            throw DaemonError("journal_error", "missing or unsupported persisted calibration marker; preserve state and repair explicitly");
        if (have_calibration) calibration = load_calibration(serialized_calibration);
        { Statement statement(database.value, "SELECT value FROM meta WHERE key='epoch'");
          if (statement.step() == SQLITE_ROW) {
              const auto value = statement.text(0);
              if (value.empty() || value.size() > 20 ||
                  !std::all_of(value.begin(), value.end(), [](char c) { return c >= '0' && c <= '9'; }))
                  throw DaemonError("journal_error", "invalid persisted epoch");
              std::size_t consumed = 0;
              try { epoch = std::stoull(value, &consumed); }
              catch (...) { throw DaemonError("journal_error", "invalid persisted epoch"); }
              if (consumed != value.size() || epoch == 0 || std::to_string(epoch) != value)
                  throw DaemonError("journal_error", "invalid persisted epoch");
          } }
        if (epoch == (std::numeric_limits<std::uint64_t>::max)())
            throw DaemonError("journal_error", "control epoch exhausted");
        ++epoch;
        // Audit persisted identities and immutable settings before interpreting
        // any record as success or reconciling an interrupted request. A bad
        // row is evidence to preserve, not a record to repair or re-execute.
        std::vector<Json> interrupted;
        {
            Statement statement(database.value, "SELECT request_id,request_json,record_json,status FROM captures");
            while (statement.step() == SQLITE_ROW) {
                try {
                    auto record = validate_row(statement.text(0), statement.text(1), statement.text(2), statement.text(3));
                    if (record.at("status") == "accepted" || record.at("status") == "capturing")
                        interrupted.push_back(std::move(record));
                } catch (const DaemonError&) { journal_fault = true; }
            }
        }
        transaction([&] {
            set_meta("authority", "native"); set_meta("epoch", std::to_string(epoch));
            if (!have_settings) {
                set_meta("settings", settings_envelope(settings).dump());
                set_meta("settings_format", settings_format);
            }
            if (!have_calibration) {
                set_meta("calibration_binding", calibration_envelope(calibration).dump());
                set_meta("calibration_binding_format", calibration_format);
            }
            if (!journal_fault) {
                for (auto& record : interrupted) {
                    record["status"] = "unknown";
                    record["error"] = "native authority restarted before completion was confirmed; do not re-execute";
                    save(record);
                }
            }
        });
        if (!journal_fault) library_init();
    }

    ~Impl() {
        cancelled.store(true);
        wake.notify_all();
        if (worker.joinable()) worker.join();
    }

    void exec(const char* sql) {
        char* error = nullptr;
        if (sqlite3_exec(database.value, sql, nullptr, nullptr, &error) != SQLITE_OK) {
            const std::string message = error ? error : "SQLite operation failed";
            sqlite3_free(error);
            throw DaemonError("journal_error", message);
        }
    }

    void transaction(const std::function<void()>& action) {
        try { exec("BEGIN IMMEDIATE"); action(); exec("COMMIT"); }
        catch (...) {
            sqlite3_exec(database.value, "ROLLBACK", nullptr, nullptr, nullptr);
            journal_fault = true;
            throw;
        }
    }

    void set_meta(const std::string& key, const std::string& value) {
        Statement statement(database.value, "INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)");
        statement.bind(1, key); statement.bind(2, value); statement.step();
    }

    void ensure_open() {
        if (closed) throw DaemonError("closed", "native authority is closed");
    }

    void ensure_journal() {
        ensure_open();
        if (journal_fault) throw DaemonError("journal_error", "journal unavailable; restart and reconcile request IDs");
    }

    void expire_control() {
        if (!control.is_null() && Clock::now() >= control_expiry) control = nullptr;
    }

    void authorize(const std::string& token, std::uint64_t requested_epoch) {
        ensure_journal(); expire_control();
        if (control.is_null() || requested_epoch != epoch ||
            !equal_token(token, control.at("token").get<std::string>()))
            throw DaemonError("control_denied", "control token or epoch is invalid");
    }

    void renew() {
        control_expiry = Clock::now() + std::chrono::duration_cast<Clock::duration>(
            std::chrono::duration<double>(lease_seconds));
    }

    Json persist_calibration(Json binding, const std::string& test_fault) {
        if (!connected) throw DaemonError("device_disconnected", "reconnect the simulated device explicitly");
        if (!active.empty()) throw DaemonError("camera_busy", "capture in progress");
        if (!test_fault.empty() && test_fault != "binding_commit_failure" && test_fault != "crash_after_binding_commit")
            throw DaemonError("invalid_request", "unsupported calibration test fault");
        if (!binding.is_null()) binding = validate_calibration_binding(binding);
        transaction([&] {
            set_meta("calibration_binding", calibration_envelope(binding).dump());
            if (test_fault == "binding_commit_failure") throw DaemonError("journal_error", "injected calibration binding commit failure");
        });
        if (test_fault == "crash_after_binding_commit") std::_Exit(89);
        calibration = std::move(binding);
        return calibration_descriptor(calibration);
    }

    Json query(const std::string& id) {
        const auto pending = uncertain.find(id);
        if (pending != uncertain.end()) return pending->second;
        Statement statement(database.value, "SELECT request_json,record_json,status FROM captures WHERE request_id=?");
        statement.bind(1, id);
        if (statement.step() != SQLITE_ROW) return nullptr;
        return validate_row(id, statement.text(0), statement.text(1), statement.text(2));
    }

    Json validate_row(const std::string& id, const std::string& payload_text,
                      const std::string& record_text, const std::string& row_status) {
        try {
            const auto payload = Json::parse(payload_text);
            const auto record = Json::parse(record_text);
            if (!valid_uuid(id) || !payload.is_object() || !record.is_object() || record_text != record.dump())
                throw std::runtime_error("invalid row identity or object");
            const std::set<std::string> statuses = {"accepted", "capturing", "complete", "failed", "cancelled", "unknown"};
            if (!statuses.count(row_status) || record.at("status") != row_status ||
                record.at("request_id") != id || record.at("capture_id") != id ||
                record.at("backend") != "native" || record.at("authority") != "native" ||
                record.at("sensor_id") != "HF-Sim")
                throw std::runtime_error("record identity does not match its journal row");
            std::string calibration_id = "synthetic-uncalibrated";
            if (record.contains("capture_record_version")) {
                if (!integer(record.at("capture_record_version")) || record.at("capture_record_version") != 2 ||
                        !record.at("hardware_calibrated").is_boolean() || record.at("hardware_calibrated") != false ||
                        !record.at("pixel_corrections_applied").is_boolean() || record.at("pixel_corrections_applied") != false)
                    throw std::runtime_error("unsupported capture calibration record version");
                const auto binding = validate_calibration_binding(record.at("calibration_binding"));
                calibration_id = binding.at("binding_id").get<std::string>();
            } else if (record.contains("calibration_binding") || record.contains("hardware_calibrated") || record.contains("pixel_corrections_applied")) {
                throw std::runtime_error("binding data requires an explicit versioned capture record");
            }
            if (record.at("calibration_id") != calibration_id)
                throw std::runtime_error("capture calibration identity does not match its snapshot");
            Json stored_settings = Json::object();
            const auto names = defaults();
            for (auto item = names.begin(); item != names.end(); ++item)
                stored_settings[item.key()] = record.at(item.key());
            auto canonical = validated_settings(stored_settings);
            const auto fault = record.at("fault");
            const std::set<std::string> faults = {"delay", "storage_full", "disconnect", "corrupt_frame", "crash_after_accept",
                "crash_after_rename", "worker_start_failure", "admission_commit_failure", "completion_commit_failure"};
            if (!fault.is_null() && (!fault.is_string() || !faults.count(fault.get<std::string>())))
                throw std::runtime_error("invalid stored fault");
            const auto deadline = record.at("deadline_unix_ms");
            if (!deadline.is_null() && (!integer(deadline) || deadline < 0 || deadline > (std::numeric_limits<std::int64_t>::max)()))
                throw std::runtime_error("invalid stored deadline");
            canonical["fault"] = fault; canonical["deadline_unix_ms"] = deadline;
            if (payload != canonical || payload_text != canonical.dump())
                throw std::runtime_error("record differs from immutable request payload");
            for (const auto& key : {"actual_exposure_us", "actual_analogue_gain", "frame_id", "timestamp", "file_path", "error"})
                if (!record.contains(key)) throw std::runtime_error("record is missing capture outcome fields");
            if (row_status == "complete") {
                const auto expected_path = (photos / (id + ".dng")).u8string();
                const auto& timestamp = record.at("timestamp");
                if (record.at("actual_exposure_us") != canonical.at("requested_exposure_us") ||
                    !integer(record.at("actual_exposure_us")) ||
                    !record.at("actual_analogue_gain").is_number() ||
                    record.at("actual_analogue_gain") != canonical.at("requested_analogue_gain") ||
                    record.at("frame_id") != id || record.at("file_path") != expected_path ||
                    !timestamp.is_number() || !std::isfinite(timestamp.get<double>()) || timestamp <= 0 ||
                    !record.at("error").is_null() || !record.contains("bytes_written") ||
                    !integer(record.at("bytes_written")) || record.at("bytes_written") <= 0 ||
                    !record.contains("native_evidence") || !record.at("native_evidence").is_object())
                    throw std::runtime_error("completed record lacks consistent capture evidence");
                const auto& evidence = record.at("native_evidence");
                if (evidence.at("request_id") != id || evidence.at("capture_id") != id || evidence.at("frame_id") != id ||
                    evidence.at("sensor_id") != "HF-Sim" || evidence.at("calibration_id") != calibration_id ||
                    evidence.at("requested_exposure_us") != canonical.at("requested_exposure_us") ||
                    evidence.at("actual_exposure_us") != canonical.at("requested_exposure_us") ||
                    evidence.at("requested_analogue_gain") != canonical.at("requested_analogue_gain") ||
                    evidence.at("actual_analogue_gain") != canonical.at("requested_analogue_gain"))
                    throw std::runtime_error("completed native evidence does not match its request");
                // File removal by the owner is separate from journal integrity.
                // No file is opened, repaired or recreated while validating a row.
            } else if (row_status == "accepted" || row_status == "capturing") {
                for (const auto& key : {"actual_exposure_us", "actual_analogue_gain", "frame_id", "timestamp", "file_path", "error"})
                    if (!record.at(key).is_null()) throw std::runtime_error("incomplete record claims a capture outcome");
            } else if (!record.at("error").is_string() || record.at("error").get<std::string>().empty()) {
                throw std::runtime_error("terminal failure record lacks a reason");
            }
            return record;
        } catch (const std::exception&) {
            journal_fault = true;
            throw DaemonError("journal_error", "invalid persisted capture record; preserve the journal and reconcile offline");
        }
    }

    void save(const Json& record) {
        Statement statement(database.value, "UPDATE captures SET status=?,record_json=? WHERE request_id=?");
        statement.bind(1, record.at("status").get<std::string>());
        statement.bind(2, record.dump()); statement.bind(3, record.at("request_id").get<std::string>());
        statement.step();
        if (sqlite3_changes(database.value) != 1) throw DaemonError("journal_error", "request record disappeared");
        if (record.at("status") == "complete" && !library_id.empty()) library_capture(record);
    }

    #include "library_extension.inc"

    void check_capture(const Json& record) {
        if (cancelled.load(std::memory_order_relaxed)) throw DaemonError("cancelled", "capture cancelled");
        if (!record.at("deadline_unix_ms").is_null() &&
            unix_ms() >= record.at("deadline_unix_ms").get<std::int64_t>())
            throw DaemonError("deadline_expired", "capture deadline expired");
    }

    void run(Json record) noexcept {
        const auto id = record.at("request_id").get<std::string>();
        const auto temporary = photos / (id + ".dng.partial");
        const auto final = photos / (id + ".dng");
        bool owns_staging = false;
        bool published_by_worker = false;
        DngOutputReceipt output_receipt;
        try {
            { std::lock_guard<std::mutex> guard(mutex);
              record["status"] = "capturing";
              transaction([&] { save(record); }); }
            const auto fault = record.at("fault").is_null() ? std::string() : record.at("fault").get<std::string>();
            if (fault == "delay") {
                const auto until = Clock::now() + std::chrono::seconds(1);
                std::unique_lock<std::mutex> guard(mutex);
                while (Clock::now() < until) {
                    check_capture(record);
                    wake.wait_for(guard, std::chrono::milliseconds(5));
                }
            }
            check_capture(record);
            if (fault == "disconnect") {
                std::lock_guard<std::mutex> guard(mutex);
                connected = false; ++pipeline_generation;
                throw DaemonError("disconnect", "injected simulated device disconnect");
            }
            if (fault == "storage_full") throw DaemonError("storage_full", "injected simulated storage full");
            FrameRequest request;
            const bool full = record.at("sensor_mode") == "sim-full";
            request.width = full ? 6064u : 128u; request.height = full ? 4040u : 96u;
            request.exposure_us = record.at("requested_exposure_us").get<std::uint32_t>();
            request.gain = record.at("requested_analogue_gain").get<double>();
            request.scene = record.at("simulation_scene").get<std::string>(); request.request_id = id;
            CaptureControl capture_control;
            capture_control.cancelled = &cancelled;
            if (!record.at("deadline_unix_ms").is_null()) {
                const auto remaining = record.at("deadline_unix_ms").get<std::int64_t>() - unix_ms();
                if (remaining <= 0) throw DaemonError("deadline_expired", "capture deadline expired");
                if (remaining < 86400000) capture_control.deadline = Clock::now() + std::chrono::milliseconds(remaining);
            }
            auto frame = camera.capture(request, capture_control);
            if (fault == "corrupt_frame") frame.pixels[0] ^= 1;
            validate_frame(frame, capture_control);
            DngIdentity identity;
            identity.capture_id = id; identity.frame_id = id;
            identity.calibration_id = record.at("calibration_id").get<std::string>();
            const auto evidence = Json::parse(capture_metadata_json(frame, identity));
            photo_directory->verify();
            const auto bytes = write_dng(temporary, frame, identity, &owns_staging, &output_receipt);
            camera.recycle(std::move(frame));
            photo_directory->verify();
            const auto verified = verify_output(temporary, output_receipt, true);
            { std::lock_guard<std::mutex> guard(mutex);
              check_capture(record);
              photo_directory->verify();
              verify_snapshot(temporary, verified);
              publish_file(temporary, final, owns_staging, published_by_worker);
              photo_directory->verify();
              verify_output(final, output_receipt, false);
              if (fault == "crash_after_rename") std::_Exit(87);
              record["status"] = "complete";
              record["actual_exposure_us"] = evidence.at("actual_exposure_us");
              record["actual_analogue_gain"] = evidence.at("actual_analogue_gain");
              record["frame_id"] = id; record["timestamp"] = unix_ms() / 1000.0;
              record["file_path"] = final.u8string();
              record["native_evidence"] = evidence; record["bytes_written"] = bytes;
              transaction([&] {
                  save(record);
                  if (fault == "completion_commit_failure")
                      throw DaemonError("journal_error", "injected completion commit failure");
              }); }
        } catch (const std::exception& error) {
            std::lock_guard<std::mutex> guard(mutex);
            std::error_code file_error;
            const bool published = published_by_worker || std::filesystem::exists(final, file_error);
            const auto* capture_error = dynamic_cast<const CaptureError*>(&error);
            const auto* daemon_error = dynamic_cast<const DaemonError*>(&error);
            const bool was_cancelled = (capture_error && capture_error->code() == ErrorCode::Cancelled) ||
                                       (daemon_error && daemon_error->code() == "cancelled");
            record["status"] = published || file_error || journal_fault ? "unknown" : (was_cancelled ? "cancelled" : "failed");
            record["error"] = error.what();
            if (published) { record["file_path"] = nullptr; record["frame_id"] = nullptr; }
            if (journal_fault) uncertain[id] = record;
            else {
                try { transaction([&] { save(record); }); }
                catch (...) {
                    record["status"] = "unknown"; record["error"] = "journal failure; restart and reconcile";
                    uncertain[id] = record;
                }
            }
        } catch (...) {
            std::lock_guard<std::mutex> guard(mutex);
            journal_fault = true; record["status"] = "unknown";
            record["error"] = "unexpected worker failure; restart and reconcile"; uncertain[id] = record;
        }
        std::error_code ignored;
        if (owns_staging && output_receipt.complete) {
            // A replacement directory may contain an unrelated entry with the
            // same name. Preserve it instead of cleaning through a redirect.
            try {
                photo_directory->verify();
                // Preserve altered or incomplete bytes for reconciliation as
                // well as replacements; a pathname alone proves no ownership.
                verify_output(temporary, output_receipt, false);
                std::filesystem::remove(temporary, ignored);
            }
            catch (...) {}
        }
        { std::lock_guard<std::mutex> guard(mutex); active.clear(); ++pipeline_generation; }
        wake.notify_all();
    }
};

CameraDaemon::CameraDaemon(const std::filesystem::path& data_dir, double lease_seconds)
    : impl_(std::make_unique<Impl>(data_dir, lease_seconds)) {}
CameraDaemon::~CameraDaemon() = default;
Json CameraDaemon::library_rpc(const std::string& operation, const Json& params) {
    std::lock_guard<std::mutex> guard(impl_->mutex);
    impl_->ensure_journal();
    return impl_->library_operation(operation, params);
}

Json CameraDaemon::capabilities() {
    return {{"api_version", "1.1"}, {"device", "HF-Sim"}, {"simulated", true}, {"backend", "native"}, {"authority", "native"},
            {"capture_record_versions", {1, 2}},
            {"device_settings", {{"persistent", true}, {"format", settings_format}, {"capture_changes_baseline", false}}},
            {"calibration_binding", {{"supported", true}, {"binding_schema_version", 1}, {"max_request_bytes", 65536},
                                      {"hardware_calibrated", false}, {"pixel_corrections_applied", false}}},
            {"modes", Json::array({{{"id", "sim-small"}, {"width", 128}, {"height", 96}, {"bit_depth", 14}, {"cfa", "RGGB"}},
                                  {{"id", "sim-full"}, {"width", 6064}, {"height", 4040}, {"bit_depth", 14}, {"cfa", "RGGB"}}})},
            {"exposure_us", {{"min", 100}, {"max", 30000000}}},
            {"analogue_gain", {{"min", 1.0}, {"max", 16.0}}}, {"output_formats", {"dng"}},
            {"simulation_scenes", {"scene", "dark", "flat"}},
            {"faults", {"delay", "storage_full", "disconnect", "corrupt_frame"}},
            {"retained_captures", {{"listing", true}, {"max_page_size", 50}, {"asset_metadata", true}}},
            {"preview_supported", false}};
}

Json CameraDaemon::acquire(const std::string& owner) {
    std::lock_guard<std::mutex> guard(impl_->mutex);
    impl_->ensure_journal(); impl_->expire_control();
    if (owner.empty() || owner.size() > 128 || owner.find_first_not_of(" \t\r\n") == std::string::npos)
        throw DaemonError("invalid_request", "owner must be nonempty and at most 128 bytes");
    if (!impl_->control.is_null()) throw DaemonError("control_busy", "control is already held");
    if (impl_->epoch == (std::numeric_limits<std::uint64_t>::max)())
        throw DaemonError("journal_error", "control epoch exhausted");
    const auto token = random_token();
    const auto next = impl_->epoch + 1;
    impl_->transaction([&] { impl_->set_meta("epoch", std::to_string(next)); });
    impl_->epoch = next;
    impl_->control = {{"owner", owner}, {"epoch", next}, {"token", token}};
    impl_->renew();
    auto result = impl_->control; result["lease_seconds"] = impl_->lease_seconds; return result;
}

Json CameraDaemon::heartbeat(const std::string& token, std::uint64_t epoch) {
    std::lock_guard<std::mutex> guard(impl_->mutex);
    impl_->authorize(token, epoch); impl_->renew();
    return {{"owner", impl_->control.at("owner")}, {"epoch", epoch}, {"lease_seconds", impl_->lease_seconds}};
}

Json CameraDaemon::release(const std::string& token, std::uint64_t epoch) {
    std::lock_guard<std::mutex> guard(impl_->mutex);
    impl_->authorize(token, epoch); impl_->control = nullptr; return {{"released", true}};
}

Json CameraDaemon::configure(const std::string& token, std::uint64_t epoch, const Json& settings, const std::string& test_fault) {
    std::lock_guard<std::mutex> guard(impl_->mutex);
    impl_->authorize(token, epoch);
    if (!impl_->connected) throw DaemonError("device_disconnected", "reconnect the simulated device explicitly");
    if (!impl_->active.empty()) throw DaemonError("camera_busy", "capture in progress");
    if (!settings.is_object()) throw DaemonError("invalid_request", "settings must be an object");
    if (!test_fault.empty() && test_fault != "settings_commit_failure" && test_fault != "crash_after_settings_commit")
        throw DaemonError("invalid_request", "unsupported settings test fault");
    auto merged = impl_->settings;
    for (auto item = settings.begin(); item != settings.end(); ++item) merged[item.key()] = item.value();
    auto accepted = validated_settings(merged);
    impl_->transaction([&] {
        impl_->set_meta("settings", settings_envelope(accepted).dump());
        if (test_fault == "settings_commit_failure") throw DaemonError("journal_error", "injected settings commit failure");
    });
    if (test_fault == "crash_after_settings_commit") std::_Exit(88);
    impl_->settings = std::move(accepted); return impl_->settings;
}

Json CameraDaemon::bind_calibration(const std::string& token, std::uint64_t epoch,
                                    const Json& binding, const std::string& test_fault) {
    std::lock_guard<std::mutex> guard(impl_->mutex);
    impl_->authorize(token, epoch);
    if (binding.is_null()) throw DaemonError("invalid_binding", "use clear_calibration to select the unbound state");
    return impl_->persist_calibration(binding, test_fault);
}

Json CameraDaemon::clear_calibration(const std::string& token, std::uint64_t epoch, const std::string& test_fault) {
    std::lock_guard<std::mutex> guard(impl_->mutex);
    impl_->authorize(token, epoch);
    return impl_->persist_calibration(nullptr, test_fault);
}

Json CameraDaemon::get_state() {
    std::lock_guard<std::mutex> guard(impl_->mutex);
    impl_->ensure_open(); impl_->expire_control();
    Json last = nullptr;
    { Statement statement(impl_->database.value, "SELECT request_id FROM captures ORDER BY rowid DESC LIMIT 1");
      if (statement.step() == SQLITE_ROW) {
          try { last = impl_->query(statement.text(0)); }
          catch (const DaemonError& error) { if (error.code() != "journal_error") throw; }
      } }
    Json owner = nullptr;
    if (!impl_->control.is_null()) owner = {{"owner", impl_->control.at("owner")}, {"epoch", impl_->epoch}};
    return {{"connected", impl_->connected}, {"simulated", true}, {"backend", "native"}, {"authority", "native"},
            {"camera_state", impl_->active.empty() ? "idle" : "capturing"}, {"journal_healthy", !impl_->journal_fault},
            {"control", owner}, {"settings", impl_->settings}, {"last_capture", last},
            {"settings_persistent", true}, {"settings_format", settings_format},
            {"calibration", calibration_descriptor(impl_->calibration)},
            {"pipeline_generation", impl_->pipeline_generation},
            {"preview", {{"running", false}, {"supported", false}, {"source", "native"}}}};
}

Json CameraDaemon::reconnect(const std::string& token, std::uint64_t epoch) {
    std::lock_guard<std::mutex> guard(impl_->mutex);
    impl_->authorize(token, epoch);
    if (!impl_->active.empty()) throw DaemonError("camera_busy", "wait for capture completion");
    impl_->connected = true; ++impl_->pipeline_generation; return {{"connected", true}, {"simulated", true}};
}

Json CameraDaemon::query_capture(const std::string& request_id) {
    std::lock_guard<std::mutex> guard(impl_->mutex); impl_->ensure_open(); return impl_->query(request_id);
}

Json CameraDaemon::list_captures(std::uint32_t limit, const std::string& cursor) {
    std::lock_guard<std::mutex> guard(impl_->mutex);
    impl_->ensure_journal();
    if (limit < 1 || limit > 50 || (!cursor.empty() && !valid_uuid(cursor)))
        throw DaemonError("invalid_request", "retained page limit must be 1-50 and cursor a canonical UUID");
    if (!cursor.empty()) {
        const auto previous = impl_->query(cursor);
        if (previous.is_null() || previous.at("status") != "complete")
            throw DaemonError("invalid_request", "retained cursor does not identify a committed capture");
    }
    const char* sql = cursor.empty() ?
        "SELECT request_id FROM captures WHERE status='complete' ORDER BY rowid DESC LIMIT ?" :
        "SELECT request_id FROM captures WHERE status='complete' AND rowid < "
        "(SELECT rowid FROM captures WHERE request_id=?) ORDER BY rowid DESC LIMIT ?";
    Statement statement(impl_->database.value, sql);
    if (!cursor.empty()) statement.bind(1, cursor);
    statement.bind_integer(cursor.empty() ? 1 : 2, static_cast<std::int64_t>(limit) + 1);
    Json items = Json::array();
    bool more = false;
    while (statement.step() == SQLITE_ROW) {
        if (items.size() == limit) { more = true; break; }
        const auto id = statement.text(0);
        const auto record = impl_->query(id);
        const bool full = record.at("sensor_mode") == "sim-full";
        Json item = {{"request_id", id}, {"capture_id", id}, {"frame_id", id}, {"status", "complete"},
            {"timestamp", record.at("timestamp")}, {"sensor_mode", record.at("sensor_mode")},
            {"width", full ? 6064 : 128}, {"height", full ? 4040 : 96}, {"bit_depth", 14}, {"cfa", "RGGB"},
            {"sensor_id", record.at("sensor_id")}, {"calibration_id", record.at("calibration_id")},
            {"simulated", true}, {"filename", id + ".dng"}};
        for (const auto* key : {"requested_exposure_us", "actual_exposure_us", "requested_analogue_gain", "actual_analogue_gain"})
            item[key] = record.at(key);
        items.push_back(std::move(item));
    }
    Json next = nullptr;
    if (more) next = items.back().at("request_id");
    return {{"items", items}, {"next_cursor", next}, {"limit", limit}, {"order", "newest_first"}};
}

Json CameraDaemon::get_capture_asset(const std::string& request_id) {
    std::lock_guard<std::mutex> guard(impl_->mutex);
    impl_->ensure_journal();
    if (!valid_uuid(request_id)) throw DaemonError("invalid_request", "request_id must be a canonical lowercase UUID");
    const auto record = impl_->query(request_id);
    if (record.is_null()) throw DaemonError("not_found", "retained capture was not found");
    if (record.at("status") != "complete")
        throw DaemonError("capture_not_complete", "only a committed completed capture can be exported");
    std::error_code error;
    const auto directory = std::filesystem::canonical(impl_->photos, error);
    if (error || directory != impl_->photos)
        throw DaemonError("capture_unavailable", "retained photo directory is unavailable or redirected");
    const auto filename = request_id + ".dng";
    const auto path = impl_->photos / filename;
    const auto status = std::filesystem::symlink_status(path, error);
    if (error || !std::filesystem::is_regular_file(status))
        throw DaemonError("capture_unavailable", "retained DNG is missing or is not a regular file");
    const auto bytes = std::filesystem::file_size(path, error);
    const auto pixels = record.at("sensor_mode") == "sim-full" ? 6064ull * 4040ull : 128ull * 96ull;
    if (error || bytes <= pixels * 2 || bytes > pixels * 2 + 65536 ||
        bytes != record.at("bytes_written").get<std::uint64_t>())
        throw DaemonError("capture_unavailable", "retained DNG size does not match its committed capture");
    return {{"request_id", request_id}, {"filename", filename}, {"byte_count", bytes},
            {"media_type", "image/x-adobe-dng"}};
}

Json CameraDaemon::capture(const Json& request) {
    std::lock_guard<std::mutex> guard(impl_->mutex);
    if (!request.is_object()) throw DaemonError("invalid_request", "capture request must be an object");
    const std::set<std::string> allowed = {"sensor_mode", "requested_exposure_us", "requested_analogue_gain",
        "output_format", "simulation_scene", "request_id", "token", "control_epoch", "fault", "deadline_unix_ms"};
    for (auto item = request.begin(); item != request.end(); ++item)
        if (!allowed.count(item.key())) throw DaemonError("invalid_request", "unsupported capture field");
    if (!request.contains("token") || !request.at("token").is_string() ||
        !request.contains("control_epoch") || !integer(request.at("control_epoch")) || request.at("control_epoch") < 0)
        throw DaemonError("control_denied", "control token or epoch is invalid");
    impl_->authorize(request.at("token").get<std::string>(), request.at("control_epoch").get<std::uint64_t>());
    if (!request.contains("request_id") || !request.at("request_id").is_string() ||
        !valid_uuid(request.at("request_id").get<std::string>()))
        throw DaemonError("invalid_request", "request_id must be a lowercase canonical UUID");
    const auto id = request.at("request_id").get<std::string>();
    Json settings = Json::object();
    const auto setting_names = defaults();
    for (auto item = setting_names.begin(); item != setting_names.end(); ++item)
        if (request.contains(item.key())) settings[item.key()] = request.at(item.key());
    auto payload = validated_settings(settings);
    const auto fault = request.value("fault", Json(nullptr));
    const std::set<std::string> faults = {"delay", "storage_full", "disconnect", "corrupt_frame", "crash_after_accept",
        "crash_after_rename", "worker_start_failure", "admission_commit_failure", "completion_commit_failure"};
    if (!fault.is_null() && (!fault.is_string() || !faults.count(fault.get<std::string>())))
        throw DaemonError("invalid_request", "unknown synthetic fault");
    const auto deadline = request.value("deadline_unix_ms", Json(nullptr));
    if (!deadline.is_null() && (!integer(deadline) || deadline < 0 || deadline > (std::numeric_limits<std::int64_t>::max)()))
        throw DaemonError("invalid_request", "deadline_unix_ms must be a nonnegative signed 64-bit integer");
    payload["fault"] = fault; payload["deadline_unix_ms"] = deadline;
    const auto serialized = payload.dump();
    { Statement statement(impl_->database.value, "SELECT request_json,record_json,status FROM captures WHERE request_id=?");
      statement.bind(1, id);
      if (statement.step() == SQLITE_ROW) {
          const auto existing = impl_->validate_row(id, statement.text(0), statement.text(1), statement.text(2));
          if (statement.text(0) != serialized) throw DaemonError("request_conflict", "request_id has different immutable parameters");
          return existing;
      } }
    if (!impl_->connected) throw DaemonError("device_disconnected", "reconnect the simulated device explicitly");
    if (!impl_->active.empty()) throw DaemonError("camera_busy", "capture in progress");
    impl_->photo_directory->verify();
    if (std::filesystem::exists(impl_->photos / (id + ".dng")) ||
        std::filesystem::exists(impl_->photos / (id + ".dng.partial")))
        throw DaemonError("request_conflict", "output for request ID already exists; reconcile it");
    if (!deadline.is_null() && deadline.get<std::int64_t>() <= unix_ms())
        throw DaemonError("deadline_expired", "capture deadline already expired");
    Json record = payload;
    if (!impl_->calibration.is_null()) {
        const auto binding = validate_calibration_binding(impl_->calibration);
        record["capture_record_version"] = 2;
        record["calibration_binding"] = binding;
        record["hardware_calibrated"] = false;
        record["pixel_corrections_applied"] = false;
    }
    record.update({{"request_id", id}, {"capture_id", id}, {"status", "accepted"}, {"backend", "native"}, {"authority", "native"},
        {"actual_exposure_us", nullptr}, {"actual_analogue_gain", nullptr}, {"frame_id", nullptr},
        {"timestamp", nullptr}, {"sensor_id", "HF-Sim"}, {"calibration_id", calibration_descriptor(impl_->calibration).at("calibration_id")},
        {"file_path", nullptr}, {"error", nullptr}});
    impl_->transaction([&] {
        Statement statement(impl_->database.value, "INSERT INTO captures(request_id,request_json,record_json,status) VALUES (?,?,?,?)");
        statement.bind(1, id); statement.bind(2, serialized); statement.bind(3, record.dump()); statement.bind(4, "accepted");
        statement.step();
        if (fault == "admission_commit_failure") throw DaemonError("journal_error", "injected admission commit failure");
    }); // Durable admission precedes any worker creation or pixel generation.
    if (fault == "crash_after_accept") std::_Exit(86);
    impl_->active = id; impl_->cancelled.store(false); ++impl_->pipeline_generation;
    // An earlier worker sets active empty only after its final lock operation.
    if (impl_->worker.joinable()) impl_->worker.join();
    try {
        if (fault == "worker_start_failure") throw DaemonError("worker_start_failed", "injected worker start failure");
        impl_->worker = std::thread([implementation = impl_.get(), record] { implementation->run(record); });
    } catch (const std::exception& error) {
        record["status"] = "failed"; record["error"] = std::string("capture worker could not start: ") + error.what();
        try { impl_->transaction([&] { impl_->save(record); }); }
        catch (...) { record["status"] = "unknown"; impl_->uncertain[id] = record; }
        impl_->active.clear(); ++impl_->pipeline_generation;
        throw DaemonError("worker_start_failed", record.at("error").get<std::string>());
    }
    return record;
}

Json CameraDaemon::cancel_capture(const std::string& token, std::uint64_t epoch, const std::string& request_id) {
    std::lock_guard<std::mutex> guard(impl_->mutex);
    impl_->authorize(token, epoch);
    const auto record = impl_->query(request_id);
    if (record.is_null()) throw DaemonError("not_found", "capture request not found");
    if (impl_->active == request_id && !terminal(record.at("status").get<std::string>())) {
        impl_->cancelled.store(true); impl_->wake.notify_all();
        return {{"requested", true}, {"request_id", request_id}};
    }
    return {{"requested", false}, {"request_id", request_id}, {"status", record.at("status")}};
}

void CameraDaemon::close() {
    { std::lock_guard<std::mutex> guard(impl_->mutex);
      if (impl_->closed) return;
      if (!impl_->active.empty()) throw DaemonError("camera_busy", "wait for capture completion before closing");
      impl_->closed = true; }
    if (impl_->worker.joinable()) impl_->worker.join();
    { std::lock_guard<std::mutex> guard(impl_->mutex);
      if (impl_->database.value) { sqlite3_close_v2(impl_->database.value); impl_->database.value = nullptr; }
      impl_->directory_lock.reset(); }
}

} // namespace hf
