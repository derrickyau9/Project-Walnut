#include "processing.hpp"
#include <opencv2/imgproc.hpp>
#include <opencv2/imgcodecs.hpp>
#include <jpeglib.h>
#include <png.h>
#include <tiffio.h>
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <setjmp.h>
#include <stdexcept>
#ifdef _WIN32
#include <windows.h>
#include <io.h>
#else
#include <unistd.h>
#include <fcntl.h>
#endif
namespace daos {
namespace {
constexpr std::uint64_t MAX_PIXELS=80ull*1000*1000;
float linear(float v) {return v<=.04045f?v/12.92f:std::pow((v+.055f)/1.055f,2.4f);}
float srgb(float v) {v=std::clamp(v,0.f,1.f);return v<=.0031308f?v*12.92f:1.055f*std::pow(v,1.f/2.4f)-.055f;}
void dimensions(int w,int h) {if(w<1||h<1||std::uint64_t(w)*h>MAX_PIXELS||w>40000||h>40000)throw std::runtime_error("image dimensions exceed bound");}
struct JpegError {jpeg_error_mgr manager;jmp_buf jump;char message[JMSG_LENGTH_MAX];};
void jpeg_error(j_common_ptr c) {auto* e=reinterpret_cast<JpegError*>(c->err);(*c->err->format_message)(c,e->message);longjmp(e->jump,1);}
struct Exif {int orientation=1;double exposure_us=0;};
Exif jpeg_exif(jpeg_saved_marker_ptr marker){Exif result;for(auto* m=marker;m;m=m->next){if(m->marker!=JPEG_APP0+1||m->data_length<14||std::memcmp(m->data,"Exif\0\0",6))continue;auto* d=m->data+6;std::size_t size=m->data_length-6;bool little=d[0]=='I';
 auto u16=[&](std::size_t n){if(n+2>size)throw std::runtime_error("invalid EXIF offset");return little?unsigned(d[n])|(unsigned(d[n+1])<<8):unsigned(d[n+1])|(unsigned(d[n])<<8);};
 auto u32=[&](std::size_t n){if(n+4>size)throw std::runtime_error("invalid EXIF offset");return little?std::uint32_t(d[n])|(std::uint32_t(d[n+1])<<8)|(std::uint32_t(d[n+2])<<16)|(std::uint32_t(d[n+3])<<24):std::uint32_t(d[n+3])|(std::uint32_t(d[n+2])<<8)|(std::uint32_t(d[n+1])<<16)|(std::uint32_t(d[n])<<24);};
 try{std::size_t exif=0,ifd=u32(4);unsigned count=u16(ifd);if(count>512)throw std::runtime_error("EXIF tags exceed bound");for(unsigned n=0;n<count;++n){std::size_t p=ifd+2+n*12;auto tag=u16(p);if(tag==274)result.orientation=int(u16(p+8));if(tag==34665)exif=u32(p+8);}if(exif){unsigned n=u16(exif);if(n>512)throw std::runtime_error("EXIF tags exceed bound");for(unsigned i=0;i<n;++i){std::size_t p=exif+2+i*12;if(u16(p)==33434){auto at=u32(p+8),denominator=u32(at+4);if(denominator)result.exposure_us=double(u32(at))/denominator*1000000;}}}}catch(...){throw std::runtime_error("malformed JPEG EXIF");}break;}return result;}
struct TiffClose {void operator()(TIFF* t) const {if(t)TIFFClose(t);}};
using Tiff=std::unique_ptr<TIFF,TiffClose>;
std::vector<unsigned char> tiff_tag(const fs::path& path,int wanted) {
 std::ifstream input(path,std::ios::binary);unsigned char header[8]{};input.read(reinterpret_cast<char*>(header),8);bool little=header[0]=='I';
 auto u16=[&](const unsigned char* p){return little?unsigned(p[0])|(unsigned(p[1])<<8):unsigned(p[1])|(unsigned(p[0])<<8);};
 auto u32=[&](const unsigned char* p){return little?std::uint32_t(p[0])|(std::uint32_t(p[1])<<8)|(std::uint32_t(p[2])<<16)|(std::uint32_t(p[3])<<24):std::uint32_t(p[3])|(std::uint32_t(p[2])<<8)|(std::uint32_t(p[1])<<16)|(std::uint32_t(p[0])<<24);};
 input.seekg(u32(header+4));unsigned char count_data[2]{};input.read(reinterpret_cast<char*>(count_data),2);unsigned count=u16(count_data);if(count>1024)throw std::runtime_error("TIFF directory exceeds bound");
 for(unsigned n=0;n<count;++n){unsigned char entry[12]{};input.read(reinterpret_cast<char*>(entry),12);if(!input)throw std::runtime_error("invalid TIFF directory");if(int(u16(entry))!=wanted)continue;
  unsigned type=u16(entry+2),size=(type==1||type==2||type==7)?1:(type==3?2:4);std::uint64_t length=std::uint64_t(u32(entry+4))*size;if(length>65536)throw std::runtime_error("TIFF metadata exceeds bound");std::vector<unsigned char> data(static_cast<std::size_t>(length));if(length<=4)std::copy_n(entry+8,data.size(),data.data());else{input.seekg(u32(entry+8));input.read(reinterpret_cast<char*>(data.data()),data.size());if(!input)throw std::runtime_error("TIFF metadata truncated");}return data;
 }return {};
}
float tiff_number(const fs::path& path,int tag,float fallback) {
 auto data=tiff_tag(path,tag);if(data.empty())return fallback;std::ifstream in(path,std::ios::binary);char c=0;in.get(c);bool little=c=='I';
 auto integer=[&](const unsigned char* p,std::size_t size){std::uint32_t value=0;for(std::size_t n=0;n<size;++n)value|=std::uint32_t(p[n])<<(8*(little?n:size-1-n));return value;};
 if(data.size()==2||data.size()==4)return float(integer(data.data(),data.size()));
 if(data.size()>=8){auto denominator=integer(data.data()+4,4);if(denominator)return float(integer(data.data(),4))/denominator;}
 return fallback;
}
Raster reorient(const Raster& input,int orientation,Job& job){
 if(orientation==1)return input;if(orientation<1||orientation>8)throw std::runtime_error("invalid source orientation");
 Raster output{input.path.parent_path()/fs::u8path(input.path.stem().u8string()+"-oriented.rgb16"),orientation>=5?input.height:input.width,orientation>=5?input.width:input.height,input.provenance};
 if(fs::space(job.scratch).available<std::uint64_t(output.width)*output.height*6+64ull*1024*1024)throw std::runtime_error("insufficient orientation workspace");
 std::ofstream file(output.path,std::ios::binary);for(int y=0;y<output.height;y+=16){job.check();int h=std::min(16,output.height-y);cv::Mat patch;
  if(orientation<=4){patch=input.read(0,orientation>=3?input.height-y-h:y,input.width,h);if(orientation==2)cv::flip(patch,patch,1);else if(orientation==3)cv::rotate(patch,patch,cv::ROTATE_180);else cv::flip(patch,patch,0);}
  else {patch=input.read(orientation>=7?input.width-y-h:y,0,h,input.height);if(orientation==5)cv::transpose(patch,patch);else if(orientation==6)cv::rotate(patch,patch,cv::ROTATE_90_CLOCKWISE);else if(orientation==7){cv::transpose(patch,patch);cv::flip(patch,patch,-1);}else cv::rotate(patch,patch,cv::ROTATE_90_COUNTERCLOCKWISE);}write_rows(file,patch);
 }file.close();return output;
}
TIFF* tiff_open(const fs::path& p,const char* mode) {
#ifdef _WIN32
 return TIFFOpenW(p.c_str(),mode);
#else
 return TIFFOpen(p.c_str(),mode);
#endif
}
void save_float_row(std::ofstream& out,const std::vector<std::uint16_t>& row) {out.write(reinterpret_cast<const char*>(row.data()),row.size()*2);if(!out)throw std::runtime_error("scratch write failed");}
void publish(const fs::path& from,const fs::path& to) {
#ifdef _WIN32
 if(!MoveFileExW(from.c_str(),to.c_str(),MOVEFILE_WRITE_THROUGH))throw std::runtime_error("destination exists or cannot publish output");
#else
 if(link(from.c_str(),to.c_str())!=0)throw std::runtime_error("destination exists or cannot publish output");
 unlink(from.c_str());
 int fd=open(to.parent_path().c_str(),O_RDONLY|O_DIRECTORY);if(fd>=0){fsync(fd);close(fd);}
#endif
}
}
FILE* open_file(const fs::path& path,const char* mode) {
#ifdef _WIN32
 std::wstring m;while(*mode)m+=static_cast<wchar_t>(*mode++);return _wfopen(path.c_str(),m.c_str());
#else
 return std::fopen(path.c_str(),mode);
#endif
}
fs::path checked_path(const std::string& value,bool existing) {
 auto path=fs::u8path(value);if(!path.is_absolute())throw std::runtime_error("absolute local path required");
 for(auto current=path;!current.empty();current=current.parent_path()) {
  if(fs::is_symlink(fs::symlink_status(current)))throw std::runtime_error("symbolic link refused");
  if(current==current.root_path())break;
 }
 if(existing&&!fs::is_regular_file(path))throw std::runtime_error("ordinary input file required");
 if(!existing&&(fs::exists(path)||!fs::is_directory(path.parent_path())))throw std::runtime_error("new output filename in existing directory required");
 return path;
}
cv::Mat Raster::read(int x,int y,int w,int h) const {
 if(x<0||y<0||w<1||h<1||x+w>width||y+h>height||std::uint64_t(w)*h>8ull*1000*1000)throw std::runtime_error("tile exceeds bound");
 std::ifstream in(path,std::ios::binary);if(!in)throw std::runtime_error("scratch tile unavailable");
 cv::Mat result(h,w,CV_32FC3);std::vector<std::uint16_t> row(w*3);
 for(int j=0;j<h;++j) {
  in.seekg((std::uint64_t(y+j)*width+x)*6);in.read(reinterpret_cast<char*>(row.data()),row.size()*2);
  if(!in)throw std::runtime_error("truncated scratch raster");
  auto* dst=result.ptr<cv::Vec3f>(j);for(int i=0;i<w;++i)dst[i]=cv::Vec3f(row[i*3],row[i*3+1],row[i*3+2])*(1.f/65535);
 }
 return result;
}
cv::Mat Raster::proxy(int edge) const {
 const double ratio=std::min(1.,double(edge)/std::max(width,height));int w=std::max(1,int(width*ratio)),h=std::max(1,int(height*ratio));
 cv::Mat result(h,w,CV_32FC3);std::ifstream in(path,std::ios::binary);std::vector<std::uint16_t> row(width*3);
 for(int y=0;y<h;++y) {
  in.seekg(std::uint64_t(int(y/ratio))*width*6);in.read(reinterpret_cast<char*>(row.data()),row.size()*2);if(!in)throw std::runtime_error("proxy source truncated");
  auto* dst=result.ptr<cv::Vec3f>(y);for(int x=0;x<w;++x){int sx=std::min(width-1,int(x/ratio))*3;dst[x]=cv::Vec3f(row[sx],row[sx+1],row[sx+2])*(1.f/65535);}
 }
 return result;
}
void write_rows(std::ofstream& output,const cv::Mat& tile) {
 std::vector<std::uint16_t> row(tile.cols*3);
 for(int y=0;y<tile.rows;++y){auto* p=tile.ptr<cv::Vec3f>(y);for(int x=0;x<tile.cols;++x)for(int c=0;c<3;++c)row[x*3+c]=std::uint16_t(std::lround(std::clamp(p[x][c],0.f,1.f)*65535));save_float_row(output,row);}
}
Raster decode(const Json& source,const fs::path& output,Job& job) {
 fs::path path=checked_path(source.at("path").get<std::string>(),true);job.check();
 const auto digest=file_sha256(path);if(source.contains("sha256")&&source["sha256"]!=digest)throw std::runtime_error("input SHA-256 mismatch");
 std::ifstream magic(path,std::ios::binary);unsigned char header[8]{};magic.read(reinterpret_cast<char*>(header),8);magic.close();
 Raster raster{output,0,0,source};raster.provenance["sha256"]=digest;
 int source_orientation=1;
 const auto options=job.request.value("params",Json::object());int early_edge=options.value("max_edge",0);
 if(early_edge<0||early_edge>1600||options.contains("region")||job.request["inputs"].size()!=1||job.request.value("kind","")=="analyze")early_edge=0;
 std::ofstream out(output,std::ios::binary|std::ios::trunc);if(!out)throw std::runtime_error("cannot create scratch raster");
 if(header[0]==255&&header[1]==216) {
  FILE* input=open_file(path,"rb");if(!input)throw std::runtime_error("JPEG input unavailable");
  jpeg_decompress_struct decoder{};JpegError error{};decoder.err=jpeg_std_error(&error.manager);error.manager.error_exit=jpeg_error;
  if(setjmp(error.jump)){jpeg_destroy_decompress(&decoder);fclose(input);throw std::runtime_error(error.message);}
  jpeg_create_decompress(&decoder);jpeg_stdio_src(&decoder,input);jpeg_save_markers(&decoder,JPEG_APP0+1,0xffff);jpeg_read_header(&decoder,TRUE);auto exif=jpeg_exif(decoder.marker_list);source_orientation=exif.orientation;if(exif.exposure_us>0&&!raster.provenance.contains("exposure_us"))raster.provenance["exposure_us"]=exif.exposure_us;bool cmyk=decoder.jpeg_color_space==JCS_CMYK||decoder.jpeg_color_space==JCS_YCCK;decoder.out_color_space=cmyk?JCS_CMYK:JCS_RGB;
  raster.provenance["original_width"]=decoder.image_width;raster.provenance["original_height"]=decoder.image_height;
  if(early_edge){int maxdim=std::max(decoder.image_width,decoder.image_height);for(int denominator:{8,4,2})if(maxdim/denominator>=early_edge){decoder.scale_num=1;decoder.scale_denom=denominator;break;}}
  jpeg_start_decompress(&decoder);
  raster.width=int(decoder.output_width);raster.height=int(decoder.output_height);dimensions(raster.width,raster.height);
  std::vector<unsigned char> bytes(raster.width*(cmyk?4:3));std::vector<std::uint16_t> row(raster.width*3);
  while(decoder.output_scanline<decoder.output_height){job.check();JSAMPROW p=bytes.data();jpeg_read_scanlines(&decoder,&p,1);for(int x=0;x<raster.width;++x)for(int c=0;c<3;++c){float value;if(cmyk){float ink=bytes[x*4+c]/255.f,black=bytes[x*4+3]/255.f;value=decoder.saw_Adobe_marker?ink*black:(1-ink)*(1-black);}else value=bytes[x*3+c]/255.f;row[x*3+c]=std::uint16_t(std::lround(linear(value)*65535));}save_float_row(out,row);}
  jpeg_finish_decompress(&decoder);jpeg_destroy_decompress(&decoder);fclose(input);raster.provenance["decoded_as"]="jpeg/sRGB-assumed";
 } else if(png_sig_cmp(header,0,8)==0) {
  FILE* input=open_file(path,"rb");if(!input)throw std::runtime_error("PNG input unavailable");
  png_structp png=png_create_read_struct(PNG_LIBPNG_VER_STRING,nullptr,nullptr,nullptr);png_infop info=png_create_info_struct(png);
  if(setjmp(png_jmpbuf(png))){png_destroy_read_struct(&png,&info,nullptr);fclose(input);throw std::runtime_error("PNG decode failed");}
  png_init_io(png,input);png_read_info(png,info);raster.width=int(png_get_image_width(png,info));raster.height=int(png_get_image_height(png,info));dimensions(raster.width,raster.height);
  int depth=png_get_bit_depth(png,info),color=png_get_color_type(png,info);
  if(png_get_interlace_type(png,info)!=PNG_INTERLACE_NONE)throw std::runtime_error("interlaced PNG needs bounded preconversion");
  if(color==PNG_COLOR_TYPE_PALETTE)png_set_palette_to_rgb(png);
  if(color==PNG_COLOR_TYPE_GRAY&&depth<8)png_set_expand_gray_1_2_4_to_8(png);
  if(png_get_valid(png,info,PNG_INFO_tRNS))png_set_tRNS_to_alpha(png);
  if(color==PNG_COLOR_TYPE_GRAY||color==PNG_COLOR_TYPE_GRAY_ALPHA)png_set_gray_to_rgb(png);
  if(color&PNG_COLOR_MASK_ALPHA||png_get_valid(png,info,PNG_INFO_tRNS))png_set_strip_alpha(png);
  png_read_update_info(png,info);depth=png_get_bit_depth(png,info);
  std::vector<unsigned char> bytes(png_get_rowbytes(png,info));std::vector<std::uint16_t> row(raster.width*3);
  for(int y=0;y<raster.height;++y){job.check();png_read_row(png,bytes.data(),nullptr);for(std::size_t i=0;i<row.size();++i){float value=depth==16?float((unsigned(bytes[i*2])<<8)|bytes[i*2+1])/65535:bytes[i]/255.f;row[i]=std::uint16_t(std::lround(linear(value)*65535));}save_float_row(out,row);}
  png_read_end(png,info);png_destroy_read_struct(&png,&info,nullptr);fclose(input);raster.provenance["decoded_as"]="png/sRGB-assumed/alpha-discarded";
 } else if((header[0]=='I'&&header[1]=='I')||(header[0]=='M'&&header[1]=='M')) {
  Tiff tiff(tiff_open(path,"r"));if(!tiff)throw std::runtime_error("TIFF input unavailable");
  std::uint32_t width=0,height=0;std::uint16_t bits=0,samples=0,photo=0,planar=0,orientation=1,sampleformat=1;
  TIFFGetField(tiff.get(),TIFFTAG_IMAGEWIDTH,&width);TIFFGetField(tiff.get(),TIFFTAG_IMAGELENGTH,&height);TIFFGetFieldDefaulted(tiff.get(),TIFFTAG_BITSPERSAMPLE,&bits);TIFFGetFieldDefaulted(tiff.get(),TIFFTAG_SAMPLESPERPIXEL,&samples);TIFFGetFieldDefaulted(tiff.get(),TIFFTAG_PHOTOMETRIC,&photo);TIFFGetFieldDefaulted(tiff.get(),TIFFTAG_PLANARCONFIG,&planar);TIFFGetFieldDefaulted(tiff.get(),TIFFTAG_ORIENTATION,&orientation);TIFFGetFieldDefaulted(tiff.get(),TIFFTAG_SAMPLEFORMAT,&sampleformat);
  raster.width=int(width);raster.height=int(height);dimensions(raster.width,raster.height);
  if((bits!=8&&bits!=16)||planar!=PLANARCONFIG_CONTIG||sampleformat!=SAMPLEFORMAT_UINT||TIFFIsTiled(tiff.get()))throw std::runtime_error("TIFF needs contiguous 8/16-bit integer scanlines");
  const bool cfa=photo==32803;char* description=nullptr;if(TIFFGetField(tiff.get(),TIFFTAG_IMAGEDESCRIPTION,&description)&&description&&std::strlen(description)<65536){try{raster.provenance["embedded_metadata"]=Json::parse(description);}catch(...){}}
  if(cfa&&(samples!=1||bits!=16))throw std::runtime_error("DNG requires 16-bit RGGB Bayer container");
  if(cfa&&tiff_tag(path,33422)!=std::vector<unsigned char>{0,1,1,2})throw std::runtime_error("DNG requires an explicit RGGB CFA pattern");
  if(!cfa&&(photo!=PHOTOMETRIC_RGB&&photo!=PHOTOMETRIC_MINISBLACK))throw std::runtime_error("unsupported TIFF photometric layout");
  if(!cfa&&samples!=1&&samples!=3&&samples!=4)throw std::runtime_error("unsupported TIFF channel count");
  source_orientation=orientation;
  float black=cfa?source.value("black_level",tiff_number(path,50714,64.f)):0,white=cfa?source.value("white_level",tiff_number(path,50717,16383.f)):65535;if(!(white>black))throw std::runtime_error("invalid Bayer black/white levels");
  std::vector<unsigned char> bytes(static_cast<std::size_t>(TIFFScanlineSize64(tiff.get())));
  if(bytes.size()>1024*1024)throw std::runtime_error("TIFF scanline exceeds bound");
  if(cfa) {
   int source_width=raster.width,source_height=raster.height;double ratio=early_edge?std::min(1.,double(early_edge)/std::max(source_width,source_height)):1.;raster.width=std::max(1,int(source_width*ratio));raster.height=std::max(1,int(source_height*ratio));raster.provenance["original_width"]=source_width;raster.provenance["original_height"]=source_height;
   if(raster.provenance.contains("embedded_metadata")){auto metadata=raster.provenance["embedded_metadata"].value("capture",Json::object());if(!raster.provenance.contains("exposure_us")&&metadata.contains("actual_exposure_us"))raster.provenance["exposure_us"]=metadata["actual_exposure_us"];}
   std::array<std::vector<std::uint16_t>,3> rows;for(auto& r:rows)r.resize(width);
   auto read=[&](int y,std::vector<std::uint16_t>& dst){if(TIFFReadScanline(tiff.get(),bytes.data(),std::uint32_t(std::clamp(y,0,source_height-1)),0)<0)throw std::runtime_error("DNG row decode failed");std::memcpy(dst.data(),bytes.data(),width*2);};
   std::vector<std::uint16_t> rgb(raster.width*3);
   for(int y=0;y<raster.height;++y){job.check();int center_y=std::min(source_height-1,int(y/ratio));read(center_y-1,rows[0]);read(center_y,rows[1]);read(center_y+1,rows[2]);
    for(int x=0;x<raster.width;++x)for(int c=0;c<3;++c){int center_x=std::min(source_width-1,int(x/ratio)),known=center_y%2==0?(center_x%2==0?0:1):(center_x%2==0?1:2);float sum=0;int count=0;if(c==known){sum=rows[1][center_x];count=1;}else for(int dy=-1;dy<=1;++dy)for(int dx=-1;dx<=1;++dx){int sx=std::clamp(center_x+dx,0,source_width-1),sy=std::clamp(center_y+dy,0,source_height-1);int channel=(sy%2==0?(sx%2==0?0:1):(sx%2==0?1:2));if(channel==c){sum+=rows[dy+1][sx];++count;}}float v=(sum/std::max(1,count)-black)/(white-black);rgb[x*3+c]=std::uint16_t(std::lround(std::clamp(v,0.f,1.f)*65535));}save_float_row(out,rgb);
   }
   raster.provenance["decoded_as"]="dng/linear-RGGB-bilinear";raster.provenance["black_level"]=black;raster.provenance["white_level"]=white;
  } else {
   bool own_linear=false;if(raster.provenance.contains("embedded_metadata")){auto m=raster.provenance["embedded_metadata"];own_linear=m.value("format","")=="daos.image-output.v1"&&m.value("params",Json::object()).value("linear_tiff",true);}
   std::vector<std::uint16_t> rgb(width*3);bool already_linear=source.value("linear",own_linear);
   for(int y=0;y<raster.height;++y){job.check();if(TIFFReadScanline(tiff.get(),bytes.data(),y,0)<0)throw std::runtime_error("TIFF row decode failed");for(int x=0;x<raster.width;++x)for(int c=0;c<3;++c){int at=x*samples+(samples==1?0:c);float v=bits==16?reinterpret_cast<std::uint16_t*>(bytes.data())[at]/65535.f:bytes[at]/255.f;rgb[x*3+c]=std::uint16_t(std::lround((already_linear?v:linear(v))*65535));}save_float_row(out,rgb);}
   raster.provenance["decoded_as"]=already_linear?"tiff/linear":"tiff/sRGB-assumed";
  }
 } else throw std::runtime_error("input format is not DNG/JPEG/PNG/TIFF");
 raster.provenance["render_sampling"]=early_edge?"explicit-derivative":"full-resolution";
 out.flush();if(!out)throw std::runtime_error("scratch raster flush failed");out.close();
 raster.provenance["source_orientation"]=source_orientation;
 if(file_sha256(path)!=digest)throw std::runtime_error("source changed during decode");return reorient(raster,source_orientation,job);
}
Raster transform(const Raster& source,const Json& options,Job& job) {
 int x=0,y=0,w=source.width,h=source.height;
 if(options.contains("region")){auto r=options["region"].get<std::vector<int>>();if(r.size()!=4)throw std::runtime_error("region needs x,y,w,h");x=r[0];y=r[1];w=r[2];h=r[3];}
 else if(options.contains("crop")&&!options["crop"].is_null()){auto r=options["crop"].get<std::vector<double>>();if(r.size()!=4)throw std::runtime_error("crop needs x,y,w,h");x=int(r[0]*w);y=int(r[1]*h);w=int(r[2]*source.width);h=int(r[3]*source.height);}
 if(x<0||y<0||w<1||h<1||x+w>source.width||y+h>source.height)throw std::runtime_error("crop outside source");
 int rotation=options.value("rotation",0);if(rotation!=0&&rotation!=90&&rotation!=180&&rotation!=270)throw std::runtime_error("rotation must be 0/90/180/270");
 int edge=options.value("max_edge",0);if(edge<0||edge>40000)throw std::runtime_error("invalid max_edge");
 double scale=edge?std::min(1.,double(edge)/std::max(w,h)):1.;int cw=std::max(1,int(w*scale)),ch=std::max(1,int(h*scale));
 Raster result{job.scratch/"transformed.rgb16",rotation%180?ch:cw,rotation%180?cw:ch,source.provenance};
 if(x==0&&y==0&&w==source.width&&h==source.height&&rotation==0&&scale==1)return source;
 std::ofstream out(result.path,std::ios::binary);std::vector<std::uint16_t> row(result.width*3);
 // Read at most 32 source rows/columns per output strip. Never load a full RAW.
 for(int oy=0;oy<result.height;oy+=16){job.check();int count=std::min(16,result.height-oy);cv::Mat tile;
  if(scale==1) {
   if(rotation==0)tile=source.read(x,y+oy,w,count);
   else if(rotation==180){tile=source.read(x,y+h-oy-count,w,count);cv::rotate(tile,tile,cv::ROTATE_180);}
   else if(rotation==90){tile=source.read(x+oy,y,count,h);cv::rotate(tile,tile,cv::ROTATE_90_CLOCKWISE);}
   else {tile=source.read(x+w-oy-count,y,count,h);cv::rotate(tile,tile,cv::ROTATE_90_COUNTERCLOCKWISE);}
  } else {
   // Decimation is only requested for explicit thumbnails/fit derivations.
   cv::Mat proxy=source.proxy(std::max(source.width,source.height)*scale+1);
   cv::Rect roi(std::min(proxy.cols-1,int(x*scale)),std::min(proxy.rows-1,int(y*scale)),std::min(cw,proxy.cols-int(x*scale)),std::min(ch,proxy.rows-int(y*scale)));
   cv::Mat reduced=proxy(roi);if(rotation==90)cv::rotate(reduced,reduced,cv::ROTATE_90_CLOCKWISE);else if(rotation==180)cv::rotate(reduced,reduced,cv::ROTATE_180);else if(rotation==270)cv::rotate(reduced,reduced,cv::ROTATE_90_COUNTERCLOCKWISE);
   cv::resize(reduced,reduced,{result.width,result.height},0,0,cv::INTER_AREA);tile=reduced.rowRange(oy,oy+count);
  }write_rows(out,tile);
 }
 out.close();return result;
}
Json encode(const Raster& raster,const fs::path& destination,const std::string& format,const Json& options,Job& job) {
 checked_path(destination.u8string(),false);fs::path temporary=destination.parent_path()/fs::u8path(".daos-imager-"+job.id+".partial");if(fs::exists(temporary))throw std::runtime_error("temporary output collision");
 const auto metadata=Json{{"format","daos.image-output.v1"},{"processor",VERSION},{"opencv",CV_VERSION},{"job_id",job.id},{"inputs",job.request.at("inputs")},{"kind",job.request.value("kind","develop_jpeg")},{"params",options},{"linear_source",true},{"color_calibrated",false},{"width",raster.width},{"height",raster.height}}.dump();
 const auto estimate=std::uint64_t(raster.width)*raster.height*(format=="tiff16"||format=="tiff"?6:3)+1024*1024;
 if(fs::space(destination.parent_path()).available<estimate+64ull*1024*1024)throw std::runtime_error("insufficient output volume space budget");
 try {
  if(format=="jpeg"||format=="jpg"){
   FILE* output=open_file(temporary,"wbx");if(!output)throw std::runtime_error("exclusive JPEG output unavailable");jpeg_compress_struct encoder{};JpegError error{};encoder.err=jpeg_std_error(&error.manager);error.manager.error_exit=jpeg_error;
   if(setjmp(error.jump)){jpeg_destroy_compress(&encoder);fclose(output);throw std::runtime_error(error.message);}
   jpeg_create_compress(&encoder);jpeg_stdio_dest(&encoder,output);encoder.image_width=raster.width;encoder.image_height=raster.height;encoder.input_components=3;encoder.in_color_space=JCS_RGB;jpeg_set_defaults(&encoder);jpeg_set_quality(&encoder,std::clamp(options.value("quality",95),1,100),TRUE);jpeg_start_compress(&encoder,TRUE);
   for(std::size_t at=0;at<metadata.size();at+=60000){std::string part=metadata.substr(at,60000);jpeg_write_marker(&encoder,JPEG_COM,reinterpret_cast<const JOCTET*>(part.data()),unsigned(part.size()));}
   std::vector<unsigned char> row(raster.width*3);for(int y=0;y<raster.height;y+=16){job.check();auto tile=raster.read(0,y,raster.width,std::min(16,raster.height-y));for(int j=0;j<tile.rows;++j){auto* p=tile.ptr<cv::Vec3f>(j);for(int x=0;x<raster.width;++x)for(int c=0;c<3;++c)row[x*3+c]=std::uint8_t(std::lround(srgb(p[x][c])*255));JSAMPROW r=row.data();jpeg_write_scanlines(&encoder,&r,1);}if(y%128==0)job.progress("encode",.8+.19*double(y)/raster.height);}
   jpeg_finish_compress(&encoder);jpeg_destroy_compress(&encoder);fflush(output);
#ifdef _WIN32
   _commit(_fileno(output));
#else
   fsync(fileno(output));
#endif
   fclose(output);
  } else if(format=="tiff16"||format=="tiff"){
   {FILE* reserve=open_file(temporary,"wbx");if(!reserve)throw std::runtime_error("exclusive TIFF output unavailable");fclose(reserve);}
   Tiff tiff(tiff_open(temporary,"w"));if(!tiff)throw std::runtime_error("TIFF writer unavailable");TIFFSetField(tiff.get(),TIFFTAG_IMAGEWIDTH,raster.width);TIFFSetField(tiff.get(),TIFFTAG_IMAGELENGTH,raster.height);TIFFSetField(tiff.get(),TIFFTAG_SAMPLESPERPIXEL,3);TIFFSetField(tiff.get(),TIFFTAG_BITSPERSAMPLE,16);TIFFSetField(tiff.get(),TIFFTAG_SAMPLEFORMAT,SAMPLEFORMAT_UINT);TIFFSetField(tiff.get(),TIFFTAG_PHOTOMETRIC,PHOTOMETRIC_RGB);TIFFSetField(tiff.get(),TIFFTAG_PLANARCONFIG,PLANARCONFIG_CONTIG);TIFFSetField(tiff.get(),TIFFTAG_COMPRESSION,COMPRESSION_NONE);TIFFSetField(tiff.get(),TIFFTAG_ROWSPERSTRIP,16);TIFFSetField(tiff.get(),TIFFTAG_ORIENTATION,ORIENTATION_TOPLEFT);TIFFSetField(tiff.get(),TIFFTAG_IMAGEDESCRIPTION,metadata.c_str());TIFFSetField(tiff.get(),TIFFTAG_SOFTWARE,VERSION);
   std::vector<std::uint16_t> row(raster.width*3);bool linear_out=options.value("linear_tiff",true);
   for(int y=0;y<raster.height;y+=16){job.check();auto tile=raster.read(0,y,raster.width,std::min(16,raster.height-y));for(int j=0;j<tile.rows;++j){auto* p=tile.ptr<cv::Vec3f>(j);for(int x=0;x<raster.width;++x)for(int c=0;c<3;++c)row[x*3+c]=std::uint16_t(std::lround((linear_out?std::clamp(p[x][c],0.f,1.f):srgb(p[x][c]))*65535));if(TIFFWriteScanline(tiff.get(),row.data(),y+j,0)<0)throw std::runtime_error("TIFF output write failed");}if(y%128==0)job.progress("encode",.8+.19*double(y)/raster.height);}
   if(!TIFFFlush(tiff.get()))throw std::runtime_error("TIFF output flush failed");
  } else if(format=="png") {
   if(std::uint64_t(raster.width)*raster.height>2ull*1000*1000)throw std::runtime_error("PNG derivation exceeds preview bound");auto image=raster.read(0,0,raster.width,raster.height);cv::Mat display;image.convertTo(display,CV_8UC3,255);cv::cvtColor(display,display,cv::COLOR_RGB2BGR);std::vector<unsigned char> encoded;if(!cv::imencode(".png",display,encoded))throw std::runtime_error("PNG encoder unavailable");std::ofstream out(temporary,std::ios::binary);out.write(reinterpret_cast<const char*>(encoded.data()),encoded.size());out.close();
  } else throw std::runtime_error("output must be jpeg/tiff16/png");
  job.check();{FILE* sync=open_file(temporary,"r+b");if(!sync)throw std::runtime_error("output sync failed");
#ifdef _WIN32
   if(_commit(_fileno(sync))!=0){fclose(sync);throw std::runtime_error("output fsync failed");}
#else
   if(fsync(fileno(sync))!=0){fclose(sync);throw std::runtime_error("output fsync failed");}
#endif
   fclose(sync);}auto bytes=fs::file_size(temporary);auto digest=file_sha256(temporary);publish(temporary,destination);
  return {{"path",destination.u8string()},{"sha256",digest},{"byte_count",bytes},{"width",raster.width},{"height",raster.height},{"format",format},{"processor",VERSION},{"color_calibrated",false}};
 } catch(...){std::error_code ec;fs::remove(temporary,ec);throw;}
}
}
