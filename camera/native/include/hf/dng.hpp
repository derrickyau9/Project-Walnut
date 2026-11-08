#pragma once

#include "hf/core.hpp"

#include <cstdint>
#include <filesystem>
#include <string>

namespace hf {

// Identifiers describe a simulated capture, never measured sensor calibration.
struct DngIdentity {
    std::string sensor_id = "HF-Sim";
    std::string calibration_id = "synthetic-uncalibrated";
    std::string capture_id;
    // An empty value uses the request ID, which survives worker restarts.
    std::string frame_id;
};

// Internal ownership/integrity receipt from the file descriptor actually opened
// by the writer. The checksum detects accidental changes, not hostile forgery.
// It is never exposed as a filesystem capability or carried in pixel JSON.
struct DngOutputReceipt {
    bool created = false;
    bool complete = false;
    std::uint64_t device = 0;
    std::uint64_t file_id = 0;
    std::uint64_t bytes = 0;
    std::uint64_t checksum = 0;
};

// The returned JSON contains metadata only, with the same capture keys as the
// Python writer. It is also used by the CLI's small stdout response.
std::string capture_metadata_json(const Frame& frame,
                                  const DngIdentity& identity = {});

// Write a new uncompressed little-endian CFA DNG. Existing paths are rejected.
// Closing/checking the supplied temporary file is this function's responsibility;
// syncing, final rename and journal transactions belong to the calling authority.
// If supplied, created_output is set only after this writer created its own
// output entry, including when a later write/close throws. It stays false when
// validation or exclusive creation fails, so cleanup cannot remove a conflict.
// An optional receipt identifies the descriptor this writer created, and after
// successful close contains the expected byte count and whole-file checksum.
// Callers must verify it before syncing/publication and must not remove a path
// that has since been replaced by a different file.
std::uint64_t write_dng(const std::filesystem::path& path, const Frame& frame,
                        const DngIdentity& identity = {}, bool* created_output = nullptr,
                        DngOutputReceipt* receipt = nullptr);

} // namespace hf
