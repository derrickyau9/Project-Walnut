#include "hf/daemon.hpp"

#include <cmath>
#include <cstdint>
#include <filesystem>
#include <iostream>
#include <locale>
#include <memory>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <fcntl.h>
#include <io.h>
#endif

namespace {
using Json = nlohmann::json;
constexpr std::size_t kMaxInputBytes = 65536;
constexpr std::size_t kMaxOutputBytes = 262144;

void invalid(const std::string& message) { throw hf::DaemonError("invalid_request", message); }

Json error_response(const Json& id, const std::string& code, const std::string& message) {
    return {{"id", id}, {"error", {{"code", code}, {"message", message}}}};
}

bool emit(const Json& response) {
    auto line = response.dump(-1, ' ', true);
    // Include the terminating newline in the client's 256 KiB wire limit.
    if (line.size() >= kMaxOutputBytes)
        line = error_response(response.value("id", Json(nullptr)), "response_too_large", "native metadata response exceeded limit").dump();
    std::cout << line << '\n';
    std::cout.flush();
    return static_cast<bool>(std::cout);
}

enum class LineState { Ready, End, TooLong };

LineState read_line(std::string& line) {
    line.clear();
    char character = 0;
    while (std::cin.get(character)) {
        if (character == '\n') return LineState::Ready;
        if (line.size() >= kMaxInputBytes) return LineState::TooLong;
        line += character;
    }
    return line.empty() ? LineState::End : LineState::Ready;
}

Json parse_request(const std::string& line) {
    std::vector<std::set<std::string>> objects;
    const auto callback = [&](int depth, Json::parse_event_t event, Json& parsed) {
        if (depth > 32) invalid("JSON nesting exceeds limit");
        if (event == Json::parse_event_t::object_start) objects.emplace_back();
        if (event == Json::parse_event_t::key &&
            (objects.empty() || !objects.back().insert(parsed.get<std::string>()).second))
            invalid("duplicate JSON key");
        if (event == Json::parse_event_t::object_end) objects.pop_back();
        return true;
    };
    try { return Json::parse(line, callback); }
    catch (const Json::exception&) { invalid("malformed JSON request"); }
    return nullptr;
}

bool valid_id(const Json& id) {
    if (id.is_string()) {
        const auto& value = id.get_ref<const std::string&>();
        return !value.empty() && value.size() <= 128 && value.find('\0') == std::string::npos;
    }
    if (id.is_number_unsigned()) return id.get<std::uint64_t>() > 0 && id.get<std::uint64_t>() <= 9007199254740991ull;
    if (id.is_number_integer()) return id.get<std::int64_t>() > 0 && id.get<std::int64_t>() <= 9007199254740991ll;
    return false;
}

void allowed_keys(const Json& object, std::initializer_list<const char*> keys) {
    if (!object.is_object()) invalid("RPC params must be an object");
    for (const auto& item : object.items()) {
        bool allowed = false;
        for (const auto* key : keys) allowed = allowed || item.key() == key;
        if (!allowed) invalid("unsupported RPC field");
    }
}

std::string string_arg(const Json& params, const char* key, std::size_t max_bytes = 512) {
    if (!params.contains(key) || !params.at(key).is_string()) invalid(std::string(key) + " must be a string");
    auto value = params.at(key).get<std::string>();
    if (value.empty() || value.size() > max_bytes || value.find('\0') != std::string::npos)
        invalid(std::string(key) + " is empty, too long or contains NUL");
    return value;
}

std::uint64_t epoch_arg(const Json& params) {
    if (!params.contains("epoch")) invalid("epoch is required");
    const auto& value = params.at("epoch");
    if (value.is_number_unsigned()) return value.get<std::uint64_t>();
    if (value.is_number_integer() && value.get<std::int64_t>() >= 0)
        return static_cast<std::uint64_t>(value.get<std::int64_t>());
    invalid("epoch must be a nonnegative integer");
    return 0;
}

bool test_fault(const Json& params) {
    if (!params.contains("fault") || !params.at("fault").is_string()) return false;
    const auto fault = params.at("fault").get<std::string>();
    return fault == "crash_after_accept" || fault == "crash_after_rename" || fault == "worker_start_failure" ||
        fault == "admission_commit_failure" || fault == "completion_commit_failure";
}

Json dispatch(hf::CameraDaemon& daemon, const std::string& method, const Json& params,
              bool enable_test_faults, bool& stopped) {
    if (!params.is_object()) invalid("RPC params must be an object");
    if (method == "get_state") { allowed_keys(params, {}); return daemon.get_state(); }
    if (method == "capabilities") { allowed_keys(params, {}); return daemon.capabilities(); }
    if (method.rfind("library_", 0) == 0) return daemon.library_rpc(method.substr(8), params);
    if (method == "acquire") {
        allowed_keys(params, {"owner"}); return daemon.acquire(string_arg(params, "owner"));
    }
    if (method == "heartbeat" || method == "release" || method == "reconnect") {
        allowed_keys(params, {"token", "epoch"});
        const auto token = string_arg(params, "token");
        const auto epoch = epoch_arg(params);
        if (method == "heartbeat") return daemon.heartbeat(token, epoch);
        if (method == "release") return daemon.release(token, epoch);
        return daemon.reconnect(token, epoch);
    }
    if (method == "configure") {
        allowed_keys(params, {"token", "epoch", "settings", "test_fault"});
        if (!params.contains("settings") || !params.at("settings").is_object()) invalid("settings must be an object");
        std::string fault;
        if (params.contains("test_fault")) {
            if (!enable_test_faults) throw hf::DaemonError("test_fault_disabled", "settings test faults require --enable-test-faults");
            fault = string_arg(params, "test_fault");
        }
        return daemon.configure(string_arg(params, "token"), epoch_arg(params), params.at("settings"), fault);
    }
    if (method == "bind_calibration" || method == "clear_calibration") {
        if (method == "bind_calibration") allowed_keys(params, {"token", "epoch", "binding", "test_fault"});
        else allowed_keys(params, {"token", "epoch", "test_fault"});
        std::string fault;
        if (params.contains("test_fault")) {
            if (!enable_test_faults) throw hf::DaemonError("test_fault_disabled", "calibration test faults require --enable-test-faults");
            fault = string_arg(params, "test_fault");
        }
        const auto token = string_arg(params, "token");
        const auto epoch = epoch_arg(params);
        if (method == "clear_calibration") return daemon.clear_calibration(token, epoch, fault);
        if (!params.contains("binding")) invalid("binding is required");
        return daemon.bind_calibration(token, epoch, params.at("binding"), fault);
    }
    if (method == "capture") {
        if (!enable_test_faults && test_fault(params))
            throw hf::DaemonError("test_fault_disabled", "process and journal test faults require --enable-test-faults");
        return daemon.capture(params);
    }
    if (method == "query_capture") {
        allowed_keys(params, {"request_id"}); return daemon.query_capture(string_arg(params, "request_id", 128));
    }
    if (method == "list_captures") {
        allowed_keys(params, {"limit", "cursor"});
        const auto limit = params.value("limit", Json(20));
        if ((!limit.is_number_integer() && !limit.is_number_unsigned()) || limit < 1 || limit > 50)
            invalid("retained page limit must be an integer from 1 to 50");
        std::string cursor;
        if (params.contains("cursor") && !params.at("cursor").is_null()) cursor = string_arg(params, "cursor", 36);
        return daemon.list_captures(limit.get<std::uint32_t>(), cursor);
    }
    if (method == "get_capture_asset") {
        allowed_keys(params, {"request_id"}); return daemon.get_capture_asset(string_arg(params, "request_id", 36));
    }
    if (method == "cancel_capture") {
        allowed_keys(params, {"token", "epoch", "request_id"});
        return daemon.cancel_capture(string_arg(params, "token"), epoch_arg(params), string_arg(params, "request_id", 128));
    }
    if (method == "close") {
        allowed_keys(params, {}); daemon.close(); stopped = true; return {{"closed", true}};
    }
    throw hf::DaemonError("method_not_found", "unsupported native RPC method");
}

int run(const std::vector<std::string>& arguments) {
    try {
        if (arguments.size() == 1 && arguments.front() == "--help") {
            std::cout << "Synthetic native camerad (no hardware driver).\n"
                "Usage: hf-camerad --data-dir PATH [--lease-seconds N] [--enable-test-faults]\n"
                "Stdio JSON lines: {id,method,params} -> {id,result} or {id,error:{code,message}}.\n"
                "Input is limited to 65536 bytes and 32 nesting levels. EOF shuts down.\n";
            return 0;
        }
        std::filesystem::path data_dir;
        double lease_seconds = 30;
        bool enable_test_faults = false;
        std::set<std::string> seen;
        for (std::size_t i = 0; i < arguments.size(); ++i) {
            const auto& option = arguments[i];
            if (!seen.insert(option).second) invalid("duplicate command line option");
            if (option == "--enable-test-faults") { enable_test_faults = true; continue; }
            if (option != "--data-dir" && option != "--lease-seconds") invalid("unsupported command line option");
            if (++i >= arguments.size() || arguments[i].empty()) invalid("missing command line value");
            if (option == "--data-dir") data_dir = std::filesystem::u8path(arguments[i]);
            else {
                std::istringstream stream(arguments[i]);
                stream.imbue(std::locale::classic());
                stream >> std::noskipws >> lease_seconds;
                if (!stream || !stream.eof() || !std::isfinite(lease_seconds) || lease_seconds <= 0 || lease_seconds > 3600)
                    invalid("lease-seconds must be positive, finite and at most 3600");
            }
        }
        if (data_dir.empty()) invalid("--data-dir is required");
        hf::CameraDaemon daemon(data_dir, lease_seconds);
        std::string line;
        bool stopped = false;
        while (!stopped) {
            const auto state = read_line(line);
            if (state == LineState::End) break;
            if (state == LineState::TooLong) {
                emit(error_response(nullptr, "invalid_request", "RPC input exceeds 65536 bytes"));
                return 2;
            }
            Json id = nullptr;
            Json response;
            try {
                const auto request = parse_request(line);
                allowed_keys(request, {"id", "method", "params"});
                if (!request.contains("id") || !valid_id(request.at("id"))) invalid("RPC id must be a positive safe integer or a short string");
                id = request.at("id");
                const auto method = string_arg(request, "method", 64);
                const auto params = request.value("params", Json::object());
                response = {{"id", id}, {"result", dispatch(daemon, method, params, enable_test_faults, stopped)}};
            } catch (const hf::DaemonError& error) {
                response = error_response(id, error.code(), error.what());
            } catch (const Json::exception&) {
                response = error_response(id, "invalid_request", "malformed RPC field type");
            } catch (const std::exception&) {
                response = error_response(id, "internal_error", "native authority could not process request");
            }
            if (!emit(response)) return 3;
        }
        return 0;
    } catch (const hf::DaemonError& error) {
        emit(error_response(nullptr, error.code(), error.what()));
        return 2;
    } catch (const std::exception& error) {
        emit(error_response(nullptr, "startup_failed", error.what()));
        return 3;
    }
}
} // namespace

#ifdef _WIN32
int wmain(int argc, wchar_t** argv) {
    _setmode(_fileno(stdin), _O_BINARY);
    _setmode(_fileno(stdout), _O_BINARY);
    try {
        std::vector<std::string> arguments;
        for (int i = 1; i < argc; ++i) {
            const int count = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, argv[i], -1, nullptr, 0, nullptr, nullptr);
            if (!count) invalid("command line contains invalid Unicode");
            std::string value(static_cast<std::size_t>(count), '\0');
            if (!WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, argv[i], -1, value.data(), count, nullptr, nullptr))
                invalid("command line Unicode conversion failed");
            value.pop_back(); arguments.push_back(std::move(value));
        }
        return run(arguments);
    } catch (const std::exception&) {
        emit(error_response(nullptr, "invalid_request", "command line Unicode conversion failed"));
        return 2;
    }
}
#else
int main(int argc, char** argv) { return run(std::vector<std::string>(argv + 1, argv + argc)); }
#endif
