#include "hf/core.hpp"
#include "hf/dng.hpp"

#include <charconv>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <iostream>
#include <locale>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>
#include <system_error>
#include <utility>
#include <vector>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

namespace {

const char* usage =
    "Synthetic HF-Sim capture worker (no hardware driver).\n"
    "Usage: hf-native-worker --output PATH --width N --height N\n"
    "  --exposure-us N --gain N --scene scene|dark|flat --request-id ID\n"
    "  --sensor-id ID --calibration-id ID [--capture-id ID]\n"
    "Writes one new temporary DNG and prints metadata JSON after closing it.\n"
    "The calling service owns syncing, final rename, and journaling.\n";

std::uint32_t unsigned_number(const std::string& text, const std::string& option) {
    std::uint32_t value = 0;
    const auto result = std::from_chars(text.data(), text.data() + text.size(), value);
    if (text.empty() || result.ec != std::errc{} || result.ptr != text.data() + text.size())
        throw std::invalid_argument(option + " requires an unsigned 32-bit integer");
    return value;
}

double positive_number(const std::string& text) {
    double value = 0;
    std::istringstream stream(text);
    stream.imbue(std::locale::classic());
    stream >> std::noskipws >> value;
    if (!stream || !stream.eof() || !std::isfinite(value) || value <= 0)
        throw std::invalid_argument("--gain requires a positive finite number");
    return value;
}

const char* error_name(hf::ErrorCode code) {
    switch (code) {
    case hf::ErrorCode::InvalidRequest: return "invalid_request";
    case hf::ErrorCode::MemoryLimit: return "memory_limit";
    case hf::ErrorCode::Cancelled: return "cancelled";
    case hf::ErrorCode::DeadlineExceeded: return "deadline_exceeded";
    case hf::ErrorCode::IntegrityFailure: return "integrity_failure";
    }
    return "capture_error";
}

int run(const std::vector<std::string>& arguments) {
    try {
        if (arguments.size() == 1 && arguments.front() == "--help") {
            std::cout << usage;
            return 0;
        }
        const std::vector<std::string> required = {"--output", "--width", "--height", "--exposure-us",
            "--gain", "--scene", "--request-id", "--sensor-id", "--calibration-id"};
        std::map<std::string, std::string> options;
        for (std::size_t i = 0; i < arguments.size(); i += 2) {
            const auto& key = arguments[i];
            bool known = key == "--capture-id";
            for (const auto& expected : required) known = known || key == expected;
            if (!known) throw std::invalid_argument("unknown option: " + key);
            if (i + 1 >= arguments.size()) throw std::invalid_argument("missing value for " + key);
            if (arguments[i + 1].empty()) throw std::invalid_argument("empty value for " + key);
            if (!options.emplace(key, arguments[i + 1]).second)
                throw std::invalid_argument("duplicate option: " + key);
        }
        for (const auto& key : required)
            if (!options.count(key)) throw std::invalid_argument("missing required option: " + key);
        hf::FrameRequest request;
        request.width = unsigned_number(options.at("--width"), "--width");
        request.height = unsigned_number(options.at("--height"), "--height");
        request.exposure_us = unsigned_number(options.at("--exposure-us"), "--exposure-us");
        request.gain = positive_number(options.at("--gain"));
        request.scene = options.at("--scene");
        request.request_id = options.at("--request-id");
        hf::DngIdentity identity;
        identity.sensor_id = options.at("--sensor-id");
        identity.calibration_id = options.at("--calibration-id");
        identity.capture_id = options.count("--capture-id") ? options.at("--capture-id") : request.request_id;
        identity.frame_id = request.request_id;
        const auto output_path = std::filesystem::u8path(options.at("--output"));

        hf::SyntheticCamera camera;
        auto frame = camera.capture(request);
        // Validate all metadata before opening the output. write_dng exclusively
        // creates this path, so an existing file cannot be truncated.
        auto result = hf::capture_metadata_json(frame, identity);
        const auto output_bytes = hf::write_dng(output_path, frame, identity);
        result.pop_back();
        result += ",\"schema_version\":1,\"source\":\"synthetic-native\",\"simulated\":true,\"width\":" +
            std::to_string(frame.metadata.actual_width) + ",\"height\":" + std::to_string(frame.metadata.actual_height) +
            ",\"bit_depth\":14,\"cfa\":\"RGGB\",\"native_frame_sequence\":\"" +
            std::to_string(frame.metadata.frame_id) + "\",\"pixel_checksum\":\"" +
            std::to_string(frame.pixel_checksum) + "\",\"captured_at_unix_ns\":" +
            std::to_string(frame.metadata.captured_at_unix_ns) + ",\"output_bytes\":" + std::to_string(output_bytes) + "}";
        std::cout << result << '\n';
        std::cout.flush();
        if (!std::cout) throw std::runtime_error("could not report completed output metadata");
        return 0;
    } catch (const hf::CaptureError& error) {
        std::cerr << error_name(error.code()) << ": " << error.what() << '\n';
        return 2;
    } catch (const std::invalid_argument& error) {
        std::cerr << "invalid_request: " << error.what() << '\n';
        return 2;
    } catch (const std::exception& error) {
        std::cerr << "worker_error: " << error.what() << '\n';
        return 3;
    }
}

} // namespace

#ifdef _WIN32
// Python passes UTF-16 command lines on Windows. Preserve both Unicode paths
// and identifiers instead of relying on the machine's current ANSI code page.
int wmain(int argc, wchar_t** argv) {
    try {
        std::vector<std::string> arguments;
        for (int i = 1; i < argc; ++i) {
            const int count = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, argv[i], -1, nullptr, 0, nullptr, nullptr);
            if (!count) throw std::invalid_argument("command line contains invalid Unicode");
            std::string value(static_cast<std::size_t>(count), '\0');
            if (!WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, argv[i], -1, value.data(), count, nullptr, nullptr))
                throw std::invalid_argument("command line Unicode conversion failed");
            value.pop_back();
            arguments.push_back(std::move(value));
        }
        return run(arguments);
    } catch (const std::exception& error) {
        std::cerr << "invalid_request: " << error.what() << '\n';
        return 2;
    }
}
#else
int main(int argc, char** argv) {
    return run(std::vector<std::string>(argv + 1, argv + argc));
}
#endif
