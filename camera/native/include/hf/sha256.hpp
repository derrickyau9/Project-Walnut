#pragma once

#include <string>
#include <string_view>

namespace hf {

// SHA-256 of the exact supplied bytes, including embedded NULs, returned as
// 64 lowercase hexadecimal digits. The input is not copied or modified.
// Uses constant working storage and throws std::length_error if the byte
// length cannot be represented by SHA-256's 64-bit bit-length field.
// This is a payload checksum, not an authentication mechanism or signature.
std::string sha256_hex(std::string_view bytes);

} // namespace hf
