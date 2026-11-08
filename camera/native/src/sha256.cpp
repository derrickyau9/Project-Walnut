#include "hf/sha256.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <stdexcept>

namespace hf {
namespace {
// SHA-256 definitions: FIPS 180-4 sections 4.1.2, 4.2.2, 5.1.1, 5.3.3 and 6.2.
// https://nvlpubs.nist.gov/nistpubs/fips/nist.fips.180-4.pdf
constexpr std::array<std::uint32_t, 64> kRoundConstants = {{
    0x428a2f98u, 0x71374491u, 0xb5c0fbcfu, 0xe9b5dba5u,
    0x3956c25bu, 0x59f111f1u, 0x923f82a4u, 0xab1c5ed5u,
    0xd807aa98u, 0x12835b01u, 0x243185beu, 0x550c7dc3u,
    0x72be5d74u, 0x80deb1feu, 0x9bdc06a7u, 0xc19bf174u,
    0xe49b69c1u, 0xefbe4786u, 0x0fc19dc6u, 0x240ca1ccu,
    0x2de92c6fu, 0x4a7484aau, 0x5cb0a9dcu, 0x76f988dau,
    0x983e5152u, 0xa831c66du, 0xb00327c8u, 0xbf597fc7u,
    0xc6e00bf3u, 0xd5a79147u, 0x06ca6351u, 0x14292967u,
    0x27b70a85u, 0x2e1b2138u, 0x4d2c6dfcu, 0x53380d13u,
    0x650a7354u, 0x766a0abbu, 0x81c2c92eu, 0x92722c85u,
    0xa2bfe8a1u, 0xa81a664bu, 0xc24b8b70u, 0xc76c51a3u,
    0xd192e819u, 0xd6990624u, 0xf40e3585u, 0x106aa070u,
    0x19a4c116u, 0x1e376c08u, 0x2748774cu, 0x34b0bcb5u,
    0x391c0cb3u, 0x4ed8aa4au, 0x5b9cca4fu, 0x682e6ff3u,
    0x748f82eeu, 0x78a5636fu, 0x84c87814u, 0x8cc70208u,
    0x90befffau, 0xa4506cebu, 0xbef9a3f7u, 0xc67178f2u,
}};

std::uint32_t rotate_right(std::uint32_t value, unsigned amount) noexcept {
    return (value >> amount) | (value << (32u - amount));
}

void compress(std::array<std::uint32_t, 8>& state, const unsigned char* block) noexcept {
    std::array<std::uint32_t, 64> schedule{};
    for (std::size_t i = 0; i < 16; ++i) {
        const auto offset = 4 * i;
        schedule[i] = (static_cast<std::uint32_t>(block[offset]) << 24u) |
                      (static_cast<std::uint32_t>(block[offset + 1]) << 16u) |
                      (static_cast<std::uint32_t>(block[offset + 2]) << 8u) |
                      static_cast<std::uint32_t>(block[offset + 3]);
    }
    for (std::size_t i = 16; i < schedule.size(); ++i) {
        const auto earlier = schedule[i - 15];
        const auto later = schedule[i - 2];
        const auto sigma0 = rotate_right(earlier, 7) ^ rotate_right(earlier, 18) ^ (earlier >> 3u);
        const auto sigma1 = rotate_right(later, 17) ^ rotate_right(later, 19) ^ (later >> 10u);
        schedule[i] = schedule[i - 16] + sigma0 + schedule[i - 7] + sigma1;
    }

    auto a = state[0]; auto b = state[1]; auto c = state[2]; auto d = state[3];
    auto e = state[4]; auto f = state[5]; auto g = state[6]; auto h = state[7];
    for (std::size_t i = 0; i < schedule.size(); ++i) {
        const auto sum1 = rotate_right(e, 6) ^ rotate_right(e, 11) ^ rotate_right(e, 25);
        const auto choose = (e & f) ^ (~e & g);
        const std::uint32_t first = h + sum1 + choose + kRoundConstants[i] + schedule[i];
        const auto sum0 = rotate_right(a, 2) ^ rotate_right(a, 13) ^ rotate_right(a, 22);
        const auto majority = (a & b) ^ (a & c) ^ (b & c);
        const std::uint32_t second = sum0 + majority;
        h = g; g = f; f = e; e = d + first;
        d = c; c = b; b = a; a = first + second;
    }
    state[0] += a; state[1] += b; state[2] += c; state[3] += d;
    state[4] += e; state[5] += f; state[6] += g; state[7] += h;
}
} // namespace

std::string sha256_hex(std::string_view bytes) {
    constexpr auto max_bytes = (std::numeric_limits<std::uint64_t>::max)() / 8u;
    if (bytes.size() > max_bytes) {
        throw std::length_error("SHA-256 input exceeds its 64-bit bit-length limit");
    }
    const auto bit_length = static_cast<std::uint64_t>(bytes.size()) * 8u;
    std::array<std::uint32_t, 8> state = {{
        0x6a09e667u, 0xbb67ae85u, 0x3c6ef372u, 0xa54ff53au,
        0x510e527fu, 0x9b05688cu, 0x1f83d9abu, 0x5be0cd19u,
    }};
    const auto* data = reinterpret_cast<const unsigned char*>(bytes.data());
    const auto block_count = bytes.size() / 64;
    for (std::size_t i = 0; i < block_count; ++i) compress(state, data + i * 64);

    // Only the tail and one or two padding blocks are buffered. In particular,
    // empty default-constructed string_views never dereference a null pointer.
    std::array<unsigned char, 128> tail{};
    const auto remaining = bytes.size() % 64;
    for (std::size_t i = 0; i < remaining; ++i) tail[i] = data[block_count * 64 + i];
    tail[remaining] = 0x80;
    const std::size_t padded_size = remaining < 56 ? 64 : 128;
    for (unsigned i = 0; i < 8; ++i) {
        tail[padded_size - 1 - i] = static_cast<unsigned char>(bit_length >> (8u * i));
    }
    compress(state, tail.data());
    if (padded_size == 128) compress(state, tail.data() + 64);

    constexpr char hex[] = "0123456789abcdef";
    std::string result(64, '0');
    for (std::size_t word = 0; word < state.size(); ++word) {
        for (unsigned digit = 0; digit < 8; ++digit) {
            result[word * 8 + digit] = hex[(state[word] >> (28u - 4u * digit)) & 0xfu];
        }
    }
    return result;
}

} // namespace hf
