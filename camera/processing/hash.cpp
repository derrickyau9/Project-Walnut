// Reuse the project's audited SHA-256 compression primitive, adding bounded
// streaming file input instead of materializing source photographs in RAM.
#include "../../camera/native/src/sha256.cpp"
#include "processing.hpp"
namespace daos {
std::string file_sha256(const fs::path& path) {
    std::ifstream file(path,std::ios::binary);
    if(!file) throw std::runtime_error("cannot hash source");
    std::array<std::uint32_t,8> state={{0x6a09e667u,0xbb67ae85u,0x3c6ef372u,0xa54ff53au,0x510e527fu,0x9b05688cu,0x1f83d9abu,0x5be0cd19u}};
    std::array<unsigned char,65536> buffer{}; std::uint64_t length=0;std::size_t remainder=0;
    while(file) {
        file.read(reinterpret_cast<char*>(buffer.data()),buffer.size());
        const auto count=static_cast<std::size_t>(file.gcount());length+=count;
        const auto blocks=count/64;
        for(std::size_t n=0;n<blocks;++n)hf::compress(state,buffer.data()+n*64);
        remainder=count%64;
        if(remainder) {std::copy_n(buffer.data()+blocks*64,remainder,buffer.data());break;}
    }
    if(file.bad())throw std::runtime_error("source hash read failed");
    std::array<unsigned char,128> tail{};std::copy_n(buffer.data(),remainder,tail.data());tail[remainder]=128;
    const std::size_t padded=remainder<56?64:128;
    for(unsigned n=0;n<8;++n)tail[padded-1-n]=static_cast<unsigned char>((length*8)>>(n*8));
    hf::compress(state,tail.data());if(padded==128)hf::compress(state,tail.data()+64);
    const char* hex="0123456789abcdef";std::string result(64,'0');
    for(std::size_t w=0;w<8;++w)for(unsigned d=0;d<8;++d)result[w*8+d]=hex[(state[w]>>(28-d*4))&15];
    return result;
}
}
