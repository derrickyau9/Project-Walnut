#pragma once

#include <string>
#include <nlohmann/json.hpp>

namespace hf {

// Validate the exact v1 binding and detached package payload. Hashes describe
// the original ASCII payload bytes, never a JSON reserialization. No pixel
// correction, measured calibration, authenticity, or signature is asserted.
nlohmann::json validate_calibration_binding(
    const nlohmann::json& binding,
    const std::string& expected_sensor_id = "HF-Sim");

// Six trace fields for a validated binding, or the explicit unbound state.
nlohmann::json calibration_descriptor(const nlohmann::json& binding_or_null);

} // namespace hf
