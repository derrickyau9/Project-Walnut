#include "processing.hpp"
#include <opencv2/core.hpp>
#include <algorithm>
#include <chrono>
#include <iostream>
#include <set>
#include <stdexcept>
#include <regex>
#ifdef _WIN32
#include <windows.h>
#include <psapi.h>
#include <fcntl.h>
#include <io.h>
#else
#include <sys/resource.h>
#include <sys/stat.h>
#endif
namespace daos {
void Job::check() const {if(!cancel.empty()&&fs::exists(cancel))throw std::runtime_error("cancelled");static thread_local unsigned checks=0;if((++checks%64)==0&&fs::space(scratch).available<32ull*1024*1024)throw std::runtime_error("scratch free-space reserve reached");}
void Job::progress(const std::string& stage,double value,const Json& extra) const {Json event=extra;event["event"]="progress";event["job_id"]=id;event["stage"]=stage;event["progress"]=std::clamp(value,0.,1.);std::cout<<event.dump()<<std::endl;}
Json run(const Json& request){
 if(!request.is_object()||request.value("format","")!="daos.image-job.v1")throw std::runtime_error("unsupported image job protocol");
 std::string kind=request.at("kind"),identity=request.at("job_id");if(!std::regex_match(identity,std::regex("[a-zA-Z0-9_-]{1,64}")))throw std::runtime_error("invalid image job identity");
 const std::set<std::string> kinds={"develop_jpeg","develop_tiff16","analyze","hdr","exposure_fusion","focus_stack","denoise_stack","panorama","astro_stack","darkflat","negative"};if(!kinds.count(kind))throw std::runtime_error("unsupported image operation");
 Job job{request,fs::u8path(request.at("scratch").get<std::string>()),{},identity};if(!job.scratch.is_absolute()||!fs::is_directory(job.scratch)||fs::is_symlink(job.scratch))throw std::runtime_error("ordinary absolute scratch directory required");
 if(request.contains("cancel_path"))job.cancel=fs::u8path(request["cancel_path"].get<std::string>());
 const auto& inputs=request.at("inputs");if(!inputs.is_array()||inputs.empty()||inputs.size()>24)throw std::runtime_error("image job needs 1..24 sources");
 auto params=request.value("params",Json::object());std::uint64_t bytes=0;for(const auto& source:inputs){auto path=checked_path(source.at("path").get<std::string>(),true);bytes+=fs::file_size(path);}
 // Decoded RGB16 rasters, processed/output rasters and publication all need
 // space. Refuse before decoding rather than filling the writable data volume.
 auto space=fs::space(job.scratch);std::uint64_t reserve=request.value("space_reserve_bytes",64ull*1024*1024);if(space.available<bytes*5+reserve)throw std::runtime_error("insufficient scratch space budget");
 auto start=std::chrono::steady_clock::now();std::vector<Raster> images;std::vector<fs::path> cleanup;
 try{
  for(std::size_t i=0;i<inputs.size();++i){auto path=job.scratch/fs::u8path("source-"+std::to_string(i)+".rgb16");if(fs::exists(path))throw std::runtime_error("scratch raster collision");cleanup.push_back(path);auto source=inputs[i];if(params.contains("exposures_us")){if(!params["exposures_us"].is_array()||params["exposures_us"].size()!=inputs.size())throw std::runtime_error("exposure list does not match sources");source["exposure_us"]=params["exposures_us"][i];}images.push_back(decode(source,path,job));cleanup.push_back(images.back().path);job.progress("decode",.2*double(i+1)/inputs.size());}
  Json result={{"format","daos.image-result.v1"},{"job_id",identity},{"processor",VERSION},{"opencv",CV_VERSION},{"inputs",Json::array()},{"kind",kind},{"outputs",Json::array()},{"color_calibrated",false},{"memory_budget_bytes",MEMORY_LIMIT}};for(const auto& image:images)result["inputs"].push_back(image.provenance);
  auto raster=process(images,kind,params,job);cleanup.push_back(raster.path);auto final=transform(raster,params,job);cleanup.push_back(final.path);
  result["processing_provenance"]=raster.provenance;
  if(kind=="analyze"){result["analysis"]=analyze(final,params,job);result["outputs"]=result["analysis"]["outputs"];}
  else{std::string format=kind=="develop_tiff16"?"tiff16":params.value("output_format",std::string("jpeg"));auto destination=checked_path(request.at("destination").get<std::string>(),false);result["outputs"].push_back(encode(final,destination,format,params,job));}
  for(const auto& source:images)if(file_sha256(fs::u8path(source.provenance.at("path").get<std::string>()))!=source.provenance.at("sha256"))throw std::runtime_error("original bytes changed during processing");
  result["elapsed_ms"]=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count();
#ifdef _WIN32
  PROCESS_MEMORY_COUNTERS memory{};if(GetProcessMemoryInfo(GetCurrentProcess(),&memory,sizeof(memory)))result["peak_rss_bytes"]=std::uint64_t(memory.PeakWorkingSetSize);
#else
  rusage usage{};if(getrusage(RUSAGE_SELF,&usage)==0)result["peak_rss_bytes"]=std::uint64_t(usage.ru_maxrss)*1024;
#endif
  for(const auto& path:cleanup){std::error_code ec;fs::remove(path,ec);}job.progress("complete",1.);return result;
 }catch(...){for(const auto& path:cleanup){std::error_code ec;fs::remove(path,ec);}throw;}
}
}
int main(int argc,char** argv){
 try{
  if(argc==2&&std::string(argv[1])=="--capabilities"){
   std::cout<<daos::Json{{"format","daos.image-processing-capabilities.v1"},{"processor",daos::VERSION},{"opencv",CV_VERSION},{"memory_budget_bytes",daos::MEMORY_LIMIT},{"inputs",{"DNG-RGGB16","JPEG","PNG","TIFF-RGB8/16"}},{"outputs",{"JPEG","linear-TIFF16","PNG-preview"}},{"kinds",{"develop_jpeg","develop_tiff16","analyze","hdr","exposure_fusion","focus_stack","denoise_stack","panorama","astro_stack","darkflat","negative"}},{"max_inputs",24},{"color_calibrated",false}}.dump()<<std::endl;return 0;
  }
  cv::setNumThreads(1);cv::setUseOptimized(true);
#ifdef _WIN32
  _setmode(_fileno(stdin),_O_BINARY);_setmode(_fileno(stdout),_O_BINARY);
  HANDLE memory_job=CreateJobObjectW(nullptr,nullptr);JOBOBJECT_EXTENDED_LIMIT_INFORMATION limit{};limit.BasicLimitInformation.LimitFlags=JOB_OBJECT_LIMIT_PROCESS_MEMORY;limit.ProcessMemoryLimit=daos::MEMORY_LIMIT;if(memory_job&&SetInformationJobObject(memory_job,JobObjectExtendedLimitInformation,&limit,sizeof(limit)))AssignProcessToJobObject(memory_job,GetCurrentProcess());
#else
  rlimit limit{daos::MEMORY_LIMIT,daos::MEMORY_LIMIT};setrlimit(RLIMIT_AS,&limit);umask(0077);
#endif
  std::string line;line.reserve(65536);char character;while(std::cin.get(character)&&character!='\n'){if(line.size()>=1024*1024)throw std::runtime_error("job JSON exceeds bound");line+=character;}if(line.empty())throw std::runtime_error("one JSON request required on stdin");
  auto result=daos::run(daos::Json::parse(line));std::cout<<daos::Json{{"event","result"},{"result",result}}.dump()<<std::endl;return 0;
 }catch(const std::exception& error){std::cout<<daos::Json{{"event","error"},{"code",std::string(error.what())=="cancelled"?"cancelled":"processing_failed"},{"message",error.what()}}.dump()<<std::endl;return 2;}
}
