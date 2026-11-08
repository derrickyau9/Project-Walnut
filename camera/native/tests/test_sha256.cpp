#include "hf/sha256.hpp"

#include <array>
#include <cstddef>
#include <iostream>
#include <stdexcept>
#include <string>
#include <string_view>

namespace {
std::size_t checks = 0;

void expect(std::string_view input, std::string_view expected, const char* name) {
    ++checks;
    const auto actual = hf::sha256_hex(input);
    if (actual != expected) {
        throw std::runtime_error(std::string(name) + ": expected " + std::string(expected) + ", got " + actual);
    }
}

void standard_vectors() {
    expect({}, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", "empty null view");
    expect("abc", "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", "abc");
    expect("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq",
           "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1", "56-byte multiblock padding");
    expect("abcdefghbcdefghicdefghijdefghijkefghijklfghijklmghijklmnhijklmno"
           "ijklmnopjklmnopqklmnopqrlmnopqrsmnopqrstnopqrstu",
           "cf5b16a778af8380036ce59e7b0492370b249b11e8f07a51afac45037afee9d1", "112-byte reference");
    expect(std::string(1000000, 'a'),
           "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0", "million a");
}

void binary_and_view_vectors() {
    // Binary and boundary expected values were independently generated with
    // Python's hashlib.sha256; lengths are supplied explicitly, never strlen.
    expect(std::string_view("a\0b", 3),
           "59b271ae1bbcb1d31d41929817f4b16fb439eb4f31520b5ad1d5ce98920a7138", "embedded NUL");
    const std::array<unsigned char, 7> bytes = {{0, 1, 2, 0, 255, 128, 0}};
    expect(std::string_view(reinterpret_cast<const char*>(bytes.data()), bytes.size()),
           "faa242eb56b54b7896b0f272d7f5cd544cfd9e18ac9e48fff54743fcd9a0264b", "NUL and high-bit bytes");
    std::string all_bytes;
    for (unsigned value = 0; value < 256; ++value) all_bytes.push_back(static_cast<char>(value));
    expect(all_bytes, "40aff2e9d2d8922e47afd4648e6967497158785fbd1da870e7110266bf944880", "all byte values");
    const std::string storage = "prefixabcTRAILER";
    expect(std::string_view(storage.data() + 6, 3),
           "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", "bounded substring view");
    expect(std::string_view(storage.data() + storage.size(), 0),
           "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", "empty end view");
    // No mutable process-wide state may leak across separate digest calls.
    expect("abc", "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", "repeat after binary input");
}

void padding_boundary_vectors() {
    struct Vector { std::size_t length; const char* digest; };
    const std::array<Vector, 10> vectors = {{
        {55, "9f4390f8d30c2dd92ec9f095b65e2b9ae9b0a925a5258e241c9f1e910f734318"},
        {56, "b35439a4ac6f0948b6d6f9e3c6af0f5f590ce20f1bde7090ef7970686ec6738a"},
        {63, "7d3e74a05d7db15bce4ad9ec0658ea98e3f06eeecf16b4c6fff2da457ddc2f34"},
        {64, "ffe054fe7ae0cb6dc65c3af9b61d5209f439851db43d0ba5997337df154668eb"},
        {65, "635361c48bb9eab14198e76ea8ab7f1a41685d6ad62aa9146d301d4f17eb0ae0"},
        {119, "31eba51c313a5c08226adf18d4a359cfdfd8d2e816b13f4af952f7ea6584dcfb"},
        {120, "2f3d335432c70b580af0e8e1b3674a7c020d683aa5f73aaaedfdc55af904c21c"},
        {127, "c57e9278af78fa3cab38667bef4ce29d783787a2f731d4e12200270f0c32320a"},
        {128, "6836cf13bac400e9105071cd6af47084dfacad4e5e302c94bfed24e013afb73e"},
        {129, "c12cb024a2e5551cca0e08fce8f1c5e314555cc3fef6329ee994a3db752166ae"},
    }};
    for (const auto& vector : vectors) {
        const std::string name = "padding at byte length " + std::to_string(vector.length);
        expect(std::string(vector.length, 'a'), vector.digest, name.c_str());
    }
}
} // namespace

int main() {
    try {
        standard_vectors();
        binary_and_view_vectors();
        padding_boundary_vectors();
        std::cout << "PASS: " << checks << " SHA-256 reference vectors\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL after " << checks << " SHA-256 checks: " << error.what() << '\n';
        return 1;
    }
}
