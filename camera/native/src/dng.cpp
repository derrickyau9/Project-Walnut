#include "hf/dng.hpp"

#include <algorithm>
#include <array>
#include <cerrno>
#include <cmath>
#include <cstdio>
#include <iomanip>
#include <limits>
#include <locale>
#include <numeric>
#include <sstream>
#include <stdexcept>
#include <system_error>
#include <utility>
#include <vector>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <io.h>
#else
#include <sys/stat.h>
#include <unistd.h>
#endif

namespace hf {
namespace {

using Bytes = std::vector<std::uint8_t>;

void put16(Bytes& bytes, std::uint16_t value) {
    bytes.push_back(static_cast<std::uint8_t>(value));
    bytes.push_back(static_cast<std::uint8_t>(value >> 8));
}

void put32(Bytes& bytes, std::uint32_t value) {
    put16(bytes, static_cast<std::uint16_t>(value));
    put16(bytes, static_cast<std::uint16_t>(value >> 16));
}

Bytes shorts(std::initializer_list<std::uint16_t> values) {
    Bytes bytes;
    for (const auto value : values) put16(bytes, value);
    return bytes;
}

Bytes longs(std::initializer_list<std::uint32_t> values) {
    Bytes bytes;
    for (const auto value : values) put32(bytes, value);
    return bytes;
}

Bytes ascii(const std::string& value) {
    Bytes bytes(value.begin(), value.end());
    bytes.push_back(0);
    return bytes;
}

// TIFF ASCII stays ASCII even for Unicode identifiers. Decode UTF-8 rather than
// escaping bytes individually, so the resulting JSON preserves the identity.
std::string quoted_identifier(const std::string& text) {
    std::string output = "\"";
    std::size_t characters = 0;
    const char* digits = "0123456789abcdef";
    const auto escape16 = [&](std::uint32_t codepoint) {
        output += "\\u";
        for (int shift = 12; shift >= 0; shift -= 4)
            output += digits[(codepoint >> shift) & 15];
    };
    for (std::size_t i = 0; i < text.size();) {
        const auto first = static_cast<unsigned char>(text[i++]);
        std::uint32_t codepoint = first;
        unsigned int continuation = 0;
        std::uint32_t minimum = 0;
        if (first >= 0xc2 && first <= 0xdf) {
            continuation = 1; codepoint = first & 0x1f; minimum = 0x80;
        } else if (first >= 0xe0 && first <= 0xef) {
            continuation = 2; codepoint = first & 0x0f; minimum = 0x800;
        } else if (first >= 0xf0 && first <= 0xf4) {
            continuation = 3; codepoint = first & 7; minimum = 0x10000;
        } else if (first >= 0x80) {
            throw std::invalid_argument("identifier is not valid UTF-8");
        }
        for (unsigned int j = 0; j < continuation; ++j) {
            if (i >= text.size()) throw std::invalid_argument("truncated UTF-8 identifier");
            const auto byte = static_cast<unsigned char>(text[i++]);
            if ((byte & 0xc0) != 0x80) throw std::invalid_argument("invalid UTF-8 identifier");
            codepoint = (codepoint << 6) | (byte & 0x3f);
        }
        if ((continuation && codepoint < minimum) || codepoint > 0x10ffff ||
            (codepoint >= 0xd800 && codepoint <= 0xdfff) || codepoint == 0)
            throw std::invalid_argument("invalid Unicode identifier");
        if (++characters > 256) throw std::invalid_argument("identifier exceeds 256 characters");
        if (codepoint == '"' || codepoint == '\\') {
            output += '\\'; output += static_cast<char>(codepoint);
        } else if (codepoint < 32 || codepoint >= 127) {
            if (codepoint <= 0xffff) escape16(codepoint);
            else {
                codepoint -= 0x10000;
                escape16(0xd800 + (codepoint >> 10));
                escape16(0xdc00 + (codepoint & 0x3ff));
            }
        } else output += static_cast<char>(codepoint);
    }
    if (characters == 0) throw std::invalid_argument("identifier must not be empty");
    output += '"';
    return output;
}

std::string real_number(double value) {
    if (!std::isfinite(value) || value <= 0) throw std::invalid_argument("gain must be positive and finite");
    std::ostringstream stream;
    stream.imbue(std::locale::classic());
    stream << std::setprecision(std::numeric_limits<double>::max_digits10) << value;
    return stream.str();
}

struct Tag {
    std::uint16_t id;
    std::uint16_t type;
    std::uint32_t count;
    Bytes value;
};

class OutputFile {
public:
    explicit OutputFile(const std::filesystem::path& path, bool checksum) : checksum_enabled_(checksum) {
#ifdef _WIN32
        const auto error = _wfopen_s(&file_, path.c_str(), L"wbx");
        if (error) throw std::system_error(static_cast<int>(error), std::generic_category(), "cannot create new DNG output");
#else
        file_ = std::fopen(path.c_str(), "wbx");
        if (!file_) throw std::system_error(errno, std::generic_category(), "cannot create new DNG output");
#endif
    }
    ~OutputFile() { if (file_) std::fclose(file_); }
    OutputFile(const OutputFile&) = delete;
    OutputFile& operator=(const OutputFile&) = delete;
    void write(const std::uint8_t* data, std::size_t size) {
        if (size && std::fwrite(data, 1, size, file_) != size)
            throw std::system_error(errno ? errno : EIO, std::generic_category(), "DNG write failed");
        if (checksum_enabled_)
            for (std::size_t i = 0; i < size; ++i) checksum_ = (checksum_ ^ data[i]) * 1099511628211ull;
    }
    DngOutputReceipt receipt() const {
        DngOutputReceipt result;
#ifdef _WIN32
        const auto handle = reinterpret_cast<HANDLE>(_get_osfhandle(_fileno(file_)));
        BY_HANDLE_FILE_INFORMATION info{};
        if (!GetFileInformationByHandle(handle, &info) ||
                (info.dwFileAttributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)))
            throw std::runtime_error("cannot identify created DNG file");
        result.device = info.dwVolumeSerialNumber;
        result.file_id = (static_cast<std::uint64_t>(info.nFileIndexHigh) << 32) | info.nFileIndexLow;
#else
        struct stat info{};
        if (::fstat(::fileno(file_), &info) != 0 || !S_ISREG(info.st_mode))
            throw std::runtime_error("cannot identify created DNG file");
        result.device = static_cast<std::uint64_t>(info.st_dev);
        result.file_id = static_cast<std::uint64_t>(info.st_ino);
#endif
        result.created = true;
        return result;
    }
    std::uint64_t checksum() const { return checksum_; }
    void close() {
        FILE* file = std::exchange(file_, nullptr);
        if (std::fclose(file) != 0)
            throw std::system_error(errno ? errno : EIO, std::generic_category(), "DNG close failed");
    }
private:
    FILE* file_ = nullptr;
    bool checksum_enabled_;
    std::uint64_t checksum_ = 14695981039346656037ull;
};

} // namespace

std::string capture_metadata_json(const Frame& frame, const DngIdentity& identity) {
    const auto& metadata = frame.metadata;
    const auto& request = metadata.requested;
    if (!request.exposure_us || !metadata.actual_exposure_us)
        throw std::invalid_argument("exposure must be a positive 32-bit integer");
    std::string output = "{\"actual_analogue_gain\":" + real_number(metadata.actual_gain) +
        ",\"actual_exposure_us\":" + std::to_string(metadata.actual_exposure_us) +
        ",\"calibration_id\":" + quoted_identifier(identity.calibration_id);
    if (!identity.capture_id.empty()) output += ",\"capture_id\":" + quoted_identifier(identity.capture_id);
    output += ",\"frame_id\":" + quoted_identifier(identity.frame_id.empty() ? request.request_id : identity.frame_id) +
        ",\"request_id\":" + quoted_identifier(request.request_id) +
        ",\"requested_analogue_gain\":" + real_number(request.gain) +
        ",\"requested_exposure_us\":" + std::to_string(request.exposure_us) +
        ",\"sensor_id\":" + quoted_identifier(identity.sensor_id) + "}";
    return output;
}

std::uint64_t write_dng(const std::filesystem::path& path, const Frame& frame,
                        const DngIdentity& identity, bool* created_output, DngOutputReceipt* receipt) {
    if (created_output) *created_output = false;
    if (receipt) *receipt = {};
    validate_frame(frame);
    if (path.empty()) throw std::invalid_argument("output path must not be empty");
    const auto& metadata = frame.metadata;
    const auto width = metadata.requested.width;
    const auto height = metadata.requested.height;
    if (metadata.bit_depth != 14 || metadata.bayer_pattern != "RGGB")
        throw std::invalid_argument("DNG writer requires 14-bit RGGB samples");
    const auto image_bytes = static_cast<std::uint64_t>(frame.pixels.size()) * 2;
    if (image_bytes > std::numeric_limits<std::uint32_t>::max())
        throw std::invalid_argument("image exceeds classic TIFF limits");
    for (const auto pixel : frame.pixels)
        if (pixel > 16383) throw std::invalid_argument("DNG samples exceed 14 bits");

    const auto description = ascii("{\"namespace\":\"org.hasslefree.hf-sim.capture.v1\",\"simulated\":true,\"capture\":" +
        capture_metadata_json(frame, identity) + ",\"native\":{\"source\":\"synthetic-native\",\"frame_sequence\":" +
        std::to_string(metadata.frame_id) + ",\"captured_at_unix_ns\":" +
        std::to_string(metadata.captured_at_unix_ns) + "}}");
    const auto make = ascii("HassleFree");
    const auto model = ascii("HF-Sim");
    Bytes matrix;
    for (unsigned int i = 0; i < 9; ++i) { put32(matrix, i % 4 == 0 ? 1 : 0); put32(matrix, 1); }
    const auto neutral = longs({1, 1, 1, 1, 1, 1});
    std::vector<Tag> tags = {
        {254, 4, 1, longs({0})}, {256, 4, 1, longs({width})}, {257, 4, 1, longs({height})},
        {258, 3, 1, shorts({16})}, {259, 3, 1, shorts({1})}, {262, 3, 1, shorts({32803})},
        {270, 2, static_cast<std::uint32_t>(description.size()), description},
        {271, 2, static_cast<std::uint32_t>(make.size()), make},
        {272, 2, static_cast<std::uint32_t>(model.size()), model},
        {273, 4, 1, longs({0})}, {274, 3, 1, shorts({1})}, {277, 3, 1, shorts({1})},
        {278, 4, 1, longs({height})}, {279, 4, 1, longs({static_cast<std::uint32_t>(image_bytes)})},
        {284, 3, 1, shorts({1})}, {33421, 3, 2, shorts({2, 2})}, {33422, 1, 4, {0, 1, 1, 2}},
        {34665, 4, 1, longs({0})}, {50706, 1, 4, {1, 4, 0, 0}}, {50707, 1, 4, {1, 1, 0, 0}},
        {50708, 2, static_cast<std::uint32_t>(model.size()), model}, {50710, 1, 3, {0, 1, 2}},
        {50711, 3, 1, shorts({1})}, {50713, 3, 2, shorts({1, 1})}, {50714, 4, 1, longs({64})},
        {50717, 4, 1, longs({16383})}, {50719, 4, 2, longs({0, 0})},
        {50720, 4, 2, longs({width, height})}, {50721, 10, 9, matrix}, {50728, 5, 3, neutral},
        {50778, 3, 1, shorts({21})}, {50829, 4, 4, longs({0, 0, height, width})}
    };
    std::sort(tags.begin(), tags.end(), [](const Tag& left, const Tag& right) { return left.id < right.id; });
    const auto extra_start = static_cast<std::uint32_t>(8 + 2 + tags.size() * 12 + 4);
    Bytes extra;
    std::vector<Bytes> fields;
    fields.reserve(tags.size());
    for (const auto& tag : tags) {
        if (tag.value.size() > 4) {
            if (extra.size() % 2) extra.push_back(0);
            fields.push_back(longs({extra_start + static_cast<std::uint32_t>(extra.size())}));
            extra.insert(extra.end(), tag.value.begin(), tag.value.end());
        } else {
            fields.push_back(tag.value);
            fields.back().resize(4, 0);
        }
    }
    if (extra.size() % 2) extra.push_back(0);
    const auto exif_offset = extra_start + static_cast<std::uint32_t>(extra.size());
    const auto common = std::gcd(metadata.actual_exposure_us, std::uint32_t{1000000});
    put16(extra, 1);
    put16(extra, 33434); put16(extra, 5); put32(extra, 1); put32(extra, exif_offset + 18);
    put32(extra, 0);
    put32(extra, metadata.actual_exposure_us / common); put32(extra, 1000000 / common);
    const auto image_offset = static_cast<std::uint64_t>(extra_start) + extra.size();
    const auto output_bytes = image_offset + image_bytes;
    if (output_bytes > std::numeric_limits<std::uint32_t>::max())
        throw std::invalid_argument("file exceeds classic TIFF limits");
    Bytes header = {'I', 'I'};
    put16(header, 42); put32(header, 8); put16(header, static_cast<std::uint16_t>(tags.size()));
    for (std::size_t i = 0; i < tags.size(); ++i) {
        if (tags[i].id == 273) fields[i] = longs({static_cast<std::uint32_t>(image_offset)});
        if (tags[i].id == 34665) fields[i] = longs({exif_offset});
        put16(header, tags[i].id); put16(header, tags[i].type); put32(header, tags[i].count);
        header.insert(header.end(), fields[i].begin(), fields[i].end());
    }
    put32(header, 0);
    OutputFile output(path, receipt != nullptr);
    if (created_output) *created_output = true;
    if (receipt) *receipt = output.receipt();
    output.write(header.data(), header.size());
    output.write(extra.data(), extra.size());
    std::array<std::uint8_t, 65536> buffer{};
    std::size_t buffered = 0;
    for (const auto pixel : frame.pixels) {
        buffer[buffered++] = static_cast<std::uint8_t>(pixel);
        buffer[buffered++] = static_cast<std::uint8_t>(pixel >> 8);
        if (buffered == buffer.size()) { output.write(buffer.data(), buffered); buffered = 0; }
    }
    output.write(buffer.data(), buffered);
    output.close();
    if (receipt) {
        receipt->bytes = output_bytes;
        receipt->checksum = output.checksum();
        receipt->complete = true;
    }
    if (std::filesystem::file_size(path) != output_bytes)
        throw std::runtime_error("DNG output size changed before completion");
    return output_bytes;
}

} // namespace hf
