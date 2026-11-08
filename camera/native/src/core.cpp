#include "hf/core.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <new>
#include <utility>

namespace hf {
namespace {
constexpr std::uint64_t kFnvOffset = 14695981039346656037ull;
constexpr std::uint64_t kFnvPrime = 1099511628211ull;

std::uint64_t checksum_sample(std::uint64_t hash, std::uint16_t sample) {
    hash = (hash ^ (sample & 0xffu)) * kFnvPrime;
    return (hash ^ (sample >> 8u)) * kFnvPrime;
}

std::size_t checked_pixel_count(std::uint32_t width, std::uint32_t height) {
    const auto limit = (std::numeric_limits<std::size_t>::max)();
    if (width != 0 && static_cast<std::uint64_t>(height) > limit / width) {
        throw CaptureError(ErrorCode::MemoryLimit, "frame dimensions overflow address space");
    }
    const auto count = static_cast<std::size_t>(width) * height;
    if (count > limit / sizeof(std::uint16_t)) {
        throw CaptureError(ErrorCode::MemoryLimit, "frame byte count overflows address space");
    }
    if (count > std::vector<std::uint16_t>().max_size()) {
        throw CaptureError(ErrorCode::MemoryLimit, "frame exceeds vector size limit");
    }
    return count;
}

bool valid_request_id(const std::string& value) {
    if (value.empty() || value.size() > 128) return false;
    for (const unsigned char c : value) {
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
              (c >= '0' && c <= '9') || c == '.' || c == '_' || c == ':' || c == '-')) {
            return false;
        }
    }
    return true;
}

void integrity_failure(const char* message) {
    throw CaptureError(ErrorCode::IntegrityFailure, message);
}
} // namespace

CaptureError::CaptureError(ErrorCode code, const std::string& message)
    : std::runtime_error(message), code_(code) {}

std::size_t validate_request(const FrameRequest& request, const CaptureLimits& limits) {
    if (request.width < 2 || request.height < 2) {
        throw CaptureError(ErrorCode::InvalidRequest, "width and height must be at least 2");
    }
    if (request.exposure_us == 0 || !std::isfinite(request.gain) || request.gain <= 0) {
        throw CaptureError(ErrorCode::InvalidRequest, "exposure and gain must be positive and finite");
    }
    // Match simulation/bayer.py's operation order and double precision.
    const double scale = (request.exposure_us / 10000.0) * request.gain;
    if (!std::isfinite(scale)) {
        throw CaptureError(ErrorCode::InvalidRequest, "exposure and gain product is too large");
    }
    if (request.scene != "scene" && request.scene != "flat" && request.scene != "dark") {
        throw CaptureError(ErrorCode::InvalidRequest, "scene must be scene, flat, or dark");
    }
    if (!valid_request_id(request.request_id)) {
        throw CaptureError(ErrorCode::InvalidRequest, "request ID must be 1-128 ASCII letters, digits, or ._:-");
    }
    const auto count = checked_pixel_count(request.width, request.height);
    if (count > limits.max_frame_bytes / sizeof(std::uint16_t)) {
        throw CaptureError(ErrorCode::MemoryLimit, "frame exceeds configured byte limit");
    }
    return count;
}

void check_control(const CaptureControl& control) {
    if (control.cancelled && control.cancelled->load(std::memory_order_relaxed)) {
        throw CaptureError(ErrorCode::Cancelled, "capture cancelled");
    }
    if (std::chrono::steady_clock::now() >= control.deadline) {
        throw CaptureError(ErrorCode::DeadlineExceeded, "capture deadline exceeded");
    }
}

void validate_frame(const Frame& frame, const CaptureControl& control) {
    check_control(control);
    const auto& metadata = frame.metadata;
    const auto& request = metadata.requested;
    std::size_t count = 0;
    try {
        CaptureLimits limits;
        limits.max_frame_bytes = (std::numeric_limits<std::size_t>::max)();
        count = validate_request(request, limits);
    } catch (const CaptureError&) {
        integrity_failure("frame contains invalid request metadata");
    }
    if (metadata.source != "synthetic-native" || metadata.bayer_pattern != "RGGB" ||
        metadata.bit_depth != kBitDepth || metadata.frame_id == 0 ||
        metadata.captured_at_unix_ns == 0 || metadata.actual_width != request.width ||
        metadata.actual_height != request.height ||
        metadata.actual_exposure_us != request.exposure_us || metadata.actual_gain != request.gain) {
        integrity_failure("frame metadata is inconsistent with the synthetic backend");
    }
    if (frame.pixels.size() != count) integrity_failure("frame sample count does not match dimensions");
    auto hash = kFnvOffset;
    for (std::uint32_t y = 0; y < request.height; ++y) {
        check_control(control);
        const auto offset = static_cast<std::size_t>(y) * request.width;
        for (std::uint32_t x = 0; x < request.width; ++x) {
            const auto sample = frame.pixels[offset + x];
            if (sample > kWhiteLevel) integrity_failure("frame contains an out-of-range 14-bit sample");
            hash = checksum_sample(hash, sample);
        }
    }
    check_control(control);
    if (hash != frame.pixel_checksum) integrity_failure("frame pixel checksum mismatch");
}

BufferPool::BufferPool(std::size_t max_bytes, std::size_t max_buffers)
    : max_bytes_(max_bytes), max_buffers_(max_buffers) {}

std::vector<std::uint16_t> BufferPool::acquire(std::size_t pixel_count) {
    if (pixel_count > std::vector<std::uint16_t>().max_size()) {
        throw CaptureError(ErrorCode::MemoryLimit, "buffer exceeds vector size limit");
    }
    std::vector<std::uint16_t> result;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        auto best = buffers_.end();
        for (auto candidate = buffers_.begin(); candidate != buffers_.end(); ++candidate) {
            if (candidate->capacity() >= pixel_count &&
                (best == buffers_.end() || candidate->capacity() < best->capacity())) best = candidate;
        }
        if (best != buffers_.end()) {
            retained_bytes_ -= best->capacity() * sizeof(std::uint16_t);
            result = std::move(*best);
            buffers_.erase(best);
        }
    }
    try {
        result.resize(pixel_count);
    } catch (const std::bad_alloc&) {
        throw CaptureError(ErrorCode::MemoryLimit, "could not allocate frame buffer");
    }
    return result;
}

void BufferPool::release(std::vector<std::uint16_t>&& buffer) noexcept {
    auto owned = std::move(buffer);
    // Capacity, rather than size, is the memory retained by a vector.
    const auto capacity = owned.capacity();
    if (capacity == 0 || capacity > max_bytes_ / sizeof(std::uint16_t)) return;
    const auto bytes = capacity * sizeof(std::uint16_t);
    try {
        std::lock_guard<std::mutex> lock(mutex_);
        if (buffers_.size() >= max_buffers_ || bytes > max_bytes_ - retained_bytes_) return;
        owned.clear();
        buffers_.push_back(std::move(owned));
        retained_bytes_ += bytes;
    } catch (...) {
        // Recycling is optional and must not mask the capture's result/error.
    }
}

std::size_t BufferPool::retained_bytes() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return retained_bytes_;
}

std::size_t BufferPool::retained_buffers() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return buffers_.size();
}

SyntheticCamera::SyntheticCamera(CaptureLimits limits)
    : limits_(limits), pool_(limits.max_retained_bytes, limits.max_retained_buffers) {}

Frame SyntheticCamera::capture(const FrameRequest& request, const CaptureControl& control) {
    const auto count = validate_request(request, limits_);
    check_control(control);
    Frame frame;
    frame.pixels = pool_.acquire(count);
    try {
        frame.metadata.requested = request;
        frame.metadata.actual_width = request.width;
        frame.metadata.actual_height = request.height;
        frame.metadata.actual_exposure_us = request.exposure_us;
        frame.metadata.actual_gain = request.gain;
        frame.metadata.frame_id = next_frame_id_.fetch_add(1, std::memory_order_relaxed);
        frame.metadata.captured_at_unix_ns = static_cast<std::uint64_t>(
            std::chrono::duration_cast<std::chrono::nanoseconds>(
                std::chrono::system_clock::now().time_since_epoch()).count());
        const double scale = (request.exposure_us / 10000.0) * request.gain;
        auto hash = kFnvOffset;
        for (std::uint32_t y = 0; y < request.height; ++y) {
            check_control(control);
            const auto offset = static_cast<std::size_t>(y) * request.width;
            for (std::uint32_t x = 0; x < request.width; ++x) {
                std::uint16_t sample = kBlackLevel;
                if (request.scene != "dark") {
                    std::uint64_t brightness = 2048;
                    if (request.scene == "scene") {
                        brightness = 256 + (4096ull * x / (request.width - 1)) +
                                     (1024ull * y / (request.height - 1));
                        if (((x / 16) + (y / 16)) % 2) brightness += 512;
                    }
                    const double response = y % 2 ? (x % 2 ? 0.6 : 0.8) : (x % 2 ? 0.8 : 1.0);
                    const double signal = static_cast<double>(brightness) * response * scale;
                    sample = signal >= (kWhiteLevel - kBlackLevel) ? kWhiteLevel :
                        static_cast<std::uint16_t>(kBlackLevel + static_cast<std::uint16_t>(signal));
                }
                frame.pixels[offset + x] = sample;
                hash = checksum_sample(hash, sample);
            }
        }
        frame.pixel_checksum = hash;
        validate_frame(frame, control);
        return frame;
    } catch (...) {
        pool_.release(std::move(frame.pixels));
        throw;
    }
}

void SyntheticCamera::recycle(Frame&& frame) noexcept {
    pool_.release(std::move(frame.pixels));
    frame.pixel_checksum = 0;
}

std::size_t SyntheticCamera::retained_buffer_bytes() const { return pool_.retained_bytes(); }
std::size_t SyntheticCamera::retained_buffers() const { return pool_.retained_buffers(); }

} // namespace hf
