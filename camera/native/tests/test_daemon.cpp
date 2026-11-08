#include "hf/daemon.hpp"
#include "hf/sha256.hpp"
#include <sqlite3.h>

#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>

namespace {
int assertions = 0;
std::uint64_t next_id = 1;

void require(bool condition, const char* message) {
    ++assertions;
    if (!condition) throw std::runtime_error(message);
}

template<class Function>
void expect_error(const std::string& code, Function function) {
    bool caught = false;
    try { function(); }
    catch (const hf::DaemonError& error) {
        caught = true; require(error.code() == code, "wrong daemon error code");
    }
    require(caught, "expected daemon failure did not occur");
}

struct Directory {
    std::filesystem::path path;
    Directory() {
        const auto stamp = std::chrono::high_resolution_clock::now().time_since_epoch().count();
        path = std::filesystem::temp_directory_path() / ("hf-native-authority-tests-" + std::to_string(stamp));
        std::filesystem::create_directory(path);
    }
    ~Directory() { std::error_code ignored; std::filesystem::remove_all(path, ignored); }
};

hf::Json request(const hf::Json& lease, const std::string& fault = {}) {
    const auto suffix = std::to_string(next_id++);
    const auto id = "00000000-0000-0000-0000-" + std::string(12 - suffix.size(), '0') + suffix;
    hf::Json value = {{"request_id", id}, {"sensor_mode", "sim-small"},
        {"requested_exposure_us", 10000}, {"requested_analogue_gain", 1.0},
        {"output_format", "dng"}, {"simulation_scene", "scene"},
        {"token", lease.at("token")}, {"control_epoch", lease.at("epoch")}};
    if (!fault.empty()) value["fault"] = fault;
    return value;
}

hf::Json wait(hf::CameraDaemon& daemon, const std::string& id) {
    const auto timeout = std::chrono::steady_clock::now() + std::chrono::seconds(8);
    while (std::chrono::steady_clock::now() < timeout) {
        const auto record = daemon.query_capture(id);
        if (!record.is_null() && record.at("status") != "accepted" && record.at("status") != "capturing" &&
            daemon.get_state().at("camera_state") == "idle") return record;
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
    }
    throw std::runtime_error("native capture did not terminate");
}

std::size_t files(const Directory& directory, const std::string& extension) {
    std::size_t count = 0;
    for (const auto& item : std::filesystem::directory_iterator(directory.path / "photos"))
        if (item.path().extension() == extension) ++count;
    return count;
}

void test_identity_durability_and_fencing() {
    Directory directory;
    hf::Json input, completed, old_lease;
    {
        hf::CameraDaemon daemon(directory.path);
        old_lease = daemon.acquire("first owner");
        require(old_lease.at("token").get<std::string>().size() == 64, "OS control token missing");
        expect_error("already_running", [&] { hf::CameraDaemon duplicate(directory.path); });
        expect_error("control_busy", [&] { daemon.acquire("second owner"); });
        input = request(old_lease);
        require(daemon.capture(input).at("status") == "accepted", "capture not durably admitted");
        completed = wait(daemon, input.at("request_id"));
        require(completed.at("status") == "complete", "capture did not complete");
        require(completed.at("requested_exposure_us") == completed.at("actual_exposure_us"), "actual exposure missing");
        require(completed.at("request_id") == completed.at("frame_id"), "frame ID differs from durable request ID");
        require(completed.at("backend") == "native" && completed.at("authority") == "native", "authority identity missing");
        require(std::filesystem::is_regular_file(std::filesystem::u8path(completed.at("file_path").get<std::string>())),
                "complete published before output exists");
        require(daemon.capture(input) == completed, "duplicate re-executed completed capture");
        auto conflict = input; conflict["requested_exposure_us"] = 20000;
        expect_error("request_conflict", [&] { daemon.capture(conflict); });
        require(files(directory, ".dng") == 1, "duplicate created another output");
        daemon.close();
    }
    {
        hf::CameraDaemon daemon(directory.path);
        require(daemon.query_capture(input.at("request_id")) == completed, "completed request did not survive restart");
        const auto current = daemon.acquire("after restart");
        require(current.at("epoch").get<std::uint64_t>() > old_lease.at("epoch").get<std::uint64_t>(), "epoch did not advance durably");
        expect_error("control_denied", [&] { daemon.heartbeat(old_lease.at("token"), old_lease.at("epoch")); });
        input["token"] = current.at("token"); input["control_epoch"] = current.at("epoch");
        require(daemon.capture(input) == completed, "restart duplicate re-executed capture");
        daemon.release(current.at("token"), current.at("epoch"));
        const auto next = daemon.acquire("next owner");
        require(next.at("epoch").get<std::uint64_t>() > current.at("epoch").get<std::uint64_t>(), "reacquire did not fence old epoch");
        daemon.close();
        expect_error("closed", [&] { daemon.get_state(); });
    }
}

void test_lease_expiry_cancellation_and_deadline() {
    Directory directory;
    hf::CameraDaemon daemon(directory.path, 0.15);
    auto lease = daemon.acquire("short lease");
    std::this_thread::sleep_for(std::chrono::milliseconds(90));
    daemon.heartbeat(lease.at("token"), lease.at("epoch"));
    std::this_thread::sleep_for(std::chrono::milliseconds(90));
    require(!daemon.get_state().at("control").is_null(), "heartbeat did not extend lease");
    auto input = request(lease, "delay");
    daemon.capture(input);
    expect_error("camera_busy", [&] { daemon.close(); });
    std::this_thread::sleep_for(std::chrono::milliseconds(180));
    require(daemon.get_state().at("control").is_null(), "control lease did not expire");
    auto next = daemon.acquire("new owner");
    expect_error("control_denied", [&] { daemon.cancel_capture(lease.at("token"), lease.at("epoch"), input.at("request_id")); });
    expect_error("camera_busy", [&] { daemon.capture(request(next)); });
    require(daemon.cancel_capture(next.at("token"), next.at("epoch"), input.at("request_id")).at("requested"), "new owner cannot cancel accepted request");
    require(wait(daemon, input.at("request_id")).at("status") == "cancelled", "cancel did not stop delayed capture");
    require(files(directory, ".dng") == 0, "cancelled capture published output");
    input = request(next, "delay");
    const auto now = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::system_clock::now().time_since_epoch()).count();
    input["deadline_unix_ms"] = now + 50;
    daemon.capture(input);
    const auto expired = wait(daemon, input.at("request_id"));
    require(expired.at("status") == "failed", "deadline did not fail capture");
    require(expired.at("error").get<std::string>().find("deadline") != std::string::npos, "deadline reason missing");
    input = request(next); input["deadline_unix_ms"] = now - 1;
    expect_error("deadline_expired", [&] { daemon.capture(input); });
    require(daemon.query_capture(input.at("request_id")).is_null(), "expired request entered journal");
}

void test_simulated_faults_and_start_failure() {
    Directory directory;
    hf::CameraDaemon daemon(directory.path);
    const auto lease = daemon.acquire("fault tests");
    for (const auto& fault : {"storage_full", "corrupt_frame"}) {
        const auto input = request(lease, fault);
        daemon.capture(input);
        require(wait(daemon, input.at("request_id")).at("status") == "failed", "injected fault did not fail capture");
        require(files(directory, ".dng") == 0 && files(directory, ".partial") == 0, "fault left output artifacts");
    }
    auto input = request(lease, "disconnect");
    daemon.capture(input); wait(daemon, input.at("request_id"));
    require(!daemon.get_state().at("connected").get<bool>(), "disconnect not sticky");
    expect_error("device_disconnected", [&] { daemon.capture(request(lease)); });
    expect_error("device_disconnected", [&] { daemon.configure(lease.at("token"), lease.at("epoch"), hf::Json::object()); });
    daemon.reconnect(lease.at("token"), lease.at("epoch"));
    input = request(lease, "worker_start_failure");
    expect_error("worker_start_failed", [&] { daemon.capture(input); });
    require(daemon.query_capture(input.at("request_id")).at("status") == "failed", "worker start failure not persisted");
    require(daemon.capture(input).at("status") == "failed", "worker start failure re-executed");
    input = request(lease); daemon.capture(input);
    require(wait(daemon, input.at("request_id")).at("status") == "complete", "fault left capture slot occupied");
}

void test_journal_failure_reconciliation() {
    Directory directory;
    hf::Json failed;
    {
        hf::CameraDaemon daemon(directory.path);
        const auto lease = daemon.acquire("admission failure");
        failed = request(lease, "admission_commit_failure");
        expect_error("journal_error", [&] { daemon.capture(failed); });
        require(daemon.query_capture(failed.at("request_id")).is_null(), "failed admission was not rolled back");
        require(!daemon.get_state().at("journal_healthy").get<bool>(), "journal failure did not fence mutations");
        expect_error("journal_error", [&] { daemon.acquire("unsafe retry"); });
        require(files(directory, ".dng") == 0, "worker ran after admission failure");
    }
    hf::Json ambiguous;
    {
        hf::CameraDaemon daemon(directory.path);
        const auto lease = daemon.acquire("completion failure");
        require(daemon.query_capture(failed.at("request_id")).is_null(), "failed admission appeared after restart");
        ambiguous = request(lease, "completion_commit_failure");
        daemon.capture(ambiguous);
        require(wait(daemon, ambiguous.at("request_id")).at("status") == "unknown", "post-publication journal failure must be unknown");
        require(files(directory, ".dng") == 1, "completion failure did not exercise publication boundary");
        require(!daemon.get_state().at("journal_healthy").get<bool>(), "completion failure did not fence mutations");
    }
    {
        hf::CameraDaemon daemon(directory.path);
        const auto lease = daemon.acquire("reconcile");
        require(daemon.query_capture(ambiguous.at("request_id")).at("status") == "unknown", "restart did not reconcile incomplete capture");
        ambiguous["token"] = lease.at("token"); ambiguous["control_epoch"] = lease.at("epoch");
        require(daemon.capture(ambiguous).at("status") == "unknown", "ambiguous capture was blindly re-executed");
        require(files(directory, ".dng") == 1, "ambiguous retry created another output");
    }
}

void test_strict_validation_and_foreign_journal() {
    Directory directory;
    {
        hf::CameraDaemon daemon(directory.path);
        const auto lease = daemon.acquire("validation");
        auto input = request(lease); input["requested_exposure_us"] = true;
        expect_error("invalid_request", [&] { daemon.capture(input); });
        input = request(lease); input["deadline_unix_ms"] = true;
        expect_error("invalid_request", [&] { daemon.capture(input); });
        input = request(lease); input["request_id"] = "not-a-uuid";
        expect_error("invalid_request", [&] { daemon.capture(input); });
        input = request(lease); input["unrecognized"] = 1;
        expect_error("invalid_request", [&] { daemon.capture(input); });
        expect_error("invalid_request", [&] { daemon.configure(lease.at("token"), lease.at("epoch"), {{"unknown", 1}}); });
        const auto configured = daemon.configure(lease.at("token"), lease.at("epoch"), {{"requested_exposure_us", 12345}});
        require(configured.at("requested_exposure_us") == 12345, "configure did not apply settings");
    }
    sqlite3* database = nullptr;
    require(sqlite3_open((directory.path / "journal.sqlite3").u8string().c_str(), &database) == SQLITE_OK, "test cannot open journal");
    require(sqlite3_exec(database, "UPDATE meta SET value='python' WHERE key='authority'", nullptr, nullptr, nullptr) == SQLITE_OK,
            "test cannot mark foreign authority");
    sqlite3_close(database);
    expect_error("journal_error", [&] { hf::CameraDaemon foreign(directory.path); });
}

void test_photo_directory_identity_fences_replacements() {
    Directory directory;
    hf::CameraDaemon daemon(directory.path);
    const auto lease = daemon.acquire("photo directory tests");
    const auto photos = directory.path / "photos";
    const auto saved = directory.path / "original-photos";
    std::filesystem::rename(photos, saved);
    std::filesystem::create_directory(photos);
    auto input = request(lease);
    expect_error("storage_error", [&] { daemon.capture(input); });
    require(daemon.query_capture(input.at("request_id")).is_null(), "redirected directory request was admitted");
    std::filesystem::remove(photos);
    std::filesystem::rename(saved, photos);
    input = request(lease, "delay");
    daemon.capture(input);
    std::filesystem::rename(photos, saved);
    std::filesystem::create_directory(photos);
    const auto foreign = photos / (input.at("request_id").get<std::string>() + ".dng.partial");
    { std::ofstream stream(foreign); stream << "foreign staging"; }
    require(wait(daemon, input.at("request_id")).at("status") == "failed", "changed photo directory falsely completed capture");
    std::ifstream stream(foreign);
    const std::string contents((std::istreambuf_iterator<char>(stream)), std::istreambuf_iterator<char>());
    require(contents == "foreign staging", "directory replacement lost foreign staging");
    stream.close();
    require(std::filesystem::is_empty(saved), "changed photo directory produced unexpected capture bytes");
    std::filesystem::remove(foreign);
    std::filesystem::remove(photos);
    std::filesystem::rename(saved, photos);
    require(daemon.capture(input).at("status") == "failed", "directory failure was blindly replayed");
    input = request(lease);
    daemon.capture(input);
    require(wait(daemon, input.at("request_id")).at("status") == "complete", "restored directory did not recover for a new request");
}

void test_durable_settings_do_not_follow_capture_parameters() {
    Directory directory;
    hf::Json accepted;
    {
        hf::CameraDaemon daemon(directory.path);
        const auto lease = daemon.acquire("persistent settings");
        require(daemon.get_state().at("settings_persistent") == true, "state does not advertise persistent settings");
        require(daemon.get_state().at("settings_format") == "hf-device-settings-v1", "state settings format is missing");
        accepted = daemon.configure(lease.at("token"), lease.at("epoch"),
            {{"requested_exposure_us", 23456}, {"requested_analogue_gain", 2.5}, {"simulation_scene", "flat"}});
        require(accepted.at("requested_exposure_us") == 23456, "configured settings were not accepted");
        const auto input = request(lease);
        daemon.capture(input);
        require(wait(daemon, input.at("request_id")).at("status") == "complete", "settings test capture failed");
        require(daemon.get_state().at("settings") == accepted, "capture changed the configured settings baseline");
        expect_error("invalid_request", [&] { daemon.configure(lease.at("token"), lease.at("epoch"), {{"requested_exposure_us", true}}); });
        require(daemon.get_state().at("settings") == accepted, "invalid configure changed settings");
    }
    {
        hf::CameraDaemon daemon(directory.path);
        require(daemon.get_state().at("settings") == accepted, "settings did not survive restart");
        const auto lease = daemon.acquire("rollback settings");
        expect_error("journal_error", [&] { daemon.configure(lease.at("token"), lease.at("epoch"),
            {{"requested_exposure_us", 34567}}, "settings_commit_failure"); });
        require(daemon.get_state().at("settings") == accepted, "failed settings commit changed confirmed memory");
        require(!daemon.get_state().at("journal_healthy").get<bool>(), "failed settings commit did not fence journal");
        expect_error("journal_error", [&] { daemon.configure(lease.at("token"), lease.at("epoch"), {{"requested_exposure_us", 45678}}); });
    }
    {
        hf::CameraDaemon daemon(directory.path);
        require(daemon.get_state().at("settings") == accepted, "settings rollback did not preserve the last committed baseline");
    }
}

hf::Json uncalibrated_binding(const std::string& reason) {
    const auto payload = hf::Json{{"schema_version", 1}, {"sensor_id", "HF-Sim"},
        {"package_id", "runtime-trace"}, {"package_version", "1"}, {"status", "uncalibrated"},
        {"hardware_calibrated", false}, {"source", "none"},
        {"notice", "Not measured hardware calibration; synthetic software data or an uncalibrated marker only."},
        {"reason", reason}, {"data", nullptr}}.dump();
    return {{"binding_schema_version", 1}, {"sensor_id", "HF-Sim"},
        {"binding_id", "cb1-" + hf::sha256_hex("HF-Sim calibration binding v1\n" + payload)},
        {"package_checksum", "sha256:" + hf::sha256_hex(payload)}, {"package_payload", payload},
        {"hardware_calibrated", false}, {"pixel_corrections_applied", false}};
}

void test_binding_snapshot_survives_restart_and_selection_changes() {
    Directory directory;
    const auto binding = uncalibrated_binding("No measured calibration is available.");
    hf::Json captured, input;
    require(hf::sha256_hex("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
            "native SHA-256 reference vector failed");
    {
        hf::CameraDaemon daemon(directory.path);
        const auto lease = daemon.acquire("calibration trace");
        require(daemon.get_state().at("calibration").at("binding_id").is_null(), "fresh state is not explicitly unbound");
        const auto selected = daemon.bind_calibration(lease.at("token"), lease.at("epoch"), binding);
        require(selected.at("calibration_id") == binding.at("binding_id"), "binding trace identity was not selected");
        require(selected.at("hardware_calibrated") == false && selected.at("pixel_corrections_applied") == false,
                "binding claimed measured calibration or corrections");
        auto bad = binding; bad["package_payload"] = binding.at("package_payload").get<std::string>() + " ";
        expect_error("checksum_mismatch", [&] { daemon.bind_calibration(lease.at("token"), lease.at("epoch"), bad); });
        require(daemon.get_state().at("calibration") == selected, "invalid binding changed selected state");
        input = request(lease);
        daemon.capture(input); captured = wait(daemon, input.at("request_id"));
        require(captured.at("status") == "complete", "bound synthetic capture failed");
        require(captured.at("capture_record_version") == 2 && captured.at("calibration_binding") == binding,
                "bound capture lost its immutable snapshot");
        require(captured.at("calibration_id") == binding.at("binding_id") &&
                captured.at("native_evidence").at("calibration_id") == binding.at("binding_id"),
                "bound capture and DNG evidence have different trace identities");
        daemon.clear_calibration(lease.at("token"), lease.at("epoch"));
        require(daemon.capture(input) == captured, "changing selection reinterpreted an admitted request");
        auto unbound = request(lease); daemon.capture(unbound);
        const auto legacy = wait(daemon, unbound.at("request_id"));
        require(!legacy.contains("capture_record_version") && legacy.at("calibration_id") == "synthetic-uncalibrated",
                "unbound capture did not preserve its legacy record shape");
        daemon.bind_calibration(lease.at("token"), lease.at("epoch"), binding);
    }
    {
        hf::CameraDaemon daemon(directory.path);
        require(daemon.get_state().at("calibration").at("binding_id") == binding.at("binding_id"), "binding did not survive restart");
        require(daemon.query_capture(input.at("request_id")) == captured, "bound record did not survive restart");
        const auto lease = daemon.acquire("binding rollback");
        expect_error("journal_error", [&] { daemon.bind_calibration(lease.at("token"), lease.at("epoch"),
            uncalibrated_binding("Different snapshot, still unmeasured."), "binding_commit_failure"); });
        require(daemon.get_state().at("calibration").at("binding_id") == binding.at("binding_id"), "failed binding commit changed memory");
        require(!daemon.get_state().at("journal_healthy").get<bool>(), "binding commit failure did not fence mutations");
    }
    {
        hf::CameraDaemon daemon(directory.path);
        require(daemon.get_state().at("calibration").at("binding_id") == binding.at("binding_id"), "binding rollback lost committed snapshot");
        const auto lease = daemon.acquire("clear binding");
        require(daemon.clear_calibration(lease.at("token"), lease.at("epoch")).at("binding_id").is_null(), "clear did not select explicit unbound state");
    }
}
} // namespace

int main() {
    try {
        test_identity_durability_and_fencing();
        test_lease_expiry_cancellation_and_deadline();
        test_simulated_faults_and_start_failure();
        test_journal_failure_reconciliation();
        test_strict_validation_and_foreign_journal();
        test_photo_directory_identity_fences_replacements();
        test_durable_settings_do_not_follow_capture_parameters();
        test_binding_snapshot_survives_restart_and_selection_changes();
        std::cout << "PASS: " << assertions << " native authority assertions\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL after " << assertions << " assertions: " << error.what() << '\n';
        return 1;
    }
}
