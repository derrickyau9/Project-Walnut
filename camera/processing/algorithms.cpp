#include "processing.hpp"
#include <opencv2/imgproc.hpp>
#include <opencv2/imgcodecs.hpp>
#include <opencv2/features2d.hpp>
#include <opencv2/calib3d.hpp>
#include <algorithm>
#include <cmath>
#include <numeric>
#include <stdexcept>
namespace daos {
namespace {
float gamma(float v){v=std::clamp(v,0.f,1.f);return v<=.0031308f?v*12.92f:1.055f*std::pow(v,1.f/2.4f)-.055f;}
cv::Mat gray(const cv::Mat& image){cv::Mat g;cv::cvtColor(image,g,cv::COLOR_RGB2GRAY);return g;}
cv::Mat gray8(const cv::Mat& image){cv::Mat g=gray(image),out;g.convertTo(out,CV_8U,255);return out;}
cv::Mat display8(const cv::Mat& image){cv::Mat out(image.rows,image.cols,CV_8UC3);for(int y=0;y<image.rows;++y){auto* a=image.ptr<cv::Vec3f>(y);auto* b=out.ptr<cv::Vec3b>(y);for(int x=0;x<image.cols;++x)for(int c=0;c<3;++c)b[x][c]=std::uint8_t(std::lround(gamma(a[x][c])*255));}return out;}
double number(const Json& params,const char* key,double fallback,double low,double high){double v=params.value(key,fallback);if(!std::isfinite(v)||v<low||v>high)throw std::runtime_error(std::string("invalid parameter: ")+key);return v;}
std::vector<cv::Mat> alignment(const std::vector<Raster>& images,const std::string& kind,const Json& p,Json& provenance){
 std::vector<cv::Mat> matrices(images.size());for(auto& m:matrices)m=cv::Mat::eye(3,3,CV_64F);
 if(images.size()<2||(!p.value("align",kind=="astro_stack"||kind=="panorama")))return matrices;
 auto first=images[0].proxy(1200);double scale=double(first.cols)/images[0].width;Json records=Json::array();
 for(std::size_t i=1;i<images.size();++i){auto proxy=images[i].proxy(1200);
  if(kind=="panorama"){
   auto previous=images[i-1].proxy(1200);auto orb=cv::ORB::create(3500);std::vector<cv::KeyPoint>a,b;cv::Mat da,db;orb->detectAndCompute(gray8(proxy),cv::noArray(),a,da);orb->detectAndCompute(gray8(previous),cv::noArray(),b,db);
   if(da.empty()||db.empty())throw std::runtime_error("panorama needs textured overlapping photographs");
   std::vector<std::vector<cv::DMatch>> matches;cv::BFMatcher(cv::NORM_HAMMING).knnMatch(da,db,matches,2);std::vector<cv::Point2f> pa,pb;
   for(const auto& match:matches)if(match.size()==2&&match[0].distance<.74f*match[1].distance){pa.push_back(a[match[0].queryIdx].pt);pb.push_back(b[match[0].trainIdx].pt);}
   if(pa.size()<8)throw std::runtime_error("panorama has insufficient reliable overlap");cv::Mat inliers;auto h=cv::findHomography(pa,pb,cv::RANSAC,3.,inliers);
   if(h.empty()||cv::countNonZero(inliers)<8)throw std::runtime_error("panorama alignment rejected");
   double a_scale=double(proxy.cols)/images[i].width,b_scale=double(previous.cols)/images[i-1].width;cv::Mat sa=(cv::Mat_<double>(3,3)<<a_scale,0,0,0,a_scale,0,0,0,1),sb=(cv::Mat_<double>(3,3)<<b_scale,0,0,0,b_scale,0,0,0,1);matrices[i]=matrices[i-1]*(sb.inv()*h*sa);records.push_back({{"source",i},{"inliers",cv::countNonZero(inliers)}});
  }else{
   if(proxy.size()!=first.size())throw std::runtime_error("stack alignment requires matching dimensions");double response=0;cv::Point2d shift=cv::phaseCorrelate(gray(first),gray(proxy),cv::noArray(),&response);
   if(response<.02||std::hypot(shift.x,shift.y)>std::max(first.cols,first.rows)*.2)throw std::runtime_error("stack translation alignment rejected");
   matrices[i].at<double>(0,2)=-shift.x/scale;matrices[i].at<double>(1,2)=-shift.y/scale;records.push_back({{"source",i},{"shift_x",-shift.x/scale},{"shift_y",-shift.y/scale},{"response",response}});
  }
 }provenance["alignment"]=records;return matrices;
}
cv::Mat warped(const Raster& image,const cv::Mat& h,int x,int y,int width,int height,cv::Mat& valid){
 cv::Mat inverse=h.inv();std::vector<cv::Point2f> corners={{float(x),float(y)},{float(x+width),float(y)},{float(x+width),float(y+height)},{float(x),float(y+height)}};std::vector<cv::Point2f> source;cv::perspectiveTransform(corners,source,inverse);
 float minx=1e9f,miny=1e9f,maxx=-1e9f,maxy=-1e9f;for(auto v:source){if(!std::isfinite(v.x)||!std::isfinite(v.y))throw std::runtime_error("invalid image transform");minx=std::min(minx,v.x);maxx=std::max(maxx,v.x);miny=std::min(miny,v.y);maxy=std::max(maxy,v.y);}
 int sx=std::max(0,int(std::floor(minx))-3),sy=std::max(0,int(std::floor(miny))-3),ex=std::min(image.width,int(std::ceil(maxx))+3),ey=std::min(image.height,int(std::ceil(maxy))+3);
 valid=cv::Mat::zeros(height,width,CV_32F);if(ex<=sx||ey<=sy)return cv::Mat::zeros(height,width,CV_32FC3);
 if(std::uint64_t(ex-sx)*(ey-sy)>6ull*1000*1000)throw std::runtime_error("transform source tile exceeds memory budget");
 auto patch=image.read(sx,sy,ex-sx,ey-sy);cv::Mat translate_source=(cv::Mat_<double>(3,3)<<1,0,sx,0,1,sy,0,0,1),translate_output=(cv::Mat_<double>(3,3)<<1,0,-x,0,1,-y,0,0,1),local=translate_output*h*translate_source;
 cv::Mat output;cv::warpPerspective(patch,output,local,{width,height},cv::INTER_LINEAR,cv::BORDER_CONSTANT);
 cv::Mat ones(patch.rows,patch.cols,CV_32F,cv::Scalar(1));cv::warpPerspective(ones,valid,local,{width,height},cv::INTER_NEAREST,cv::BORDER_CONSTANT);return output;
}
void edit(cv::Mat& tile,const Json& p,const cv::Vec3f& wb){
 double ev=number(p,"exposure_ev",p.value("exposure",0.),-10,10),contrast=number(p,"contrast",1.,0,3),saturation=number(p,"saturation",1.,0,3);
 double noise=number(p,"noise",0.,0,1),sharpen=number(p,"sharpen",0.,0,3);float exposure=std::pow(2.f,float(ev));
 if(noise>0){cv::Mat filtered;cv::bilateralFilter(tile,filtered,5,.015+noise*.08,2.);tile=filtered;}
 std::vector<cv::Point2f> curve={{0,0},{1,1}};if(p.contains("curve")){curve.clear();if(!p["curve"].is_array()||p["curve"].size()<2||p["curve"].size()>32)throw std::runtime_error("curve needs 2 to 32 points");float lastx=-1,lasty=-1;for(const auto& v:p["curve"]){if(!v.is_array()||v.size()!=2)throw std::runtime_error("invalid curve point");float x=v[0],y=v[1];if(!std::isfinite(x)||!std::isfinite(y)||x<=lastx||y<lasty||x<0||x>1||y<0||y>1)throw std::runtime_error("curve must be finite and monotonic");curve.emplace_back(x,y);lastx=x;lasty=y;}if(curve.front().x!=0||curve.back().x!=1)throw std::runtime_error("curve must cover 0..1");}
 std::array<float,65536> lut{};std::size_t segment=1;for(int n=0;n<65536;++n){float x=n/65535.f;while(segment+1<curve.size()&&x>curve[segment].x)++segment;auto a=curve[segment-1],b=curve[segment];lut[n]=a.y+(b.y-a.y)*(x-a.x)/(b.x-a.x);}
 for(int y=0;y<tile.rows;++y){auto* row=tile.ptr<cv::Vec3f>(y);for(int x=0;x<tile.cols;++x){cv::Vec3f v=row[x];for(int c=0;c<3;++c)v[c]=std::clamp((v[c]*wb[c]*exposure-.18f)*float(contrast)+.18f,0.f,1.f);float l=.2126f*v[0]+.7152f*v[1]+.0722f*v[2];for(int c=0;c<3;++c){float value=std::clamp(l+(v[c]-l)*float(saturation),0.f,1.f);v[c]=lut[std::lround(value*65535)];}row[x]=v;}}
 if(sharpen>0){cv::Mat blur;cv::GaussianBlur(tile,blur,{0,0},1.);tile+=float(sharpen)*(tile-blur);}
}
}
Raster process(const std::vector<Raster>& images,const std::string& kind,const Json& p,Job& job){
 if(images.empty()||images.size()>24)throw std::runtime_error("processing needs 1..24 inputs");
 bool multi=kind=="hdr"||kind=="exposure_fusion"||kind=="focus_stack"||kind=="denoise_stack"||kind=="astro_stack"||kind=="panorama"||kind=="darkflat";
 if(multi&&images.size()<2)throw std::runtime_error("this operation needs at least two inputs");
 if(kind=="hdr"){double first=-1;bool different=false;for(const auto& image:images){if(!image.provenance.contains("exposure_us"))throw std::runtime_error("HDR needs actual exposure metadata or explicit exposures_us");double value=image.provenance["exposure_us"];if(!std::isfinite(value)||value<=0)throw std::runtime_error("HDR exposure must be positive");if(first<0)first=value;else different|=std::abs(value-first)>.01;}if(!different)throw std::runtime_error("HDR needs different exposure times; use exposure fusion for unknown brackets");}
 if(kind!="panorama")for(const auto& image:images)if(image.width!=images[0].width||image.height!=images[0].height)throw std::runtime_error("stack inputs must have matching dimensions");
 Json provenance=images[0].provenance;auto matrices=alignment(images,kind,p,provenance);int width=images[0].width,height=images[0].height;
 if(kind=="panorama"){
  double minx=0,miny=0,maxx=width,maxy=height;for(std::size_t n=0;n<images.size();++n){std::vector<cv::Point2f> corners={{0,0},{float(images[n].width),0},{float(images[n].width),float(images[n].height)},{0,float(images[n].height)}};std::vector<cv::Point2f> transformed;cv::perspectiveTransform(corners,transformed,matrices[n]);for(auto pt:transformed){minx=std::min(minx,double(pt.x));miny=std::min(miny,double(pt.y));maxx=std::max(maxx,double(pt.x));maxy=std::max(maxy,double(pt.y));}}
  width=int(std::ceil(maxx-minx));height=int(std::ceil(maxy-miny));if(width<1||height<1||std::uint64_t(width)*height>80ull*1000*1000||width>40000||height>20000)throw std::runtime_error("panorama output exceeds budget");cv::Mat shift=(cv::Mat_<double>(3,3)<<1,0,-minx,0,1,-miny,0,0,1);for(auto& h:matrices)h=shift*h;
 }
 cv::Vec3f wb(1,1,1);if(p.contains("wb")){auto values=p["wb"].get<std::vector<float>>();if(values.size()!=3)throw std::runtime_error("WB needs RGB multipliers");for(int c=0;c<3;++c){if(!std::isfinite(values[c])||values[c]<.1f||values[c]>8)throw std::runtime_error("WB multiplier exceeds bound");wb[c]=values[c];}}
 if(p.value("auto_wb",false)){auto m=cv::mean(images[0].proxy(600));wb={float(m[1]/std::max(.0001,m[0])),1,float(m[1]/std::max(.0001,m[2]))};for(int c=0;c<3;++c)wb[c]=std::clamp(wb[c],.1f,8.f);}
 if(p.contains("temperature")){double temperature=number(p,"temperature",6500,2000,12000),tint=number(p,"tint",0,-1,1);wb[0]*=std::pow(float(temperature/6500),.65f);wb[2]*=std::pow(float(6500/temperature),.65f);wb[1]*=std::pow(2.f,float(tint*.3));}
 cv::Vec3f film_base(1,1,1);if(kind=="negative"){
  if(p.contains("film_base")){auto v=p["film_base"].get<std::vector<float>>();if(v.size()!=3)throw std::runtime_error("film base needs RGB");for(int c=0;c<3;++c){if(v[c]<=.001||v[c]>1)throw std::runtime_error("invalid film base");film_base[c]=v[c];}}
  else {auto proxy=images[0].proxy(600);for(int c=0;c<3;++c){std::vector<float> v;for(int y=0;y<proxy.rows;++y)for(int x=0;x<proxy.cols;++x)v.push_back(proxy.at<cv::Vec3f>(y,x)[c]);std::size_t at=v.size()*95/100;std::nth_element(v.begin(),v.begin()+at,v.end());film_base[c]=std::max(.001f,v[at]);}}
  provenance["film_base"]=std::vector<float>{film_base[0],film_base[1],film_base[2]};
 }
 cv::Scalar flat_mean(1,1,1);if(kind=="darkflat"&&images.size()>2){auto flat=images[2].proxy(600),dark=images[1].proxy(600);flat_mean=cv::mean(flat-dark);for(int c=0;c<3;++c)if(flat_mean[c]<=.00001)throw std::runtime_error("flat frame has no positive response");}
 if(fs::space(job.scratch).available<std::uint64_t(width)*height*6+64ull*1024*1024)throw std::runtime_error("insufficient processed raster space budget");
 Raster output{job.scratch/"processed.rgb16",width,height,provenance};std::ofstream out(output.path,std::ios::binary);if(!out)throw std::runtime_error("cannot create processed raster");
 const int strip=16,halo=8;
 for(int y=0;y<height;y+=strip){job.check();int y0=std::max(0,y-halo),y1=std::min(height,y+strip+halo),rows=y1-y0;
  cv::Mat sum=cv::Mat::zeros(rows,width,CV_32FC3),weights=cv::Mat::zeros(rows,width,CV_32F),best=cv::Mat::zeros(rows,width,CV_32F);cv::Mat variance;
  bool average=kind=="denoise_stack"||kind=="astro_stack";
  if(average)variance=cv::Mat::zeros(rows,width,CV_32FC3);
  for(std::size_t n=0;n<images.size();++n){job.check();cv::Mat valid;auto tile=warped(images[n],matrices[n],0,y0,width,rows,valid);
   if(kind=="darkflat"){
    if(n>0)break;auto dark=images[1].read(0,y0,width,rows);tile-=dark;if(images.size()>2){cv::Mat flat=images[2].read(0,y0,width,rows)-dark;for(int j=0;j<rows;++j)for(int x=0;x<width;++x)for(int c=0;c<3;++c)tile.at<cv::Vec3f>(j,x)[c]*=float(flat_mean[c])/std::max(.0001f,flat.at<cv::Vec3f>(j,x)[c]);}sum=tile;weights.setTo(1);break;
   }
   if(kind=="negative"){for(int j=0;j<rows;++j){auto* row=tile.ptr<cv::Vec3f>(j);for(int x=0;x<width;++x)for(int c=0;c<3;++c)row[x][c]=1.f-row[x][c]/film_base[c];}sum=tile;weights.setTo(1);break;}
   if(!multi){sum=tile;weights.setTo(1);break;}
   cv::Mat g=gray(tile),contrast;cv::Laplacian(g,contrast,CV_32F,3);cv::absdiff(contrast,cv::Scalar(0),contrast);cv::GaussianBlur(contrast,contrast,{5,5},1.);
   for(int j=0;j<rows;++j){auto* row=tile.ptr<cv::Vec3f>(j);auto* s=sum.ptr<cv::Vec3f>(j);auto* ws=weights.ptr<float>(j);auto* vs=valid.ptr<float>(j);auto* cs=contrast.ptr<float>(j);auto* bs=best.ptr<float>(j);auto* var=average?variance.ptr<cv::Vec3f>(j):nullptr;
    for(int x=0;x<width;++x){if(vs[x]<.5f)continue;cv::Vec3f value=row[x];
     if(average){float count=ws[x]+1;auto delta=value-s[x];s[x]+=delta*(1.f/count);auto delta2=value-s[x];for(int c=0;c<3;++c)var[x][c]+=delta[c]*delta2[c];ws[x]=count;continue;}
     if(kind=="focus_stack"){float score=cs[x];if(ws[x]==0||score>bs[x]){s[x]=value;bs[x]=score;ws[x]=1;}continue;}
     float well=std::exp(-((value[0]-.5f)*(value[0]-.5f)+(value[1]-.5f)*(value[1]-.5f)+(value[2]-.5f)*(value[2]-.5f))/.12f)+.00001f;
     float weight=well;
     if(kind=="hdr"){double reference=images[0].provenance.value("exposure_us",10000.);double exposure=images[n].provenance.value("exposure_us",reference);if(exposure<=0||reference<=0)throw std::runtime_error("HDR requires positive exposure metadata");value*=float(reference/exposure);}
     else if(kind=="exposure_fusion"){float mean=(value[0]+value[1]+value[2])/3;float sat=std::sqrt(((value[0]-mean)*(value[0]-mean)+(value[1]-mean)*(value[1]-mean)+(value[2]-mean)*(value[2]-mean))/3);weight*=((cs[x]+.001f)*(sat+.01f));}
     else if(kind=="panorama"){float edge=std::min({float(x+1),float(width-x),float(j+1),float(rows-j)});weight=std::max(.01f,edge);}
     s[x]+=value*weight;ws[x]+=weight;
    }
   }
  }
  if(average&&p.value("sigma_clip",true)&&images.size()>=3){cv::Mat clipped=cv::Mat::zeros(rows,width,CV_32FC3),counts=cv::Mat::zeros(rows,width,CV_32F);float sigma=float(number(p,"sigma",2.8,1,6));
   for(std::size_t n=0;n<images.size();++n){job.check();cv::Mat valid;auto tile=warped(images[n],matrices[n],0,y0,width,rows,valid);for(int j=0;j<rows;++j)for(int x=0;x<width;++x){if(valid.at<float>(j,x)<.5f)continue;auto delta=tile.at<cv::Vec3f>(j,x)-sum.at<cv::Vec3f>(j,x);auto var=variance.at<cv::Vec3f>(j,x);bool keep=true;for(int c=0;c<3;++c)if(std::abs(delta[c])>sigma*std::sqrt(var[c]/std::max(1.f,weights.at<float>(j,x)-1)+1e-8f))keep=false;if(keep){clipped.at<cv::Vec3f>(j,x)+=tile.at<cv::Vec3f>(j,x);counts.at<float>(j,x)+=1;}}}
   for(int j=0;j<rows;++j)for(int x=0;x<width;++x)if(counts.at<float>(j,x)>0)sum.at<cv::Vec3f>(j,x)=clipped.at<cv::Vec3f>(j,x)/counts.at<float>(j,x);
  }
  for(int j=0;j<rows;++j)for(int x=0;x<width;++x){auto& v=sum.at<cv::Vec3f>(j,x);float weight=weights.at<float>(j,x);if(!average&&weight>0)v*=1.f/weight;if(kind=="hdr"){float luminance=.2126f*v[0]+.7152f*v[1]+.0722f*v[2];if(luminance>0)v*=((1+luminance/16)/(1+luminance));}}
  edit(sum,p,wb);write_rows(out,sum.rowRange(y-y0,std::min(y+strip,height)-y0));job.progress("process",.2+.55*double(y)/height);
 }
 out.close();output.provenance["algorithm"]=kind;output.provenance["wb_applied"]=std::vector<float>{wb[0],wb[1],wb[2]};return output;
}
Json analyze(const Raster& image,const Json& options,Job& job){
 std::array<std::array<std::uint64_t,256>,4> hist{};std::uint64_t low=0,high=0,pixels=std::uint64_t(image.width)*image.height;double luminance_sum=0;
 for(int y=0;y<image.height;y+=16){job.check();auto tile=image.read(0,y,image.width,std::min(16,image.height-y));for(int j=0;j<tile.rows;++j){auto* row=tile.ptr<cv::Vec3f>(j);for(int x=0;x<tile.cols;++x){auto v=row[x];float l=.2126f*v[0]+.7152f*v[1]+.0722f*v[2];luminance_sum+=l;low+=l<.005;high+=l>.98;for(int c=0;c<3;++c)++hist[c][std::clamp(int(std::lround(gamma(v[c])*255)),0,255)];++hist[3][std::clamp(int(std::lround(gamma(l)*255)),0,255)];}}}
 auto proxy=image.proxy(1280),g=gray(proxy);cv::Mat lap;cv::Laplacian(g,lap,CV_32F,3);cv::Scalar mean,stddev;cv::meanStdDev(lap,mean,stddev);
 Json result={{"scope","full raster RGB/luma histogram; sharpness sampled at fit1280"},{"width",image.width},{"height",image.height},{"pixel_count",pixels},{"histogram",hist},{"shadow_fraction",double(low)/pixels},{"highlight_fraction",double(high)/pixels},{"sharpness_laplacian_variance",stddev[0]*stddev[0]},{"suggested_exposure_ev",std::clamp(std::log2(.18/std::max(1e-8,luminance_sum/pixels)),-3.,3.)},{"processor",VERSION},{"outputs",Json::array()}};
 if(options.contains("overlays")){auto rgb=display8(proxy);for(const auto& value:options["overlays"]){job.check();std::string name=value;cv::Mat overlay=rgb.clone();
   if(name=="zebra"){float threshold=float(number(options,"zebra_threshold",.98,.5,1));for(int y=0;y<g.rows;++y)for(int x=0;x<g.cols;++x)if(g.at<float>(y,x)>=threshold)overlay.at<cv::Vec3b>(y,x)=((x+y)/5)%2?cv::Vec3b(255,255,255):cv::Vec3b(0,0,0);}
   else if(name=="falsecolor"){cv::Mat levels;g.convertTo(levels,CV_8U,255);cv::applyColorMap(levels,overlay,cv::COLORMAP_TURBO);cv::cvtColor(overlay,overlay,cv::COLOR_BGR2RGB);}
   else if(name=="peaking"){double threshold=std::max(.005,stddev[0]*1.5);for(int y=0;y<lap.rows;++y)for(int x=0;x<lap.cols;++x)if(std::abs(lap.at<float>(y,x))>threshold)overlay.at<cv::Vec3b>(y,x)=cv::Vec3b(255,45,45);}
   else throw std::runtime_error("unknown analysis overlay");
   cv::cvtColor(overlay,overlay,cv::COLOR_RGB2BGR);auto path=job.scratch/fs::u8path("analysis-"+name+".png");if(!cv::imwrite(path.u8string(),overlay))throw std::runtime_error("overlay encoder failed");result["outputs"].push_back({{"path",path.u8string()},{"kind",name},{"sha256",file_sha256(path)},{"width",overlay.cols},{"height",overlay.rows}});
  }}return result;
}
}
