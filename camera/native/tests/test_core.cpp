#include "hf/core.hpp"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <future>
#include <iostream>
#include <limits>
#include <set>
#include <stdexcept>
#include <thread>
#include <utility>

namespace {
int assertions = 0;

void require(bool condition, const char* message) {
    ++assertions;
    if (!condition) throw std::runtime_error(message);
}

template<class Function>
void expect_error(hf::ErrorCode expected, Function function) {
    bool caught = false;
    try {
        function();
    } catch (const hf::CaptureError& error) {
        caught = true;
        require(error.code() == expected, "capture returned wrong error code");
    }
    require(caught, "expected capture failure did not occur");
}

hf::FrameRequest request(std::uint32_t width = 4, std::uint32_t height = 4) {
    hf::FrameRequest value;
    value.width = width;
    value.height = height;
    value.request_id = "native-core-test";
    return value;
}

void test_reference_pixels_and_metadata() {
    hf::SyntheticCamera camera;
    const auto input = request();
    auto first = camera.capture(input);
    // Literal output of simulation.bayer.generate_bayer(4, 4); this catches CFA,
    // integer ramp rounding and floating-point truncation differences.
    const std::vector<std::uint16_t> expected = {
        320,1360,3050,3545,541,1241,2725,2879,
        1002,1906,3732,4091,1088,1651,3272,3289
    };
    require(first.pixels == expected, "native scene differs from Python reference");
    require(first.metadata.requested.request_id == input.request_id, "request ID lost");
    require(first.metadata.actual_width == input.width && first.metadata.actual_height == input.height,
            "actual dimensions missing");
    require(first.metadata.actual_exposure_us == input.exposure_us &&
            first.metadata.actual_gain == input.gain, "actual exposure/gain missing");
    require(first.metadata.captured_at_unix_ns > 0 && first.metadata.frame_id > 0,
            "capture identity/timestamp missing");
    require(first.metadata.source == "synthetic-native" && first.metadata.bayer_pattern == "RGGB" &&
            first.metadata.bit_depth == 14, "synthetic interpretation missing");
    hf::validate_frame(first);
    auto second = camera.capture(input);
    require(first.pixels == second.pixels && first.pixel_checksum == second.pixel_checksum,
            "equal inputs must have equal pixels/checksum");
    require(first.metadata.frame_id != second.metadata.frame_id, "frame identity repeated");

    auto flat = request(2, 2);
    flat.exposure_us = 5000;
    flat.gain = 1.5;
    flat.scene = "flat";
    require(camera.capture(flat).pixels == std::vector<std::uint16_t>({1600,1292,1292,985}),
            "flat response differs from Python reference");
    flat.scene = "dark";
    require(camera.capture(flat).pixels == std::vector<std::uint16_t>({64,64,64,64}),
            "dark frame must equal synthetic black level");
    flat.scene = "flat";
    flat.gain = (std::numeric_limits<double>::max)();
    // Finite scale with an overflowing per-pixel signal must clip safely.
    require(camera.capture(flat).pixels == std::vector<std::uint16_t>({16383,16383,16383,16383}),
            "large finite gain must saturate without integer conversion overflow");

    auto odd = request(35, 33);
    const auto checker = camera.capture(odd);
    require(checker.pixels.size() == 1155, "odd dimensions lost samples");
    // Reference samples cross both checker axes and include unequal CFA sites.
    require(checker.pixels[16] == 2759 && checker.pixels[16 * 35] == 1344,
            "checker transitions differ from Python scene");
}

void test_invalid_requests_and_memory_limit() {
    hf::SyntheticCamera camera;
    auto input = request();
    input.width = 1;
    expect_error(hf::ErrorCode::InvalidRequest, [&] { camera.capture(input); });
    input = request(); input.height = 0;
    expect_error(hf::ErrorCode::InvalidRequest, [&] { camera.capture(input); });
    input = request(); input.exposure_us = 0;
    expect_error(hf::ErrorCode::InvalidRequest, [&] { camera.capture(input); });
    for (const double gain : {0.0, -1.0, std::numeric_limits<double>::infinity(),
                              std::numeric_limits<double>::quiet_NaN()}) {
        input = request(); input.gain = gain;
        expect_error(hf::ErrorCode::InvalidRequest, [&] { camera.capture(input); });
    }
    input = request(); input.exposure_us = (std::numeric_limits<std::uint32_t>::max)();
    input.gain = (std::numeric_limits<double>::max)();
    expect_error(hf::ErrorCode::InvalidRequest, [&] { camera.capture(input); });
    input = request(); input.scene = "hardware";
    expect_error(hf::ErrorCode::InvalidRequest, [&] { camera.capture(input); });
    for (const std::string& id : {std::string(), std::string("bad/id"), std::string(129, 'x')}) {
        input = request(); input.request_id = id;
        expect_error(hf::ErrorCode::InvalidRequest, [&] { camera.capture(input); });
    }
    hf::CaptureLimits limits;
    limits.max_frame_bytes = 31;
    hf::SyntheticCamera small(limits);
    input = request();
    expect_error(hf::ErrorCode::MemoryLimit, [&] { small.capture(input); });
    limits.max_frame_bytes = 32;
    hf::SyntheticCamera exact(limits);
    require(exact.capture(input).pixels.size() == 16, "exact byte limit should fit");
    input.width = (std::numeric_limits<std::uint32_t>::max)();
    input.height = input.width;
    expect_error(hf::ErrorCode::MemoryLimit, [&] { camera.capture(input); });
    limits.max_frame_bytes = (std::numeric_limits<std::size_t>::max)();
    expect_error(hf::ErrorCode::MemoryLimit, [&] { hf::validate_request(input, limits); });
}

void test_corruption_detection() {
    hf::SyntheticCamera camera;
    auto frame = camera.capture(request());
    frame.pixels[3] ^= 1;
    expect_error(hf::ErrorCode::IntegrityFailure, [&] { hf::validate_frame(frame); });
    frame = camera.capture(request()); frame.pixels[3] = 16384;
    expect_error(hf::ErrorCode::IntegrityFailure, [&] { hf::validate_frame(frame); });
    frame = camera.capture(request()); frame.pixels.pop_back();
    expect_error(hf::ErrorCode::IntegrityFailure, [&] { hf::validate_frame(frame); });
    frame = camera.capture(request()); frame.metadata.actual_gain = 2;
    expect_error(hf::ErrorCode::IntegrityFailure, [&] { hf::validate_frame(frame); });
    frame = camera.capture(request()); frame.metadata.bayer_pattern = "BGGR";
    expect_error(hf::ErrorCode::IntegrityFailure, [&] { hf::validate_frame(frame); });
    frame = camera.capture(request()); frame.metadata.requested.width = 0;
    expect_error(hf::ErrorCode::IntegrityFailure, [&] { hf::validate_frame(frame); });
    frame = camera.capture(request()); frame.pixel_checksum = 0;
    expect_error(hf::ErrorCode::IntegrityFailure, [&] { hf::validate_frame(frame); });
}

void test_cancellation_deadline_and_recovery() {
    hf::SyntheticCamera camera;
    std::atomic_bool cancelled{true};
    hf::CaptureControl control;
    control.cancelled = &cancelled;
    expect_error(hf::ErrorCode::Cancelled, [&] { camera.capture(request(), control); });
    require(camera.retained_buffers() == 0, "pre-cancelled capture allocated a buffer");
    cancelled = false;
    control.deadline = std::chrono::steady_clock::now();
    expect_error(hf::ErrorCode::DeadlineExceeded, [&] { camera.capture(request(), control); });
    require(camera.retained_buffers() == 0, "expired capture allocated a buffer");
    control.deadline = (std::chrono::steady_clock::time_point::max)();
    const auto started = std::chrono::steady_clock::now();
    auto running = std::async(std::launch::async, [&] { return camera.capture(request(4096, 4096), control); });
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
    cancelled = true;
    expect_error(hf::ErrorCode::Cancelled, [&] { running.get(); });
    require(std::chrono::steady_clock::now() - started < std::chrono::seconds(3),
            "capture did not respond promptly to cancellation");
    require(camera.retained_buffer_bytes() <= 64u * 1024u * 1024u, "cancelled capture exceeded pool limit");
    cancelled = false;
    control.deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(1);
    expect_error(hf::ErrorCode::DeadlineExceeded, [&] { camera.capture(request(4096, 4096), control); });
    const auto recovered = camera.capture(request());
    require(recovered.pixels[0] == 320, "camera failed to recover after cancellation/deadline");
    cancelled = true;
    control.deadline = (std::chrono::steady_clock::time_point::max)();
    expect_error(hf::ErrorCode::Cancelled, [&] { hf::validate_frame(recovered, control); });
}

void test_bounded_pool_and_concurrent_capture() {
    hf::BufferPool pool(128, 2);
    auto first = pool.acquire(16);
    const auto first_data = first.data();
    const auto first_bytes = first.capacity() * sizeof(std::uint16_t);
    pool.release(std::move(first));
    require(first.empty(), "released buffer retained by caller");
    require(pool.retained_buffers() == 1 && pool.retained_bytes() == first_bytes,
            "pool failed to account for capacity");
    auto reused = pool.acquire(8);
    require(reused.data() == first_data && reused.size() == 8, "pool did not reuse suitably sized buffer");
    require(pool.retained_bytes() == 0, "in-flight buffer counted as retained");
    auto second = pool.acquire(16);
    auto third = pool.acquire(16);
    pool.release(std::move(reused)); pool.release(std::move(second)); pool.release(std::move(third));
    require(pool.retained_buffers() == 2 && pool.retained_bytes() <= 128, "buffer-count budget not enforced");
    auto oversized = pool.acquire(100);
    pool.release(std::move(oversized));
    require(oversized.empty() && pool.retained_bytes() <= 128, "oversized buffer should be discarded");

    hf::CaptureLimits limits;
    limits.max_retained_bytes = 64;
    limits.max_retained_buffers = 1;
    hf::SyntheticCamera camera(limits);
    auto frame = camera.capture(request());
    camera.recycle(std::move(frame));
    require(frame.pixels.empty() && camera.retained_buffers() == 1, "explicit frame recycle failed");
    std::vector<std::future<hf::Frame>> pending;
    for (int i = 0; i < 8; ++i) {
        pending.push_back(std::async(std::launch::async, [&camera, i] {
            auto input = request(32, 33);
            input.request_id = "parallel-" + std::to_string(i);
            return camera.capture(input);
        }));
    }
    std::set<std::uint64_t> ids;
    std::uint64_t checksum = 0;
    for (auto& future : pending) {
        auto captured = future.get();
        hf::validate_frame(captured);
        ids.insert(captured.metadata.frame_id);
        if (checksum) require(checksum == captured.pixel_checksum, "concurrent captures differ");
        checksum = captured.pixel_checksum;
        camera.recycle(std::move(captured));
    }
    require(ids.size() == 8, "concurrent frame IDs are not unique");
    require(camera.retained_buffer_bytes() <= 64 && camera.retained_buffers() <= 1,
            "camera recycle exceeded configured retention limits");
}
} // namespace

int main() {
    try {
        test_reference_pixels_and_metadata();
        test_invalid_requests_and_memory_limit();
        test_corruption_detection();
        test_cancellation_deadline_and_recovery();
        test_bounded_pool_and_concurrent_capture();
        std::cout << "PASS: " << assertions << " native core assertions\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL after " << assertions << " assertions: " << error.what() << '\n';
        return 1;
    }
}
