#pragma once

#include <cstdint>
#include <filesystem>
#include <memory>
#include <stdexcept>
#include <string>
#include <nlohmann/json.hpp>

namespace hf {

using Json = nlohmann::json;

class DaemonError : public std::runtime_error {
public:
    DaemonError(std::string code, const std::string& message);
    const std::string& code() const noexcept { return code_; }
private:
    std::string code_;
};

// A separate native synthetic authority, not a hardware driver. Each directory
// has one OS-locked owner and a SQLite request journal. Incomplete requests are
// reconciled as unknown on restart and are never automatically re-executed.
class CameraDaemon {
public:
    explicit CameraDaemon(const std::filesystem::path& data_dir,
                          double lease_seconds = 30.0);
    ~CameraDaemon();
    CameraDaemon(const CameraDaemon&) = delete;
    CameraDaemon& operator=(const CameraDaemon&) = delete;

    static Json capabilities();
    Json acquire(const std::string& owner);
    Json heartbeat(const std::string& token, std::uint64_t epoch);
    Json release(const std::string& token, std::uint64_t epoch);
    Json configure(const std::string& token, std::uint64_t epoch,
                   const Json& settings, const std::string& test_fault = "");
    Json bind_calibration(const std::string& token, std::uint64_t epoch,
                          const Json& binding, const std::string& test_fault = "");
    Json clear_calibration(const std::string& token, std::uint64_t epoch,
                           const std::string& test_fault = "");
    Json get_state();
    Json reconnect(const std::string& token, std::uint64_t epoch);
    Json query_capture(const std::string& request_id);
    // Read-only retained media APIs. Pagination is a newest-first keyset over
    // durable committed rows; cursor is an earlier page's canonical UUID.
    Json list_captures(std::uint32_t limit = 20, const std::string& cursor = "");
    // Metadata only. Binary DNG content never crosses the JSON RPC channel.
    Json get_capture_asset(const std::string& request_id);
    // Additive media lifecycle API. Capture records remain immutable history.
    Json library_rpc(const std::string& operation, const Json& params);
    Json capture(const Json& request);
    Json cancel_capture(const std::string& token, std::uint64_t epoch,
                        const std::string& request_id);
    // Active capture must finish/cancel before explicit close. The destructor
    // cancels and joins any remaining worker before releasing the directory.
    void close();

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace hf
