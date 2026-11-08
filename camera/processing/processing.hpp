#pragma once
#include <opencv2/core.hpp>
#include <nlohmann/json.hpp>
#include <filesystem>
#include <fstream>
#include <functional>
#include <memory>
#include <string>
#include <vector>
namespace daos {
namespace fs=std::filesystem;
using Json=nlohmann::json;
constexpr const char* VERSION="daos-imager-1.0/opencv-4.12.0";
constexpr std::uint64_t MEMORY_LIMIT=192ull*1024*1024;
struct Job {
    Json request; fs::path scratch,cancel; std::string id;
    void check() const;
    void progress(const std::string&,double,const Json& extra=Json::object()) const;
};
struct Raster {
    fs::path path; int width=0,height=0; Json provenance;
    cv::Mat read(int x,int y,int w,int h) const;
    cv::Mat proxy(int edge=1200) const;
};
std::string file_sha256(const fs::path&);
fs::path checked_path(const std::string&,bool existing);
FILE* open_file(const fs::path&,const char*);
Raster decode(const Json&,const fs::path&,Job&);
void write_rows(std::ofstream&,const cv::Mat&);
Raster transform(const Raster&,const Json&,Job&);
Raster process(const std::vector<Raster>&,const std::string&,const Json&,Job&);
Json analyze(const Raster&,const Json&,Job&);
Json encode(const Raster&,const fs::path&,const std::string&,const Json&,Job&);
Json run(const Json&);
}
