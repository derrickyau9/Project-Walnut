#include "hf/calibration.hpp"

#include "hf/daemon.hpp"
#include "hf/sha256.hpp"

#include <cmath>
#include <cstddef>
#include <cstdint>
#include <initializer_list>
#include <set>
#include <string_view>
#include <vector>

namespace hf {
namespace {
using Json = nlohmann::json;
constexpr std::size_t kMaxPayloadBytes = 65536;
constexpr std::size_t kMaxBindingBytes = 131072;
constexpr const char* kDomain = "HF-Sim calibration binding v1\n";
constexpr const char* kNotice =
    "Not measured hardware calibration; synthetic software data or an uncalibrated marker only.";

void require(bool condition, const char* message, const char* code = "invalid_package") {
    if (!condition) throw DaemonError(code, message);
}

bool fields(const Json& object, std::initializer_list<const char*> names) {
    if (!object.is_object() || object.size() != names.size()) return false;
    for (const auto* name : names) if (!object.contains(name)) return false;
    return true;
}

bool integer(const Json& value, std::uint64_t low, std::uint64_t high) {
    if (value.is_number_unsigned()) {
        const auto number = value.get<std::uint64_t>();
        return low <= number && number <= high;
    }
    if (!value.is_number_integer()) return false;
    const auto number = value.get<std::int64_t>();
    return number >= 0 && low <= static_cast<std::uint64_t>(number) &&
           static_cast<std::uint64_t>(number) <= high;
}

bool number(const Json& value, double low, double high) {
    if (!value.is_number()) return false;
    const auto numeric = value.get<double>();
    return std::isfinite(numeric) && low <= numeric && numeric <= high;
}

bool false_boolean(const Json& value) {
    return value.is_boolean() && !value.get<bool>();
}

void identifier(const Json& value) {
    require(value.is_string(), "calibration identity must be a portable identifier", "invalid_identifier");
    const auto& text = value.get_ref<const std::string&>();
    bool valid = !text.empty() && text.size() <= 64 && text.back() != '.';
    for (std::size_t i = 0; i < text.size(); ++i) {
        const unsigned char c = static_cast<unsigned char>(text[i]);
        const bool alphanumeric = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
                                  (c >= '0' && c <= '9');
        if (!alphanumeric && (i == 0 || (c != '_' && c != '-' && c != '.'))) valid = false;
    }
    auto base = text.substr(0, text.find('.'));
    for (auto& c : base) if (c >= 'a' && c <= 'z') c = static_cast<char>(c - 'a' + 'A');
    if (base == "CON" || base == "PRN" || base == "AUX" || base == "NUL" ||
        (base.size() == 4 && (base.substr(0, 3) == "COM" || base.substr(0, 3) == "LPT") &&
         base[3] >= '1' && base[3] <= '9')) valid = false;
    require(valid, "calibration identity must be a portable identifier", "invalid_identifier");
}

bool python_whitespace(std::uint32_t cp) {
    return (cp >= 0x09 && cp <= 0x0d) || (cp >= 0x1c && cp <= 0x20) ||
           cp == 0x85 || cp == 0xa0 || cp == 0x1680 || (cp >= 0x2000 && cp <= 0x200a) ||
           cp == 0x2028 || cp == 0x2029 || cp == 0x202f || cp == 0x205f || cp == 0x3000;
}

bool reason_valid(const Json& value) {
    if (!value.is_string()) return false;
    const auto& text = value.get_ref<const std::string&>();
    std::size_t count = 0;
    std::uint32_t first = 0, last = 0;
    for (std::size_t offset = 0; offset < text.size();) {
        const auto lead = static_cast<unsigned char>(text[offset]);
        std::uint32_t cp = 0;
        std::size_t length = 0;
        if (lead < 0x80) { cp = lead; length = 1; }
        else if (lead >= 0xc2 && lead <= 0xdf) { cp = lead & 0x1fu; length = 2; }
        else if (lead >= 0xe0 && lead <= 0xef) { cp = lead & 0x0fu; length = 3; }
        else if (lead >= 0xf0 && lead <= 0xf4) { cp = lead & 0x07u; length = 4; }
        else return false;
        if (length > text.size() - offset) return false;
        for (std::size_t i = 1; i < length; ++i) {
            const auto continuation = static_cast<unsigned char>(text[offset + i]);
            if ((continuation & 0xc0u) != 0x80u) return false;
            cp = (cp << 6u) | (continuation & 0x3fu);
        }
        if ((length == 2 && cp < 0x80) || (length == 3 && cp < 0x800) ||
            (length == 4 && cp < 0x10000) || cp > 0x10ffff ||
            cp < 32 || (cp >= 0xd800 && cp <= 0xdfff)) return false;
        if (count == 0) first = cp;
        last = cp;
        if (++count > 512) return false;
        offset += length;
    }
    return count != 0 && !python_whitespace(first) && !python_whitespace(last);
}

void validate_data(const Json& data) {
    require(fields(data, {"width", "height", "cfa", "bit_depth", "exposure_us", "analogue_gain", "dark", "flat"}),
            "synthetic data has missing or unknown fields");
    require(integer(data["width"], 2, 256) && integer(data["height"], 2, 256),
            "synthetic dimensions must be even integers from 2 through 256");
    const auto width = data["width"].get<std::uint64_t>();
    const auto height = data["height"].get<std::uint64_t>();
    require(width % 2 == 0 && height % 2 == 0,
            "synthetic dimensions must be even integers from 2 through 256");
    require(data["cfa"] == "RGGB" && integer(data["bit_depth"], 14, 14),
            "only synthetic 14-bit RGGB is supported");
    require(integer(data["exposure_us"], 1, 0xffffffffu) && number(data["analogue_gain"], 0.000001, 64),
            "invalid synthetic exposure or analogue gain");
    for (const auto* scene : {"dark", "flat"}) {
        const auto& summary = data[scene];
        require(fields(summary, {"sample_count", "minimum", "maximum", "mean", "population_stddev"}),
                "synthetic statistics have missing or unknown fields");
        require(integer(summary["sample_count"], width * height, width * height),
                "synthetic sample_count does not match frame dimensions");
        require(integer(summary["minimum"], 0, 16383), "synthetic minimum is outside 14-bit range");
        const auto low = summary["minimum"].get<std::uint64_t>();
        require(integer(summary["maximum"], low, 16383), "synthetic maximum is outside 14-bit range");
        const auto high = summary["maximum"].get<std::uint64_t>();
        require(number(summary["mean"], static_cast<double>(low), static_cast<double>(high)) &&
                number(summary["population_stddev"], 0, static_cast<double>(high - low) / 2),
                "synthetic statistics are outside declared range");
    }
}

void validate_content(const Json& package, const std::string& sensor) {
    require(fields(package, {"schema_version", "sensor_id", "package_id", "package_version", "status",
                             "hardware_calibrated", "source", "notice", "reason", "data"}),
            "package has missing or unknown fields");
    require(integer(package["schema_version"], 1, 1), "unsupported calibration schema_version");
    for (const auto* key : {"sensor_id", "package_id", "package_version"}) identifier(package[key]);
    identifier(sensor);
    require(package["sensor_id"] == sensor, "package sensor does not match expected sensor", "sensor_mismatch");
    require(false_boolean(package["hardware_calibrated"]) && package["notice"] == kNotice,
            "packages must disclaim measured hardware calibration");
    if (package["status"] == "uncalibrated") {
        require(package["source"] == "none" && package["data"].is_null(),
                "uncalibrated packages cannot contain calibration data");
        require(reason_valid(package["reason"]), "uncalibrated package reason is invalid");
    } else if (package["status"] == "synthetic") {
        require(package["source"] == "simulation.bayer.v1" && package["reason"].is_null(),
                "synthetic packages must identify the simulator source");
        validate_data(package["data"]);
    } else {
        throw DaemonError("invalid_package", "status must be synthetic or uncalibrated");
    }
}

bool equal_digest(std::string_view first, std::string_view second) {
    if (first.size() != second.size()) return false;
    unsigned difference = 0;
    for (std::size_t i = 0; i < first.size(); ++i) {
        difference |= static_cast<unsigned char>(first[i]) ^ static_cast<unsigned char>(second[i]);
    }
    return difference == 0;
}

Json parse_payload(const std::string& payload) {
    // The JSON library treats a raw NUL as EOF, while the wire format forbids
    // NUL/trailing garbage. Do not silently validate only a payload prefix.
    require(payload.find('\0') == std::string::npos, "binding payload contains a raw NUL", "invalid_binding");
    bool quoted = false, escaped = false;
    for (std::size_t i = 0; i < payload.size(); ++i) {
        const char c = payload[i];
        if (quoted) {
            if (escaped) escaped = false;
            else if (c == '\\') escaped = true;
            else if (c == '"') quoted = false;
        } else if (c == '"') quoted = true;
        else if (payload.compare(i, 3, "NaN") == 0 || payload.compare(i, 8, "Infinity") == 0 ||
                 payload.compare(i, 9, "-Infinity") == 0) {
            throw DaemonError("invalid_package", "non-finite JSON constant in calibration payload");
        }
    }
    std::vector<std::set<std::string>> object_keys;
    auto callback = [&object_keys](int depth, Json::parse_event_t event, Json& parsed) {
        require(depth <= 32, "calibration JSON nesting exceeds schema limits", "invalid_binding");
        if (event == Json::parse_event_t::object_start) object_keys.emplace_back();
        else if (event == Json::parse_event_t::key) {
            require(!object_keys.empty() && object_keys.back().insert(parsed.get<std::string>()).second,
                    "duplicate JSON key in calibration payload");
        } else if (event == Json::parse_event_t::object_end) object_keys.pop_back();
        return true;
    };
    try {
        return Json::parse(payload, callback);
    } catch (const Json::out_of_range&) {
        throw DaemonError("invalid_package", "non-finite calibration JSON number");
    } catch (const Json::parse_error& error) {
        // Python's JSON reader retains lone UTF-16 surrogates; package reason
        // validation then rejects them. The native reader rejects them sooner.
        if (std::string_view(error.what()).find("surrogate") != std::string_view::npos) {
            throw DaemonError("invalid_package", "calibration JSON contains an unpaired surrogate");
        }
        throw DaemonError("invalid_binding", "invalid calibration payload JSON");
    } catch (const Json::exception&) {
        throw DaemonError("invalid_binding", "invalid calibration payload JSON");
    }
}

std::size_t ascii_string_size(std::string_view text) {
    std::size_t size = 2;
    for (const unsigned char c : text) {
        if (c == '"' || c == '\\' || c == '\b' || c == '\f' || c == '\n' || c == '\r' || c == '\t') size += 2;
        else size += c < 0x20 || c >= 0x7f ? 6 : 1;
    }
    return size;
}

std::size_t python_encoded_binding_size(const Json& binding) {
    // Match Python's compact ensure_ascii=True wire size, including its final
    // newline. DEL is escaped by Python even though the JSON library emits it.
    std::size_t size = 3;
    bool first = true;
    for (auto item = binding.begin(); item != binding.end(); ++item) {
        if (!first) ++size;
        first = false;
        size += ascii_string_size(item.key()) + 1;
        size += item.value().is_string() ? ascii_string_size(item.value().get_ref<const std::string&>()) :
                item.value().is_boolean() ? 5 : 1;
    }
    return size;
}
} // namespace

nlohmann::json validate_calibration_binding(const nlohmann::json& binding, const std::string& expected_sensor_id) {
    require(fields(binding, {"binding_schema_version", "binding_id", "sensor_id", "package_checksum", "package_payload",
                             "hardware_calibrated", "pixel_corrections_applied"}),
            "binding has missing or unknown fields", "invalid_binding");
    require(integer(binding["binding_schema_version"], 1, 1), "unsupported binding_schema_version", "invalid_binding");
    require(false_boolean(binding["hardware_calibrated"]) && false_boolean(binding["pixel_corrections_applied"]),
            "binding cannot claim measured calibration or pixel correction", "invalid_binding");
    require(binding["binding_id"].is_string(), "invalid calibration binding_id", "invalid_binding");
    const auto& id = binding["binding_id"].get_ref<const std::string&>();
    bool valid_id = id.size() == 68 && id.compare(0, 4, "cb1-") == 0;
    if (valid_id) for (std::size_t i = 4; i < id.size(); ++i) {
        if (!((id[i] >= '0' && id[i] <= '9') || (id[i] >= 'a' && id[i] <= 'f'))) valid_id = false;
    }
    require(valid_id, "invalid calibration binding_id", "invalid_binding");
    require(binding["sensor_id"].is_string() && binding["sensor_id"] == expected_sensor_id,
            "binding sensor does not match expected sensor", "sensor_mismatch");
    require(binding["package_payload"].is_string(), "package_payload must be ASCII JSON", "invalid_binding");
    const auto& payload = binding["package_payload"].get_ref<const std::string&>();
    require(!payload.empty() && payload.size() <= kMaxPayloadBytes, "binding package_payload exceeds byte limit", "invalid_binding");
    for (const unsigned char c : payload) require(c <= 0x7f, "package_payload must contain ASCII bytes", "invalid_binding");
    const auto checksum = "sha256:" + sha256_hex(payload);
    require(binding["package_checksum"].is_string() &&
            equal_digest(binding["package_checksum"].get_ref<const std::string&>(), checksum),
            "binding package checksum mismatch", "checksum_mismatch");
    require(equal_digest(id, "cb1-" + sha256_hex(std::string(kDomain) + payload)),
            "binding_id does not match its exact snapshot", "checksum_mismatch");
    validate_content(parse_payload(payload), expected_sensor_id);
    require(python_encoded_binding_size(binding) <= kMaxBindingBytes, "binding exceeds encoded byte limit", "invalid_binding");
    return binding;
}

nlohmann::json calibration_descriptor(const nlohmann::json& binding_or_null) {
    if (binding_or_null.is_null()) {
        return {{"sensor_id", "HF-Sim"}, {"binding_id", nullptr},
                {"calibration_id", "synthetic-uncalibrated"}, {"package_checksum", nullptr},
                {"hardware_calibrated", false}, {"pixel_corrections_applied", false}};
    }
    const auto binding = validate_calibration_binding(binding_or_null);
    return {{"sensor_id", binding["sensor_id"]}, {"binding_id", binding["binding_id"]},
            {"calibration_id", binding["binding_id"]}, {"package_checksum", binding["package_checksum"]},
            {"hardware_calibrated", false}, {"pixel_corrections_applied", false}};
}

} // namespace hf
