#include "hf/calibration.hpp"
#include "hf/daemon.hpp"
#include "hf/sha256.hpp"

#include <filesystem>
#include <fstream>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {
using Json = nlohmann::json;
std::size_t checks = 0;

void require(bool condition, const char* message) {
    ++checks;
    if (!condition) throw std::runtime_error(message);
}

template<class Function>
void expect_error(const std::string& code, Function function) {
    bool caught = false;
    try { function(); }
    catch (const hf::DaemonError& error) {
        caught = true;
        require(error.code() == code, ("wrong error code: " + error.code() + " instead of " + code).c_str());
    }
    require(caught, "invalid calibration binding was accepted");
}

Json load(const std::filesystem::path& directory, const char* name) {
    std::ifstream input(directory / name, std::ios::binary);
    if (!input) throw std::runtime_error(std::string("could not read fixture ") + name);
    return Json::parse(input);
}

Json seal(const std::string& payload, const std::string& sensor = "HF-Sim") {
    return {{"binding_schema_version", 1}, {"binding_id", "cb1-" + hf::sha256_hex("HF-Sim calibration binding v1\n" + payload)},
            {"sensor_id", sensor}, {"package_checksum", "sha256:" + hf::sha256_hex(payload)},
            {"package_payload", payload}, {"hardware_calibrated", false}, {"pixel_corrections_applied", false}};
}

Json seal(const Json& package) { return seal(package.dump(-1, ' ', true)); }

void expect_package_error(const Json& package, const char* code = "invalid_package") {
    const auto binding = seal(package);
    expect_error(code, [&] { hf::validate_calibration_binding(binding); });
}

void fixtures_and_descriptors(const Json& uncalibrated, const Json& synthetic, const Json& unicode) {
    for (const auto& binding : {uncalibrated, synthetic, unicode}) {
        const auto copy = hf::validate_calibration_binding(binding);
        require(copy == binding, "validator changed exact fixture bytes or fields");
        const auto descriptor = hf::calibration_descriptor(binding);
        require(descriptor.size() == 6 && descriptor["sensor_id"] == "HF-Sim" &&
                descriptor["binding_id"] == binding["binding_id"] &&
                descriptor["calibration_id"] == binding["binding_id"] &&
                descriptor["package_checksum"] == binding["package_checksum"] &&
                descriptor["hardware_calibrated"] == false && descriptor["pixel_corrections_applied"] == false,
                "bound descriptor lost trace identity or disclaimers");
    }
    const auto unbound = hf::calibration_descriptor(nullptr);
    require(unbound.size() == 6 && unbound["sensor_id"] == "HF-Sim" && unbound["binding_id"].is_null() &&
            unbound["package_checksum"].is_null() && unbound["calibration_id"] == "synthetic-uncalibrated" &&
            unbound["hardware_calibrated"] == false && unbound["pixel_corrections_applied"] == false,
            "unbound descriptor does not explicitly disclaim calibration");
    auto changed = uncalibrated;
    auto detached = hf::validate_calibration_binding(changed);
    changed["package_payload"] = "changed";
    require(detached == uncalibrated, "returned artifact is not an independent copy");
}

void outer_schema_and_hashes(const Json& fixture) {
    for (auto value : std::vector<Json>{nullptr, Json::array(), Json::object()}) {
        expect_error("invalid_binding", [&] { hf::validate_calibration_binding(value); });
    }
    for (const auto& field : {"binding_schema_version", "binding_id", "sensor_id", "package_checksum",
                              "package_payload", "hardware_calibrated", "pixel_corrections_applied"}) {
        auto value = fixture; value.erase(field);
        expect_error("invalid_binding", [&] { hf::validate_calibration_binding(value); });
    }
    auto value = fixture; value["unknown"] = 1;
    expect_error("invalid_binding", [&] { hf::validate_calibration_binding(value); });
    for (const auto& schema : std::vector<Json>{true, 1.0, 2, "1"}) {
        value = fixture; value["binding_schema_version"] = schema;
        expect_error("invalid_binding", [&] { hf::validate_calibration_binding(value); });
    }
    for (const auto& field : {"hardware_calibrated", "pixel_corrections_applied"}) {
        for (const auto& flag : std::vector<Json>{true, 0, nullptr, "false"}) {
            value = fixture; value[field] = flag;
            expect_error("invalid_binding", [&] { hf::validate_calibration_binding(value); });
        }
    }
    for (const auto& id : {std::string("cb1-") + std::string(64, 'A'), std::string("cb1-bad"), std::string(68, '0')}) {
        value = fixture; value["binding_id"] = id;
        expect_error("invalid_binding", [&] { hf::validate_calibration_binding(value); });
    }
    value = fixture; value["sensor_id"] = "Other-Sensor";
    expect_error("sensor_mismatch", [&] { hf::validate_calibration_binding(value); });
    value = fixture; value["sensor_id"] = 1;
    expect_error("sensor_mismatch", [&] { hf::validate_calibration_binding(value); });
    value = fixture; value["package_checksum"] = "sha256:" + std::string(64, '0');
    expect_error("checksum_mismatch", [&] { hf::validate_calibration_binding(value); });
    value = fixture; value["binding_id"] = "cb1-" + std::string(64, '0');
    expect_error("checksum_mismatch", [&] { hf::validate_calibration_binding(value); });
    value = fixture; value["package_payload"] = value["package_payload"].get<std::string>() + " ";
    expect_error("checksum_mismatch", [&] { hf::validate_calibration_binding(value); });
    value = fixture; value["package_payload"] = nullptr;
    expect_error("invalid_binding", [&] { hf::validate_calibration_binding(value); });
    for (const auto& payload : {std::string(), std::string(65537, ' '), std::string("\xc3\xa9")}) {
        value = seal(payload);
        expect_error("invalid_binding", [&] { hf::validate_calibration_binding(value); });
    }
}

void exact_spelling_and_parse_guards(const Json& uncalibrated, const Json& synthetic) {
    const auto original = synthetic["package_payload"].get<std::string>();
    auto decimal = original;
    const auto exponent = decimal.find("1e-06");
    require(exponent != std::string::npos, "synthetic fixture lost its exponent-spelling interoperability case");
    decimal.replace(exponent, 5, "0.000001");
    const auto variant = seal(decimal);
    require(hf::validate_calibration_binding(variant) == variant, "equivalent decimal spelling was rejected");
    require(variant["binding_id"] != synthetic["binding_id"], "exact-byte bindings collapsed distinct number spellings");
    const auto whitespace = seal("\n\t" + original + " \r\n");
    require(hf::validate_calibration_binding(whitespace) == whitespace, "valid ASCII JSON whitespace was rejected");
    require(whitespace["binding_id"] != synthetic["binding_id"], "whitespace did not change exact-byte binding identity");
    auto duplicate = uncalibrated["package_payload"].get<std::string>();
    duplicate.insert(duplicate.size() - 1, ",\"status\":\"uncalibrated\"");
    expect_error("invalid_package", [&] { hf::validate_calibration_binding(seal(duplicate)); });
    auto nested = original;
    const auto location = nested.find("\"sample_count\":4");
    require(location != std::string::npos, "synthetic fixture sample count changed");
    nested.insert(location, "\"sample_count\":4,");
    expect_error("invalid_package", [&] { hf::validate_calibration_binding(seal(nested)); });
    const std::string deep = std::string(200, '[') + "0" + std::string(200, ']');
    expect_error("invalid_binding", [&] { hf::validate_calibration_binding(seal(deep)); });
    for (const auto& raw : {original + "junk", original + std::string("\0junk", 5), std::string("{")}) {
        expect_error("invalid_binding", [&] { hf::validate_calibration_binding(seal(raw)); });
    }
    for (const auto* constant : {"NaN", "Infinity", "-Infinity", "1e999"}) {
        auto raw = original;
        const auto start = raw.find("1e-06");
        raw.replace(start, 5, constant);
        expect_error("invalid_package", [&] { hf::validate_calibration_binding(seal(raw)); });
    }
}

void uncalibrated_semantics_and_unicode(const Json& fixture, const Json& unicode) {
    const auto original = Json::parse(fixture["package_payload"].get<std::string>());
    for (const auto& change : std::vector<std::pair<std::string, Json>>{
            {"schema_version", true}, {"schema_version", 1.0}, {"hardware_calibrated", 0},
            {"notice", "Measured"}, {"status", "measured"}, {"source", "sensor"},
            {"data", Json::object()}, {"reason", nullptr}, {"reason", ""}, {"reason", " leading"},
            {"reason", "trailing "}, {"reason", "bad\nline"}, {"checksum", "not a payload field"}}) {
        auto package = original; package[change.first] = change.second;
        expect_package_error(package);
    }
    for (const auto& id : {"CON", "nul.json", "Com1.any", "lpt9", "name.", ".name", "a/b", "name with spaces"}) {
        auto package = original; package["package_id"] = id;
        expect_package_error(package, "invalid_identifier");
    }
    auto package = original; package["package_version"] = std::string(65, 'x');
    expect_package_error(package, "invalid_identifier");
    package = original; package["sensor_id"] = "Other-Sensor";
    expect_package_error(package, "sensor_mismatch");
    package = Json::parse(unicode["package_payload"].get<std::string>());
    package["reason"] = package["reason"].get<std::string>() + "x";
    expect_package_error(package); // The shared fixture already has 512 Unicode scalars.
    const std::vector<std::string> whitespace = {
        " ", u8"\u0085", u8"\u00a0", u8"\u1680", u8"\u2000", u8"\u2001", u8"\u2002", u8"\u2003",
        u8"\u2004", u8"\u2005", u8"\u2006", u8"\u2007", u8"\u2008", u8"\u2009", u8"\u200a",
        u8"\u2028", u8"\u2029", u8"\u202f", u8"\u205f", u8"\u3000",
    };
    for (const auto& space : whitespace) {
        package = original; package["reason"] = space + "A";
        expect_package_error(package);
        package["reason"] = "A" + space;
        expect_package_error(package);
        package["reason"] = "A" + space + "B";
        require(hf::validate_calibration_binding(seal(package)).is_object(), "internal Python whitespace was incorrectly rejected");
    }
    for (const auto& accepted : {std::string(1, '\x7f'), std::string(u8"\u180e"), std::string(u8"\u200b"), std::string("NaN Infinity")}) {
        package = original; package["reason"] = accepted;
        require(hf::validate_calibration_binding(seal(package)).is_object(), "Python-valid reason text was rejected");
    }
    auto raw = fixture["package_payload"].get<std::string>();
    const auto reason = raw.find("\"reason\":");
    const auto start = raw.find('"', reason + 9);
    const auto end = raw.find('"', start + 1);
    raw.replace(start, end - start + 1, "\"\\ud800\"");
    expect_error("invalid_package", [&] { hf::validate_calibration_binding(seal(raw)); });
}

void synthetic_semantics(const Json& fixture) {
    const auto original = Json::parse(fixture["package_payload"].get<std::string>());
    for (const auto& change : std::vector<std::pair<std::string, Json>>{
            {"width", 3}, {"width", 258}, {"height", 1}, {"height", true}, {"cfa", "BGGR"},
            {"bit_depth", 14.0}, {"exposure_us", 0}, {"exposure_us", 4294967296ull},
            {"exposure_us", 1.0}, {"analogue_gain", true}, {"analogue_gain", 0.0000009}, {"analogue_gain", 65}}) {
        auto package = original; package["data"][change.first] = change.second;
        expect_package_error(package);
    }
    for (const auto& scene : {"dark", "flat"}) {
        for (const auto& change : std::vector<std::pair<std::string, Json>>{
                {"sample_count", 3}, {"sample_count", 4.0}, {"minimum", -1}, {"maximum", 16384},
                {"mean", -1}, {"population_stddev", -0.1}, {"population_stddev", 16384}}) {
            auto package = original; package["data"][scene][change.first] = change.second;
            expect_package_error(package);
        }
        auto package = original; package["data"][scene]["extra"] = 1;
        expect_package_error(package);
    }
    auto package = original; package["reason"] = "invented measurement";
    expect_package_error(package);
    package = original; package["data"]["extra"] = 1;
    expect_package_error(package);
    package = original; package["data"]["flat"]["maximum"] = 590;
    expect_package_error(package);
}

void byte_limits(const Json& fixture) {
    const auto raw = fixture["package_payload"].get<std::string>();
    const auto padding = 65536 - raw.size();
    const auto maximum = seal(raw + std::string(padding, ' '));
    require(hf::validate_calibration_binding(maximum).is_object(), "exact 65536-byte payload limit should be accepted");
    // Only ASCII data is involved here, so the fixture's compact dump plus
    // newline has the same length as Python's ensure_ascii=True encoding.
    const auto original_encoded_size = fixture.dump().size() + 1;
    const auto tabs = 131072 - original_encoded_size - padding;
    require(tabs < padding, "fixture does not fit exact outer-byte boundary construction");
    const auto exact = seal(raw + std::string(tabs, '\t') + std::string(padding - tabs, ' '));
    require(hf::validate_calibration_binding(exact).is_object(), "exact 131072-byte binding limit should be accepted");
    const auto too_large = seal(raw + std::string(tabs + 1, '\t') + std::string(padding - tabs - 1, ' '));
    expect_error("invalid_binding", [&] { hf::validate_calibration_binding(too_large); });
}
} // namespace

int main(int argc, char** argv) {
    try {
        const auto fixtures = argc > 1 ? std::filesystem::path(argv[1]) :
            std::filesystem::path(__FILE__).parent_path().parent_path().parent_path().parent_path() / "camera/calibration/fixtures";
        const auto uncalibrated = load(fixtures, "binding-uncalibrated.json");
        const auto synthetic = load(fixtures, "binding-synthetic.json");
        const auto unicode = load(fixtures, "binding-unicode.json");
        fixtures_and_descriptors(uncalibrated, synthetic, unicode);
        outer_schema_and_hashes(uncalibrated);
        exact_spelling_and_parse_guards(uncalibrated, synthetic);
        uncalibrated_semantics_and_unicode(uncalibrated, unicode);
        synthetic_semantics(synthetic);
        byte_limits(uncalibrated);
        std::cout << "PASS: " << checks << " calibration binding checks\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL after " << checks << " calibration checks: " << error.what() << '\n';
        return 1;
    }
}
