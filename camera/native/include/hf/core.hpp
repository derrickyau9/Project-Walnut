#pragma once

#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <mutex>
#include <stdexcept>
#include <string>
#include <vector>

namespace hf {

// This backend produces synthetic RGGB samples. It does not drive a sensor.
constexpr std::uint16_t kBitDepth = 14;
constexpr std::uint16_t kWhiteLevel = 16383;
constexpr std::uint16_t kBlackLevel = 64;

enum class ErrorCode {
    InvalidRequest,
    MemoryLimit,
    Cancelled,
    DeadlineExceeded,
    IntegrityFailure
};

class CaptureError : public std::runtime_error {
public:
    CaptureError(ErrorCode code, const std::string& message);
    ErrorCode code() const noexcept { return code_; }
private:
    ErrorCode code_;
};

struct FrameRequest {
    std::uint32_t width = 640;
    std::uint32_t height = 480;
    std::uint32_t exposure_us = 10000;
    double gain = 1.0;
    std::string scene = "scene";
    std::string request_id;
};

struct CaptureControl {
    // The caller keeps the atomic alive until capture returns. Both checks are
    // made before allocation, at every row, and during frame validation.
    const std::atomic_bool* cancelled = nullptr;
    std::chrono::steady_clock::time_point deadline =
        (std::chrono::steady_clock::time_point::max)();
};

struct CaptureLimits {
    std::size_t max_frame_bytes = 128u * 1024u * 1024u;
    std::size_t max_retained_bytes = 64u * 1024u * 1024u;
    std::size_t max_retained_buffers = 2;
};

struct FrameMetadata {
    FrameRequest requested;
    std::uint32_t actual_width = 0;
    std::uint32_t actual_height = 0;
    std::uint32_t actual_exposure_us = 0;
    double actual_gain = 0;
    std::uint64_t frame_id = 0;
    std::uint64_t captured_at_unix_ns = 0;
    std::string source = "synthetic-native";
    std::string bayer_pattern = "RGGB";
    std::uint16_t bit_depth = kBitDepth;
};

struct Frame {
    FrameMetadata metadata;
    std::vector<std::uint16_t> pixels;
    // FNV-1a over each sample's low then high byte, independent of host byte
    // order. This detects accidental buffer corruption, not hostile changes.
    std::uint64_t pixel_checksum = 0;
};

std::size_t validate_request(const FrameRequest& request,
                             const CaptureLimits& limits = {});
void check_control(const CaptureControl& control);
void validate_frame(const Frame& frame, const CaptureControl& control = {});

// Only retained, returned buffers are bounded by this pool. Callers own live
// frames and must bound their own in-flight frame count. No buffer is shared.
class BufferPool {
public:
    BufferPool(std::size_t max_bytes, std::size_t max_buffers);
    std::vector<std::uint16_t> acquire(std::size_t pixel_count);
    void release(std::vector<std::uint16_t>&& buffer) noexcept;
    std::size_t retained_bytes() const;
    std::size_t retained_buffers() const;
private:
    const std::size_t max_bytes_;
    const std::size_t max_buffers_;
    mutable std::mutex mutex_;
    std::vector<std::vector<std::uint16_t>> buffers_;
    std::size_t retained_bytes_ = 0;
};

class SyntheticCamera {
public:
    explicit SyntheticCamera(CaptureLimits limits = {});
    Frame capture(const FrameRequest& request,
                  const CaptureControl& control = {});
    // Explicit return permits reuse without retaining a buffer behind a Frame.
    void recycle(Frame&& frame) noexcept;
    std::size_t retained_buffer_bytes() const;
    std::size_t retained_buffers() const;
private:
    CaptureLimits limits_;
    BufferPool pool_;
    std::atomic<std::uint64_t> next_frame_id_{1};
};

} // namespace hf
